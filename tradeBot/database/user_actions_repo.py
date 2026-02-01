"""
User Actions Repository

사용자 진입/청산 기록 저장/조회
1단계: 사용자가 수동으로 진입/청산한 기록
2단계: 신호와 병합하여 학습 데이터셋 구축
"""

import logging
import json
from typing import List, Dict, Optional
from datetime import datetime
from sqlalchemy import create_engine, text
import pandas as pd

logger = logging.getLogger(__name__)


class UserActionsRepo:
    """사용자 액션 Repository"""

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
                CREATE TABLE IF NOT EXISTS tradebot_user_actions (
                    action_id VARCHAR(50) PRIMARY KEY,
                    signal_id VARCHAR(50),
                    action_type VARCHAR(20) NOT NULL,
                    symbol VARCHAR(20) NOT NULL,
                    side VARCHAR(10) NOT NULL,
                    price DECIMAL(20, 8),
                    quantity DECIMAL(20, 8),
                    reason TEXT,
                    metadata JSONB,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_tradebot_user_actions_signal_id ON tradebot_user_actions(signal_id);
                CREATE INDEX IF NOT EXISTS idx_tradebot_user_actions_symbol ON tradebot_user_actions(symbol);
                CREATE INDEX IF NOT EXISTS idx_tradebot_user_actions_created_at ON tradebot_user_actions(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_tradebot_user_actions_type ON tradebot_user_actions(action_type);
            """)
            with self.engine.connect() as conn:
                conn.execute(ddl)
                conn.commit()
            logger.info("✅ tradebot_user_actions 테이블 확인/생성 완료")
        except Exception as e:
            logger.warning(f"⚠️ tradebot_user_actions 테이블 확인/생성 실패: {e}")

    def save_action(
        self,
        action_id: str,
        action_type: str,
        symbol: str,
        side: str,
        price: Optional[float] = None,
        quantity: Optional[float] = None,
        signal_id: Optional[str] = None,
        reason: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        사용자 액션 저장

        Args:
            action_id: 고유 액션 ID
            action_type: 'enter', 'exit', 'modify'
            symbol: 심볼
            side: 'buy', 'sell'
            price: 가격
            quantity: 수량
            signal_id: 연결된 신호 ID (선택)
            reason: 사용자가 입력한 이유 (선택)
            metadata: 추가 메타데이터 (선택)

        Returns:
            성공 여부
        """
        try:
            query = text("""
                INSERT INTO tradebot_user_actions (
                    action_id, signal_id, action_type, symbol, side,
                    price, quantity, reason, metadata
                )
                VALUES (
                    :action_id, :signal_id, :action_type, :symbol, :side,
                    :price, :quantity, :reason, :metadata
                )
                ON CONFLICT (action_id) DO UPDATE
                SET price = EXCLUDED.price,
                    quantity = EXCLUDED.quantity,
                    reason = EXCLUDED.reason,
                    metadata = EXCLUDED.metadata
            """)

            with self.engine.connect() as conn:
                conn.execute(query, {
                    'action_id': action_id,
                    'signal_id': signal_id,
                    'action_type': action_type,
                    'symbol': symbol,
                    'side': side,
                    'price': price,
                    'quantity': quantity,
                    'reason': reason,
                    'metadata': json.dumps(metadata or {})
                })
                conn.commit()

            logger.info(f"✅ 사용자 액션 저장: {action_id} ({action_type})")
            return True

        except Exception as e:
            logger.error(f"❌ 사용자 액션 저장 실패 ({action_id}): {e}")
            return False

    def get_actions(
        self,
        signal_id: Optional[str] = None,
        symbol: Optional[str] = None,
        action_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict]:
        """
        사용자 액션 조회

        Args:
            signal_id: 신호 ID 필터
            symbol: 심볼 필터
            action_type: 액션 타입 필터 ('enter', 'exit', 'modify')
            start_date: 시작일
            end_date: 종료일
            limit: 최대 개수
            offset: 오프셋

        Returns:
            액션 리스트
        """
        try:
            query = """
                SELECT action_id, signal_id, action_type, symbol, side,
                       price, quantity, reason, metadata, created_at
                FROM tradebot_user_actions
                WHERE 1=1
            """
            params = {}

            if signal_id:
                query += " AND signal_id = %(signal_id)s"
                params['signal_id'] = signal_id

            if symbol:
                query += " AND symbol = %(symbol)s"
                params['symbol'] = symbol

            if action_type:
                query += " AND action_type = %(action_type)s"
                params['action_type'] = action_type

            if start_date:
                query += " AND created_at >= %(start_date)s"
                params['start_date'] = start_date

            if end_date:
                query += " AND created_at <= %(end_date)s"
                params['end_date'] = end_date

            query += " ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s"
            params['limit'] = limit
            params['offset'] = offset

            df = pd.read_sql(query, self.engine, params=params)

            # NaN 처리 및 JSON 파싱
            results = []
            for _, row in df.iterrows():
                metadata = row['metadata']
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except:
                        metadata = {}
                elif metadata is None:
                    metadata = {}

                results.append({
                    'action_id': row['action_id'],
                    'signal_id': row['signal_id'],
                    'action_type': row['action_type'],
                    'symbol': row['symbol'],
                    'side': row['side'],
                    'price': float(row['price']) if pd.notna(row['price']) else None,
                    'quantity': float(row['quantity']) if pd.notna(row['quantity']) else None,
                    'reason': row['reason'],
                    'metadata': metadata,
                    'created_at': row['created_at'].isoformat() if pd.notna(row['created_at']) else None
                })

            logger.debug(f"📋 사용자 액션 {len(results)}개 조회")
            return results

        except Exception as e:
            logger.error(f"❌ 사용자 액션 조회 실패: {e}")
            return []

    def get_actions_by_signal(self, signal_id: str) -> List[Dict]:
        """
        특정 신호와 연결된 사용자 액션 조회

        Args:
            signal_id: 신호 ID

        Returns:
            액션 리스트
        """
        return self.get_actions(signal_id=signal_id, limit=1000)
