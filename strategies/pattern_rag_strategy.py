# -*- coding: utf-8 -*-
"""
Pattern RAG 전략

과거 캔들 패턴을 학습하고, 유사 패턴을 찾아
OLLAMA AI로 신호를 생성하는 전략입니다.

특징:
- 과거 데이터 기반 패턴 학습
- 코사인 유사도 기반 패턴 매칭
- OLLAMA AI 분석 통합
- 자동 학습 및 신호 생성
"""

import logging
import sys
import os
from typing import Dict, List, Optional, Any
import pandas as pd
from datetime import datetime

# 경로 설정
_current_dir = os.path.dirname(os.path.abspath(__file__))
_pgdb_dir = os.path.dirname(_current_dir)
_tradebot_dir = os.path.join(_pgdb_dir, 'tradeBot')

if _pgdb_dir not in sys.path:
    sys.path.insert(0, _pgdb_dir)
if _tradebot_dir not in sys.path:
    sys.path.insert(0, _tradebot_dir)

from .base import BaseStrategy, TradeSignal

# PatternRAG 임포트 (tradeBot 내부)
try:
    from tradeBot.ai.pattern_rag import PatternRAGSignalGenerator, PatternDatabase
except ImportError:
    # 직접 경로로 임포트
    sys.path.insert(0, os.path.join(_tradebot_dir, 'ai'))
    from pattern_rag import PatternRAGSignalGenerator, PatternDatabase

logger = logging.getLogger(__name__)


class PatternRAGStrategy(BaseStrategy):
    """
    Pattern RAG 전략

    과거 데이터에서 패턴을 학습하고, 현재 패턴과 유사한
    과거 사례를 찾아 OLLAMA AI로 신호를 생성합니다.

    설정 옵션:
        - ollama_url: OLLAMA 서버 주소
        - ollama_model: 모델 이름
        - top_k: 유사 패턴 검색 개수
        - min_confidence: 최소 신뢰도
        - min_patterns: 최소 유사 패턴 수
        - auto_learn: 자동 학습 여부
        - learn_on_no_patterns: 패턴 없을 때 자동 학습
    """

    CONFIG_SCHEMA = {
        'enabled': {'label': '활성화', 'type': 'checkbox', 'default': True},
        'use_ai': {'label': '🤖 AI 검증', 'type': 'checkbox', 'default': False},
        'timeframe': {'label': '타임프레임', 'type': 'select', 'options': ['30m', '1h', '4h'], 'default': '1h'},
        'top_k': {'label': '유사 패턴 개수', 'type': 'number', 'min': 3, 'max': 30, 'step': 1, 'default': 10},
        'min_confidence': {'label': '최소 신뢰도', 'type': 'number', 'min': 0, 'max': 1, 'step': 0.1, 'default': 0.6},
        'min_patterns': {'label': '최소 패턴 수', 'type': 'number', 'min': 1, 'max': 20, 'step': 1, 'default': 5},
        'min_avg_return': {'label': '최소 평균수익률 (%)', 'type': 'number', 'min': 0, 'max': 50, 'step': 1, 'default': 10.0},
        'auto_learn': {'label': '자동 학습', 'type': 'checkbox', 'default': True},
        'atr_multiplier': {'label': 'ATR 배수', 'type': 'number', 'min': 0.5, 'max': 5, 'step': 0.1, 'default': 2.0},
    }

    def __init__(self, name: str, config: Dict):
        """
        초기화

        Args:
            name: 전략 이름
            config: 전략 설정
        """
        super().__init__(name, config)

        # OLLAMA 설정
        self.ollama_url = config.get('ollama_url', 'http://localhost:11434')
        self.ollama_model = config.get('ollama_model', 'qwen2.5:7b-instruct')

        # RAG 설정
        self.top_k = config.get('top_k', 10)
        self.min_confidence = config.get('min_confidence', 0.6)
        self.min_patterns = config.get('min_patterns', 5)
        self.min_avg_return = config.get('min_avg_return', 0.0)  # 최소 평균 수익률 (%)
        self.auto_learn = config.get('auto_learn', True)
        self.learn_on_no_patterns = config.get('learn_on_no_patterns', True)

        # ATR 설정
        self.atr_multiplier = config.get('atr_multiplier', 2.0)

        # 백테스트 모드 (skip_ai 사용)
        self.backtest_mode = config.get('backtest_mode', False)

        # RAG 생성기 (지연 초기화)
        self._rag_generator = None
        self._learned_symbols = set()

        logger.info(f"PatternRAGStrategy 초기화: model={self.ollama_model}, top_k={self.top_k}, min_avg_return={self.min_avg_return}%, backtest_mode={self.backtest_mode}")

    @property
    def rag_generator(self) -> PatternRAGSignalGenerator:
        """RAG 생성기 (지연 초기화)"""
        if self._rag_generator is None:
            self._rag_generator = PatternRAGSignalGenerator(
                ollama_host=self.ollama_url,
                ollama_model=self.ollama_model,
                top_k=self.top_k
            )
        return self._rag_generator

    def get_required_indicators(self) -> List[str]:
        """필요한 지표 목록"""
        return ['ema20', 'ema50', 'ema200', 'rsi', 'atr', 'bb_upper', 'bb_lower']

    def get_indicator_display_schema(self) -> List[Dict]:
        """Pattern RAG 전략 지표 표시 스키마 (기본 EMA/RSI/BB)"""
        return [
            {"key": "current_price", "label": "현재가", "align": "right", "format": "price"},
            {"key": "ema_20", "label": "EMA20", "align": "right", "format": "price"},
            {"key": "ema_50", "label": "EMA50", "align": "right", "format": "price"},
            {"key": "rsi", "label": "RSI", "align": "right", "format": "number"},
            {"key": "bb_upper", "label": "BB 상단", "align": "right", "format": "price"},
            {"key": "bb_lower", "label": "BB 하단", "align": "right", "format": "price"},
            {"key": "atr", "label": "ATR", "align": "right", "format": "price"},
        ]

    def compute_realtime_indicators(self, candles: List[Dict]) -> Optional[Dict]:
        """Pattern RAG용 BB 실시간 계산 (AdaptiveExitManager에 bb 없음)"""
        if len(candles) < 25:
            return None
        try:
            from indicators.technical import TechnicalIndicators
            df = pd.DataFrame(candles)
            for c in ['open', 'high', 'low', 'close', 'volume']:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors='coerce')
            df = TechnicalIndicators.calculate_all(df, inplace=False)
            last = df.iloc[-1]
            out = {}
            if 'bb_upper' in df.columns and not pd.isna(last.get('bb_upper')):
                out['bb_upper'] = float(last['bb_upper'])
            if 'bb_lower' in df.columns and not pd.isna(last.get('bb_lower')):
                out['bb_lower'] = float(last['bb_lower'])
            return out if out else None
        except Exception:
            return None

    def analyze(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame
    ) -> List[TradeSignal]:
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

            # 심볼 키
            symbol_key = f"{symbol}_{timeframe}"

            # 자동 학습 (처음 분석하는 심볼이면)
            if self.auto_learn and symbol_key not in self._learned_symbols:
                self._auto_learn(symbol, timeframe, df)
                self._learned_symbols.add(symbol_key)

            # RAG 신호 생성 (백테스트 모드면 AI 스킵)
            rag_signal = self.rag_generator.generate_signal(
                df, symbol, timeframe, skip_ai=self.backtest_mode
            )

            # 유사 패턴이 없으면 학습 시도
            if rag_signal.get('similar_patterns', 0) < self.min_patterns:
                if self.learn_on_no_patterns:
                    logger.info(f"{symbol} 유사 패턴 부족 ({rag_signal.get('similar_patterns', 0)}개) - 학습 시작")
                    self._auto_learn(symbol, timeframe, df)

                    # 다시 신호 생성 (백테스트 모드면 AI 스킵)
                    rag_signal = self.rag_generator.generate_signal(
                        df, symbol, timeframe, skip_ai=self.backtest_mode
                    )

            # 신호 변환
            signal = self._convert_to_trade_signal(symbol, timeframe, df, rag_signal)

            if signal:
                signals.append(signal)

        except Exception as e:
            logger.error(f"❌ {symbol} {timeframe} PatternRAG 분석 실패: {e}")
            import traceback
            traceback.print_exc()

        return signals

    def _auto_learn(self, symbol: str, timeframe: str, df: pd.DataFrame):
        """자동 학습"""
        try:
            # 학습용 데이터 (마지막 50개 제외)
            learn_df = df.iloc[:-50] if len(df) > 100 else df

            saved = self.rag_generator.learn_from_data(learn_df, symbol, timeframe)
            logger.info(f"✅ {symbol} {timeframe} 자동 학습 완료: {saved}개 패턴")

        except Exception as e:
            logger.error(f"❌ {symbol} {timeframe} 자동 학습 실패: {e}")

    def _convert_to_trade_signal(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        rag_signal: Dict
    ) -> Optional[TradeSignal]:
        """RAG 신호를 TradeSignal로 변환"""
        signal_type = rag_signal.get('signal', 'hold')
        confidence = rag_signal.get('confidence', 0)

        # hold 또는 신뢰도 부족
        if signal_type == 'hold' or confidence < self.min_confidence:
            return None

        # 유사 패턴 부족
        if rag_signal.get('similar_patterns', 0) < self.min_patterns:
            logger.debug(f"{symbol} 유사 패턴 부족: {rag_signal.get('similar_patterns', 0)}개")
            return None

        # 평균 수익률 필터링 (음수는 무조건 필터, 양수만 min_avg_return 이상 허용)
        pattern_stats = rag_signal.get('pattern_stats', {})
        avg_return = pattern_stats.get('avg_return', 0)
        if avg_return < self.min_avg_return:
            logger.debug(f"{symbol} 평균 수익률 부족: {avg_return:.2f}% < {self.min_avg_return}%")
            return None

        entry_price = rag_signal.get('entry_price', df.iloc[-1]['close'])
        stop_loss = rag_signal.get('stop_loss')
        take_profit = rag_signal.get('take_profit')

        # SL/TP가 없으면 ATR 기반 계산
        if stop_loss is None or take_profit is None:
            atr = df.iloc[-1].get('atr', 0)
            if pd.isna(atr) or atr == 0:
                atr = self.calculate_atr(df)

            stop_loss, tp1, tp2 = self.calculate_sl_tp(
                entry_price, atr, signal_type, self.atr_multiplier
            )
            take_profit = tp1

        # 이유 생성
        reasons = []
        reasons.append(f"RAG 유사 패턴 {rag_signal.get('similar_patterns', 0)}개 분석")

        pattern_stats = rag_signal.get('pattern_stats', {})
        up_ratio = pattern_stats.get('up_ratio', 0.5)
        avg_return = pattern_stats.get('avg_return', 0)

        if signal_type == 'buy':
            reasons.append(f"상승 확률 {up_ratio*100:.0f}%")
        else:
            reasons.append(f"하락 확률 {(1-up_ratio)*100:.0f}%")

        reasons.append(f"평균 수익률 {avg_return:.2f}%")

        ai_reasoning = rag_signal.get('reasoning', '')
        if ai_reasoning:
            reasons.append(f"AI: {ai_reasoning}")

        # 가격 정밀도 유지 (8자리)
        stop_loss = round(stop_loss, 8) if stop_loss else None
        take_profit = round(take_profit, 8) if take_profit else None

        # 신호 생성
        return self.create_signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type=signal_type,
            entry_price=round(entry_price, 8),
            stop_loss=stop_loss,
            take_profit_1=take_profit,
            take_profit_2=round(take_profit * 1.5, 8) if take_profit else None,
            confidence=confidence,
            reasons=reasons,
            metadata={
                'strategy': 'pattern_rag',
                'similar_patterns': rag_signal.get('similar_patterns', 0),
                'pattern_stats': pattern_stats,
                'risk_level': rag_signal.get('risk_level', 'medium'),
                'ai_analysis': rag_signal.get('ai_analysis', {})
            }
        )

    def learn_symbol(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame
    ) -> int:
        """
        수동 학습

        Args:
            symbol: 심볼
            timeframe: 타임프레임
            df: OHLCV 데이터프레임

        Returns:
            저장된 패턴 수
        """
        return self.rag_generator.learn_from_data(df, symbol, timeframe)

    def get_pattern_stats(
        self,
        symbol: str = None,
        timeframe: str = None
    ) -> Dict:
        """
        패턴 통계 조회

        Args:
            symbol: 필터링할 심볼
            timeframe: 필터링할 타임프레임

        Returns:
            통계 딕셔너리
        """
        return self.rag_generator.db.get_stats(symbol, timeframe)
