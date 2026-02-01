# 🚨 실전 자동매매 시작 가이드

## ⚠️ 매우 중요!

### 실전으로 바로 시작하기로 결정하셨으므로, 다음 안전 조치를 **반드시** 따라주세요.

---

## 🛡️ Step 1: API 키 안전 설정 (필수!)

### Binance API 키 생성

1. **Binance 로그인** → API Management
2. **API 키 생성**
3. **권한 설정 (매우 중요!)**
   ```
   ✅ Enable Reading (읽기 - 필수)
   ✅ Enable Spot & Margin Trading (거래 - 필수)
   ✅ Enable Futures (선물 - 필요시)
   ❌ Enable Withdrawals (출금 - 절대 비활성화!)
   ❌ Enable Internal Transfer (내부 이체 - 비활성화!)
   ```

4. **IP 제한 설정 (강력 권장)**
   - "Restrict access to trusted IPs only" 체크
   - 현재 IP 추가: https://whatismyipaddress.com
   - 여러 위치에서 접속 시 모두 추가

5. **2FA 인증 필수**
   - Google Authenticator 또는 SMS

---

## 💰 Step 2: 초기 자본 설정 (보수적!)

### 권장 시작 금액:

```
🟢 초보자: $100 - $200
🟡 경험자: $200 - $500
🔴 전문가: $500 - $1,000

⚠️ 절대 규칙:
- 잃어도 괜찮은 돈만 사용
- 전체 자산의 1-2%만 사용
- 레버리지 1-3배 이하
```

### Binance Futures 계좌에 소액 입금

```
1. Binance 지갑 → Futures 계좌
2. USDT 이체 ($100-$500)
3. 나머지는 Spot 계좌에 보관
```

---

## 📝 Step 3: .env 파일 설정

```env
# Binance Live (실전)
BINANCE_LIVE_API_KEY=your_real_api_key_here
BINANCE_LIVE_API_SECRET=your_real_api_secret_here

# 안전 설정
INITIAL_CAPITAL=100          # 초기 자본 (달러)
MAX_POSITIONS=2              # 최대 포지션 2개
MAX_LEVERAGE=3               # 최대 레버리지 3배
RISK_PER_TRADE=0.01          # 거래당 1% 리스크
MAX_DAILY_LOSS=0.02          # 일일 최대 2% 손실

# OLLAMA
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct

# PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_NAME=marketdb
DB_USER=trader
DB_PASSWORD=your_password
```

---

## 🧪 Step 4: 연결 테스트

### test_live.py 생성:

```python
from dotenv import load_dotenv
load_dotenv()

from exchanges.binance_live import BinanceLive
import logging

logging.basicConfig(level=logging.INFO)

print("⚠️⚠️⚠️ 실전 연결 테스트 ⚠️⚠️⚠️")
print()

# 연결 (초기 자본 $100)
exchange = BinanceLive(
    initial_capital=100.0,
    max_positions=2,
    max_daily_loss=0.02
)

# 테스트
if exchange.test_connection():
    print("\n✅ 연결 성공!")
    
    # 잔고
    balance = exchange.get_balance()
    print(f"\n💰 USDT 잔고: ${balance['total']:,.2f}")
    
    # 안전 점검
    if exchange.check_safety():
        print("🛡️ 안전 점검 통과")
    else:
        print("⚠️ 거래 불가 상태")
    
    # 포지션
    positions = exchange.get_positions()
    print(f"📈 열린 포지션: {len(positions)}개")
    
else:
    print("\n❌ 연결 실패!")
```

**실행:**
```cmd
python test_live.py
```

---

## 🎯 Step 5: 첫 번째 거래 (매우 조심스럽게)

### 초소량 테스트 거래:

```python
# test_trade.py
from dotenv import load_dotenv
load_dotenv()

from exchanges.binance_live import BinanceLive
import logging

logging.basicConfig(level=logging.INFO)

exchange = BinanceLive(initial_capital=100.0)

# 현재가 확인
ticker = exchange.get_ticker('BTC/USDT')
print(f"BTC 현재가: ${ticker['last']:,.2f}")

# 레버리지 설정 (1배)
exchange.set_leverage('BTC/USDT', 1)

# 초소량 매수 (0.001 BTC ≈ $43)
print("\n⚠️ 초소량 매수 테스트...")
response = input("계속하시겠습니까? (yes/no): ")

if response.lower() == 'yes':
    order = exchange.create_market_order(
        symbol='BTC/USDT',
        side='buy',
        amount=0.001
    )
    
    if order:
        print(f"✅ 주문 성공: {order['id']}")
        
        # 손절 설정 (2% 손실)
        stop_price = ticker['last'] * 0.98
        exchange.set_stop_loss(
            symbol='BTC/USDT',
            side='sell',
            amount=0.001,
            stop_price=stop_price
        )
        
        print(f"✅ 손절 설정: ${stop_price:.2f}")
    else:
        print("❌ 주문 실패")
else:
    print("취소됨")
```

---

## 🛡️ 안전장치 (코드에 내장)

### 1. 최대 포지션 제한
```python
max_positions = 2  # 최대 2개만
```

### 2. 일일 손실 한도
```python
max_daily_loss = 0.02  # 2% 손실 시 자동 중단
```

### 3. 긴급 정지
```python
# 문제 발생 시
exchange.activate_emergency_stop()
# → 모든 포지션 종료 + 거래 중지
```

### 4. 모든 거래 로깅
```
logs/trades_20260119.log
→ 모든 주문 기록
```

---

## ⚠️ 실전 규칙 (절대 지킬 것!)

### Rule 1: 손절 없는 거래 금지
```python
# 모든 주문에 손절 필수
order = exchange.create_market_order(...)
exchange.set_stop_loss(...)  # 필수!
```

### Rule 2: 레버리지 제한
```python
# 최대 3배 (코드에서 강제)
exchange.set_leverage('BTC/USDT', 3)
```

### Rule 3: 일일 손실 2% 도달 시 중단
```python
# 자동으로 체크
if not exchange.check_safety():
    # 거래 불가
```

### Rule 4: 소액으로 시작
```python
# $100-$200으로 시작
initial_capital = 100.0
```

### Rule 5: 매일 성과 확인
```python
# 일일 손익
pnl = exchange.get_daily_pnl()
print(f"오늘 손익: ${pnl['pnl']:.2f} ({pnl['pnl_pct']:.2f}%)")
```

---

## 📊 실전 운영 체크리스트

### 매일:
```
⬜ 아침: 포지션 확인
⬜ 아침: 일일 카운터 리셋
⬜ 저녁: 성과 확인
⬜ 저녁: 로그 확인
```

### 매주:
```
⬜ 주간 성과 분석
⬜ 전략 파라미터 조정
⬜ 리스크 관리 점검
```

### 매월:
```
⬜ 월간 성과 리포트
⬜ 자본 증액 여부 결정
⬜ 전략 최적화
```

---

## 🚨 긴급 상황 대처

### 문제 발생 시:

1. **즉시 긴급 정지**
   ```python
   exchange.activate_emergency_stop()
   ```

2. **수동으로 모든 포지션 종료**
   - Binance 웹사이트 접속
   - Futures → Positions
   - "Close All" 클릭

3. **API 키 비활성화**
   - Binance → API Management
   - API 키 삭제 또는 비활성화

---

## 💡 첫 주 목표

### Week 1: 생존 & 학습

```
목표:
✅ 시스템이 안전하게 작동하는지 확인
✅ 1-2개 거래 경험
✅ 손절/익절 자동화 확인
✅ 버그 없는지 확인

성공 기준:
- 손실 < $10 (10%)
- 시스템 안정성 확인
- 로그 정상 작동
```

---

## 📈 증액 기준

### 다음 단계로 가는 조건:

```
4주 연속 성공:
✅ 주간 수익 > 0%
✅ MDD < -5%
✅ 승률 > 40%
✅ 시스템 안정성

→ $100 → $200 → $500 → $1,000
```

---

## ⚠️ 최종 경고

```
🚨 실전은 테스트와 다릅니다!
🚨 심리적 압박이 있습니다!
🚨 손실은 실제 돈입니다!

권장:
1. 소액으로 시작 ($100-$200)
2. 천천히 증액
3. 감정 통제
4. 규칙 준수
```

---

## ✅ 준비 완료 체크

```
⬜ Binance API 키 생성 (권한 제한)
⬜ IP 제한 설정
⬜ 2FA 인증 활성화
⬜ Futures 계좌에 $100-$200 입금
⬜ .env 파일 작성
⬜ binance_live.py 복사
⬜ test_live.py 실행 (연결 테스트)
⬜ 안전장치 이해
⬜ 긴급 정지 방법 숙지
```

---

## 🚀 시작할 준비가 되셨나요?

**다음 단계:**
1. API 키 설정
2. .env 파일 작성
3. 연결 테스트
4. 첫 거래 (초소량)

**지금 어느 단계에 계신가요?** 

도움이 필요하시면 말씀해주세요! 🛡️
