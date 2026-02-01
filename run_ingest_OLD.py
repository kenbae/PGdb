import time
import yaml
from datetime import datetime, timedelta, timezone
import ccxt
import psycopg


# ---------------------------
# Helpers
# ---------------------------
def utc_now():
    return datetime.now(timezone.utc)


def dt_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def normalize_symbol_from_binance_id(symbol_id: str) -> str:
    """
    Binance linear perpetual symbols are usually like 'BTCUSDT'
    We store symbols in DB as that exact id for consistency.
    """
    return symbol_id


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


def ensure_tables_exist(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.candles');")
        if cur.fetchone()[0] != "candles":
            raise RuntimeError("candles 테이블이 없습니다. 먼저 스키마를 생성하세요.")


def get_last_open_time(conn, symbol: str, tf: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT max(open_time)
            FROM candles
            WHERE symbol=%s AND tf=%s
            """,
            (symbol, tf),
        )
        return cur.fetchone()[0]


def upsert_candles(conn, exchange_name: str, market: str, symbol: str, tf: str, ohlcvs):
    """
    ohlcvs: list of [ms, open, high, low, close, volume]
    """
    if not ohlcvs:
        return 0

    rows = []
    for o in ohlcvs:
        open_time = ms_to_dt(o[0])  # UTC
        rows.append(
            (
                exchange_name,
                symbol,
                market,
                tf,
                open_time,
                float(o[1]),
                float(o[2]),
                float(o[3]),
                float(o[4]),
                float(o[5]),
            )
        )

    sql = """
        INSERT INTO candles
        (exchange, symbol, market, tf, open_time, open, high, low, close, volume)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (symbol, tf, open_time) DO NOTHING
    """

    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


# ---------------------------
# Binance Futures selection (Top N by 24h quoteVolume)
# ---------------------------
def build_binance_usdtm():
    ex = ccxt.binance(
        {
            "enableRateLimit": True,
            "options": {"defaultType": "future"},  # USDT-M futures
        }
    )
    return ex


def select_top_symbols(ex, top_n: int, quote="USDT", contract="PERP", exclude=None):
    """
    Returns list of DB symbol ids like 'BTCUSDT'
    Uses /fapi/v1/ticker/24hr (via ccxt fetch_tickers) and filters:
      - quote == USDT
      - contractType == PERPETUAL (when available)
    """
    exclude = set(exclude or [])
    markets = ex.load_markets()

    # Use Binance futures tickers
    tickers = ex.fetch_tickers()

    candidates = []
    for sym, t in tickers.items():
        m = markets.get(sym)
        if not m:
            continue

        # Only linear USDT futures
        # In ccxt, linear swap has: m.get('linear') == True and m.get('swap') == True
        if not (m.get("swap") and m.get("linear")):
            continue

        # Quote filter
        if m.get("quote") != quote:
            continue

        # Exclude list (store by id, e.g., BTCUSDT)
        sym_id = m.get("id") or sym.replace("/", "")
        sym_id = normalize_symbol_from_binance_id(sym_id)
        if sym_id in exclude:
            continue

        # Perpetual filter: on Binance, contractType may be 'PERPETUAL' in info
        info = t.get("info") or {}
        contract_type = info.get("contractType") or info.get("contractType".lower())
        if contract == "PERP":
            if contract_type and str(contract_type).upper() != "PERPETUAL":
                continue

        # quoteVolume (24h) is in quote currency; prefer it
        qv = None
        try:
            qv = float(info.get("quoteVolume")) if info.get("quoteVolume") is not None else None
        except Exception:
            qv = None

        if qv is None:
            # fallback: ccxt standardized 'quoteVolume' if present
            try:
                qv = float(t.get("quoteVolume")) if t.get("quoteVolume") is not None else 0.0
            except Exception:
                qv = 0.0

        candidates.append((sym_id, sym, qv))

    # sort by quote volume desc
    candidates.sort(key=lambda x: x[2], reverse=True)
    selected = candidates[:top_n]

    # return list of (db_symbol, ccxt_symbol, quoteVol)
    return selected


# ---------------------------
# OHLCV incremental fetch
# ---------------------------
def fetch_ohlcv_incremental(ex, ccxt_symbol: str, tf: str, since_dt: datetime | None, limit: int):
    since_ms = dt_to_ms(since_dt) if since_dt else None
    return ex.fetch_ohlcv(ccxt_symbol, timeframe=tf, since=since_ms, limit=limit)


def run_ingest(config_path="config.yaml"):
    cfg = load_config(config_path)

    exchange_name = cfg["exchange"]["name"]
    market = cfg["exchange"]["market"]
    top_n = int(cfg["exchange"]["top_n"])
    quote = cfg["exchange"].get("quote", "USDT")
    contract = cfg["exchange"].get("contract", "PERP")
    exclude = cfg["exchange"].get("exclude_symbols", [])

    tfs = cfg["timeframes"]
    backfill_days = int(cfg["ingest"].get("backfill_days", 30))
    limit = int(cfg["ingest"].get("max_fetch_limit", 1000))
    sleep_ms = int(cfg["ingest"].get("sleep_ms", 150))

    ex = build_binance_usdtm()

    with get_conn(cfg) as conn:
        ensure_tables_exist(conn)

        # Select Top N
        selected = select_top_symbols(ex, top_n=top_n, quote=quote, contract=contract, exclude=exclude)
        print(f"[INFO] Selected symbols: {len(selected)} (Top {top_n} by 24h quoteVolume)")

        # Ingest for each symbol/tf
        total_inserted = 0

        for i, (db_symbol, ccxt_symbol, qv) in enumerate(selected, start=1):
            print(f"\n[{i}/{len(selected)}] {db_symbol} ({ccxt_symbol}) 24hQuoteVol={qv:,.0f}")

            for tf in tfs:
                last = get_last_open_time(conn, db_symbol, tf)

                if last:
                    # Start from the last open_time (Binance may return it again; conflict handles it)
                    since_dt = last.astimezone(timezone.utc)
                else:
                    since_dt = utc_now() - timedelta(days=backfill_days)

                try:
                    ohlcvs = fetch_ohlcv_incremental(ex, ccxt_symbol, tf, since_dt, limit=limit)
                except Exception as e:
                    print(f"[WARN] fetch_ohlcv failed: {db_symbol} tf={tf} err={e}")
                    continue

                inserted = upsert_candles(conn, exchange_name, market, db_symbol, tf, ohlcvs)
                total_inserted += inserted

                if ohlcvs:
                    first_dt = ms_to_dt(ohlcvs[0][0])
                    last_dt = ms_to_dt(ohlcvs[-1][0])
                    print(f"  tf={tf:<3} fetched={len(ohlcvs):<4} inserted={inserted:<4} range={first_dt} ~ {last_dt}")
                else:
                    print(f"  tf={tf:<3} fetched=0")

                if sleep_ms > 0:
                    time.sleep(sleep_ms / 1000)

        print(f"\n[DONE] total_inserted_rows={total_inserted}")


if __name__ == "__main__":
    run_ingest()
