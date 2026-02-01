# -*- coding: utf-8 -*-
"""
EMA Divergence + Volume Breakout 전략

- EMA20과 EMA59 이격이 점점 벌어지는 구간 포착
- 롱: EMA20 > EMA59, 이격 확대 + 볼륨 돌파 + 상승 캔들
- 숏: EMA20 < EMA59, 이격 확대 + 볼륨 돌파 + 하락 캔들
"""

import logging
from typing import Dict, List, Optional, Any
import pandas as pd
import numpy as np
from datetime import datetime

import sys
import os

_current_dir = os.path.dirname(os.path.abspath(__file__))
_pgdb_dir = os.path.dirname(_current_dir)
if _pgdb_dir not in sys.path:
    sys.path.insert(0, _pgdb_dir)

from .base import BaseStrategy, TradeSignal
from indicators.technical import TechnicalIndicators

logger = logging.getLogger(__name__)


class EMADivergenceVolumeStrategy(BaseStrategy):
    """
    EMA 이격 확대 + 볼륨 돌파 전략

    조건:
    1. 롱: EMA20 > EMA59, N캔들 동안 이격이 확대, 볼륨 > MA*multiplier, 상승 캔들
    2. 숏: EMA20 < EMA59, N캔들 동안 이격이 확대, 볼륨 > MA*multiplier, 하락 캔들
    """

    CONFIG_SCHEMA = {
        'enabled': {'label': '활성화', 'type': 'checkbox', 'default': True},
        'use_ai': {'label': '🤖 AI 검증', 'type': 'checkbox', 'default': False},
        'timeframe': {'label': '타임프레임', 'type': 'select', 'options': ['5m', '15m', '30m', '1h', '4h'], 'default': '15m'},
        'ema_fast': {'label': 'EMA 빠른선', 'type': 'number', 'min': 5, 'max': 50, 'step': 1, 'default': 20},
        'ema_slow': {'label': 'EMA 느린선', 'type': 'number', 'min': 30, 'max': 100, 'step': 1, 'default': 59},
        'divergence_lookback': {'label': '이격 확대 확인 캔들', 'type': 'number', 'min': 2, 'max': 20, 'step': 1, 'default': 5},
        'min_spread_increase_pct': {'label': '최소 이격 증가율(%)', 'type': 'number', 'min': 0.01, 'max': 2.0, 'step': 0.05, 'default': 0.05},
        'volume_ma_period': {'label': '볼륨 MA 기간', 'type': 'number', 'min': 5, 'max': 50, 'step': 1, 'default': 20},
        'volume_multiplier': {'label': '볼륨 돌파 배수', 'type': 'number', 'min': 1.0, 'max': 3.0, 'step': 0.1, 'default': 1.5},
        'atr_multiplier': {'label': 'ATR 손절 배수', 'type': 'number', 'min': 0.5, 'max': 3.0, 'step': 0.1, 'default': 1.5},
    }

    def __init__(self, name: str, config: Dict):
        super().__init__(name, config)
        self.ema_fast = config.get('ema_fast', 20)
        self.ema_slow = config.get('ema_slow', 59)
        self.divergence_lookback = config.get('divergence_lookback', 5)
        self.min_spread_increase_pct = config.get('min_spread_increase_pct', 0.05)
        self.volume_ma_period = config.get('volume_ma_period', 20)
        self.volume_multiplier = config.get('volume_multiplier', 1.5)
        self.atr_multiplier = config.get('atr_multiplier', 1.5)
        logger.info(f"EMADivergenceVolumeStrategy 초기화: EMA{self.ema_fast}/{self.ema_slow}, vol_mult={self.volume_multiplier}")

    def get_required_indicators(self) -> List[str]:
        return ['ema20', 'atr']

    def get_indicator_display_schema(self) -> List[Dict]:
        """EMA 이격+볼륨 전략용 지표 표시 스키마"""
        return [
            {"key": "current_price", "label": "현재가", "align": "right", "format": "price"},
            {"key": "ema_20", "label": "EMA20", "align": "right", "format": "price"},
            {"key": "ema_59", "label": "EMA59", "align": "right", "format": "price"},
            {"key": "ema_spread", "label": "이격(%)", "align": "right", "format": "percent"},
            {"key": "volume_ratio", "label": "볼륨/MA", "align": "right", "format": "number"},
            {"key": "rsi", "label": "RSI", "align": "right", "format": "number"},
        ]

    def compute_realtime_indicators(self, candles: List[Dict]) -> Optional[Dict]:
        """EMA59, volume_ratio 실시간 계산 (AdaptiveExitManager 기본에 병합)"""
        if len(candles) < 60:
            return None
        try:
            df = pd.DataFrame(candles)
            for c in ['open', 'high', 'low', 'close', 'volume']:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors='coerce')
            closes = df['close'].dropna()
            if len(closes) < 59:
                return None
            ema59 = float(closes.ewm(span=59, adjust=False).mean().iloc[-1])
            out = {'ema_59': ema59}
            if 'volume' in df.columns and len(df) >= 20:
                vol_ma = df['volume'].rolling(20).mean().iloc[-1]
                if vol_ma and vol_ma > 0:
                    out['volume_ratio'] = round(float(df['volume'].iloc[-1]) / vol_ma, 2)
            return out
        except Exception:
            return None

    def analyze(self, symbol: str, timeframe: str, df: pd.DataFrame) -> List[TradeSignal]:
        signals = []
        min_rows = max(100, self.ema_slow + self.divergence_lookback + 10)
        if not self.validate_dataframe(df, min_rows=min_rows):
            return signals

        try:
            df = df.copy()
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df.columns:
                    df[col] = df[col].astype(float)

            df = TechnicalIndicators.calculate_all(df, inplace=False)
            df['ema59'] = TechnicalIndicators.calculate_ema(df['close'], self.ema_slow)
            ema_fast_col = f'ema{self.ema_fast}' if self.ema_fast != 20 else 'ema20'
            if ema_fast_col not in df.columns:
                df[ema_fast_col] = TechnicalIndicators.calculate_ema(df['close'], self.ema_fast)

            ema20 = df[ema_fast_col]
            ema59 = df['ema59']
            spread = ema20 - ema59

            vol_ma = df['volume'].rolling(self.volume_ma_period, min_periods=1).mean()
            volume_surge = df['volume'] >= vol_ma * self.volume_multiplier

            spread_ago = spread.shift(self.divergence_lookback)
            spread_abs_now = spread.abs()
            spread_abs_ago = spread_ago.abs()
            spread_increase_ratio = np.where(
                spread_abs_ago > 1e-10,
                (spread_abs_now - spread_abs_ago) / spread_abs_ago * 100,
                0
            )

            bullish = df['close'] > df['open']
            bearish = df['close'] < df['open']

            divergence_long = (ema20 > ema59) & (spread_increase_ratio >= self.min_spread_increase_pct)
            divergence_short = (ema20 < ema59) & (spread_increase_ratio >= self.min_spread_increase_pct)

            long_signal_mask = divergence_long & volume_surge & bullish
            short_signal_mask = divergence_short & volume_surge & bearish

            idx = len(df) - 1
            current = df.iloc[idx]

            if long_signal_mask.iloc[idx]:
                signal = self._create_buy_signal(symbol, timeframe, df, current)
                if signal:
                    signals.append(signal)
                    logger.info(f"✅ {symbol} {timeframe} EMA Divergence+Vol 롱 신호")

            if short_signal_mask.iloc[idx]:
                signal = self._create_sell_signal(symbol, timeframe, df, current)
                if signal:
                    signals.append(signal)
                    logger.info(f"✅ {symbol} {timeframe} EMA Divergence+Vol 숏 신호")

        except Exception as e:
            logger.error(f"❌ {symbol} {timeframe} 분석 실패: {e}")

        return signals

    def _create_buy_signal(
        self, symbol: str, timeframe: str, df: pd.DataFrame, current: pd.Series
    ) -> Optional[TradeSignal]:
        entry = float(current['close'])
        atr = float(current.get('atr', 0)) or self._calc_atr(df)
        if atr <= 0:
            return None
        stop_loss = entry - self.atr_multiplier * atr
        take_profit_1 = entry + 2.0 * atr

        reasons = [
            "EMA20 > EMA59 이격 확대",
            "볼륨 돌파 (MA 대비 배수 초과)",
            "상승 캔들",
        ]
        return self.create_signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type='buy',
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            confidence=0.65,
            reasons=reasons,
            metadata={
                'ema20': float(current.get('ema20', 0)),
                'ema59': float(current.get('ema59', 0)),
                'volume_ratio': self._safe_vol_ratio(df, current),
            }
        )

    def _create_sell_signal(
        self, symbol: str, timeframe: str, df: pd.DataFrame, current: pd.Series
    ) -> Optional[TradeSignal]:
        entry = float(current['close'])
        atr = float(current.get('atr', 0)) or self._calc_atr(df)
        if atr <= 0:
            return None
        stop_loss = entry + self.atr_multiplier * atr
        take_profit_1 = entry - 2.0 * atr

        reasons = [
            "EMA20 < EMA59 이격 확대(하락)",
            "볼륨 돌파 (MA 대비 배수 초과)",
            "하락 캔들",
        ]
        return self.create_signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type='sell',
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            confidence=0.65,
            reasons=reasons,
            metadata={
                'ema20': float(current.get('ema20', 0)),
                'ema59': float(current.get('ema59', 0)),
                'volume_ratio': self._safe_vol_ratio(df, current),
            }
        )

    def _safe_vol_ratio(self, df: pd.DataFrame, current: pd.Series) -> float:
        """볼륨 / 볼륨 MA 비율 (안전 계산)"""
        if len(df) < self.volume_ma_period:
            return 0.0
        vol_ma = df['volume'].rolling(self.volume_ma_period).mean().iloc[-1]
        if vol_ma is None or vol_ma <= 0 or pd.isna(vol_ma):
            return 0.0
        v = float(current.get('volume', 0) or 0)
        return round(v / vol_ma, 2) if v else 0.0

    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        high = df['high'].astype(float)
        low = df['low'].astype(float)
        close = df['close'].astype(float)
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        return float(tr.rolling(period).mean().iloc[-1]) if len(tr) >= period else 0
