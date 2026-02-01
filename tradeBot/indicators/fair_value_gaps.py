"""
ICT Fair Value Gaps (FVG) - 공정가치 갭

FVG = 3개 캔들 사이의 갭 (비효율적 가격 영역)
- Bullish FVG: 상승 중 생긴 갭 (Support 역할)
- Bearish FVG: 하락 중 생긴 갭 (Resistance 역할)

시장은 FVG를 메우려는 경향이 있음 (rebalancing)
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class FairValueGap:
    """Fair Value Gap 데이터"""
    symbol: str
    timeframe: str
    type: str  # 'bullish' or 'bearish'
    
    # 갭 영역
    top: float  # 갭 상단
    bottom: float  # 갭 하단
    gap_size: float  # 갭 크기
    
    # 시간
    start_time: datetime  # 첫 번째 캔들 시간
    middle_time: datetime  # 두 번째 캔들 시간
    end_time: datetime  # 세 번째 캔들 시간
    
    # 메타 정보
    strength: float  # 강도 (0-1)
    fill_percentage: float  # 메워진 비율 (0-1)
    created_at: datetime
    
    # 상태
    is_filled: bool = False  # 완전히 메워짐
    is_active: bool = True  # 아직 유효


class FVGDetector:
    """
    Fair Value Gap 탐지기
    
    Features:
    - Bullish/Bearish FVG 식별
    - 갭 크기 계산
    - 메움 추적 (fill tracking)
    """
    
    def __init__(
        self,
        min_gap_pct: float = 0.001,  # 최소 갭 크기 (0.1%)
        max_gap_pct: float = 0.05,   # 최대 갭 크기 (5%)
        fill_threshold: float = 0.5   # 메움 임계값 (50%)
    ):
        """
        초기화
        
        Args:
            min_gap_pct: 최소 갭 비율
            max_gap_pct: 최대 갭 비율
            fill_threshold: 메움 판정 임계값
        """
        self.min_gap_pct = min_gap_pct
        self.max_gap_pct = max_gap_pct
        self.fill_threshold = fill_threshold
        
        logger.info(f"✅ FVGDetector 초기화")
        logger.info(f"   min_gap: {min_gap_pct*100:.2f}%")
        logger.info(f"   max_gap: {max_gap_pct*100:.2f}%")
    
    def detect(self, df: pd.DataFrame) -> List[FairValueGap]:
        """
        Fair Value Gaps 탐지
        
        Args:
            df: OHLCV DataFrame
        
        Returns:
            FVG 리스트
        """
        if len(df) < 3:
            logger.warning(f"⚠️  데이터 부족: {len(df)} < 3")
            return []
        
        df = df.copy()
        
        fvgs = []
        
        # 3개 캔들씩 확인
        for i in range(len(df) - 2):
            candle1 = df.iloc[i]
            candle2 = df.iloc[i + 1]
            candle3 = df.iloc[i + 2]
            
            # Bullish FVG 확인
            bullish_fvg = self._check_bullish_fvg(candle1, candle2, candle3)
            if bullish_fvg:
                fvgs.append(bullish_fvg)
            
            # Bearish FVG 확인
            bearish_fvg = self._check_bearish_fvg(candle1, candle2, candle3)
            if bearish_fvg:
                fvgs.append(bearish_fvg)
        
        logger.info(f"📊 FVG 탐지: {len(fvgs)}개")
        
        return fvgs
    
    def _check_bullish_fvg(
        self, 
        candle1: pd.Series, 
        candle2: pd.Series, 
        candle3: pd.Series
    ) -> Optional[FairValueGap]:
        """
        Bullish FVG 확인
        
        조건: candle1의 high < candle3의 low
        (중간에 갭 발생)
        """
        # 갭 존재 확인
        if candle1['high'] >= candle3['low']:
            return None
        
        # 갭 크기
        gap_bottom = candle1['high']
        gap_top = candle3['low']
        gap_size = gap_top - gap_bottom
        
        # 가격 기준 갭 비율
        avg_price = (candle1['close'] + candle2['close'] + candle3['close']) / 3
        gap_pct = gap_size / avg_price
        
        # 갭이 너무 작거나 크면 무시
        if gap_pct < self.min_gap_pct or gap_pct > self.max_gap_pct:
            return None
        
        # 상승 추세 확인 (선택적)
        if candle3['close'] <= candle1['close']:
            return None
        
        # FVG 생성
        fvg = FairValueGap(
            symbol=candle1.get('symbol', 'UNKNOWN'),
            timeframe=candle1.get('tf', '1m'),
            type='bullish',
            top=gap_top,
            bottom=gap_bottom,
            gap_size=gap_size,
            start_time=candle1['open_time'],
            middle_time=candle2['open_time'],
            end_time=candle3['open_time'],
            strength=self._calculate_strength(candle1, candle2, candle3, 'bullish'),
            fill_percentage=0.0,
            created_at=datetime.now(),
            is_filled=False,
            is_active=True
        )
        
        return fvg
    
    def _check_bearish_fvg(
        self, 
        candle1: pd.Series, 
        candle2: pd.Series, 
        candle3: pd.Series
    ) -> Optional[FairValueGap]:
        """
        Bearish FVG 확인
        
        조건: candle1의 low > candle3의 high
        (중간에 갭 발생)
        """
        # 갭 존재 확인
        if candle1['low'] <= candle3['high']:
            return None
        
        # 갭 크기
        gap_top = candle1['low']
        gap_bottom = candle3['high']
        gap_size = gap_top - gap_bottom
        
        # 가격 기준 갭 비율
        avg_price = (candle1['close'] + candle2['close'] + candle3['close']) / 3
        gap_pct = gap_size / avg_price
        
        # 갭이 너무 작거나 크면 무시
        if gap_pct < self.min_gap_pct or gap_pct > self.max_gap_pct:
            return None
        
        # 하락 추세 확인 (선택적)
        if candle3['close'] >= candle1['close']:
            return None
        
        # FVG 생성
        fvg = FairValueGap(
            symbol=candle1.get('symbol', 'UNKNOWN'),
            timeframe=candle1.get('tf', '1m'),
            type='bearish',
            top=gap_top,
            bottom=gap_bottom,
            gap_size=gap_size,
            start_time=candle1['open_time'],
            middle_time=candle2['open_time'],
            end_time=candle3['open_time'],
            strength=self._calculate_strength(candle1, candle2, candle3, 'bearish'),
            fill_percentage=0.0,
            created_at=datetime.now(),
            is_filled=False,
            is_active=True
        )
        
        return fvg
    
    def _calculate_strength(
        self, 
        candle1: pd.Series, 
        candle2: pd.Series, 
        candle3: pd.Series,
        fvg_type: str
    ) -> float:
        """
        FVG 강도 계산
        
        Args:
            candle1, candle2, candle3: 3개 캔들
            fvg_type: 'bullish' or 'bearish'
        
        Returns:
            강도 (0-1)
        """
        # 1. 갭 크기 (클수록 강함)
        gap_size = abs(candle3['low'] - candle1['high']) if fvg_type == 'bullish' else abs(candle1['low'] - candle3['high'])
        avg_price = (candle1['close'] + candle2['close'] + candle3['close']) / 3
        gap_score = min(gap_size / avg_price / 0.01, 1.0)  # 1% = 만점
        
        # 2. 중간 캔들의 움직임 (클수록 강함)
        candle2_move = abs(candle2['close'] - candle2['open']) / candle2['open']
        move_score = min(candle2_move / 0.02, 1.0)  # 2% = 만점
        
        # 3. 거래량 (평균 대비)
        avg_volume = (candle1['volume'] + candle2['volume'] + candle3['volume']) / 3
        if candle2['volume'] > 0 and avg_volume > 0:
            volume_score = min(candle2['volume'] / avg_volume / 1.5, 1.0)
        else:
            volume_score = 0.5
        
        # 종합 점수
        strength = (gap_score * 0.4 + move_score * 0.3 + volume_score * 0.3)
        
        return round(strength, 3)
    
    def update_fills(self, fvgs: List[FairValueGap], df: pd.DataFrame) -> List[FairValueGap]:
        """
        FVG 메움 업데이트
        
        Args:
            fvgs: FVG 리스트
            df: 최신 캔들 데이터
        
        Returns:
            업데이트된 FVG 리스트
        """
        for fvg in fvgs:
            if fvg.is_filled or not fvg.is_active:
                continue
            
            # 최근 캔들들 확인
            recent_candles = df[df['open_time'] > fvg.end_time]
            
            if len(recent_candles) == 0:
                continue
            
            # Bullish FVG: 가격이 갭을 아래로 메웠는지
            if fvg.type == 'bullish':
                lowest_touch = recent_candles['low'].min()
                
                if lowest_touch <= fvg.bottom:
                    # 완전히 메워짐
                    fvg.fill_percentage = 1.0
                    fvg.is_filled = True
                    fvg.is_active = False
                    logger.info(f"💚 Bullish FVG 완전 메움: {fvg.symbol} @ {fvg.bottom:.2f}")
                
                elif lowest_touch < fvg.top:
                    # 부분적으로 메워짐
                    filled = (fvg.top - lowest_touch) / fvg.gap_size
                    fvg.fill_percentage = round(filled, 3)
                    
                    if fvg.fill_percentage >= self.fill_threshold:
                        fvg.is_active = False
                        logger.info(f"💚 Bullish FVG 부분 메움: {fvg.symbol} ({fvg.fill_percentage*100:.1f}%)")
            
            # Bearish FVG: 가격이 갭을 위로 메웠는지
            elif fvg.type == 'bearish':
                highest_touch = recent_candles['high'].max()
                
                if highest_touch >= fvg.top:
                    # 완전히 메워짐
                    fvg.fill_percentage = 1.0
                    fvg.is_filled = True
                    fvg.is_active = False
                    logger.info(f"❤️  Bearish FVG 완전 메움: {fvg.symbol} @ {fvg.top:.2f}")
                
                elif highest_touch > fvg.bottom:
                    # 부분적으로 메워짐
                    filled = (highest_touch - fvg.bottom) / fvg.gap_size
                    fvg.fill_percentage = round(filled, 3)
                    
                    if fvg.fill_percentage >= self.fill_threshold:
                        fvg.is_active = False
                        logger.info(f"❤️  Bearish FVG 부분 메움: {fvg.symbol} ({fvg.fill_percentage*100:.1f}%)")
        
        return fvgs
    
    def filter_active(self, fvgs: List[FairValueGap]) -> List[FairValueGap]:
        """활성 FVG만 필터링"""
        return [fvg for fvg in fvgs if fvg.is_active and not fvg.is_filled]
    
    def get_nearest(
        self, 
        fvgs: List[FairValueGap], 
        current_price: float, 
        fvg_type: Optional[str] = None
    ) -> Optional[FairValueGap]:
        """
        현재가에서 가장 가까운 FVG
        
        Args:
            fvgs: FVG 리스트
            current_price: 현재가
            fvg_type: 'bullish' or 'bearish'
        
        Returns:
            가장 가까운 FVG
        """
        active_fvgs = self.filter_active(fvgs)
        
        if fvg_type:
            active_fvgs = [fvg for fvg in active_fvgs if fvg.type == fvg_type]
        
        if not active_fvgs:
            return None
        
        # 현재가와의 거리 계산
        for fvg in active_fvgs:
            # FVG 중심까지의 거리
            fvg_center = (fvg.top + fvg.bottom) / 2
            fvg.distance = abs(current_price - fvg_center)
        
        # 가장 가까운 것
        nearest = min(active_fvgs, key=lambda x: x.distance)
        
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
    print("Fair Value Gap 탐지 테스트")
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
    
    # FVG 탐지
    detector = FVGDetector(min_gap_pct=0.001)
    fvgs = detector.detect(df)
    
    print()
    print(f"탐지된 FVG: {len(fvgs)}개")
    
    # 메움 업데이트
    fvgs = detector.update_fills(fvgs, df)
    
    # 통계
    bullish_fvgs = [f for f in fvgs if f.type == 'bullish']
    bearish_fvgs = [f for f in fvgs if f.type == 'bearish']
    filled_fvgs = [f for f in fvgs if f.is_filled]
    active_fvgs = detector.filter_active(fvgs)
    
    print()
    print(f"Bullish: {len(bullish_fvgs)}개")
    print(f"Bearish: {len(bearish_fvgs)}개")
    print(f"메워짐: {len(filled_fvgs)}개")
    print(f"활성: {len(active_fvgs)}개")
    print()
    
    # 상위 5개 (강도 순)
    if active_fvgs:
        top_fvgs = sorted(active_fvgs, key=lambda x: x.strength, reverse=True)[:5]
        
        print("강도 Top 5:")
        print("-" * 70)
        for i, fvg in enumerate(top_fvgs, 1):
            print(f"{i}. [{fvg.type.upper()}] {fvg.symbol}")
            print(f"   갭: {fvg.bottom:.2f} - {fvg.top:.2f} (크기: {fvg.gap_size:.2f})")
            print(f"   시간: {fvg.end_time}")
            print(f"   강도: {fvg.strength:.3f}")
            print(f"   메움: {fvg.fill_percentage*100:.1f}%")
            print()
