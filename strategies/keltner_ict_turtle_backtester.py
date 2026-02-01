"""
Keltner ICT Turtle Strategy Backtester

현대화된 터틀 트레이딩 전략:
- 켈트너 채널 (동적)
- ICT Order Blocks
- Fair Value Gaps
- 피보나치 익절
- 다단계 포지션 관리
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import yaml
import argparse
from typing import List, Tuple, Optional, Dict
import json
from dataclasses import dataclass, asdict
import logging

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(f'backtest_{datetime.now().strftime("%Y%m%d")}.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """거래 기록"""
    symbol: str
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime]
    exit_price: Optional[float]
    size: float
    direction: str  # 'long' or 'short'
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    exit_reason: Optional[str]
    pnl: Optional[float]
    pnl_pct: Optional[float]
    
    def to_dict(self):
        d = asdict(self)
        # datetime을 문자열로 변환
        d['entry_time'] = self.entry_time.isoformat() if self.entry_time else None
        d['exit_time'] = self.exit_time.isoformat() if self.exit_time else None
        return d


@dataclass
class BacktestResult:
    """백테스트 결과"""
    symbol: str
    start_date: datetime
    end_date: datetime
    initial_capital: float
    final_capital: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    total_pnl_pct: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    max_win: float
    max_loss: float
    trades: List[Trade]


class KeltnerICTTurtle:
    """켈트너 ICT 터틀 전략"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self.load_config(config_path)
        self.db_conn = None
        
        # 전략 파라미터
        self.ema_period = 20
        self.atr_period = 20
        self.atr_multiplier = 2.5
        self.donchian_period = 55  # 터틀 원본
        
        # 리스크 관리
        self.risk_per_trade = 0.02  # 계좌의 2%
        self.max_positions = 4  # 최대 동시 포지션
        
        # ICT 파라미터
        self.ob_lookback = 10  # Order Block 탐색 기간
        self.fvg_min_gap = 0.001  # FVG 최소 갭 (0.1%)
        
        logger.info("켈트너 ICT 터틀 전략 초기화 완료")
    
    def load_config(self, config_path: str) -> dict:
        """설정 파일 로드"""
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    
    def get_db_connection(self):
        """DB 연결"""
        if not self.db_conn or self.db_conn.closed:
            db_config = self.config.get('db', {})
            self.db_conn = psycopg2.connect(
                host=db_config.get('host', 'localhost'),
                port=db_config.get('port', 5432),
                database=db_config.get('name', 'marketdb'),
                user=db_config.get('user', 'trader'),
                password=db_config.get('password', ''),
                client_encoding='utf8'
            )
        return self.db_conn
    
    def get_available_symbols(self, tf: str = '1h') -> List[str]:
        """사용 가능한 심볼 목록"""
        conn = self.get_db_connection()
        query = """
            SELECT DISTINCT symbol 
            FROM candles 
            WHERE tf = %s
            ORDER BY symbol
        """
        with conn.cursor() as cur:
            cur.execute(query, (tf,))
            return [row[0] for row in cur.fetchall()]
    
    def load_data(
        self, 
        symbol: str, 
        tf: str, 
        start_date: datetime, 
        end_date: datetime
    ) -> pd.DataFrame:
        """DB에서 데이터 로드"""
        conn = self.get_db_connection()
        
        query = """
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE symbol = %s AND tf = %s
              AND open_time >= %s AND open_time <= %s
            ORDER BY open_time ASC
        """
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (symbol, tf, start_date, end_date))
            data = cur.fetchall()
        
        if not data:
            logger.warning(f"데이터 없음: {symbol} {tf} ({start_date} ~ {end_date})")
            return pd.DataFrame()
        
        df = pd.DataFrame(data)
        logger.info(f"✅ {symbol} {tf}: {len(df):,}개 캔들 로드")
        
        return df
    
    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """ATR 계산"""
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = true_range.rolling(window=period).mean()
        
        return atr
    
    def calculate_keltner_channel(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """켈트너 채널 계산"""
        # EMA
        ema = df['close'].ewm(span=self.ema_period, adjust=False).mean()
        
        # ATR
        atr = self.calculate_atr(df, self.atr_period)
        
        # 채널
        upper = ema + (self.atr_multiplier * atr)
        lower = ema - (self.atr_multiplier * atr)
        
        return ema, upper, lower
    
    def identify_order_blocks(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """Order Blocks 식별"""
        bullish_ob = pd.Series(False, index=df.index)
        bearish_ob = pd.Series(False, index=df.index)
        
        for i in range(self.ob_lookback, len(df) - 1):
            # Bullish OB: 강한 상승 직전의 마지막 하락 캔들
            if (df['close'].iloc[i] > df['open'].iloc[i] and  # 현재 상승
                df['close'].iloc[i-1] < df['open'].iloc[i-1] and  # 이전 하락
                df['close'].iloc[i+1] > df['close'].iloc[i]):  # 다음도 상승
                bullish_ob.iloc[i-1] = True
            
            # Bearish OB: 강한 하락 직전의 마지막 상승 캔들
            if (df['close'].iloc[i] < df['open'].iloc[i] and  # 현재 하락
                df['close'].iloc[i-1] > df['open'].iloc[i-1] and  # 이전 상승
                df['close'].iloc[i+1] < df['close'].iloc[i]):  # 다음도 하락
                bearish_ob.iloc[i-1] = True
        
        return bullish_ob, bearish_ob
    
    def identify_fvg(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """Fair Value Gaps 식별"""
        bullish_fvg = pd.Series(False, index=df.index)
        bearish_fvg = pd.Series(False, index=df.index)
        
        for i in range(1, len(df) - 1):
            # Bullish FVG: 캔들 i-1의 high < 캔들 i+1의 low
            gap_size = (df['low'].iloc[i+1] - df['high'].iloc[i-1]) / df['close'].iloc[i]
            if gap_size > self.fvg_min_gap:
                bullish_fvg.iloc[i] = True
            
            # Bearish FVG: 캔들 i-1의 low > 캔들 i+1의 high
            gap_size = (df['low'].iloc[i-1] - df['high'].iloc[i+1]) / df['close'].iloc[i]
            if gap_size > self.fvg_min_gap:
                bearish_fvg.iloc[i] = True
        
        return bullish_fvg, bearish_fvg
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """모든 지표 계산"""
        # 켈트너 채널
        df['ema'], df['keltner_upper'], df['keltner_lower'] = self.calculate_keltner_channel(df)
        
        # ATR
        df['atr'] = self.calculate_atr(df, self.atr_period)
        
        # 돈치안 채널 (터틀 원본)
        df['donchian_high'] = df['high'].rolling(window=self.donchian_period).max()
        df['donchian_low'] = df['low'].rolling(window=self.donchian_period).min()
        
        # Order Blocks
        df['bullish_ob'], df['bearish_ob'] = self.identify_order_blocks(df)
        
        # Fair Value Gaps
        df['bullish_fvg'], df['bearish_fvg'] = self.identify_fvg(df)
        
        # 볼륨
        df['volume_ma'] = df['volume'].rolling(window=20).mean()
        
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        df['rsi'] = 100 - (100 / (1 + gain / loss))
        
        return df
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """매매 신호 생성"""
        df['signal'] = 0.0  # 0: 없음, 1: Long, -1: Short (float 타입)
        
        for i in range(self.donchian_period, len(df)):
            # Long 신호
            long_conditions = [
                df['close'].iloc[i] > df['keltner_upper'].iloc[i],  # 켈트너 상단 돌파
                df['close'].iloc[i] > df['donchian_high'].iloc[i-1],  # 돈치안 돌파
                df['volume'].iloc[i] > df['volume_ma'].iloc[i] * 1.3,  # 볼륨 확인
                df['rsi'].iloc[i] < 70,  # 과매수 필터
            ]
            
            # ICT 조건 (선택적 - 있으면 더 좋음)
            has_bullish_structure = (
                df['bullish_ob'].iloc[i-self.ob_lookback:i].any() or
                df['bullish_fvg'].iloc[i-self.ob_lookback:i].any()
            )
            
            if all(long_conditions):
                if has_bullish_structure:
                    df.loc[df.index[i], 'signal'] = 1.0  # 강한 Long
                else:
                    df.loc[df.index[i], 'signal'] = 0.5  # 약한 Long (스킵 가능)
            
            # Short 신호
            short_conditions = [
                df['close'].iloc[i] < df['keltner_lower'].iloc[i],
                df['close'].iloc[i] < df['donchian_low'].iloc[i-1],
                df['volume'].iloc[i] > df['volume_ma'].iloc[i] * 1.3,
                df['rsi'].iloc[i] > 30,
            ]
            
            has_bearish_structure = (
                df['bearish_ob'].iloc[i-self.ob_lookback:i].any() or
                df['bearish_fvg'].iloc[i-self.ob_lookback:i].any()
            )
            
            if all(short_conditions):
                if has_bearish_structure:
                    df.loc[df.index[i], 'signal'] = -1.0  # 강한 Short
                else:
                    df.loc[df.index[i], 'signal'] = -0.5  # 약한 Short
        
        return df
    
    def backtest(
        self,
        symbol: str,
        tf: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float = 10000.0
    ) -> BacktestResult:
        """백테스트 실행"""
        logger.info(f"\n{'='*70}")
        logger.info(f"백테스트: {symbol} {tf}")
        logger.info(f"기간: {start_date} ~ {end_date}")
        logger.info(f"초기 자본: ${initial_capital:,.2f}")
        logger.info(f"{'='*70}")
        
        # 데이터 로드
        df = self.load_data(symbol, tf, start_date, end_date)
        if df.empty:
            return None
        
        # 지표 계산
        df = self.calculate_indicators(df)
        df = self.generate_signals(df)
        
        # 백테스트 변수
        capital = initial_capital
        position = None  # 현재 포지션
        trades = []
        equity_curve = [initial_capital]
        
        # 거래 실행
        for i in range(self.donchian_period, len(df)):
            current_bar = df.iloc[i]
            
            # 포지션 없을 때: 진입 신호 확인
            if position is None:
                signal = current_bar['signal']
                
                # Long 진입
                if signal == 1.0:
                    # 포지션 사이즈 계산 (리스크 2%)
                    risk_amount = capital * self.risk_per_trade
                    atr = current_bar['atr']
                    stop_distance = 2.5 * atr
                    position_size = risk_amount / stop_distance
                    
                    # 진입
                    entry_price = current_bar['close']
                    stop_loss = entry_price - stop_distance
                    
                    # 피보나치 익절 레벨
                    tp_distance = entry_price - stop_loss
                    take_profit_1 = entry_price + (tp_distance * 1.618)
                    take_profit_2 = entry_price + (tp_distance * 2.618)
                    take_profit_3 = entry_price + (tp_distance * 4.236)
                    
                    position = Trade(
                        symbol=symbol,
                        entry_time=current_bar['open_time'],
                        entry_price=entry_price,
                        exit_time=None,
                        exit_price=None,
                        size=position_size,
                        direction='long',
                        stop_loss=stop_loss,
                        take_profit_1=take_profit_1,
                        take_profit_2=take_profit_2,
                        take_profit_3=take_profit_3,
                        exit_reason=None,
                        pnl=None,
                        pnl_pct=None
                    )
                    
                    logger.debug(f"Long 진입: {entry_price:.2f} @ {current_bar['open_time']}")
                
                # Short 진입 (동일 로직)
                elif signal == -1.0:
                    risk_amount = capital * self.risk_per_trade
                    atr = current_bar['atr']
                    stop_distance = 2.5 * atr
                    position_size = risk_amount / stop_distance
                    
                    entry_price = current_bar['close']
                    stop_loss = entry_price + stop_distance
                    
                    tp_distance = stop_loss - entry_price
                    take_profit_1 = entry_price - (tp_distance * 1.618)
                    take_profit_2 = entry_price - (tp_distance * 2.618)
                    take_profit_3 = entry_price - (tp_distance * 4.236)
                    
                    position = Trade(
                        symbol=symbol,
                        entry_time=current_bar['open_time'],
                        entry_price=entry_price,
                        exit_time=None,
                        exit_price=None,
                        size=position_size,
                        direction='short',
                        stop_loss=stop_loss,
                        take_profit_1=take_profit_1,
                        take_profit_2=take_profit_2,
                        take_profit_3=take_profit_3,
                        exit_reason=None,
                        pnl=None,
                        pnl_pct=None
                    )
                    
                    logger.debug(f"Short 진입: {entry_price:.2f} @ {current_bar['open_time']}")
            
            # 포지션 있을 때: 청산 조건 확인
            else:
                exit_price = None
                exit_reason = None
                
                if position.direction == 'long':
                    # 손절
                    if current_bar['low'] <= position.stop_loss:
                        exit_price = position.stop_loss
                        exit_reason = 'stop_loss'
                    
                    # 익절
                    elif current_bar['high'] >= position.take_profit_1:
                        exit_price = position.take_profit_1
                        exit_reason = 'take_profit_1'
                    
                    # 추세 반전 (켈트너 하단 이탈)
                    elif current_bar['close'] < current_bar['keltner_lower']:
                        exit_price = current_bar['close']
                        exit_reason = 'trend_reversal'
                
                else:  # short
                    if current_bar['high'] >= position.stop_loss:
                        exit_price = position.stop_loss
                        exit_reason = 'stop_loss'
                    
                    elif current_bar['low'] <= position.take_profit_1:
                        exit_price = position.take_profit_1
                        exit_reason = 'take_profit_1'
                    
                    elif current_bar['close'] > current_bar['keltner_upper']:
                        exit_price = current_bar['close']
                        exit_reason = 'trend_reversal'
                
                # 청산 실행
                if exit_price:
                    position.exit_time = current_bar['open_time']
                    position.exit_price = exit_price
                    position.exit_reason = exit_reason
                    
                    # PnL 계산
                    if position.direction == 'long':
                        position.pnl = (exit_price - position.entry_price) * position.size
                    else:
                        position.pnl = (position.entry_price - exit_price) * position.size
                    
                    position.pnl_pct = (position.pnl / capital) * 100
                    
                    # 자본 업데이트
                    capital += position.pnl
                    
                    logger.debug(f"청산: {exit_price:.2f} | PnL: ${position.pnl:.2f} ({position.pnl_pct:.2f}%) | {exit_reason}")
                    
                    trades.append(position)
                    position = None
            
            # 자본 기록
            equity_curve.append(capital)
        
        # 열린 포지션 강제 청산
        if position:
            position.exit_time = df.iloc[-1]['open_time']
            position.exit_price = df.iloc[-1]['close']
            position.exit_reason = 'end_of_data'
            
            if position.direction == 'long':
                position.pnl = (position.exit_price - position.entry_price) * position.size
            else:
                position.pnl = (position.entry_price - position.exit_price) * position.size
            
            position.pnl_pct = (position.pnl / capital) * 100
            capital += position.pnl
            trades.append(position)
        
        # 결과 계산
        if not trades:
            logger.warning("거래 없음")
            return None
        
        winning_trades = [t for t in trades if t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl <= 0]
        
        total_pnl = sum(t.pnl for t in trades)
        win_rate = len(winning_trades) / len(trades) * 100 if trades else 0
        
        avg_win = sum(t.pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t.pnl for t in losing_trades) / len(losing_trades) if losing_trades else 0
        
        max_win = max((t.pnl for t in winning_trades), default=0)
        max_loss = min((t.pnl for t in losing_trades), default=0)
        
        # Drawdown 계산
        equity_series = pd.Series(equity_curve)
        running_max = equity_series.expanding().max()
        drawdown = equity_series - running_max
        max_drawdown = drawdown.min()
        max_drawdown_pct = (max_drawdown / initial_capital) * 100
        
        # Sharpe Ratio
        returns = equity_series.pct_change().dropna()
        sharpe_ratio = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0
        
        # Profit Factor
        gross_profit = sum(t.pnl for t in winning_trades) if winning_trades else 0
        gross_loss = abs(sum(t.pnl for t in losing_trades)) if losing_trades else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        result = BacktestResult(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            final_capital=capital,
            total_trades=len(trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=win_rate,
            total_pnl=total_pnl,
            total_pnl_pct=(total_pnl / initial_capital) * 100,
            max_drawdown=max_drawdown,
            max_drawdown_pct=max_drawdown_pct,
            sharpe_ratio=sharpe_ratio,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
            max_win=max_win,
            max_loss=max_loss,
            trades=trades
        )
        
        return result
    
    def print_result(self, result: BacktestResult):
        """결과 출력"""
        print("\n" + "="*70)
        print(f"📊 백테스트 결과: {result.symbol}")
        print("="*70)
        print(f"기간: {result.start_date.date()} ~ {result.end_date.date()}")
        print(f"초기 자본: ${result.initial_capital:,.2f}")
        print(f"최종 자본: ${result.final_capital:,.2f}")
        print(f"총 수익: ${result.total_pnl:,.2f} ({result.total_pnl_pct:+.2f}%)")
        print()
        print(f"총 거래: {result.total_trades}")
        print(f"승리: {result.winning_trades} | 패배: {result.losing_trades}")
        print(f"승률: {result.win_rate:.2f}%")
        print()
        print(f"평균 수익: ${result.avg_win:,.2f}")
        print(f"평균 손실: ${result.avg_loss:,.2f}")
        print(f"최대 수익: ${result.max_win:,.2f}")
        print(f"최대 손실: ${result.max_loss:,.2f}")
        print()
        print(f"최대 낙폭: ${result.max_drawdown:,.2f} ({result.max_drawdown_pct:.2f}%)")
        print(f"샤프 비율: {result.sharpe_ratio:.2f}")
        print(f"프로핏 팩터: {result.profit_factor:.2f}")
        print("="*70)
    
    def save_result(self, result: BacktestResult, filename: str):
        """결과 저장"""
        output = {
            'summary': {
                'symbol': result.symbol,
                'start_date': result.start_date.isoformat(),
                'end_date': result.end_date.isoformat(),
                'initial_capital': result.initial_capital,
                'final_capital': result.final_capital,
                'total_pnl': result.total_pnl,
                'total_pnl_pct': result.total_pnl_pct,
                'total_trades': result.total_trades,
                'winning_trades': result.winning_trades,
                'losing_trades': result.losing_trades,
                'win_rate': result.win_rate,
                'max_drawdown': result.max_drawdown,
                'max_drawdown_pct': result.max_drawdown_pct,
                'sharpe_ratio': result.sharpe_ratio,
                'profit_factor': result.profit_factor,
                'avg_win': result.avg_win,
                'avg_loss': result.avg_loss,
                'max_win': result.max_win,
                'max_loss': result.max_loss
            },
            'trades': [t.to_dict() for t in result.trades]
        }
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        
        logger.info(f"✅ 결과 저장: {filename}")
    
    def run_multi_symbol_backtest(
        self,
        symbols: List[str],
        tf: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float = 10000.0
    ) -> Dict[str, BacktestResult]:
        """여러 심볼 백테스트"""
        results = {}
        
        for symbol in symbols:
            try:
                result = self.backtest(symbol, tf, start_date, end_date, initial_capital)
                if result:
                    results[symbol] = result
                    self.print_result(result)
            except Exception as e:
                logger.error(f"❌ {symbol} 백테스트 실패: {e}")
        
        return results


def main():
    """메인 실행"""
    parser = argparse.ArgumentParser(
        description='Keltner ICT Turtle Backtester',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 단일 심볼
  python keltner_ict_turtle.py --symbol BTCUSDT --tf 1h --start 2023-01-01 --end 2024-01-01
  
  # 여러 심볼
  python keltner_ict_turtle.py --symbols BTCUSDT ETHUSDT XRPUSDT --tf 1h --start 2023-01-01
  
  # 전체 심볼
  python keltner_ict_turtle.py --all-symbols --tf 1h --start 2023-01-01 --end 2024-01-01
  
  # 초기 자본 지정
  python keltner_ict_turtle.py --symbol BTCUSDT --capital 50000 --tf 4h
        """
    )
    
    # 심볼 옵션
    symbol_group = parser.add_mutually_exclusive_group(required=True)
    symbol_group.add_argument('--symbol', help='단일 심볼 (예: BTCUSDT)')
    symbol_group.add_argument('--symbols', nargs='+', help='여러 심볼 (예: BTCUSDT ETHUSDT)')
    symbol_group.add_argument('--all-symbols', action='store_true', help='DB의 모든 심볼')
    
    # 날짜 옵션
    parser.add_argument('--start', help='시작 날짜 (YYYY-MM-DD)', required=True)
    parser.add_argument('--end', help='종료 날짜 (YYYY-MM-DD, 기본: 현재)', 
                       default=datetime.now().strftime('%Y-%m-%d'))
    
    # 기타 옵션
    parser.add_argument('--tf', default='1h', help='타임프레임 (기본: 1h)')
    parser.add_argument('--capital', type=float, default=10000, help='초기 자본 (기본: 10000)')
    parser.add_argument('--output', help='결과 파일명 (JSON)')
    
    args = parser.parse_args()
    
    # 날짜 파싱
    start_date = datetime.strptime(args.start, '%Y-%m-%d')
    end_date = datetime.strptime(args.end, '%Y-%m-%d')
    
    # 백테스터 초기화
    backtester = KeltnerICTTurtle()
    
    # 심볼 결정
    if args.symbol:
        symbols = [args.symbol]
    elif args.symbols:
        symbols = args.symbols
    else:  # all-symbols
        symbols = backtester.get_available_symbols(args.tf)
        logger.info(f"전체 심볼: {len(symbols)}개")
    
    logger.info(f"\n📊 백테스트 설정:")
    logger.info(f"심볼: {', '.join(symbols)}")
    logger.info(f"타임프레임: {args.tf}")
    logger.info(f"기간: {args.start} ~ {args.end}")
    logger.info(f"초기 자본: ${args.capital:,.2f}")
    
    # 백테스트 실행
    if len(symbols) == 1:
        result = backtester.backtest(symbols[0], args.tf, start_date, end_date, args.capital)
        if result:
            backtester.print_result(result)
            
            if args.output:
                backtester.save_result(result, args.output)
    else:
        results = backtester.run_multi_symbol_backtest(
            symbols, args.tf, start_date, end_date, args.capital
        )
        
        # 전체 요약
        if results:
            print("\n" + "="*70)
            print("📊 전체 요약")
            print("="*70)
            
            total_pnl = sum(r.total_pnl for r in results.values())
            avg_win_rate = sum(r.win_rate for r in results.values()) / len(results)
            avg_sharpe = sum(r.sharpe_ratio for r in results.values()) / len(results)
            
            print(f"테스트 심볼: {len(results)}개")
            print(f"총 수익: ${total_pnl:,.2f}")
            print(f"평균 승률: {avg_win_rate:.2f}%")
            print(f"평균 샤프: {avg_sharpe:.2f}")
            print("="*70)
            
            # 상위 5개
            top_5 = sorted(results.items(), key=lambda x: x[1].total_pnl, reverse=True)[:5]
            print("\n🏆 상위 5개:")
            for i, (sym, res) in enumerate(top_5, 1):
                print(f"{i}. {sym}: ${res.total_pnl:,.2f} ({res.total_pnl_pct:+.2f}%) | 승률 {res.win_rate:.1f}%")
            
            if args.output:
                # 전체 결과 저장
                all_results = {
                    sym: {
                        'summary': {
                            'total_pnl': res.total_pnl,
                            'total_pnl_pct': res.total_pnl_pct,
                            'win_rate': res.win_rate,
                            'sharpe_ratio': res.sharpe_ratio,
                            'total_trades': res.total_trades
                        }
                    }
                    for sym, res in results.items()
                }
                
                with open(args.output, 'w', encoding='utf-8') as f:
                    json.dump(all_results, f, indent=2, ensure_ascii=False)
                
                logger.info(f"✅ 전체 결과 저장: {args.output}")


if __name__ == "__main__":
    main()
