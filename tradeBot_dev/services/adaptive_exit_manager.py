"""
Adaptive Exit Manager

동적 청산 관리
- 실시간 움직임 분석
- 동적 청산 조건 (EMA 50선 등)
- 실시간 포지션 모니터링
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from brokers.base import Position, PositionStatus, OrderSide

logger = logging.getLogger(__name__)


class AdaptiveExitManager:
    """동적 청산 매니저"""

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        initial_capital: float = 10000.0
    ):
        """
        초기화

        Args:
            symbol: 거래 심볼
            timeframe: 타임프레임
            initial_capital: 초기 자본
        """
        self.symbol = symbol
        self.timeframe = timeframe
        self.initial_capital = initial_capital

        # 실시간 캔들 데이터 (최근 200개)
        self.realtime_candles: List[Dict] = []
        self.max_candles = 200

        # 기술적 지표 캐시
        self.indicators_cache: Dict[str, float] = {}

        logger.info(f"🔄 AdaptiveExitManager 초기화: {symbol} {timeframe}")
    
    def reset_candles(self):
        """캔들 데이터 초기화 (최신 데이터로 재시작 시 사용)"""
        self.realtime_candles = []
        self.indicators_cache = {}
        # 검증 플래그도 리셋
        if hasattr(self, '_ema_calc_verified'):
            delattr(self, '_ema_calc_verified')
        if hasattr(self, '_ema_logged'):
            delattr(self, '_ema_logged')
        if hasattr(self, '_last_ema_log_time'):
            delattr(self, '_last_ema_log_time')
        logger.info("🔄 캔들 데이터 초기화 완료")

    def update_realtime_candle(self, candle: Dict):
        """
        실시간 캔들 업데이트

        Args:
            candle: 캔들 데이터 {
                'open_time': datetime,
                'open': float,
                'high': float,
                'low': float,
                'close': float,
                'volume': float,
                'timeframe': str (선택, 타임프레임 확인용)
            }
        """
        # 타임프레임 확인 (디버깅용)
        candle_tf = candle.get('timeframe', 'unknown')
        if candle_tf != self.timeframe and candle_tf != 'unknown':
            logger.warning(f"⚠️ 타임프레임 불일치: 설정={self.timeframe}, 캔들={candle_tf}")
        
        # 중복 체크 (같은 open_time의 캔들이 있으면 제거)
        open_time = candle.get('open_time')
        if open_time:
            self.realtime_candles = [c for c in self.realtime_candles if c.get('open_time') != open_time]
        
        # 최신 캔들 추가
        self.realtime_candles.append(candle)

        # 최대 개수 유지
        if len(self.realtime_candles) > self.max_candles:
            self.realtime_candles = self.realtime_candles[-self.max_candles:]

        # 기술적 지표 재계산
        self._update_indicators()

    def _update_indicators(self):
        """기술적 지표 업데이트"""
        if len(self.realtime_candles) < 50:
            return

        try:
            # DataFrame 생성
            df = pd.DataFrame(self.realtime_candles)
            
            # 중복 제거 (같은 open_time의 캔들 제거)
            df = df.drop_duplicates(subset=['open_time'], keep='last')
            
            # 시간 순서대로 정렬 (오래된 것부터 최신 순서)
            df = df.sort_values('open_time').reset_index(drop=True)
            
            # 디버깅: 캔들 데이터 검증 (처음 한 번만)
            if not hasattr(self, '_candles_verified') and len(df) >= 50:
                first_5 = df.head(5)[['open_time', 'close']].to_dict('records')
                last_5 = df.tail(5)[['open_time', 'close']].to_dict('records')
                logger.info(f"🔍 캔들 데이터 검증: 총 {len(df)}개")
                first_5_str = [(r['open_time'].strftime('%H:%M'), f"${r['close']:.2f}") for r in first_5]
                last_5_str = [(r['open_time'].strftime('%H:%M'), f"${r['close']:.2f}") for r in last_5]
                logger.debug(f"   첫 5개: {first_5_str}")
                logger.debug(f"   마지막 5개: {last_5_str}")
                self._candles_verified = True

            # 타임프레임 확인 (캔들 데이터에서)
            if 'timeframe' in df.columns:
                unique_tfs = df['timeframe'].dropna().unique()
                if len(unique_tfs) > 0:
                    tf_info = f", 캔들 타임프레임: {', '.join(unique_tfs)}"
                else:
                    tf_info = ""
            else:
                tf_info = ""

            # EMA 계산 (최신 50개만 사용하여 정확도 향상)
            # 최근 50개 이상이면 최근 100개만 사용 (너무 오래된 데이터 제외)
            if len(df) > 100:
                df_recent = df.tail(100).copy()
                logger.debug(f"📊 캔들 데이터가 {len(df)}개이므로 최근 100개만 사용하여 EMA 계산")
            else:
                df_recent = df.copy()

            # EMA 계산
            ema_20 = self._calculate_ema(df_recent, 20)
            ema_50 = self._calculate_ema(df_recent, 50)
            ema_100 = self._calculate_ema(df_recent, 100)
            
            self.indicators_cache['ema_20'] = ema_20
            self.indicators_cache['ema_50'] = ema_50
            self.indicators_cache['ema_100'] = ema_100

            # RSI(14) 계산
            rsi = self._calculate_rsi(df_recent, 14)
            if rsi is not None:
                self.indicators_cache['rsi'] = rsi

            # ATR(14) 계산 (전략 지표 패널용)
            atr = self._calculate_atr(df_recent, 14)
            if atr is not None:
                self.indicators_cache['atr'] = atr

            # 정배열(EMA20 > EMA50 > EMA100) / 역배열(EMA20 < EMA50 < EMA100)
            alignment = None
            if ema_20 is not None and ema_50 is not None and ema_100 is not None:
                if ema_20 > ema_50 > ema_100:
                    alignment = '정배열'
                elif ema_20 < ema_50 < ema_100:
                    alignment = '역배열'
            if alignment:
                self.indicators_cache['alignment'] = alignment

            # EMA 스프레드 (EMA20 대비 EMA50 차이 %): (ema_20 - ema_50) / ema_50 * 100
            if ema_20 is not None and ema_50 is not None and ema_50 != 0:
                ema_spread = (ema_20 - ema_50) / ema_50 * 100
                self.indicators_cache['ema_spread'] = round(ema_spread, 4)

            # 현재 가격
            if len(df_recent) > 0:
                current_price = float(df_recent.iloc[-1]['close'])
                self.indicators_cache['current_price'] = current_price
                
                # 디버깅: EMA 계산 상세 정보 (주기적으로 로그)
                if ema_50:
                    # 최근 10개 캔들의 close 가격 확인
                    recent_closes = df_recent['close'].tail(10).tolist()
                    first_close = df_recent['close'].iloc[0] if len(df_recent) > 0 else None
                    last_close = df_recent['close'].iloc[-1] if len(df_recent) > 0 else None
                    
                    # EMA 계산에 사용된 최근 5개 캔들의 close와 EMA 값
                    if len(df_recent) >= 50:
                        ema_series = df_recent['close'].ewm(span=50, adjust=False).mean()
                        recent_emas = ema_series.tail(5).tolist()
                    else:
                        recent_emas = []
                    
                    # 10초마다 한 번씩만 로그 (너무 많이 출력 방지)
                    import time
                    if not hasattr(self, '_last_ema_log_time'):
                        self._last_ema_log_time = 0
                    
                    current_time = time.time()
                    if current_time - self._last_ema_log_time >= 10:  # 10초마다
                        logger.info(f"📊 EMA 50 상세: 값={ema_50:.8f}, 현재가={current_price:.8f}, 차이={current_price - ema_50:.8f} "
                                  f"(타임프레임: {self.timeframe}{tf_info}, 전체 캔들: {len(df)}, 사용 캔들: {len(df_recent)}, "
                                  f"첫 캔들: {first_close:.8f}, 마지막 캔들: {last_close:.8f})")
                        logger.debug(f"   최근 10개 캔들 close: {[f'{c:.2f}' for c in recent_closes]}")
                        if recent_emas:
                            logger.debug(f"   최근 5개 EMA 50 값: {[f'{e:.2f}' for e in recent_emas]}")
                        self._last_ema_log_time = current_time

        except Exception as e:
            logger.error(f"❌ 지표 계산 실패: {e}", exc_info=True)

    def _calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> Optional[float]:
        """RSI(period) 계산. 최신 값 1개 반환."""
        if len(df) < period + 1:
            return None
        closes = df['close'].dropna()
        if len(closes) < period + 1:
            return None
        deltas = closes.diff()
        gains = deltas.where(deltas > 0, 0.0)
        losses = (-deltas).where(deltas < 0, 0.0)
        avg_gain = gains.ewm(alpha=1.0 / period, min_periods=period).mean().iloc[-1]
        avg_loss = losses.ewm(alpha=1.0 / period, min_periods=period).mean().iloc[-1]
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return round(float(rsi), 2)

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> Optional[float]:
        """ATR(period) 계산. 최신 값 1개 반환."""
        if len(df) < period + 1:
            return None
        high = df['high']
        low = df['low']
        close = df['close']
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean().iloc[-1]
        return float(atr) if not pd.isna(atr) else None

    def _calculate_ema(self, df: pd.DataFrame, period: int) -> float:
        """
        EMA 계산 (TradingView 방식)
        
        TradingView는 최신 데이터를 기준으로 EMA를 계산합니다.
        pandas ewm은 자동으로 최신 데이터에 더 높은 가중치를 부여합니다.
        
        TradingView EMA 공식:
        - 첫 EMA = SMA(period)
        - 이후 EMA = (Close - 이전 EMA) * multiplier + 이전 EMA
        - multiplier = 2 / (period + 1)
        """
        if len(df) < period:
            return None

        # close 가격만 추출 (NaN 제거, 시간 순서 유지)
        closes = df['close'].dropna()
        if len(closes) < period:
            return None
        
        # TradingView 방식으로 직접 계산 (pandas ewm과 동일하지만 검증용)
        # pandas ewm(span=period, adjust=False)는 TradingView와 동일
        ema_series = closes.ewm(span=period, adjust=False).mean()
        ema_value = float(ema_series.iloc[-1])
        
        # 디버깅: EMA 계산 검증 (처음 한 번만)
        if period == 50 and not hasattr(self, '_ema_calc_verified'):
            # 최근 10개 값으로 EMA 추세 확인
            recent_closes = closes.tail(10).tolist()
            recent_emas = ema_series.tail(10).tolist()
            
            # 수동 계산으로 검증 (TradingView 방식)
            multiplier = 2.0 / (period + 1)
            # 첫 EMA는 SMA
            sma = closes.head(period).mean()
            manual_ema = sma
            # 나머지 계산
            for close in closes.iloc[period:]:
                manual_ema = (close - manual_ema) * multiplier + manual_ema
            
            logger.info(f"🔍 EMA 50 계산 검증:")
            logger.info(f"   period={period}, 데이터 수={len(closes)}")
            logger.info(f"   첫 값={closes.iloc[0]:.2f}, 마지막 값={closes.iloc[-1]:.2f}")
            logger.info(f"   pandas EMA={ema_value:.2f}, 수동 계산 EMA={manual_ema:.2f}")
            logger.info(f"   차이={abs(ema_value - manual_ema):.4f}")
            logger.debug(f"   최근 10개 close: {[f'{c:.2f}' for c in recent_closes]}")
            logger.debug(f"   최근 10개 EMA: {[f'{e:.2f}' for e in recent_emas]}")
            self._ema_calc_verified = True
        
        return ema_value

    def should_exit(
        self,
        position: Position,
        current_price: float,
        market_data: Optional[Dict] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        청산 여부 결정

        Args:
            position: 현재 포지션
            current_price: 현재 가격
            market_data: 추가 시장 데이터 (선택)

        Returns:
            (should_exit: bool, exit_reason: str or None)
        """
        if position.status != PositionStatus.OPEN:
            return False, None

        # 기본 SL/TP 체크 (기존 로직)
        if position.stop_loss and current_price:
            if position.side == OrderSide.BUY:
                if current_price <= position.stop_loss:
                    return True, "stop_loss"
            else:  # SELL
                if current_price >= position.stop_loss:
                    return True, "stop_loss"

        if position.take_profit and current_price:
            if position.side == OrderSide.BUY:
                if current_price >= position.take_profit:
                    return True, "take_profit"
            else:  # SELL
                if current_price <= position.take_profit:
                    return True, "take_profit"

        # 동적 청산 조건 체크
        exit_result = self._check_dynamic_exit_conditions(position, current_price)
        if exit_result[0]:
            return exit_result

        return False, None

    def _check_dynamic_exit_conditions(
        self,
        position: Position,
        current_price: float
    ) -> Tuple[bool, Optional[str]]:
        """
        동적 청산 조건 체크

        Args:
            position: 현재 포지션
            current_price: 현재 가격

        Returns:
            (should_exit: bool, exit_reason: str or None)
        """
        if len(self.realtime_candles) < 50:
            return False, None

        try:
            # EMA 50선 체크
            ema_50 = self.indicators_cache.get('ema_50')
            if ema_50:
                # 디버깅: 현재 상태 로그
                position_side = "LONG" if position.side == OrderSide.BUY else "SHORT"
                logger.debug(f"🔍 청산 체크: {position_side} 포지션, 가격={current_price:.8f}, EMA50={ema_50:.8f}, 차이={current_price - ema_50:.8f}")
                # LONG 포지션 (BUY): 가격이 EMA 50선 아래로 떨어지면 청산
                if position.side == OrderSide.BUY:
                    # 현재 캔들의 몸통(open/close)이 EMA 50선과 교차하는지 체크
                    if len(self.realtime_candles) > 0:
                        latest_candle = self.realtime_candles[-1]
                        candle_low = latest_candle.get('low', current_price)
                        candle_high = latest_candle.get('high', current_price)
                        candle_open = latest_candle.get('open', current_price)
                        candle_close = latest_candle.get('close', current_price)

                        # 몸통 범위 (open과 close 사이)
                        body_low = min(candle_open, candle_close)
                        body_high = max(candle_open, candle_close)

                        # EMA 50선이 몸통 범위 내에 있고, 가격이 EMA 아래로 내려가는 추세일 때만 청산
                        # (캔들이 하락 추세: close < open이면 하락 캔들)
                        if body_low <= ema_50 <= body_high:
                            # 하락 추세 확인: close가 open보다 낮거나, 가격이 EMA 아래에 있으면 청산
                            is_downtrend = candle_close < candle_open or current_price < ema_50
                            if is_downtrend:
                                logger.info(f"📊 LONG 포지션: EMA 50선({ema_50:.8f})이 캔들 몸통에 닿고 하락 추세 → 청산")
                                return True, "ema_50_touch"

                    # 가격이 EMA 50선 아래로 떨어지면 청산 (롱 포지션은 가격이 EMA 아래로 내려가면 청산)
                    if current_price < ema_50:
                        logger.info(f"📊 LONG 포지션: 가격({current_price:.8f})이 EMA 50선({ema_50:.8f}) 아래 → 청산")
                        return True, "ema_50_below"

                # SHORT 포지션 (SELL): 가격이 EMA 50선 위로 올라가면 청산
                elif position.side == OrderSide.SELL:
                    if len(self.realtime_candles) > 0:
                        latest_candle = self.realtime_candles[-1]
                        candle_low = latest_candle.get('low', current_price)
                        candle_high = latest_candle.get('high', current_price)
                        candle_open = latest_candle.get('open', current_price)
                        candle_close = latest_candle.get('close', current_price)

                        # 몸통 범위
                        body_low = min(candle_open, candle_close)
                        body_high = max(candle_open, candle_close)

                        # EMA 50선이 몸통 범위 내에 있고, 가격이 EMA 위로 올라가는 추세일 때만 청산
                        # (캔들이 상승 추세: close > open이면 상승 캔들)
                        if body_low <= ema_50 <= body_high:
                            # 상승 추세 확인: close가 open보다 높거나, 가격이 EMA 위에 있으면 청산
                            is_uptrend = candle_close > candle_open or current_price > ema_50
                            if is_uptrend:
                                logger.info(f"📊 SHORT 포지션: EMA 50선({ema_50:.8f})이 캔들 몸통에 닿고 상승 추세 → 청산")
                                return True, "ema_50_touch"

                    # 가격이 EMA 50선 위로 올라가면 청산 (숏 포지션은 가격이 EMA 위로 올라가면 청산)
                    if current_price > ema_50:
                        logger.info(f"📊 SHORT 포지션: 가격({current_price:.8f})이 EMA 50선({ema_50:.8f}) 위 → 청산")
                        return True, "ema_50_above"

            # 추가 동적 청산 조건을 여기에 추가 가능
            # 예: RSI, 볼린저 밴드, 추세 전환 등

        except Exception as e:
            logger.error(f"❌ 동적 청산 조건 체크 실패: {e}", exc_info=True)

        return False, None

    def get_indicators(self) -> Dict[str, float]:
        """현재 기술적 지표 반환"""
        return self.indicators_cache.copy()

    def get_exit_info(self, position: Position, current_price: float) -> Dict:
        """
        청산 정보 반환 (UI 표시용)

        Args:
            position: 현재 포지션
            current_price: 현재 가격

        Returns:
            청산 정보 딕셔너리
        """
        ema_50 = self.indicators_cache.get('ema_50')
        
        info = {
            'current_price': current_price,
            'ema_50': ema_50,
            'stop_loss': position.stop_loss,
            'take_profit': position.take_profit,
            'exit_conditions': []
        }

        if ema_50:
            if position.side == OrderSide.BUY:
                # 롱 포지션: 가격이 EMA 50선 아래로 떨어지면 청산
                distance_to_ema = current_price - ema_50
                info['exit_conditions'].append({
                    'type': 'ema_50',
                    'position_type': 'LONG',
                    'condition': f'[롱 포지션] 가격이 EMA 50선({ema_50:.8f}) 아래로 떨어지거나 캔들 몸통이 닿으면 청산',
                    'distance': distance_to_ema,
                    'distance_percent': (distance_to_ema / current_price * 100) if current_price > 0 else 0,
                    'trigger_price': ema_50,
                    'trigger_condition': 'current_price < ema_50 or candle_body_touches_ema_50'
                })
            else:  # SELL
                # 숏 포지션: 가격이 EMA 50선 위로 올라가면 청산
                distance_to_ema = ema_50 - current_price
                info['exit_conditions'].append({
                    'type': 'ema_50',
                    'position_type': 'SHORT',
                    'condition': f'[숏 포지션] 가격이 EMA 50선({ema_50:.8f}) 위로 올라가거나 캔들 몸통이 닿으면 청산',
                    'distance': distance_to_ema,
                    'distance_percent': (distance_to_ema / current_price * 100) if current_price > 0 else 0,
                    'trigger_price': ema_50,
                    'trigger_condition': 'current_price > ema_50 or candle_body_touches_ema_50'
                })

        return info
