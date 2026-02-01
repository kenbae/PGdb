"""
Paper Trading Engine

3단계: 실시간 신호 기반 자동 매매 시뮬레이션
- 신호 수신 및 처리
- 타이밍/정책 모델 추천 적용
- PaperBroker를 통한 주문 실행
- 실시간 포지션 관리
"""

import logging
import uuid
import asyncio
from typing import Dict, List, Optional, Callable
from datetime import datetime, timedelta
from brokers.paper_broker import PaperBroker
from brokers.base import OrderIntent, OrderSide, OrderType, Position
from services.timing_learner import TimingLearner
from services.policy_recommender import PolicyRecommender
from services.signal_action_matcher import SignalActionMatcher
from database.events_repo import EventsRepo
from database.positions_repo import PositionsRepo

logger = logging.getLogger(__name__)


class PaperTradingEngine:
    """Paper Trading 엔진"""

    def __init__(
        self,
        broker: PaperBroker,
        signals_repo,
        events_repo: EventsRepo,
        positions_repo: PositionsRepo,
        timing_learner: Optional[TimingLearner] = None,
        policy_recommender: Optional[PolicyRecommender] = None,
        signal_matcher: Optional[SignalActionMatcher] = None,
        require_approval: bool = False,  # 사용자 승인 필요 여부
        on_position_update: Optional[Callable] = None,  # 포지션 업데이트 콜백
        symbol_filter: Optional[str] = None,  # 특정 심볼만 처리
        strategy_filter: Optional[str] = None  # 특정 전략만 처리
    ):
        """
        초기화

        Args:
            broker: PaperBroker 인스턴스
            signals_repo: 신호 Repository
            events_repo: 이벤트 Repository
            positions_repo: 포지션 Repository
            timing_learner: 타이밍 학습 모델 (선택사항)
            policy_recommender: 정책 추천 모델 (선택사항)
            signal_matcher: 신호 매칭기 (시장 상태 조회용)
            require_approval: 사용자 승인 필요 여부
            on_position_update: 포지션 업데이트 콜백 (선택사항)
            symbol_filter: 특정 심볼만 처리 (None이면 전체)
            strategy_filter: 특정 전략만 처리 (None이면 전체)
        """
        self.broker = broker
        self.signals_repo = signals_repo
        self.events_repo = events_repo
        self.positions_repo = positions_repo
        self.timing_learner = timing_learner
        self.policy_recommender = policy_recommender
        self.signal_matcher = signal_matcher
        self.require_approval = require_approval
        self.on_position_update = on_position_update  # 포지션 업데이트 콜백
        self.symbol_filter = symbol_filter
        self.strategy_filter = strategy_filter

        # 현재 가격 저장 (WebSocket에서 업데이트)
        self.current_prices: Dict[str, float] = {}

        # 활성 신호 추적
        self.active_signals: Dict[str, Dict] = {}  # signal_id -> signal data

        # 규칙 관리
        self.rules: List[Dict] = []  # 청산 규칙 목록

        # 실행 상태
        self.is_running = False
        self.update_task = None

        filter_info = []
        if symbol_filter:
            filter_info.append(f"심볼={symbol_filter}")
        if strategy_filter:
            filter_info.append(f"전략={strategy_filter}")
        filter_str = f" ({', '.join(filter_info)})" if filter_info else ""
        logger.info(f"📝 Paper Trading Engine 초기화 완료{filter_str}")

    def start(self):
        """엔진 시작"""
        if self.is_running:
            logger.warning("⚠️ 엔진이 이미 실행 중입니다")
            return

        self.is_running = True
        logger.info("🚀 Paper Trading Engine 시작")

        # 포지션 업데이트 태스크 시작
        self.update_task = asyncio.create_task(self._update_loop())

    def stop(self):
        """엔진 중지"""
        if not self.is_running:
            return

        self.is_running = False
        if self.update_task:
            self.update_task.cancel()
        logger.info("⏹️ Paper Trading Engine 중지")

    async def _update_loop(self):
        """포지션 업데이트 루프 (주기적 실행)"""
        while self.is_running:
            try:
                # 포지션 상태 업데이트 (SL/TP 체크, PnL 계산)
                if self.current_prices:
                    updated = self.broker.update_positions(self.current_prices)
                    position_updated = False
                    for position in updated:
                        if position.status.value == 'closed':
                            # 포지션 청산 이벤트 저장
                            await self._save_position_event(position, 'closed')
                            position_updated = True
                        else:
                            position_updated = True
                    
                    # 포지션이 업데이트되었으면 콜백 호출
                    if position_updated and self.on_position_update:
                        try:
                            await self.on_position_update()
                        except Exception as e:
                            logger.warning(f"⚠️ 포지션 업데이트 콜백 실패: {e}")

                # 규칙 체크
                await self._check_rules()

                await asyncio.sleep(1)  # 1초마다 업데이트

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ 업데이트 루프 오류: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def process_signal(self, signal: Dict, is_manual: bool = False, manual_entry_price: Optional[float] = None) -> Dict:
        """
        신호 처리

        Args:
            signal: 신호 데이터
            is_manual: 수동 진입 여부 (기본: False, 자동 진입)
            manual_entry_price: 수동 진입 시 사용할 가격 (기본: None, 현재 가격 사용)

        Returns:
            처리 결과
        """
        signal_id = signal.get('signal_id') or f"signal_{uuid.uuid4().hex[:12]}"
        symbol = signal.get('symbol')
        signal_type = signal.get('signal_type', '').lower()
        strategy = signal.get('strategy') or signal.get('strategy_name')

        if not symbol or signal_type not in ['buy', 'sell']:
            logger.warning(f"⚠️ 잘못된 신호: {signal_id}")
            return {'success': False, 'reason': 'invalid_signal'}

        # 심볼 필터링 체크
        if self.symbol_filter and symbol != self.symbol_filter:
            logger.debug(f"⏭️ 심볼 필터링: {symbol} != {self.symbol_filter} (필터: {self.symbol_filter})")
            return {'success': False, 'reason': 'symbol_filtered'}

        # 전략 필터링 체크
        if self.strategy_filter and strategy != self.strategy_filter:
            logger.debug(f"⏭️ 전략 필터링: {strategy} != {self.strategy_filter} (필터: {self.strategy_filter})")
            return {'success': False, 'reason': 'strategy_filtered'}

        logger.info(f"📡 신호 수신: {signal_id} ({symbol} {signal_type.upper()})")

        # 타이밍/정책 추천 받기
        timing_recommendation = None
        policy_recommendation = None

        try:
            if self.timing_learner and self.signal_matcher:
                try:
                    market_state = self.signal_matcher._get_market_state(signal)
                    timing_recommendation = self.timing_learner.get_recommendations(signal, market_state)
                except Exception as e:
                    logger.warning(f"⚠️ 타이밍 추천 실패: {e}")

            if self.policy_recommender and self.signal_matcher:
                try:
                    market_state = self.signal_matcher._get_market_state(signal)
                    policy_recommendation = self.policy_recommender.recommend_policy(signal, market_state)
                except Exception as e:
                    logger.warning(f"⚠️ 정책 추천 실패: {e}")
        except Exception as e:
            logger.warning(f"⚠️ 추천 조회 실패: {e}")

        # 사용자 승인 필요 시 대기
        if self.require_approval:
            # TODO: 승인 대기 로직 (UI에서 승인 받기)
            logger.info(f"⏳ 승인 대기 중: {signal_id}")
            return {'success': False, 'reason': 'pending_approval', 'signal_id': signal_id}

        # 주문 의도 생성
        # 수동 진입인 경우 manual_entry_price 우선 사용
        if is_manual and manual_entry_price:
            entry_price = manual_entry_price
        else:
            entry_price = signal.get('entry_price', 0)
            if not entry_price and self.current_prices.get(symbol):
                entry_price = self.current_prices[symbol]

        if not entry_price:
            logger.warning(f"⚠️ 가격 정보 없음: {symbol}")
            return {'success': False, 'reason': 'no_price'}

        # 정책 추천 반영 (가격 조정)
        if policy_recommendation and policy_recommendation.get('price_adjustment_percent'):
            adjustment = policy_recommendation['price_adjustment_percent']
            entry_price = entry_price * (1 + adjustment / 100)
            logger.info(f"💰 가격 조정: {adjustment:+.2f}% → ${entry_price:.2f}")

        # 수량 계산 (기본: 자본의 2%)
        risk_percent = 2.0
        if policy_recommendation and policy_recommendation.get('risk_reward'):
            # 정책 추천의 리스크:리워드 비율 고려
            pass

        capital = self.broker.capital
        risk_amount = capital * (risk_percent / 100)
        
        # SL/TP 가져오기 (신호에서 직접 가져오기, 없으면 기본값 계산)
        signal_stop_loss = signal.get('stop_loss')
        if signal_stop_loss is not None:
            stop_loss = float(signal_stop_loss)
        else:
            stop_loss = entry_price * 0.98  # 기본 2% 손절
        
        signal_take_profit = signal.get('take_profit_1') or signal.get('take_profit')
        take_profit = float(signal_take_profit) if signal_take_profit is not None else None
        
        # 수량 계산
        risk_per_trade = abs(entry_price - stop_loss) if stop_loss else abs(entry_price * 0.02)

        if risk_per_trade > 0:
            quantity = risk_amount / risk_per_trade
        else:
            quantity = 0

        # 수량이 0이면 주문 거부
        if quantity <= 0:
            logger.warning(f"⚠️ 수량 계산 실패: quantity={quantity}, entry_price={entry_price}, stop_loss={stop_loss}")
            return {'success': False, 'reason': 'invalid_quantity'}

        # 주문 의도 생성
        intent_id = f"intent_{uuid.uuid4().hex[:12]}"
        intent = OrderIntent(
            intent_id=intent_id,
            signal_id=signal_id,
            symbol=symbol,
            side=OrderSide.BUY if signal_type == 'buy' else OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=quantity,
            price=entry_price,
            stop_loss=stop_loss if stop_loss else None,
            take_profit=take_profit if take_profit else None,
            metadata={
                'signal': signal,
                'timing_recommendation': timing_recommendation,
                'policy_recommendation': policy_recommendation,
                'is_manual': is_manual,  # 자동/수동 구분
                'entry_method': 'manual' if is_manual else 'auto'  # 진입 방법
            }
        )

        # 주문 제출
        order = self.broker.submit_order(intent)

        if order.status.value == 'rejected':
            logger.warning(f"❌ 주문 거부: {order.order_id}")
            return {'success': False, 'reason': 'rejected', 'order_id': order.order_id}

        # 현재 가격으로 즉시 체결 (시뮬레이션)
        # 수동 진입인 경우 manual_entry_price 사용, 아니면 현재 가격 사용
        if is_manual and manual_entry_price:
            fill_price = manual_entry_price
        else:
            fill_price = self.current_prices.get(symbol, entry_price)
        fill = self.broker.fill_order(order.order_id, fill_price)

        if fill:
            # 이벤트 저장 (자동/수동 구분 정보 포함)
            await self._save_order_event(order, fill, timing_recommendation, policy_recommendation, is_manual, fill_price)
            
            # 수동 진입인 경우 user_actions에도 저장
            if is_manual and hasattr(self, 'user_actions_repo') and self.user_actions_repo:
                try:
                    action_id = f"paper_manual_{uuid.uuid4().hex[:12]}"
                    signal_id = signal.get('signal_id')
                    
                    # user_actions_repo.save_action은 동기 함수이므로 await 불필요
                    self.user_actions_repo.save_action(
                        action_id=action_id,
                        signal_id=signal_id,
                        action_type='enter',
                        symbol=symbol,
                        side='buy' if signal_type == 'buy' else 'sell',  # side는 'buy' 또는 'sell'
                        price=fill_price,
                        quantity=quantity,
                        reason='Paper Trading 수동 진입',
                        metadata={
                            'is_manual': True,
                            'entry_method': 'manual',
                            'entry_price': fill_price,
                            'signal_entry_price': signal.get('entry_price'),
                            'order_id': order.order_id,
                            'fill_id': fill.fill_id
                        }
                    )
                except Exception as e:
                    logger.warning(f"⚠️ 사용자 액션 저장 실패: {e}")
            
            # 사용자 액션 저장 (수동 진입인 경우)
            if is_manual and self.positions_repo:
                try:
                    from database.user_actions_repo import UserActionsRepo
                    # user_actions_repo가 없으면 생성하지 않음 (이미 전달받아야 함)
                    # 대신 이벤트에 저장하는 것으로 충분
                except:
                    pass

            # 활성 신호 추적
            self.active_signals[signal_id] = {
                'signal': signal,
                'order': order,
                'fill': fill,
                'position_id': None
            }

            # 포지션 ID 찾기
            position_id = None
            for pos_id, pos in self.broker.positions.items():
                if pos.entry_fill_id == fill.fill_id:
                    self.active_signals[signal_id]['position_id'] = pos_id
                    position_id = pos_id
                    break

            logger.info(f"✅ 주문 체결: {fill.fill_id} ({symbol} {intent.side.value} {quantity:.4f} @ ${fill.price:.2f})")
            
            # 포지션 업데이트 콜백 호출 (WebSocket 브로드캐스트)
            if self.on_position_update:
                try:
                    await self.on_position_update()
                except Exception as e:
                    logger.warning(f"⚠️ 포지션 업데이트 콜백 실패: {e}")
            
            return {
                'success': True,
                'order_id': order.order_id,
                'fill_id': fill.fill_id,
                'position_id': position_id,
                'timing_recommendation': timing_recommendation,
                'policy_recommendation': policy_recommendation
            }
        else:
            return {'success': False, 'reason': 'fill_failed'}

    async def _save_order_event(self, order, fill, timing_recommendation, policy_recommendation, is_manual: bool = False, entry_price: Optional[float] = None):
        """주문 이벤트 저장"""
        try:
            event_data = {
                'order_id': order.order_id,
                'fill_id': fill.fill_id,
                'symbol': order.symbol,
                'side': order.side.value,
                'quantity': order.quantity,
                'price': fill.price,
                'timing_recommendation': timing_recommendation,
                'policy_recommendation': policy_recommendation,
                'is_manual': is_manual,  # 자동/수동 구분
                'entry_method': 'manual' if is_manual else 'auto',  # 진입 방법
                'entry_price': entry_price or fill.price,  # 실제 진입 가격
                'signal_entry_price': order.metadata.get('signal', {}).get('entry_price') if order.metadata else None  # 신호의 진입가
            }

            await self.events_repo.save_event(
                event_type='paper_order',
                symbol=order.symbol,
                data=event_data
            )
        except Exception as e:
            logger.error(f"❌ 이벤트 저장 실패: {e}")

    async def _save_position_event(self, position: Position, event_type: str):
        """포지션 이벤트 저장"""
        try:
            event_data = {
                'position_id': position.position_id,
                'symbol': position.symbol,
                'side': position.side.value,
                'entry_price': position.entry_price,
                'exit_price': position.exit_price,
                'quantity': position.quantity,
                'pnl': position.pnl,
                'pnl_percent': position.pnl_percent
            }

            await self.events_repo.save_event(
                event_type=f'paper_position_{event_type}',
                symbol=position.symbol,
                data=event_data
            )

            # DB에도 저장 (type='paper')
            if self.positions_repo:
                try:
                    self.positions_repo.save_position({
                        'position_id': position.position_id,
                        'symbol': position.symbol,
                        'side': position.side.value,
                        'entry_price': position.entry_price,
                        'exit_price': position.exit_price,
                        'quantity': position.quantity,
                        'pnl': position.pnl,
                        'pnl_percent': position.pnl_percent,
                        'status': position.status.value,
                        'type': 'paper'  # Paper Trading 포지션 표시
                    })
                except Exception as e:
                    logger.warning(f"⚠️ 포지션 DB 저장 실패: {e}")

        except Exception as e:
            logger.error(f"❌ 포지션 이벤트 저장 실패: {e}")

    def update_price(self, symbol: str, price: float):
        """가격 업데이트 (WebSocket에서 호출)"""
        self.current_prices[symbol] = price

    async def _check_rules(self):
        """규칙 체크 및 실행"""
        if not self.rules:
            return

        for rule in self.rules:
            if not rule.get('approved', False):
                continue

            try:
                should_execute = await self._evaluate_rule(rule)
                if should_execute:
                    await self._execute_rule(rule)
            except Exception as e:
                logger.error(f"❌ 규칙 체크 실패: {e}")

    async def _evaluate_rule(self, rule: Dict) -> bool:
        """규칙 평가"""
        condition_type = rule.get('condition_type')
        condition_params = rule.get('condition_params', {})

        # TODO: 규칙 평가 로직 구현
        # 예: 가격이 MA에 닿았는지, RSI가 특정 수준인지 등
        return False

    async def _execute_rule(self, rule: Dict):
        """규칙 실행"""
        action = rule.get('action')
        symbol = rule.get('symbol')

        # TODO: 규칙 실행 로직
        logger.info(f"🎯 규칙 실행: {rule.get('rule_id')} ({action})")

    def add_rule(self, rule: Dict):
        """규칙 추가"""
        rule_id = rule.get('rule_id') or f"rule_{uuid.uuid4().hex[:12]}"
        rule['rule_id'] = rule_id
        rule['created_at'] = datetime.now()
        self.rules.append(rule)
        logger.info(f"➕ 규칙 추가: {rule_id}")

    def get_stats(self) -> Dict:
        """통계 조회"""
        open_positions = self.broker.get_open_positions()
        total_pnl = sum([p.pnl for p in open_positions])

        closed_positions = [p for p in self.broker.positions.values() if p.status.value == 'closed']
        total_closed_pnl = sum([p.pnl for p in closed_positions])

        wins = len([p for p in closed_positions if p.pnl > 0])
        win_rate = (wins / len(closed_positions) * 100) if closed_positions else 0

        return {
            'capital': self.broker.capital,
            'initial_capital': self.broker.initial_capital,
            'total_pnl': total_pnl,
            'total_closed_pnl': total_closed_pnl,
            'open_positions': len(open_positions),
            'closed_positions': len(closed_positions),
            'win_rate': win_rate,
            'daily_pnl': self.broker.daily_pnl
        }
