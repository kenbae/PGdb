# -*- coding: utf-8 -*-
"""
Strategy Backtester - 전략 선택 가능한 백테스터

tradeBot의 전략 시스템을 사용하여 백테스트 수행
- EMA Cross, ICT, RSI, Bollinger, Keltner ICT Turtle, Pattern RAG 전략 지원
- 전략별 또는 통합 백테스트 가능
"""

import sys
import os
# PGdb 루트 경로 추가 (strategies 모듈 위치)
_pgdb_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _pgdb_dir)
# tradeBot 경로도 추가
sys.path.insert(0, os.path.join(_pgdb_dir, 'tradeBot'))

import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import yaml
import argparse
from typing import List, Dict, Optional, Tuple
import json
from dataclasses import dataclass, asdict, field
import logging

# 공용 전략 시스템 (PGdb/strategies/)
from strategies import (
    create_strategy_manager,
    StrategyManager,
    TradeSignal,
    KeltnerICTTurtle
)

# tradeBot DB override (전략 설정 단일 소스)
try:
    from sqlalchemy import create_engine
    from database.strategy_settings_repo import StrategySettingsRepo
except Exception:
    create_engine = None
    StrategySettingsRepo = None

# 로깅
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """거래 기록"""
    symbol: str
    strategy: str
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime]
    exit_price: Optional[float]
    size: float
    direction: str  # 'long' or 'short'
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: Optional[float]
    exit_reason: Optional[str]
    pnl: Optional[float]
    pnl_pct: Optional[float]
    confidence: float = 0.5
    reasons: List[str] = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d['entry_time'] = self.entry_time.isoformat() if self.entry_time else None
        d['exit_time'] = self.exit_time.isoformat() if self.exit_time else None
        return d


@dataclass
class BacktestResult:
    """백테스트 결과"""
    symbol: str
    strategy: str
    timeframe: str
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
    trades: List[Trade] = field(default_factory=list)


class StrategyBacktester:
    """전략 선택 가능한 백테스터"""

    # 전략 설명 (자동 등록된 전략에 설명 추가용)
    STRATEGY_DESCRIPTIONS = {
        'ema_cross': 'EMA Cross 전략 (정배열/역배열 + 크로스오버)',
        'ict': 'ICT 전략 (Order Block + Fair Value Gap)',
        'rsi': 'RSI 전략 (과매수/과매도 반전)',
        'bollinger': 'Bollinger Band 전략 (돌파/반전)',
        'keltner_ict_turtle': 'Keltner ICT Turtle 전략 (켈트너 + 돈치안 + ICT)',
        'pattern_rag': 'Pattern RAG 전략 (과거 패턴 학습 + OLLAMA AI 신호)',
        'ema_divergence_volume': 'EMA 이격 확대 + 볼륨 돌파 전략',
        'bb_adaptive_rsi': 'BB 3σ + Adaptive RSI 전략',
    }

    def __init__(self, config_path: str = "config.yaml"):
        self.config = self.load_config(config_path)
        self.db_conn = None

        # 전략 매니저 초기화 (DB 기반, config.yaml strategies 사용 안 함)
        repo = self._get_strategy_settings_repo()
        self.strategy_manager = create_strategy_manager(strategy_settings_repo=repo, config=self.config)

        # Keltner ICT Turtle 백테스터 (기존)
        self.keltner_backtester = KeltnerICTTurtle(config_path)

        # 리스크 관리
        self.risk_per_trade = self.config.get('trading', {}).get('risk_per_trade', 0.02)
        self.atr_multiplier = self.config.get('trading', {}).get('atr_multiplier', 2.5)

        logger.info(f"StrategyBacktester 초기화 완료")
        logger.info(f"등록된 전략: {list(self.get_available_strategies().keys())}")

    def _get_strategy_settings_repo(self):
        """DB 기반 StrategySettingsRepo 반환 (연결 실패 시 None)"""
        if create_engine is None or StrategySettingsRepo is None:
            return None
        try:
            db = (self.config or {}).get('db', {}) or {}
            db_url = (
                f"postgresql://{db.get('user')}:{db.get('password')}"
                f"@{db.get('host', 'localhost')}:{db.get('port', 5432)}/{db.get('name', 'marketdb')}"
            )
            engine = create_engine(db_url)
            return StrategySettingsRepo(engine)
        except Exception as e:
            logger.warning(f"⚠️ DB 전략 설정 조회 실패 → 기본값 사용: {e}")
            return None

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

    def get_available_strategies(self) -> Dict[str, str]:
        """
        사용 가능한 전략 목록 (strategy_manager에서 자동 로드)

        config.yaml의 strategies 섹션에 정의된 전략이 자동으로 포함됩니다.
        """
        strategies = {}

        # strategy_manager에 등록된 전략 가져오기
        for strategy in self.strategy_manager.get_all_strategies():
            name = strategy.get_name()
            # 설명이 있으면 사용, 없으면 기본 설명 생성
            desc = self.STRATEGY_DESCRIPTIONS.get(name, f'{name} 전략')
            strategies[name] = desc

        # keltner_ict_turtle은 별도 백테스터 사용 (항상 포함)
        if 'keltner_ict_turtle' not in strategies:
            strategies['keltner_ict_turtle'] = self.STRATEGY_DESCRIPTIONS.get(
                'keltner_ict_turtle', 'Keltner ICT Turtle 전략'
            )

        return strategies

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

        # Decimal → float 변환 (DB에서 numeric 타입으로 가져온 경우)
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = df[col].astype(float)

        logger.debug(f"✅ {symbol} {tf}: {len(df):,}개 캔들 로드")

        return df

    def _run_tradebot_strategy_backtest(
        self,
        strategy_name: str,
        symbol: str,
        tf: str,
        df: pd.DataFrame,
        initial_capital: float,
        risk_pct: float = 2.0,
        leverage: float = 1.0,
        ema_cross_exit_config: Optional[Dict] = None
    ) -> Optional[BacktestResult]:
        """
        tradeBot 전략으로 백테스트 실행

        Args:
            strategy_name: 전략 이름 (ema_cross, ict, rsi, bollinger)
            symbol: 심볼
            tf: 타임프레임
            df: OHLCV 데이터
            initial_capital: 초기 자본
            risk_pct: 리스크 비율 (%)
            leverage: 레버리지

        Returns:
            BacktestResult: 백테스트 결과
        """
        if df.empty or len(df) < 100:
            return None

        # 변수 초기화
        capital = initial_capital
        position = None
        trades = []
        equity_curve = [initial_capital]

        # 슬라이딩 윈도우로 신호 생성 및 거래
        window_size = 200  # 지표 계산용 최소 캔들

        for i in range(window_size, len(df)):
            # 현재까지의 데이터
            current_df = df.iloc[max(0, i - window_size):i + 1].copy()
            current_bar = df.iloc[i]

            # 포지션 없을 때: 신호 확인
            if position is None:
                try:
                    signals = self.strategy_manager.analyze_strategy(
                        strategy_name=strategy_name,
                        symbol=symbol,
                        timeframe=tf,
                        df=current_df,
                        apply_ai=False
                    )

                    if signals:
                        signal = signals[0]  # 첫 번째 신호 사용

                        # 포지션 사이즈 계산
                        risk_amount = capital * (risk_pct / 100)
                        stop_distance = abs(signal.entry_price - signal.stop_loss)
                        if stop_distance > 0:
                            position_size = (risk_amount / stop_distance) * leverage

                            # TP3 확인 (metadata에서)
                            tp3 = None
                            if signal.metadata and 'take_profit_3' in signal.metadata:
                                tp3 = signal.metadata['take_profit_3']

                            position = Trade(
                                symbol=symbol,
                                strategy=strategy_name,
                                entry_time=current_bar['open_time'],
                                entry_price=signal.entry_price,
                                exit_time=None,
                                exit_price=None,
                                size=position_size,
                                direction='long' if signal.signal_type == 'buy' else 'short',
                                stop_loss=signal.stop_loss,
                                take_profit_1=signal.take_profit_1,
                                take_profit_2=signal.take_profit_2 or signal.take_profit_1 * 1.5,
                                take_profit_3=tp3,
                                exit_reason=None,
                                pnl=None,
                                pnl_pct=None,
                                confidence=signal.confidence,
                                reasons=signal.reasons or []
                            )

                            logger.debug(f"{signal.signal_type.upper()} 진입: {signal.entry_price:.2f}")

                except Exception as e:
                    logger.debug(f"신호 분석 오류: {e}")
                    continue

            # 포지션 있을 때: 청산 조건 확인
            else:
                exit_price = None
                exit_reason = None
                partial_exit = False  # 부분 청산 여부
                partial_exit_pct = 0.0  # 부분 청산 비율

                # EMA Cross 전략 전용 청산 로직
                if strategy_name.lower() == 'ema_cross':
                    exit_price, exit_reason, partial_exit, partial_exit_pct = self._check_ema_cross_exit(
                        position, current_bar, df, idx, ema_cross_exit_config
                    )
                else:
                    # 기본 청산 로직
                    if position.direction == 'long':
                        # 손절
                        if current_bar['low'] <= position.stop_loss:
                            exit_price = position.stop_loss
                            exit_reason = 'stop_loss'
                        # 익절 1
                        elif current_bar['high'] >= position.take_profit_1:
                            exit_price = position.take_profit_1
                            exit_reason = 'take_profit_1'
                    else:  # short
                        if current_bar['high'] >= position.stop_loss:
                            exit_price = position.stop_loss
                            exit_reason = 'stop_loss'
                        elif current_bar['low'] <= position.take_profit_1:
                            exit_price = position.take_profit_1
                            exit_reason = 'take_profit_1'

                # 청산 실행
                if exit_price:
                    if partial_exit:
                        # 부분 청산 (TP1에서 N% 청산)
                        exit_size = position.size * (partial_exit_pct / 100)
                        remaining_size = position.size - exit_size
                        
                        # 부분 청산 PnL 계산
                        if position.direction == 'long':
                            partial_pnl = (exit_price - position.entry_price) * exit_size
                        else:
                            partial_pnl = (position.entry_price - exit_price) * exit_size
                        
                        # 부분 청산 거래 기록
                        partial_trade = Trade(
                            symbol=position.symbol,
                            strategy=position.strategy,
                            entry_time=position.entry_time,
                            entry_price=position.entry_price,
                            exit_time=current_bar['open_time'],
                            exit_price=exit_price,
                            size=exit_size,
                            direction=position.direction,
                            stop_loss=position.stop_loss,
                            take_profit_1=position.take_profit_1,
                            take_profit_2=position.take_profit_2,
                            take_profit_3=position.take_profit_3,
                            exit_reason=exit_reason,
                            pnl=partial_pnl,
                            pnl_pct=(partial_pnl / capital) * 100,
                            confidence=position.confidence,
                            reasons=position.reasons
                        )
                        trades.append(partial_trade)
                        capital += partial_pnl
                        
                        # 남은 포지션 업데이트
                        position.size = remaining_size
                        position.entry_price = exit_price  # 부분 청산 후 진입가 조정
                        logger.debug(f"📊 부분 청산: {exit_size:.4f} ({partial_exit_pct}%), 남은 수량: {remaining_size:.4f}")
                    else:
                        # 전체 청산
                        position.exit_time = current_bar['open_time']
                        position.exit_price = exit_price
                        position.exit_reason = exit_reason

                        # PnL 계산
                        if position.direction == 'long':
                            position.pnl = (exit_price - position.entry_price) * position.size
                        else:
                            position.pnl = (position.entry_price - exit_price) * position.size

                        position.pnl_pct = (position.pnl / capital) * 100
                        capital += position.pnl

                        trades.append(position)
                        position = None

            equity_curve.append(capital)

        # 열린 포지션 강제 청산
        if position:
            position.exit_time = df.iloc[-1]['open_time']
            position.exit_price = df.iloc[-1]['close']
            position.exit_reason = 'end_of_data'
            
            # PnL 계산
            if position.direction == 'long':
                position.pnl = (position.exit_price - position.entry_price) * position.size
            else:
                position.pnl = (position.entry_price - position.exit_price) * position.size
            
            position.pnl_pct = (position.pnl / capital) * 100
            capital += position.pnl
            trades.append(position)

        # 결과 계산
        return self._calculate_result(
            symbol=symbol,
            strategy=strategy_name,
            timeframe=tf,
            start_date=df.iloc[0]['open_time'],
            end_date=df.iloc[-1]['open_time'],
            initial_capital=initial_capital,
            final_capital=capital,
            trades=trades,
            equity_curve=equity_curve
        )

    def _check_ema_cross_exit(
        self,
        position: Trade,
        current_bar: pd.Series,
        df: pd.DataFrame,
        current_idx: int,
        exit_config: Optional[Dict] = None
    ) -> Tuple[Optional[float], Optional[str], bool, float]:
        """
        EMA Cross 전략 전용 청산 조건 체크

        Args:
            exit_config: EMA Cross 청산 규칙 설정
                - tp1_partial_exit_pct: TP1 부분 청산 비율 (기본값: 50.0)
                - use_trailing_stop: TP2 트레일링 스톱 사용 여부 (기본값: True)
                - exit_on_ema50_touch: EMA50 터치 시 청산 여부 (기본값: True)

        Returns:
            (exit_price, exit_reason, partial_exit, partial_exit_pct)
        """
        # 설정 기본값
        if exit_config is None:
            exit_config = {}
        tp1_partial_exit_pct = exit_config.get('tp1_partial_exit_pct', 50.0)
        use_trailing_stop = exit_config.get('use_trailing_stop', True)
        exit_on_ema50_touch = exit_config.get('exit_on_ema50_touch', True)
        
        # 포지션 상태 추적 (백테스트용)
        position_key = f"{position.entry_time}_{position.entry_price}"
        if not hasattr(self, '_position_states'):
            self._position_states = {}
        
        if position_key not in self._position_states:
            self._position_states[position_key] = {
                'tp1_reached': False,
                'tp2_reached': False,
                'original_stop_loss': position.stop_loss,
                'trailing_stop_price': None
            }
        
        state = self._position_states[position_key]
        current_price = current_bar['close']
        
        # EMA 50 가져오기 (이미 계산된 경우)
        ema_50 = None
        try:
            if 'ema50' in current_bar.index:
                ema_val = current_bar.get('ema50')
                if ema_val is not None and not pd.isna(ema_val):
                    ema_50 = float(ema_val)
        except Exception:
            pass
        
        if ema_50 is None and current_idx >= 50:
            # 수동 계산
            try:
                df_recent = df.iloc[max(0, current_idx-100):current_idx+1]
                if len(df_recent) >= 50:
                    closes = df_recent['close'].dropna()
                    if len(closes) >= 50:
                        ema_series = closes.ewm(span=50, adjust=False).mean()
                        ema_50 = float(ema_series.iloc[-1])
            except Exception:
                pass
        
        # 1. TP1 도달 체크 (부분 청산)
        if not state['tp1_reached'] and tp1_partial_exit_pct > 0:
            if position.direction == 'long':
                if current_bar['high'] >= position.take_profit_1:
                    state['tp1_reached'] = True
                    return position.take_profit_1, 'tp1_partial', True, tp1_partial_exit_pct
            else:  # short
                if current_bar['low'] <= position.take_profit_1:
                    state['tp1_reached'] = True
                    return position.take_profit_1, 'tp1_partial', True, tp1_partial_exit_pct
        
        # 2. TP2 도달 체크 (트레일링 스톱 활성화)
        if use_trailing_stop and state['tp1_reached'] and not state['tp2_reached']:
            tp2 = position.take_profit_2 or (position.take_profit_1 * 1.5)
            
            if position.direction == 'long':
                if current_bar['high'] >= tp2:
                    state['tp2_reached'] = True
                    # SL을 본절(진입가)로 이동
                    state['trailing_stop_price'] = position.entry_price
                    logger.debug(f"📊 TP2 도달: 트레일링 스톱 활성화 (SL → {position.entry_price:.2f})")
            else:  # short
                if current_bar['low'] <= tp2:
                    state['tp2_reached'] = True
                    state['trailing_stop_price'] = position.entry_price
                    logger.debug(f"📊 TP2 도달: 트레일링 스톱 활성화 (SL → {position.entry_price:.2f})")
        
        # 3. 트레일링 스톱 체크
        if use_trailing_stop and state['tp2_reached'] and state['trailing_stop_price']:
            if position.direction == 'long':
                if current_bar['low'] <= state['trailing_stop_price']:
                    return state['trailing_stop_price'], 'trailing_stop', False, 0.0
            else:  # short
                if current_bar['high'] >= state['trailing_stop_price']:
                    return state['trailing_stop_price'], 'trailing_stop', False, 0.0
        
        # 4. EMA50 터치 체크
        if exit_on_ema50_touch and ema_50:
            candle_open = current_bar['open']
            candle_close = current_bar['close']
            body_low = min(candle_open, candle_close)
            body_high = max(candle_open, candle_close)
            
            # EMA50이 캔들 몸통에 닿았는지 확인
            if body_low <= ema_50 <= body_high:
                if position.direction == 'long':
                    # 롱 포지션: EMA50 터치 시 익절
                    return current_price, 'ema50_touch', False, 0.0
                else:  # short
                    # 숏 포지션: EMA50 터치 시 익절
                    return current_price, 'ema50_touch', False, 0.0
        
        # 5. 기본 SL 체크 (트레일링 스톱이 활성화되지 않은 경우만)
        if not state['tp2_reached']:
            if position.direction == 'long':
                if current_bar['low'] <= position.stop_loss:
                    return position.stop_loss, 'stop_loss', False, 0.0
            else:  # short
                if current_bar['high'] >= position.stop_loss:
                    return position.stop_loss, 'stop_loss', False, 0.0
        
        return None, None, False, 0.0

    def _calculate_result(
        self,
        symbol: str,
        strategy: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float,
        final_capital: float,
        trades: List[Trade],
        equity_curve: List[float]
    ) -> Optional[BacktestResult]:
        """결과 계산"""
        if not trades:
            return None

        winning_trades = [t for t in trades if t.pnl and t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl and t.pnl <= 0]

        total_pnl = sum(t.pnl for t in trades if t.pnl)
        win_rate = len(winning_trades) / len(trades) * 100 if trades else 0

        avg_win = sum(t.pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t.pnl for t in losing_trades) / len(losing_trades) if losing_trades else 0

        max_win = max((t.pnl for t in winning_trades), default=0)
        max_loss = min((t.pnl for t in losing_trades), default=0)

        # Drawdown
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

        return BacktestResult(
            symbol=symbol,
            strategy=strategy,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            final_capital=final_capital,
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

    def backtest(
        self,
        symbol: str,
        tf: str,
        start_date: datetime,
        end_date: datetime,
        strategy: str = 'keltner_ict_turtle',
        initial_capital: float = 10000.0,
        risk_pct: float = 2.0,
        leverage: float = 1.0,
        ema_cross_exit_config: Optional[Dict] = None
    ) -> Optional[BacktestResult]:
        """
        백테스트 실행

        Args:
            symbol: 심볼
            tf: 타임프레임
            start_date: 시작일
            end_date: 종료일
            strategy: 전략 이름
            initial_capital: 초기 자본
            risk_pct: 리스크 비율 (%)
            leverage: 레버리지

        Returns:
            BacktestResult: 백테스트 결과
        """
        logger.info(f"백테스트: {symbol} {tf} | 전략: {strategy}")

        # 데이터 로드
        df = self.load_data(symbol, tf, start_date, end_date)
        if df.empty:
            return None

        # 전략에 따라 분기
        if strategy == 'keltner_ict_turtle':
            # 기존 Keltner ICT Turtle 사용
            result = self.keltner_backtester.backtest(
                symbol, tf, start_date, end_date, initial_capital
            )
            if result:
                # BacktestResult로 변환
                return BacktestResult(
                    symbol=result.symbol,
                    strategy='keltner_ict_turtle',
                    timeframe=tf,
                    start_date=result.start_date,
                    end_date=result.end_date,
                    initial_capital=result.initial_capital,
                    final_capital=result.final_capital,
                    total_trades=result.total_trades,
                    winning_trades=result.winning_trades,
                    losing_trades=result.losing_trades,
                    win_rate=result.win_rate,
                    total_pnl=result.total_pnl,
                    total_pnl_pct=result.total_pnl_pct,
                    max_drawdown=result.max_drawdown,
                    max_drawdown_pct=result.max_drawdown_pct,
                    sharpe_ratio=result.sharpe_ratio,
                    profit_factor=result.profit_factor,
                    avg_win=result.avg_win,
                    avg_loss=result.avg_loss,
                    max_win=result.max_win,
                    max_loss=result.max_loss,
                    trades=[]
                )
            return None

        elif self.strategy_manager.get_strategy(strategy) is not None:
            # tradeBot 전략 시스템 사용 (자동 감지)

            # pattern_rag 전략은 백테스트 모드로 실행 (AI 스킵)
            if strategy == 'pattern_rag':
                pattern_rag_strategy = self.strategy_manager.get_strategy('pattern_rag')
                if pattern_rag_strategy:
                    pattern_rag_strategy.backtest_mode = True
                    logger.info(f"Pattern RAG: 백테스트 모드 활성화 (AI 스킵)")

            return self._run_tradebot_strategy_backtest(
                strategy_name=strategy,
                symbol=symbol,
                tf=tf,
                df=df,
                initial_capital=initial_capital,
                risk_pct=risk_pct,
                leverage=leverage,
                ema_cross_exit_config=ema_cross_exit_config
            )

        else:
            logger.error(f"알 수 없는 전략: {strategy}")
            return None

    def run_multi_strategy_backtest(
        self,
        symbol: str,
        tf: str,
        start_date: datetime,
        end_date: datetime,
        strategies: List[str],
        initial_capital: float = 10000.0
    ) -> Dict[str, BacktestResult]:
        """여러 전략 동시 백테스트"""
        results = {}

        for strategy in strategies:
            try:
                result = self.backtest(
                    symbol, tf, start_date, end_date,
                    strategy=strategy,
                    initial_capital=initial_capital
                )
                if result:
                    results[strategy] = result
            except Exception as e:
                logger.error(f"{strategy} 백테스트 실패: {e}")

        return results

    def print_result(self, result: BacktestResult):
        """결과 출력"""
        print("\n" + "=" * 70)
        print(f"📊 백테스트 결과: {result.symbol} | 전략: {result.strategy}")
        print("=" * 70)
        print(f"타임프레임: {result.timeframe}")
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
        print("=" * 70)


def main():
    """CLI 실행"""
    parser = argparse.ArgumentParser(
        description='Strategy Backtester - 전략 선택 가능한 백테스터',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 기본 (Keltner ICT Turtle)
  python strategy_backtester.py --symbol BTCUSDT --tf 1h --start 2024-01-01

  # 특정 전략 선택
  python strategy_backtester.py --symbol BTCUSDT --strategy ict --tf 15m --start 2024-01-01

  # 여러 전략 비교
  python strategy_backtester.py --symbol BTCUSDT --strategies ema_cross ict rsi --tf 1h

  # 사용 가능한 전략 목록
  python strategy_backtester.py --list-strategies
        """
    )

    parser.add_argument('--list-strategies', action='store_true', help='사용 가능한 전략 목록')
    parser.add_argument('--symbol', help='심볼 (예: BTCUSDT)')
    parser.add_argument('--strategy', help='단일 전략')
    parser.add_argument('--strategies', nargs='+', help='여러 전략 비교')
    parser.add_argument('--start', help='시작 날짜 (YYYY-MM-DD)')
    parser.add_argument('--end', help='종료 날짜 (YYYY-MM-DD)', default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--tf', default='1h', help='타임프레임 (기본: 1h)')
    parser.add_argument('--capital', type=float, default=10000, help='초기 자본')

    args = parser.parse_args()

    backtester = StrategyBacktester()

    # 전략 목록 출력
    if args.list_strategies:
        print("\n📋 사용 가능한 전략:")
        print("-" * 60)
        for name, desc in backtester.get_available_strategies().items():
            print(f"  {name:20} : {desc}")
        print("-" * 60)
        return

    # 필수 인자 확인
    if not args.symbol or not args.start:
        parser.print_help()
        return

    start_date = datetime.strptime(args.start, '%Y-%m-%d')
    end_date = datetime.strptime(args.end, '%Y-%m-%d')

    # 여러 전략 비교
    if args.strategies:
        results = backtester.run_multi_strategy_backtest(
            args.symbol, args.tf, start_date, end_date,
            strategies=args.strategies,
            initial_capital=args.capital
        )

        for strategy, result in results.items():
            backtester.print_result(result)

        # 비교 요약
        if results:
            print("\n📊 전략 비교 요약:")
            print("-" * 80)
            print(f"{'전략':<20} {'수익률':>10} {'승률':>10} {'샤프':>10} {'PF':>10}")
            print("-" * 80)
            for strategy, result in sorted(results.items(), key=lambda x: x[1].total_pnl_pct, reverse=True):
                print(f"{strategy:<20} {result.total_pnl_pct:>9.2f}% {result.win_rate:>9.2f}% {result.sharpe_ratio:>10.2f} {result.profit_factor:>10.2f}")
            print("-" * 80)

    # 단일 전략
    else:
        strategy = args.strategy or 'keltner_ict_turtle'
        result = backtester.backtest(
            args.symbol, args.tf, start_date, end_date,
            strategy=strategy,
            initial_capital=args.capital
        )

        if result:
            backtester.print_result(result)


if __name__ == "__main__":
    main()
