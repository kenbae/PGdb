"""
Positions Repository

포지션 히스토리 CRUD 작업
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
import pandas as pd
import json

logger = logging.getLogger(__name__)


class PositionsRepo:
    """포지션 히스토리 Repository"""

    def __init__(self, db_engine):
        """
        초기화

        Args:
            db_engine: SQLAlchemy Engine
        """
        self.engine = db_engine

    # ============================================================
    # Positions (포지션)
    # ============================================================

    def save_position(self, position_data: Dict) -> bool:
        """
        포지션 저장

        Args:
            position_data: 포지션 데이터
                {
                    'position_id': str,
                    'symbol': str,
                    'side': str,  # 'buy' or 'sell'
                    'entry_price': float,
                    'exit_price': float,
                    'quantity': float,
                    'pnl': float,
                    'pnl_percent': float,
                    'commission': float,
                    'status': str,  # 'open', 'closed', 'liquidated'
                    'leverage': int,
                    'margin': float,
                    'open_time': datetime,
                    'close_time': datetime,
                    'duration_minutes': int,
                    'order_ids': list,
                    'metadata': dict,
                    'raw_data': dict
                }

        Returns:
            성공 여부
        """
        try:
            query = text("""
                INSERT INTO tradebot_positions (
                    position_id, symbol, side,
                    entry_price, exit_price, quantity,
                    pnl, pnl_percent, commission,
                    status, leverage, margin,
                    open_time, close_time, duration_minutes,
                    order_ids, metadata, raw_data, source
                )
                VALUES (
                    :position_id, :symbol, :side,
                    :entry_price, :exit_price, :quantity,
                    :pnl, :pnl_percent, :commission,
                    :status, :leverage, :margin,
                    :open_time, :close_time, :duration_minutes,
                    :order_ids, :metadata, :raw_data, :source
                )
                ON CONFLICT (position_id) DO UPDATE
                SET exit_price = EXCLUDED.exit_price,
                    pnl = EXCLUDED.pnl,
                    pnl_percent = EXCLUDED.pnl_percent,
                    commission = EXCLUDED.commission,
                    status = EXCLUDED.status,
                    close_time = EXCLUDED.close_time,
                    duration_minutes = EXCLUDED.duration_minutes,
                    metadata = EXCLUDED.metadata,
                    raw_data = EXCLUDED.raw_data,
                    source = EXCLUDED.source,
                    updated_at = NOW()
            """)

            # JSON 변환
            order_ids_json = json.dumps(position_data.get('order_ids', []))
            metadata_json = json.dumps(position_data.get('metadata', {}))
            raw_data_json = json.dumps(position_data.get('raw_data', {}))

            # Closed/Liquidated 상태인데 exit_price가 없으면 entry_price 사용
            exit_price = position_data.get('exit_price')
            status = position_data.get('status', '').lower()
            if status in ['closed', 'liquidated'] and (exit_price is None or exit_price == 0):
                exit_price = position_data.get('entry_price')
                logger.warning(f"⚠️ 포지션 {position_data['position_id']}: Closed 상태인데 exit_price가 없어 entry_price({exit_price}) 사용")
            
            with self.engine.connect() as conn:
                conn.execute(query, {
                    'position_id': position_data['position_id'],
                    'symbol': position_data['symbol'],
                    'side': position_data['side'],
                    'entry_price': position_data['entry_price'],
                    'exit_price': exit_price,
                    'quantity': position_data['quantity'],
                    'pnl': position_data.get('pnl'),
                    'pnl_percent': position_data.get('pnl_percent'),
                    'commission': position_data.get('commission'),
                    'status': position_data['status'],
                    'leverage': position_data.get('leverage'),
                    'margin': position_data.get('margin'),
                    'open_time': position_data['open_time'],
                    'close_time': position_data.get('close_time'),
                    'duration_minutes': position_data.get('duration_minutes'),
                    'order_ids': order_ids_json,
                    'metadata': metadata_json,
                    'raw_data': raw_data_json,
                    'source': position_data.get('source', 'binance')
                })
                conn.commit()

            logger.info(f"✅ 포지션 저장: {position_data['position_id']}")
            return True

        except Exception as e:
            logger.error(f"❌ 포지션 저장 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def get_positions(
        self,
        limit: int = 100,
        offset: int = 0,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict]:
        """
        포지션 목록 조회

        Args:
            limit: 최대 개수
            offset: 오프셋
            symbol: 심볼 필터
            side: 'buy', 'sell' 필터
            status: 'open', 'closed', 'liquidated' 필터
            start_date: 시작일
            end_date: 종료일

        Returns:
            포지션 리스트
        """
        try:
            # 오픈 시간이 같은 포지션들을 합쳐서 조회
            query = """
                SELECT
                    MIN(position_id) as position_id,
                    symbol,
                    side,
                    AVG(entry_price) as entry_price,
                    AVG(exit_price) as exit_price,
                    SUM(quantity) as quantity,
                    SUM(pnl) as pnl,
                    AVG(pnl_percent) as pnl_percent,
                    SUM(commission) as commission,
                    status,
                    leverage,
                    SUM(margin) as margin,
                    open_time,
                    close_time,
                    AVG(duration_minutes) as duration_minutes,
                    COUNT(*) as trade_count
                FROM tradebot_positions
                WHERE 1=1
            """

            params = {}

            # 필터링
            if symbol:
                query += " AND symbol = %(symbol)s"
                params['symbol'] = symbol

            if side:
                query += " AND side = %(side)s"
                params['side'] = side

            if status:
                query += " AND status = %(status)s"
                params['status'] = status

            if start_date:
                query += " AND open_time >= %(start_date)s"
                params['start_date'] = start_date

            if end_date:
                query += " AND open_time <= %(end_date)s"
                params['end_date'] = end_date

            # 그룹화 (같은 심볼, 방향, 오픈시간)
            query += """
                GROUP BY symbol, side, status, leverage, open_time, close_time
                ORDER BY open_time DESC
                LIMIT %(limit)s OFFSET %(offset)s
            """
            params['limit'] = limit
            params['offset'] = offset

            df = pd.read_sql(query, self.engine, params=params)

            # NaN 값을 None으로 변환 (JSON 직렬화 문제 방지)
            df = df.where(pd.notnull(df), None)
            results = df.to_dict('records')

            # 숫자 필드의 nan을 0 또는 None으로 치환
            for r in results:
                for key in ['leverage', 'pnl', 'pnl_percent', 'commission', 'margin', 'duration_minutes', 'trade_count']:
                    if key in r and r[key] is not None:
                        try:
                            import math
                            if math.isnan(float(r[key])):
                                r[key] = 0 if key in ['leverage', 'trade_count'] else None
                        except (ValueError, TypeError):
                            pass
                
                # Closed/Liquidated 상태인데 exit_price가 NULL이면 entry_price 사용
                status = r.get('status', '').lower()
                if status in ['closed', 'liquidated']:
                    if r.get('exit_price') is None or (isinstance(r.get('exit_price'), float) and r.get('exit_price') == 0):
                        entry_price = r.get('entry_price')
                        if entry_price is not None:
                            r['exit_price'] = entry_price
                            logger.debug(f"⚠️ 포지션 {r.get('position_id', 'unknown')}: exit_price가 없어 entry_price({entry_price}) 사용")

            logger.debug(f"📋 포지션 {len(results)}개 조회 (그룹화됨)")

            return results

        except Exception as e:
            logger.error(f"❌ 포지션 조회 실패: {e}")
            return []

    def get_position_by_id(self, position_id: str) -> Optional[Dict]:
        """
        포지션 ID로 조회

        Args:
            position_id: 포지션 ID

        Returns:
            포지션 데이터 or None
        """
        try:
            query = """
                SELECT *
                FROM tradebot_positions
                WHERE position_id = %(position_id)s
            """

            df = pd.read_sql(query, self.engine, params={'position_id': position_id})

            if len(df) == 0:
                return None

            # NaN 값을 None으로 변환 (JSON 직렬화 문제 방지)
            df = df.where(pd.notnull(df), None)
            result = df.iloc[0].to_dict()

            # 숫자 필드의 nan을 0 또는 None으로 치환
            for key in ['leverage', 'pnl', 'pnl_percent', 'commission', 'margin', 'duration_minutes']:
                if key in result and result[key] is not None:
                    try:
                        import math
                        if math.isnan(float(result[key])):
                            result[key] = 0 if key == 'leverage' else None
                    except (ValueError, TypeError):
                        pass

            return result

        except Exception as e:
            logger.error(f"❌ 포지션 조회 실패 ({position_id}): {e}")
            return None

    def delete_positions_by_date(self, target_date: datetime) -> int:
        """
        특정 날짜의 포지션 삭제

        Args:
            target_date: 삭제할 날짜 (UTC)

        Returns:
            삭제된 포지션 개수
        """
        try:
            # 해당 날짜의 시작과 끝 시간 계산
            start_time = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_time = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)

            query = text("""
                DELETE FROM tradebot_positions
                WHERE open_time >= :start_time 
                  AND open_time <= :end_time
                  AND source = 'binance'
            """)

            with self.engine.connect() as conn:
                result = conn.execute(query, {
                    'start_time': start_time,
                    'end_time': end_time
                })
                conn.commit()
                deleted_count = result.rowcount

            logger.info(f"🗑️ 포지션 삭제: {target_date.strftime('%Y-%m-%d')} - {deleted_count}개")
            return deleted_count

        except Exception as e:
            logger.error(f"❌ 포지션 삭제 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return 0

    # ============================================================
    # 통계
    # ============================================================

    def get_statistics(
        self,
        symbol: Optional[str] = None,
        days: int = 30
    ) -> Dict:
        """
        통계 조회

        Args:
            symbol: 심볼 필터
            days: 최근 N일

        Returns:
            통계 데이터
        """
        try:
            cutoff = datetime.now() - timedelta(days=days)

            # 기본 쿼리
            query = """
                SELECT
                    COUNT(*) as total_positions,
                    COUNT(CASE WHEN pnl > 0 THEN 1 END) as wins,
                    COUNT(CASE WHEN pnl < 0 THEN 1 END) as losses,
                    AVG(pnl) as avg_pnl,
                    AVG(pnl_percent) as avg_pnl_percent,
                    SUM(pnl) as total_pnl,
                    MAX(pnl) as max_win,
                    MIN(pnl) as max_loss,
                    AVG(duration_minutes) as avg_duration_minutes,
                    SUM(commission) as total_commission
                FROM tradebot_positions
                WHERE open_time >= %(cutoff)s
                  AND status = 'closed'
            """

            params = {'cutoff': cutoff}

            if symbol:
                query += " AND symbol = %(symbol)s"
                params['symbol'] = symbol

            df = pd.read_sql(query, self.engine, params=params)

            if len(df) == 0:
                return {}

            stats = df.iloc[0].to_dict()

            # 승률 계산
            wins = stats.get('wins', 0) or 0
            losses = stats.get('losses', 0) or 0
            total = wins + losses

            if total > 0:
                stats['win_rate'] = wins / total
            else:
                stats['win_rate'] = 0.0

            return stats

        except Exception as e:
            logger.error(f"❌ 통계 조회 실패: {e}")
            return {}

    def get_daily_pnl(
        self,
        symbol: Optional[str] = None,
        days: int = 30,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict]:
        """
        일별 손익 조회

        Args:
            symbol: 심볼 필터
            days: 최근 N일 (start_date가 없을 때 사용)
            start_date: 시작일 (옵션)
            end_date: 종료일 (옵션)

        Returns:
            일별 손익 리스트
        """
        try:
            query = """
                SELECT
                    DATE(close_time) as date,
                    COUNT(*) as trades,
                    SUM(pnl) as daily_pnl,
                    AVG(pnl_percent) as avg_pnl_percent,
                    SUM(commission) as commission
                FROM tradebot_positions
                WHERE status = 'closed'
            """

            params = {}

            # 날짜 필터 적용
            if start_date:
                query += " AND close_time >= %(start_date)s"
                params['start_date'] = start_date
            else:
                cutoff = datetime.now() - timedelta(days=days)
                query += " AND close_time >= %(cutoff)s"
                params['cutoff'] = cutoff

            if end_date:
                query += " AND close_time <= %(end_date)s"
                params['end_date'] = end_date

            if symbol:
                query += " AND symbol = %(symbol)s"
                params['symbol'] = symbol

            query += " GROUP BY DATE(close_time) ORDER BY DATE(close_time) DESC"

            df = pd.read_sql(query, self.engine, params=params)

            results = df.to_dict('records')
            logger.debug(f"📋 일별 손익 {len(results)}개 조회")

            return results

        except Exception as e:
            logger.error(f"❌ 일별 손익 조회 실패: {e}")
            return []

    def get_hourly_pnl(
        self,
        symbol: Optional[str] = None,
        days: int = 7,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict]:
        """
        시간별 손익 조회

        Args:
            symbol: 심볼 필터
            days: 최근 N일 (start_date가 없을 때 사용)
            start_date: 시작일 (옵션)
            end_date: 종료일 (옵션)

        Returns:
            시간별 손익 리스트
        """
        try:
            query = """
                SELECT
                    DATE_TRUNC('hour', close_time) as hour,
                    COUNT(*) as trades,
                    SUM(pnl) as hourly_pnl,
                    AVG(pnl_percent) as avg_pnl_percent,
                    SUM(commission) as commission
                FROM tradebot_positions
                WHERE status = 'closed'
            """

            params = {}

            if start_date:
                query += " AND close_time >= %(start_date)s"
                params['start_date'] = start_date
            else:
                cutoff = datetime.now() - timedelta(days=days)
                query += " AND close_time >= %(cutoff)s"
                params['cutoff'] = cutoff

            if end_date:
                query += " AND close_time <= %(end_date)s"
                params['end_date'] = end_date

            if symbol:
                query += " AND symbol = %(symbol)s"
                params['symbol'] = symbol

            query += " GROUP BY DATE_TRUNC('hour', close_time) ORDER BY hour DESC"

            df = pd.read_sql(query, self.engine, params=params)

            results = df.to_dict('records')
            logger.debug(f"📋 시간별 손익 {len(results)}개 조회")

            return results

        except Exception as e:
            logger.error(f"❌ 시간별 손익 조회 실패: {e}")
            return []

    def get_monthly_pnl(
        self,
        symbol: Optional[str] = None,
        months: int = 12,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict]:
        """
        월별 손익 조회

        Args:
            symbol: 심볼 필터
            months: 최근 N개월 (start_date가 없을 때 사용)
            start_date: 시작일 (옵션)
            end_date: 종료일 (옵션)

        Returns:
            월별 손익 리스트
        """
        try:
            query = """
                SELECT
                    DATE_TRUNC('month', close_time) as month,
                    COUNT(*) as trades,
                    SUM(pnl) as monthly_pnl,
                    AVG(pnl_percent) as avg_pnl_percent,
                    SUM(commission) as commission
                FROM tradebot_positions
                WHERE status = 'closed'
            """

            params = {}

            if start_date:
                query += " AND close_time >= %(start_date)s"
                params['start_date'] = start_date
            else:
                cutoff = datetime.now() - timedelta(days=months * 30)
                query += " AND close_time >= %(cutoff)s"
                params['cutoff'] = cutoff

            if end_date:
                query += " AND close_time <= %(end_date)s"
                params['end_date'] = end_date

            if symbol:
                query += " AND symbol = %(symbol)s"
                params['symbol'] = symbol

            query += " GROUP BY DATE_TRUNC('month', close_time) ORDER BY month DESC"

            df = pd.read_sql(query, self.engine, params=params)

            results = df.to_dict('records')
            logger.debug(f"📋 월별 손익 {len(results)}개 조회")

            return results

        except Exception as e:
            logger.error(f"❌ 월별 손익 조회 실패: {e}")
            return []

    def get_symbol_pnl(
        self,
        days: int = 30
    ) -> List[Dict]:
        """
        심볼별 손익 조회

        Args:
            days: 최근 N일

        Returns:
            심볼별 손익 리스트
        """
        try:
            cutoff = datetime.now() - timedelta(days=days)

            query = """
                SELECT
                    symbol,
                    COUNT(*) as trades,
                    SUM(pnl) as total_pnl,
                    AVG(pnl) as avg_pnl,
                    AVG(pnl_percent) as avg_pnl_percent,
                    SUM(commission) as commission,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses
                FROM tradebot_positions
                WHERE close_time >= %(cutoff)s
                  AND status = 'closed'
                GROUP BY symbol
                ORDER BY total_pnl DESC
            """

            params = {'cutoff': cutoff}

            df = pd.read_sql(query, self.engine, params=params)

            results = df.to_dict('records')

            # 승률 계산
            for result in results:
                wins = result.get('wins', 0) or 0
                losses = result.get('losses', 0) or 0
                total = wins + losses
                result['win_rate'] = (wins / total) if total > 0 else 0.0

            logger.debug(f"📋 심볼별 손익 {len(results)}개 조회")

            return results

        except Exception as e:
            logger.error(f"❌ 심볼별 손익 조회 실패: {e}")
            return []

    def get_weekday_pnl(
        self,
        symbol: Optional[str] = None,
        days: int = 30,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict]:
        """
        요일별 손익 조회

        Args:
            symbol: 심볼 필터
            days: 최근 N일 (start_date가 없을 때 사용)
            start_date: 시작일 (옵션)
            end_date: 종료일 (옵션)

        Returns:
            요일별 손익 리스트 (0=일요일, 6=토요일)
        """
        try:
            query = """
                SELECT
                    EXTRACT(DOW FROM close_time) as weekday,
                    COUNT(*) as trades,
                    SUM(pnl) as total_pnl,
                    AVG(pnl) as avg_pnl,
                    AVG(pnl_percent) as avg_pnl_percent,
                    SUM(commission) as commission,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses
                FROM tradebot_positions
                WHERE status = 'closed'
            """

            params = {}

            if start_date:
                query += " AND close_time >= %(start_date)s"
                params['start_date'] = start_date
            else:
                cutoff = datetime.now() - timedelta(days=days)
                query += " AND close_time >= %(cutoff)s"
                params['cutoff'] = cutoff

            if end_date:
                query += " AND close_time <= %(end_date)s"
                params['end_date'] = end_date

            if symbol:
                query += " AND symbol = %(symbol)s"
                params['symbol'] = symbol

            query += " GROUP BY EXTRACT(DOW FROM close_time) ORDER BY weekday"

            df = pd.read_sql(query, self.engine, params=params)

            results = df.to_dict('records')

            # 승률 계산 및 요일 이름 추가
            weekday_names = ['일요일', '월요일', '화요일', '수요일', '목요일', '금요일', '토요일']
            for result in results:
                wins = result.get('wins', 0) or 0
                losses = result.get('losses', 0) or 0
                total = wins + losses
                result['win_rate'] = (wins / total) if total > 0 else 0.0
                result['weekday_name'] = weekday_names[int(result['weekday'])]

            logger.debug(f"📋 요일별 손익 {len(results)}개 조회")

            return results

        except Exception as e:
            logger.error(f"❌ 요일별 손익 조회 실패: {e}")
            return []

    def get_hour_pnl(
        self,
        symbol: Optional[str] = None,
        days: int = 30,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict]:
        """
        시간대별 손익 조회 (0~23시)

        Args:
            symbol: 심볼 필터
            days: 최근 N일 (start_date가 없을 때 사용)
            start_date: 시작일 (옵션)
            end_date: 종료일 (옵션)

        Returns:
            시간대별 손익 리스트
        """
        try:
            query = """
                SELECT
                    EXTRACT(HOUR FROM close_time) as hour,
                    COUNT(*) as trades,
                    SUM(pnl) as total_pnl,
                    AVG(pnl) as avg_pnl,
                    AVG(pnl_percent) as avg_pnl_percent,
                    SUM(commission) as commission,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses
                FROM tradebot_positions
                WHERE status = 'closed'
            """

            params = {}

            if start_date:
                query += " AND close_time >= %(start_date)s"
                params['start_date'] = start_date
            else:
                cutoff = datetime.now() - timedelta(days=days)
                query += " AND close_time >= %(cutoff)s"
                params['cutoff'] = cutoff

            if end_date:
                query += " AND close_time <= %(end_date)s"
                params['end_date'] = end_date

            if symbol:
                query += " AND symbol = %(symbol)s"
                params['symbol'] = symbol

            query += " GROUP BY EXTRACT(HOUR FROM close_time) ORDER BY hour"

            df = pd.read_sql(query, self.engine, params=params)

            results = df.to_dict('records')

            # 승률 계산
            for result in results:
                wins = result.get('wins', 0) or 0
                losses = result.get('losses', 0) or 0
                total = wins + losses
                result['win_rate'] = (wins / total) if total > 0 else 0.0

            logger.debug(f"📋 시간대별 손익 {len(results)}개 조회")

            return results

        except Exception as e:
            logger.error(f"❌ 시간대별 손익 조회 실패: {e}")
            return []
