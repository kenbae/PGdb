# -*- coding: utf-8 -*-
"""
EMA20/50 Cross-only Scanner (Binance USDT-M) + Alignment Fresh Filter
+ OPTIONAL LLM JSON Report -> Telegram (telegram_notify.py)
+ TEST MODE (force-send) for pipeline verification

ALERT CONDITIONS (ONLY THESE):
- Golden Cross:
  - prev EMA20 <= prev EMA50
  - curr EMA20 > curr EMA50
  - AND current bullish alignment: EMA20 > EMA50 > EMA100

- Dead Cross:
  - prev EMA20 >= prev EMA50
  - curr EMA20 < curr EMA50
  - AND current bearish alignment: EMA20 < EMA50 < EMA100

Whipsaw Filter (default ON):
- Alignment must have turned TRUE within last N bars
- Controlled by --align-fresh-bars (default: 3)
- Disable with --align-fresh-bars 0

Observability (logs):
- --log-level [error|info|debug|trace]
- --show-skip-reasons
- Loop summary always printed at info+

LLM (optional):
- Enable with --llm-alert
- Uses local Ollama via trading_llm_report.generate_report()
- Sends via telegram_notify.send_report()
- Plain text telegram can be disabled with --no-plain-alert

TEST MODE (recommended):
- --test-llm-symbol BTC/USDT:USDT  (force one LLM report send using latest bars)
- --test-llm-direction bull|bear   (default bull)
- --test-plain                    (force one plain telegram send)
"""

import os, time, argparse
import re
import json
import yaml
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

import pandas as pd
import requests
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

try:
    import ccxt
except ImportError:
    raise SystemExit("ccxt가 설치되어 있지 않습니다.  pip install -U ccxt")

# PostgreSQL DB 저장용
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    print("[WARNING] psycopg2가 설치되지 않았습니다. DB 저장 기능이 비활성화됩니다.")
    print("          설치: pip install psycopg2-binary")

KST = ZoneInfo("Asia/Seoul")

# ============================================================
# Configuration
# ============================================================
def load_config(config_path: str = "config.yaml") -> dict:
    """config.yaml 파일 로드 (여러 인코딩 시도)"""
    encodings = ['utf-8', 'utf-8-sig', 'cp949', 'euc-kr', 'latin1']
    
    for encoding in encodings:
        try:
            with open(config_path, 'r', encoding=encoding) as f:
                config = yaml.safe_load(f)
            print(f"[INFO] config.yaml 로드 완료 (encoding: {encoding})")
            return config
        except (UnicodeDecodeError, UnicodeError):
            continue
        except FileNotFoundError:
            print(f"[ERROR] {config_path} 파일이 없습니다.")
            raise
        except Exception as e:
            print(f"[ERROR] config.yaml 로드 실패 ({encoding}): {e}")
            continue
    
    # 모든 인코딩 실패
    print(f"[ERROR] config.yaml을 읽을 수 없습니다. UTF-8로 다시 저장하세요.")
    raise ValueError("config.yaml 인코딩 문제")

# 전역 config
CONFIG = load_config()

# ============================================================
# Logging
# ============================================================
def now_kst_str() -> str:
    return datetime.now(tz=KST).strftime("%Y-%m-%d %H:%M:%S KST")

def log(level: str, msg: str, args=None):
    order = {"error": 0, "info": 1, "debug": 2, "trace": 3}
    if args is None:
        print(msg)
        return
    cur = order.get(getattr(args, "log_level", "info"), 1)
    want = order.get(level, 1)
    if want <= cur:
        print(f"[{level.upper()}] {now_kst_str()} | {msg}")

# ============================================================
# Telegram (plain)
# ============================================================
def send_telegram(text: str, token: Optional[str], chat_id: Optional[str], timeout: int = 10, args=None) -> bool:
    """
    NOTE: HTML/Markdown 파싱 문제 회피를 위해 기본은 'plain text'만 전송.
    """
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    try:
        r = requests.post(url, data=payload, timeout=timeout)
        ok = (r.status_code == 200)
        if (not ok) and args is not None and getattr(args, "log_level", "info") in ("debug","trace"):
            log("debug", f"Telegram response={r.status_code} body={r.text[:300]}", args)
        return ok
    except Exception as e:
        if args is not None and getattr(args, "log_level", "info") in ("debug","trace"):
            log("debug", f"Telegram exception: {type(e).__name__}: {e}", args)
        return False

# ============================================================
# PostgreSQL DB 저장
# ============================================================
def get_db_connection():
    """PostgreSQL 연결 (config.yaml의 db 섹션 사용)"""
    if not DB_AVAILABLE:
        return None
    
    try:
        db_config = CONFIG.get('db', {})
        
        db_host = db_config.get('host', 'localhost')
        db_port = db_config.get('port', 5432)
        db_name = db_config.get('name', 'marketdb')
        db_user = db_config.get('user', 'trader')
        db_password = db_config.get('password', '')
        
        # 비밀번호를 안전하게 인코딩
        if isinstance(db_password, str):
            try:
                # UTF-8 인코딩 가능한지 확인
                db_password.encode('utf-8')
            except UnicodeEncodeError:
                # 인코딩 실패 시 안전한 문자열로 변환
                db_password = db_password.encode('utf-8', errors='ignore').decode('utf-8')
        
        print(f"[DEBUG] DB 연결 시도: {db_user}@{db_host}:{db_port}/{db_name}")
        
        # 연결 생성 (DSN 방식으로 변경)
        if 'dsn' in CONFIG.get('pg', {}):
            # pg.dsn이 있으면 사용
            dsn = CONFIG['pg']['dsn']
            print(f"[DEBUG] DSN 사용")
            conn = psycopg2.connect(dsn, client_encoding='utf8')
        else:
            # 개별 파라미터 사용
            conn = psycopg2.connect(
                host=db_host,
                port=db_port,
                database=db_name,
                user=db_user,
                password=db_password,
                client_encoding='utf8'
            )
        
        print(f"[DEBUG] DB 연결 성공!")
        return conn
        
    except Exception as e:
        print(f"[ERROR] DB 연결 실패: {e}")
        print(f"[ERROR] config.yaml의 db 또는 pg.dsn 섹션을 확인하세요")
        print(f"[TIP] config.yaml을 UTF-8로 다시 저장해보세요")
        import traceback
        traceback.print_exc()
        return None


def save_signal_to_db(
    symbol: str,
    timeframe: str,
    direction: str,  # 'GOLDEN' or 'DEAD'
    signal_time: datetime,
    bar_open_time: datetime,
    ema_values: Dict[str, float],
    price_info: Dict[str, float],
    probabilities: Optional[Dict[str, Any]] = None,
    trade_plan: Optional[Dict[str, float]] = None,
    args=None
) -> bool:
    """
    EMA 신호를 DB에 저장
    
    Returns:
        True: 저장 성공
        False: 저장 실패 (중복 또는 에러)
    """
    if not DB_AVAILABLE:
        return False
    
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return False
        
        # 신호 ID 생성 (중복 방지)
        bar_ts = int(bar_open_time.timestamp())
        signal_id = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}_{direction}_{bar_ts}"
        
        with conn.cursor() as cur:
            # INSERT ... ON CONFLICT DO NOTHING (중복 방지)
            query = """
                INSERT INTO ema_signals (
                    signal_id, symbol, timeframe, direction,
                    signal_time, bar_open_time,
                    ema20, ema50, ema100, ema20_prev, ema50_prev,
                    close_price, atr, vwap,
                    prob_long, prob_short, prob_samples,
                    entry_price, stop_loss, take_profit_1, take_profit_2
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (signal_id) DO NOTHING
                RETURNING id
            """
            
            values = (
                signal_id,
                symbol,
                timeframe,
                direction,
                signal_time,
                bar_open_time,
                ema_values.get("ema20"),
                ema_values.get("ema50"),
                ema_values.get("ema100"),
                ema_values.get("ema20_prev"),
                ema_values.get("ema50_prev"),
                price_info.get("close"),
                price_info.get("atr"),
                price_info.get("vwap"),
                probabilities.get("p_long") if probabilities else None,
                probabilities.get("p_short") if probabilities else None,
                probabilities.get("samples") if probabilities else None,
                trade_plan.get("entry") if trade_plan else None,
                trade_plan.get("sl") if trade_plan else None,
                trade_plan.get("tp1") if trade_plan else None,
                trade_plan.get("tp2") if trade_plan else None,
            )
            
            cur.execute(query, values)
            result = cur.fetchone()
            
            conn.commit()
            
            # 중복이면 None 반환, 새로 삽입되면 ID 반환
            if result is not None:
                log("debug", f"{symbol} 신호 DB 저장 완료: {signal_id}", args)
                return True
            else:
                log("debug", f"{symbol} 신호 중복, DB 저장 스킵", args)
                return False
        
    except Exception as e:
        log("error", f"DB 저장 실패: {type(e).__name__}: {e}", args)
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return False
    
    finally:
        # CRITICAL: 연결을 반드시 닫음
        if conn:
            try:
                conn.close()
            except:
                pass


def save_scanner_status(
    timeframe: str,
    scan_time: datetime,
    stats: Dict[str, int],
    scan_duration: float,
    args=None
) -> bool:
    """
    스캐너 실행 상태를 DB에 저장
    
    Args:
        timeframe: 타임프레임
        scan_time: 스캔 시각
        stats: 통계 딕셔너리
        scan_duration: 스캔 소요 시간 (초)
    
    Returns:
        True: 저장 성공
        False: 저장 실패
    """
    if not DB_AVAILABLE:
        return False
    
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return False
        
        with conn.cursor() as cur:
            query = """
                INSERT INTO ema_scanner_status (
                    timeframe, scan_time,
                    total_symbols, signals_fired, cross_detected, no_cross,
                    fetch_failed, short_df, duplicates,
                    plain_sent, plain_failed,
                    llm_attempt, llm_sent, llm_skipped, llm_failed,
                    scan_duration_sec
                ) VALUES (
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s
                )
                RETURNING id
            """
            
            values = (
                timeframe,
                scan_time,
                stats.get('total', 0),
                stats.get('fired', 0),
                stats.get('cross', 0),
                stats.get('no_cross', 0),
                stats.get('fetch_fail', 0),
                stats.get('short_df', 0),
                stats.get('dup', 0),
                stats.get('plain_sent', 0),
                stats.get('plain_fail', 0),
                stats.get('llm_attempt', 0),
                stats.get('llm_sent', 0),
                stats.get('llm_skipped', 0),
                stats.get('llm_fail', 0),
                round(scan_duration, 2)
            )
            
            cur.execute(query, values)
            result = cur.fetchone()
            
            conn.commit()
            
            if result is not None:
                log("trace", f"스캐너 상태 DB 저장 완료", args)
                return True
            else:
                return False
        
    except Exception as e:
        log("error", f"스캐너 상태 DB 저장 실패: {type(e).__name__}: {e}", args)
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return False
    
    finally:
        # CRITICAL: 연결을 반드시 닫음
        if conn:
            try:
                conn.close()
            except:
                pass

# ============================================================
# Links
# ============================================================
def tv_link(symbol: str) -> str:
    base = symbol.replace("/", "").replace(":USDT", "")
    return f"https://www.tradingview.com/chart/?symbol=BINANCE:{base}.P"

def binance_futures_link(symbol: str) -> str:
    base = symbol.replace("/", "").replace(":USDT", "")
    return f"https://www.binance.com/en/futures/{base}"


# ============================================================
# Timeframe helpers
# ============================================================
def timeframe_to_seconds(tf: str) -> int:
    """Convert ccxt timeframe string like '30m','15m','1h','4h','1d' to seconds."""
    tf = (tf or "").strip().lower()
    m = re.match(r"^(\d+)([mhd])$", tf)
    if not m:
        return 0
    n = int(m.group(1))
    u = m.group(2)
    if u == "m":
        return n * 60
    if u == "h":
        return n * 3600
    if u == "d":
        return n * 86400
    return 0

def drop_incomplete_last_candle(df: pd.DataFrame, timeframe: str, grace_sec: int = 5, args=None) -> pd.DataFrame:
    """
    If the last row is the currently-forming candle, drop it so signals align with TradingView 'closed bar' logic.
    We decide 'incomplete' by comparing now(ms) with last candle open_ms + tf_seconds*1000.
    """
    if df is None or len(df) < 3:
        return df
    tf_sec = timeframe_to_seconds(timeframe)
    grace_sec = int(max(0, grace_sec))
    if tf_sec <= 0:
        return df
    try:
        last_open_ms = int(df["ts_ms"].iloc[-1])
    except Exception:
        return df
    now_ms = int(datetime.now(tz=KST).timestamp() * 1000)
    if now_ms < last_open_ms + tf_sec * 1000:
        return df.iloc[:-1].reset_index(drop=True)
    return df

# ============================================================
# Indicators
# ============================================================
def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()

def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(span=length, adjust=False).mean()

def vwap_rolling(df: pd.DataFrame, window: int = 50) -> float:
    w = min(window, len(df))
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].clip(lower=0)
    tpw = (tp.iloc[-w:] * vol.iloc[-w:]).sum()
    vw = vol.iloc[-w:].sum()
    if vw == 0:
        return float(df["close"].iloc[-1])
    return float(tpw / vw)

def ema_snapshot(df: pd.DataFrame) -> Optional[Dict[str, float]]:
    if df is None or len(df) < 120:
        return None
    close = df["close"]
    e20 = ema(close, 20)
    e50 = ema(close, 50)
    e100 = ema(close, 100)
    return {
        "ema20_prev": float(e20.iloc[-2]),
        "ema50_prev": float(e50.iloc[-2]),
        "ema20": float(e20.iloc[-1]),
        "ema50": float(e50.iloc[-1]),
        "ema100": float(e100.iloc[-1]),
    }

def cross_conditions(m: Dict[str, float]) -> Tuple[bool, bool]:
    golden = (
        (m["ema20_prev"] <= m["ema50_prev"])
        and (m["ema20"] > m["ema50"])
        and (m["ema20"] > m["ema50"] > m["ema100"])
    )
    dead = (
        (m["ema20_prev"] >= m["ema50_prev"])
        and (m["ema20"] < m["ema50"])
        and (m["ema20"] < m["ema50"] < m["ema100"])
    )
    return golden, dead

def alignment_fresh(df: pd.DataFrame, kind: str, fresh_bars: int) -> bool:
    if fresh_bars <= 0:
        return True
    close = df["close"]
    e20 = ema(close, 20)
    e50 = ema(close, 50)
    e100 = ema(close, 100)
    if kind == "bull":
        align = (e20 > e50) & (e50 > e100)
    else:
        align = (e20 < e50) & (e50 < e100)
    win = align.iloc[-(fresh_bars + 1):].astype(bool).tolist()
    # "막 TRUE가 된" 케이스만 통과: 첫 값 False, 마지막 True
    return bool(win[-1]) and (bool(win[0]) is False)

# ============================================================
# Recent-cross + Probabilities (local backtest)
# ============================================================
def find_recent_cross(df: pd.DataFrame, lookback_bars: int = 10) -> Optional[Dict[str, Any]]:
    """Find the most recent EMA20/EMA50 cross within last N CLOSED bars (excluding the current last row already closed)."""
    if df is None or len(df) < 120 or lookback_bars <= 0:
        return None
    close = df["close"]
    e20 = ema(close, 20)
    e50 = ema(close, 50)

    n = min(lookback_bars, len(df) - 2)
    for k in range(1, n + 1):
        i = -k
        p = i - 1
        golden = (e20.iloc[p] <= e50.iloc[p]) and (e20.iloc[i] > e50.iloc[i])
        dead = (e20.iloc[p] >= e50.iloc[p]) and (e20.iloc[i] < e50.iloc[i])
        if golden:
            return {"type": "GOLDEN", "direction": "bull", "index": i, "ts_ms": int(df["ts_ms"].iloc[i])}
        if dead:
            return {"type": "DEAD", "direction": "bear", "index": i, "ts_ms": int(df["ts_ms"].iloc[i])}
    return None

def current_alignment_state(df: pd.DataFrame) -> str:
    """bull/bear/neutral by EMA20/50/100 at CURRENT closed bar."""
    close = df["close"]
    e20 = float(ema(close, 20).iloc[-1])
    e50 = float(ema(close, 50).iloc[-1])
    e100 = float(ema(close, 100).iloc[-1])
    if e20 > e50 > e100:
        return "bull"
    if e20 < e50 < e100:
        return "bear"
    return "neutral"

def _walkout_success(df: pd.DataFrame, i: int, horizon: int, tp_atr: float, sl_atr: float, side: str) -> Optional[bool]:
    """Simple path-based check: TP hit before SL within horizon. Returns None if insufficient future bars."""
    if i < 0:
        i = len(df) + i
    j_end = min(len(df) - 1, i + horizon)
    if j_end <= i:
        return None
    entry = float(df["close"].iloc[i])
    a = float(atr(df, 14).iloc[i])
    if not (a > 0):
        return None

    tp = entry + tp_atr * a if side == "long" else entry - tp_atr * a
    sl = entry - sl_atr * a if side == "long" else entry + sl_atr * a

    # walk forward to respect order
    for j in range(i + 1, j_end + 1):
        hi = float(df["high"].iloc[j])
        lo = float(df["low"].iloc[j])
        if side == "long":
            # SL first if both touch in same candle -> conservative
            if lo <= sl:
                return False
            if hi >= tp:
                return True
        else:
            if hi >= sl:
                return False
            if lo <= tp:
                return True
    return False

def compute_signal_probabilities(
    df: pd.DataFrame,
    signal_direction: str,  # bull|bear
    history_bars: int = 1500,
    horizon_bars: int = 10,
    tp_atr: float = 1.0,
    sl_atr: float = 1.0,
) -> Dict[str, Any]:
    """Local, on-the-fly backtest probability for the given signal type on this symbol/timeframe."""
    out = {"p_long": None, "p_short": None, "samples": 0, "method": "local_backtest"}
    if df is None or len(df) < 200:
        return out

    # Restrict history window
    d = df.iloc[-min(history_bars, len(df)):].copy().reset_index(drop=True)

    close = d["close"]
    e20 = ema(close, 20)
    e50 = ema(close, 50)

    # Find all crosses in history window
    crosses = []
    for i in range(2, len(d) - horizon_bars - 1):
        golden = (e20.iloc[i-1] <= e50.iloc[i-1]) and (e20.iloc[i] > e50.iloc[i])
        dead   = (e20.iloc[i-1] >= e50.iloc[i-1]) and (e20.iloc[i] < e50.iloc[i])
        if golden:
            crosses.append(("bull", i))
        elif dead:
            crosses.append(("bear", i))

    # Filter to same type as current signal
    target = [i for (dirn, i) in crosses if dirn == signal_direction]
    if len(target) < 5:
        out["samples"] = len(target)
        return out

    wins = 0
    for i in target:
        res = _walkout_success(d, i, horizon_bars, tp_atr, sl_atr, "long" if signal_direction == "bull" else "short")
        if res is None:
            continue
        if res:
            wins += 1

    samples = len(target)
    p = int(round(100.0 * (wins / samples))) if samples > 0 else None

    if signal_direction == "bull":
        out["p_long"] = p
        out["p_short"] = 100 - p if p is not None else None
    else:
        out["p_short"] = p
        out["p_long"] = 100 - p if p is not None else None

    out["samples"] = samples
    out["horizon_bars"] = horizon_bars
    out["tp_atr"] = tp_atr
    out["sl_atr"] = sl_atr
    return out

def build_trade_plan_from_atr(df: pd.DataFrame, direction: str, rr2: float = 2.0, sl_atr: float = 1.0) -> Dict[str, float]:
    """Compute Entry/SL/TP1/TP2 from current close and ATR14."""
    close = float(df["close"].iloc[-1])
    a = float(atr(df, 14).iloc[-1])
    if not (a > 0):
        a = max(abs(close) * 0.005, 1e-9)
    if direction == "bull":
        entry = close
        sl = close - sl_atr * a
        tp1 = close + 1.0 * a
        tp2 = close + rr2 * a
    else:
        entry = close
        sl = close + sl_atr * a
        tp1 = close - 1.0 * a
        tp2 = close - rr2 * a
    return {"entry": round(entry, 4), "sl": round(sl, 4), "tp1": round(tp1, 4), "tp2": round(tp2, 4), "atr14": round(a, 4)}

# ============================================================
# Exchange / data
# ============================================================
def make_exchange(api_key: Optional[str], api_secret: Optional[str]):
    return ccxt.binance({
        "apiKey": api_key or "",
        "secret": api_secret or "",
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })

def fetch_top_symbols(ex, top_n: int) -> List[str]:
    tickers = ex.fetch_tickers()
    rows = []
    for sym, t in tickers.items():
        if sym.endswith(":USDT") and t.get("quoteVolume"):
            rows.append((sym, float(t["quoteVolume"])))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in rows[:top_n]]

def fetch_df(ex, symbol: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
    try:
        ohlcv = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["ts","open","high","low","close","volume"])

        # ts in ccxt is candle OPEN time (ms, UTC). Keep raw ms as stable key.
        df["ts_ms"] = pd.to_numeric(df["ts"], errors="coerce").astype("Int64")
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert(KST)

        for c in ["open","high","low","close","volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna().reset_index(drop=True)
        return df
    except Exception:
        return None

# ============================================================
# Optional LLM integration
# ============================================================

# 금지어/밈 자동 차단: LLM이 규칙을 무시해도 최종 송신은 정돈됨
BANNED_WORDS = [
    "금수저", "은수저", "떡상", "개미털기", "문샷", "펌핑", "덤핑", "설거지", "존버", "풀매수", "풀매도"
]
def sanitize_text(text: str) -> str:
    if not text:
        return text
    out = text
    for w in BANNED_WORDS:
        out = out.replace(w, "[금지어]")
    return out



def sanitize_any(obj):
    """Recursively sanitize banned words in str fields inside dict/list."""
    if obj is None:
        return obj
    if isinstance(obj, str):
        return sanitize_text(obj)
    if isinstance(obj, dict):
        return {k: sanitize_any(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_any(x) for x in obj]
    return obj
def llm_available() -> bool:
    try:
        from trading_llm_report import generate_report  # noqa
        from telegram_notify import send_report          # noqa
        return True
    except Exception:
        return False

def make_event_id(exchange: str, symbol: str, timeframe: str, signal: str, candle_open_ms: int) -> str:
    return f"{exchange}:{symbol}:{timeframe}:{signal}:{candle_open_ms}"

def mtf_bias_keys(ex, symbol: str, args) -> Dict[str, Dict[str, str]]:
    out = {
        "1D": {"bias": "neutral", "key": "na"},
        "4H": {"bias": "neutral", "key": "na"},
        "30m": {"bias": "neutral", "key": "na"},
    }

    df1d = fetch_df(ex, symbol, "1d", 260)
    if df1d is not None and len(df1d) >= 210:
        e200 = ema(df1d["close"], 200).iloc[-1]
        c = float(df1d["close"].iloc[-1])
        out["1D"] = {"bias": "bull" if c > e200 else "bear", "key": "above_ema200" if c > e200 else "below_ema200"}
    else:
        log("debug", f"{symbol} MTF 1D skip (no data)", args)

    df4h = fetch_df(ex, symbol, "4h", 220)
    if df4h is not None and len(df4h) >= 120:
        e20 = ema(df4h["close"], 20).iloc[-1]
        e50 = ema(df4h["close"], 50).iloc[-1]
        if e20 > e50:
            out["4H"] = {"bias": "bull", "key": "ema20>ema50"}
        elif e20 < e50:
            out["4H"] = {"bias": "bear", "key": "ema20<ema50"}
        else:
            out["4H"] = {"bias": "neutral", "key": "ema20=ema50"}
    else:
        log("debug", f"{symbol} MTF 4H skip (no data)", args)

    return out

def build_event_for_cross(
    ex,
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    m: Dict[str, float],
    direction: str,  # bull|bear
    args,
    cross_ts_ms: Optional[int] = None,
    bars_since_cross: int = 0,
) -> Dict[str, Any]:
    exchange = "BINANCE"
    signal = "EMA20_50_GOLDEN_CROSS" if direction == "bull" else "EMA20_50_DEAD_CROSS"

    close = float(df["close"].iloc[-1])
    high = float(df["high"].iloc[-1])
    low  = float(df["low"].iloc[-1])
    analysis_open_ms = int(df["ts_ms"].iloc[-1])
    candle_open_ms = int(cross_ts_ms) if cross_ts_ms else analysis_open_ms
    candle_open_ts = int(candle_open_ms // 1000)

    atr14 = float(atr(df, 14).iloc[-1])
    vwap = float(vwap_rolling(df, window=50))

    # Conservative levels from recent range
    n1 = min(60, len(df))
    n2 = min(20, len(df))
    r1 = float(df["high"].iloc[-n2:].max())
    r2 = float(df["high"].iloc[-n1:].max())
    s1 = float(df["low"].iloc[-n2:].min())
    s2 = float(df["low"].iloc[-n1:].min())

    resistance = sorted(list({round(r1, 1), round(r2, 1)}))
    support = sorted(list({round(s1, 1), round(s2, 1)}))

    mtf = mtf_bias_keys(ex, symbol, args)
    mtf["30m"] = {"bias": "bull" if direction == "bull" else "bear", "key": "cross"}

    event_type = "GOLDEN_CROSS" if direction == "bull" else "DEAD_CROSS"

    # trade plan + local probabilities
    trade_plan = build_trade_plan_from_atr(df, direction, rr2=getattr(args, 'tp2_rr', 2.0), sl_atr=getattr(args, 'sl_atr', 1.0))
    probs = compute_signal_probabilities(
        df,
        direction,
        history_bars=getattr(args, 'prob_history_bars', 1500),
        horizon_bars=getattr(args, 'prob_horizon_bars', 10),
        tp_atr=getattr(args, 'prob_tp_atr', 1.0),
        sl_atr=getattr(args, 'prob_sl_atr', 1.0),
    )

    event = {
        "event_id": make_event_id(exchange, symbol, timeframe, signal, candle_open_ms),

        # standard fields
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "event_type": event_type,
        "ts": candle_open_ts,
        "candle_open_ms": candle_open_ms,
        "tradingview_url": tv_link(symbol),
        "exchange_url": binance_futures_link(symbol),

        "price_value": close,
        "ema_fast": float(m["ema20"]),
        "ema_slow": float(m["ema50"]),

        # structured payload
        "price": {"close": close, "high": high, "low": low},
        "levels": {
            "resistance": resistance,
            "support": support,
            "vwap": round(vwap, 2),
            "ema_cluster": [round(m["ema20"], 2), round(m["ema50"], 2)]
        },
        "structure": {"swing": {"last_swing_high": resistance[-1], "last_swing_low": support[0]}},
        "indicators": {"ema20": round(m["ema20"], 2), "ema50": round(m["ema50"], 2), "ema100": round(m["ema100"], 2), "atr14": round(atr14, 2)},
        "mtf": mtf,
        "analysis": {
            "analysis_open_ms": analysis_open_ms,
            "bars_since_cross": int(bars_since_cross),
            "trade_plan": trade_plan,
            "probabilities": probs,
        },
        "constraints": {
            "report_language": "ko",
            "candle_closed": True,
            "note": "마감봉 기준(확정봉)으로 생성된 신호",
            "style": "research_note",
            "forbid_slang": True,
            "banned_words": BANNED_WORDS,
            "no_hype": True
        },
        "signals": [{"type": signal, "direction": direction}],
    }
    return event

def fire_llm_alert(event: Dict[str, Any], cooldown_sec: int, args) -> bool:
    """
    방탄 처리:
    - generate_report 실패해도 스캐너 계속
    - send_report 실패해도 스캐너 계속
    - 금지어 후처리 적용
    """
    try:
        from trading_llm_report import generate_report
        from telegram_notify import send_report
    except Exception as e:
        log("error", f"LLM import 실패: {type(e).__name__}: {e}", args)
        return False

    # Auto map creds for telegram_notify.py if it expects TG_*
    if os.getenv("TELEGRAM_BOT_TOKEN") and not os.getenv("TG_BOT_TOKEN"):
        os.environ["TG_BOT_TOKEN"] = os.getenv("TELEGRAM_BOT_TOKEN")
    if os.getenv("TELEGRAM_CHAT_ID") and not os.getenv("TG_CHAT_ID"):
        os.environ["TG_CHAT_ID"] = os.getenv("TELEGRAM_CHAT_ID")

    event_id = event.get("event_id", "NO_EVENT_ID")

    # 1) LLM report generation
    try:
        t0 = time.time()
        log("debug", f"LLM generate_report start | {event_id}", args)
        report = generate_report(event)
        # ✅ Sanitize banned words inside report (dict/list/str)
        report = sanitize_any(report)
        # Attach meta for telegram formatter (does not affect LLM schema)
        try:
            meta = (event.get("analysis") or {})
            probs = (meta.get("probabilities") or {})
            report["_meta"] = {
                "p_long": probs.get("p_long"),
                "p_short": probs.get("p_short"),
                "samples": probs.get("samples"),
                "trade_plan": meta.get("trade_plan"),
                "bars_since_cross": meta.get("bars_since_cross"),
            }
        except Exception:
            pass
        log("debug", f"LLM generate_report done  | {event_id} | {time.time()-t0:.2f}s", args)
    except Exception as e:
        log("error", f"LLM generate_report ERROR | {event_id} | {type(e).__name__}: {e}", args)
        return False

    # 2) Send report
    try:
        t1 = time.time()
        log("debug", f"LLM send_report start     | {event_id} | cooldown={cooldown_sec}s", args)
        sent = send_report(report, event_id=event_id, cooldown_sec=cooldown_sec)
        log("debug", f"LLM send_report done      | {event_id} | sent={sent} | {time.time()-t1:.2f}s", args)
        return bool(sent)
    except Exception as e:
        log("error", f"LLM send_report ERROR | {event_id} | {type(e).__name__}: {e}", args)
        return False

# ============================================================
# Scanner core
# ============================================================
def scan_once(ex, symbols, timeframe, lookback, state, tg_token, tg_chat, fresh_bars, args):
    # 스캔 시작 시간 기록
    scan_start_time = time.time()
    scan_timestamp = datetime.now(tz=KST)
    
    total = len(symbols)

    # Stats
    fired = 0
    cnt_fetch_fail = 0
    cnt_short_df = 0
    cnt_no_cross = 0
    cnt_cross = 0
    cnt_dup = 0

    cnt_plain_sent = 0
    cnt_plain_fail = 0

    cnt_llm_attempt = 0
    cnt_llm_sent = 0
    cnt_llm_skipped = 0
    cnt_llm_fail = 0

    log("info", f"SCAN START | symbols={total} tf={timeframe} lookback={lookback} fresh_bars={fresh_bars} "
                f"plain={'OFF' if args.no_plain_alert else 'ON'} llm={'ON' if args.llm_alert else 'OFF'}", args)

    for i, sym in enumerate(symbols, 1):
        log("trace", f"[{i}/{total}] fetch {sym}", args)

        df = fetch_df(ex, sym, timeframe, max(lookback, 180))
        df = drop_incomplete_last_candle(df, timeframe, grace_sec=5, args=args)
        if df is None:
            cnt_fetch_fail += 1
            if args.show_skip_reasons:
                log("debug", f"{sym} skip: fetch_df failed", args)
            continue
        if len(df) < 120:
            cnt_short_df += 1
            if args.show_skip_reasons:
                log("debug", f"{sym} skip: insufficient bars ({len(df)})", args)
            continue

        m = ema_snapshot(df)
        if not m:
            cnt_short_df += 1
            if args.show_skip_reasons:
                log("debug", f"{sym} skip: ema_snapshot not ready", args)
            continue

        golden, dead = cross_conditions(m)

        if golden and not alignment_fresh(df, "bull", fresh_bars):
            if args.show_skip_reasons:
                log("debug", f"{sym} golden rejected: alignment not fresh", args)
            golden = False
        if dead and not alignment_fresh(df, "bear", fresh_bars):
            if args.show_skip_reasons:
                log("debug", f"{sym} dead rejected: alignment not fresh", args)
            dead = False

        if not (golden or dead):
            # Optional: if a cross happened recently, analyze at current time using that cross as context
            rc = find_recent_cross(df, lookback_bars=getattr(args, "recent_cross_bars", 0))
            if not rc:
                cnt_no_cross += 1
                log("trace", f"{sym} no signal | EMA20={m['ema20']:.3f} EMA50={m['ema50']:.3f} EMA100={m['ema100']:.3f}", args)
                continue

            # Require current alignment not opposite of the recent cross (logic correction)
            align_now = current_alignment_state(df)
            if rc["direction"] == "bull" and align_now == "bear":
                cnt_no_cross += 1
                if args.show_skip_reasons:
                    log("debug", f"{sym} recent GOLDEN ignored: current alignment is bear (cross failed)", args)
                continue
            if rc["direction"] == "bear" and align_now == "bull":
                cnt_no_cross += 1
                if args.show_skip_reasons:
                    log("debug", f"{sym} recent DEAD ignored: current alignment is bull (cross failed)", args)
                continue

            # Activate as contextual signal
            golden = (rc["direction"] == "bull")
            dead = (rc["direction"] == "bear")
            recent_cross_ts_ms = int(rc["ts_ms"])
            recent_bars_since = abs(int(rc["index"]))
        else:
            recent_cross_ts_ms = int(df["ts_ms"].iloc[-1])
            recent_bars_since = 0

        cnt_cross += 1

        # bar_id를 문자열 대신 ts_ms 기반 정수로 사용
        bar_open_ms = int(recent_cross_ts_ms)
        bar_time = df["ts"].iloc[-1].strftime("%Y-%m-%d %H:%M:%S KST")

        # ---------- GOLDEN ----------
        if golden:
            key = f"{sym}|{timeframe}|GOLDEN"
            if state.get(key) == bar_open_ms:
                cnt_dup += 1
                if args.show_skip_reasons:
                    log("debug", f"{sym} GOLDEN duplicate in same bar -> skip", args)
            else:
                log("info", f"{sym} GOLDEN fired | bar={bar_time}", args)

                # === DB 저장 ===
                try:
                    # 트레이드 플랜 계산
                    trade_plan = build_trade_plan_from_atr(
                        df, "bull", 
                        rr2=getattr(args, 'tp2_rr', 2.0), 
                        sl_atr=getattr(args, 'sl_atr', 1.0)
                    )
                    
                    # 백테스트 확률 계산
                    probs = compute_signal_probabilities(
                        df, "bull",
                        history_bars=getattr(args, 'prob_history_bars', 1500),
                        horizon_bars=getattr(args, 'prob_horizon_bars', 10),
                        tp_atr=getattr(args, 'prob_tp_atr', 1.0),
                        sl_atr=getattr(args, 'prob_sl_atr', 1.0),
                    )
                    
                    # 가격 정보
                    close = float(df["close"].iloc[-1])
                    atr14 = float(atr(df, 14).iloc[-1])
                    vwap_val = float(vwap_rolling(df, window=50))
                    
                    save_signal_to_db(
                        symbol=sym,
                        timeframe=timeframe,
                        direction='GOLDEN',
                        signal_time=datetime.now(tz=KST),
                        bar_open_time=datetime.fromtimestamp(bar_open_ms / 1000, tz=KST),
                        ema_values={
                            "ema20": m['ema20'],
                            "ema50": m['ema50'],
                            "ema100": m['ema100'],
                            "ema20_prev": m['ema20_prev'],
                            "ema50_prev": m['ema50_prev']
                        },
                        price_info={
                            "close": close,
                            "atr": atr14,
                            "vwap": vwap_val
                        },
                        probabilities=probs,
                        trade_plan=trade_plan,
                        args=args
                    )
                except Exception as e:
                    log("error", f"{sym} DB 저장 실패: {type(e).__name__}: {e}", args)
                # === DB 저장 끝 ===

                if not args.no_plain_alert:
                    msg = "\n".join([
                        "[EMA] EMA20/50 GOLDEN CROSS",
                        f"Symbol: {sym}",
                        f"TF: {timeframe}",
                        f"Time: {bar_time}",
                        f"EMA20/50/100: {m['ema20']:.6g} / {m['ema50']:.6g} / {m['ema100']:.6g}",
                        tv_link(sym),
                        binance_futures_link(sym)
                    ])
                    ok = send_telegram(msg, tg_token, tg_chat, args=args)
                    if ok:
                        cnt_plain_sent += 1
                        log("debug", f"{sym} plain telegram SENT", args)
                    else:
                        cnt_plain_fail += 1
                        log("error", f"{sym} plain telegram FAILED", args)

                if args.llm_alert:
                    cnt_llm_attempt += 1
                    try:
                        event = build_event_for_cross(ex, sym, timeframe, df, m, "bull", args, cross_ts_ms=recent_cross_ts_ms, bars_since_cross=recent_bars_since)
                        sent = fire_llm_alert(event, cooldown_sec=args.llm_cooldown_sec, args=args)
                        if sent:
                            cnt_llm_sent += 1
                            log("info", f"{sym} LLM SENT | {event.get('event_id')}", args)
                        else:
                            cnt_llm_skipped += 1
                            log("info", f"{sym} LLM SKIPPED (dedup/cooldown or failure) | {event.get('event_id')}", args)
                    except Exception as e:
                        cnt_llm_fail += 1
                        log("error", f"{sym} LLM ERROR | {type(e).__name__}: {e}", args)

                state[key] = bar_open_ms
                fired += 1

        # ---------- DEAD ----------
        if dead:
            key = f"{sym}|{timeframe}|DEAD"
            if state.get(key) == bar_open_ms:
                cnt_dup += 1
                if args.show_skip_reasons:
                    log("debug", f"{sym} DEAD duplicate in same bar -> skip", args)
            else:
                log("info", f"{sym} DEAD fired | bar={bar_time}", args)

                # === DB 저장 ===
                try:
                    # 트레이드 플랜 계산
                    trade_plan = build_trade_plan_from_atr(
                        df, "bear", 
                        rr2=getattr(args, 'tp2_rr', 2.0), 
                        sl_atr=getattr(args, 'sl_atr', 1.0)
                    )
                    
                    # 백테스트 확률 계산
                    probs = compute_signal_probabilities(
                        df, "bear",
                        history_bars=getattr(args, 'prob_history_bars', 1500),
                        horizon_bars=getattr(args, 'prob_horizon_bars', 10),
                        tp_atr=getattr(args, 'prob_tp_atr', 1.0),
                        sl_atr=getattr(args, 'prob_sl_atr', 1.0),
                    )
                    
                    # 가격 정보
                    close = float(df["close"].iloc[-1])
                    atr14 = float(atr(df, 14).iloc[-1])
                    vwap_val = float(vwap_rolling(df, window=50))
                    
                    save_signal_to_db(
                        symbol=sym,
                        timeframe=timeframe,
                        direction='DEAD',
                        signal_time=datetime.now(tz=KST),
                        bar_open_time=datetime.fromtimestamp(bar_open_ms / 1000, tz=KST),
                        ema_values={
                            "ema20": m['ema20'],
                            "ema50": m['ema50'],
                            "ema100": m['ema100'],
                            "ema20_prev": m['ema20_prev'],
                            "ema50_prev": m['ema50_prev']
                        },
                        price_info={
                            "close": close,
                            "atr": atr14,
                            "vwap": vwap_val
                        },
                        probabilities=probs,
                        trade_plan=trade_plan,
                        args=args
                    )
                except Exception as e:
                    log("error", f"{sym} DB 저장 실패: {type(e).__name__}: {e}", args)
                # === DB 저장 끝 ===

                if not args.no_plain_alert:
                    msg = "\n".join([
                        "[EMA] EMA20/50 DEAD CROSS",
                        f"Symbol: {sym}",
                        f"TF: {timeframe}",
                        f"Time: {bar_time}",
                        f"EMA20/50/100: {m['ema20']:.6g} / {m['ema50']:.6g} / {m['ema100']:.6g}",
                        tv_link(sym),
                        binance_futures_link(sym)
                    ])
                    ok = send_telegram(msg, tg_token, tg_chat, args=args)
                    if ok:
                        cnt_plain_sent += 1
                        log("debug", f"{sym} plain telegram SENT", args)
                    else:
                        cnt_plain_fail += 1
                        log("error", f"{sym} plain telegram FAILED", args)

                if args.llm_alert:
                    cnt_llm_attempt += 1
                    try:
                        event = build_event_for_cross(ex, sym, timeframe, df, m, "bear", args, cross_ts_ms=recent_cross_ts_ms, bars_since_cross=recent_bars_since)
                        sent = fire_llm_alert(event, cooldown_sec=args.llm_cooldown_sec, args=args)
                        if sent:
                            cnt_llm_sent += 1
                            log("info", f"{sym} LLM SENT | {event.get('event_id')}", args)
                        else:
                            cnt_llm_skipped += 1
                            log("info", f"{sym} LLM SKIPPED (dedup/cooldown or failure) | {event.get('event_id')}", args)
                    except Exception as e:
                        cnt_llm_fail += 1
                        log("error", f"{sym} LLM ERROR | {type(e).__name__}: {e}", args)

                state[key] = bar_open_ms
                fired += 1

        time.sleep(0.05)

    # 스캔 소요 시간 계산
    scan_duration = time.time() - scan_start_time
    
    log("info",
        "SCAN END | "
        f"total={total} fired={fired} cross={cnt_cross} no_cross={cnt_no_cross} "
        f"fetch_fail={cnt_fetch_fail} short_df={cnt_short_df} dup={cnt_dup} "
        f"plain_sent={cnt_plain_sent} plain_fail={cnt_plain_fail} "
        f"llm_attempt={cnt_llm_attempt} llm_sent={cnt_llm_sent} llm_skipped={cnt_llm_skipped} llm_fail={cnt_llm_fail}",
        args)
    
    # 스캐너 상태 DB 저장
    try:
        stats = {
            'total': total,
            'fired': fired,
            'cross': cnt_cross,
            'no_cross': cnt_no_cross,
            'fetch_fail': cnt_fetch_fail,
            'short_df': cnt_short_df,
            'dup': cnt_dup,
            'plain_sent': cnt_plain_sent,
            'plain_fail': cnt_plain_fail,
            'llm_attempt': cnt_llm_attempt,
            'llm_sent': cnt_llm_sent,
            'llm_skipped': cnt_llm_skipped,
            'llm_fail': cnt_llm_fail
        }
        save_scanner_status(timeframe, scan_timestamp, stats, scan_duration, args)
    except Exception as e:
        log("error", f"스캐너 상태 저장 실패: {type(e).__name__}: {e}", args)

    return fired

# ============================================================
# main
# ============================================================
def main():
    # config.yaml은 전역에서 이미 로드됨 (CONFIG)
    
    p = argparse.ArgumentParser()
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--top", type=int, default=50)
    p.add_argument("--symbols", default="")
    p.add_argument("--scan-every-sec", type=int, default=60)
    p.add_argument("--lookback", type=int, default=200)
    p.add_argument("--align-fresh-bars", type=int, default=3)
    p.add_argument("--recent-cross-bars", type=int, default=0, help="if >0: treat cross within last N closed bars as active for analysis")
    p.add_argument("--prob-history-bars", type=int, default=1500)
    p.add_argument("--prob-horizon-bars", type=int, default=10)
    p.add_argument("--prob-tp-atr", type=float, default=1.0)
    p.add_argument("--prob-sl-atr", type=float, default=1.0)
    p.add_argument("--sl-atr", type=float, default=1.0, help="SL distance in ATR for trade plan")
    p.add_argument("--tp2-rr", type=float, default=2.0, help="TP2 distance in ATR multiples (TP1 is 1 ATR)")
    p.add_argument("--startup-telegram", action="store_true")

    # Logs
    p.add_argument("--log-level", default="info", choices=["error","info","debug","trace"])
    p.add_argument("--show-skip-reasons", action="store_true")

    # LLM options
    p.add_argument("--llm-alert", action="store_true")
    p.add_argument("--llm-cooldown-sec", type=int, default=1800)
    p.add_argument("--no-plain-alert", action="store_true")

    # TEST options
    p.add_argument("--test-llm-symbol", default="", help="force one LLM send. e.g. BTC/USDT:USDT")
    p.add_argument("--test-llm-direction", default="auto", choices=["auto","bull","bear"])
    p.add_argument("--test-plain", action="store_true", help="force one plain telegram send (pipeline check)")

    args = p.parse_args()

    # config.yaml에서 텔레그램 설정 읽기
    telegram_config = CONFIG.get('telegram', {})
    tg_token = telegram_config.get('bot_token', '')
    tg_chat = telegram_config.get('chat_id', '')
    
    # Binance API (환경 변수 또는 config.yaml)
    api_key = os.getenv("BINANCE_API_KEY", '')
    api_secret = os.getenv("BINANCE_API_SECRET", '')
    
    if not tg_token or not tg_chat:
        print("[WARNING] config.yaml에 텔레그램 설정이 없습니다. 텔레그램 알림이 비활성화됩니다.")
    
    ex = make_exchange(api_key, api_secret)
    ex.load_markets()

    # ===== TEST: plain telegram only =====
    if args.test_plain:
        msg = "\n".join([
            "[TEST] Plain Telegram OK",
            f"Time: {now_kst_str()}",
            "This is a forced test message."
        ])
        ok = send_telegram(msg, tg_token, tg_chat, args=args)
        log("info", f"TEST PLAIN DONE | ok={ok}", args)
        return

    # ===== TEST: force one LLM send =====
    
    if args.test_llm_symbol:
        if not args.llm_alert:
            raise SystemExit("TEST LLM requires --llm-alert")
        if not llm_available():
            raise SystemExit("LLM import 실패: trading_llm_report.py / telegram_notify.py 확인")

        sym = args.test_llm_symbol.strip()
        log("info", f"TEST LLM START | symbol={sym} tf={args.timeframe} dir={args.test_llm_direction}", args)

        df = fetch_df(ex, sym, args.timeframe, max(args.lookback, 260))
        if df is None or len(df) < 120:
            raise SystemExit(f"TEST LLM failed: cannot fetch enough bars for {sym}")

        # TradingView와 최대한 동일하게 '마감봉(확정봉) 기준'으로만 판정
        df = drop_incomplete_last_candle(df, args.timeframe, grace_sec=5, args=args)
        if df is None or len(df) < 120:
            raise SystemExit(f"TEST LLM failed: not enough CLOSED bars for {sym}")

        m = ema_snapshot(df)
        if not m:
            raise SystemExit(f"TEST LLM failed: ema_snapshot not ready for {sym}")

        golden, dead = cross_conditions(m)
        cross_ts_ms = None
        bars_since = 0

        if not (golden or dead):
            # If enabled, fall back to the most recent cross in last N closed bars
            rc = find_recent_cross(df, lookback_bars=getattr(args, "recent_cross_bars", 0))
            if not rc:
                raise SystemExit("TEST LLM: last CLOSED candle has NO cross (EMA20/50). Wait for a cross or enable --recent-cross-bars N.")
            detected_dir = rc["direction"]
            cross_ts_ms = int(rc["ts_ms"])
            bars_since = abs(int(rc["index"]))
        else:
            detected_dir = "bull" if golden else "bear"
            cross_ts_ms = int(df["ts_ms"].iloc[-1])
            bars_since = 0

        if args.test_llm_direction != "auto":
            want = args.test_llm_direction
            if (want != detected_dir) and (not args.test_llm_force_direction):
                raise SystemExit(
                    f"TEST LLM direction mismatch: detected={detected_dir} but requested={want}. "
                    f"Use --test-llm-direction auto or add --test-llm-force-direction."
                )
            direction = want
        else:
            direction = detected_dir

        event = build_event_for_cross(ex, sym, args.timeframe, df, m, direction, args, cross_ts_ms=cross_ts_ms, bars_since_cross=bars_since)

        # 테스트는 cooldown=0으로 1회 무조건 발송 시도
        sent = fire_llm_alert(event, cooldown_sec=0, args=args)

        log("info", f"TEST LLM DONE | sent={sent} event_id={event.get('event_id')}", args)
        return

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else fetch_top_symbols(ex, args.top)
    state = {}

    log("info", f"CONFIG | tf={args.timeframe} symbols={len(symbols)} scan_every={args.scan_every_sec}s "
               f"align_fresh={args.align_fresh_bars} llm={args.llm_alert} plain={'OFF' if args.no_plain_alert else 'ON'}", args)

    if args.startup_telegram:
        send_telegram(f"[EMA20/50] Cross scanner started {now_kst_str()}", tg_token, tg_chat, args=args)

    if args.llm_alert and not llm_available():
        raise SystemExit("LLM 옵션 ON인데 trading_llm_report.py / telegram_notify.py import 실패. 같은 폴더에 있는지 확인하세요.")

    if args.scan_every_sec <= 0:
        scan_once(ex, symbols, args.timeframe, args.lookback, state, tg_token, tg_chat, args.align_fresh_bars, args)
        return

    while True:
        scan_once(ex, symbols, args.timeframe, args.lookback, state, tg_token, tg_chat, args.align_fresh_bars, args)
        time.sleep(max(1, args.scan_every_sec))

if __name__ == "__main__":
    main()
