# -*- coding: utf-8 -*-
"""
config.yaml을 UTF-8로 변환하는 스크립트
"""

import yaml

# 여러 인코딩으로 읽기 시도
encodings = ['utf-8', 'utf-8-sig', 'cp949', 'euc-kr', 'latin1']

config = None
detected_encoding = None

for encoding in encodings:
    try:
        with open('config.yaml', 'r', encoding=encoding) as f:
            config = yaml.safe_load(f)
        detected_encoding = encoding
        print(f"✅ config.yaml을 {encoding} 인코딩으로 읽기 성공!")
        break
    except (UnicodeDecodeError, UnicodeError):
        continue
    except Exception as e:
        print(f"❌ {encoding}로 읽기 실패: {e}")
        continue

if config is None:
    print("❌ config.yaml을 읽을 수 없습니다.")
    exit(1)

# 백업 생성
import shutil
import os
from datetime import datetime

backup_name = f"config.yaml.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
shutil.copy('config.yaml', backup_name)
print(f"✅ 백업 생성: {backup_name}")

# UTF-8로 다시 쓰기
with open('config.yaml', 'w', encoding='utf-8') as f:
    yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

print(f"✅ config.yaml을 UTF-8로 재생성했습니다!")
print("")
print("🔍 확인:")

# 재확인
with open('config.yaml', 'r', encoding='utf-8') as f:
    test_config = yaml.safe_load(f)

print("  - db.host:", test_config.get('db', {}).get('host'))
print("  - db.name:", test_config.get('db', {}).get('name'))
print("  - db.user:", test_config.get('db', {}).get('user'))
print("  - telegram.bot_token:", test_config.get('telegram', {}).get('bot_token')[:20] + "...")
print("")
print("✅ 완료! 이제 ema_scanner.py를 다시 실행하세요.")
