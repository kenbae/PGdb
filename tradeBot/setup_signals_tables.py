"""
signals 및 signal_results 테이블 생성 스크립트

실행:
    python setup_signals_tables.py
"""

import sys
import os
from pathlib import Path

# UTF-8 출력 설정 (Windows)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from core.config_loader import get_config

# .env 로드
load_dotenv()

def main():
    """메인 함수"""
    print("🚀 signals 및 signal_results 테이블 생성 시작...")

    # Config 로드
    config = get_config()

    # DB 연결
    db_url = f"postgresql://{config.get('db.user')}:{config.get('db.password')}@{config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}"
    engine = create_engine(db_url)

    print(f"✅ DB 연결: {config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}")

    # SQL 파일 읽기
    sql_file = Path(__file__).parent / "database" / "signals_schema.sql"

    if not sql_file.exists():
        print(f"❌ SQL 파일 없음: {sql_file}")
        return

    with open(sql_file, 'r', encoding='utf-8') as f:
        sql = f.read()

    print(f"✅ SQL 파일 로드: {sql_file}")

    # 실행
    try:
        # raw psycopg2 connection 사용
        import psycopg2

        conn_str = f"host={config.get('db.host')} port={config.get('db.port')} dbname={config.get('db.name')} user={config.get('db.user')} password={config.get('db.password')}"
        conn = psycopg2.connect(conn_str)
        cur = conn.cursor()

        # SQL 전체 실행
        cur.execute(sql)
        conn.commit()

        cur.close()
        conn.close()

        print("✅ signals 및 signal_results 테이블 생성 완료!")

        # 확인
        with engine.connect() as conn:
            # tradebot_signals 테이블
            result = conn.execute(text("SELECT COUNT(*) FROM tradebot_signals"))
            count = result.fetchone()[0]
            print(f"\n📋 tradebot_signals 테이블: {count}개 레코드")

            # tradebot_signal_results 테이블
            result = conn.execute(text("SELECT COUNT(*) FROM tradebot_signal_results"))
            count = result.fetchone()[0]
            print(f"📋 tradebot_signal_results 테이블: {count}개 레코드")

    except Exception as e:
        print(f"❌ 테이블 생성 실패: {e}")
        import traceback
        traceback.print_exc()

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
