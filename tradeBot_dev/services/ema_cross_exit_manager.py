"""
EMA Cross 전략 전용 청산 매니저

청산 규칙:
1. TP1 도달 시 N% 부분 청산
2. TP2 도달 시 SL을 본절로 이동 (트레일링 스톱)
3. EMA50에 닿았을 때 익절
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from brokers.base import Position, PositionStatus, OrderSide
import pandas as pd

logger = logging.getLogger(__name__)


class EMACrossExitManager:
    """EMA Cross 전략 전용 청산 매니저"""

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        tp1_partial_exit_pct: float = 50.0,  # TP1에서 부분 청산 비율 (%)
        use_trailing_stop: bool = True,  # TP2 도달 후 트레일링 스톱 사용
        trailing_stop_atr_multiplier: float = 1.0,  # 트레일링 스톱 ATR 배수
        exit_on_ema50_touch: bool = True  # EMA50 터치 시 청산
    ):
        """
        초기화

        Args:
            symbol: 거래 심볼
            timeframe: 타임프레임
            tp1_partial_exit_pct: TP1 도달 시 부분 청산 비율 (0-100)
            use_trailing_stop: TP2 도달 후 트레일링 스톱 사용 여부
            trailing_stop_atr_multiplier: 트레일링 스톱 ATR 배수
            exit_on_ema50_touch: EMA50 터치 시 청산 여부
        """
        self.symbol = symbol
        self.timeframe = timeframe
        self.tp1_partial_exit_pct = tp1_partial_exit_pct
        self.use_trailing_stop = use_trailing_stop
        self.trailing_stop_atr_multiplier = trailing_stop_atr_multiplier
        self.exit_on_ema50_touch = exit_on_ema50_touch

        # 포지션 상태 추적
        self.position_states: Dict[str, Dict] = {}  # position_id -> 상태 정보

        # 실시간 캔들 데이터 (EMA 계산용)
        self.realtime_candles: List[Dict] = []
        self.max_candles = 200

        # 기술적 지표 캐시
        self.indicators_cache: Dict[str, float] = {}

        logger.info(f"🔄 EMACrossExitManager 초기화: {symbol} {timeframe}")
        logger.info(f"   TP1 부분 청산: {tp1_partial_exit_pct}%")
        logger.info(f"   트레일링 스톱: {use_trailing_stop} (ATR × {trailing_stop_atr_multiplier})")
        logger.info(f"   EMA50 터치 청산: {exit_on_ema50_touch}")

    def reset_candles(self):
        """캔들 데이터 초기화 (최신 데이터로 재시작 시 사용)"""
        self.realtime_candles = []
        self.indicators_cache = {}
        logger.info("🔄 EMACrossExitManager: 캔들 데이터 초기화 완료")

    def update_realtime_candle(self, candle: Dict):
        """실시간 캔들 업데이트"""
        open_time = candle.get('open_time')
        if open_time:
            # 중복 제거
            self.realtime_candles = [c for c in self.realtime_candles if c.get('open_time') != open_time]
        
        self.realtime_candles.append(candle)
        
        if len(self.realtime_candles) > self.max_candles:
            self.realtime_candles = self.realtime_candles[-self.max_candles:]
        
        # 지표 업데이트
        self._update_indicators()

    def _update_indicators(self):
        """기술적 지표 업데이트"""
        if len(self.realtime_candles) < 50:
            return

        try:
            df = pd.DataFrame(self.realtime_candles)
            df = df.drop_duplicates(subset=['open_time'], keep='last')
            df = df.sort_values('open_time').reset_index(drop=True)

            if len(df) > 100:
                df = df.tail(100).copy()

            # EMA 20, 50 계산
            closes = df['close'].dropna()
            if len(closes) >= 50:
                ema_series_50 = closes.ewm(span=50, adjust=False).mean()
                ema_50 = float(ema_series_50.iloc[-1])
                self.indicators_cache['ema_50'] = ema_50
                if len(closes) >= 20:
                    ema_series_20 = closes.ewm(span=20, adjust=False).mean()
                    ema_20 = float(ema_series_20.iloc[-1])
                    self.indicators_cache['ema_20'] = ema_20
                    if ema_50 != 0:
                        self.indicators_cache['ema_spread'] = round((ema_20 - ema_50) / ema_50 * 100, 4)
                # RSI(14)
                if len(closes) >= 15:
                    deltas = closes.diff()
                    gains = deltas.where(deltas > 0, 0.0)
                    losses = (-deltas).where(deltas < 0, 0.0)
                    avg_gain = gains.ewm(alpha=1.0 / 14, min_periods=14).mean().iloc[-1]
                    avg_loss = losses.ewm(alpha=1.0 / 14, min_periods=14).mean().iloc[-1]
                    if avg_loss == 0:
                        rsi = 100.0
                    else:
                        rsi = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
                    self.indicators_cache['rsi'] = round(float(rsi), 2)
                # 정배열/역배열 (ema_20 vs ema_50만 사용, ema_100 없음)
                ema_20 = self.indicators_cache.get('ema_20')
                if ema_20 is not None:
                    self.indicators_cache['alignment'] = '정배열' if ema_20 > ema_50 else '역배열'

                if len(df) > 0:
                    current_price = float(df.iloc[-1]['close'])
                    self.indicators_cache['current_price'] = current_price
                    
                    # ATR 계산 (트레일링 스톱용)
                    try:
                        high = df['high']
                        low = df['low']
                        close = df['close']
                        tr1 = high - low
                        tr2 = abs(high - close.shift())
                        tr3 = abs(low - close.shift())
                        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                        atr = tr.rolling(14).mean().iloc[-1]
                        if not pd.isna(atr):
                            self.indicators_cache['atr'] = float(atr)
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"❌ 지표 계산 실패: {e}", exc_info=True)

    def should_exit(
        self,
        position: Position,
        current_price: float,
        market_data: Optional[Dict] = None
    ) -> Tuple[bool, Optional[str], Optional[float]]:
        """
        청산 여부 결정

        Args:
            position: 현재 포지션
            current_price: 현재 가격
            market_data: 추가 시장 데이터 (선택)

        Returns:
            (should_exit: bool, exit_reason: str or None, exit_quantity: float or None)
            exit_quantity가 None이면 전체 청산, 값이 있으면 부분 청산
        """
        if position.status != PositionStatus.OPEN:
            return False, None, None

        position_id = position.position_id
        
        # 포지션 상태 초기화
        if position_id not in self.position_states:
            self.position_states[position_id] = {
                'tp1_reached': False,
                'tp2_reached': False,
                'original_stop_loss': position.stop_loss,
                'trailing_stop_price': None,
                'partial_exited': False
            }

        state = self.position_states[position_id]

        # 1. TP1 도달 체크 (부분 청산)
        if not state['tp1_reached'] and position.take_profit:
            if position.side == OrderSide.BUY:
                if current_price >= position.take_profit:
                    state['tp1_reached'] = True
                    # 부분 청산
                    exit_quantity = position.quantity * (self.tp1_partial_exit_pct / 100)
                    logger.info(f"📊 TP1 도달: {self.tp1_partial_exit_pct}% 부분 청산 ({exit_quantity:.4f}/{position.quantity:.4f})")
                    return True, "tp1_partial", exit_quantity
            else:  # SELL
                if current_price <= position.take_profit:
                    state['tp1_reached'] = True
                    exit_quantity = position.quantity * (self.tp1_partial_exit_pct / 100)
                    logger.info(f"📊 TP1 도달: {self.tp1_partial_exit_pct}% 부분 청산 ({exit_quantity:.4f}/{position.quantity:.4f})")
                    return True, "tp1_partial", exit_quantity

        # 2. TP2 도달 체크 (트레일링 스톱 활성화)
        if state['tp1_reached'] and not state['tp2_reached']:
            # TP2는 take_profit_2 또는 take_profit_1 * 1.5로 계산
            tp2 = getattr(position, 'take_profit_2', None)
            if tp2 is None and position.take_profit:
                tp2 = position.take_profit * 1.5 if position.side == OrderSide.BUY else position.take_profit * 0.5
            
            if tp2:
                if position.side == OrderSide.BUY:
                    if current_price >= tp2:
                        state['tp2_reached'] = True
                        # SL을 본절로 이동 (트레일링 스톱)
                        if self.use_trailing_stop:
                            # ATR 기반 트레일링 스톱 계산
                            atr = market_data.get('atr') if market_data else None
                            if atr:
                                trailing_distance = atr * self.trailing_stop_atr_multiplier
                                state['trailing_stop_price'] = current_price - trailing_distance
                                logger.info(f"📊 TP2 도달: 트레일링 스톱 활성화 (SL → {state['trailing_stop_price']:.2f})")
                            else:
                                # ATR이 없으면 진입가로 설정
                                state['trailing_stop_price'] = position.entry_price
                                logger.info(f"📊 TP2 도달: SL을 진입가로 이동 ({state['trailing_stop_price']:.2f})")
                else:  # SELL
                    if current_price <= tp2:
                        state['tp2_reached'] = True
                        if self.use_trailing_stop:
                            atr = market_data.get('atr') if market_data else None
                            if atr:
                                trailing_distance = atr * self.trailing_stop_atr_multiplier
                                state['trailing_stop_price'] = current_price + trailing_distance
                                logger.info(f"📊 TP2 도달: 트레일링 스톱 활성화 (SL → {state['trailing_stop_price']:.2f})")
                            else:
                                state['trailing_stop_price'] = position.entry_price
                                logger.info(f"📊 TP2 도달: SL을 진입가로 이동 ({state['trailing_stop_price']:.2f})")

        # 3. 트레일링 스톱 체크
        if state['tp2_reached'] and state['trailing_stop_price']:
            if position.side == OrderSide.BUY:
                if current_price <= state['trailing_stop_price']:
                    logger.info(f"📊 트레일링 스톱 청산: {current_price:.2f} <= {state['trailing_stop_price']:.2f}")
                    return True, "trailing_stop", None
            else:  # SELL
                if current_price >= state['trailing_stop_price']:
                    logger.info(f"📊 트레일링 스톱 청산: {current_price:.2f} >= {state['trailing_stop_price']:.2f}")
                    return True, "trailing_stop", None

        # 4. EMA50 터치 체크
        if self.exit_on_ema50_touch:
            ema_50 = self.indicators_cache.get('ema_50')
            if ema_50:
                if len(self.realtime_candles) > 0:
                    latest_candle = self.realtime_candles[-1]
                    candle_open = latest_candle.get('open', current_price)
                    candle_close = latest_candle.get('close', current_price)
                    body_low = min(candle_open, candle_close)
                    body_high = max(candle_open, candle_close)

                    # EMA50이 캔들 몸통에 닿았는지 확인
                    if body_low <= ema_50 <= body_high:
                        if position.side == OrderSide.BUY:
                            # 롱 포지션: EMA50 터치 시 익절
                            logger.info(f"📊 EMA50 터치 청산: 롱 포지션 (EMA50: {ema_50:.2f})")
                            return True, "ema50_touch", None
                        else:  # SELL
                            # 숏 포지션: EMA50 터치 시 익절
                            logger.info(f"📊 EMA50 터치 청산: 숏 포지션 (EMA50: {ema_50:.2f})")
                            return True, "ema50_touch", None

        # 5. 기본 SL 체크 (트레일링 스톱이 활성화되지 않은 경우만)
        if not state['tp2_reached'] and position.stop_loss:
            if position.side == OrderSide.BUY:
                if current_price <= position.stop_loss:
                    return True, "stop_loss", None
            else:  # SELL
                if current_price >= position.stop_loss:
                    return True, "stop_loss", None

        return False, None, None

    def get_exit_info(self, position: Position, current_price: float) -> Dict:
        """청산 정보 반환 (UI 표시용)"""
        position_id = position.position_id
        state = self.position_states.get(position_id, {})
        ema_50 = self.indicators_cache.get('ema_50')

        info = {
            'current_price': current_price,
            'ema_50': ema_50,
            'stop_loss': position.stop_loss,
            'take_profit': position.take_profit,
            'tp1_reached': state.get('tp1_reached', False),
            'tp2_reached': state.get('tp2_reached', False),
            'trailing_stop_price': state.get('trailing_stop_price'),
            'exit_conditions': []
        }

        # TP1 부분 청산 조건
        if position.take_profit:
            if position.side == OrderSide.BUY:
                distance_to_tp1 = position.take_profit - current_price
                info['exit_conditions'].append({
                    'type': 'tp1_partial',
                    'condition': f'TP1 도달 시 {self.tp1_partial_exit_pct}% 부분 청산',
                    'distance': distance_to_tp1,
                    'reached': state.get('tp1_reached', False)
                })
            else:  # SELL
                distance_to_tp1 = current_price - position.take_profit
                info['exit_conditions'].append({
                    'type': 'tp1_partial',
                    'condition': f'TP1 도달 시 {self.tp1_partial_exit_pct}% 부분 청산',
                    'distance': distance_to_tp1,
                    'reached': state.get('tp1_reached', False)
                })

        # TP2 트레일링 스톱 조건
        if state.get('tp2_reached', False) and state.get('trailing_stop_price'):
            trailing_stop = state['trailing_stop_price']
            if position.side == OrderSide.BUY:
                distance_to_trailing = current_price - trailing_stop
                info['exit_conditions'].append({
                    'type': 'trailing_stop',
                    'condition': f'트레일링 스톱: {trailing_stop:.2f}',
                    'distance': distance_to_trailing,
                    'active': True
                })
            else:  # SELL
                distance_to_trailing = trailing_stop - current_price
                info['exit_conditions'].append({
                    'type': 'trailing_stop',
                    'condition': f'트레일링 스톱: {trailing_stop:.2f}',
                    'distance': distance_to_trailing,
                    'active': True
                })

        # EMA50 터치 조건
        if ema_50 and self.exit_on_ema50_touch:
            if position.side == OrderSide.BUY:
                distance_to_ema = current_price - ema_50
                info['exit_conditions'].append({
                    'type': 'ema50_touch',
                    'condition': f'EMA50 터치 시 익절 (EMA50: {ema_50:.2f})',
                    'distance': distance_to_ema
                })
            else:  # SELL
                distance_to_ema = ema_50 - current_price
                info['exit_conditions'].append({
                    'type': 'ema50_touch',
                    'condition': f'EMA50 터치 시 익절 (EMA50: {ema_50:.2f})',
                    'distance': distance_to_ema
                })

        return info

    def get_indicators(self) -> Dict[str, float]:
        """기술적 지표 반환"""
        return self.indicators_cache.copy()

    def reset_position_state(self, position_id: str):
        """포지션 상태 초기화"""
        if position_id in self.position_states:
            del self.position_states[position_id]
