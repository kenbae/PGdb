# -*- coding: utf-8 -*-
"""
통합 전략 매니저
- 여러 전략 등록 및 관리
- 전략별 분석 실행
- 선택적 AI 검증

주의: 모든 전략 파일은 PGdb/strategies/ 폴더에서 import합니다.
      tradeBot/strategies/에는 이 파일(strategy_manager.py)만 존재합니다.
"""

import logging
import sys
import os
from typing import Dict, List, Optional, Any
import pandas as pd
from datetime import datetime

# PGdb/strategies 경로 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_tradebot_dir = os.path.dirname(_current_dir)
_pgdb_dir = os.path.dirname(_tradebot_dir)
_strategies_dir = os.path.join(_pgdb_dir, 'strategies')
if _strategies_dir not in sys.path:
    sys.path.insert(0, _strategies_dir)

# PGdb/strategies/base.py에서 import
from base import BaseStrategy, TradeSignal

logger = logging.getLogger(__name__)


class StrategyManager:
    """
    전략 매니저

    여러 전략을 등록하고, 통합 실행하며, AI 검증을 적용합니다.
    """

    def __init__(self, config: Dict = None):
        """
        초기화

        Args:
            config: 전체 설정 (config.yaml)
        """
        self.config = config or {}
        self.strategies: Dict[str, BaseStrategy] = {}
        self.ai_analyzer = None  # 지연 초기화
        self.ai_config = config.get('ai', {}) if config else {}

        logger.info("StrategyManager 초기화됨")

    def register(self, strategy: BaseStrategy):
        """
        전략 등록

        Args:
            strategy: 등록할 전략 객체
        """
        name = strategy.get_name()
        self.strategies[name] = strategy
        logger.info(f"전략 등록: {name} (enabled={strategy.is_enabled()})")

    # 호환성을 위한 별칭
    def register_strategy(self, strategy: BaseStrategy):
        """전략 등록 (기존 API 호환)"""
        self.register(strategy)

    def list_strategies(self) -> List[str]:
        """전략 이름 목록 (기존 API 호환)"""
        return list(self.strategies.keys())

    def unregister(self, name: str) -> bool:
        """
        전략 등록 해제

        Args:
            name: 전략 이름

        Returns:
            bool: 성공 여부
        """
        if name in self.strategies:
            del self.strategies[name]
            logger.info(f"전략 해제: {name}")
            return True
        return False

    def get_strategy(self, name: str) -> Optional[BaseStrategy]:
        """전략 조회"""
        return self.strategies.get(name)

    def get_enabled_strategies(self) -> List[BaseStrategy]:
        """활성화된 전략 목록"""
        return [s for s in self.strategies.values() if s.is_enabled()]

    def get_all_strategies(self) -> List[BaseStrategy]:
        """모든 전략 목록"""
        return list(self.strategies.values())

    def analyze_all(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        apply_ai: bool = True,
        strategy_filter: Optional[List[str]] = None
    ) -> Dict[str, List[TradeSignal]]:
        """
        모든 활성 전략 분석 실행

        Args:
            symbol: 거래 심볼
            timeframe: 타임프레임
            df: OHLCV 데이터프레임
            apply_ai: AI 검증 적용 여부
            strategy_filter: 실행할 전략 이름 목록 (None이면 모든 활성 전략)

        Returns:
            Dict[str, List[TradeSignal]]: 전략별 신호 목록
        """
        results: Dict[str, List[TradeSignal]] = {}

        # 전략 필터 적용
        strategies_to_run = self.get_enabled_strategies()
        if strategy_filter:
            strategy_filter_lower = [s.lower() for s in strategy_filter]
            strategies_to_run = [
                s for s in strategies_to_run
                if s.get_name().lower() in strategy_filter_lower
            ]
            logger.info(f"📋 선택된 전략: {[s.get_name() for s in strategies_to_run]}")

        if not strategies_to_run:
            logger.warning(f"⚠️ 실행할 전략이 없습니다. (필터: {strategy_filter})")
            logger.warning(f"   등록된 전략: {[s.get_name() for s in self.get_all_strategies()]}")
            logger.warning(f"   활성화된 전략: {[s.get_name() for s in self.get_enabled_strategies()]}")

        for strategy in strategies_to_run:
            strategy_name = strategy.get_name()

            try:
                logger.info(f"🔍 {strategy_name} 분석 시작: {symbol} {timeframe}")

                # 전략 분석 실행
                signals = strategy.analyze(symbol, timeframe, df)

                # AI 검증 (선택적)
                if apply_ai and strategy.use_ai and signals:
                    signals = self._apply_ai_filter(signals, df, strategy)

                results[strategy_name] = signals

                if signals:
                    logger.info(f"✅ {strategy_name}: {len(signals)}개 신호 생성")
                else:
                    logger.info(f"⏸️ {strategy_name}: 신호 없음 (조건 미충족)")

            except Exception as e:
                logger.error(f"❌ {strategy_name} 분석 실패: {e}")
                import traceback
                logger.error(traceback.format_exc())
                results[strategy_name] = []

        return results

    def analyze_strategy(
        self,
        strategy_name: str,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        apply_ai: bool = True
    ) -> List[TradeSignal]:
        """
        특정 전략만 분석

        Args:
            strategy_name: 전략 이름
            symbol: 거래 심볼
            timeframe: 타임프레임
            df: OHLCV 데이터프레임
            apply_ai: AI 검증 적용 여부

        Returns:
            List[TradeSignal]: 신호 목록
        """
        strategy = self.get_strategy(strategy_name)

        if not strategy:
            logger.warning(f"전략 없음: {strategy_name}")
            return []

        if not strategy.is_enabled():
            logger.warning(f"전략 비활성화됨: {strategy_name}")
            return []

        try:
            signals = strategy.analyze(symbol, timeframe, df)

            if apply_ai and strategy.use_ai and signals:
                signals = self._apply_ai_filter(signals, df, strategy)

            return signals

        except Exception as e:
            logger.error(f"❌ {strategy_name} 분석 실패: {e}")
            return []

    def _apply_ai_filter(
        self,
        signals: List[TradeSignal],
        df: pd.DataFrame,
        strategy: BaseStrategy
    ) -> List[TradeSignal]:
        """
        AI 검증 적용

        Args:
            signals: 검증할 신호 목록
            df: OHLCV 데이터프레임
            strategy: 전략 객체

        Returns:
            List[TradeSignal]: AI 검증이 적용된 신호 목록
        """
        if not self.ai_config.get('enabled', False):
            return signals

        # AI 분석기 지연 초기화
        if self.ai_analyzer is None:
            try:
                from ai.ollama_analyzer import OLLAMAAnalyzer
                self.ai_analyzer = OLLAMAAnalyzer(
                    host=self.ai_config.get('ollama_url', 'http://localhost:11434'),
                    model=self.ai_config.get('ollama_model', 'qwen2.5:7b-instruct')
                )
                logger.info("AI 분석기 초기화 완료")
            except Exception as e:
                logger.error(f"AI 분석기 초기화 실패: {e}")
                return signals

        filtered_signals = []
        min_confidence = self.ai_config.get('min_confidence', 0.65)

        for signal in signals:
            try:
                # AI 분석용 신호 및 시장 데이터 구성
                signal_dict = {
                    'signal_type': signal.signal_type,
                    'symbol': signal.symbol,
                    'entry_price': signal.entry_price,
                    'stop_loss': signal.stop_loss,
                    'take_profit_1': signal.take_profit_1,
                    'confidence': signal.confidence,
                    'risk_reward': signal.risk_reward,
                    'confluence': signal.reasons or []
                }

                market_data = {
                    'current_price': float(df.iloc[-1]['close']),
                    'trend': '알 수 없음',
                    'volatility': '보통'
                }

                # AI 분석 요청
                ai_result = self.ai_analyzer.analyze_signal(signal_dict, market_data)

                # AI 결과 적용
                signal.ai_decision = ai_result.decision
                signal.ai_confidence = ai_result.confidence
                signal.ai_reasoning = ai_result.reasoning

                # AI가 승인한 경우만 포함
                if signal.ai_decision == 'approve' and signal.ai_confidence >= min_confidence:
                    filtered_signals.append(signal)
                    logger.info(f"✅ AI 승인: {signal.symbol} {signal.signal_type} (conf: {signal.ai_confidence:.2f})")
                else:
                    logger.info(f"❌ AI 거부: {signal.symbol} {signal.signal_type} ({signal.ai_decision})")

            except Exception as e:
                logger.error(f"AI 검증 실패: {e}")
                # AI 검증 실패 시 원본 신호 유지
                filtered_signals.append(signal)

        return filtered_signals

    def _build_ai_context(self, signal: TradeSignal, df: pd.DataFrame) -> Dict:
        """AI 분석용 컨텍스트 구성"""
        # 최근 캔들 정보
        recent_candles = df.tail(20).to_dict('records') if len(df) >= 20 else df.to_dict('records')

        return {
            'signal': signal.to_dict(),
            'recent_candles': recent_candles,
            'current_price': float(df.iloc[-1]['close']),
            'strategy': signal.strategy_name,
        }

    def get_status(self) -> Dict:
        """전략 매니저 상태 조회"""
        return {
            'total_strategies': len(self.strategies),
            'enabled_strategies': len(self.get_enabled_strategies()),
            'ai_enabled': self.ai_config.get('enabled', False),
            'strategies': {
                name: strategy.get_status()
                for name, strategy in self.strategies.items()
            }
        }

    def enable_strategy(self, name: str) -> bool:
        """전략 활성화"""
        strategy = self.get_strategy(name)
        if strategy:
            strategy.set_enabled(True)
            return True
        return False

    def disable_strategy(self, name: str) -> bool:
        """전략 비활성화"""
        strategy = self.get_strategy(name)
        if strategy:
            strategy.set_enabled(False)
            return True
        return False

    def update_strategy_config(self, name: str, new_config: Dict) -> bool:
        """전략 설정 업데이트"""
        strategy = self.get_strategy(name)
        if strategy:
            strategy.update_config(new_config)
            return True
        return False


def create_strategy_manager(config: Dict) -> StrategyManager:
    """
    설정에서 전략 매니저 생성 및 전략 등록

    모든 전략은 PGdb/strategies/ 폴더에서 import합니다.

    Args:
        config: 전체 설정 (config.yaml)

    Returns:
        StrategyManager: 전략이 등록된 매니저
    """
    manager = StrategyManager(config)

    strategies_config = config.get('strategies', {})

    # EMA Cross 전략
    if 'ema_cross' in strategies_config:
        try:
            from ema_cross_strategy import EMACrossStrategy
            strategy = EMACrossStrategy('ema_cross', strategies_config['ema_cross'])
            manager.register(strategy)
        except ImportError as e:
            logger.warning(f"EMA Cross 전략 로드 실패: {e}")

    # ICT 전략
    if 'ict' in strategies_config:
        try:
            from ict_strategy import ICTStrategy
            strategy = ICTStrategy('ict', strategies_config['ict'])
            manager.register(strategy)
        except ImportError as e:
            logger.warning(f"ICT 전략 로드 실패: {e}")

    # RSI 전략
    if 'rsi' in strategies_config:
        try:
            from rsi_strategy import RSIStrategy
            strategy = RSIStrategy('rsi', strategies_config['rsi'])
            manager.register(strategy)
        except ImportError as e:
            logger.warning(f"RSI 전략 로드 실패: {e}")

    # Bollinger Band 전략
    if 'bollinger' in strategies_config:
        try:
            from bollinger_strategy import BollingerStrategy
            strategy = BollingerStrategy('bollinger', strategies_config['bollinger'])
            manager.register(strategy)
        except ImportError as e:
            logger.warning(f"Bollinger 전략 로드 실패: {e}")

    # Keltner ICT Turtle 전략
    if 'keltner_ict_turtle' in strategies_config:
        try:
            from keltner_ict_turtle_strategy import KeltnerICTTurtleStrategy
            strategy = KeltnerICTTurtleStrategy('keltner_ict_turtle', strategies_config['keltner_ict_turtle'])
            manager.register(strategy)
        except ImportError as e:
            logger.warning(f"Keltner ICT Turtle 전략 로드 실패: {e}")

    # Pattern RAG 전략
    if 'pattern_rag' in strategies_config:
        try:
            from pattern_rag_strategy import PatternRAGStrategy
            strategy = PatternRAGStrategy('pattern_rag', strategies_config['pattern_rag'])
            manager.register(strategy)
        except ImportError as e:
            logger.warning(f"Pattern RAG 전략 로드 실패: {e}")

    logger.info(f"StrategyManager 생성 완료: {len(manager.strategies)}개 전략 등록됨")

    return manager
