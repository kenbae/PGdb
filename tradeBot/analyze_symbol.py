"""
심볼 지정 분석

원하는 심볼을 명령줄에서 지정해서 분석
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import pandas as pd
import logging
from datetime import datetime

from core.config_loader import get_config
from strategies.base import BaseStrategy, TradeSignal
from strategies.ict_strategy import ICTStrategy

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger(__name__)


def analyze_symbol(symbol: str, timeframe: str = '15m', use_ai: bool = True):
    """
    특정 심볼 분석
    
    Args:
        symbol: 심볼 (예: 'BTCUSDT', 'ETHUSDT')
        timeframe: 타임프레임 (예: '15m', '1h')
        use_ai: AI 검증 사용 여부
    """
    print("=" * 70)
    print(f"ICT 분석: {symbol} ({timeframe})")
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
    
    # 데이터 로드
    print(f"📊 데이터 로드 중: {symbol} {timeframe}")
    
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
        LIMIT 500
    """
    
    df = pd.read_sql(query, conn, params=(symbol, timeframe))
    conn.close()
    
    if len(df) == 0:
        print(f"❌ 데이터 없음: {symbol} {timeframe}")
        print()
        print("DB에 데이터가 있는지 확인하세요:")
        print(f"  SELECT COUNT(*) FROM candles WHERE symbol = '{symbol}' AND tf = '{timeframe}';")
        return
    
    # 시간 순 정렬
    df = df.sort_values('open_time').reset_index(drop=True)
    
    print(f"✅ {len(df)}개 캔들 로드")
    print(f"   기간: {df['open_time'].min()} ~ {df['open_time'].max()}")
    print()
    
    # 현재가
    current_price = df.iloc[-1]['close']
    prev_price = df.iloc[-20]['close'] if len(df) > 20 else df.iloc[0]['close']
    change_pct = (current_price - prev_price) / prev_price * 100
    
    print(f"현재가: ${current_price:,.2f}")
    print(f"20캔들 전: ${prev_price:,.2f} ({change_pct:+.2f}%)")
    print()
    
    # 전략 생성
    print("🔧 전략 초기화...")
    
    strategy = ICTStrategy(
        name='ict',
        config={
            'enabled': True,
            'timeframe': timeframe,
            'ob_lookback': 20,
            'fvg_min_gap_pct': 0.001,
            'min_confidence': 0.65,
            'min_risk_reward': 1.5,
            'atr_multiplier': 2.0,
            'use_ai': use_ai
        }
    )
    
    print(f"✅ AI 사용: {use_ai}")
    print()
    
    # 분석
    print("🔍 분석 중...")
    print()
    
    signals = strategy.analyze(symbol, timeframe, df)
    
    # 결과
    print("=" * 70)
    print("분석 결과")
    print("=" * 70)
    print()
    
    if not signals:
        print("❌ 신호 없음")
        print()
        print("가능한 이유:")
        print("  - Order Block이나 Fair Value Gap이 현재가 근처에 없음")
        print("  - 신뢰도가 너무 낮음 (< 65%)")
        print("  - R/R 비율이 너무 낮음 (< 1.5)")
        if use_ai:
            print("  - AI가 신호를 거부함")
        return
    
    print(f"✅ {len(signals)}개 신호 발견")
    print()
    
    for i, signal in enumerate(signals, 1):
        print(f"{i}. [{signal.signal_type.upper()}] 신호")
        print("-" * 70)
        
        # 기본 정보
        print(f"진입가: ${signal.entry_price:,.2f}")
        print(f"손절가: ${signal.stop_loss:,.2f}")
        print(f"익절1: ${signal.take_profit_1:,.2f}")
        if signal.take_profit_2:
            print(f"익절2: ${signal.take_profit_2:,.2f}")
        print()
        
        # 손익 계산
        if signal.signal_type == 'buy':
            risk = signal.entry_price - signal.stop_loss
            reward1 = signal.take_profit_1 - signal.entry_price
        else:
            risk = signal.stop_loss - signal.entry_price
            reward1 = signal.entry_price - signal.take_profit_1
        
        risk_pct = (risk / signal.entry_price) * 100
        reward1_pct = (reward1 / signal.entry_price) * 100
        
        print(f"리스크: ${risk:,.2f} ({risk_pct:.2f}%)")
        print(f"보상1: ${reward1:,.2f} ({reward1_pct:.2f}%)")
        print()
        
        # 품질
        print(f"신뢰도: {signal.confidence:.1%}")
        print(f"R/R 비율: {signal.risk_reward:.2f}")
        print()
        
        # 근거
        print(f"근거: {', '.join(signal.reasons)}")
        print()
        
        # AI 분석
        if 'ai_analysis' in signal.metadata:
            ai = signal.metadata['ai_analysis']
            print(f"AI 검증: {ai['decision'].upper()}")
            print(f"AI 신뢰도: {ai['confidence']:.1%}")
            print(f"AI 판단: {ai['reasoning']}")
            print()
        
        print()
    
    # 거래 제안
    if signals:
        best_signal = max(signals, key=lambda x: x.confidence)
        
        print("=" * 70)
        print("추천 거래")
        print("=" * 70)
        print()
        print(f"방향: {best_signal.signal_type.upper()}")
        print(f"진입: ${best_signal.entry_price:,.2f}")
        print(f"손절: ${best_signal.stop_loss:,.2f}")
        print(f"익절: ${best_signal.take_profit_1:,.2f}")
        print(f"신뢰도: {best_signal.confidence:.1%}")
        print()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='심볼 분석')
    parser.add_argument('symbol', type=str, help='심볼 (예: BTCUSDT)')
    parser.add_argument('--tf', type=str, default='15m', help='타임프레임 (기본: 15m)')
    parser.add_argument('--no-ai', action='store_true', help='AI 사용 안함 (ICT만)')
    
    args = parser.parse_args()
    
    # 실행
    analyze_symbol(
        symbol=args.symbol,
        timeframe=args.tf,
        use_ai=not args.no_ai
    )
