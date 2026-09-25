"""
주문 실행 전용 서버 (Order Execution Server)

신호 분석 서버(api_server.py)로부터 주문 요청을 받아
최소 지연으로 바이낸스에 주문을 전송합니다.

포트: 8889
"""

import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, List
from contextlib import asynccontextmanager
import uvicorn

from core.config_loader import get_config
from exchanges.binance_live import BinanceLive

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 전역 변수
exchange: Optional[BinanceLive] = None


# ============================================================
# Pydantic 모델
# ============================================================

class OrderRequest(BaseModel):
    """주문 요청"""
    symbol: str
    side: str  # 'buy' or 'sell'
    order_type: str = 'market'  # 'market' or 'limit'
    quantity: Optional[float] = None
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    leverage: Optional[int] = None
    reduce_only: bool = False

    # 신호 정보 (로깅용)
    signal_id: Optional[str] = None
    strategy: Optional[str] = None


class ClosePositionRequest(BaseModel):
    """포지션 종료 요청"""
    symbol: str
    side: str  # 'buy' or 'sell' (종료할 포지션의 반대)
    quantity: Optional[float] = None  # None이면 전체 종료

    # 신호 정보 (로깅용)
    signal_id: Optional[str] = None
    reason: Optional[str] = None  # 'tp', 'sl', 'manual'


# ============================================================
# 생명주기
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 실행"""
    global exchange

    try:
        logger.info("🚀 주문 서버 시작...")

        # Config 로드
        config = get_config()
        logger.info("✅ Config 로드")

        # 거래소 연결 (API 키를 문자열로 전달)
        import os
        api_key = (
            os.getenv('BINANCE_LIVE_API_KEY')
            or config.get('api.binance.live.api_key')
            or config.get('binance.api_key')
        )
        api_secret = (
            os.getenv('BINANCE_LIVE_API_SECRET')
            or config.get('api.binance.live.api_secret')
            or config.get('binance.api_secret')
        )

        exchange = BinanceLive(api_key=api_key, api_secret=api_secret)
        logger.info("✅ 바이낸스 거래소 연결")

        # 연결 테스트
        balance = exchange.get_balance()
        logger.info(f"✅ 잔고: ${balance['total']:.2f}")

        logger.info("🎉 주문 서버 준비 완료!")

        yield

        # 종료
        logger.info("👋 주문 서버 종료")

    except Exception as e:
        logger.error(f"❌ 서버 시작 실패: {e}")
        raise


# ============================================================
# FastAPI 앱
# ============================================================

app = FastAPI(
    title="Trading Bot - Order Execution Server",
    description="주문 실행 전용 서버 (최소 지연)",
    version="1.0.0",
    lifespan=lifespan
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 헬스체크
# ============================================================

@app.get("/")
async def root():
    """루트 엔드포인트"""
    return {
        "service": "Order Execution Server",
        "status": "running",
        "port": 8889
    }


@app.get("/health")
async def health_check():
    """헬스 체크"""
    try:
        # 거래소 연결 확인
        if not exchange:
            raise Exception("거래소 연결 없음")

        balance = exchange.get_balance()

        return {
            "status": "healthy",
            "exchange": "connected",
            "balance": balance['total'],
            "timestamp": balance.get('timestamp')
        }
    except Exception as e:
        logger.error(f"❌ 헬스체크 실패: {e}")
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")


# ============================================================
# 주문 API
# ============================================================

@app.post("/api/orders/create")
async def create_order(request: OrderRequest):
    """
    주문 생성 (시장가/지정가)

    최소 지연으로 바이낸스에 주문을 전송합니다.
    """
    try:
        logger.info(f"📨 주문 요청: {request.symbol} {request.side} {request.order_type}")

        if not exchange:
            raise HTTPException(status_code=503, detail="거래소 연결 없음")

        # 레버리지 설정
        if request.leverage:
            exchange.set_leverage(request.symbol, request.leverage)
            logger.info(f"⚙️  레버리지 설정: {request.leverage}x")

        # 주문 실행
        if request.order_type == 'market':
            # 시장가 주문
            result = exchange.create_market_order(
                symbol=request.symbol,
                side=request.side,
                amount=request.quantity
            )
        elif request.order_type == 'limit':
            # 지정가 주문
            result = exchange.create_limit_order(
                symbol=request.symbol,
                side=request.side,
                amount=request.quantity,
                price=request.price
            )
        else:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 주문 타입: {request.order_type}")

        # TP/SL 설정 (주문 성공 시)
        if result and result.get('id'):
            order_id = result['id']

            # Stop Loss
            if request.stop_loss:
                try:
                    sl_side = 'sell' if request.side == 'buy' else 'buy'
                    sl_result = exchange.create_stop_loss_order(
                        symbol=request.symbol,
                        side=sl_side,
                        amount=request.quantity,
                        stop_price=request.stop_loss
                    )
                    logger.info(f"🛡️  Stop Loss 설정: ${request.stop_loss}")
                except Exception as sl_error:
                    logger.error(f"⚠️  Stop Loss 설정 실패: {sl_error}")

            # Trailing Stop (1% 콜백)
            if request.take_profit:
                try:
                    tp_side = 'sell' if request.side == 'buy' else 'buy'
                    tp_result = exchange.create_trailing_stop_order(
                        symbol=request.symbol,
                        side=tp_side,
                        amount=request.quantity,
                        callback_rate=1.0  # 1% 콜백
                    )
                    logger.info(f"🎯 Trailing Stop 설정: 1% 콜백")
                except Exception as tp_error:
                    logger.error(f"⚠️  Trailing Stop 설정 실패: {tp_error}")

        logger.info(f"✅ 주문 성공: {result.get('id')}")

        return {
            "success": True,
            "order": result,
            "signal_id": request.signal_id,
            "message": f"{request.symbol} {request.side} 주문 완료"
        }

    except Exception as e:
        logger.error(f"❌ 주문 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/orders/close")
async def close_position(request: ClosePositionRequest):
    """
    포지션 종료

    최소 지연으로 포지션을 종료합니다.
    """
    try:
        logger.info(f"📨 포지션 종료 요청: {request.symbol} {request.side}")

        if not exchange:
            raise HTTPException(status_code=503, detail="거래소 연결 없음")

        # 시장가로 즉시 종료
        result = exchange.create_market_order(
            symbol=request.symbol,
            side=request.side,
            amount=request.quantity,
            reduce_only=True  # 포지션 감소만
        )

        logger.info(f"✅ 포지션 종료 성공: {result.get('id')}")

        return {
            "success": True,
            "order": result,
            "signal_id": request.signal_id,
            "reason": request.reason,
            "message": f"{request.symbol} 포지션 종료 완료"
        }

    except Exception as e:
        logger.error(f"❌ 포지션 종료 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/orders/active")
async def get_active_orders(symbol: Optional[str] = None):
    """
    활성 주문 조회
    """
    try:
        if not exchange:
            raise HTTPException(status_code=503, detail="거래소 연결 없음")

        orders = exchange.get_open_orders(symbol=symbol)

        return {
            "success": True,
            "orders": orders,
            "count": len(orders)
        }

    except Exception as e:
        logger.error(f"❌ 활성 주문 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/orders/cancel/{order_id}")
async def cancel_order(order_id: str, symbol: str):
    """
    주문 취소
    """
    try:
        logger.info(f"📨 주문 취소 요청: {order_id}")

        if not exchange:
            raise HTTPException(status_code=503, detail="거래소 연결 없음")

        result = exchange.cancel_order(order_id, symbol)

        logger.info(f"✅ 주문 취소 성공: {order_id}")

        return {
            "success": True,
            "order": result,
            "message": f"주문 {order_id} 취소 완료"
        }

    except Exception as e:
        logger.error(f"❌ 주문 취소 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/current")
async def get_current_positions(symbol: Optional[str] = None):
    """
    현재 포지션 조회
    """
    try:
        if not exchange:
            raise HTTPException(status_code=503, detail="거래소 연결 없음")

        positions = exchange.get_positions(symbol=symbol)

        # 활성 포지션만 필터링
        active_positions = [p for p in positions if float(p.get('contracts', 0)) > 0]

        return {
            "success": True,
            "positions": active_positions,
            "count": len(active_positions)
        }

    except Exception as e:
        logger.error(f"❌ 포지션 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/balance")
async def get_balance():
    """
    계좌 잔고 조회
    """
    try:
        if not exchange:
            raise HTTPException(status_code=503, detail="거래소 연결 없음")

        balance = exchange.get_balance()

        return {
            "success": True,
            "balance": balance
        }

    except Exception as e:
        logger.error(f"❌ 잔고 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 실행
# ============================================================

if __name__ == "__main__":
    uvicorn.run(
        "order_server:app",
        host="0.0.0.0",
        port=8889,
        reload=True,
        log_level="info"
    )
