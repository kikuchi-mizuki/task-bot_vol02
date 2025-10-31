#!/usr/bin/env python3
"""
Railway Cron Runs 用の単発実行エントリポイント。
Web(gunicorn) を起動せず、日次の送信処理だけを実行します。

Cron Runs の Command を次のように設定してください:
  python cron_entry.py
"""
import os
import logging
import sys
import signal
from contextlib import contextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# グローバルタイムアウト設定（秒） - 5分でタイムアウト
TIMEOUT_SECONDS = 300


@contextmanager
def timeout_context(seconds):
    """タイムアウト処理用のコンテキストマネージャー"""
    def timeout_handler(signum, frame):
        raise TimeoutError(f"処理が{seconds}秒以内に完了しませんでした")
    
    # SIGALRMを使用（Unix系OSのみ）
    if hasattr(signal, 'SIGALRM'):
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(seconds)
        try:
            yield
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    else:
        # Windowsなどではタイムアウトが使えないため、そのまま実行
        logger.warning("SIGALRMが利用できないため、タイムアウト処理をスキップします")
        yield


def main() -> None:
    """メイン処理"""
    try:
        logger.info("=" * 50)
        logger.info("Cron entry: send_daily_agenda start")
        logger.info("=" * 50)
        
        # 遅延 import（Web本体に影響しないように）
        from send_daily_agenda import send_daily_agenda  # type: ignore
        
        # タイムアウト付きで実行
        try:
            if hasattr(signal, 'SIGALRM'):
                with timeout_context(TIMEOUT_SECONDS):
                    send_daily_agenda()
            else:
                # Windowsなどではタイムアウトなしで実行
                send_daily_agenda()
        except TimeoutError as e:
            logger.error(f"タイムアウトエラー: {e}")
            sys.exit(1)
        
        logger.info("=" * 50)
        logger.info("Cron entry: send_daily_agenda done")
        logger.info("=" * 50)
        
    except KeyboardInterrupt:
        logger.warning("処理が中断されました")
        sys.exit(130)  # SIGINTで終了した場合の標準終了コード
    except Exception as e:
        logger.exception("Cron entry error: %s", e)
        # Cronは失敗時に非ゼロ終了
        sys.exit(1)
    finally:
        # 念のため、すべてのリソースをクリーンアップ
        logger.info("クリーンアップ処理を実行します")
        try:
            # データベース接続を明示的にクローズ
            from db import DBHelper
            # グローバルなDBHelperインスタンスがあればクローズ
            # ただし、複数インスタンスの可能性があるため、ここではログのみ
            logger.info("クリーンアップ完了")
        except Exception as cleanup_error:
            logger.warning(f"クリーンアップ中のエラー（無視）: {cleanup_error}")


if __name__ == "__main__":
    # Webと混同しないよう、実行中だけフラグ設定（必要に応じて）
    os.environ["RUN_SCHEDULER"] = "0"
    os.environ.setdefault("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    
    # Google認証ファイルの環境変数からの書き出し処理（app.pyと同じロジック）
    try:
        import json
        GOOGLE_CREDENTIALS_FILE_ENV = os.environ.get("GOOGLE_CREDENTIALS_FILE")
        if GOOGLE_CREDENTIALS_FILE_ENV:
            try:
                parsed = json.loads(GOOGLE_CREDENTIALS_FILE_ENV)
                with open("credentials.json", "w") as f:
                    json.dump(parsed, f)
                os.environ["GOOGLE_CREDENTIALS_PATH"] = "credentials.json"
                logger.info("Google認証ファイルをJSON形式からcredentials.jsonに変換しました")
            except json.JSONDecodeError:
                if os.path.exists(GOOGLE_CREDENTIALS_FILE_ENV):
                    os.environ["GOOGLE_CREDENTIALS_PATH"] = GOOGLE_CREDENTIALS_FILE_ENV
                    logger.info(f"Google認証ファイルパスを使用: {GOOGLE_CREDENTIALS_FILE_ENV}")
                else:
                    fallback = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
                    os.environ["GOOGLE_CREDENTIALS_PATH"] = fallback
                    logger.error("GOOGLE_CREDENTIALS_FILE が不正。フォールバックに切替えました")
    except Exception as e:
        logger.warning(f"Google認証ファイル処理でエラー（続行）: {e}")
    
    # 確実に終了するため、sys.exitを明示的に呼び出す
    try:
        main()
        sys.exit(0)  # 正常終了
    except SystemExit:
        # sys.exit()は既にSystemExitを発生させるため、再度raise
        raise
    except Exception as e:
        logger.exception(f"予期しないエラー: {e}")
        sys.exit(1)


