# -*- coding: utf-8 -*-
"""
Bollinger Band 전략
- Breakout (추세 추종) 또는 Reversal (역추세) 모드
- 밴드 돌파 및 복귀 신호
- ATR 기반 SL/TP
"""

import logging
import sys
import os
from typing import Dict, List, Optional, Any
import pandas as pd
from datetime import datetime

# 공용 폴더 경로 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_pgdb_dir = os.path.dirname(_current_dir)
if _pgdb_dir not in sys.path:
    sys.path.insert(0, _pgdb_dir)

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

    3. MA Cross (옵션):
       - SMA20/SMA200 골든크로스 → 매수
       - SMA20/SMA200 데드크로스 → 매도
    """

    # 전략 설정 스키마 (웹 UI 동적 생성용)
    CONFIG_SCHEMA = {
        'enabled': {'label': '활성화', 'type': 'checkbox', 'default': True},
        'use_ai': {'label': '🤖 AI 검증', 'type': 'checkbox', 'default': False},
        'timeframe': {'label': '타임프레임', 'type': 'select', 'options': ['15m', '30m', '1h', '4h', '1d'], 'default': '4h'},
        'period': {'label': 'BB 기간', 'type': 'number', 'min': 10, 'max': 50, 'step': 1, 'default': 20},
        'std_dev': {'label': '표준편차 배수', 'type': 'number', 'min': 1, 'max': 4, 'step': 0.1, 'default': 2.0},
        'mode': {'label': '모드', 'type': 'select', 'options': ['breakout', 'reversal'], 'default': 'breakout'},
        'use_ma_cross': {'label': 'SMA20/200 교차 신호', 'type': 'checkbox', 'default': False},
        'volume_filter': {'label': '볼륨 필터', 'type': 'checkbox', 'default': True},
        'atr_multiplier': {'label': 'ATR 배수', 'type': 'number', 'min': 0.5, 'max': 5, 'step': 0.1, 'default': 1.5},
        'min_confidence': {'label': '최소 신뢰도', 'type': 'number', 'min': 0, 'max': 1, 'step': 0.1, 'default': 0.5},
    }

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
                - use_ma_cross: SMA20/SMA200 교차 신호 사용 (기본 False)
        """
        super().__init__(name, config)

        self.period = config.get('period', 20)
        self.std_dev = config.get('std_dev', 2.0)
        self.mode = config.get('mode', 'breakout')  # 'breakout' or 'reversal'
        self.volume_filter = config.get('volume_filter', True)
        self.use_ma_cross = config.get('use_ma_cross', False)  # SMA20/SMA200 교차 옵션

        logger.info(f"BollingerStrategy 초기화: mode={self.mode}, period={self.period}, std={self.std_dev}, ma_cross={self.use_ma_cross}")

    def get_required_indicators(self) -> List[str]:
        """필요한 지표 목록"""
        indicators = ['bb_upper', 'bb_mid', 'bb_lower', 'atr']
        if self.use_ma_cross:
            indicators.append('sma200')
        return indicators

    def get_indicator_display_schema(self) -> List[Dict]:
        """볼린저 전략 지표 표시 스키마"""
        return [
            {"key": "current_price", "label": "현재가", "align": "right", "format": "price"},
            {"key": "bb_upper", "label": "BB 상단", "align": "right", "format": "price"},
            {"key": "bb_mid", "label": "BB 중심", "align": "right", "format": "price"},
            {"key": "bb_lower", "label": "BB 하단", "align": "right", "format": "price"},
            {"key": "bb_width_pct", "label": "밴드폭(%)", "align": "right", "format": "percent"},
            {"key": "rsi", "label": "RSI", "align": "right", "format": "number"},
            {"key": "atr", "label": "ATR", "align": "right", "format": "price"},
        ]

    def compute_realtime_indicators(self, candles: List[Dict]) -> Optional[Dict]:
        """볼린저 밴드 실시간 계산"""
        if len(candles) < self.period + 5:
            return None
        try:
            df = pd.DataFrame(candles)
            for c in ['open', 'high', 'low', 'close', 'volume']:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors='coerce')
            bb_upper, bb_mid, bb_lower = TechnicalIndicators.calculate_bollinger(
                df['close'], self.period, self.std_dev
            )
            if pd.isna(bb_upper.iloc[-1]):
                return None
            out = {
                'bb_upper': float(bb_upper.iloc[-1]),
                'bb_mid': float(bb_mid.iloc[-1]),
                'bb_lower': float(bb_lower.iloc[-1]),
            }
            if bb_mid.iloc[-1] and bb_mid.iloc[-1] != 0:
                w = (bb_upper.iloc[-1] - bb_lower.iloc[-1]) / bb_mid.iloc[-1] * 100
                out['bb_width_pct'] = round(float(w), 2)
            df = TechnicalIndicators.calculate_all(df, inplace=False)
            if 'atr' in df.columns and not pd.isna(df['atr'].iloc[-1]):
                out['atr'] = float(df['atr'].iloc[-1])
            if 'rsi' in df.columns and not pd.isna(df['rsi'].iloc[-1]):
                out['rsi'] = round(float(df['rsi'].iloc[-1]), 2)
            return out
        except Exception:
            return None

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
            # Decimal → float 변환 (DB에서 가져온 데이터 처리)
            df = df.copy()
            numeric_cols = ['open', 'high', 'low', 'close', 'volume']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = df[col].astype(float)

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
            logger.info(f"📊 {symbol} BB: 가격=${close:.8f} | 상단=${bb_upper:.8f} | 하단=${bb_lower:.8f} | 위치: {position} | 모드: {self.mode}")

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

            # SMA20/SMA200 교차 신호 (옵션)
            if self.use_ma_cross:
                sma20 = current.get('bb_mid')  # 볼린저 중심선 = SMA20
                sma200 = current.get('sma200')
                prev_sma20 = prev.get('bb_mid')
                prev_sma200 = prev.get('sma200')

                if all(not pd.isna(x) for x in [sma20, sma200, prev_sma20, prev_sma200]):
                    # Golden Cross: SMA20이 SMA200을 상향 돌파
                    if prev_sma20 <= prev_sma200 and sma20 > sma200:
                        logger.info(f"🔄 {symbol} SMA20/SMA200 골든크로스 감지! SMA20={sma20:.4f} > SMA200={sma200:.4f}")
                        signal = self._evaluate_ma_cross_buy(symbol, timeframe, df, current)
                        if signal:
                            signals.append(signal)

                    # Dead Cross: SMA20이 SMA200을 하향 돌파
                    if prev_sma20 >= prev_sma200 and sma20 < sma200:
                        logger.info(f"🔄 {symbol} SMA20/SMA200 데드크로스 감지! SMA20={sma20:.4f} < SMA200={sma200:.4f}")
                        signal = self._evaluate_ma_cross_sell(symbol, timeframe, df, current)
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
        stop_loss = entry_price - (atr * self.atr_multiplier)
        tp1 = bb_mid  # 중심선
        tp2 = current['bb_upper'] * 0.98  # 상단 밴드 근처

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
        stop_loss = entry_price + (atr * self.atr_multiplier)
        tp1 = bb_mid  # 중심선
        tp2 = current['bb_lower'] * 1.02  # 하단 밴드 근처

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

    def _evaluate_ma_cross_buy(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series
    ) -> Optional[TradeSignal]:
        """
        SMA20/SMA200 골든크로스 매수 신호

        Args:
            symbol: 심볼
            timeframe: 타임프레임
            df: 데이터프레임
            current: 현재 캔들

        Returns:
            Optional[TradeSignal]: 조건 충족 시 신호
        """
        score = 0.0
        reasons = []

        sma20 = current.get('bb_mid')
        sma200 = current.get('sma200')

        # 1. 골든크로스 확인 (+50점)
        score += 0.5
        reasons.append(f"SMA20/SMA200 골든크로스 (SMA20={sma20:.2f} > SMA200={sma200:.2f})")

        # 2. 가격이 SMA20 위에 있는지 (+15점)
        close = current['close']
        if close > sma20:
            score += 0.15
            reasons.append("가격이 SMA20 위")

        # 3. 거래량 확인 (+10점)
        if self.volume_filter and len(df) >= 20:
            avg_volume = df['volume'].tail(20).mean()
            if current['volume'] > avg_volume * 1.3:
                score += 0.1
                reasons.append("거래량 증가 동반")

        # 4. RSI 확인 (+10점)
        rsi = current.get('rsi')
        if rsi and not pd.isna(rsi):
            if 40 < rsi < 70:
                score += 0.1
                reasons.append(f"RSI 적정 ({rsi:.1f})")

        # 점수 임계값 확인
        if score < self.min_confidence:
            logger.debug(f"{symbol} MA Cross 매수: 점수 부족 ({score:.2f})")
            return None

        # SL/TP 계산
        entry_price = current['close']
        atr = current.get('atr', 0)
        if pd.isna(atr) or atr == 0:
            atr = self.calculate_atr(df)

        # SL: SMA200 아래 또는 ATR 기반
        sl_by_sma = sma200 * 0.99  # SMA200 1% 아래
        sl_by_atr = entry_price - (atr * self.atr_multiplier)
        stop_loss = max(sl_by_sma, sl_by_atr)  # 더 가까운 SL 선택

        # TP: ATR 기반
        tp1 = entry_price + (atr * self.atr_multiplier * 2)
        tp2 = entry_price + (atr * self.atr_multiplier * 3)

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
                'mode': 'ma_cross',
                'trigger': 'golden_cross',
                'sma20': float(sma20),
                'sma200': float(sma200),
                'atr': float(atr)
            }
        )

    def _evaluate_ma_cross_sell(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series
    ) -> Optional[TradeSignal]:
        """
        SMA20/SMA200 데드크로스 매도 신호

        Args:
            symbol: 심볼
            timeframe: 타임프레임
            df: 데이터프레임
            current: 현재 캔들

        Returns:
            Optional[TradeSignal]: 조건 충족 시 신호
        """
        score = 0.0
        reasons = []

        sma20 = current.get('bb_mid')
        sma200 = current.get('sma200')

        # 1. 데드크로스 확인 (+50점)
        score += 0.5
        reasons.append(f"SMA20/SMA200 데드크로스 (SMA20={sma20:.2f} < SMA200={sma200:.2f})")

        # 2. 가격이 SMA20 아래에 있는지 (+15점)
        close = current['close']
        if close < sma20:
            score += 0.15
            reasons.append("가격이 SMA20 아래")

        # 3. 거래량 확인 (+10점)
        if self.volume_filter and len(df) >= 20:
            avg_volume = df['volume'].tail(20).mean()
            if current['volume'] > avg_volume * 1.3:
                score += 0.1
                reasons.append("거래량 증가 동반")

        # 4. RSI 확인 (+10점)
        rsi = current.get('rsi')
        if rsi and not pd.isna(rsi):
            if 30 < rsi < 60:
                score += 0.1
                reasons.append(f"RSI 적정 ({rsi:.1f})")

        # 점수 임계값 확인
        if score < self.min_confidence:
            logger.debug(f"{symbol} MA Cross 매도: 점수 부족 ({score:.2f})")
            return None

        # SL/TP 계산
        entry_price = current['close']
        atr = current.get('atr', 0)
        if pd.isna(atr) or atr == 0:
            atr = self.calculate_atr(df)

        # SL: SMA200 위 또는 ATR 기반
        sl_by_sma = sma200 * 1.01  # SMA200 1% 위
        sl_by_atr = entry_price + (atr * self.atr_multiplier)
        stop_loss = min(sl_by_sma, sl_by_atr)  # 더 가까운 SL 선택

        # TP: ATR 기반
        tp1 = entry_price - (atr * self.atr_multiplier * 2)
        tp2 = entry_price - (atr * self.atr_multiplier * 3)

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
                'mode': 'ma_cross',
                'trigger': 'dead_cross',
                'sma20': float(sma20),
                'sma200': float(sma200),
                'atr': float(atr)
            }
        )
