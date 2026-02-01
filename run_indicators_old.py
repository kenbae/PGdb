import yaml
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

import psycopg

# ta 라이브러리의 IndexError 회피를 위해 직접 ATR/RSI 구현(안정)
# (데이터가 짧으면 NaN 유지)
def calc_atr(high, low, close, window=14):
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)

    if len(close) < 2:
        return np.full(len(close), np.nan)

    prev_close = np.roll(close, 1)
    prev_close[0] = np.nan

    tr = np.nanmax(
        np.vstack([
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ]),
        axis=0
    )

    atr = pd.Series(tr).rolling(window=window, min_periods=window).mean().to_numpy()
    return atr


def calc_rsi(close, window=14):
    close = pd.Series(close, dtype=float)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    # Wilder's smoothing (RMA)
    avg_gain = gain.ewm(alpha=1/window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1/window, adjust=False, min_periods=window).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.to_numpy()


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_conn(cfg):
    dsn = (
        f"host={cfg['db']['host']} port={cfg['db']['port']} "
        f"dbname={cfg['db']['name']} user={cfg['db']['user']} "
        f"password={cfg['db']['password']}"
    )
    return psycopg.connect(dsn)


def get_symbols(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT symbol FROM candles")
        return [r[0] for r in cur.fetchall()]


def get_last_indicator_time(conn, symbol, tf):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(open_time) FROM indicators WHERE symbol=%s AND tf=%s",
            (symbol, tf),
        )
        return cur.fetchone()[0]


def load_candles_incremental(conn, symbol, tf, since_time_utc, lookback_bars=250):
    """
    증분 계산용:
    - 마지막 indicator 시간 이후의 캔들만 가져오되,
    - EMA/ATR/RSI 안정화를 위해 lookback_bars만큼 과거도 함께 가져옴
    """
    if since_time_utc is None:
        # 최초 계산: 최근 lookback_bars + 여유 데이터
        query = """
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE symbol=%s AND tf=%s
            ORDER BY open_time
        """
        df = pd.read_sql(query, conn, params=(symbol, tf))
        return df

    # since_time_utc 이전의 lookback_bars 구간을 확보하기 위해
    # since_time_utc 기준으로 과거 lookback_bars를 포함해 로드
    query = """
        WITH base AS (
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE symbol=%s AND tf=%s AND open_time <= %s
            ORDER BY open_time DESC
            LIMIT %s
        )
        SELECT open_time, open, high, low, close, volume
        FROM (
            SELECT * FROM base
            UNION ALL
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE symbol=%s AND tf=%s AND open_time > %s
        ) x
        ORDER BY open_time
    """
    df = pd.read_sql(
        query,
        conn,
        params=(symbol, tf, since_time_utc, lookback_bars, symbol, tf, since_time_utc),
    )
    return df


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if df.empty:
        return df

    # open_time을 UTC로 정규화
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)

    # 정렬 보장
    df = df.sort_values("open_time")

    # 최소 길이 체크 (ATR/RSI window=14)
    # EMA/VWAP은 데이터 1개여도 계산 가능하나, ATR/RSI는 NaN 유지
    close = df["close"].astype(float)

    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()

    tp = (df["high"].astype(float) + df["low"].astype(float) + close) / 3.0
    vol = df["volume"].astype(float)

    # VWAP: 누적 방식(단순). volume이 0이면 NaN 처리
    pv = (tp * vol).cumsum()
    vv = vol.cumsum()
    df["vwap"] = np.where(vv > 0, pv / vv, np.nan)

    df["atr"] = calc_atr(df["high"], df["low"], df["close"], window=14)
    df["rsi"] = calc_rsi(df["close"], window=14)

    return df


def upsert_indicators(conn, df, symbol, tf, only_newer_than_utc):
    if df.empty:
        return 0

    # only_newer_than_utc 이후의 open_time만 적재(증분)
    if only_newer_than_utc is not None:
        df = df[df["open_time"] > pd.to_datetime(only_newer_than_utc, utc=True)]

    if df.empty:
        return 0

    rows = []
    for _, r in df.iterrows():
        rows.append(
            (
                symbol,
                tf,
                r["open_time"].to_pydatetime(),  # tz-aware(UTC)
                None if pd.isna(r["ema20"]) else float(r["ema20"]),
                None if pd.isna(r["ema50"]) else float(r["ema50"]),
                None if pd.isna(r["ema200"]) else float(r["ema200"]),
                None if pd.isna(r["vwap"]) else float(r["vwap"]),
                None if pd.isna(r["rsi"]) else float(r["rsi"]),
                None if pd.isna(r["atr"]) else float(r["atr"]),
            )
        )

    sql = """
        INSERT INTO indicators
        (symbol, tf, open_time, ema20, ema50, ema200, vwap, rsi, atr)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (symbol, tf, open_time) DO NOTHING
    """

    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def run_indicators():
    cfg = load_config()
    tfs = cfg["timeframes"]

    with get_conn(cfg) as conn:
        symbols = get_symbols(conn)
        print(f"[INFO] Symbols: {len(symbols)}")

        total = 0

        for symbol in symbols:
            for tf in tfs:
                last_ind = get_last_indicator_time(conn, symbol, tf)
                # last_ind는 timestamptz. 계산은 UTC로 통일
                since_utc = last_ind.astimezone(timezone.utc) if last_ind else None

                df = load_candles_incremental(conn, symbol, tf, since_utc, lookback_bars=300)
                if df.empty:
                    continue

                # 캔들이 너무 적으면(예: 1개) EMA/VWAP만 계산되고 ATR/RSI는 NaN으로 남음
                df_ind = calculate_indicators(df)

                inserted = upsert_indicators(conn, df_ind, symbol, tf, since_utc)
                total += inserted
                print(f"[OK] {symbol} {tf} indicators inserted: {inserted}")

        print(f"[DONE] total indicators inserted: {total}")


if __name__ == "__main__":
    run_indicators()
