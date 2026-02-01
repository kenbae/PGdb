import yaml
import logging
import argparse
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Tuple

import pandas as pd
import numpy as np
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


def tf_to_minutes(tf: str) -> int:
    """타임프레임을 분 단위로 변환"""
    try:
        tf = tf.strip().lower()
        if tf.endswith("m"):
            return int(tf[:-1])
        if tf.endswith("h"):
            return int(tf[:-1]) * 60
        if tf.endswith("d"):
            return int(tf[:-1]) * 1440
        raise ValueError(f"Unsupported tf: {tf}")
    except Exception as e:
        logger.error(f"타임프레임 변환 오류 ({tf}): {e}")
        raise


def compute_r_multiple(direction: str, entry: float, sl: float, exit_price: float) -> float:
    """
    R-Multiple 계산 (위험 대비 수익 비율)
    
    Args:
        direction: 'long' or 'short'
        entry: 진입 가격
        sl: 손절 가격
        exit_price: 청산 가격
    
    Returns:
        R-Multiple (예: 2.0 = 위험의 2배 수익)
    """
    try:
        if direction == "long":
            risk = entry - sl
            if risk <= 0:
                return 0.0
            return (exit_price - entry) / risk
        else:  # short
            risk = sl - entry
            if risk <= 0:
                return 0.0
            return (entry - exit_price) / risk
    except Exception as e:
        logger.error(f"R-Multiple 계산 오류: {e}")
        return 0.0


def load_candles_for_signal(engine, symbol: str, tf: str, start_time, end_time) -> List[Tuple]:
    """
    신호 평가를 위한 캔들 데이터 로드
    
    Args:
        engine: SQLAlchemy engine
        symbol: 심볼
        tf: 타임프레임
        start_time: 시작 시간 (신호 발생 시간 다음)
        end_time: 종료 시간 (horizon까지)
    
    Returns:
        캔들 데이터 리스트 [(open_time, open, high, low, close), ...]
    """
    try:
        query = text("""
            SELECT open_time, open, high, low, close
            FROM candles
            WHERE symbol = :symbol AND tf = :tf
              AND open_time > :start_time
              AND open_time <= :end_time
            ORDER BY open_time ASC
        """)
        
        with engine.connect() as conn:
            result = conn.execute(
                query,
                {
                    "symbol": symbol,
                    "tf": tf,
                    "start_time": start_time,
                    "end_time": end_time
                }
            )
            rows = result.fetchall()
        
        return rows
    except SQLAlchemyError as e:
        logger.error(f"캔들 데이터 로드 중 오류 ({symbol} {tf}): {e}")
        return []


def label_signal(
    engine, 
    sig: dict, 
    horizon_bars: int, 
    tie_breaker: str,
    slippage_pct: float = 0.0
) -> Optional[Dict]:
    """
    신호의 결과를 평가
    
    Args:
        engine: SQLAlchemy engine
        sig: 신호 정보 dict
        horizon_bars: 평가 기간 (캔들 개수)
        tie_breaker: 동시 터치 시 우선순위 ('sl_first' or 'tp_first')
        slippage_pct: 슬리피지 (%) - 실제 거래 비용
    
    Returns:
        결과 dict 또는 None
    """
    try:
        symbol = sig["symbol"]
        tf = sig["tf"]
        direction = sig["direction"]
        open_time = sig["open_time"]
        entry = float(sig["entry"])
        sl = float(sig["stop_loss"])
        tp1 = float(sig["take_profit_1"]) if sig["take_profit_1"] is not None else None
        tp2 = float(sig["take_profit_2"]) if sig["take_profit_2"] is not None else None

        # 슬리피지 적용
        slippage_multiplier = 1 + (slippage_pct / 100.0)
        if direction == "long":
            entry = entry * slippage_multiplier  # 진입 시 불리하게
        else:
            entry = entry / slippage_multiplier  # 진입 시 불리하게

        # 평가 구간 종료 시간 계산
        minutes = tf_to_minutes(tf)
        end_time = open_time + timedelta(minutes=minutes * horizon_bars)

        # 캔들 로드
        rows = load_candles_for_signal(engine, symbol, tf, open_time, end_time)

        if not rows:
            logger.debug(f"{symbol} {open_time}: 캔들 데이터 부족")
            return None

        # MFE/MAE 계산용
        mfe = 0.0
        mae = 0.0

        exit_time = None
        result = "none"
        exit_price = None

        # 각 캔들을 순회하며 SL/TP 도달 여부 확인
        for (ot, o, h, l, c) in rows:
            h = float(h)
            l = float(l)
            c = float(c)

            if direction == "long":
                # MFE/MAE 업데이트
                mfe = max(mfe, h - entry)
                mae = max(mae, entry - l)

                hit_sl = l <= sl
                hit_tp2 = (tp2 is not None) and (h >= tp2)
                hit_tp1 = (tp1 is not None) and (h >= tp1)

                # 같은 캔들에서 동시 터치 처리
                if hit_sl and (hit_tp2 or hit_tp1):
                    if tie_breaker == "tp_first":
                        if hit_tp2:
                            result, exit_price = "tp2", tp2
                        else:
                            result, exit_price = "tp1", tp1
                    else:  # sl_first
                        result, exit_price = "sl", sl
                    exit_time = ot
                    break

                # 개별 터치 확인
                if hit_sl:
                    result, exit_price, exit_time = "sl", sl, ot
                    break
                if hit_tp2:
                    result, exit_price, exit_time = "tp2", tp2, ot
                    break
                if hit_tp1:
                    result, exit_price, exit_time = "tp1", tp1, ot
                    break

            else:  # short
                # MFE/MAE 업데이트
                mfe = max(mfe, entry - l)  # 유리: 아래로 내려갈수록
                mae = max(mae, h - entry)  # 불리: 위로 튈수록

                hit_sl = h >= sl
                hit_tp2 = (tp2 is not None) and (l <= tp2)
                hit_tp1 = (tp1 is not None) and (l <= tp1)

                # 같은 캔들에서 동시 터치 처리
                if hit_sl and (hit_tp2 or hit_tp1):
                    if tie_breaker == "tp_first":
                        if hit_tp2:
                            result, exit_price = "tp2", tp2
                        else:
                            result, exit_price = "tp1", tp1
                    else:  # sl_first
                        result, exit_price = "sl", sl
                    exit_time = ot
                    break

                # 개별 터치 확인
                if hit_sl:
                    result, exit_price, exit_time = "sl", sl, ot
                    break
                if hit_tp2:
                    result, exit_price, exit_time = "tp2", tp2, ot
                    break
                if hit_tp1:
                    result, exit_price, exit_time = "tp1", tp1, ot
                    break

        # 버그 수정: horizon 내에 아무것도 안 맞으면 마지막 캔들에서 청산
        if exit_time is None and rows:
            last_ot, last_o, last_h, last_l, last_c = rows[-1]
            exit_time = last_ot
            exit_price = float(last_c)
            result = "none"
            
            # 마지막 캔들의 MFE/MAE도 반영
            if direction == "long":
                mfe = max(mfe, float(last_h) - entry)
                mae = max(mae, entry - float(last_l))
            else:
                mfe = max(mfe, entry - float(last_l))
                mae = max(mae, float(last_h) - entry)

        # R-Multiple 계산
        r_multiple = None
        if exit_price is not None:
            r_multiple = compute_r_multiple(direction, entry, sl, float(exit_price))

        return {
            "signal_id": sig["signal_id"],
            "exit_time": exit_time,
            "exit_price": exit_price,
            "result": result,
            "r_multiple": r_multiple,
            "mfe": mfe,
            "mae": mae,
        }

    except Exception as e:
        logger.error(f"신호 평가 중 오류 ({sig.get('symbol')} {sig.get('open_time')}): {e}")
        return None


def get_signals_to_evaluate(engine, symbols_filter=None, min_age_minutes: int = 60) -> List[Dict]:
    """
    평가할 신호 목록 조회
    
    Args:
        engine: SQLAlchemy engine
        symbols_filter: 특정 심볼만 처리
        min_age_minutes: 최소 경과 시간 (분)
    
    Returns:
        신호 목록
    """
    try:
        now_utc = datetime.now(timezone.utc)
        cutoff = now_utc - timedelta(minutes=min_age_minutes)

        # outcomes가 없거나 exit_price가 NULL인 신호만 조회
        query = """
            SELECT
                s.signal_id,
                s.symbol,
                s.tf,
                s.open_time,
                s.direction,
                s.entry,
                s.stop_loss,
                s.take_profit_1,
                s.take_profit_2
            FROM signals s
            LEFT JOIN outcomes o ON o.signal_id = s.signal_id
            WHERE (o.signal_id IS NULL OR o.exit_price IS NULL)
              AND s.open_time <= :cutoff
        """
        
        params = {"cutoff": cutoff}
        
        # 심볼 필터링
        if symbols_filter:
            if isinstance(symbols_filter, str):
                symbols_filter = [symbols_filter]
            placeholders = ','.join([f':symbol{i}' for i in range(len(symbols_filter))])
            query += f" AND s.symbol IN ({placeholders})"
            for i, sym in enumerate(symbols_filter):
                params[f'symbol{i}'] = sym
        
        query += " ORDER BY s.open_time ASC"
        
        with engine.connect() as conn:
            result = conn.execute(text(query), params)
            rows = result.fetchall()
        
        signals = []
        for r in rows:
            signals.append({
                "signal_id": r[0],
                "symbol": r[1],
                "tf": r[2],
                "open_time": r[3],
                "direction": r[4],
                "entry": r[5],
                "stop_loss": r[6],
                "take_profit_1": r[7],
                "take_profit_2": r[8],
            })
        
        return signals

    except SQLAlchemyError as e:
        logger.error(f"신호 조회 중 오류: {e}")
        return []


def save_outcome(engine, outcome: Dict) -> bool:
    """
    결과를 DB에 저장
    
    Args:
        engine: SQLAlchemy engine
        outcome: 결과 dict
    
    Returns:
        성공 여부
    """
    try:
        query = text("""
            INSERT INTO outcomes (signal_id, exit_time, exit_price, result, r_multiple, mfe, mae)
            VALUES (:signal_id, :exit_time, :exit_price, :result, :r_multiple, :mfe, :mae)
            ON CONFLICT (signal_id) DO UPDATE SET
                exit_time   = COALESCE(outcomes.exit_time, EXCLUDED.exit_time),
                exit_price  = COALESCE(outcomes.exit_price, EXCLUDED.exit_price),
                result      = COALESCE(outcomes.result, EXCLUDED.result),
                r_multiple  = COALESCE(outcomes.r_multiple, EXCLUDED.r_multiple),
                mfe         = COALESCE(outcomes.mfe, EXCLUDED.mfe),
                mae         = COALESCE(outcomes.mae, EXCLUDED.mae)
        """)
        
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(query, outcome)
        
        return True
    except SQLAlchemyError as e:
        logger.error(f"결과 저장 중 오류 (signal_id={outcome['signal_id']}): {e}")
        return False


def run_outcomes(symbols_filter=None, config_path="config.yaml"):
    """
    신호 결과 평가 메인 함수
    
    Args:
        symbols_filter: 특정 심볼만 처리
        config_path: 설정 파일 경로
    """
    engine = None
    try:
        cfg = load_config(config_path)
        
        # outcomes 설정 로드
        ocfg = cfg.get("outcomes", {})
        horizon_map = ocfg.get("horizon_bars", {"30m": 48, "4h": 30, "1d": 20})
        tie_breaker = ocfg.get("tie_breaker", "sl_first")
        min_age_minutes = int(ocfg.get("min_age_minutes", 60))
        slippage_pct = float(ocfg.get("slippage_pct", 0.0))

        logger.info(f"결과 평가 설정 - Tie-breaker: {tie_breaker}, 최소 경과: {min_age_minutes}분, 슬리피지: {slippage_pct}%")

        engine = get_engine(cfg)
        
        # 평가할 신호 조회
        signals = get_signals_to_evaluate(engine, symbols_filter, min_age_minutes)
        
        if not signals:
            logger.info("평가할 신호가 없습니다.")
            return
        
        logger.info(f"평가할 신호 수: {len(signals)}")
        if symbols_filter:
            logger.info(f"필터링된 심볼: {symbols_filter}")

        total = 0
        saved = 0
        skipped = 0
        failed = 0

        for sig in signals:
            try:
                tf = sig["tf"]
                horizon_bars = int(horizon_map.get(tf, 48))

                outcome = label_signal(
                    engine, 
                    sig, 
                    horizon_bars=horizon_bars, 
                    tie_breaker=tie_breaker,
                    slippage_pct=slippage_pct
                )
                total += 1

                if outcome is None:
                    skipped += 1
                    logger.debug(f"스킵: {sig['symbol']} {sig['open_time']} (데이터 부족)")
                    continue

                # 결과 저장
                if save_outcome(engine, outcome):
                    saved += 1
                    
                    # 결과 로깅
                    result_emoji = {
                        "tp2": "✓✓",
                        "tp1": "✓",
                        "sl": "✗",
                        "none": "○"
                    }.get(outcome["result"], "?")
                    
                    logger.info(
                        f"{result_emoji} {sig['symbol']} {sig['open_time'].strftime('%Y-%m-%d %H:%M')} - "
                        f"{sig['direction'].upper()} | {outcome['result'].upper()} | "
                        f"R: {outcome['r_multiple']:.2f} | "
                        f"MFE: {outcome['mfe']:.2f} | MAE: {outcome['mae']:.2f}"
                    )
                else:
                    failed += 1

                # 진행 상황 표시
                if saved and saved % 50 == 0:
                    logger.info(f"진행 상황: {saved}/{total} 저장 완료")

            except Exception as e:
                failed += 1
                logger.error(f"신호 처리 중 오류 ({sig['symbol']} {sig['open_time']}): {e}")
                continue

        logger.info("=" * 60)
        logger.info(f"평가 완료 - 총 {total}개 처리")
        logger.info(f"저장: {saved}, 스킵: {skipped}, 실패: {failed}")
        logger.info("=" * 60)
        
        # 통계 출력
        if saved > 0:
            print_statistics(engine)

    except Exception as e:
        logger.error(f"결과 평가 실행 중 치명적 오류: {e}")
        raise
    finally:
        if engine:
            engine.dispose()
            logger.info("데이터베이스 연결 종료")


def print_statistics(engine):
    """결과 통계 출력"""
    try:
        query = text("""
            SELECT 
                result,
                COUNT(*) as count,
                AVG(r_multiple) as avg_r,
                AVG(mfe) as avg_mfe,
                AVG(mae) as avg_mae
            FROM outcomes
            WHERE exit_price IS NOT NULL
            GROUP BY result
            ORDER BY result
        """)
        
        with engine.connect() as conn:
            result = conn.execute(query)
            rows = result.fetchall()
        
        if rows:
            logger.info("\n📊 결과 통계:")
            for row in rows:
                result_type, count, avg_r, avg_mfe, avg_mae = row
                logger.info(
                    f"  {result_type.upper()}: {count}건 | "
                    f"평균 R: {avg_r:.2f} | "
                    f"평균 MFE: {avg_mfe:.2f} | "
                    f"평균 MAE: {avg_mae:.2f}"
                )
    except Exception as e:
        logger.debug(f"통계 출력 중 오류: {e}")


def main():
    """CLI 인터페이스"""
    parser = argparse.ArgumentParser(
        description='암호화폐 트레이딩 신호 결과 평가 스크립트',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 모든 신호 평가
  python run_outcomes.py
  
  # 특정 심볼만 평가
  python run_outcomes.py --symbol BTCUSDT
  
  # 여러 심볼 평가
  python run_outcomes.py --symbol BTCUSDT ETHUSDT BNBUSDT
  
  # 설정 파일 지정
  python run_outcomes.py --config custom_config.yaml

설정 파일 예시 (config.yaml):
  outcomes:
    horizon_bars:
      30m: 48    # 30분 × 48 = 24시간
      4h: 30     # 4시간 × 30 = 5일
      1d: 20     # 1일 × 20 = 20일
    tie_breaker: "sl_first"  # 또는 "tp_first"
    min_age_minutes: 60      # 최소 1시간 경과 후 평가
    slippage_pct: 0.05       # 슬리피지 0.05%
        """
    )
    
    parser.add_argument(
        '--symbol', '-s',
        nargs='+',
        help='평가할 심볼 (예: BTCUSDT). 여러 개 지정 가능',
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
            logger.info(f"지정된 심볼 평가: {symbols}")
            run_outcomes(symbols_filter=symbols, config_path=args.config)
        else:
            logger.info("모든 심볼 평가")
            run_outcomes(config_path=args.config)
    except KeyboardInterrupt:
        logger.info("사용자에 의해 중단되었습니다.")
    except Exception as e:
        logger.error(f"실행 실패: {e}")
        exit(1)


if __name__ == "__main__":
    main()
