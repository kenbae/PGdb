"""
Paper Broker (시뮬레이션 브로커)

3단계: 실시간 웹소켓 차트 기반 자동매매 시뮬레이션
- 실제 돈 없이 체결 흉내
- 슬리피지/수수료 모델링
- 리스크 엔진 포함
"""

import logging
import uuid
from typing import Dict, List, Optional
from datetime import datetime
from .base import (
    BaseBroker, OrderIntent, Order, Fill, Position,
    OrderSide, OrderType, OrderStatus, PositionStatus
)

logger = logging.getLogger(__name__)


class PaperBroker(BaseBroker):
    """페이퍼 트레이딩 브로커 (시뮬레이션)"""

    def __init__(self, config: Dict):
        """
        초기화

        Args:
            config: {
                'initial_capital': float,  # 초기 자본
                'commission_rate': float,  # 수수료율 (예: 0.001 = 0.1%)
                'slippage_pct': float,  # 슬리피지 % (예: 0.05 = 0.05%)
                'max_positions': int,  # 최대 동시 포지션 수
                'max_daily_loss_pct': float,  # 일일 최대 손실 % (예: 0.02 = 2%)
            }
        """
        super().__init__(config)
        self.initial_capital = config.get('initial_capital', 10000.0)
        self.capital = self.initial_capital
        self.commission_rate = config.get('commission_rate', 0.001)
        self.slippage_pct = config.get('slippage_pct', 0.05)
        self.max_positions = config.get('max_positions', 5)
        self.max_daily_loss_pct = config.get('max_daily_loss_pct', 0.02)
        self.daily_pnl = 0.0
        self.last_reset_date = datetime.now().date()

        logger.info(f"📝 PaperBroker 초기화: 자본=${self.capital:.2f}")

    def _reset_daily_pnl_if_needed(self):
        """날짜가 바뀌면 일일 손익 리셋"""
        today = datetime.now().date()
        if today != self.last_reset_date:
            self.daily_pnl = 0.0
            self.last_reset_date = today

    def _check_risk_limits(self) -> bool:
        """리스크 한도 체크"""
        self._reset_daily_pnl_if_needed()

        # 일일 손실 한도 체크
        daily_loss_limit = self.initial_capital * self.max_daily_loss_pct
        if self.daily_pnl <= -daily_loss_limit:
            logger.warning(f"⚠️ 일일 손실 한도 도달: ${self.daily_pnl:.2f} <= ${-daily_loss_limit:.2f}")
            return False

        # 최대 포지션 수 체크
        open_count = len([p for p in self.positions.values() if p.status == PositionStatus.OPEN])
        if open_count >= self.max_positions:
            logger.warning(f"⚠️ 최대 포지션 수 도달: {open_count} >= {self.max_positions}")
            return False

        return True

    def submit_order(self, intent: OrderIntent) -> Order:
        """주문 제출 (시뮬레이션)"""
        if not self._check_risk_limits():
            order = Order(
                order_id=f"rejected_{uuid.uuid4().hex[:8]}",
                intent_id=intent.intent_id,
                symbol=intent.symbol,
                side=intent.side,
                order_type=intent.order_type,
                quantity=intent.quantity,
                price=intent.price,
                status=OrderStatus.REJECTED,
                metadata={'reject_reason': 'risk_limit'}
            )
            self.orders[order.order_id] = order
            return order

        order_id = f"paper_{uuid.uuid4().hex[:12]}"
        # metadata에 SL/TP 정보 추가 (None이 아닌 경우만)
        metadata = intent.metadata.copy() if intent.metadata else {}
        if intent.stop_loss is not None:
            metadata['stop_loss'] = intent.stop_loss
        if intent.take_profit is not None:
            metadata['take_profit'] = intent.take_profit
        
        order = Order(
            order_id=order_id,
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            quantity=intent.quantity,
            price=intent.price,
            status=OrderStatus.PENDING,
            metadata=metadata
        )

        self.orders[order_id] = order
        logger.info(f"📝 주문 제출: {order_id} ({intent.symbol} {intent.side.value} {intent.quantity})")

        # 시뮬레이션: 즉시 체결 (나중에 웹소켓 가격으로 실제 체결 시뮬레이션)
        # 여기서는 기본적으로 pending 상태로 두고, update_positions에서 처리

        return order

    def cancel_order(self, order_id: str) -> bool:
        """주문 취소"""
        order = self.orders.get(order_id)
        if not order:
            return False

        if order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED]:
            return False

        order.status = OrderStatus.CANCELLED
        order.updated_at = datetime.now()
        logger.info(f"❌ 주문 취소: {order_id}")
        return True

    def get_market_price(self, symbol: str) -> float:
        """
        현재 시장 가격 조회
        (실제로는 웹소켓에서 받은 최신 가격 사용)

        Args:
            symbol: 심볼

        Returns:
            현재 가격 (기본값: 0.0, 실제로는 외부에서 주입 필요)
        """
        # 실제 구현에서는 웹소켓 피드에서 받은 가격을 사용
        return 0.0

    def fill_order(self, order_id: str, market_price: float) -> Optional[Fill]:
        """
        주문 체결 (시뮬레이션)

        Args:
            order_id: 주문 ID
            market_price: 현재 시장 가격

        Returns:
            체결 정보 or None
        """
        order = self.orders.get(order_id)
        if not order or order.status not in [OrderStatus.PENDING, OrderStatus.OPEN]:
            return None

        # 슬리피지 적용
        if order.order_type == OrderType.MARKET:
            if order.side == OrderSide.BUY:
                fill_price = market_price * (1 + self.slippage_pct / 100)
            else:
                fill_price = market_price * (1 - self.slippage_pct / 100)
        else:  # LIMIT
            fill_price = order.price or market_price

        # 수수료 계산
        commission = order.quantity * fill_price * self.commission_rate

        fill_id = f"fill_{uuid.uuid4().hex[:12]}"
        fill = Fill(
            fill_id=fill_id,
            order_id=order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            commission=commission
        )

        self.fills.append(fill)

        # 주문 상태 업데이트
        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.avg_fill_price = fill_price
        order.updated_at = datetime.now()

        logger.info(f"✅ 체결: {fill_id} ({order.symbol} {order.side.value} {order.quantity} @ ${fill_price:.2f})")

        # 포지션 생성 (진입)
        if order.side in [OrderSide.BUY, OrderSide.SELL]:
            position_id = f"pos_{uuid.uuid4().hex[:12]}"
            
            # OrderIntent에서 SL/TP 가져오기 (order.metadata에서 intent 정보 찾기)
            stop_loss = None
            take_profit = None
            
            # order.metadata에서 intent 정보 찾기
            if hasattr(order, 'metadata') and order.metadata:
                # metadata에서 직접 SL/TP 찾기
                stop_loss = order.metadata.get('stop_loss')
                take_profit = order.metadata.get('take_profit')
            
            # OrderIntent에서 직접 가져오기 (order가 intent를 참조하는 경우)
            # 실제로는 fill_order 호출 시 intent를 전달해야 하지만,
            # 현재 구조에서는 order.metadata에 저장된 정보를 사용
            
            position = Position(
                position_id=position_id,
                symbol=order.symbol,
                side=order.side,
                entry_price=fill_price,
                quantity=order.quantity,
                entry_fill_id=fill_id,
                stop_loss=stop_loss,
                take_profit=take_profit,
                opened_at=datetime.now()
            )
            self.positions[position_id] = position
            sl_str = f"${stop_loss:.8f}" if stop_loss else "None"
            tp_str = f"${take_profit:.8f}" if take_profit else "None"
            logger.info(f"📊 포지션 오픈: {position_id} (SL: {sl_str}, TP: {tp_str})")

        return fill

    def update_positions(self, current_prices: Dict[str, float]) -> List[Position]:
        """
        포지션 상태 업데이트 (SL/TP 체크, PnL 계산)

        Args:
            current_prices: 심볼별 현재 가격 {symbol: price}

        Returns:
            업데이트된 포지션 리스트
        """
        updated = []

        for position_id, position in self.positions.items():
            if position.status != PositionStatus.OPEN:
                continue

            current_price = current_prices.get(position.symbol)
            if not current_price:
                continue

            # PnL 계산
            if position.side == OrderSide.BUY:
                pnl = (current_price - position.entry_price) * position.quantity
            else:  # SELL
                pnl = (position.entry_price - current_price) * position.quantity

            position.pnl = pnl
            position.pnl_percent = (pnl / (position.entry_price * position.quantity)) * 100

            # SL/TP 체크
            should_close = False
            exit_price = current_price

            if position.stop_loss:
                if position.side == OrderSide.BUY and current_price <= position.stop_loss:
                    should_close = True
                elif position.side == OrderSide.SELL and current_price >= position.stop_loss:
                    should_close = True

            if position.take_profit:
                if position.side == OrderSide.BUY and current_price >= position.take_profit:
                    should_close = True
                elif position.side == OrderSide.SELL and current_price <= position.take_profit:
                    should_close = True

            if should_close:
                position.status = PositionStatus.CLOSED
                position.exit_price = exit_price
                position.closed_at = datetime.now()
                self.daily_pnl += pnl
                logger.info(f"🔒 포지션 청산: {position_id} (PnL: ${pnl:.2f})")

            updated.append(position)

        return updated

    def get_open_positions(self) -> List[Position]:
        """오픈 포지션 조회"""
        return [p for p in self.positions.values() if p.status == PositionStatus.OPEN]

    def close_position(self, position_id: str, exit_price: Optional[float] = None) -> bool:
        """
        포지션 수동 청산

        Args:
            position_id: 포지션 ID
            exit_price: 청산 가격 (None이면 현재 시장 가격 사용)

        Returns:
            성공 여부
        """
        position = self.positions.get(position_id)
        if not position or position.status != PositionStatus.OPEN:
            logger.warning(f"⚠️ 포지션을 찾을 수 없거나 이미 닫혔습니다: {position_id}")
            return False

        # 청산 가격 결정
        if exit_price is None:
            # 현재 시장 가격 사용 (update_positions에서 계산된 가격)
            exit_price = position.entry_price  # 기본값 (실제로는 current_prices에서 가져와야 함)

        # PnL 계산
        if position.side == OrderSide.BUY:
            pnl = (exit_price - position.entry_price) * position.quantity
        else:  # SELL
            pnl = (position.entry_price - exit_price) * position.quantity

        # 포지션 닫기
        position.status = PositionStatus.CLOSED
        position.exit_price = exit_price
        position.closed_at = datetime.now()
        position.pnl = pnl
        position.pnl_percent = (pnl / (position.entry_price * position.quantity)) * 100

        # 일일 PnL 업데이트
        self.daily_pnl += pnl
        self.capital += pnl

        logger.info(f"🔒 포지션 수동 청산: {position_id} @ ${exit_price:.8f} (PnL: ${pnl:.2f})")

        return True
