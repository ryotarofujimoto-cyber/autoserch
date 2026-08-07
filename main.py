import os
import json
import re
import requests
import gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright
from google import genai

# ==========================================
# 1. 設定情報の準備（環境変数より取得）
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
CHATWORK_TOKEN = os.environ.get("CHATWORK_TOKEN")
CHATWORK_ROOM_ID = os.environ.get("CHATWORK_ROOM_ID")
SPREADSHEET_ID = "1ySo2dw6sFk467Wi9cDgwfU47xYm8LU94RzfAUxtOGf8"

# ==========================================
# 2. Gemini API (新 SDK) の初期設定
# ==========================================
client = genai.Client(api_key=GEMINI_API_KEY)


# ==========================================
# 3. スプレッドシートから条件を取得する関数
# ==========================================
def get_search_conditions():
    """スプレッドシートの「依頼」シートからD列の詳細町名を取得"""
    creds_json_str = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json_str:
        print("エラー: GOOGLE_CREDENTIALS が設定されていません。")
        return []

    creds_json = json.loads(creds_json_str)
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    g_client = gspread.authorize(creds)
    
    sheet = g_client.open_by_key(SPREADSHEET_ID).worksheet("依頼")
    records = sheet.get_all_values()
    
    conditions = []
    for row in records[1:]:
        if not row or row[0] == 'ボツ' or (len(row) > 3 and row[3] == 'ボツ'):
            break
        
        if len(row) > 3 and row[3] and row[3] not in ['金額', '依頼内容']:
            towns = [t.strip().lstrip('-').strip() for t in re.split(r'[\n,、・]', row[3]) if t.strip()]
            valid_towns = [t for t in towns if "指定なし" not in t and "情報が不足" not in t]
            
            if valid_towns:
                conditions.append({
                    "client": row[1] if len(row) > 1 and row[1] else "担当者未設定",
                    "towns": valid_towns
                })
    return conditions


# ==========================================
# 4. ポータルサイトからのスクレイピング関数
# ==========================================

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def fetch_site_data(site_name, url):
    """Playwrightを使って指定されたURLからテキストデータを取得"""
    print(f"[{site_name}] アクセス中: {url}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()
            
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page_text = page.locator("body").inner_text()
            browser.close()
            return page_text, url
    except Exception as e:
        print(f"[{site_name}] 取得エラー: {e}")
        return "", url


# ==========================================
# 5. Gemini API による解析関数（1日以内フィルター対応）
# ==========================================
def analyze_with_gemini(site_name, raw_text, conditions):
    """取得した画面テキストと依頼条件を Gemini API に渡し、1日以内に公開・更新された物件のみ抽出"""
    if not raw_text:
        return []
        
    # 本日と昨日の日付を取得
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    
    today_str = today.strftime("%Y年%m月%d日")
    yesterday_str = yesterday.strftime("%Y年%m月%d日")

    prompt = f"""
以下のWebサイト（サイト名: {site_name}）のテキストから、指定された【町名キーワード】が含まれ、かつ【情報公開日・更新日・登録日】が「1日以内（本日または昨日）」の物件情報のみを抽出してください。

【基準日時】:
- 本日: {today_str}
- 昨日: {yesterday_str}

【町名キーワード条件】:
{json.dumps(conditions, ensure_ascii=False)}

【Webサイトテキストデータ】:
{raw_text[:12000]}

【抽出・厳密ルール】:
1. テキスト内に条件に含まれる町名（例: 飾磨区、勝原区、大津区など）が記載されていること。
2. 物件の「公開日」「登録日」「更新日」「掲載日」が【本日 ({today_str})】または【昨日 ({yesterday_str})】に該当するもの、もしくは「本日掲載」「昨日掲載」「新着」などの表記があるものだけを抽出してください。
3. 公開日・更新日が2日以上前の明確な日付が記載されている物件は絶対に除外してください。
4. 必ず以下のJSON配列フォーマットのみで出力してください。該当がなければ [] を返してください。

[
  {{
    "siteName": "{site_name}",
    "townName": "一致した町名",
    "client": "依頼者名",
    "title": "物件名",
    "price": "価格",
    "address": "住所",
    "publishedDate": "公開日または更新日",
    "url": "URL"
  }}
]
"""
    try:
        response = client.models.generate_content(
            model='gemini-flash-latest',
            contents=prompt
        )
        
        match = re.search(r'\[.*\]', response.text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return []
    except Exception as e:
        print(f"[{site_name}] AI解析エラー: {e}")
        return []


# ==========================================
# 6. Chatworkへの通知関数
# ==========================================
def send_chatwork(property_data):
    """条件にマッチした物件をChatworkに送信"""
    if not CHATWORK_TOKEN or not CHATWORK_ROOM_ID:
        print("警告: CHATWORK_TOKEN または CHATWORK_ROOM_ID が設定されていません。")
        return

    url = f"https://api.chatwork.com/v2/rooms/{CHATWORK_ROOM_ID}/messages"
    headers = {"X-ChatWorkToken": CHATWORK_TOKEN}
    
    site_name = property_data.get("siteName", "ポータル")
    pub_date = property_data.get("publishedDate", "直近1日以内")
    
    body_text = (
        f"[info][title]🏠 【AI検知】新着物件通知 [{site_name}] ({property_data.get('townName', '地域')})[/title]"
        f"■ 掲載サイト: {site_name}\n"
        f"■ 公開/更新: {pub_date}\n"
        f"■ 依頼者/担当: {property_data.get('client', '未指定')}\n"
        f"■ 物件名: {property_data.get('title', '名称不明')}\n"
        f"■ 価格: {property_data.get('price', '要確認')}\n"
        f"■ 所在地: {property_data.get('address', '-')}\n"
        f"■ 物件URL: {property_data.get('url', '-')}[/info]"
    )
    
    res = requests.post(url, headers=headers, data={"body": body_text})
    print(f"Chatwork送信結果 [{site_name}] ({property_data.get('townName')}): ステータスコード {res.status_code}")


# ==========================================
# 7. メイン実行処理
# ==========================================
def main():
    print("--- 処理を開始します ---")
    
    # 1. スプレッドシートから条件を取得
    conditions = get_search_conditions()
    print(f"スプレッドシートから取得した条件数: {len(conditions)}件")
    if not conditions:
        print("処理対象の条件がないため終了します。")
        return

    # 2. 巡回対象のポータルサイトURL一覧（姫路市の土地新着順）
    targets = [
        {
            "name": "SUUMO",
            "url": "https://suumo.jp/jj/bukken/ichiran/JJ010001/?ar=060&bs=030&ta=28&sc=28201&srz=01"
        },
        {
            "name": "アットホーム",
            "url": "https://www.athome.co.jp/tochi/hyogo/himeji-city/list/?sort=1"
        },
        {
            "name": "LIFULL HOME'S",
            "url": "https://www.homes.co.jp/tochi/hyogo/himeji-city/list/?sort=registered_date"
        }
    ]

    # 3. 各サイトを巡回して解析＆通知
    total_matches = 0
    for target in targets:
        site_name = target["name"]
        site_url = target["url"]
        
        raw_text, base_url = fetch_site_data(site_name, site_url)
        if not raw_text:
            continue
            
        print(f"[{site_name}] Gemini API (AI) による解析を実行中...")
        matched_properties = analyze_with_gemini(site_name, raw_text, conditions)
        print(f"[{site_name}] 条件一致件数: {len(matched_properties)}件")
        
        total_matches += len(matched_properties)
        
        for prop in matched_properties:
            if not prop.get("url") or prop.get("url") == "URL":
                prop["url"] = base_url
            send_chatwork(prop)

    print(f"--- すべての処理が完了しました（合計検出数: {total_matches}件） ---")


if __name__ == "__main__":
    main()
