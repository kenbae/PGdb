"""
Binance Futures WebSocket 데이터 피드

실시간 가격, 캔들, 거래 데이터 수신
"""

import asyncio
import websockets
import json
import logging
from typing import Dict, List, Callable, Optional
from datetime import datetime
import threading

logger = logging.getLogger(__name__)


class BinanceWebSocket:
    """
    Binance Futures WebSocket 클라이언트
    
    Features:
    - 실시간 가격 (ticker)
    - 실시간 캔들 (kline)
    - 자동 재연결
    - 콜백 시스템
    """
    
    def __init__(
        self,
        symbols: List[str] = None,
        timeframes: List[str] = None,
        base_url: str = "wss://fstream.binance.com"
    ):
        """
        초기화
        
        Args:
            symbols: 심볼 리스트 (예: ['BTCUSDT', 'ETHUSDT'])
            timeframes: 타임프레임 리스트 (예: ['1m', '5m', '15m'])
            base_url: WebSocket URL (기본: Futures)
        """
        self.symbols = symbols or ['BTCUSDT']
        self.timeframes = timeframes or ['1m', '15m', '1h']
        self.base_url = base_url
        
        # WebSocket 연결
        self.ws = None
        self.is_running = False
        self.loop = None
        self.thread = None
        
        # 콜백
        self.callbacks = {
            'ticker': [],
            'kline': [],
            'trade': [],
            'error': []
        }
        
        # 재연결
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        
        logger.info(f"✅ WebSocket 초기화")
        logger.info(f"   심볼: {', '.join(self.symbols)}")
        logger.info(f"   타임프레임: {', '.join(self.timeframes)}")
    
    def on_ticker(self, callback: Callable):
        """
        Ticker 콜백 등록
        
        Args:
            callback: 함수 (data: dict)
        """
        self.callbacks['ticker'].append(callback)
    
    def on_kline(self, callback: Callable):
        """
        Kline 콜백 등록
        
        Args:
            callback: 함수 (data: dict)
        """
        self.callbacks['kline'].append(callback)
    
    def on_trade(self, callback: Callable):
        """
        Trade 콜백 등록
        
        Args:
            callback: 함수 (data: dict)
        """
        self.callbacks['trade'].append(callback)
    
    def on_error(self, callback: Callable):
        """
        Error 콜백 등록
        
        Args:
            callback: 함수 (error: Exception)
        """
        self.callbacks['error'].append(callback)
    
    def _get_stream_names(self) -> List[str]:
        """
        스트림 이름 생성
        
        Returns:
            스트림 이름 리스트
        """
        streams = []
        
        for symbol in self.symbols:
            symbol_lower = symbol.lower()
            
            # Ticker (24hr mini ticker)
            streams.append(f"{symbol_lower}@miniTicker")
            
            # Klines (각 타임프레임)
            for tf in self.timeframes:
                streams.append(f"{symbol_lower}@kline_{tf}")
        
        return streams
    
    def _build_ws_url(self) -> str:
        """
        WebSocket URL 생성
        
        Returns:
            완성된 URL
        """
        streams = self._get_stream_names()
        stream_path = '/'.join(streams)
        
        url = f"{self.base_url}/stream?streams={stream_path}"
        
        return url
    
    async def _connect(self):
        """WebSocket 연결"""
        url = self._build_ws_url()
        
        logger.info(f"🔌 WebSocket 연결 시도...")
        logger.debug(f"   URL: {url[:100]}...")
        
        try:
            self.ws = await websockets.connect(url, ping_interval=20, ping_timeout=10)
            self.reconnect_attempts = 0
            
            logger.info(f"✅ WebSocket 연결 성공")
            
        except Exception as e:
            logger.error(f"❌ WebSocket 연결 실패: {e}")
            raise
    
    async def _handle_message(self, message: str):
        """
        메시지 처리
        
        Args:
            message: JSON 메시지
        """
        try:
            data = json.loads(message)
            
            # 스트림 데이터
            if 'stream' in data:
                stream = data['stream']
                payload = data['data']
                
                # Ticker
                if 'miniTicker' in stream:
                    self._handle_ticker(payload)
                
                # Kline
                elif 'kline' in stream:
                    self._handle_kline(payload)
                
                # Trade
                elif 'trade' in stream:
                    self._handle_trade(payload)
        
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON 파싱 실패: {e}")
        
        except Exception as e:
            logger.error(f"❌ 메시지 처리 실패: {e}")
            self._trigger_callbacks('error', e)
    
    def _handle_ticker(self, data: Dict):
        """
        Ticker 데이터 처리
        
        Args:
            data: Ticker 데이터
        """
        try:
            ticker = {
                'symbol': data['s'],
                'last': float(data['c']),
                'open': float(data['o']),
                'high': float(data['h']),
                'low': float(data['l']),
                'volume': float(data['v']),
                'quote_volume': float(data['q']),
                'timestamp': data['E']
            }
            
            self._trigger_callbacks('ticker', ticker)
        
        except Exception as e:
            logger.error(f"❌ Ticker 처리 실패: {e}")
    
    def _handle_kline(self, data: Dict):
        """
        Kline 데이터 처리
        
        Args:
            data: Kline 데이터
        """
        try:
            kline_data = data['k']
            
            kline = {
                'symbol': kline_data['s'],
                'timeframe': kline_data['i'],
                'open_time': kline_data['t'],
                'close_time': kline_data['T'],
                'open': float(kline_data['o']),
                'high': float(kline_data['h']),
                'low': float(kline_data['l']),
                'close': float(kline_data['c']),
                'volume': float(kline_data['v']),
                'is_closed': kline_data['x'],  # 캔들 완성 여부
                'quote_volume': float(kline_data['q']),
                'trades': kline_data['n']
            }
            
            # 캔들 완성 시에만 콜백 (선택)
            if kline['is_closed']:
                self._trigger_callbacks('kline', kline)
        
        except Exception as e:
            logger.error(f"❌ Kline 처리 실패: {e}")
    
    def _handle_trade(self, data: Dict):
        """
        Trade 데이터 처리
        
        Args:
            data: Trade 데이터
        """
        try:
            trade = {
                'symbol': data['s'],
                'price': float(data['p']),
                'quantity': float(data['q']),
                'timestamp': data['T'],
                'is_buyer_maker': data['m']
            }
            
            self._trigger_callbacks('trade', trade)
        
        except Exception as e:
            logger.error(f"❌ Trade 처리 실패: {e}")
    
    def _trigger_callbacks(self, event_type: str, data):
        """
        콜백 실행
        
        Args:
            event_type: 이벤트 타입 ('ticker', 'kline', 'trade', 'error')
            data: 데이터
        """
        for callback in self.callbacks.get(event_type, []):
            try:
                callback(data)
            except Exception as e:
                logger.error(f"❌ 콜백 실행 실패 ({event_type}): {e}")
    
    async def _listen(self):
        """메시지 수신 루프"""
        try:
            async for message in self.ws:
                await self._handle_message(message)
        
        except websockets.exceptions.ConnectionClosed:
            logger.warning("⚠️  WebSocket 연결 종료")
            await self._reconnect()
        
        except Exception as e:
            logger.error(f"❌ 수신 에러: {e}")
            self._trigger_callbacks('error', e)
            await self._reconnect()
    
    async def _reconnect(self):
        """재연결"""
        if not self.is_running:
            return
        
        self.reconnect_attempts += 1
        
        if self.reconnect_attempts > self.max_reconnect_attempts:
            logger.error(f"❌ 최대 재연결 시도 초과: {self.max_reconnect_attempts}")
            self.stop()
            return
        
        wait_time = min(2 ** self.reconnect_attempts, 60)
        logger.info(f"🔄 재연결 시도 {self.reconnect_attempts}/{self.max_reconnect_attempts} ({wait_time}초 후)")
        
        await asyncio.sleep(wait_time)
        
        try:
            await self._connect()
            await self._listen()
        
        except Exception as e:
            logger.error(f"❌ 재연결 실패: {e}")
            await self._reconnect()
    
    async def start_async(self):
        """비동기 시작"""
        self.is_running = True
        
        try:
            await self._connect()
            await self._listen()
        
        except Exception as e:
            logger.error(f"❌ WebSocket 시작 실패: {e}")
            self._trigger_callbacks('error', e)
    
    def start(self):
        """
        WebSocket 시작 (스레드)
        
        별도 스레드에서 실행하여 블로킹 방지
        """
        def run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.start_async())
        
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        
        logger.info("✅ WebSocket 스레드 시작")
    
    def stop(self):
        """WebSocket 중지"""
        self.is_running = False
        
        logger.info("⏹️  WebSocket 중지 요청")
        
        # WebSocket 종료
        if self.ws and self.loop:
            try:
                # 루프에 close 태스크 추가
                future = asyncio.run_coroutine_threadsafe(self.ws.close(), self.loop)
                future.result(timeout=5)  # 5초 대기
            except Exception as e:
                logger.debug(f"WebSocket 종료 중 에러 (무시 가능): {e}")
        
        # 스레드 대기
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        
        logger.info("⏹️  WebSocket 중지 완료")


# 테스트
if __name__ == "__main__":
    import time
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    print("=" * 70)
    print("WebSocket 테스트")
    print("=" * 70)
    print()
    
    # WebSocket 생성
    ws = BinanceWebSocket(
        symbols=['BTCUSDT', 'ETHUSDT'],
        timeframes=['1m', '15m']
    )
    
    # Ticker 콜백
    def on_ticker(data):
        print(f"📊 [{data['symbol']}] ${data['last']:,.2f} | Vol: {data['volume']:,.2f}")
    
    # Kline 콜백
    def on_kline(data):
        print(f"🕯️  [{data['symbol']}] {data['timeframe']} | O:{data['open']:.2f} H:{data['high']:.2f} L:{data['low']:.2f} C:{data['close']:.2f}")
    
    # Error 콜백
    def on_error(error):
        print(f"❌ 에러: {error}")
    
    # 콜백 등록
    ws.on_ticker(on_ticker)
    ws.on_kline(on_kline)
    ws.on_error(on_error)
    
    # 시작
    ws.start()
    
    print()
    print("WebSocket 실행 중... (Ctrl+C로 종료)")
    print()
    
    try:
        # 60초 동안 실행
        time.sleep(60)
    
    except KeyboardInterrupt:
        print("\n종료 중...")
    
    finally:
        ws.stop()
        print("✅ 종료 완료")
