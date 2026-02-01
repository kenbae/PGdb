# -*- coding: utf-8 -*-
"""
통합 전략 매니저
- 여러 전략 등록 및 관리
- 전략별 분석 실행
- 선택적 AI 검증

백테스터와 오토봇이 공유하는 공용 모듈.

전략 설정: DB (tradebot_strategy_settings) 기반. config.yaml 사용 안 함.
"""

import logging
import sys
import os
from typing import Dict, List, Optional, Any, Tuple
import pandas as pd
from datetime import datetime

# 공용 폴더 경로 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_pgdb_dir = os.path.dirname(_current_dir)
if _pgdb_dir not in sys.path:
    sys.path.insert(0, _pgdb_dir)

from .base import BaseStrategy, TradeSignal
from .strategy_defaults import STRATEGY_DEFAULTS

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
            config: 전체 설정 (AI 등. 전략 설정은 DB에서 로드)
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

        all_signals = []  # 모든 신호 (승인 + 거부) 반환
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
                    'confluence': signal.reasons or [],
                    'strategy': strategy.name,  # 전략명 추가
                    'strategy_name': signal.strategy_name  # 신호에 저장된 전략명
                }

                market_data = {
                    'current_price': float(df.iloc[-1]['close']),
                    'trend': '알 수 없음',
                    'volatility': '보통'
                }

                # AI 분석 요청
                ai_result = self.ai_analyzer.analyze_signal(signal_dict, market_data)

                # AI 결과 적용 (속성)
                signal.ai_decision = ai_result.decision
                signal.ai_confidence = ai_result.confidence
                signal.ai_reasoning = ai_result.reasoning

                # AI 결과를 metadata에도 저장 (API에서 읽을 수 있도록)
                if not hasattr(signal, 'metadata') or signal.metadata is None:
                    signal.metadata = {}
                signal.metadata['ai_analysis'] = {
                    'decision': ai_result.decision,
                    'confidence': ai_result.confidence,
                    'reasoning': ai_result.reasoning,
                    'risk_assessment': getattr(ai_result, 'risk_assessment', None),
                    'market_context': getattr(ai_result, 'market_context', None)
                }

                # AI가 승인한 경우
                if signal.ai_decision == 'approve' and signal.ai_confidence >= min_confidence:
                    logger.info(f"✅ AI 승인: {signal.symbol} {signal.signal_type} (conf: {signal.ai_confidence:.2f})")
                else:
                    # AI가 approve했지만 신뢰도가 낮은 경우 → reject로 변경
                    if signal.ai_decision == 'approve' and signal.ai_confidence < min_confidence:
                        signal.ai_decision = 'reject'
                        signal.metadata['ai_analysis']['decision'] = 'reject'
                        signal.metadata['ai_analysis']['reject_reason'] = f'low_confidence ({signal.ai_confidence:.2f} < {min_confidence})'
                        logger.info(f"❌ AI 거부: {signal.symbol} {signal.signal_type} (신뢰도 부족: {signal.ai_confidence:.2f} < {min_confidence})")
                    else:
                        logger.info(f"❌ AI 거부: {signal.symbol} {signal.signal_type} ({signal.ai_decision})")

                # 모든 신호 추가 (승인/거부 모두)
                all_signals.append(signal)

            except Exception as e:
                logger.error(f"AI 검증 실패: {e}")
                # AI 검증 실패 시 원본 신호 유지
                all_signals.append(signal)

        return all_signals

    def _build_ai_context(self, signal: TradeSignal, df: pd.DataFrame) -> Dict:
        """AI 분석용 컨텍스트 구성"""
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


def _get_strategy_config(
    name: str,
    repo_overrides: List[Dict],
) -> Tuple[Dict, bool]:
    """
    전략별 config + enabled 조회 (DB 우선, 없으면 기본값)

    Returns:
        (config_dict, enabled)
    """
    defaults = STRATEGY_DEFAULTS.get(name, {})
    config = dict(defaults)
    enabled = config.pop('enabled', True)

    for row in (repo_overrides or []):
        if row.get('strategy_name') != name:
            continue
        if row.get('enabled') is not None:
            enabled = bool(row['enabled'])
        cfg = row.get('config') or {}
        if isinstance(cfg, dict) and cfg:
            cfg = dict(cfg)
            cfg.pop('enabled', None)
            config.update(cfg)
        break

    return config, enabled


def create_strategy_manager(
    strategy_settings_repo=None,
    config: Optional[Dict] = None,
) -> StrategyManager:
    """
    DB 기반 전략 매니저 생성

    Args:
        strategy_settings_repo: StrategySettingsRepo 인스턴스 (DB에서 전략 설정 로드)
        config: AI 등 기타 설정 (optional)

    Returns:
        StrategyManager: 전략이 등록된 매니저
    """
    manager = StrategyManager(config or {})

    overrides = []
    if strategy_settings_repo:
        try:
            overrides = strategy_settings_repo.get_all()
        except Exception as e:
            logger.warning(f"전략 설정 DB 조회 실패, 기본값 사용: {e}")

    # 전략별 등록 (DB 또는 기본값 사용, config.yaml 참조 안 함)
    def _reg(name: str):
        cfg, en = _get_strategy_config(name, overrides)
        cfg['enabled'] = en
        return cfg, en

    try:
        from .ema_cross_strategy import EMACrossStrategy
        c, e = _reg('ema_cross')
        s = EMACrossStrategy('ema_cross', c)
        s.set_enabled(e)
        manager.register(s)
    except ImportError as ex:
        logger.warning(f"EMA Cross 전략 로드 실패: {ex}")

    try:
        from .ict_strategy import ICTStrategy
        c, e = _reg('ict')
        s = ICTStrategy('ict', c)
        s.set_enabled(e)
        manager.register(s)
    except ImportError as ex:
        logger.warning(f"ICT 전략 로드 실패: {ex}")

    try:
        from .rsi_strategy import RSIStrategy
        c, e = _reg('rsi')
        s = RSIStrategy('rsi', c)
        s.set_enabled(e)
        manager.register(s)
    except ImportError as ex:
        logger.warning(f"RSI 전략 로드 실패: {ex}")

    try:
        from .bollinger_strategy import BollingerStrategy
        c, e = _reg('bollinger')
        s = BollingerStrategy('bollinger', c)
        s.set_enabled(e)
        manager.register(s)
    except ImportError as ex:
        logger.warning(f"Bollinger 전략 로드 실패: {ex}")

    try:
        from .keltner_ict_turtle_strategy import KeltnerICTTurtleStrategy
        c, e = _reg('keltner_ict_turtle')
        s = KeltnerICTTurtleStrategy('keltner_ict_turtle', c)
        s.set_enabled(e)
        manager.register(s)
    except ImportError as ex:
        logger.warning(f"Keltner ICT Turtle 전략 로드 실패: {ex}")

    try:
        from .pattern_rag_strategy import PatternRAGStrategy
        c, e = _reg('pattern_rag')
        s = PatternRAGStrategy('pattern_rag', c)
        s.set_enabled(e)
        manager.register(s)
    except ImportError as ex:
        logger.warning(f"Pattern RAG 전략 로드 실패: {ex}")

    try:
        from .bb_adaptive_rsi_strategy import BBAdaptiveRSIStrategy
        c, e = _reg('bb_adaptive_rsi')
        s = BBAdaptiveRSIStrategy('bb_adaptive_rsi', c)
        s.set_enabled(e)
        manager.register(s)
    except ImportError as ex:
        logger.warning(f"BB Adaptive RSI 전략 로드 실패: {ex}")

    try:
        from .ema_divergence_volume_strategy import EMADivergenceVolumeStrategy
        c, e = _reg('ema_divergence_volume')
        s = EMADivergenceVolumeStrategy('ema_divergence_volume', c)
        s.set_enabled(e)
        manager.register(s)
    except ImportError as ex:
        logger.warning(f"EMA Divergence Volume 전략 로드 실패: {ex}")

    logger.info(f"StrategyManager 생성 완료: {len(manager.strategies)}개 전략 등록됨")

    return manager
