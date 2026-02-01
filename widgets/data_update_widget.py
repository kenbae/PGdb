"""
데이터 업데이트 위젯
파이프라인 실행, 보고서 생성, 최신 업데이트 정보
"""

from widgets.base_widget import BaseWidget
from sqlalchemy import text
from datetime import datetime


class DataUpdateWidget(BaseWidget):
    """데이터 업데이트 및 작업 실행 위젯"""
    
    @property
    def widget_id(self) -> str:
        return "data_update"
    
    @property
    def widget_name(self) -> str:
        return "데이터 업데이트"
    
    @property
    def widget_icon(self) -> str:
        return "🔄"
    
    @property
    def widget_color(self) -> str:
        return "indigo"
    
    @property
    def widget_size(self) -> str:
        return "large"  # 큰 위젯
    
    @property
    def widget_category(self) -> str:
        return "control"  # 컨트롤 위젯
    
    def get_data(self):
        """최신 업데이트 정보 조회"""
        
        # 최신 신호 시간
        query_signal = text("""
            SELECT open_time
            FROM signals
            ORDER BY open_time DESC
            LIMIT 1
        """)
        
        # 최신 결과 시간
        query_outcome = text("""
            SELECT exit_time
            FROM outcomes
            WHERE exit_price IS NOT NULL
            ORDER BY exit_time DESC
            LIMIT 1
        """)
        
        # 오늘 생성된 신호 수 (최근 24시간)
        query_today = text("""
            SELECT COUNT(*) as today_count
            FROM signals
            WHERE open_time >= NOW() - INTERVAL '24 hours'
        """)
        
        with self.engine.connect() as conn:
            last_signal_row = conn.execute(query_signal).fetchone()
            last_outcome_row = conn.execute(query_outcome).fetchone()
            today_count_row = conn.execute(query_today).fetchone()
        
        last_signal_time = last_signal_row[0] if last_signal_row else None
        last_outcome_time = last_outcome_row[0] if last_outcome_row else None
        today_count = today_count_row[0] if today_count_row else 0
        
        # 시간 포맷팅
        if last_signal_time:
            # 타임존 aware datetime 처리
            if last_signal_time.tzinfo is not None:
                # UTC로 변환 후 naive로 만들기
                from datetime import timezone
                last_signal_naive = last_signal_time.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                last_signal_naive = last_signal_time
            
            signal_str = last_signal_naive.strftime('%Y-%m-%d %H:%M')
            time_diff = datetime.utcnow() - last_signal_naive
            minutes_ago = int(time_diff.total_seconds() / 60)
            
            if minutes_ago < 1:
                signal_ago = "방금 전"
            elif minutes_ago < 60:
                signal_ago = f"{minutes_ago}분 전"
            elif minutes_ago < 1440:
                hours = minutes_ago // 60
                signal_ago = f"{hours}시간 전"
            else:
                days = minutes_ago // 1440
                signal_ago = f"{days}일 전"
        else:
            signal_str = "없음"
            signal_ago = ""
        
        if last_outcome_time:
            # 타임존 aware datetime 처리
            if last_outcome_time.tzinfo is not None:
                from datetime import timezone
                last_outcome_naive = last_outcome_time.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                last_outcome_naive = last_outcome_time
            
            outcome_str = last_outcome_naive.strftime('%Y-%m-%d %H:%M')
        else:
            outcome_str = "없음"
        
        return {
            "value": today_count,
            "label": "개 (오늘)",
            "last_signal": signal_str,
            "signal_ago": signal_ago,
            "last_outcome": outcome_str,
            "buttons": [
                {
                    "id": "run_pipeline",
                    "label": "데이터 로드",
                    "icon": "▶️",
                    "color": "blue"
                },
                {
                    "id": "run_outcomes",
                    "label": "결과 계산",
                    "icon": "📊",
                    "color": "green"
                },
                {
                    "id": "generate_report",
                    "label": "보고서 생성",
                    "icon": "📄",
                    "color": "purple"
                }
            ]
        }
