"""
ICT 통합 전략

Order Blocks + Fair Value Gaps 결합
→ 고확률 매매 신호 생성
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import logging

from order_blocks import OrderBlockDetector, OrderBlock
from fair_value_gaps import FVGDetector, FairValueGap

logger = logging.getLogger(__name__)


@dataclass
class ICTSignal:
    """ICT 신호"""
    symbol: str
    timeframe: str
    signal_type: str  # 'buy' or 'sell'
    
    # 가격 정보
    current_price: float
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    
    # 근거
    order_block: Optional[OrderBlock]
    fvg: Optional[FairValueGap]
    confluence: List[str]  # 합치는 요소들
    
    # 신호 품질
    confidence: float  # 신뢰도 (0-1)
    risk_reward: float  # 리스크/보상 비율
    
    # 메타
    created_at: datetime
    is_active: bool = True


class ICTStrategy:
    """
    ICT 통합 전략
    
    Features:
    - Order Blocks + FVG 분석
    - Confluence (합치) 확인
    - 진입/손절/익절 계산
    - 신호 품질 평가
    """
    
    def __init__(
        self,
        ob_detector: OrderBlockDetector = None,
        fvg_detector: FVGDetector = None,
        min_confidence: float = 0.65,
        min_risk_reward: float = 1.5,
        atr_multiplier: float = 2.0
    ):
        """
        초기화
        
        Args:
            ob_detector: Order Block 탐지기
            fvg_detector: FVG 탐지기
            min_confidence: 최소 신뢰도
            min_risk_reward: 최소 리스크/보상 비율
            atr_multiplier: ATR 배수 (손절 거리)
        """
        self.ob_detector = ob_detector or OrderBlockDetector()
        self.fvg_detector = fvg_detector or FVGDetector()
        
        self.min_confidence = min_confidence
        self.min_risk_reward = min_risk_reward
        self.atr_multiplier = atr_multiplier
        
        logger.info(f"✅ ICTStrategy 초기화")
        logger.info(f"   min_confidence: {min_confidence}")
        logger.info(f"   min_risk_reward: {min_risk_reward}")
    
    def analyze(self, df: pd.DataFrame) -> List[ICTSignal]:
        """
        ICT 분석 및 신호 생성
        
        Args:
            df: OHLCV DataFrame
        
        Returns:
            신호 리스트
        """
        if len(df) < 50:
            logger.warning(f"⚠️  데이터 부족: {len(df)}")
            return []
        
        # 1. Order Blocks 탐지
        obs = self.ob_detector.detect(df)
        obs = self.ob_detector.check_touches(obs, df)
        active_obs = self.ob_detector.filter_active(obs)
        
        # 2. FVG 탐지
        fvgs = self.fvg_detector.detect(df)
        fvgs = self.fvg_detector.update_fills(fvgs, df)
        active_fvgs = self.fvg_detector.filter_active(fvgs)
        
        logger.info(f"📊 활성 OB: {len(active_obs)}개, 활성 FVG: {len(active_fvgs)}개")
        
        # 3. 현재 상태
        current_price = float(df.iloc[-1]['close'])
        
        # 4. ATR 계산 (손절 거리용)
        atr = self._calculate_atr(df, period=14)
        
        # 5. 신호 생성
        signals = []
        
        # 매수 신호
        buy_signal = self._check_buy_signal(
            current_price, active_obs, active_fvgs, atr, df
        )
        if buy_signal:
            signals.append(buy_signal)
        
        # 매도 신호
        sell_signal = self._check_sell_signal(
            current_price, active_obs, active_fvgs, atr, df
        )
        if sell_signal:
            signals.append(sell_signal)
        
        # 6. 필터링 (최소 신뢰도, R/R)
        signals = [
            s for s in signals 
            if s.confidence >= self.min_confidence 
            and s.risk_reward >= self.min_risk_reward
        ]
        
        if signals:
            logger.info(f"🎯 신호 생성: {len(signals)}개")
        
        return signals
    
    def _check_buy_signal(
        self,
        current_price: float,
        obs: List[OrderBlock],
        fvgs: List[FairValueGap],
        atr: float,
        df: pd.DataFrame
    ) -> Optional[ICTSignal]:
        """
        매수 신호 확인
        
        조건:
        1. Bullish OB 근처
        2. Bullish FVG 근처
        3. Confluence (둘 다 겹침)
        """
        # Bullish OB
        bullish_obs = [ob for ob in obs if ob.type == 'bullish']
        
        # Bullish FVG
        bullish_fvgs = [fvg for fvg in fvgs if fvg.type == 'bullish']
        
        if not bullish_obs and not bullish_fvgs:
            return None
        
        # 가장 가까운 것들
        nearest_ob = self.ob_detector.get_nearest(bullish_obs, current_price, 'bullish') if bullish_obs else None
        nearest_fvg = self.fvg_detector.get_nearest(bullish_fvgs, current_price, 'bullish') if bullish_fvgs else None
        
        # Confluence 확인
        confluence = []
        support_level = None
        
        # OB 근처? (1% 이내)
        if nearest_ob:
            distance_pct = abs(current_price - nearest_ob.bottom) / current_price
            if distance_pct < 0.01:
                confluence.append('bullish_ob')
                support_level = nearest_ob.bottom
        
        # FVG 근처? (1% 이내)
        if nearest_fvg:
            fvg_center = (nearest_fvg.top + nearest_fvg.bottom) / 2
            distance_pct = abs(current_price - fvg_center) / current_price
            if distance_pct < 0.01:
                confluence.append('bullish_fvg')
                if support_level:
                    # 두 개가 겹침!
                    confluence.append('confluence')
                else:
                    support_level = fvg_center
        
        # 신호 없음
        if not confluence:
            return None
        
        # 진입/손절/익절 계산
        entry_price = current_price
        stop_loss = support_level - (atr * self.atr_multiplier) if support_level else current_price - (atr * self.atr_multiplier)
        
        # 익절: Fibonacci 레벨
        risk = entry_price - stop_loss
        take_profit_1 = entry_price + (risk * 1.618)  # 1.618 R
        take_profit_2 = entry_price + (risk * 2.618)  # 2.618 R
        
        # 신뢰도 계산
        confidence = self._calculate_confidence(
            confluence=confluence,
            ob=nearest_ob,
            fvg=nearest_fvg
        )
        
        # R/R 계산
        risk_reward = (take_profit_1 - entry_price) / (entry_price - stop_loss)
        
        # 신호 생성
        signal = ICTSignal(
            symbol=df.iloc[0].get('symbol', 'UNKNOWN'),
            timeframe=df.iloc[0].get('tf', '1m'),
            signal_type='buy',
            current_price=current_price,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            order_block=nearest_ob,
            fvg=nearest_fvg,
            confluence=confluence,
            confidence=confidence,
            risk_reward=risk_reward,
            created_at=datetime.now(),
            is_active=True
        )
        
        logger.info(f"💚 매수 신호: {signal.symbol} @ {entry_price:.2f}")
        logger.info(f"   신뢰도: {confidence:.2%}")
        logger.info(f"   R/R: {risk_reward:.2f}")
        logger.info(f"   Confluence: {', '.join(confluence)}")
        
        return signal
    
    def _check_sell_signal(
        self,
        current_price: float,
        obs: List[OrderBlock],
        fvgs: List[FairValueGap],
        atr: float,
        df: pd.DataFrame
    ) -> Optional[ICTSignal]:
        """
        매도 신호 확인
        
        조건:
        1. Bearish OB 근처
        2. Bearish FVG 근처
        3. Confluence (둘 다 겹침)
        """
        # Bearish OB
        bearish_obs = [ob for ob in obs if ob.type == 'bearish']
        
        # Bearish FVG
        bearish_fvgs = [fvg for fvg in fvgs if fvg.type == 'bearish']
        
        if not bearish_obs and not bearish_fvgs:
            return None
        
        # 가장 가까운 것들
        nearest_ob = self.ob_detector.get_nearest(bearish_obs, current_price, 'bearish') if bearish_obs else None
        nearest_fvg = self.fvg_detector.get_nearest(bearish_fvgs, current_price, 'bearish') if bearish_fvgs else None
        
        # Confluence 확인
        confluence = []
        resistance_level = None
        
        # OB 근처? (1% 이내)
        if nearest_ob:
            distance_pct = abs(current_price - nearest_ob.top) / current_price
            if distance_pct < 0.01:
                confluence.append('bearish_ob')
                resistance_level = nearest_ob.top
        
        # FVG 근처? (1% 이내)
        if nearest_fvg:
            fvg_center = (nearest_fvg.top + nearest_fvg.bottom) / 2
            distance_pct = abs(current_price - fvg_center) / current_price
            if distance_pct < 0.01:
                confluence.append('bearish_fvg')
                if resistance_level:
                    confluence.append('confluence')
                else:
                    resistance_level = fvg_center
        
        # 신호 없음
        if not confluence:
            return None
        
        # 진입/손절/익절 계산
        entry_price = current_price
        stop_loss = resistance_level + (atr * self.atr_multiplier) if resistance_level else current_price + (atr * self.atr_multiplier)
        
        # 익절
        risk = stop_loss - entry_price
        take_profit_1 = entry_price - (risk * 1.618)
        take_profit_2 = entry_price - (risk * 2.618)
        
        # 신뢰도
        confidence = self._calculate_confidence(
            confluence=confluence,
            ob=nearest_ob,
            fvg=nearest_fvg
        )
        
        # R/R
        risk_reward = (entry_price - take_profit_1) / (stop_loss - entry_price)
        
        # 신호
        signal = ICTSignal(
            symbol=df.iloc[0].get('symbol', 'UNKNOWN'),
            timeframe=df.iloc[0].get('tf', '1m'),
            signal_type='sell',
            current_price=current_price,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            order_block=nearest_ob,
            fvg=nearest_fvg,
            confluence=confluence,
            confidence=confidence,
            risk_reward=risk_reward,
            created_at=datetime.now(),
            is_active=True
        )
        
        logger.info(f"❤️  매도 신호: {signal.symbol} @ {entry_price:.2f}")
        logger.info(f"   신뢰도: {confidence:.2%}")
        logger.info(f"   R/R: {risk_reward:.2f}")
        logger.info(f"   Confluence: {', '.join(confluence)}")
        
        return signal
    
    def _calculate_confidence(
        self,
        confluence: List[str],
        ob: Optional[OrderBlock],
        fvg: Optional[FairValueGap]
    ) -> float:
        """
        신뢰도 계산
        
        Args:
            confluence: Confluence 리스트
            ob: Order Block
            fvg: Fair Value Gap
        
        Returns:
            신뢰도 (0-1)
        """
        confidence = 0.5  # 기본
        
        # 1. Confluence (40%)
        if 'confluence' in confluence:
            confidence += 0.4  # OB + FVG 겹침
        elif len(confluence) == 1:
            confidence += 0.2  # 하나만
        
        # 2. OB 강도 (30%)
        if ob:
            confidence += ob.strength * 0.3
        
        # 3. FVG 강도 (30%)
        if fvg:
            confidence += fvg.strength * 0.3
        
        return min(round(confidence, 3), 1.0)
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """ATR 계산"""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        
        return float(atr)


# 테스트
if __name__ == "__main__":
    import sys
    import os
    
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    import psycopg2
    from core.config_loader import get_config
    
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 70)
    print("ICT 통합 전략 테스트")
    print("=" * 70)
    print()
    
    # Config
    config = get_config()
    
    # DB
    conn = psycopg2.connect(
        host=config.get('db.host'),
        port=config.get('db.port'),
        dbname=config.get('db.name'),
        user=config.get('db.user'),
        password=config.get('db.password')
    )
    
    # 데이터
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
    print(f"현재가: ${df.iloc[-1]['close']:,.2f}")
    print()
    
    # 전략
    strategy = ICTStrategy(
        min_confidence=0.65,
        min_risk_reward=1.5
    )
    
    # 분석
    signals = strategy.analyze(df)
    
    print()
    print("=" * 70)
    print(f"🎯 신호: {len(signals)}개")
    print("=" * 70)
    print()
    
    if signals:
        for i, signal in enumerate(signals, 1):
            print(f"{i}. [{signal.signal_type.upper()}] {signal.symbol}")
            print(f"   진입: ${signal.entry_price:,.2f}")
            print(f"   손절: ${signal.stop_loss:,.2f}")
            print(f"   익절1: ${signal.take_profit_1:,.2f}")
            print(f"   익절2: ${signal.take_profit_2:,.2f}")
            print(f"   신뢰도: {signal.confidence:.2%}")
            print(f"   R/R: {signal.risk_reward:.2f}")
            print(f"   Confluence: {', '.join(signal.confluence)}")
            print()
    else:
        print("신호 없음")
