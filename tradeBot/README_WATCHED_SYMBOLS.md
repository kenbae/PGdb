# 감시 심볼 DB 관리

자동 분석할 심볼 목록을 DB에 저장하고 관리하는 기능입니다.

## 📋 개요

- **테이블**: `watched_symbols`
- **목적**: 서버 재시작 시에도 감시 심볼 목록 유지
- **자동 로드**: 서버 시작 시 DB에서 활성화된 심볼 자동 로드

---

## 🗄️ 테이블 구조

```sql
CREATE TABLE watched_symbols (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL DEFAULT '15m',
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(symbol)
);
```

**컬럼:**
- `id`: 자동 증가 ID
- `symbol`: 심볼 (예: BTCUSDT) - 유니크
- `timeframe`: 타임프레임 (기본값: 15m)
- `enabled`: 활성화 여부 (true/false)
- `created_at`: 생성 시간
- `updated_at`: 수정 시간 (자동 업데이트)

---

## 🚀 설치

### 1. 테이블 생성

```bash
cd PGdb/tradeBot
python setup_watched_symbols_table.py
```

**실행 결과:**
```
🚀 watched_symbols 테이블 생성 시작...
✅ DB 연결: localhost:5432/marketdb
✅ SQL 파일 로드: database/watched_symbols_schema.sql
✅ watched_symbols 테이블 생성 완료!

📋 현재 감시 심볼: 3개
  - BTCUSDT (15m) [enabled=True]
  - ETHUSDT (15m) [enabled=True]
  - SOLUSDT (15m) [enabled=True]
```

### 2. 테스트 실행

```bash
python test_watched_symbols.py
```

---

## 📡 API 엔드포인트

### 1. 감시 심볼 목록 조회

```http
GET /api/watched-symbols
```

**응답:**
```json
{
  "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
  "watched_list": [
    {
      "id": 1,
      "symbol": "BTCUSDT",
      "timeframe": "15m",
      "enabled": true,
      "created_at": "2026-01-20T10:00:00",
      "updated_at": "2026-01-20T10:00:00"
    }
  ],
  "timeframe": "15m",
  "interval": 300,
  "count": 3
}
```

### 2. 감시 심볼 추가

```http
POST /api/watched-symbols/add
Content-Type: application/json

{
  "symbol": "BNBUSDT",
  "timeframe": "15m"
}
```

**응답:**
```json
{
  "success": true,
  "symbol": "BNBUSDT",
  "timeframe": "15m",
  "watched_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
}
```

### 3. 감시 심볼 제거

```http
POST /api/watched-symbols/remove
Content-Type: application/json

{
  "symbol": "BNBUSDT"
}
```

**응답:**
```json
{
  "success": true,
  "symbol": "BNBUSDT",
  "watched_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
}
```

---

## 🔧 Python API (내부 사용)

### Repository 초기화

```python
from database.watched_symbols_repo import WatchedSymbolsRepo
from sqlalchemy import create_engine

engine = create_engine("postgresql://user:pass@host:port/db")
repo = WatchedSymbolsRepo(engine)
```

### 주요 메서드

```python
# 전체 조회
all_symbols = repo.get_all(enabled_only=False)

# 활성화된 심볼만 (리스트)
symbols = repo.get_symbols_list(enabled_only=True)
# → ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']

# 추가
repo.add('BNBUSDT', '15m', enabled=True)

# 삭제
repo.remove('BNBUSDT')

# 토글
new_state = repo.toggle('BTCUSDT')  # True → False 또는 False → True

# 활성화/비활성화
repo.set_enabled('BTCUSDT', enabled=False)

# 타임프레임 변경
repo.update_timeframe('BTCUSDT', '30m')

# 존재 확인
exists = repo.exists('BTCUSDT')  # True/False
```

---

## 🔄 서버 동작 플로우

### 서버 시작

1. DB 연결
2. `WatchedSymbolsRepo` 초기화
3. DB에서 활성화된 심볼 로드
4. `app_state.watched_symbols`에 저장
5. 백그라운드 분석 시작

```python
# lifespan 함수에서 자동 로드
symbols = watched_symbols_repo.get_symbols_list(enabled_only=True)
if symbols:
    app_state.watched_symbols = symbols
    logger.info(f"✅ DB에서 감시 심볼 로드: {symbols}")
```

### 심볼 추가 시

1. API 요청 → `POST /api/watched-symbols/add`
2. DB에 저장 (`watched_symbols` 테이블)
3. 메모리 상태 업데이트 (`app_state.watched_symbols`)
4. 다음 분석 주기에 자동 포함

### 서버 재시작 시

1. DB에서 자동 로드
2. 이전 상태 그대로 복원
3. 수동 설정 불필요

---

## 📝 예제

### 심볼 추가 (curl)

```bash
curl -X POST http://localhost:8888/api/watched-symbols/add \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BNBUSDT", "timeframe": "15m"}'
```

### 심볼 제거 (curl)

```bash
curl -X POST http://localhost:8888/api/watched-symbols/remove \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BNBUSDT"}'
```

### 목록 조회 (curl)

```bash
curl http://localhost:8888/api/watched-symbols
```

---

## ✅ 완료된 기능

- [x] DB 테이블 설계 및 생성
- [x] Repository 패턴 구현 (CRUD)
- [x] API 서버 통합
- [x] 서버 시작 시 자동 로드
- [x] 메모리-DB 동기화
- [x] REST API 엔드포인트
- [x] 테스트 스크립트

---

## 🎯 주요 장점

1. **영속성**: 서버 재시작해도 설정 유지
2. **중앙 관리**: DB에서 일괄 관리
3. **히스토리**: created_at, updated_at으로 추적
4. **확장성**: 타임프레임별 관리 가능
5. **유연성**: enabled 플래그로 임시 비활성화

---

## 📌 참고

- 기본 심볼: BTCUSDT, ETHUSDT, SOLUSDT
- 기본 타임프레임: 15m
- 기본 분석 주기: 300초 (5분)
- DB 없을 경우: 기본값 사용

---

## 🔍 트러블슈팅

### 테이블이 없을 때

```bash
python setup_watched_symbols_table.py
```

### 심볼이 로드 안 될 때

1. DB 연결 확인
2. `watched_symbols` 테이블 존재 확인
3. `enabled=true` 확인

```sql
SELECT * FROM watched_symbols WHERE enabled = true;
```

### 로그 확인

```
✅ DB에서 감시 심볼 로드: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
```

이 메시지가 보이면 정상 동작입니다.
