# 🚀 Trading Bot 빠른 시작 가이드

## ✅ 현재 상태
- Binance 테스트넷 계정 ✅
- Binance 실전 계정 ✅  
- 프로젝트 디렉토리: `.\tradeBot` ✅

---

## 📋 Step-by-Step 가이드

### Step 1: 디렉토리 구조 생성 (2분)

```cmd
cd tradeBot
setup_project.bat
```

또는 수동:
```cmd
mkdir config\strategies config\exchanges core strategies exchanges indicators\ict ai database monitoring logs
```

---

### Step 2: API 키 설정 (.env 파일)

```env
BINANCE_TESTNET_API_KEY=your_testnet_key
BINANCE_TESTNET_API_SECRET=your_testnet_secret
OLLAMA_URL=http://localhost:11434
```

---

### Step 3: 패키지 설치

```cmd
pip install ccxt python-binance pandas numpy psycopg2-binary websockets pyyaml python-dotenv requests colorlog
```

---

### Step 4: 코드 복사

```
exchanges/binance_testnet.py
strategies/base_strategy.py
strategies/ict_ai_strategy.py
```

---

### Step 5: 테스트

```python
from exchanges.binance_testnet import BinanceTestnet
exchange = BinanceTestnet()
exchange.test_connection()
```

---

## 🎯 다음 단계

준비되면 알려주세요!
