# -*- coding: utf-8 -*-
"""
tradeBot/indicators/ - 공용 폴더로 리다이렉트

실제 지표 코드는 PGdb/indicators/에 있습니다.
백테스터와 오토봇이 같은 지표를 공유합니다.
"""

import sys
import os
import importlib.util

# 공용 폴더 경로
_current_dir = os.path.dirname(os.path.abspath(__file__))
_tradebot_dir = os.path.dirname(_current_dir)
_pgdb_dir = os.path.dirname(_tradebot_dir)
_shared_indicators_path = os.path.join(_pgdb_dir, 'indicators', '__init__.py')

# 공용 폴더를 다른 이름으로 로드
spec = importlib.util.spec_from_file_location("shared_indicators", _shared_indicators_path)
shared_indicators = importlib.util.module_from_spec(spec)
sys.modules["shared_indicators"] = shared_indicators
spec.loader.exec_module(shared_indicators)

# 공용 모듈에서 가져오기
TechnicalIndicators = shared_indicators.TechnicalIndicators
OrderBlockDetector = shared_indicators.OrderBlockDetector
FVGDetector = shared_indicators.FVGDetector

__all__ = [
    'TechnicalIndicators',
    'OrderBlockDetector',
    'FVGDetector',
]
