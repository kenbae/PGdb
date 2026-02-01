"""
Adaptive Strategy Manager

심볼별 최적 전략을 자동으로 선택하고 전환하는 시스템
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text

from strategy_performance_tracker import StrategyPerformanceTracker

logger = logging.getLogger(__name__)


class AdaptiveStrategyManager:
    """적응형 전략 매니저"""

    def __init__(
        self,
        db_engine,
        symbol: str,
        timeframe: str,
        auto_switch: bool = False,
        suggest_switches: bool = True,
        min_confidence: float = 0.7,
        min_switch_interval_hours: int = 24,
        min_trades_for_evaluation: int = 10
    ):
        """
        초기화

        Args:
            db_engine: SQLAlchemy Engine
            symbol: 거래 심볼
            timeframe: 타임프레임
            auto_switch: 자동 전환 활성화 여부
            suggest_switches: 전환 제안 활성화 여부
            min_confidence: 최소 신뢰도 (0-1)
            min_switch_interval_hours: 최소 전환 간격 (시간)
            min_trades_for_evaluation: 평가를 위한 최소 거래 수
        """
        self.db_engine = db_engine
        self.symbol = symbol
        self.timeframe = timeframe
        self.auto_switch = auto_switch
        self.suggest_switches = suggest_switches
        self.min_confidence = min_confidence
        self.min_switch_interval_hours = min_switch_interval_hours
        self.min_trades_for_evaluation = min_trades_for_evaluation

        self.performance_tracker = StrategyPerformanceTracker(db_engine)

        logger.info(f"🔄 AdaptiveStrategyManager 초기화: {symbol} {timeframe}")

    def evaluate_and_suggest(
        self,
        current_strategy: str,
        period_type: str = 'medium'
    ) -> Optional[Dict]:
        """
        현재 전략 평가 및 전환 제안

        Args:
            current_strategy: 현재 사용 중인 전략
            period_type: 평가 기간 타입

        Returns:
            전환 제안 딕셔너리 또는 None
        """
        try:
            # 모든 전략 성과 조회
            all_performances = self.performance_tracker.get_all_strategies_performance(
                self.symbol, self.timeframe, period_type
            )

            if len(all_performances) < 2:
                logger.debug(f"⚠️ {self.symbol}: 평가 가능한 전략이 부족합니다 ({len(all_performances)})")
                return None

            # 현재 전략 성과 찾기
            current_perf = next(
                (p for p in all_performances if p['strategy_name'] == current_strategy),
                None
            )

            if not current_perf:
                logger.debug(f"⚠️ {self.symbol}: 현재 전략 '{current_strategy}'의 성과 데이터가 없습니다")
                return None

            # 최고 성과 전략 찾기
            best_perf = all_performances[0]  # 이미 점수 순으로 정렬됨

            # 현재 전략이 최고인 경우
            if best_perf['strategy_name'] == current_strategy:
                return None

            # 성과 차이 계산
            score_diff = best_perf['composite_score'] - current_perf['composite_score']
            improvement_pct = (score_diff / current_perf['composite_score'] * 100) if current_perf['composite_score'] > 0 else 0

            # 신뢰도 계산 (거래 수 기반)
            confidence = min(
                best_perf['total_trades'] / self.min_trades_for_evaluation,
                1.0
            )

            # 전환 조건 체크
            should_switch = False
            should_suggest = False

            if self.auto_switch:
                # 자동 전환: 성과 차이가 20% 이상이고 신뢰도 충분
                if improvement_pct >= 20 and confidence >= self.min_confidence:
                    should_switch = True
            elif self.suggest_switches:
                # 제안만: 성과 차이가 10% 이상
                if improvement_pct >= 10:
                    should_suggest = True

            # 최근 전환 확인
            if should_switch:
                last_switch = self._get_last_switch()
                if last_switch:
                    hours_since_switch = (datetime.now() - last_switch['created_at']).total_seconds() / 3600
                    if hours_since_switch < self.min_switch_interval_hours:
                        logger.debug(f"⚠️ {self.symbol}: 최근 전환 후 {hours_since_switch:.1f}시간 경과 (최소 {self.min_switch_interval_hours}시간 필요)")
                        should_switch = False
                        should_suggest = True  # 제안으로 변경

            if should_switch or should_suggest:
                suggestion = {
                    'current_strategy': current_strategy,
                    'suggested_strategy': best_perf['strategy_name'],
                    'current_score': current_perf['composite_score'],
                    'suggested_score': best_perf['composite_score'],
                    'improvement_pct': round(improvement_pct, 2),
                    'confidence': round(confidence, 2),
                    'should_switch': should_switch,
                    'should_suggest': should_suggest,
                    'current_performance': {
                        'win_rate': current_perf['win_rate'],
                        'avg_return': current_perf['avg_return'],
                        'total_trades': current_perf['total_trades']
                    },
                    'suggested_performance': {
                        'win_rate': best_perf['win_rate'],
                        'avg_return': best_perf['avg_return'],
                        'total_trades': best_perf['total_trades']
                    }
                }

                if should_switch:
                    logger.info(f"🔄 {self.symbol}: 자동 전환 제안 - {current_strategy} → {best_perf['strategy_name']} (개선: {improvement_pct:.1f}%)")
                else:
                    logger.info(f"💡 {self.symbol}: 전환 제안 - {current_strategy} → {best_perf['strategy_name']} (개선: {improvement_pct:.1f}%)")

                return suggestion

            return None

        except Exception as e:
            logger.error(f"❌ 전략 평가 실패: {e}", exc_info=True)
            return None

    def switch_strategy(
        self,
        from_strategy: str,
        to_strategy: str,
        switch_reason: str,
        switch_type: str = 'auto'  # 'auto', 'manual', 'suggested'
    ) -> bool:
        """
        전략 전환 기록

        Args:
            from_strategy: 이전 전략
            to_strategy: 새 전략
            switch_reason: 전환 이유
            switch_type: 전환 타입

        Returns:
            성공 여부
        """
        try:
            # 성과 조회
            current_perf = self.performance_tracker.calculate_performance(
                self.symbol, from_strategy, self.timeframe
            )
            new_perf = self.performance_tracker.calculate_performance(
                self.symbol, to_strategy, self.timeframe
            )

            old_score = current_perf['composite_score'] if current_perf else 0
            new_score = new_perf['composite_score'] if new_perf else 0
            expected_improvement = ((new_score - old_score) / old_score * 100) if old_score > 0 else 0

            query = text("""
                INSERT INTO strategy_switches (
                    symbol, timeframe,
                    from_strategy, to_strategy, switch_reason, switch_type,
                    old_strategy_score, new_strategy_score, expected_improvement
                )
                VALUES (
                    :symbol, :timeframe,
                    :from_strategy, :to_strategy, :switch_reason, :switch_type,
                    :old_strategy_score, :new_strategy_score, :expected_improvement
                )
            """)

            with self.db_engine.connect() as conn:
                conn.execute(query, {
                    'symbol': self.symbol,
                    'timeframe': self.timeframe,
                    'from_strategy': from_strategy,
                    'to_strategy': to_strategy,
                    'switch_reason': switch_reason,
                    'switch_type': switch_type,
                    'old_strategy_score': old_score,
                    'new_strategy_score': new_score,
                    'expected_improvement': expected_improvement
                })
                conn.commit()

            logger.info(f"✅ 전략 전환 기록: {self.symbol} {from_strategy} → {to_strategy}")
            return True

        except Exception as e:
            logger.error(f"❌ 전략 전환 기록 실패: {e}", exc_info=True)
            return False

    def _get_last_switch(self) -> Optional[Dict]:
        """최근 전환 기록 조회"""
        try:
            query = text("""
                SELECT *
                FROM strategy_switches
                WHERE symbol = :symbol AND timeframe = :timeframe
                ORDER BY created_at DESC
                LIMIT 1
            """)

            with self.db_engine.connect() as conn:
                result = conn.execute(query, {
                    'symbol': self.symbol,
                    'timeframe': self.timeframe
                })
                row = result.fetchone()

            if row:
                return dict(row)
            return None

        except Exception as e:
            logger.error(f"❌ 최근 전환 조회 실패: {e}", exc_info=True)
            return None

    def get_performance_report(self) -> Dict:
        """
        성과 리포트 생성

        Returns:
            성과 리포트 딕셔너리
        """
        try:
            all_performances = self.performance_tracker.get_all_strategies_performance(
                self.symbol, self.timeframe, 'medium'
            )

            if not all_performances:
                return {
                    'symbol': self.symbol,
                    'timeframe': self.timeframe,
                    'strategies': [],
                    'best_strategy': None,
                    'message': '성과 데이터가 없습니다'
                }

            best_strategy = all_performances[0]

            return {
                'symbol': self.symbol,
                'timeframe': self.timeframe,
                'strategies': all_performances,
                'best_strategy': best_strategy['strategy_name'],
                'best_score': best_strategy['composite_score'],
                'total_strategies': len(all_performances)
            }

        except Exception as e:
            logger.error(f"❌ 성과 리포트 생성 실패: {e}", exc_info=True)
            return {
                'symbol': self.symbol,
                'timeframe': self.timeframe,
                'error': str(e)
            }
