# run_llm_reports.py
import os
import json
import argparse
import datetime as _dt
from typing import Any, Dict, List, Optional, Tuple

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


# -------------------------
# JSON-safe helpers
# -------------------------
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


# -------------------------
# LLM JSON schema
# -------------------------
class ReportJSON(BaseModel):
    headline: str
    bias: str = Field(..., description="bull | bear | neutral")
    evidence: List[str]
    levels: Dict[str, Any] = Field(default_factory=dict)
    risks: List[str] = Field(default_factory=list)
    plan: List[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    tags: List[str] = Field(default_factory=list)


# -------------------------
# Helpers
# -------------------------
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


# -------------------------
# Ollama client
# -------------------------
def ollama_generate(host: str, model: str, prompt: str, temperature: float = 0.2, timeout: int = 120) -> str:
    url = f"{host}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json().get("response", "")


def ollama_embed(host: str, model: str, text: str, timeout: int = 120) -> List[float]:
    url = f"{host}/api/embeddings"
    payload = {"model": model, "prompt": text}
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    emb = r.json().get("embedding")
    if not emb:
        raise RuntimeError("No embedding returned from Ollama")
    return emb


# -------------------------
# Prompt builders (schema-aligned + RAG injected)
# -------------------------
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

    return f"""
You are a trading analyst assistant.
Return ONLY valid JSON (no markdown, no commentary). 반드시 JSON만 출력.

JSON schema (keys must match exactly):
{json.dumps(schema, ensure_ascii=False)}

Signal (facts):
signal_id: {ev["signal_id"]}
symbol: {ev.get("symbol")}
tf: {ev.get("tf")}
open_time: {to_json_safe(ev.get("open_time"))}
strategy: {ev.get("strategy")}
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
- evidence/risks/plan/tags MUST be arrays of strings only. Do NOT use objects/dicts.
- If you want structured info, encode it into a single string like "Type: details".
""".strip()


def build_post_prompt(ev: Dict[str, Any], outcome: Dict[str, Any], pre_report: Dict[str, Any]) -> str:
    return f"""
You are a trading analyst assistant.
Return ONLY valid JSON (no markdown, no commentary). 반드시 JSON만 출력.

We are evaluating a past signal and its outcome.
Use the same JSON schema as the pre-report.

Signal (facts):
signal_id: {ev["signal_id"]}
symbol: {ev.get("symbol")}
tf: {ev.get("tf")}
open_time: {to_json_safe(ev.get("open_time"))}
strategy: {ev.get("strategy")}
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
- evidence/risks/plan/tags MUST be arrays of strings only. Do NOT use objects/dicts.
- If you want structured info, encode it into a single string like "Type: details".
""".strip()


# -------------------------
# FAISS store
# -------------------------
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


# -------------------------
# PG access
# -------------------------
def pg_connect(dsn: str):
    return psycopg2.connect(dsn)


def fetch_pending_signals(conn, table_cfg: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    t_signals = table_cfg["signals"]
    s = table_cfg["signals_cols"]

    # pre 리포트가 없는 signals만
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
     AND r.stage='pre'
     AND r.ok=TRUE
    WHERE r.id IS NULL
    ORDER BY s.{s["ts"]} DESC
    LIMIT %s;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def fetch_recent_signals(conn, table_cfg: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    """Force 테스트/재생성용: 최근 N개 signals를 가져옴 (pre 존재 여부 무시)"""
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


def upsert_llm_report(conn, signal_id: str, stage: str, model: str, prompt_version: str,
                      prompt: str, response_json: Dict[str, Any], ok: bool, error: Optional[str]) -> int:
    sql = """
    INSERT INTO llm_reports(event_key, stage, model, prompt_version, prompt, response_json, ok, error)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT(event_key, stage, model, prompt_version)
    DO UPDATE SET
      prompt = EXCLUDED.prompt,
      response_json = EXCLUDED.response_json,
      ok = EXCLUDED.ok,
      error = EXCLUDED.error
    RETURNING id;
    """
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (str(signal_id), stage, model, prompt_version, prompt, json.dumps(to_json_safe(response_json)), ok, error),
        )
        rid = cur.fetchone()[0]
    conn.commit()
    return rid


def upsert_embedding_meta(conn, report_id: int, embed_model: str, dim: int, faiss_id: int) -> None:
    sql = """
    INSERT INTO llm_report_embeddings(report_id, embed_model, dim, faiss_id)
    VALUES (%s,%s,%s,%s)
    ON CONFLICT(report_id, embed_model)
    DO UPDATE SET dim=EXCLUDED.dim, faiss_id=EXCLUDED.faiss_id;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (report_id, embed_model, dim, faiss_id))
    conn.commit()


def fetch_reports_by_faiss_ids(conn, faiss_ids: List[int], embed_model: str, exclude_signal_id: str,
                               outcomes_table: str = "outcomes") -> List[Dict[str, Any]]:
    """
    faiss_id -> post report rows (+ outcome result/r_multiple)
    """
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


# -------------------------
# RAG helpers
# -------------------------
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


# -------------------------
# Main
# -------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=str, default="config.yaml")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--pre-only", action="store_true")
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--force-last", type=int, default=0, help="Force run last N signals even if pre exists")
    ap.add_argument("--force-stage", type=str, default="pre", choices=["pre", "post", "both"],
                    help="Which stages to force when --force-last > 0")
    args = ap.parse_args()

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

    # enforce post_only in this project (safety)
    if embed_text_mode not in ("pre_only", "post_only", "both"):
        embed_text_mode = "post_only"

    conn = pg_connect(dsn)

    force_last = int(args.force_last or 0)
    if force_last > 0:
        pending = fetch_recent_signals(conn, table_cfg, limit=force_last)
    else:
        pending = fetch_pending_signals(conn, table_cfg, limit=limit)

    if not pending:
        print("[OK] No pending signals (pre reports already exist).")
        conn.close()
        return

    faiss_store = None
    embed_dim = None

    print(f"[INFO] signals={len(pending)} llm_model={llm_model} embed={emb_enabled} embed_model={embed_model} topk={topk} force_last={force_last} force_stage={args.force_stage}")

    for ev in pending:
        signal_id = str(ev["signal_id"])
        print(f"\n=== signal_id={signal_id} {ev.get('symbol')} {ev.get('tf')} {ev.get('strategy')} ===")

        # -------------------------
        # RAG retrieval (TopK post reports)
        # -------------------------
        memory_snippets: List[str] = []
        if emb_enabled and topk > 0:
            try:
                if os.path.exists(faiss_path):
                    qtext = build_query_embed_text(ev)
                    qemb = np.array(ollama_embed(host, embed_model, qtext), dtype="float32")

                    if embed_dim is None:
                        embed_dim = int(qemb.shape[0])
                        faiss_store = load_faiss(faiss_path, embed_dim)
                        print(f"[OK] FAISS loaded dim={embed_dim} ntotal={faiss_store.index.ntotal}")

                    if int(qemb.shape[0]) == embed_dim and faiss_store.index.ntotal > 0:
                        q = normalize(qemb).reshape(1, -1)
                        D, I = faiss_store.index.search(q, topk)
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
                            print("[INFO] RAG: no neighbors")
                    else:
                        print("[INFO] RAG: empty index or dim mismatch")
                else:
                    print("[INFO] RAG: FAISS index not found yet -> skip")
            except Exception as e:
                print(f"[WARN] RAG retrieval failed -> skip. err={e}")

        # -------------------------
        # PRE (force-last일 땐 항상 upsert로 업데이트됨)
        # -------------------------
        do_pre = True
        if force_last > 0 and args.force_stage == "post":
            do_pre = False

        pre_id = None
        pre_report = None
        if do_pre:
            pre_prompt = build_pre_prompt(ev, memory_snippets)
            raw = ""
            try:
                raw = ollama_generate(host, llm_model, pre_prompt, temperature=temperature)
                pre_obj = force_json(raw)
                pre_report = validate_report(pre_obj)
                pre_id = upsert_llm_report(conn, signal_id, "pre", llm_model, prompt_version, pre_prompt, pre_report, True, None)
                print(f"[OK] pre saved report_id={pre_id}")
            except (requests.RequestException, ValueError, json.JSONDecodeError, ValidationError) as e:
                pre_id = upsert_llm_report(conn, signal_id, "pre", llm_model, prompt_version, pre_prompt, {"raw": raw}, False, str(e))
                print(f"[ERR] pre failed report_id={pre_id}: {e}")

        # -------------------------
        # POST (only if outcome exists)
        # -------------------------
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
                # pre_report가 없으면(예: force-stage=post) 기존 pre를 DB에서 가져오지 않고도 동작하도록 빈값 허용
                if pre_report is None:
                    pre_report = {"headline": "", "bias": "neutral", "evidence": [], "levels": {}, "risks": [], "plan": [], "confidence": 0.5, "tags": []}

                post_prompt = build_post_prompt(ev, outcome, pre_report)
                raw2 = ""
                try:
                    raw2 = ollama_generate(host, llm_model, post_prompt, temperature=temperature)
                    post_obj = force_json(raw2)
                    post_report = validate_report(post_obj)
                    post_id = upsert_llm_report(conn, signal_id, "post", llm_model, prompt_version, post_prompt, post_report, True, None)
                    print(f"[OK] post saved report_id={post_id} result={outcome.get('result')} r={outcome.get('r_multiple')}")
                except (requests.RequestException, ValueError, json.JSONDecodeError, ValidationError) as e:
                    post_id = upsert_llm_report(conn, signal_id, "post", llm_model, prompt_version, post_prompt, {"raw": raw2}, False, str(e))
                    print(f"[ERR] post failed report_id={post_id}: {e}")
            else:
                print("[INFO] outcome not found -> skip post")

        # -------------------------
        # EMBED + FAISS (post_only 기본)
        # -------------------------
        if emb_enabled:
            targets: List[Tuple[str, int, Dict[str, Any]]] = []

            if embed_text_mode in ("pre_only", "both") and pre_id and pre_report:
                targets.append(("pre", pre_id, pre_report))

            if embed_text_mode in ("post_only", "both") and post_id and post_report:
                targets.append(("post", post_id, post_report))

            for stage, rid, rep in targets:
                ctx = ev.get("reason_json") or {}
                embed_payload = {
                    "signal_id": signal_id,
                    "stage": stage,
                    "symbol": ev.get("symbol"),
                    "tf": ev.get("tf"),
                    "strategy": ev.get("strategy"),
                    "direction": ev.get("direction"),
                    "score": ev.get("score"),
                    "headline": rep.get("headline"),
                    "bias": rep.get("bias"),
                    "evidence": (rep.get("evidence") or [])[:5],
                    "levels": rep.get("levels") or {},
                    "risks": (rep.get("risks") or [])[:5],
                    "plan": (rep.get("plan") or [])[:5],
                    "tags": (rep.get("tags") or [])[:12],
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
                    emb = ollama_embed(host, embed_model, embed_text)
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

                    upsert_embedding_meta(conn, rid, embed_model, embed_dim, faiss_id)
                    save_faiss(faiss_store, faiss_path)

                    print(f"[OK] embedded stage={stage} report_id={rid} faiss_id={faiss_id}")
                except Exception as e:
                    print(f"[ERR] embed failed stage={stage} report_id={rid}: {e}")

    conn.close()
    print("\n[Done]")


if __name__ == "__main__":
    main()
