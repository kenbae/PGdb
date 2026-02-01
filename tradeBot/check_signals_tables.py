"""신호 테이블 확인"""
import sys
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from core.config_loader import get_config

load_dotenv()
config = get_config()

db_url = f"postgresql://{config.get('db.user')}:{config.get('db.password')}@{config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}"
engine = create_engine(db_url)

with engine.connect() as conn:
    # 테이블 목록
    result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name LIKE 'signal%'"))
    tables = [r[0] for r in result]
    print(f"신호 관련 테이블: {tables}")

    # signals 테이블 확인
    if 'signals' in tables:
        result = conn.execute(text("SELECT COUNT(*) FROM signals"))
        count = result.fetchone()[0]
        print(f"signals 테이블: {count}개 레코드")

    # signal_results 테이블 확인
    if 'signal_results' in tables:
        result = conn.execute(text("SELECT COUNT(*) FROM signal_results"))
        count = result.fetchone()[0]
        print(f"signal_results 테이블: {count}개 레코드")

engine.dispose()
