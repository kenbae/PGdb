"""
Historical Data Dashboard API v2
개선사항:
- 년/월별 분포 추가
- 카운트 전용 API 추가
- 그리드 데이터 API 추가
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional
from datetime import datetime
import yaml
import traceback

app = FastAPI(title="Historical Data Dashboard v2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_config(config_path: str = "config.yaml") -> dict:
    encodings = ['utf-8', 'utf-8-sig', 'cp949', 'euc-kr']
    for encoding in encodings:
        try:
            with open(config_path, 'r', encoding=encoding) as f:
                return yaml.safe_load(f)
        except (UnicodeDecodeError, UnicodeError):
            continue
        except FileNotFoundError:
            raise FileNotFoundError(f"{config_path} 파일이 없습니다.")
    raise ValueError("config.yaml을 읽을 수 없습니다.")

try:
    CONFIG = load_config()
except:
    CONFIG = {}

def get_db_connection():
    db_config = CONFIG.get('db', {})
    return psycopg2.connect(
        host=db_config.get('host', 'localhost'),
        port=db_config.get('port', 5432),
        database=db_config.get('name', 'marketdb'),
        user=db_config.get('user', 'trader'),
        password=db_config.get('password', ''),
        client_encoding='utf8'
    )

@app.get("/")
async def root():
    try:
        with open("historical_data_dashboard.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: HTML file not found</h1>", status_code=404)

@app.get("/api/overview")
async def get_overview():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT COUNT(DISTINCT symbol) as total_symbols,
                       COUNT(*) as total_candles,
                       MIN(open_time) as earliest_date,
                       MAX(open_time) as latest_date
                FROM candles
            """)
            overview = cur.fetchone()
            
            cur.execute("""
                SELECT tf, COUNT(*) as count, COUNT(DISTINCT symbol) as symbols
                FROM candles GROUP BY tf
                ORDER BY CASE tf
                    WHEN '1m' THEN 1 WHEN '5m' THEN 2 WHEN '15m' THEN 3
                    WHEN '30m' THEN 4 WHEN '1h' THEN 5 WHEN '4h' THEN 6
                    WHEN '1d' THEN 7 ELSE 99 END
            """)
            timeframes = cur.fetchall()
            
            cur.execute("""
                SELECT EXTRACT(YEAR FROM open_time)::INTEGER as year,
                       COUNT(*) as count, COUNT(DISTINCT symbol) as symbols
                FROM candles
                WHERE open_time >= '2020-01-01' AND open_time < '2027-01-01'
                GROUP BY year ORDER BY year DESC
            """)
            years = cur.fetchall()
            
            cur.execute("""
                SELECT TO_CHAR(open_time, 'YYYY-MM') as month,
                       COUNT(*) as count, COUNT(DISTINCT symbol) as symbols
                FROM candles
                WHERE open_time >= NOW() - INTERVAL '12 months'
                GROUP BY month ORDER BY month DESC LIMIT 12
            """)
            months = cur.fetchall()
            
            return {
                "total_symbols": overview["total_symbols"],
                "total_candles": overview["total_candles"],
                "earliest_date": overview["earliest_date"].isoformat() if overview["earliest_date"] else None,
                "latest_date": overview["latest_date"].isoformat() if overview["latest_date"] else None,
                "timeframes": [dict(row) for row in timeframes],
                "year_distribution": [dict(row) for row in years],
                "month_distribution": [dict(row) for row in months],
                "timestamp": datetime.now().isoformat()
            }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

@app.get("/api/count-only")
async def get_count_only():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) as total_candles FROM candles")
            result = cur.fetchone()
            return {
                "total_candles": result["total_candles"],
                "timestamp": datetime.now().isoformat()
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

@app.get("/api/grid-data")
async def get_grid_data():
    """그리드용 데이터: 심볼/년도별로 TF 그룹화"""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 심볼/년도별로 TF 데이터 수집
            cur.execute("""
                SELECT 
                    symbol,
                    EXTRACT(YEAR FROM open_time)::INTEGER as year,
                    tf,
                    COUNT(*) as count
                FROM candles
                WHERE open_time >= '2020-01-01' AND open_time < '2027-01-01'
                GROUP BY symbol, year, tf
                ORDER BY symbol, year DESC, 
                    CASE tf
                        WHEN '1m' THEN 1 WHEN '5m' THEN 2 WHEN '15m' THEN 3
                        WHEN '30m' THEN 4 WHEN '1h' THEN 5 WHEN '4h' THEN 6
                        WHEN '1d' THEN 7 ELSE 99
                    END
            """)
            raw_data = cur.fetchall()
            
            # 심볼/년도별로 그룹화
            grouped = {}
            for row in raw_data:
                key = f"{row['symbol']}_{row['year']}"
                if key not in grouped:
                    grouped[key] = {
                        'symbol': row['symbol'],
                        'year': row['year'],
                        'tf_data': {},
                        'total': 0
                    }
                grouped[key]['tf_data'][row['tf']] = row['count']
                grouped[key]['total'] += row['count']
            
            # 리스트로 변환
            result = list(grouped.values())
            
            return {
                "data": result,
                "count": len(result),
                "timestamp": datetime.now().isoformat()
            }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

@app.get("/api/months-detail")
async def get_months_detail(symbol: str, year: int):
    """특정 심볼/년도의 월별 상세 (모든 TF)"""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    EXTRACT(MONTH FROM open_time)::INTEGER as month,
                    tf,
                    COUNT(*) as count
                FROM candles
                WHERE symbol = %s AND EXTRACT(YEAR FROM open_time) = %s
                GROUP BY month, tf
                ORDER BY month, 
                    CASE tf
                        WHEN '1m' THEN 1 WHEN '5m' THEN 2 WHEN '15m' THEN 3
                        WHEN '30m' THEN 4 WHEN '1h' THEN 5 WHEN '4h' THEN 6
                        WHEN '1d' THEN 7 ELSE 99
                    END
            """, (symbol, year))
            raw_data = cur.fetchall()
            
            # 월별로 그룹화
            months_data = {}
            for row in raw_data:
                month = row['month']
                if month not in months_data:
                    months_data[month] = {'month': month, 'tf_data': {}, 'total': 0}
                months_data[month]['tf_data'][row['tf']] = row['count']
                months_data[month]['total'] += row['count']
            
            return {
                "symbol": symbol,
                "year": year,
                "months": list(months_data.values()),
                "timestamp": datetime.now().isoformat()
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

if __name__ == "__main__":
    import uvicorn
    print("[INFO] Historical Data Dashboard v2")
    print("[INFO] http://localhost:8002")
    uvicorn.run(app, host="0.0.0.0", port=8002)
