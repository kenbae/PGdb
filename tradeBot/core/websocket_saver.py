"""
WebSocket 데이터 → PostgreSQL 저장

실시간 캔들 데이터를 DB에 저장
"""

import logging
from datetime import datetime
from typing import Dict
import psycopg2
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)


class CandleStorage:
    """
    캔들 데이터 저장 클래스
    
    WebSocket에서 받은 캔들을 PostgreSQL에 저장
    """
    
    def __init__(self, db_config: Dict):
        """
        초기화
        
        Args:
            db_config: DB 설정 {'host', 'port', 'dbname', 'user', 'password'}
        """
        self.db_config = db_config
        self.conn = None
        self.cursor = None
        
        # 연결
        self.connect()
    
    def connect(self):
        """DB 연결"""
        try:
            self.conn = psycopg2.connect(**self.db_config)
            self.cursor = self.conn.cursor()
            
            logger.info("✅ PostgreSQL 연결")
        
        except Exception as e:
            logger.error(f"❌ PostgreSQL 연결 실패: {e}")
            raise
    
    def save_candle(self, candle: Dict):
        """
        캔들 저장 (중복 시 무시)
        
        Args:
            candle: 캔들 데이터
        """
        try:
            # 먼저 존재하는지 확인
            check_query = """
                SELECT 1 FROM candles 
                WHERE symbol = %s 
                  AND tf = %s 
                  AND open_time = to_timestamp(%s / 1000.0)
            """
            
            self.cursor.execute(
                check_query, 
                (candle['symbol'], candle['timeframe'], candle['open_time'])
            )
            
            exists = self.cursor.fetchone()
            
            if exists:
                # 이미 존재하면 UPDATE
                update_query = """
                    UPDATE candles
                    SET open = %s,
                        high = %s,
                        low = %s,
                        close = %s,
                        volume = %s
                    WHERE symbol = %s
                      AND tf = %s
                      AND open_time = to_timestamp(%s / 1000.0)
                """
                
                self.cursor.execute(
                    update_query,
                    (
                        candle['open'],
                        candle['high'],
                        candle['low'],
                        candle['close'],
                        candle['volume'],
                        candle['symbol'],
                        candle['timeframe'],
                        candle['open_time']
                    )
                )
            else:
                # 없으면 INSERT (exchange, market 컬럼 포함)
                insert_query = """
                    INSERT INTO candles (
                        exchange, market, symbol, tf, open_time, open, high, low, close, volume
                    ) VALUES (
                        %s, %s, %s, %s, to_timestamp(%s / 1000.0), %s, %s, %s, %s, %s
                    )
                """
                
                self.cursor.execute(
                    insert_query,
                    (
                        'binance',  # exchange
                        'usdtm',    # market (USDT-M Futures)
                        candle['symbol'],
                        candle['timeframe'],
                        candle['open_time'],
                        candle['open'],
                        candle['high'],
                        candle['low'],
                        candle['close'],
                        candle['volume']
                    )
                )
            
            self.conn.commit()
            
            logger.debug(f"💾 저장: {candle['symbol']} {candle['timeframe']} {candle['open_time']}")
        
        except Exception as e:
            logger.error(f"❌ 캔들 저장 실패: {e}")
            self.conn.rollback()
    
    def save_candles_batch(self, candles: list):
        """
        캔들 일괄 저장
        
        Args:
            candles: 캔들 리스트
        """
        if not candles:
            return
        
        try:
            query = """
                INSERT INTO candles (
                    symbol, tf, open_time, open, high, low, close, volume
                ) VALUES %s
                ON CONFLICT (symbol, tf, open_time) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume
            """
            
            # 데이터 준비
            values = [
                (
                    c['symbol'],
                    c['timeframe'],  # timeframe을 tf로 매핑
                    datetime.fromtimestamp(c['open_time'] / 1000.0),
                    c['open'],
                    c['high'],
                    c['low'],
                    c['close'],
                    c['volume']
                )
                for c in candles
            ]
            
            execute_values(self.cursor, query, values)
            self.conn.commit()
            
            logger.info(f"💾 일괄 저장: {len(candles)}개 캔들")
        
        except Exception as e:
            logger.error(f"❌ 일괄 저장 실패: {e}")
            self.conn.rollback()
    
    def close(self):
        """연결 종료"""
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()
        
        logger.info("⏹️  PostgreSQL 연결 종료")


class WebSocketDataSaver:
    """
    WebSocket → DB 저장 통합 클래스
    """
    
    def __init__(self, ws_client, db_config: Dict):
        """
        초기화
        
        Args:
            ws_client: BinanceWebSocket 인스턴스
            db_config: DB 설정
        """
        self.ws = ws_client
        self.storage = CandleStorage(db_config)
        
        # 콜백 등록
        self.ws.on_kline(self.on_kline)
        
        logger.info("✅ WebSocket → DB 저장 준비 완료")
    
    def on_kline(self, kline: Dict):
        """
        Kline 콜백
        
        캔들 완성 시 DB 저장
        """
        # 저장
        self.storage.save_candle(kline)
        
        # 로그
        logger.info(
            f"💾 [{kline['symbol']}] {kline['timeframe']} | "
            f"O:{kline['open']:.2f} H:{kline['high']:.2f} "
            f"L:{kline['low']:.2f} C:{kline['close']:.2f}"
        )
    
    def start(self):
        """시작"""
        self.ws.start()
        logger.info("✅ WebSocket 데이터 저장 시작")
    
    def stop(self):
        """중지"""
        self.ws.stop()
        self.storage.close()
        logger.info("⏹️  WebSocket 데이터 저장 중지")


# 테스트
if __name__ == "__main__":
    import time
    from core.config_loader import get_config
    from websocket_feed import BinanceWebSocket
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    print("=" * 70)
    print("WebSocket → DB 저장 테스트")
    print("=" * 70)
    print()
    
    # Config 로드
    config = get_config()
    
    # DB 설정
    db_config = {
        'host': config.get('db.host'),
        'port': config.get('db.port'),
        'dbname': config.get('db.name'),
        'user': config.get('db.user'),
        'password': config.get('db.password')
    }
    
    print(f"DB: {db_config['host']}:{db_config['port']}/{db_config['dbname']}")
    print()
    
    # WebSocket 생성
    ws = BinanceWebSocket(
        symbols=['BTCUSDT', 'ETHUSDT'],
        timeframes=['1m', '5m', '15m']
    )
    
    # 저장 시작
    saver = WebSocketDataSaver(ws, db_config)
    saver.start()
    
    print("실행 중... (Ctrl+C로 종료)")
    print()
    
    try:
        # 계속 실행
        while True:
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\n종료 중...")
    
    finally:
        saver.stop()
        print("✅ 종료 완료")
