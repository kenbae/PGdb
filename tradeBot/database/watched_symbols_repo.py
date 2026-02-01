"""
감시 심볼 DB Repository

watched_symbols 테이블 CRUD 작업
"""

import logging
from typing import List, Dict, Optional
from sqlalchemy import create_engine, text
import pandas as pd

logger = logging.getLogger(__name__)


class WatchedSymbolsRepo:
    """감시 심볼 Repository"""

    def __init__(self, db_engine):
        """
        초기화

        Args:
            db_engine: SQLAlchemy Engine
        """
        self.engine = db_engine

    def get_all(self, enabled_only: bool = True) -> List[Dict]:
        """
        전체 감시 심볼 조회

        Args:
            enabled_only: enabled=true만 조회할지 여부

        Returns:
            감시 심볼 리스트
        """
        try:
            query = """
                SELECT id, symbol, timeframe, enabled, created_at, updated_at
                FROM watched_symbols
            """

            if enabled_only:
                query += " WHERE enabled = true"

            query += " ORDER BY created_at ASC"

            df = pd.read_sql(query, self.engine)

            results = df.to_dict('records')
            logger.debug(f"📋 감시 심볼 {len(results)}개 조회")

            return results

        except Exception as e:
            logger.error(f"❌ 감시 심볼 조회 실패: {e}")
            return []

    def get_symbols_list(self, enabled_only: bool = True) -> List[str]:
        """
        심볼 이름만 리스트로 반환

        Args:
            enabled_only: enabled=true만 조회할지 여부

        Returns:
            심볼 리스트 ['BTCUSDT', 'ETHUSDT', ...]
        """
        try:
            query = """
                SELECT symbol
                FROM watched_symbols
            """

            if enabled_only:
                query += " WHERE enabled = true"

            query += " ORDER BY created_at ASC"

            df = pd.read_sql(query, self.engine)

            symbols = df['symbol'].tolist()
            logger.debug(f"📋 감시 심볼: {symbols}")

            return symbols

        except Exception as e:
            logger.error(f"❌ 심볼 리스트 조회 실패: {e}")
            return []

    def add(self, symbol: str, timeframe: str = '15m', enabled: bool = True) -> bool:
        """
        감시 심볼 추가

        Args:
            symbol: 심볼
            timeframe: 타임프레임
            enabled: 활성화 여부

        Returns:
            성공 여부
        """
        try:
            query = text("""
                INSERT INTO watched_symbols (symbol, timeframe, enabled)
                VALUES (:symbol, :timeframe, :enabled)
                ON CONFLICT (symbol) DO UPDATE
                SET timeframe = EXCLUDED.timeframe,
                    enabled = EXCLUDED.enabled,
                    updated_at = NOW()
            """)

            with self.engine.connect() as conn:
                conn.execute(query, {
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'enabled': enabled
                })
                conn.commit()

            logger.info(f"✅ 감시 심볼 추가: {symbol} ({timeframe})")
            return True

        except Exception as e:
            logger.error(f"❌ 감시 심볼 추가 실패 ({symbol}): {e}")
            return False

    def remove(self, symbol: str) -> bool:
        """
        감시 심볼 삭제

        Args:
            symbol: 심볼

        Returns:
            성공 여부
        """
        try:
            query = text("""
                DELETE FROM watched_symbols
                WHERE symbol = :symbol
            """)

            with self.engine.connect() as conn:
                result = conn.execute(query, {'symbol': symbol})
                conn.commit()

            if result.rowcount > 0:
                logger.info(f"🗑️  감시 심볼 삭제: {symbol}")
                return True
            else:
                logger.warning(f"⚠️  감시 심볼 없음: {symbol}")
                return False

        except Exception as e:
            logger.error(f"❌ 감시 심볼 삭제 실패 ({symbol}): {e}")
            return False

    def toggle(self, symbol: str) -> Optional[bool]:
        """
        감시 심볼 활성화/비활성화 토글

        Args:
            symbol: 심볼

        Returns:
            변경된 enabled 상태 (True/False), 실패 시 None
        """
        try:
            query = text("""
                UPDATE watched_symbols
                SET enabled = NOT enabled,
                    updated_at = NOW()
                WHERE symbol = :symbol
                RETURNING enabled
            """)

            with self.engine.connect() as conn:
                result = conn.execute(query, {'symbol': symbol})
                conn.commit()
                row = result.fetchone()

            if row:
                new_state = row[0]
                logger.info(f"🔄 감시 심볼 토글: {symbol} → {new_state}")
                return new_state
            else:
                logger.warning(f"⚠️  감시 심볼 없음: {symbol}")
                return None

        except Exception as e:
            logger.error(f"❌ 감시 심볼 토글 실패 ({symbol}): {e}")
            return None

    def set_enabled(self, symbol: str, enabled: bool) -> bool:
        """
        감시 심볼 활성화/비활성화 설정

        Args:
            symbol: 심볼
            enabled: 활성화 여부

        Returns:
            성공 여부
        """
        try:
            query = text("""
                UPDATE watched_symbols
                SET enabled = :enabled,
                    updated_at = NOW()
                WHERE symbol = :symbol
            """)

            with self.engine.connect() as conn:
                result = conn.execute(query, {
                    'symbol': symbol,
                    'enabled': enabled
                })
                conn.commit()

            if result.rowcount > 0:
                logger.info(f"✅ 감시 심볼 설정: {symbol} → {enabled}")
                return True
            else:
                logger.warning(f"⚠️  감시 심볼 없음: {symbol}")
                return False

        except Exception as e:
            logger.error(f"❌ 감시 심볼 설정 실패 ({symbol}): {e}")
            return False

    def update_timeframe(self, symbol: str, timeframe: str) -> bool:
        """
        감시 심볼 타임프레임 변경

        Args:
            symbol: 심볼
            timeframe: 타임프레임

        Returns:
            성공 여부
        """
        try:
            query = text("""
                UPDATE watched_symbols
                SET timeframe = :timeframe,
                    updated_at = NOW()
                WHERE symbol = :symbol
            """)

            with self.engine.connect() as conn:
                result = conn.execute(query, {
                    'symbol': symbol,
                    'timeframe': timeframe
                })
                conn.commit()

            if result.rowcount > 0:
                logger.info(f"✅ 타임프레임 변경: {symbol} → {timeframe}")
                return True
            else:
                logger.warning(f"⚠️  감시 심볼 없음: {symbol}")
                return False

        except Exception as e:
            logger.error(f"❌ 타임프레임 변경 실패 ({symbol}): {e}")
            return False

    def exists(self, symbol: str) -> bool:
        """
        감시 심볼 존재 여부 확인

        Args:
            symbol: 심볼

        Returns:
            존재 여부
        """
        try:
            query = text("""
                SELECT COUNT(*) as cnt
                FROM watched_symbols
                WHERE symbol = :symbol
            """)

            with self.engine.connect() as conn:
                result = conn.execute(query, {'symbol': symbol})
                row = result.fetchone()

            return row[0] > 0 if row else False

        except Exception as e:
            logger.error(f"❌ 감시 심볼 존재 확인 실패 ({symbol}): {e}")
            return False

    def sync_from_backtest_watch_symbols(self) -> Dict:
        """
        백테스트의 watch_symbols 테이블에서 심볼+전략 정보를 가져와 동기화

        Returns:
            동기화 결과 {'added': int, 'symbols': list}
        """
        try:
            # 1. watch_symbols 테이블에서 활성 심볼+전략 조회
            query = """
                SELECT symbol, strategies, notes
                FROM watch_symbols
                WHERE active = true
            """

            df = pd.read_sql(query, self.engine)

            if df.empty:
                logger.info("📭 백테스트 watch_symbols에 데이터 없음")
                return {'added': 0, 'symbols': []}

            # 2. watched_symbols 테이블에 strategies 컬럼 추가 (없으면)
            add_column = text("""
                ALTER TABLE watched_symbols
                ADD COLUMN IF NOT EXISTS strategies VARCHAR(200),
                ADD COLUMN IF NOT EXISTS notes TEXT
            """)

            with self.engine.connect() as conn:
                try:
                    conn.execute(add_column)
                    conn.commit()
                except:
                    pass  # 이미 컬럼이 있으면 무시

            # 3. 각 심볼을 watched_symbols에 추가/업데이트
            added = 0
            symbols = []

            insert_query = text("""
                INSERT INTO watched_symbols (symbol, timeframe, enabled, strategies, notes)
                VALUES (:symbol, :timeframe, :enabled, :strategies, :notes)
                ON CONFLICT (symbol) DO UPDATE
                SET strategies = EXCLUDED.strategies,
                    notes = EXCLUDED.notes,
                    enabled = true,
                    updated_at = NOW()
            """)

            with self.engine.connect() as conn:
                for _, row in df.iterrows():
                    symbol = row['symbol']
                    strategies = row.get('strategies', '')
                    notes = row.get('notes', '')

                    conn.execute(insert_query, {
                        'symbol': symbol,
                        'timeframe': '15m',  # 기본 타임프레임
                        'enabled': True,
                        'strategies': strategies,
                        'notes': notes
                    })

                    added += 1
                    symbols.append(symbol)

                conn.commit()

            logger.info(f"✅ 백테스트 watch_symbols에서 {added}개 심볼 동기화: {symbols}")

            return {'added': added, 'symbols': symbols}

        except Exception as e:
            logger.error(f"❌ watch_symbols 동기화 실패: {e}")
            return {'added': 0, 'symbols': [], 'error': str(e)}

    def get_all_with_strategies(self, enabled_only: bool = True) -> List[Dict]:
        """
        전략 정보를 포함한 전체 감시 심볼 조회

        Args:
            enabled_only: enabled=true만 조회할지 여부

        Returns:
            감시 심볼 리스트 (전략 포함)
        """
        try:
            query = """
                SELECT id, symbol, timeframe, enabled, strategies, notes,
                       COALESCE(sort_order, 0) as sort_order, created_at, updated_at
                FROM watched_symbols
            """

            if enabled_only:
                query += " WHERE enabled = true"

            query += " ORDER BY sort_order ASC, symbol ASC"

            df = pd.read_sql(query, self.engine)

            results = df.to_dict('records')
            logger.debug(f"📋 감시 심볼 (전략 포함) {len(results)}개 조회")

            return results

        except Exception as e:
            logger.error(f"❌ 감시 심볼 조회 실패: {e}")
            return []

    def update_sort_order(self, symbols_order: List[str]) -> bool:
        """
        심볼 순서 업데이트

        Args:
            symbols_order: 순서대로 정렬된 심볼 리스트

        Returns:
            성공 여부
        """
        try:
            query = text("""
                UPDATE watched_symbols
                SET sort_order = :sort_order,
                    updated_at = NOW()
                WHERE symbol = :symbol
            """)

            with self.engine.connect() as conn:
                for idx, symbol in enumerate(symbols_order):
                    conn.execute(query, {
                        'symbol': symbol,
                        'sort_order': idx
                    })
                conn.commit()

            logger.info(f"✅ 심볼 순서 업데이트: {len(symbols_order)}개")
            return True

        except Exception as e:
            logger.error(f"❌ 심볼 순서 업데이트 실패: {e}")
            return False

    def sort_alphabetically(self) -> bool:
        """
        심볼을 알파벳 순으로 정렬

        Returns:
            성공 여부
        """
        try:
            query = text("""
                WITH ranked AS (
                    SELECT id, ROW_NUMBER() OVER (ORDER BY symbol) as rn
                    FROM watched_symbols
                )
                UPDATE watched_symbols
                SET sort_order = ranked.rn,
                    updated_at = NOW()
                FROM ranked
                WHERE watched_symbols.id = ranked.id
            """)

            with self.engine.connect() as conn:
                conn.execute(query)
                conn.commit()

            logger.info("✅ 심볼 알파벳순 정렬 완료")
            return True

        except Exception as e:
            logger.error(f"❌ 심볼 정렬 실패: {e}")
            return False

    def add_with_strategy(self, symbol: str, strategies: str, timeframe: str = '15m',
                          enabled: bool = True, notes: str = '') -> bool:
        """
        전략 정보와 함께 감시 심볼 추가

        Args:
            symbol: 심볼
            strategies: 전략 목록 (쉼표 구분)
            timeframe: 타임프레임
            enabled: 활성화 여부
            notes: 메모

        Returns:
            성공 여부
        """
        try:
            # strategies 컬럼 추가 (없으면)
            add_column = text("""
                ALTER TABLE watched_symbols
                ADD COLUMN IF NOT EXISTS strategies VARCHAR(200),
                ADD COLUMN IF NOT EXISTS notes TEXT
            """)

            with self.engine.connect() as conn:
                try:
                    conn.execute(add_column)
                    conn.commit()
                except:
                    pass

            query = text("""
                INSERT INTO watched_symbols (symbol, timeframe, enabled, strategies, notes)
                VALUES (:symbol, :timeframe, :enabled, :strategies, :notes)
                ON CONFLICT (symbol) DO UPDATE
                SET timeframe = EXCLUDED.timeframe,
                    enabled = EXCLUDED.enabled,
                    strategies = EXCLUDED.strategies,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
            """)

            with self.engine.connect() as conn:
                conn.execute(query, {
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'enabled': enabled,
                    'strategies': strategies,
                    'notes': notes
                })
                conn.commit()

            logger.info(f"✅ 감시 심볼 추가 (전략 포함): {symbol} ({strategies})")
            return True

        except Exception as e:
            logger.error(f"❌ 감시 심볼 추가 실패 ({symbol}): {e}")
            return False

    def update_strategies(self, symbol: str, strategies: str, timeframe: str = None) -> bool:
        """
        심볼의 전략 및 타임프레임 정보 업데이트

        Args:
            symbol: 심볼
            strategies: 전략 목록 (쉼표 구분)
            timeframe: 타임프레임 (선택적)

        Returns:
            성공 여부
        """
        try:
            if timeframe:
                query = text("""
                    UPDATE watched_symbols
                    SET strategies = :strategies,
                        timeframe = :timeframe,
                        updated_at = NOW()
                    WHERE symbol = :symbol
                """)
                params = {
                    'symbol': symbol,
                    'strategies': strategies,
                    'timeframe': timeframe
                }
            else:
                query = text("""
                    UPDATE watched_symbols
                    SET strategies = :strategies,
                        updated_at = NOW()
                    WHERE symbol = :symbol
                """)
                params = {
                    'symbol': symbol,
                    'strategies': strategies
                }

            with self.engine.connect() as conn:
                result = conn.execute(query, params)
                conn.commit()

            if result.rowcount > 0:
                tf_info = f" (TF: {timeframe})" if timeframe else ""
                logger.info(f"✅ 전략 업데이트: {symbol} → {strategies}{tf_info}")
                return True
            else:
                logger.warning(f"⚠️  감시 심볼 없음: {symbol}")
                return False

        except Exception as e:
            logger.error(f"❌ 전략 업데이트 실패 ({symbol}): {e}")
            return False
