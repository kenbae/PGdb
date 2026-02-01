"""
API 서버 감시 심볼 기능 테스트

사용법:
1. 서버 시작: python api_server.py
2. 테스트 실행: python test_api_watched_symbols.py
"""

import sys
import requests
import json
from time import sleep

# UTF-8 출력 설정 (Windows)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_URL = "http://localhost:8888"


def test_get_watched_symbols():
    """감시 심볼 조회 테스트"""
    print("=" * 60)
    print("1. 감시 심볼 조회")
    print("=" * 60)

    response = requests.get(f"{BASE_URL}/api/watched-symbols")
    data = response.json()

    print(f"상태 코드: {response.status_code}")
    print(f"현재 활성화된 심볼: {data['symbols']}")
    print(f"타임프레임: {data['timeframe']}")
    print(f"분석 주기: {data['interval']}초")
    print(f"전체 감시 목록: {len(data.get('watched_list', []))}개")

    if 'watched_list' in data:
        print("\n전체 목록:")
        for item in data['watched_list']:
            print(f"  - {item['symbol']:10} {item['timeframe']:5} enabled={item['enabled']}")

    print()
    return data


def test_add_watched_symbol(symbol, timeframe="15m"):
    """감시 심볼 추가 테스트"""
    print("=" * 60)
    print(f"2. 감시 심볼 추가: {symbol} ({timeframe})")
    print("=" * 60)

    response = requests.post(
        f"{BASE_URL}/api/watched-symbols/add",
        params={"symbol": symbol, "timeframe": timeframe}
    )
    data = response.json()

    print(f"상태 코드: {response.status_code}")
    print(f"성공: {data['success']}")
    print(f"현재 감시 심볼: {data['watched_symbols']}")
    print()
    return data


def test_remove_watched_symbol(symbol):
    """감시 심볼 제거 테스트"""
    print("=" * 60)
    print(f"3. 감시 심볼 제거: {symbol}")
    print("=" * 60)

    response = requests.post(
        f"{BASE_URL}/api/watched-symbols/remove",
        params={"symbol": symbol}
    )
    data = response.json()

    print(f"상태 코드: {response.status_code}")
    print(f"성공: {data['success']}")
    print(f"현재 감시 심볼: {data['watched_symbols']}")
    print()
    return data


def main():
    """메인 함수"""
    print("🧪 API 서버 감시 심볼 기능 테스트\n")

    try:
        # 1. 초기 상태 확인
        data = test_get_watched_symbols()
        initial_symbols = data['symbols'].copy()

        # 2. 심볼 추가 테스트
        test_add_watched_symbol("BNBUSDT", "15m")
        sleep(0.5)

        # 3. 추가 확인
        data = test_get_watched_symbols()
        assert "BNBUSDT" in data['symbols'], "BNBUSDT가 추가되지 않았습니다!"
        print("✅ 심볼 추가 성공 확인\n")

        # 4. 심볼 제거 테스트
        test_remove_watched_symbol("BNBUSDT")
        sleep(0.5)

        # 5. 제거 확인
        data = test_get_watched_symbols()
        assert "BNBUSDT" not in data['symbols'], "BNBUSDT가 제거되지 않았습니다!"
        print("✅ 심볼 제거 성공 확인\n")

        # 6. 최종 상태 확인
        print("=" * 60)
        print("4. 최종 상태")
        print("=" * 60)
        print(f"초기 심볼: {initial_symbols}")
        print(f"현재 심볼: {data['symbols']}")
        print(f"일치 여부: {initial_symbols == data['symbols']}")
        print()

        print("✅ 모든 테스트 통과!")

    except requests.exceptions.ConnectionError:
        print("❌ 서버에 연결할 수 없습니다.")
        print("   먼저 'python api_server.py'로 서버를 시작하세요.")
    except Exception as e:
        print(f"❌ 테스트 실패: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
