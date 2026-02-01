"""
범용 암호화폐 데이터베이스 분석 스크립트
사용법: python analyze_symbol.py BTCUSDT
       python analyze_symbol.py ETHUSDT --timeframe 4h
"""

import sys
import yaml
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
import pandas as pd
import argparse

def load_config(config_path="config.yaml"):
    """Multi-encoding config loader"""
    for encoding in ['utf-8', 'cp949', 'euc-kr']:
        try:
            with open(config_path, 'r', encoding=encoding) as f:
                return yaml.safe_load(f)
        except (UnicodeDecodeError, FileNotFoundError):
            continue
    raise RuntimeError(f"Cannot load {config_path}")

def connect_db(config):
    db = config['db']
    conn = psycopg2.connect(
        host=db['host'],
        port=db['port'],
        database=db['name'],
        user=db['user'],
        password=db['password']
    )
    return conn

def format_price(price):
    """가격 포맷팅 (소액은 소수점 많이)"""
    if price < 0.01:
        return f"${price:.6f}"
    elif price < 1:
        return f"${price:.4f}"
    else:
        return f"${price:.2f}"

def analyze_symbol(symbol, timeframe='1d'):
    print("=" * 80)
    print(f"{symbol} 데이터베이스 분석")
    print(f"타임프레임: {timeframe}")
    print("=" * 80)
    print()
    
    config = load_config()
    conn = connect_db(config)
    
    # ==========================================
    # 1. 최근 지표 데이터
    # ==========================================
    print(f"📊 1. 최근 지표 데이터 ({timeframe})")
    print("-" * 80)
    
    query_indicators = """
    SELECT 
        open_time,
        timeframe,
        open,
        high,
        low,
        close,
        volume,
        ema20,
        ema50,
        ema100,
        ema200,
        rsi,
        macd,
        macd_signal,
        bb_upper,
        bb_middle,
        bb_lower
    FROM indicators
    WHERE symbol = %s 
      AND timeframe = %s
    ORDER BY open_time DESC
    LIMIT 20
    """
    
    df = pd.read_sql(query_indicators, conn, params=(symbol, timeframe))
    
    if len(df) > 0:
        latest = df.iloc[0]
        prev = df.iloc[1] if len(df) > 1 else latest
        
        # 가격 변화
        price_change = latest['close'] - prev['close']
        price_change_pct = (price_change / prev['close']) * 100
        
        print(f"📅 최근 업데이트: {latest['open_time']}")
        print()
        print(f"💰 가격 정보:")
        print(f"   현재가: {format_price(latest['close'])} ({price_change_pct:+.2f}%)")
        print(f"   고가: {format_price(latest['high'])}")
        print(f"   저가: {format_price(latest['low'])}")
        print(f"   거래량: {latest['volume']:,.0f}")
        print()
        
        print(f"📈 이동평균선 (EMA):")
        print(f"   EMA20:  {format_price(latest['ema20'])}")
        print(f"   EMA50:  {format_price(latest['ema50'])}")
        print(f"   EMA100: {format_price(latest['ema100'])}")
        if pd.notna(latest.get('ema200')):
            print(f"   EMA200: {format_price(latest['ema200'])}")
        print()
        
        # EMA 배열
        if latest['ema20'] > latest['ema50'] > latest['ema100']:
            print("   ✅ 상승 배열 (골든)")
        elif latest['ema20'] < latest['ema50'] < latest['ema100']:
            print("   📉 하락 배열 (데드)")
        else:
            print("   ↔️ 혼조 배열")
        
        # 현재가 위치
        if latest['close'] > latest['ema20']:
            diff = ((latest['close'] - latest['ema20']) / latest['ema20']) * 100
            print(f"   ✅ 현재가 > EMA20 (+{diff:.2f}%)")
        else:
            diff = ((latest['ema20'] - latest['close']) / latest['ema20']) * 100
            print(f"   ⚠️ 현재가 < EMA20 (-{diff:.2f}%)")
        print()
        
        print(f"🎯 기술적 지표:")
        print(f"   RSI: {latest['rsi']:.2f}", end="")
        if latest['rsi'] > 70:
            print(" (과매수 ⚠️)")
        elif latest['rsi'] < 30:
            print(" (과매도 ✅)")
        else:
            print(" (중립)")
        
        if pd.notna(latest.get('macd')) and pd.notna(latest.get('macd_signal')):
            macd_diff = latest['macd'] - latest['macd_signal']
            macd_status = "상승" if macd_diff > 0 else "하락"
            print(f"   MACD: {latest['macd']:.4f} (시그널 대비 {macd_status})")
        
        # 볼린저 밴드
        if pd.notna(latest.get('bb_upper')):
            bb_range = latest['bb_upper'] - latest['bb_lower']
            bb_position = (latest['close'] - latest['bb_lower']) / bb_range * 100 if bb_range > 0 else 50
            
            print(f"   볼린저 밴드: {bb_position:.1f}%", end="")
            if bb_position > 80:
                print(" (상단 근접 ⚠️)")
            elif bb_position < 20:
                print(" (하단 근접 ✅)")
            else:
                print(" (중립)")
        
        print()
        
        # 최근 추세 (5일)
        if len(df) >= 5:
            df_recent = df.head(5)
            closes = df_recent['close'].values
            
            # 단순 추세 판단
            if closes[0] > closes[-1]:
                trend_pct = ((closes[0] - closes[-1]) / closes[-1]) * 100
                print(f"📊 최근 5개 캔들 추세: 상승 (+{trend_pct:.2f}%)")
            else:
                trend_pct = ((closes[-1] - closes[0]) / closes[0]) * 100
                print(f"📊 최근 5개 캔들 추세: 하락 (-{trend_pct:.2f}%)")
        
        print()
        
        # 테이블로 표시
        print("최근 데이터:")
        display_df = df.head(10)[['open_time', 'close', 'ema20', 'ema50', 'rsi']].copy()
        display_df['close'] = display_df['close'].apply(lambda x: f"{x:.4f}")
        display_df['ema20'] = display_df['ema20'].apply(lambda x: f"{x:.4f}" if pd.notna(x) else 'N/A')
        display_df['ema50'] = display_df['ema50'].apply(lambda x: f"{x:.4f}" if pd.notna(x) else 'N/A')
        display_df['rsi'] = display_df['rsi'].apply(lambda x: f"{x:.2f}" if pd.notna(x) else 'N/A')
        print(display_df.to_string(index=False))
        
    else:
        print("❌ 지표 데이터 없음")
    
    print()
    print()
    
    # ==========================================
    # 2. EMA 신호
    # ==========================================
    print("📡 2. 최근 EMA 크로스 신호")
    print("-" * 80)
    
    query_signals = """
    SELECT 
        signal_id,
        timeframe,
        direction,
        signal_time,
        close_price,
        entry_price,
        stop_loss,
        take_profit_1,
        take_profit_2,
        prob_long,
        prob_short
    FROM ema_signals
    WHERE symbol = %s
    ORDER BY signal_time DESC
    LIMIT 10
    """
    
    df_signals = pd.read_sql(query_signals, conn, params=(symbol,))
    
    if len(df_signals) > 0:
        print(f"최근 {len(df_signals)}개 신호:")
        print()
        
        for idx, row in df_signals.iterrows():
            direction_icon = "📈" if row['direction'] == 'GOLDEN' else "📉"
            prob = row['prob_long'] if row['direction'] == 'GOLDEN' else row['prob_short']
            
            print(f"{direction_icon} {row['direction']} ({row['timeframe']})")
            print(f"   시간: {row['signal_time']}")
            print(f"   진입: {format_price(row['entry_price'])}")
            print(f"   손절: {format_price(row['stop_loss'])}")
            print(f"   목표1: {format_price(row['take_profit_1'])}")
            print(f"   목표2: {format_price(row['take_profit_2'])}")
            
            if prob:
                prob_color = "✅" if prob >= 60 else "⚠️" if prob >= 50 else "❌"
                print(f"   승률: {prob:.1f}% {prob_color}")
            print()
            
            if idx >= 4:  # 최대 5개만 상세 표시
                break
    else:
        print("❌ 신호 없음")
    
    print()
    print()
    
    # ==========================================
    # 3. 신호 성과
    # ==========================================
    print("📈 3. 신호 성과 통계")
    print("-" * 80)
    
    query_performance = """
    SELECT 
        s.timeframe,
        s.direction,
        COUNT(*) as total_signals,
        COUNT(o.result) as completed_signals,
        SUM(CASE WHEN o.result = 'WIN' THEN 1 ELSE 0 END) as wins,
        AVG(o.r_multiple) as avg_r_multiple
    FROM ema_signals s
    LEFT JOIN outcomes o ON s.signal_id = o.signal_id
    WHERE s.symbol = %s
    GROUP BY s.timeframe, s.direction
    ORDER BY s.timeframe, s.direction
    """
    
    df_perf = pd.read_sql(query_performance, conn, params=(symbol,))
    
    if len(df_perf) > 0:
        for idx, row in df_perf.iterrows():
            direction_icon = "📈" if row['direction'] == 'GOLDEN' else "📉"
            
            total = row['total_signals']
            completed = row['completed_signals']
            
            if completed > 0:
                wins = row['wins']
                win_rate = (wins / completed) * 100
                avg_r = row['avg_r_multiple'] if pd.notna(row['avg_r_multiple']) else 0
                
                print(f"{direction_icon} {row['direction']} ({row['timeframe']})")
                print(f"   총 신호: {total}개")
                print(f"   완료: {completed}개 (승률: {win_rate:.1f}%)")
                print(f"   평균 R: {avg_r:.2f}R")
                print()
            else:
                print(f"{direction_icon} {row['direction']} ({row['timeframe']})")
                print(f"   총 신호: {total}개 (진행 중)")
                print()
    else:
        print("❌ 성과 데이터 없음")
    
    print()
    print("=" * 80)
    
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='암호화폐 데이터베이스 분석')
    parser.add_argument('symbol', help='심볼 (예: BTCUSDT, ETHUSDT)')
    parser.add_argument('--timeframe', '-tf', default='1d', help='타임프레임 (기본: 1d)')
    parser.add_argument('--config', default='config.yaml', help='설정 파일 경로')
    
    args = parser.parse_args()
    
    try:
        analyze_symbol(args.symbol.upper(), args.timeframe)
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
