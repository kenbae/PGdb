# -*- coding: utf-8 -*-
"""
tradeBot/strategies/ - 공용 폴더로 리다이렉트

실제 전략 코드는 PGdb/strategies/에 있습니다.
백테스터와 오토봇이 같은 전략을 공유합니다.
"""

import sys
import os
import importlib.util

# 공용 폴더 경로
_current_dir = os.path.dirname(os.path.abspath(__file__))
_tradebot_dir = os.path.dirname(_current_dir)
_pgdb_dir = os.path.dirname(_tradebot_dir)
_shared_strategies_path = os.path.join(_pgdb_dir, 'strategies', '__init__.py')

# 공용 폴더를 다른 이름으로 로드
spec = importlib.util.spec_from_file_location("shared_strategies", _shared_strategies_path)
shared_strategies = importlib.util.module_from_spec(spec)
sys.modules["shared_strategies"] = shared_strategies
spec.loader.exec_module(shared_strategies)

# 공용 모듈에서 가져오기
BaseStrategy = shared_strategies.BaseStrategy
TradeSignal = shared_strategies.TradeSignal
StrategyManager = shared_strategies.StrategyManager
create_strategy_manager = shared_strategies.create_strategy_manager
STRATEGY_DEFAULTS = shared_strategies.STRATEGY_DEFAULTS
EMACrossStrategy = shared_strategies.EMACrossStrategy
ICTStrategy = shared_strategies.ICTStrategy
RSIStrategy = shared_strategies.RSIStrategy
BollingerStrategy = shared_strategies.BollingerStrategy
KeltnerICTTurtleStrategy = shared_strategies.KeltnerICTTurtleStrategy
PatternRAGStrategy = shared_strategies.PatternRAGStrategy
KeltnerICTTurtle = shared_strategies.KeltnerICTTurtle
BBAdaptiveRSIStrategy = shared_strategies.BBAdaptiveRSIStrategy
EMADivergenceVolumeStrategy = shared_strategies.EMADivergenceVolumeStrategy

__all__ = [
    'BaseStrategy',
    'TradeSignal',
    'StrategyManager',
    'create_strategy_manager',
    'STRATEGY_DEFAULTS',
    'EMACrossStrategy',
    'ICTStrategy',
    'RSIStrategy',
    'BollingerStrategy',
    'KeltnerICTTurtleStrategy',
    'PatternRAGStrategy',
    'KeltnerICTTurtle',
    'BBAdaptiveRSIStrategy',
    'EMADivergenceVolumeStrategy',
]
