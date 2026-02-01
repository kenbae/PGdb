# -*- coding: utf-8 -*-
"""
EMA Cross + HTF 정렬 전략
- EMA20/50 크로스오버 감지
- 상위 타임프레임 레짐 확인
- 정배열/역배열 필터
- run_signals.py 로직 기반
"""

import logging
from typing import Dict, List, Optional
import pandas as pd
from datetime import datetime

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

    def __init__(self, name: str, config: Dict):
        """
        초기화

        Args:
            name: 전략 이름
            config: 전략 설정
                - timeframe: 분석 타임프레임 (기본 30m)
                - htf_timeframes: 상위 타임프레임 목록 (예: ['4h', '1d'])
                - score_threshold: 최소 점수 (0-1, 기본 0.7)
                - atr_multiplier: ATR 배수 (기본 1.5)
                - use_rsi_filter: RSI 필터 사용 (기본 True)
                - use_vwap_filter: VWAP 필터 사용 (기본 True)
        """
        super().__init__(name, config)

        self.htf_timeframes = config.get('htf_timeframes', ['4h', '1d'])
        self.score_threshold = config.get('score_threshold', 0.7)
        self.atr_multiplier = config.get('atr_multiplier', 1.5)
        self.use_rsi_filter = config.get('use_rsi_filter', True)
        self.use_vwap_filter = config.get('use_vwap_filter', True)

        logger.info(f"EMACrossStrategy 초기화: threshold={self.score_threshold}, ATR mult={self.atr_multiplier}")

    def get_required_indicators(self) -> List[str]:
        """필요한 지표 목록"""
        return ['ema20', 'ema50', 'ema200', 'atr', 'rsi', 'vwap']

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
            # 지표 계산
            df = TechnicalIndicators.calculate_all(df, inplace=False)

            # EMA 크로스 감지
            df = TechnicalIndicators.detect_ema_cross(df)

            # 최신 캔들 확인
            current = df.iloc[-1]
            prev = df.iloc[-2]

            # 골든 크로스 (매수 신호)
            if current['golden_cross']:
                signal = self._evaluate_buy_signal(symbol, timeframe, df, current)
                if signal:
                    signals.append(signal)

            # 데드 크로스 (매도 신호)
            if current['dead_cross']:
                signal = self._evaluate_sell_signal(symbol, timeframe, df, current)
                if signal:
                    signals.append(signal)

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

        # 3. VWAP 위치 (+10점)
        if self.use_vwap_filter:
            vwap = current.get('vwap')
            close = current['close']
            if vwap and not pd.isna(vwap):
                if close > vwap:
                    score += 0.1
                    reasons.append("가격 > VWAP")

        # 4. 거래량 확인 (+10점)
        if len(df) >= 20:
            avg_volume = df['volume'].tail(20).mean()
            if current['volume'] > avg_volume * 1.2:
                score += 0.1
                reasons.append("거래량 증가")

        # 5. 추세 강도 (+10점) - EMA 간격
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

        # 3. VWAP 위치 (+10점)
        if self.use_vwap_filter:
            vwap = current.get('vwap')
            close = current['close']
            if vwap and not pd.isna(vwap):
                if close < vwap:
                    score += 0.1
                    reasons.append("가격 < VWAP")

        # 4. 거래량 확인 (+10점)
        if len(df) >= 20:
            avg_volume = df['volume'].tail(20).mean()
            if current['volume'] > avg_volume * 1.2:
                score += 0.1
                reasons.append("거래량 증가")

        # 5. 추세 강도 (+10점)
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
