# 1단계 검토 가이드

## 📍 확인 위치

### 1. 웹 UI (가장 쉬운 방법)

#### 신호 화면 (`/signals`)
- **URL**: `http://localhost:8888/signals`
- **확인 사항**:
  - 신호 목록이 표시되는지
  - 각 신호에 **"진입"** 버튼이 있는지
  - "진입" 버튼 클릭 시 모달이 열리는지
  - 진입 기록 후 DB에 저장되는지

#### 포지션 화면 (`/positions`)
- **URL**: `http://localhost:8888/positions`
- **확인 사항**:
  - 포지션 목록이 표시되는지
  - 각 포지션에 **"청산"** 버튼이 있는지 (Actions 컬럼)
  - "청산" 버튼 클릭 시 모달이 열리는지
  - 청산 기록 후 DB에 저장되는지

---

### 2. API로 직접 확인

#### 신호 목록
```bash
GET http://localhost:8888/api/signals/list?limit=10
```

#### 사용자 액션 목록
```bash
GET http://localhost:8888/api/actions/list?limit=10
# 인증 필요: Authorization: Bearer {token}
```

#### 이벤트 로그
```bash
GET http://localhost:8888/api/events/list?event_type=signal&limit=10
# 인증 필요: Authorization: Bearer {token}
```

#### 특정 신호의 이벤트
```bash
GET http://localhost:8888/api/events/list?event_type=user_action&limit=10
```

---

### 3. 데이터베이스 직접 확인

#### PostgreSQL 쿼리

```sql
-- 1. 신호 확인
SELECT signal_id, symbol, signal_type, created_at 
FROM tradebot_signals 
ORDER BY created_at DESC 
LIMIT 10;

-- 2. 사용자 액션 확인
SELECT action_id, signal_id, action_type, symbol, side, price, quantity, created_at
FROM tradebot_user_actions
ORDER BY created_at DESC
LIMIT 10;

-- 3. 이벤트 로그 확인
SELECT event_id, event_type, symbol, timestamp, data
FROM tradebot_events
ORDER BY timestamp DESC
LIMIT 10;

-- 4. 신호와 액션 연결 확인
SELECT 
    s.signal_id,
    s.symbol,
    s.signal_type,
    s.created_at as signal_time,
    a.action_id,
    a.action_type,
    a.price,
    a.quantity,
    a.created_at as action_time
FROM tradebot_signals s
LEFT JOIN tradebot_user_actions a ON s.signal_id = a.signal_id
ORDER BY s.created_at DESC
LIMIT 20;
```

---

## ✅ 검증 체크리스트

### 기본 기능
- [ ] 신호가 생성되고 `tradebot_signals` 테이블에 저장되는가?
- [ ] 신호 생성 시 `tradebot_events` 테이블에도 이벤트가 저장되는가?
- [ ] `signals.html`에서 "진입" 버튼이 보이는가?
- [ ] "진입" 버튼 클릭 시 모달이 열리고 데이터 입력이 가능한가?
- [ ] 진입 기록 후 `tradebot_user_actions` 테이블에 저장되는가?
- [ ] 진입 기록 시 `tradebot_events` 테이블에도 이벤트가 저장되는가?
- [ ] `positions.html`에서 "청산" 버튼이 보이는가?
- [ ] "청산" 버튼 클릭 시 모달이 열리고 데이터 입력이 가능한가?
- [ ] 청산 기록 후 `tradebot_user_actions` 테이블에 저장되는가?
- [ ] 청산 기록 시 `tradebot_events` 테이블에도 이벤트가 저장되는가?

### 데이터 연결
- [ ] `signal_id`로 신호와 액션이 연결되는가?
- [ ] 같은 `signal_id`로 여러 액션을 조회할 수 있는가?
- [ ] 이벤트 로그에서 신호와 액션을 모두 조회할 수 있는가?

---

## 🧪 테스트 시나리오

### 시나리오 1: 신호 생성 → 진입 기록
1. `analyzer_server`가 신호를 생성 (자동)
2. `http://localhost:8888/signals` 접속
3. 신호 목록에서 "진입" 버튼 클릭
4. 모달에서 정보 입력 (가격, 수량, 이유 등)
5. "기록 저장" 클릭
6. DB 확인:
   ```sql
   SELECT * FROM tradebot_user_actions WHERE action_type = 'enter' ORDER BY created_at DESC LIMIT 1;
   SELECT * FROM tradebot_events WHERE event_type = 'user_action' ORDER BY timestamp DESC LIMIT 1;
   ```

### 시나리오 2: 포지션 → 청산 기록
1. `http://localhost:8888/positions` 접속
2. 포지션 목록에서 "청산" 버튼 클릭
3. 모달에서 정보 입력
4. "기록 저장" 클릭
5. DB 확인:
   ```sql
   SELECT * FROM tradebot_user_actions WHERE action_type = 'exit' ORDER BY created_at DESC LIMIT 1;
   ```

---

## 🔍 문제 해결

### "진입" 버튼이 안 보이는 경우
- 브라우저 개발자 도구(F12) → Console 탭에서 에러 확인
- `signals.html` 파일이 최신 버전인지 확인

### API 호출 실패
- 인증 토큰이 필요한 API는 `Authorization: Bearer {token}` 헤더 필요
- 서버 로그 확인: `tradeBot` 서버 콘솔 출력 확인

### DB에 데이터가 없는 경우
- Repository 초기화 확인: 서버 시작 시 로그에서 "✅ tradebot_user_actions 테이블 확인/생성 완료" 메시지 확인
- 테이블이 없으면 자동 생성되지만, 수동 확인:
  ```sql
  SELECT table_name FROM information_schema.tables 
  WHERE table_name IN ('tradebot_events', 'tradebot_user_actions');
  ```

---

## 📊 빠른 확인 명령어

### curl로 API 테스트
```bash
# 신호 목록
curl http://localhost:8888/api/signals/list?limit=5

# 액션 목록 (인증 필요)
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8888/api/actions/list?limit=5

# 이벤트 로그 (인증 필요)
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8888/api/events/list?limit=5
```

### Python으로 빠른 확인
```python
import requests

# 신호 목록
response = requests.get("http://localhost:8888/api/signals/list?limit=5")
print(response.json())

# 액션 목록 (인증 필요)
headers = {"Authorization": "Bearer YOUR_TOKEN"}
response = requests.get("http://localhost:8888/api/actions/list?limit=5", headers=headers)
print(response.json())
```
