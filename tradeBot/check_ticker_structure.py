"""
Ticker 응답 구조 확인

어떤 키들이 있는지 확인
"""

import ccxt
import os
from dotenv import load_dotenv
import json

load_dotenv()

print("=" * 70)
print("Ticker 응답 구조 확인")
print("=" * 70)
print()

# 거래소 초기화
exchange = ccxt.binance({
    'apiKey': os.getenv('BINANCE_LIVE_API_KEY'),
    'secret': os.getenv('BINANCE_LIVE_API_SECRET'),
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',
        'adjustForTimeDifference': True
    }
})

exchange.set_sandbox_mode(False)

# Ticker 조회
print("BTC/USDT ticker 조회 중...")
print()

try:
    ticker = exchange.fetch_ticker('BTC/USDT')
    
    print("✅ 성공!")
    print()
    print("=" * 70)
    print("Ticker 데이터 구조:")
    print("=" * 70)
    print()
    
    # 전체 키 출력
    for key in sorted(ticker.keys()):
        value = ticker[key]
        
        # 숫자는 포맷팅
        if isinstance(value, (int, float)):
            print(f"{key:20s}: {value:,.4f}")
        else:
            print(f"{key:20s}: {value}")
    
    print()
    print("=" * 70)
    print("권장 사용법:")
    print("=" * 70)
    print()
    
    # 권장 키들
    recommended_keys = [
        ('last', '현재가'),
        ('bid', '매수호가'),
        ('ask', '매도호가'),
        ('high', '24h 최고가'),
        ('low', '24h 최저가'),
        ('baseVolume', 'BTC 거래량'),
        ('quoteVolume', 'USDT 거래량'),
        ('percentage', '24h 변동률'),
    ]
    
    for key, desc in recommended_keys:
        if key in ticker:
            value = ticker[key]
            if isinstance(value, (int, float)):
                print(f"{desc:15s} ({key:12s}): {value:,.4f}")
            else:
                print(f"{desc:15s} ({key:12s}): {value}")
    
    print()
    print("=" * 70)
    print("코드에서 사용:")
    print("=" * 70)
    print()
    print("ticker = exchange.fetch_ticker('BTC/USDT')")
    print()
    print("# 안전한 방법 (.get() 사용)")
    print("last = ticker.get('last', 0)")
    print("bid = ticker.get('bid', 0)")
    print("ask = ticker.get('ask', 0)")
    print("volume = ticker.get('baseVolume', ticker.get('quoteVolume', 0))")
    print()

except Exception as e:
    print(f"❌ 실패: {e}")
    import traceback
    traceback.print_exc()
