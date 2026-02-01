#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
30분봉 전략 백테스트
여러 전략의 성과를 비교 분석
"""

import yaml
import logging
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# ============================================================
# 설정 및 DB 연결
# ============================================================

def load_config(path="config.yaml"):
    """설정 파일 로드 (다중 인코딩 지원)"""
    encodings = ['utf-8', 'cp949', 'euc-kr', 'utf-8-sig']
    
    for encoding in encodings:
        try:
            with open(path, "r", encoding=encoding) as f:
                config = yaml.safe_load(f)
                logger.info(f"Config loaded: {path} (encoding: {encoding})")
                return config
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logger.error(f"Config load error: {e}")
            raise
    
    # 모든 인코딩 실패 시
    logger.error(f"Failed to load config with any encoding: {encodings}")
    raise ValueError("Could not decode config file")


def get_engine(cfg):
    """SQLAlchemy engine 생성"""
    try:
        db = cfg['db']
        dsn = f"postgresql://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['name']}"
        engine = create_engine(dsn, pool_pre_ping=True)
        logger.info("DB connection established")
        return engine
    except Exception as e:
        logger.error(f"DB connection error: {e}")
        raise


# ============================================================
# 데이터 로드
# ============================================================

def load_candles(engine, symbol: str, tf: str = "30m", days: int = 90) -> pd.DataFrame:
    """캔들 데이터 로드"""
    try:
        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)
        
        query = text("""
            SELECT 
                open_time,
                open,
                high,
                low,
                close,
                volume
            FROM candles
            WHERE symbol = :symbol 
              AND tf = :tf
              AND open_time >= :start_time
              AND open_time <= :end_time
            ORDER BY open_time ASC
        """)
        
        with engine.connect() as conn:
            df = pd.read_sql(query, conn, params={
                "symbol": symbol,
                "tf": tf,
                "start_time": start_time,
                "end_time": end_time
            })
        
        if df.empty:
            logger.warning(f"No data for {symbol} {tf}")
            return pd.DataFrame()
        
        df['open_time'] = pd.to_datetime(df['open_time'])
        df.set_index('open_time', inplace=True)
        
        logger.info(f"Loaded {len(df)} candles for {symbol} ({start_time.date()} ~ {end_time.date()})")
        
        return df
    
    except Exception as e:
        logger.error(f"Load candles error: {e}")
        return pd.DataFrame()


# ============================================================
# 지표 계산
# ============================================================

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """기술적 지표 계산"""
    if df.empty:
        return df
    
    # EMA
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
    
    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # Bollinger Bands
    df['bb_middle'] = df['close'].rolling(window=20).mean()
    bb_std = df['close'].rolling(window=20).std()
    df['bb_upper'] = df['bb_middle'] + (bb_std * 2)
    df['bb_lower'] = df['bb_middle'] - (bb_std * 2)
    
    # MACD
    ema_12 = df['close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = ema_12 - ema_26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    
    # ATR (Average True Range)
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    df['atr'] = true_range.rolling(14).mean()
    
    return df


# ============================================================
# 전략 정의
# ============================================================

class Strategy:
    """기본 전략 클래스"""
    
    def __init__(self, name: str, initial_capital: float = 10000, fee_rate: float = 0.0004):
        self.name = name
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.capital = initial_capital
        self.position = None
        self.trades = []
        
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """신호 생성 (서브클래스에서 구현)"""
        raise NotImplementedError
    
    def backtest(self, df: pd.DataFrame) -> Dict:
        """백테스트 실행"""
        df = self.generate_signals(df.copy())
        
        self.capital = self.initial_capital
        self.position = None
        self.trades = []
        
        for i in range(len(df)):
            if i < 1:
                continue
            
            current = df.iloc[i]
            
            # 진입 신호
            if current['signal'] == 1 and self.position is None:
                entry_price = current['close']
                stop_loss = current['stop_loss']
                take_profit = current['take_profit']
                
                # 수수료 적용
                entry_price_with_fee = entry_price * (1 + self.fee_rate)
                
                self.position = {
                    'entry_time': current.name,
                    'entry_price': entry_price_with_fee,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'direction': 'long'
                }
            
            # 청산 확인
            if self.position is not None:
                exit_triggered = False
                exit_price = None
                exit_reason = None
                
                # 손절/익절 체크
                if current['low'] <= self.position['stop_loss']:
                    exit_price = self.position['stop_loss']
                    exit_reason = 'stop_loss'
                    exit_triggered = True
                elif current['high'] >= self.position['take_profit']:
                    exit_price = self.position['take_profit']
                    exit_reason = 'take_profit'
                    exit_triggered = True
                
                if exit_triggered:
                    # 수수료 적용
                    exit_price_with_fee = exit_price * (1 - self.fee_rate)
                    
                    # 수익률 계산
                    pnl = exit_price_with_fee - self.position['entry_price']
                    pnl_pct = (pnl / self.position['entry_price']) * 100
                    
                    # 거래 기록
                    trade = {
                        'entry_time': self.position['entry_time'],
                        'entry_price': self.position['entry_price'],
                        'exit_time': current.name,
                        'exit_price': exit_price_with_fee,
                        'exit_reason': exit_reason,
                        'pnl': pnl,
                        'pnl_pct': pnl_pct
                    }
                    self.trades.append(trade)
                    
                    # 자본 업데이트
                    self.capital += pnl
                    
                    # 포지션 청산
                    self.position = None
        
        return self.calculate_metrics()
    
    def calculate_metrics(self) -> Dict:
        """성과 지표 계산"""
        if not self.trades:
            return {
                'strategy': self.name,
                'total_trades': 0,
                'win_rate': 0,
                'total_return': 0,
                'total_return_pct': 0,
                'profit_factor': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'max_drawdown': 0,
                'sharpe_ratio': 0
            }
        
        trades_df = pd.DataFrame(self.trades)
        
        # 기본 통계
        total_trades = len(trades_df)
        wins = trades_df[trades_df['pnl'] > 0]
        losses = trades_df[trades_df['pnl'] < 0]
        
        win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0
        
        # 수익률
        total_return = self.capital - self.initial_capital
        total_return_pct = (total_return / self.initial_capital) * 100
        
        # Profit Factor
        total_profit = wins['pnl'].sum() if not wins.empty else 0
        total_loss = abs(losses['pnl'].sum()) if not losses.empty else 0
        profit_factor = (total_profit / total_loss) if total_loss > 0 else 0
        
        # 평균 손익
        avg_win = wins['pnl'].mean() if not wins.empty else 0
        avg_loss = losses['pnl'].mean() if not losses.empty else 0
        
        # Maximum Drawdown
        cumulative = trades_df['pnl'].cumsum()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max)
        max_drawdown = drawdown.min()
        max_drawdown_pct = (max_drawdown / self.initial_capital) * 100 if self.initial_capital > 0 else 0
        
        # Sharpe Ratio (간소화 버전)
        returns = trades_df['pnl_pct']
        sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(252) if returns.std() > 0 else 0
        
        return {
            'strategy': self.name,
            'total_trades': total_trades,
            'win_rate': round(win_rate, 2),
            'total_return': round(total_return, 2),
            'total_return_pct': round(total_return_pct, 2),
            'profit_factor': round(profit_factor, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'max_drawdown': round(max_drawdown, 2),
            'max_drawdown_pct': round(max_drawdown_pct, 2),
            'sharpe_ratio': round(sharpe_ratio, 2)
        }


# ============================================================
# 전략 구현
# ============================================================

class EMACrossStrategy(Strategy):
    """EMA 크로스 전략"""
    
    def __init__(self):
        super().__init__("EMA Cross (9/21)")
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['signal'] = 0
        df['stop_loss'] = 0.0  # float으로 초기화
        df['take_profit'] = 0.0  # float으로 초기화
        
        # EMA 9가 EMA 21을 상향 돌파
        df.loc[(df['ema_9'] > df['ema_21']) & 
               (df['ema_9'].shift(1) <= df['ema_21'].shift(1)), 'signal'] = 1
        
        # 손절/익절
        for i in range(len(df)):
            if df.iloc[i]['signal'] == 1:
                entry_price = df.iloc[i]['close']
                atr = df.iloc[i]['atr']
                df.at[df.index[i], 'stop_loss'] = float(entry_price - (atr * 1.5))
                df.at[df.index[i], 'take_profit'] = float(entry_price + (atr * 3))
        
        return df


class RSIStrategy(Strategy):
    """RSI 전략"""
    
    def __init__(self):
        super().__init__("RSI (30/70)")
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['signal'] = 0
        df['stop_loss'] = 0.0
        df['take_profit'] = 0.0
        
        # RSI 과매도에서 상승 (30 돌파)
        df.loc[(df['rsi'] > 30) & 
               (df['rsi'].shift(1) <= 30), 'signal'] = 1
        
        # 손절/익절
        for i in range(len(df)):
            if df.iloc[i]['signal'] == 1:
                entry_price = df.iloc[i]['close']
                atr = df.iloc[i]['atr']
                df.at[df.index[i], 'stop_loss'] = float(entry_price - (atr * 1.5))
                df.at[df.index[i], 'take_profit'] = float(entry_price + (atr * 2.5))
        
        return df


class BollingerStrategy(Strategy):
    """볼린저 밴드 전략"""
    
    def __init__(self):
        super().__init__("Bollinger Bands")
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['signal'] = 0
        df['stop_loss'] = 0.0
        df['take_profit'] = 0.0
        
        # 하단 밴드 터치 후 반등
        df.loc[(df['close'] < df['bb_lower']) & 
               (df['close'].shift(1) >= df['bb_lower'].shift(1)), 'signal'] = 1
        
        # 손절/익절
        for i in range(len(df)):
            if df.iloc[i]['signal'] == 1:
                entry_price = df.iloc[i]['close']
                bb_middle = df.iloc[i]['bb_middle']
                bb_lower = df.iloc[i]['bb_lower']
                
                df.at[df.index[i], 'stop_loss'] = float(bb_lower * 0.98)
                df.at[df.index[i], 'take_profit'] = float(bb_middle)
        
        return df


class MACDStrategy(Strategy):
    """MACD 전략"""
    
    def __init__(self):
        super().__init__("MACD Cross")
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['signal'] = 0
        df['stop_loss'] = 0.0
        df['take_profit'] = 0.0
        
        # MACD가 시그널을 상향 돌파
        df.loc[(df['macd'] > df['macd_signal']) & 
               (df['macd'].shift(1) <= df['macd_signal'].shift(1)), 'signal'] = 1
        
        # 손절/익절
        for i in range(len(df)):
            if df.iloc[i]['signal'] == 1:
                entry_price = df.iloc[i]['close']
                atr = df.iloc[i]['atr']
                df.at[df.index[i], 'stop_loss'] = float(entry_price - (atr * 2))
                df.at[df.index[i], 'take_profit'] = float(entry_price + (atr * 3))
        
        return df


class ComboStrategy(Strategy):
    """복합 전략 (EMA + RSI)"""
    
    def __init__(self):
        super().__init__("EMA + RSI Combo")
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['signal'] = 0
        df['stop_loss'] = 0.0
        df['take_profit'] = 0.0
        
        # EMA 골든 크로스 + RSI 과매도 탈출
        df.loc[(df['ema_9'] > df['ema_21']) & 
               (df['ema_9'].shift(1) <= df['ema_21'].shift(1)) &
               (df['rsi'] > 30) &
               (df['rsi'] < 70), 'signal'] = 1
        
        # 손절/익절
        for i in range(len(df)):
            if df.iloc[i]['signal'] == 1:
                entry_price = df.iloc[i]['close']
                atr = df.iloc[i]['atr']
                df.at[df.index[i], 'stop_loss'] = float(entry_price - (atr * 1.5))
                df.at[df.index[i], 'take_profit'] = float(entry_price + (atr * 3.5))
        
        return df


# ============================================================
# 메인 실행
# ============================================================

def run_backtest(symbol: str = "BTCUSDT", tf: str = "30m", days: int = 90, config_path: str = "config.yaml"):
    """백테스트 실행"""
    
    print("=" * 60)
    print("30분봉 전략 백테스트")
    print("=" * 60)
    print(f"심볼: {symbol}")
    print(f"타임프레임: {tf}")
    print(f"기간: {days}일")
    print("=" * 60)
    print()
    
    # 설정 및 DB
    config = load_config(config_path)
    engine = get_engine(config)
    
    # 데이터 로드
    print("Downloading candle data...")
    df = load_candles(engine, symbol, tf, days)
    
    if df.empty:
        print("ERROR: No data available")
        return
    
    # 지표 계산
    print("Calculating indicators...")
    df = calculate_indicators(df)
    
    # 전략 목록
    strategies = [
        EMACrossStrategy(),
        RSIStrategy(),
        BollingerStrategy(),
        MACDStrategy(),
        ComboStrategy()
    ]
    
    # 백테스트 실행
    print("\nRunning backtest...\n")
    results = []
    
    for strategy in strategies:
        print(f"Testing {strategy.name}...", end=" ")
        metrics = strategy.backtest(df)
        results.append(metrics)
        print(f"Done ({metrics['total_trades']} trades)")
    
    # 결과 출력
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('total_return_pct', ascending=False)
    
    # 포맷팅
    print(f"\n{'Strategy':<20} {'Trades':<8} {'Win%':<8} {'Return%':<10} {'PF':<6} {'Sharpe':<8} {'MDD%':<8}")
    print("-" * 90)
    
    for _, row in results_df.iterrows():
        print(f"{row['strategy']:<20} "
              f"{row['total_trades']:<8} "
              f"{row['win_rate']:<8.1f} "
              f"{row['total_return_pct']:<10.2f} "
              f"{row['profit_factor']:<6.2f} "
              f"{row['sharpe_ratio']:<8.2f} "
              f"{row['max_drawdown_pct']:<8.2f}")
    
    print("\n" + "=" * 60)
    print("BEST STRATEGY")
    print("=" * 60)
    
    best = results_df.iloc[0]
    print(f"\nStrategy: {best['strategy']}")
    print(f"Total Trades: {best['total_trades']}")
    print(f"Win Rate: {best['win_rate']}%")
    print(f"Total Return: ${best['total_return']:.2f} ({best['total_return_pct']:.2f}%)")
    print(f"Profit Factor: {best['profit_factor']:.2f}")
    print(f"Avg Win: ${best['avg_win']:.2f}")
    print(f"Avg Loss: ${best['avg_loss']:.2f}")
    print(f"Max Drawdown: ${best['max_drawdown']:.2f} ({best['max_drawdown_pct']:.2f}%)")
    print(f"Sharpe Ratio: {best['sharpe_ratio']:.2f}")
    
    print("\n" + "=" * 60)
    print("Complete!")
    print("=" * 60)


def main():
    """CLI 인터페이스"""
    parser = argparse.ArgumentParser(
        description='30분봉 전략 백테스트',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # BTCUSDT 90일 백테스트
  python backtest_tf30m.py
  
  # ETHUSDT 180일 백테스트
  python backtest_tf30m.py --symbol ETHUSDT --days 180
  
  # 1시간봉으로 백테스트
  python backtest_tf30m.py --tf 1h --days 365
        """
    )
    
    parser.add_argument('--symbol', '-s', default='BTCUSDT', help='심볼 (기본값: BTCUSDT)')
    parser.add_argument('--tf', default='30m', help='타임프레임 (기본값: 30m)')
    parser.add_argument('--days', '-d', type=int, default=90, help='백테스트 기간 (일) (기본값: 90)')
    parser.add_argument('--config', '-c', default='config.yaml', help='설정 파일 경로')
    
    args = parser.parse_args()
    
    try:
        run_backtest(
            symbol=args.symbol,
            tf=args.tf,
            days=args.days,
            config_path=args.config
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
