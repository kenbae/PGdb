# 실시간 트레이딩 개발 가이드

## 📁 개발용 폴더 구조

```
PGdb/
├── tradeBot/                    # 운영 시스템 (현재)
│   ├── services/
│   ├── brokers/
│   ├── static/
│   └── servers/
│
└── tradeBot_dev/                # 개발용 폴더 (새로 생성)
    ├── services/
    │   └── realtime_trading_engine.py  # 새로 개발
    ├── static/
    │   └── realtime_trading.html       # 새로 개발
    ├── servers/
    │   └── realtime_api.py             # 새 API 엔드포인트 (테스트용)
    └── README.md                       # 개발 가이드
```

---

## 🔄 개발 및 통합 프로세스

### Phase 1: 개발 (tradeBot_dev/)
1. `tradeBot_dev/` 폴더 생성
2. 필요한 파일들 개발
3. 독립적으로 테스트

### Phase 2: 테스트
1. 개발용 서버로 테스트
2. 기능 검증
3. 버그 수정

### Phase 3: 통합
1. 테스트 완료 후 운영 시스템으로 이동
2. 기존 시스템과 통합
3. 최종 검증

---

## 📝 개발 체크리스트

### Backend
- [ ] `tradeBot_dev/services/realtime_trading_engine.py` 구현
- [ ] `tradeBot_dev/servers/realtime_api.py` 구현 (테스트용 서버)
- [ ] 운영 시스템 통합 준비

### Frontend
- [ ] `tradeBot_dev/static/realtime_trading.html` 구현
- [ ] UI 테스트
- [ ] 운영 시스템 통합 준비

---

## 🚀 개발 시작

1. 개발용 폴더 생성
2. 필요한 파일들 개발
3. 독립 테스트
4. 운영 시스템 통합
