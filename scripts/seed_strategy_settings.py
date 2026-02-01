# -*- coding: utf-8 -*-
"""
전략 설정 DB 시드 스크립트

STRATEGY_DEFAULTS 기반으로 tradebot_strategy_settings에 초기 데이터 삽입.
기존 행이 있으면 건너뜀 (덮어쓰지 않음).

사용법:
  python scripts/seed_strategy_settings.py
  python scripts/seed_strategy_settings.py --force   # 기존 데이터도 덮어쓰기
"""

import sys
import os
import argparse

# PGdb 루트 경로 추가 (PGdb가 먼저 와야 strategies.strategy_defaults 로드됨)
_script_dir = os.path.dirname(os.path.abspath(__file__))
_pgdb_dir = os.path.dirname(_script_dir)
sys.path.insert(0, os.path.join(_pgdb_dir, 'tradeBot'))
sys.path.insert(0, _pgdb_dir)

import yaml
from sqlalchemy import create_engine
from database.strategy_settings_repo import StrategySettingsRepo
from strategies.strategy_defaults import STRATEGY_DEFAULTS


def load_config():
    """config.yaml 로드 (DB 연결 정보)"""
    config_path = os.path.join(_pgdb_dir, 'config.yaml')
    if not os.path.exists(config_path):
        return {}
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def main():
    parser = argparse.ArgumentParser(description='전략 설정 DB 시드')
    parser.add_argument('--force', action='store_true', help='기존 데이터도 덮어쓰기')
    args = parser.parse_args()

    config = load_config()
    db = config.get('db', {}) or {}
    db_url = (
        f"postgresql://{db.get('user', 'trader')}:{db.get('password', '')}"
        f"@{db.get('host', 'localhost')}:{db.get('port', 5432)}/{db.get('name', 'marketdb')}"
    )

    print("DB 연결 중...")
    engine = create_engine(db_url)
    repo = StrategySettingsRepo(engine)

    seeded = 0
    skipped = 0

    for name, defaults in STRATEGY_DEFAULTS.items():
        config_dict = dict(defaults)
        enabled = config_dict.pop('enabled', True)

        existing = repo.get(name)
        if existing and not args.force:
            print(f"  [{name}] 건너뜀 (이미 존재)")
            skipped += 1
            continue

        ok = repo.upsert(name, enabled=enabled, config=config_dict)
        if ok:
            print(f"  [{name}] 시드 완료 (enabled={enabled})")
            seeded += 1
        else:
            print(f"  [{name}] 실패")

    print(f"\n완료: {seeded}건 시드, {skipped}건 건너뜀")


if __name__ == '__main__':
    main()
