# -*- coding: utf-8 -*-
"""
BB 3σ + Adaptive RSI 전략
- 볼린저 밴드 3 표준편차 극단 영역
- Kaufman Efficiency Ratio 기반 적응형 RSI
- 역추세 전략 (극단에서 평균 회귀)
"""

import logging
import sys
import os
from typing import Dict, List, Optional, Any
import pandas as pd
import numpy as np
from datetime import datetime

# 공용 폴더 경로 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_pgdb_dir = os.path.dirname(_current_dir)
if _pgdb_dir not in sys.path:
    sys.path.insert(0, _pgdb_dir)

from .base import BaseStrategy, TradeSignal
from indicators.technical import TechnicalIndicators

logger = logging.getLogger(__name__)


class BBAdaptiveRSIStrategy(BaseStrategy):
    """
    BB 3σ + Adaptive RSI 전략

    로직:
    1. 볼린저 밴드 3σ → 극단적 가격 이탈 감지
    2. Adaptive RSI → 시장 효율성에 따른 동적 RSI
    3. 롱 진입: BB 하단 돌파 + RSI 과매도 (또는 RSI 상승 크로스)
    4. 숏 진입: BB 상단 돌파 + RSI 과매수 (또는 RSI 하락 크로스)

    Adaptive RSI (Alex Gonzalez):
    - Kaufman Efficiency Ratio (ER)로 시장 효율성 측정
    - 추세 시장(ER 높음): 빠른 RSI 가중치 ↑
    - 횡보 시장(ER 낮음): 느린 RSI 가중치 ↑
    """

    CONFIG_SCHEMA = {
        'enabled': {'label': '활성화', 'type': 'checkbox', 'default': True},
        'use_ai': {'label': '🤖 AI 검증', 'type': 'checkbox', 'default': False},
        'timeframe': {'label': '타임프레임', 'type': 'select', 'options': ['15m', '30m', '1h', '4h'], 'default': '30m'},
        'bb_length': {'label': 'BB 기간', 'type': 'number', 'min': 10, 'max': 50, 'step': 1, 'default': 20},
        'bb_std_dev': {'label': 'BB 표준편차 (σ)', 'type': 'number', 'min': 1, 'max': 5, 'step': 0.5, 'default': 3.0},
        'er_length': {'label': 'ER 기간', 'type': 'number', 'min': 5, 'max': 30, 'step': 1, 'default': 10},
        'fast_rsi_length': {'label': '빠른 RSI 기간', 'type': 'number', 'min': 2, 'max': 10, 'step': 1, 'default': 2},
        'slow_rsi_length': {'label': '느린 RSI 기간', 'type': 'number', 'min': 10, 'max': 50, 'step': 1, 'default': 30},
        'rsi_smooth': {'label': 'RSI 스무딩', 'type': 'number', 'min': 1, 'max': 10, 'step': 1, 'default': 3},
        'rsi_overbought': {'label': 'RSI 과매수', 'type': 'number', 'min': 60, 'max': 90, 'step': 1, 'default': 70},
        'rsi_oversold': {'label': 'RSI 과매도', 'type': 'number', 'min': 10, 'max': 40, 'step': 1, 'default': 30},
        'use_rsi_filter': {'label': 'RSI 필터 사용', 'type': 'checkbox', 'default': True},
        'atr_multiplier': {'label': 'ATR 배수', 'type': 'number', 'min': 0.5, 'max': 5, 'step': 0.1, 'default': 1.5},
        'min_confidence': {'label': '최소 신뢰도', 'type': 'number', 'min': 0, 'max': 1, 'step': 0.1, 'default': 0.5},
    }

    def __init__(self, name: str, config: Dict):
        """
        초기화

        Args:
            name: 전략 이름
            config: 전략 설정
                - bb_length: 볼린저 밴드 기간 (기본 20)
                - bb_std_dev: 표준편차 배수 (기본 3.0)
                - er_length: Efficiency Ratio 기간 (기본 10)
                - fast_rsi_length: 빠른 RSI 기간 (기본 2)
                - slow_rsi_length: 느린 RSI 기간 (기본 30)
                - rsi_smooth: RSI 스무딩 기간 (기본 3)
                - rsi_overbought: RSI 과매수 (기본 70)
                - rsi_oversold: RSI 과매도 (기본 30)
                - use_rsi_filter: RSI 필터 사용 여부 (기본 True)
                - atr_multiplier: ATR 배수 (기본 1.5)
        """
        super().__init__(name, config)

        # Bollinger Band 설정
        self.bb_length = config.get('bb_length', 20)
        self.bb_std_dev = config.get('bb_std_dev', 3.0)

        # Adaptive RSI 설정
        self.er_length = config.get('er_length', 10)
        self.fast_rsi_length = config.get('fast_rsi_length', 2)
        self.slow_rsi_length = config.get('slow_rsi_length', 30)
        self.rsi_smooth = config.get('rsi_smooth', 3)
        self.rsi_overbought = config.get('rsi_overbought', 70)
        self.rsi_oversold = config.get('rsi_oversold', 30)
        self.use_rsi_filter = config.get('use_rsi_filter', True)

        logger.info(f"BBAdaptiveRSIStrategy 초기화: BB({self.bb_length}, {self.bb_std_dev}σ), "
                   f"Adaptive RSI(ER={self.er_length}, fast={self.fast_rsi_length}, slow={self.slow_rsi_length})")

    def get_required_indicators(self) -> List[str]:
        """필요한 지표 목록"""
        return ['bb_upper_3', 'bb_lower_3', 'bb_mid', 'adaptive_rsi', 'er', 'atr']

    def get_indicator_display_schema(self) -> List[Dict]:
        """BB Adaptive RSI 전략 지표 표시 스키마"""
        return [
            {"key": "current_price", "label": "현재가", "align": "right", "format": "price"},
            {"key": "bb_upper_3", "label": "BB 3σ 상단", "align": "right", "format": "price"},
            {"key": "bb_mid", "label": "BB 중심", "align": "right", "format": "price"},
            {"key": "bb_lower_3", "label": "BB 3σ 하단", "align": "right", "format": "price"},
            {"key": "adaptive_rsi", "label": "Adaptive RSI", "align": "right", "format": "number"},
            {"key": "er", "label": "ER", "align": "right", "format": "number"},
            {"key": "atr", "label": "ATR", "align": "right", "format": "price"},
        ]

    def compute_realtime_indicators(self, candles: List[Dict]) -> Optional[Dict]:
        """BB 3σ + Adaptive RSI 실시간 계산"""
        if len(candles) < max(self.bb_length, 50) + 5:
            return None
        try:
            df = pd.DataFrame(candles)
            for c in ['open', 'high', 'low', 'close', 'volume']:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors='coerce')
            bb_upper, bb_mid, bb_lower = self._calculate_bollinger_bands_3sigma(df)
            smoothed_rsi, _, er = self._calculate_adaptive_rsi(df)
            df = TechnicalIndicators.calculate_all(df, inplace=False)
            last = df.iloc[-1]
            out = {
                'bb_upper_3': float(bb_upper.iloc[-1]),
                'bb_mid': float(bb_mid.iloc[-1]),
                'bb_lower_3': float(bb_lower.iloc[-1]),
                'adaptive_rsi': round(float(smoothed_rsi.iloc[-1]), 2),
                'er': round(float(er.iloc[-1]), 4),
            }
            if 'atr' in df.columns and not pd.isna(last.get('atr')):
                out['atr'] = float(last['atr'])
            return out
        except Exception:
            return None

    def _calculate_efficiency_ratio(self, df: pd.DataFrame) -> pd.Series:
        """
        Kaufman Efficiency Ratio 계산

        ER = 방향성 변화 / 총 변동성
        - 1에 가까우면 추세적 (방향성 있음)
        - 0에 가까우면 횡보 (노이즈)
        """
        close = df['close']

        # 방향성 변화 (절대값)
        direction = abs(close - close.shift(self.er_length))

        # 총 변동성 (기간 내 모든 변화의 합)
        volatility = abs(close - close.shift(1)).rolling(self.er_length).sum()

        # Efficiency Ratio
        er = direction / volatility
        er = er.fillna(0)

        return er

    def _calculate_rsi(self, series: pd.Series, period: int) -> pd.Series:
        """
        RSI 계산

        Args:
            series: 가격 시리즈
            period: RSI 기간

        Returns:
            RSI 시리즈 (0-100)
        """
        delta = series.diff()

        gain = delta.where(delta > 0, 0)
        loss = (-delta).where(delta < 0, 0)

        avg_gain = gain.ewm(span=period, adjust=False).mean()
        avg_loss = loss.ewm(span=period, adjust=False).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def _calculate_adaptive_rsi(self, df: pd.DataFrame) -> tuple:
        """
        Adaptive RSI 계산 (Alex Gonzalez 방식)

        Returns:
            tuple: (smoothed_rsi, signal_line, er)
        """
        close = df['close']

        # Efficiency Ratio
        er = self._calculate_efficiency_ratio(df)

        # Fast/Slow RSI
        fast_rsi = self._calculate_rsi(close, self.fast_rsi_length)
        slow_rsi = self._calculate_rsi(close, self.slow_rsi_length)

        # Adaptive RSI = ER * FastRSI + (1-ER) * SlowRSI
        adaptive_rsi = er * fast_rsi + (1 - er) * slow_rsi

        # EMA 스무딩
        smoothed_rsi = adaptive_rsi.ewm(span=self.rsi_smooth, adjust=False).mean()

        # 시그널 라인 (9기간 EMA)
        signal_line = smoothed_rsi.ewm(span=9, adjust=False).mean()

        return smoothed_rsi, signal_line, er

    def _calculate_bollinger_bands_3sigma(self, df: pd.DataFrame) -> tuple:
        """
        볼린저 밴드 3σ 계산

        Returns:
            tuple: (upper, middle, lower)
        """
        close = df['close']

        middle = close.rolling(self.bb_length).mean()
        std = close.rolling(self.bb_length).std()

        upper = middle + (std * self.bb_std_dev)
        lower = middle - (std * self.bb_std_dev)

        return upper, middle, lower

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
            # Decimal → float 변환
            df = df.copy()
            numeric_cols = ['open', 'high', 'low', 'close', 'volume']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = df[col].astype(float)

            # 지표 계산
            bb_upper, bb_mid, bb_lower = self._calculate_bollinger_bands_3sigma(df)
            smoothed_rsi, rsi_signal, er = self._calculate_adaptive_rsi(df)

            # ATR 계산 (TechnicalIndicators 사용)
            df = TechnicalIndicators.calculate_all(df, inplace=False)

            # 데이터프레임에 지표 추가
            df['bb_upper_3'] = bb_upper
            df['bb_mid'] = bb_mid
            df['bb_lower_3'] = bb_lower
            df['adaptive_rsi'] = smoothed_rsi
            df['rsi_signal'] = rsi_signal
            df['er'] = er

            # 최신 캔들
            current = df.iloc[-1]
            prev = df.iloc[-2]

            # 현재 상태 로그
            close = current['close']
            curr_rsi = current['adaptive_rsi']
            curr_er = current['er']
            curr_bb_upper = current['bb_upper_3']
            curr_bb_lower = current['bb_lower_3']

            # BB 위치 백분율 (0-100)
            bb_percent = ((close - curr_bb_lower) / (curr_bb_upper - curr_bb_lower)) * 100 if (curr_bb_upper - curr_bb_lower) > 0 else 50

            position = "밴드 내"
            if close >= curr_bb_upper:
                position = "상단 3σ 돌파"
            elif close <= curr_bb_lower:
                position = "하단 3σ 돌파"

            logger.info(f"📊 {symbol} BB3σ+AdaptiveRSI: 가격=${close:.8f} | "
                       f"BB상단=${curr_bb_upper:.8f} | BB하단=${curr_bb_lower:.8f} | "
                       f"위치: {position} | RSI={curr_rsi:.1f} | ER={curr_er:.2f}")

            # ============================================
            # 롱 조건: BB 하단 돌파 + RSI 과매도 (또는 RSI 필터 비활성화)
            # ============================================
            long_bb_cond = close <= curr_bb_lower

            if self.use_rsi_filter:
                rsi_oversold_cond = curr_rsi < self.rsi_oversold
                rsi_bullish_cross = (prev['adaptive_rsi'] < prev['rsi_signal'] and
                                    curr_rsi > current['rsi_signal'])
                long_rsi_cond = rsi_oversold_cond or rsi_bullish_cross
            else:
                long_rsi_cond = True

            if long_bb_cond and long_rsi_cond:
                signal = self._evaluate_long(symbol, timeframe, df, current, curr_rsi, curr_er, bb_percent)
                if signal:
                    signals.append(signal)

            # ============================================
            # 숏 조건: BB 상단 돌파 + RSI 과매수 (또는 RSI 필터 비활성화)
            # ============================================
            short_bb_cond = close >= curr_bb_upper

            if self.use_rsi_filter:
                rsi_overbought_cond = curr_rsi > self.rsi_overbought
                rsi_bearish_cross = (prev['adaptive_rsi'] > prev['rsi_signal'] and
                                    curr_rsi < current['rsi_signal'])
                short_rsi_cond = rsi_overbought_cond or rsi_bearish_cross
            else:
                short_rsi_cond = True

            if short_bb_cond and short_rsi_cond:
                signal = self._evaluate_short(symbol, timeframe, df, current, curr_rsi, curr_er, bb_percent)
                if signal:
                    signals.append(signal)

        except Exception as e:
            logger.error(f"❌ {symbol} {timeframe} BB3σ+AdaptiveRSI 분석 실패: {e}")

        return signals

    def _evaluate_long(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series,
        rsi: float,
        er: float,
        bb_percent: float
    ) -> Optional[TradeSignal]:
        """
        롱 신호 평가
        """
        score = 0.0
        reasons = []

        # 1. BB 하단 3σ 돌파 (+40점)
        score += 0.4
        reasons.append(f"BB 3σ 하단 돌파 ({bb_percent:.1f}%)")

        # 2. RSI 과매도 확인 (+20점)
        if rsi < self.rsi_oversold:
            score += 0.2
            reasons.append(f"Adaptive RSI 과매도 ({rsi:.1f})")
        elif rsi < 40:
            score += 0.1
            reasons.append(f"Adaptive RSI 낮음 ({rsi:.1f})")

        # 3. RSI 시그널 상향 크로스 (+15점)
        if len(df) >= 2:
            prev = df.iloc[-2]
            if prev['adaptive_rsi'] < prev['rsi_signal'] and rsi > current['rsi_signal']:
                score += 0.15
                reasons.append("RSI 시그널 상향 크로스")

        # 4. Efficiency Ratio 확인 (+10점)
        if er < 0.3:
            score += 0.1
            reasons.append(f"횡보 시장 (ER={er:.2f}) - 평균회귀 유리")
        elif er > 0.6:
            score += 0.05
            reasons.append(f"추세 시장 (ER={er:.2f})")

        # 5. 양봉 확인 (+10점)
        if current['close'] > current['open']:
            score += 0.1
            reasons.append("양봉 확인")

        # 점수 임계값 확인
        if score < self.min_confidence:
            logger.debug(f"{symbol} 롱 신호: 점수 부족 ({score:.2f})")
            return None

        # SL/TP 계산
        entry_price = current['close']
        atr = current.get('atr', 0)
        if pd.isna(atr) or atr == 0:
            atr = self.calculate_atr(df)

        # 역추세 전략: 보수적 SL, 중심선 목표
        stop_loss = entry_price - (atr * self.atr_multiplier)
        tp1 = current['bb_mid']  # 중심선 목표
        tp2 = current['bb_upper_3'] * 0.95  # 상단 밴드 근처

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
                'strategy': 'bb_adaptive_rsi',
                'mode': 'reversal',
                'trigger': 'lower_band_3sigma',
                'bb_std_dev': self.bb_std_dev,
                'bb_upper': float(current['bb_upper_3']),
                'bb_lower': float(current['bb_lower_3']),
                'bb_mid': float(current['bb_mid']),
                'bb_percent': float(bb_percent),
                'adaptive_rsi': float(rsi),
                'efficiency_ratio': float(er),
                'atr': float(atr)
            }
        )

    def _evaluate_short(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series,
        rsi: float,
        er: float,
        bb_percent: float
    ) -> Optional[TradeSignal]:
        """
        숏 신호 평가
        """
        score = 0.0
        reasons = []

        # 1. BB 상단 3σ 돌파 (+40점)
        score += 0.4
        reasons.append(f"BB 3σ 상단 돌파 ({bb_percent:.1f}%)")

        # 2. RSI 과매수 확인 (+20점)
        if rsi > self.rsi_overbought:
            score += 0.2
            reasons.append(f"Adaptive RSI 과매수 ({rsi:.1f})")
        elif rsi > 60:
            score += 0.1
            reasons.append(f"Adaptive RSI 높음 ({rsi:.1f})")

        # 3. RSI 시그널 하향 크로스 (+15점)
        if len(df) >= 2:
            prev = df.iloc[-2]
            if prev['adaptive_rsi'] > prev['rsi_signal'] and rsi < current['rsi_signal']:
                score += 0.15
                reasons.append("RSI 시그널 하향 크로스")

        # 4. Efficiency Ratio 확인 (+10점)
        if er < 0.3:
            score += 0.1
            reasons.append(f"횡보 시장 (ER={er:.2f}) - 평균회귀 유리")
        elif er > 0.6:
            score += 0.05
            reasons.append(f"추세 시장 (ER={er:.2f})")

        # 5. 음봉 확인 (+10점)
        if current['close'] < current['open']:
            score += 0.1
            reasons.append("음봉 확인")

        # 점수 임계값 확인
        if score < self.min_confidence:
            logger.debug(f"{symbol} 숏 신호: 점수 부족 ({score:.2f})")
            return None

        # SL/TP 계산
        entry_price = current['close']
        atr = current.get('atr', 0)
        if pd.isna(atr) or atr == 0:
            atr = self.calculate_atr(df)

        # 역추세 전략: 보수적 SL, 중심선 목표
        stop_loss = entry_price + (atr * self.atr_multiplier)
        tp1 = current['bb_mid']  # 중심선 목표
        tp2 = current['bb_lower_3'] * 1.05  # 하단 밴드 근처

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
                'strategy': 'bb_adaptive_rsi',
                'mode': 'reversal',
                'trigger': 'upper_band_3sigma',
                'bb_std_dev': self.bb_std_dev,
                'bb_upper': float(current['bb_upper_3']),
                'bb_lower': float(current['bb_lower_3']),
                'bb_mid': float(current['bb_mid']),
                'bb_percent': float(bb_percent),
                'adaptive_rsi': float(rsi),
                'efficiency_ratio': float(er),
                'atr': float(atr)
            }
        )
