#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTCUSDT.P (Binance USDT-M Perp) surge early-warning monitor.

Collects futures microstructure locally (SQLite) and sends Telegram alerts when
setup / trigger / squeeze conditions resemble the 2026-08-19 short-squeeze pattern.

Usage:
  python surge_watch.py --once              # single snapshot (cron-friendly)
  python surge_watch.py --loop              # continuous poll
  python surge_watch.py --once --no-telegram # collect only
  python surge_watch.py --analyze-day 2026-08-19  # offline report from local DB
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml

logger = logging.getLogger("surge_watch")

KST = timezone(timedelta(hours=9))
DEFAULT_DB = Path("data/surge_watch.db")
STATE_FILE = Path("data/surge_watch_state.json")

# Restricted environments often block fapi.binance.com; www.binance.com usually works.
BINANCE_BASES = [
    "https://www.binance.com",
    "https://fapi.binance.com",
]


@dataclass
class Snapshot:
    ts: datetime
    symbol: str
    mark_price: float
    index_price: float
    last_price: float
    funding_rate: float
    next_funding_time: Optional[int]
    open_interest: float
    oi_value_usdt: Optional[float]
    long_short_ratio: Optional[float]
    short_account: Optional[float]
    top_ls_ratio: Optional[float]
    ret_1m_pct: float
    ret_5m_pct: float
    ret_15m_pct: float
    volume_1m: float
    volume_5m: float
    volume_z_5m: float
    premium_bps: float
    price_change_24h_pct: float
    quote_volume_24h: float
    alerts: List[str] = field(default_factory=list)
    risk_score: int = 0


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def surge_cfg(cfg: dict) -> dict:
    defaults = {
        "symbols": ["BTCUSDT"],
        "poll_sec": 30,
        "db_path": str(DEFAULT_DB),
        "lookback_1m_bars": 120,
        "cooldown_sec": 900,
        "thresholds": {
            "funding_negative": -0.00005,      # -0.005% per 8h ≈ crowded shorts
            "funding_very_negative": -0.0001,
            "short_account_min": 0.52,         # global accounts short share
            "ls_ratio_max": 0.95,              # <1 means shorts dominate accounts
            "ret_1m_pct": 0.35,
            "ret_5m_pct": 0.8,
            "ret_15m_pct": 1.5,
            "volume_z_5m": 2.5,
            "oi_drop_pct_15m": 1.5,            # OI falling while price up → covering
            "oi_rise_pct_15m": 2.0,
            "premium_bps_abs": 8.0,
            "setup_score": 40,
            "trigger_score": 60,
            "squeeze_score": 75,
        },
    }
    user = cfg.get("surge_watch") or {}
    merged = {**defaults, **user}
    th = {**defaults["thresholds"], **(user.get("thresholds") or {})}
    merged["thresholds"] = th
    return merged


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fmt_kst(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


# ---------------------------
# HTTP helpers
# ---------------------------
class BinanceClient:
    def __init__(self, timeout: float = 12.0, session: Optional[requests.Session] = None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {"User-Agent": "PGdb-surge-watch/1.0", "Accept": "application/json"}
        )
        self.base = BINANCE_BASES[0]

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        last_err: Optional[Exception] = None
        bases = [self.base] + [b for b in BINANCE_BASES if b != self.base]
        for base in bases:
            url = f"{base}{path}"
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, dict) and data.get("code") not in (None, 0, "0") and "msg" in data:
                        # Binance geo block style payload
                        last_err = RuntimeError(f"{base}: {data.get('msg')}")
                        continue
                    self.base = base
                    return data
                last_err = RuntimeError(f"{base} HTTP {r.status_code}: {r.text[:200]}")
            except Exception as e:
                last_err = e
        raise RuntimeError(f"Binance request failed for {path}: {last_err}")


def fetch_snapshot(client: BinanceClient, symbol: str, lookback: int, th: dict) -> Snapshot:
    premium = client.get("/fapi/v1/premiumIndex", {"symbol": symbol})
    oi = client.get("/fapi/v1/openInterest", {"symbol": symbol})
    ticker = client.get("/fapi/v1/ticker/24hr", {"symbol": symbol})
    klines = client.get(
        "/fapi/v1/klines",
        {"symbol": symbol, "interval": "1m", "limit": max(lookback, 60)},
    )

    ls = None
    top_ls = None
    oi_hist = None
    try:
        ls = client.get(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol, "period": "5m", "limit": 12},
        )
    except Exception as e:
        logger.warning("L/S ratio unavailable: %s", e)
    try:
        top_ls = client.get(
            "/futures/data/topLongShortPositionRatio",
            {"symbol": symbol, "period": "5m", "limit": 12},
        )
    except Exception as e:
        logger.warning("Top L/S ratio unavailable: %s", e)
    try:
        oi_hist = client.get(
            "/futures/data/openInterestHist",
            {"symbol": symbol, "period": "5m", "limit": 12},
        )
    except Exception as e:
        logger.warning("OI hist unavailable: %s", e)

    closes = [float(k[4]) for k in klines]
    vols = [float(k[5]) for k in klines]
    last_price = closes[-1]
    mark = float(premium["markPrice"])
    index = float(premium["indexPrice"])
    funding = float(premium["lastFundingRate"])
    next_ft = int(premium.get("nextFundingTime") or 0) or None
    oi_qty = float(oi["openInterest"])

    def ret_pct(n: int) -> float:
        if len(closes) <= n or closes[-1 - n] == 0:
            return 0.0
        return (closes[-1] / closes[-1 - n] - 1.0) * 100.0

    vol_5m = sum(vols[-5:])
    # volume z vs prior window (exclude last 5)
    prior = vols[-60:-5] if len(vols) >= 60 else vols[:-5]
    if prior:
        mean = sum(prior) / len(prior)
        var = sum((x - mean) ** 2 for x in prior) / max(len(prior), 1)
        std = var ** 0.5
        vol_z = (vol_5m - mean * 5) / std if std > 1e-12 else 0.0
    else:
        vol_z = 0.0

    ls_ratio = float(ls[-1]["longShortRatio"]) if ls else None
    short_acc = float(ls[-1]["shortAccount"]) if ls else None
    top_ratio = float(top_ls[-1]["longShortRatio"]) if top_ls else None

    oi_value = None
    oi_drop_pct = 0.0
    oi_rise_pct = 0.0
    if oi_hist and len(oi_hist) >= 4:
        # ~15m ago is 3 bars of 5m
        cur = float(oi_hist[-1]["sumOpenInterest"])
        prev = float(oi_hist[-4]["sumOpenInterest"])
        oi_value = float(oi_hist[-1].get("sumOpenInterestValue") or 0) or None
        if prev > 0:
            chg = (cur / prev - 1.0) * 100.0
            oi_drop_pct = -chg if chg < 0 else 0.0
            oi_rise_pct = chg if chg > 0 else 0.0
        oi_qty = cur

    premium_bps = ((mark / index) - 1.0) * 10000.0 if index else 0.0

    snap = Snapshot(
        ts=utc_now(),
        symbol=symbol,
        mark_price=mark,
        index_price=index,
        last_price=last_price,
        funding_rate=funding,
        next_funding_time=next_ft,
        open_interest=oi_qty,
        oi_value_usdt=oi_value,
        long_short_ratio=ls_ratio,
        short_account=short_acc,
        top_ls_ratio=top_ratio,
        ret_1m_pct=ret_pct(1),
        ret_5m_pct=ret_pct(5),
        ret_15m_pct=ret_pct(15),
        volume_1m=vols[-1] if vols else 0.0,
        volume_5m=vol_5m,
        volume_z_5m=vol_z,
        premium_bps=premium_bps,
        price_change_24h_pct=float(ticker.get("priceChangePercent") or 0),
        quote_volume_24h=float(ticker.get("quoteVolume") or 0),
    )
    score_alerts(snap, th, oi_drop_pct=oi_drop_pct, oi_rise_pct=oi_rise_pct)
    return snap


def score_alerts(snap: Snapshot, th: dict, oi_drop_pct: float = 0.0, oi_rise_pct: float = 0.0) -> None:
    score = 0
    alerts: List[str] = []

    # --- SETUP (fuel): crowded shorts / negative funding ---
    if snap.funding_rate <= th["funding_very_negative"]:
        score += 25
        alerts.append(f"SETUP:funding_very_neg({snap.funding_rate:.6f})")
    elif snap.funding_rate <= th["funding_negative"]:
        score += 15
        alerts.append(f"SETUP:funding_neg({snap.funding_rate:.6f})")

    if snap.short_account is not None and snap.short_account >= th["short_account_min"]:
        score += 15
        alerts.append(f"SETUP:short_crowd({snap.short_account:.3f})")
    if snap.long_short_ratio is not None and snap.long_short_ratio <= th["ls_ratio_max"]:
        score += 10
        alerts.append(f"SETUP:ls_ratio_low({snap.long_short_ratio:.3f})")

    # --- TRIGGER (spark): sudden price + volume ---
    if abs(snap.ret_1m_pct) >= th["ret_1m_pct"]:
        score += 20
        alerts.append(f"TRIGGER:ret_1m({snap.ret_1m_pct:+.2f}%)")
    if abs(snap.ret_5m_pct) >= th["ret_5m_pct"]:
        score += 20
        alerts.append(f"TRIGGER:ret_5m({snap.ret_5m_pct:+.2f}%)")
    if abs(snap.ret_15m_pct) >= th["ret_15m_pct"]:
        score += 15
        alerts.append(f"TRIGGER:ret_15m({snap.ret_15m_pct:+.2f}%)")
    if snap.volume_z_5m >= th["volume_z_5m"]:
        score += 15
        alerts.append(f"TRIGGER:vol_z({snap.volume_z_5m:.2f})")

    # --- SQUEEZE confirm: price up + OI drop (covering) or OI spike ---
    if snap.ret_5m_pct > 0 and oi_drop_pct >= th["oi_drop_pct_15m"]:
        score += 25
        alerts.append(f"SQUEEZE:oi_drop_while_up({oi_drop_pct:.2f}%)")
    if snap.ret_5m_pct > 0 and oi_rise_pct >= th["oi_rise_pct_15m"]:
        score += 10
        alerts.append(f"MOMENTUM:oi_rise({oi_rise_pct:.2f}%)")

    if abs(snap.premium_bps) >= th["premium_bps_abs"]:
        score += 5
        alerts.append(f"PREMIUM:{snap.premium_bps:+.1f}bps")

    snap.risk_score = min(score, 100)
    snap.alerts = alerts


# ---------------------------
# Storage
# ---------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS surge_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    mark_price REAL,
    index_price REAL,
    last_price REAL,
    funding_rate REAL,
    next_funding_time INTEGER,
    open_interest REAL,
    oi_value_usdt REAL,
    long_short_ratio REAL,
    short_account REAL,
    top_ls_ratio REAL,
    ret_1m_pct REAL,
    ret_5m_pct REAL,
    ret_15m_pct REAL,
    volume_1m REAL,
    volume_5m REAL,
    volume_z_5m REAL,
    premium_bps REAL,
    price_change_24h_pct REAL,
    quote_volume_24h REAL,
    risk_score INTEGER,
    alerts_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_surge_ts ON surge_snapshots(symbol, ts);
CREATE TABLE IF NOT EXISTS surge_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    level TEXT NOT NULL,
    risk_score INTEGER,
    message TEXT,
    sent INTEGER DEFAULT 0
);
"""


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(SCHEMA)
    return conn


def save_snapshot(conn: sqlite3.Connection, snap: Snapshot) -> int:
    cur = conn.execute(
        """
        INSERT INTO surge_snapshots (
            ts, symbol, mark_price, index_price, last_price, funding_rate,
            next_funding_time, open_interest, oi_value_usdt, long_short_ratio,
            short_account, top_ls_ratio, ret_1m_pct, ret_5m_pct, ret_15m_pct,
            volume_1m, volume_5m, volume_z_5m, premium_bps, price_change_24h_pct,
            quote_volume_24h, risk_score, alerts_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            snap.ts.isoformat(),
            snap.symbol,
            snap.mark_price,
            snap.index_price,
            snap.last_price,
            snap.funding_rate,
            snap.next_funding_time,
            snap.open_interest,
            snap.oi_value_usdt,
            snap.long_short_ratio,
            snap.short_account,
            snap.top_ls_ratio,
            snap.ret_1m_pct,
            snap.ret_5m_pct,
            snap.ret_15m_pct,
            snap.volume_1m,
            snap.volume_5m,
            snap.volume_z_5m,
            snap.premium_bps,
            snap.price_change_24h_pct,
            snap.quote_volume_24h,
            snap.risk_score,
            json.dumps(snap.alerts, ensure_ascii=False),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"last_alert_ts": {}}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def alert_level(score: int, th: dict) -> Optional[str]:
    if score >= th["squeeze_score"]:
        return "SQUEEZE"
    if score >= th["trigger_score"]:
        return "TRIGGER"
    if score >= th["setup_score"]:
        return "SETUP"
    return None


def format_alert(snap: Snapshot, level: str) -> str:
    oi_m = (snap.oi_value_usdt or 0) / 1e6
    ls = "n/a" if snap.long_short_ratio is None else f"{snap.long_short_ratio:.3f}"
    short_a = "n/a" if snap.short_account is None else f"{snap.short_account:.3f}"
    lines = [
        f"<b>[{html.escape(level)}] {html.escape(snap.symbol)}.P surge watch</b>",
        f"score: <b>{snap.risk_score}</b> | {html.escape(fmt_kst(snap.ts))}",
        f"mark: {snap.mark_price:,.2f} | 24h: {snap.price_change_24h_pct:+.2f}%",
        f"ret 1/5/15m: {snap.ret_1m_pct:+.2f}% / {snap.ret_5m_pct:+.2f}% / {snap.ret_15m_pct:+.2f}%",
        f"funding: {snap.funding_rate:.6f} | premium: {snap.premium_bps:+.1f} bps",
        f"OI: {snap.open_interest:,.2f} (~${oi_m:,.1f}M) | L/S: {ls} | short%: {short_a}",
        f"vol_z(5m): {snap.volume_z_5m:.2f}",
        "flags: " + html.escape(", ".join(snap.alerts) if snap.alerts else "-"),
        "",
        "<i>사전 경고용. 매매 신호가 아닙니다.</i>",
    ]
    return "\n".join(lines)


def tg_send(cfg: dict, text: str) -> bool:
    tg = cfg.get("telegram") or {}
    token = tg.get("bot_token")
    chat_id = tg.get("chat_id")
    if not token or not chat_id:
        logger.warning("Telegram token/chat_id missing; skip send")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": tg.get("parse_mode", "HTML"),
        "disable_web_page_preview": tg.get("disable_web_page_preview", True),
    }
    r = requests.post(url, json=payload, timeout=15)
    if not r.ok:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")
    return True


def maybe_alert(
    conn: sqlite3.Connection,
    cfg: dict,
    scfg: dict,
    snap: Snapshot,
    state: dict,
    send: bool,
) -> Optional[str]:
    th = scfg["thresholds"]
    level = alert_level(snap.risk_score, th)
    if not level:
        return None

    last_map = state.setdefault("last_alert_ts", {})
    key = f"{snap.symbol}:{level}"
    now = time.time()
    last = float(last_map.get(key) or 0)
    cooldown = float(scfg.get("cooldown_sec", 900))
    # Higher severity can break cooldown of lower ones
    if now - last < cooldown and level != "SQUEEZE":
        logger.info("Cooldown active for %s (%ss left)", key, int(cooldown - (now - last)))
        return None
    if level == "SQUEEZE" and now - last < cooldown / 2:
        return None

    msg = format_alert(snap, level)
    sent = 0
    if send:
        try:
            tg_send(cfg, msg)
            sent = 1
        except Exception as e:
            logger.error("Telegram send failed: %s", e)

    conn.execute(
        "INSERT INTO surge_alerts (ts, symbol, level, risk_score, message, sent) VALUES (?,?,?,?,?,?)",
        (snap.ts.isoformat(), snap.symbol, level, snap.risk_score, msg, sent),
    )
    conn.commit()
    last_map[key] = now
    # also stamp lower levels to reduce spam bursts
    for lv in ("SETUP", "TRIGGER", "SQUEEZE"):
        last_map[f"{snap.symbol}:{lv}"] = now
    save_state(state)
    return level


def print_snapshot(snap: Snapshot) -> None:
    print(
        f"[{fmt_kst(snap.ts)}] {snap.symbol} mark={snap.mark_price:,.2f} "
        f"fund={snap.funding_rate:.6f} OI={snap.open_interest:,.1f} "
        f"ret5m={snap.ret_5m_pct:+.2f}% volz={snap.volume_z_5m:.2f} "
        f"score={snap.risk_score} alerts={snap.alerts}"
    )


def analyze_day(conn: sqlite3.Connection, day: str, symbol: str) -> None:
    """Offline summary from local DB for a UTC date YYYY-MM-DD."""
    start = f"{day}T00:00:00"
    end = f"{day}T23:59:59.999999"
    rows = conn.execute(
        """
        SELECT ts, mark_price, funding_rate, open_interest, ret_5m_pct, ret_15m_pct,
               volume_z_5m, risk_score, alerts_json, long_short_ratio, short_account
        FROM surge_snapshots
        WHERE symbol=? AND ts >= ? AND ts <= ?
        ORDER BY ts
        """,
        (symbol, start, end + "+00:00"),
    ).fetchall()
    if not rows:
        # try without timezone suffix match
        rows = conn.execute(
            """
            SELECT ts, mark_price, funding_rate, open_interest, ret_5m_pct, ret_15m_pct,
                   volume_z_5m, risk_score, alerts_json, long_short_ratio, short_account
            FROM surge_snapshots
            WHERE symbol=? AND substr(ts,1,10)=?
            ORDER BY ts
            """,
            (symbol, day),
        ).fetchall()
    print(f"=== Local analyze {symbol} {day} | rows={len(rows)} ===")
    if not rows:
        print("No local snapshots. Run the watcher continuously before the event.")
        return
    prices = [r[1] for r in rows if r[1]]
    scores = [r[7] for r in rows]
    print(f"price range: {min(prices):,.2f} → {max(prices):,.2f} ({(max(prices)/min(prices)-1)*100:+.2f}%)")
    print(f"max risk_score: {max(scores)}")
    top = sorted(rows, key=lambda r: r[7], reverse=True)[:5]
    for r in top:
        print(f"  {r[0]} px={r[1]:,.2f} score={r[7]} ret5={r[4]:+.2f}% flags={r[8]}")


def run_once(cfg: dict, scfg: dict, send: bool) -> List[Snapshot]:
    client = BinanceClient()
    conn = connect_db(Path(scfg["db_path"]))
    state = load_state()
    out: List[Snapshot] = []
    try:
        for symbol in scfg["symbols"]:
            snap = fetch_snapshot(client, symbol, scfg["lookback_1m_bars"], scfg["thresholds"])
            save_snapshot(conn, snap)
            print_snapshot(snap)
            level = maybe_alert(conn, cfg, scfg, snap, state, send=send)
            if level:
                print(f"  -> ALERT {level} sent={send}")
            out.append(snap)
    finally:
        conn.close()
    return out


def run_loop(cfg: dict, scfg: dict, send: bool) -> None:
    poll = max(10, int(scfg.get("poll_sec", 30)))
    logger.info("surge_watch loop start poll=%ss symbols=%s", poll, scfg["symbols"])
    while True:
        try:
            run_once(cfg, scfg, send=send)
        except Exception as e:
            logger.exception("poll failed: %s", e)
        time.sleep(poll)


def main():
    p = argparse.ArgumentParser(description="BTCUSDT.P surge early-warning watcher")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--once", action="store_true", help="Collect one snapshot and exit")
    p.add_argument("--loop", action="store_true", help="Continuous polling")
    p.add_argument("--no-telegram", action="store_true", help="Store only, do not send alerts")
    p.add_argument("--analyze-day", metavar="YYYY-MM-DD", help="Summarize local DB for a UTC day")
    p.add_argument("--symbol", default=None, help="Override symbol (default from config)")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = p.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    cfg = load_config(args.config)
    scfg = surge_cfg(cfg)
    if args.symbol:
        scfg["symbols"] = [args.symbol.replace(".P", "").replace("/", "").upper()]
        if not scfg["symbols"][0].endswith("USDT"):
            scfg["symbols"][0] = scfg["symbols"][0] + "USDT"

    if args.analyze_day:
        conn = connect_db(Path(scfg["db_path"]))
        try:
            for sym in scfg["symbols"]:
                analyze_day(conn, args.analyze_day, sym)
        finally:
            conn.close()
        return

    send = not args.no_telegram
    if args.loop:
        run_loop(cfg, scfg, send=send)
    else:
        # default: once
        run_once(cfg, scfg, send=send)


if __name__ == "__main__":
    main()
