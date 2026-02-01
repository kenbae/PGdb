"""
Base Broker Interface

모든 브로커(Backtest/Paper/Live)가 구현해야 하는 공통 인터페이스
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass
from enum import Enum


class OrderSide(Enum):
    """주문 방향"""
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """주문 타입"""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(Enum):
    """주문 상태"""
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PositionStatus(Enum):
    """포지션 상태"""
    OPEN = "open"
    CLOSED = "closed"
    LIQUIDATED = "liquidated"


@dataclass
class OrderIntent:
    """주문 의도 (시스템이 생성)"""
    intent_id: str
    signal_id: Optional[str]
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None  # 지정가/스탑가
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    metadata: Optional[Dict] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass
class Order:
    """주문 (브로커에 제출됨)"""
    order_id: str
    intent_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    avg_fill_price: Optional[float] = None
    created_at: datetime = None
    updated_at: datetime = None
    metadata: Optional[Dict] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = self.created_at


@dataclass
class Fill:
    """체결 (주문이 부분/전체 체결됨)"""
    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    commission: float = 0.0
    timestamp: datetime = None
    metadata: Optional[Dict] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass
class Position:
    """포지션 (진입 후 보유 중)"""
    position_id: str
    symbol: str
    side: OrderSide
    entry_price: float
    quantity: float
    entry_fill_id: str  # 진입 체결 ID
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    status: PositionStatus = PositionStatus.OPEN
    exit_price: Optional[float] = None
    exit_fill_id: Optional[str] = None
    pnl: float = 0.0
    pnl_percent: float = 0.0
    opened_at: datetime = None
    closed_at: Optional[datetime] = None
    metadata: Optional[Dict] = None

    def __post_init__(self):
        if self.opened_at is None:
            self.opened_at = datetime.now()


class BaseBroker(ABC):
    """브로커 기본 인터페이스"""

    def __init__(self, config: Dict):
        """
        초기화

        Args:
            config: 브로커 설정 (거래소, 수수료, 슬리피지 등)
        """
        self.config = config or {}
        self.positions: Dict[str, Position] = {}  # position_id -> Position
        self.orders: Dict[str, Order] = {}  # order_id -> Order
        self.fills: List[Fill] = []  # 체결 히스토리

    @abstractmethod
    def submit_order(self, intent: OrderIntent) -> Order:
        """
        주문 제출

        Args:
            intent: 주문 의도

        Returns:
            생성된 주문
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """
        주문 취소

        Args:
            order_id: 주문 ID

        Returns:
            성공 여부
        """
        pass

    @abstractmethod
    def get_market_price(self, symbol: str) -> float:
        """
        현재 시장 가격 조회

        Args:
            symbol: 심볼

        Returns:
            현재 가격
        """
        pass

    @abstractmethod
    def update_positions(self, current_prices: Dict[str, float]) -> List[Position]:
        """
        포지션 상태 업데이트 (SL/TP 체크, PnL 계산)

        Args:
            current_prices: 심볼별 현재 가격 {symbol: price}

        Returns:
            업데이트된 포지션 리스트
        """
        pass

    @abstractmethod
    def get_open_positions(self) -> List[Position]:
        """
        오픈 포지션 조회

        Returns:
            오픈 포지션 리스트
        """
        pass

    def get_position(self, position_id: str) -> Optional[Position]:
        """
        포지션 조회

        Args:
            position_id: 포지션 ID

        Returns:
            포지션 or None
        """
        return self.positions.get(position_id)

    def get_order(self, order_id: str) -> Optional[Order]:
        """
        주문 조회

        Args:
            order_id: 주문 ID

        Returns:
            주문 or None
        """
        return self.orders.get(order_id)
