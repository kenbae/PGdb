"""
DB 스키마 확인 유틸리티

테이블 구조를 빠르게 확인
"""

import psycopg2
from core.config_loader import get_config


def get_table_schema(table_name: str):
    """
    테이블 구조 조회
    
    Args:
        table_name: 테이블 이름
    """
    config = get_config()
    
    conn = psycopg2.connect(
        host=config.get('db.host'),
        port=config.get('db.port'),
        dbname=config.get('db.name'),
        user=config.get('db.user'),
        password=config.get('db.password')
    )
    
    cursor = conn.cursor()
    
    # 테이블 구조
    cursor.execute("""
        SELECT 
            column_name,
            data_type,
            character_maximum_length,
            is_nullable,
            column_default
        FROM information_schema.columns 
        WHERE table_name = %s
        ORDER BY ordinal_position
    """, (table_name,))
    
    print("=" * 80)
    print(f"테이블: {table_name}")
    print("=" * 80)
    print()
    print(f"{'컬럼명':<25} {'타입':<20} {'NULL':<8} {'기본값':<20}")
    print("-" * 80)
    
    for row in cursor.fetchall():
        col_name = row[0]
        data_type = row[1]
        max_length = row[2]
        is_nullable = row[3]
        default = row[4] or ''
        
        # 타입 표시
        if max_length:
            type_str = f"{data_type}({max_length})"
        else:
            type_str = data_type
        
        nullable = "YES" if is_nullable == "YES" else "NOT NULL"
        
        print(f"{col_name:<25} {type_str:<20} {nullable:<8} {default:<20}")
    
    # 제약조건
    cursor.execute("""
        SELECT
            tc.constraint_name,
            tc.constraint_type,
            kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
        WHERE tc.table_name = %s
        ORDER BY tc.constraint_type, kcu.ordinal_position
    """, (table_name,))
    
    constraints = cursor.fetchall()
    
    if constraints:
        print()
        print("제약조건:")
        print("-" * 80)
        
        for row in constraints:
            constraint_name = row[0]
            constraint_type = row[1]
            column_name = row[2]
            
            print(f"  {constraint_type:<15} {column_name:<25} ({constraint_name})")
    
    # 인덱스
    cursor.execute("""
        SELECT
            indexname,
            indexdef
        FROM pg_indexes
        WHERE tablename = %s
        ORDER BY indexname
    """, (table_name,))
    
    indexes = cursor.fetchall()
    
    if indexes:
        print()
        print("인덱스:")
        print("-" * 80)
        
        for row in indexes:
            index_name = row[0]
            index_def = row[1]
            
            print(f"  {index_name}")
            print(f"    {index_def}")
            print()
    
    cursor.close()
    conn.close()


def list_tables():
    """모든 테이블 목록"""
    config = get_config()
    
    conn = psycopg2.connect(
        host=config.get('db.host'),
        port=config.get('db.port'),
        dbname=config.get('db.name'),
        user=config.get('db.user'),
        password=config.get('db.password')
    )
    
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    
    print("=" * 80)
    print("테이블 목록")
    print("=" * 80)
    print()
    
    for row in cursor.fetchall():
        print(f"  {row[0]}")
    
    cursor.close()
    conn.close()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("사용법:")
        print("  python check_schema.py list              # 테이블 목록")
        print("  python check_schema.py <table_name>      # 테이블 구조")
        print()
        print("예:")
        print("  python check_schema.py candles")
        print("  python check_schema.py signals")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "list":
        list_tables()
    else:
        get_table_schema(command)
