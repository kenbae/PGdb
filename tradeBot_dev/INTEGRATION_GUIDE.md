# 실시간 트레이딩 통합 가이드

## 📋 개발 완료 후 통합 절차

### 1. 파일 이동

#### Backend
```
tradeBot_dev/services/realtime_trading_engine.py
  → tradeBot/services/realtime_trading_engine.py
```

#### Frontend
```
tradeBot_dev/static/realtime_trading.html
  → tradeBot/static/realtime_trading.html
```

### 2. web_server.py에 API 엔드포인트 추가

`tradeBot_dev/servers/realtime_api.py`의 엔드포인트들을 `tradeBot/servers/web_server.py`에 추가:

```python
# 실시간 트레이딩 API
@app.post("/api/realtime/start")
@app.post("/api/realtime/stop")
@app.get("/api/realtime/status")
@app.get("/api/realtime/logs")
@app.websocket("/ws/realtime")
```

### 3. 페이지 라우트 추가

`web_server.py`에 페이지 라우트 추가:

```python
@app.get("/realtime-trading")
async def realtime_trading_page(request: Request, user: dict = Depends(get_current_user)):
    """실시간 트레이딩 페이지"""
    realtime_file = static_dir / "realtime_trading.html"
    if realtime_file.exists():
        return FileResponse(realtime_file)
    return HTMLResponse("<h1>realtime_trading.html을 찾을 수 없습니다</h1>")
```

### 4. 네비게이션 링크 추가

모든 HTML 페이지의 네비게이션에 "실시간 트레이딩" 링크 추가:

```html
<a href="/realtime-trading">실시간 트레이딩</a>
```

### 5. WebSocket 피드 수정 (선택사항)

실시간 캔들 업데이트를 받으려면 `core/websocket_feed.py`의 `_handle_kline` 수정:

```python
# is_closed가 False일 때도 콜백 호출하도록 수정
if kline['is_closed']:
    self._trigger_callbacks('kline', kline)
else:
    # 실시간 업데이트도 콜백 호출
    self._trigger_callbacks('kline_realtime', kline)
```

### 6. 테스트

1. 개발용 서버로 테스트 (`python tradeBot_dev/servers/realtime_api.py`)
2. 통합 후 운영 서버로 테스트
3. 기능 검증

---

## ✅ 통합 체크리스트

- [ ] `realtime_trading_engine.py` 이동
- [ ] `realtime_trading.html` 이동
- [ ] API 엔드포인트 추가
- [ ] 페이지 라우트 추가
- [ ] 네비게이션 링크 추가
- [ ] WebSocket 피드 수정 (선택)
- [ ] 테스트 완료

---

**통합 후 개발용 폴더(`tradeBot_dev`)는 삭제하거나 보관할 수 있습니다.**
