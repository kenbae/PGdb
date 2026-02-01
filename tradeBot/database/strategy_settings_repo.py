"""
Strategy Settings Repository

tradeBot에서 전략별 enabled/config override를 DB에 저장/조회합니다.

테이블: tradebot_strategy_settings
"""

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


class StrategySettingsRepo:
    """전략 설정(override) Repository"""

    def __init__(self, db_engine):
        self.engine = db_engine
        self._ensure_table()

    def _ensure_table(self):
        """테이블이 없으면 생성"""
        try:
            ddl = text("""
                CREATE TABLE IF NOT EXISTS tradebot_strategy_settings (
                    strategy_name VARCHAR(50) PRIMARY KEY,
                    enabled BOOLEAN,
                    config JSONB,
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """)
            with self.engine.connect() as conn:
                conn.execute(ddl)
                conn.commit()
        except Exception as e:
            # DB 권한/연결 문제 등으로 실패할 수 있으므로, 런타임에서 경고만 남김
            logger.warning(f"⚠️ tradebot_strategy_settings 테이블 확인/생성 실패: {e}")

    def get_version(self) -> Optional[str]:
        """마지막 변경 시각(버전) 문자열 반환"""
        try:
            q = text("SELECT MAX(updated_at) FROM tradebot_strategy_settings")
            with self.engine.connect() as conn:
                row = conn.execute(q).fetchone()
            if not row or not row[0]:
                return None
            return row[0].isoformat()
        except Exception as e:
            logger.error(f"❌ 전략 설정 버전 조회 실패: {e}")
            return None

    def get_all(self) -> List[Dict[str, Any]]:
        """전체 override 조회"""
        try:
            q = "SELECT strategy_name, enabled, config, updated_at FROM tradebot_strategy_settings ORDER BY strategy_name"
            # pandas 없이도 충분하지만, 기존 repo 패턴과 통일하기 위해 단순 fetch 사용
            with self.engine.connect() as conn:
                rows = conn.execute(text(q)).fetchall()
            results: List[Dict[str, Any]] = []
            for r in rows:
                cfg = r[2]
                # psycopg2는 jsonb를 dict로 반환하는 경우가 많지만, 문자열일 수도 있어 방어
                if isinstance(cfg, str):
                    try:
                        cfg = json.loads(cfg)
                    except Exception:
                        cfg = {}
                results.append({
                    "strategy_name": r[0],
                    "enabled": r[1],
                    "config": cfg or {},
                    "updated_at": r[3].isoformat() if r[3] else None
                })
            return results
        except Exception as e:
            logger.error(f"❌ 전략 설정 조회 실패: {e}")
            return []

    def get(self, strategy_name: str) -> Optional[Dict[str, Any]]:
        """단일 override 조회"""
        try:
            q = text("""
                SELECT strategy_name, enabled, config, updated_at
                FROM tradebot_strategy_settings
                WHERE strategy_name = :strategy_name
                LIMIT 1
            """)
            with self.engine.connect() as conn:
                row = conn.execute(q, {"strategy_name": strategy_name}).fetchone()
            if not row:
                return None
            cfg = row[2]
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except Exception:
                    cfg = {}
            return {
                "strategy_name": row[0],
                "enabled": row[1],
                "config": cfg or {},
                "updated_at": row[3].isoformat() if row[3] else None
            }
        except Exception as e:
            logger.error(f"❌ 전략 설정 단일 조회 실패({strategy_name}): {e}")
            return None

    def upsert(self, strategy_name: str, enabled: Optional[bool] = None, config: Optional[Dict[str, Any]] = None) -> bool:
        """enabled/config를 그대로 저장(덮어쓰기)"""
        try:
            q = text("""
                INSERT INTO tradebot_strategy_settings (strategy_name, enabled, config)
                VALUES (:strategy_name, :enabled, :config)
                ON CONFLICT (strategy_name) DO UPDATE
                SET enabled = EXCLUDED.enabled,
                    config = EXCLUDED.config,
                    updated_at = NOW()
            """)
            with self.engine.connect() as conn:
                conn.execute(q, {
                    "strategy_name": strategy_name,
                    "enabled": enabled,
                    "config": json.dumps(config or {})
                })
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 전략 설정 upsert 실패({strategy_name}): {e}")
            return False

    def patch(self, strategy_name: str, enabled: Optional[bool] = None, config_patch: Optional[Dict[str, Any]] = None) -> bool:
        """
        부분 업데이트: 기존 config에 patch를 merge 후 저장
        """
        current = self.get(strategy_name)
        new_config: Dict[str, Any] = {}
        if current and isinstance(current.get("config"), dict):
            new_config.update(current["config"])
        if config_patch:
            new_config.update(config_patch)

        new_enabled = enabled if enabled is not None else (current.get("enabled") if current else None)
        return self.upsert(strategy_name=strategy_name, enabled=new_enabled, config=new_config)

