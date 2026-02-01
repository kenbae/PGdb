# -*- coding: utf-8 -*-
"""
Bollinger Band 전략
- Breakout (추세 추종) 또는 Reversal (역추세) 모드
- 밴드 돌파 및 복귀 신호
- ATR 기반 SL/TP
"""

import logging
from typing import Dict, List, Optional
import pandas as pd
from datetime import datetime

from .base import BaseStrategy, TradeSignal
from indicators.technical import TechnicalIndicators

logger = logging.getLogger(__name__)


class BollingerStrategy(BaseStrategy):
    """
    Bollinger Band 전략

    모드:
    1. Breakout (추세 추종):
       - 상단 밴드 돌파 → 매수 (상승 추세 가속)
       - 하단 밴드 돌파 → 매도 (하락 추세 가속)

    2. Reversal (역추세):
       - 상단 밴드에서 복귀 → 매도 (과매수 반전)
       - 하단 밴드에서 복귀 → 매수 (과매도 반전)
    """

    def __init__(self, name: str, config: Dict):
        """
        초기화

        Args:
            name: 전략 이름
            config: 전략 설정
                - timeframe: 분석 타임프레임 (기본 4h)
                - period: 볼린저 밴드 기간 (기본 20)
                - std_dev: 표준편차 배수 (기본 2.0)
                - mode: 'breakout' 또는 'reversal' (기본 breakout)
                - atr_multiplier: ATR 배수 (기본 1.5)
                - volume_filter: 거래량 필터 사용 (기본 True)
        """
        super().__init__(name, config)

        self.period = config.get('period', 20)
        self.std_dev = config.get('std_dev', 2.0)
        self.mode = config.get('mode', 'breakout')  # 'breakout' or 'reversal'
        self.volume_filter = config.get('volume_filter', True)

        logger.info(f"BollingerStrategy 초기화: mode={self.mode}, period={self.period}, std={self.std_dev}")

    def get_required_indicators(self) -> List[str]:
        """필요한 지표 목록"""
        return ['bb_upper', 'bb_mid', 'bb_lower', 'atr']

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
            df = TechnicalIndicators.calculate_all(df, inplace=False)

            # 최신 캔들 확인
            current = df.iloc[-1]
            prev = df.iloc[-2]

            bb_upper = current.get('bb_upper')
            bb_lower = current.get('bb_lower')
            bb_mid = current.get('bb_mid')
            prev_bb_upper = prev.get('bb_upper')
            prev_bb_lower = prev.get('bb_lower')

            if any(pd.isna(x) for x in [bb_upper, bb_lower, bb_mid, prev_bb_upper, prev_bb_lower]):
                logger.warning(f"{symbol} 볼린저 밴드 값 없음")
                return signals

            close = current['close']
            prev_close = prev['close']

            # 현재 볼린저 밴드 상태 로그
            position = "밴드 내"
            if close > bb_upper:
                position = "상단 돌파"
            elif close < bb_lower:
                position = "하단 돌파"
            logger.info(f"📊 {symbol} BB: 가격=${close:.2f} | 상단=${bb_upper:.2f} | 하단=${bb_lower:.2f} | 위치: {position} | 모드: {self.mode}")

            if self.mode == 'breakout':
                # Breakout 모드
                # 상단 돌파 → 매수
                if prev_close <= prev_bb_upper and close > bb_upper:
                    signal = self._evaluate_breakout_buy(symbol, timeframe, df, current)
                    if signal:
                        signals.append(signal)

                # 하단 돌파 → 매도
                if prev_close >= prev_bb_lower and close < bb_lower:
                    signal = self._evaluate_breakout_sell(symbol, timeframe, df, current)
                    if signal:
                        signals.append(signal)

            else:  # reversal 모드
                # Reversal 모드
                # 상단에서 복귀 → 매도
                if prev_close >= prev_bb_upper and close < bb_upper:
                    signal = self._evaluate_reversal_sell(symbol, timeframe, df, current)
                    if signal:
                        signals.append(signal)

                # 하단에서 복귀 → 매수
                if prev_close <= prev_bb_lower and close > bb_lower:
                    signal = self._evaluate_reversal_buy(symbol, timeframe, df, current)
                    if signal:
                        signals.append(signal)

        except Exception as e:
            logger.error(f"❌ {symbol} {timeframe} Bollinger 분석 실패: {e}")

        return signals

    def _evaluate_breakout_buy(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series
    ) -> Optional[TradeSignal]:
        """
        상단 돌파 매수 신호 (추세 추종)

        Args:
            symbol: 심볼
            timeframe: 타임프레임
            df: 데이터프레임
            current: 현재 캔들

        Returns:
            Optional[TradeSignal]: 조건 충족 시 신호, 아니면 None
        """
        score = 0.0
        reasons = []

        # 1. 상단 밴드 돌파 (+40점)
        score += 0.4
        reasons.append("볼린저 상단 돌파")

        # 2. 거래량 확인 (+15점)
        if self.volume_filter and len(df) >= 20:
            avg_volume = df['volume'].tail(20).mean()
            if current['volume'] > avg_volume * 1.5:
                score += 0.15
                reasons.append("강한 거래량 동반")
            elif current['volume'] > avg_volume * 1.2:
                score += 0.1
                reasons.append("거래량 증가")

        # 3. 밴드 확장 확인 (+10점) - 변동성 증가
        bb_width = (current['bb_upper'] - current['bb_lower']) / current['bb_mid']
        prev_width = (df.iloc[-2]['bb_upper'] - df.iloc[-2]['bb_lower']) / df.iloc[-2]['bb_mid']
        if bb_width > prev_width:
            score += 0.1
            reasons.append("밴드 확장 (변동성 증가)")

        # 4. RSI 확인 (+10점)
        rsi = current.get('rsi')
        if rsi and not pd.isna(rsi):
            if 50 < rsi < 80:  # 상승 추세지만 과매수 아님
                score += 0.1
                reasons.append(f"RSI 상승 모멘텀 ({rsi:.1f})")

        # 점수 임계값 확인
        if score < self.min_confidence:
            logger.debug(f"{symbol} 매수 신호: 점수 부족 ({score:.2f})")
            return None

        # SL/TP 계산
        entry_price = current['close']
        atr = current.get('atr', 0)
        if pd.isna(atr) or atr == 0:
            atr = self.calculate_atr(df)

        stop_loss, tp1, tp2 = self.calculate_sl_tp(entry_price, atr, 'buy', self.atr_multiplier)

        return self.create_signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type='buy',
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            confidence=min(score, 1.0),
            reasons=reasons,
            metadata={
                'mode': 'breakout',
                'trigger': 'upper_band_break',
                'bb_upper': float(current['bb_upper']),
                'bb_lower': float(current['bb_lower']),
                'bb_width': float(bb_width),
                'atr': float(atr)
            }
        )

    def _evaluate_breakout_sell(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series
    ) -> Optional[TradeSignal]:
        """
        하단 돌파 매도 신호 (추세 추종)
        """
        score = 0.0
        reasons = []

        # 1. 하단 밴드 돌파 (+40점)
        score += 0.4
        reasons.append("볼린저 하단 돌파")

        # 2. 거래량 확인 (+15점)
        if self.volume_filter and len(df) >= 20:
            avg_volume = df['volume'].tail(20).mean()
            if current['volume'] > avg_volume * 1.5:
                score += 0.15
                reasons.append("강한 거래량 동반")
            elif current['volume'] > avg_volume * 1.2:
                score += 0.1
                reasons.append("거래량 증가")

        # 3. 밴드 확장 확인 (+10점)
        bb_width = (current['bb_upper'] - current['bb_lower']) / current['bb_mid']
        prev_width = (df.iloc[-2]['bb_upper'] - df.iloc[-2]['bb_lower']) / df.iloc[-2]['bb_mid']
        if bb_width > prev_width:
            score += 0.1
            reasons.append("밴드 확장 (변동성 증가)")

        # 4. RSI 확인 (+10점)
        rsi = current.get('rsi')
        if rsi and not pd.isna(rsi):
            if 20 < rsi < 50:  # 하락 추세지만 과매도 아님
                score += 0.1
                reasons.append(f"RSI 하락 모멘텀 ({rsi:.1f})")

        # 점수 임계값 확인
        if score < self.min_confidence:
            logger.debug(f"{symbol} 매도 신호: 점수 부족 ({score:.2f})")
            return None

        # SL/TP 계산
        entry_price = current['close']
        atr = current.get('atr', 0)
        if pd.isna(atr) or atr == 0:
            atr = self.calculate_atr(df)

        stop_loss, tp1, tp2 = self.calculate_sl_tp(entry_price, atr, 'sell', self.atr_multiplier)

        return self.create_signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type='sell',
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            confidence=min(score, 1.0),
            reasons=reasons,
            metadata={
                'mode': 'breakout',
                'trigger': 'lower_band_break',
                'bb_upper': float(current['bb_upper']),
                'bb_lower': float(current['bb_lower']),
                'bb_width': float(bb_width),
                'atr': float(atr)
            }
        )

    def _evaluate_reversal_buy(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series
    ) -> Optional[TradeSignal]:
        """
        하단에서 복귀 매수 신호 (역추세)
        """
        score = 0.0
        reasons = []

        # 1. 하단 밴드에서 복귀 (+40점)
        score += 0.4
        reasons.append("볼린저 하단 복귀 (반등)")

        # 2. RSI 과매도 확인 (+15점)
        rsi = current.get('rsi')
        if rsi and not pd.isna(rsi):
            if rsi < 30:
                score += 0.15
                reasons.append(f"RSI 과매도 ({rsi:.1f})")
            elif rsi < 40:
                score += 0.1
                reasons.append(f"RSI 낮음 ({rsi:.1f})")

        # 3. 캔들 패턴 확인 (+10점) - 양봉
        if current['close'] > current['open']:
            score += 0.1
            reasons.append("양봉 확인")

        # 4. 중심선과의 거리 (+10점)
        bb_mid = current['bb_mid']
        distance_to_mid = (bb_mid - current['close']) / bb_mid
        if distance_to_mid > 0.02:  # 중심선까지 2% 이상 상승 여지
            score += 0.1
            reasons.append(f"중심선까지 {distance_to_mid*100:.1f}% 상승 여지")

        # 점수 임계값 확인
        if score < self.min_confidence:
            return None

        # SL/TP 계산 - 중심선을 1차 목표로
        entry_price = current['close']
        atr = current.get('atr', 0)
        if pd.isna(atr) or atr == 0:
            atr = self.calculate_atr(df)

        # 역추세는 보수적인 SL/TP
        stop_loss = round(entry_price - (atr * self.atr_multiplier), 8)
        tp1 = round(bb_mid, 8)  # 중심선
        tp2 = round(current['bb_upper'] * 0.98, 8)  # 상단 밴드 근처

        return self.create_signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type='buy',
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            confidence=min(score, 1.0),
            reasons=reasons,
            metadata={
                'mode': 'reversal',
                'trigger': 'lower_band_return',
                'bb_mid': float(bb_mid),
                'bb_lower': float(current['bb_lower']),
                'atr': float(atr)
            }
        )

    def _evaluate_reversal_sell(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series
    ) -> Optional[TradeSignal]:
        """
        상단에서 복귀 매도 신호 (역추세)
        """
        score = 0.0
        reasons = []

        # 1. 상단 밴드에서 복귀 (+40점)
        score += 0.4
        reasons.append("볼린저 상단 복귀 (하락)")

        # 2. RSI 과매수 확인 (+15점)
        rsi = current.get('rsi')
        if rsi and not pd.isna(rsi):
            if rsi > 70:
                score += 0.15
                reasons.append(f"RSI 과매수 ({rsi:.1f})")
            elif rsi > 60:
                score += 0.1
                reasons.append(f"RSI 높음 ({rsi:.1f})")

        # 3. 캔들 패턴 확인 (+10점) - 음봉
        if current['close'] < current['open']:
            score += 0.1
            reasons.append("음봉 확인")

        # 4. 중심선과의 거리 (+10점)
        bb_mid = current['bb_mid']
        distance_to_mid = (current['close'] - bb_mid) / bb_mid
        if distance_to_mid > 0.02:  # 중심선까지 2% 이상 하락 여지
            score += 0.1
            reasons.append(f"중심선까지 {distance_to_mid*100:.1f}% 하락 여지")

        # 점수 임계값 확인
        if score < self.min_confidence:
            return None

        # SL/TP 계산 - 중심선을 1차 목표로
        entry_price = current['close']
        atr = current.get('atr', 0)
        if pd.isna(atr) or atr == 0:
            atr = self.calculate_atr(df)

        # 역추세는 보수적인 SL/TP
        stop_loss = round(entry_price + (atr * self.atr_multiplier), 8)
        tp1 = round(bb_mid, 8)  # 중심선
        tp2 = round(current['bb_lower'] * 1.02, 8)  # 하단 밴드 근처

        return self.create_signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type='sell',
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            confidence=min(score, 1.0),
            reasons=reasons,
            metadata={
                'mode': 'reversal',
                'trigger': 'upper_band_return',
                'bb_mid': float(bb_mid),
                'bb_upper': float(current['bb_upper']),
                'atr': float(atr)
            }
        )
