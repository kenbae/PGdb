# -*- coding: utf-8 -*-
"""
candles 테이블 구조 확인
"""

import psycopg2
import yaml

# Config 로드
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('db', {})

# DB 연결
conn = psycopg2.connect(
    host=db_config.get('host', 'localhost'),
    port=db_config.get('port', 5432),
    database=db_config.get('name', 'marketdb'),
    user=db_config.get('user', 'trader'),
    password=db_config.get('password', ''),
    client_encoding='utf8'
)

cur = conn.cursor()

print("=" * 60)
print("candles 테이블 구조")
print("=" * 60)

# 테이블 구조 확인
cur.execute("""
    SELECT 
        column_name, 
        data_type, 
        is_nullable,
        column_default,
        ordinal_position
    FROM information_schema.columns 
    WHERE table_name = 'candles'
    ORDER BY ordinal_position
""")

columns = cur.fetchall()

print(f"\n{'순서':<5} {'컬럼명':<20} {'타입':<30} {'NULL 허용':<10} {'기본값':<20}")
print("-" * 90)

for col in columns:
    pos, name, dtype, nullable, default = col[4], col[0], col[1], col[2], col[3]
    default_str = str(default)[:20] if default else ''
    print(f"{pos:<5} {name:<20} {dtype:<30} {nullable:<10} {default_str:<20}")

# UNIQUE 제약조건 확인
print("\n" + "=" * 60)
print("UNIQUE 제약조건")
print("=" * 60)

cur.execute("""
    SELECT
        conname as constraint_name,
        pg_get_constraintdef(oid) as constraint_def
    FROM pg_constraint
    WHERE conrelid = 'candles'::regclass
    AND contype = 'u'
""")

constraints = cur.fetchall()

for constraint in constraints:
    print(f"\n제약조건명: {constraint[0]}")
    print(f"정의: {constraint[1]}")

# 샘플 데이터 확인
print("\n" + "=" * 60)
print("샘플 데이터 (최근 5개)")
print("=" * 60)

cur.execute("""
    SELECT * FROM candles 
    ORDER BY created_at DESC 
    LIMIT 5
""")

rows = cur.fetchall()
col_names = [desc[0] for desc in cur.description]

print("\n컬럼:", ", ".join(col_names))
print("-" * 60)

for row in rows:
    print(row[:10])  # 처음 10개 컬럼만 출력

cur.close()
conn.close()

print("\n" + "=" * 60)
print("완료!")
print("=" * 60)
