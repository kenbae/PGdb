from core.websocket_feed import BinanceWebSocket
from core.websocket_saver import WebSocketDataSaver
from core.config_loader import get_config
import time
import logging

logging.basicConfig(level=logging.INFO)

print("=" * 70)
print("WebSocket → DB 저장 테스트")
print("=" * 70)
print()

# Config 로드
config = get_config()

# DB 설정
db_config = {
    'host': config.get('db.host'),
    'port': config.get('db.port'),
    'dbname': config.get('db.name'),
    'user': config.get('db.user'),
    'password': config.get('db.password')
}

print(f"DB: {db_config['host']}:{db_config['port']}/{db_config['dbname']}")
print()

# WebSocket 생성
ws = BinanceWebSocket(
    symbols=['BTCUSDT'],
    timeframes=['1m', '15m']
)

# DB 저장 시작
saver = WebSocketDataSaver(ws, db_config)
saver.start()

print("실행 중 (60초)...")
print("캔들 완성 시 DB에 저장됩니다.")
print()

try:
    time.sleep(60)
except KeyboardInterrupt:
    print("\n종료...")
finally:
    saver.stop()
    print("✅ 완료")