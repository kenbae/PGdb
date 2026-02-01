"""
Signal-Action Matcher

2단계: 신호와 사용자 액션을 자동으로 매칭하여 학습 데이터셋 구축
"""

import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import pandas as pd
from sqlalchemy import create_engine, text
from database.signals_repo import SignalsRepo
from database.user_actions_repo import UserActionsRepo
from database.positions_repo import PositionsRepo

logger = logging.getLogger(__name__)


class SignalActionMatcher:
    """신호-액션 매칭 시스템"""

    def __init__(
        self,
        signals_repo: SignalsRepo,
        user_actions_repo: UserActionsRepo,
        positions_repo: Optional[PositionsRepo] = None,
        db_engine = None
    ):
        """
        초기화

        Args:
            signals_repo: 신호 Repository
            user_actions_repo: 사용자 액션 Repository
            positions_repo: 포지션 Repository (선택)
            db_engine: 데이터베이스 엔진 (시장 상태 데이터 수집용)
        """
        self.signals_repo = signals_repo
        self.user_actions_repo = user_actions_repo
        self.positions_repo = positions_repo
        self.db_engine = db_engine

    def match_signal_to_action(
        self,
        signal_id: str,
        max_time_window_minutes: int = 60
    ) -> Optional[Dict]:
        """
        특정 신호에 대한 사용자 액션 매칭

        Args:
            signal_id: 신호 ID
            max_time_window_minutes: 신호 생성 후 최대 매칭 시간 (분)

        Returns:
            매칭 결과 {
                'signal': {...},
                'enter_action': {...} or None,
                'exit_action': {...} or None,
                'position': {...} or None,
                'matched': bool
            }
        """
        # 신호 조회
        signals = self.signals_repo.get_signals(signal_id=signal_id, limit=1)
        if not signals:
            return None

        signal = signals[0]
        signal_time = signal.get('created_at')
        if isinstance(signal_time, str):
            signal_time = datetime.fromisoformat(signal_time.replace('Z', '+00:00'))
        elif not isinstance(signal_time, datetime):
            return None

        # 매칭 시간 윈도우
        window_end = signal_time + timedelta(minutes=max_time_window_minutes)

        # 진입 액션 찾기 (신호 후 X분 내)
        enter_actions = self.user_actions_repo.get_actions(
            signal_id=signal_id,
            action_type='enter',
            limit=1
        )

        # signal_id로 직접 매칭되지 않은 경우, 시간 기반 매칭
        if not enter_actions:
            all_enter_actions = self.user_actions_repo.get_actions(
                symbol=signal.get('symbol'),
                action_type='enter',
                start_date=signal_time,
                end_date=window_end,
                limit=100
            )
            # 같은 방향(buy/sell)이고 가장 가까운 시간의 액션 선택
            signal_type = signal.get('signal_type', '').lower()
            matching_actions = [
                a for a in all_enter_actions
                if a.get('side', '').lower() == signal_type
            ]
            if matching_actions:
                enter_actions = [matching_actions[0]]  # 가장 가까운 것

        enter_action = enter_actions[0] if enter_actions else None

        # user_action이 없으면 실제 포지션 데이터에서 찾기
        if not enter_action and self.positions_repo:
            # 신호 시간 기준으로 포지션 찾기
            # 신호 시간 이전 30분부터 신호 시간 + 윈도우까지의 포지션 검색
            # (신호 생성 전에 이미 포지션이 열려있을 수도 있음)
            window_start = signal_time - timedelta(minutes=30)
            positions = self.positions_repo.get_positions(
                symbol=signal.get('symbol'),
                start_date=window_start,
                end_date=window_end,
                limit=50  # 더 많은 포지션 검색
            )
            
            # 같은 방향(buy/sell)이고 가장 가까운 시간의 포지션 선택
            signal_type = signal.get('signal_type', '').lower()
            matching_positions = []
            
            for p in positions:
                p_side = p.get('side', '').lower()
                if p_side != signal_type:
                    continue
                
                open_time = p.get('open_time')
                if isinstance(open_time, str):
                    try:
                        # 시간대 정보가 없으면 UTC로 가정
                        if 'Z' in open_time or '+' in open_time or open_time.endswith('UTC'):
                            open_time = datetime.fromisoformat(open_time.replace('Z', '+00:00'))
                        else:
                            # 시간대 정보가 없으면 naive datetime으로 처리
                            open_time = datetime.fromisoformat(open_time)
                    except:
                        continue
                elif not isinstance(open_time, datetime):
                    continue
                
                # 시간 차이 계산 (분 단위)
                time_diff = abs((open_time - signal_time).total_seconds() / 60)
                
                # 시간 차이가 너무 크면 제외 (최대 2시간)
                if time_diff > 120:
                    continue
                
                p['_open_time'] = open_time
                p['_time_diff'] = time_diff
                matching_positions.append(p)
            
            # 시간 차이가 가장 작은 포지션 선택
            if matching_positions:
                matching_positions.sort(key=lambda x: x.get('_time_diff', float('inf')))
                position = matching_positions[0]
                open_time = position.get('_open_time')
                time_diff_min = position.get('_time_diff', 'N/A')
                
                logger.debug(f"📊 포지션 매칭: {position.get('position_id')} - "
                           f"신호시간={signal_time.strftime('%Y-%m-%d %H:%M:%S')}, "
                           f"포지션오픈={open_time.strftime('%Y-%m-%d %H:%M:%S') if open_time else 'N/A'}, "
                           f"시간차이={time_diff_min:.2f}분, "
                           f"entry_price={position.get('entry_price')}, pnl={position.get('pnl')}")
                
                # 포지션을 enter_action으로 변환
                # 이미 계산된 _open_time 사용
                open_time = position.get('_open_time')
                if not open_time:
                    # fallback: 원본 open_time 사용
                    open_time = position.get('open_time')
                    if isinstance(open_time, str):
                        try:
                            if 'Z' in open_time or '+' in open_time:
                                open_time = datetime.fromisoformat(open_time.replace('Z', '+00:00'))
                            else:
                                open_time = datetime.fromisoformat(open_time)
                        except:
                            open_time = signal_time
                    elif not isinstance(open_time, datetime):
                        open_time = signal_time
                
                # 포지션 데이터에서 숫자 변환 (pandas/numpy 타입 처리)
                entry_price = position.get('entry_price')
                if entry_price is not None:
                    try:
                        entry_price = float(entry_price)
                    except (ValueError, TypeError):
                        entry_price = None
                        logger.warning(f"⚠️ entry_price 변환 실패: {position.get('entry_price')}")
                
                quantity = position.get('quantity')
                if quantity is not None:
                    try:
                        quantity = float(quantity)
                    except (ValueError, TypeError):
                        quantity = None
                        logger.warning(f"⚠️ quantity 변환 실패: {position.get('quantity')}")
                
                enter_action = {
                    'action_id': f"pos_{position.get('position_id', '')}",
                    'signal_id': signal_id,
                    'action_type': 'enter',
                    'symbol': position.get('symbol'),
                    'side': position.get('side'),
                    'price': entry_price,
                    'quantity': quantity,
                    'created_at': open_time.isoformat() if isinstance(open_time, datetime) else str(open_time),
                    'metadata': {'position_id': position.get('position_id'), 'from_position': True}
                }
                
                # 포지션 정보도 함께 저장 (나중에 PnL 계산 시 사용)
                position_data = position

        # 청산 액션 찾기 (진입 액션이 있으면 그 이후)
        exit_action = None
        if enter_action:
            enter_time = enter_action.get('created_at')
            if isinstance(enter_time, str):
                enter_time = datetime.fromisoformat(enter_time.replace('Z', '+00:00'))
            elif not isinstance(enter_time, datetime):
                enter_time = signal_time

            exit_actions = self.user_actions_repo.get_actions(
                signal_id=signal_id,
                action_type='exit',
                start_date=enter_time,
                limit=1
            )
            if exit_actions:
                exit_action = exit_actions[0]
            else:
                # 시간 기반 매칭
                all_exit_actions = self.user_actions_repo.get_actions(
                    symbol=signal.get('symbol'),
                    action_type='exit',
                    start_date=enter_time,
                    limit=100
                )
                if all_exit_actions:
                    exit_action = all_exit_actions[0]
                
                # user_action이 포지션에서 온 경우, 포지션의 exit 정보 사용
                if not exit_action and enter_action and enter_action.get('metadata', {}).get('from_position'):
                    position_id = enter_action.get('metadata', {}).get('position_id')
                    if position_id and self.positions_repo:
                        position = self.positions_repo.get_position_by_id(position_id)
                        if position and position.get('status') in ['closed', 'liquidated']:
                            close_time = position.get('close_time')
                            if isinstance(close_time, str):
                                try:
                                    close_time = datetime.fromisoformat(close_time.replace('Z', '+00:00'))
                                except:
                                    close_time = None
                            elif not isinstance(close_time, datetime):
                                close_time = None
                            
                            # exit_price도 숫자 변환
                            exit_price = position.get('exit_price')
                            if exit_price is not None:
                                try:
                                    exit_price = float(exit_price)
                                except (ValueError, TypeError):
                                    exit_price = None
                            
                            exit_quantity = position.get('quantity')
                            if exit_quantity is not None:
                                try:
                                    exit_quantity = float(exit_quantity)
                                except (ValueError, TypeError):
                                    exit_quantity = None
                            
                            exit_action = {
                                'action_id': f"exit_{position_id}",
                                'signal_id': signal_id,
                                'action_type': 'exit',
                                'symbol': position.get('symbol'),
                                'side': position.get('side'),
                                'price': exit_price,
                                'quantity': exit_quantity,
                                'created_at': close_time.isoformat() if close_time else None,
                                'metadata': {'position_id': position_id, 'from_position': True}
                            }

        # 포지션 찾기
        position = None
        if enter_action:
            position_id = enter_action.get('metadata', {}).get('position_id')
            if position_id and self.positions_repo:
                position = self.positions_repo.get_position_by_id(position_id)
            # 포지션에서 직접 가져온 경우 position_data 사용
            elif 'position_data' in locals():
                position = position_data

        return {
            'signal': signal,
            'enter_action': enter_action,
            'exit_action': exit_action,
            'position': position,
            'matched': enter_action is not None
        }

    def match_all_signals(
        self,
        symbol: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_time_window_minutes: int = 60
    ) -> List[Dict]:
        """
        모든 신호에 대해 액션 매칭 수행

        Args:
            symbol: 심볼 필터
            start_date: 시작일
            end_date: 종료일
            max_time_window_minutes: 매칭 시간 윈도우

        Returns:
            매칭 결과 리스트
        """
        # 신호 조회
        signals = self.signals_repo.get_signals(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            limit=10000
        )

        results = []
        # 포지션 중복 매칭 방지: position_id -> signal_id 매핑
        used_positions = {}  # {position_id: signal_id}
        
        # 신호를 시간순으로 정렬 (오래된 것부터)
        signals_sorted = sorted(signals, key=lambda s: s.get('created_at', ''))
        
        for signal in signals_sorted:
            signal_id = signal.get('signal_id')
            if not signal_id:
                continue

            match_result = self.match_signal_to_action(
                signal_id=signal_id,
                max_time_window_minutes=max_time_window_minutes
            )
            
            if match_result:
                enter_action = match_result.get('enter_action')
                # 포지션에서 온 액션인 경우 중복 체크
                if enter_action and enter_action.get('metadata', {}).get('from_position'):
                    position_id = enter_action.get('metadata', {}).get('position_id')
                    if position_id:
                        # 이미 다른 신호에 매칭된 포지션이면 스킵
                        if position_id in used_positions:
                            logger.debug(f"⏭️ 포지션 {position_id}는 이미 신호 {used_positions[position_id]}에 매칭됨. 신호 {signal_id} 스킵")
                            # 매칭 결과를 None으로 설정 (미매칭으로 처리)
                            match_result['enter_action'] = None
                            match_result['exit_action'] = None
                            match_result['position'] = None
                            match_result['matched'] = False
                        else:
                            # 포지션 사용 표시
                            used_positions[position_id] = signal_id
                
                results.append(match_result)

        logger.info(f"✅ 신호-액션 매칭 완료: {len(results)}개 (전체 {len(signals)}개 중), 사용된 포지션: {len(used_positions)}개")
        return results

    def build_training_dataset(
        self,
        matched_results: List[Dict]
    ) -> List[Dict]:
        """
        학습 데이터셋 구축

        Args:
            matched_results: match_all_signals 결과

        Returns:
            학습 데이터셋 [
                {
                    'input': {
                        'signal': {...},
                        'market_state': {...}  # 캔들/지표 (추후 추가)
                    },
                    'output': {
                        'enter_timing': datetime,
                        'exit_timing': datetime or None,
                        'enter_price': float,
                        'exit_price': float or None,
                        'pnl': float or None,
                        'pnl_percent': float or None
                    }
                },
                ...
            ]
        """
        dataset = []

        for match in matched_results:
            signal = match.get('signal')
            enter_action = match.get('enter_action')
            exit_action = match.get('exit_action')
            position = match.get('position')

            # enter_action이 없어도 데이터셋에 포함 (미매칭 신호도 표시)
            # if not enter_action:
            #     continue
            
            # enter_action이 없으면 기본값 사용
            if not enter_action:
                enter_action = {}

            # 입력: 신호 정보
            input_data = {
                'signal': {
                    'signal_id': signal.get('signal_id'),
                    'strategy_name': signal.get('strategy_name'),
                    'symbol': signal.get('symbol'),
                    'timeframe': signal.get('timeframe'),
                    'signal_type': signal.get('signal_type'),
                    'entry_price': signal.get('entry_price'),
                    'stop_loss': signal.get('stop_loss'),
                    'take_profit_1': signal.get('take_profit_1'),
                    'confidence': signal.get('confidence'),
                    'risk_reward': signal.get('risk_reward'),
                    'ai_decision': signal.get('ai_decision'),
                    'ai_confidence': signal.get('ai_confidence'),
                    'created_at': signal.get('created_at')
                },
                'market_state': self._get_market_state(signal)  # 캔들/지표 데이터
            }

            # 출력: 사용자 행동 + 결과
            if enter_action:
                enter_time = enter_action.get('created_at')
                if isinstance(enter_time, str):
                    try:
                        enter_time = datetime.fromisoformat(enter_time.replace('Z', '+00:00'))
                    except:
                        enter_time = None
                elif isinstance(enter_time, datetime):
                    pass
                else:
                    enter_time = None

                # 가격/수량 숫자 변환
                enter_price = enter_action.get('price')
                if enter_price is not None:
                    try:
                        enter_price = float(enter_price)
                    except (ValueError, TypeError):
                        enter_price = None
                
                enter_quantity = enter_action.get('quantity')
                if enter_quantity is not None:
                    try:
                        enter_quantity = float(enter_quantity)
                    except (ValueError, TypeError):
                        enter_quantity = None
                
                output_data = {
                    'enter_timing': enter_time.isoformat() if enter_time else None,
                    'enter_price': enter_price,
                    'enter_quantity': enter_quantity,
                    'enter_reason': enter_action.get('reason')
                }
            else:
                # enter_action이 없으면 모든 필드를 None으로 설정
                output_data = {
                    'enter_timing': None,
                    'enter_price': None,
                    'enter_quantity': None,
                    'enter_reason': None
                }

            if exit_action:
                exit_time = exit_action.get('created_at')
                if isinstance(exit_time, str):
                    exit_time = datetime.fromisoformat(exit_time.replace('Z', '+00:00'))

                output_data['exit_timing'] = exit_time.isoformat() if exit_time else None
                
                # exit_price/exit_quantity 숫자 변환
                exit_price = exit_action.get('price')
                if exit_price is not None:
                    try:
                        exit_price = float(exit_price)
                    except (ValueError, TypeError):
                        exit_price = None
                
                exit_quantity = exit_action.get('quantity')
                if exit_quantity is not None:
                    try:
                        exit_quantity = float(exit_quantity)
                    except (ValueError, TypeError):
                        exit_quantity = None
                
                output_data['exit_price'] = exit_price
                output_data['exit_quantity'] = exit_quantity
                output_data['exit_reason'] = exit_action.get('reason')

                # PnL 계산 (포지션에서 직접 가져온 경우 우선 사용)
                position = match.get('position')
                if position:
                    # 포지션의 실제 PnL 사용
                    pnl_value = position.get('pnl')
                    if pnl_value is not None:
                        try:
                            output_data['pnl'] = float(pnl_value)
                        except (ValueError, TypeError):
                            output_data['pnl'] = None
                    else:
                        output_data['pnl'] = None
                    
                    pnl_percent_value = position.get('pnl_percent')
                    if pnl_percent_value is not None:
                        try:
                            output_data['pnl_percent'] = float(pnl_percent_value)
                        except (ValueError, TypeError):
                            output_data['pnl_percent'] = None
                    else:
                        output_data['pnl_percent'] = None
                    
                    # 포지션의 exit_price가 있으면 사용 (더 정확)
                    if position.get('exit_price') is not None:
                        try:
                            output_data['exit_price'] = float(position.get('exit_price'))
                        except (ValueError, TypeError):
                            pass
                elif enter_action.get('price') and exit_action.get('price'):
                    # 직접 계산
                    side = enter_action.get('side', '').lower()
                    quantity = enter_action.get('quantity') or 0
                    if side == 'buy':
                        pnl = (exit_action['price'] - enter_action['price']) * quantity
                    else:  # sell
                        pnl = (enter_action['price'] - exit_action['price']) * quantity

                    output_data['pnl'] = pnl
                    if enter_action.get('price') and quantity:
                        output_data['pnl_percent'] = (pnl / (enter_action['price'] * quantity)) * 100
            else:
                output_data['exit_timing'] = None
                output_data['exit_price'] = None
                output_data['pnl'] = None
                output_data['pnl_percent'] = None

            dataset.append({
                'input': input_data,
                'output': output_data
            })

        logger.info(f"📊 학습 데이터셋 구축 완료: {len(dataset)}개 샘플")
        return dataset

    def _get_market_state(self, signal: Dict) -> Dict:
        """
        신호 생성 시점의 시장 상태 수집 (캔들 + 지표)

        Args:
            signal: 신호 데이터

        Returns:
            시장 상태 데이터 {
                'candles': [...],  # 최근 N개 캔들
                'indicators': {
                    'rsi': float,
                    'ema20': float,
                    'ema50': float,
                    'ema200': float,
                    'atr': float,
                    'bb_upper': float,
                    'bb_mid': float,
                    'bb_lower': float,
                    ...
                },
                'price_action': {
                    'trend': str,
                    'volatility': str,
                    'rsi_status': str
                }
            }
        """
        if not self.db_engine:
            return {}

        try:
            symbol = signal.get('symbol')
            timeframe = signal.get('timeframe', '15m')
            signal_time = signal.get('created_at')

            if not symbol or not signal_time:
                return {}

            # 신호 시간 파싱
            if isinstance(signal_time, str):
                signal_time = datetime.fromisoformat(signal_time.replace('Z', '+00:00'))
            elif not isinstance(signal_time, datetime):
                return {}

            # 신호 생성 시점 이전의 최근 100개 캔들 조회
            query = text("""
                SELECT open_time, open, high, low, close, volume
                FROM candles
                WHERE symbol = :symbol AND tf = :tf AND open_time <= :signal_time
                ORDER BY open_time DESC
                LIMIT 100
            """)

            df = pd.read_sql(query, self.db_engine, params={
                'symbol': symbol,
                'tf': timeframe,
                'signal_time': signal_time
            })

            if len(df) < 50:
                logger.warning(f"⚠️ 시장 상태 데이터 부족: {symbol} {timeframe} ({len(df)}개)")
                return {}

            # 시간순 정렬
            df = df.sort_values('open_time').reset_index(drop=True)

            # 기술적 지표 계산
            df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

            # RSI
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))

            # ATR
            high_low = df['high'] - df['low']
            high_close = (df['high'] - df['close'].shift()).abs()
            low_close = (df['low'] - df['close'].shift()).abs()
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            df['atr'] = tr.rolling(window=14).mean()

            # Bollinger Bands
            df['bb_mid'] = df['close'].rolling(window=20).mean()
            df['bb_std'] = df['close'].rolling(window=20).std()
            df['bb_upper'] = df['bb_mid'] + 2 * df['bb_std']
            df['bb_lower'] = df['bb_mid'] - 2 * df['bb_std']

            # 최신 값 가져오기
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest

            current_price = float(latest['close'])
            rsi_value = float(latest['rsi']) if pd.notna(latest['rsi']) else 50.0

            # 트렌드 판단
            trend = "sideways"
            if pd.notna(latest['ema20']) and pd.notna(latest['ema50']) and pd.notna(latest['ema200']):
                if float(latest['ema20']) > float(latest['ema50']) > float(latest['ema200']):
                    trend = "strong_uptrend"
                elif float(latest['ema20']) > float(latest['ema50']):
                    trend = "uptrend"
                elif float(latest['ema20']) < float(latest['ema50']) < float(latest['ema200']):
                    trend = "strong_downtrend"
                elif float(latest['ema20']) < float(latest['ema50']):
                    trend = "downtrend"

            # 변동성
            volatility = "normal"
            if pd.notna(latest['atr']):
                atr_pct = (float(latest['atr']) / current_price) * 100
                if atr_pct > 3:
                    volatility = "high"
                elif atr_pct < 1:
                    volatility = "low"

            # RSI 상태
            rsi_status = "neutral"
            if rsi_value > 70:
                rsi_status = "overbought"
            elif rsi_value < 30:
                rsi_status = "oversold"

            # 최근 20개 캔들만 반환 (메모리 절약)
            recent_candles = df.tail(20).to_dict('records')
            for candle in recent_candles:
                if isinstance(candle.get('open_time'), pd.Timestamp):
                    candle['open_time'] = candle['open_time'].isoformat()
                # NaN 값 처리
                for key in ['ema20', 'ema50', 'ema200', 'rsi', 'atr', 'bb_mid', 'bb_upper', 'bb_lower']:
                    if key in candle and pd.isna(candle[key]):
                        candle[key] = None

            return {
                'candles': recent_candles,
                'indicators': {
                    'rsi': rsi_value,
                    'ema20': float(latest['ema20']) if pd.notna(latest['ema20']) else None,
                    'ema50': float(latest['ema50']) if pd.notna(latest['ema50']) else None,
                    'ema200': float(latest['ema200']) if pd.notna(latest['ema200']) else None,
                    'atr': float(latest['atr']) if pd.notna(latest['atr']) else None,
                    'bb_upper': float(latest['bb_upper']) if pd.notna(latest['bb_upper']) else None,
                    'bb_mid': float(latest['bb_mid']) if pd.notna(latest['bb_mid']) else None,
                    'bb_lower': float(latest['bb_lower']) if pd.notna(latest['bb_lower']) else None,
                    'volume': float(latest['volume']) if pd.notna(latest['volume']) else None
                },
                'price_action': {
                    'trend': trend,
                    'volatility': volatility,
                    'rsi_status': rsi_status,
                    'current_price': current_price
                }
            }

        except Exception as e:
            logger.error(f"❌ 시장 상태 수집 실패: {e}", exc_info=True)
            return {}
