# -*- coding: utf-8 -*-
"""
EMA Cross + HTF 정렬 전략
- EMA20/50 크로스오버 감지
- 상위 타임프레임 레짐 확인
- 정배열/역배열 필터
- run_signals.py 로직 기반
"""

import logging
from typing import Dict, List, Optional, Any
import pandas as pd
from datetime import datetime

import sys
import os

# 공용 폴더 경로 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_pgdb_dir = os.path.dirname(_current_dir)
if _pgdb_dir not in sys.path:
    sys.path.insert(0, _pgdb_dir)

from .base import BaseStrategy, TradeSignal
from indicators.technical import TechnicalIndicators

logger = logging.getLogger(__name__)


class EMACrossStrategy(BaseStrategy):
    """
    EMA Cross + HTF 정렬 전략

    조건:
    1. EMA20이 EMA50을 상향/하향 돌파 (크로스오버)
    2. 정배열 (EMA20 > EMA50 > EMA200) 또는 역배열
    3. 선택적: 상위 타임프레임 레짐 일치
    4. 선택적: RSI, VWAP 추가 조건
    """

    CONFIG_SCHEMA = {
        'enabled': {'label': '활성화', 'type': 'checkbox', 'default': True},
        'use_ai': {'label': '🤖 AI 검증', 'type': 'checkbox', 'default': False},
        'timeframe': {'label': '타임프레임', 'type': 'select', 'options': ['15m', '30m', '1h', '4h'], 'default': '30m'},
        'score_threshold': {'label': '최소 점수', 'type': 'number', 'min': 0, 'max': 1, 'step': 0.1, 'default': 0.5},
        'atr_multiplier': {'label': 'ATR 배수', 'type': 'number', 'min': 0.5, 'max': 5, 'step': 0.1, 'default': 1.5},
        'use_rsi_filter': {'label': 'RSI 필터', 'type': 'checkbox', 'default': True},
        'use_vwap_filter': {'label': 'VWAP 필터', 'type': 'checkbox', 'default': True},
        'min_confidence': {'label': '최소 신뢰도', 'type': 'number', 'min': 0, 'max': 1, 'step': 0.1, 'default': 0.5},
    }

    def __init__(self, name: str, config: Dict):
        """
        초기화

        Args:
            name: 전략 이름
            config: 전략 설정
                - timeframe: 분석 타임프레임 (기본 30m)
                - htf_timeframes: 상위 타임프레임 목록 (예: ['4h', '1d'])
                - score_threshold: 최소 점수 (0-1, 기본 0.5, VWAP/volume 제거 후 최대 0.65)
                - atr_multiplier: ATR 배수 (기본 1.5)
                - use_rsi_filter: RSI 필터 사용 (기본 True)
                - use_vwap_filter: VWAP 필터 사용 (기본 True)
        """
        super().__init__(name, config)

        self.htf_timeframes = config.get('htf_timeframes', ['4h', '1d'])
        self.score_threshold = config.get('score_threshold', 0.5)
        self.atr_multiplier = config.get('atr_multiplier', 1.5)
        self.use_rsi_filter = config.get('use_rsi_filter', True)
        self.use_vwap_filter = config.get('use_vwap_filter', True)

        logger.info(f"EMACrossStrategy 초기화: threshold={self.score_threshold}, ATR mult={self.atr_multiplier}")

    def get_required_indicators(self) -> List[str]:
        """필요한 지표 목록 (VWAP/volume 조건 제거됨)"""
        return ['ema20', 'ema50', 'ema200', 'atr', 'rsi']

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
        if not self.validate_dataframe(df, min_rows=100):
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

            # EMA 크로스 감지
            df = TechnicalIndicators.detect_ema_cross(df)

            # 최신 캔들 확인
            current = df.iloc[-1]
            prev = df.iloc[-2]

            # 디버그: EMA 상태 로깅
            ema20 = current.get('ema20', 0)
            ema50 = current.get('ema50', 0)
            ema200 = current.get('ema200', 0)
            is_golden = current.get('golden_cross', False)
            is_dead = current.get('dead_cross', False)

            if is_golden or is_dead:
                logger.info(f"📊 {symbol} {timeframe} EMA 크로스 감지: golden={is_golden}, dead={is_dead}")
                logger.info(f"   EMA20={ema20:.2f}, EMA50={ema50:.2f}, EMA200={ema200:.2f}")
                is_bullish = TechnicalIndicators.check_ema_alignment(df, 'bullish')
                is_bearish = TechnicalIndicators.check_ema_alignment(df, 'bearish')
                logger.info(f"   정배열={is_bullish}, 역배열={is_bearish}")

            # 골든 크로스 (매수 신호)
            if current['golden_cross']:
                signal = self._evaluate_buy_signal(symbol, timeframe, df, current)
                if signal:
                    logger.info(f"✅ {symbol} {timeframe} 매수 신호 생성: conf={signal.confidence:.2f}")
                    signals.append(signal)
                else:
                    logger.debug(f"⚠️ {symbol} {timeframe} 골든 크로스 but 조건 불충족")

            # 데드 크로스 (매도 신호)
            if current['dead_cross']:
                signal = self._evaluate_sell_signal(symbol, timeframe, df, current)
                if signal:
                    logger.info(f"✅ {symbol} {timeframe} 매도 신호 생성: conf={signal.confidence:.2f}")
                    signals.append(signal)
                else:
                    logger.debug(f"⚠️ {symbol} {timeframe} 데드 크로스 but 조건 불충족")

        except Exception as e:
            logger.error(f"❌ {symbol} {timeframe} 분석 실패: {e}")

        return signals

    def _evaluate_buy_signal(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series
    ) -> Optional[TradeSignal]:
        """
        매수 신호 평가

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

        # 1. EMA 정배열 확인 (+40점)
        if TechnicalIndicators.check_ema_alignment(df, 'bullish'):
            score += 0.4
            reasons.append("EMA 정배열 (20 > 50 > 200)")
        else:
            # 정배열이 아니면 신호 무시
            return None

        # 2. RSI 조건 (+10점)
        if self.use_rsi_filter:
            rsi = current.get('rsi')
            if rsi and not pd.isna(rsi):
                if 30 < rsi < 70:  # 과매수/과매도 아님
                    score += 0.1
                    reasons.append(f"RSI 정상 범위 ({rsi:.1f})")
                elif rsi <= 30:  # 과매도 → 반등 기대
                    score += 0.15
                    reasons.append(f"RSI 과매도 반등 ({rsi:.1f})")

        # 3. 추세 강도 (+10점) - EMA 간격
        ema20 = current['ema20']
        ema50 = current['ema50']
        if ema50 > 0:
            spread = (ema20 - ema50) / ema50 * 100
            if spread > 0.5:  # 0.5% 이상 간격
                score += 0.1
                reasons.append(f"EMA 간격 양호 ({spread:.2f}%)")

        # 점수 임계값 확인
        if score < self.score_threshold:
            logger.debug(f"{symbol} 매수 신호: 점수 부족 ({score:.2f} < {self.score_threshold})")
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
                'cross_type': 'golden',
                'ema20': float(ema20),
                'ema50': float(ema50),
                'rsi': float(current.get('rsi', 0)) if not pd.isna(current.get('rsi')) else None,
                'atr': float(atr)
            }
        )

    def _evaluate_sell_signal(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        current: pd.Series
    ) -> Optional[TradeSignal]:
        """
        매도 신호 평가

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

        # 1. EMA 역배열 확인 (+40점)
        if TechnicalIndicators.check_ema_alignment(df, 'bearish'):
            score += 0.4
            reasons.append("EMA 역배열 (20 < 50 < 200)")
        else:
            return None

        # 2. RSI 조건 (+10점)
        if self.use_rsi_filter:
            rsi = current.get('rsi')
            if rsi and not pd.isna(rsi):
                if 30 < rsi < 70:
                    score += 0.1
                    reasons.append(f"RSI 정상 범위 ({rsi:.1f})")
                elif rsi >= 70:  # 과매수 → 하락 기대
                    score += 0.15
                    reasons.append(f"RSI 과매수 하락 ({rsi:.1f})")

        # 3. 추세 강도 (+10점)
        ema20 = current['ema20']
        ema50 = current['ema50']
        if ema50 > 0:
            spread = (ema50 - ema20) / ema50 * 100
            if spread > 0.5:
                score += 0.1
                reasons.append(f"EMA 간격 양호 ({spread:.2f}%)")

        # 점수 임계값 확인
        if score < self.score_threshold:
            logger.debug(f"{symbol} 매도 신호: 점수 부족 ({score:.2f} < {self.score_threshold})")
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
                'cross_type': 'dead',
                'ema20': float(ema20),
                'ema50': float(ema50),
                'rsi': float(current.get('rsi', 0)) if not pd.isna(current.get('rsi')) else None,
                'atr': float(atr)
            }
        )

    def get_indicator_display_schema(self) -> List[Dict]:
        """EMA Cross 전략 지표 표시 스키마"""
        return [
            {"key": "current_price", "label": "현재가", "align": "right", "format": "price"},
            {"key": "ema_20", "label": "EMA20", "align": "right", "format": "price"},
            {"key": "ema_50", "label": "EMA50", "align": "right", "format": "price"},
            {"key": "alignment", "label": "정/역배열", "align": "center", "format": "text", "color_key": "alignment"},
            {"key": "rsi", "label": "RSI", "align": "right", "format": "number"},
            {"key": "ema_spread", "label": "EMA SPREAD(%)", "align": "right", "format": "percent"},
            {"key": "entry_score", "label": "진입 점수", "align": "right", "format": "number"},
        ]

    def compute_realtime_indicators(self, candles: List[Dict]) -> Optional[Dict]:
        """실시간 진입 점수 계산 (EMACrossExitManager 지표에 병합됨)"""
        if len(candles) < 100:
            return None
        try:
            df = pd.DataFrame(candles)
            if not all(c in df.columns for c in ['open', 'high', 'low', 'close', 'volume']):
                return None
            score = self.get_entry_score(df, 'long')
            return {'entry_score': float(score)} if score is not None else None
        except Exception:
            return None

    def get_entry_score(self, df: pd.DataFrame, direction: str = 'long') -> Optional[float]:
        """
        현재 진입 점수만 계산 (VWAP/volume 제외, 신호 생성 없음).
        실시간 지표 패널용.

        Args:
            df: OHLCV 데이터프레임 (지표 미포함 가능)
            direction: 'long' = 매수 진입 점수, 'sell' = 매도 진입 점수

        Returns:
            0.0 ~ 1.0 점수, 또는 조건 미충족 시 0.0
        """
        if df is None or len(df) < 100:
            return None
        try:
            df = df.copy()
            numeric_cols = ['open', 'high', 'low', 'close', 'volume']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = df[col].astype(float)
            df = TechnicalIndicators.calculate_all(df, inplace=False)
            current = df.iloc[-1]
            score = 0.0

            if direction == 'long':
                if not TechnicalIndicators.check_ema_alignment(df, 'bullish'):
                    return 0.0
                score += 0.4
                if self.use_rsi_filter:
                    rsi = current.get('rsi')
                    if rsi is not None and not pd.isna(rsi):
                        if 30 < rsi < 70:
                            score += 0.1
                        elif rsi <= 30:
                            score += 0.15
                ema20 = current.get('ema20')
                ema50 = current.get('ema50')
                if ema20 is not None and ema50 is not None and ema50 > 0:
                    spread = (ema20 - ema50) / ema50 * 100
                    if spread > 0.5:
                        score += 0.1
            else:
                if not TechnicalIndicators.check_ema_alignment(df, 'bearish'):
                    return 0.0
                score += 0.4
                if self.use_rsi_filter:
                    rsi = current.get('rsi')
                    if rsi is not None and not pd.isna(rsi):
                        if 30 < rsi < 70:
                            score += 0.1
                        elif rsi >= 70:
                            score += 0.15
                ema20 = current.get('ema20')
                ema50 = current.get('ema50')
                if ema20 is not None and ema50 is not None and ema50 > 0:
                    spread = (ema50 - ema20) / ema50 * 100
                    if spread > 0.5:
                        score += 0.1
            return round(min(score, 1.0), 2)
        except Exception as e:
            logger.debug(f"get_entry_score 실패: {e}")
            return None
