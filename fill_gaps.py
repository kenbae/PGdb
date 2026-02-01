"""
캔들 데이터 갭(빠진 구간) 채우기

사용법:
    python fill_gaps.py --symbol 1000BONKUSDT --tf 15m
    python fill_gaps.py --symbol 1000BONKUSDT --tf 15m --start 2025-01-01 --end 2025-01-21
    python fill_gaps.py --symbol 1000BONKUSDT --tf 15m --dry-run  # 빠진 구간만 확인
"""

import argparse
import logging
import time
import yaml
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Optional

import ccxt
import pandas as pd
from sqlalchemy import create_engine, text
from tqdm import tqdm

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_engine(cfg):
    db = cfg['db']
    dsn = f"postgresql://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['name']}"
    return create_engine(dsn, pool_pre_ping=True)


def get_timeframe_minutes(tf: str) -> int:
    """타임프레임을 분 단위로 변환"""
    tf_map = {
        '1m': 1,
        '3m': 3,
        '5m': 5,
        '15m': 15,
        '30m': 30,
        '1h': 60,
        '2h': 120,
        '4h': 240,
        '6h': 360,
        '8h': 480,
        '12h': 720,
        '1d': 1440,
        '3d': 4320,
        '1w': 10080,
    }
    return tf_map.get(tf, 15)


def find_gaps(engine, symbol: str, tf: str, start_date: datetime, end_date: datetime) -> List[Tuple[datetime, datetime]]:
    """
    DB에서 캔들 데이터의 빠진 구간(gap)을 찾음

    Returns:
        List of (gap_start, gap_end) tuples
    """
    tf_minutes = get_timeframe_minutes(tf)
    expected_interval = timedelta(minutes=tf_minutes)

    # 기존 캔들 시간 조회
    query = text("""
        SELECT open_time
        FROM candles
        WHERE symbol = :symbol AND tf = :tf
          AND open_time >= :start_date AND open_time <= :end_date
        ORDER BY open_time
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {
            "symbol": symbol,
            "tf": tf,
            "start_date": start_date,
            "end_date": end_date
        })
        existing_times = [row[0] for row in result.fetchall()]

    if not existing_times:
        logger.warning(f"해당 기간에 데이터가 없습니다: {symbol} {tf}")
        return [(start_date, end_date)]

    # 갭 찾기
    gaps = []

    # 시작 부분 갭 확인
    first_time = existing_times[0]
    if first_time.tzinfo is None:
        first_time = first_time.replace(tzinfo=timezone.utc)

    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=timezone.utc)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)

    if first_time > start_date + expected_interval:
        gaps.append((start_date, first_time - expected_interval))

    # 중간 갭 찾기
    for i in range(1, len(existing_times)):
        prev_time = existing_times[i-1]
        curr_time = existing_times[i]

        if prev_time.tzinfo is None:
            prev_time = prev_time.replace(tzinfo=timezone.utc)
        if curr_time.tzinfo is None:
            curr_time = curr_time.replace(tzinfo=timezone.utc)

        expected_next = prev_time + expected_interval

        # 갭이 있으면 (예상 시간과 실제 시간 차이가 interval보다 크면)
        if curr_time > expected_next + timedelta(minutes=1):  # 1분 여유
            gaps.append((expected_next, curr_time - expected_interval))

    # 끝 부분 갭 확인
    last_time = existing_times[-1]
    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=timezone.utc)

    if last_time < end_date - expected_interval:
        gaps.append((last_time + expected_interval, end_date))

    return gaps


def fetch_and_fill_gap(
    engine,
    ex,
    symbol: str,
    ccxt_symbol: str,
    tf: str,
    gap_start: datetime,
    gap_end: datetime,
    exchange_name: str = "binance",
    market: str = "usdtm"
) -> int:
    """
    단일 갭 구간의 캔들 데이터를 가져와서 DB에 저장

    Returns:
        삽입된 캔들 수
    """
    tf_minutes = get_timeframe_minutes(tf)
    limit = 1000  # Binance 최대 limit

    total_inserted = 0
    current_start = gap_start

    while current_start < gap_end:
        since_ms = int(current_start.timestamp() * 1000)

        try:
            ohlcv = ex.fetch_ohlcv(ccxt_symbol, timeframe=tf, since=since_ms, limit=limit)
        except Exception as e:
            logger.error(f"데이터 가져오기 실패: {symbol} {tf} from {current_start}: {e}")
            break

        if not ohlcv:
            break

        # DB에 저장
        inserted = 0
        with engine.connect() as conn:
            for candle in ohlcv:
                candle_time = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc)

                # 범위 내 데이터만 저장
                if candle_time > gap_end:
                    continue

                # 이미 존재하는지 확인
                check_query = text("""
                    SELECT 1 FROM candles
                    WHERE symbol = :symbol AND tf = :tf AND open_time = :open_time
                    LIMIT 1
                """)
                exists = conn.execute(check_query, {
                    "symbol": symbol,
                    "tf": tf,
                    "open_time": candle_time
                }).fetchone()

                if not exists:
                    insert_query = text("""
                        INSERT INTO candles (exchange, market, symbol, tf, open_time, open, high, low, close, volume)
                        VALUES (:exchange, :market, :symbol, :tf, :open_time, :open, :high, :low, :close, :volume)
                    """)
                    conn.execute(insert_query, {
                        "exchange": exchange_name,
                        "market": market,
                        "symbol": symbol,
                        "tf": tf,
                        "open_time": candle_time,
                        "open": candle[1],
                        "high": candle[2],
                        "low": candle[3],
                        "close": candle[4],
                        "volume": candle[5]
                    })
                    inserted += 1

            conn.commit()

        total_inserted += inserted

        # 다음 구간으로 이동
        if ohlcv:
            last_candle_time = datetime.fromtimestamp(ohlcv[-1][0] / 1000, tz=timezone.utc)
            current_start = last_candle_time + timedelta(minutes=tf_minutes)
        else:
            break

        # Rate limit
        time.sleep(0.2)

    return total_inserted


def fill_gaps(
    symbol: str,
    tf: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    config_path: str = "config.yaml",
    dry_run: bool = False
):
    """
    심볼의 캔들 데이터 갭을 찾아서 채움
    """
    cfg = load_config(config_path)
    engine = get_engine(cfg)

    # 기본 날짜 설정
    if end_date is None:
        end_date = datetime.now(timezone.utc)
    if start_date is None:
        start_date = end_date - timedelta(days=30)

    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=timezone.utc)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)

    logger.info(f"심볼: {symbol}, 타임프레임: {tf}")
    logger.info(f"기간: {start_date.strftime('%Y-%m-%d %H:%M')} ~ {end_date.strftime('%Y-%m-%d %H:%M')} UTC")

    # 갭 찾기
    logger.info("갭 찾는 중...")
    gaps = find_gaps(engine, symbol, tf, start_date, end_date)

    if not gaps:
        logger.info("✅ 갭이 없습니다. 데이터가 완전합니다.")
        return

    # 갭 정보 출력
    logger.info(f"발견된 갭: {len(gaps)}개")
    for i, (gap_start, gap_end) in enumerate(gaps, 1):
        duration = gap_end - gap_start
        logger.info(f"  [{i}] {gap_start.strftime('%Y-%m-%d %H:%M')} ~ {gap_end.strftime('%Y-%m-%d %H:%M')} ({duration})")

    if dry_run:
        logger.info("(dry-run 모드 - 실제 데이터를 가져오지 않습니다)")
        return

    # Binance 연결
    ex = ccxt.binance({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })
    ex.load_markets()

    # ccxt 심볼 형식 변환
    ccxt_symbol = symbol
    if '/' not in symbol and symbol.endswith('USDT'):
        base = symbol[:-4]
        ccxt_symbol = f"{base}/USDT:USDT"

    logger.info(f"ccxt 심볼: {ccxt_symbol}")

    # 갭 채우기
    total_inserted = 0
    with tqdm(total=len(gaps), desc="갭 채우기", unit="gap") as pbar:
        for gap_start, gap_end in gaps:
            inserted = fetch_and_fill_gap(
                engine, ex, symbol, ccxt_symbol, tf, gap_start, gap_end
            )
            total_inserted += inserted
            pbar.set_postfix({'inserted': total_inserted})
            pbar.update(1)

    logger.info(f"✅ 완료: {total_inserted}개 캔들 추가됨")

    # 다시 갭 확인
    remaining_gaps = find_gaps(engine, symbol, tf, start_date, end_date)
    if remaining_gaps:
        logger.warning(f"⚠️ 여전히 {len(remaining_gaps)}개의 갭이 남아있습니다")
    else:
        logger.info("✅ 모든 갭이 채워졌습니다")

    engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="캔들 데이터 갭 채우기")
    parser.add_argument(
        "--symbol",
        type=str,
        required=True,
        help="심볼 (예: 1000BONKUSDT)"
    )
    parser.add_argument(
        "--tf",
        type=str,
        default="15m",
        help="타임프레임 (기본: 15m)"
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="시작 날짜 (YYYY-MM-DD 형식, 기본: 30일 전)"
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="종료 날짜 (YYYY-MM-DD 형식, 기본: 오늘)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="설정 파일 경로"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="갭만 확인하고 실제로 채우지 않음"
    )

    args = parser.parse_args()

    start_date = None
    end_date = None

    if args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if args.end:
        end_date = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    fill_gaps(
        symbol=args.symbol.upper(),
        tf=args.tf,
        start_date=start_date,
        end_date=end_date,
        config_path=args.config,
        dry_run=args.dry_run
    )


if __name__ == "__main__":
    main()
