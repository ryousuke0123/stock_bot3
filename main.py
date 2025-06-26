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
import re

# .envの読み込み
load_dotenv()

app = FastAPI()

# Supabaseクライアントの初期化
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

class UserRegister(BaseModel):
    line_user_id: str
    experience: str

# LINE APIの初期化
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

user_latest_ticker = {}

@app.get("/run-check")
async def run_check():
    check_and_send_notifications()
    return {"status": "通知チェック完了です！"}

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
    for h2 in soup.select("h2.SearchItem__name__1ApM"):
        a = h2.find_parent("a", href=True)  # 企業名が含まれてるリンクを探す
        if a and "/quote/" in a["href"]:
            ticker = a["href"].split("/quote/")[-1]
            name = h2.text.strip()
            if ".T" in ticker:
                candidates.append((ticker, name))
        if len(candidates) >= 5:
            break
    return candidates

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

    # ユーザーのプロフィール取得
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
        # 通知設定: の受信時
        if text.startswith("通知設定:"):
            ticker = text.replace("通知設定:", "")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="通知の条件を下記のように入力してください！\n\n【時間ベースが良い時】\n・毎日５時\n・毎週木曜の17時\n・毎月23日の0時\n【変動ベースが良い時】\n・5％上がった時\n・25%下がった時\n・株価が5000円を超えた時\n・株価が1600円を下回った時")
            )
            return
        # 通知条件の受信時
        elif text.startswith("毎") or ("上が" in text) or ("下が" in text) or ("円を超え" in text) or ("円を下回" in text):
            condition = parse_notification_condition(text)
            ticker = user_latest_ticker.get(line_user_id)
            if not ticker:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="企業名が正しく設定されていません。\n先に企業名を入力してください！")
                )
                return
            # Supabaseに通知設定を保存
            supabase.table("notifications").insert({
                "line_user_id": line_user_id,
                "condition_type": condition.get("type"),
                "condition_detail": str(condition),
                "ticker": ticker,  # add the ticker to enable checking
                "user_name": user_name
            }).execute()

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text=(
                        "通知設定が完了しました！\n"
                        "指定した条件で株価の通知をお届けします！\n\n"
                        "もし設定をやり直したいときは、\n"
                        "「通知取り消し選択」または「初期化」と送ってください！\n\n"
                        "他に通知を受け取りたい企業があったら企業名を入力してください！\n\n"
                        "それでは引き続き「おおきなかぶ」をご活用ください！"
                    )
                )
            )
            return
        # 通知取り消し選択肢表示
        elif text == "通知取り消し選択":
            # 登録済み企業リストを取得
            notifications = supabase.table("notifications").select("ticker").eq("line_user_id", line_user_id).execute().data
            tickers = list(set(n["ticker"] for n in notifications if "ticker" in n))
            if not tickers:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="現在、通知登録されている企業はありません"))
                return
            quick_items = [
                QuickReplyButton(action=MessageAction(label=t, text=f"通知取消:{t}")) for t in tickers
            ]
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="通知を取り消したい企業を選んでください！",
                quick_reply=QuickReply(items=quick_items)
            ))
            return

        elif text.startswith("通知取消:"):
            ticker = text.replace("通知取消:", "")
            supabase.table("notifications").delete().eq("line_user_id", line_user_id).eq("ticker", ticker).execute()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"{ticker} の通知設定を取り消しました！"
            ))
            return

        elif text == "初期化":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="登録された情報が全て取り消され、初期化します。\n本当によろしいですか？",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="はい", text="リセット確認:はい")),
                    QuickReplyButton(action=MessageAction(label="いいえ", text="リセット確認:いいえ")),
                ])
            ))
            return

        elif text == "リセット確認:はい":
            supabase.table("notifications").delete().eq("line_user_id", line_user_id).execute()
            supabase.table("users").delete().eq("line_user_id", line_user_id).execute()

            line_bot_api.push_message(line_user_id, TextSendMessage(
                text="登録情報を全て削除しました！\nはじめからやり直します！"
            ))

            line_bot_api.push_message(line_user_id, TextSendMessage(
                text="まずはあなたの投資レベルを教えてください！",
                quick_reply=QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="初心者", text="レベル:初心者")),
                        QuickReplyButton(action=MessageAction(label="中級者", text="レベル:中級者")),
                        QuickReplyButton(action=MessageAction(label="上級者", text="レベル:上級者")),
                    ]
                )
            ))
            return

        elif text == "リセット確認:いいえ":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="了解です！引き続きBotをご活用ください。"
            ))
            return
        # 通知スキップ
        elif text == "通知スキップ":
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="通知設定をスキップしました！")
            )
            return
        else:
            if text.startswith("候補:"):
                ticker = text.replace("候補:", "")
                user_latest_ticker[line_user_id] = ticker
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
                else:
                    quick_items = [
                        QuickReplyButton(action=MessageAction(label=name, text=f"候補:{code}"))
                        for code, name in candidates
                    ]
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(
                            text="候補が複数見つかりました。該当する企業を選んでください！",
                            quick_reply=QuickReply(items=quick_items)
                        )
                    )
                    return

            user_latest_ticker[line_user_id] = ticker

            detail_url = f"https://finance.yahoo.co.jp/quote/{ticker}"
            try:
                stock = yf.Ticker(ticker)
                info = stock.info

                reply_text = (
                    f"【{ticker}】\n"
                    f"現在値: {info.get('currentPrice', 'N/A')}円\n"
                    f"前日終値: {info.get('previousClose', 'N/A')}円\n"
                    f"始値: {info.get('open', 'N/A')}円\n"
                    f"高値: {info.get('dayHigh', 'N/A')}円\n"
                    f"安値: {info.get('dayLow', 'N/A')}円\n"
                    f"出来高: {info.get('volume', 'N/A')}\n"
                    f"詳細: {detail_url}"
                )
            except Exception as e:
                reply_text = f"株価情報の取得中にエラーが発生しました: {e}"

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
            # 株価通知を受け取るか確認
            line_bot_api.push_message(
                line_user_id,
                TextSendMessage(
                    text="この株価の通知を受け取りますか？",
                    quick_reply=QuickReply(
                        items=[
                            QuickReplyButton(action=MessageAction(label="受け取る", text=f"通知設定:{ticker}")),
                            QuickReplyButton(action=MessageAction(label="受け取らない", text="通知スキップ")),
                        ]
                    )
                )
            )
# 通知条件のテキストから条件を解析する関数
# 通知条件のテキストから条件を解析する関数
def parse_notification_condition(text: str):
    # 毎日
    if "毎日" in text:
        match = re.findall(r"(\d{1,2})時(\d{1,2})?分?", text)
        times = [f"{h}時{m if m else '00'}分" for h, m in match]
        return {"type": "daily", "times": times}

    # 毎週
    elif "毎週" in text:
        match = re.match(r"毎週(.+?)の(\d{1,2})時(\d{1,2})?分?", text)
        if match:
            return {"type": "weekly", "day": match[1], "time": f"{match[2]}時{match[3] if match[3] else '00'}分"}

    # 毎月
    elif "毎月" in text:
        match = re.match(r"毎月(\d{1,2})日の(\d{1,2})時(\d{1,2})?分?", text)
        if match:
            return {"type": "monthly", "day": int(match[1]), "time": f"{match[2]}時{match[3] if match[3] else '00'}分"}

    # 上昇変動通知（%）
    elif re.search(r"\d+%.*上が", text):
        percent = re.search(r"(\d+)%", text).group(1)
        return {"type": "percent_up", "percent": int(percent)}

    # 下降変動通知（%）
    elif re.search(r"\d+%.*下が", text):
        percent = re.search(r"(\d+)%", text).group(1)
        return {"type": "percent_down", "percent": int(percent)}

    # 株価が○円を超えたら通知
    elif "円を超え" in text:
        match = re.search(r"(\d+)円", text)
        return {"type": "price_over", "price": int(match.group(1))}

    # 株価が○円を下回ったら通知
    elif "円を下回" in text:
        match = re.search(r"(\d+)円", text)
        return {"type": "price_under", "price": int(match.group(1))}

    return {"type": "unknown"}

def check_and_send_notifications():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    now_time = f"{now.hour}時{now.minute:02d}分"

    notifications = supabase.table("notifications").select("*").execute().data
    for n in notifications:
        user_id = n["line_user_id"]
        cond_type = n["condition_type"]
        cond_detail = eval(n["condition_detail"])
        ticker = n.get("ticker", "7203.T")

        stock = yf.Ticker(ticker)
        info = stock.info
        current_price = info.get("currentPrice")
        prev_close = info.get("previousClose")

        if cond_type == "daily":
            if now_time in cond_detail.get("times", []):
                send_stock_info(user_id, ticker, info)

        elif cond_type == "weekly":
            weekday_map = ["月", "火", "水", "木", "金", "土", "日"]
            today = weekday_map[now.weekday()]
            if cond_detail["day"] == today and cond_detail["time"] == now_time:
                send_stock_info(user_id, ticker, info)

        elif cond_type == "monthly":
            if now.day == cond_detail["day"] and cond_detail["time"] == now_time:
                send_stock_info(user_id, ticker, info)

        elif cond_type in ["percent_up", "percent_down"]:
            if prev_close and current_price:
                diff_percent = ((current_price - prev_close) / prev_close) * 100
                if cond_type == "percent_up" and diff_percent >= cond_detail["percent"]:
                    send_stock_info(user_id, ticker, info, diff_percent)
                elif cond_type == "percent_down" and diff_percent <= -cond_detail["percent"]:
                    send_stock_info(user_id, ticker, info, diff_percent)

        elif cond_type == "price_over":
            if current_price >= cond_detail["price"]:
                send_stock_info(user_id, ticker, info)

        elif cond_type == "price_under":
            if current_price <= cond_detail["price"]:
                send_stock_info(user_id, ticker, info)

def send_stock_info(user_id, ticker, info, diff_percent=None):
    detail_url = f"https://finance.yahoo.co.jp/quote/{ticker}"
    price_info = (
        f"【{ticker}】\n"
        f"現在値: {info.get('currentPrice', 'N/A')}円\n"
        f"前日終値: {info.get('previousClose', 'N/A')}円\n"
        f"始値: {info.get('open', 'N/A')}円\n"
        f"高値: {info.get('dayHigh', 'N/A')}円\n"
        f"安値: {info.get('dayLow', 'N/A')}円\n"
        f"出来高: {info.get('volume', 'N/A')}\n"
        f"詳細: {detail_url}"
    )

    if diff_percent is not None:
        price_info = f"株価が{'上昇' if diff_percent > 0 else '下降'}しました（{diff_percent:.2f}%）\n\n" + price_info

    line_bot_api.push_message(user_id, TextSendMessage(text=price_info))