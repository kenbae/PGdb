"""
빠른 디버깅 스크립트

어떤 심볼 형식이 작동하는지 확인
"""

import ccxt
import os
from dotenv import load_dotenv

load_dotenv()

# API 키
api_key = os.getenv('BINANCE_LIVE_API_KEY')
api_secret = os.getenv('BINANCE_LIVE_API_SECRET')

print("=" * 70)
print("Binance Futures 심볼 형식 테스트")
print("=" * 70)
print()

# 거래소 초기화
exchange = ccxt.binance({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',
        'adjustForTimeDifference': True
    }
})

# 실전 모드
exchange.set_sandbox_mode(False)

print("1. 시장 로드 중...")
try:
    markets = exchange.load_markets()
    print(f"✅ {len(markets)}개 시장 로드 완료")
    print()
except Exception as e:
    print(f"❌ 시장 로드 실패: {e}")
    exit(1)

print("2. BTC 관련 시장:")
btc_markets = [m for m in markets.keys() if 'BTC' in m and 'USDT' in m]
for market in btc_markets[:10]:
    print(f"   {market}")
print()

print("3. 가격 조회 테스트:")
print()

# 테스트할 형식들
test_symbols = [
    'BTC/USDT',
    'BTCUSDT',
    'BTC/USDT:USDT',  # Futures 영구 계약
    'BTCUSD_PERP',
]

for symbol in test_symbols:
    print(f"시도: '{symbol}'")
    
    try:
        ticker = exchange.fetch_ticker(symbol)
        print(f"  ✅ 성공! 현재가: ${ticker['last']:,.2f}")
    except Exception as e:
        error_msg = str(e)[:100]
        print(f"  ❌ 실패: {error_msg}")
    
    print()

print("=" * 70)
print("권장 형식:")
print()

# 작동하는 형식 찾기
working_symbols = []
for symbol in test_symbols:
    try:
        exchange.fetch_ticker(symbol)
        working_symbols.append(symbol)
    except:
        pass

if working_symbols:
    print(f"✅ 작동하는 형식: {working_symbols}")
    print()
    print(f"코드에서 사용하세요:")
    print(f"  symbol = '{working_symbols[0]}'")
else:
    print("❌ 작동하는 형식을 찾지 못했습니다.")
    print()
    print("대안:")
    print("1. Binance 웹사이트에서 심볼 확인")
    print("2. API 문서 확인: https://binance-docs.github.io/")
    print("3. defaultType 변경: 'spot' 또는 'future'")

print()
