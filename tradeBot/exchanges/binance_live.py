"""
Binance Live Exchange (실전용)

⚠️ 주의사항:
1. 소액으로 시작 ($100-$500)
2. API 키 권한: 거래만 활성화, 출금 비활성화
3. IP 제한 설정 필수
4. 2FA 인증 필수
"""

import ccxt
import os
from typing import Dict, List, Optional, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class BinanceLive:
    """
    Binance 실전 거래소
    
    안전장치:
    - 최대 포지션 제한
    - 일일 손실 한도
    - 긴급 정지 기능
    - 모든 거래 로깅
    """
    
    def __init__(
        self, 
        api_key: str = None, 
        api_secret: str = None,
        max_positions: int = 2,  # 최대 2개로 제한
        max_daily_loss: float = 0.02,  # 일일 최대 2% 손실
        initial_capital: float = 100.0  # 초기 자본 (매우 소액)
    ):
        """
        초기화
        
        Args:
            api_key: Binance API Key
            api_secret: Binance API Secret
            max_positions: 최대 포지션 수 (기본: 2)
            max_daily_loss: 일일 최대 손실 비율 (기본: 2%)
            initial_capital: 초기 자본 (기본: $100)
        """
        # API 키
        self.api_key = api_key or os.getenv('BINANCE_LIVE_API_KEY')
        self.api_secret = api_secret or os.getenv('BINANCE_LIVE_API_SECRET')
        
        if not self.api_key or not self.api_secret:
            raise ValueError("⚠️ 실전 API 키가 필요합니다!")
        
        # 안전장치 설정
        self.max_positions = max_positions
        self.max_daily_loss = max_daily_loss
        self.initial_capital = initial_capital
        self.daily_start_capital = initial_capital
        self.emergency_stop = False
        
        # CCXT 거래소 객체 (선물 전용)
        self.exchange = ccxt.binanceusdm({
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'enableRateLimit': True,
            'options': {
                'adjustForTimeDifference': True
            }
        })

        # 실전 모드 (샌드박스 비활성화)
        self.exchange.set_sandbox_mode(False)

        # 마켓 정보 로드 (정밀도 처리용) - 선물만 로드
        self.exchange.load_markets()

        logger.warning("⚠️⚠️⚠️ 실전 모드 활성화! ⚠️⚠️⚠️")
        logger.info(f"   최대 포지션: {max_positions}개")
        logger.info(f"   일일 손실 한도: {max_daily_loss*100}%")
        logger.info(f"   초기 자본: ${initial_capital:.2f}")
    
    def _get_precision(self, symbol: str) -> Dict[str, Any]:
        """
        심볼의 정밀도 정보 조회

        Returns:
            {'price': 소수점자릿수 또는 tick_size, 'amount': 소수점자릿수 또는 step_size}
        """
        try:
            market = self.exchange.market(symbol)
            return {
                'price': market['precision'].get('price'),
                'amount': market['precision'].get('amount'),
                'min_amount': market['limits']['amount']['min'] if market.get('limits') else None
            }
        except Exception as e:
            logger.warning(f"⚠️ {symbol} 정밀도 조회 실패: {e}")
            return {'price': 8, 'amount': 8, 'min_amount': None}

    def _round_price(self, price: float, symbol: str) -> float:
        """가격을 심볼의 정밀도에 맞게 반올림"""
        precision = self._get_precision(symbol)
        price_prec = precision['price']

        if price_prec is None:
            return price

        if isinstance(price_prec, float) and price_prec < 1:
            # tick size 방식: 0.0001 단위로 반올림
            return round(price / price_prec) * price_prec
        else:
            # 자릿수 방식
            return round(price, int(price_prec))

    def _round_amount(self, amount: float, symbol: str) -> float:
        """수량을 심볼의 정밀도에 맞게 반올림"""
        precision = self._get_precision(symbol)
        amount_prec = precision['amount']
        min_amount = precision['min_amount']

        if amount_prec is not None:
            if isinstance(amount_prec, float) and amount_prec < 1:
                amount = round(amount / amount_prec) * amount_prec
            else:
                amount = round(amount, int(amount_prec))

        if min_amount and amount < min_amount:
            amount = min_amount

        return amount

    def test_connection(self) -> bool:
        """
        연결 테스트 (실전)
        """
        try:
            balance = self.exchange.fetch_balance()
            usdt_balance = balance['USDT']['free']
            
            logger.info(f"✅ Binance 실전 연결 성공")
            logger.info(f"   USDT 잔고: ${usdt_balance:,.2f}")
            
            # 경고
            if usdt_balance > 1000:
                logger.warning(f"⚠️ 잔고가 높습니다: ${usdt_balance:,.2f}")
                logger.warning(f"⚠️ 소액($100-$500)으로 시작하는 것을 권장합니다!")
            
            return True
        
        except Exception as e:
            logger.error(f"❌ 연결 실패: {e}")
            return False
    
    def check_safety(self) -> bool:
        """
        안전 점검
        
        Returns:
            거래 가능 여부
        """
        # 1. 긴급 정지 확인
        if self.emergency_stop:
            logger.error("🚨 긴급 정지 활성화! 거래 불가!")
            return False
        
        # 2. 포지션 수 확인
        positions = self.get_positions()
        if len(positions) >= self.max_positions:
            logger.warning(f"⚠️ 최대 포지션 도달: {len(positions)}/{self.max_positions}")
            return False
        
        # 3. 일일 손실 확인
        current_capital = self.get_balance()['total']
        daily_loss = (self.daily_start_capital - current_capital) / self.daily_start_capital
        
        if daily_loss >= self.max_daily_loss:
            logger.error(f"🚨 일일 손실 한도 도달: {daily_loss*100:.2f}%")
            logger.error(f"🚨 오늘은 더 이상 거래할 수 없습니다!")
            self.emergency_stop = True
            return False
        
        return True
    
    def get_balance(self, currency: str = 'USDT') -> Dict[str, float]:
        """선물 잔고 조회"""
        try:
            # 선물 잔고 조회 (fapiPrivateV2GetBalance 사용)
            balances = self.exchange.fapiPrivateV2GetBalance()

            for bal in balances:
                if bal.get('asset') == currency:
                    return {
                        'free': float(bal.get('availableBalance', 0)),
                        'used': float(bal.get('balance', 0)) - float(bal.get('availableBalance', 0)),
                        'total': float(bal.get('balance', 0))
                    }

            # 통화를 찾지 못한 경우
            return {'free': 0, 'used': 0, 'total': 0}

        except Exception as e:
            logger.error(f"❌ 잔고 조회 실패: {e}")
            return {'free': 0, 'used': 0, 'total': 0}
    
    def get_ticker(self, symbol: str = 'BTC/USDT') -> Dict[str, Any]:
        """현재가 조회"""
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
        시장가 주문 (안전장치 포함)
        """
        # 안전 점검
        if not self.check_safety():
            logger.error("❌ 안전 점검 실패! 주문 취소")
            return None

        try:
            # 수량 정밀도 적용
            amount = self._round_amount(amount, symbol)

            # 주문 전 확인
            logger.warning(f"⚠️ 실전 주문 실행:")
            logger.warning(f"   심볼: {symbol}")
            logger.warning(f"   방향: {side.upper()}")
            logger.warning(f"   수량: {amount}")

            # 주문 실행
            order = self.exchange.create_market_order(
                symbol=symbol,
                side=side,
                amount=amount,
                params=params or {}
            )
            
            logger.info(f"✅ 주문 실행 완료: {order['id']}")
            
            # 주문 로그 저장
            self._log_trade(order)
            
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
        """지정가 주문"""
        if not self.check_safety():
            return None

        try:
            # 정밀도 적용
            amount = self._round_amount(amount, symbol)
            price = self._round_price(price, symbol)

            order = self.exchange.create_limit_order(
                symbol=symbol,
                side=side,
                amount=amount,
                price=price,
                params=params or {}
            )

            logger.info(f"✅ 지정가 주문: {side.upper()} {amount} {symbol} @ ${price}")
            self._log_trade(order)
            
            return order
        
        except Exception as e:
            logger.error(f"❌ 주문 실패: {e}")
            return None
    
    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """레버리지 설정"""
        try:
            # 최대 3배로 제한 (보수적)
            if leverage > 3:
                logger.warning(f"⚠️ 레버리지 {leverage}x는 위험합니다!")
                logger.warning(f"⚠️ 최대 3배로 제한합니다.")
                leverage = 3
            
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
        """손절 주문"""
        try:
            # 정밀도 적용
            amount = self._round_amount(amount, symbol)
            stop_price = self._round_price(stop_price, symbol)

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

            logger.info(f"✅ 손절 설정: {symbol} @ ${stop_price}")
            return order

        except Exception as e:
            logger.error(f"❌ 손절 설정 실패: {e}")
            return None

    def create_stop_loss_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float
    ) -> Optional[Dict]:
        """손절 주문 (별칭)"""
        return self.set_stop_loss(symbol, side, amount, stop_price)
    
    def set_take_profit(
        self,
        symbol: str,
        side: str,
        amount: float,
        take_profit_price: float
    ) -> Optional[Dict]:
        """익절 주문"""
        try:
            # 정밀도 적용
            amount = self._round_amount(amount, symbol)
            take_profit_price = self._round_price(take_profit_price, symbol)

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

            logger.info(f"✅ 익절 설정: {symbol} @ ${take_profit_price}")
            return order

        except Exception as e:
            logger.error(f"❌ 익절 설정 실패: {e}")
            return None

    def create_trailing_stop_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        callback_rate: float = 1.0
    ) -> Optional[Dict]:
        """
        Trailing Stop 주문 생성

        Args:
            symbol: 심볼
            side: 'buy' or 'sell'
            amount: 수량
            callback_rate: 콜백 비율 (%) - 기본값 1.0%

        Returns:
            주문 정보
        """
        try:
            # 수량 정밀도 적용
            amount = self._round_amount(amount, symbol)

            # 바이낸스 선물 Trailing Stop 주문
            params = {
                'callbackRate': callback_rate,  # 콜백 비율 (%)
                'activationPrice': None,  # 활성화 가격 (None이면 즉시 활성화)
                'reduceOnly': True  # 포지션 감소만 허용
            }

            order = self.exchange.create_order(
                symbol=symbol,
                type='TRAILING_STOP_MARKET',
                side=side,
                amount=amount,
                params=params
            )

            logger.info(f"✅ Trailing Stop 설정: {symbol} {side} {amount} (콜백: {callback_rate}%)")
            return order

        except Exception as e:
            logger.error(f"❌ Trailing Stop 설정 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def get_positions(self) -> List[Dict]:
        """포지션 조회"""
        try:
            positions = self.exchange.fetch_positions()
            
            # 활성 포지션만
            active_positions = [
                p for p in positions 
                if float(p.get('contracts', 0)) > 0
            ]
            
            return active_positions
        
        except Exception as e:
            logger.error(f"❌ 포지션 조회 실패: {e}")
            return []
    
    def close_position(self, symbol: str) -> bool:
        """포지션 종료"""
        try:
            positions = self.get_positions()
            
            for pos in positions:
                if pos['symbol'] == symbol:
                    amount = abs(float(pos['contracts']))
                    side = 'sell' if float(pos['contracts']) > 0 else 'buy'
                    
                    logger.warning(f"⚠️ 포지션 종료: {symbol}")
                    self.create_market_order(symbol, side, amount)
                    return True
            
            logger.warning(f"⚠️ 포지션 없음: {symbol}")
            return False
        
        except Exception as e:
            logger.error(f"❌ 포지션 종료 실패: {e}")
            return False
    
    def close_all_positions(self) -> int:
        """모든 포지션 종료"""
        logger.warning("🚨 모든 포지션 종료 시작!")
        
        positions = self.get_positions()
        closed = 0
        
        for pos in positions:
            if self.close_position(pos['symbol']):
                closed += 1
        
        logger.info(f"✅ {closed}개 포지션 종료 완료")
        return closed
    
    def activate_emergency_stop(self):
        """긴급 정지 활성화"""
        logger.error("🚨🚨🚨 긴급 정지 활성화! 🚨🚨🚨")
        self.emergency_stop = True
        
        # 모든 포지션 종료
        self.close_all_positions()
        
        logger.error("🚨 모든 거래가 중지되었습니다!")
    
    def _log_trade(self, order: Dict):
        """거래 로그 저장"""
        try:
            log_file = f"logs/trades_{datetime.now().strftime('%Y%m%d')}.log"
            
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"{datetime.now().isoformat()},{order}\n")
        
        except Exception as e:
            logger.error(f"❌ 거래 로그 저장 실패: {e}")
    
    def get_daily_pnl(self) -> Dict[str, float]:
        """일일 손익 조회"""
        current_capital = self.get_balance()['total']
        daily_pnl = current_capital - self.daily_start_capital
        daily_pnl_pct = (daily_pnl / self.daily_start_capital) * 100
        
        return {
            'start_capital': self.daily_start_capital,
            'current_capital': current_capital,
            'pnl': daily_pnl,
            'pnl_pct': daily_pnl_pct
        }
    
    def reset_daily_counter(self):
        """일일 카운터 리셋 (새로운 날)"""
        self.daily_start_capital = self.get_balance()['total']
        self.emergency_stop = False

    def get_trade_history(
        self,
        symbol: Optional[str] = None,
        since: Optional[int] = None,
        limit: int = 1000
    ) -> List[Dict]:
        """
        거래 히스토리 조회

        Args:
            symbol: 심볼 (None이면 전체)
            since: 시작 타임스탬프 (밀리초)
            limit: 최대 개수

        Returns:
            거래 리스트
        """
        try:
            if symbol:
                # 특정 심볼 조회
                trades = self.exchange.fetch_my_trades(
                    symbol=symbol,
                    since=since,
                    limit=limit
                )
                logger.info(f"✅ {symbol} 거래 히스토리: {len(trades)}개")
            else:
                # 바이낸스 선물 API로 모든 거래 조회
                logger.info("🔄 모든 선물 거래 조회 중...")

                params = {}
                if since:
                    params['startTime'] = since
                if limit:
                    params['limit'] = limit

                # fapiPrivateGetAllOrders 대신 fapiPrivateGetUserTrades 사용
                try:
                    trades = self.exchange.fapiPrivateGetUserTrades(params)
                    logger.info(f"✅ 전체 거래 히스토리: {len(trades)}개")
                except Exception as api_error:
                    logger.warning(f"⚠️ 전체 거래 조회 실패, 개별 심볼 조회 시도: {api_error}")

                    # 대체 방법: 주요 심볼들의 거래 조회
                    popular_symbols = [
                        'BTC/USDT', 'ETH/USDT', 'BNB/USDT',
                        'SOL/USDT', 'XRP/USDT', 'ADA/USDT',
                        'DOGE/USDT', 'MATIC/USDT', 'DOT/USDT', 'AVAX/USDT'
                    ]

                    all_trades = []
                    for sym in popular_symbols:
                        try:
                            sym_trades = self.exchange.fetch_my_trades(
                                symbol=sym,
                                since=since,
                                limit=100
                            )
                            if sym_trades:
                                logger.info(f"  - {sym}: {len(sym_trades)}개")
                                all_trades.extend(sym_trades)
                        except Exception as sym_error:
                            logger.debug(f"  - {sym}: 거래 없음 또는 오류")
                            continue

                    trades = all_trades
                    logger.info(f"✅ 주요 심볼 거래 총계: {len(trades)}개")

            return trades

        except Exception as e:
            logger.error(f"❌ 거래 히스토리 조회 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def get_income_history(
        self,
        symbol: Optional[str] = None,
        income_type: Optional[str] = None,
        since: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict]:
        """
        수입 히스토리 조회 (실현 손익, 수수료 등)

        Args:
            symbol: 심볼
            income_type: 'REALIZED_PNL', 'COMMISSION', 'FUNDING_FEE' 등
            since: 시작 타임스탬프
            limit: 최대 개수

        Returns:
            수입 리스트
        """
        try:
            # CCXT에서 바이낸스 선물 수입 히스토리 API 사용
            params = {}
            if income_type:
                params['incomeType'] = income_type
            if since:
                params['startTime'] = since
            if limit:
                params['limit'] = limit
            if symbol:
                params['symbol'] = symbol.replace('/', '')

            income = self.exchange.fapiPrivateGetIncome(params)

            logger.info(f"✅ 수입 히스토리 조회: {len(income)}개")
            return income

        except Exception as e:
            logger.error(f"❌ 수입 히스토리 조회 실패: {e}")
            return []


# 테스트 (주의!)
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    # 로깅
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    print("⚠️⚠️⚠️ 실전 모드 테스트 ⚠️⚠️⚠️")
    print()
    print("주의:")
    print("1. 소액으로 시작하세요 ($100-$500)")
    print("2. API 키 권한 확인 (거래만)")
    print("3. IP 제한 설정 확인")
    print()
    
    response = input("계속하시겠습니까? (yes/no): ")
    
    if response.lower() == 'yes':
        # 거래소 연결 (초기 자본 $100)
        exchange = BinanceLive(initial_capital=100.0)
        
        # 연결 테스트
        if exchange.test_connection():
            print("\n✅ 연결 성공!")
            
            # 잔고
            balance = exchange.get_balance()
            print(f"\n💰 USDT 잔고:")
            print(f"   사용가능: ${balance['free']:,.2f}")
            print(f"   사용중: ${balance['used']:,.2f}")
            print(f"   총합: ${balance['total']:,.2f}")
            
            # 현재가
            ticker = exchange.get_ticker('BTC/USDT')
            print(f"\n📊 BTC/USDT:")
            print(f"   Last: ${ticker['last']:,.2f}")
            
            # 포지션
            positions = exchange.get_positions()
            print(f"\n📈 열린 포지션: {len(positions)}개")
            
            # 안전 점검
            print(f"\n🛡️ 안전 점검:")
            if exchange.check_safety():
                print("   ✅ 거래 가능")
            else:
                print("   ❌ 거래 불가")
        
        else:
            print("\n❌ 연결 실패!")
    
    else:
        print("\n취소됨")
