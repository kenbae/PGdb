# 변경 이력

## 2026-01-20

### 손익 차트 기능 추가

**다양한 기간별 손익 분석:**
- 시간별 추이 (최근 7일) - 시간 흐름에 따른 손익 변화
- 일별 (최근 30일) - 일별 손익 추이
- 월별 (최근 12개월) - 월별 손익 추이
- **요일별 합계** - 선택한 기간의 모든 거래를 요일별(일~토)로 집계
- **시간대별 합계 (0~23시)** - 선택한 기간의 모든 거래를 시간대별로 집계하여 최적 거래 시간 파악
- 심볼별 손익 및 승률 분석 차트
- 차트 필터를 통한 특정 심볼 분석 가능
- **날짜 범위 필터 (From-To)** - 시작일/종료일 지정으로 원하는 기간 분석

**차트 구성:**
- 손익 추이 차트: 막대 그래프(손익) + 선 그래프(누적 손익)
- 심볼별 손익 차트: 가로 막대 그래프로 심볼 비교
- 심볼별 승률 차트: 가로 막대 그래프로 승률 시각화
- 요일별/시간대별 분석으로 최적의 거래 타이밍 파악
- 날짜 범위 지정으로 특정 기간의 거래 패턴 집중 분석

**기술 스택:**
- Chart.js 4.4.1 사용
- 듀얼 Y축으로 손익과 누적 손익 동시 표시
- PostgreSQL DATE_TRUNC 및 EXTRACT 함수를 이용한 시간 분석
- 요일별 승률 및 수익 패턴 시각화
- HTML5 date input을 통한 직관적인 날짜 선택

**파일:**
- [database/positions_repo.py](database/positions_repo.py:320-700) - 시간별/월별/요일별/시간대별/심볼별 손익 조회 메서드 (start_date/end_date 파라미터 지원)
- [api_server.py](api_server.py:797-1018) - 차트 데이터 API 엔드포인트 (날짜 필터 지원)
- [static/positions.html](static/positions.html:578-611,1078-1110) - Chart.js 및 날짜 필터 UI 구현

---

## 2026-01-20

### 바이낸스 동기화 개선

**동기화 범위 수정:**
- `since` 파라미터 제거하여 최신 거래 1000개 조회
- 기존: 30일 이내 거래만 조회 (12/28까지만 가져옴)
- 개선: 최근 1000개 거래 조회 (1/20 최신 데이터까지 가져옴)
- 조회 개수를 500개에서 1000개로 증가

**파일:**
- [services/binance_sync.py](services/binance_sync.py:45-51) - since 파라미터 제거, limit 증가

---

### 포지션 그룹화 및 UI 개선

**포지션 그룹화:**
- 오픈 시간이 같은 포지션들을 자동으로 합쳐서 한 줄로 표시
- SQL GROUP BY를 사용하여 같은 심볼, 방향, 오픈 시간의 거래를 통합
- 거래 수량, 손익, 수수료는 합산
- 진입가, 종료가, 손익률은 평균값 표시
- "거래 수" 컬럼 추가로 몇 개의 거래가 합쳐졌는지 표시

**자동 새로고침 제거:**
- 마이 포지션 페이지의 30초 자동 새로고침 제거
- 신호 리스트 페이지의 30초 자동 새로고침 제거
- 수동 새로고침만 지원 (필요시 사용자가 직접 새로고침)

**파일:**
- [database/positions_repo.py](database/positions_repo.py:158-217) - 포지션 그룹화 쿼리
- [static/positions.html](static/positions.html:542-556,735-752) - 거래 수 컬럼 및 UI
- [static/signals.html](static/signals.html:886-887) - 자동 새로고침 제거

---

### 바이낸스 동기화 기능 추가 + 서비스 레이어 분리

**마이 포지션 페이지에 바이낸스 거래 히스토리 동기화 기능 추가:**

**서비스 레이어 분리:**
- `BinanceSyncService` 클래스 생성 - 바이낸스 동기화 비즈니스 로직 분리
- API 서버 경량화 (120줄 → 30줄)
- 코드 재사용성 및 테스트 용이성 향상

**거래소 전역 관리:**
- `exchange` 전역 변수 추가
- 서버 시작시 자동으로 BinanceLive 인스턴스 생성 및 연결
- API 엔드포인트에서 전역 exchange 인스턴스 사용

**API 엔드포인트:**
- `POST /api/positions/sync-binance?days=30`: 바이낸스에서 최근 N일 거래 히스토리 조회 및 DB 저장

**기능:**
- 바이낸스 거래 히스토리를 자동으로 가져와서 포지션으로 변환
- 매수/매도 거래를 FIFO 방식으로 매칭하여 포지션 생성
- 손익, 손익률, 수수료, 지속시간 자동 계산
- 중복 방지 (position_id 기반)
- DB에 자동 저장

**UI:**
- 마이 포지션 페이지 헤더에 "🔄 바이낸스 동기화" 버튼 추가 (바이낸스 색상)
- 클릭시 최근 30일 거래 데이터 자동 가져오기
- 로딩 상태 표시 및 결과 알림
- 동기화 완료 후 자동으로 데이터 새로고침

**파일:**
- [services/binance_sync.py](services/binance_sync.py) - 바이낸스 동기화 서비스 (신규)
- [services/__init__.py](services/__init__.py) - 서비스 패키지 초기화 (신규)
- [api_server.py](api_server.py:829-862) - `/api/positions/sync-binance` 엔드포인트 (서비스 사용)
- [static/positions.html](static/positions.html:449-451,596-628) - 동기화 버튼 및 함수 추가

---

### SQL 파라미터 구문 오류 수정

**문제:**
- `pd.read_sql()`에서 SQLAlchemy 파라미터 구문(`:param`)을 사용하여 PostgreSQL 구문 오류 발생
- 신호 리스트 조회, 통계 조회 등 여러 쿼리에서 오류 발생

**해결:**
- PostgreSQL/pandas 호환 구문(`%(param)s`)으로 변경
- [database/signals_repo.py](database/signals_repo.py) 전체 쿼리 수정
  - `get_signals()`: 신호 목록 조회 쿼리 수정
  - `get_signal_by_id()`: 신호 상세 조회 쿼리 수정
  - `get_pending_signals()`: 대기 중인 신호 조회 쿼리 수정
  - `get_statistics()`: 통계 조회 쿼리 수정
- [database/positions_repo.py](database/positions_repo.py) 전체 쿼리 수정
  - `get_positions()`: 포지션 목록 조회 쿼리 수정
  - `get_position_by_id()`: 포지션 상세 조회 쿼리 수정
  - `get_statistics()`: 포지션 통계 조회 쿼리 수정
  - `get_daily_pnl()`: 일별 손익 조회 쿼리 수정

---

### 네비게이션 메뉴 추가

**모든 페이지에 통합 네비게이션 추가:**
- 메인 대시보드 ([index.html](static/index.html)): 홈, 신호 리스트, 마이 포지션 메뉴 추가
- 신호 리스트 페이지 ([signals.html](static/signals.html)): 홈, 마이 포지션 링크 추가
- 마이 포지션 페이지 ([positions.html](static/positions.html)): 홈, 신호 리스트 링크 추가

**기능:**
- 세 페이지 간 원활한 이동 가능
- 일관된 UI/UX (이모지 아이콘 포함)
- 호버 효과 및 반응형 디자인

---

### 마이 포지션 - 바이낸스 거래 히스토리 시스템 구축 완료

#### 1. 데이터베이스 설계 및 구현

**테이블 생성:**
- `tradebot_positions`: 포지션 히스토리 테이블
  - 기본 정보 (심볼, 방향, 상태)
  - 가격 정보 (진입가, 종료가, 수량)
  - 손익 정보 (PnL, 손익률, 수수료)
  - 레버리지 및 마진
  - 시간 정보 (오픈/종료 시간, 지속시간)
  - 메타데이터 및 바이낸스 원본 데이터 (JSONB)

**파일:**
- `database/positions_schema.sql`
- `database/positions_repo.py`
- `setup_positions_table.py`

#### 2. 바이낸스 API 연동

**BinanceLive 클래스 확장:**
- `get_trade_history()`: 거래 히스토리 조회
- `get_income_history()`: 수입 히스토리 조회 (실현 손익, 수수료 등)

**위치:**
- `exchanges/binance_live.py`

#### 3. REST API 엔드포인트

**포지션 조회:**
```
GET /api/positions/list
  - 필터링: symbol, side, status, start_date, end_date
  - 페이징: limit, offset
```

**포지션 상세:**
```
GET /api/positions/{position_id}
  - 포지션 상세 정보 조회
```

**통계:**
```
GET /api/positions/stats
  - 승률, 평균 손익, 총 수익, 수수료 등
  - 필터링: symbol, days
```

**일별 손익:**
```
GET /api/positions/daily-pnl
  - 일별 거래 수, 손익, 수수료
  - 필터링: symbol, days
```

#### 4. 웹 대시보드

**마이 포지션 페이지:**
- URL: http://localhost:8888/positions
- 파일: `static/positions.html`

**기능:**
- 포지션 테이블 (페이징 50개/페이지)
- 실시간 통계 카드 (전체 포지션, 승률, 손익, 수수료 등)
- 필터링 (심볼, 방향, 상태)
- 포지션 상세 모달
- 탭: 포지션 리스트 / 일별 손익 차트 (예정)
- 30초 자동 새로고침

**UI:**
- 반응형 디자인
- 색상 코딩 (LONG/SHORT, 진행중/종료/청산)
- PnL 양수/음수 표시
- 지속시간 자동 포맷 (분/시간/일)

#### 5. 문서화

**파일:**
- `README_POSITIONS.md`: 마이 포지션 시스템 전체 가이드
- `CHANGELOG.md`: 변경 이력 업데이트

---

### 신호 추적 시스템 구축 완료

#### 1. 데이터베이스 설계 및 구현

**테이블 생성:**
- `tradebot_signals`: 신호 저장 테이블
  - 신호 기본 정보 (심볼, 타임프레임, 타입)
  - 가격 정보 (진입가, 손절, 익절)
  - 신뢰도 및 R:R
  - AI 분석 결과 (결정, 신뢰도, 근거)
  - JSONB 메타데이터

- `tradebot_signal_results`: 결과 저장 테이블
  - 결과 상태 (pending, tp1_hit, tp2_hit, sl_hit, expired)
  - 성과 지표 (PnL, PnL %, R-Multiple)
  - 최대/최소 움직임 (MFE, MAE)
  - 시뮬레이션 여부

**파일:**
- `database/signals_schema.sql`
- `database/signals_repo.py`
- `setup_signals_tables.py`

#### 2. 백그라운드 작업 구현

**신호 자동 저장:**
- AI 승인된 신호만 자동으로 DB 저장
- 초기 결과 레코드 생성 (pending 상태)
- 5분마다 실행

**결과 자동 업데이트:**
- 최근 72시간 내 pending 신호 조회
- 캔들 데이터 분석하여 TP/SL 달성 여부 확인
- PnL, R-Multiple, MFE, MAE 자동 계산
- DB에 결과 저장
- 5분마다 실행

**위치:**
- `api_server.py:background_analyzer()` (신호 저장)
- `api_server.py:background_result_updater()` (결과 업데이트)

#### 3. REST API 엔드포인트

**신호 조회:**
```
GET /api/signals/list
  - 필터링: symbol, timeframe, signal_type, ai_decision, start_date, end_date
  - 페이징: limit, offset
```

**신호 상세:**
```
GET /api/signals/{signal_id}
  - AI 분석 상세 정보 포함
```

**통계:**
```
GET /api/signals/stats
  - 전체 신호 수, 승률, 평균 PnL, R-Multiple
  - 필터링: symbol, timeframe, days
```

**현재 메모리 신호:**
```
GET /api/signals/current
  - 최근 분석 결과 (기존 /api/signals에서 변경)
```

#### 4. 웹 대시보드

**신호 리스트 페이지:**
- URL: http://localhost:8888/signals
- 파일: `static/signals.html`

**기능:**
- 신호 테이블 (페이징 50개/페이지)
- 실시간 통계 카드 (전체, 승률, PnL 등)
- 필터링 (심볼, 타입, AI 결정, 결과 상태)
- 신호 상세 모달
- 30초 자동 새로고침

**UI:**
- 반응형 디자인
- 색상 코딩 (매수/매도, 승인/거부/주의, TP/SL)
- PnL 양수/음수 표시

#### 5. 감시 심볼 DB 관리

**이전 작업 (같은 날 완료):**

**테이블 생성:**
- `watched_symbols`: 감시 심볼 관리
  - symbol, timeframe, enabled
  - 서버 재시작시 자동 로드

**Repository:**
- `database/watched_symbols_repo.py`
- CRUD 작업: get_all(), add(), remove(), toggle()

**API:**
```
GET /api/watched-symbols/list
POST /api/watched-symbols/add
POST /api/watched-symbols/remove
```

#### 문제 해결

**1. SQL 파싱 오류:**
- 문제: 세미콜론으로 분리시 PostgreSQL 함수 정의 깨짐
- 해결: raw psycopg2 사용하여 SQL 파일 전체 실행

**2. UTF-8 인코딩 오류:**
- 문제: Windows 콘솔에서 이모지 출력 실패
- 해결: `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`

**3. 테이블 이름 충돌:**
- 문제: 기존 PGdb의 `signals` 테이블과 충돌
- 해결: `tradebot_signals`, `tradebot_signal_results`로 이름 변경

**4. API 라우트 충돌:**
- 문제: `/api/signals/{signal_id}`와 `/api/signals/stats` 순서
- 해결: stats를 signal_id 앞으로 이동

**5. 미사용 변수 경고:**
- 문제: `idx`, `close` 미사용
- 해결: `idx` → `_`, `close` 제거

## 문서화

- `README_SIGNALS.md`: 신호 시스템 전체 가이드
- `CHANGELOG.md`: 변경 이력 (이 문서)

## 다음 작업 제안

1. AI 피드백 루프 구현
   - 승인/거부 신호 결과 분석
   - 학습 데이터 구성
   - AI 모델 개선

2. 알림 기능
   - TP/SL 달성시 알림
   - Telegram/Discord 연동

3. 백테스팅
   - 과거 데이터로 전략 검증
   - 성과 시뮬레이션

4. 포트폴리오 관리
   - 실제 거래 연동
   - 자금 관리
   - 리스크 관리
