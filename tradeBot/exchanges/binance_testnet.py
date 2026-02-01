"""
Binance Testnet Exchange

테스트넷에서 안전하게 거래 테스트
"""

import ccxt
import os
from typing import Dict, List, Optional, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class BinanceTestnet:
    """
    Binance Testnet 거래소 클래스
    
    - 안전한 테스트 환경
    - 실제 시장 데이터
    - 가상 주문 실행
    """
    
    def __init__(self, api_key: str = None, api_secret: str = None):
        """
        초기화
        
        Args:
            api_key: Binance Testnet API Key
            api_secret: Binance Testnet API Secret
        """
        # 환경 변수에서 로드
        self.api_key = api_key or os.getenv('BINANCE_TESTNET_API_KEY')
        self.api_secret = api_secret or os.getenv('BINANCE_TESTNET_API_SECRET')
        
        if not self.api_key or not self.api_secret:
            raise ValueError("API 키가 필요합니다. .env 파일을 확인하세요.")
        
        # CCXT 거래소 객체
        self.exchange = ccxt.binance({
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',  # 선물
                'adjustForTimeDifference': True
            }
        })
        
        # 테스트넷 URL 설정
        self.exchange.set_sandbox_mode(True)
        
        logger.info("✅ Binance Testnet 연결 완료")
    
    def test_connection(self) -> bool:
        """
        연결 테스트
        
        Returns:
            성공 여부
        """
        try:
            balance = self.exchange.fetch_balance()
            logger.info(f"✅ 연결 성공! USDT 잔고: {balance['USDT']['free']:.2f}")
            return True
        except Exception as e:
            logger.error(f"❌ 연결 실패: {e}")
            return False
    
    def get_balance(self, currency: str = 'USDT') -> Dict[str, float]:
        """
        잔고 조회
        
        Args:
            currency: 통화 (기본: USDT)
        
        Returns:
            {'free': 사용가능, 'used': 사용중, 'total': 총합}
        """
        try:
            balance = self.exchange.fetch_balance()
            return {
                'free': balance[currency]['free'],
                'used': balance[currency]['used'],
                'total': balance[currency]['total']
            }
        except Exception as e:
            logger.error(f"❌ 잔고 조회 실패: {e}")
            return {'free': 0, 'used': 0, 'total': 0}
    
    def get_ticker(self, symbol: str = 'BTC/USDT') -> Dict[str, Any]:
        """
        현재가 조회
        
        Args:
            symbol: 심볼 (예: 'BTC/USDT')
        
        Returns:
            가격 정보
        """
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return {
                'symbol': symbol,
                'last': ticker['last'],
                'bid': ticker['bid'],
                'ask': ticker['ask'],
                'high': ticker['high'],
                'low': ticker['low'],
                'volume': ticker['volume']
            }
        except Exception as e:
            logger.error(f"❌ 가격 조회 실패: {e}")
            return {}
    
    def create_market_order(
        self, 
        symbol: str, 
        side: str, 
        amount: float,
        params: Dict = None
    ) -> Optional[Dict]:
        """
        시장가 주문
        
        Args:
            symbol: 심볼 (예: 'BTC/USDT')
            side: 'buy' or 'sell'
            amount: 수량
            params: 추가 파라미터
        
        Returns:
            주문 정보
        """
        try:
            order = self.exchange.create_market_order(
                symbol=symbol,
                side=side,
                amount=amount,
                params=params or {}
            )
            
            logger.info(f"✅ 시장가 주문 실행: {side.upper()} {amount} {symbol}")
            return order
        
        except Exception as e:
            logger.error(f"❌ 주문 실패: {e}")
            return None
    
    def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        params: Dict = None
    ) -> Optional[Dict]:
        """
        지정가 주문
        
        Args:
            symbol: 심볼
            side: 'buy' or 'sell'
            amount: 수량
            price: 가격
            params: 추가 파라미터
        
        Returns:
            주문 정보
        """
        try:
            order = self.exchange.create_limit_order(
                symbol=symbol,
                side=side,
                amount=amount,
                price=price,
                params=params or {}
            )
            
            logger.info(f"✅ 지정가 주문: {side.upper()} {amount} {symbol} @ ${price}")
            return order
        
        except Exception as e:
            logger.error(f"❌ 주문 실패: {e}")
            return None
    
    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        레버리지 설정 (선물)
        
        Args:
            symbol: 심볼
            leverage: 레버리지 (1-125)
        
        Returns:
            성공 여부
        """
        try:
            self.exchange.set_leverage(leverage, symbol)
            logger.info(f"✅ 레버리지 설정: {symbol} -> {leverage}x")
            return True
        except Exception as e:
            logger.error(f"❌ 레버리지 설정 실패: {e}")
            return False
    
    def set_stop_loss(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float
    ) -> Optional[Dict]:
        """
        손절 주문
        
        Args:
            symbol: 심볼
            side: 'buy' or 'sell'
            amount: 수량
            stop_price: 손절가
        
        Returns:
            주문 정보
        """
        try:
            params = {
                'stopPrice': stop_price,
                'type': 'STOP_MARKET'
            }
            
            order = self.exchange.create_order(
                symbol=symbol,
                type='stop',
                side=side,
                amount=amount,
                params=params
            )
            
            logger.info(f"✅ 손절 주문: {symbol} @ ${stop_price}")
            return order
        
        except Exception as e:
            logger.error(f"❌ 손절 설정 실패: {e}")
            return None
    
    def set_take_profit(
        self,
        symbol: str,
        side: str,
        amount: float,
        take_profit_price: float
    ) -> Optional[Dict]:
        """
        익절 주문
        
        Args:
            symbol: 심볼
            side: 'buy' or 'sell'
            amount: 수량
            take_profit_price: 익절가
        
        Returns:
            주문 정보
        """
        try:
            params = {
                'stopPrice': take_profit_price,
                'type': 'TAKE_PROFIT_MARKET'
            }
            
            order = self.exchange.create_order(
                symbol=symbol,
                type='take_profit',
                side=side,
                amount=amount,
                params=params
            )
            
            logger.info(f"✅ 익절 주문: {symbol} @ ${take_profit_price}")
            return order
        
        except Exception as e:
            logger.error(f"❌ 익절 설정 실패: {e}")
            return None
    
    def get_open_orders(self, symbol: str = None) -> List[Dict]:
        """
        열린 주문 조회
        
        Args:
            symbol: 심볼 (None이면 전체)
        
        Returns:
            주문 목록
        """
        try:
            orders = self.exchange.fetch_open_orders(symbol)
            return orders
        except Exception as e:
            logger.error(f"❌ 주문 조회 실패: {e}")
            return []
    
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """
        주문 취소
        
        Args:
            order_id: 주문 ID
            symbol: 심볼
        
        Returns:
            성공 여부
        """
        try:
            self.exchange.cancel_order(order_id, symbol)
            logger.info(f"✅ 주문 취소: {order_id}")
            return True
        except Exception as e:
            logger.error(f"❌ 주문 취소 실패: {e}")
            return False
    
    def cancel_all_orders(self, symbol: str) -> int:
        """
        모든 주문 취소
        
        Args:
            symbol: 심볼
        
        Returns:
            취소된 주문 수
        """
        try:
            orders = self.get_open_orders(symbol)
            count = 0
            
            for order in orders:
                if self.cancel_order(order['id'], symbol):
                    count += 1
            
            logger.info(f"✅ {count}개 주문 취소")
            return count
        
        except Exception as e:
            logger.error(f"❌ 일괄 취소 실패: {e}")
            return 0
    
    def get_positions(self) -> List[Dict]:
        """
        포지션 조회 (선물)
        
        Returns:
            포지션 목록
        """
        try:
            positions = self.exchange.fetch_positions()
            
            # 포지션이 있는 것만 필터
            active_positions = [
                p for p in positions 
                if float(p.get('contracts', 0)) > 0
            ]
            
            return active_positions
        
        except Exception as e:
            logger.error(f"❌ 포지션 조회 실패: {e}")
            return []
    
    def close_position(self, symbol: str) -> bool:
        """
        포지션 종료
        
        Args:
            symbol: 심볼
        
        Returns:
            성공 여부
        """
        try:
            positions = self.get_positions()
            
            for pos in positions:
                if pos['symbol'] == symbol:
                    amount = abs(float(pos['contracts']))
                    side = 'sell' if float(pos['contracts']) > 0 else 'buy'
                    
                    self.create_market_order(symbol, side, amount)
                    logger.info(f"✅ 포지션 종료: {symbol}")
                    return True
            
            logger.warning(f"⚠️  포지션 없음: {symbol}")
            return False
        
        except Exception as e:
            logger.error(f"❌ 포지션 종료 실패: {e}")
            return False


# 테스트
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    # 거래소 연결
    exchange = BinanceTestnet()
    
    # 연결 테스트
    if exchange.test_connection():
        print("\n✅ 테스트넷 연결 성공!")
        
        # 잔고 확인
        balance = exchange.get_balance()
        print(f"\n💰 USDT 잔고:")
        print(f"   사용가능: ${balance['free']:,.2f}")
        print(f"   사용중: ${balance['used']:,.2f}")
        print(f"   총합: ${balance['total']:,.2f}")
        
        # 현재가 확인
        ticker = exchange.get_ticker('BTC/USDT')
        print(f"\n📊 BTC/USDT 현재가:")
        print(f"   Last: ${ticker['last']:,.2f}")
        print(f"   Bid: ${ticker['bid']:,.2f}")
        print(f"   Ask: ${ticker['ask']:,.2f}")
        
        # 포지션 확인
        positions = exchange.get_positions()
        print(f"\n📈 열린 포지션: {len(positions)}개")
    
    else:
        print("\n❌ 연결 실패!")
        print("   .env 파일에 API 키를 확인하세요.")
