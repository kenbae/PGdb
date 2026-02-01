"""
Signals Repository

signals 및 signal_results 테이블 CRUD 작업
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
import pandas as pd
import json

logger = logging.getLogger(__name__)


def convert_to_python_types(obj):
    """
    객체 내의 모든 numpy/pandas 타입을 Python 기본 타입으로 변환

    Args:
        obj: 변환할 객체 (dict, list, 또는 기본 타입)

    Returns:
        변환된 객체
    """
    import numpy as np

    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, (np.integer, np.floating)):
        return obj.item()  # numpy 타입을 Python 타입으로 변환
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {key: convert_to_python_types(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_python_types(item) for item in obj]
    else:
        return obj


class SignalsRepo:
    """신호 및 결과 Repository"""

    def __init__(self, db_engine):
        """
        초기화

        Args:
            db_engine: SQLAlchemy Engine
        """
        self.engine = db_engine

    # ============================================================
    # Signals (신호)
    # ============================================================

    def save_signal(self, signal_data: Dict) -> bool:
        """
        신호 저장

        Args:
            signal_data: 신호 데이터
                {
                    'signal_id': str,
                    'strategy_name': str,
                    'symbol': str,
                    'timeframe': str,
                    'signal_type': str,  # 'buy' or 'sell'
                    'entry_price': float,
                    'stop_loss': float,
                    'take_profit_1': float,
                    'take_profit_2': float,
                    'confidence': float,
                    'risk_reward': float,
                    'ai_decision': str,
                    'ai_confidence': float,
                    'ai_reasoning': str,
                    'ai_risk_assessment': str,
                    'ai_market_context': str,
                    'reasons': list,
                    'metadata': dict
                }

        Returns:
            성공 여부
        """
        try:
            query = text("""
                INSERT INTO tradebot_signals (
                    signal_id, strategy_name, symbol, timeframe, signal_type,
                    entry_price, stop_loss, take_profit_1, take_profit_2,
                    confidence, risk_reward,
                    ai_decision, ai_confidence, ai_reasoning, ai_risk_assessment, ai_market_context,
                    reasons, metadata
                )
                VALUES (
                    :signal_id, :strategy_name, :symbol, :timeframe, :signal_type,
                    :entry_price, :stop_loss, :take_profit_1, :take_profit_2,
                    :confidence, :risk_reward,
                    :ai_decision, :ai_confidence, :ai_reasoning, :ai_risk_assessment, :ai_market_context,
                    :reasons, :metadata
                )
                ON CONFLICT (signal_id) DO UPDATE
                SET updated_at = NOW()
            """)

            # 모든 numpy/pandas 타입을 Python 타입으로 변환
            reasons = convert_to_python_types(signal_data.get('reasons', []))
            metadata = convert_to_python_types(signal_data.get('metadata', {}))

            reasons_json = json.dumps(reasons)
            metadata_json = json.dumps(metadata)

            # 파라미터 값들도 변환
            params = {
                'signal_id': signal_data['signal_id'],
                'strategy_name': signal_data['strategy_name'],
                'symbol': signal_data['symbol'],
                'timeframe': signal_data['timeframe'],
                'signal_type': signal_data['signal_type'],
                'entry_price': convert_to_python_types(signal_data['entry_price']),
                'stop_loss': convert_to_python_types(signal_data['stop_loss']),
                'take_profit_1': convert_to_python_types(signal_data.get('take_profit_1')),
                'take_profit_2': convert_to_python_types(signal_data.get('take_profit_2')),
                'confidence': convert_to_python_types(signal_data.get('confidence')),
                'risk_reward': convert_to_python_types(signal_data.get('risk_reward')),
                'ai_decision': signal_data.get('ai_decision'),
                'ai_confidence': convert_to_python_types(signal_data.get('ai_confidence')),
                'ai_reasoning': signal_data.get('ai_reasoning'),
                'ai_risk_assessment': signal_data.get('ai_risk_assessment'),
                'ai_market_context': signal_data.get('ai_market_context'),
                'reasons': reasons_json,
                'metadata': metadata_json
            }

            with self.engine.connect() as conn:
                conn.execute(query, params)
                conn.commit()

            logger.info(f"✅ 신호 저장: {signal_data['signal_id']}")
            return True

        except Exception as e:
            logger.error(f"❌ 신호 저장 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def get_signals(
        self,
        limit: int = 100,
        offset: int = 0,
        signal_id: Optional[str] = None,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        signal_type: Optional[str] = None,
        ai_decision: Optional[str] = None,
        result_status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict]:
        """
        신호 목록 조회

        Args:
            limit: 최대 개수
            offset: 오프셋
            signal_id: 신호 ID 필터 (특정 신호 조회)
            symbol: 심볼 필터
            timeframe: 타임프레임 필터
            signal_type: 'buy', 'sell' 필터
            ai_decision: 'approve', 'reject', 'caution' 필터
            result_status: 결과 상태 필터 ('pending', 'tp1_hit', 'tp2_hit', 'sl_hit')
            start_date: 시작일
            end_date: 종료일

        Returns:
            신호 리스트
        """
        try:
            # 기본 쿼리
            query = """
                SELECT
                    s.*,
                    sr.status as result_status,
                    sr.pnl,
                    sr.pnl_percent,
                    sr.r_multiple,
                    sr.exit_time,
                    sr.is_simulation
                FROM tradebot_signals s
                LEFT JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
                WHERE 1=1
            """

            params = {}

            # 필터링
            if signal_id:
                query += " AND s.signal_id = %(signal_id)s"
                params['signal_id'] = signal_id

            if symbol:
                query += " AND s.symbol = %(symbol)s"
                params['symbol'] = symbol

            if timeframe:
                query += " AND s.timeframe = %(timeframe)s"
                params['timeframe'] = timeframe

            if signal_type:
                query += " AND s.signal_type = %(signal_type)s"
                params['signal_type'] = signal_type

            if ai_decision:
                query += " AND s.ai_decision = %(ai_decision)s"
                params['ai_decision'] = ai_decision

            if result_status:
                if result_status == 'pending':
                    # pending은 결과 테이블에 없거나 status가 NULL인 경우
                    query += " AND (sr.status IS NULL OR sr.status = 'pending')"
                elif result_status == 'win':
                    # win은 tp1_hit 또는 tp2_hit
                    query += " AND sr.status IN ('tp1_hit', 'tp2_hit')"
                elif result_status == 'loss':
                    # loss는 sl_hit
                    query += " AND sr.status = 'sl_hit'"
                else:
                    query += " AND sr.status = %(result_status)s"
                    params['result_status'] = result_status

            if start_date:
                query += " AND s.created_at >= %(start_date)s"
                params['start_date'] = start_date

            if end_date:
                query += " AND s.created_at <= %(end_date)s"
                params['end_date'] = end_date

            # 정렬 및 페이징
            query += " ORDER BY s.created_at DESC LIMIT %(limit)s OFFSET %(offset)s"
            params['limit'] = limit
            params['offset'] = offset

            df = pd.read_sql(query, self.engine, params=params)

            # NaN 값을 None으로 변환 (JSON 직렬화 오류 방지)
            df = df.replace({float('nan'): None, float('inf'): None, float('-inf'): None})
            df = df.where(pd.notna(df), None)

            results = df.to_dict('records')

            # 추가로 개별 값 NaN 체크 (float 타입의 NaN 처리)
            import math
            for record in results:
                for key, value in record.items():
                    if isinstance(value, float):
                        try:
                            if math.isnan(value) or math.isinf(value):
                                record[key] = None
                        except (TypeError, ValueError):
                            pass

            logger.debug(f"📋 신호 {len(results)}개 조회")

            return results

        except Exception as e:
            logger.error(f"❌ 신호 조회 실패: {e}")
            return []

    def get_signals_count(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        signal_type: Optional[str] = None,
        ai_decision: Optional[str] = None,
        result_status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> int:
        """
        신호 전체 개수 조회 (페이징용)

        Args:
            symbol: 심볼 필터
            timeframe: 타임프레임 필터
            signal_type: 'buy', 'sell' 필터
            ai_decision: 'approve', 'reject', 'caution' 필터
            result_status: 결과 상태 필터
            start_date: 시작일
            end_date: 종료일

        Returns:
            전체 개수
        """
        try:
            params = {}

            # result_status 필터가 있을 때만 JOIN 사용
            if result_status:
                if result_status == 'pending':
                    # pending은 결과 테이블에 없거나 status가 NULL인 경우
                    query = """
                        SELECT COUNT(*) as total
                        FROM tradebot_signals s
                        LEFT JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
                        WHERE (sr.status IS NULL OR sr.status = 'pending')
                    """
                elif result_status == 'win':
                    # win은 tp1_hit 또는 tp2_hit
                    query = """
                        SELECT COUNT(*) as total
                        FROM tradebot_signals s
                        INNER JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
                        WHERE sr.status IN ('tp1_hit', 'tp2_hit')
                    """
                elif result_status == 'loss':
                    # loss는 sl_hit
                    query = """
                        SELECT COUNT(*) as total
                        FROM tradebot_signals s
                        INNER JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
                        WHERE sr.status = 'sl_hit'
                    """
                else:
                    query = """
                        SELECT COUNT(*) as total
                        FROM tradebot_signals s
                        INNER JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
                        WHERE sr.status = :result_status
                    """
                    params['result_status'] = result_status
            else:
                query = """
                    SELECT COUNT(*) as total
                    FROM tradebot_signals s
                    WHERE 1=1
                """

            # 필터링
            if symbol:
                query += " AND s.symbol = :symbol"
                params['symbol'] = symbol

            if timeframe:
                query += " AND s.timeframe = :timeframe"
                params['timeframe'] = timeframe

            if signal_type:
                query += " AND s.signal_type = :signal_type"
                params['signal_type'] = signal_type

            if ai_decision:
                query += " AND s.ai_decision = :ai_decision"
                params['ai_decision'] = ai_decision

            if start_date:
                query += " AND s.created_at >= :start_date"
                params['start_date'] = start_date

            if end_date:
                query += " AND s.created_at <= :end_date"
                params['end_date'] = end_date

            with self.engine.connect() as conn:
                result = conn.execute(text(query), params)
                total = result.scalar()

            logger.debug(f"📊 신호 총 개수: {total}")
            return total

        except Exception as e:
            logger.error(f"❌ 신호 개수 조회 실패: {e}")
            return 0

    def get_signal_by_id(self, signal_id: str) -> Optional[Dict]:
        """
        신호 ID로 조회

        Args:
            signal_id: 신호 ID

        Returns:
            신호 데이터 or None
        """
        try:
            query = """
                SELECT
                    s.*,
                    sr.status as result_status,
                    sr.pnl,
                    sr.pnl_percent,
                    sr.r_multiple,
                    sr.exit_time,
                    sr.is_simulation
                FROM tradebot_signals s
                LEFT JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
                WHERE s.signal_id = %(signal_id)s
            """

            df = pd.read_sql(query, self.engine, params={'signal_id': signal_id})

            if len(df) == 0:
                return None

            # NaN 값을 None으로 변환
            df = df.replace({float('nan'): None, float('inf'): None, float('-inf'): None})
            df = df.where(pd.notna(df), None)

            result = df.iloc[0].to_dict()

            # 추가로 개별 값 NaN 체크
            import math
            for key, value in result.items():
                if isinstance(value, float):
                    try:
                        if math.isnan(value) or math.isinf(value):
                            result[key] = None
                    except (TypeError, ValueError):
                        pass

            return result

        except Exception as e:
            logger.error(f"❌ 신호 조회 실패 ({signal_id}): {e}")
            return None

    # ============================================================
    # Signal Results (결과)
    # ============================================================

    def save_result(self, result_data: Dict) -> bool:
        """
        결과 저장

        Args:
            result_data: 결과 데이터
                {
                    'signal_id': str,
                    'status': str,  # 'pending', 'tp1_hit', 'tp2_hit', 'sl_hit', 'expired', 'manual_close'
                    'exit_price': float,
                    'exit_time': datetime,
                    'pnl': float,
                    'pnl_percent': float,
                    'r_multiple': float,
                    'max_favorable_excursion': float,
                    'max_adverse_excursion': float,
                    'duration_minutes': int,
                    'is_simulation': bool,
                    'notes': str
                }

        Returns:
            성공 여부
        """
        try:
            query = text("""
                INSERT INTO tradebot_signal_results (
                    signal_id, status, exit_price, exit_time,
                    pnl, pnl_percent, r_multiple,
                    max_favorable_excursion, max_adverse_excursion,
                    duration_minutes, is_simulation, notes
                )
                VALUES (
                    :signal_id, :status, :exit_price, :exit_time,
                    :pnl, :pnl_percent, :r_multiple,
                    :max_favorable_excursion, :max_adverse_excursion,
                    :duration_minutes, :is_simulation, :notes
                )
                ON CONFLICT (signal_id) DO UPDATE
                SET status = EXCLUDED.status,
                    exit_price = EXCLUDED.exit_price,
                    exit_time = EXCLUDED.exit_time,
                    pnl = EXCLUDED.pnl,
                    pnl_percent = EXCLUDED.pnl_percent,
                    r_multiple = EXCLUDED.r_multiple,
                    max_favorable_excursion = EXCLUDED.max_favorable_excursion,
                    max_adverse_excursion = EXCLUDED.max_adverse_excursion,
                    duration_minutes = EXCLUDED.duration_minutes,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
            """)

            with self.engine.connect() as conn:
                conn.execute(query, result_data)
                conn.commit()

            logger.info(f"✅ 결과 저장: {result_data['signal_id']} → {result_data['status']}")
            return True

        except Exception as e:
            logger.error(f"❌ 결과 저장 실패: {e}")
            return False

    def get_pending_signals(self, hours: int = 72) -> List[Dict]:
        """
        결과 업데이트가 필요한 신호 조회

        Args:
            hours: 최근 N시간 이내 신호

        Returns:
            신호 리스트
        """
        try:
            cutoff = datetime.now() - timedelta(hours=hours)

            query = """
                SELECT s.*
                FROM tradebot_signals s
                LEFT JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
                WHERE s.created_at >= %(cutoff)s
                  AND (sr.status IS NULL OR sr.status = 'pending')
                ORDER BY s.created_at DESC
            """

            df = pd.read_sql(query, self.engine, params={'cutoff': cutoff})

            results = df.to_dict('records')
            logger.debug(f"📋 대기 중인 신호 {len(results)}개 조회")

            return results

        except Exception as e:
            logger.error(f"❌ 대기 신호 조회 실패: {e}")
            return []

    # ============================================================
    # 통계
    # ============================================================

    def get_statistics(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        days: int = 30
    ) -> Dict:
        """
        통계 조회

        Args:
            symbol: 심볼 필터
            timeframe: 타임프레임 필터
            days: 최근 N일

        Returns:
            통계 데이터
        """
        try:
            cutoff = datetime.now() - timedelta(days=days)

            # 기본 쿼리
            query = """
                SELECT
                    COUNT(*) as total_signals,
                    COUNT(CASE WHEN sr.status IN ('tp1_hit', 'tp2_hit') THEN 1 END) as wins,
                    COUNT(CASE WHEN sr.status = 'sl_hit' THEN 1 END) as losses,
                    AVG(sr.pnl_percent) as avg_pnl_percent,
                    AVG(sr.r_multiple) as avg_r_multiple,
                    SUM(sr.pnl) as total_pnl
                FROM tradebot_signals s
                LEFT JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
                WHERE s.created_at >= %(cutoff)s
            """

            params = {'cutoff': cutoff}

            if symbol:
                query += " AND s.symbol = %(symbol)s"
                params['symbol'] = symbol

            if timeframe:
                query += " AND s.timeframe = %(timeframe)s"
                params['timeframe'] = timeframe

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

    # ============================================================
    # 포지션 매칭
    # ============================================================

    def get_unmatched_signals(
        self,
        symbol: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> List[Dict]:
        """
        아직 포지션과 매칭되지 않은 신호 조회

        Args:
            symbol: 심볼 필터
            start_date: 시작 날짜 (YYYY-MM-DD)
            end_date: 종료 날짜 (YYYY-MM-DD)

        Returns:
            매칭되지 않은 신호 리스트
        """
        try:
            query = """
                SELECT signal_id, symbol, signal_type, entry_price, created_at
                FROM tradebot_signals
                WHERE matched_position_id IS NULL
            """
            params = {}

            if symbol:
                query += " AND symbol = %(symbol)s"
                params['symbol'] = symbol

            if start_date:
                query += " AND created_at >= %(start_date)s"
                params['start_date'] = start_date

            if end_date:
                query += " AND created_at <= %(end_date)s::date + INTERVAL '1 day'"
                params['end_date'] = end_date

            query += " ORDER BY created_at DESC"

            df = pd.read_sql(query, self.engine, params=params)
            return df.to_dict('records')

        except Exception as e:
            logger.error(f"❌ 미매칭 신호 조회 실패: {e}")
            return []

    def update_signal_match(
        self,
        signal_id: str,
        position_id: str
    ) -> bool:
        """
        신호에 매칭된 포지션 ID 저장

        Args:
            signal_id: 신호 ID
            position_id: 매칭된 포지션 ID

        Returns:
            성공 여부
        """
        try:
            query = text("""
                UPDATE tradebot_signals
                SET matched_position_id = :position_id,
                    matched_at = NOW()
                WHERE signal_id = :signal_id
            """)

            with self.engine.connect() as conn:
                result = conn.execute(query, {
                    'signal_id': signal_id,
                    'position_id': position_id
                })
                conn.commit()

            if result.rowcount > 0:
                logger.info(f"✅ 신호 매칭 업데이트: {signal_id} → {position_id}")
                return True
            return False

        except Exception as e:
            logger.error(f"❌ 신호 매칭 업데이트 실패: {e}")
            return False

    def get_matched_signals(
        self,
        symbol: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> List[Dict]:
        """
        포지션과 매칭된 신호 조회 (포지션 정보 포함)

        Args:
            symbol: 심볼 필터
            start_date: 시작 날짜 (YYYY-MM-DD)
            end_date: 종료 날짜 (YYYY-MM-DD)

        Returns:
            매칭된 신호 + 포지션 정보 리스트
        """
        try:
            query = """
                SELECT
                    s.*,
                    p.entry_price as position_entry_price,
                    p.exit_price as position_exit_price,
                    p.quantity as position_quantity,
                    p.pnl as position_pnl,
                    p.pnl_percent as position_pnl_percent,
                    p.open_time as position_open_time,
                    p.close_time as position_close_time,
                    p.source as position_source
                FROM tradebot_signals s
                INNER JOIN tradebot_positions p ON s.matched_position_id = p.position_id
                WHERE s.matched_position_id IS NOT NULL
            """
            params = {}

            if symbol:
                query += " AND s.symbol = %(symbol)s"
                params['symbol'] = symbol

            if start_date:
                query += " AND s.created_at >= %(start_date)s"
                params['start_date'] = start_date

            if end_date:
                query += " AND s.created_at <= %(end_date)s::date + INTERVAL '1 day'"
                params['end_date'] = end_date

            query += " ORDER BY s.created_at DESC"

            df = pd.read_sql(query, self.engine, params=params)
            return df.to_dict('records')

        except Exception as e:
            logger.error(f"❌ 매칭된 신호 조회 실패: {e}")
            return []

    # ============================================================
    # 필터링용 고유 값 조회
    # ============================================================

    def get_unique_symbols(self, limit: int = 100) -> List[str]:
        """
        고유 심볼 목록 조회 (최근 7일 이내 신호가 있는 심볼)

        Args:
            limit: 최대 개수

        Returns:
            심볼 리스트
        """
        try:
            query = """
                SELECT DISTINCT symbol 
                FROM tradebot_signals 
                WHERE created_at >= NOW() - INTERVAL '7 days'
                ORDER BY symbol
                LIMIT %(limit)s
            """
            df = pd.read_sql(query, self.engine, params={'limit': limit})
            symbols = df['symbol'].tolist()
            logger.debug(f"📋 고유 심볼 {len(symbols)}개 조회")
            return symbols
        except Exception as e:
            logger.error(f"❌ 고유 심볼 조회 실패: {e}")
            return []

    def get_unique_strategies(self, limit: int = 50) -> List[str]:
        """
        고유 전략 목록 조회 (최근 7일 이내 신호가 있는 전략)

        Args:
            limit: 최대 개수

        Returns:
            전략 리스트
        """
        try:
            query = """
                SELECT DISTINCT strategy_name as strategy
                FROM tradebot_signals 
                WHERE created_at >= NOW() - INTERVAL '7 days'
                AND strategy_name IS NOT NULL
                ORDER BY strategy_name
                LIMIT %(limit)s
            """
            df = pd.read_sql(query, self.engine, params={'limit': limit})
            strategies = df['strategy'].tolist()
            logger.debug(f"📋 고유 전략 {len(strategies)}개 조회")
            return strategies
        except Exception as e:
            logger.error(f"❌ 고유 전략 조회 실패: {e}")
            return []
