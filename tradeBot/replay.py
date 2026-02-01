"""
Event Replay System

저장된 이벤트를 재생하여 검증/학습 가능하게 만드는 시스템
"""

import logging
from typing import List, Dict, Optional, Callable
from datetime import datetime
from database.events_repo import EventsRepo
from brokers.base import BaseBroker

logger = logging.getLogger(__name__)


class EventReplayer:
    """이벤트 리플레이 시스템"""

    def __init__(self, events_repo: EventsRepo, broker: BaseBroker):
        """
        초기화

        Args:
            events_repo: 이벤트 Repository
            broker: 브로커 인스턴스 (Paper/Backtest/Live)
        """
        self.events_repo = events_repo
        self.broker = broker
        self.event_handlers: Dict[str, Callable] = {
            'signal': self._handle_signal,
            'user_action': self._handle_user_action,
            'order': self._handle_order,
            'fill': self._handle_fill,
            'position': self._handle_position,
            'outcome': self._handle_outcome
        }

    def replay_events(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        event_types: Optional[List[str]] = None
    ) -> List[Dict]:
        """
        이벤트 재생

        Args:
            symbol: 심볼 필터
            timeframe: 타임프레임 필터
            start_time: 시작 시각
            end_time: 종료 시각
            event_types: 이벤트 타입 필터

        Returns:
            재생 결과 리스트
        """
        events = self.events_repo.get_events(
            event_type=None,  # 모든 타입
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_time,
            end_time=end_time,
            limit=10000,
            offset=0
        )

        # 타임스탬프 순으로 정렬 (오름차순)
        events.sort(key=lambda e: e.get('timestamp') or '')

        # 필터링
        if event_types:
            events = [e for e in events if e.get('event_type') in event_types]

        results = []
        for event in events:
            event_type = event.get('event_type')
            handler = self.event_handlers.get(event_type)
            if handler:
                try:
                    result = handler(event)
                    if result:
                        results.append(result)
                except Exception as e:
                    logger.error(f"❌ 이벤트 처리 실패 ({event.get('event_id')}): {e}")

        logger.info(f"✅ 이벤트 재생 완료: {len(results)}개 처리")
        return results

    def _handle_signal(self, event: Dict) -> Optional[Dict]:
        """신호 이벤트 처리"""
        data = event.get('data', {})
        signal_id = data.get('signal_id')
        logger.debug(f"📡 신호 재생: {signal_id}")
        return {'type': 'signal', 'event_id': event.get('event_id'), 'data': data}

    def _handle_user_action(self, event: Dict) -> Optional[Dict]:
        """사용자 액션 이벤트 처리"""
        data = event.get('data', {})
        action_type = data.get('action_type')
        logger.debug(f"👤 사용자 액션 재생: {action_type}")
        return {'type': 'user_action', 'event_id': event.get('event_id'), 'data': data}

    def _handle_order(self, event: Dict) -> Optional[Dict]:
        """주문 이벤트 처리"""
        data = event.get('data', {})
        order_id = data.get('order_id')
        logger.debug(f"📝 주문 재생: {order_id}")
        return {'type': 'order', 'event_id': event.get('event_id'), 'data': data}

    def _handle_fill(self, event: Dict) -> Optional[Dict]:
        """체결 이벤트 처리"""
        data = event.get('data', {})
        fill_id = data.get('fill_id')
        logger.debug(f"✅ 체결 재생: {fill_id}")
        return {'type': 'fill', 'event_id': event.get('event_id'), 'data': data}

    def _handle_position(self, event: Dict) -> Optional[Dict]:
        """포지션 이벤트 처리"""
        data = event.get('data', {})
        position_id = data.get('position_id')
        logger.debug(f"📊 포지션 재생: {position_id}")
        return {'type': 'position', 'event_id': event.get('event_id'), 'data': data}

    def _handle_outcome(self, event: Dict) -> Optional[Dict]:
        """결과 이벤트 처리"""
        data = event.get('data', {})
        outcome_id = data.get('outcome_id')
        logger.debug(f"📈 결과 재생: {outcome_id}")
        return {'type': 'outcome', 'event_id': event.get('event_id'), 'data': data}
