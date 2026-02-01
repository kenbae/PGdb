# -*- coding: utf-8 -*-
"""
공용 전략 시스템

백테스터와 오토봇이 공유하는 전략 모듈.
전략 추가/삭제는 이 폴더에서만 관리합니다.

전략 목록:
- EMACrossStrategy: EMA 크로스오버 + HTF 정렬
- ICTStrategy: Order Block + Fair Value Gap
- RSIStrategy: RSI 과매수/과매도
- BollingerStrategy: 볼린저 밴드 Breakout/Reversal
- KeltnerICTTurtleStrategy: 켈트너 채널 + ICT + 터틀 통합
- PatternRAGStrategy: 과거 패턴 학습 + OLLAMA AI 신호 생성
- BBAdaptiveRSIStrategy: BB 3σ + Adaptive RSI (Kaufman ER 기반)
- KeltnerICTTurtle: 백테스트용 켈트너 ICT 터틀 클래스
"""

from .base import BaseStrategy, TradeSignal
from .strategy_manager import StrategyManager, create_strategy_manager
from .strategy_defaults import STRATEGY_DEFAULTS
from .ema_cross_strategy import EMACrossStrategy
from .ict_strategy import ICTStrategy
from .rsi_strategy import RSIStrategy
from .bollinger_strategy import BollingerStrategy
from .keltner_ict_turtle_strategy import KeltnerICTTurtleStrategy
from .pattern_rag_strategy import PatternRAGStrategy
from .keltner_ict_turtle_backtester import KeltnerICTTurtle
from .bb_adaptive_rsi_strategy import BBAdaptiveRSIStrategy
from .ema_divergence_volume_strategy import EMADivergenceVolumeStrategy

__all__ = [
    # 베이스 클래스
    'BaseStrategy',
    'TradeSignal',

    # 매니저
    'StrategyManager',
    'create_strategy_manager',
    'STRATEGY_DEFAULTS',

    # 개별 전략
    'EMACrossStrategy',
    'ICTStrategy',
    'RSIStrategy',
    'BollingerStrategy',
    'KeltnerICTTurtleStrategy',
    'PatternRAGStrategy',
    'BBAdaptiveRSIStrategy',
    'EMADivergenceVolumeStrategy',

    # 백테스트 전용
    'KeltnerICTTurtle',
]
