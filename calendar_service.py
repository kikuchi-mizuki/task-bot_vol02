from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import os
import json
import pytz
from config import Config
from dateutil import parser
from db import DBHelper
import logging

logger = logging.getLogger("calendar_service")
logger.setLevel(logging.INFO)
if not logger.hasHandlers():
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

class GoogleCalendarService:
    def __init__(self):
        self.SCOPES = ['https://www.googleapis.com/auth/calendar']
        self.db_helper = DBHelper()
        self.creds = None
        self.service = None
        self._authenticate()
    
    def _authenticate(self):
        """Google Calendar APIの認証を行います（非推奨のpickle形式は使用しない）"""
        # このメソッドは非推奨。ユーザーごとの認証は_get_user_credentialsを使用
        self.creds = None
        self.service = None
    
    def _get_user_credentials(self, line_user_id):
        """ユーザーの認証トークンをDBから取得（JSON形式対応）"""
        try:
            print(f"[DEBUG] _get_user_credentials開始: line_user_id={line_user_id}")
            
            # まずJSON形式で取得を試行
            json_data = self.db_helper.get_google_token_json(line_user_id)
            if json_data:
                try:
                    print(f"[DEBUG] JSON形式のトークンデータを取得")
                    credentials = Credentials.from_authorized_user_info(json.loads(json_data))
                    print(f"[DEBUG] JSON形式のトークンデータのデシリアライズ完了: credentials={credentials is not None}")
                    
                    # トークンの有効期限をチェック
                    if credentials and credentials.expired and credentials.refresh_token:
                        print(f"[DEBUG] トークンのリフレッシュ開始")
                        credentials.refresh(Request())
                        print(f"[DEBUG] トークンのリフレッシュ完了")
                        # 更新されたトークンをDBに保存
                        self.db_helper.save_google_token_json(line_user_id, credentials.to_json())
                        print(f"[DEBUG] 更新されたトークンをDBに保存完了")
                    
                    return credentials
                    
                except Exception as e:
                    print(f"[DEBUG] JSON形式のトークン読み込みエラー: {e}")
                    # JSON形式で失敗した場合は古いpickle形式を試行
                    pass
            
            # 古いpickle形式のトークンを取得（後方互換性）
            token_data = self.db_helper.get_google_token(line_user_id)
            print(f"[DEBUG] DBから取得したトークンデータ: {token_data is not None}")
            
            if not token_data:
                print(f"[DEBUG] トークンデータが取得できませんでした")
                return None
            
            try:
                print(f"[DEBUG] 古いpickle形式のトークンデータのデシリアライズ開始")
                import pickle
                credentials = pickle.loads(token_data)
                print(f"[DEBUG] 古いpickle形式のトークンデータのデシリアライズ完了: credentials={credentials is not None}")
                
                # トークンの有効期限をチェック
                if credentials and credentials.expired and credentials.refresh_token:
                    print(f"[DEBUG] トークンのリフレッシュ開始")
                    credentials.refresh(Request())
                    print(f"[DEBUG] トークンのリフレッシュ完了")
                    # 更新されたトークンをJSON形式で保存（移行）
                    self.db_helper.save_google_token_json(line_user_id, credentials.to_json())
                    print(f"[DEBUG] 更新されたトークンをJSON形式でDBに保存完了")
                
                return credentials
                
            except Exception as e:
                print(f"[DEBUG] 古いpickle形式のトークン読み込みエラー: {e}")
                import traceback
                traceback.print_exc()
                return None
                
        except Exception as e:
            print(f"[DEBUG] _get_user_credentialsで例外発生: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _get_calendar_service(self, line_user_id):
        """ユーザーごとのGoogle Calendarサービスを取得"""
        try:
            print(f"[DEBUG] _get_calendar_service開始: line_user_id={line_user_id}")
            credentials = self._get_user_credentials(line_user_id)
            print(f"[DEBUG] 認証情報取得結果: credentials={credentials is not None}")
            
            if not credentials:
                print(f"[DEBUG] 認証情報が取得できませんでした")
                raise Exception("ユーザーの認証トークンが見つかりません。認証を完了してください。")
            
            print(f"[DEBUG] Google Calendar APIサービス構築開始")
            service = build('calendar', 'v3', credentials=credentials)
            print(f"[DEBUG] Google Calendar APIサービス構築完了")
            return service
            
        except Exception as e:
            print(f"[DEBUG] _get_calendar_serviceで例外発生: {e}")
            import traceback
            traceback.print_exc()
            raise e
    
    def check_availability(self, start_time, end_time):
        """指定された時間帯の空き時間を確認します"""
        try:
            if not self.service:
                return None, "Google認証が必要です。"
            # ISO文字列がタイムゾーン付きかどうかでZを付与しない
            def iso_no_z(dt):
                s = dt.isoformat()
                return s if s.endswith(("+09:00", "+00:00", "-0")) else s + "Z"
            # 指定された時間帯のイベントを取得
            events_result = self.service.events().list(
                calendarId=Config.GOOGLE_CALENDAR_ID,  # 'primary'（各ユーザーのメインカレンダー）
                timeMin=iso_no_z(start_time),
                timeMax=iso_no_z(end_time),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            events = events_result.get('items', [])
            if not events:
                return True, "指定された時間帯は空いています。"
            # 既存のイベント情報を取得
            existing_events = []
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                end = event['end'].get('dateTime', event['end'].get('date'))
                title = event.get('summary', 'タイトルなし')
                existing_events.append({
                    'title': title,
                    'start': start,
                    'end': end
                })
            return False, existing_events
        except Exception as e:
            return None, f"エラーが発生しました: {str(e)}"
    
    def add_event(self, title, start_time, end_time, description="", line_user_id=None, force_add=False):
        """カレンダーにイベントを追加します"""
        try:
            if not line_user_id:
                return False, "ユーザーIDが必要です", None
            service = self._get_calendar_service(line_user_id)
            # 既存の予定をチェック（force_addがFalseのときのみ）
            if not force_add:
                events = self.get_events_for_time_range(start_time, end_time, line_user_id)
                logger.info(f"[DEBUG] add_event: 追加前に取得したevents = {events}")
                if events and len(events) > 0:
                    conflicting_events = []
                    for event in events:
                        if not isinstance(event, dict):
                            logger.warning(f"[WARN] add_event: eventがdict型でないためスキップ: {event}")
                            continue
                        conflicting_events.append({
                            'title': event.get('title', '予定なし'),
                            'start': event['start'].get('dateTime', event['start'].get('date')) if isinstance(event['start'], dict) else event['start'],
                            'end': event['end'].get('dateTime', event['end'].get('date')) if isinstance(event['end'], dict) else event['end']
                        })
                    logger.info(f"[DEBUG] 既存の予定があるため追加しません: {conflicting_events}")
                    if conflicting_events:
                        return False, "指定された時間に既存の予定があります", conflicting_events
            # イベントを作成
            event = {
                'summary': title,
                'description': description,
                'start': {
                    'dateTime': start_time.isoformat(),
                    'timeZone': 'Asia/Tokyo',
                },
                'end': {
                    'dateTime': end_time.isoformat(),
                    'timeZone': 'Asia/Tokyo',
                },
            }
            logger.info(f"[DEBUG] Google Calendar APIへイベント追加リクエスト: {event}")
            # イベントを追加
            event = service.events().insert(
                calendarId=Config.GOOGLE_CALENDAR_ID,  # 'primary'（各ユーザーのメインカレンダー）
                body=event
            ).execute()
            logger.info(f"[DEBUG] Google Calendar APIレスポンス: {event}")
            return True, "✅予定を追加しました", {
                'title': title,
                'start': start_time.isoformat(),
                'end': end_time.isoformat()
            }
        except Exception as e:
            logger.error(f"[ERROR] add_eventで例外発生: {e}")
            return False, f"エラーが発生しました: {str(e)}", None
    
    def get_events_for_dates(self, dates, line_user_id=None):
        """指定された日付のイベントを取得します（ユーザーごとの認証トークン対応、JST日付で正確に抽出）"""
        import pytz
        events_info = []
        jst = pytz.timezone('Asia/Tokyo')
        for date in dates:
            # JST 0:00〜翌日0:00をUTCに変換
            start_of_day_jst = jst.localize(datetime.combine(date, datetime.min.time()))
            end_of_day_jst = start_of_day_jst + timedelta(days=1)
            start_of_day_utc = start_of_day_jst.astimezone(pytz.UTC)
            end_of_day_utc = end_of_day_jst.astimezone(pytz.UTC)
            try:
                service = self._get_calendar_service(line_user_id) if line_user_id else self.service
                if not service:
                    events_info.append({
                        'date': date.strftime('%Y-%m-%d'),
                        'events': [],
                        'error': 'Google認証が必要です。'
                    })
                    continue
                events_result = service.events().list(
                    calendarId=Config.GOOGLE_CALENDAR_ID,
                    timeMin=start_of_day_utc.isoformat(),
                    timeMax=end_of_day_utc.isoformat(),
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
                print(f"[DEBUG] Google Calendar APIレスポンス: {events_result}")
                events = events_result.get('items', [])
                if events:
                    day_events = []
                    for event in events:
                        start = event['start'].get('dateTime', event['start'].get('date'))
                        end = event['end'].get('dateTime', event['end'].get('date'))
                        title = event.get('summary', 'タイトルなし')
                        day_events.append({
                            'title': title,
                            'start': start,
                            'end': end
                        })
                    events_info.append({
                        'date': date.strftime('%Y-%m-%d'),
                        'events': day_events
                    })
                else:
                    events_info.append({
                        'date': date.strftime('%Y-%m-%d'),
                        'events': []
                    })
            except Exception as e:
                events_info.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'error': str(e)
                })
        return events_info
    
    def get_events_for_time_range(self, start_time, end_time, line_user_id):
        """指定された時間範囲のイベントを取得します"""
        try:
            print(f"[DEBUG] get_events_for_time_range開始")
            print(f"[DEBUG] 入力: start_time={start_time}, end_time={end_time}, line_user_id={line_user_id}")
            
            jst = pytz.timezone('Asia/Tokyo')
            # タイムゾーンなしならJSTを付与
            if start_time.tzinfo is None:
                start_time = jst.localize(start_time)
            if end_time.tzinfo is None:
                end_time = jst.localize(end_time)
            
            print(f"[DEBUG] タイムゾーン調整後: start_time={start_time}, end_time={end_time}")
            
            service = self._get_calendar_service(line_user_id)
            print(f"[DEBUG] カレンダーサービス取得完了")
            
            # タイムゾーンをUTCに変換
            utc_start = start_time.astimezone(pytz.UTC)
            utc_end = end_time.astimezone(pytz.UTC)
            
            print(f"[DEBUG] UTC変換後: utc_start={utc_start}, utc_end={utc_end}")
            print(f"[DEBUG] Google Calendar APIリクエスト: calendarId={Config.GOOGLE_CALENDAR_ID}, timeMin={utc_start.isoformat()}, timeMax={utc_end.isoformat()}")
            
            events_result = service.events().list(
                calendarId=Config.GOOGLE_CALENDAR_ID,  # 'primary'（各ユーザーのメインカレンダー）
                timeMin=utc_start.isoformat(),
                timeMax=utc_end.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            print(f"[DEBUG] Google Calendar APIレスポンス: {events_result}")
            
            events = events_result.get('items', [])
            print(f"[DEBUG] 取得イベント数: {len(events) if events else 0}")
            
            if not events:
                print(f"[DEBUG] イベントなし、空リストを返す")
                return []
            
            event_list = []
            for i, event in enumerate(events):
                print(f"[DEBUG] イベント{i+1}処理: {event}")
                start = event['start'].get('dateTime', event['start'].get('date'))
                end = event['end'].get('dateTime', event['end'].get('date'))
                title = event.get('summary', 'タイトルなし')
                
                event_data = {
                    'title': title,
                    'start': start,
                    'end': end
                }
                event_list.append(event_data)
                print(f"[DEBUG] イベント{i+1}追加: {event_data}")
            
            print(f"[DEBUG] 最終イベントリスト: {event_list}")
            return event_list
            
        except Exception as e:
            print(f"[DEBUG] get_events_for_time_rangeで例外発生: {e}")
            import traceback
            traceback.print_exc()
            logging.error(f"イベント取得エラー: {e}")
            return []
    
    def find_free_slots_for_day(self, start_dt, end_dt, events):
        """指定枠(start_dt, end_dt)内で既存予定を除いた空き時間帯リストを返す"""
        try:
            logger.info(f"[DEBUG] find_free_slots_for_day開始")
            logger.info(f"[DEBUG] 検索枠: {start_dt} 〜 {end_dt}")
            logger.info(f"[DEBUG] 既存予定数: {len(events) if events else 0}")
            
            jst = pytz.timezone('Asia/Tokyo')
            if start_dt.tzinfo is None:
                start_dt = jst.localize(start_dt)
            if end_dt.tzinfo is None:
                end_dt = jst.localize(end_dt)
                
            # eventsがNoneや空の場合は必ず再取得
            if events is None or len(events) == 0:
                logger.info(f"[DEBUG] 既存予定なし、全日空き時間として返す")
                return [{
                    'start': start_dt.strftime('%H:%M'),
                    'end': end_dt.strftime('%H:%M')
                }]
                
            # 既存予定を時間順にbusy_timesへ
            busy_times = []
            logger.info(f"[DEBUG] 既存予定の処理開始")
            
            for i, event in enumerate(events):
                logger.info(f"[DEBUG] 予定{i+1}: {event}")
                
                start = event['start'] if isinstance(event['start'], str) else event['start'].get('dateTime', event['start'].get('date'))
                end = event['end'] if isinstance(event['end'], str) else event['end'].get('dateTime', event['end'].get('date'))
                
                logger.info(f"[DEBUG] 予定{i+1}の時間: {start} 〜 {end}")
                
                if 'T' in start:  # dateTime形式
                    start_ev = datetime.fromisoformat(start.replace('Z', '+00:00'))
                    end_ev = datetime.fromisoformat(end.replace('Z', '+00:00'))
                    
                    logger.info(f"[DEBUG] 予定{i+1}のパース後: {start_ev} 〜 {end_ev}")
                    
                    # 枠外の予定は除外
                    if end_ev <= start_dt or start_ev >= end_dt:
                        logger.info(f"[DEBUG] 予定{i+1}は枠外のため除外")
                        continue
                        
                    busy_start = max(start_ev, start_dt)
                    busy_end = min(end_ev, end_dt)
                    busy_times.append((busy_start, busy_end))
                    logger.info(f"[DEBUG] 予定{i+1}をbusy_timesに追加: {busy_start} 〜 {busy_end}")
                    
                else:  # date型（終日予定）
                    allday_start = jst.localize(datetime.combine(datetime.strptime(start, "%Y-%m-%d"), datetime.min.time()))
                    allday_end = allday_start + timedelta(days=1)
                    
                    logger.info(f"[DEBUG] 予定{i+1}は終日予定: {allday_start} 〜 {allday_end}")
                    
                    if allday_end <= start_dt or allday_start >= end_dt:
                        logger.info(f"[DEBUG] 予定{i+1}は枠外のため除外")
                        continue
                        
                    busy_start = max(allday_start, start_dt)
                    busy_end = min(allday_end, end_dt)
                    busy_times.append((busy_start, busy_end))
                    logger.info(f"[DEBUG] 予定{i+1}をbusy_timesに追加: {busy_start} 〜 {busy_end}")
            
            logger.info(f"[DEBUG] busy_times: {busy_times}")
            
            # 空き時間を計算
            free_slots = []
            if not busy_times:
                logger.info(f"[DEBUG] busy_timesが空、全日空き時間として返す")
                free_slots.append({
                    'start': start_dt.strftime('%H:%M'),
                    'end': end_dt.strftime('%H:%M')
                })
                return free_slots
                
            # busy_timesを開始時刻順に明示的にソート
            busy_times = sorted(busy_times, key=lambda x: x[0])
            logger.info(f"[DEBUG] ソート後のbusy_times: {busy_times}")
            
            current_time = start_dt
            logger.info(f"[DEBUG] 空き時間計算開始、current_time: {current_time}")
            
            for i, (busy_start, busy_end) in enumerate(busy_times):
                logger.info(f"[DEBUG] busy_times[{i}]処理: {busy_start} 〜 {busy_end}")
                
                if current_time < busy_start:
                    free_slot = {
                        'start': current_time.strftime('%H:%M'),
                        'end': busy_start.strftime('%H:%M')
                    }
                    free_slots.append(free_slot)
                    logger.info(f"[DEBUG] 空き時間を追加: {free_slot}")
                    
                current_time = max(current_time, busy_end)
                logger.info(f"[DEBUG] current_time更新: {current_time}")
                
            if current_time < end_dt:
                free_slot = {
                    'start': current_time.strftime('%H:%M'),
                    'end': end_dt.strftime('%H:%M')
                }
                free_slots.append(free_slot)
                logger.info(f"[DEBUG] 最後の空き時間を追加: {free_slot}")
                
            logger.info(f"[DEBUG] 最終的な空き時間: {free_slots}")
            return free_slots 
            
        except Exception as e:
            logger.error(f"空き時間検索エラー: {e}")
            return [] 