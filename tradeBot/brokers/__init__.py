"""
Broker 어댑터 패턴

Backtest/Paper/Live가 동일 인터페이스를 구현하여
1~4단계 전체에서 재사용 가능한 구조
"""

from .base import BaseBroker, OrderIntent, Order, Fill, Position
from .paper_broker import PaperBroker
from .backtest_broker import BacktestBroker

__all__ = [
    'BaseBroker',
    'OrderIntent',
    'Order',
    'Fill',
    'Position',
    'PaperBroker',
    'BacktestBroker'
]
