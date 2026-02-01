# 실시간 트레이딩 개발 폴더

이 폴더는 단일 심볼 실시간 트레이딩 기능을 개발하고 테스트하는 공간입니다.

## 📁 구조

```
tradeBot_dev/
├── services/
│   └── realtime_trading_engine.py  # 실시간 트레이딩 엔진
├── static/
│   └── realtime_trading.html       # 실시간 모니터링 UI
├── servers/
│   └── realtime_api.py             # 테스트용 API 서버
└── README.md                       # 이 파일
```

## 🔄 개발 프로세스

1. **개발**: 이 폴더에서 모든 기능 개발
2. **테스트**: 독립적으로 테스트
3. **통합**: 테스트 완료 후 `../tradeBot/`로 이동

## 📝 통합 가이드

테스트 완료 후:
1. `services/realtime_trading_engine.py` → `../tradeBot/services/`
2. `static/realtime_trading.html` → `../tradeBot/static/`
3. API 엔드포인트를 `../tradeBot/servers/web_server.py`에 추가
