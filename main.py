from fastapi import FastAPI, Request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.models import QuickReply, QuickReplyButton, MessageAction
from linebot.models import FollowEvent
from linebot.models import Profile
from dotenv import load_dotenv
import os
from pydantic import BaseModel
from supabase import create_client, Client
import yfinance as yf
import requests
from bs4 import BeautifulSoup

# .envの読み込み
load_dotenv()

app = FastAPI()

# Supabaseクライアントの初期化
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

class UserRegister(BaseModel):
    line_user_id: str
    experience: str

@app.post("/register")
async def register_user(user: UserRegister):
    response = supabase.table("line_users").insert({
        "line_user_id": user.line_user_id,
        "experience": user.experience
    }).execute()
    return {"status": "ok", "data": response.data}

# LINE APIの初期化
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers["X-Line-Signature"]
    body = await request.body()
    body_str = body.decode("utf-8")

    try:
        handler.handle(body_str, signature)
    except Exception as e:
        print("LINE webhook error:", e)
        return {"status": "error"}

    return {"status": "ok"}


# Yahooファイナンスから証券コード候補リストを取得
def get_ticker_candidates(company_name: str):
    search_url = f"https://finance.yahoo.co.jp/search/?query={company_name}"
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(search_url, headers=headers)
    soup = BeautifulSoup(res.text, "lxml")

    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if "/quote/" in href and ".T" in href:
            ticker = href.split("/quote/")[-1].strip()
            if (ticker, text) not in candidates:
                candidates.append((ticker, text))
        if len(candidates) >= 5:
            break
    return candidates

def fetch_stock_info(company_name: str):
    candidates = get_ticker_candidates(company_name)
    if not candidates:
        return {"error": f"{company_name} の証券コードが見つかりませんでした。"}
    ticker, _ = candidates[0]

    detail_url = f"https://finance.yahoo.co.jp/quote/{ticker}"
    headers = {
        "User-Agent": "Mozilla/5.0"
    }
    res = requests.get(detail_url, headers=headers)
    soup = BeautifulSoup(res.text, "lxml")

    rows = soup.select("table tbody tr")
    info_map = {}

    try:
        for row in rows:
            th = row.select_one("th")
            td = row.select_one("td")
            if th and td:
                label = th.text.strip()
                value = td.text.strip()
                if "前日終値" in label:
                    info_map["prev_close"] = value
                elif "始値" in label:
                    info_map["open"] = value
                elif "高値" in label:
                    info_map["high"] = value
                elif "安値" in label:
                    info_map["low"] = value
                elif "出来高" in label:
                    info_map["volume"] = value
                elif "売買代金" in label:
                    info_map["value"] = value
                elif "値幅制限" in label:
                    info_map["range"] = value
    except Exception as e:
        return {"error": f"{company_name} の株価情報を解析できませんでした。"}

    return {
        "company_name": company_name,
        **info_map,
        "detail_url": detail_url
    }

# フォローイベントのハンドラを追加
@handler.add(FollowEvent)
def handle_follow(event):
    welcome_message = "まずはあなたの投資レベルを教えてください！"
    reply = TextSendMessage(
        text=welcome_message,
        quick_reply=QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="初心者", text="レベル:初心者")),
                QuickReplyButton(action=MessageAction(label="中級者", text="レベル:中級者")),
                QuickReplyButton(action=MessageAction(label="上級者", text="レベル:上級者")),
            ]
        )
    )
    line_bot_api.reply_message(event.reply_token, reply)

# 初心者向けの説明メッセージを定数として定義
BEGINNER_GUIDE = (
    "[投資の基本]\n"
    "1.　投資ってなに？\n"
    "会社の株などを購入し、成長したときに利益（リターン）を得る仕組みのことです！企業の「応援団」になって、そのお礼として配当や株価上昇の利益をもらう感じ！\n\n"
    "2.　投資のリスク\n"
    "値段（株価）はすぐに変わるから、損することもあります。でも、「分散投資」や「長期投資」を意識すればリスクは減らせます！\n\n"
    "[投資のやり方]\n"
    "ステップ①：証券口座を作る\n"
    "SBI証券や楽天証券など、ネットで無料で開設できます。その際、マイナンバーと本人確認書類が必要です！\n"
    "SBI証券口座開設のリンク：https://go.sbisec.co.jp/account/sogoflow_01.html?id=id01\n"
    "楽天証券口座開設のリンク：https://www.rakuten-sec.co.jp/web/account-flow/\n\n"
    "ステップ②：口座にお金を入れる\n"
    "銀行口座から証券口座にお金を移すにゃ。アプリからも簡単にできます。\n\n"
    "ステップ③：買いたい株を選ぶ\n"
    "最初は知ってる企業（例：トヨタ、任天堂など）を調べると安心！企業の名前をこのBotに送れば、株価や基本情報を調べられます！\n\n"
    "ステップ④：株を買う\n"
    "証券口座のアプリから、買いたい株を選んで購入！少額からでも始められます！\n\n"
    "ステップ⑤：株価のチェックと売却\n"
    "このBotで通知設定すれば、変動もすぐわかります！値上がりしたタイミングで「売り注文」を出せば利益になります！\n\n\n"
    "補足：\n"
    "・100円から投資できる「投資信託」もあります。\n"
    "・毎月コツコツ積み立てる「積立投資」も人気です。\n\n"
    "もっと詳しく知りたい場合は、各証券会社のサイトをチェック！\n"
)

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text
    print("受信したテキスト:", text)
    line_user_id = event.source.user_id

    # 🌟 ユーザーのプロフィール取得
    try:
        profile = line_bot_api.get_profile(line_user_id)
        user_name = profile.display_name
    except Exception as e:
        print("プロフィール取得失敗:", e)
        user_name = "未設定"

    if text.startswith("レベル:"):
        level = text.replace("レベル:", "")
        # Supabaseに保存
        supabase.table("users").upsert({
            "line_user_id": line_user_id,
            "name": user_name,
            "experience": level,
        }, on_conflict=["line_user_id"]).execute()

        print(f"{level}が押されました")
        confirm_message = "投資の基本的な説明を聞きますか？"
        reply = TextSendMessage(
            text=confirm_message,
            quick_reply=QuickReply(
                items=[
                    QuickReplyButton(
                        action=MessageAction(label="説明を聞く", text="基本説明:はい")
                    ),
                    QuickReplyButton(
                        action=MessageAction(label="説明をスキップ", text="基本説明:いいえ")
                    )
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, reply)
        return
    elif text == "基本説明:はい":
        # まず説明を送信
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=BEGINNER_GUIDE)
        )
        # Botの機能を説明
        line_bot_api.push_message(line_user_id, TextSendMessage(
            text="このBotでは以下のことができます！\n\n・気になる企業の株価を調べる\n・AIから投資のアドバイスを受ける\n・株価通知を設定する"
        ))
        # 企業名入力を促す
        line_bot_api.push_message(line_user_id, TextSendMessage(
            text="それでは、通知を受け取りたい企業名を入力してください！（例：トヨタ）"
        ))
    elif text == "基本説明:いいえ":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="わかりました！")
        )
        line_bot_api.push_message(line_user_id, TextSendMessage(
            text="このBotでは以下のことができます！\n\n・気になる企業の株価を調べる\n・AIから投資のアドバイスを受ける\n・株価通知を設定する"
        ))
        line_bot_api.push_message(line_user_id, TextSendMessage(
            text="それでは、通知を受け取りたい企業名を入力してください！（例：トヨタ）"
        ))
    else:
        if text.startswith("候補:"):
            ticker = text.replace("候補:", "")
            detail_url = f"https://finance.yahoo.co.jp/quote/{ticker}"
            headers = {"User-Agent": "Mozilla/5.0"}
            res = requests.get(detail_url, headers=headers)
            soup = BeautifulSoup(res.text, "lxml")

            rows = soup.select("table tbody tr")
            info_map = {}
            for row in rows:
                th = row.select_one("th")
                td = row.select_one("td")
                if th and td:
                    label = th.text.strip()
                    value = td.text.strip()
                    if "前日終値" in label:
                        info_map["prev_close"] = value
                    elif "始値" in label:
                        info_map["open"] = value
                    elif "高値" in label:
                        info_map["high"] = value
                    elif "安値" in label:
                        info_map["low"] = value
                    elif "出来高" in label:
                        info_map["volume"] = value
                    elif "売買代金" in label:
                        info_map["value"] = value
                    elif "値幅制限" in label:
                        info_map["range"] = value

            reply_text = (
                f"【{ticker}】\n"
                f"前日終値: {info_map.get('prev_close')}\n"
                f"始値: {info_map.get('open')}\n"
                f"高値: {info_map.get('high')}\n"
                f"安値: {info_map.get('low')}\n"
                f"出来高: {info_map.get('volume')}\n"
                f"売買代金: {info_map.get('value')}\n"
                f"値幅制限: {info_map.get('range')}\n"
                f"詳細: {detail_url}"
            )
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
        else:
            candidates = get_ticker_candidates(text)
            if not candidates:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"{text} に対応する証券コードが見つかりませんでした。")
                )
                return

            if len(candidates) == 1:
                ticker, _ = candidates[0]
                detail_url = f"https://finance.yahoo.co.jp/quote/{ticker}"
                headers = {"User-Agent": "Mozilla/5.0"}
                res = requests.get(detail_url, headers=headers)
                soup = BeautifulSoup(res.text, "lxml")

                rows = soup.select("table tbody tr")
                info_map = {}
                for row in rows:
                    th = row.select_one("th")
                    td = row.select_one("td")
                    if th and td:
                        label = th.text.strip()
                        value = td.text.strip()
                        if "前日終値" in label:
                            info_map["prev_close"] = value
                        elif "始値" in label:
                            info_map["open"] = value
                        elif "高値" in label:
                            info_map["high"] = value
                        elif "安値" in label:
                            info_map["low"] = value
                        elif "出来高" in label:
                            info_map["volume"] = value
                        elif "売買代金" in label:
                            info_map["value"] = value
                        elif "値幅制限" in label:
                            info_map["range"] = value

                info_text = (
                    f"【{ticker}】\n"
                    f"前日終値: {info_map.get('prev_close')}\n"
                    f"始値: {info_map.get('open')}\n"
                    f"高値: {info_map.get('high')}\n"
                    f"安値: {info_map.get('low')}\n"
                    f"出来高: {info_map.get('volume')}\n"
                    f"売買代金: {info_map.get('value')}\n"
                    f"値幅制限: {info_map.get('range')}\n"
                    f"詳細: {detail_url}"
                )
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=info_text)
                )
            else:
                quick_items = [
                    QuickReplyButton(action=MessageAction(label=name, text=f"候補:{code}"))
                    for code, name in candidates
                ]
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="候補が複数見つかりました。該当する企業を選んでくださいにゃ！",
                        quick_reply=QuickReply(items=quick_items)
                    )
                )
