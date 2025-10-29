#!/usr/bin/env python3
"""
Railway Cron Runs 用の単発実行エントリポイント。
Web(gunicorn) を起動せず、日次の送信処理だけを実行します。

Cron Runs の Command を次のように設定してください:
  python cron_entry.py
"""
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    try:
        # 遅延 import（Web本体に影響しないように）
        from send_daily_agenda import send_daily_agenda  # type: ignore
        logger.info("Cron entry: send_daily_agenda start")
        send_daily_agenda()
        logger.info("Cron entry: send_daily_agenda done")
    except Exception as e:
        logger.exception("Cron entry error: %s", e)
        # Cronは失敗時に非ゼロ終了
        raise


if __name__ == "__main__":
    # Webと混同しないよう、実行中だけフラグ設定（必要に応じて）
    os.environ["RUN_SCHEDULER"] = "0"
    main()


