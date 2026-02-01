"""
TOP 10 위젯
TP 달성 / 누적 수익 / 월별 수익을 토글하여 볼 수 있는 위젯
"""

from widgets.base_widget import BaseWidget
from sqlalchemy import text
from datetime import datetime


class Top10Widget(BaseWidget):
    """TOP 10 심볼/전략 위젯 (토글 가능)"""
    
    @property
    def widget_id(self) -> str:
        return "top10"
    
    @property
    def widget_name(self) -> str:
        return "TOP 10"
    
    @property
    def widget_icon(self) -> str:
        return "🏆"
    
    @property
    def widget_color(self) -> str:
        return "yellow"
    
    @property
    def widget_size(self) -> str:
        return "large"
    
    @property
    def widget_category(self) -> str:
        return "analytics"
    
    def get_data(self):
        """TOP 10 데이터 조회 - 네 가지 뷰"""
        
        # 1. TP 달성 TOP 10 (TP1 + TP2 횟수)
        query_tp = text("""
            SELECT 
                s.symbol,
                COUNT(CASE WHEN o.result IN ('tp1', 'tp2') THEN 1 END) as tp_count,
                COUNT(CASE WHEN o.result = 'tp2' THEN 1 END) as tp2_count,
                COUNT(CASE WHEN o.result = 'tp1' THEN 1 END) as tp1_count,
                COUNT(*) as total_signals
            FROM signals s
            LEFT JOIN outcomes o ON o.signal_id = s.signal_id
            WHERE o.result IS NOT NULL
            GROUP BY s.symbol
            HAVING COUNT(CASE WHEN o.result IN ('tp1', 'tp2') THEN 1 END) > 0
            ORDER BY tp_count DESC
            LIMIT 10
        """)
        
        # 2. 누적 수익 TOP 10 (R-Multiple 합계)
        query_cumulative = text("""
            SELECT 
                s.symbol,
                ROUND(SUM(o.r_multiple)::numeric, 2) as total_r,
                COUNT(*) as signal_count,
                ROUND(AVG(o.r_multiple)::numeric, 2) as avg_r,
                COUNT(CASE WHEN o.result IN ('tp1', 'tp2') THEN 1 END) as wins
            FROM signals s
            LEFT JOIN outcomes o ON o.signal_id = s.signal_id
            WHERE o.r_multiple IS NOT NULL
            GROUP BY s.symbol
            HAVING SUM(o.r_multiple) > 0
            ORDER BY total_r DESC
            LIMIT 10
        """)
        
        # 3. 월별 수익 TOP 10 (이번 달)
        query_monthly = text("""
            SELECT 
                s.symbol,
                ROUND(SUM(o.r_multiple)::numeric, 2) as month_r,
                COUNT(*) as signal_count,
                ROUND(AVG(o.r_multiple)::numeric, 2) as avg_r,
                COUNT(CASE WHEN o.result IN ('tp1', 'tp2') THEN 1 END) as wins
            FROM signals s
            LEFT JOIN outcomes o ON o.signal_id = s.signal_id
            WHERE o.r_multiple IS NOT NULL
              AND DATE_TRUNC('month', s.open_time) = DATE_TRUNC('month', CURRENT_DATE)
            GROUP BY s.symbol
            HAVING SUM(o.r_multiple) > 0
            ORDER BY month_r DESC
            LIMIT 10
        """)
        
        # 4. 승률 TOP 10 (최소 5개 이상 신호)
        query_winrate = text("""
            SELECT 
                s.symbol,
                COUNT(CASE WHEN o.result IN ('tp1', 'tp2') THEN 1 END) as wins,
                COUNT(CASE WHEN o.result = 'sl' THEN 1 END) as losses,
                COUNT(*) as total_signals,
                ROUND(
                    (COUNT(CASE WHEN o.result IN ('tp1', 'tp2') THEN 1 END)::numeric * 100.0 / 
                    NULLIF(COUNT(*), 0))::numeric, 
                    1
                ) as win_rate,
                ROUND(AVG(o.r_multiple)::numeric, 2) as avg_r
            FROM signals s
            LEFT JOIN outcomes o ON o.signal_id = s.signal_id
            WHERE o.result IS NOT NULL
            GROUP BY s.symbol
            HAVING COUNT(*) >= 5
            ORDER BY win_rate DESC, total_signals DESC
            LIMIT 10
        """)
        
        with self.engine.connect() as conn:
            # TP 달성 데이터
            tp_results = conn.execute(query_tp).fetchall()
            tp_data = []
            for row in tp_results:
                tp_data.append({
                    "symbol": row[0],
                    "tp_count": row[1],
                    "tp2_count": row[2],
                    "tp1_count": row[3],
                    "total_signals": row[4],
                    "win_rate": round((row[1] / row[4] * 100) if row[4] > 0 else 0, 1)
                })
            
            # 누적 수익 데이터
            cumulative_results = conn.execute(query_cumulative).fetchall()
            cumulative_data = []
            for row in cumulative_results:
                cumulative_data.append({
                    "symbol": row[0],
                    "total_r": float(row[1]) if row[1] else 0,
                    "signal_count": row[2],
                    "avg_r": float(row[3]) if row[3] else 0,
                    "wins": row[4]
                })
            
            # 월별 수익 데이터
            monthly_results = conn.execute(query_monthly).fetchall()
            monthly_data = []
            for row in monthly_results:
                monthly_data.append({
                    "symbol": row[0],
                    "month_r": float(row[1]) if row[1] else 0,
                    "signal_count": row[2],
                    "avg_r": float(row[3]) if row[3] else 0,
                    "wins": row[4]
                })
            
            # 승률 데이터
            winrate_results = conn.execute(query_winrate).fetchall()
            winrate_data = []
            for row in winrate_results:
                winrate_data.append({
                    "symbol": row[0],
                    "wins": row[1],
                    "losses": row[2],
                    "total_signals": row[3],
                    "win_rate": float(row[4]) if row[4] else 0,
                    "avg_r": float(row[5]) if row[5] else 0
                })
        
        # 현재 월 정보 (인코딩 문제 방지)
        now = datetime.now()
        current_month = f"{now.year}-{now.month:02d}"
        
        return {
            "views": {
                "tp": {
                    "title": "TP Achievement TOP 10",
                    "data": tp_data,
                    "columns": ["Symbol", "TP", "TP2", "TP1", "Win%"]
                },
                "cumulative": {
                    "title": "Cumulative Profit TOP 10",
                    "data": cumulative_data,
                    "columns": ["Symbol", "Total R", "Signals", "Avg R", "Wins"]
                },
                "monthly": {
                    "title": f"Monthly Profit TOP 10 ({current_month})",
                    "data": monthly_data,
                    "columns": ["Symbol", "Month R", "Signals", "Avg R", "Wins"]
                },
                "winrate": {
                    "title": "Win Rate TOP 10 (Min 5 signals)",
                    "data": winrate_data,
                    "columns": ["Symbol", "Wins", "Losses", "Total", "Win%", "Avg R"]
                }
            },
            "default_view": "tp",
            "widget_type": "top10"  # 특별한 렌더링 타입
        }
