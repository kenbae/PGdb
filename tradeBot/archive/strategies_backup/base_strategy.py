# -*- coding: utf-8 -*-
"""
전략 베이스 클래스 (DEPRECATED)

NOTE: 이 파일은 구형입니다. 새로운 코드에서는 strategies.base를 사용하세요.
호환성을 위해 새로운 모듈에서 re-export합니다.
"""

import warnings

# Deprecation 경고
warnings.warn(
    "base_strategy 모듈은 deprecated입니다. "
    "from strategies.base import BaseStrategy, TradeSignal을 사용하세요.",
    DeprecationWarning,
    stacklevel=2
)

# 새로운 모듈에서 re-export (호환성 유지)
from strategies.base import BaseStrategy, TradeSignal
from strategies.strategy_manager import StrategyManager

# 기존 코드 호환성을 위해 __all__ 정의
__all__ = ['BaseStrategy', 'TradeSignal', 'StrategyManager']
