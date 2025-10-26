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
import pytz
from datetime import datetime

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    """メイン関数"""
    logger.info("定期実行cronジョブを開始します")
    
    # 日本時間で19:00に設定
    jst = pytz.timezone('Asia/Tokyo')
    schedule.every().day.at("19:00").do(send_daily_agenda)
    
    logger.info("スケジュール設定完了: 毎日19:00（JST）に明日の予定一覧を送信")
    
    # 現在時刻をログ出力
    now_jst = datetime.now(jst)
    logger.info(f"現在時刻（JST）: {now_jst.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # メインループ
    while True:
        schedule.run_pending()
        time.sleep(60)  # 1分ごとにチェック
        # 毎時間ログ出力
        current_time = datetime.now(jst)
        if current_time.minute == 0:
            logger.info(f"スケジューラー実行中... 現在時刻: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    main()
