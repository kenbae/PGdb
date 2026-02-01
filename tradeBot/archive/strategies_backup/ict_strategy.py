# -*- coding: utf-8 -*-
"""
ICT 전략 (Order Block + Fair Value Gap) - Enhanced Version
- Order Block 탐지 및 활용
- Fair Value Gap 탐지 및 활용
- Confluence 기반 신호 생성
- 볼륨/RSI 필터 추가 (Keltner ICT Turtle 방식)
- 3단계 익절 (TP1, TP2, TP3)
- Keltner Channel 추세 반전 청산
- 선택적 AI 검증 (StrategyManager에서 처리)
"""

import logging
from typing import Dict, List, Optional
import pandas as pd
import numpy as np
from datetime import datetime

from .base import BaseStrategy, TradeSignal
from indicators.order_blocks import OrderBlockDetector
from indicators.fair_value_gaps import FVGDetector

logger = logging.getLogger(__name__)


class ICTStrategy(BaseStrategy):
    """
    ICT 전략 (Enhanced)

    조건:
    1. Order Block (OB) 탐지 - 가격 반전 영역
    2. Fair Value Gap (FVG) 탐지 - 가격 불균형 영역
    3. Confluence 확인 (OB + FVG 동시 존재)
    4. 볼륨 필터 (볼륨 > MA × 1.3)
    5. RSI 필터 (Long: <70, Short: >30)
    6. ATR 기반 SL/TP 계산 (피보나치 비율 적용: 1.618, 2.618, 4.236)
    7. Keltner Channel 기반 추세 반전 감지
    """

    def __init__(self, name: str, config: Dict):
        """
        초기화

        Args:
            name: 전략 이름
            config: 전략 설정
                - timeframe: 분석 타임프레임 (기본 15m)
                - ob_lookback: Order Block 탐지 범위 (기본 20)
                - fvg_min_gap_pct: FVG 최소 갭 비율 (기본 0.001)
                - min_confidence: 최소 신뢰도 (기본 0.65)
                - min_risk_reward: 최소 R/R 비율 (기본 1.5)
                - atr_multiplier: ATR 배수 (기본 2.0)
                - proximity_pct: 가격 근접 비율 (기본 0.01 = 1%)
                - use_volume_filter: 볼륨 필터 사용 여부 (기본 True)
                - use_rsi_filter: RSI 필터 사용 여부 (기본 True)
                - volume_multiplier: 볼륨 배수 (기본 1.3)
                - rsi_overbought: RSI 과매수 임계값 (기본 70)
                - rsi_oversold: RSI 과매도 임계값 (기본 30)
                - keltner_multiplier: Keltner Channel ATR 배수 (기본 2.5)
        """
        super().__init__(name, config)

        # ICT 탐지기 설정
        self.ob_lookback = config.get('ob_lookback', 20)
        self.fvg_min_gap_pct = config.get('fvg_min_gap_pct', 0.001)
        self.min_risk_reward = config.get('min_risk_reward', 1.5)
        self.proximity_pct = config.get('proximity_pct', 0.01)

        # 필터 설정 (Keltner ICT Turtle에서 가져옴)
        self.use_volume_filter = config.get('use_volume_filter', True)
        self.use_rsi_filter = config.get('use_rsi_filter', True)
        self.volume_multiplier = config.get('volume_multiplier', 1.3)
        self.rsi_overbought = config.get('rsi_overbought', 70)
        self.rsi_oversold = config.get('rsi_oversold', 30)
        self.keltner_multiplier = config.get('keltner_multiplier', 2.5)

        # 탐지기 초기화
        self.ob_detector = OrderBlockDetector(lookback=self.ob_lookback)
        self.fvg_detector = FVGDetector(min_gap_pct=self.fvg_min_gap_pct)

        logger.info(f"ICTStrategy Enhanced 초기화: OB={self.ob_lookback}, FVG={self.fvg_min_gap_pct}, "
                   f"Vol={self.use_volume_filter}, RSI={self.use_rsi_filter}")

    def get_required_indicators(self) -> List[str]:
        """필요한 지표 목록 (OB/FVG는 자체 계산)"""
        return ['atr', 'rsi', 'volume_ma', 'keltner_upper', 'keltner_lower', 'ema20']

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        필요한 지표 계산

        Args:
            df: OHLCV 데이터프레임

        Returns:
            pd.DataFrame: 지표가 추가된 데이터프레임
        """
        df = df.copy()

        # EMA 20 (Keltner 중심선)
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()

        # ATR
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = true_range.rolling(window=14).mean()

        # Keltner Channel
        df['keltner_upper'] = df['ema20'] + (self.keltner_multiplier * df['atr'])
        df['keltner_lower'] = df['ema20'] - (self.keltner_multiplier * df['atr'])

        # RSI
        delta = df['close'].diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
        avg_loss = loss.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
        rs = avg_gain / avg_loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # Volume MA
        df['volume_ma'] = df['volume'].rolling(window=20).mean()

        return df

    def _check_volume_filter(self, df: pd.DataFrame) -> bool:
        """볼륨 필터 확인"""
        if not self.use_volume_filter:
            return True

        last = df.iloc[-1]
        if pd.isna(last['volume_ma']) or last['volume_ma'] == 0:
            return True

        return last['volume'] > last['volume_ma'] * self.volume_multiplier

    def _check_rsi_filter(self, df: pd.DataFrame, direction: str) -> bool:
        """RSI 필터 확인"""
        if not self.use_rsi_filter:
            return True

        last = df.iloc[-1]
        if pd.isna(last['rsi']):
            return True

        rsi = last['rsi']

        if direction == 'buy':
            # 매수: 과매수 영역 아님
            return rsi < self.rsi_overbought
        else:
            # 매도: 과매도 영역 아님
            return rsi > self.rsi_oversold

    def analyze(self, symbol: str, timeframe: str, df: pd.DataFrame) -> List[TradeSignal]:
        """
        신호 분석

        Args:
            symbol: 거래 심볼
            timeframe: 타임프레임
            df: OHLCV 데이터프레임

        Returns:
            List[TradeSignal]: 생성된 신호 목록
        """
        signals = []

        # 데이터 유효성 검사
        if not self.validate_dataframe(df, min_rows=50):
            return signals

        try:
            # 지표 계산
            df = self._calculate_indicators(df)

            # 1. Order Blocks 탐지
            obs = self.ob_detector.detect(df)
            obs = self.ob_detector.check_touches(obs, df)
            active_obs = self.ob_detector.filter_active(obs)

            # 2. Fair Value Gaps 탐지
            fvgs = self.fvg_detector.detect(df)
            fvgs = self.fvg_detector.update_fills(fvgs, df)
            active_fvgs = self.fvg_detector.filter_active(fvgs)

            logger.debug(f"📊 {symbol} {timeframe}: OB {len(active_obs)}개, FVG {len(active_fvgs)}개")

            # 3. 현재가 및 ATR
            current_price = float(df.iloc[-1]['close'])
            atr = float(df.iloc[-1]['atr']) if not pd.isna(df.iloc[-1]['atr']) else self.calculate_atr(df)

            # 4. 매수 신호 확인
            buy_signal = self._check_buy_signal(
                symbol, timeframe, current_price,
                active_obs, active_fvgs, atr, df
            )
            if buy_signal:
                signals.append(buy_signal)

            # 5. 매도 신호 확인
            sell_signal = self._check_sell_signal(
                symbol, timeframe, current_price,
                active_obs, active_fvgs, atr, df
            )
            if sell_signal:
                signals.append(sell_signal)

        except Exception as e:
            logger.error(f"❌ {symbol} {timeframe} ICT 분석 실패: {e}")

        return signals

    def _check_buy_signal(
        self,
        symbol: str,
        timeframe: str,
        current_price: float,
        obs: List,
        fvgs: List,
        atr: float,
        df: pd.DataFrame
    ) -> Optional[TradeSignal]:
        """
        매수 신호 확인

        Args:
            symbol: 심볼
            timeframe: 타임프레임
            current_price: 현재가
            obs: 활성 Order Blocks
            fvgs: 활성 FVGs
            atr: ATR 값
            df: 지표가 포함된 데이터프레임

        Returns:
            Optional[TradeSignal]: 조건 충족 시 신호, 아니면 None
        """
        # 볼륨 필터 확인
        if not self._check_volume_filter(df):
            logger.debug(f"{symbol} 매수: 볼륨 필터 미충족")
            return None

        # RSI 필터 확인
        if not self._check_rsi_filter(df, 'buy'):
            logger.debug(f"{symbol} 매수: RSI 과매수 영역")
            return None

        # Bullish OB/FVG 필터
        bullish_obs = [ob for ob in obs if ob.type == 'bullish']
        bullish_fvgs = [fvg for fvg in fvgs if fvg.type == 'bullish']

        if not bullish_obs and not bullish_fvgs:
            return None

        # 가장 가까운 것 찾기
        nearest_ob = self.ob_detector.get_nearest(bullish_obs, current_price, 'bullish') if bullish_obs else None
        nearest_fvg = self.fvg_detector.get_nearest(bullish_fvgs, current_price, 'bullish') if bullish_fvgs else None

        # Confluence 확인
        confluence = []
        reasons = []
        support_level = None

        if nearest_ob:
            distance_pct = abs(current_price - nearest_ob.bottom) / current_price
            if distance_pct < self.proximity_pct:
                confluence.append('bullish_ob')
                reasons.append(f"Bullish OB @ {nearest_ob.bottom:.4f}")
                support_level = nearest_ob.bottom

        if nearest_fvg:
            fvg_center = (nearest_fvg.top + nearest_fvg.bottom) / 2
            distance_pct = abs(current_price - fvg_center) / current_price
            if distance_pct < self.proximity_pct:
                confluence.append('bullish_fvg')
                reasons.append(f"Bullish FVG @ {fvg_center:.4f}")
                if support_level:
                    confluence.append('confluence')
                    reasons.append("OB + FVG Confluence")
                else:
                    support_level = fvg_center

        if not confluence:
            return None

        # 필터 적용 사유 추가
        if self.use_volume_filter:
            reasons.append("Volume confirmed")
        if self.use_rsi_filter:
            rsi_val = df.iloc[-1]['rsi']
            reasons.append(f"RSI={rsi_val:.1f}")

        # 진입/손절/익절 계산 (피보나치 비율 - 3단계)
        entry_price = current_price
        stop_loss = (support_level if support_level else current_price) - (atr * self.atr_multiplier)

        risk = entry_price - stop_loss
        if risk <= 0:
            return None

        tp1 = entry_price + (risk * 1.618)  # 피보나치 확장
        tp2 = entry_price + (risk * 2.618)
        tp3 = entry_price + (risk * 4.236)  # TP3 추가

        # 가격 정밀도 유지 (8자리)
        stop_loss = round(stop_loss, 8)
        tp1 = round(tp1, 8)
        tp2 = round(tp2, 8)
        tp3 = round(tp3, 8)

        # 신뢰도 계산
        confidence = self._calculate_confidence(confluence, nearest_ob, nearest_fvg, df)

        # Risk/Reward 계산
        risk_reward = (tp1 - entry_price) / risk

        # 필터링
        if confidence < self.min_confidence or risk_reward < self.min_risk_reward:
            logger.debug(f"{symbol} 매수 신호: 조건 미충족 (conf={confidence:.2f}, rr={risk_reward:.2f})")
            return None

        # OB/FVG 메타데이터 준비
        last = df.iloc[-1]
        metadata = {
            'confluence': confluence,
            'order_block': self._serialize_ob(nearest_ob) if nearest_ob else None,
            'fvg': self._serialize_fvg(nearest_fvg) if nearest_fvg else None,
            'support_level': support_level,
            'atr': float(atr),
            'rsi': float(last['rsi']) if not pd.isna(last['rsi']) else None,
            'volume_ratio': float(last['volume'] / last['volume_ma']) if last['volume_ma'] > 0 else None,
            'keltner_upper': float(last['keltner_upper']) if not pd.isna(last['keltner_upper']) else None,
            'keltner_lower': float(last['keltner_lower']) if not pd.isna(last['keltner_lower']) else None,
            'take_profit_3': float(tp3)  # TP3 추가
        }

        # 신호 생성
        return self.create_signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type='buy',
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            confidence=confidence,
            reasons=reasons,
            metadata=metadata
        )

    def _check_sell_signal(
        self,
        symbol: str,
        timeframe: str,
        current_price: float,
        obs: List,
        fvgs: List,
        atr: float,
        df: pd.DataFrame
    ) -> Optional[TradeSignal]:
        """
        매도 신호 확인

        Args:
            symbol: 심볼
            timeframe: 타임프레임
            current_price: 현재가
            obs: 활성 Order Blocks
            fvgs: 활성 FVGs
            atr: ATR 값
            df: 지표가 포함된 데이터프레임

        Returns:
            Optional[TradeSignal]: 조건 충족 시 신호, 아니면 None
        """
        # 볼륨 필터 확인
        if not self._check_volume_filter(df):
            logger.debug(f"{symbol} 매도: 볼륨 필터 미충족")
            return None

        # RSI 필터 확인
        if not self._check_rsi_filter(df, 'sell'):
            logger.debug(f"{symbol} 매도: RSI 과매도 영역")
            return None

        # Bearish OB/FVG 필터
        bearish_obs = [ob for ob in obs if ob.type == 'bearish']
        bearish_fvgs = [fvg for fvg in fvgs if fvg.type == 'bearish']

        if not bearish_obs and not bearish_fvgs:
            return None

        # 가장 가까운 것 찾기
        nearest_ob = self.ob_detector.get_nearest(bearish_obs, current_price, 'bearish') if bearish_obs else None
        nearest_fvg = self.fvg_detector.get_nearest(bearish_fvgs, current_price, 'bearish') if bearish_fvgs else None

        # Confluence 확인
        confluence = []
        reasons = []
        resistance_level = None

        if nearest_ob:
            distance_pct = abs(current_price - nearest_ob.top) / current_price
            if distance_pct < self.proximity_pct:
                confluence.append('bearish_ob')
                reasons.append(f"Bearish OB @ {nearest_ob.top:.4f}")
                resistance_level = nearest_ob.top

        if nearest_fvg:
            fvg_center = (nearest_fvg.top + nearest_fvg.bottom) / 2
            distance_pct = abs(current_price - fvg_center) / current_price
            if distance_pct < self.proximity_pct:
                confluence.append('bearish_fvg')
                reasons.append(f"Bearish FVG @ {fvg_center:.4f}")
                if resistance_level:
                    confluence.append('confluence')
                    reasons.append("OB + FVG Confluence")
                else:
                    resistance_level = fvg_center

        if not confluence:
            return None

        # 필터 적용 사유 추가
        if self.use_volume_filter:
            reasons.append("Volume confirmed")
        if self.use_rsi_filter:
            rsi_val = df.iloc[-1]['rsi']
            reasons.append(f"RSI={rsi_val:.1f}")

        # 진입/손절/익절 계산 (피보나치 비율 - 3단계)
        entry_price = current_price
        stop_loss = (resistance_level if resistance_level else current_price) + (atr * self.atr_multiplier)

        risk = stop_loss - entry_price
        if risk <= 0:
            return None

        tp1 = entry_price - (risk * 1.618)
        tp2 = entry_price - (risk * 2.618)
        tp3 = entry_price - (risk * 4.236)  # TP3 추가

        # 가격 정밀도 유지 (8자리)
        stop_loss = round(stop_loss, 8)
        tp1 = round(tp1, 8)
        tp2 = round(tp2, 8)
        tp3 = round(tp3, 8)

        # 신뢰도 계산
        confidence = self._calculate_confidence(confluence, nearest_ob, nearest_fvg, df)

        # Risk/Reward 계산
        risk_reward = (entry_price - tp1) / risk

        # 필터링
        if confidence < self.min_confidence or risk_reward < self.min_risk_reward:
            logger.debug(f"{symbol} 매도 신호: 조건 미충족 (conf={confidence:.2f}, rr={risk_reward:.2f})")
            return None

        # OB/FVG 메타데이터 준비
        last = df.iloc[-1]
        metadata = {
            'confluence': confluence,
            'order_block': self._serialize_ob(nearest_ob) if nearest_ob else None,
            'fvg': self._serialize_fvg(nearest_fvg) if nearest_fvg else None,
            'resistance_level': resistance_level,
            'atr': float(atr),
            'rsi': float(last['rsi']) if not pd.isna(last['rsi']) else None,
            'volume_ratio': float(last['volume'] / last['volume_ma']) if last['volume_ma'] > 0 else None,
            'keltner_upper': float(last['keltner_upper']) if not pd.isna(last['keltner_upper']) else None,
            'keltner_lower': float(last['keltner_lower']) if not pd.isna(last['keltner_lower']) else None,
            'take_profit_3': float(tp3)  # TP3 추가
        }

        # 신호 생성
        return self.create_signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type='sell',
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            confidence=confidence,
            reasons=reasons,
            metadata=metadata
        )

    def _calculate_confidence(self, confluence: List[str], ob, fvg, df: pd.DataFrame = None) -> float:
        """
        신뢰도 계산 (Enhanced)

        Args:
            confluence: Confluence 목록
            ob: Order Block 객체
            fvg: FVG 객체
            df: 지표가 포함된 데이터프레임

        Returns:
            float: 신뢰도 (0.0 ~ 1.0)
        """
        confidence = 0.5  # 기본 신뢰도

        # Confluence 보너스
        if 'confluence' in confluence:
            confidence += 0.25  # OB + FVG 동시 존재
        elif len(confluence) == 1:
            confidence += 0.1  # 단일 신호

        # OB 강도 반영
        if ob and hasattr(ob, 'strength'):
            confidence += ob.strength * 0.15

        # FVG 강도 반영
        if fvg and hasattr(fvg, 'strength'):
            confidence += fvg.strength * 0.15

        # 볼륨 확인 보너스
        if df is not None and self.use_volume_filter:
            last = df.iloc[-1]
            if not pd.isna(last['volume_ma']) and last['volume_ma'] > 0:
                vol_ratio = last['volume'] / last['volume_ma']
                if vol_ratio > 2.0:
                    confidence += 0.1  # 강한 볼륨
                elif vol_ratio > 1.5:
                    confidence += 0.05  # 중간 볼륨

        # RSI 확인 보너스
        if df is not None and self.use_rsi_filter:
            last = df.iloc[-1]
            if not pd.isna(last['rsi']):
                rsi = last['rsi']
                # 중립 영역 (40-60)에 있으면 보너스
                if 40 <= rsi <= 60:
                    confidence += 0.05

        return min(round(confidence, 3), 1.0)

    def _serialize_ob(self, ob) -> Optional[Dict]:
        """Order Block 직렬화"""
        if ob is None:
            return None
        return {
            'type': ob.type,
            'top': float(ob.top),
            'bottom': float(ob.bottom),
            'strength': float(ob.strength) if hasattr(ob, 'strength') else 0.5,
            'index': int(ob.index) if hasattr(ob, 'index') else 0
        }

    def _serialize_fvg(self, fvg) -> Optional[Dict]:
        """FVG 직렬화"""
        if fvg is None:
            return None
        return {
            'type': fvg.type,
            'top': float(fvg.top),
            'bottom': float(fvg.bottom),
            'strength': float(fvg.strength) if hasattr(fvg, 'strength') else 0.5,
            'index': int(fvg.index) if hasattr(fvg, 'index') else 0
        }
