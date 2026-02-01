from core.websocket_feed import BinanceWebSocket
import time
import logging

logging.basicConfig(level=logging.INFO)

print("=" * 70)
print("WebSocket 테스트")
print("=" * 70)
print()

# WebSocket 생성
ws = BinanceWebSocket(
    symbols=['BTCUSDT', 'ETHUSDT'],
    timeframes=['1m', '15m', '1h']
)

# Ticker 콜백
def on_ticker(data):
    print(f"📊 [{data['symbol']}] ${data['last']:,.2f}")

# Kline 콜백
def on_kline(data):
    print(f"🕯️  [{data['symbol']}] {data['timeframe']} | "
          f"O:{data['open']:.2f} H:{data['high']:.2f} "
          f"L:{data['low']:.2f} C:{data['close']:.2f}")

# 콜백 등록
ws.on_ticker(on_ticker)
ws.on_kline(on_kline)

# 시작
ws.start()

print("실행 중 (60초)...")
print()

try:
    time.sleep(60)
except KeyboardInterrupt:
    print("\n종료...")
finally:
    ws.stop()
    print("✅ 완료")