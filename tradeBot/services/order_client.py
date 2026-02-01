"""
주문 클라이언트 (Order Client)

신호 분석 서버에서 주문 실행 서버로 주문을 전송하는 클라이언트
"""

import logging
import httpx
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class OrderClient:
    """주문 서버 클라이언트"""

    def __init__(self, order_server_url: str = "http://localhost:8889"):
        """
        초기화

        Args:
            order_server_url: 주문 서버 URL
        """
        self.order_server_url = order_server_url
        self.client = httpx.AsyncClient(timeout=10.0)  # 10초 타임아웃

    async def close(self):
        """클라이언트 종료"""
        await self.client.aclose()

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str = 'market',
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        leverage: Optional[int] = None,
        signal_id: Optional[str] = None,
        strategy: Optional[str] = None
    ) -> Dict:
        """
        주문 생성

        Args:
            symbol: 심볼
            side: 'buy' or 'sell'
            order_type: 'market' or 'limit'
            quantity: 수량
            price: 가격 (지정가 주문시)
            stop_loss: 손절가
            take_profit: 익절가
            leverage: 레버리지
            signal_id: 신호 ID (로깅용)
            strategy: 전략명 (로깅용)

        Returns:
            주문 결과
        """
        try:
            url = f"{self.order_server_url}/api/orders/create"

            payload = {
                "symbol": symbol,
                "side": side,
                "order_type": order_type,
                "quantity": quantity,
                "price": price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "leverage": leverage,
                "signal_id": signal_id,
                "strategy": strategy
            }

            # None 값 제거
            payload = {k: v for k, v in payload.items() if v is not None}

            logger.info(f"📤 주문 전송: {symbol} {side} → 주문 서버")

            response = await self.client.post(url, json=payload)
            response.raise_for_status()

            result = response.json()
            logger.info(f"✅ 주문 성공: {result.get('message')}")

            return result

        except httpx.HTTPStatusError as e:
            logger.error(f"❌ 주문 실패 (HTTP {e.response.status_code}): {e.response.text}")
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text}"
            }
        except Exception as e:
            logger.error(f"❌ 주문 전송 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "error": str(e)
            }

    async def close_position(
        self,
        symbol: str,
        side: str,
        quantity: Optional[float] = None,
        signal_id: Optional[str] = None,
        reason: Optional[str] = None
    ) -> Dict:
        """
        포지션 종료

        Args:
            symbol: 심볼
            side: 'buy' or 'sell' (종료할 포지션의 반대)
            quantity: 수량 (None이면 전체)
            signal_id: 신호 ID (로깅용)
            reason: 종료 사유 (로깅용)

        Returns:
            종료 결과
        """
        try:
            url = f"{self.order_server_url}/api/orders/close"

            payload = {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "signal_id": signal_id,
                "reason": reason
            }

            # None 값 제거
            payload = {k: v for k, v in payload.items() if v is not None}

            logger.info(f"📤 포지션 종료 전송: {symbol} → 주문 서버")

            response = await self.client.post(url, json=payload)
            response.raise_for_status()

            result = response.json()
            logger.info(f"✅ 포지션 종료 성공: {result.get('message')}")

            return result

        except httpx.HTTPStatusError as e:
            logger.error(f"❌ 포지션 종료 실패 (HTTP {e.response.status_code}): {e.response.text}")
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text}"
            }
        except Exception as e:
            logger.error(f"❌ 포지션 종료 전송 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "error": str(e)
            }

    async def get_active_orders(self, symbol: Optional[str] = None) -> Dict:
        """
        활성 주문 조회

        Args:
            symbol: 심볼 (선택)

        Returns:
            활성 주문 리스트
        """
        try:
            url = f"{self.order_server_url}/api/orders/active"

            params = {}
            if symbol:
                params['symbol'] = symbol

            response = await self.client.get(url, params=params)
            response.raise_for_status()

            return response.json()

        except Exception as e:
            logger.error(f"❌ 활성 주문 조회 실패: {e}")
            return {
                "success": False,
                "error": str(e),
                "orders": []
            }

    async def cancel_order(self, order_id: str, symbol: str) -> Dict:
        """
        주문 취소

        Args:
            order_id: 주문 ID
            symbol: 심볼

        Returns:
            취소 결과
        """
        try:
            url = f"{self.order_server_url}/api/orders/cancel/{order_id}"

            params = {"symbol": symbol}

            response = await self.client.delete(url, params=params)
            response.raise_for_status()

            return response.json()

        except Exception as e:
            logger.error(f"❌ 주문 취소 실패: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def get_current_positions(self, symbol: Optional[str] = None) -> Dict:
        """
        현재 포지션 조회

        Args:
            symbol: 심볼 (선택)

        Returns:
            현재 포지션 리스트
        """
        try:
            url = f"{self.order_server_url}/api/positions/current"

            params = {}
            if symbol:
                params['symbol'] = symbol

            response = await self.client.get(url, params=params)
            response.raise_for_status()

            return response.json()

        except Exception as e:
            logger.error(f"❌ 포지션 조회 실패: {e}")
            return {
                "success": False,
                "error": str(e),
                "positions": []
            }

    async def get_balance(self) -> Dict:
        """
        계좌 잔고 조회

        Returns:
            잔고 정보
        """
        try:
            url = f"{self.order_server_url}/api/balance"

            response = await self.client.get(url)
            response.raise_for_status()

            return response.json()

        except Exception as e:
            logger.error(f"❌ 잔고 조회 실패: {e}")
            return {
                "success": False,
                "error": str(e),
                "balance": {}
            }

    async def health_check(self) -> bool:
        """
        주문 서버 헬스 체크

        Returns:
            연결 상태 (True/False)
        """
        try:
            url = f"{self.order_server_url}/health"

            response = await self.client.get(url, timeout=5.0)
            response.raise_for_status()

            result = response.json()
            return result.get('status') == 'healthy'

        except Exception as e:
            logger.warning(f"⚠️  주문 서버 연결 실패: {e}")
            return False
