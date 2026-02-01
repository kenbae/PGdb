# -*- coding: utf-8 -*-
"""
RSI 과매수/과매도 전략
- RSI 기반 반전 신호
- EMA 트렌드 필터 (선택적)
- ATR 기반 SL/TP
"""

import logging
from typing import Dict, List, Optional
import pandas as pd
from datetime import datetime

from .base import BaseStrategy, TradeSignal
from indicators.technical import TechnicalIndicators

logger = logging.getLogger(__name__)


class RSIStrategy(BaseStrategy):
    """
    RSI 과매수/과매도 전략

    조건:
    1. RSI 과매도(<30) → 매수 신호 (반등 기대)
    2. RSI 과매수(>70) → 매도 신호 (하락 기대)
    3. 선택적: EMA 트렌드 필터 (정배열/역배열)
    4. ATR 기반 SL/TP 계산
    """

    def __init__(self, name: str, config: Dict):
        """
        초기화

        Args:
            name: 전략 이름
            config: 전략 설정
                - timeframe: 분석 타임프레임 (기본 1h)
                - overbought: RSI 과매수 기준 (기본 70)
                - oversold: RSI 과매도 기준 (기본 30)
                - use_trend_filter: EMA 트렌드 필터 사용 (기본 True)
                - atr_multiplier: ATR 배수 (기본 1.5)
                - rsi_period: RSI 계산 기간 (기본 14)
        """
        super().__init__(name, config)

        self.overbought = config.get('overbought', 70)
        self.oversold = config.get('oversold', 30)
        self.use_trend_filter = config.get('use_trend_filter', True)
        self.rsi_period = config.get('rsi_period', 14)

        logger.info(f"RSIStrategy 초기화: overbought={self.overbought}, oversold={self.oversold}")

    def get_required_indicators(self) -> List[str]:
        """필요한 지표 목록"""
        indicators = ['rsi', 'atr']
        if self.use_trend_filter:
            indicators.extend(['ema20', 'ema50'])
        return indicators

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

            rsi = current.get('rsi')
            prev_rsi = prev.get('rsi')

            if rsi is None or pd.isna(rsi) or prev_rsi is None or pd.isna(prev_rsi):
                logger.warning(f"{symbol} RSI 값 없음")
                return signals

            # 현재 RSI 상태 로그
            logger.info(f"📊 {symbol} RSI: {rsi:.1f} (이전: {prev_rsi:.1f}) | 과매도<{self.oversold}, 과매수>{self.overbought}")

            # 과매도 탈출 → 매수 신호 (이전 RSI가 30 이하였다가 30 초과로 변경)
            if prev_rsi <= self.oversold and rsi > self.oversold:
                logger.info(f"🔔 {symbol} 과매도 탈출 감지!")
                signal = self._evaluate_buy_signal(symbol, timeframe, df, current, rsi)
                if signal:
                    signals.append(signal)

            # 과매수 탈출 → 매도 신호 (이전 RSI가 70 이상이었다가 70 미만으로 변경)
            if prev_rsi >= self.overbought and rsi < self.overbought:
                logger.info(f"🔔 {symbol} 과매수 탈출 감지!")
                signal = self._evaluate_sell_signal(symbol, timeframe, df, current, rsi)
                if signal:
                    signals.append(signal)

        except Exception as e:
            logger.error(f"❌ {symbol} {timeframe} RSI 분석 실패: {e}")

        return signals

    def _evaluate_buy_signal(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series,
        rsi: float
    ) -> Optional[TradeSignal]:
        """
        매수 신호 평가

        Args:
            symbol: 심볼
            timeframe: 타임프레임
            df: 데이터프레임
            current: 현재 캔들
            rsi: 현재 RSI

        Returns:
            Optional[TradeSignal]: 조건 충족 시 신호, 아니면 None
        """
        score = 0.0
        reasons = []

        # 1. RSI 과매도 탈출 (+40점)
        score += 0.4
        reasons.append(f"RSI 과매도 탈출 ({rsi:.1f})")

        # 2. EMA 트렌드 필터 (선택적, +20점)
        if self.use_trend_filter:
            ema20 = current.get('ema20')
            ema50 = current.get('ema50')
            if ema20 and ema50 and not pd.isna(ema20) and not pd.isna(ema50):
                if ema20 > ema50:
                    score += 0.2
                    reasons.append("EMA 정배열 (상승 추세)")
                else:
                    # 역배열이면 점수 감소
                    score -= 0.1
                    reasons.append("EMA 역배열 (하락 추세)")

        # 3. RSI 반등 강도 (+10점)
        rsi_change = rsi - df.iloc[-2]['rsi']
        if rsi_change > 5:
            score += 0.1
            reasons.append(f"RSI 강한 반등 (+{rsi_change:.1f})")

        # 4. 거래량 확인 (+10점)
        if len(df) >= 20:
            avg_volume = df['volume'].tail(20).mean()
            if current['volume'] > avg_volume * 1.2:
                score += 0.1
                reasons.append("거래량 증가")

        # 5. 가격 위치 확인 (+10점)
        if 'bb_lower' in df.columns:
            bb_lower = current.get('bb_lower')
            if bb_lower and not pd.isna(bb_lower):
                if current['close'] <= bb_lower * 1.02:  # 하단 밴드 근처
                    score += 0.1
                    reasons.append("볼린저 하단 근처")

        # 점수 임계값 확인
        if score < self.min_confidence:
            logger.debug(f"{symbol} 매수 신호: 점수 부족 ({score:.2f} < {self.min_confidence})")
            return None

        # SL/TP 계산
        entry_price = current['close']
        atr = current.get('atr', 0)
        if pd.isna(atr) or atr == 0:
            atr = self.calculate_atr(df)

        stop_loss, tp1, tp2 = self.calculate_sl_tp(entry_price, atr, 'buy', self.atr_multiplier)

        # 신호 생성
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
                'rsi': float(rsi),
                'rsi_change': float(rsi_change),
                'trigger': 'oversold_exit',
                'atr': float(atr)
            }
        )

    def _evaluate_sell_signal(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series,
        rsi: float
    ) -> Optional[TradeSignal]:
        """
        매도 신호 평가

        Args:
            symbol: 심볼
            timeframe: 타임프레임
            df: 데이터프레임
            current: 현재 캔들
            rsi: 현재 RSI

        Returns:
            Optional[TradeSignal]: 조건 충족 시 신호, 아니면 None
        """
        score = 0.0
        reasons = []

        # 1. RSI 과매수 탈출 (+40점)
        score += 0.4
        reasons.append(f"RSI 과매수 탈출 ({rsi:.1f})")

        # 2. EMA 트렌드 필터 (선택적, +20점)
        if self.use_trend_filter:
            ema20 = current.get('ema20')
            ema50 = current.get('ema50')
            if ema20 and ema50 and not pd.isna(ema20) and not pd.isna(ema50):
                if ema20 < ema50:
                    score += 0.2
                    reasons.append("EMA 역배열 (하락 추세)")
                else:
                    # 정배열이면 점수 감소
                    score -= 0.1
                    reasons.append("EMA 정배열 (상승 추세)")

        # 3. RSI 하락 강도 (+10점)
        rsi_change = rsi - df.iloc[-2]['rsi']
        if rsi_change < -5:
            score += 0.1
            reasons.append(f"RSI 강한 하락 ({rsi_change:.1f})")

        # 4. 거래량 확인 (+10점)
        if len(df) >= 20:
            avg_volume = df['volume'].tail(20).mean()
            if current['volume'] > avg_volume * 1.2:
                score += 0.1
                reasons.append("거래량 증가")

        # 5. 가격 위치 확인 (+10점)
        if 'bb_upper' in df.columns:
            bb_upper = current.get('bb_upper')
            if bb_upper and not pd.isna(bb_upper):
                if current['close'] >= bb_upper * 0.98:  # 상단 밴드 근처
                    score += 0.1
                    reasons.append("볼린저 상단 근처")

        # 점수 임계값 확인
        if score < self.min_confidence:
            logger.debug(f"{symbol} 매도 신호: 점수 부족 ({score:.2f} < {self.min_confidence})")
            return None

        # SL/TP 계산
        entry_price = current['close']
        atr = current.get('atr', 0)
        if pd.isna(atr) or atr == 0:
            atr = self.calculate_atr(df)

        stop_loss, tp1, tp2 = self.calculate_sl_tp(entry_price, atr, 'sell', self.atr_multiplier)

        # 신호 생성
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
                'rsi': float(rsi),
                'rsi_change': float(rsi_change),
                'trigger': 'overbought_exit',
                'atr': float(atr)
            }
        )
