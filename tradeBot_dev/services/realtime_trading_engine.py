"""
Realtime Trading Engine

단일 심볼 실시간 트레이딩 엔진
- 실시간 캔들 데이터 수신 (WebSocket)
- 선택한 전략 실행
- 신호 생성 시 즉시 진입
- 실시간 포지션 모니터링
"""

import asyncio
import math
import logging
import pandas as pd
from typing import Dict, List, Optional, Callable
from datetime import datetime, timedelta, timezone
import sys
import os

# 운영 시스템 경로 추가 (전략 매니저, 브로커 등 사용)
_current_dir = os.path.dirname(os.path.abspath(__file__))  # tradeBot_dev/services
_tradebot_dev_dir = os.path.dirname(_current_dir)  # tradeBot_dev
_pgdb_dir = os.path.dirname(_tradebot_dev_dir)  # PGdb
_tradebot_dir = os.path.join(_pgdb_dir, 'tradeBot')  # tradeBot (운영 시스템)

sys.path.insert(0, _tradebot_dir)
sys.path.insert(0, _pgdb_dir)
sys.path.insert(0, _current_dir)  # tradeBot_dev/services 경로 추가

from brokers.paper_broker import PaperBroker
from brokers.base import OrderIntent, OrderSide, OrderType
from core.websocket_feed import BinanceWebSocket
from adaptive_exit_manager import AdaptiveExitManager
from ema_cross_exit_manager import EMACrossExitManager

try:
    from indicators.technical import TechnicalIndicators
except ImportError:
    TechnicalIndicators = None

logger = logging.getLogger(__name__)


class RealtimeTradingEngine:
    """실시간 트레이딩 엔진"""

    def __init__(
        self,
        symbol: str,
        strategy_name: str,
        timeframe: str,
        broker: PaperBroker,
        strategy_manager,
        positions_repo=None,  # PositionsRepo (선택)
        on_log_update: Optional[Callable] = None,  # 로그 업데이트 콜백
        on_position_update: Optional[Callable] = None  # 포지션 업데이트 콜백
    ):
        """
        초기화

        Args:
            symbol: 거래 심볼 (예: 'BTCUSDT')
            strategy_name: 전략 이름 (예: 'ema_cross')
            timeframe: 타임프레임 (예: '5m')
            broker: PaperBroker 인스턴스
            strategy_manager: StrategyManager 인스턴스
            on_log_update: 로그 업데이트 콜백 (log_message: str)
            on_position_update: 포지션 업데이트 콜백
        """
        # 심볼 정규화: ETHUSDT.P -> ETHUSDT (TradingView 형식 제거)
        normalized_symbol = symbol.upper().replace('.P', '')
        self.symbol = normalized_symbol
        self.strategy_name = strategy_name
        self.timeframe = timeframe
        self.broker = broker
        self.strategy_manager = strategy_manager
        self.positions_repo = positions_repo  # PositionsRepo 저장
        self.on_log_update = on_log_update
        self.on_position_update = on_position_update
        self.on_trade_event = None  # 거래 이벤트 콜백 (진입/청산)

        # 전략 객체 가져오기
        self.strategy = strategy_manager.get_strategy(strategy_name)
        if not self.strategy:
            raise ValueError(f"전략을 찾을 수 없습니다: {strategy_name}")

        # 캔들 데이터 저장
        self.candles: List[Dict] = []  # 최근 캔들들 (최대 500개)
        self.current_candle: Optional[Dict] = None  # 현재 진행 중인 캔들
        self.last_closed_candle_time: Optional[int] = None  # 마지막 완성된 캔들 시간

        # WebSocket 연결
        self.ws: Optional[BinanceWebSocket] = None
        self._realtime_ws_task: Optional[asyncio.Task] = None
        self.is_running = False

        # 현재 가격
        self.current_price: Optional[float] = None

        # 로그 저장
        self.logs: List[Dict] = []  # 최근 로그 (최대 100개)

        # 거래 히스토리 저장
        self.trade_history: List[Dict] = []  # 진입/청산 히스토리

        # 청산 후 재진입 쿨다운 (1시간)
        self.last_exit_time: Optional[datetime] = None
        self.reentry_cooldown_minutes: int = 60

        # 동적 청산 매니저 (전략별로 선택)
        # EMA Cross 전략인 경우 전용 청산 매니저 사용
        if strategy_name.lower() == 'ema_cross':
            # EMA Cross 전용 청산 매니저 (TP1 부분 청산, TP2 트레일링 스톱, EMA50 터치)
            self.exit_manager = EMACrossExitManager(
                symbol=symbol,
                timeframe=timeframe,
                tp1_partial_exit_pct=50.0,  # TP1에서 50% 부분 청산
                use_trailing_stop=True,  # TP2 도달 후 트레일링 스톱
                trailing_stop_atr_multiplier=1.0,  # 트레일링 스톱 ATR 배수
                exit_on_ema50_touch=True  # EMA50 터치 시 청산
            )
            logger.info(f"✅ EMA Cross 전용 청산 매니저 사용")
        else:
            # 다른 전략은 기본 AdaptiveExitManager 사용
            self.exit_manager = AdaptiveExitManager(
                symbol=symbol,
                timeframe=timeframe,
                initial_capital=broker.initial_capital if hasattr(broker, 'initial_capital') else 10000.0
            )

        # 적응형 전략 매니저 (선택적, DB 엔진이 있을 때만 초기화)
        self.adaptive_manager = None
        self._adaptive_evaluation_task = None

        logger.info(f"🔄 RealtimeTradingEngine 초기화: {symbol} {strategy_name} {timeframe}")

    def _add_log(self, message: str, level: str = "info"):
        """로그 추가"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'level': level,
            'message': message
        }
        self.logs.append(log_entry)
        # 최대 100개까지만 유지
        if len(self.logs) > 100:
            self.logs = self.logs[-100:]

        # 콜백 호출
        if self.on_log_update:
            try:
                self.on_log_update(log_entry)
            except Exception as e:
                logger.error(f"로그 콜백 실패: {e}")

        # 로거에도 출력
        if level == "info":
            logger.info(message)
        elif level == "warning":
            logger.warning(message)
        elif level == "error":
            logger.error(message)

    async def start(self):
        """실시간 모니터링 시작"""
        if self.is_running:
            logger.warning("⚠️ 엔진이 이미 실행 중입니다")
            return

        self.is_running = True
        self._add_log(f"🚀 실시간 모니터링 시작: {self.symbol} {self.strategy_name} {self.timeframe}")

        try:
            # WebSocket 연결
            await self._connect_websocket()

            # 초기 캔들 데이터 로드 (DB에서)
            await self._load_initial_candles()

            # 캔들 모니터링 루프 시작
            asyncio.create_task(self._candle_monitoring_loop())

            # 가격 업데이트 루프 시작
            asyncio.create_task(self._price_update_loop())

            # 포지션 모니터링 루프 시작
            asyncio.create_task(self._position_monitoring_loop())

            # 적응형 전략 평가 루프 시작 (DB가 있을 때만)
            if self.adaptive_manager:
                self._adaptive_evaluation_task = asyncio.create_task(self._periodic_strategy_evaluation())

        except Exception as e:
            logger.error(f"❌ 시작 실패: {e}", exc_info=True)
            self.is_running = False
            raise

    async def stop(self):
        """실시간 모니터링 중지"""
        if not self.is_running:
            return

        self.is_running = False
        self._add_log("⏹️ 실시간 모니터링 중지")

        # WebSocket 연결 종료
        if self._realtime_ws_task:
            self._realtime_ws_task.cancel()
            self._realtime_ws_task = None

        if self.ws:
            try:
                self.ws.stop()
            except:
                pass
            self.ws = None

        logger.info("🛑 RealtimeTradingEngine 중지됨")

    async def _connect_websocket(self):
        """Binance WebSocket 연결"""
        try:
            self._add_log(f"📡 WebSocket 연결 중... ({self.symbol} {self.timeframe})")

            # 심볼 정규화: .P 제거 (BinanceWebSocket에 전달)
            normalized_symbol = self.symbol.upper().replace('.P', '')
            
            # Binance WebSocket 생성
            self.ws = BinanceWebSocket(
                symbols=[normalized_symbol],
                timeframes=[self.timeframe]
            )

            # 캔들 콜백 등록
            # Note: 현재 BinanceWebSocket은 is_closed=True일 때만 콜백 호출
            # 실시간 업데이트를 받으려면 websocket_feed.py 수정 필요 (통합 시)
            def on_kline_update(kline: Dict):
                """캔들 업데이트"""
                kline_symbol = kline.get('symbol', '').upper().replace('.P', '')
                if kline_symbol == normalized_symbol and kline.get('timeframe') == self.timeframe:
                    # 완성된 캔들 처리
                    asyncio.create_task(self._handle_closed_candle(kline))

            self.ws.on_kline(on_kline_update)
            
            # 실시간 캔들 업데이트를 위해 직접 WebSocket 연결 (개발용)
            # 운영 시스템 통합 시 websocket_feed.py 수정 필요
            self._realtime_ws_task = asyncio.create_task(self._connect_realtime_kline_ws())

            # 실시간 가격 콜백 등록
            def on_ticker(ticker: Dict):
                """실시간 가격 업데이트"""
                ticker_symbol = ticker.get('symbol', '').upper().replace('.P', '')
                if ticker_symbol == normalized_symbol:
                    self.current_price = ticker.get('last')
                    # 포지션이 있으면 PnL 업데이트
                    if self.broker.get_open_positions():
                        asyncio.create_task(self._update_positions_pnl())

            self.ws.on_ticker(on_ticker)

            # WebSocket 시작
            self.ws.start()

            self._add_log("✅ WebSocket 연결 완료")

        except Exception as e:
            logger.error(f"❌ WebSocket 연결 실패: {e}", exc_info=True)
            self._add_log(f"❌ WebSocket 연결 실패: {e}", "error")
            raise

    async def _load_initial_candles(self):
        """초기 캔들 데이터 로드 (바이낸스 API에서 최신 데이터 직접 가져오기)"""
        try:
            self._add_log("📊 바이낸스 API에서 최신 캔들 데이터 로드 중...")

            # 바이낸스 API에서 직접 최신 캔들 가져오기
            import ccxt
            
            # 바이낸스 선물 연결
            binance = ccxt.binance({
                'enableRateLimit': True,
                'options': {'defaultType': 'future'}  # USDT-M 선물
            })

            # 심볼 정규화: .P 제거 및 대문자 변환
            normalized_symbol = self.symbol.upper().replace('.P', '')
            
            # 심볼 형식 변환: ETHUSDT -> ETH/USDT:USDT (Binance 선물)
            ccxt_symbol = normalized_symbol
            if '/' not in normalized_symbol:
                if normalized_symbol.endswith('USDT'):
                    base = normalized_symbol[:-4]
                    ccxt_symbol = f"{base}/USDT:USDT"

            # 최신 100개 캔들 가져오기 (EMA 50 계산에 충분)
            self._add_log(f"📡 바이낸스 선물 API 호출: {ccxt_symbol} {self.timeframe} (원본 심볼: {self.symbol})")
            ohlcv = binance.fetch_ohlcv(ccxt_symbol, timeframe=self.timeframe, limit=100)
            
            if not ohlcv or len(ohlcv) == 0:
                self._add_log("⚠️ 바이낸스에서 캔들 데이터를 가져올 수 없습니다. DB에서 로드 시도...", "warning")
                await self._load_initial_candles_from_db()
                return

            # 동적 청산 매니저의 캔들 데이터 초기화 (최신 데이터로 시작)
            if hasattr(self.exit_manager, 'reset_candles'):
                self.exit_manager.reset_candles()
            
            # OHLCV 데이터를 캔들 딕셔너리로 변환 (시간 순서대로 정렬)
            candles_list = []
            for candle_data in ohlcv:
                # candle_data: [timestamp_ms, open, high, low, close, volume]
                timestamp_ms = candle_data[0]
                open_time = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
                
                candle = {
                    'open_time': open_time,
                    'open': float(candle_data[1]),
                    'high': float(candle_data[2]),
                    'low': float(candle_data[3]),
                    'close': float(candle_data[4]),
                    'volume': float(candle_data[5]),
                    'is_closed': True,
                    'timeframe': self.timeframe
                }
                candles_list.append(candle)
            
            # 시간 순서대로 정렬 (오래된 것부터 최신 순서)
            candles_list.sort(key=lambda x: x['open_time'])
            
            # 정렬된 캔들을 추가
            for candle in candles_list:
                self.candles.append(candle)
                # 동적 청산 매니저에 초기 캔들 추가
                self.exit_manager.update_realtime_candle(candle)
            
            # 디버깅: 첫 캔들과 마지막 캔들 확인
            if len(candles_list) > 0:
                first_candle = candles_list[0]
                last_candle = candles_list[-1]
                self._add_log(f"📊 캔들 범위: {first_candle['open_time'].strftime('%H:%M:%S')} (${first_candle['close']:.2f}) ~ {last_candle['open_time'].strftime('%H:%M:%S')} (${last_candle['close']:.2f})")

            self._add_log(f"✅ 바이낸스에서 최신 캔들 {len(self.candles)}개 로드 완료")
            
            # EMA 계산 확인
            ema_50 = self.exit_manager.indicators_cache.get('ema_50')
            if ema_50:
                current_price = self.exit_manager.indicators_cache.get('current_price', self.current_price)
                if current_price:
                    self._add_log(f"📊 초기 EMA 50: {ema_50:.2f}, 현재가: {current_price:.2f}, 차이: {current_price - ema_50:.2f}")

        except Exception as e:
            logger.error(f"❌ 바이낸스 API에서 캔들 로드 실패: {e}", exc_info=True)
            self._add_log(f"❌ 바이낸스 API 로드 실패: {e}, DB에서 로드 시도...", "error")
            # 실패 시 DB에서 로드 시도
            await self._load_initial_candles_from_db()
    
    async def _load_initial_candles_from_db(self):
        """DB에서 초기 캔들 데이터 로드 (폴백)"""
        try:
            self._add_log("📊 DB에서 초기 캔들 데이터 로드 중...")

            from sqlalchemy import create_engine
            from core.config_loader import get_config

            config = get_config()
            db_url = f"postgresql://{config.get('db.user')}:{config.get('db.password')}@{config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}"
            engine = create_engine(db_url)

            # 최신 100개 캔지만 가져오기
            query = """
                SELECT open_time, open, high, low, close, volume
                FROM candles
                WHERE symbol = %(symbol)s AND tf = %(tf)s
                ORDER BY open_time DESC
                LIMIT 100
            """
            df = pd.read_sql(query, engine, params={'symbol': self.symbol, 'tf': self.timeframe})
            engine.dispose()

            if len(df) > 0:
                # 동적 청산 매니저의 캔들 데이터 초기화 (최신 데이터로 시작)
                if hasattr(self.exit_manager, 'reset_candles'):
                    self.exit_manager.reset_candles()
                
                # 시간 순서대로 정렬 (오래된 것부터 최신 순서)
                df = df.sort_values('open_time').reset_index(drop=True)
                
                # DataFrame을 딕셔너리 리스트로 변환
                for _, row in df.iterrows():
                    # datetime을 naive로 변환 (UTC로 가정)
                    open_time = row['open_time']
                    if pd.notna(open_time):
                        if isinstance(open_time, pd.Timestamp):
                            # timezone-aware면 UTC로 변환 후 naive로
                            if open_time.tz is not None:
                                open_time = open_time.tz_convert('UTC').tz_localize(None)
                            else:
                                open_time = open_time
                        elif isinstance(open_time, datetime):
                            # timezone-aware면 UTC로 변환 후 naive로
                            if open_time.tzinfo is not None:
                                open_time = open_time.astimezone(timezone.utc).replace(tzinfo=None)
                            else:
                                open_time = open_time
                    
                    candle = {
                        'open_time': open_time,
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close']),
                        'volume': float(row['volume']),
                        'is_closed': True,
                        'timeframe': self.timeframe  # 타임프레임 정보 추가
                    }
                    candle = {
                        'open_time': open_time,
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close']),
                        'volume': float(row['volume']),
                        'is_closed': True,
                        'timeframe': self.timeframe
                    }
                    self.candles.append(candle)
                    # 동적 청산 매니저에 초기 캔들 추가
                    self.exit_manager.update_realtime_candle(candle)

                self._add_log(f"✅ DB에서 초기 캔들 {len(self.candles)}개 로드 완료")
                
                # EMA 계산 확인
                ema_50 = self.exit_manager.indicators_cache.get('ema_50')
                if ema_50:
                    current_price = self.exit_manager.indicators_cache.get('current_price', self.current_price)
                    if current_price:
                        self._add_log(f"📊 초기 EMA 50: {ema_50:.2f}, 현재가: {current_price:.2f}, 차이: {current_price - ema_50:.2f}")
            else:
                self._add_log("⚠️ DB에 초기 캔들 데이터가 없습니다", "warning")

        except Exception as e:
            logger.error(f"❌ DB에서 초기 캔들 로드 실패: {e}", exc_info=True)
            self._add_log(f"❌ DB에서 초기 캔들 로드 실패: {e}", "error")

    async def _handle_closed_candle(self, kline: Dict):
        """완성된 캔들 처리"""
        try:
            # open_time이 밀리초인지 확인
            open_time_ms = kline.get('open_time', 0)
            if open_time_ms > 1e12:  # 밀리초 (13자리)
                open_time = datetime.fromtimestamp(open_time_ms / 1000)
            else:  # 초 (10자리)
                open_time = datetime.fromtimestamp(open_time_ms)

            # 중복 처리 방지
            if self.last_closed_candle_time and open_time_ms <= self.last_closed_candle_time:
                return

            self.last_closed_candle_time = open_time_ms

            # datetime을 naive로 변환 (UTC로 가정)
            if isinstance(open_time, datetime):
                if open_time.tzinfo is not None:
                    open_time = open_time.astimezone(timezone.utc).replace(tzinfo=None)
            
            # 캔들 추가
            candle = {
                'open_time': open_time,
                'open': float(kline.get('open', 0)),
                'high': float(kline.get('high', 0)),
                'low': float(kline.get('low', 0)),
                'close': float(kline.get('close', 0)),
                'volume': float(kline.get('volume', 0)),
                'is_closed': True,
                'timeframe': self.timeframe  # 타임프레임 정보 추가
            }
            self.candles.append(candle)

            # 최대 500개까지만 유지
            if len(self.candles) > 500:
                self.candles = self.candles[-500:]

            # 동적 청산 매니저에 캔들 업데이트
            if hasattr(self.exit_manager, 'update_realtime_candle'):
                self.exit_manager.update_realtime_candle(candle)

            self._add_log(f"📊 캔들 완성: {candle['open_time'].strftime('%H:%M:%S')} Close=${candle['close']:.2f}")

            # DB에 캔들 저장 (비동기로 실행하여 전략 실행을 블로킹하지 않음)
            logger.info(f"💾 캔들 DB 저장 시작: {self.symbol} {self.timeframe} {candle['open_time'].strftime('%Y-%m-%d %H:%M:%S')}, positions_repo={self.positions_repo is not None}")
            asyncio.create_task(self._save_candle_to_db_async(candle))

            # 전략 실행
            await self._run_strategy()

        except Exception as e:
            logger.error(f"❌ 캔들 처리 실패: {e}", exc_info=True)
            self._add_log(f"❌ 캔들 처리 실패: {e}", "error")
    
    def _save_candle_to_db_sync(self, candle: Dict):
        """캔들을 DB에 저장 (동기 함수, UPSERT)"""
        if not self.positions_repo:
            logger.warning(f"⚠️ positions_repo가 없어 캔들 저장 불가: {self.symbol} {self.timeframe}")
            return
        
        if not hasattr(self.positions_repo, 'engine'):
            logger.warning(f"⚠️ positions_repo.engine이 없어 캔들 저장 불가: {self.symbol} {self.timeframe}")
            return
        
        try:
            from sqlalchemy import text
            
            db_engine = self.positions_repo.engine
            
            # UPSERT 쿼리 (ON CONFLICT DO UPDATE)
            query = text("""
                INSERT INTO candles (exchange, market, symbol, tf, open_time, open, high, low, close, volume)
                VALUES ('binance', 'futures', :symbol, :tf, :open_time, :open, :high, :low, :close, :volume)
                ON CONFLICT (exchange, symbol, market, tf, open_time) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume
            """)
            
            logger.info(f"💾 캔들 DB 저장 시도: {self.symbol} {self.timeframe} {candle['open_time'].strftime('%Y-%m-%d %H:%M:%S')} (O={candle['open']:.2f}, H={candle['high']:.2f}, L={candle['low']:.2f}, C={candle['close']:.2f})")
            
            with db_engine.begin() as conn:
                result = conn.execute(query, {
                    'symbol': self.symbol,
                    'tf': self.timeframe,
                    'open_time': candle['open_time'],
                    'open': candle['open'],
                    'high': candle['high'],
                    'low': candle['low'],
                    'close': candle['close'],
                    'volume': candle['volume']
                })
            
            logger.info(f"💾 캔들 DB 저장 완료: {self.symbol} {self.timeframe} {candle['open_time'].strftime('%Y-%m-%d %H:%M:%S')}")
            
        except Exception as e:
            logger.error(f"❌ 캔들 DB 저장 실패: {self.symbol} {self.timeframe} {candle.get('open_time', 'N/A')} - {e}", exc_info=True)
            # DB 저장 실패는 치명적이지 않으므로 에러 로그만 출력
    
    async def _save_candle_to_db_async(self, candle: Dict):
        """캔들을 DB에 저장 (비동기 래퍼)"""
        # 동기 DB 작업을 별도 스레드에서 실행하여 이벤트 루프를 블로킹하지 않음
        try:
            await asyncio.to_thread(self._save_candle_to_db_sync, candle)
        except Exception as e:
            logger.warning(f"⚠️ 캔들 DB 저장 비동기 실행 실패: {e}")

    async def _run_strategy(self):
        """전략 실행"""
        try:
            # EMA Cross 등 전략은 100봉 이상 필요
            min_candles = 100
            if len(self.candles) < min_candles:
                self._add_log(f"⏳ 캔들 데이터 수집 중... ({len(self.candles)}/{min_candles})", "info")
                return

            self._add_log(f"🔍 {self.timeframe} {self.strategy_name} 전략 스캔 중...")

            # DataFrame 생성
            df = pd.DataFrame(self.candles)
            if len(df) == 0:
                return
            
            # open_time을 naive datetime으로 통일 (UTC로 가정)
            if 'open_time' in df.columns:
                # 모든 datetime을 naive로 변환
                def normalize_datetime(dt):
                    if pd.isna(dt):
                        return dt
                    if isinstance(dt, pd.Timestamp):
                        # timezone-aware면 UTC로 변환 후 naive로
                        if dt.tz is not None:
                            return dt.tz_convert('UTC').tz_localize(None)
                        return dt
                    elif isinstance(dt, datetime):
                        # timezone-aware면 UTC로 변환 후 naive로
                        if dt.tzinfo is not None:
                            return dt.astimezone(timezone.utc).replace(tzinfo=None)
                        return dt
                    else:
                        # 문자열이면 파싱
                        parsed = pd.to_datetime(dt)
                        if isinstance(parsed, pd.Timestamp) and parsed.tz is not None:
                            return parsed.tz_convert('UTC').tz_localize(None)
                        return parsed
                
                df['open_time'] = df['open_time'].apply(normalize_datetime)
                # Series를 datetime으로 변환
                df['open_time'] = pd.to_datetime(df['open_time'], errors='coerce')
            
            df = df.sort_values('open_time').reset_index(drop=True)
            
            # 컬럼명 확인 및 정리 (전략이 기대하는 형식)
            required_cols = ['open', 'high', 'low', 'close', 'volume']
            for col in required_cols:
                if col not in df.columns:
                    logger.warning(f"⚠️ 필수 컬럼 없음: {col}")
                    return

            # 전략 실행
            signals = self.strategy.analyze(
                symbol=self.symbol,
                timeframe=self.timeframe,
                df=df
            )

            if signals:
                # 청산 후 1시간 이내 재진입 금지
                if self.last_exit_time:
                    elapsed = (datetime.now() - self.last_exit_time).total_seconds() / 60
                    if elapsed < self.reentry_cooldown_minutes:
                        self._add_log(f"⏳ 청산 후 재진입 쿨다운 중 ({elapsed:.0f}/{self.reentry_cooldown_minutes}분 경과)", "info")
                        return

                # RSI 진입 필터: 과매도(RSI<25) 또는 과매수(RSI>75) 구간에서는 진입 금지
                rsi = None
                if hasattr(self.exit_manager, 'indicators_cache'):
                    rsi = self.exit_manager.indicators_cache.get('rsi')
                if rsi is None and len(df) >= 15:  # RSI(14) 계산에 최소 15봉 필요
                    rsi = self._compute_rsi(df, 14)
                if rsi is not None:
                    if rsi < 25:
                        self._add_log(f"⏳ RSI 진입 금지: 과매도 구간 (RSI={rsi:.1f} < 25)", "info")
                        return
                    if rsi > 75:
                        self._add_log(f"⏳ RSI 진입 금지: 과매수 구간 (RSI={rsi:.1f} > 75)", "info")
                        return

                signal = signals[0]  # 첫 번째 신호 사용
                self._add_log(f"🎯 신호 포착! {signal.signal_type.upper()} @ ${signal.entry_price:.2f}", "info")
                self._add_log(f"   SL: ${signal.stop_loss:.2f}, TP: ${signal.take_profit_1:.2f}", "info")

                # 즉시 진입
                await self._enter_position(signal)
            else:
                self._add_log("⏭️ 신호 없음", "info")

        except Exception as e:
            logger.error(f"❌ 전략 실행 실패: {e}", exc_info=True)
            self._add_log(f"❌ 전략 실행 실패: {e}", "error")

    def _compute_rsi(self, df: pd.DataFrame, period: int = 14) -> Optional[float]:
        """RSI(period) 계산. 최신 값 1개 반환. 진입 필터용."""
        if len(df) < period + 1:
            return None
        closes = df['close'].dropna()
        if len(closes) < period + 1:
            return None
        deltas = closes.diff()
        gains = deltas.where(deltas > 0, 0.0)
        losses = (-deltas).where(deltas < 0, 0.0)
        avg_gain = gains.ewm(alpha=1.0 / period, min_periods=period).mean().iloc[-1]
        avg_loss = losses.ewm(alpha=1.0 / period, min_periods=period).mean().iloc[-1]
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return round(float(rsi), 2)

    async def _enter_position(self, signal):
        """포지션 진입"""
        try:
            # 이미 오픈 포지션이 있으면 스킵
            open_positions = self.broker.get_open_positions()
            if open_positions:
                self._add_log(f"⏭️ 이미 오픈 포지션 존재: {len(open_positions)}개", "warning")
                return

            self._add_log("🚀 진입 중...", "info")

            # 수량 계산 (자본의 2% 리스크)
            capital = self.broker.capital
            risk_percent = 2.0
            risk_amount = capital * (risk_percent / 100)
            
            # SL 거리 계산
            stop_distance = abs(signal.entry_price - signal.stop_loss)
            if stop_distance > 0:
                quantity = risk_amount / stop_distance
            else:
                quantity = 0

            if quantity <= 0:
                self._add_log(f"❌ 수량 계산 실패: quantity={quantity}", "error")
                return

            # 주문 의도 생성
            intent_id = f"realtime_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            intent = OrderIntent(
                intent_id=intent_id,
                signal_id=signal.generate_signal_id(),
                symbol=signal.symbol,
                side=OrderSide.BUY if signal.signal_type == 'buy' else OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=quantity,
                price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit_1,
                metadata={
                    'strategy': self.strategy_name,
                    'timeframe': self.timeframe,
                    'signal': signal.to_dict()
                }
            )

            # 주문 제출
            order = self.broker.submit_order(intent)

            # 주문 체결 (현재 가격 사용, 없으면 신호의 진입가 사용)
            fill_price = self.current_price or signal.entry_price
            if fill_price:
                fill = self.broker.fill_order(order.order_id, fill_price)
                if fill:
                    self._add_log("✅ 진입 완료!", "info")
                    position = self.broker.get_open_positions()[0] if self.broker.get_open_positions() else None
                    if position:
                        self._add_log(f"   포지션 ID: {position.position_id}", "info")
                        self._add_log(f"   진입가: ${position.entry_price:.8f}", "info")
                        self._add_log(f"   수량: {position.quantity:.4f}", "info")
                        if position.stop_loss:
                            sl_percent = ((position.stop_loss - position.entry_price) / position.entry_price * 100) if position.side == OrderSide.BUY else ((position.entry_price - position.stop_loss) / position.entry_price * 100)
                            self._add_log(f"   SL: ${position.stop_loss:.8f} ({sl_percent:+.2f}%)", "info")
                        if position.take_profit:
                            tp_percent = ((position.take_profit - position.entry_price) / position.entry_price * 100) if position.side == OrderSide.BUY else ((position.entry_price - position.take_profit) / position.entry_price * 100)
                            self._add_log(f"   TP: ${position.take_profit:.8f} ({tp_percent:+.2f}%)", "info")

                        # 진입 히스토리에 추가
                        await self._add_entry_to_history(position, 'signal')

                    # 포지션 업데이트 콜백
                    if self.on_position_update:
                        try:
                            await self.on_position_update()
                        except Exception as e:
                            logger.warning(f"⚠️ 포지션 업데이트 콜백 실패: {e}")
                else:
                    self._add_log(f"❌ 주문 체결 실패", "error")
            else:
                self._add_log(f"❌ 진입가 정보 없음 (current_price, entry_price 모두 없음)", "error")

        except Exception as e:
            logger.error(f"❌ 진입 실패: {e}", exc_info=True)
            self._add_log(f"❌ 진입 실패: {e}", "error")

    async def manual_entry(
        self,
        side: str,
        quantity: Optional[float] = None,
        risk_percent: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ):
        """
        수동 진입
        
        Args:
            side: 'BUY' 또는 'SELL'
            quantity: 수량 (직접 지정)
            risk_percent: 리스크 비율 (quantity가 None일 때 사용)
            stop_loss: 손절가 (선택)
            take_profit: 익절가 (선택)
        """
        try:
            # 이미 오픈 포지션이 있으면 스킵
            open_positions = self.broker.get_open_positions()
            if open_positions:
                self._add_log(f"⏭️ 이미 오픈 포지션 존재: {len(open_positions)}개", "warning")
                return {
                    "success": False,
                    "message": "이미 오픈 포지션이 있습니다"
                }

            if not self.current_price:
                self._add_log("⚠️ 현재 가격 정보가 없습니다", "warning")
                return {
                    "success": False,
                    "message": "현재 가격 정보가 없습니다"
                }

            self._add_log(f"🚀 수동 진입 중... ({side})", "info")

            # 사이드 변환
            order_side = OrderSide.BUY if side.upper() == 'BUY' else OrderSide.SELL
            if side.upper() not in ['BUY', 'SELL']:
                return {
                    "success": False,
                    "message": "side는 'BUY' 또는 'SELL'이어야 합니다"
                }

            # 수량 계산
            if quantity is None or quantity <= 0:
                if risk_percent is None or risk_percent <= 0:
                    risk_percent = 2.0  # 기본 2%
                
                capital = self.broker.capital
                risk_amount = capital * (risk_percent / 100)
                
                # SL 거리 계산
                if stop_loss:
                    stop_distance = abs(self.current_price - stop_loss)
                    if stop_distance > 0:
                        quantity = risk_amount / stop_distance
                    else:
                        quantity = risk_amount / (self.current_price * 0.01)  # 기본 1% 거리
                else:
                    # SL이 없으면 기본 1% 거리로 계산
                    quantity = risk_amount / (self.current_price * 0.01)

            if quantity <= 0:
                self._add_log(f"❌ 수량 계산 실패: quantity={quantity}", "error")
                return {
                    "success": False,
                    "message": f"수량 계산 실패: {quantity}"
                }

            # 주문 의도 생성
            intent_id = f"manual_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            intent = OrderIntent(
                intent_id=intent_id,
                signal_id=f"manual_{intent_id}",
                symbol=self.symbol,
                side=order_side,
                order_type=OrderType.MARKET,
                quantity=quantity,
                price=self.current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                metadata={
                    'strategy': 'manual',
                    'timeframe': self.timeframe,
                    'entry_type': 'manual'
                }
            )

            # 주문 제출
            order = self.broker.submit_order(intent)

            # 주문 체결
            fill = self.broker.fill_order(order.order_id, self.current_price)
            if fill:
                self._add_log("✅ 수동 진입 완료!", "info")
                position = self.broker.get_open_positions()[0] if self.broker.get_open_positions() else None
                if position:
                    self._add_log(f"   포지션 ID: {position.position_id}", "info")
                    self._add_log(f"   진입가: ${position.entry_price:.8f}", "info")
                    self._add_log(f"   수량: {position.quantity:.4f}", "info")
                    if position.stop_loss:
                        sl_percent = ((position.stop_loss - position.entry_price) / position.entry_price * 100) if position.side == OrderSide.BUY else ((position.entry_price - position.stop_loss) / position.entry_price * 100)
                        self._add_log(f"   SL: ${position.stop_loss:.8f} ({sl_percent:+.2f}%)", "info")
                    if position.take_profit:
                        tp_percent = ((position.take_profit - position.entry_price) / position.entry_price * 100) if position.side == OrderSide.BUY else ((position.entry_price - position.take_profit) / position.entry_price * 100)
                        self._add_log(f"   TP: ${position.take_profit:.8f} ({tp_percent:+.2f}%)", "info")

                    # 진입 히스토리에 추가
                    if position:
                        await self._add_entry_to_history(position, 'manual')

                # 포지션 업데이트 콜백
                if self.on_position_update:
                    try:
                        await self.on_position_update()
                    except Exception as e:
                        logger.warning(f"⚠️ 포지션 업데이트 콜백 실패: {e}")

                return {
                    "success": True,
                    "message": "수동 진입 완료",
                    "position_id": position.position_id if position else None
                }
            else:
                self._add_log(f"❌ 주문 체결 실패", "error")
                return {
                    "success": False,
                    "message": "주문 체결 실패"
                }

        except Exception as e:
            logger.error(f"❌ 수동 진입 실패: {e}", exc_info=True)
            self._add_log(f"❌ 수동 진입 실패: {e}", "error")
            return {
                "success": False,
                "message": f"수동 진입 실패: {str(e)}"
            }

    async def _connect_realtime_kline_ws(self):
        """실시간 캔들 업데이트를 위한 직접 WebSocket 연결"""
        import websockets
        import json

        while self.is_running:
            try:
                # 심볼 정규화: .P 제거 및 소문자 변환 (Binance 선물 WebSocket 형식)
                normalized_symbol = self.symbol.upper().replace('.P', '')
                symbol_lower = normalized_symbol.lower()
                stream = f"{symbol_lower}@kline_{self.timeframe}"
                url = f"wss://fstream.binance.com/ws/{stream}"

                self._add_log(f"📡 실시간 캔들 WebSocket 연결: {stream} (원본 심볼: {self.symbol})")

                async with websockets.connect(url) as ws:
                    while self.is_running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                            data = json.loads(message)
                            kline_data = data.get('k', {})

                            if kline_data:
                                # WebSocket에서 받은 심볼 정규화
                                ws_symbol = kline_data.get('s', '').upper().replace('.P', '')
                                normalized_self_symbol = self.symbol.upper().replace('.P', '')
                                
                                kline = {
                                    'symbol': ws_symbol,
                                    'timeframe': kline_data.get('i', ''),
                                    'open_time': kline_data.get('t', 0),
                                    'open': float(kline_data.get('o', 0)),
                                    'high': float(kline_data.get('h', 0)),
                                    'low': float(kline_data.get('l', 0)),
                                    'close': float(kline_data.get('c', 0)),
                                    'volume': float(kline_data.get('v', 0)),
                                    'is_closed': kline_data.get('x', False)
                                }

                                if kline['symbol'] == normalized_self_symbol and kline['timeframe'] == self.timeframe:
                                    if kline['is_closed']:
                                        # 완성된 캔들 → 전략 실행 및 진입 검토
                                        # (BinanceWebSocket은 별도 스레드에서 동작하므로 여기서 처리)
                                        await self._handle_closed_candle(kline)
                                    else:
                                        # 실시간 캔들 업데이트
                                        await self._handle_realtime_candle(kline)

                        except asyncio.TimeoutError:
                            continue
                        except Exception as e:
                            logger.error(f"❌ 실시간 캔들 WebSocket 오류: {e}")
                            await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"❌ 실시간 캔들 WebSocket 연결 실패: {e}", exc_info=True)
                if self.is_running:
                    await asyncio.sleep(5)  # 재연결 대기

    async def _handle_realtime_candle(self, kline: Dict):
        """실시간 캔들 업데이트 (완성 전)"""
        try:
            # open_time이 밀리초인지 확인
            open_time_ms = kline.get('open_time', 0)
            if open_time_ms > 1e12:  # 밀리초 (13자리)
                open_time = datetime.fromtimestamp(open_time_ms / 1000)
            else:  # 초 (10자리)
                open_time = datetime.fromtimestamp(open_time_ms)

            # datetime을 naive로 변환 (UTC로 가정)
            if isinstance(open_time, datetime):
                if open_time.tzinfo is not None:
                    open_time = open_time.astimezone(timezone.utc).replace(tzinfo=None)
            
            # 현재 캔들 업데이트
            self.current_candle = {
                'open_time': open_time,
                'open': float(kline.get('open', 0)),
                'high': float(kline.get('high', 0)),
                'low': float(kline.get('low', 0)),
                'close': float(kline.get('close', 0)),
                'volume': float(kline.get('volume', 0)),
                'is_closed': False,
                'timeframe': self.timeframe  # 타임프레임 정보 추가
            }

            # 현재 가격 업데이트
            self.current_price = float(kline.get('close', 0))

            # 동적 청산 매니저에 실시간 캔들 업데이트
            if hasattr(self.exit_manager, 'update_realtime_candle'):
                self.exit_manager.update_realtime_candle(self.current_candle)

            # 캔들 완성까지 남은 시간 계산
            timeframe_minutes = {
                '1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30,
                '1h': 60, '2h': 120, '4h': 240, '6h': 360, '8h': 480, '12h': 720, '1d': 1440
            }
            tf_minutes = timeframe_minutes.get(self.timeframe, 15)

            # 현재 시간과 캔들 시작 시간의 차이
            now = datetime.now()
            candle_start = self.current_candle['open_time']
            elapsed = (now - candle_start).total_seconds() / 60  # 분 단위
            remaining = tf_minutes - elapsed

            if remaining > 0:
                minutes = int(remaining)
                seconds = int((remaining - minutes) * 60)
                # 로그는 너무 자주 출력하지 않도록 (10초마다)
                if not hasattr(self, '_last_realtime_log_time') or (now - self._last_realtime_log_time).total_seconds() >= 10:
                    self._add_log(f"⏳ 캔들 close 대기 중... {minutes}:{seconds:02d}", "info")
                    self._last_realtime_log_time = now

        except Exception as e:
            logger.error(f"❌ 실시간 캔들 처리 실패: {e}", exc_info=True)

    async def _candle_monitoring_loop(self):
        """캔들 모니터링 루프 (현재 캔들 진행 상황 표시)"""
        while self.is_running:
            try:
                await asyncio.sleep(1)  # 1초마다 체크

                # 현재 캔들 진행 상황은 WebSocket에서 실시간으로 업데이트됨

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ 캔들 모니터링 루프 오류: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _price_update_loop(self):
        """가격 업데이트 루프"""
        while self.is_running:
            try:
                await asyncio.sleep(1)  # 1초마다 업데이트

                # 포지션이 있으면 PnL 업데이트
                if self.broker.get_open_positions() and self.current_price:
                    await self._update_positions_pnl()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ 가격 업데이트 루프 오류: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _position_monitoring_loop(self):
        """포지션 모니터링 루프 (SL/TP 체크 + 동적 청산)"""
        while self.is_running:
            try:
                await asyncio.sleep(1)  # 1초마다 체크

                if not self.current_price:
                    continue

                # 오픈 포지션 확인
                open_positions = self.broker.get_open_positions()
                if not open_positions:
                    continue

                position = open_positions[0]  # 단일 심볼이므로 첫 번째 포지션

                # 동적 청산 조건 체크
                # EMA Cross 전용 매니저는 부분 청산 지원
                if isinstance(self.exit_manager, EMACrossExitManager):
                    should_exit, exit_reason, exit_quantity = self.exit_manager.should_exit(
                        position,
                        self.current_price,
                        market_data={'atr': self.exit_manager.indicators_cache.get('atr')}
                    )
                else:
                    should_exit, exit_reason = self.exit_manager.should_exit(
                        position,
                        self.current_price
                    )
                    exit_quantity = None
                
                # 디버깅: 청산 체크 결과 로그 (1초마다 너무 많으니 5초마다)
                if hasattr(self, '_last_exit_check_log'):
                    if (datetime.now() - self._last_exit_check_log).total_seconds() >= 5:
                        position_side = "LONG" if position.side == OrderSide.BUY else "SHORT"
                        ema_50 = self.exit_manager.indicators_cache.get('ema_50')
                        if ema_50:
                            logger.debug(f"🔍 청산 체크: {position_side}, 가격={self.current_price:.8f}, EMA50={ema_50:.8f}, 청산여부={should_exit}")
                        self._last_exit_check_log = datetime.now()
                else:
                    self._last_exit_check_log = datetime.now()

                if should_exit:
                    self._add_log(f"🔄 동적 청산 조건 충족: {exit_reason}", "info")
                    
                    # 부분 청산 처리
                    if exit_quantity and exit_quantity < position.quantity:
                        # 부분 청산 (EMA Cross 전략의 TP1 부분 청산)
                        try:
                            # 부분 청산은 PaperBroker에서 직접 지원하지 않으므로
                            # 전체 청산 후 남은 수량으로 새 포지션 생성 (간단한 구현)
                            # 실제로는 broker에 부분 청산 기능이 필요함
                            self._add_log(f"📊 부분 청산: {exit_quantity:.4f}/{position.quantity:.4f} ({exit_quantity/position.quantity*100:.1f}%)", "info")
                            
                            # 현재는 전체 청산으로 처리 (부분 청산은 향후 구현)
                            closed = self.broker.close_position(position.position_id, self.current_price)
                            if closed:
                                self._add_log(f"✅ 부분 청산 완료: {exit_reason}", "info")
                                
                                closed_position = self.broker.positions.get(position.position_id)
                                if closed_position:
                                    await self._add_exit_to_history(closed_position, exit_reason)
                        except Exception as e:
                            logger.error(f"❌ 부분 청산 실행 실패: {e}", exc_info=True)
                    else:
                        # 전체 청산
                        try:
                            closed = self.broker.close_position(position.position_id, self.current_price)
                            if closed:
                                self._add_log(f"✅ 동적 청산 완료: {exit_reason}", "info")
                                
                                # 청산된 포지션 가져오기 (broker의 positions에서)
                                closed_position = self.broker.positions.get(position.position_id)
                                if closed_position:
                                    await self._add_exit_to_history(closed_position, exit_reason)
                                
                                # 포지션 업데이트 콜백
                                if self.on_position_update:
                                    try:
                                        await self.on_position_update()
                                    except Exception as e:
                                        logger.warning(f"⚠️ 포지션 업데이트 콜백 실패: {e}")
                            else:
                                self._add_log(f"❌ 동적 청산 실패", "error")
                        except Exception as e:
                            logger.error(f"❌ 동적 청산 실행 실패: {e}", exc_info=True)
                        self._add_log(f"❌ 동적 청산 실행 실패: {e}", "error")
                else:
                    # 기본 SL/TP 체크 (기존 로직)
                    updated = self.broker.update_positions({self.symbol: self.current_price})

                    # 포지션이 닫혔으면 청산 처리
                    if updated:
                        # 청산된 포지션 찾기 (CLOSED 상태인 포지션)
                        for pos_id, pos in self.broker.positions.items():
                            if pos.status.value == 'closed' and pos_id == position.position_id:
                                await self._add_exit_to_history(pos, "sl_tp")
                                break
                        
                        if self.on_position_update:
                            try:
                                await self.on_position_update()
                            except Exception as e:
                                logger.warning(f"⚠️ 포지션 업데이트 콜백 실패: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ 포지션 모니터링 루프 오류: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _update_positions_pnl(self):
        """포지션 PnL 업데이트"""
        try:
            if self.on_position_update:
                await self.on_position_update()
        except Exception as e:
            logger.warning(f"⚠️ PnL 업데이트 콜백 실패: {e}")

    def get_strategy_entry_rules(self) -> Dict:
        """
        현재 전략의 진입 규칙 정보 반환
        
        Returns:
            전략 진입 규칙 딕셔너리
        """
        if not self.strategy:
            return {
                'strategy_name': self.strategy_name,
                'error': '전략 객체가 없습니다'
            }
        
        # 전략별 진입 규칙 요약
        strategy_name = self.strategy_name.lower()
        
        rules = {
            'strategy_name': self.strategy_name,
            'timeframe': self.timeframe,
            'symbol': self.symbol,
            'entry_rules': {}
        }
        
        if strategy_name == 'ema_cross':
            rules['entry_rules'] = {
                'buy': {
                    'condition': '진입은 골든/데드 크로스가 발생한 봉에서만 평가됩니다.',
                    'required': [
                        '골든 크로스: EMA20이 EMA50을 상향 돌파 (크로스 발생 시에만 진입 검사)',
                        'EMA 정배열: EMA20 > EMA50 > EMA200 (필수)'
                    ],
                    'scoring': {
                        'ema_alignment': '+0.4점 (필수)',
                        'rsi_condition': '+0.1~0.15점 (RSI 30~70 또는 과매도 반등)',
                        'ema_spread': '+0.1점 (EMA 간격 > 0.5%)'
                    },
                    'min_score': getattr(self.strategy, 'score_threshold', 0.5),
                    'sl_tp': {
                        'stop_loss': 'Entry - (ATR × atr_multiplier)',
                        'take_profit_1': 'Entry + (ATR × atr_multiplier × 1.5)',
                        'take_profit_2': 'Entry + (ATR × atr_multiplier × 2.5)',
                        'atr_multiplier': getattr(self.strategy, 'atr_multiplier', 1.5)
                    }
                },
                'sell': {
                    'condition': '진입은 골든/데드 크로스가 발생한 봉에서만 평가됩니다.',
                    'required': [
                        '데드 크로스: EMA20이 EMA50을 하향 돌파 (크로스 발생 시에만 진입 검사)',
                        'EMA 역배열: EMA20 < EMA50 < EMA200 (필수)'
                    ],
                    'scoring': {
                        'ema_alignment': '+0.4점 (필수)',
                        'rsi_condition': '+0.1~0.15점 (RSI 30~70 또는 과매수 하락)',
                        'ema_spread': '+0.1점 (EMA 간격 > 0.5%)'
                    },
                    'min_score': getattr(self.strategy, 'score_threshold', 0.5),
                    'sl_tp': {
                        'stop_loss': 'Entry + (ATR × atr_multiplier)',
                        'take_profit_1': 'Entry - (ATR × atr_multiplier × 1.5)',
                        'take_profit_2': 'Entry - (ATR × atr_multiplier × 2.5)',
                        'atr_multiplier': getattr(self.strategy, 'atr_multiplier', 1.5)
                    }
                }
            }
            rules['filters'] = {
                'rsi_filter': getattr(self.strategy, 'use_rsi_filter', True),
                'rsi_entry_ban': 'RSI < 25(과매도) 또는 RSI > 75(과매수) 구간에서는 진입 금지'
            }
        elif strategy_name == 'rsi':
            rules['entry_rules'] = {
                'buy': {
                    'condition': '과매도 탈출: 이전 RSI ≤ 30 → 현재 RSI > 30'
                },
                'sell': {
                    'condition': '과매수 탈출: 이전 RSI ≥ 70 → 현재 RSI < 70'
                }
            }
        elif strategy_name == 'ict':
            rules['entry_rules'] = {
                'buy': {
                    'condition': 'Order Block 또는 Fair Value Gap 탐지 + 현재가가 OB/FVG 영역에 위치'
                },
                'sell': {
                    'condition': 'Bearish Order Block 또는 FVG 탐지 + 현재가가 OB/FVG 영역에 위치'
                }
            }
        elif strategy_name == 'bollinger':
            rules['entry_rules'] = {
                'buy': {
                    'condition': '하단 밴드 터치 후 반등 또는 밴드 수축 후 확장'
                },
                'sell': {
                    'condition': '상단 밴드 터치 후 하락 또는 밴드 수축 후 확장'
                }
            }
        elif strategy_name == 'ema_divergence_volume':
            rules['entry_rules'] = {
                'buy': {
                    'required': [
                        'EMA20 > EMA59 (상승 추세)',
                        f'N캔들({getattr(self.strategy, "divergence_lookback", 5)}) 동안 이격 확대',
                        f'볼륨 >= MA×{getattr(self.strategy, "volume_multiplier", 1.5)}',
                        '상승 캔들 (종가 > 시가)'
                    ],
                    'sl_tp': {
                        'stop_loss': f'진입가 - ATR × {getattr(self.strategy, "atr_multiplier", 1.5)}',
                        'take_profit_1': '진입가 + ATR × 2.0'
                    }
                },
                'sell': {
                    'required': [
                        'EMA20 < EMA59 (하락 추세)',
                        f'N캔들({getattr(self.strategy, "divergence_lookback", 5)}) 동안 이격 확대',
                        f'볼륨 >= MA×{getattr(self.strategy, "volume_multiplier", 1.5)}',
                        '하락 캔들 (종가 < 시가)'
                    ],
                    'sl_tp': {
                        'stop_loss': f'진입가 + ATR × {getattr(self.strategy, "atr_multiplier", 1.5)}',
                        'take_profit_1': '진입가 - ATR × 2.0'
                    }
                }
            }
        else:
            rules['entry_rules'] = {
                'note': f'{self.strategy_name} 전략의 상세 진입 규칙은 전략 파일을 참고하세요'
            }

        # 공통 필터 (모든 전략): RSI 진입 금지
        rules['filters'] = rules.get('filters', {})
        rules['filters']['rsi_entry_ban'] = 'RSI < 25(과매도) 또는 RSI > 75(과매수) 구간에서는 진입 금지'

        # 전략 설정 정보
        if hasattr(self.strategy, 'config'):
            rules['strategy_config'] = self.strategy.config

        # 설정 스키마 (편집 UI용)
        if hasattr(self.strategy.__class__, 'get_config_schema'):
            rules['config_schema'] = self.strategy.__class__.get_config_schema()

        # 청산 조건 (exit manager 타입별)
        rules['exit_rules'] = self._get_exit_rules()
        
        return rules

    def _get_exit_rules(self) -> Dict:
        """청산 조건 요약 반환"""
        exit_rules = {
            'sl_tp': '손절(SL) / 익절(TP) 도달 시 청산',
            'dynamic': []
        }
        if hasattr(self, 'exit_manager'):
            em = self.exit_manager
            em_name = type(em).__name__
            if em_name == 'EMACrossExitManager':
                exit_rules['dynamic'] = [
                    'TP1 도달 시 부분 청산 (설정 비율)',
                    'TP2 도달 시 SL을 본절로 이동 (트레일링 스톱)',
                    'EMA50선 터치 시 청산'
                ]
                exit_rules['tp1_partial_pct'] = getattr(em, 'tp1_partial_exit_pct', 50)
                exit_rules['trailing_stop'] = getattr(em, 'use_trailing_stop', True)
            elif em_name == 'AdaptiveExitManager':
                exit_rules['dynamic'] = [
                    'LONG: 가격이 EMA50 아래로 떨어지거나 캔들 몸통이 EMA50에 닿을 때 청산',
                    'SHORT: 가격이 EMA50 위로 올라가거나 캔들 몸통이 EMA50에 닿을 때 청산'
                ]
        exit_rules['reentry_cooldown'] = f'청산 후 {self.reentry_cooldown_minutes}분 이내 신호 진입 금지'
        return exit_rules

    def _normalize_indicators(self, indicators: Dict) -> Dict:
        """지표 값을 JSON 직렬화 가능한 타입으로 정규화 (numpy/NaN 제거)."""
        out = {}
        for k, v in indicators.items():
            if v is None:
                out[k] = None
            elif isinstance(v, str):
                out[k] = v
            elif isinstance(v, (int, float)):
                try:
                    f = float(v)
                    out[k] = None if (math.isnan(f) or math.isinf(f)) else f
                except (TypeError, ValueError):
                    out[k] = None
            else:
                try:
                    f = float(v)
                    out[k] = None if (math.isnan(f) or math.isinf(f)) else f
                except (TypeError, ValueError):
                    out[k] = None
        return out

    def get_status(self) -> Dict:
        """상태 조회"""
        open_positions = self.broker.get_open_positions()
        position = open_positions[0] if open_positions else None

        # 동적 청산 정보 (포지션이 있을 때만)
        exit_info = None
        if position and self.current_price and hasattr(self.exit_manager, 'get_exit_info'):
            exit_info = self.exit_manager.get_exit_info(position, self.current_price)

        # 기술적 지표는 포지션 유무와 관계없이 항상 반환 (실행 종목 지표 패널용)
        indicators = None
        if hasattr(self.exit_manager, 'get_indicators'):
            indicators = self.exit_manager.get_indicators()
            # 캔들이 50개 이상인데 지표가 비어 있으면 마지막 캔들로 한 번 갱신 (UI에서 지표가 나오도록)
            if (not indicators or not indicators.get('ema_50')) and len(self.candles) >= 50:
                if hasattr(self.exit_manager, 'update_realtime_candle') and self.candles:
                    try:
                        self.exit_manager.update_realtime_candle(self.candles[-1])
                        indicators = self.exit_manager.get_indicators()
                    except Exception as e:
                        logger.debug(f"지표 갱신 실패 (무시): {e}")
            # JSON 직렬화 가능하도록 정규화 (numpy/NaN 제거)
            if indicators:
                indicators = self._normalize_indicators(indicators)
            # 전략별 실시간 지표 계산 및 병합 (compute_realtime_indicators)
            if indicators is not None and hasattr(self.strategy, 'compute_realtime_indicators') and self.candles:
                try:
                    extra = self.strategy.compute_realtime_indicators(self.candles)
                    if extra:
                        extra_norm = self._normalize_indicators(extra)
                        indicators.update(extra_norm)
                        # ema_divergence_volume: ema_spread = (ema_20 - ema_59) / ema_59 * 100
                        if self.strategy_name.lower() == 'ema_divergence_volume':
                            ema20 = indicators.get('ema_20')
                            ema59 = indicators.get('ema_59')
                            if ema20 and ema59:
                                indicators['ema_spread'] = round((ema20 - ema59) / ema59 * 100, 4)
                except Exception as e:
                    logger.debug(f"전략 지표 계산 실패 (무시): {e}")

        # 마지막 봉 기준 골든/데드 크로스 여부 (진입은 이 봉에서만 검사됨)
        last_candle_cross = None
        if self.strategy_name.lower() == 'ema_cross' and TechnicalIndicators and len(self.candles) >= 100:
            try:
                df_candles = pd.DataFrame(self.candles)
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    if col in df_candles.columns:
                        df_candles[col] = pd.to_numeric(df_candles[col], errors='coerce')
                df_candles = TechnicalIndicators.calculate_all(df_candles, inplace=False)
                df_candles = TechnicalIndicators.detect_ema_cross(df_candles)
                last = df_candles.iloc[-1]
                last_candle_cross = {
                    'golden_cross': bool(last.get('golden_cross', False)),
                    'dead_cross': bool(last.get('dead_cross', False)),
                }
            except Exception as e:
                logger.debug(f"마지막 봉 크로스 계산 실패 (무시): {e}")

        position_data = None
        if position:
            position_data = {
                'position_id': position.position_id,
                'symbol': position.symbol,  # 심볼 추가
                'side': position.side.value,
                'entry_price': position.entry_price,
                'quantity': position.quantity,
                'current_price': self.current_price,
                'pnl': position.pnl if self.current_price else None,
                'pnl_percent': position.pnl_percent if self.current_price else None,
                'stop_loss': position.stop_loss,
                'take_profit': position.take_profit,
                'exit_info': exit_info,  # 동적 청산 정보
                'indicators': indicators  # 기술적 지표
            }

        # 지표 표시 스키마 (전략별 동적 컬럼 정의)
        indicator_display_schema = []
        if hasattr(self.strategy, 'get_indicator_display_schema'):
            try:
                indicator_display_schema = self.strategy.get_indicator_display_schema()
            except Exception as e:
                logger.debug(f"지표 스키마 조회 실패 (무시): {e}")

        return {
            'running': self.is_running,
            'symbol': self.symbol,
            'strategy': self.strategy_name,
            'timeframe': self.timeframe,
            'current_price': self.current_price,
            'candles_count': len(self.candles),
            'position': position_data,
            'indicators': indicators,  # 포지션이 없어도 지표 표시
            'indicator_display_schema': indicator_display_schema,  # 전략별 지표 컬럼 정의
            'last_candle_cross': last_candle_cross,  # 마지막 봉 골든/데드 크로스 (진입 검사는 이 봉에서만)
            'logs': self.logs[-20:] if self.logs else [],  # 최근 20개 로그
            'trade_history': self.trade_history[-50:] if self.trade_history else []  # 최근 50개 거래 히스토리
        }

    async def _add_entry_to_history(self, position, entry_type: str = 'signal'):
        """진입 히스토리 추가 및 이벤트 전송"""
        """진입 히스토리에 추가"""
        try:
            entry_time = datetime.now()
            history_entry = {
                'type': 'entry',
                'entry_type': entry_type,  # 'signal' or 'manual'
                'position_id': position.position_id,
                'symbol': position.symbol,
                'side': position.side.value,
                'direction': 'long' if position.side == OrderSide.BUY else 'short',
                'entry_price': position.entry_price,
                'price': position.entry_price,  # 호환성
                'quantity': position.quantity,
                'size': position.quantity,  # 호환성
                'stop_loss': position.stop_loss,
                'take_profit': position.take_profit,
                'time': entry_time.isoformat(),
                'entry_time': entry_time.isoformat(),  # 호환성
                'timestamp': entry_time.timestamp()
            }
            self.trade_history.append(history_entry)
            logger.info(f"📝 진입 히스토리 추가: {position.position_id}")
            
            # 거래 이벤트 콜백 호출
            if self.on_trade_event:
                try:
                    await self.on_trade_event({
                        'type': 'entry',
                        'data': history_entry
                    })
                except Exception as e:
                    logger.warning(f"⚠️ 거래 이벤트 콜백 실패: {e}")
            
            # DB에 저장 (진입 시점에 open 상태로 저장)
            if self.positions_repo:
                try:
                    position_data = {
                        'position_id': position.position_id,
                        'symbol': position.symbol,
                        'side': position.side.value.lower(),  # 'buy' or 'sell'
                        'entry_price': float(position.entry_price),
                        'exit_price': None,  # 아직 청산되지 않음
                        'quantity': float(position.quantity),
                        'pnl': None,  # 아직 청산되지 않음
                        'pnl_percent': None,
                        'commission': float(position.commission) if hasattr(position, 'commission') and position.commission else 0.0,
                        'status': 'open',  # 진입 시점에는 open
                        'leverage': 1,  # Paper trading은 레버리지 없음
                        'margin': None,
                        'open_time': entry_time,
                        'close_time': None,  # 아직 청산되지 않음
                        'duration_minutes': None,
                        'order_ids': [],
                        'metadata': {
                            'strategy': self.strategy_name,
                            'strategy_name': self.strategy_name,
                            'timeframe': self.timeframe,
                            'entry_type': entry_type,
                            'source': 'realtime_trading'
                        },
                        'raw_data': {},
                        'source': 'realtime_trading'
                    }
                    
                    saved = self.positions_repo.save_position(position_data)
                    if saved:
                        logger.info(f"💾 진입 포지션 DB 저장 완료: {position.position_id}")
                except Exception as e:
                    logger.warning(f"⚠️ 진입 포지션 DB 저장 실패: {e}")
        except Exception as e:
            logger.error(f"❌ 진입 히스토리 추가 실패: {e}", exc_info=True)

    async def _add_exit_to_history(self, position, exit_reason: str = 'unknown'):
        """청산 히스토리에 추가 및 DB 저장"""
        try:
            exit_time = datetime.now()
            self.last_exit_time = exit_time  # 재진입 쿨다운 시작
            
            # 히스토리에 추가
            history_entry = {
                'type': 'exit',
                'exit_reason': exit_reason,  # 'sl_tp', 'ema_50_touch', 'ema_50_below', etc.
                'position_id': position.position_id,
                'symbol': position.symbol,
                'side': position.side.value,
                'direction': 'long' if position.side == OrderSide.BUY else 'short',
                'entry_price': position.entry_price,
                'exit_price': position.exit_price if hasattr(position, 'exit_price') else self.current_price,
                'price': position.exit_price if hasattr(position, 'exit_price') else self.current_price,  # 호환성
                'quantity': position.quantity,
                'size': position.quantity,  # 호환성
                'pnl': position.pnl if hasattr(position, 'pnl') else None,
                'pnl_pct': position.pnl_percent if hasattr(position, 'pnl_percent') else None,  # 호환성
                'pnl_percent': position.pnl_percent if hasattr(position, 'pnl_percent') else None,
                'time': exit_time.isoformat(),
                'exit_time': exit_time.isoformat(),  # 호환성
                'timestamp': exit_time.timestamp()
            }
            self.trade_history.append(history_entry)
            
            # 거래 이벤트 콜백 호출
            if self.on_trade_event:
                try:
                    await self.on_trade_event({
                        'type': 'exit',
                        'data': history_entry
                    })
                except Exception as e:
                    logger.warning(f"⚠️ 거래 이벤트 콜백 실패: {e}")
            
            # DB에 저장
            if self.positions_repo:
                try:
                    # 진입 시간 찾기 (히스토리에서)
                    entry_time = exit_time  # 기본값
                    for entry in reversed(self.trade_history):
                        if entry.get('type') == 'entry' and entry.get('position_id') == position.position_id:
                            entry_time = datetime.fromisoformat(entry['time'])
                            break
                    
                    duration_minutes = int((exit_time - entry_time).total_seconds() / 60) if entry_time else 0
                    
                    position_data = {
                        'position_id': position.position_id,
                        'symbol': position.symbol,
                        'side': position.side.value.lower(),  # 'buy' or 'sell'
                        'entry_price': float(position.entry_price),
                        'exit_price': float(position.exit_price if hasattr(position, 'exit_price') and position.exit_price else self.current_price),
                        'quantity': float(position.quantity),
                        'pnl': float(position.pnl) if hasattr(position, 'pnl') and position.pnl else 0.0,
                        'pnl_percent': float(position.pnl_percent) if hasattr(position, 'pnl_percent') and position.pnl_percent else 0.0,
                        'commission': float(position.commission) if hasattr(position, 'commission') and position.commission else 0.0,
                        'status': 'closed',
                        'leverage': 1,  # Paper trading은 레버리지 없음
                        'margin': None,
                        'open_time': entry_time,
                        'close_time': exit_time,
                        'duration_minutes': duration_minutes,
                        'order_ids': [],
                        'metadata': {
                            'strategy': self.strategy_name,
                            'strategy_name': self.strategy_name,  # 적응형 전략 시스템용
                            'timeframe': self.timeframe,
                            'exit_reason': exit_reason,
                            'source': 'realtime_trading'
                        },
                        'raw_data': {},
                        'source': 'realtime_trading'
                    }
                    
                    saved = self.positions_repo.save_position(position_data)
                    if saved:
                        logger.info(f"💾 포지션 DB 저장 완료: {position.position_id}")
                        self._add_log(f"💾 포지션 DB 저장 완료: {position.position_id}", "info")
                        
                        # 적응형 전략 성과 업데이트 (비동기로 실행)
                        if self.adaptive_manager:
                            asyncio.create_task(self._update_strategy_performance())
                    else:
                        logger.warning(f"⚠️ 포지션 DB 저장 실패: {position.position_id}")
                except Exception as e:
                    logger.error(f"❌ 포지션 DB 저장 실패: {e}", exc_info=True)
            
            # 청산 알림
            pnl_text = f" (PnL: {position.pnl:+.2f} USDT, {position.pnl_percent:+.2f}%)" if hasattr(position, 'pnl') and position.pnl else ""
            exit_message = f"청산 완료: {exit_reason}{pnl_text}"
            self._add_log(f"🔔 {exit_message}", "info")
            
            # 포지션 업데이트 콜백에 청산 알림 전달
            if self.on_position_update:
                try:
                    await self.on_position_update('exit', exit_message)
                except Exception as e:
                    logger.warning(f"⚠️ 청산 알림 전송 실패: {e}")
            
            logger.info(f"📝 청산 히스토리 추가: {position.position_id} - {exit_reason}")
            
        except Exception as e:
            logger.error(f"❌ 청산 히스토리 추가 실패: {e}", exc_info=True)

    async def _periodic_strategy_evaluation(self):
        """주기적 전략 평가 (1시간마다)"""
        if not self.adaptive_manager:
            return
        
        await asyncio.sleep(3600)  # 첫 실행 전 1시간 대기
        
        while self.is_running:
            try:
                # 현재 전략 평가
                suggestion = self.adaptive_manager.evaluate_and_suggest(
                    self.strategy_name
                )
                
                if suggestion:
                    if suggestion['should_switch']:
                        # 자동 전환 제안
                        self._add_log(
                            f"🔄 전략 자동 전환 제안: {suggestion['current_strategy']} → "
                            f"{suggestion['suggested_strategy']} "
                            f"(개선: {suggestion['improvement_pct']:.1f}%, "
                            f"신뢰도: {suggestion['confidence']*100:.1f}%)",
                            "info"
                        )
                        # 실제 전략 변경은 수동으로 처리 (엔진 재시작 필요)
                    elif suggestion['should_suggest']:
                        # 제안만
                        self._add_log(
                            f"💡 전략 전환 제안: {suggestion['current_strategy']} → "
                            f"{suggestion['suggested_strategy']} "
                            f"(개선: {suggestion['improvement_pct']:.1f}%, "
                            f"신뢰도: {suggestion['confidence']*100:.1f}%)",
                            "info"
                        )
            except Exception as e:
                logger.error(f"❌ 전략 평가 실패: {e}", exc_info=True)
            
            # 1시간 대기
            await asyncio.sleep(3600)

    async def _update_strategy_performance(self):
        """전략 성과 업데이트 (거래 완료 시)"""
        if not self.adaptive_manager:
            return
        
        try:
            tracker = self.adaptive_manager.performance_tracker
            
            # 성과 계산
            performance = tracker.calculate_performance(
                symbol=self.symbol,
                strategy_name=self.strategy_name,
                timeframe=self.timeframe,
                period_type='medium',
                min_trades=5
            )
            
            if performance:
                # DB 저장
                tracker.save_performance(performance)
                self._add_log(
                    f"📊 전략 성과 업데이트: {self.strategy_name} "
                    f"(점수: {performance['composite_score']:.2f}, "
                    f"승률: {performance['win_rate']:.1f}%, "
                    f"거래 수: {performance['total_trades']})",
                    "info"
                )
        except Exception as e:
            logger.error(f"❌ 성과 업데이트 실패: {e}", exc_info=True)
