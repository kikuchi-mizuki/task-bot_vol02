#!/usr/bin/env python3
"""
Railway用のcronジョブスクリプト
毎日19:00に明日の予定一覧を送信
"""
import os
import time
import schedule
from send_daily_agenda import send_daily_agenda
import logging

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    """メイン関数"""
    logger.info("定期実行cronジョブを開始します")
    
    # 毎日19:00に明日の予定一覧を送信
    schedule.every().day.at("19:00").do(send_daily_agenda)
    
    logger.info("スケジュール設定完了: 毎日19:00に明日の予定一覧を送信")
    
    # メインループ
    while True:
        schedule.run_pending()
        time.sleep(60)  # 1分ごとにチェック
        logger.debug("スケジューラー実行中...")

if __name__ == "__main__":
    main()
