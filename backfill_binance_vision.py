# -*- coding: utf-8 -*-
"""
Binance Vision 데이터를 DB에 채우기

DB에 있는 종목들의 과거 데이터를 Binance Vision에서 다운로드하여 채웁니다.
"""

import requests
import zipfile
import pandas as pd
import psycopg2
from datetime import datetime, timedelta, timezone
import yaml
import os
from typing import List, Tuple
import time

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
def get_symbols_from_db() -> List[Tuple[str, str]]:
    """
    DB에서 종목 목록 가져오기 (1분봉, 5분봉 제외)
    
    Returns:
        List of (symbol, timeframe) tuples
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # candles 테이블에서 고유한 symbol, tf 조합 가져오기 (1분봉, 5분봉 제외)
        query = """
            SELECT DISTINCT symbol, tf 
            FROM candles 
            WHERE tf NOT IN ('1m', '5m')
            ORDER BY symbol, tf
        """
        
        cur.execute(query)
        symbols = cur.fetchall()
        
        cur.close()
        return symbols
        
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass
    
    print(f"  ℹ️  1분봉, 5분봉 제외됨 (1m, 5m 타임프레임 스킵)")
    
    return symbols

# ============================================================
# DB에서 마지막 데이터 시간 확인
# ============================================================
def get_last_timestamp(symbol: str, timeframe: str) -> datetime:
    """
    특정 종목의 마지막 데이터 시간 가져오기
    
    Returns:
        마지막 타임스탬프 또는 None (데이터 없음)
    """
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
            try:
                conn.close()
            except:
                pass

# ============================================================
# Binance Vision 다운로드
# ============================================================
def download_binance_vision_monthly(
    symbol: str, 
    timeframe: str, 
    year: int, 
    month: int,
    market_type: str = "futures"  # "spot" or "futures"
) -> pd.DataFrame:
    """
    Binance Vision에서 월별 데이터 다운로드
    
    Args:
        symbol: 'BTCUSDT' (슬래시 없음)
        timeframe: '1m', '5m', '15m', '30m', '1h', '4h', '1d'
        year: 2024
        month: 1
        market_type: 'spot' or 'futures'
    
    Returns:
        DataFrame or None
    """
    # URL 생성
    if market_type == "futures":
        base_url = "https://data.binance.vision/data/futures/um/monthly/klines"
    else:
        base_url = "https://data.binance.vision/data/spot/monthly/klines"
    
    filename = f"{symbol}-{timeframe}-{year}-{month:02d}.zip"
    url = f"{base_url}/{symbol}/{timeframe}/{filename}"
    
    print(f"  Downloading: {filename}")
    
    try:
        response = requests.get(url, timeout=30)
        
        if response.status_code == 404:
            print(f"  [INFO] No data (coin not listed or no data for this month)")
            return None
        elif response.status_code != 200:
            print(f"  [FAIL] HTTP {response.status_code}")
            return None
        
        # 임시 파일로 저장
        temp_zip = f"temp_{filename}"
        with open(temp_zip, 'wb') as f:
            f.write(response.content)
        
        # 압축 해제
        with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
            zip_ref.extractall('.')
        
        # CSV 읽기
        csv_filename = filename.replace('.zip', '.csv')
        
        # 첫 줄이 헤더인지 확인
        with open(csv_filename, 'r') as f:
            first_line = f.readline()
            # 'open_time'이라는 문자열이 있으면 헤더 있음
            has_header = 'open_time' in first_line.lower()
        
        if has_header:
            # 헤더가 있으면 skiprows=1
            df = pd.read_csv(csv_filename, skiprows=1, header=None)
        else:
            # 헤더가 없으면 그대로 읽기
            df = pd.read_csv(csv_filename, header=None)
        
        # 컬럼명 지정
        df.columns = [
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ]
        
        # 불필요한 컬럼 제거
        df = df[['open_time', 'open', 'high', 'low', 'close', 'volume']]
        
        # 타임스탬프 변환 (이미 숫자형인지 확인)
        df['open_time'] = pd.to_numeric(df['open_time'], errors='coerce')
        df = df.dropna(subset=['open_time'])  # 변환 실패한 행 제거
        
        if len(df) == 0:
            print(f"  [INFO] No data (empty file)")
            os.remove(temp_zip)
            os.remove(csv_filename)
            return None
        
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        
        # 임시 파일 삭제
        os.remove(temp_zip)
        os.remove(csv_filename)
        
        print(f"  [OK] Downloaded: {len(df)} candles")
        return df
        
    except Exception as e:
        print(f"  [ERROR] {e}")
        return None

# ============================================================
# DB에 데이터 삽입
# ============================================================
def insert_candles_to_db(df: pd.DataFrame, symbol: str, timeframe: str, exchange: str = "binance", market: str = "usdtm"):
    """
    DataFrame을 DB에 삽입 (배치 처리)
    
    Args:
        df: OHLCV DataFrame
        symbol: 'BTCUSDT'
        timeframe: '1h'
        exchange: 'binance' (기본값)
        market: 'usdtm' (기본값)
    """
    conn = None
    try:
        conn = get_db_connection()
        
        # INSERT 쿼리 with ON CONFLICT (UNIQUE 제약조건 있음: candles_unique_key)
        query = """
            INSERT INTO candles (exchange, symbol, market, tf, open_time, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (exchange, symbol, market, tf, open_time) DO NOTHING
        """
        
        # 데이터 준비
        values_list = []
        
        for _, row in df.iterrows():
            try:
                values = (
                    exchange,      # 1. exchange
                    symbol,        # 2. symbol
                    market,        # 3. market
                    timeframe,     # 4. tf
                    row['open_time'],  # 5. open_time
                    float(row['open']),    # 6. open
                    float(row['high']),    # 7. high
                    float(row['low']),     # 8. low
                    float(row['close']),   # 9. close
                    float(row['volume'])   # 10. volume
                )
                values_list.append(values)
            except Exception as e:
                print(f"  ⚠️  Row conversion error: {e}")
                continue
        
        if not values_list:
            print(f"  ℹ️  삽입할 데이터 없음")
            return
        
        # with 구문으로 cursor 자동 정리
        with conn.cursor() as cur:
            # 배치 삽입
            try:
                from psycopg2.extras import execute_batch
                
                # 배치 실행
                execute_batch(cur, query, values_list, page_size=1000)
                conn.commit()
                
                print(f"  [DB] Batch inserted: {len(values_list)} rows processed (duplicates auto-skipped)")
                
            except Exception as e:
                print(f"  ⚠️  Batch insert error: {e}")
                conn.rollback()
                
                # 배치 실패 시 개별 삽입으로 폴백
                print(f"  ℹ️  Falling back to row-by-row insert...")
                
                inserted = 0
                skipped = 0
                
                for values in values_list:
                    try:
                        cur.execute(query, values)
                        if cur.rowcount > 0:
                            inserted += 1
                        else:
                            skipped += 1
                    except Exception as row_error:
                        # 중복 에러는 무시
                        error_msg = str(row_error)
                        if '중복된 키' in error_msg or 'duplicate key' in error_msg.lower():
                            skipped += 1
                        else:
                            # 다른 에러는 첫 번째만 출력
                            if inserted == 0 and skipped == 0:
                                print(f"  ⚠️  Row insert error: {row_error}")
                
                conn.commit()
                print(f"  [DB] Inserted: {inserted} new candles (skipped {skipped} duplicates)")
    
    except Exception as e:
        print(f"  [ERROR] DB insert error: {e}")
        if conn:
            try:
                conn.rollback()
            except:
                pass
    
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

# ============================================================
# 메인 함수
# ============================================================
def backfill_historical_data(
    start_year: int = 2020,
    start_month: int = 1,
    market_type: str = "futures",
    skip_recent: bool = False,  # 최근 데이터 있으면 스킵 여부
    timeframes: List[str] = None,  # 특정 타임프레임만 처리 (예: ['15m', '1h'])
    symbols: List[str] = None  # 특정 심볼만 처리 (예: ['BTCUSDT', 'ETHUSDT'])
):
    """
    DB에 있는 모든 종목의 과거 데이터를 Binance Vision에서 다운로드하여 채우기

    Args:
        start_year: 시작 연도 (기본: 2020)
        start_month: 시작 월 (기본: 1)
        market_type: 'spot' or 'futures' (기본: 'futures')
        skip_recent: 최근 7일 이내 데이터 있으면 스킵 (기본: False)
        timeframes: 특정 타임프레임만 처리 (None이면 전체)
        symbols: 특정 심볼만 처리 (None이면 전체)
    """
    print("=" * 60)
    print("Binance Vision Historical Data Backfill")
    print("=" * 60)

    # 1. DB에서 종목 목록 가져오기
    print("\n[1] DB에서 종목 목록 가져오기...")
    symbols_tf = get_symbols_from_db()

    # 심볼 필터 적용
    if symbols:
        original_count = len(symbols_tf)
        # 심볼 이름 정규화 (대문자로)
        symbols_upper = [s.upper() for s in symbols]
        symbols_tf = [(s, tf) for s, tf in symbols_tf if s.upper() in symbols_upper]
        print(f"  [FILTER] Symbol filter: {symbols}")
        print(f"  [INFO] {original_count} -> {len(symbols_tf)} (filtered by symbol)")

    # 타임프레임 필터 적용
    if timeframes:
        original_count = len(symbols_tf)
        symbols_tf = [(s, tf) for s, tf in symbols_tf if tf in timeframes]
        print(f"  [FILTER] Timeframe filter: {timeframes}")
        print(f"  [INFO] {original_count} -> {len(symbols_tf)} (filtered by tf)")
    print(f"[OK] Total {len(symbols_tf)} symbol-tf pairs found")

    if not symbols_tf:
        print("[ERROR] No symbols in DB.")
        return
    
    # 종목별로 처리
    for symbol, timeframe in symbols_tf:
        print(f"\n{'=' * 60}")
        print(f"종목: {symbol} | TF: {timeframe}")
        print(f"{'=' * 60}")
        
        # 심볼 변환 (BTC/USDT:USDT → BTCUSDT)
        clean_symbol = symbol.replace('/', '').replace(':', '')
        
        # 2. 마지막 데이터 시간 확인
        last_timestamp = get_last_timestamp(symbol, timeframe)
        
        if last_timestamp:
            print(f"[DB] Last data: {last_timestamp}")
            
            # skip_recent 플래그가 True일 때만 최근 데이터 체크
            if skip_recent:
                # timezone-aware 비교를 위해 UTC로 변환
                if last_timestamp.tzinfo is None:
                    last_timestamp = last_timestamp.replace(tzinfo=timezone.utc)
                
                now_utc = datetime.now(timezone.utc)
                
                # 마지막 데이터가 최근 7일 이내면 스킵
                if last_timestamp > now_utc - timedelta(days=7):
                    print("[SKIP] Recent data already exists.")
                    continue
        else:
            print("[DB] No data in DB -> full download")
        
        # 3. 현재 시점부터 start_year까지 역순으로 다운로드
        current_date = datetime.now()
        start_date = datetime(start_year, start_month, 1)
        
        total_downloaded = 0
        
        # 월별로 루프
        year = current_date.year
        month = current_date.month
        
        while True:
            # 시작 날짜 이전이면 종료
            if datetime(year, month, 1) < start_date:
                break
            
            # 미래 날짜는 건너뛰기
            if datetime(year, month, 1) > current_date:
                print(f"\n  [DATE] {year}-{month:02d} (future date - skip)")
                month -= 1
                if month < 1:
                    month = 12
                    year -= 1
                continue
            
            print(f"\n  [DATE] {year}-{month:02d}")
            
            # Binance Vision에서 다운로드
            df = download_binance_vision_monthly(
                clean_symbol, 
                timeframe, 
                year, 
                month,
                market_type
            )
            
            if df is not None and not df.empty:
                # DB에 삽입 (exchange='binance', market='usdtm' 추가)
                insert_candles_to_db(df, symbol, timeframe, exchange='binance', market='usdtm')
                total_downloaded += len(df)
            else:
                # 404 또는 에러 - 경고만 출력하고 계속
                pass
            
            # 다음 월로 이동 (역순)
            month -= 1
            if month < 1:
                month = 12
                year -= 1
            
            # Rate Limit 완화
            time.sleep(0.5)
        
        print(f"\n[DONE] {symbol} {timeframe}: {total_downloaded} candles downloaded")
    
    print("\n" + "=" * 60)
    print("[DONE] All symbols backfill complete!")
    print("=" * 60)

# ============================================================
# 실행
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Binance Vision 데이터를 DB에 채우기")
    parser.add_argument("--start-year", type=int, default=2020, help="시작 연도 (기본: 2020)")
    parser.add_argument("--start-month", type=int, default=1, help="시작 월 (기본: 1)")
    parser.add_argument("--market-type", choices=['spot', 'futures'], default='futures', help="마켓 타입")
    parser.add_argument("--skip-recent", action='store_true', help="최근 7일 이내 데이터 있으면 스킵")
    parser.add_argument("--timeframes", type=str, default=None,
                        help="타임프레임 필터 (쉼표 구분, 예: 15m,1h,4h)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="심볼 필터 (쉼표 구분, 예: BTCUSDT,ETHUSDT)")

    args = parser.parse_args()

    # 타임프레임 파싱
    tf_list = None
    if args.timeframes:
        tf_list = [tf.strip() for tf in args.timeframes.split(',')]

    # 심볼 파싱
    symbol_list = None
    if args.symbols:
        symbol_list = [s.strip().upper() for s in args.symbols.split(',')]

    print(f"""
설정:
- 시작 날짜: {args.start_year}-{args.start_month:02d}
- 마켓 타입: {args.market_type}
- 최근 데이터 스킵: {args.skip_recent}
- 타임프레임 필터: {tf_list if tf_list else '전체'}
- 심볼 필터: {symbol_list if symbol_list else '전체'}
- DB: {CONFIG['db']['name']}@{CONFIG['db']['host']}
    """)

    # 실행
    backfill_historical_data(
        start_year=args.start_year,
        start_month=args.start_month,
        market_type=args.market_type,
        skip_recent=args.skip_recent,
        timeframes=tf_list,
        symbols=symbol_list
    )
