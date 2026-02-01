#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
실시간 트레이딩 히스토리 DB 확인 스크립트
"""

import os
import sys
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import json

# .env 파일 로드
load_dotenv()

# DB 연결 정보
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'marketdb')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')

def check_database():
    """DB 확인"""
    try:
        db_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        engine = create_engine(db_url)
        
        print("=" * 80)
        print("🔍 실시간 트레이딩 히스토리 DB 확인")
        print("=" * 80)
        print()
        
        with engine.connect() as conn:
            # 1. 테이블 존재 확인
            print("1️⃣ 테이블 존재 확인")
            print("-" * 80)
            table_check = text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = 'tradebot_positions'
                );
            """)
            result = conn.execute(table_check).fetchone()
            table_exists = result[0] if result else False
            
            if not table_exists:
                print("❌ tradebot_positions 테이블이 존재하지 않습니다!")
                return
            print("✅ tradebot_positions 테이블 존재")
            print()
            
            # 2. 전체 포지션 수 확인
            print("2️⃣ 전체 포지션 통계")
            print("-" * 80)
            total_query = text("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN status = 'open' THEN 1 END) as open_count,
                    COUNT(CASE WHEN status = 'closed' THEN 1 END) as closed_count,
                    COUNT(CASE WHEN status = 'liquidated' THEN 1 END) as liquidated_count
                FROM tradebot_positions;
            """)
            result = conn.execute(total_query).fetchone()
            print(f"   전체 포지션: {result[0]}개")
            print(f"   - Open: {result[1]}개")
            print(f"   - Closed: {result[2]}개")
            print(f"   - Liquidated: {result[3]}개")
            print()
            
            # 3. Source별 통계
            print("3️⃣ Source별 통계")
            print("-" * 80)
            source_query = text("""
                SELECT 
                    COALESCE(source, 'NULL') as source,
                    COUNT(*) as count,
                    COUNT(CASE WHEN status = 'closed' THEN 1 END) as closed_count
                FROM tradebot_positions
                GROUP BY source
                ORDER BY count DESC;
            """)
            results = conn.execute(source_query).fetchall()
            if results:
                for row in results:
                    print(f"   {row[0]}: {row[1]}개 (Closed: {row[2]}개)")
            else:
                print("   데이터 없음")
            print()
            
            # 4. Realtime Trading 데이터 확인
            print("4️⃣ Realtime Trading 데이터 확인")
            print("-" * 80)
            realtime_query = text("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN status = 'closed' THEN 1 END) as closed_count,
                    COUNT(CASE WHEN status = 'open' THEN 1 END) as open_count
                FROM tradebot_positions
                WHERE source = 'realtime_trading';
            """)
            result = conn.execute(realtime_query).fetchone()
            realtime_total = result[0] if result else 0
            realtime_closed = result[1] if result else 0
            realtime_open = result[2] if result else 0
            
            print(f"   Realtime Trading 포지션: {realtime_total}개")
            print(f"   - Closed: {realtime_closed}개")
            print(f"   - Open: {realtime_open}개")
            print()
            
            # 5. Metadata에서 source 확인 (source 컬럼이 없을 수도 있음)
            print("5️⃣ Metadata에서 source 확인")
            print("-" * 80)
            metadata_query = text("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN status = 'closed' THEN 1 END) as closed_count
                FROM tradebot_positions
                WHERE metadata::text LIKE '%realtime_trading%'
                   OR metadata::text LIKE '%"source"%';
            """)
            result = conn.execute(metadata_query).fetchone()
            metadata_total = result[0] if result else 0
            metadata_closed = result[1] if result else 0
            print(f"   Metadata에 realtime_trading 포함: {metadata_total}개 (Closed: {metadata_closed}개)")
            print()
            
            # 6. 최근 7일간 Closed 포지션 (히스토리 페이지에서 조회하는 데이터)
            print("6️⃣ 최근 7일간 Closed 포지션 (히스토리 페이지용)")
            print("-" * 80)
            recent_query = text("""
                SELECT 
                    position_id,
                    symbol,
                    side,
                    entry_price,
                    exit_price,
                    quantity,
                    pnl,
                    pnl_percent,
                    status,
                    open_time,
                    close_time,
                    source,
                    metadata
                FROM tradebot_positions
                WHERE status = 'closed'
                  AND close_time >= NOW() - INTERVAL '7 days'
                ORDER BY close_time DESC
                LIMIT 20;
            """)
            results = conn.execute(recent_query).fetchall()
            
            if results:
                print(f"   최근 7일간 Closed 포지션: {len(results)}개 (최대 20개 표시)")
                print()
                print("   상세 내역:")
                print("   " + "-" * 76)
                for row in results:
                    metadata = row[12] if row[12] else {}
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except:
                            metadata = {}
                    
                    source = row[11] or metadata.get('source', 'unknown')
                    is_realtime = source == 'realtime_trading' or metadata.get('source') == 'realtime_trading'
                    
                    marker = "✅" if is_realtime else "  "
                    print(f"   {marker} {row[0][:20]:20s} | {row[1]:10s} | {row[2]:5s} | "
                          f"진입: {row[3]:10.2f} | 청산: {row[4]:10.2f} | "
                          f"PnL: {row[6]:10.2f} ({row[7]:6.2f}%) | "
                          f"시간: {row[10].strftime('%Y-%m-%d %H:%M:%S') if row[10] else 'N/A'}")
            else:
                print("   ❌ 최근 7일간 Closed 포지션이 없습니다!")
            print()
            
            # 7. Realtime Trading Closed 포지션 상세
            if realtime_closed > 0:
                print("7️⃣ Realtime Trading Closed 포지션 상세")
                print("-" * 80)
                detail_query = text("""
                    SELECT 
                        position_id,
                        symbol,
                        side,
                        entry_price,
                        exit_price,
                        quantity,
                        pnl,
                        pnl_percent,
                        close_time,
                        metadata
                    FROM tradebot_positions
                    WHERE source = 'realtime_trading'
                      AND status = 'closed'
                    ORDER BY close_time DESC
                    LIMIT 10;
                """)
                results = conn.execute(detail_query).fetchall()
                
                for row in results:
                    metadata = row[9] if row[9] else {}
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except:
                            metadata = {}
                    
                    exit_reason = metadata.get('exit_reason', 'unknown')
                    strategy = metadata.get('strategy', 'N/A')
                    timeframe = metadata.get('timeframe', 'N/A')
                    
                    print(f"   📊 {row[0][:20]:20s}")
                    print(f"      심볼: {row[1]} | 방향: {row[2]} | 전략: {strategy} | 타임프레임: {timeframe}")
                    print(f"      진입: {row[3]:.2f} | 청산: {row[4]:.2f} | 수량: {row[5]:.4f}")
                    print(f"      PnL: {row[6]:.2f} USDT ({row[7]:.2f}%) | 청산 사유: {exit_reason}")
                    print(f"      시간: {row[8].strftime('%Y-%m-%d %H:%M:%S') if row[8] else 'N/A'}")
                    print()
            
            # 8. 테이블 스키마 확인
            print("8️⃣ 테이블 스키마 확인")
            print("-" * 80)
            schema_query = text("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'tradebot_positions'
                ORDER BY ordinal_position;
            """)
            results = conn.execute(schema_query).fetchall()
            print("   컬럼 목록:")
            for row in results:
                nullable = "NULL" if row[2] == 'YES' else "NOT NULL"
                print(f"   - {row[0]:20s} ({row[1]:20s}) {nullable}")
            print()
            
            # 9. 권장 사항
            print("9️⃣ 권장 사항")
            print("-" * 80)
            if realtime_total == 0:
                print("   ⚠️ Realtime Trading 데이터가 없습니다.")
                print("   - 실시간 트레이딩 엔진이 실행 중인지 확인하세요")
                print("   - 포지션이 실제로 저장되고 있는지 확인하세요")
            elif realtime_closed == 0:
                print("   ⚠️ Closed 상태의 Realtime Trading 포지션이 없습니다.")
                print("   - 아직 포지션이 청산되지 않았을 수 있습니다")
                print("   - Open 상태의 포지션을 확인하세요")
            else:
                print("   ✅ Realtime Trading 데이터가 존재합니다!")
                print(f"   - 총 {realtime_total}개 포지션 (Closed: {realtime_closed}개)")
            
            print()
            print("=" * 80)
            
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_database()
