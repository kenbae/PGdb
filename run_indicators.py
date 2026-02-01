import yaml
import logging
import argparse
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# ta 라이브러리의 IndexError 회피를 위해 직접 ATR/RSI 구현(안정)
# (데이터가 짧으면 NaN 유지)
def calc_atr(high, low, close, window=14):
    """ATR (Average True Range) 계산"""
    try:
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
    except Exception as e:
        logger.error(f"ATR 계산 중 오류: {e}")
        return np.full(len(close), np.nan)


def calc_rsi(close, window=14):
    """RSI (Relative Strength Index) 계산"""
    try:
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
    except Exception as e:
        logger.error(f"RSI 계산 중 오류: {e}")
        return np.full(len(close), np.nan)


def load_config(path="config.yaml"):
    """설정 파일 로드"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            logger.info(f"설정 파일 로드 완료: {path}")
            return config
    except FileNotFoundError:
        logger.error(f"설정 파일을 찾을 수 없습니다: {path}")
        raise
    except yaml.YAMLError as e:
        logger.error(f"YAML 파싱 오류: {e}")
        raise


def get_engine(cfg):
    """SQLAlchemy engine 생성"""
    try:
        db = cfg['db']
        dsn = f"postgresql://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['name']}"
        engine = create_engine(dsn)
        # 연결 테스트
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("데이터베이스 연결 성공")
        return engine
    except KeyError as e:
        logger.error(f"설정 파일에 필수 항목이 없습니다: {e}")
        raise
    except SQLAlchemyError as e:
        logger.error(f"데이터베이스 연결 실패: {e}")
        raise


def get_symbols(engine, symbol_filter=None):
    """
    심볼 목록 조회
    
    Args:
        engine: SQLAlchemy engine
        symbol_filter: 특정 심볼만 처리하고 싶을 때 (예: 'BTCUSDT' 또는 ['BTCUSDT', 'ETHUSDT'])
    
    Returns:
        list: 심볼 목록
    """
    try:
        with engine.connect() as conn:
            if symbol_filter:
                # 단일 심볼인 경우 리스트로 변환
                if isinstance(symbol_filter, str):
                    symbol_filter = [symbol_filter]
                
                # IN 쿼리로 필터링
                placeholders = ','.join([f':symbol{i}' for i in range(len(symbol_filter))])
                query = f"SELECT DISTINCT symbol FROM candles WHERE symbol IN ({placeholders})"
                params = {f'symbol{i}': sym for i, sym in enumerate(symbol_filter)}
                result = conn.execute(text(query), params)
                symbols = [r[0] for r in result.fetchall()]
                
                # 요청한 심볼이 DB에 없는 경우 경고
                missing = set(symbol_filter) - set(symbols)
                if missing:
                    logger.warning(f"다음 심볼은 DB에 존재하지 않습니다: {missing}")
                
                return symbols
            else:
                # 모든 심볼 조회
                result = conn.execute(text("SELECT DISTINCT symbol FROM candles"))
                return [r[0] for r in result.fetchall()]
    except SQLAlchemyError as e:
        logger.error(f"심볼 조회 중 오류: {e}")
        raise


def get_last_indicator_time(engine, symbol, tf):
    """마지막 지표 계산 시간 조회"""
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT max(open_time) FROM indicators WHERE symbol=:symbol AND tf=:tf"),
                {"symbol": symbol, "tf": tf}
            )
            return result.fetchone()[0]
    except SQLAlchemyError as e:
        logger.error(f"마지막 지표 시간 조회 중 오류 ({symbol} {tf}): {e}")
        return None


def load_candles_incremental(engine, symbol, tf, since_time_utc, lookback_bars=250):
    """
    증분 계산용:
    - 마지막 indicator 시간 이후의 캔들만 가져오되,
    - EMA/ATR/RSI 안정화를 위해 lookback_bars만큼 과거도 함께 가져옴
    """
    try:
        if since_time_utc is None:
            # 최초 계산: 최근 lookback_bars + 여유 데이터
            query = """
                SELECT open_time, open, high, low, close, volume
                FROM candles
                WHERE symbol=:symbol AND tf=:tf
                ORDER BY open_time
            """
            df = pd.read_sql(text(query), engine, params={"symbol": symbol, "tf": tf})
            return df

        # since_time_utc 이전의 lookback_bars 구간을 확보하기 위해
        # since_time_utc 기준으로 과거 lookback_bars를 포함해 로드
        query = """
            WITH base AS (
                SELECT open_time, open, high, low, close, volume
                FROM candles
                WHERE symbol=:symbol AND tf=:tf AND open_time <= :since_time
                ORDER BY open_time DESC
                LIMIT :lookback
            )
            SELECT open_time, open, high, low, close, volume
            FROM (
                SELECT * FROM base
                UNION ALL
                SELECT open_time, open, high, low, close, volume
                FROM candles
                WHERE symbol=:symbol AND tf=:tf AND open_time > :since_time
            ) x
            ORDER BY open_time
        """
        df = pd.read_sql(
            text(query),
            engine,
            params={
                "symbol": symbol,
                "tf": tf,
                "since_time": since_time_utc,
                "lookback": lookback_bars
            }
        )
        return df
    except SQLAlchemyError as e:
        logger.error(f"캔들 데이터 로드 중 오류 ({symbol} {tf}): {e}")
        return pd.DataFrame()


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """기술적 지표 계산"""
    try:
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
    except Exception as e:
        logger.error(f"지표 계산 중 오류: {e}")
        return pd.DataFrame()


def upsert_indicators(engine, df, symbol, tf, only_newer_than_utc):
    """지표 데이터를 DB에 저장"""
    try:
        if df.empty:
            return 0

        # only_newer_than_utc 이후의 open_time만 적재(증분)
        if only_newer_than_utc is not None:
            df = df[df["open_time"] > pd.to_datetime(only_newer_than_utc, utc=True)]

        if df.empty:
            return 0

        rows = []
        for _, r in df.iterrows():
            rows.append({
                "symbol": symbol,
                "tf": tf,
                "open_time": r["open_time"].to_pydatetime(),  # tz-aware(UTC)
                "ema20": None if pd.isna(r["ema20"]) else float(r["ema20"]),
                "ema50": None if pd.isna(r["ema50"]) else float(r["ema50"]),
                "ema200": None if pd.isna(r["ema200"]) else float(r["ema200"]),
                "vwap": None if pd.isna(r["vwap"]) else float(r["vwap"]),
                "rsi": None if pd.isna(r["rsi"]) else float(r["rsi"]),
                "atr": None if pd.isna(r["atr"]) else float(r["atr"]),
            })

        sql = text("""
            INSERT INTO indicators
            (symbol, tf, open_time, ema20, ema50, ema200, vwap, rsi, atr)
            VALUES (:symbol, :tf, :open_time, :ema20, :ema50, :ema200, :vwap, :rsi, :atr)
            ON CONFLICT (symbol, tf, open_time) DO NOTHING
        """)

        with engine.connect() as conn:
            with conn.begin():
                for row in rows:
                    conn.execute(sql, row)
        
        return len(rows)
    except SQLAlchemyError as e:
        logger.error(f"지표 저장 중 오류 ({symbol} {tf}): {e}")
        return 0


def run_indicators(symbols_filter=None):
    """
    지표 계산 메인 함수
    
    Args:
        symbols_filter: 특정 심볼만 처리 (str 또는 list)
    """
    try:
        cfg = load_config()
        tfs = cfg["timeframes"]

        engine = get_engine(cfg)
        
        symbols = get_symbols(engine, symbols_filter)
        
        if not symbols:
            logger.warning("처리할 심볼이 없습니다.")
            return
        
        logger.info(f"처리할 심볼 수: {len(symbols)}")
        if symbols_filter:
            logger.info(f"필터링된 심볼: {symbols}")

        total = 0
        success_count = 0
        fail_count = 0

        for symbol in symbols:
            for tf in tfs:
                try:
                    last_ind = get_last_indicator_time(engine, symbol, tf)
                    # last_ind는 timestamptz. 계산은 UTC로 통일
                    since_utc = last_ind.astimezone(timezone.utc) if last_ind else None

                    df = load_candles_incremental(engine, symbol, tf, since_utc, lookback_bars=300)
                    if df.empty:
                        logger.debug(f"{symbol} {tf}: 캔들 데이터 없음")
                        continue

                    # 캔들이 너무 적으면(예: 1개) EMA/VWAP만 계산되고 ATR/RSI는 NaN으로 남음
                    df_ind = calculate_indicators(df)

                    inserted = upsert_indicators(engine, df_ind, symbol, tf, since_utc)
                    total += inserted
                    success_count += 1
                    logger.info(f"✓ {symbol} {tf}: {inserted}개 지표 저장")
                    
                except Exception as e:
                    fail_count += 1
                    logger.error(f"✗ {symbol} {tf} 처리 중 오류: {e}")
                    continue

        logger.info("=" * 60)
        logger.info(f"처리 완료 - 총 {total}개 지표 저장")
        logger.info(f"성공: {success_count}, 실패: {fail_count}")
        logger.info("=" * 60)
        
        engine.dispose()
        
    except Exception as e:
        logger.error(f"지표 계산 실행 중 치명적 오류: {e}")
        raise


def main():
    """CLI 인터페이스"""
    parser = argparse.ArgumentParser(
        description='암호화폐 기술적 지표 계산 스크립트',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 모든 심볼 처리
  python run_indicators.py
  
  # 특정 심볼만 처리
  python run_indicators.py --symbol BTCUSDT
  
  # 여러 심볼 처리
  python run_indicators.py --symbol BTCUSDT ETHUSDT BNBUSDT
        """
    )
    
    parser.add_argument(
        '--symbol', '-s',
        nargs='+',
        help='처리할 심볼 (예: BTCUSDT). 여러 개 지정 가능',
        metavar='SYMBOL'
    )
    
    parser.add_argument(
        '--config', '-c',
        default='config.yaml',
        help='설정 파일 경로 (기본값: config.yaml)',
        metavar='PATH'
    )
    
    args = parser.parse_args()
    
    # 설정 파일 경로 업데이트 (필요시)
    # 실제로는 load_config() 함수를 수정하여 경로를 받도록 해야 함
    
    try:
        if args.symbol:
            # 단일 심볼인 경우 문자열로, 다중인 경우 리스트로
            symbols = args.symbol[0] if len(args.symbol) == 1 else args.symbol
            logger.info(f"지정된 심볼 처리: {symbols}")
            run_indicators(symbols_filter=symbols)
        else:
            logger.info("모든 심볼 처리")
            run_indicators()
    except KeyboardInterrupt:
        logger.info("사용자에 의해 중단되었습니다.")
    except Exception as e:
        logger.error(f"실행 실패: {e}")
        exit(1)


if __name__ == "__main__":
    main()
