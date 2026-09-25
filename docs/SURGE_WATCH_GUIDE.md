# BTCUSDT.P 급등 감시 가이드 (2026-08-19 사례)

> 목적: 2026-08-19 BTCUSDT 무기한 급등을 다각도로 복기하고, **로컬에 선행 지표를 쌓아 텔레그램 알림**을 받는 방법을 정리합니다.

---

## 1. 2026-08-19 급등 요약

| 항목 | 내용 |
|------|------|
| 날짜 | 2026-08-19 (수) ~ 20일 전후 |
| 심볼 | BTCUSDT.P (USDT-M 무기한) |
| 가격 | 장중 저점 약 **$64,100** → **$69,000+**, 이후 확장 국면에서 **$79k~$81k**까지 보도 |
| 일간 | 약 **+6%~+8%** (당일), 멀티데이로는 **+20%** 급등 구간으로 보도 |
| 성격 | **매크로 촉매 + 숏 포지션 과밀 → 숏스퀴즈/강제청산 캐스케이드** |

핵심은 “갑자기 뜬 호재 하나”가 아니라, **이미 쌓여 있던 숏 연료에 재무부 발표가 불씨를 붙인 구조**입니다.

---

## 2. 다각도 원인 분석

### 2.1 매크로 (1차 촉매)

- **미 재무부(Treasury)** 가 장기채(대략 10–30년 구간) **유동성 지원 바이백 한도를 $2B → 최소 $4B/회** 로 확대한다고 발표 (시행은 9/9부터로 공지).
- 직전 30년물 금리가 **2007년 이후 최고 수준(~5.34%)** 까지 치솟아 리스크 자산을 압박하던 상황.
- 발표 직후 장기 금리 급락(30년물 ~5.19% 등), 달러 약세·위험선호 회복으로 **BTC가 고베타 유동성 헤지**처럼 반응.
- 시장은 이를 “조용한 QE/유동성 주입”으로 해석(기술적으로는 연준 QE와 다름: 재무부 바이백은 부채 총량을 줄이지 않음).

### 2.2 파생/포지셔닝 (증폭기)

- 수주간 **펀딩비 음수·숏 우세**가 지속 → 숏이 “이자를 받으며” 포지션을 쌓는 구조.
- 8/19 오전대 크로스 거래소 펀딩이 **2년 기준 하위 퍼센타일**까지 압축, 일시적으로 더 음수.
- 촉매 이후 **가격↑ + 강제청산(숏)** → 숏 커버 매수 → 추가 청산의 연쇄.
- 보도 기준 숏 청산만 **약 $1.5B~$2.7B+ / 24h**, 전체 청산은 **$1.9B~$4B+** 규모(출처·집계 구간별 차이).
- 일부 구간에서는 **가격 상승 + OI 감소** → 신규 롱 축적보다 **숏 청산/자진청산**이 주도였음을 시사.

### 2.3 수요 측 보조 요인

- 8월 초중순 **현물 BTC ETF 유입** 재개(보도: 8월 첫 2주 약 $1B 등).
- 규제/정책 낙관, 크립토 관련 일정 등 **센티먼트 보조**.
- 주식·금리 시장과의 동조: 금리 급락 시 리스크 온.

### 2.4 기술적/시장미시구조

- 6월 이후 고점권 이탈 후 **수주 박스/약세** → 숏이 편안히 쌓인 구간.
- 저유동성·스테이블코인 거래소 잔고 감소(건파우더 부족)로 **상방은 제한적**이라는 진단도 있었으나, 숏스퀴즈는 신규 매수보다 **강제 커버**로도 급등 가능.
- 1시간 단위로 수억 달러 청산이 몰리면 **호가 공백 + 마크프라이스 급등**이 동시에 발생.

### 2.5 “미리 알 수 있었나?”에 대한 정직한 답

| 구분 | 가능 여부 | 설명 |
|------|-----------|------|
| 재무부 발표 시각 자체를 분 단위로 예측 | ❌ 사실상 불가 | 이벤트 리스크 |
| 숏 과밀·음수 펀딩 등 **연료(SETUP)** 사전 감지 | ✅ | 수일~수주 전 데이터로 가능 |
| 급등 **첫 수분~수십분(TRIGGER)** 조기 경보 | ✅ | 1m 수익률·거래량 z·OI 변화 |
| 스퀴즈 **확정(CONFIRM)** | ✅ | 가격↑+OI↓, 펀딩 급반전, 청산 쏠림 |

즉 **완벽한 예측**이 아니라, **“스퀴즈에 취약한 장세”를 상시 감시하다가 발화 초기에 알림**을 받는 체계가 현실적입니다.

---

## 3. 로컬 저장 + 알림 시스템 (`surge_watch.py`)

이 저장소에 추가된 워처는 Binance USDT-M 공개 API에서 아래를 주기적으로 받아 **SQLite(`data/surge_watch.db`)** 에 저장하고, 임계값 초과 시 기존 `config.yaml` 텔레그램으로 알립니다.

### 수집 지표

- mark / index / last, premium(bps)
- funding rate, next funding time
- open interest (+ 5m 히스토리로 15m 변화율)
- global long/short account ratio, top trader position ratio
- 1m 캔들 기반: 1/5/15분 수익률, 5분 거래량 z-score
- 24h 변동률·거래대금

### 알림 레벨

1. **SETUP** — 음수 펀딩, 숏 계정 비중 과다 등 (연료)
2. **TRIGGER** — 급격한 1m/5m 수익률 + 거래량 스파이크 (발화)
3. **SQUEEZE** — 상승 중 OI 감소 등 커버 패턴 + 고득점

### 실행 방법

```bash
# 웹 대시보드 + 백그라운드 수집 + 텔레그램 (권장)
python surge_watch_api.py --port 8003 --collect
# → http://localhost:8003

# Process Manager에서 "급등 감시" 시작 버튼으로도 동일

# CLI 1회 수집 (cron 권장)
python surge_watch.py --once

# 수집만 (텔레그램 없이)
python surge_watch.py --once --no-telegram

# 30초 폴링 루프 (CLI)
python surge_watch.py --loop

# 로컬 DB 일자 요약
python surge_watch.py --analyze-day 2026-08-19 --symbol BTCUSDT
```

### Process Manager

1. `python process_manager.py` 실행 후 웹에서 **급등 감시** 카드 시작
2. 카드의 포트 링크(`http://localhost:8003`)로 대시보드 접속
3. 경보 발생 시 `config.yaml` 텔레그램으로 SETUP/TRIGGER/SQUEEZE 전송

대시보드에는 데이터 레이어 용도, 지표 사전, 실시간 점수/플래그, 경보 이력이 포함됩니다.

### 외부 PC에서 "연결이 거부됨" 나올 때

서버는 `0.0.0.0:8003` 으로 listen 합니다. 거부는 대개 **Windows 방화벽**입니다.

1. **급등 감시가 실행 중인지** Process Manager에서 확인 (또는 `python surge_watch_api.py --port 8003 --collect`)
2. **관리자 권한**으로 `setup_firewall.bat` 실행 → **8003**  inbound 허용
3. 같은 LAN에서 `http://[서버PC_IP]:8003` 접속 (`ipconfig`로 IPv4 확인)
4. PowerShell에서 서버 PC로 확인:
   ```powershell
   Test-NetConnection -ComputerName [서버IP] -Port 8003
   ```
   `TcpTestSucceeded : False` 이면 방화벽/실행 여부 문제입니다.

### crontab 예시

```cron
# 매분 스냅샷 (SETUP/TRIGGER 조기 포착)
* * * * * cd /path/to/PGdb && /usr/bin/python3 surge_watch.py --once >> logs/surge_watch.log 2>&1
```

또는 systemd / `process_manager`에 `surge_watch.py --loop` 등록.

### 설정 (`config.yaml`)

```yaml
surge_watch:
  symbols: [BTCUSDT]
  poll_sec: 30
  db_path: data/surge_watch.db
  cooldown_sec: 900
  thresholds:
    funding_negative: -0.00005
    short_account_min: 0.52
    ret_1m_pct: 0.35
    ret_5m_pct: 0.8
    volume_z_5m: 2.5
    oi_drop_pct_15m: 1.5
    setup_score: 40
    trigger_score: 60
    squeeze_score: 75
```

임계값은 백테스트로 조정하세요. 너무 낮으면 알림 스팸, 너무 높으면 초기를 놓칩니다.

---

## 4. 권장 데이터 레이어 (확장)

`surge_watch` 만으로도 SETUP/TRIGGER는 가능합니다. 더 강하게 가려면:

| 데이터 | 소스 | 용도 |
|--------|------|------|
| 1m OHLCV (이미 있음) | `run_ingest.py` / candles | 가격·거래량 베이스라인 |
| Funding / OI / L-S | `surge_watch.py` | 숏 과밀·스퀴즈 |
| 청산 스트림 | Binance `!forceOrder@arr` WS | 캐스케이드 확정 |
| 현물 ETF 유입 | SoSoValue 등 | 매크로 수요 |
| 미국 10Y/30Y, DXY | FRED / Yahoo | 8/19형 금리 촉매 |
| 재무부/연준 헤드라인 | RSS + 키워드 필터 | 이벤트 리스크 플래그 |

매크로 뉴스는 **예측이 아니라 “플래그”** 로 두고, 파생 지표가 TRIGGER를 내면 가중치를 올리는 방식이 안전합니다.

---

## 5. 8/19형 시나리오를 다시 마주쳤을 때 체크리스트

1. funding 음수 지속 + short account > ~52% → **SETUP 경계**
2. 금리/유동성 관련 헤드라인 또는 장 초반 급등 → **수동 경계 상향**
3. 1~5분 +0.5%~1% 이상 + volume z > 2.5 → **TRIGGER 알림 확인**
4. 가격↑인데 OI↓ → **숏커버/스퀴즈 확률↑** (추격보다 리스크 관리 우선)
5. 펀딩이 한 사이클 만에 크게 플러스 전환 → 과열/되돌림 구간 경계

---

## 6. 한계

- 공개 REST 폴링은 **초 단위 호가 공백**까지는 못 잡습니다. 필요 시 WebSocket 청산/체결을 추가하세요.
- 지리적으로 `fapi.binance.com` 이 막힌 환경에서는 `www.binance.com` 경유(코드에 폴백 포함).
- 알림은 **조기 경보**이지 자동매매 신호가 아닙니다.
