# check_current_report_v2.py
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

import psycopg2.extras


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
