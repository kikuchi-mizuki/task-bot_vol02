#!/usr/bin/env python3
"""
Railway Cron Runs 用の1回実行スクリプト
Webサーバーを起動せず、日次の予定送信処理だけを実行して終了します。

Railway Scheduled Jobs の Command:
  python jobs/run_daily_agenda.py
"""
import os
import sys
import logging
import json

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# 環境変数のデフォルト設定
os.environ.setdefault("RUN_SCHEDULER", "0")
os.environ.setdefault("GOOGLE_CREDENTIALS_PATH", "credentials.json")

# Google認証ファイルの環境変数からの書き出し処理（app.pyと同じロジック）
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


def main():
    """メイン処理：1回だけ実行して終了"""
    try:
        logger.info("=" * 60)
        logger.info("Starting daily agenda job...")
        logger.info("=" * 60)
        
        # 遅延import（Webサーバーに影響しないように）
        from send_daily_agenda import send_daily_agenda
        
        # 実行
        send_daily_agenda()
        
        logger.info("=" * 60)
        logger.info("Job done.")
        logger.info("=" * 60)
        
        # 正常終了（Exit code 0）
        sys.exit(0)
        
    except KeyboardInterrupt:
        logger.warning("Job interrupted")
        sys.exit(130)  # SIGINTで終了した場合の標準終了コード
    except Exception as e:
        logger.exception("Job failed: %s", e)
        # 失敗は非0で終了（Cronが検知できる）
        sys.exit(1)


if __name__ == "__main__":
    main()

