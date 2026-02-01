"""
데이터베이스 연결 수 위젯
현재 PostgreSQL 연결 수를 표시
"""

from widgets.base_widget import BaseWidget
from sqlalchemy import create_engine, text
import yaml


class DbConnectionWidget(BaseWidget):
    """데이터베이스 연결 수 위젯"""
    
    def __init__(self):
        super().__init__()
        self.widget_id = "db_connections"
        self.widget_name = "DB 연결 수"
        self.widget_icon = "🔌"
        self.widget_color = "purple"
        self.widget_size = "small"
        self.widget_category = "system"
    
    def get_data(self):
        """데이터베이스 연결 수 조회"""
        try:
            # config.yaml 로드
            with open("config.yaml", "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            
            db = config['db']
            dsn = f"postgresql://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['name']}"
            engine = create_engine(dsn)
            
            with engine.connect() as conn:
                # 현재 연결 수 조회
                result = conn.execute(text("""
                    SELECT 
                        COUNT(*) as total_connections,
                        COUNT(*) FILTER (WHERE state = 'active') as active_connections,
                        COUNT(*) FILTER (WHERE state = 'idle') as idle_connections,
                        (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') as max_connections
                    FROM pg_stat_activity
                    WHERE datname = :dbname
                """), {"dbname": db['name']})
                
                row = result.fetchone()
                
                total = row[0]
                active = row[1]
                idle = row[2]
                max_conn = row[3]
                
                # 사용률 계산
                usage_percent = (total / max_conn * 100) if max_conn > 0 else 0
                
                return {
                    "value": total,
                    "label": f"{total}/{max_conn}",
                    "subtitle": f"활성: {active}, 대기: {idle}",
                    "trend": "neutral",
                    "details": {
                        "total": total,
                        "active": active,
                        "idle": idle,
                        "max": max_conn,
                        "usage_percent": round(usage_percent, 1)
                    }
                }
        
        except Exception as e:
            return {
                "value": 0,
                "label": "에러",
                "subtitle": str(e)[:50],
                "trend": "down"
            }
