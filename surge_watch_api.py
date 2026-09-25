#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTCUSDT.P 급등 감시 웹 대시보드 + 백그라운드 수집기.

Process Manager에서 시작:
  python surge_watch_api.py --port 8003 --collect

브라우저: http://localhost:8003
"""

from __future__ import annotations

import argparse
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

import surge_watch as sw

logger = logging.getLogger("surge_watch_api")

# Runtime state (filled in main / startup)
_CFG: dict = {}
_SCFG: dict = {}
_COLLECTOR_STOP = threading.Event()
_COLLECTOR_THREAD: Optional[threading.Thread] = None
_COLLECTOR_STATUS: Dict[str, Any] = {
    "running": False,
    "telegram": True,
    "last_ok_at": None,
    "last_error": None,
    "poll_sec": 30,
    "symbols": ["BTCUSDT"],
}
_LOCK = threading.Lock()
_BOOT: Dict[str, Any] = {"collect": True, "telegram": True, "config": "config.yaml"}


def _reload_config(path: Optional[str] = None) -> None:
    global _CFG, _SCFG
    cfg_path = path or _BOOT.get("config") or "config.yaml"
    _BOOT["config"] = cfg_path
    _CFG = sw.load_config(cfg_path)
    _SCFG = sw.surge_cfg(_CFG)
    _COLLECTOR_STATUS["poll_sec"] = int(_SCFG.get("poll_sec", 30))
    _COLLECTOR_STATUS["symbols"] = list(_SCFG.get("symbols") or ["BTCUSDT"])


def _db():
    return sw.connect_db(Path(_SCFG.get("db_path", "data/surge_watch.db")))


def _telegram_configured() -> bool:
    tg = _CFG.get("telegram") or {}
    return bool(tg.get("bot_token") and tg.get("chat_id"))


def _collector_loop(send_telegram: bool) -> None:
    client = sw.BinanceClient()
    _COLLECTOR_STATUS["running"] = True
    _COLLECTOR_STATUS["telegram"] = send_telegram
    # poll_sec은 매 틱마다 config.yaml에서 다시 읽어 반영
    _reload_config()
    logger.info(
        "collector started poll=%ss telegram=%s",
        _COLLECTOR_STATUS["poll_sec"],
        send_telegram,
    )
    while not _COLLECTOR_STOP.is_set():
        try:
            _reload_config()
            poll = max(10, int(_SCFG.get("poll_sec", 30)))
            _COLLECTOR_STATUS["poll_sec"] = poll
            conn = _db()
            state = sw.load_state()
            try:
                for symbol in _SCFG["symbols"]:
                    snap = sw.fetch_snapshot(
                        client, symbol, _SCFG["lookback_1m_bars"], _SCFG["thresholds"]
                    )
                    sw.save_snapshot(conn, snap)
                    level = sw.maybe_alert(
                        conn, _CFG, _SCFG, snap, state, send=send_telegram
                    )
                    logger.info(
                        "%s score=%s level=%s fund=%.6f ret5=%+.2f%% poll=%ss",
                        symbol,
                        snap.risk_score,
                        level or "-",
                        snap.funding_rate,
                        snap.ret_5m_pct,
                        poll,
                    )
                _COLLECTOR_STATUS["last_ok_at"] = sw.fmt_kst(sw.utc_now())
                _COLLECTOR_STATUS["last_error"] = None
            finally:
                conn.close()
        except Exception as e:
            logger.exception("collector tick failed: %s", e)
            _COLLECTOR_STATUS["last_error"] = str(e)
            poll = max(10, int(_COLLECTOR_STATUS.get("poll_sec") or 30))
        _COLLECTOR_STOP.wait(poll)
    _COLLECTOR_STATUS["running"] = False
    logger.info("collector stopped")


def start_collector(send_telegram: bool = True) -> dict:
    global _COLLECTOR_THREAD
    _reload_config()
    with _LOCK:
        if _COLLECTOR_THREAD and _COLLECTOR_THREAD.is_alive():
            return {"status": "already_running", **_COLLECTOR_STATUS}
        _COLLECTOR_STOP.clear()
        _COLLECTOR_THREAD = threading.Thread(
            target=_collector_loop,
            kwargs={"send_telegram": send_telegram},
            name="surge-collector",
            daemon=True,
        )
        _COLLECTOR_THREAD.start()
    return {"status": "started", **dict(_COLLECTOR_STATUS), "running": True}


def stop_collector() -> dict:
    with _LOCK:
        _COLLECTOR_STOP.set()
        t = _COLLECTOR_THREAD
    if t and t.is_alive():
        t.join(timeout=5)
    _COLLECTOR_STATUS["running"] = False
    return {"status": "stopped", **dict(_COLLECTOR_STATUS)}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _BOOT.get("collect"):
        start_collector(send_telegram=bool(_BOOT.get("telegram")) and _telegram_configured())
        logger.info(
            "auto-started collector (telegram=%s)",
            bool(_BOOT.get("telegram")) and _telegram_configured(),
        )
    yield
    stop_collector()


app = FastAPI(title="BTCUSDT.P Surge Watch", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def root():
    path = Path("surge_watch_dashboard.html")
    if not path.exists():
        return HTMLResponse("<h1>surge_watch_dashboard.html not found</h1>", status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/api/meta")
async def api_meta():
    _reload_config()
    return {
        "purpose": sw.PURPOSE,
        "data_layers": sw.DATA_LAYERS,
        "indicators": sw.INDICATORS,
        "thresholds": _SCFG.get("thresholds", {}),
        "symbols": _SCFG.get("symbols", ["BTCUSDT"]),
        "telegram_configured": _telegram_configured(),
        "db_path": _SCFG.get("db_path"),
        "poll_sec": _SCFG.get("poll_sec"),
    }


@app.get("/api/status")
async def api_status():
    _reload_config()
    latest = None
    try:
        conn = _db()
        try:
            sym = (_SCFG.get("symbols") or ["BTCUSDT"])[0]
            latest = sw.query_latest(conn, sym, _SCFG.get("thresholds"))
        finally:
            conn.close()
    except Exception as e:
        latest = {"error": str(e)}
    return {
        "collector": dict(_COLLECTOR_STATUS),
        "telegram_configured": _telegram_configured(),
        "latest": latest,
        "now_kst": sw.fmt_kst(sw.utc_now()),
        "config_poll_sec": int(_SCFG.get("poll_sec", 30)),
    }


@app.get("/api/latest")
async def api_latest(symbol: Optional[str] = None):
    sym = symbol or (_SCFG.get("symbols") or ["BTCUSDT"])[0]
    conn = _db()
    try:
        row = sw.query_latest(conn, sym, _SCFG.get("thresholds"))
        if not row:
            raise HTTPException(404, detail="스냅샷 없음. 수집기를 시작하세요.")
        return row
    finally:
        conn.close()


@app.get("/api/history")
async def api_history(
    symbol: Optional[str] = None,
    limit: int = Query(120, ge=10, le=1000),
):
    sym = symbol or (_SCFG.get("symbols") or ["BTCUSDT"])[0]
    conn = _db()
    try:
        return {"symbol": sym, "rows": sw.query_history(conn, sym, limit, _SCFG.get("thresholds"))}
    finally:
        conn.close()


@app.get("/api/klines")
async def api_klines(
    symbol: Optional[str] = None,
    interval: str = Query("1m", pattern="^(1m|3m|5m|15m|30m|1h)$"),
    limit: int = Query(60, ge=10, le=500),
):
    """Binance USDT-M 캔들 (차트용)."""
    _reload_config()
    sym = symbol or (_SCFG.get("symbols") or ["BTCUSDT"])[0]
    try:
        client = sw.BinanceClient()
        raw = client.get(
            "/fapi/v1/klines",
            {"symbol": sym, "interval": interval, "limit": limit},
        )
        candles = []
        for k in raw:
            candles.append(
                {
                    "time": int(k[0]) // 1000,  # unix sec (UTC)
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                }
            )
        return {"symbol": sym, "interval": interval, "candles": candles}
    except Exception as e:
        logger.exception("klines failed")
        raise HTTPException(500, detail=str(e))


@app.get("/api/alerts")
async def api_alerts(limit: int = Query(50, ge=1, le=200)):
    conn = _db()
    try:
        return {"alerts": sw.query_alerts(conn, limit)}
    finally:
        conn.close()


@app.post("/api/collect-once")
async def api_collect_once(telegram: bool = True):
    """수동 1회 수집 (+ 조건 충족 시 텔레그램)."""
    _reload_config()
    try:
        snaps = sw.run_once(_CFG, _SCFG, send=telegram and _telegram_configured())
        return {
            "status": "ok",
            "telegram_attempted": bool(telegram and _telegram_configured()),
            "snapshots": [sw.snapshot_to_dict(s, _SCFG.get("thresholds")) for s in snaps],
        }
    except Exception as e:
        logger.exception("collect-once failed")
        raise HTTPException(500, detail=str(e))


@app.post("/api/collector/start")
async def api_collector_start(telegram: bool = True):
    return start_collector(send_telegram=telegram and _telegram_configured())


@app.post("/api/collector/stop")
async def api_collector_stop():
    return stop_collector()


@app.post("/api/test-telegram")
async def api_test_telegram():
    if not _telegram_configured():
        raise HTTPException(400, detail="config.yaml 에 telegram.bot_token / chat_id 가 없습니다.")
    text = (
        "<b>[TEST] Surge Watch</b>\n"
        f"{sw.fmt_kst(sw.utc_now())}\n"
        "급등 감시 텔레그램 연결 테스트입니다."
    )
    try:
        sw.tg_send(_CFG, text)
        return {"status": "sent"}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


def main():
    parser = argparse.ArgumentParser(description="Surge Watch Web Dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--collect",
        action="store_true",
        default=True,
        help="백그라운드 수집기 자동 시작 (기본 ON)",
    )
    parser.add_argument(
        "--no-collect",
        action="store_true",
        help="웹만 띄우고 수집기는 수동 시작",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="경보 텔레그램 비활성화",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _reload_config(args.config)
    do_collect = args.collect and not args.no_collect
    send_tg = not args.no_telegram
    _BOOT["config"] = args.config
    _BOOT["collect"] = do_collect
    _BOOT["telegram"] = send_tg

    print("=" * 60)
    print("BTCUSDT.P Surge Watch Dashboard")
    print(f"  UI:        http://localhost:{args.port}")
    print(f"  API docs:  http://localhost:{args.port}/docs")
    print(f"  Collector: {'ON' if do_collect else 'OFF'} | Telegram: {'ON' if send_tg else 'OFF'}")
    print(f"  poll_sec:  {_COLLECTOR_STATUS.get('poll_sec')}")
    print("=" * 60)

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
