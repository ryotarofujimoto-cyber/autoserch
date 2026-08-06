import os
import json
import re
import requests
import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright
from google import genai # 新しいライブラリのインポート

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
# 4. Playwrightでポータルサイト(SUUMO)の画面テキストを取得する関数
# ==========================================
def fetch_suumo_data():
    """裏でブラウザを起動し、SUUMOの姫路市新着土地一覧からテキストを取得"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        target_url = "https://suumo.jp/jj/bukken/ichiran/JJ010001/?ar=060&bs=030&ta=28&sc=28201&srz=01"
        
        print(f"SUUMOへアクセス中: {target_url}")
        page.goto(target_url, wait_until="domcontentloaded")
        
        page_text = page.locator("body").inner_text()
        browser.close()
        return page_text, target_url


# ==========================================
# 5. Gemini API (新 SDK) に物件の判定を行わせる関数
# ==========================================
def analyze_with_gemini(raw_text, conditions):
    """取得した画面テキストと依頼条件を Gemini API に渡し、合致物件を抽出"""
    prompt = f"""
以下のWebサイトテキストから、指定された【町名キーワード】が含まれる物件情報を抽出してください。

【町名キーワード条件】:
{json.dumps(conditions, ensure_ascii=False)}

【Webサイトテキストデータ】:
{raw_text[:12000]}

【抽出ルール】:
- テキスト内に条件に含まれる町名（例: 飾磨区、勝原区、大津区など）が記載されている物件を見つけてください。
- 条件に少しでも当てはまれば抽出対象としてください。
- 必ず以下のJSON配列フォーマットのみで出力してください。該当がなければ [] を返してください。

[
  {{
    "townName": "一致した町名",
    "client": "依頼者名",
    "title": "物件名",
    "price": "価格",
    "address": "住所",
    "url": "URL"
  }}
]
"""
    try:
        # 新しい SDK (google-genai) での呼び出し形式
        response = client.models.generate_content(
            model='gemini-2.5-flash', # 新世代モデルを指定
            contents=prompt
        )
        
        match = re.search(r'\[.*\]', response.text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return []
    except Exception as e:
        print(f"AI解析エラー: {e}")
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
    
    body_text = (
        f"[info][title]🏠 【AI検知】新着物件通知 ({property_data.get('townName', '地域')})[/title]"
        f"■ 依頼者/担当: {property_data.get('client', '未指定')}\n"
        f"■ 物件名: {property_data.get('title', '名称不明')}\n"
        f"■ 価格: {property_data.get('price', '要確認')}\n"
        f"■ 所在地: {property_data.get('address', '-')}\n"
        f"■ 物件URL: {property_data.get('url', '-')}[/info]"
    )
    
    res = requests.post(url, headers=headers, data={"body": body_text})
    print(f"Chatwork送信結果 ({property_data.get('townName')}): ステータスコード {res.status_code}")


# ==========================================
# 7. メイン実行処理
# ==========================================
def main():
    print("--- 処理を開始します ---")
    
    # 接続動作確認用テスト通知（確認後に不要であれば消してください）
    send_chatwork({
        "townName": "システムテスト",
        "client": "管理者",
        "title": "GitHub Actions接続テストメッセージ",
        "price": "-",
        "address": "-",
        "url": "https://github.com"
    })
    
    conditions = get_search_conditions()
    print(f"スプレッドシートから取得した条件数: {len(conditions)}件")
    if not conditions:
        print("処理対象の条件がないため終了します。")
        return

    raw_text, base_url = fetch_suumo_data()
    print("SUUMOからのデータ取得が完了しました。")

    print("Gemini API (AI) によるデータ解析を実行中...")
    matched_properties = analyze_with_gemini(raw_text, conditions)
    print(f"AIが条件一致と判定した物件数: {len(matched_properties)}件")

    for prop in matched_properties:
        if not prop.get("url"):
            prop["url"] = base_url
        send_chatwork(prop)

    print("--- すべての処理が完了しました ---")


if __name__ == "__main__":
    main()