"""
총 신호 수 위젯
"""

from widgets.base_widget import BaseWidget
from sqlalchemy import text


class TotalSignalsWidget(BaseWidget):
    """총 신호 수를 표시하는 위젯"""
    
    @property
    def widget_id(self) -> str:
        return "total_signals"
    
    @property
    def widget_name(self) -> str:
        return "총 신호"
    
    @property
    def widget_icon(self) -> str:
        return "📊"
    
    @property
    def widget_color(self) -> str:
        return "blue"
    
    def get_data(self):
        """총 신호 수 계산"""
        query = text("""
            SELECT COUNT(*) as total
            FROM signals
        """)
        
        with self.engine.connect() as conn:
            result = conn.execute(query).fetchone()
        
        return {
            "value": result[0] if result else 0,
            "label": "개",
            "change": None,  # 변화율 (선택사항)
            "trend": None    # "up" or "down" (선택사항)
        }
