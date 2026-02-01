"""
바이낸스 동기화 서비스

바이낸스 거래 히스토리를 가져와서 포지션으로 변환 및 저장
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)


class BinanceSyncService:
    """바이낸스 거래 히스토리 동기화 서비스"""

    def __init__(self, exchange, positions_repo):
        """
        초기화

        Args:
            exchange: 거래소 인스턴스 (BinanceLive)
            positions_repo: 포지션 Repository
        """
        self.exchange = exchange
        self.positions_repo = positions_repo

    def sync_positions(self, days: int = 30) -> Dict:
        """
        바이낸스에서 거래 히스토리 가져와서 포지션으로 저장

        Args:
            days: 최근 N일 거래 조회

        Returns:
            결과 딕셔너리
            {
                'success': bool,
                'message': str,
                'saved': int,
                'total': int
            }
        """
        try:
            # 최근 거래 조회 (since 파라미터 없이 최근 N개 조회)
            # days 파라미터는 표시용으로만 사용
            logger.info(f"🔄 바이낸스에서 최근 거래 히스토리 조회 중... (최대 1000개)")

            # 거래 히스토리 조회 (since 없이 최근 거래만)
            trades = self.exchange.get_trade_history(since=None, limit=1000)

            if not trades:
                return {
                    "success": True,
                    "message": "조회된 거래가 없습니다",
                    "saved": 0,
                    "total": 0
                }

            logger.info(f"📋 {len(trades)}개 거래 조회됨")

            # 거래 데이터 구조 확인
            if trades:
                logger.info(f"🔍 첫 번째 거래 데이터 샘플: {trades[0]}")

            # 거래를 포지션으로 변환
            positions = self._convert_trades_to_positions(trades)

            # DB에 저장
            saved_count = self._save_positions(positions)

            logger.info(f"✅ {saved_count}개 포지션 저장 완료")

            return {
                "success": True,
                "message": f"{saved_count}개 포지션 저장 완료",
                "saved": saved_count,
                "total": len(trades)
            }

        except Exception as e:
            logger.error(f"❌ 바이낸스 동기화 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": f"동기화 실패: {str(e)}",
                "saved": 0,
                "total": 0
            }

    def _convert_trades_to_positions(self, trades: List[Dict]) -> List[Dict]:
        """
        거래를 포지션으로 변환

        Args:
            trades: 거래 리스트

        Returns:
            포지션 리스트
        """
        # 거래를 심볼별로 그룹화
        positions_by_symbol = defaultdict(list)

        for trade in trades:
            symbol = trade['symbol']
            positions_by_symbol[symbol].append(trade)

        # 각 심볼의 거래를 포지션으로 변환
        positions = []

        for symbol, symbol_trades in positions_by_symbol.items():
            # 거래를 시간순 정렬 (바이낸스 선물 API는 'time' 키 사용)
            symbol_trades.sort(key=lambda x: x.get('time', x.get('timestamp', 0)))

            # 매수/매도 거래 분리 (바이낸스 선물 API는 'side'가 대문자)
            buy_trades = [t for t in symbol_trades if t['side'].lower() == 'buy']
            sell_trades = [t for t in symbol_trades if t['side'].lower() == 'sell']

            # 포지션 구성 (간단한 FIFO 방식)
            for i in range(min(len(buy_trades), len(sell_trades))):
                buy = buy_trades[i]
                sell = sell_trades[i]

                position = self._create_position_from_trades(buy, sell)
                positions.append(position)

        return positions

    def _create_position_from_trades(self, buy: Dict, sell: Dict) -> Dict:
        """
        매수/매도 거래로부터 포지션 생성

        Args:
            buy: 매수 거래
            sell: 매도 거래

        Returns:
            포지션 데이터
        """
        # 바이낸스 선물 API 데이터 구조에 맞춰 처리
        # 'price', 'qty' 등이 문자열로 반환됨
        position_id = f"binance_{buy['id']}_{sell['id']}"
        entry_price = float(buy['price'])
        exit_price = float(sell['price'])
        quantity = min(float(buy.get('qty', buy.get('amount', 0))),
                      float(sell.get('qty', sell.get('amount', 0))))

        # 손익 계산
        pnl = (exit_price - entry_price) * quantity
        pnl_percent = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0

        # 수수료 계산 (바이낸스 선물 API: 'commission' 키 사용)
        buy_commission = float(buy.get('commission', buy.get('fee', {}).get('cost', 0)))
        sell_commission = float(sell.get('commission', sell.get('fee', {}).get('cost', 0)))
        commission = buy_commission + sell_commission

        # 시간 정보 ('time' 키 사용, 밀리초)
        buy_time = int(buy.get('time', buy.get('timestamp', 0)))
        sell_time = int(sell.get('time', sell.get('timestamp', 0)))
        open_time = datetime.fromtimestamp(buy_time / 1000)
        close_time = datetime.fromtimestamp(sell_time / 1000)
        duration_minutes = int((close_time - open_time).total_seconds() / 60)

        position_data = {
            'position_id': position_id,
            'symbol': buy['symbol'],
            'side': 'buy',  # LONG 포지션
            'entry_price': entry_price,
            'exit_price': exit_price,
            'quantity': quantity,
            'pnl': pnl,
            'pnl_percent': pnl_percent,
            'commission': commission,
            'status': 'closed',
            'leverage': None,  # 거래 히스토리에서는 알 수 없음
            'margin': None,
            'open_time': open_time,
            'close_time': close_time,
            'duration_minutes': duration_minutes,
            'order_ids': [buy['id'], sell['id']],
            'metadata': {
                'source': 'binance_api',
                'synced_at': datetime.now().isoformat()
            },
            'raw_data': {
                'buy_trade': buy,
                'sell_trade': sell
            }
        }

        return position_data

    def _save_positions(self, positions: List[Dict]) -> int:
        """
        포지션을 DB에 저장

        Args:
            positions: 포지션 리스트

        Returns:
            저장된 개수
        """
        saved_count = 0

        for position in positions:
            if self.positions_repo.save_position(position):
                saved_count += 1

        return saved_count
