import os
import sys
# 最初期ログ（起動確認用）
print("[BOOT] app.py import start", flush=True)
def _excepthook(exc_type, exc, tb):
    import traceback
    print("[BOOT][FATAL] Uncaught exception:", exc_type.__name__, str(exc), flush=True)
    traceback.print_tb(tb)
sys.excepthook = _excepthook
import logging
import json
import urllib3
import secrets
import sys as _sys_for_logging

# ログ設定（stdoutとstderrの両方に出力）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s:%(lineno)d - %(message)s",
    handlers=[
        logging.StreamHandler(_sys_for_logging.stdout),
        logging.StreamHandler(_sys_for_logging.stderr)  # gunicornはstderrに出力
    ],
    force=True  # 既存のハンドラを上書き
)

# gunicornのログ設定を統合
gunicorn_error = logging.getLogger("gunicorn.error")
if gunicorn_error.handlers:
    root = logging.getLogger()
    root.handlers = gunicorn_error.handlers
    root.setLevel(gunicorn_error.level)
    print("[BOOT] Gunicorn logger handlers detected", flush=True)

 

from flask import Flask, request, abort, render_template_string, redirect, url_for, session, Response, make_response
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from config import Config
from datetime import datetime
from google_auth_oauthlib.flow import Flow
from werkzeug.middleware.proxy_fix import ProxyFix
import threading
import schedule
import time

# 危険なモジュールは遅延/防御的に読み込む
LineBotHandler = None
try:
    from line_bot_handler import LineBotHandler  # noqa: F401
except Exception as e:
    logger = logging.getLogger(__name__)
    logger.error("line_bot_handler import failed: %s", e)

DBHelper = None
try:
    from db import DBHelper  # noqa: F401
except Exception as e:
    logger = logging.getLogger(__name__)
    logger.error("db import failed: %s", e)

def _lazy_ai_service():
    from ai_service import AIService  # noqa: WPS433
    return AIService()

def _lazy_send_daily_agenda():
    from send_daily_agenda import send_daily_agenda as _send  # noqa: WPS433
    return _send

# GOOGLE_CREDENTIALS_FILEの自動判定（JSON or パス）
# 注意: Configをimportしてから使用（Config.validate_config()より前でも安全）
try:
    GOOGLE_CREDENTIALS_FILE_ENV = os.environ.get("GOOGLE_CREDENTIALS_FILE")
    if GOOGLE_CREDENTIALS_FILE_ENV:
        try:
            parsed = json.loads(GOOGLE_CREDENTIALS_FILE_ENV)
            with open("credentials.json", "w") as f:
                json.dump(parsed, f)
            os.environ["GOOGLE_CREDENTIALS_PATH"] = "credentials.json"
            print("[BOOT] Google認証ファイルをJSON形式からcredentials.jsonに変換しました", flush=True)
        except json.JSONDecodeError:
            if os.path.exists(GOOGLE_CREDENTIALS_FILE_ENV):
                os.environ["GOOGLE_CREDENTIALS_PATH"] = GOOGLE_CREDENTIALS_FILE_ENV
                print(f"[BOOT] Google認証ファイルパスを使用: {GOOGLE_CREDENTIALS_FILE_ENV}", flush=True)
            else:
                # Config.GOOGLE_CREDENTIALS_FILEを安全に取得
                try:
                    fallback = getattr(Config, "GOOGLE_CREDENTIALS_FILE", "credentials.json")
                except Exception:
                    fallback = "credentials.json"
                os.environ["GOOGLE_CREDENTIALS_PATH"] = fallback
                print("[BOOT][ERROR] GOOGLE_CREDENTIALS_FILE が不正。フォールバックに切替えました", flush=True)
    else:
        # Config.GOOGLE_CREDENTIALS_FILEを安全に取得
        try:
            default_path = getattr(Config, "GOOGLE_CREDENTIALS_FILE", None) or os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        except Exception:
            default_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        os.environ["GOOGLE_CREDENTIALS_PATH"] = default_path
        print(f"[BOOT] デフォルトのGoogle認証ファイルパスを使用: {os.environ['GOOGLE_CREDENTIALS_PATH']}", flush=True)
except Exception as e:
    # 初期化エラーでも起動は継続
    print(f"[BOOT][WARNING] Google認証ファイル設定エラー（起動継続）: {e}", flush=True)
    import traceback
    traceback.print_exc()
    os.environ.setdefault("GOOGLE_CREDENTIALS_PATH", "credentials.json")

# ログ設定
logger = logging.getLogger(__name__)

# Flaskアプリの初期化
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

# ProxyFixを追加（Railway対応）
try:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    print("[BOOT] ProxyFix設定完了", flush=True)
except Exception as e:
    print(f"[BOOT][WARNING] ProxyFix設定エラー（起動継続）: {e}", flush=True)

# 設定の検証（失敗しても起動は継続）
try:
    Config.validate_config()
    logger.info("設定の検証が完了しました")
except Exception as e:
    logger.error(f"設定エラー（起動は継続）: {e}")
    logger.error("暫定運用: 必須環境変数が不足していますが、疎通確認のため起動を継続します")

# BOOT_MODEの確認（lightモードでは軽量起動）
BOOT_MODE = os.environ.get('BOOT_MODE', 'full').lower()
DISABLE_LINE = os.environ.get('DISABLE_LINE', '0') == '1'
DISABLE_DB = os.environ.get('DISABLE_DB', '0') == '1'

print(f"[BOOT] BOOT_MODE: {BOOT_MODE}", flush=True)
print(f"[BOOT] DISABLE_LINE: {DISABLE_LINE}", flush=True)
print(f"[BOOT] DISABLE_DB: {DISABLE_DB}", flush=True)
logger.info(f"BOOT_MODE: {BOOT_MODE}, DISABLE_LINE: {DISABLE_LINE}, DISABLE_DB: {DISABLE_DB}")

# BASE_URLの事前チェック（OAuth redirect_uri mismatch予防）
if BOOT_MODE != 'light':
    try:
        base_url = os.getenv('BASE_URL')
        if not base_url:
            logger.warning("BASE_URL未設定（OAuth認証に必要）")
        else:
            if not base_url.startswith("https://"):
                logger.error("BASE_URLはhttpsで始まる必要があります（OAuth認証のため）")
            if base_url.endswith("/"):
                logger.error("BASE_URL末尾の/は不要です（redirect_uri mismatchの原因になります）")
            if base_url.startswith("https://") and not base_url.endswith("/"):
                logger.info("BASE_URLは妥当です")
    except Exception as e:
        logger.warning(f"BASE_URLの確認: {e}")

# LINEボットハンドラーを初期化（失敗しても起動は継続）
line_ready = False
handler = None
line_bot_handler = None
if BOOT_MODE == 'light' or DISABLE_LINE:
    logger.info("LINEボットハンドラーの初期化をスキップしました（BOOT_MODE=light または DISABLE_LINE=1）")
else:
    try:
        line_bot_handler = LineBotHandler()
        handler = line_bot_handler.get_handler()
        line_ready = True
        logger.info("LINEボットハンドラーの初期化が完了しました")
    except Exception as e:
        logger.error(f"LINEボットハンドラーの初期化に失敗しました（起動は継続）: {e}")
        import traceback
        traceback.print_exc()

# セキュリティのためAPIキーなどの機密情報はログに出力しない

# DBヘルパーの初期化（失敗しても起動継続）
db_ready = False
db_helper = None
if BOOT_MODE == 'light' or DISABLE_DB:
    logger.info("DB初期化をスキップしました（BOOT_MODE=light または DISABLE_DB=1）")
else:
    try:
        db_helper = DBHelper()
        db_ready = True
        logger.info("DB初期化が完了しました")
    except Exception as e:
        logger.error(f"DB初期化に失敗しました（起動は継続）: {e}")
        import traceback
        traceback.print_exc()

# 定期実行スケジューラーの設定（バックアップ用）
def run_scheduler_backup():
    """定期実行スケジューラーを実行（バックアップ用）"""
    import pytz
    from datetime import datetime
    
    jst = pytz.timezone('Asia/Tokyo')
    
    # タイムゾーンを明示的に設定
    os.environ['TZ'] = 'Asia/Tokyo'
    try:
        time.tzset()  # Unix系OSでタイムゾーンを再設定
    except AttributeError:
        # Windowsではtzsetが存在しないためスキップ
        pass
    
    # スケジュールを設定（JST 19:00）
    send_func = _lazy_send_daily_agenda()
    schedule.every().day.at("19:00").do(send_func)
    
    # 現在時刻をログ出力
    now_utc = datetime.now(pytz.UTC)
    now_jst = datetime.now(jst)
    logger.info(f"バックアップ用スケジューラーを開始しました（毎日19:00 JSTに明日の予定を送信）")
    logger.info(f"現在時刻（UTC）: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"現在時刻（JST）: {now_jst.strftime('%Y-%m-%d %H:%M:%S')}")
    
    last_execution_date = None
    
    while True:
        schedule.run_pending()
        time.sleep(60)  # 1分ごとにチェック
        
        # 毎時間ログ出力（デバッグ用）
        current_time_jst = datetime.now(jst)
        if current_time_jst.minute == 0:
            logger.info(f"バックアップスケジューラー実行中... 現在時刻（JST）: {current_time_jst.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 19:00になったら手動で実行をトリガー（念のため）
            if current_time_jst.hour == 19 and last_execution_date != current_time_jst.date():
                logger.info("19:00を検出。手動で予定送信をトリガーします")
                try:
                    send_func()
                    last_execution_date = current_time_jst.date()
                    logger.info("手動トリガーによる予定送信が完了しました")
                except Exception as e:
                    logger.error(f"手動トリガーによる予定送信でエラー: {e}")

# バックグラウンドでスケジューラーを開始（cronジョブが動作しない場合のバックアップ）
# lightモードではスケジューラーを起動しない
if BOOT_MODE == 'light':
    logger.info("BOOT_MODE=light のためスケジューラー未起動")
elif os.environ.get("RUN_SCHEDULER") == "1":
    scheduler_thread = threading.Thread(target=run_scheduler_backup, daemon=True)
    scheduler_thread.start()
    logger.info("バックアップ用定期実行スケジューラーを開始しました")
else:
    logger.info("RUN_SCHEDULER!=1 のためスケジューラー未起動")

@app.route("/callback", methods=['POST'])
def callback():
    """LINE Webhookのコールバックエンドポイント（即ACK - 1秒以内に200返却）"""
    from flask import make_response
    
    # リクエストヘッダーからX-Line-Signatureを取得
    signature = request.headers.get('X-Line-Signature')
    if not signature:
        return make_response(("bad request", 400))
    
    # リクエストボディを取得
    body = request.get_data(as_text=True)
    logger.info("Webhook received (len=%s)", len(body))
    
    # 軽量JSONパース（失敗しても後段でSDKがはじくのでOK）
    try:
        payload = json.loads(body)
    except Exception:
        payload = {}

    # Verify用の高速レーン: events=[] のみ署名検証して即200
    if isinstance(payload, dict) and payload.get("events") == []:
        try:
            WebhookHandler(os.environ.get("LINE_CHANNEL_SECRET")).handle(body, signature)
        except Exception as e:
            logger.debug("Verify signature NG: %s", e)
            return make_response(("bad signature", 400))
        return "OK"
    
    # 非同期処理でLINEメッセージを処理（即座に200を返す）
    def _process():
        try:
            if not line_ready or not handler:
                return
            handler.handle(body, signature)
        except InvalidSignatureError:
            logger.error("署名検証に失敗しました")
        except Exception as e:
            logger.error("callback async 例外: %s", e)
    
    # バックグラウンドで処理を実行（daemon=Trueで即座に200を返す）
    threading.Thread(target=_process, daemon=True).start()
    
    # 即座に200を返す（LINEの1秒応答要件に対応）
    return "OK"

if line_ready and handler:
    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        """テキストメッセージを処理"""
        try:
            logger.info(f"メッセージを受信: {event.message.text}")
            
            # メッセージを処理してレスポンスを取得
            response = line_bot_handler.handle_message(event)
            
            # LINEにメッセージを送信（SSLエラー対応のリトライ機能付き）
            max_retries = 5
            retry_delay = 2  # 秒
            
            for attempt in range(max_retries):
                try:
                    line_bot_handler.line_bot_api.reply_message(
                        event.reply_token,
                        response
                    )
                    logger.info("メッセージの処理が完了しました")
                    break
                except Exception as send_error:
                    error_msg = str(send_error)
                    logger.warning(f"メッセージ送信試行 {attempt + 1}/{max_retries} でエラー: {error_msg}")
                    
                    # SSLエラーの場合は特別な処理
                    if "SSL SYSCALL error" in error_msg or "EOF detected" in error_msg:
                        logger.info(f"SSLエラーを検出、{retry_delay}秒後にリトライします")
                        logger.info(f"SSLエラー詳細: {type(send_error).__name__}: {error_msg}")
                        import time
                        time.sleep(retry_delay)
                        
                        if attempt < max_retries - 1:
                            retry_delay *= 2  # 指数バックオフ（次の試行用）
                            logger.info(f"次のリトライまでの待機時間: {retry_delay}秒")
                            continue
                        else:
                            logger.error("SSLエラーが継続し、最大リトライ回数に達しました")
                            raise send_error
                    
                    # その他のエラーの場合
                    if attempt == max_retries - 1:
                        logger.error(f"最大リトライ回数に達しました: {send_error}")
                        raise send_error
                    
                    import time
                    time.sleep(1)  # 1秒待機してからリトライ
            
        except Exception as e:
            logger.error(f"メッセージ処理でエラーが発生しました: {e}")
            # エラーが発生した場合はエラーメッセージを送信
            try:
                # エラーメッセージ送信時もリトライ機能を適用
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        line_bot_handler.line_bot_api.reply_message(
                            event.reply_token,
                            TextSendMessage(text="申し訳ございません。エラーが発生しました。しばらく時間をおいて再度お試しください。")
                        )
                        logger.info("エラーメッセージの送信が完了しました")
                        break
                    except Exception as reply_error:
                        logger.warning(f"エラーメッセージ送信試行 {attempt + 1}/{max_retries} でエラー: {reply_error}")
                        if attempt == max_retries - 1:
                            logger.error(f"エラーメッセージの送信に失敗しました: {reply_error}")
                        else:
                            import time
                            time.sleep(1)
            except Exception as reply_error:
                logger.error(f"エラーメッセージの送信に失敗しました: {reply_error}")
else:
    logger.warning("LINEハンドラ未準備のため、MessageEventハンドラを登録しません")

# gunicorn起動時の確認ログ（モジュールimport完了の確認）
print("[BOOT] app.py module loaded successfully", flush=True)
logger.info("app.py module loaded successfully")

@app.route("/", methods=['GET'])
def index():
    """ヘルスチェック用エンドポイント"""
    return "LINE Calendar Bot is running!"

@app.route("/_debug/ping", methods=['GET'])
def _ping():
    """疎通確認用の軽いエンドポイント"""
    return "pong", 200

@app.route("/health", methods=['GET'])
def health():
    """ヘルスチェック用エンドポイント"""
    return {
        "status": "healthy",
        "service": "line-calendar-bot",
        "degraded": (not line_ready) or (not db_ready),
        "components": {
            "line": "ready" if line_ready else "not_ready",
            "db": "ready" if db_ready else "not_ready"
        }
    }

@app.route("/test", methods=['GET'])
def test():
    """テスト用エンドポイント"""
    path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    is_google_ok = os.path.exists(path)
    return {
        "message": "LINE Calendar Bot Test",
        "config": {
            "line_configured": bool(Config.LINE_CHANNEL_ACCESS_TOKEN and Config.LINE_CHANNEL_SECRET),
            "openai_configured": bool(Config.OPENAI_API_KEY),
            "google_configured": is_google_ok,
        }
    }

@app.route('/onetime_login', methods=['GET', 'POST'])
def onetime_login():
    """ワンタイムコード認証ページ"""
    from flask import make_response
    if not db_ready or db_helper is None:
        return make_response(("DB not ready", 503))
    
    if request.method == 'GET':
        # ワンタイムコード入力フォームを表示
        html = '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Google Calendar 認証</title>
            <meta charset="utf-8">
            <style>
                body { font-family: Arial, sans-serif; max-width: 500px; margin: 50px auto; padding: 20px; }
                .form-group { margin-bottom: 20px; }
                label { display: block; margin-bottom: 5px; font-weight: bold; }
                input[type="text"] { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; }
                button { background: #4285f4; color: white; padding: 12px 24px; border: none; border-radius: 4px; cursor: pointer; }
                button:hover { background: #3367d6; }
                .error { color: red; margin-top: 10px; }
                .success { color: green; margin-top: 10px; }
            </style>
        </head>
        <body>
            <h1>Google Calendar 認証</h1>
            <p>LINE BotでGoogle Calendarを利用するために認証が必要です。</p>
            <form method="POST">
                <div class="form-group">
                    <label for="code">ワンタイムコード:</label>
                    <input type="text" id="code" name="code" placeholder="8文字のコードを入力" required>
                </div>
                <button type="submit">認証を開始</button>
            </form>
            {% if error %}
            <div class="error">{{ error }}</div>
            {% endif %}
            {% if success %}
            <div class="success">{{ success }}</div>
            {% endif %}
        </body>
        </html>
        '''
        return render_template_string(html, error=None, success=None)
    
    elif request.method == 'POST':
        code = request.form.get('code', '').strip().upper()
        
        # ワンタイムコードを検証
        line_user_id = db_helper.verify_onetime_code(code)
        if not line_user_id:
            html = '''
            <!DOCTYPE html>
            <html>
            <head>
                <title>認証エラー</title>
                <meta charset="utf-8">
                <style>
                    body { font-family: Arial, sans-serif; max-width: 500px; margin: 50px auto; padding: 20px; }
                    .error { color: red; margin: 20px 0; }
                    .back-link { margin-top: 20px; }
                </style>
            </head>
            <body>
                <h1>認証エラー</h1>
                <div class="error">
                    無効なワンタイムコードです。<br>
                    コードが正しいか、有効期限が切れていないか確認してください。
                </div>
                <div class="back-link">
                    <a href="/onetime_login">戻る</a>
                </div>
            </body>
            </html>
            '''
            return render_template_string(html)
        
        # ワンタイムコードを使用済みにマーク
        db_helper.mark_onetime_used(code)
        
        try:
            # Google OAuth認証フローを開始（Flow使用）
            SCOPES = ['https://www.googleapis.com/auth/calendar']
            path = os.environ.get('GOOGLE_CREDENTIALS_PATH', 'credentials.json')
            base_url = os.getenv('BASE_URL')
            if not base_url:
                raise ValueError('BASE_URL環境変数が設定されていません')
            redirect_uri = base_url.rstrip('/') + '/oauth2callback'
            
            flow = Flow.from_client_secrets_file(
                path,
                scopes=SCOPES,
                redirect_uri=redirect_uri
            )
            
            # デバッグ用ログ（機微情報は出さない）
            logger.debug('[DEBUG] OAuthフロー初期化済み（BASE_URL/Redirect URIは非表示）')
            
            auth_url, state = flow.authorization_url(
                access_type='offline',
                include_granted_scopes='true',
                prompt='consent'
            )
            # stateとline_user_idをDBに保存
            db_helper.save_oauth_state(state, line_user_id)
            return redirect(auth_url)
        except Exception as e:
            logger.error(f"Google OAuth認証エラー: {e}")
            html = '''
            <!DOCTYPE html>
            <html>
            <head>
                <title>認証エラー</title>
                <meta charset="utf-8">
                <style>
                    body { font-family: Arial, sans-serif; max-width: 500px; margin: 50px auto; padding: 20px; }
                    .error { color: red; margin: 20px 0; }
                </style>
            </head>
            <body>
                <h1>認証エラー</h1>
                <div class="error">
                    Google認証の初期化に失敗しました。<br>
                    しばらく時間をおいて再度お試しください。
                </div>
            </body>
            </html>
            '''
            return render_template_string(html)

@app.route('/oauth2callback')
def oauth2callback():
    """Google OAuth認証コールバック"""
    from flask import make_response
    if not db_ready or db_helper is None:
        return make_response(("DB not ready", 503))
    
    try:
        # stateからline_user_idを取得
        state = request.args.get('state')
        line_user_id = db_helper.get_line_user_id_by_state(state)
        if not line_user_id:
            return make_response("認証セッションが無効です", 400)
        # 新たにflowを生成（Flow使用、monkey patch撤去）
        SCOPES = ['https://www.googleapis.com/auth/calendar']
        path = os.environ.get('GOOGLE_CREDENTIALS_PATH', 'credentials.json')
        base_url = os.getenv('BASE_URL')
        if not base_url:
            raise ValueError('BASE_URL環境変数が設定されていません')
        redirect_uri = base_url.rstrip('/') + '/oauth2callback'
        
        flow = Flow.from_client_secrets_file(
            path,
            scopes=SCOPES,
            redirect_uri=redirect_uri
        )
        
        # デバッグ用ログ（機微情報は出さない）
        logger.debug('[DEBUG] oauth2callback 実行（BASE_URL/Redirect URIは非表示）')

        # Googleからのコールバックを用いてトークンを取得（モンキーパッチ不要）
        flow.fetch_token(authorization_response=request.url)
        
        creds = flow.credentials
        # JSON 形式で保存（推奨）
        db_helper.save_google_token_json(line_user_id, creds.to_json())
        
        # ワンタイムコードは state 起点で使用済みにするなど、一貫したAPIに統一
        db_helper.mark_onetime_used_by_state(state)
        # 認証完了画面
        html = "<h2>Google認証が完了しました。LINEに戻って操作を続けてください。</h2>"
        return make_response(html, 200)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return make_response(f"OAuth2コールバックエラー: {e}", 400)

@app.route('/debug/ai_test', methods=['GET', 'POST'])
def debug_ai_test():
    """AI抽出機能のデバッグ用エンドポイント"""
    from flask import render_template_string, request, jsonify
    
    if request.method == 'POST':
        try:
            text = request.form.get('text', '')
            if not text:
                return jsonify({"error": "テキストが入力されていません"})
            
            # AIサービスでテスト
            ai_service = _lazy_ai_service()
            result = ai_service.extract_dates_and_times(text)
            
            return jsonify({
                "input": text,
                "result": result,
                "success": True
            })
            
        except Exception as e:
            return jsonify({
                "error": str(e),
                "success": False
            })
    
    # GETリクエストの場合はテストフォームを表示
    test_form = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>AI抽出テスト</title>
        <meta charset="utf-8">
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .container { max-width: 600px; margin: 0 auto; }
            textarea { width: 100%; height: 100px; padding: 10px; margin: 10px 0; }
            button { background: #007bff; color: white; padding: 10px 20px; border: none; cursor: pointer; }
            .result { background: #f8f9fa; padding: 15px; margin: 10px 0; border-radius: 5px; }
            pre { white-space: pre-wrap; word-wrap: break-word; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>AI抽出機能テスト</h2>
            <form id="testForm">
                <label for="text">テストテキスト:</label><br>
                <textarea id="text" name="text" placeholder="例: ・7/10 9-10時&#10;・7/11 9-10時"></textarea><br>
                <button type="submit">テスト実行</button>
            </form>
            <div id="result" class="result" style="display: none;">
                <h3>結果:</h3>
                <pre id="resultContent"></pre>
            </div>
        </div>
        
        <script>
        document.getElementById('testForm').addEventListener('submit', function(e) {
            e.preventDefault();
            
            const text = document.getElementById('text').value;
            const resultDiv = document.getElementById('result');
            const resultContent = document.getElementById('resultContent');
            
            resultContent.textContent = '処理中...';
            resultDiv.style.display = 'block';
            
            fetch('/debug/ai_test', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: 'text=' + encodeURIComponent(text)
            })
            .then(response => response.json())
            .then(data => {
                resultContent.textContent = JSON.stringify(data, null, 2);
            })
            .catch(error => {
                resultContent.textContent = 'エラー: ' + error;
            });
        });
        </script>
    </body>
    </html>
    """
    return render_template_string(test_form)

def _get_agenda_token(req):
    """トークンをヘッダーまたはクエリパラメータから取得（互換性のため両方対応）"""
    return req.headers.get('X-Agenda-Token') or req.args.get('token')

@app.route('/api/send_daily_agenda', methods=['POST'])
def api_send_daily_agenda():
    import os
    from flask import request, jsonify
    if not db_ready:
        return jsonify({'status': 'error', 'message': 'DB not ready'}), 503
    secret_token = os.environ.get('DAILY_AGENDA_SECRET_TOKEN')
    req_token = _get_agenda_token(request)
    if not secret_token or not req_token or not secrets.compare_digest(req_token, secret_token):
        return jsonify({'status': 'error', 'message': 'Invalid or missing token'}), 403
    try:
        _lazy_send_daily_agenda()()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/debug_users', methods=['GET'])
def api_debug_users():
    import os
    from flask import request, jsonify
    if not db_ready:
        return jsonify({'status': 'error', 'message': 'DB not ready'}), 503
    secret_token = os.environ.get('DAILY_AGENDA_SECRET_TOKEN')
    req_token = _get_agenda_token(request)
    if not secret_token or not req_token or not secrets.compare_digest(req_token, secret_token):
        return jsonify({'status': 'error', 'message': 'Invalid or missing token'}), 403
    from db import DBHelper
    db = DBHelper()
    c = db.conn.cursor()
    # セキュリティ: line_user_idのみ返す（メタ情報の露出を防ぐ）
    c.execute('SELECT line_user_id FROM users')
    rows = c.fetchall()
    return jsonify({'users': [row[0] for row in rows]})

@app.route('/api/test_daily_agenda', methods=['POST'])
def api_test_daily_agenda():
    """手動で明日の予定一覧送信をテスト"""
    import os
    from flask import request, jsonify
    if not db_ready:
        return jsonify({'status': 'error', 'message': 'DB not ready'}), 503
    secret_token = os.environ.get('DAILY_AGENDA_SECRET_TOKEN')
    req_token = _get_agenda_token(request)
    if not secret_token or not req_token or not secrets.compare_digest(req_token, secret_token):
        return jsonify({'status': 'error', 'message': 'Invalid or missing token'}), 403
    try:
        _lazy_send_daily_agenda()()
        return jsonify({'status': 'ok', 'message': '明日の予定一覧を送信しました'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    
    # 起動ログを明示的に出力（stdout/stderrに確実に出力）
    print("=" * 60, flush=True)
    print("[BOOT] LINE Calendar Bot を起動しています...", flush=True)
    print(f"[BOOT] PORT: {port}", flush=True)
    print(f"[BOOT] Python: {sys.version}", flush=True)
    print("=" * 60, flush=True)
    
    logger.info("=" * 60)
    logger.info("LINE Calendar Bot を起動しています...")
    logger.info(f"PORT: {port}")
    logger.info(f"Python: {sys.version}")
    
    # SSL設定の確認
    import ssl
    logger.info(f"SSL バージョン: {ssl.OPENSSL_VERSION}")
    names = getattr(ssl, "_PROTOCOL_NAMES", None)
    logger.info(f"利用可能なSSLプロトコル: {names or 'N/A'}")
    
    # ネットワーク設定の確認
    import requests
    logger.info(f"Requests バージョン: {requests.__version__}")
    logger.info(f"urllib3 バージョン: {urllib3.__version__}")
    
    # コンポーネントの状態を確認
    logger.info(f"LINE Bot Handler: {'ready' if line_ready else 'not ready'}")
    logger.info(f"Database: {'ready' if db_ready else 'not ready'}")
    
    logger.info("=" * 60)
    logger.info(f"サーバーを起動します: http://0.0.0.0:{port}")
    logger.info("=" * 60)
    print(f"[BOOT] サーバーを起動します: http://0.0.0.0:{port}", flush=True)
    
    # 本番は gunicorn 起動（gunicorn app:app）推奨。デバッグはローカルのみ
    try:
        app.run(debug=False, host='0.0.0.0', port=port)
    except Exception as e:
        logger.error(f"サーバー起動エラー: {e}")
        print(f"[BOOT][FATAL] サーバー起動エラー: {e}", flush=True)
        raise 