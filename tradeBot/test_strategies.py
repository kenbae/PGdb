# -*- coding: utf-8 -*-
"""
전략 테스트 스크립트

다른 화면/서비스에서 전략 시스템을 독립적으로 사용하는 예제입니다.
api_server.py 없이도 전략을 테스트하고 사용할 수 있습니다.

사용법:
    python test_strategies.py                    # 모든 전략 테스트
    python test_strategies.py --strategy ict     # 특정 전략만 테스트
    python test_strategies.py --symbol BTCUSDT   # 특정 심볼만 테스트
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import logging
from datetime import datetime
import pandas as pd
from sqlalchemy import create_engine, text

# 전략 시스템 import
from strategies import create_strategy_manager, StrategyManager
from strategies.base import TradeSignal
from core.config_loader import get_config

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_candles_from_db(engine, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
    """
    DB에서 캔들 데이터 로드

    Args:
        engine: SQLAlchemy 엔진
        symbol: 심볼 (예: 'BTCUSDT')
        timeframe: 타임프레임 (예: '15m')
        limit: 캔들 개수

    Returns:
        DataFrame: OHLCV 데이터
    """
    query = text("""
        SELECT
            open_time, open, high, low, close, volume
        FROM candles
        WHERE symbol = :symbol AND tf = :timeframe
        ORDER BY open_time DESC
        LIMIT :limit
    """)

    df = pd.read_sql(query, engine, params={
        'symbol': symbol,
        'timeframe': timeframe,
        'limit': limit
    })

    if len(df) == 0:
        logger.warning(f"데이터 없음: {symbol} {timeframe}")
        return pd.DataFrame()

    # 시간순 정렬
    df = df.sort_values('open_time').reset_index(drop=True)
    logger.info(f"📊 {symbol} {timeframe}: {len(df)}개 캔들 로드")

    return df


def test_single_strategy(
    strategy_manager: StrategyManager,
    strategy_name: str,
    symbol: str,
    timeframe: str,
    df: pd.DataFrame
) -> list:
    """
    단일 전략 테스트

    Args:
        strategy_manager: 전략 매니저
        strategy_name: 테스트할 전략 이름
        symbol: 심볼
        timeframe: 타임프레임
        df: OHLCV 데이터

    Returns:
        list: 생성된 신호 목록
    """
    signals = strategy_manager.analyze_strategy(
        strategy_name=strategy_name,
        symbol=symbol,
        timeframe=timeframe,
        df=df,
        apply_ai=False  # 테스트에서는 AI 검증 비활성화
    )

    return signals


def test_all_strategies(
    strategy_manager: StrategyManager,
    symbol: str,
    timeframe: str,
    df: pd.DataFrame
) -> dict:
    """
    모든 활성 전략 테스트

    Args:
        strategy_manager: 전략 매니저
        symbol: 심볼
        timeframe: 타임프레임
        df: OHLCV 데이터

    Returns:
        dict: 전략별 신호 목록
    """
    results = strategy_manager.analyze_all(
        symbol=symbol,
        timeframe=timeframe,
        df=df,
        apply_ai=False  # 테스트에서는 AI 검증 비활성화
    )

    return results


def print_signal(signal: TradeSignal):
    """신호 출력"""
    direction = "🟢 매수" if signal.signal_type == 'buy' else "🔴 매도"
    print(f"\n  {direction}: {signal.symbol}")
    print(f"    진입가: ${signal.entry_price:,.4f}")
    print(f"    손절가: ${signal.stop_loss:,.4f}")
    print(f"    익절가1: ${signal.take_profit_1:,.4f}")
    if signal.take_profit_2:
        print(f"    익절가2: ${signal.take_profit_2:,.4f}")
    print(f"    신뢰도: {signal.confidence:.1%}")
    print(f"    R/R: {signal.risk_reward:.2f}")
    if signal.reasons:
        print(f"    근거: {', '.join(signal.reasons)}")


def main():
    parser = argparse.ArgumentParser(description='전략 테스트 스크립트')
    parser.add_argument('--strategy', '-s', help='테스트할 전략 이름 (예: ict, ema_cross, rsi, bollinger)')
    parser.add_argument('--symbol', help='테스트할 심볼 (기본: BTCUSDT)', default='BTCUSDT')
    parser.add_argument('--timeframe', '-tf', help='타임프레임 (기본: 15m)', default='15m')
    parser.add_argument('--limit', '-l', type=int, help='캔들 개수 (기본: 500)', default=500)
    args = parser.parse_args()

    print("=" * 70)
    print("🧪 전략 테스트 스크립트")
    print("=" * 70)
    print()

    # Config 로드
    config = get_config()
    raw_config = config.config if hasattr(config, 'config') else {}

    # DB 연결
    db_url = f"postgresql://{config.get('db.user')}:{config.get('db.password')}@{config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}"
    engine = create_engine(db_url)
    logger.info("✅ DB 연결 성공")

    # 전략 매니저 생성 (DB 기반)
    try:
        from database.strategy_settings_repo import StrategySettingsRepo
        repo = StrategySettingsRepo(engine)
    except Exception:
        repo = None
    strategy_manager = create_strategy_manager(strategy_settings_repo=repo, config=raw_config)
    print(f"📋 등록된 전략: {strategy_manager.list_strategies()}")
    print(f"✅ 활성 전략: {[s.get_name() for s in strategy_manager.get_enabled_strategies()]}")
    print()

    # 데이터 로드
    df = load_candles_from_db(engine, args.symbol, args.timeframe, args.limit)
    if df.empty:
        print(f"❌ {args.symbol} {args.timeframe} 데이터가 없습니다.")
        return

    current_price = float(df.iloc[-1]['close'])
    print(f"💰 현재가: ${current_price:,.2f}")
    print()

    # 전략 테스트
    if args.strategy:
        # 특정 전략만 테스트
        print(f"🔍 전략 테스트: {args.strategy}")
        print("-" * 70)

        signals = test_single_strategy(
            strategy_manager, args.strategy, args.symbol, args.timeframe, df
        )

        if signals:
            print(f"✅ {len(signals)}개 신호 생성:")
            for signal in signals:
                print_signal(signal)
        else:
            print("⏸️  신호 없음")
    else:
        # 모든 전략 테스트
        print("🔍 모든 전략 테스트")
        print("-" * 70)

        results = test_all_strategies(
            strategy_manager, args.symbol, args.timeframe, df
        )

        total_signals = 0
        for strategy_name, signals in results.items():
            print(f"\n📊 {strategy_name}:")
            if signals:
                total_signals += len(signals)
                for signal in signals:
                    print_signal(signal)
            else:
                print("  ⏸️  신호 없음")

        print("\n" + "=" * 70)
        print(f"📈 총 {total_signals}개 신호 생성")

    # 연결 종료
    engine.dispose()
    print("\n✅ 테스트 완료")


if __name__ == "__main__":
    main()
