import os
import json
import re
import requests
import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright
import google.generativeai as genai

# ==========================================
# 1. 設定情報の準備
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
CHATWORK_TOKEN = os.environ.get("CHATWORK_TOKEN")
CHATWORK_ROOM_ID = os.environ.get("CHATWORK_ROOM_ID")
SPREADSHEET_ID = "1ySo2dw6sFk467Wi9cDgwfU47xYm8LU94RzfAUxtOGf8"

# ==========================================
# ★ここに貼り付けます（Gemini APIの初期設定）
# ==========================================
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')


# ==========================================
# 2. ここから下に各種処理（関数）を書く
# ==========================================
def get_search_conditions():
    # スプレッドシートから条件を取ってくる処理...
    pass

def fetch_suumo_data():
    # ブラウザでSUUMOを見に行く処理...
    pass

def analyze_with_gemini(raw_text, conditions):
    # 上で準備した「model」を使ってAI解析する処理...
    pass