# -*- coding: utf-8 -*-
"""
기술적 지표 계산 모듈
- EMA, RSI, ATR, Bollinger Band 등
- 모든 전략에서 공통으로 사용
"""

import numpy as np
import pandas as pd
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class TechnicalIndicators:
    """기술적 지표 계산 클래스"""

    @staticmethod
    def calculate_all(df: pd.DataFrame, inplace: bool = True) -> pd.DataFrame:
        """
        모든 기술적 지표 계산

        Args:
            df: OHLCV 데이터프레임
            inplace: True면 원본 수정, False면 복사본 반환

        Returns:
            pd.DataFrame: 지표가 추가된 데이터프레임
        """
        if not inplace:
            df = df.copy()

        # EMA
        df['ema20'] = TechnicalIndicators.calculate_ema(df['close'], 20)
        df['ema50'] = TechnicalIndicators.calculate_ema(df['close'], 50)
        df['ema200'] = TechnicalIndicators.calculate_ema(df['close'], 200)

        # RSI
        df['rsi'] = TechnicalIndicators.calculate_rsi(df['close'], 14)

        # ATR
        df['atr'] = TechnicalIndicators.calculate_atr(df, 14)

        # Bollinger Bands
        bb_upper, bb_mid, bb_lower = TechnicalIndicators.calculate_bollinger(df['close'], 20, 2.0)
        df['bb_upper'] = bb_upper
        df['bb_mid'] = bb_mid
        df['bb_lower'] = bb_lower

        # VWAP
        df['vwap'] = TechnicalIndicators.calculate_vwap(df)

        return df

    @staticmethod
    def calculate_ema(series: pd.Series, period: int) -> pd.Series:
        """
        지수이동평균 (EMA) 계산

        Args:
            series: 가격 시리즈
            period: EMA 기간

        Returns:
            pd.Series: EMA 값
        """
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_sma(series: pd.Series, period: int) -> pd.Series:
        """
        단순이동평균 (SMA) 계산

        Args:
            series: 가격 시리즈
            period: SMA 기간

        Returns:
            pd.Series: SMA 값
        """
        return series.rolling(window=period).mean()

    @staticmethod
    def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """
        RSI (Relative Strength Index) 계산
        Wilder's smoothing 방식 사용

        Args:
            close: 종가 시리즈
            period: RSI 기간 (기본 14)

        Returns:
            pd.Series: RSI 값 (0-100)
        """
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)

        # Wilder's smoothing (RMA)
        avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        ATR (Average True Range) 계산

        Args:
            df: OHLCV 데이터프레임
            period: ATR 기간 (기본 14)

        Returns:
            pd.Series: ATR 값
        """
        high = df['high']
        low = df['low']
        close = df['close']

        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = abs(high - prev_close)
        tr3 = abs(low - prev_close)

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period, min_periods=period).mean()

        return atr

    @staticmethod
    def calculate_atr_value(df: pd.DataFrame, period: int = 14) -> float:
        """
        최신 ATR 값 반환

        Args:
            df: OHLCV 데이터프레임
            period: ATR 기간

        Returns:
            float: 최신 ATR 값
        """
        atr_series = TechnicalIndicators.calculate_atr(df, period)
        return float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0.0

    @staticmethod
    def calculate_bollinger(
        close: pd.Series,
        period: int = 20,
        std_dev: float = 2.0
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Bollinger Bands 계산

        Args:
            close: 종가 시리즈
            period: SMA 기간 (기본 20)
            std_dev: 표준편차 배수 (기본 2.0)

        Returns:
            Tuple[pd.Series, pd.Series, pd.Series]: (상단 밴드, 중간 밴드, 하단 밴드)
        """
        mid = close.rolling(window=period).mean()
        std = close.rolling(window=period).std()

        upper = mid + (std * std_dev)
        lower = mid - (std * std_dev)

        return upper, mid, lower

    @staticmethod
    def calculate_vwap(df: pd.DataFrame) -> pd.Series:
        """
        VWAP (Volume Weighted Average Price) 계산

        Args:
            df: OHLCV 데이터프레임

        Returns:
            pd.Series: VWAP 값
        """
        tp = (df['high'] + df['low'] + df['close']) / 3.0
        volume = df['volume']

        pv_cumsum = (tp * volume).cumsum()
        v_cumsum = volume.cumsum()

        vwap = np.where(v_cumsum > 0, pv_cumsum / v_cumsum, np.nan)

        return pd.Series(vwap, index=df.index)

    @staticmethod
    def calculate_macd(
        close: pd.Series,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        MACD 계산

        Args:
            close: 종가 시리즈
            fast_period: 빠른 EMA 기간 (기본 12)
            slow_period: 느린 EMA 기간 (기본 26)
            signal_period: 시그널 기간 (기본 9)

        Returns:
            Tuple[pd.Series, pd.Series, pd.Series]: (MACD 라인, 시그널 라인, 히스토그램)
        """
        fast_ema = close.ewm(span=fast_period, adjust=False).mean()
        slow_ema = close.ewm(span=slow_period, adjust=False).mean()

        macd_line = fast_ema - slow_ema
        signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
        histogram = macd_line - signal_line

        return macd_line, signal_line, histogram

    @staticmethod
    def calculate_stochastic(
        df: pd.DataFrame,
        k_period: int = 14,
        d_period: int = 3
    ) -> Tuple[pd.Series, pd.Series]:
        """
        Stochastic Oscillator 계산

        Args:
            df: OHLCV 데이터프레임
            k_period: %K 기간 (기본 14)
            d_period: %D 기간 (기본 3)

        Returns:
            Tuple[pd.Series, pd.Series]: (%K, %D)
        """
        high_max = df['high'].rolling(window=k_period).max()
        low_min = df['low'].rolling(window=k_period).min()

        k = 100 * (df['close'] - low_min) / (high_max - low_min)
        d = k.rolling(window=d_period).mean()

        return k, d

    @staticmethod
    def detect_ema_cross(df: pd.DataFrame, fast_col: str = 'ema20', slow_col: str = 'ema50') -> pd.DataFrame:
        """
        EMA 크로스 감지

        Args:
            df: 지표가 포함된 데이터프레임
            fast_col: 빠른 EMA 컬럼명
            slow_col: 느린 EMA 컬럼명

        Returns:
            pd.DataFrame: 크로스 정보가 추가된 데이터프레임
        """
        df = df.copy()

        # 현재 상태 (NaN은 False로 처리)
        ema_bull = df[fast_col] > df[slow_col]
        df['ema_bull'] = ema_bull.where(ema_bull.notna(), False).astype(bool)

        # 이전 상태 (shift 후 NaN은 False로 채움)
        ema_bull_prev = df['ema_bull'].shift(1)
        df['ema_bull_prev'] = ema_bull_prev.where(ema_bull_prev.notna(), False).astype(bool)

        # 크로스 감지
        df['golden_cross'] = (~df['ema_bull_prev']) & df['ema_bull']  # 데드 → 골든
        df['dead_cross'] = df['ema_bull_prev'] & (~df['ema_bull'])    # 골든 → 데드

        return df

    @staticmethod
    def check_ema_alignment(df: pd.DataFrame, direction: str = 'bullish') -> bool:
        """
        EMA 정배열/역배열 확인

        Args:
            df: 지표가 포함된 데이터프레임
            direction: 'bullish' (정배열) 또는 'bearish' (역배열)

        Returns:
            bool: 정렬 여부
        """
        if len(df) < 1:
            return False

        last = df.iloc[-1]

        # EMA 컬럼 확인
        if 'ema20' not in df.columns or 'ema50' not in df.columns or 'ema200' not in df.columns:
            return False

        ema20 = last['ema20']
        ema50 = last['ema50']
        ema200 = last['ema200']

        if pd.isna(ema20) or pd.isna(ema50) or pd.isna(ema200):
            return False

        if direction == 'bullish':
            # 정배열: EMA20 > EMA50 > EMA200
            return ema20 > ema50 > ema200
        else:
            # 역배열: EMA20 < EMA50 < EMA200
            return ema20 < ema50 < ema200

    @staticmethod
    def get_current_price(df: pd.DataFrame) -> float:
        """최신 종가 반환"""
        return float(df.iloc[-1]['close'])

    @staticmethod
    def get_indicator_value(df: pd.DataFrame, indicator: str) -> Optional[float]:
        """특정 지표의 최신 값 반환"""
        if indicator not in df.columns:
            return None
        value = df.iloc[-1][indicator]
        return float(value) if not pd.isna(value) else None
