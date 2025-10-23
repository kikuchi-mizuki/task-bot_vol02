from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime, timedelta
from dateutil import parser
import pytz
import re
from calendar_service import GoogleCalendarService
from ai_service import AIService
from config import Config
from db import DBHelper
import logging

logger = logging.getLogger("line_bot_handler")

class LineBotHandler:
    def __init__(self):
        # LINE Bot API クライアント初期化（標準）
        if not Config.LINE_CHANNEL_ACCESS_TOKEN:
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN environment variable is not set")
        if not Config.LINE_CHANNEL_SECRET:
            raise ValueError("LINE_CHANNEL_SECRET environment variable is not set")
            
        self.line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
        self.handler = WebhookHandler(Config.LINE_CHANNEL_SECRET)
        
        # カスタムセッション設定をグローバルに適用
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        # リトライ戦略を設定（より詳細な設定）
        retry_strategy = Retry(
            total=5,  # 最大リトライ回数を増加
            backoff_factor=2,  # バックオフ係数を増加
            status_forcelist=[429, 500, 502, 503, 504, 520, 521, 522, 523, 524],  # リトライするHTTPステータスコードを拡張
            allowed_methods=["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE", "POST"],  # 全HTTPメソッドでリトライ
            raise_on_status=False,  # ステータスエラーで例外を発生させない
        )
        
        # アダプターを設定
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=20)
        
        # グローバルセッション設定
        session = requests.Session()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.timeout = (15, 45)  # (接続タイムアウト, 読み取りタイムアウト) を増加
        
        # LINE Bot SDKの内部セッションを置き換え
        self.line_bot_api._session = session
        
        # DBヘルパーの初期化
        self.db_helper = DBHelper()
        
        try:
            self.calendar_service = GoogleCalendarService()
        except Exception as e:
            print(f"Google Calendarサービス初期化エラー: {e}")
            self.calendar_service = None
            
        try:
            self.ai_service = AIService()
        except Exception as e:
            print(f"AIサービス初期化エラー: {e}")
            self.ai_service = None
            
        self.jst = pytz.timezone('Asia/Tokyo')
    
    def _check_user_auth(self, line_user_id):
        """ユーザーの認証状態をチェック"""
        return self.db_helper.user_exists(line_user_id)
    
    def _send_auth_guide(self, line_user_id):
        """認証案内メッセージを送信"""
        # ワンタイムコードを生成
        code = self.db_helper.generate_onetime_code(line_user_id)
        
        # 認証URLを生成（環境変数から取得）
        import os
        base_url = os.getenv('BASE_URL', 'https://web-production-xxxx.up.railway.app')
        auth_url = f"{base_url}/onetime_login"
        
        message = f"""Google Calendar認証が必要です。

🔐 ワンタイムコード: {code}

📱 認証手順:
1. 下のURLをクリックまたはコピー
2. ワンタイムコードを入力
3. Googleアカウントで認証

🔗 認証URL:
{auth_url}

⚠️ コードの有効期限は10分です
"""
        return TextSendMessage(text=message)
    
    def handle_message(self, event):
        """メッセージを処理します"""
        user_message = event.message.text
        line_user_id = event.source.user_id

        # Google認証未完了なら必ず認証案内を返す
        if not self._check_user_auth(line_user_id):
            return self._send_auth_guide(line_user_id)

        # 「はい」返答による強制追加判定
        if user_message.strip() in ["はい", "追加", "OK", "Yes", "yes"]:
            pending_json = self.db_helper.get_pending_event(line_user_id)
            if pending_json:
                import json
                event_info = json.loads(pending_json)
                # 予定を強制追加
                from dateutil import parser
                start_datetime = parser.parse(event_info['start_datetime'])
                end_datetime = parser.parse(event_info['end_datetime'])
                # 既にタイムゾーンが設定されている場合はそのまま使用、そうでなければJSTを設定
                if start_datetime.tzinfo is None:
                    start_datetime = self.jst.localize(start_datetime)
                if end_datetime.tzinfo is None:
                    end_datetime = self.jst.localize(end_datetime)
                if not self.calendar_service or not self.ai_service:
                    return TextSendMessage(text="カレンダーサービスまたはAIサービスが初期化されていません。")
                
                # 移動時間フラグをチェック
                has_travel = event_info.get('has_travel', False)
                print(f"[DEBUG] 強制追加: 移動時間フラグ = {has_travel}")
                
                success, message, result = self.calendar_service.add_event(
                    event_info['title'],
                    start_datetime,
                    end_datetime,
                    event_info.get('description', ''),
                    line_user_id=line_user_id,
                    force_add=True
                )
                
                # 移動時間フラグがある場合は、移動時間も追加
                if success and has_travel:
                    print(f"[DEBUG] 移動時間を追加開始")
                    # 移動時間の予定を作成
                    from datetime import timedelta
                    import pytz
                    jst = pytz.timezone('Asia/Tokyo')
                    
                    # 往路（1時間前）
                    travel_before_start = start_datetime - timedelta(hours=1)
                    travel_before_end = start_datetime
                    
                    # 復路（1時間後）
                    travel_after_start = end_datetime
                    travel_after_end = end_datetime + timedelta(hours=1)
                    
                    # 往路を追加
                    travel_success1, travel_message1, travel_result1 = self.calendar_service.add_event(
                        "移動時間（往路）",
                        travel_before_start,
                        travel_before_end,
                        "移動のための時間",
                        line_user_id=line_user_id,
                        force_add=True
                    )
                    print(f"[DEBUG] 往路追加結果: {travel_success1}")
                    
                    # 復路を追加
                    travel_success2, travel_message2, travel_result2 = self.calendar_service.add_event(
                        "移動時間（復路）",
                        travel_after_start,
                        travel_after_end,
                        "移動のための時間",
                        line_user_id=line_user_id,
                        force_add=True
                    )
                    print(f"[DEBUG] 復路追加結果: {travel_success2}")
                
                self.db_helper.delete_pending_event(line_user_id)
                response_text = self.ai_service.format_event_confirmation(success, message, result)
                return TextSendMessage(text=response_text)
        else:
            # 「はい」以外の返答でpending_eventsがあれば削除し、キャンセルメッセージを返す
            pending_json = self.db_helper.get_pending_event(line_user_id)
            if pending_json:
                self.db_helper.delete_pending_event(line_user_id)
                return TextSendMessage(text="予定追加をキャンセルしました。")
        
        try:
            # 環境変数が設定されていない場合の処理
            if not Config.LINE_CHANNEL_ACCESS_TOKEN or not Config.LINE_CHANNEL_SECRET:
                return TextSendMessage(text="LINE Botの設定が完了していません。環境変数を設定してください。")
            
            if not self.ai_service:
                return TextSendMessage(text="AIサービスの初期化に失敗しました。OpenAI APIキーを設定してください。")
            
            # AIを使ってメッセージの意図を判断
            ai_result = self.ai_service.extract_dates_and_times(user_message)
            print(f"[DEBUG] ai_result: {ai_result}")
            
            if 'error' in ai_result:
                # AI処理に失敗した場合、ガイダンスメッセージを返す
                return TextSendMessage(text="日時の送信で空き時間が分かります！\n日時と内容の送信で予定を追加します！\n\n例：\n・「明日の空き時間」\n・「7/15 15:00〜16:00の空き時間」\n・「明日の午前9時から会議を追加して」\n・「来週月曜日の14時から打ち合わせ」")
            
            # タスクタイプに基づいて処理
            task_type = ai_result.get('task_type', 'add_event')
            
            if task_type == 'availability_check':
                print(f"[DEBUG] dates_info: {ai_result.get('dates', [])}")
                return self._handle_availability_check(ai_result.get('dates', []), line_user_id)
            elif task_type == 'add_event':
                # 予定追加時の重複確認ロジック（複数予定対応）
                if not self.calendar_service:
                    return TextSendMessage(text="カレンダーサービスが初期化されていません。")
                
                dates = ai_result.get('dates', [])
                if not dates:
                    return TextSendMessage(text="イベント情報を正しく認識できませんでした。\n\n例: 「明日の午前9時から会議を追加して」\n「来週月曜日の14時から打ち合わせ」")
                
                # 複数の予定を処理
                return self._handle_multiple_events(dates, line_user_id)
            else:
                # 未対応コマンドの場合もガイダンスメッセージ
                return TextSendMessage(text="日時の送信で空き時間が分かります！\n日時と内容の送信で予定を追加します！\n\n例：\n・「明日の空き時間」\n・「7/15 15:00〜16:00の空き時間」\n・「明日の午前9時から会議を追加して」\n・「来週月曜日の14時から打ち合わせ」")
        except Exception as e:
            return TextSendMessage(text=f"エラーが発生しました: {str(e)}")
    
    def _handle_multiple_events(self, dates, line_user_id):
        """複数の予定を処理します"""
        try:
            from dateutil import parser
            import json
            
            added_events = []
            failed_events = []
            
            for date_info in dates:
                try:
                    # 日時を構築
                    date_str = date_info.get('date')
                    time_str = date_info.get('time')
                    end_time_str = date_info.get('end_time')
                    title = date_info.get('title', '予定')
                    description = date_info.get('description', '')
                    
                    if not date_str or not time_str:
                        print(f"[DEBUG] 不完全な予定情報をスキップ: {date_info}")
                        continue
                    
                    # 終了時間が設定されていない場合は1時間後に設定（元の設定を維持）
                    if not end_time_str or end_time_str == time_str:
                        from datetime import datetime, timedelta
                        time_obj = datetime.strptime(time_str, "%H:%M")
                        end_time_obj = time_obj + timedelta(hours=1)
                        end_time_str = end_time_obj.strftime("%H:%M")
                        print(f"[DEBUG] 終了時間を自動設定: {time_str} -> {end_time_str}")
                    
                    # 日時文字列を構築
                    start_datetime_str = f"{date_str}T{time_str}:00+09:00"
                    end_datetime_str = f"{date_str}T{end_time_str}:00+09:00"
                    
                    print(f"[DEBUG] 予定追加処理: {title} - {start_datetime_str} to {end_datetime_str}")
                    
                    # 日時をパース（タイムゾーン処理を改善）
                    start_datetime = parser.parse(start_datetime_str)
                    end_datetime = parser.parse(end_datetime_str)
                    
                    # 既にタイムゾーンが設定されている場合はそのまま使用、そうでなければJSTを設定
                    if start_datetime.tzinfo is None:
                        start_datetime = self.jst.localize(start_datetime)
                    if end_datetime.tzinfo is None:
                        end_datetime = self.jst.localize(end_datetime)
                    
                    # 移動時間を含む場合の重複チェック
                    # 移動キーワードをチェック
                    travel_keywords = ['移動', '移動あり', '移動時間', '移動必要']
                    has_travel = any(keyword in title or keyword in description for keyword in travel_keywords)
                    
                    # チェックする時間範囲を決定
                    check_start = start_datetime
                    check_end = end_datetime
                    
                    if has_travel:
                        # 移動時間を含む場合は、往路（1時間前）と復路（1時間後）も含めてチェック
                        from datetime import timedelta
                        check_start = start_datetime - timedelta(hours=1)  # 往路
                        check_end = end_datetime + timedelta(hours=1)     # 復路
                        print(f"[DEBUG] 移動時間を含む重複チェック: {check_start} 〜 {check_end}")
                    
                    # 既存予定をチェック
                    events = self.calendar_service.get_events_for_time_range(check_start, check_end, line_user_id)
                    if events:
                        print(f"[DEBUG] 重複予定を検出: {title}")
                        # 重複確認メッセージを表示
                        conflicting_events = []
                        for event in events:
                            conflicting_events.append({
                                'title': event.get('title', '予定なし'),
                                'start': event.get('start', ''),
                                'end': event.get('end', '')
                            })
                        
                        # 重複確認メッセージを構築
                        response_text = "⚠️ この時間帯に既に予定が存在します:\n"
                        for event in conflicting_events:
                            # 時間をフォーマット
                            start_time = event['start']
                            end_time = event['end']
                            if 'T' in start_time:
                                start_dt = parser.parse(start_time)
                                end_dt = parser.parse(end_time)
                                start_dt = start_dt.astimezone(self.jst)
                                end_dt = end_dt.astimezone(self.jst)
                                time_str = f"{start_dt.strftime('%H:%M')}~{end_dt.strftime('%H:%M')}"
                            else:
                                time_str = f"{start_time}~{end_time}"
                            
                            response_text += f"- {event['title']}\n({time_str})\n"
                        
                        if has_travel:
                            response_text += "\n※移動時間（往路・復路）も含めて重複チェックしています。\n"
                        
                        response_text += "\nそれでも追加しますか？\n「はい」と返信してください。"
                        
                        # 予定情報をpending_eventsに保存（移動時間フラグも含める）
                        event_info = {
                            'title': title,
                            'start_datetime': start_datetime_str,
                            'end_datetime': end_datetime_str,
                            'description': description,
                            'has_travel': has_travel
                        }
                        import json
                        self.db_helper.save_pending_event(line_user_id, json.dumps(event_info))
                        
                        return TextSendMessage(text=response_text)
                    
                    # 予定を追加
                    success, message, result = self.calendar_service.add_event(
                        title,
                        start_datetime,
                        end_datetime,
                        description,
                        line_user_id=line_user_id,
                        force_add=True
                    )
                    
                    if success:
                        # 元の表示形式に合わせて日時をフォーマット
                        from datetime import datetime
                        import pytz
                        jst = pytz.timezone('Asia/Tokyo')
                        start_dt = start_datetime.astimezone(jst)
                        end_dt = end_datetime.astimezone(jst)
                        weekday = "月火水木金土日"[start_dt.weekday()]
                        date_str = f"{start_dt.month}/{start_dt.day}（{weekday}）"
                        time_str = f"{start_dt.strftime('%H:%M')}〜{end_dt.strftime('%H:%M')}"
                        
                        added_events.append({
                            'title': title,
                            'time': f"{date_str}{time_str}"
                        })
                        print(f"[DEBUG] 予定追加成功: {title}")
                    else:
                        failed_events.append({
                            'title': title,
                            'time': f"{time_str}-{end_time_str}",
                            'reason': message
                        })
                        print(f"[DEBUG] 予定追加失敗: {title} - {message}")
                        
                except Exception as e:
                    print(f"[DEBUG] 予定処理エラー: {e}")
                    failed_events.append({
                        'title': date_info.get('title', '予定'),
                        'time': f"{date_info.get('time', '')}-{date_info.get('end_time', '')}",
                        'reason': str(e)
                    })
            
            # 結果メッセージを構築（移動時間を含む場合は統一形式）
            if added_events:
                # 移動時間が含まれているかチェック
                has_travel = any('移動時間' in event['title'] for event in added_events)
                
                if has_travel and len(added_events) > 1:
                    # 移動時間を含む場合は統一形式で表示
                    response_text = "✅予定を追加しました！\n\n"
                    
                    # 日付を取得（最初の予定から）
                    first_event = added_events[0]
                    time_str = first_event['time']
                    # "10/18 (土)19:00〜20:00" から "10/18 (土)" を抽出
                    date_match = re.search(r'(\d{1,2}/\d{1,2}\s*\([月火水木金土日]\)\s*)', time_str)
                    date_part = date_match.group(1).strip() if date_match else time_str
                    response_text += f"{date_part}\n"
                    response_text += "────────\n"
                    
                    # 時間順でソート（開始時間でソート）
                    def get_start_time(event):
                        time_str = event['time']
                        # "10/18 (土)19:00〜20:00" から "19:00〜20:00" を抽出
                        time_match = re.search(r'(\d{1,2}:\d{2}〜\d{1,2}:\d{2})', time_str)
                        time_part = time_match.group(1) if time_match else time_str
                        start_time = time_part.split('〜')[0]  # "19:00〜20:00" -> "19:00"
                        return start_time
                    
                    sorted_events = sorted(added_events, key=get_start_time)
                    
                    # 各予定を番号付きで表示
                    for i, event in enumerate(sorted_events, 1):
                        # 時間部分を抽出（"10:00~11:00" の形式）
                        # 日付と時間の区切りを正しく処理
                        time_str = event['time']
                        # "10/18 (土)19:00〜20:00" から "19:00〜20:00" を抽出
                        time_match = re.search(r'(\d{1,2}:\d{2}〜\d{1,2}:\d{2})', time_str)
                        time_part = time_match.group(1) if time_match else time_str
                        response_text += f"{i}. {event['title']}\n"
                        response_text += f"🕐 {time_part}\n"
                    
                    response_text += "────────"
                else:
                    # 通常の表示形式
                    response_text = "✅予定を追加しました！\n\n"
                    for event in added_events:
                        response_text += f"📅{event['title']}\n{event['time']}\n"
                
                if failed_events:
                    response_text += "\n\n⚠️追加できなかった予定:\n"
                    for event in failed_events:
                        response_text += f"• {event['title']} ({event['time']}) - {event['reason']}\n"
            else:
                response_text = "❌予定を追加できませんでした。\n\n"
                for event in failed_events:
                    response_text += f"• {event['title']} ({event['time']}) - {event['reason']}\n"
            
            return TextSendMessage(text=response_text)
            
        except Exception as e:
            print(f"[DEBUG] 複数予定処理エラー: {e}")
            return TextSendMessage(text=f"予定の処理中にエラーが発生しました: {str(e)}")
    
    def _handle_availability_check(self, dates_info, line_user_id):
        """空き時間確認を処理します"""
        try:
            print(f"[DEBUG] _handle_availability_check開始")
            print(f"[DEBUG] dates_info: {dates_info}")
            print(f"[DEBUG] line_user_id: {line_user_id}")
            
            # ユーザーの認証状態をチェック
            if not self._check_user_auth(line_user_id):
                print(f"[DEBUG] ユーザー認証未完了")
                return self._send_auth_guide(line_user_id)
            
            if not self.calendar_service:
                print(f"[DEBUG] カレンダーサービス未初期化")
                return TextSendMessage(text="Google Calendarサービスが初期化されていません。認証ファイルを確認してください。")
            
            if not self.ai_service:
                print(f"[DEBUG] AIサービス未初期化")
                return TextSendMessage(text="AIサービスが初期化されていません。")
            
            if not dates_info:
                print(f"[DEBUG] dates_infoが空")
                return TextSendMessage(text="日付を正しく認識できませんでした。\n\n例: 「明日7/7 15:00〜15:30の空き時間を教えて」")
            
            print(f"[DEBUG] 空き時間計算開始")
            free_slots_by_frame = []
            for i, date_info in enumerate(dates_info):
                print(f"[DEBUG] 日付{i+1}処理開始: {date_info}")
                date_str = date_info.get('date')
                start_time = date_info.get('time')
                end_time = date_info.get('end_time')
                
                print(f"[DEBUG] 日付{i+1}の抽出値: date={date_str}, start_time={start_time}, end_time={end_time}")
                
                if date_str and start_time and end_time:
                    try:
                        jst = pytz.timezone('Asia/Tokyo')
                        start_dt = jst.localize(datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M"))
                        end_dt = jst.localize(datetime.strptime(f"{date_str} {end_time}", "%Y-%m-%d %H:%M"))
                        
                        print(f"[DEBUG] 日付{i+1}のdatetime: start_dt={start_dt}, end_dt={end_dt}")
                        
                        # 枠内の予定を取得
                        print(f"[DEBUG] 日付{i+1}の予定取得開始")
                        events = self.calendar_service.get_events_for_time_range(start_dt, end_dt, line_user_id)
                        print(f"[DEBUG] 日付{i+1}の取得予定: {events}")
                        
                        # 8:00〜22:00の間で空き時間を返す
                        day_start = "08:00"
                        day_end = "22:00"
                        # 枠の範囲と8:00〜22:00の重なり部分だけを対象にする
                        slot_start = max(start_time, day_start)
                        slot_end = min(end_time, day_end)
                        
                        print(f"[DEBUG] 日付{i+1}のスロット範囲: slot_start={slot_start}, slot_end={slot_end}")
                        
                        slot_start_dt = jst.localize(datetime.strptime(f"{date_str} {slot_start}", "%Y-%m-%d %H:%M"))
                        slot_end_dt = jst.localize(datetime.strptime(f"{date_str} {slot_end}", "%Y-%m-%d %H:%M"))
                        
                        print(f"[DEBUG] 日付{i+1}のスロットdatetime: slot_start_dt={slot_start_dt}, slot_end_dt={slot_end_dt}")
                        
                        if slot_start < slot_end:
                            print(f"[DEBUG] 日付{i+1}の空き時間計算開始")
                            free_slots = self.calendar_service.find_free_slots_for_day(slot_start_dt, slot_end_dt, events)
                            print(f"[DEBUG] 日付{i+1}の空き時間結果: {free_slots}")
                        else:
                            print(f"[DEBUG] 日付{i+1}のスロット範囲が無効: {slot_start} >= {slot_end}")
                            free_slots = []
                        
                        free_slots_by_frame.append({
                            'date': date_str,
                            'start_time': slot_start,
                            'end_time': slot_end,
                            'free_slots': free_slots
                        })
                        print(f"[DEBUG] 日付{i+1}のfree_slots_by_frame追加完了")
                        
                    except Exception as e:
                        print(f"[DEBUG] 日付{i+1}処理でエラー: {e}")
                        import traceback
                        traceback.print_exc()
                        # エラーが発生しても他の日付は処理を続行
                        free_slots_by_frame.append({
                            'date': date_str,
                            'start_time': start_time,
                            'end_time': end_time,
                            'free_slots': []
                        })
                else:
                    print(f"[DEBUG] 日付{i+1}の必須項目が不足: date_str={date_str}, start_time={start_time}, end_time={end_time}")
            
            print(f"[DEBUG] 全日付処理完了、free_slots_by_frame: {free_slots_by_frame}")
            
            print(f"[DEBUG] format_free_slots_response_by_frame呼び出し")
            response_text = self.ai_service.format_free_slots_response_by_frame(free_slots_by_frame)
            print(f"[DEBUG] レスポンス生成完了: {response_text}")
            
            return TextSendMessage(text=response_text)
            
        except Exception as e:
            print(f"[DEBUG] _handle_availability_checkで例外発生: {e}")
            import traceback
            traceback.print_exc()
            return TextSendMessage(text=f"空き時間確認でエラーが発生しました: {str(e)}")
    
    def _handle_event_addition(self, user_message, line_user_id):
        """イベント追加を処理します"""
        try:
            # ユーザーの認証状態をチェック
            if not self._check_user_auth(line_user_id):
                return self._send_auth_guide(line_user_id)
            
            if not self.calendar_service:
                return TextSendMessage(text="Google Calendarサービスが初期化されていません。認証ファイルを確認してください。")
            
            if not self.ai_service:
                return TextSendMessage(text="AIサービスが初期化されていません。")
            
            # AIを使ってイベント情報を抽出
            event_info = self.ai_service.extract_event_info(user_message)
            
            if 'error' in event_info:
                # 日程のみの場合は空き時間確認として処理
                dates_info = self.ai_service.extract_dates_and_times(user_message)
                if 'error' not in dates_info and dates_info.get('dates'):
                    return self._handle_availability_check(dates_info.get('dates', []), line_user_id)
                
                return TextSendMessage(text="・日時を打つと空き時間を返します\n・予定を打つとカレンダーに追加します\n\n例：\n・「明日の空き時間」\n・「7/15 15:00〜16:00の空き時間」\n・「明日の午前9時から会議を追加して」\n・「来週月曜日の14時から打ち合わせ」")
            
            # 日時をパース
            start_datetime = parser.parse(event_info['start_datetime'])
            end_datetime = parser.parse(event_info['end_datetime'])
            
            # タイムゾーンを設定
            start_datetime = self.jst.localize(start_datetime)
            end_datetime = self.jst.localize(end_datetime)
            
            # カレンダーにイベントを追加
            success, message, result = self.calendar_service.add_event(
                event_info['title'],
                start_datetime,
                end_datetime,
                event_info.get('description', ''),
                line_user_id=line_user_id,
                force_add=True
            )
            logger.info(f"[DEBUG] add_event result: success={success}, message={message}, result={result}")
            
            # AIを使ってレスポンスをフォーマット
            response_text = self.ai_service.format_event_confirmation(success, message, result)
            
            return TextSendMessage(text=response_text)
            
        except Exception as e:
            return TextSendMessage(text=f"イベント追加でエラーが発生しました: {str(e)}")
    
    def get_handler(self):
        """WebhookHandlerを取得します"""
        return self.handler 