# -*- coding: utf-8 -*-
"""
통합 전략 베이스 클래스 (리다이렉트)

실제 구현은 PGdb/strategies/base.py에 있습니다.
이 파일은 호환성을 위해 해당 파일을 import합니다.

주의: 절대로 이 파일에 직접 코드를 작성하지 마세요!
     모든 수정은 PGdb/strategies/base.py에서 해야 합니다.
"""

import sys
import os

# PGdb/strategies 경로 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_tradebot_dir = os.path.dirname(_current_dir)
_pgdb_dir = os.path.dirname(_tradebot_dir)
_strategies_dir = os.path.join(_pgdb_dir, 'strategies')

if _strategies_dir not in sys.path:
    sys.path.insert(0, _strategies_dir)

# PGdb/strategies/base.py에서 모든 클래스 import
from base import TradeSignal, BaseStrategy

# 명시적 export
__all__ = ['TradeSignal', 'BaseStrategy']
