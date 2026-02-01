"""
전략 성과 추적 테이블 생성 스크립트

실행:
    python setup_strategy_performance.py
"""

import sys
import os
from pathlib import Path
from sqlalchemy import create_engine, text, inspect
from dotenv import load_dotenv

# UTF-8 출력 설정 (Windows)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 프로젝트 경로 설정
_current_dir = os.path.dirname(os.path.abspath(__file__))  # tradeBot_dev/database
_tradebot_dev_dir = os.path.dirname(_current_dir)  # tradeBot_dev
_pgdb_dir = os.path.dirname(_tradebot_dev_dir)  # PGdb
_tradebot_dir = os.path.join(_pgdb_dir, 'tradeBot')  # tradeBot (운영 시스템)

# 경로 추가 (운영 시스템 모듈 사용)
sys.path.insert(0, _tradebot_dir)
sys.path.insert(0, _pgdb_dir)

from core.config_loader import get_config

# .env 로드
load_dotenv()

def setup_tables():
    """테이블 생성"""
    try:
        print("🚀 전략 성과 추적 테이블 생성 시작...")
        
        # Config 로드
        config = get_config()
        
        # DB 연결
        db_url = f"postgresql://{config.get('db.user')}:{config.get('db.password')}@{config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}"
        engine = create_engine(db_url)

        print(f"✅ DB 연결: {config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}")

        # SQL 파일 읽기
        sql_file = Path(_current_dir) / 'create_strategy_performance_tables.sql'
        
        if not sql_file.exists():
            print(f"❌ SQL 파일 없음: {sql_file}")
            return
        
        with open(sql_file, 'r', encoding='utf-8') as f:
            sql = f.read()

        print(f"✅ SQL 파일 로드: {sql_file}")

        # SQL 실행 (psycopg2 직접 사용 - 함수 정의 등 복잡한 SQL 처리)
        try:
            import psycopg2

            conn_str = f"host={config.get('db.host')} port={config.get('db.port')} dbname={config.get('db.name')} user={config.get('db.user')} password={config.get('db.password')}"
            conn = psycopg2.connect(conn_str)
            cur = conn.cursor()

            # SQL 전체 실행
            cur.execute(sql)
            conn.commit()

            cur.close()
            conn.close()

            print("✅ SQL 실행 완료")
        except Exception as e:
            # 이미 존재하는 경우 무시
            if 'already exists' not in str(e).lower() and 'duplicate' not in str(e).lower():
                print(f"⚠️ SQL 실행 경고: {e}")
                import traceback
                traceback.print_exc()

        print("✅ 전략 성과 추적 테이블 생성 완료!")

        # 테이블 확인
        inspector = inspect(engine)
        tables = inspector.get_table_names()

        if 'strategy_performance' in tables:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM strategy_performance"))
                count = result.scalar()
                print(f"📋 strategy_performance 테이블: {count}개 레코드")

        if 'strategy_switches' in tables:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM strategy_switches"))
                count = result.scalar()
                print(f"📋 strategy_switches 테이블: {count}개 레코드")

    except Exception as e:
        print(f"❌ 테이블 생성 실패: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == '__main__':
    setup_tables()
