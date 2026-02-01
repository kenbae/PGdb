"""
Backtest Broker

백테스트용 브로커 (과거 데이터 기반)
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
from .base import (
    BaseBroker, OrderIntent, Order, Fill, Position,
    OrderSide, OrderType, OrderStatus, PositionStatus
)
from .paper_broker import PaperBroker

logger = logging.getLogger(__name__)


class BacktestBroker(PaperBroker):
    """
    백테스트 브로커 (PaperBroker 상속)

    PaperBroker와 동일한 로직이지만,
    과거 캔들 데이터를 순차적으로 재생하며 체결 시뮬레이션
    """

    def __init__(self, config: Dict):
        """
        초기화

        Args:
            config: PaperBroker와 동일 + 추가 옵션
        """
        super().__init__(config)
        self.current_time: Optional[datetime] = None  # 현재 백테스트 시각
        self.candle_data: Dict[str, List[Dict]] = {}  # symbol -> [candles]

        logger.info("📊 BacktestBroker 초기화")

    def set_current_time(self, timestamp: datetime):
        """백테스트 현재 시각 설정"""
        self.current_time = timestamp

    def load_candles(self, symbol: str, candles: List[Dict]):
        """
        캔들 데이터 로드

        Args:
            symbol: 심볼
            candles: 캔들 리스트 [{'timestamp': datetime, 'open': float, 'high': float, 'low': float, 'close': float, 'volume': float}, ...]
        """
        self.candle_data[symbol] = candles
        logger.info(f"📊 캔들 로드: {symbol} ({len(candles)}개)")

    def get_market_price(self, symbol: str) -> float:
        """
        현재 시장 가격 조회 (백테스트 시각 기준)

        Args:
            symbol: 심볼

        Returns:
            현재 가격 (현재 시각의 캔들 close 가격)
        """
        if not self.current_time:
            return 0.0

        candles = self.candle_data.get(symbol, [])
        if not candles:
            return 0.0

        # 현재 시각 이하의 가장 최근 캔들 찾기
        for candle in reversed(candles):
            if candle['timestamp'] <= self.current_time:
                return float(candle['close'])

        return 0.0
