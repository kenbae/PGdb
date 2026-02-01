# -*- coding: utf-8 -*-
"""
통합 전략 베이스 클래스
- TradeSignal: 표준 신호 데이터 클래스
- BaseStrategy: 모든 전략의 추상 베이스 클래스
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    """표준화된 거래 신호 데이터 클래스"""

    # 필수 필드
    strategy_name: str
    symbol: str
    timeframe: str
    signal_type: str              # 'buy' or 'sell'
    entry_price: float
    stop_loss: float
    take_profit_1: float

    # 선택 필드
    take_profit_2: Optional[float] = None
    take_profit_3: Optional[float] = None
    confidence: float = 0.5       # 0.0 ~ 1.0
    risk_reward: float = 0.0
    reasons: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    is_active: bool = True

    # AI 분석 결과 (선택적)
    ai_decision: Optional[str] = None       # 'approve', 'reject', 'caution'
    ai_confidence: Optional[float] = None
    ai_reasoning: Optional[str] = None

    def __post_init__(self):
        """초기화 후 처리"""
        # risk_reward 자동 계산
        if self.risk_reward == 0.0 and self.entry_price and self.stop_loss and self.take_profit_1:
            risk = abs(self.entry_price - self.stop_loss)
            reward = abs(self.take_profit_1 - self.entry_price)
            if risk > 0:
                self.risk_reward = round(reward / risk, 2)

    def to_dict(self) -> Dict:
        """딕셔너리 변환"""
        return {
            'strategy_name': self.strategy_name,
            'symbol': self.symbol,
            'timeframe': self.timeframe,
            'signal_type': self.signal_type,
            'entry_price': self.entry_price,
            'stop_loss': self.stop_loss,
            'take_profit_1': self.take_profit_1,
            'take_profit_2': self.take_profit_2,
            'take_profit_3': self.take_profit_3,
            'confidence': self.confidence,
            'risk_reward': self.risk_reward,
            'reasons': self.reasons,
            'metadata': self.metadata,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_active': self.is_active,
            'ai_decision': self.ai_decision,
            'ai_confidence': self.ai_confidence,
            'ai_reasoning': self.ai_reasoning,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'TradeSignal':
        """딕셔너리에서 생성"""
        created_at = data.get('created_at')
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now()

        return cls(
            strategy_name=data['strategy_name'],
            symbol=data['symbol'],
            timeframe=data['timeframe'],
            signal_type=data['signal_type'],
            entry_price=data['entry_price'],
            stop_loss=data['stop_loss'],
            take_profit_1=data['take_profit_1'],
            take_profit_2=data.get('take_profit_2'),
            take_profit_3=data.get('take_profit_3'),
            confidence=data.get('confidence', 0.5),
            risk_reward=data.get('risk_reward', 0.0),
            reasons=data.get('reasons', []),
            metadata=data.get('metadata', {}),
            created_at=created_at,
            is_active=data.get('is_active', True),
            ai_decision=data.get('ai_decision'),
            ai_confidence=data.get('ai_confidence'),
            ai_reasoning=data.get('ai_reasoning'),
        )

    def generate_signal_id(self) -> str:
        """고유 신호 ID 생성"""
        timestamp = self.created_at.strftime('%Y%m%d%H%M%S') if self.created_at else datetime.now().strftime('%Y%m%d%H%M%S')
        return f"{self.symbol}_{self.timeframe}_{self.signal_type}_{timestamp}"


class BaseStrategy(ABC):
    """
    전략 추상 베이스 클래스

    모든 전략은 이 클래스를 상속받아 구현해야 합니다.
    """

    # 전략 설정 스키마 (서브클래스에서 오버라이드)
    # 형식: {param_name: {label, type, default, min, max, step, options}}
    CONFIG_SCHEMA: Dict[str, Dict[str, Any]] = {
        'enabled': {'label': '활성화', 'type': 'checkbox', 'default': True},
        'use_ai': {'label': '🤖 AI 검증', 'type': 'checkbox', 'default': False},
        'timeframe': {'label': '타임프레임', 'type': 'select', 'options': ['5m', '15m', '30m', '1h', '4h', '1d'], 'default': '15m'},
        'atr_multiplier': {'label': 'ATR 배수', 'type': 'number', 'min': 0.5, 'max': 5, 'step': 0.1, 'default': 2.0},
        'min_confidence': {'label': '최소 신뢰도', 'type': 'number', 'min': 0, 'max': 1, 'step': 0.1, 'default': 0.5},
    }

    @classmethod
    def get_config_schema(cls) -> Dict[str, Dict[str, Any]]:
        """
        전략 설정 스키마 반환 (웹 UI 동적 생성용)

        Returns:
            Dict: 설정 항목별 스키마
                {
                    'param_name': {
                        'label': '표시 이름',
                        'type': 'checkbox' | 'number' | 'select',
                        'default': 기본값,
                        'min': 최소값 (number),
                        'max': 최대값 (number),
                        'step': 단계 (number),
                        'options': ['옵션1', '옵션2'] (select)
                    }
                }
        """
        return cls.CONFIG_SCHEMA

    def __init__(self, name: str, config: Dict):
        """
        전략 초기화

        Args:
            name: 전략 이름
            config: 전략 설정 (config.yaml의 strategies 섹션)
        """
        self.name = name
        self.config = config or {}

        # 런타임 필드 초기화 (config와 동기화)
        self.enabled: bool = True
        self.use_ai: bool = False
        self.timeframe: str = '15m'
        self.atr_multiplier: float = 2.0
        self.min_confidence: float = 0.5
        self._sync_runtime_from_config()

        logger.info(f"전략 초기화: {name} (enabled={self.enabled}, use_ai={self.use_ai})")

    @abstractmethod
    def analyze(self, symbol: str, timeframe: str, df: pd.DataFrame) -> List[TradeSignal]:
        """
        신호 분석 (필수 구현)

        Args:
            symbol: 거래 심볼 (예: BTCUSDT)
            timeframe: 타임프레임 (예: 15m)
            df: OHLCV 데이터프레임 (open, high, low, close, volume, open_time)

        Returns:
            List[TradeSignal]: 생성된 신호 목록
        """
        pass

    @abstractmethod
    def get_required_indicators(self) -> List[str]:
        """
        필요한 지표 목록 반환

        Returns:
            List[str]: 필요한 지표 이름 목록
                예: ['ema20', 'ema50', 'rsi', 'atr']
        """
        pass

    def get_name(self) -> str:
        """전략 이름 반환"""
        return self.name

    def get_indicator_display_schema(self) -> List[Dict[str, Any]]:
        """
        실행 종목 지표 패널에 표시할 컬럼 스키마 (전략별 동적 표시용)

        Returns:
            List[Dict]: 컬럼 정의 목록
                [
                    {"key": "ema_20", "label": "EMA20", "align": "right", "format": "price"},
                    {"key": "alignment", "label": "정/역배열", "align": "center", "format": "text", "color_key": "alignment"},
                ]
                format: "price" | "number" | "percent" | "text"
                color_key: 정/역배열 등 특정 키에 따라 색상 적용 시 사용
        """
        return [
            {"key": "current_price", "label": "현재가", "align": "right", "format": "price"},
            {"key": "ema_20", "label": "EMA20", "align": "right", "format": "price"},
            {"key": "ema_50", "label": "EMA50", "align": "right", "format": "price"},
            {"key": "alignment", "label": "정/역배열", "align": "center", "format": "text", "color_key": "alignment"},
            {"key": "rsi", "label": "RSI", "align": "right", "format": "number"},
            {"key": "ema_spread", "label": "EMA SPREAD(%)", "align": "right", "format": "percent"},
        ]

    def compute_realtime_indicators(self, candles: List[Dict]) -> Optional[Dict[str, Any]]:
        """
        실시간 모니터링용 전략별 지표 계산 (선택 오버라이드)

        청산 매니저 기본 지표에 덧붙여 전략 고유 지표를 계산합니다.
        엔진에서 exit_manager.get_indicators() 결과와 병합됩니다.

        Args:
            candles: 실시간 캔들 목록 [{'open','high','low','close','volume','open_time'}, ...]

        Returns:
            Optional[Dict]: 추가할 지표 {key: value}. None이면 추가 없음.
        """
        return None

    def get_config(self) -> Dict:
        """전략 설정 반환"""
        return self.config

    def is_enabled(self) -> bool:
        """전략 활성화 여부"""
        return self.enabled

    def set_enabled(self, enabled: bool):
        """전략 활성화/비활성화"""
        self.enabled = enabled
        logger.info(f"전략 {self.name}: enabled={enabled}")

    def update_config(self, new_config: Dict):
        """설정 업데이트"""
        self.config.update(new_config or {})
        self._sync_runtime_from_config()
        self.on_config_updated()
        logger.info(f"전략 {self.name} 설정 업데이트됨 (enabled={self.enabled}, use_ai={self.use_ai})")

    def on_config_updated(self):
        """
        설정 업데이트 훅 (서브클래스에서 선택적으로 오버라이드)

        - config 변경 후 파생 값 재계산 등
        """
        return

    def _sync_runtime_from_config(self):
        """config → 런타임 필드 동기화 (BaseStrategy 공통)"""
        cfg = self.config or {}

        # 공통 필드
        self.enabled = bool(cfg.get('enabled', True))
        self.use_ai = bool(cfg.get('use_ai', False))
        self.timeframe = str(cfg.get('timeframe', self.timeframe or '15m'))

        # 숫자 필드 (안전 캐스팅)
        try:
            self.atr_multiplier = float(cfg.get('atr_multiplier', self.atr_multiplier if self.atr_multiplier is not None else 2.0))
        except Exception:
            logger.warning(f"{self.name}: atr_multiplier 파싱 실패 → 기본값 유지")

        try:
            self.min_confidence = float(cfg.get('min_confidence', self.min_confidence if self.min_confidence is not None else 0.5))
        except Exception:
            logger.warning(f"{self.name}: min_confidence 파싱 실패 → 기본값 유지")

    def get_status(self) -> Dict:
        """전략 상태 반환"""
        return {
            'name': self.name,
            'enabled': self.enabled,
            'use_ai': self.use_ai,
            'timeframe': self.timeframe,
            'config': self.config
        }

    # ============================================================
    # 공통 유틸리티 메서드
    # ============================================================

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """
        ATR (Average True Range) 계산

        Args:
            df: OHLCV 데이터프레임
            period: ATR 기간 (기본 14)

        Returns:
            float: 최신 ATR 값
        """
        high = df['high']
        low = df['low']
        close = df['close']

        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]

        return float(atr) if not pd.isna(atr) else 0.0

    def calculate_sl_tp(
        self,
        entry_price: float,
        atr: float,
        signal_type: str,
        atr_multiplier: float = None,
        tp_ratio: float = 2.0
    ) -> tuple:
        """
        ATR 기반 손절/익절 계산

        Args:
            entry_price: 진입 가격
            atr: ATR 값
            signal_type: 'buy' or 'sell'
            atr_multiplier: ATR 배수 (기본: self.atr_multiplier)
            tp_ratio: TP/SL 비율 (기본 2.0)

        Returns:
            tuple: (stop_loss, take_profit_1, take_profit_2)
        """
        if atr_multiplier is None:
            atr_multiplier = self.atr_multiplier

        sl_distance = atr * atr_multiplier

        if signal_type == 'buy':
            stop_loss = entry_price - sl_distance
            take_profit_1 = entry_price + (sl_distance * tp_ratio)
            take_profit_2 = entry_price + (sl_distance * tp_ratio * 1.5)
        else:  # sell
            stop_loss = entry_price + sl_distance
            take_profit_1 = entry_price - (sl_distance * tp_ratio)
            take_profit_2 = entry_price - (sl_distance * tp_ratio * 1.5)

        return round(stop_loss, 8), round(take_profit_1, 8), round(take_profit_2, 8)

    def create_signal(
        self,
        symbol: str,
        timeframe: str,
        signal_type: str,
        entry_price: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float = None,
        confidence: float = 0.5,
        reasons: List[str] = None,
        metadata: Dict = None
    ) -> TradeSignal:
        """
        TradeSignal 객체 생성 헬퍼

        Args:
            symbol: 심볼
            timeframe: 타임프레임
            signal_type: 'buy' or 'sell'
            entry_price: 진입가
            stop_loss: 손절가
            take_profit_1: 익절가 1
            take_profit_2: 익절가 2 (선택)
            confidence: 신뢰도 (0-1)
            reasons: 신호 근거
            metadata: 추가 메타데이터

        Returns:
            TradeSignal: 생성된 신호 객체
        """
        # 가격 정밀도 보장 (8자리) - 저가 코인 지원
        return TradeSignal(
            strategy_name=self.name,
            symbol=symbol,
            timeframe=timeframe,
            signal_type=signal_type,
            entry_price=round(entry_price, 8) if entry_price else entry_price,
            stop_loss=round(stop_loss, 8) if stop_loss else stop_loss,
            take_profit_1=round(take_profit_1, 8) if take_profit_1 else take_profit_1,
            take_profit_2=round(take_profit_2, 8) if take_profit_2 else take_profit_2,
            confidence=confidence,
            reasons=reasons or [],
            metadata=metadata or {},
            created_at=datetime.now()
        )

    def validate_dataframe(self, df: pd.DataFrame, min_rows: int = 50) -> bool:
        """
        데이터프레임 유효성 검사

        Args:
            df: 검사할 데이터프레임
            min_rows: 최소 행 수

        Returns:
            bool: 유효 여부
        """
        if df is None or df.empty:
            logger.warning(f"{self.name}: 데이터프레임이 비어있음")
            return False

        if len(df) < min_rows:
            logger.warning(f"{self.name}: 데이터 부족 ({len(df)} < {min_rows})")
            return False

        required_cols = ['open', 'high', 'low', 'close', 'volume']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            logger.warning(f"{self.name}: 필수 컬럼 누락: {missing_cols}")
            return False

        return True
