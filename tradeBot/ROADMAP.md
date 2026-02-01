# 자동매매 시스템 개발 로드맵

## 🎯 목표
보수적이고 확장 가능한 ICT + AI 자동매매 시스템

---

## 📅 Phase 1: 인프라 구축 (Week 1-2)

### Week 1: 거래소 연동

#### Day 1-2: Binance 테스트넷
```
✅ 할 일:
1. Binance 테스트넷 계정 생성
2. API 키 발급
3. 테스트넷 API 연동
   - REST API (계좌 조회, 주문)
   - WebSocket (실시간 가격)

📁 파일:
- exchanges/binance_testnet.py
- .env (API 키 저장)

✅ 테스트:
- 계좌 잔고 조회
- 시장가 주문 실행
- WebSocket 실시간 데이터 수신
```

#### Day 3-4: 데이터 피드
```
✅ 할 일:
1. WebSocket 캔들 데이터 수신
2. DB 저장 (실시간)
3. 캔들 종료 이벤트 발생

📁 파일:
- core/data_feed.py
- database/repositories/candle_repo.py

✅ 테스트:
- 1h, 4h 캔들 실시간 수신
- DB 저장 확인
- 이벤트 발생 확인
```

#### Day 5-7: 주문 관리자
```
✅ 할 일:
1. 주문 생성/취소/조회
2. 포지션 사이즈 계산
3. 손절/익절 주문 설정

📁 파일:
- core/order_manager.py
- core/position_manager.py

✅ 테스트:
- 시장가/지정가 주문
- 포지션 추적
- 자동 손절/익절
```

### Week 2: 리스크 관리 & 이벤트 시스템

#### Day 8-10: 리스크 관리자
```
✅ 할 일:
1. 계좌의 2% 리스크 제한
2. 최대 4개 포지션 제한
3. 일일 손실 5% 제한
4. 포지션 사이즈 검증

📁 파일:
- core/risk_manager.py

✅ 테스트:
- 리스크 초과 시 거부
- 손실 한도 도달 시 중단
```

#### Day 11-14: 이벤트 버스
```
✅ 할 일:
1. 이벤트 버스 구현
2. 전략 ↔ 주문 관리자 통신
3. 로깅 시스템

📁 파일:
- core/event_bus.py
- core/engine.py (메인 엔진)
- monitoring/logger.py

✅ 테스트:
- 이벤트 발행/구독
- 전략 신호 → 주문 실행
```

---

## 📅 Phase 2: ICT + AI 전략 (Week 3-4)

### Week 3: ICT 지표 구현

#### Day 15-17: Order Blocks & FVG
```
✅ 할 일:
1. Order Blocks 식별
2. Fair Value Gaps 식별
3. 유효성 검증

📁 파일:
- indicators/ict/order_blocks.py
- indicators/ict/fair_value_gaps.py

✅ 테스트:
- 과거 데이터로 검증
- 실시간 식별 테스트
```

#### Day 18-21: Liquidity & Market Structure
```
✅ 할 일:
1. BSL/SSL 레벨 식별
2. 시장 구조 분석 (HH, HL, LH, LL)
3. 통합 ICT 분석기

📁 파일:
- indicators/ict/liquidity_levels.py
- indicators/ict/market_structure.py

✅ 테스트:
- 실시간 구조 분석
- 신호 생성 테스트
```

### Week 4: AI 통합

#### Day 22-25: OLLAMA 통합
```
✅ 할 일:
1. OLLAMA API 클라이언트
2. 분석 프롬프트 템플릿
3. 신뢰도 점수 시스템

📁 파일:
- ai/ollama_client.py
- ai/prompt_templates.py
- ai/confidence_scorer.py

✅ 테스트:
- AI 분석 요청/응답
- 신뢰도 점수 검증
```

#### Day 26-28: ICT + AI 전략 완성
```
✅ 할 일:
1. ICT 구조 → AI 분석
2. 신호 생성 로직
3. 전략 테스트

📁 파일:
- strategies/ict_ai_strategy.py ✅ (이미 작성됨)

✅ 테스트:
- 더미 데이터 테스트
- 과거 데이터 검증
```

---

## 📅 Phase 3: 백테스트 & 검증 (Week 5)

### Week 5: 백테스트

#### Day 29-31: 백테스트 엔진
```
✅ 할 일:
1. 백테스트 엔진 구현
2. ICT + AI 전략 백테스트
3. 성과 리포트 생성

📁 파일:
- backtesting/backtest_engine.py
- backtesting/report_generator.py

✅ 테스트:
- 2023-2024년 데이터
- 승률, 샤프, MDD 확인
```

#### Day 32-35: 최적화
```
✅ 할 일:
1. 파라미터 최적화
2. Walk-Forward 테스트
3. 과적합 방지

📁 파일:
- backtesting/optimizer.py

✅ 목표:
- 승률 > 45%
- 샤프 > 1.2
- MDD < -25%
```

---

## 📅 Phase 4: 페이퍼 트레이딩 (Week 6-9)

### Week 6-7: 페이퍼 트레이딩 시작

#### Day 36-49: 테스트넷 실행
```
✅ 할 일:
1. 전략 배포 (테스트넷)
2. 실시간 모니터링
3. 성과 추적

실행:
python main.py --mode testnet --strategy ict_ai --symbols BTCUSDT ETHUSDT

✅ 모니터링:
- 일일 성과 체크
- 신호 품질 분석
- 버그 수정
```

### Week 8-9: 모니터링 시스템

#### Day 50-63: 대시보드 & 알림
```
✅ 할 일:
1. 실시간 대시보드
2. 텔레그램 알림
3. 성과 트래킹

📁 파일:
- monitoring/dashboard.py
- monitoring/alerts.py
- monitoring/metrics.py

✅ 기능:
- 실시간 포지션 표시
- PnL 그래프
- 진입/청산 알림
```

---

## 📅 Phase 5: 소액 실전 (Week 10-13)

### Week 10: 실전 준비

#### Day 64-70: 실전 API 연동
```
✅ 할 일:
1. Binance 실전 API 연동
2. 안전장치 추가
3. 긴급 정지 버튼

📁 파일:
- exchanges/binance.py
- scripts/emergency_stop.py

✅ 체크리스트:
- [ ] API 키 안전하게 저장
- [ ] Rate Limit 준수
- [ ] 에러 처리 완벽
- [ ] 긴급 정지 테스트
```

### Week 11-13: 소액 실전 ($100-$500)

#### Day 71-91: 실전 운영
```
✅ 목표:
- 초기 자본: $100-$500
- 심볼: BTCUSDT, ETHUSDT
- 전략: ICT + AI

실행:
python main.py --mode live --strategy ict_ai --capital 500 --symbols BTCUSDT ETHUSDT

✅ 모니터링:
- 일일 성과 분석
- AI 신호 품질
- 리스크 관리 확인

✅ 성공 기준:
- 3주 연속 수익
- MDD < -15%
- 승률 > 40%
```

---

## 📅 Phase 6: 확장 & 최적화 (Week 14+)

### Week 14+: 증액 & 멀티 전략

```
✅ 할 일:
1. 검증된 전략 증액
   - $500 → $1,000 → $5,000
   
2. 추가 전략 개발
   - Turtle Strategy
   - EMA Cross Strategy
   - 멀티 전략 포트폴리오

3. 추가 심볼 확장
   - 메이저 10개 코인
   - 섹터별 분산

4. 자동화 개선
   - 자동 재조정
   - 동적 파라미터
   - 시장 레짐 감지
```

---

## 📊 마일스톤

### Milestone 1: 인프라 (Week 2)
```
✅ Binance 테스트넷 연동
✅ 실시간 데이터 피드
✅ 주문 실행 시스템
✅ 리스크 관리 시스템
```

### Milestone 2: ICT + AI (Week 4)
```
✅ ICT 지표 완성
✅ OLLAMA AI 통합
✅ 전략 구현 완료
```

### Milestone 3: 검증 (Week 5)
```
✅ 백테스트 통과
✅ 파라미터 최적화
✅ 과적합 방지 확인
```

### Milestone 4: 페이퍼 (Week 9)
```
✅ 4주 페이퍼 트레이딩
✅ 안정적인 성과
✅ 모니터링 시스템 완성
```

### Milestone 5: 실전 (Week 13)
```
✅ 소액 실전 3주
✅ 일관된 수익
✅ 리스크 관리 검증
```

### Milestone 6: 확장 (Week 14+)
```
✅ 자본 증액
✅ 멀티 전략
✅ 포트폴리오 관리
```

---

## 🎯 다음 액션 아이템

### 즉시 시작 가능:

#### 1. Binance 테스트넷 계정 생성 (10분)
```
1. https://testnet.binance.vision 접속
2. 계정 생성
3. API 키 발급
4. 테스트 자금 받기 (무료)
```

#### 2. 프로젝트 구조 생성 (30분)
```bash
mkdir -p trading_system/{core,strategies,exchanges,indicators,ai,database,monitoring,backtesting}
cd trading_system
touch main.py
```

#### 3. 환경 설정 (20분)
```bash
# requirements.txt
pip install ccxt pandas numpy pyyaml requests python-telegram-bot websockets

# .env
BINANCE_TESTNET_API_KEY=your_key
BINANCE_TESTNET_API_SECRET=your_secret
OLLAMA_URL=http://localhost:11434
```

#### 4. 첫 번째 코드 작성 (1시간)
```python
# exchanges/binance_testnet.py
# 테스트넷 API 연동 코드 작성
```

---

## ⚠️ 주의사항

### 절대 규칙:
```
1. 테스트넷 먼저, 실전은 나중에
2. 백테스트 통과 전까지 실전 금지
3. 페이퍼 트레이딩 4주 필수
4. 소액($100-$500)으로 시작
5. 리스크 관리 철저히 (2% 룰)
```

### 개발 원칙:
```
1. 작은 단계로 나누기
2. 각 단계마다 테스트
3. 로깅 철저히
4. 에러 처리 완벽하게
5. 코드 리뷰 & 문서화
```

---

## ✅ 완료!

**다음 단계:**
1. Binance 테스트넷 계정 생성
2. 프로젝트 디렉토리 생성
3. `exchanges/binance_testnet.py` 작성

**시작할까요?** 🚀
