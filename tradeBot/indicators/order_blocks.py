"""
ICT Order Blocks (주문 블록)

Order Block = 큰 움직임이 시작되기 전 마지막 반대 방향 캔들
- Bullish OB: 상승 전 마지막 하락 캔들
- Bearish OB: 하락 전 마지막 상승 캔들
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class OrderBlock:
    """Order Block 데이터"""
    symbol: str
    timeframe: str
    type: str  # 'bullish' or 'bearish'
    
    # 캔들 정보
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    
    # Order Block 영역
    top: float  # 상단 (resistance)
    bottom: float  # 하단 (support)
    
    # 메타 정보
    strength: float  # 강도 (0-1)
    volume: float
    created_at: datetime
    
    # 상태
    is_active: bool = True  # 아직 터치되지 않음
    touches: int = 0  # 터치 횟수


class OrderBlockDetector:
    """
    Order Block 탐지기
    
    Features:
    - Bullish/Bearish OB 식별
    - 강도 계산
    - 터치 추적
    """
    
    def __init__(
        self,
        lookback: int = 20,
        min_body_ratio: float = 0.5,
        min_move_pct: float = 0.01,
        max_age_bars: int = 100
    ):
        """
        초기화
        
        Args:
            lookback: OB 탐색 범위 (캔들 개수)
            min_body_ratio: 최소 몸통 비율 (0-1)
            min_move_pct: 최소 이동 비율 (1% = 0.01)
            max_age_bars: OB 최대 유효기간 (캔들 개수)
        """
        self.lookback = lookback
        self.min_body_ratio = min_body_ratio
        self.min_move_pct = min_move_pct
        self.max_age_bars = max_age_bars
        
        logger.info(f"✅ OrderBlockDetector 초기화")
        logger.info(f"   lookback: {lookback}")
        logger.info(f"   min_body_ratio: {min_body_ratio}")
    
    def detect(self, df: pd.DataFrame) -> List[OrderBlock]:
        """
        Order Blocks 탐지
        
        Args:
            df: OHLCV DataFrame
                - open_time, open, high, low, close, volume
        
        Returns:
            Order Blocks 리스트
        """
        if len(df) < self.lookback + 5:
            logger.warning(f"⚠️  데이터 부족: {len(df)} < {self.lookback + 5}")
            return []
        
        df = df.copy()
        
        # 1. 캔들 타입 (bullish/bearish)
        df['is_bullish'] = df['close'] > df['open']
        df['is_bearish'] = df['close'] < df['open']
        
        # 2. 몸통 크기
        df['body'] = abs(df['close'] - df['open'])
        df['range'] = df['high'] - df['low']
        df['body_ratio'] = df['body'] / df['range'].replace(0, np.nan)
        
        # 3. Order Blocks 찾기
        order_blocks = []
        
        # Bullish Order Blocks
        bullish_obs = self._find_bullish_obs(df)
        order_blocks.extend(bullish_obs)
        
        # Bearish Order Blocks
        bearish_obs = self._find_bearish_obs(df)
        order_blocks.extend(bearish_obs)
        
        logger.info(f"📊 Order Blocks 탐지: {len(order_blocks)}개 (Bullish: {len(bullish_obs)}, Bearish: {len(bearish_obs)})")
        
        return order_blocks
    
    def _find_bullish_obs(self, df: pd.DataFrame) -> List[OrderBlock]:
        """
        Bullish Order Blocks 찾기
        
        상승 전 마지막 하락 캔들
        """
        obs = []
        
        for i in range(self.lookback, len(df) - 3):
            # 현재 캔들이 하락 캔들인지
            if not df.iloc[i]['is_bearish']:
                continue
            
            # 몸통이 충분히 큰지
            if df.iloc[i]['body_ratio'] < self.min_body_ratio:
                continue
            
            # 다음 캔들들이 상승했는지
            next_candles = df.iloc[i+1:i+4]
            
            if len(next_candles) < 3:
                continue
            
            # 강한 상승 확인
            price_move = (next_candles['close'].max() - df.iloc[i]['close']) / df.iloc[i]['close']
            
            if price_move < self.min_move_pct:
                continue
            
            # Order Block 생성
            candle = df.iloc[i]
            
            ob = OrderBlock(
                symbol=df.iloc[0].get('symbol', 'UNKNOWN'),
                timeframe=df.iloc[0].get('tf', '1m'),
                type='bullish',
                open_time=candle['open_time'],
                open=candle['open'],
                high=candle['high'],
                low=candle['low'],
                close=candle['close'],
                top=candle['high'],  # Bullish OB의 상단
                bottom=candle['low'],  # Bullish OB의 하단 (support)
                strength=self._calculate_strength(df, i, 'bullish'),
                volume=candle['volume'],
                created_at=datetime.now(),
                is_active=True,
                touches=0
            )
            
            obs.append(ob)
        
        return obs
    
    def _find_bearish_obs(self, df: pd.DataFrame) -> List[OrderBlock]:
        """
        Bearish Order Blocks 찾기
        
        하락 전 마지막 상승 캔들
        """
        obs = []
        
        for i in range(self.lookback, len(df) - 3):
            # 현재 캔들이 상승 캔들인지
            if not df.iloc[i]['is_bullish']:
                continue
            
            # 몸통이 충분히 큰지
            if df.iloc[i]['body_ratio'] < self.min_body_ratio:
                continue
            
            # 다음 캔들들이 하락했는지
            next_candles = df.iloc[i+1:i+4]
            
            if len(next_candles) < 3:
                continue
            
            # 강한 하락 확인
            price_move = (df.iloc[i]['close'] - next_candles['close'].min()) / df.iloc[i]['close']
            
            if price_move < self.min_move_pct:
                continue
            
            # Order Block 생성
            candle = df.iloc[i]
            
            ob = OrderBlock(
                symbol=df.iloc[0].get('symbol', 'UNKNOWN'),
                timeframe=df.iloc[0].get('tf', '1m'),
                type='bearish',
                open_time=candle['open_time'],
                open=candle['open'],
                high=candle['high'],
                low=candle['low'],
                close=candle['close'],
                top=candle['high'],  # Bearish OB의 상단 (resistance)
                bottom=candle['low'],  # Bearish OB의 하단
                strength=self._calculate_strength(df, i, 'bearish'),
                volume=candle['volume'],
                created_at=datetime.now(),
                is_active=True,
                touches=0
            )
            
            obs.append(ob)
        
        return obs
    
    def _calculate_strength(self, df: pd.DataFrame, idx: int, ob_type: str) -> float:
        """
        Order Block 강도 계산
        
        Args:
            df: DataFrame
            idx: OB 인덱스
            ob_type: 'bullish' or 'bearish'
        
        Returns:
            강도 (0-1)
        """
        candle = df.iloc[idx]
        
        # 1. 몸통 비율 (클수록 강함)
        body_score = min(candle['body_ratio'], 1.0)
        
        # 2. 거래량 (평균 대비)
        volume_ma = df.iloc[max(0, idx-20):idx]['volume'].mean()
        volume_score = min(candle['volume'] / volume_ma, 2.0) / 2.0 if volume_ma > 0 else 0.5
        
        # 3. 이후 움직임 크기
        if idx + 3 < len(df):
            next_candles = df.iloc[idx+1:idx+4]
            
            if ob_type == 'bullish':
                move = (next_candles['high'].max() - candle['close']) / candle['close']
            else:
                move = (candle['close'] - next_candles['low'].min()) / candle['close']
            
            move_score = min(move / 0.05, 1.0)  # 5% 이상이면 만점
        else:
            move_score = 0.5
        
        # 종합 점수
        strength = (body_score * 0.3 + volume_score * 0.3 + move_score * 0.4)
        
        return round(strength, 3)
    
    def check_touches(self, obs: List[OrderBlock], df: pd.DataFrame) -> List[OrderBlock]:
        """
        Order Block 터치 확인
        
        Args:
            obs: Order Blocks 리스트
            df: 최신 캔들 데이터
        
        Returns:
            업데이트된 Order Blocks
        """
        for ob in obs:
            if not ob.is_active:
                continue
            
            # 최근 캔들들 확인
            recent_candles = df.tail(10)
            
            for _, candle in recent_candles.iterrows():
                # Bullish OB: 가격이 하단(support)을 터치했는지
                if ob.type == 'bullish':
                    if candle['low'] <= ob.bottom <= candle['high']:
                        ob.touches += 1
                        logger.info(f"💚 Bullish OB 터치: {ob.symbol} @ {ob.bottom:.2f} (터치: {ob.touches})")
                
                # Bearish OB: 가격이 상단(resistance)을 터치했는지
                elif ob.type == 'bearish':
                    if candle['low'] <= ob.top <= candle['high']:
                        ob.touches += 1
                        logger.info(f"❤️  Bearish OB 터치: {ob.symbol} @ {ob.top:.2f} (터치: {ob.touches})")
            
            # 너무 많이 터치되면 비활성화
            if ob.touches >= 3:
                ob.is_active = False
                logger.info(f"⚫ OB 비활성화: {ob.symbol} (터치: {ob.touches})")
        
        return obs
    
    def filter_active(self, obs: List[OrderBlock]) -> List[OrderBlock]:
        """활성 Order Blocks만 필터링"""
        return [ob for ob in obs if ob.is_active]
    
    def get_nearest(
        self, 
        obs: List[OrderBlock], 
        current_price: float, 
        ob_type: Optional[str] = None
    ) -> Optional[OrderBlock]:
        """
        현재가에서 가장 가까운 Order Block
        
        Args:
            obs: Order Blocks 리스트
            current_price: 현재가
            ob_type: 'bullish' or 'bearish' (None이면 모두)
        
        Returns:
            가장 가까운 Order Block
        """
        active_obs = self.filter_active(obs)
        
        if ob_type:
            active_obs = [ob for ob in active_obs if ob.type == ob_type]
        
        if not active_obs:
            return None
        
        # 현재가와의 거리 계산
        for ob in active_obs:
            if ob.type == 'bullish':
                ob.distance = abs(current_price - ob.bottom)
            else:
                ob.distance = abs(current_price - ob.top)
        
        # 가장 가까운 것
        nearest = min(active_obs, key=lambda x: x.distance)
        
        return nearest


# 테스트
if __name__ == "__main__":
    import sys
    import os
    
    # 상위 디렉토리를 경로에 추가
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    import psycopg2
    from core.config_loader import get_config
    
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 70)
    print("Order Block 탐지 테스트")
    print("=" * 70)
    print()
    
    # Config
    config = get_config()
    
    # DB 연결
    conn = psycopg2.connect(
        host=config.get('db.host'),
        port=config.get('db.port'),
        dbname=config.get('db.name'),
        user=config.get('db.user'),
        password=config.get('db.password')
    )
    
    # 데이터 로드
    query = """
        SELECT open_time, open, high, low, close, volume, symbol, tf
        FROM candles
        WHERE symbol = 'BTCUSDT'
          AND tf = '15m'
        ORDER BY open_time DESC
        LIMIT 500
    """
    
    df = pd.read_sql(query, conn)
    df = df.sort_values('open_time').reset_index(drop=True)
    
    conn.close()
    
    print(f"데이터: {len(df)}개 캔들")
    print(f"기간: {df['open_time'].min()} ~ {df['open_time'].max()}")
    print()
    
    # Order Block 탐지
    detector = OrderBlockDetector(lookback=20)
    obs = detector.detect(df)
    
    print()
    print(f"탐지된 Order Blocks: {len(obs)}개")
    print()
    
    # 활성 OB만
    active_obs = detector.filter_active(obs)
    print(f"활성 Order Blocks: {len(active_obs)}개")
    print()
    
    # 상위 5개 (강도 순)
    top_obs = sorted(active_obs, key=lambda x: x.strength, reverse=True)[:5]
    
    print("강도 Top 5:")
    print("-" * 70)
    for i, ob in enumerate(top_obs, 1):
        print(f"{i}. [{ob.type.upper()}] {ob.symbol} @ {ob.bottom if ob.type == 'bullish' else ob.top:.2f}")
        print(f"   시간: {ob.open_time}")
        print(f"   강도: {ob.strength:.3f}")
        print(f"   범위: {ob.bottom:.2f} - {ob.top:.2f}")
        print()
