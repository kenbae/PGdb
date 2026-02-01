import json
import yaml
import logging
import argparse
import pandas as pd
import numpy as np
from datetime import timezone
from typing import Optional, Dict, List, Tuple, Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


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


def get_last_signal_time(engine, symbol: str, tf: str, strategy: str) -> Optional[Any]:
    """마지막 신호 생성 시간 조회"""
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT max(open_time)
                    FROM signals
                    WHERE symbol=:symbol AND tf=:tf AND strategy=:strategy
                """),
                {"symbol": symbol, "tf": tf, "strategy": strategy}
            )
            return result.fetchone()[0]
    except SQLAlchemyError as e:
        logger.error(f"마지막 신호 시간 조회 중 오류 ({symbol} {tf}): {e}")
        return None


def load_indicators(engine, symbol: str, tf: str, since_time_utc=None, lookback_bars=300) -> pd.DataFrame:
    """
    indicators 테이블에서 신호 탐지에 필요한 컬럼만 로드.
    증분 실행을 위해 since_time_utc 이후만 보되, 직전봉 비교를 위해 과거 lookback 포함.
    """
    try:
        if since_time_utc is None:
            q = text("""
                SELECT open_time, ema20, ema50, ema200, vwap, rsi, atr
                FROM indicators
                WHERE symbol=:symbol AND tf=:tf
                ORDER BY open_time
            """)
            df = pd.read_sql(q, engine, params={"symbol": symbol, "tf": tf})
            return df

        q = text("""
            WITH base AS (
                SELECT open_time, ema20, ema50, ema200, vwap, rsi, atr
                FROM indicators
                WHERE symbol=:symbol AND tf=:tf AND open_time <= :since_time
                ORDER BY open_time DESC
                LIMIT :lookback
            )
            SELECT open_time, ema20, ema50, ema200, vwap, rsi, atr
            FROM (
                SELECT * FROM base
                UNION ALL
                SELECT open_time, ema20, ema50, ema200, vwap, rsi, atr
                FROM indicators
                WHERE symbol=:symbol2 AND tf=:tf2 AND open_time > :since_time2
            ) x
            ORDER BY open_time
        """)
        df = pd.read_sql(
            q,
            engine,
            params={
                "symbol": symbol, 
                "tf": tf, 
                "since_time": since_time_utc, 
                "lookback": lookback_bars,
                "symbol2": symbol,
                "tf2": tf,
                "since_time2": since_time_utc
            }
        )
        return df
    except SQLAlchemyError as e:
        logger.error(f"지표 데이터 로드 중 오류 ({symbol} {tf}): {e}")
        return pd.DataFrame()


def load_closes(engine, symbol: str, tf: str, open_times: List) -> Dict:
    """특정 open_time들의 close 가격 조회"""
    if not open_times:
        return {}

    try:
        # PostgreSQL의 ANY를 사용하기 위해 ARRAY 생성
        q = text("""
            SELECT open_time, close
            FROM candles
            WHERE symbol=:symbol
              AND tf=:tf
              AND open_time = ANY(:open_times)
        """)

        with engine.connect() as conn:
            result = conn.execute(q, {"symbol": symbol, "tf": tf, "open_times": list(open_times)})
            rows = result.fetchall()

        return {r[0]: float(r[1]) for r in rows}
    except SQLAlchemyError as e:
        logger.error(f"Close 가격 조회 중 오류 ({symbol} {tf}): {e}")
        return {}


def get_htf_regime(engine, symbol: str, tf: str, at_time) -> Optional[str]:
    """
    HTF에서 at_time 이전(<=) 가장 최근 인디케이터로 레짐 판단.
    bull: ema50 > ema200
    bear: ema50 < ema200
    flat: 그 외
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT ema50, ema200
                    FROM indicators
                    WHERE symbol=:symbol AND tf=:tf AND open_time <= :at_time
                    ORDER BY open_time DESC
                    LIMIT 1
                """),
                {"symbol": symbol, "tf": tf, "at_time": at_time}
            )
            row = result.fetchone()

        if not row or row[0] is None or row[1] is None:
            return None

        ema50, ema200 = float(row[0]), float(row[1])
        if ema50 > ema200:
            return "bull"
        if ema50 < ema200:
            return "bear"
        return "flat"
    except SQLAlchemyError as e:
        logger.error(f"HTF 레짐 조회 중 오류 ({symbol} {tf} at {at_time}): {e}")
        return None


def score_signal(direction: str, row: pd.Series, htf4h: Optional[str], htf1d: Optional[str]) -> Tuple[int, Dict]:
    """
    신호 점수 계산
    
    Args:
        direction: 'long' or 'short'
        row: indicators row (ema20/50/200, vwap, rsi, atr)
        htf4h: 4h 타임프레임 레짐
        htf1d: 1d 타임프레임 레짐
    
    Returns:
        (score, reasons): 점수와 상세 사유
    """
    score = 0
    reasons = {}

    ema20 = row["ema20"]
    ema50 = row["ema50"]
    ema200 = row["ema200"]
    vwap = row["vwap"]
    rsi = row["rsi"]

    # 정배열/역배열은 이미 통과한 상태에서 호출되는 걸 전제로 하지만,
    # 점수에도 반영
    score += 40
    reasons["ema_alignment"] = True

    # HTF 일치
    htf_ok = False
    if direction == "long":
        htf_ok = (htf4h == "bull") or (htf1d == "bull")
    else:
        htf_ok = (htf4h == "bear") or (htf1d == "bear")

    if htf_ok:
        score += 30
    reasons["htf_4h"] = htf4h
    reasons["htf_1d"] = htf1d

    # RSI
    if rsi is not None and not np.isnan(rsi):
        if direction == "long" and rsi < 70:
            score += 10
            reasons["rsi_ok"] = True
            reasons["rsi_value"] = float(rsi)
        elif direction == "short" and rsi > 30:
            score += 10
            reasons["rsi_ok"] = True
            reasons["rsi_value"] = float(rsi)
        else:
            reasons["rsi_ok"] = False
            reasons["rsi_value"] = float(rsi)
    else:
        reasons["rsi_ok"] = False
        reasons["rsi_value"] = None

    # VWAP 위치
    if vwap is not None and not np.isnan(vwap):
        reasons["vwap"] = float(vwap)
    else:
        reasons["vwap"] = None

    return score, reasons


def calc_plan(entry: float, atr: float, direction: str, k: float = 1.2) -> Optional[Tuple[float, float, float]]:
    """
    ATR 기반 SL/TP 계산
    
    Args:
        entry: 진입 가격
        atr: Average True Range
        direction: 'long' or 'short'
        k: ATR 배수 (기본 1.2)
    
    Returns:
        (sl, tp1, tp2) 또는 None
    """
    try:
        if atr is None or np.isnan(atr) or atr <= 0:
            return None

        risk = k * float(atr)

        if direction == "long":
            sl = entry - risk
            tp1 = entry + risk * 1.0
            tp2 = entry + risk * 2.0
        else:
            sl = entry + risk
            tp1 = entry - risk * 1.0
            tp2 = entry - risk * 2.0

        return sl, tp1, tp2
    except Exception as e:
        logger.error(f"손절/익절 계산 중 오류: {e}")
        return None


def insert_signal(
    engine, 
    symbol: str, 
    tf: str, 
    open_time, 
    strategy: str, 
    direction: str, 
    entry: float, 
    sl: float, 
    tp1: float, 
    tp2: float, 
    score: int, 
    reason_json: Dict
) -> bool:
    """신호를 DB에 저장"""
    try:
        sql = text("""
            INSERT INTO signals
            (symbol, tf, open_time, strategy, direction, entry, stop_loss, take_profit_1, take_profit_2, score, reason_json)
            VALUES (:symbol, :tf, :open_time, :strategy, :direction, :entry, :stop_loss, :take_profit_1, :take_profit_2, :score, CAST(:reason_json AS jsonb))
            ON CONFLICT (symbol, tf, open_time, strategy, direction) DO NOTHING
        """)
        
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(
                    sql,
                    {
                        "symbol": symbol,
                        "tf": tf,
                        "open_time": open_time,
                        "strategy": strategy,
                        "direction": direction,
                        "entry": entry,
                        "stop_loss": sl,
                        "take_profit_1": tp1,
                        "take_profit_2": tp2,
                        "score": score,
                        "reason_json": json.dumps(reason_json),
                    }
                )
        return True
    except SQLAlchemyError as e:
        logger.error(f"신호 저장 중 오류 ({symbol} {tf} {open_time}): {e}")
        return False


def process_symbol(
    engine, 
    symbol: str, 
    ltf: str, 
    strategy: str, 
    score_threshold: int,
    atr_multiplier: float
) -> int:
    """
    단일 심볼의 신호 생성 처리
    
    Returns:
        생성된 신호 개수
    """
    try:
        last_sig = get_last_signal_time(engine, symbol, ltf, strategy)
        since_utc = last_sig.astimezone(timezone.utc) if last_sig else None

        df = load_indicators(engine, symbol, ltf, since_time_utc=since_utc, lookback_bars=400)
        if df.empty or len(df) < 2:
            logger.debug(f"{symbol}: 지표 데이터 부족")
            return 0

        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        df = df.sort_values("open_time").reset_index(drop=True)

        # 크로스 판정 (직전봉 vs 현재봉)
        prev = df.shift(1)

        golden = (prev["ema20"] <= prev["ema50"]) & (df["ema20"] > df["ema50"])
        dead = (prev["ema20"] >= prev["ema50"]) & (df["ema20"] < df["ema50"])

        # 정배열/역배열 필터 (현재봉 기준)
        bull_align = (df["ema20"] > df["ema50"]) & (df["ema50"] > df["ema200"])
        bear_align = (df["ema20"] < df["ema50"]) & (df["ema50"] < df["ema200"])

        signal_mask = (golden & bull_align) | (dead & bear_align)

        sig_rows = df[signal_mask].copy()
        if sig_rows.empty:
            logger.debug(f"{symbol}: 신호 없음")
            return 0

        # open_time 리스트로 close 매칭
        close_map = load_closes(engine, symbol, ltf, sig_rows["open_time"].to_list())

        new_signals = 0

        for _, r in sig_rows.iterrows():
            ot = r["open_time"].to_pydatetime()

            # entry = close
            entry = close_map.get(ot)
            if entry is None:
                # candles에 없으면 패스
                logger.debug(f"{symbol} {ot}: close 가격 없음")
                continue

            # 방향 결정
            direction = "long" if (r["ema20"] > r["ema50"]) else "short"

            # HTF 레짐 조회(4h/1d)
            htf4h = get_htf_regime(engine, symbol, "4h", ot)
            htf1d = get_htf_regime(engine, symbol, "1d", ot)

            score, reasons = score_signal(direction, r, htf4h, htf1d)

            # VWAP 위치 조건 점수(+10)
            vwap = r["vwap"]
            if vwap is not None and not np.isnan(vwap):
                if direction == "long" and entry > float(vwap):
                    score += 10
                    reasons["vwap_position"] = "above"
                elif direction == "short" and entry < float(vwap):
                    score += 10
                    reasons["vwap_position"] = "below"
                else:
                    reasons["vwap_position"] = "neutral"
            else:
                reasons["vwap_position"] = None

            # HTF 필수(최소 1개 일치) 강제: 안 맞으면 컷
            htf_ok = (htf4h == ("bull" if direction == "long" else "bear")) or \
                     (htf1d == ("bull" if direction == "long" else "bear"))
            if not htf_ok:
                logger.debug(f"{symbol} {ot}: HTF 불일치")
                continue

            # ATR 기반 플랜
            plan = calc_plan(entry, r["atr"], direction, k=atr_multiplier)
            if plan is None:
                logger.debug(f"{symbol} {ot}: ATR 기반 계획 생성 실패")
                continue
            sl, tp1, tp2 = plan

            # 최종 컷
            if score < score_threshold:
                logger.debug(f"{symbol} {ot}: 점수 부족 ({score} < {score_threshold})")
                continue

            # Risk-Reward Ratio 계산
            risk = abs(entry - sl)
            reward = abs(tp2 - entry)
            rr_ratio = reward / risk if risk > 0 else 0

            reasons.update({
                "entry": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "score": score,
                "ltf": ltf,
                "strategy": strategy,
                "risk_reward_ratio": round(rr_ratio, 2),
                "confidence": round(score / 100, 2),
            })

            if insert_signal(engine, symbol, ltf, ot, strategy, direction, entry, sl, tp1, tp2, score, reasons):
                new_signals += 1
                logger.info(
                    f"✓ {symbol} {ot.strftime('%Y-%m-%d %H:%M')} - "
                    f"{direction.upper()} | Score: {score} | Entry: {entry:.2f} | RR: {rr_ratio:.2f}"
                )

        return new_signals

    except Exception as e:
        logger.error(f"{symbol} 처리 중 오류: {e}")
        return 0


def run_signals(symbols_filter=None, config_path="config.yaml"):
    """
    신호 생성 메인 함수
    
    Args:
        symbols_filter: 특정 심볼만 처리 (str 또는 list)
        config_path: 설정 파일 경로
    """
    engine = None
    try:
        cfg = load_config(config_path)
        
        # 설정에서 신호 관련 파라미터 로드 (없으면 기본값 사용)
        signal_cfg = cfg.get("signals", {})
        ltf = signal_cfg.get("ltf", "30m")
        strategy = signal_cfg.get("strategy", "EMA20_50_CROSS_ALIGNED_HTF")
        score_threshold = signal_cfg.get("score_threshold", 70)
        atr_multiplier = signal_cfg.get("atr_multiplier", 1.2)

        logger.info(f"신호 생성 설정 - LTF: {ltf}, 전략: {strategy}, 임계값: {score_threshold}, ATR 배수: {atr_multiplier}")

        engine = get_engine(cfg)
        
        symbols = get_symbols(engine, symbols_filter)
        
        if not symbols:
            logger.warning("처리할 심볼이 없습니다.")
            return
        
        logger.info(f"처리할 심볼 수: {len(symbols)}")
        if symbols_filter:
            logger.info(f"필터링된 심볼: {symbols}")

        total_new = 0
        success_count = 0
        fail_count = 0

        for symbol in symbols:
            try:
                new_signals = process_symbol(
                    engine, 
                    symbol, 
                    ltf, 
                    strategy, 
                    score_threshold,
                    atr_multiplier
                )
                total_new += new_signals
                
                if new_signals > 0:
                    success_count += 1
                    logger.info(f"✓ {symbol}: {new_signals}개 신호 생성")
                else:
                    logger.debug(f"○ {symbol}: 신호 없음")
                
            except Exception as e:
                fail_count += 1
                logger.error(f"✗ {symbol} 처리 실패: {e}")
                continue

        logger.info("=" * 60)
        logger.info(f"처리 완료 - 총 {total_new}개 신호 생성")
        logger.info(f"성공: {success_count}, 실패: {fail_count}")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"신호 생성 실행 중 치명적 오류: {e}")
        raise
    finally:
        if engine:
            engine.dispose()
            logger.info("데이터베이스 연결 종료")


def main():
    """CLI 인터페이스"""
    parser = argparse.ArgumentParser(
        description='암호화폐 트레이딩 신호 생성 스크립트',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 모든 심볼 처리
  python run_signals.py
  
  # 특정 심볼만 처리
  python run_signals.py --symbol BTCUSDT
  
  # 여러 심볼 처리
  python run_signals.py --symbol BTCUSDT ETHUSDT BNBUSDT
  
  # 설정 파일 지정
  python run_signals.py --config custom_config.yaml

설정 파일 예시 (config.yaml):
  signals:
    ltf: "30m"
    strategy: "EMA20_50_CROSS_ALIGNED_HTF"
    score_threshold: 70
    atr_multiplier: 1.2
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
    
    parser.add_argument(
        '--debug', '-d',
        action='store_true',
        help='디버그 모드 활성화 (상세 로그 출력)'
    )
    
    args = parser.parse_args()
    
    # 디버그 모드 설정
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("디버그 모드 활성화")
    
    try:
        if args.symbol:
            # 단일 심볼인 경우 문자열로, 다중인 경우 리스트로
            symbols = args.symbol[0] if len(args.symbol) == 1 else args.symbol
            logger.info(f"지정된 심볼 처리: {symbols}")
            run_signals(symbols_filter=symbols, config_path=args.config)
        else:
            logger.info("모든 심볼 처리")
            run_signals(config_path=args.config)
    except KeyboardInterrupt:
        logger.info("사용자에 의해 중단되었습니다.")
    except Exception as e:
        logger.error(f"실행 실패: {e}")
        exit(1)


if __name__ == "__main__":
    main()
