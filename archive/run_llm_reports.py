# run_llm_reports_merged.py
# - Backward compatible with existing "run_llm_reports.py" behavior
# - Adds subcommand: report current
# - Dashboard output: signals + indicators + llm_reports (candle-first)
#
# Usage:
#   python run_llm_reports_merged.py --config config.yaml ...        # same as before (defaults to "run")
#   python run_llm_reports_merged.py run --config config.yaml ...    # explicit
#   python run_llm_reports_merged.py report current --config config.yaml --symbol XMRUSDT --tf 30m

import os
import sys
import json
import argparse
import datetime as _dt
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from zoneinfo import ZoneInfo

import yaml
import requests
import numpy as np
import psycopg2
import psycopg2.extras

try:
    import faiss  # faiss-cpu
except ImportError:
    faiss = None

from pydantic import BaseModel, Field, ValidationError


# =============================================================================
# Locale/time helpers
# =============================================================================
KST = ZoneInfo("Asia/Seoul")


def _to_kst_str(dt: Any) -> str:
    """Best-effort: convert datetime to KST string."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    if isinstance(dt, (_dt.date, _dt.time)) and not isinstance(dt, _dt.datetime):
        return str(dt)
    if isinstance(dt, _dt.datetime):
        if dt.tzinfo is None:
            # Many deployments store naive timestamps as UTC.
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    return str(dt)


# =============================================================================
# Language/consistency guards (Korean-only output + symbol/tag hygiene)
# =============================================================================
import re

_RE_CJK = re.compile(r"[\u4e00-\u9fff]")  # Chinese Han range
_RE_JP = re.compile(r"[\u3040-\u30ff]")   # Hiragana/Katakana


def _has_cjk_or_jp(text: str) -> bool:
    if not text:
        return False
    return bool(_RE_CJK.search(text) or _RE_JP.search(text))


def _walk_has_cjk(obj: Any) -> bool:
    if obj is None:
        return False
    if isinstance(obj, str):
        return _has_cjk_or_jp(obj)
    if isinstance(obj, list):
        return any(_walk_has_cjk(v) for v in obj)
    if isinstance(obj, dict):
        return any(_walk_has_cjk(v) for v in obj.values())
    return False


def enforce_consistency(report: Dict[str, Any], *, symbol: str, tf: str, strategy_tag: str) -> Dict[str, Any]:
    """Force tags and keep headline anchored to the right symbol/tf."""
    out = dict(report or {})
    # Hard override tags to avoid cross-symbol contamination.
    out["tags"] = [strategy_tag, tf, symbol]

    hl = str(out.get("headline") or "").strip()
    if symbol and symbol not in hl:
        out["headline"] = f"{symbol} {tf} {strategy_tag} {hl}".strip()

    return out


def generate_valid_report(
    *,
    session: requests.Session,
    host: str,
    model: str,
    prompt: str,
    temperature: float,
    timeout: int,
    symbol: str,
    tf: str,
    strategy_tag: str,
    max_regen: int = 2,
) -> Tuple[Dict[str, Any], str]:
    """LLM -> JSON -> schema validation -> consistency enforcement -> (optional) Korean-only regen."""
    last_raw = ""
    last_err: Optional[Exception] = None

    regen_note = (
        "\n\n[재생성 지시]\n"
        "- 이전 출력에 한국어가 아닌(중국어/일본어) 문자가 포함되었거나 규칙을 위반했습니다.\n"
        "- 반드시 한국어만 사용하고, 다른 코인 심볼을 언급하지 말고, JSON만 출력하세요.\n"
    )

    for attempt in range(max_regen + 1):
        p = prompt
        if attempt > 0:
            p = prompt + regen_note

        try:
            last_raw = ollama_generate(session, host, model, p, temperature=temperature, timeout=int(timeout))
            obj = force_json(last_raw)
            rep = validate_report(obj)
            rep = enforce_consistency(rep, symbol=symbol, tf=tf, strategy_tag=strategy_tag)

            # Korean-only guard: block Chinese/Japanese characters in key narrative fields.
            if _walk_has_cjk({
                "headline": rep.get("headline"),
                "evidence": rep.get("evidence"),
                "plan": rep.get("plan"),
                "risks": rep.get("risks"),
            }):
                raise ValueError("Non-Korean characters detected in report fields")

            return rep, last_raw
        except Exception as e:
            last_err = e

    # If we get here, raise the last error.
    raise RuntimeError(f"LLM report generation failed after retries: {last_err}")

# Candle-first report fetch helper (embedded; no external import required)
def _latest_open_time_from_indicators(
    conn,
    symbol: str,
    tf: str,
    *,
    indicators_table: str = "indicators",
) -> Optional[Any]:
    """
    Returns latest open_time from indicators table (best proxy for "current candle").
    Requires columns: symbol, tf, open_time.
    """
    sql = f"""
    SELECT MAX(open_time) AS open_time
    FROM {indicators_table}
    WHERE symbol=%s AND tf=%s;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute(sql, (symbol, tf))
            row = cur.fetchone()
            return row["open_time"] if row else None
        except Exception:
            # indicators table/cols may not exist in some deployments
            return None


def _latest_open_time_from_reports(
    conn,
    symbol: str,
    tf: str,
    *,
    stage: Optional[str] = None,
    require_ok: bool = True,
) -> Optional[Any]:
    """
    Fallback: latest open_time from llm_reports itself.
    """
    ok_clause = "AND ok=TRUE" if require_ok else ""
    stage_clause = "AND stage=%s" if stage else ""
    sql = f"""
    SELECT open_time
    FROM llm_reports
    WHERE symbol=%s AND tf=%s
      AND open_time IS NOT NULL
      {stage_clause}
      {ok_clause}
    ORDER BY open_time DESC, created_at DESC
    LIMIT 1;
    """
    with conn.cursor() as cur:
        try:
            if stage:
                cur.execute(sql, (symbol, tf, stage))
            else:
                cur.execute(sql, (symbol, tf))
            row = cur.fetchone()
            return row[0] if row else None
        except Exception:
            return None


def check_current_report_v2(
    conn,
    *,
    stage: str,
    symbol: Optional[str] = None,
    tf: Optional[str] = None,
    open_time=None,                 # datetime with tz recommended
    event_key: Optional[str] = None,
    require_ok: bool = True,
    indicators_table: str = "indicators",
    auto_pick_latest_open_time: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Candle-first report fetch.

    Priority:
      0) If (symbol, tf) provided and open_time is None and auto_pick_latest_open_time=True:
           open_time := latest open_time from indicators (fallback: llm_reports)
      1) (symbol, tf, open_time, stage) -> latest created_at row
      2) (event_key, stage) -> latest created_at row

    Returns:
      dict with id, stage, ok, created_at, response_json, bias, confidence, headline,
      symbol, tf, open_time, event_key
    """

    ok_clause = "AND ok=TRUE" if require_ok else ""

    # Auto choose latest candle if requested
    if auto_pick_latest_open_time and symbol and tf and open_time is None:
        ot = _latest_open_time_from_indicators(conn, symbol, tf, indicators_table=indicators_table)
        if ot is None:
            ot = _latest_open_time_from_reports(conn, symbol, tf, stage=stage, require_ok=require_ok)
        open_time = ot

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # 1) candle-first
        if symbol and tf and open_time is not None:
            sql = f"""
            SELECT
              id,
              stage,
              ok,
              created_at,
              response_json,
              response_json->>'bias' AS bias,
              NULLIF(response_json->>'confidence','')::float AS confidence,
              response_json->>'headline' AS headline,
              symbol, tf, open_time, event_key
            FROM llm_reports
            WHERE symbol=%s
              AND tf=%s
              AND open_time=%s
              AND stage=%s
              {ok_clause}
            ORDER BY created_at DESC
            LIMIT 1;
            """
            cur.execute(sql, (symbol, tf, open_time, stage))
            row = cur.fetchone()
            if row:
                return dict(row)

        # 2) fallback: event_key
        if event_key:
            sql = f"""
            SELECT
              id,
              stage,
              ok,
              created_at,
              response_json,
              response_json->>'bias' AS bias,
              NULLIF(response_json->>'confidence','')::float AS confidence,
              response_json->>'headline' AS headline,
              symbol, tf, open_time, event_key
            FROM llm_reports
            WHERE event_key=%s
              AND stage=%s
              {ok_clause}
            ORDER BY created_at DESC
            LIMIT 1;
            """
            cur.execute(sql, (str(event_key), stage))
            row = cur.fetchone()
            if row:
                return dict(row)

    return None

# =============================================================================
# JSON-safe helpers
# =============================================================================
def to_json_safe(obj):
    if isinstance(obj, dict):
        return {k: to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_json_safe(v) for v in obj]
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    return obj


def _item_to_str(x: Any) -> str:
    """LLM이 dict/list로 주면 사람이 읽을 수 있는 한 줄 문자열로 변환."""
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip()
    if isinstance(x, (int, float, bool)):
        return str(x)
    if isinstance(x, dict):
        t = x.get("type") or x.get("improvement_point") or x.get("title") or ""
        d = x.get("detail") or x.get("details") or x.get("desc") or x.get("description") or ""
        if t and d:
            return f"{t}: {d}".strip()
        if t:
            rest = {k: v for k, v in x.items() if k not in ("type", "title", "improvement_point")}
            if rest:
                return f"{t}: {json.dumps(to_json_safe(rest), ensure_ascii=False)}"
            return str(t).strip()
        return json.dumps(to_json_safe(x), ensure_ascii=False)
    if isinstance(x, list):
        parts = [_item_to_str(i) for i in x]
        parts = [p for p in parts if p]
        return "; ".join(parts)
    return str(x).strip()


def normalize_report_payload(obj: Dict[str, Any]) -> Dict[str, Any]:
    """ReportJSON 스키마에 맞게 강제 변환."""
    obj = dict(obj or {})

    obj.setdefault("headline", "")
    obj.setdefault("bias", "neutral")
    obj.setdefault("evidence", [])
    obj.setdefault("levels", {})
    obj.setdefault("risks", [])
    obj.setdefault("plan", [])
    obj.setdefault("confidence", 0.5)
    obj.setdefault("tags", [])

    for k in ("evidence", "risks", "plan", "tags"):
        v = obj.get(k)
        if v is None:
            obj[k] = []
        elif isinstance(v, list):
            obj[k] = [s for s in (_item_to_str(i) for i in v) if s]
        else:
            s = _item_to_str(v)
            obj[k] = [s] if s else []

    if not isinstance(obj.get("levels"), dict):
        obj["levels"] = {"value": _item_to_str(obj.get("levels"))}

    try:
        obj["confidence"] = float(obj.get("confidence", 0.5))
    except Exception:
        obj["confidence"] = 0.5
    obj["confidence"] = max(0.0, min(1.0, obj["confidence"]))

    bias = str(obj.get("bias", "neutral")).lower().strip()
    obj["bias"] = bias if bias in ("bull", "bear", "neutral") else "neutral"

    if not str(obj.get("headline") or "").strip():
        ev0 = obj["evidence"][0] if obj["evidence"] else "Report"
        obj["headline"] = ev0[:80]

    return obj


def normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v if n == 0 else (v / n)


# =============================================================================
# LLM JSON schema
# =============================================================================
class ReportJSON(BaseModel):
    headline: str
    bias: str = Field(..., description="bull | bear | neutral")
    evidence: List[str]
    levels: Dict[str, Any] = Field(default_factory=dict)
    risks: List[str] = Field(default_factory=list)
    plan: List[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    tags: List[str] = Field(default_factory=list)


# =============================================================================
# Config + validation helpers
# =============================================================================
def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_dsn(cfg: Dict[str, Any]) -> str:
    # prefer pg.dsn if present
    if "pg" in cfg and isinstance(cfg["pg"], dict) and cfg["pg"].get("dsn"):
        return cfg["pg"]["dsn"]

    # fallback: construct from db: {host, port, name, user, password}
    db = cfg.get("db", {})
    host = db.get("host", "localhost")
    port = db.get("port", 5432)
    name = db.get("name")
    user = db.get("user")
    password = db.get("password", "")
    if not (name and user):
        raise RuntimeError("config.yaml needs either pg.dsn or db.{name,user} fields")
    return f"host={host} port={port} dbname={name} user={user} password={password}"


def force_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e != -1 and e > s:
        return json.loads(text[s:e + 1])
    raise ValueError("No JSON object found in LLM output")


def validate_report(obj: Dict[str, Any]) -> Dict[str, Any]:
    obj2 = normalize_report_payload(obj)
    return ReportJSON(**obj2).model_dump()


# =============================================================================
# Ollama client (Session + connect/read timeout)
# =============================================================================
def ollama_generate(session: requests.Session, host: str, model: str, prompt: str,
                    temperature: float = 0.2, timeout: int = 180) -> str:
    url = f"{host}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": temperature}}
    r = session.post(url, json=payload, timeout=(5, timeout))
    r.raise_for_status()
    return r.json().get("response", "")


def ollama_embed(session: requests.Session, host: str, model: str, text: str, timeout: int = 120) -> List[float]:
    url = f"{host}/api/embeddings"
    payload = {"model": model, "prompt": text}
    r = session.post(url, json=payload, timeout=(5, timeout))
    r.raise_for_status()
    emb = r.json().get("embedding")
    if not emb:
        raise RuntimeError("No embedding returned from Ollama")
    return emb


# =============================================================================
# Prompt builders (schema-aligned + RAG injected)
# =============================================================================
def build_pre_prompt(ev: Dict[str, Any], memory_snippets: List[str]) -> str:
    schema = {
        "headline": "string",
        "bias": "bull|bear|neutral",
        "evidence": ["string"],
        "levels": {"entry": "...", "tp": "...", "sl": "..."},
        "risks": ["string"],
        "plan": ["string"],
        "confidence": "0.0~1.0",
        "tags": ["string"],
    }

    reason = ev.get("reason_json") or {}
    reason_str = json.dumps(to_json_safe(reason), ensure_ascii=False)

    mem_block = "None"
    if memory_snippets:
        mem_block = "\n".join([f"- {s}" for s in memory_snippets])

    symbol = str(ev.get("symbol") or "").strip().upper()
    tf = str(ev.get("tf") or "").strip()
    strategy_tag = str(ev.get("strategy") or "").strip()

    return f"""
[절대 규칙]
- 출력은 반드시 한국어만 사용한다. (영어/중국어/일본어 금지)
- 예외: 심볼/지표키/타임프레임 키워드(예: {symbol}, {strategy_tag}, {tf})는 원문 유지 가능.
- headline/plan/risks/evidence의 문장들은 모두 한국어로 작성한다.
- 아래 event 정보의 symbol/tf/strategy를 절대 바꾸지 말고, 다른 코인 심볼을 언급하지 않는다.
- 출력은 오직 JSON 한 개만 반환한다. 마크다운/설명/코드블록 금지.

[출력 형식]
반드시 JSON만 출력.

JSON schema (keys must match exactly):
{json.dumps(schema, ensure_ascii=False)}

Event (facts):
signal_id: {ev["signal_id"]}
symbol: {symbol}
tf: {tf}
open_time: {to_json_safe(ev.get("open_time"))}
strategy: {strategy_tag}
direction: {ev.get("direction")}
entry: {ev.get("entry")}
stop_loss: {ev.get("stop_loss")}
take_profit_1: {ev.get("take_profit_1")}
take_profit_2: {ev.get("take_profit_2")}
score: {ev.get("score")}

Context JSON (reason_json):
{reason_str}

Similar past cases (TopK):
{mem_block}

Rules:
- evidence: reason_json + 지표조건에서 '관측 사실'만 적기.
- levels: entry/tp/sl은 숫자 또는 조건 형태로 간결히.
- risks/plan: 과도한 확신 금지. (유사 과거 사례의 실패/성공 요인을 참고)
- evidence/risks/plan/tags 는 반드시 string 배열만 사용. dict/객체 금지.
- 구조화가 필요하면 한 줄 문자열로 인코딩. 예: "개선: ..."
""".strip()


def build_post_prompt(ev: Dict[str, Any], outcome: Dict[str, Any], pre_report: Dict[str, Any]) -> str:
    symbol = str(ev.get("symbol") or "").strip().upper()
    tf = str(ev.get("tf") or "").strip()
    strategy_tag = str(ev.get("strategy") or "").strip()

    return f"""
[절대 규칙]
- 출력은 반드시 한국어만 사용한다. (영어/중국어/일본어 금지)
- 예외: 심볼/지표키/타임프레임 키워드(예: {symbol}, {strategy_tag}, {tf})는 원문 유지 가능.
- 아래 event 정보의 symbol/tf/strategy를 절대 바꾸지 말고, 다른 코인 심볼을 언급하지 않는다.
- 출력은 오직 JSON 한 개만 반환한다. 마크다운/설명/코드블록 금지.

[출력 형식]
반드시 JSON만 출력.

We are evaluating a past signal and its outcome.
Use the same JSON schema as the pre-report.

Signal (facts):
signal_id: {ev["signal_id"]}
symbol: {symbol}
tf: {tf}
open_time: {to_json_safe(ev.get("open_time"))}
strategy: {strategy_tag}
direction: {ev.get("direction")}
entry: {ev.get("entry")}
stop_loss: {ev.get("stop_loss")}
take_profit_1: {ev.get("take_profit_1")}
take_profit_2: {ev.get("take_profit_2")}
score: {ev.get("score")}
reason_json: {json.dumps(to_json_safe(ev.get("reason_json") or {}), ensure_ascii=False)}

Pre-report(JSON):
{json.dumps(pre_report, ensure_ascii=False)}

Outcome (facts):
{json.dumps(to_json_safe(outcome), ensure_ascii=False)}

Instructions:
- evidence: outcome 기반으로 TP/SL/시간/변동성(MFE/MAE) 관점 근거를 추가.
- plan: 다음번 유사 조건에서 개선점을 구체적으로.
- confidence: 재현성/명확성 관점으로 조정.
- evidence/risks/plan/tags 는 반드시 string 배열만 사용. dict/객체 금지.
- 구조화가 필요하면 한 줄 문자열로 인코딩. 예: "개선: ..."
""".strip()


# =============================================================================
# FAISS store
# =============================================================================
class FaissStore:
    def __init__(self, index, next_id: int, dim: int):
        self.index = index
        self.next_id = next_id
        self.dim = dim


def load_faiss(path: str, dim: int) -> FaissStore:
    if faiss is None:
        raise RuntimeError("faiss not installed. pip install faiss-cpu")
    if os.path.exists(path):
        idx = faiss.read_index(path)
        return FaissStore(idx, next_id=idx.ntotal, dim=dim)
    base = faiss.IndexFlatIP(dim)
    idx = faiss.IndexIDMap2(base)
    return FaissStore(idx, next_id=0, dim=dim)


def save_faiss(store: FaissStore, path: str) -> None:
    faiss.write_index(store.index, path)


# =============================================================================
# PG access
# =============================================================================
def pg_connect(dsn: str):
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    return conn


def fetch_pending_signals(conn, table_cfg: Dict[str, Any], limit: int, stage_missing: str) -> List[Dict[str, Any]]:
    t_signals = table_cfg["signals"]
    s = table_cfg["signals_cols"]

    sql = f"""
    SELECT
      s.{s["event_key"]}   AS signal_id,
      s.{s["symbol"]}      AS symbol,
      s.{s["timeframe"]}   AS tf,
      s.{s["ts"]}          AS open_time,
      s.{s["signal_type"]} AS strategy,
      s.{s["direction"]}   AS direction,
      s.{s["entry"]}       AS entry,
      s.{s["stop_loss"]}   AS stop_loss,
      s.{s["tp1"]}         AS take_profit_1,
      s.{s["tp2"]}         AS take_profit_2,
      s.{s["score"]}       AS score,
      s.{s["payload"]}     AS reason_json
    FROM {t_signals} s
    LEFT JOIN llm_reports r
      ON r.event_key = s.{s["event_key"]}::text
     AND r.stage = %s
     AND r.ok=TRUE
    WHERE r.id IS NULL
    ORDER BY s.{s["ts"]} DESC
    LIMIT %s;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (stage_missing, limit))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def fetch_recent_signals(conn, table_cfg: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    t_signals = table_cfg["signals"]
    s = table_cfg["signals_cols"]

    sql = f"""
    SELECT
      s.{s["event_key"]}   AS signal_id,
      s.{s["symbol"]}      AS symbol,
      s.{s["timeframe"]}   AS tf,
      s.{s["ts"]}          AS open_time,
      s.{s["signal_type"]} AS strategy,
      s.{s["direction"]}   AS direction,
      s.{s["entry"]}       AS entry,
      s.{s["stop_loss"]}   AS stop_loss,
      s.{s["tp1"]}         AS take_profit_1,
      s.{s["tp2"]}         AS take_profit_2,
      s.{s["score"]}       AS score,
      s.{s["payload"]}     AS reason_json
    FROM {t_signals} s
    ORDER BY s.{s["ts"]} DESC
    LIMIT %s;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def fetch_latest_signal(conn, table_cfg: Dict[str, Any], symbol: Optional[str], tf: Optional[str]) -> Optional[Dict[str, Any]]:
    t_signals = table_cfg["signals"]
    s = table_cfg["signals_cols"]

    wh = []
    params: List[Any] = []
    if symbol:
        wh.append(f"s.{s['symbol']} = %s")
        params.append(symbol)
    if tf:
        wh.append(f"s.{s['timeframe']} = %s")
        params.append(tf)
    where_sql = ("WHERE " + " AND ".join(wh)) if wh else ""

    sql = f"""
    SELECT
      s.{s["event_key"]}   AS signal_id,
      s.{s["symbol"]}      AS symbol,
      s.{s["timeframe"]}   AS tf,
      s.{s["ts"]}          AS open_time,
      s.{s["signal_type"]} AS strategy,
      s.{s["direction"]}   AS direction,
      s.{s["entry"]}       AS entry,
      s.{s["stop_loss"]}   AS stop_loss,
      s.{s["tp1"]}         AS take_profit_1,
      s.{s["tp2"]}         AS take_profit_2,
      s.{s["score"]}       AS score,
      s.{s["payload"]}     AS reason_json
    FROM {t_signals} s
    {where_sql}
    ORDER BY s.{s["ts"]} DESC
    LIMIT 1;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, tuple(params))
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_outcome(conn, table_cfg: Dict[str, Any], signal_id: str) -> Optional[Dict[str, Any]]:
    t_out = table_cfg["outcomes"]
    o = table_cfg["outcomes_cols"]

    sql = f"""
    SELECT
      o.{o["event_key"]}    AS signal_id,
      o.{o["exit_time"]}    AS exit_time,
      o.{o["result"]}       AS result,
      o.{o["r_multiple"]}   AS r_multiple,
      o.{o["mfe"]}          AS mfe,
      o.{o["mae"]}          AS mae,
      o.{o["evaluated_at"]} AS evaluated_at,
      o.{o["exit_price"]}   AS exit_price
    FROM {t_out} o
    WHERE o.{o["event_key"]} = %s
    LIMIT 1;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (signal_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_indicators_row(conn, symbol: str, tf: str, open_time, table: str = "indicators") -> Optional[Dict[str, Any]]:
    sql = f"""
    SELECT *
    FROM {table}
    WHERE symbol=%s AND tf=%s AND open_time=%s
    LIMIT 1;
    """
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (symbol, tf, open_time))
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def upsert_llm_report(conn, signal_id: str, stage: str, model: str, prompt_version: str,
                      prompt: str, response_json: Dict[str, Any], ok: bool, error: Optional[str],
                      symbol: Optional[str] = None, tf: Optional[str] = None, open_time=None) -> int:
    """
    Candle-aware upsert:
      - If (symbol,tf,open_time) present -> use candle uniq
      - Else fallback to event_key uniq
    """
    if symbol is not None and tf is not None and open_time is not None:
        sql = """
        INSERT INTO llm_reports(
        event_key, stage, model, prompt_version, prompt, response_json, ok, error,
        symbol, tf, open_time
        )
        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
        ON CONFLICT(symbol, tf, open_time, stage, model, prompt_version)
        WHERE symbol IS NOT NULL AND tf IS NOT NULL AND open_time IS NOT NULL
        DO UPDATE SET
        event_key     = EXCLUDED.event_key,
        prompt        = EXCLUDED.prompt,
        response_json = EXCLUDED.response_json,
        ok            = EXCLUDED.ok,
        error         = EXCLUDED.error
        RETURNING id;
        """
        params = (
            str(signal_id), stage, model, prompt_version, prompt,
            json.dumps(to_json_safe(response_json), ensure_ascii=False),
            ok, error,
            symbol, tf, open_time,
        )
    else:
        sql = """
        INSERT INTO llm_reports(event_key, stage, model, prompt_version, prompt, response_json, ok, error)
        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
        ON CONFLICT(event_key, stage, model, prompt_version)
        DO UPDATE SET
          prompt = EXCLUDED.prompt,
          response_json = EXCLUDED.response_json,
          ok = EXCLUDED.ok,
          error = EXCLUDED.error
        RETURNING id;
        """
        params = (
            str(signal_id), stage, model, prompt_version, prompt,
            json.dumps(to_json_safe(response_json), ensure_ascii=False),
            ok, error,
        )

    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def upsert_embedding_meta(conn, report_id: int, embed_model: str, dim: int, faiss_id: int) -> None:
    sql = """
    INSERT INTO llm_report_embeddings(report_id, embed_model, dim, faiss_id)
    VALUES (%s,%s,%s,%s)
    ON CONFLICT(report_id, embed_model)
    DO UPDATE SET dim=EXCLUDED.dim, faiss_id=EXCLUDED.faiss_id;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (report_id, embed_model, dim, faiss_id))


def fetch_reports_by_faiss_ids(conn, faiss_ids: List[int], embed_model: str, exclude_signal_id: str,
                               outcomes_table: str = "outcomes") -> List[Dict[str, Any]]:
    if not faiss_ids:
        return []
    sql = f"""
    SELECT
      r.id,
      r.event_key,
      r.stage,
      r.response_json,
      r.created_at,
      e.faiss_id,
      o.result       AS outcome_result,
      o.r_multiple   AS outcome_r_multiple,
      o.mfe          AS outcome_mfe,
      o.mae          AS outcome_mae,
      o.evaluated_at AS outcome_evaluated_at
    FROM llm_report_embeddings e
    JOIN llm_reports r ON r.id = e.report_id
    LEFT JOIN {outcomes_table} o ON o.signal_id = r.event_key::uuid
    WHERE e.embed_model = %s
      AND e.faiss_id = ANY(%s)
      AND r.ok = TRUE
      AND r.event_key <> %s
      AND r.stage = 'post'
    ORDER BY r.created_at DESC;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (embed_model, faiss_ids, exclude_signal_id))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


# =============================================================================
# RAG helpers
# =============================================================================
def build_query_embed_text(ev: Dict[str, Any]) -> str:
    ctx = ev.get("reason_json") or {}
    query_payload = {
        "symbol": ev.get("symbol"),
        "tf": ev.get("tf"),
        "strategy": ev.get("strategy"),
        "direction": ev.get("direction"),
        "score": ev.get("score"),
        "entry": ev.get("entry"),
        "sl": ev.get("stop_loss"),
        "tp1": ev.get("take_profit_1"),
        "tp2": ev.get("take_profit_2"),
        "ctx": {
            "htf_1d": ctx.get("htf_1d"),
            "htf_4h": ctx.get("htf_4h"),
            "vwap_position": ctx.get("vwap_position"),
            "ema_alignment": ctx.get("ema_alignment"),
            "rsi_ok": ctx.get("rsi_ok"),
        }
    }
    return json.dumps(to_json_safe(query_payload), ensure_ascii=False)


def summarize_post_reports_for_prompt(rows: List[Dict[str, Any]], max_items: int = 5) -> List[str]:
    out: List[str] = []
    for r in rows[:max_items]:
        rep = r.get("response_json") or {}
        headline = rep.get("headline", "")
        bias = rep.get("bias", "")
        tags = rep.get("tags", []) or []
        risks = rep.get("risks", []) or []
        plan = rep.get("plan", []) or []

        outcome_result = r.get("outcome_result")
        outcome_r = r.get("outcome_r_multiple")

        def cut(s: str, n: int = 160) -> str:
            s = (s or "").strip().replace("\n", " ")
            return s if len(s) <= n else s[:n] + "…"

        tag_part = ", ".join([str(t) for t in tags[:5]]) if tags else ""
        risk_part = cut("; ".join([str(x) for x in risks[:2]]), 120) if risks else ""
        plan_part = cut("; ".join([str(x) for x in plan[:2]]), 120) if plan else ""

        outcome_part = ""
        if outcome_result is not None or outcome_r is not None:
            r_str = ""
            try:
                if outcome_r is not None:
                    r_str = f"{float(outcome_r):+.2f}".rstrip("0").rstrip(".")
            except Exception:
                r_str = str(outcome_r)
            outcome_part = f" | outcome={outcome_result or 'NA'} r={r_str or 'NA'}"

        line = f"[{r.get('event_key')}] {cut(headline, 120)} | bias={bias}{outcome_part}"
        if tag_part:
            line += f" | tags={cut(tag_part, 80)}"
        if risk_part:
            line += f" | risks={risk_part}"
        if plan_part:
            line += f" | fix={plan_part}"
        out.append(line)
    return out


# =============================================================================
# REPORT DASHBOARD
# =============================================================================
def _print_kv(title: str, d: Dict[str, Any], keys: List[str]):
    print(f"\n[{title}]")
    for k in keys:
        if k in d:
            print(f"- {k}: {d.get(k)}")


def report_current(args) -> None:
    cfg = load_config(args.config)
    dsn = build_dsn(cfg)

    llm_cfg = cfg.get("llm_reports", {})
    table_cfg = llm_cfg.get("tables", {}) or {}
    if not table_cfg:
        raise RuntimeError("config.yaml missing llm_reports.tables")

    conn = pg_connect(dsn)
    try:
        sym = (args.symbol or "").strip().upper() or None
        tf = (args.tf or "").strip() or None
        if (sym and not tf) or (tf and not sym):
            raise RuntimeError("report current: --symbol and --tf must be provided together (or both omitted).")

        # 1) latest signal (source of truth for 'current')
        sig = fetch_latest_signal(conn, table_cfg, sym, tf)
        if not sig:
            print("[MISS] No signal found.")
            return

        # stage default: mode-dependent
        stage = args.stage or ("pre_rag" if args.mode == "rag" else "pre")

        # 2) indicators at that open_time (best effort)
        ind_table = table_cfg.get("indicators", "indicators")
        ind = fetch_indicators_row(conn, sig["symbol"], sig["tf"], sig["open_time"], table=ind_table)

        # 3) report candle-first
        rep = check_current_report_v2(
            conn,
            stage=stage,
            symbol=sig["symbol"],
            tf=sig["tf"],
            open_time=sig["open_time"],      # use signal candle
            event_key=str(sig["signal_id"]), # fallback
            require_ok=True,
            indicators_table=ind_table,
            auto_pick_latest_open_time=False,
        )

        # 4) Print dashboard
        print("\n==================== CURRENT DASHBOARD ====================")
        print(f"stage={stage} mode={args.mode}")
        _print_kv("signal", sig, ["signal_id", "symbol", "tf", "open_time", "strategy", "direction", "entry", "stop_loss", "take_profit_1", "take_profit_2", "score"])
        if sig.get("open_time") is not None:
            print(f"- open_time_kst: {_to_kst_str(sig.get('open_time'))}")
        if sig.get("reason_json") is not None:
            print("\n[signal.reason_json]")
            print(json.dumps(to_json_safe(sig.get("reason_json") or {}), ensure_ascii=False, indent=2))

        if ind:
            # show compact indicators first
            keys = ["ema20", "ema50", "ema200", "vwap", "rsi", "atr"]
            compact = {k: ind.get(k) for k in keys if k in ind}
            print("\n[indicators]")
            print(json.dumps(compact if compact else ind, ensure_ascii=False, indent=2))
        else:
            print("\n[indicators]\n- (not found / table mismatch)")

        if rep:
            print("\n[llm_report]")
            print(f"- id: {rep.get('id')}")
            print(f"- created_at: {rep.get('created_at')}")
            if rep.get("created_at") is not None:
                print(f"- created_at_kst: {_to_kst_str(rep.get('created_at'))}")
            print(f"- event_key: {rep.get('event_key')}")
            print(f"- bias: {rep.get('bias')}")
            print(f"- confidence: {rep.get('confidence')}")
            print(f"- headline: {rep.get('headline')}")
            if args.pretty_json:
                print("\n[llm_report.response_json]")
                print(json.dumps(rep.get("response_json") or {}, ensure_ascii=False, indent=2))
        else:
            print("\n[llm_report]\n- (not found for this candle/stage)")

        print("===========================================================\n")

    finally:
        conn.close()


# =============================================================================
# MAIN (default: run; subcommand: report current)
# =============================================================================
def build_run_parser(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--config", type=str, default="config.yaml")
    ap.add_argument("--limit", type=int, default=None)

    ap.add_argument("--pre-only", action="store_true", help="Generate only pre_rag/pre stage (skip post).")
    ap.add_argument("--no-embed", action="store_true")

    ap.add_argument("--force-last", type=int, default=0, help="Force run last N signals even if stage exists")

    ap.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if pre_rag/pre stage exists (overwrite same candle report)."
    )
    ap.add_argument("--force-stage", type=str, default="pre", choices=["pre", "post", "both"],
                    help="Which stages to force when --force-last > 0")

    ap.add_argument("--mode", type=str, default="rag", choices=["rag", "legacy"],
                    help="rag: generate stage=pre_rag only (operational). legacy: generate stage=pre only.")

    ap.add_argument("--only-symbol", type=str, default="", help="Run only for this symbol (e.g., BTCUSDT).")

    # Speed controls
    ap.add_argument("--workers", type=int, default=4, help="Parallel workers for LLM generation (rag pre stage).")
    ap.add_argument("--commit-every", type=int, default=20, help="Commit every N DB writes (0=commit each write).")
    ap.add_argument("--timeout-llm", type=int, default=240, help="Ollama generate timeout seconds.")
    ap.add_argument("--timeout-embed", type=int, default=120, help="Ollama embedding timeout seconds.")
    ap.add_argument("--save-faiss-every", type=int, default=50, help="Save FAISS index every N embeddings (0=end only).")


def build_report_parser(sp: argparse._SubParsersAction) -> None:
    p_report = sp.add_parser("report", help="Reporting utilities")
    sp_report = p_report.add_subparsers(dest="report_cmd", required=True)

    p_cur = sp_report.add_parser("current", help="Show current dashboard (signals + indicators + llm_reports)")
    p_cur.add_argument("--config", type=str, default="config.yaml")
    p_cur.add_argument("--mode", type=str, default="rag", choices=["rag", "legacy"])
    p_cur.add_argument("--stage", type=str, default="", help="Override stage (default pre_rag/pre)")
    p_cur.add_argument("--symbol", type=str, default="", help="Optional. If omitted, uses latest signal overall.")
    p_cur.add_argument("--tf", type=str, default="", help="Optional. Must be paired with --symbol.")
    p_cur.add_argument("--pretty-json", action="store_true", help="Print full response_json")
    p_cur.set_defaults(_handler=lambda a: report_current(a))
    p_bf = sp_report.add_parser("backfill", help="Create/Upsert latest-candle report (indicators MAX(open_time)).")
    p_bf.add_argument("--config", type=str, default="config.yaml")
    p_bf.add_argument("--symbol", required=True, help="e.g., XMRUSDT")
    p_bf.add_argument("--tf", required=True, help="e.g., 30m")
    p_bf.add_argument("--mode", type=str, default="rag", choices=["rag", "legacy"], help="rag -> pre_rag, legacy -> pre")
    p_bf.add_argument("--stage", type=str, default="", help="Override stage (default pre_rag/pre)")
    p_bf.add_argument("--no-rag", action="store_true", help="Disable RAG retrieval even if embeddings exist")
    p_bf.add_argument("--force-open-time", action="store_true", help="Force ev.open_time to indicators MAX(open_time)")
    p_bf.add_argument("--force", action="store_true", help="Regenerate even if report already exists for the candle")
    p_bf.add_argument("--timeout-llm", type=int, default=240)
    p_bf.add_argument("--timeout-embed", type=int, default=120)
    p_bf.set_defaults(_handler=lambda a: report_backfill(a))

    p_dm = sp_report.add_parser("daemon", help="Auto mode: periodically backfill latest-candle reports.")
    p_dm.add_argument("--config", type=str, default="config.yaml")
    p_dm.add_argument("--symbols", required=True, help="Comma-separated, e.g., XMRUSDT,BTCUSDT")
    p_dm.add_argument("--tf", required=True, help="e.g., 30m")
    p_dm.add_argument("--mode", type=str, default="rag", choices=["rag", "legacy"])
    p_dm.add_argument("--stage", type=str, default="", help="Override stage (default pre_rag/pre)")
    p_dm.add_argument("--every-sec", type=int, default=120, help="Loop interval seconds")
    p_dm.add_argument("--no-rag", action="store_true")
    p_dm.add_argument("--timeout-llm", type=int, default=240)
    p_dm.add_argument("--timeout-embed", type=int, default=120)
    p_dm.set_defaults(_handler=lambda a: report_daemon(a))





# =============================================================================
# Helpers for "report backfill-current"
# =============================================================================
def fetch_latest_open_time_from_indicators(conn, table_cfg: Dict[str, Any], symbol: str, tf: str) -> Optional[_dt.datetime]:
    t_ind = table_cfg.get("indicators", "indicators")
    sql = f"""
    SELECT MAX(open_time) AS open_time
    FROM {t_ind}
    WHERE symbol=%s AND tf=%s;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (symbol, tf))
        row = cur.fetchone()
    return row["open_time"] if row else None


def fetch_signal_by_candle(conn, table_cfg: Dict[str, Any], symbol: str, tf: str, open_time: _dt.datetime) -> Optional[Dict[str, Any]]:
    """
    Try exact candle match first; if not found, fallback to latest <= open_time.
    """
    t_signals = table_cfg.get("signals", "signals")
    s = table_cfg.get("signals_cols", {}) or {}

    col_event_key = s.get("event_key", "event_key")
    col_symbol = s.get("symbol", "symbol")
    col_tf = s.get("timeframe", "tf")
    col_ts = s.get("ts", "open_time")
    col_signal_type = s.get("signal_type", "signal_type")
    col_direction = s.get("direction", "direction")
    col_entry = s.get("entry", "entry")
    col_sl = s.get("stop_loss", "stop_loss")
    col_tp1 = s.get("tp1", "take_profit_1")
    col_tp2 = s.get("tp2", "take_profit_2")
    col_score = s.get("score", "score")
    col_payload = s.get("payload", "reason_json")

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        sql1 = f"""
        SELECT
          {col_event_key}   AS signal_id,
          {col_symbol}      AS symbol,
          {col_tf}          AS tf,
          {col_ts}          AS open_time,
          {col_signal_type} AS strategy,
          {col_direction}   AS direction,
          {col_entry}       AS entry,
          {col_sl}          AS stop_loss,
          {col_tp1}         AS take_profit_1,
          {col_tp2}         AS take_profit_2,
          {col_score}       AS score,
          {col_payload}     AS reason_json
        FROM {t_signals}
        WHERE {col_symbol}=%s AND {col_tf}=%s AND {col_ts}=%s
        LIMIT 1;
        """
        cur.execute(sql1, (symbol, tf, open_time))
        row = cur.fetchone()
        if row:
            return dict(row)

        sql2 = f"""
        SELECT
          {col_event_key}   AS signal_id,
          {col_symbol}      AS symbol,
          {col_tf}          AS tf,
          {col_ts}          AS open_time,
          {col_signal_type} AS strategy,
          {col_direction}   AS direction,
          {col_entry}       AS entry,
          {col_sl}          AS stop_loss,
          {col_tp1}         AS take_profit_1,
          {col_tp2}         AS take_profit_2,
          {col_score}       AS score,
          {col_payload}     AS reason_json
        FROM {t_signals}
        WHERE {col_symbol}=%s AND {col_tf}=%s AND {col_ts} <= %s
        ORDER BY {col_ts} DESC
        LIMIT 1;
        """
        cur.execute(sql2, (symbol, tf, open_time))
        row2 = cur.fetchone()
        return dict(row2) if row2 else None





def report_backfill_current(args) -> None:
    """
    Create (or upsert) the pre_rag/pre report for the latest indicators candle for a given symbol/tf.
    Intended to keep the web dashboard truly "current".
    """
    cfg = load_config(args.config)
    dsn = build_dsn(cfg)

    ollama_cfg = cfg.get("ollama", {})
    host = ollama_cfg.get("host", "http://localhost:11434")
    llm_model = ollama_cfg.get("llm_model", "qwen2.5:7b-instruct")
    embed_model = ollama_cfg.get("embed_model", "bge-m3")

    llm_cfg = cfg.get("llm_reports", {})
    prompt_version = llm_cfg.get("prompt_version", "v1")
    temperature = float(llm_cfg.get("temperature", 0.2))

    table_cfg = llm_cfg.get("tables", {})
    if not table_cfg:
        raise RuntimeError("config.yaml missing llm_reports.tables")

    symbol = args.symbol.strip().upper()
    tf = args.tf.strip()

    # Determine stage
    default_stage = "pre_rag" if args.mode == "rag" else "pre"
    stage = (args.stage or "").strip() or default_stage

    conn = pg_connect(dsn)
    session = requests.Session()
    try:
        # 1) latest indicators candle
        ot = fetch_latest_open_time_from_indicators(conn, table_cfg, symbol, tf)
        if not ot:
            raise RuntimeError(f"indicators 최신 open_time을 찾지 못했습니다: {symbol} {tf}")

        # 2) get signal row for that candle (exact, else <=)
        ev = fetch_signal_by_candle(conn, table_cfg, symbol, tf, ot)
        if not ev:
            raise RuntimeError(f"signals에서 해당 캔들(또는 이전) 데이터를 찾지 못했습니다: {symbol} {tf} open_time={ot}")

        # Ensure candle key fields align with indicators if desired
        if args.force_open_time:
            ev["open_time"] = ot
        ev["symbol"] = symbol
        ev["tf"] = tf

        # 3) (optional) RAG memory snippets
        memory_snippets: List[str] = []
        if not args.no_rag:
            emb_cfg = llm_cfg.get("embedding", {})
            topk = int(emb_cfg.get("topk", 5))
            faiss_path = emb_cfg.get("faiss_path", "./faiss_reports.index")
            if bool(emb_cfg.get("enabled", False)) and topk > 0 and os.path.exists(faiss_path):
                try:
                    qtext = build_query_embed_text(ev)
                    qemb = np.array(
                        ollama_embed(session, host, embed_model, qtext, timeout=int(args.timeout_embed)),
                        dtype="float32",
                    )
                    embed_dim = int(qemb.shape[0])
                    fs = load_faiss(faiss_path, embed_dim)
                    if fs.index.ntotal > 0:
                        q = normalize(qemb).reshape(1, -1)
                        _D, I = fs.index.search(q, topk)
                        faiss_ids = [int(x) for x in I[0] if int(x) >= 0]
                        if faiss_ids:
                            rows = fetch_reports_by_faiss_ids(
                                conn,
                                faiss_ids,
                                embed_model,
                                exclude_signal_id=str(ev["signal_id"]),
                                outcomes_table=table_cfg.get("outcomes", "outcomes"),
                            )
                            memory_snippets = summarize_post_reports_for_prompt(rows, max_items=topk)
                except Exception as e:
                    print(f"[WARN] RAG retrieval failed -> continue without RAG. err={e}")

        # 4) Generate + upsert report (Korean-only + tag/symbol hygiene)
        prompt = build_pre_prompt(ev, memory_snippets)
        raw = ""
        signal_id = str(ev.get("signal_id"))
        strategy_tag = str(ev.get("strategy") or "").strip()
        try:
            rep, raw = generate_valid_report(
                session=session,
                host=host,
                model=llm_model,
                prompt=prompt,
                temperature=temperature,
                timeout=int(args.timeout_llm),
                symbol=symbol,
                tf=tf,
                strategy_tag=strategy_tag,
            )
            rid = upsert_llm_report(
                conn,
                signal_id,
                stage,
                llm_model,
                prompt_version,
                prompt,
                rep,
                True,
                None,
                symbol=symbol,
                tf=tf,
                open_time=ev.get("open_time"),
            )
            conn.commit()
            print(f"[OK] backfill-current saved stage={stage} report_id={rid} symbol={symbol} tf={tf} open_time={ev.get('open_time')}")
        except Exception as e:
            rid = upsert_llm_report(
                conn,
                signal_id,
                stage,
                llm_model,
                prompt_version,
                prompt,
                {"raw": raw},
                False,
                str(e),
                symbol=symbol,
                tf=tf,
                open_time=ev.get("open_time"),
            )
            conn.commit()
            print(f"[ERR] backfill-current failed report_id={rid}: {e}")

    finally:
        try:
            conn.close()
        except Exception:
            pass





# =============================================================================
# Helpers for "report backfill/daemon"
# =============================================================================
def fetch_latest_open_time_from_indicators(conn, table_cfg: Dict[str, Any], symbol: str, tf: str) -> Optional[_dt.datetime]:
    t_ind = table_cfg.get("indicators", "indicators")
    sql = f"""
    SELECT MAX(open_time) AS open_time
    FROM {t_ind}
    WHERE symbol=%s AND tf=%s;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (symbol, tf))
        row = cur.fetchone()
    return row["open_time"] if row else None


def fetch_signal_by_candle(conn, table_cfg: Dict[str, Any], symbol: str, tf: str, open_time: _dt.datetime) -> Optional[Dict[str, Any]]:
    """
    Try exact candle match first; if not found, fallback to latest <= open_time.
    NOTE: signals의 timestamp 컬럼명은 config.yaml의 signals_cols.ts 매핑을 따릅니다.
    """
    t_signals = table_cfg.get("signals", "signals")
    s = table_cfg.get("signals_cols", {}) or {}

    col_event_key = s.get("event_key", "event_key")
    col_symbol = s.get("symbol", "symbol")
    col_tf = s.get("timeframe", "tf")
    col_ts = s.get("ts", "open_time")
    col_signal_type = s.get("signal_type", "signal_type")
    col_direction = s.get("direction", "direction")
    col_entry = s.get("entry", "entry")
    col_sl = s.get("stop_loss", "stop_loss")
    col_tp1 = s.get("tp1", "take_profit_1")
    col_tp2 = s.get("tp2", "take_profit_2")
    col_score = s.get("score", "score")
    col_payload = s.get("payload", "reason_json")

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        sql1 = f"""
        SELECT
          {col_event_key}   AS signal_id,
          {col_symbol}      AS symbol,
          {col_tf}          AS tf,
          {col_ts}          AS open_time,
          {col_signal_type} AS strategy,
          {col_direction}   AS direction,
          {col_entry}       AS entry,
          {col_sl}          AS stop_loss,
          {col_tp1}         AS take_profit_1,
          {col_tp2}         AS take_profit_2,
          {col_score}       AS score,
          {col_payload}     AS reason_json
        FROM {t_signals}
        WHERE {col_symbol}=%s AND {col_tf}=%s AND {col_ts}=%s
        LIMIT 1;
        """
        cur.execute(sql1, (symbol, tf, open_time))
        row = cur.fetchone()
        if row:
            return dict(row)

        sql2 = f"""
        SELECT
          {col_event_key}   AS signal_id,
          {col_symbol}      AS symbol,
          {col_tf}          AS tf,
          {col_ts}          AS open_time,
          {col_signal_type} AS strategy,
          {col_direction}   AS direction,
          {col_entry}       AS entry,
          {col_sl}          AS stop_loss,
          {col_tp1}         AS take_profit_1,
          {col_tp2}         AS take_profit_2,
          {col_score}       AS score,
          {col_payload}     AS reason_json
        FROM {t_signals}
        WHERE {col_symbol}=%s AND {col_tf}=%s AND {col_ts} <= %s
        ORDER BY {col_ts} DESC
        LIMIT 1;
        """
        cur.execute(sql2, (symbol, tf, open_time))
        row2 = cur.fetchone()
        return dict(row2) if row2 else None


def exists_llm_report_for_candle(conn, symbol: str, tf: str, open_time: _dt.datetime, stage: str, model: str, prompt_version: str) -> bool:
    sql = """
    SELECT 1
    FROM llm_reports
    WHERE symbol=%s AND tf=%s AND open_time=%s
      AND stage=%s AND model=%s AND prompt_version=%s
      AND ok=TRUE
    LIMIT 1;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (symbol, tf, open_time, stage, model, prompt_version))
        return cur.fetchone() is not None





def report_backfill(args) -> None:
    """
    indicators 최신 open_time 기준으로 signals/LLM report를 생성(upsert).
    - 기본: pre_rag (mode=rag) / pre (mode=legacy)
    - 리포트가 이미 있으면 skip(기본). --force 로 항상 재생성 가능
    """
    cfg = load_config(args.config)
    dsn = build_dsn(cfg)

    ollama_cfg = cfg.get("ollama", {})
    host = ollama_cfg.get("host", "http://localhost:11434")
    llm_model = ollama_cfg.get("llm_model", "qwen2.5:7b-instruct")
    embed_model = ollama_cfg.get("embed_model", "bge-m3")

    llm_cfg = cfg.get("llm_reports", {})
    prompt_version = llm_cfg.get("prompt_version", "v1")
    temperature = float(llm_cfg.get("temperature", 0.2))

    table_cfg = llm_cfg.get("tables", {})
    if not table_cfg:
        raise RuntimeError("config.yaml missing llm_reports.tables")

    symbol = args.symbol.strip().upper()
    tf = args.tf.strip()

    default_stage = "pre_rag" if args.mode == "rag" else "pre"
    stage = (args.stage or "").strip() or default_stage

    conn = pg_connect(dsn)
    session = requests.Session()
    try:
        ot = fetch_latest_open_time_from_indicators(conn, table_cfg, symbol, tf)
        if not ot:
            raise RuntimeError(f"indicators 최신 open_time을 찾지 못했습니다: {symbol} {tf}")

        if (not args.force) and exists_llm_report_for_candle(conn, symbol, tf, ot, stage, llm_model, prompt_version):
            print(f"[OK] already exists -> skip: {symbol} {tf} open_time={ot} stage={stage}")
            return

        ev = fetch_signal_by_candle(conn, table_cfg, symbol, tf, ot)
        if not ev:
            raise RuntimeError(f"signals에서 해당 캔들(또는 이전) 데이터를 찾지 못했습니다: {symbol} {tf} open_time={ot}")

        ev["symbol"] = symbol
        ev["tf"] = tf
        if args.force_open_time:
            ev["open_time"] = ot

        memory_snippets: List[str] = []
        if not args.no_rag:
            emb_cfg = llm_cfg.get("embedding", {})
            topk = int(emb_cfg.get("topk", 5))
            faiss_path = emb_cfg.get("faiss_path", "./faiss_reports.index")
            if bool(emb_cfg.get("enabled", False)) and topk > 0 and os.path.exists(faiss_path):
                try:
                    qtext = build_query_embed_text(ev)
                    qemb = np.array(
                        ollama_embed(session, host, embed_model, qtext, timeout=int(args.timeout_embed)),
                        dtype="float32",
                    )
                    embed_dim = int(qemb.shape[0])
                    fs = load_faiss(faiss_path, embed_dim)
                    if fs.index.ntotal > 0:
                        q = normalize(qemb).reshape(1, -1)
                        _D, I = fs.index.search(q, topk)
                        faiss_ids = [int(x) for x in I[0] if int(x) >= 0]
                        if faiss_ids:
                            rows = fetch_reports_by_faiss_ids(
                                conn,
                                faiss_ids,
                                embed_model,
                                exclude_signal_id=str(ev["signal_id"]),
                                outcomes_table=table_cfg.get("outcomes", "outcomes"),
                            )
                            memory_snippets = summarize_post_reports_for_prompt(rows, max_items=topk)
                except Exception as e:
                    print(f"[WARN] RAG retrieval failed -> continue without RAG. err={e}")

        prompt = build_pre_prompt(ev, memory_snippets)
        raw = ""
        signal_id = str(ev.get("signal_id"))
        strategy_tag = str(ev.get("strategy") or "").strip()
        try:
            rep, raw = generate_valid_report(
                session=session,
                host=host,
                model=llm_model,
                prompt=prompt,
                temperature=temperature,
                timeout=int(args.timeout_llm),
                symbol=symbol,
                tf=tf,
                strategy_tag=strategy_tag,
            )
            rid = upsert_llm_report(
                conn,
                signal_id,
                stage,
                llm_model,
                prompt_version,
                prompt,
                rep,
                True,
                None,
                symbol=symbol,
                tf=tf,
                open_time=ev.get("open_time"),
            )
            conn.commit()
            print(f"[OK] backfill saved stage={stage} report_id={rid} symbol={symbol} tf={tf} open_time={ev.get('open_time')}")
        except Exception as e:
            rid = upsert_llm_report(
                conn,
                signal_id,
                stage,
                llm_model,
                prompt_version,
                prompt,
                {"raw": raw},
                False,
                str(e),
                symbol=symbol,
                tf=tf,
                open_time=ev.get("open_time"),
            )
            conn.commit()
            print(f"[ERR] backfill failed report_id={rid}: {e}")

    finally:
        try:
            conn.close()
        except Exception:
            pass





def report_daemon(args) -> None:
    """
    자동화(옵션 3): 주기적으로 indicators 최신 캔들을 확인하고,
    해당 캔들에 pre_rag/pre 리포트가 없으면 생성한다.
    """
    cfg = load_config(args.config)
    dsn = build_dsn(cfg)

    llm_cfg = cfg.get("llm_reports", {})
    prompt_version = llm_cfg.get("prompt_version", "v1")
    table_cfg = llm_cfg.get("tables", {})
    if not table_cfg:
        raise RuntimeError("config.yaml missing llm_reports.tables")

    ollama_cfg = cfg.get("ollama", {})
    llm_model = ollama_cfg.get("llm_model", "qwen2.5:7b-instruct")

    default_stage = "pre_rag" if args.mode == "rag" else "pre"
    stage = (args.stage or "").strip() or default_stage

    symbols = [s.strip().upper() for s in (args.symbols or "").split(",") if s.strip()]
    if not symbols:
        raise RuntimeError("--symbols 가 비어있습니다. 예: --symbols XMRUSDT,BTCUSDT")
    tf = args.tf.strip()

    print(f"[INFO] daemon start stage={stage} mode={args.mode} tf={tf} every={args.every_sec}s symbols={len(symbols)}")
    import time
    while True:
        try:
            conn = pg_connect(dsn)
            try:
                for sym in symbols:
                    ot = fetch_latest_open_time_from_indicators(conn, table_cfg, sym, tf)
                    if not ot:
                        continue

                    if exists_llm_report_for_candle(conn, sym, tf, ot, stage, llm_model, prompt_version):
                        continue

                    # missing -> create
                    class _A: pass
                    a = _A()
                    a.config = args.config
                    a.symbol = sym
                    a.tf = tf
                    a.mode = args.mode
                    a.stage = stage
                    a.no_rag = args.no_rag
                    a.force_open_time = True
                    a.timeout_llm = args.timeout_llm
                    a.timeout_embed = args.timeout_embed
                    a.force = True
                    report_backfill(a)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except KeyboardInterrupt:
            print("\n[INFO] daemon stopped by user")
            return
        except Exception as e:
            print(f"[WARN] daemon tick error: {e}")

        time.sleep(int(args.every_sec))



def main():
    # Backward compatibility:
    # - If first token is "report", use subcommand mode
    # - Else default to legacy run mode (same flags as before)
    argv = sys.argv[1:]
    if argv[:1] == ["report"]:
        parser = argparse.ArgumentParser()
        sp = parser.add_subparsers(dest="cmd", required=True)
        build_report_parser(sp)
        args = parser.parse_args(argv)
        args._handler(args)
        return

    # default = run (legacy)
    parser = argparse.ArgumentParser()
    build_run_parser(parser)
    args = parser.parse_args(argv)

    # ---- original run flow below (mostly unchanged) ----
    cfg = load_config(args.config)
    dsn = build_dsn(cfg)

    ollama_cfg = cfg.get("ollama", {})
    host = ollama_cfg.get("host", "http://localhost:11434")
    llm_model = ollama_cfg.get("llm_model", "qwen2.5:7b-instruct")
    embed_model = ollama_cfg.get("embed_model", "bge-m3")

    llm_cfg = cfg.get("llm_reports", {})
    prompt_version = llm_cfg.get("prompt_version", "v1")
    temperature = float(llm_cfg.get("temperature", 0.2))
    limit = args.limit if args.limit is not None else int(llm_cfg.get("limit", 50))

    table_cfg = llm_cfg.get("tables", {})
    if not table_cfg:
        raise RuntimeError("config.yaml missing llm_reports.tables")

    emb_cfg = llm_cfg.get("embedding", {})
    emb_enabled = bool(emb_cfg.get("enabled", False)) and (not args.no_embed)
    embed_text_mode = emb_cfg.get("embed_text", "post_only")  # pre_only|post_only|both
    faiss_path = emb_cfg.get("faiss_path", "./faiss_reports.index")
    topk = int(emb_cfg.get("topk", 5))

    # enforce post_only
    if embed_text_mode not in ("pre_only", "post_only", "both"):
        embed_text_mode = "post_only"
    if embed_text_mode != "post_only":
        embed_text_mode = "post_only"

    conn = pg_connect(dsn)
    stage_missing = "pre_rag" if args.mode == "rag" else "pre"

    force_last = int(args.force_last or 0)
    # --force: regenerate even if stage exists (overwrite by candle unique key).
    if args.force and force_last <= 0:
        force_last = max(int(limit or 50), 1)

    if force_last > 0:
        pending = fetch_recent_signals(conn, table_cfg, limit=force_last)
    else:
        pending = fetch_pending_signals(conn, table_cfg, limit=limit, stage_missing=stage_missing)

    if args.only_symbol:
        sym = args.only_symbol.strip().upper()
        pending = [ev for ev in pending if str(ev.get("symbol", "")).upper() == sym]

    if not pending:
        print(f"[OK] No pending signals (stage='{stage_missing}' reports already exist)." + (" (use --force or --force-last to regenerate)" if not args.force else ""))
        conn.close()
        return

    faiss_store = None
    embed_dim = None
    faiss_dirty = False
    embeds_written = 0

    session_main = requests.Session()

    print(
        f"[INFO] mode={args.mode} stage_missing={stage_missing} signals={len(pending)} "
        f"llm_model={llm_model} embed={emb_enabled} embed_model={embed_model} topk={topk} "
        f"force_last={force_last} force_stage={args.force_stage} only_symbol={args.only_symbol or '-'} "
        f"workers={args.workers} commit_every={args.commit_every}"
    )

    pre_stage = "pre_rag" if args.mode == "rag" else "pre"
    writes = 0

    def maybe_commit(force: bool = False):
        nonlocal writes
        if args.commit_every == 0:
            conn.commit()
            return
        if force or (writes > 0 and writes % int(args.commit_every) == 0):
            conn.commit()

    # ---- normal path (sequential). fast-path removed for clarity/robustness. ----
    for ev in pending:
        signal_id = str(ev["signal_id"])
        print(f"\n=== signal_id={signal_id} {ev.get('symbol')} {ev.get('tf')} {ev.get('strategy')} ===")

        memory_snippets: List[str] = []
        if emb_enabled and topk > 0:
            try:
                if os.path.exists(faiss_path):
                    qtext = build_query_embed_text(ev)
                    qemb = np.array(ollama_embed(session_main, host, embed_model, qtext, timeout=args.timeout_embed), dtype="float32")

                    if embed_dim is None:
                        embed_dim = int(qemb.shape[0])
                        faiss_store = load_faiss(faiss_path, embed_dim)
                        print(f"[OK] FAISS loaded dim={embed_dim} ntotal={faiss_store.index.ntotal}")

                    if int(qemb.shape[0]) == embed_dim and faiss_store.index.ntotal > 0:
                        q = normalize(qemb).reshape(1, -1)
                        _, I = faiss_store.index.search(q, topk)
                        faiss_ids = [int(x) for x in I[0] if int(x) >= 0]
                        if faiss_ids:
                            rows = fetch_reports_by_faiss_ids(
                                conn, faiss_ids, embed_model,
                                exclude_signal_id=signal_id,
                                outcomes_table=table_cfg.get("outcomes", "outcomes")
                            )
                            memory_snippets = summarize_post_reports_for_prompt(rows, max_items=topk)
                            if memory_snippets:
                                print(f"[OK] RAG hits(post)={len(memory_snippets)}")
                else:
                    print("[INFO] RAG: FAISS index not found yet -> skip")
            except Exception as e:
                print(f"[WARN] RAG retrieval failed -> skip. err={e}")

        # PRE
        do_pre_stage = True
        if force_last > 0 and args.force_stage == "post":
            do_pre_stage = False

        pre_report = None
        if do_pre_stage:
            pre_prompt = build_pre_prompt(ev, memory_snippets)
            raw = ""
            strategy_tag = str(ev.get("strategy") or "").strip()
            try:
                pre_report, raw = generate_valid_report(
                    session=session_main,
                    host=host,
                    model=llm_model,
                    prompt=pre_prompt,
                    temperature=temperature,
                    timeout=int(args.timeout_llm),
                    symbol=str(ev.get("symbol") or "").strip().upper(),
                    tf=str(ev.get("tf") or "").strip(),
                    strategy_tag=strategy_tag,
                )
                rid = upsert_llm_report(
                    conn, signal_id, pre_stage, llm_model, prompt_version, pre_prompt, pre_report, True, None,
                    symbol=ev.get("symbol"), tf=ev.get("tf"), open_time=ev.get("open_time")
                )
                writes += 1
                maybe_commit()
                print(f"[OK] {pre_stage} saved report_id={rid}")
            except Exception as e:
                rid = upsert_llm_report(
                    conn, signal_id, pre_stage, llm_model, prompt_version, pre_prompt, {"raw": raw}, False, str(e),
                    symbol=ev.get("symbol"), tf=ev.get("tf"), open_time=ev.get("open_time")
                )
                writes += 1
                maybe_commit()
                print(f"[ERR] {pre_stage} failed report_id={rid}: {e}")

        # POST
        if args.pre_only:
            continue

        do_post = True
        if force_last > 0 and args.force_stage == "pre":
            do_post = False

        post_id = None
        post_report = None

        if do_post:
            outcome = fetch_outcome(conn, table_cfg, signal_id)
            if outcome:
                if pre_report is None:
                    pre_report = {"headline": "", "bias": "neutral", "evidence": [], "levels": {}, "risks": [], "plan": [], "confidence": 0.5, "tags": []}

                post_prompt = build_post_prompt(ev, outcome, pre_report)
                raw2 = ""
                strategy_tag = str(ev.get("strategy") or "").strip()
                try:
                    post_report, raw2 = generate_valid_report(
                        session=session_main,
                        host=host,
                        model=llm_model,
                        prompt=post_prompt,
                        temperature=temperature,
                        timeout=int(args.timeout_llm),
                        symbol=str(ev.get("symbol") or "").strip().upper(),
                        tf=str(ev.get("tf") or "").strip(),
                        strategy_tag=strategy_tag,
                    )
                    post_id = upsert_llm_report(
                        conn, signal_id, "post", llm_model, prompt_version, post_prompt, post_report, True, None,
                        symbol=ev.get("symbol"), tf=ev.get("tf"), open_time=ev.get("open_time")
                    )
                    writes += 1
                    maybe_commit()
                    print(f"[OK] post saved report_id={post_id} result={outcome.get('result')} r={outcome.get('r_multiple')}")
                except Exception as e:
                    post_id = upsert_llm_report(
                        conn, signal_id, "post", llm_model, prompt_version, post_prompt, {"raw": raw2}, False, str(e),
                        symbol=ev.get("symbol"), tf=ev.get("tf"), open_time=ev.get("open_time")
                    )
                    writes += 1
                    maybe_commit()
                    print(f"[ERR] post failed report_id={post_id}: {e}")
            else:
                print("[INFO] outcome not found -> skip post")

        # EMBED + FAISS (post_only)
        if emb_enabled and embed_text_mode == "post_only" and post_id and post_report:
            ctx = ev.get("reason_json") or {}
            embed_payload = {
                "signal_id": signal_id,
                "stage": "post",
                "symbol": ev.get("symbol"),
                "tf": ev.get("tf"),
                "strategy": ev.get("strategy"),
                "direction": ev.get("direction"),
                "score": ev.get("score"),
                "headline": post_report.get("headline"),
                "bias": post_report.get("bias"),
                "evidence": (post_report.get("evidence") or [])[:5],
                "levels": post_report.get("levels") or {},
                "risks": (post_report.get("risks") or [])[:5],
                "plan": (post_report.get("plan") or [])[:5],
                "tags": (post_report.get("tags") or [])[:12],
                "ctx": {
                    "htf_1d": ctx.get("htf_1d"),
                    "htf_4h": ctx.get("htf_4h"),
                    "vwap_position": ctx.get("vwap_position"),
                    "ema_alignment": ctx.get("ema_alignment"),
                    "rsi_ok": ctx.get("rsi_ok"),
                }
            }
            embed_text = json.dumps(to_json_safe(embed_payload), ensure_ascii=False)

            try:
                emb = ollama_embed(session_main, host, embed_model, embed_text, timeout=args.timeout_embed)
                v = np.array(emb, dtype="float32")

                if embed_dim is None:
                    embed_dim = int(v.shape[0])
                    faiss_store = load_faiss(faiss_path, embed_dim)
                    print(f"[OK] FAISS ready dim={embed_dim} ntotal={faiss_store.index.ntotal}")

                if int(v.shape[0]) != embed_dim:
                    raise RuntimeError(f"Embedding dim mismatch got={v.shape[0]} expected={embed_dim}")

                v = normalize(v).reshape(1, -1)
                faiss_id = faiss_store.next_id
                faiss_store.index.add_with_ids(v, np.array([faiss_id], dtype="int64"))
                faiss_store.next_id += 1

                upsert_embedding_meta(conn, post_id, embed_model, embed_dim, faiss_id)
                writes += 1
                embeds_written += 1
                faiss_dirty = True

                maybe_commit()
                if args.save_faiss_every and int(args.save_faiss_every) > 0 and (embeds_written % int(args.save_faiss_every) == 0):
                    save_faiss(faiss_store, faiss_path)
                    faiss_dirty = False
                    print(f"[OK] FAISS saved (batch) embeds_written={embeds_written}")

                print(f"[OK] embedded stage=post report_id={post_id} faiss_id={faiss_id}")
            except Exception as e:
                print(f"[ERR] embed failed stage=post report_id={post_id}: {e}")

    conn.commit()
    if faiss_store is not None and faiss_dirty:
        save_faiss(faiss_store, faiss_path)
        print("[OK] FAISS saved (final)")

    conn.close()
    print("\n[Done]")


if __name__ == "__main__":
    main()
