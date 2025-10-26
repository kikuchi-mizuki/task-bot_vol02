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
    
    # Railwayの環境ではUTC時間で動作するため、19:00 JST = 10:00 UTC
    # ただし、scheduleライブラリはローカル時間を使用するため、環境変数でタイムゾーンを設定
    import os
    os.environ['TZ'] = 'Asia/Tokyo'
    time.tzset()  # タイムゾーンを再設定
    
    schedule.every().day.at("19:00").do(send_daily_agenda)
    
    logger.info("スケジュール設定完了: 毎日19:00（JST）に明日の予定一覧を送信")
    
    # 現在時刻をログ出力（UTC、JST両方）
    now_utc = datetime.now(pytz.UTC)
    now_jst = datetime.now(jst)
    logger.info(f"現在時刻（UTC）: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"現在時刻（JST）: {now_jst.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # メインループ
    while True:
        schedule.run_pending()
        time.sleep(60)  # 1分ごとにチェック
        # 毎時間ログ出力
        current_time_utc = datetime.now(pytz.UTC)
        current_time_jst = datetime.now(jst)
        if current_time_jst.minute == 0:
            logger.info(f"スケジューラー実行中... UTC: {current_time_utc.strftime('%Y-%m-%d %H:%M:%S')}, JST: {current_time_jst.strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    main()
