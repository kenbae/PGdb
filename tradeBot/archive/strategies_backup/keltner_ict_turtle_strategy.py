# -*- coding: utf-8 -*-
"""
Keltner ICT Turtle 전략

현대화된 터틀 트레이딩 전략:
- 켈트너 채널 (동적)
- ICT Order Blocks
- Fair Value Gaps
- 피보나치 익절
- 다단계 포지션 관리
"""

import logging
from typing import Dict, List, Optional
import pandas as pd
import numpy as np
from datetime import datetime

from .base import BaseStrategy, TradeSignal
from indicators.technical import TechnicalIndicators

logger = logging.getLogger(__name__)


class KeltnerICTTurtleStrategy(BaseStrategy):
    """
    Keltner ICT Turtle 전략

    조건:
    1. 켈트너 채널 돌파 (상단/하단)
    2. 돈치안 채널 돌파 (터틀 원본)
    3. ICT Order Blocks 확인 (선택적 강화)
    4. Fair Value Gaps 확인 (선택적 강화)
    5. 볼륨 확인
    6. RSI 필터 (과매수/과매도 회피)
    """

    def __init__(self, name: str, config: Dict):
        """
        초기화

        Args:
            name: 전략 이름
            config: 전략 설정
                - timeframe: 분석 타임프레임 (기본 1h)
                - ema_period: EMA 기간 (기본 20)
                - atr_period: ATR 기간 (기본 20)
                - atr_multiplier: ATR 배수 (기본 2.5)
                - donchian_period: 돈치안 채널 기간 (기본 55)
                - ob_lookback: Order Block 탐색 기간 (기본 10)
                - fvg_min_gap: FVG 최소 갭 비율 (기본 0.001)
                - volume_multiplier: 볼륨 확인 배수 (기본 1.3)
                - require_ict: ICT 조건 필수 여부 (기본 False)
        """
        super().__init__(name, config)

        # 켈트너 채널 파라미터
        self.ema_period = config.get('ema_period', 20)
        self.atr_period = config.get('atr_period', 20)
        self.keltner_multiplier = config.get('keltner_multiplier', 2.5)

        # 돈치안 채널 (터틀 원본)
        self.donchian_period = config.get('donchian_period', 55)

        # ICT 파라미터
        self.ob_lookback = config.get('ob_lookback', 10)
        self.fvg_min_gap = config.get('fvg_min_gap', 0.001)

        # 필터
        self.volume_multiplier = config.get('volume_multiplier', 1.3)
        self.require_ict = config.get('require_ict', False)

        logger.info(
            f"KeltnerICTTurtleStrategy 초기화: "
            f"ema={self.ema_period}, donchian={self.donchian_period}, "
            f"keltner_mult={self.keltner_multiplier}"
        )

    def get_required_indicators(self) -> List[str]:
        """필요한 지표 목록"""
        return ['ema20', 'atr', 'rsi', 'volume_ma']

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
        min_rows = max(self.donchian_period + 10, 70)
        if not self.validate_dataframe(df, min_rows=min_rows):
            return signals

        try:
            # 지표 계산
            df = self._calculate_indicators(df)

            # 최신 캔들 확인
            i = len(df) - 1
            current = df.iloc[i]
            prev = df.iloc[i - 1]

            # 지표 값 추출
            close = current['close']
            keltner_upper = current.get('keltner_upper')
            keltner_lower = current.get('keltner_lower')
            donchian_high = prev.get('donchian_high')  # 이전 캔들 기준
            donchian_low = prev.get('donchian_low')
            volume = current['volume']
            volume_ma = current.get('volume_ma')
            rsi = current.get('rsi')

            # 필수 값 검증
            if pd.isna(keltner_upper) or pd.isna(donchian_high):
                logger.warning(f"{symbol} 지표 값 부족")
                return signals

            # 현재 상태 로그
            logger.info(
                f"📊 {symbol} Keltner: 가격=${close:.2f} | "
                f"상단=${keltner_upper:.2f} | 하단=${keltner_lower:.2f} | "
                f"돈치안 상단=${donchian_high:.2f} | RSI={rsi:.1f if rsi else 'N/A'}"
            )

            # ICT 구조 확인
            has_bullish_structure = self._check_bullish_structure(df, i)
            has_bearish_structure = self._check_bearish_structure(df, i)

            # Long 신호 조건
            long_signal = self._evaluate_long_signal(
                symbol, timeframe, df, current, prev,
                has_bullish_structure
            )
            if long_signal:
                signals.append(long_signal)

            # Short 신호 조건
            short_signal = self._evaluate_short_signal(
                symbol, timeframe, df, current, prev,
                has_bearish_structure
            )
            if short_signal:
                signals.append(short_signal)

        except Exception as e:
            logger.error(f"❌ {symbol} {timeframe} Keltner ICT 분석 실패: {e}")
            import traceback
            logger.debug(traceback.format_exc())

        return signals

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """모든 지표 계산"""
        df = df.copy()

        # EMA (켈트너 중심선)
        df['ema'] = df['close'].ewm(span=self.ema_period, adjust=False).mean()

        # ATR
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=self.atr_period).mean()

        # 켈트너 채널
        df['keltner_upper'] = df['ema'] + (self.keltner_multiplier * df['atr'])
        df['keltner_lower'] = df['ema'] - (self.keltner_multiplier * df['atr'])

        # 돈치안 채널 (터틀 원본)
        df['donchian_high'] = df['high'].rolling(window=self.donchian_period).max()
        df['donchian_low'] = df['low'].rolling(window=self.donchian_period).min()

        # 볼륨 이동평균
        df['volume_ma'] = df['volume'].rolling(window=20).mean()

        # RSI
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # Order Blocks
        df['bullish_ob'], df['bearish_ob'] = self._identify_order_blocks(df)

        # Fair Value Gaps
        df['bullish_fvg'], df['bearish_fvg'] = self._identify_fvg(df)

        return df

    def _identify_order_blocks(self, df: pd.DataFrame) -> tuple:
        """Order Blocks 식별"""
        bullish_ob = pd.Series(False, index=df.index)
        bearish_ob = pd.Series(False, index=df.index)

        for i in range(self.ob_lookback, len(df) - 1):
            # Bullish OB: 강한 상승 직전의 마지막 하락 캔들
            if (df['close'].iloc[i] > df['open'].iloc[i] and
                df['close'].iloc[i - 1] < df['open'].iloc[i - 1] and
                i + 1 < len(df) and df['close'].iloc[i + 1] > df['close'].iloc[i]):
                bullish_ob.iloc[i - 1] = True

            # Bearish OB: 강한 하락 직전의 마지막 상승 캔들
            if (df['close'].iloc[i] < df['open'].iloc[i] and
                df['close'].iloc[i - 1] > df['open'].iloc[i - 1] and
                i + 1 < len(df) and df['close'].iloc[i + 1] < df['close'].iloc[i]):
                bearish_ob.iloc[i - 1] = True

        return bullish_ob, bearish_ob

    def _identify_fvg(self, df: pd.DataFrame) -> tuple:
        """Fair Value Gaps 식별"""
        bullish_fvg = pd.Series(False, index=df.index)
        bearish_fvg = pd.Series(False, index=df.index)

        for i in range(1, len(df) - 1):
            # Bullish FVG: 캔들 i-1의 high < 캔들 i+1의 low
            gap_size = (df['low'].iloc[i + 1] - df['high'].iloc[i - 1]) / df['close'].iloc[i]
            if gap_size > self.fvg_min_gap:
                bullish_fvg.iloc[i] = True

            # Bearish FVG: 캔들 i-1의 low > 캔들 i+1의 high
            gap_size = (df['low'].iloc[i - 1] - df['high'].iloc[i + 1]) / df['close'].iloc[i]
            if gap_size > self.fvg_min_gap:
                bearish_fvg.iloc[i] = True

        return bullish_fvg, bearish_fvg

    def _check_bullish_structure(self, df: pd.DataFrame, idx: int) -> bool:
        """Bullish ICT 구조 확인"""
        start_idx = max(0, idx - self.ob_lookback)
        return (
            df['bullish_ob'].iloc[start_idx:idx].any() or
            df['bullish_fvg'].iloc[start_idx:idx].any()
        )

    def _check_bearish_structure(self, df: pd.DataFrame, idx: int) -> bool:
        """Bearish ICT 구조 확인"""
        start_idx = max(0, idx - self.ob_lookback)
        return (
            df['bearish_ob'].iloc[start_idx:idx].any() or
            df['bearish_fvg'].iloc[start_idx:idx].any()
        )

    def _evaluate_long_signal(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series,
        prev: pd.Series,
        has_bullish_structure: bool
    ) -> Optional[TradeSignal]:
        """Long 신호 평가"""
        score = 0.0
        reasons = []

        close = current['close']
        keltner_upper = current['keltner_upper']
        donchian_high = prev['donchian_high']
        volume = current['volume']
        volume_ma = current['volume_ma']
        rsi = current.get('rsi', 50)

        # 1. 켈트너 상단 돌파 (+30점)
        if close > keltner_upper:
            score += 0.3
            reasons.append(f"켈트너 상단 돌파 (${close:.2f} > ${keltner_upper:.2f})")
        else:
            return None  # 필수 조건

        # 2. 돈치안 채널 돌파 (+25점)
        if close > donchian_high:
            score += 0.25
            reasons.append(f"돈치안 {self.donchian_period}일 고점 돌파")

        # 3. 볼륨 확인 (+15점)
        if volume_ma and not pd.isna(volume_ma):
            if volume > volume_ma * self.volume_multiplier:
                score += 0.15
                reasons.append(f"볼륨 증가 ({volume / volume_ma:.1f}x)")

        # 4. RSI 필터 (+10점)
        if rsi and not pd.isna(rsi):
            if rsi < 70:
                score += 0.1
                reasons.append(f"RSI 적정 ({rsi:.1f})")
            else:
                score -= 0.1
                reasons.append(f"RSI 과매수 ({rsi:.1f})")

        # 5. ICT 구조 확인 (+20점)
        if has_bullish_structure:
            score += 0.2
            reasons.append("Bullish ICT 구조 (OB/FVG)")
        elif self.require_ict:
            logger.debug(f"{symbol} ICT 구조 없음 - 스킵")
            return None

        # 점수 임계값 확인
        if score < self.min_confidence:
            logger.debug(f"{symbol} Long 신호: 점수 부족 ({score:.2f} < {self.min_confidence})")
            return None

        logger.info(f"🔔 {symbol} Long 신호 감지! Score: {score:.2f}")

        # SL/TP 계산 (피보나치 기반)
        entry_price = close
        atr = current.get('atr', 0)
        if pd.isna(atr) or atr == 0:
            atr = self.calculate_atr(df)

        stop_distance = 2.5 * atr
        stop_loss = entry_price - stop_distance

        # 피보나치 익절 레벨
        tp_distance = stop_distance
        take_profit_1 = entry_price + (tp_distance * 1.618)
        take_profit_2 = entry_price + (tp_distance * 2.618)

        # 신호 생성
        return self.create_signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type='buy',
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            confidence=min(score, 1.0),
            reasons=reasons,
            metadata={
                'keltner_upper': float(keltner_upper),
                'donchian_high': float(donchian_high),
                'rsi': float(rsi) if rsi else None,
                'atr': float(atr),
                'has_ict_structure': has_bullish_structure,
                'trigger': 'keltner_breakout'
            }
        )

    def _evaluate_short_signal(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series,
        prev: pd.Series,
        has_bearish_structure: bool
    ) -> Optional[TradeSignal]:
        """Short 신호 평가"""
        score = 0.0
        reasons = []

        close = current['close']
        keltner_lower = current['keltner_lower']
        donchian_low = prev['donchian_low']
        volume = current['volume']
        volume_ma = current['volume_ma']
        rsi = current.get('rsi', 50)

        # 1. 켈트너 하단 돌파 (+30점)
        if close < keltner_lower:
            score += 0.3
            reasons.append(f"켈트너 하단 돌파 (${close:.2f} < ${keltner_lower:.2f})")
        else:
            return None  # 필수 조건

        # 2. 돈치안 채널 돌파 (+25점)
        if close < donchian_low:
            score += 0.25
            reasons.append(f"돈치안 {self.donchian_period}일 저점 돌파")

        # 3. 볼륨 확인 (+15점)
        if volume_ma and not pd.isna(volume_ma):
            if volume > volume_ma * self.volume_multiplier:
                score += 0.15
                reasons.append(f"볼륨 증가 ({volume / volume_ma:.1f}x)")

        # 4. RSI 필터 (+10점)
        if rsi and not pd.isna(rsi):
            if rsi > 30:
                score += 0.1
                reasons.append(f"RSI 적정 ({rsi:.1f})")
            else:
                score -= 0.1
                reasons.append(f"RSI 과매도 ({rsi:.1f})")

        # 5. ICT 구조 확인 (+20점)
        if has_bearish_structure:
            score += 0.2
            reasons.append("Bearish ICT 구조 (OB/FVG)")
        elif self.require_ict:
            logger.debug(f"{symbol} ICT 구조 없음 - 스킵")
            return None

        # 점수 임계값 확인
        if score < self.min_confidence:
            logger.debug(f"{symbol} Short 신호: 점수 부족 ({score:.2f} < {self.min_confidence})")
            return None

        logger.info(f"🔔 {symbol} Short 신호 감지! Score: {score:.2f}")

        # SL/TP 계산 (피보나치 기반)
        entry_price = close
        atr = current.get('atr', 0)
        if pd.isna(atr) or atr == 0:
            atr = self.calculate_atr(df)

        stop_distance = 2.5 * atr
        stop_loss = entry_price + stop_distance

        # 피보나치 익절 레벨
        tp_distance = stop_distance
        take_profit_1 = entry_price - (tp_distance * 1.618)
        take_profit_2 = entry_price - (tp_distance * 2.618)

        # 신호 생성
        return self.create_signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type='sell',
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            confidence=min(score, 1.0),
            reasons=reasons,
            metadata={
                'keltner_lower': float(keltner_lower),
                'donchian_low': float(donchian_low),
                'rsi': float(rsi) if rsi else None,
                'atr': float(atr),
                'has_ict_structure': has_bearish_structure,
                'trigger': 'keltner_breakdown'
            }
        )
