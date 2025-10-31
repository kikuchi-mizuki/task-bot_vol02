from datetime import datetime, timedelta
from calendar_service import GoogleCalendarService
from db import DBHelper
from linebot import LineBotApi
from linebot.models import TextSendMessage
from config import Config
import logging
import pytz
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
    # 日本時間で明日の日付を計算
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)
    tomorrow = now_jst.date() + timedelta(days=1)
    
    logging.info(f"[DEBUG] 日次予定送信開始: {now_jst.strftime('%Y-%m-%d %H:%M:%S')} (JST)")
    logging.info(f"[DEBUG] 明日の日付: {tomorrow}")
    
    db = None
    try:
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
        
        # LINE Bot APIを初期化（カスタムセッション設定付き）
        line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
        
        # カスタムセッション設定を適用（line_bot_handler.pyと同じ設定）
        try:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            
            # リトライ戦略を設定
            retry_strategy = Retry(
                total=5,
                backoff_factor=2,
                status_forcelist=[429, 500, 502, 503, 504, 520, 521, 522, 523, 524],
                allowed_methods=["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE", "POST"],
                raise_on_status=False,
            )
            
            # アダプターを設定
            adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=20)
            
            # カスタムセッションを設定
            session = requests.Session()
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            session.timeout = (15, 45)  # (接続タイムアウト, 読み取りタイムアウト)
            
            # LINE Bot SDKの内部セッションを置き換え
            line_bot_api._session = session
            logging.info("[DEBUG] LINE Bot APIにカスタムセッション設定を適用しました")
        except Exception as session_error:
            logging.warning(f"[WARNING] カスタムセッション設定の適用に失敗しました（続行）: {session_error}")
        
        user_ids = db.get_all_user_ids()  # 認証済みユーザーのみ返すようにDBHelperを調整
        logging.info(f"[DEBUG] 送信対象ユーザー: {user_ids}")

        for user_id in user_ids:
            try:
                events_info = calendar_service.get_events_for_dates([tomorrow], user_id)
                logging.info(f"[DEBUG] ユーザー: {user_id} の取得した予定: {events_info}")
                message = format_rich_agenda(events_info, is_tomorrow=True)
                logging.info(f"[DEBUG] 送信先: {user_id}, メッセージ: {message}")
                
                # LINEメッセージ送信（リトライ機能付き）
                max_retries = 3
                retry_delay = 2
                sent = False
                
                for attempt in range(max_retries):
                    try:
                        line_bot_api.push_message(user_id, TextSendMessage(text=message))
                        logging.info(f"[DEBUG] ユーザー {user_id} への送信完了")
                        sent = True
                        break
                    except Exception as send_error:
                        error_msg = str(send_error)
                        logging.warning(f"[WARNING] メッセージ送信試行 {attempt + 1}/{max_retries} でエラー: {error_msg}")
                        
                        if attempt < max_retries - 1:
                            import time
                            time.sleep(retry_delay)
                            retry_delay *= 2
                        else:
                            raise send_error
                
                if not sent:
                    logging.error(f"[ERROR] ユーザー {user_id} へのメッセージ送信に失敗しました")
                    
            except Exception as e:
                logging.error(f"[ERROR] ユーザー {user_id} への送信中にエラー: {e}")
                import traceback
                logging.error(f"[ERROR] 詳細なトレースバック: {traceback.format_exc()}")
                
                # 認証エラー時はLINEで再認証案内を送信
                try:
                    onetime_code = db.generate_onetime_code(user_id)
                    auth_message = (
                        "Googleカレンダー連携の認証が切れています。\n"
                        "下記URLから再認証をお願いします。\n\n"
                        f"🔐 ワンタイムコード: {onetime_code}\n\n"
                        "https://task-bot-production.up.railway.app/onetime_login\n"
                        "（上記ページでワンタイムコードを入力してください）"
                    )
                    
                    # 再認証案内もリトライ機能付きで送信
                    max_retries_auth = 2
                    for attempt_auth in range(max_retries_auth):
                        try:
                            line_bot_api.push_message(user_id, TextSendMessage(text=auth_message))
                            logging.info(f"[DEBUG] ユーザー {user_id} に再認証案内を送信（ワンタイムコード付き）")
                            break
                        except Exception as auth_send_error:
                            logging.warning(f"[WARNING] 再認証案内送信試行 {attempt_auth + 1}/{max_retries_auth} でエラー: {auth_send_error}")
                            if attempt_auth < max_retries_auth - 1:
                                import time
                                time.sleep(1)
                            else:
                                logging.error(f"[ERROR] ユーザー {user_id} への再認証案内送信エラー: {auth_send_error}")
                except Exception as auth_error:
                    logging.error(f"[ERROR] 再認証案内の生成または送信でエラー: {auth_error}")
        
        logging.info(f"[DEBUG] 日次予定送信完了: {now_jst.strftime('%Y-%m-%d %H:%M:%S')} (JST)")
    finally:
        # データベース接続を明示的にクローズ
        if db is not None:
            try:
                db.close()
                logging.info("[DEBUG] データベース接続をクローズしました")
            except Exception as e:
                logging.warning(f"[WARNING] データベース接続のクローズ中にエラー（無視）: {e}")

if __name__ == "__main__":
    send_daily_agenda() 