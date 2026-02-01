"""
watched_symbols Repository 테스트

실행:
    python test_watched_symbols.py
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

from sqlalchemy import create_engine
from dotenv import load_dotenv
from core.config_loader import get_config
from database.watched_symbols_repo import WatchedSymbolsRepo

# .env 로드
load_dotenv()


def main():
    """메인 함수"""
    print("🧪 watched_symbols Repository 테스트 시작...\n")

    # Config 로드
    config = get_config()

    # DB 연결
    db_url = f"postgresql://{config.get('db.user')}:{config.get('db.password')}@{config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}"
    engine = create_engine(db_url)

    print(f"✅ DB 연결: {config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}\n")

    # Repository 초기화
    repo = WatchedSymbolsRepo(engine)

    # 1. 전체 조회
    print("=" * 60)
    print("1. 전체 감시 심볼 조회")
    print("=" * 60)
    all_symbols = repo.get_all(enabled_only=False)
    print(f"전체: {len(all_symbols)}개")
    for s in all_symbols:
        print(f"  - {s['symbol']:10} {s['timeframe']:5} enabled={s['enabled']}")
    print()

    # 2. 활성화된 심볼만 조회
    print("=" * 60)
    print("2. 활성화된 심볼만 조회")
    print("=" * 60)
    enabled_symbols = repo.get_symbols_list(enabled_only=True)
    print(f"활성화: {enabled_symbols}")
    print()

    # 3. 심볼 추가
    print("=" * 60)
    print("3. 심볼 추가 테스트")
    print("=" * 60)
    test_symbol = "BNBUSDT"
    success = repo.add(test_symbol, "15m", enabled=True)
    print(f"추가: {test_symbol} → {success}")
    print()

    # 4. 심볼 존재 확인
    print("=" * 60)
    print("4. 심볼 존재 확인")
    print("=" * 60)
    exists = repo.exists(test_symbol)
    print(f"{test_symbol} 존재: {exists}")
    print()

    # 5. 타임프레임 변경
    print("=" * 60)
    print("5. 타임프레임 변경")
    print("=" * 60)
    success = repo.update_timeframe(test_symbol, "30m")
    print(f"{test_symbol} 타임프레임 → 30m: {success}")
    print()

    # 6. 토글
    print("=" * 60)
    print("6. 활성화/비활성화 토글")
    print("=" * 60)
    new_state = repo.toggle(test_symbol)
    print(f"{test_symbol} 토글 → {new_state}")
    print()

    # 7. 활성화 설정
    print("=" * 60)
    print("7. 활성화 설정")
    print("=" * 60)
    success = repo.set_enabled(test_symbol, enabled=True)
    print(f"{test_symbol} 활성화 → {success}")
    print()

    # 8. 최종 상태 확인
    print("=" * 60)
    print("8. 최종 상태 확인")
    print("=" * 60)
    enabled_symbols = repo.get_symbols_list(enabled_only=True)
    print(f"활성화된 심볼: {enabled_symbols}")
    print()

    # 9. 심볼 삭제 (테스트 데이터 정리)
    print("=" * 60)
    print("9. 테스트 심볼 삭제")
    print("=" * 60)
    success = repo.remove(test_symbol)
    print(f"{test_symbol} 삭제 → {success}")
    print()

    # 10. 최종 확인
    print("=" * 60)
    print("10. 최종 확인")
    print("=" * 60)
    all_symbols = repo.get_all(enabled_only=False)
    print(f"전체: {len(all_symbols)}개")
    for s in all_symbols:
        print(f"  - {s['symbol']:10} {s['timeframe']:5} enabled={s['enabled']}")

    # 종료
    engine.dispose()
    print("\n✅ 테스트 완료!")


if __name__ == "__main__":
    main()
