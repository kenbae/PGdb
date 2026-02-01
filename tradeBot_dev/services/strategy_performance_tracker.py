"""
Strategy Performance Tracker

심볼별, 전략별 성과를 추적하고 평가하는 시스템
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class StrategyPerformanceTracker:
    """전략 성과 추적기"""

    def __init__(self, db_engine):
        """
        초기화

        Args:
            db_engine: SQLAlchemy Engine
        """
        self.engine = db_engine

    def calculate_performance(
        self,
        symbol: str,
        strategy_name: str,
        timeframe: str,
        period_type: str = 'medium',  # 'short' (10), 'medium' (30), 'long' (100)
        min_trades: int = 5
    ) -> Optional[Dict]:
        """
        전략 성과 계산

        Args:
            symbol: 거래 심볼
            strategy_name: 전략 이름
            timeframe: 타임프레임
            period_type: 평가 기간 타입
            min_trades: 최소 거래 수

        Returns:
            성과 딕셔너리 또는 None
        """
        try:
            # 기간별 거래 수 설정
            period_map = {
                'short': 10,
                'medium': 30,
                'long': 100
            }
            trade_limit = period_map.get(period_type, 30)

            # 최근 거래 가져오기 (metadata에 strategy_name이 있는 경우)
            query = text("""
                SELECT 
                    position_id, symbol, side,
                    entry_price, exit_price, quantity,
                    pnl, pnl_percent, status,
                    open_time, close_time, duration_minutes,
                    metadata
                FROM tradebot_positions
                WHERE symbol = :symbol
                    AND status IN ('closed', 'liquidated')
                    AND exit_price IS NOT NULL
                    AND pnl IS NOT NULL
                    AND (metadata->>'strategy_name' = :strategy_name 
                         OR metadata->>'strategy' = :strategy_name)
                ORDER BY close_time DESC
                LIMIT :limit
            """)

            with self.engine.connect() as conn:
                result = conn.execute(query, {
                    'symbol': symbol,
                    'strategy_name': strategy_name,
                    'limit': trade_limit
                })
                trades = [dict(row) for row in result]

            if len(trades) < min_trades:
                logger.debug(f"⚠️ {symbol} {strategy_name}: 거래 수 부족 ({len(trades)} < {min_trades})")
                return None

            # 성과 지표 계산
            df = pd.DataFrame(trades)
            df['pnl'] = pd.to_numeric(df['pnl'], errors='coerce')
            df['pnl_percent'] = pd.to_numeric(df['pnl_percent'], errors='coerce')

            # 기본 통계
            total_trades = len(df)
            winning_trades = len(df[df['pnl'] > 0])
            losing_trades = len(df[df['pnl'] <= 0])
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

            # 수익 통계
            total_pnl = df['pnl'].sum()
            avg_return = df['pnl_percent'].mean() if total_trades > 0 else 0
            avg_win = df[df['pnl'] > 0]['pnl'].mean() if winning_trades > 0 else 0
            avg_loss = df[df['pnl'] <= 0]['pnl'].mean() if losing_trades > 0 else 0

            # Profit Factor
            gross_profit = df[df['pnl'] > 0]['pnl'].sum() if winning_trades > 0 else 0
            gross_loss = abs(df[df['pnl'] <= 0]['pnl'].sum()) if losing_trades > 0 else 1
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

            # Sharpe Ratio (간단 버전: 수익률의 표준편차 기반)
            returns = df['pnl_percent'].dropna()
            if len(returns) > 1 and returns.std() > 0:
                sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(252)  # 연율화
            else:
                sharpe_ratio = 0

            # Max Drawdown
            cumulative_pnl = df['pnl'].cumsum()
            running_max = cumulative_pnl.expanding().max()
            drawdown = cumulative_pnl - running_max
            max_drawdown = abs(drawdown.min()) if len(drawdown) > 0 else 0
            max_drawdown_pct = (max_drawdown / abs(total_pnl)) * 100 if total_pnl != 0 else 0

            # 종합 점수 계산
            composite_score = self._calculate_composite_score(
                win_rate, avg_return, sharpe_ratio, profit_factor
            )

            # 평가 기간
            if len(trades) > 0:
                evaluation_start = trades[-1]['close_time']  # 가장 오래된 거래
                evaluation_end = trades[0]['close_time']  # 가장 최근 거래
            else:
                evaluation_start = None
                evaluation_end = None

            performance = {
                'symbol': symbol,
                'strategy_name': strategy_name,
                'timeframe': timeframe,
                'period_type': period_type,
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'losing_trades': losing_trades,
                'win_rate': round(win_rate, 2),
                'avg_return': round(avg_return, 4),
                'total_pnl': round(total_pnl, 8),
                'sharpe_ratio': round(sharpe_ratio, 4),
                'profit_factor': round(profit_factor, 4),
                'max_drawdown': round(max_drawdown, 8),
                'max_drawdown_pct': round(max_drawdown_pct, 4),
                'avg_win': round(avg_win, 8),
                'avg_loss': round(avg_loss, 8),
                'composite_score': round(composite_score, 4),
                'evaluation_start': evaluation_start,
                'evaluation_end': evaluation_end
            }

            return performance

        except Exception as e:
            logger.error(f"❌ 성과 계산 실패: {e}", exc_info=True)
            return None

    def _calculate_composite_score(
        self,
        win_rate: float,
        avg_return: float,
        sharpe_ratio: float,
        profit_factor: float
    ) -> float:
        """
        종합 점수 계산

        Args:
            win_rate: 승률 (%)
            avg_return: 평균 수익률 (%)
            sharpe_ratio: 샤프 비율
            profit_factor: Profit Factor

        Returns:
            종합 점수 (0-100)
        """
        # 정규화 (0-1 범위로)
        win_rate_norm = min(win_rate / 100, 1.0)  # 100% = 1.0
        avg_return_norm = min(abs(avg_return) / 10, 1.0)  # 10% = 1.0 (임시)
        sharpe_norm = min(sharpe_ratio / 3, 1.0)  # 3 = 1.0
        profit_factor_norm = min(profit_factor / 5, 1.0)  # 5 = 1.0

        # 가중 평균
        composite = (
            win_rate_norm * 0.3 +
            avg_return_norm * 0.3 +
            sharpe_norm * 0.2 +
            profit_factor_norm * 0.2
        ) * 100

        return composite

    def save_performance(self, performance: Dict) -> bool:
        """
        성과를 DB에 저장

        Args:
            performance: 성과 딕셔너리

        Returns:
            성공 여부
        """
        try:
            query = text("""
                INSERT INTO strategy_performance (
                    symbol, strategy_name, timeframe, period_type,
                    total_trades, winning_trades, losing_trades,
                    win_rate, avg_return, total_pnl,
                    sharpe_ratio, profit_factor, max_drawdown, max_drawdown_pct,
                    composite_score, evaluation_start, evaluation_end
                )
                VALUES (
                    :symbol, :strategy_name, :timeframe, :period_type,
                    :total_trades, :winning_trades, :losing_trades,
                    :win_rate, :avg_return, :total_pnl,
                    :sharpe_ratio, :profit_factor, :max_drawdown, :max_drawdown_pct,
                    :composite_score, :evaluation_start, :evaluation_end
                )
                ON CONFLICT (symbol, strategy_name, timeframe, period_type) DO UPDATE
                SET total_trades = EXCLUDED.total_trades,
                    winning_trades = EXCLUDED.winning_trades,
                    losing_trades = EXCLUDED.losing_trades,
                    win_rate = EXCLUDED.win_rate,
                    avg_return = EXCLUDED.avg_return,
                    total_pnl = EXCLUDED.total_pnl,
                    sharpe_ratio = EXCLUDED.sharpe_ratio,
                    profit_factor = EXCLUDED.profit_factor,
                    max_drawdown = EXCLUDED.max_drawdown,
                    max_drawdown_pct = EXCLUDED.max_drawdown_pct,
                    composite_score = EXCLUDED.composite_score,
                    evaluation_start = EXCLUDED.evaluation_start,
                    evaluation_end = EXCLUDED.evaluation_end,
                    updated_at = NOW()
            """)

            with self.engine.connect() as conn:
                conn.execute(query, performance)
                conn.commit()

            logger.debug(f"✅ 성과 저장: {performance['symbol']} {performance['strategy_name']}")
            return True

        except Exception as e:
            logger.error(f"❌ 성과 저장 실패: {e}", exc_info=True)
            return False

    def get_best_strategy(
        self,
        symbol: str,
        timeframe: str,
        period_type: str = 'medium',
        min_score: float = 50.0
    ) -> Optional[Dict]:
        """
        심볼별 최고 성과 전략 조회

        Args:
            symbol: 거래 심볼
            timeframe: 타임프레임
            period_type: 평가 기간 타입
            min_score: 최소 종합 점수

        Returns:
            최고 성과 전략 정보 또는 None
        """
        try:
            query = text("""
                SELECT *
                FROM strategy_performance
                WHERE symbol = :symbol
                    AND timeframe = :timeframe
                    AND period_type = :period_type
                    AND composite_score >= :min_score
                ORDER BY composite_score DESC
                LIMIT 1
            """)

            with self.engine.connect() as conn:
                result = conn.execute(query, {
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'period_type': period_type,
                    'min_score': min_score
                })
                row = result.fetchone()

            if row:
                return dict(row)
            return None

        except Exception as e:
            logger.error(f"❌ 최고 전략 조회 실패: {e}", exc_info=True)
            return None

    def get_all_strategies_performance(
        self,
        symbol: str,
        timeframe: str,
        period_type: str = 'medium'
    ) -> List[Dict]:
        """
        심볼별 모든 전략 성과 조회

        Args:
            symbol: 거래 심볼
            timeframe: 타임프레임
            period_type: 평가 기간 타입

        Returns:
            전략 성과 리스트 (점수 순)
        """
        try:
            query = text("""
                SELECT *
                FROM strategy_performance
                WHERE symbol = :symbol
                    AND timeframe = :timeframe
                    AND period_type = :period_type
                ORDER BY composite_score DESC
            """)

            with self.engine.connect() as conn:
                result = conn.execute(query, {
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'period_type': period_type
                })
                return [dict(row) for row in result]

        except Exception as e:
            logger.error(f"❌ 전략 성과 조회 실패: {e}", exc_info=True)
            return []
