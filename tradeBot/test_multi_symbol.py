"""
다중 심볼 분석 테스트

여러 심볼을 동시에 분석
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import pandas as pd
import logging
from datetime import datetime

from core.config_loader import get_config
from strategies import StrategyManager
from strategies.ict_strategy import ICTStrategy

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_candles(conn, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
    """
    DB에서 캔들 데이터 로드
    
    Args:
        conn: DB 연결
        symbol: 심볼 (예: 'BTCUSDT')
        timeframe: 타임프레임 (예: '15m')
        limit: 캔들 개수
    
    Returns:
        DataFrame
    """
    query = """
        SELECT 
            open_time, 
            open, 
            high, 
            low, 
            close, 
            volume,
            symbol,
            tf
        FROM candles
        WHERE symbol = %s
          AND tf = %s
        ORDER BY open_time DESC
        LIMIT %s
    """
    
    df = pd.read_sql(query, conn, params=(symbol, timeframe, limit))
    
    if len(df) == 0:
        logger.warning(f"⚠️  데이터 없음: {symbol} {timeframe}")
        return None
    
    # 시간 순으로 정렬
    df = df.sort_values('open_time').reset_index(drop=True)
    
    logger.info(f"📊 {symbol} {timeframe}: {len(df)}개 캔들 로드")
    
    return df


def analyze_multiple_symbols(symbols: list, timeframe: str = '15m'):
    """
    여러 심볼 분석
    
    Args:
        symbols: 심볼 리스트 ['BTCUSDT', 'ETHUSDT', ...]
        timeframe: 타임프레임
    """
    print("=" * 70)
    print("다중 심볼 분석")
    print("=" * 70)
    print()
    
    # Config
    config = get_config()
    
    # DB 연결
    conn = psycopg2.connect(
        host=config.get('db.host'),
        port=config.get('db.port'),
        dbname=config.get('db.name'),
        user=config.get('db.user'),
        password=config.get('db.password')
    )
    
    # 전략 생성
    ict_strategy = ICTStrategy(
        name='ict',
        config={
            'enabled': True,
            'timeframe': timeframe,
            'ob_lookback': 20,
            'fvg_min_gap_pct': 0.001,
            'min_confidence': 0.65,
            'min_risk_reward': 1.5,
            'atr_multiplier': 2.0,
            'use_ai': True  # AI 검증은 StrategyManager에서 처리
        }
    )
    
    # 전략 관리자
    manager = StrategyManager()
    manager.register_strategy(ict_strategy)
    
    print(f"전략: {manager.list_strategies()}")
    print(f"심볼: {symbols}")
    print(f"타임프레임: {timeframe}")
    print()
    
    # 각 심볼 분석
    all_signals = []
    
    for symbol in symbols:
        print("-" * 70)
        print(f"📊 분석 중: {symbol}")
        print("-" * 70)
        
        # 데이터 로드
        df = load_candles(conn, symbol, timeframe)
        
        if df is None or len(df) < 50:
            print(f"❌ {symbol}: 데이터 부족")
            print()
            continue
        
        # 현재가
        current_price = df.iloc[-1]['close']
        print(f"현재가: ${current_price:,.2f}")
        print()
        
        # 전략 실행
        results = manager.analyze_all(symbol, timeframe, df)
        
        # 결과 출력
        for strategy_name, signals in results.items():
            if signals:
                print(f"✅ {strategy_name}: {len(signals)}개 신호")
                print()
                
                for i, signal in enumerate(signals, 1):
                    print(f"  {i}. [{signal.signal_type.upper()}] {signal.symbol}")
                    print(f"     진입: ${signal.entry_price:,.2f}")
                    print(f"     손절: ${signal.stop_loss:,.2f}")
                    print(f"     익절1: ${signal.take_profit_1:,.2f}")
                    print(f"     신뢰도: {signal.confidence:.1%}")
                    print(f"     R/R: {signal.risk_reward:.2f}")
                    print(f"     근거: {', '.join(signal.reasons)}")
                    
                    # AI 분석 결과
                    if 'ai_analysis' in signal.metadata:
                        ai = signal.metadata['ai_analysis']
                        print(f"     AI: {ai['decision']} ({ai['confidence']:.1%})")
                    
                    print()
                
                all_signals.extend(signals)
            else:
                print(f"❌ {strategy_name}: 신호 없음")
                print()
        
        print()
    
    conn.close()
    
    # 요약
    print("=" * 70)
    print("요약")
    print("=" * 70)
    print()
    print(f"분석 심볼: {len(symbols)}개")
    print(f"총 신호: {len(all_signals)}개")
    
    if all_signals:
        buy_signals = [s for s in all_signals if s.signal_type == 'buy']
        sell_signals = [s for s in all_signals if s.signal_type == 'sell']
        
        print(f"  매수: {len(buy_signals)}개")
        print(f"  매도: {len(sell_signals)}개")
        print()
        
        # 신뢰도 높은 순
        top_signals = sorted(all_signals, key=lambda x: x.confidence, reverse=True)[:5]
        
        print("신뢰도 Top 5:")
        for i, signal in enumerate(top_signals, 1):
            print(f"  {i}. {signal.symbol} {signal.signal_type.upper()}")
            print(f"     신뢰도: {signal.confidence:.1%}")
            print(f"     R/R: {signal.risk_reward:.2f}")
            
            # AI 결과
            if 'ai_analysis' in signal.metadata:
                ai = signal.metadata['ai_analysis']
                print(f"     AI: {ai['confidence']:.1%}")
            print()


if __name__ == "__main__":
    # 분석할 심볼 리스트
    symbols = [
        'BTCUSDT',
        'ETHUSDT',
        'SOLUSDT',
        'BNBUSDT',
        'XRPUSDT'
    ]
    
    # 타임프레임
    timeframe = '15m'
    
    # 실행
    analyze_multiple_symbols(symbols, timeframe)
