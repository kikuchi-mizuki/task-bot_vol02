from datetime import datetime, timedelta
from calendar_service import GoogleCalendarService
from db import DBHelper
from linebot import LineBotApi
from linebot.models import TextSendMessage
from config import Config
import logging
logging.basicConfig(level=logging.INFO)

def format_rich_agenda(events_info, is_tomorrow=False):
    if not events_info or not events_info[0]['events']:
        return "✅明日の予定はありません！" if is_tomorrow else "✅今日の予定はありません！"

    date = events_info[0]['date']
    dt = datetime.strptime(date, "%Y-%m-%d")
    weekday = "月火水木金土日"[dt.weekday()]
    
    # 画像の形式に合わせた表示
    header = f"✅明日の予定です！\n\n📅 {dt.strftime('%Y/%m/%d')} ({weekday})\n━━━━━━━━━━"
    lines = []
    for i, event in enumerate(events_info[0]['events'], 1):
        title = event['title']
        start = datetime.fromisoformat(event['start']).strftime('%H:%M')
        end = datetime.fromisoformat(event['end']).strftime('%H:%M')
        lines.append(f"{i}. {title}\n⏰ {start}~{end}\n")
    footer = "━━━━━━━━━━"
    return f"{header}\n" + "\n".join(lines) + footer

def send_daily_agenda():
    logging.info(f"[DEBUG] 日次予定送信開始: {datetime.now()}")
    db = DBHelper()
    # 追加デバッグ: usersテーブル全件ダンプ
    c = db.conn.cursor()
    try:
        c.execute("SELECT 1 FROM information_schema.tables WHERE table_name='users'")
        if c.fetchone():
            logging.info('[DEBUG] usersテーブルは存在します')
        else:
            logging.info('[DEBUG] usersテーブルは存在しません')
    except Exception as e:
        logging.error(f'[DEBUG] usersテーブル存在確認クエリエラー: {e}')
    try:
        c.execute('SELECT line_user_id, LENGTH(google_token), created_at, updated_at FROM users')
        users = c.fetchall()
        if users:
            logging.info(f'[DEBUG] usersテーブル全件: {users}')
        else:
            logging.info('[DEBUG] usersテーブルは空です')
    except Exception as e:
        logging.error(f'[DEBUG] usersテーブル全件取得エラー: {e}')
    calendar_service = GoogleCalendarService()
    line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
    tomorrow = datetime.now().date() + timedelta(days=1)
    logging.info(f"[DEBUG] 明日の日付: {tomorrow}")
    user_ids = db.get_all_user_ids()  # 認証済みユーザーのみ返すようにDBHelperを調整
    logging.info(f"[DEBUG] 送信対象ユーザー: {user_ids}")

    for user_id in user_ids:
        try:
            events_info = calendar_service.get_events_for_dates([tomorrow], user_id)
            logging.info(f"[DEBUG] ユーザー: {user_id} の取得した予定: {events_info}")
            message = format_rich_agenda(events_info, is_tomorrow=True)
            logging.info(f"[DEBUG] 送信先: {user_id}, メッセージ: {message}")
            line_bot_api.push_message(user_id, TextSendMessage(text=message))
            logging.info(f"[DEBUG] ユーザー {user_id} への送信完了")
        except Exception as e:
            logging.error(f"[ERROR] ユーザー {user_id} への送信中にエラー: {e}")
            # 認証エラー時はLINEで再認証案内を送信
            onetime_code = db.generate_onetime_code(user_id)
            auth_message = (
                "Googleカレンダー連携の認証が切れています。\n"
                "下記URLから再認証をお願いします。\n\n"
                f"🔐 ワンタイムコード: {onetime_code}\n\n"
                "https://task-bot-production.up.railway.app/onetime_login\n"
                "（上記ページでワンタイムコードを入力してください）"
            )
            try:
                line_bot_api.push_message(user_id, TextSendMessage(text=auth_message))
                logging.info(f"[DEBUG] ユーザー {user_id} に再認証案内を送信（ワンタイムコード付き）")
            except Exception as e2:
                logging.error(f"[ERROR] ユーザー {user_id} への再認証案内送信エラー: {e2}")
    
    logging.info(f"[DEBUG] 日次予定送信完了: {datetime.now()}")

if __name__ == "__main__":
    send_daily_agenda() 