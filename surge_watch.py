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
    direction: str = "neutral"  # up | down | neutral
    up_score: int = 0
    down_score: int = 0


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
            "funding_negative": -0.00005,      # crowded shorts → 급등(스퀴즈) 연료
            "funding_very_negative": -0.0001,
            "funding_positive": 0.00005,       # crowded longs → 급락 연료
            "funding_very_positive": 0.0001,
            "short_account_min": 0.52,         # 숏 과밀
            "long_account_min": 0.52,          # 롱 과밀 (= 1 - short)
            "ls_ratio_max": 0.95,              # <1 숏 우세
            "ls_ratio_min": 1.05,              # >1 롱 우세
            "ret_1m_pct": 0.35,
            "ret_5m_pct": 0.8,
            "ret_15m_pct": 1.5,
            "volume_z_5m": 2.5,
            "oi_drop_pct_15m": 1.5,            # OI↓ + 방향성 가격 → 청산/커버
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
# Glossary / data layers (web UI)
# ---------------------------
PURPOSE = {
    "title": "BTCUSDT.P 급등·급락 사전 감시",
    "summary": (
        "숏/롱 과밀(SETUP)을 상시 감시하다가, 급등·급락의 첫 수분(TRIGGER)과 "
        "청산 캐스케이드 확정(SQUEEZE=급등 / DUMP=급락)을 로컬에 기록하고 텔레그램으로 알립니다."
    ),
    "case_20260819": (
        "2026-08-19 급등 사례: 미 재무부 장기채 바이백 확대 → 금리 급락 → "
        "숏/음수 펀딩 과밀이 숏스퀴즈로 증폭. 반대로 롱 과밀+양수 펀딩이면 급락(롱청산) 대칭 구조."
    ),
}

DATA_LAYERS = [
    {
        "id": "price_volume",
        "name": "가격·거래량 레이어",
        "purpose": "발화(TRIGGER) 감지 — 급등·급락이 ‘시작’되는 순간을 수분 단위로 포착",
        "sources": ["Binance Futures 1m klines", "24h ticker"],
        "metrics": ["ret_1m_pct", "ret_5m_pct", "ret_15m_pct", "volume_z_5m", "price_change_24h_pct"],
        "why": "매크로 뉴스는 예측하기 어렵지만, +/- 분봉 스파이크와 거래량 이상치는 조기 경보로 쓸 수 있습니다.",
    },
    {
        "id": "derivatives_positioning",
        "name": "파생 포지셔닝 레이어",
        "purpose": "연료(SETUP) 감지 — 숏 과밀(급등 위험) / 롱 과밀(급락 위험)",
        "sources": ["funding rate", "open interest", "global L/S account ratio", "top trader L/S"],
        "metrics": ["funding_rate", "open_interest", "long_short_ratio", "short_account", "top_ls_ratio"],
        "why": "음수 펀딩·숏 우세는 스퀴즈(급등) 연료, 양수 펀딩·롱 우세는 롱청산(급락) 연료입니다.",
    },
    {
        "id": "squeeze_confirm",
        "name": "캐스케이드 확정 레이어",
        "purpose": "상승=숏커버(SQUEEZE) / 하락=롱청산(DUMP) 구분",
        "sources": ["OI 5m history", "mark vs index premium"],
        "metrics": ["oi_drop_while_up", "oi_drop_while_down", "premium_bps", "risk_score", "direction"],
        "why": "가격↑+OI↓ → 숏커버 급등, 가격↓+OI↓ → 롱청산 급락. 추격보다 리스크 관리가 우선입니다.",
    },
    {
        "id": "alert_delivery",
        "name": "알림·저장 레이어",
        "purpose": "로컬 이력 보존 + 텔레그램 즉시 전달",
        "sources": ["SQLite data/surge_watch.db", "Telegram Bot API"],
        "metrics": ["surge_snapshots", "surge_alerts"],
        "why": "웹에서 보고, 자리비움 시에도 SETUP/TRIGGER/SQUEEZE/DUMP를 텔레그램으로 받습니다.",
    },
]

INDICATORS = [
    {
        "key": "risk_score",
        "name": "위험 점수 (0–100)",
        "plain": "급등·급락 SETUP/TRIGGER/캐스케이드 조건을 합산한 종합 점수입니다.",
        "how_to_read": "40↑ SETUP, 60↑ TRIGGER, 75↑ SQUEEZE(급등) 또는 DUMP(급락). direction으로 방향을 봅니다.",
        "layer": "squeeze_confirm",
    },
    {
        "key": "direction",
        "name": "방향 (up/down)",
        "plain": "현재 신호가 급등(up) 쪽인지 급락(down) 쪽인지입니다.",
        "how_to_read": "up=숏스퀴즈/급등 쪽, down=롱청산/급락 쪽, neutral=뚜렷하지 않음.",
        "layer": "squeeze_confirm",
    },
    {
        "key": "mark_price",
        "name": "마크 가격",
        "plain": "청산·펀딩 정산에 쓰이는 공정가에 가까운 가격입니다.",
        "how_to_read": "체결가와 크게 벌어지면 변동성이 크거나 유동성이 얇다는 신호일 수 있습니다.",
        "layer": "price_volume",
    },
    {
        "key": "funding_rate",
        "name": "펀딩비",
        "plain": "롱·숏이 서로 지불하는 비용입니다. 음수면 숏이 받고, 양수면 롱이 숏에게 냅니다.",
        "how_to_read": "오래 음수=숏 과밀(급등 SETUP). 오래 양수=롱 과밀(급락 SETUP).",
        "layer": "derivatives_positioning",
    },
    {
        "key": "open_interest",
        "name": "미결제약정 (OI)",
        "plain": "아직 청산되지 않은 선물 포지션 규모입니다.",
        "how_to_read": "가격↑+OI↓ → 숏커버 급등. 가격↓+OI↓ → 롱청산 급락. 가격·OI 동시↑ → 신규 레버리지.",
        "layer": "derivatives_positioning",
    },
    {
        "key": "long_short_ratio",
        "name": "롱/숏 계정 비율",
        "plain": "계정 수 기준 롱÷숏입니다.",
        "how_to_read": "1 미만 지속=숏 군중(급등 연료). 1 초과 지속=롱 군중(급락 연료).",
        "layer": "derivatives_positioning",
    },
    {
        "key": "short_account",
        "name": "숏 계정 비중",
        "plain": "전체 계정 중 숏을 든 비율입니다. (롱 비중 ≈ 1 − 숏 비중)",
        "how_to_read": "숏 ≥52% → 급등 SETUP. 롱 ≥52%(숏 ≤48%) → 급락 SETUP.",
        "layer": "derivatives_positioning",
    },
    {
        "key": "top_ls_ratio",
        "name": "탑트레이더 롱/숏",
        "plain": "대형 계정의 포지션 롱÷숏 비율입니다.",
        "how_to_read": "소매 L/S와 괴리가 크면 스마트머니 방향 힌트가 될 수 있습니다.",
        "layer": "derivatives_positioning",
    },
    {
        "key": "ret_1m_pct",
        "name": "1분 수익률",
        "plain": "최근 1분 가격 변화율입니다. (+급등 / −급락)",
        "how_to_read": "기본 임계 |값| ≥ 0.35% 이면 TRIGGER 후보.",
        "layer": "price_volume",
    },
    {
        "key": "ret_5m_pct",
        "name": "5분 수익률",
        "plain": "최근 5분 가격 변화율입니다.",
        "how_to_read": "|값| ≥ 0.8% TRIGGER. +이면서 OI↓ → SQUEEZE, −이면서 OI↓ → DUMP.",
        "layer": "price_volume",
    },
    {
        "key": "ret_15m_pct",
        "name": "15분 수익률",
        "plain": "최근 15분 가격 변화율입니다.",
        "how_to_read": "|값| ≥ 1.5%. 단발성 노이즈와 실제 급변을 구분하는 데 도움.",
        "layer": "price_volume",
    },
    {
        "key": "volume_z_5m",
        "name": "거래량 Z-Score (5분)",
        "plain": "최근 5분 거래량이 평소보다 얼마나 이상한지(표준편차 단위)입니다.",
        "how_to_read": "2.5 이상이면 비정상 체결 폭주. 급등·급락 TRIGGER를 강화합니다.",
        "layer": "price_volume",
    },
    {
        "key": "premium_bps",
        "name": "프리미엄 (bps)",
        "plain": "마크가격이 현물지수를 몇 bp 웃도는지입니다.",
        "how_to_read": "급등 중 확대=롱 과열, 급락 중 깊게 음수=숏 과열. 되돌림 경계.",
        "layer": "squeeze_confirm",
    },
    {
        "key": "price_change_24h_pct",
        "name": "24시간 변동률",
        "plain": "지난 24시간 대비 현재가 변화입니다.",
        "how_to_read": "배경 추세 파악용. 단독 TRIGGER는 아니지만 맥락을 줍니다.",
        "layer": "price_volume",
    },
]



def snapshot_to_dict(snap: Snapshot, thresholds: Optional[dict] = None) -> dict:
    th = thresholds or {}
    level = alert_level(snap.risk_score, th, snap.direction) if th else None
    return {
        "ts": snap.ts.isoformat(),
        "ts_kst": fmt_kst(snap.ts),
        "symbol": snap.symbol,
        "mark_price": snap.mark_price,
        "index_price": snap.index_price,
        "last_price": snap.last_price,
        "funding_rate": snap.funding_rate,
        "next_funding_time": snap.next_funding_time,
        "open_interest": snap.open_interest,
        "oi_value_usdt": snap.oi_value_usdt,
        "long_short_ratio": snap.long_short_ratio,
        "short_account": snap.short_account,
        "top_ls_ratio": snap.top_ls_ratio,
        "ret_1m_pct": snap.ret_1m_pct,
        "ret_5m_pct": snap.ret_5m_pct,
        "ret_15m_pct": snap.ret_15m_pct,
        "volume_1m": snap.volume_1m,
        "volume_5m": snap.volume_5m,
        "volume_z_5m": snap.volume_z_5m,
        "premium_bps": snap.premium_bps,
        "price_change_24h_pct": snap.price_change_24h_pct,
        "quote_volume_24h": snap.quote_volume_24h,
        "alerts": list(snap.alerts),
        "risk_score": snap.risk_score,
        "up_score": snap.up_score,
        "down_score": snap.down_score,
        "direction": snap.direction,
        "bias": direction_label(snap.direction),
        "level": level,
    }


def row_to_snapshot_dict(row: sqlite3.Row, thresholds: Optional[dict] = None) -> dict:
    d = dict(row)
    alerts = d.get("alerts_json")
    try:
        d["alerts"] = json.loads(alerts) if alerts else []
    except Exception:
        d["alerts"] = []
    d.pop("alerts_json", None)
    ts = d.get("ts")
    if ts:
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            d["ts_kst"] = fmt_kst(dt)
        except Exception:
            d["ts_kst"] = ts
    alerts = d.get("alerts") or []
    direction = infer_direction(alerts, float(d.get("ret_5m_pct") or 0))
    d["direction"] = direction
    d["bias"] = direction_label(direction)
    d["up_score"] = d.get("up_score")
    d["down_score"] = d.get("down_score")
    if thresholds is not None:
        d["level"] = alert_level(int(d.get("risk_score") or 0), thresholds, direction)
    return d


def query_latest(conn: sqlite3.Connection, symbol: Optional[str] = None, thresholds: Optional[dict] = None) -> Optional[dict]:
    conn.row_factory = sqlite3.Row
    if symbol:
        row = conn.execute(
            "SELECT * FROM surge_snapshots WHERE symbol=? ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM surge_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return row_to_snapshot_dict(row, thresholds)


def query_history(conn: sqlite3.Connection, symbol: str, limit: int = 120, thresholds: Optional[dict] = None) -> List[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM surge_snapshots WHERE symbol=? ORDER BY id DESC LIMIT ?",
        (symbol, limit),
    ).fetchall()
    return [row_to_snapshot_dict(r, thresholds) for r in reversed(rows)]


def query_alerts(conn: sqlite3.Connection, limit: int = 50) -> List[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM surge_alerts ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


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
    """급등(up) / 급락(down) 양방향 점수. risk_score = max(up, down)."""
    up = 0
    down = 0
    alerts: List[str] = []

    # --- SETUP: short crowd -> 급등 연료 / long crowd -> 급락 연료 ---
    if snap.funding_rate <= th["funding_very_negative"]:
        up += 25
        alerts.append(f"SETUP_UP:funding_very_neg({snap.funding_rate:.6f})")
    elif snap.funding_rate <= th["funding_negative"]:
        up += 15
        alerts.append(f"SETUP_UP:funding_neg({snap.funding_rate:.6f})")

    if snap.funding_rate >= th.get("funding_very_positive", 0.0001):
        down += 25
        alerts.append(f"SETUP_DOWN:funding_very_pos({snap.funding_rate:.6f})")
    elif snap.funding_rate >= th.get("funding_positive", 0.00005):
        down += 15
        alerts.append(f"SETUP_DOWN:funding_pos({snap.funding_rate:.6f})")

    if snap.short_account is not None and snap.short_account >= th["short_account_min"]:
        up += 15
        alerts.append(f"SETUP_UP:short_crowd({snap.short_account:.3f})")
    long_account = (1.0 - snap.short_account) if snap.short_account is not None else None
    if long_account is not None and long_account >= th.get("long_account_min", 0.52):
        down += 15
        alerts.append(f"SETUP_DOWN:long_crowd({long_account:.3f})")

    if snap.long_short_ratio is not None and snap.long_short_ratio <= th["ls_ratio_max"]:
        up += 10
        alerts.append(f"SETUP_UP:ls_ratio_low({snap.long_short_ratio:.3f})")
    if snap.long_short_ratio is not None and snap.long_short_ratio >= th.get("ls_ratio_min", 1.05):
        down += 10
        alerts.append(f"SETUP_DOWN:ls_ratio_high({snap.long_short_ratio:.3f})")

    # --- TRIGGER: signed returns ---
    def add_trigger(label: str, ret: float, thr: float, pts: int):
        nonlocal up, down
        if abs(ret) < thr:
            return
        if ret > 0:
            up += pts
            alerts.append(f"TRIGGER_UP:{label}({ret:+.2f}%)")
        else:
            down += pts
            alerts.append(f"TRIGGER_DOWN:{label}({ret:+.2f}%)")

    add_trigger("ret_1m", snap.ret_1m_pct, th["ret_1m_pct"], 20)
    add_trigger("ret_5m", snap.ret_5m_pct, th["ret_5m_pct"], 20)
    add_trigger("ret_15m", snap.ret_15m_pct, th["ret_15m_pct"], 15)

    if snap.volume_z_5m >= th["volume_z_5m"]:
        # volume confirms whichever side price is leaning
        if snap.ret_5m_pct >= 0:
            up += 15
            alerts.append(f"TRIGGER_UP:vol_z({snap.volume_z_5m:.2f})")
        else:
            down += 15
            alerts.append(f"TRIGGER_DOWN:vol_z({snap.volume_z_5m:.2f})")

    # --- CASCADE confirm ---
    if snap.ret_5m_pct > 0 and oi_drop_pct >= th["oi_drop_pct_15m"]:
        up += 25
        alerts.append(f"SQUEEZE:oi_drop_while_up({oi_drop_pct:.2f}%)")
    if snap.ret_5m_pct < 0 and oi_drop_pct >= th["oi_drop_pct_15m"]:
        down += 25
        alerts.append(f"DUMP:oi_drop_while_down({oi_drop_pct:.2f}%)")
    if snap.ret_5m_pct > 0 and oi_rise_pct >= th["oi_rise_pct_15m"]:
        up += 10
        alerts.append(f"MOMENTUM_UP:oi_rise({oi_rise_pct:.2f}%)")
    if snap.ret_5m_pct < 0 and oi_rise_pct >= th["oi_rise_pct_15m"]:
        down += 10
        alerts.append(f"MOMENTUM_DOWN:oi_rise({oi_rise_pct:.2f}%)")

    if abs(snap.premium_bps) >= th["premium_bps_abs"]:
        if snap.premium_bps > 0:
            up += 5
            alerts.append(f"PREMIUM_UP:{snap.premium_bps:+.1f}bps")
        else:
            down += 5
            alerts.append(f"PREMIUM_DOWN:{snap.premium_bps:+.1f}bps")

    snap.up_score = min(up, 100)
    snap.down_score = min(down, 100)
    snap.risk_score = max(snap.up_score, snap.down_score)
    if snap.up_score > snap.down_score + 5:
        snap.direction = "up"
    elif snap.down_score > snap.up_score + 5:
        snap.direction = "down"
    elif snap.risk_score == 0:
        snap.direction = "neutral"
    else:
        # tie-break with recent return
        if abs(snap.ret_5m_pct) >= 0.05:
            snap.direction = "up" if snap.ret_5m_pct > 0 else "down"
        else:
            snap.direction = "neutral"
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


def infer_direction(alerts: List[str], ret_5m: float = 0.0) -> str:
    up = sum(1 for a in alerts if ("_UP" in a or a.startswith("SQUEEZE") or "while_up" in a or "funding_neg" in a or "short_crowd" in a or "ls_ratio_low" in a))
    down = sum(1 for a in alerts if ("_DOWN" in a or a.startswith("DUMP") or "while_down" in a or "funding_pos" in a or "long_crowd" in a or "ls_ratio_high" in a))
    if up > down:
        return "up"
    if down > up:
        return "down"
    if ret_5m > 0.05:
        return "up"
    if ret_5m < -0.05:
        return "down"
    return "neutral"


def alert_level(score: int, th: dict, direction: str = "neutral") -> Optional[str]:
    if score >= th["squeeze_score"]:
        if direction == "down":
            return "DUMP"
        if direction == "up":
            return "SQUEEZE"
        return "CASCADE"
    if score >= th["trigger_score"]:
        return "TRIGGER"
    if score >= th["setup_score"]:
        return "SETUP"
    return None


def direction_label(direction: str) -> str:
    return {"up": "급등", "down": "급락", "neutral": "중립"}.get(direction, direction)


def format_alert(snap: Snapshot, level: str) -> str:
    oi_m = (snap.oi_value_usdt or 0) / 1e6
    ls = "n/a" if snap.long_short_ratio is None else f"{snap.long_short_ratio:.3f}"
    short_a = "n/a" if snap.short_account is None else f"{snap.short_account:.3f}"
    bias = direction_label(snap.direction)
    lines = [
        f"<b>[{html.escape(level)}] {html.escape(snap.symbol)}.P {html.escape(bias)} 감시</b>",
        f"direction: <b>{html.escape(snap.direction)}</b> ({html.escape(bias)}) | score: <b>{snap.risk_score}</b> (up {snap.up_score}/down {snap.down_score})",
        f"{html.escape(fmt_kst(snap.ts))}",
        f"mark: {snap.mark_price:,.2f} | 24h: {snap.price_change_24h_pct:+.2f}%",
        f"ret 1/5/15m: {snap.ret_1m_pct:+.2f}% / {snap.ret_5m_pct:+.2f}% / {snap.ret_15m_pct:+.2f}%",
        f"funding: {snap.funding_rate:.6f} | premium: {snap.premium_bps:+.1f} bps",
        f"OI: {snap.open_interest:,.2f} (~${oi_m:,.1f}M) | L/S: {ls} | short%: {short_a}",
        f"vol_z(5m): {snap.volume_z_5m:.2f}",
        "flags: " + html.escape(", ".join(snap.alerts) if snap.alerts else "-"),
        "",
        "<i>급등·급락 사전 경고용. 매매 신호가 아닙니다.</i>",
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
    level = alert_level(snap.risk_score, th, snap.direction)
    if not level:
        return None

    last_map = state.setdefault("last_alert_ts", {})
    key = f"{snap.symbol}:{level}:{snap.direction}"
    now = time.time()
    last = float(last_map.get(key) or 0)
    cooldown = float(scfg.get("cooldown_sec", 900))
    severe = level in {"SQUEEZE", "DUMP", "CASCADE"}
    if now - last < cooldown and not severe:
        logger.info("Cooldown active for %s (%ss left)", key, int(cooldown - (now - last)))
        return None
    if severe and now - last < cooldown / 2:
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
    for lv in ("SETUP", "TRIGGER", "SQUEEZE", "DUMP", "CASCADE"):
        last_map[f"{snap.symbol}:{lv}:{snap.direction}"] = now
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
