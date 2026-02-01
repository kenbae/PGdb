# -*- coding: utf-8 -*-
"""
Binance Vision 데이터를 DB에 채우기 (병렬 처리 버전)

- 병렬 다운로드 및 DB 삽입
- 연도별 지정 가능 (예: 2024년만)
- 멀티프로세싱으로 속도 향상
"""

import requests
import zipfile
import pandas as pd
import psycopg2
from datetime import datetime, timedelta, timezone
import yaml
import os
from typing import List, Tuple, Optional
import time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from multiprocessing import cpu_count
import logging

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(f'backfill_{datetime.now().strftime("%Y%m%d")}.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# Config 로드
# ============================================================
def load_config(config_path: str = "config.yaml") -> dict:
    """config.yaml 로드"""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

CONFIG = load_config()

# ============================================================
# DB 연결
# ============================================================
def get_db_connection():
    """PostgreSQL 연결"""
    db_config = CONFIG.get('db', {})
    
    return psycopg2.connect(
        host=db_config.get('host', 'localhost'),
        port=db_config.get('port', 5432),
        database=db_config.get('name', 'marketdb'),
        user=db_config.get('user', 'trader'),
        password=db_config.get('password', ''),
        client_encoding='utf8'
    )

# ============================================================
# DB에서 종목 목록 가져오기
# ============================================================
def get_symbols_from_db(filter_symbols: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    """
    DB에서 종목 목록 가져오기 (1분봉, 5분봉 제외)
    
    Args:
        filter_symbols: 특정 심볼만 필터링 (예: ['BTCUSDT', 'ETHUSDT'])
    
    Returns:
        List of (symbol, timeframe) tuples
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        if filter_symbols:
            # 특정 심볼만 선택
            placeholders = ','.join(['%s'] * len(filter_symbols))
            query = f"""
                SELECT DISTINCT symbol, tf 
                FROM candles 
                WHERE tf NOT IN ('1m', '5m')
                  AND symbol IN ({placeholders})
                ORDER BY symbol, tf
            """
            cur.execute(query, filter_symbols)
        else:
            # 전체 심볼
            query = """
                SELECT DISTINCT symbol, tf 
                FROM candles 
                WHERE tf NOT IN ('1m', '5m')
                ORDER BY symbol, tf
            """
            cur.execute(query)
        
        symbols = cur.fetchall()
        cur.close()
        
        logger.info(f"ℹ️  1분봉, 5분봉 제외됨 (1m, 5m 타임프레임 스킵)")
        if filter_symbols:
            logger.info(f"ℹ️  필터링된 심볼: {len(filter_symbols)}개")
        
        return symbols
        
    finally:
        if conn:
            conn.close()

# ============================================================
# DB에서 마지막 데이터 시간 확인
# ============================================================
def get_last_timestamp(symbol: str, timeframe: str) -> Optional[datetime]:
    """특정 종목의 마지막 데이터 시간 가져오기"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        query = """
            SELECT MAX(open_time) 
            FROM candles 
            WHERE symbol = %s AND tf = %s
        """
        
        cur.execute(query, (symbol, timeframe))
        result = cur.fetchone()
        cur.close()
        
        return result[0] if result[0] else None
        
    finally:
        if conn:
            conn.close()

# ============================================================
# Binance Vision 다운로드
# ============================================================
def download_binance_vision_monthly(
    symbol: str, 
    timeframe: str, 
    year: int, 
    month: int,
    market_type: str = "futures"
) -> Optional[pd.DataFrame]:
    """
    Binance Vision에서 월별 데이터 다운로드
    """
    if market_type == "futures":
        base_url = "https://data.binance.vision/data/futures/um/monthly/klines"
    else:
        base_url = "https://data.binance.vision/data/spot/monthly/klines"
    
    filename = f"{symbol}-{timeframe}-{year}-{month:02d}.zip"
    url = f"{base_url}/{symbol}/{timeframe}/{filename}"
    
    try:
        response = requests.get(url, timeout=30)
        
        if response.status_code == 404:
            return None
        elif response.status_code != 200:
            logger.warning(f"❌ {filename}: HTTP {response.status_code}")
            return None
        
        # 임시 파일
        temp_zip = f"temp_{os.getpid()}_{filename}"
        with open(temp_zip, 'wb') as f:
            f.write(response.content)
        
        # 압축 해제
        with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
            zip_ref.extractall('.')
        
        # CSV 읽기
        csv_filename = filename.replace('.zip', '.csv')
        
        # 헤더 확인
        with open(csv_filename, 'r') as f:
            first_line = f.readline()
            has_header = 'open_time' in first_line.lower()
        
        if has_header:
            df = pd.read_csv(csv_filename, skiprows=1, header=None)
        else:
            df = pd.read_csv(csv_filename, header=None)
        
        # 컬럼명
        df.columns = [
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ]
        
        df = df[['open_time', 'open', 'high', 'low', 'close', 'volume']]
        
        # 타임스탬프 변환
        df['open_time'] = pd.to_numeric(df['open_time'], errors='coerce')
        df = df.dropna(subset=['open_time'])
        
        if len(df) == 0:
            os.remove(temp_zip)
            os.remove(csv_filename)
            return None
        
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        
        # 임시 파일 삭제
        os.remove(temp_zip)
        os.remove(csv_filename)
        
        logger.info(f"✅ {filename}: {len(df)} 캔들")
        return df
        
    except Exception as e:
        logger.error(f"❌ {filename}: {e}")
        return None

# ============================================================
# DB에 데이터 삽입
# ============================================================
def insert_candles_to_db(
    df: pd.DataFrame, 
    symbol: str, 
    timeframe: str, 
    exchange: str = "binance", 
    market: str = "usdtm"
):
    """DataFrame을 DB에 삽입 (배치 처리)"""
    conn = None
    try:
        conn = get_db_connection()
        
        query = """
            INSERT INTO candles (exchange, symbol, market, tf, open_time, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (exchange, symbol, market, tf, open_time) DO NOTHING
        """
        
        values_list = []
        
        for _, row in df.iterrows():
            try:
                values = (
                    exchange, symbol, market, timeframe,
                    row['open_time'],
                    float(row['open']),
                    float(row['high']),
                    float(row['low']),
                    float(row['close']),
                    float(row['volume'])
                )
                values_list.append(values)
            except Exception as e:
                continue
        
        if not values_list:
            return 0
        
        with conn.cursor() as cur:
            try:
                from psycopg2.extras import execute_batch
                execute_batch(cur, query, values_list, page_size=1000)
                conn.commit()
                return len(values_list)
                
            except Exception as e:
                conn.rollback()
                
                # 개별 삽입 폴백
                inserted = 0
                for values in values_list:
                    try:
                        cur.execute(query, values)
                        if cur.rowcount > 0:
                            inserted += 1
                    except:
                        pass
                
                conn.commit()
                return inserted
    
    except Exception as e:
        logger.error(f"❌ DB 삽입 실패: {e}")
        if conn:
            conn.rollback()
        return 0
    
    finally:
        if conn:
            conn.close()

# ============================================================
# 단일 작업 처리 (병렬 실행용)
# ============================================================
def process_single_month(args):
    """
    단일 월 데이터 다운로드 및 DB 삽입
    
    Args:
        args: (symbol, timeframe, year, month, market_type, clean_symbol)
    
    Returns:
        (symbol, timeframe, year, month, success, count)
    """
    symbol, timeframe, year, month, market_type, clean_symbol = args
    
    try:
        # 다운로드
        df = download_binance_vision_monthly(
            clean_symbol, 
            timeframe, 
            year, 
            month,
            market_type
        )
        
        if df is not None and not df.empty:
            # DB 삽입
            count = insert_candles_to_db(df, symbol, timeframe, 'binance', 'usdtm')
            return (symbol, timeframe, year, month, True, count)
        else:
            return (symbol, timeframe, year, month, False, 0)
            
    except Exception as e:
        logger.error(f"❌ {symbol} {timeframe} {year}-{month:02d}: {e}")
        return (symbol, timeframe, year, month, False, 0)

# ============================================================
# 메인 함수 (병렬 처리)
# ============================================================
def backfill_historical_data_parallel(
    start_year: int = 2020,
    start_month: int = 1,
    end_year: Optional[int] = None,  # 종료 연도 (None이면 현재까지)
    end_month: Optional[int] = None,  # 종료 월
    market_type: str = "futures",
    skip_recent: bool = False,
    max_workers: int = 4,  # 병렬 작업 수
    filter_symbols: Optional[List[str]] = None  # 특정 심볼만 (NEW)
):
    """
    병렬 처리로 과거 데이터 백필
    
    Args:
        start_year: 시작 연도
        start_month: 시작 월
        end_year: 종료 연도 (None이면 현재)
        end_month: 종료 월 (None이면 현재)
        market_type: 'spot' or 'futures'
        skip_recent: 최근 데이터 있으면 스킵
        max_workers: 병렬 작업 수 (기본: 4)
        filter_symbols: 특정 심볼만 (예: ['BTCUSDT', 'ETHUSDT'])
    """
    logger.info("=" * 70)
    logger.info("Binance Vision Backfill (병렬 처리)")
    logger.info("=" * 70)
    
    # 1. DB에서 종목 목록
    logger.info("\n[1] DB에서 종목 목록 가져오기...")
    symbols_tf = get_symbols_from_db(filter_symbols)  # 필터 적용
    logger.info(f"✅ 총 {len(symbols_tf)}개 종목 발견")
    
    if not symbols_tf:
        logger.error("❌ DB에 종목 없음")
        return
    
    # 2. 날짜 범위 설정
    current_date = datetime.now()
    
    if end_year is None:
        end_year = current_date.year
        end_month = current_date.month
    elif end_month is None:
        end_month = 12
    
    logger.info(f"\n📅 기간: {start_year}-{start_month:02d} ~ {end_year}-{end_month:02d}")
    
    # 3. 모든 작업 생성
    tasks = []
    
    for symbol, timeframe in symbols_tf:
        # 최근 데이터 체크
        if skip_recent:
            last_timestamp = get_last_timestamp(symbol, timeframe)
            if last_timestamp:
                if last_timestamp.tzinfo is None:
                    last_timestamp = last_timestamp.replace(tzinfo=timezone.utc)
                now_utc = datetime.now(timezone.utc)
                if last_timestamp > now_utc - timedelta(days=7):
                    logger.info(f"⏭️  {symbol} {timeframe}: 최근 데이터 있음 (스킵)")
                    continue
        
        # 심볼 변환
        clean_symbol = symbol.replace('/', '').replace(':', '')
        
        # 월별 작업 생성
        year = end_year
        month = end_month
        
        while True:
            # 시작 날짜 이전이면 종료
            if datetime(year, month, 1) < datetime(start_year, start_month, 1):
                break
            
            # 미래 날짜 스킵
            if datetime(year, month, 1) > current_date:
                month -= 1
                if month < 1:
                    month = 12
                    year -= 1
                continue
            
            tasks.append((symbol, timeframe, year, month, market_type, clean_symbol))
            
            # 다음 월
            month -= 1
            if month < 1:
                month = 12
                year -= 1
    
    total_tasks = len(tasks)
    logger.info(f"\n📊 총 {total_tasks}개 작업 생성")
    logger.info(f"⚙️  병렬 작업 수: {max_workers}")
    
    # 4. 병렬 실행
    completed = 0
    success_count = 0
    total_candles = 0
    
    logger.info("\n🚀 병렬 다운로드 시작...\n")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 작업 제출
        futures = {executor.submit(process_single_month, task): task for task in tasks}
        
        # 완료된 작업 처리
        for future in as_completed(futures):
            completed += 1
            task = futures[future]
            
            try:
                symbol, timeframe, year, month, success, count = future.result()
                
                if success:
                    success_count += 1
                    total_candles += count
                    logger.info(f"[{completed}/{total_tasks}] ✅ {symbol} {timeframe} {year}-{month:02d}: {count} 캔들")
                else:
                    logger.debug(f"[{completed}/{total_tasks}] ⏭️  {symbol} {timeframe} {year}-{month:02d}: 데이터 없음")
                
            except Exception as e:
                logger.error(f"[{completed}/{total_tasks}] ❌ 작업 실패: {e}")
    
    # 5. 완료
    logger.info("\n" + "=" * 70)
    logger.info(f"✅ 백필 완료!")
    logger.info(f"📊 통계:")
    logger.info(f"   - 총 작업: {total_tasks}")
    logger.info(f"   - 성공: {success_count}")
    logger.info(f"   - 실패/없음: {total_tasks - success_count}")
    logger.info(f"   - 총 캔들: {total_candles:,}")
    logger.info("=" * 70)

# ============================================================
# 연도별 백필 함수
# ============================================================
def backfill_single_year(
    year: int,
    market_type: str = "futures",
    max_workers: int = 4
):
    """
    특정 연도만 백필
    
    Args:
        year: 연도 (예: 2024)
        market_type: 'spot' or 'futures'
        max_workers: 병렬 작업 수
    """
    logger.info(f"🎯 {year}년 데이터만 백필")
    
    backfill_historical_data_parallel(
        start_year=year,
        start_month=1,
        end_year=year,
        end_month=12,
        market_type=market_type,
        skip_recent=False,
        max_workers=max_workers
    )

# ============================================================
# 실행
# ============================================================
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Binance Vision 병렬 백필")
    
    # 기간 설정
    parser.add_argument("--start-year", type=int, default=2020, help="시작 연도")
    parser.add_argument("--start-month", type=int, default=1, help="시작 월")
    parser.add_argument("--end-year", type=int, help="종료 연도 (기본: 현재)")
    parser.add_argument("--end-month", type=int, help="종료 월 (기본: 현재)")
    
    # 단일 연도 모드
    parser.add_argument("--year", type=int, help="특정 연도만 (예: 2024)")
    
    # 심볼 선택 (NEW)
    parser.add_argument("--symbols", nargs='+', help="특정 심볼만 (예: BTCUSDT ETHUSDT XRPUSDT)")
    
    # 기타 옵션
    parser.add_argument("--market-type", choices=['spot', 'futures'], default='futures', help="마켓 타입")
    parser.add_argument("--skip-recent", action='store_true', help="최근 7일 이내 데이터 스킵")
    parser.add_argument("--workers", type=int, default=4, help="병렬 작업 수 (기본: 4)")
    
    args = parser.parse_args()
    
    # CPU 코어 수 확인
    cpu_cores = cpu_count()
    logger.info(f"""
설정:
- CPU 코어: {cpu_cores}
- 병렬 작업 수: {args.workers}
- 마켓 타입: {args.market_type}
- 최근 데이터 스킵: {args.skip_recent}
- DB: {CONFIG['db']['name']}@{CONFIG['db']['host']}
- 심볼 필터: {args.symbols if args.symbols else '전체'}
    """)
    
    # 실행
    if args.year:
        # 단일 연도 모드
        backfill_single_year(
            year=args.year,
            market_type=args.market_type,
            max_workers=args.workers
        )
    else:
        # 범위 모드
        logger.info(f"- 기간: {args.start_year}-{args.start_month:02d} ~ " + 
                   (f"{args.end_year}-{args.end_month:02d}" if args.end_year else "현재"))
        
        backfill_historical_data_parallel(
            start_year=args.start_year,
            start_month=args.start_month,
            end_year=args.end_year,
            end_month=args.end_month,
            market_type=args.market_type,
            skip_recent=args.skip_recent,
            max_workers=args.workers,
            filter_symbols=args.symbols  # 심볼 필터 전달
        )
