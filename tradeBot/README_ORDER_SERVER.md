# 주문 서버 분리 아키텍처

실시간 신호 분석과 주문 실행을 분리하여 주문 실행 지연을 최소화합니다.

## 📊 아키텍처

```
┌─────────────────────────────────┐
│  웹 서버 (web_server.py)        │
│  포트: 8888                      │
│  - API 엔드포인트 제공           │
│  - 대시보드 UI                   │
│  - WebSocket 실시간 통신         │
└────────────┬────────────────────┘
             │ HTTP 요청
             ▼
┌─────────────────────────────────┐
│ 주문 실행 서버 (order_server.py) │
│  포트: 8889                      │
│  - 주문 실행만 담당              │
│  - 최소 지연                     │
│  - 바이낸스 API 직접 연결        │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│  분석 서버 (analyzer_server.py) │
│  포트: 8887                      │
│  - 정시 분석 (0/15/30/45분)     │
│  - DB 저장                       │
│  - 텔레그램 알림                 │
└─────────────────────────────────┘
```

## 🎯 장점

1. **최소 지연**: 주문 서버는 오직 주문 실행만 처리하여 지연 최소화
2. **안정성**: 신호 분석 서버가 느려져도 주문 실행에 영향 없음
3. **독립성**: 각 서버를 독립적으로 재시작/배포 가능
4. **확장성**: 필요시 여러 주문 서버 운영 가능

## 🚀 사용 방법

### 1. 주문 서버 시작 (포트 8889)

```bash
cd PGdb/tradeBot
python order_server.py
```

**로그 예시:**
```
INFO:     🚀 주문 서버 시작...
INFO:     ✅ Config 로드
INFO:     ✅ 바이낸스 거래소 연결
INFO:     ✅ 잔고: $100.00
INFO:     🎉 주문 서버 준비 완료!
INFO:     Uvicorn running on http://0.0.0.0:8889
```

### 2. 웹 서버 시작 (포트 8888)

```bash
cd PGdb/tradeBot
python servers/web_server.py
```

**로그 예시:**
```
INFO:     🚀 웹 서버 시작 중...
INFO:     ✅ Config 로드
INFO:     ✅ DB 연결
INFO:     ✅ Repository 초기화
INFO:     🎉 웹 서버 준비 완료! (포트 8888)
```

### 3. 분석 서버 시작 (포트 8887, 선택사항)

```bash
cd PGdb/tradeBot
python servers/analyzer_server.py
```

**로그 예시:**
```
INFO:     🚀 분석 서버 시작 (포트 8887)
INFO:     ✅ Config 로드
INFO:     ✅ DB 연결
INFO:     ✅ 감시 심볼 로드
INFO:     ⏰ 정시 스케줄러 시작
```

### 4. 브라우저에서 확인

- **웹 대시보드**: http://localhost:8888
- **신호 페이지**: http://localhost:8888/signals
- **주문 서버 헬스체크**: http://localhost:8889/health
- **분석 서버 헬스체크**: http://localhost:8887/health

## 📡 API 엔드포인트

### 주문 서버 (8889)

#### POST /api/orders/create
주문 생성 (시장가/지정가)

**Request:**
```json
{
  "symbol": "BTCUSDT",
  "side": "buy",
  "order_type": "market",
  "quantity": 0.001,
  "stop_loss": 50000,
  "take_profit": 55000,
  "leverage": 10,
  "signal_id": "signal_123",
  "strategy": "ICT+AI"
}
```

**Response:**
```json
{
  "success": true,
  "order": {
    "id": "1234567890",
    "symbol": "BTCUSDT",
    "side": "buy",
    "status": "filled"
  },
  "message": "BTCUSDT buy 주문 완료"
}
```

#### POST /api/orders/close
포지션 종료

**Request:**
```json
{
  "symbol": "BTCUSDT",
  "side": "sell",
  "quantity": 0.001,
  "signal_id": "signal_123",
  "reason": "tp"
}
```

#### GET /api/orders/active?symbol=BTCUSDT
활성 주문 조회

#### DELETE /api/orders/cancel/{order_id}?symbol=BTCUSDT
주문 취소

#### GET /api/positions/current?symbol=BTCUSDT
현재 포지션 조회

#### GET /api/balance
계좌 잔고 조회

#### GET /health
헬스 체크

### 웹 서버 (8888)

#### POST /api/trade
거래 실행 (주문 서버로 전달)

**Request:**
```json
{
  "symbol": "BTCUSDT",
  "side": "buy",
  "entry_price": 52000,
  "stop_loss": 50000,
  "take_profit": 55000,
  "quantity": 0.001,
  "leverage": 10,
  "signal_id": "signal_123"
}
```

이 요청은 자동으로 주문 서버의 `/api/orders/create`로 전달됩니다.

## 🔧 주문 클라이언트 사용 예시

웹 서버나 분석 서버에서 주문 서버로 요청을 보낼 때:

```python
from services.order_client import OrderClient

# 클라이언트 초기화
order_client = OrderClient(order_server_url="http://localhost:8889")

# 주문 생성
result = await order_client.create_order(
    symbol="BTCUSDT",
    side="buy",
    order_type="market",
    quantity=0.001,
    stop_loss=50000,
    take_profit=55000,
    leverage=10,
    signal_id="signal_123"
)

if result['success']:
    print(f"✅ 주문 성공: {result['message']}")
else:
    print(f"❌ 주문 실패: {result['error']}")

# 포지션 종료
result = await order_client.close_position(
    symbol="BTCUSDT",
    side="sell",
    quantity=0.001,
    reason="tp"
)

# 주문 서버 연결 확인
healthy = await order_client.health_check()
if healthy:
    print("✅ 주문 서버 정상")
else:
    print("❌ 주문 서버 연결 실패")
```

## 🛡️ 에러 처리

주문 서버가 연결되지 않은 경우:
- 웹 서버는 데모 모드로 동작
- 로그에 경고 메시지 출력: `⚠️  주문 서버 연결 실패 - 주문 기능 비활성화`
- `/api/trade` 요청은 성공하지만 실제 주문은 실행되지 않음

주문 실패 시:
- OrderClient가 에러를 캐치하여 `success: false` 반환
- 로그에 에러 메시지 출력
- 신호 분석 서버는 계속 동작

## 📝 주의사항

1. **실행 순서**: 주문 서버를 먼저 시작한 후 웹 서버를 시작하는 것을 권장
2. **포트 충돌**: 
   - 8887: 분석 서버
   - 8888: 웹 서버
   - 8889: 주문 서버
3. **타임아웃**: HTTP 요청 타임아웃은 10초로 설정 (order_client.py에서 변경 가능)
4. **데모 모드**: 주문 서버 없이도 웹 서버는 정상 동작 (데모 모드)

## 🔄 재시작 시나리오

### 주문 서버만 재시작
```bash
# 주문 서버 종료 (Ctrl+C)
# 주문 서버 재시작
python order_server.py
```
→ 신호 분석 서버는 그대로 유지, 다음 주문부터 정상 작동

### 웹 서버만 재시작
```bash
# 웹 서버 종료 (Ctrl+C)
# 웹 서버 재시작
python servers/web_server.py
```
→ 주문 서버는 그대로 유지, 재연결 자동 수행

### 분석 서버만 재시작
```bash
# 분석 서버 종료 (Ctrl+C)
# 분석 서버 재시작
python servers/analyzer_server.py
```
→ 다른 서버는 그대로 유지, 정시 분석 계속 진행

## 🧪 테스트

### 헬스 체크
```bash
curl http://localhost:8889/health
```

### 주문 테스트 (주문 서버 직접)
```bash
curl -X POST http://localhost:8889/api/orders/create \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTCUSDT",
    "side": "buy",
    "order_type": "market",
    "quantity": 0.001,
    "stop_loss": 50000,
    "take_profit": 55000
  }'
```

### 주문 테스트 (웹 서버 경유)
```bash
curl -X POST http://localhost:8888/api/trade \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTCUSDT",
    "side": "buy",
    "entry_price": 52000,
    "stop_loss": 50000,
    "take_profit": 55000,
    "quantity": 0.001
  }'
```

## 📊 모니터링

### 로그 확인
- **주문 서버**: 주문 실행 로그만 출력 (깔끔함)
- **웹 서버**: API 요청 + WebSocket 로그 출력
- **분석 서버**: 신호 분석 + DB 저장 + 텔레그램 알림 로그 출력

### 성능
- 주문 서버는 최소한의 로직만 수행하므로 매우 빠름
- 웹 서버와 분석 서버의 무거운 작업이 주문 실행에 영향 없음

## 🔮 향후 개선 사항

1. **메시지 큐 도입** (Redis Pub/Sub, RabbitMQ)
   - 더 안정적인 비동기 처리
   - 주문 대기열 관리

2. **로드 밸런싱**
   - 여러 주문 서버 운영
   - 트래픽 분산

3. **주문 재시도**
   - 실패 시 자동 재시도
   - Exponential backoff

4. **상태 동기화**
   - 주문 서버 상태를 신호 분석 서버와 동기화
   - WebSocket 또는 주기적 폴링

## 📚 관련 파일

- `order_server.py` - 주문 실행 전용 서버 (포트 8889)
- `servers/web_server.py` - 웹 서버 (포트 8888)
- `servers/analyzer_server.py` - 분석 서버 (포트 8887)
- `services/order_client.py` - 주문 클라이언트 (HTTP 요청)
- `exchanges/binance_live.py` - 바이낸스 API 래퍼

## ❓ FAQ

**Q: 주문 서버가 다운되면?**
A: 웹 서버는 데모 모드로 계속 동작하며, 주문 서버가 복구되면 자동으로 재연결됩니다.

**Q: 세 서버를 하나의 서버로 통합할 수 있나요?**
A: 네, 가능합니다. 하지만 서버 분리의 장점(독립성, 성능, 확장성)을 잃게 됩니다. 신호 분석의 무거운 작업이 주문 실행에 지연을 줄 수 있습니다.

**Q: api_server.py는 어디 있나요?**
A: `api_server.py`는 더 이상 사용하지 않습니다. 기능이 다음으로 분리되었습니다:
- `servers/web_server.py` - API + 대시보드
- `servers/analyzer_server.py` - 분석 + DB 저장
- `order_server.py` - 주문 실행

**Q: 레버리지는 어떻게 설정하나요?**
A: 주문 요청 시 `leverage` 파라미터를 전달하면 주문 서버가 자동으로 설정합니다.

**Q: 포지션을 수동으로 종료하려면?**
A: `/api/orders/close` 엔드포인트를 사용하거나, 바이낸스 웹/앱에서 직접 종료할 수 있습니다.
