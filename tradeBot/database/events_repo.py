"""
Events Repository

append-only 이벤트 로그 저장/조회
1~4단계 전체에서 사용하는 공통 이벤트 스키마
"""

import logging
import json
from typing import List, Dict, Optional
from datetime import datetime
from sqlalchemy import create_engine, text
import pandas as pd

logger = logging.getLogger(__name__)


class EventsRepo:
    """이벤트 로그 Repository (append-only)"""

    def __init__(self, db_engine):
        """
        초기화

        Args:
            db_engine: SQLAlchemy Engine
        """
        self.engine = db_engine
        self._ensure_table()

    def _ensure_table(self):
        """테이블이 없으면 생성"""
        try:
            ddl = text("""
                CREATE TABLE IF NOT EXISTS tradebot_events (
                    event_id VARCHAR(50) PRIMARY KEY,
                    event_type VARCHAR(30) NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    symbol VARCHAR(20),
                    timeframe VARCHAR(10),
                    data JSONB NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_tradebot_events_type ON tradebot_events(event_type);
                CREATE INDEX IF NOT EXISTS idx_tradebot_events_timestamp ON tradebot_events(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_tradebot_events_symbol ON tradebot_events(symbol);
                CREATE INDEX IF NOT EXISTS idx_tradebot_events_symbol_timeframe ON tradebot_events(symbol, timeframe);
            """)
            with self.engine.connect() as conn:
                conn.execute(ddl)
                conn.commit()
            logger.info("✅ tradebot_events 테이블 확인/생성 완료")
        except Exception as e:
            logger.warning(f"⚠️ tradebot_events 테이블 확인/생성 실패: {e}")

    def save_event(
        self,
        event_id: str,
        event_type: str,
        timestamp: datetime,
        data: Dict,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None
    ) -> bool:
        """
        이벤트 저장 (append-only)

        Args:
            event_id: 고유 이벤트 ID
            event_type: 'signal', 'user_action', 'order', 'fill', 'position', 'outcome'
            timestamp: 이벤트 발생 시각
            data: 이벤트 상세 데이터 (JSONB)
            symbol: 심볼 (선택)
            timeframe: 타임프레임 (선택)

        Returns:
            성공 여부
        """
        try:
            query = text("""
                INSERT INTO tradebot_events (event_id, event_type, timestamp, symbol, timeframe, data)
                VALUES (:event_id, :event_type, :timestamp, :symbol, :timeframe, :data)
                ON CONFLICT (event_id) DO NOTHING
            """)

            with self.engine.connect() as conn:
                conn.execute(query, {
                    'event_id': event_id,
                    'event_type': event_type,
                    'timestamp': timestamp,
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'data': json.dumps(data)
                })
                conn.commit()

            logger.debug(f"✅ 이벤트 저장: {event_id} ({event_type})")
            return True

        except Exception as e:
            logger.error(f"❌ 이벤트 저장 실패 ({event_id}): {e}")
            return False

    def get_events(
        self,
        event_type: Optional[str] = None,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[Dict]:
        """
        이벤트 조회

        Args:
            event_type: 이벤트 타입 필터
            symbol: 심볼 필터
            timeframe: 타임프레임 필터
            start_time: 시작 시각
            end_time: 종료 시각
            limit: 최대 개수
            offset: 오프셋

        Returns:
            이벤트 리스트
        """
        try:
            query = """
                SELECT event_id, event_type, timestamp, symbol, timeframe, data, created_at
                FROM tradebot_events
                WHERE 1=1
            """
            params = {}

            if event_type:
                query += " AND event_type = :event_type"
                params['event_type'] = event_type

            if symbol:
                query += " AND symbol = :symbol"
                params['symbol'] = symbol

            if timeframe:
                query += " AND timeframe = :timeframe"
                params['timeframe'] = timeframe

            if start_time:
                query += " AND timestamp >= :start_time"
                params['start_time'] = start_time

            if end_time:
                query += " AND timestamp <= :end_time"
                params['end_time'] = end_time

            query += " ORDER BY timestamp DESC LIMIT :limit OFFSET :offset"
            params['limit'] = limit
            params['offset'] = offset

            df = pd.read_sql(query, self.engine, params=params)

            # JSONB 파싱
            results = []
            for _, row in df.iterrows():
                data = row['data']
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except:
                        data = {}
                elif data is None:
                    data = {}

                results.append({
                    'event_id': row['event_id'],
                    'event_type': row['event_type'],
                    'timestamp': row['timestamp'].isoformat() if pd.notna(row['timestamp']) else None,
                    'symbol': row['symbol'],
                    'timeframe': row['timeframe'],
                    'data': data,
                    'created_at': row['created_at'].isoformat() if pd.notna(row['created_at']) else None
                })

            logger.debug(f"📋 이벤트 {len(results)}개 조회")
            return results

        except Exception as e:
            logger.error(f"❌ 이벤트 조회 실패: {e}")
            return []

    def get_events_by_signal(self, signal_id: str) -> List[Dict]:
        """
        특정 신호와 관련된 모든 이벤트 조회

        Args:
            signal_id: 신호 ID

        Returns:
            이벤트 리스트
        """
        try:
            query = text("""
                SELECT event_id, event_type, timestamp, symbol, timeframe, data, created_at
                FROM tradebot_events
                WHERE data->>'signal_id' = :signal_id
                   OR data->>'related_signal_id' = :signal_id
                ORDER BY timestamp ASC
            """)

            df = pd.read_sql(query, self.engine, params={'signal_id': signal_id})

            results = []
            for _, row in df.iterrows():
                data = row['data']
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except:
                        data = {}
                elif data is None:
                    data = {}

                results.append({
                    'event_id': row['event_id'],
                    'event_type': row['event_type'],
                    'timestamp': row['timestamp'].isoformat() if pd.notna(row['timestamp']) else None,
                    'symbol': row['symbol'],
                    'timeframe': row['timeframe'],
                    'data': data,
                    'created_at': row['created_at'].isoformat() if pd.notna(row['created_at']) else None
                })

            return results

        except Exception as e:
            logger.error(f"❌ 신호별 이벤트 조회 실패 ({signal_id}): {e}")
            return []
