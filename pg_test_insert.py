import os
from datetime import datetime, timezone
import psycopg

DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "marketdb"
DB_USER = "trader"
DB_PASSWORD = "kh0070"

def main():
    dsn = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD}"

    # 1) 연결
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # 2) 서버/DB 확인
            cur.execute("SELECT version();")
            print("[OK] Connected. Server:", cur.fetchone()[0])

            # 3) candles 테이블 존재 확인
            cur.execute("""
                SELECT to_regclass('public.candles');
            """)
            tbl = cur.fetchone()[0]
            if tbl != "candles":
                raise RuntimeError("candles 테이블을 찾지 못했습니다. (스키마 생성 먼저 필요)")

            # 4) 테스트 데이터 1건 insert (중복 시 DO NOTHING)
            symbol = "BTC/USDT"   # ccxt 스타일 심볼 예시 (원하면 BTCUSDT로 통일해도 됨)
            tf = "30m"
            open_time = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)

            cur.execute(
                """
                INSERT INTO candles
                (exchange, symbol, market, tf, open_time, open, high, low, close, volume, quote_volume)
                VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, tf, open_time) DO NOTHING
                RETURNING symbol, tf, open_time;
                """,
                ("binance", symbol, "usdtm", tf, open_time, 100.0, 110.0, 95.0, 105.0, 1234.5, 999999.0),
            )

            row = cur.fetchone()
            if row:
                print("[OK] Inserted:", row)
            else:
                print("[OK] Row already exists (ON CONFLICT DO NOTHING).")

            # 5) 들어간 값 조회
            cur.execute("""
                SELECT exchange, symbol, market, tf, open_time, open, high, low, close, volume
                FROM candles
                WHERE symbol=%s AND tf=%s AND open_time=%s
            """, (symbol, tf, open_time))
            print("[OK] Selected:", cur.fetchone())

        conn.commit()

if __name__ == "__main__":
    main()
