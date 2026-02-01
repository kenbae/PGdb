import time
import yaml
import logging
import argparse
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import ccxt
from sqlalchemy import create_engine, text
from tqdm import tqdm

# ---------------------------
# Logging Setup
# ---------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


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


def get_engine(cfg):
    """SQLAlchemy engine 생성"""
    db = cfg['db']
    dsn = f"postgresql://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['name']}"
    return create_engine(dsn, pool_pre_ping=True)


def ensure_tables_exist(engine):
    with engine.connect() as conn:
        result = conn.execute(text("SELECT to_regclass('public.candles');"))
        if result.fetchone()[0] != "candles":
            raise RuntimeError("candles 테이블이 없습니다. 먼저 스키마를 생성하세요.")


def get_last_open_time(engine, symbol: str, tf: str):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT max(open_time) FROM candles WHERE symbol=:symbol AND tf=:tf"),
            {"symbol": symbol, "tf": tf}
        )
        return result.fetchone()[0]


def upsert_candles(engine, exchange_name: str, market: str, symbol: str, tf: str, ohlcvs):
    """
    ohlcvs: list of [ms, open, high, low, close, volume]
    """
    if not ohlcvs:
        return 0

    rows = []
    for o in ohlcvs:
        open_time = ms_to_dt(o[0])  # UTC
        rows.append({
            "exchange": exchange_name,
            "symbol": symbol,
            "market": market,
            "tf": tf,
            "open_time": open_time,
            "open": float(o[1]),
            "high": float(o[2]),
            "low": float(o[3]),
            "close": float(o[4]),
            "volume": float(o[5]),
        })

    sql = text("""
        INSERT INTO candles
        (exchange, symbol, market, tf, open_time, open, high, low, close, volume)
        VALUES (:exchange, :symbol, :market, :tf, :open_time, :open, :high, :low, :close, :volume)
        ON CONFLICT (exchange, symbol, market, tf, open_time) DO NOTHING
    """)

    with engine.connect() as conn:
        with conn.begin():
            for row in rows:
                conn.execute(sql, row)
    
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


def get_specific_symbols(ex, symbols: List[str]):
    """
    특정 심볼들을 가져옴 (--symbols 옵션 사용시)
    Returns list of (db_symbol, ccxt_symbol, 0.0)
    """
    markets = ex.load_markets()
    result = []
    
    for sym in symbols:
        # BTCUSDT -> BTC/USDT:USDT 형식으로 변환 시도
        ccxt_symbol = None
        
        # 1. 정확한 매칭 시도
        for market_sym, m in markets.items():
            if m.get("id") == sym and m.get("swap") and m.get("linear"):
                ccxt_symbol = market_sym
                break
        
        # 2. 못찾으면 변환 시도 (BTCUSDT -> BTC/USDT:USDT)
        if not ccxt_symbol:
            if sym.endswith("USDT"):
                base = sym[:-4]
                potential = f"{base}/USDT:USDT"
                if potential in markets:
                    ccxt_symbol = potential
        
        if ccxt_symbol:
            result.append((sym, ccxt_symbol, 0.0))
            logger.info(f"Found symbol: {sym} -> {ccxt_symbol}")
        else:
            logger.warning(f"Symbol not found: {sym}")
    
    return result


# ---------------------------
# OHLCV incremental fetch
# ---------------------------
def fetch_ohlcv_incremental(ex, ccxt_symbol: str, tf: str, since_dt: datetime | None, limit: int):
    since_ms = dt_to_ms(since_dt) if since_dt else None
    return ex.fetch_ohlcv(ccxt_symbol, timeframe=tf, since=since_ms, limit=limit)


# ---------------------------
# Worker function for parallel processing
# ---------------------------
def process_symbol(
    db_symbol: str,
    ccxt_symbol: str,
    qv: float,
    cfg: dict,
    engine,
    ex,
    tfs: List[str],
    max_retries: int = 3
) -> Tuple[str, int, List[str]]:
    """
    단일 심볼에 대해 모든 타임프레임 수집
    Returns: (symbol, total_inserted, errors)
    """
    exchange_name = cfg["exchange"]["name"]
    market = cfg["exchange"]["market"]
    backfill_days = int(cfg["ingest"].get("backfill_days", 30))
    limit = int(cfg["ingest"].get("max_fetch_limit", 1000))
    sleep_ms = int(cfg["ingest"].get("sleep_ms", 150))
    
    total_inserted = 0
    errors = []
    
    for tf in tfs:
        retry_count = 0
        success = False
        
        while retry_count < max_retries and not success:
            try:
                last = get_last_open_time(engine, db_symbol, tf)
                
                if last:
                    since_dt = last.astimezone(timezone.utc)
                else:
                    since_dt = utc_now() - timedelta(days=backfill_days)
                
                ohlcvs = fetch_ohlcv_incremental(ex, ccxt_symbol, tf, since_dt, limit=limit)
                inserted = upsert_candles(engine, exchange_name, market, db_symbol, tf, ohlcvs)
                total_inserted += inserted
                
                if ohlcvs:
                    first_dt = ms_to_dt(ohlcvs[0][0])
                    last_dt = ms_to_dt(ohlcvs[-1][0])
                    logger.debug(
                        f"{db_symbol} tf={tf} fetched={len(ohlcvs)} inserted={inserted} "
                        f"range={first_dt.strftime('%Y-%m-%d')} ~ {last_dt.strftime('%Y-%m-%d')}"
                    )
                
                success = True
                
                if sleep_ms > 0:
                    time.sleep(sleep_ms / 1000)
                    
            except Exception as e:
                retry_count += 1
                error_msg = f"{db_symbol} tf={tf} attempt={retry_count}/{max_retries} error={str(e)}"
                
                if retry_count < max_retries:
                    logger.warning(f"Retrying... {error_msg}")
                    time.sleep(1)  # 재시도 전 대기
                else:
                    logger.error(f"Failed: {error_msg}")
                    errors.append(error_msg)
    
    return (db_symbol, total_inserted, errors)


# ---------------------------
# Main ingest function
# ---------------------------
def run_ingest(
    config_path="config.yaml",
    workers: int = 4,
    specific_symbols: Optional[List[str]] = None
):
    cfg = load_config(config_path)

    exchange_name = cfg["exchange"]["name"]
    market = cfg["exchange"]["market"]
    top_n = int(cfg["exchange"]["top_n"])
    quote = cfg["exchange"].get("quote", "USDT")
    contract = cfg["exchange"].get("contract", "PERP")
    exclude = cfg["exchange"].get("exclude_symbols", [])

    tfs = cfg["timeframes"]

    logger.info(f"Starting ingest: exchange={exchange_name} market={market}")
    logger.info(f"Timeframes: {tfs}")
    logger.info(f"Workers: {workers}")
    
    ex = build_binance_usdtm()
    engine = get_engine(cfg)
    
    ensure_tables_exist(engine)
    
    # Select symbols
    if specific_symbols:
        logger.info(f"Using specific symbols: {specific_symbols}")
        selected = get_specific_symbols(ex, specific_symbols)
    else:
        logger.info(f"Selecting Top {top_n} symbols by 24h quoteVolume")
        selected = select_top_symbols(ex, top_n=top_n, quote=quote, contract=contract, exclude=exclude)
    
    logger.info(f"Selected {len(selected)} symbols")
    
    if not selected:
        logger.error("No symbols selected. Exiting.")
        return
    
    # Parallel processing with progress bar
    total_inserted = 0
    all_errors = []
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                process_symbol,
                db_symbol,
                ccxt_symbol,
                qv,
                cfg,
                engine,
                ex,
                tfs
            ): db_symbol
            for db_symbol, ccxt_symbol, qv in selected
        }
        
        # Progress bar
        with tqdm(total=len(futures), desc="Processing symbols", unit="symbol") as pbar:
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    db_symbol, inserted, errors = future.result()
                    total_inserted += inserted
                    all_errors.extend(errors)
                    
                    pbar.set_postfix({
                        'symbol': db_symbol,
                        'inserted': inserted,
                        'total': total_inserted
                    })
                    pbar.update(1)
                    
                except Exception as e:
                    logger.error(f"Unexpected error for {symbol}: {e}")
                    all_errors.append(f"{symbol}: {str(e)}")
                    pbar.update(1)
    
    # Summary
    logger.info("=" * 60)
    logger.info(f"INGEST COMPLETE")
    logger.info(f"Total symbols processed: {len(selected)}")
    logger.info(f"Total rows inserted: {total_inserted}")
    logger.info(f"Total errors: {len(all_errors)}")
    
    if all_errors:
        logger.warning("Errors encountered:")
        for err in all_errors[:10]:  # 최대 10개만 표시
            logger.warning(f"  - {err}")
        if len(all_errors) > 10:
            logger.warning(f"  ... and {len(all_errors) - 10} more errors")
    
    logger.info("=" * 60)
    
    engine.dispose()


# ---------------------------
# CLI
# ---------------------------
def main():
    parser = argparse.ArgumentParser(description="Binance Futures OHLCV Data Ingestion")
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to config file (default: config.yaml)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers (default: 4)"
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated list of specific symbols (e.g., BTCUSDT,ETHUSDT)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging (DEBUG level)"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    specific_symbols = None
    if args.symbols:
        specific_symbols = [s.strip().upper() for s in args.symbols.split(",")]
    
    run_ingest(
        config_path=args.config,
        workers=args.workers,
        specific_symbols=specific_symbols
    )


if __name__ == "__main__":
    main()
