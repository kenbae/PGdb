"""
FastAPI 기반 암호화폐 트레이딩 대시보드
HTML 템플릿 분리 버전
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from datetime import datetime, timedelta
from typing import Optional, List
import yaml
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
import os

app = FastAPI(
    title="Crypto Trading Dashboard", 
    version="1.0.0",
    description="암호화폐 트레이딩 대시보드 API"
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 템플릿 설정
templates = Jinja2Templates(directory="templates")

# Static 파일 설정 (CSS, JS, 이미지 등)
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


def load_config(path="config.yaml"):
    """설정 파일 로드"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_engine():
    """SQLAlchemy engine 생성"""
    cfg = load_config()
    db = cfg['db']
    dsn = f"postgresql://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['name']}"
    return create_engine(dsn)


# ============================================================
# 메인 페이지
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """메인 대시보드 페이지"""
    return templates.TemplateResponse("index.html", {"request": request})


# ============================================================
# API Endpoints
# ============================================================

@app.get("/api/stats")
async def get_stats():
    """전체 통계 API"""
    try:
        engine = get_engine()
        query = text("""
            SELECT 
                COUNT(*) as total_signals,
                COUNT(*) FILTER (WHERE o.result IN ('tp1', 'tp2')) * 100.0 / NULLIF(COUNT(*), 0) as win_rate,
                AVG(o.r_multiple) as avg_r,
                SUM(o.r_multiple) as total_profit
            FROM signals s
            LEFT JOIN outcomes o ON o.signal_id = s.signal_id
            WHERE o.exit_price IS NOT NULL
        """)
        
        with engine.connect() as conn:
            result = conn.execute(query).fetchone()
        
        return {
            "total_signals": result[0] or 0,
            "win_rate": float(result[1]) if result[1] else 0,
            "avg_r": float(result[2]) if result[2] else 0,
            "total_profit": float(result[3]) if result[3] else 0
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/result-distribution")
async def get_result_distribution():
    """결과 분포 API"""
    try:
        engine = get_engine()
        query = text("""
            SELECT result, COUNT(*) as count
            FROM outcomes
            WHERE exit_price IS NOT NULL
            GROUP BY result
            ORDER BY 
                CASE result
                    WHEN 'tp2' THEN 1
                    WHEN 'tp1' THEN 2
                    WHEN 'sl' THEN 3
                    WHEN 'none' THEN 4
                    ELSE 5
                END
        """)
        
        with engine.connect() as conn:
            result = conn.execute(query).fetchall()
        
        labels = []
        values = []
        for row in result:
            labels.append(row[0].upper() if row[0] else 'UNKNOWN')
            values.append(row[1])
        
        return {"labels": labels, "values": values}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/daily-profit")
async def get_daily_profit(days: int = Query(30, ge=1, le=365)):
    """일별 수익 API"""
    try:
        engine = get_engine()
        query = text("""
            SELECT 
                DATE(o.exit_time) as date,
                SUM(o.r_multiple) as daily_profit
            FROM outcomes o
            WHERE o.exit_price IS NOT NULL
              AND o.exit_time >= CURRENT_DATE - INTERVAL ':days days'
            GROUP BY DATE(o.exit_time)
            ORDER BY date
        """)
        
        with engine.connect() as conn:
            result = conn.execute(query, {"days": days}).fetchall()
        
        dates = []
        profits = []
        cumulative = 0
        cumulative_profits = []
        
        for row in result:
            dates.append(row[0].strftime('%Y-%m-%d'))
            profit = float(row[1]) if row[1] else 0
            profits.append(profit)
            cumulative += profit
            cumulative_profits.append(cumulative)
        
        return {
            "dates": dates,
            "daily_profit": profits,
            "cumulative_profit": cumulative_profits
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/recent-signals")
async def get_recent_signals(
    limit: int = Query(20, ge=1, le=100),
    symbol: Optional[str] = None
):
    """최근 신호 목록 API"""
    try:
        engine = get_engine()
        
        query = """
            SELECT 
                s.signal_id,
                s.symbol,
                s.tf,
                s.open_time,
                s.direction,
                s.entry,
                s.score,
                o.result,
                o.r_multiple,
                o.exit_time
            FROM signals s
            LEFT JOIN outcomes o ON o.signal_id = s.signal_id
            WHERE 1=1
        """
        
        params = {"limit": limit}
        
        if symbol:
            query += " AND s.symbol = :symbol"
            params["symbol"] = symbol
        
        query += " ORDER BY s.open_time DESC LIMIT :limit"
        
        with engine.connect() as conn:
            result = conn.execute(text(query), params).fetchall()
        
        signals = []
        for row in result:
            signals.append({
                "signal_id": row[0],
                "symbol": row[1],
                "tf": row[2],
                "open_time": row[3].isoformat() if row[3] else None,
                "direction": row[4],
                "entry": float(row[5]) if row[5] else None,
                "score": row[6],
                "result": row[7],
                "r_multiple": float(row[8]) if row[8] else None,
                "exit_time": row[9].isoformat() if row[9] else None
            })
        
        return signals
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/symbols")
async def get_symbols():
    """심볼 목록 API"""
    try:
        engine = get_engine()
        query = text("SELECT DISTINCT symbol FROM signals ORDER BY symbol")
        
        with engine.connect() as conn:
            result = conn.execute(query).fetchall()
        
        return [row[0] for row in result]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/symbol-stats/{symbol}")
async def get_symbol_stats(symbol: str):
    """심볼별 통계 API"""
    try:
        engine = get_engine()
        query = text("""
            SELECT 
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE o.result IN ('tp1', 'tp2')) * 100.0 / NULLIF(COUNT(*), 0) as win_rate,
                AVG(o.r_multiple) as avg_r,
                SUM(o.r_multiple) as total_r
            FROM signals s
            LEFT JOIN outcomes o ON o.signal_id = s.signal_id
            WHERE s.symbol = :symbol
              AND o.exit_price IS NOT NULL
        """)
        
        with engine.connect() as conn:
            result = conn.execute(query, {"symbol": symbol}).fetchone()
        
        return {
            "symbol": symbol,
            "total": result[0] or 0,
            "win_rate": float(result[1]) if result[1] else 0,
            "avg_r": float(result[2]) if result[2] else 0,
            "total_r": float(result[3]) if result[3] else 0
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/performance-by-timeframe")
async def get_performance_by_timeframe():
    """타임프레임별 성과 API"""
    try:
        engine = get_engine()
        query = text("""
            SELECT 
                s.tf,
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE o.result IN ('tp1', 'tp2')) * 100.0 / NULLIF(COUNT(*), 0) as win_rate,
                AVG(o.r_multiple) as avg_r
            FROM signals s
            LEFT JOIN outcomes o ON o.signal_id = s.signal_id
            WHERE o.exit_price IS NOT NULL
            GROUP BY s.tf
            ORDER BY s.tf
        """)
        
        with engine.connect() as conn:
            result = conn.execute(query).fetchall()
        
        data = []
        for row in result:
            data.append({
                "tf": row[0],
                "total": row[1] or 0,
                "win_rate": float(row[2]) if row[2] else 0,
                "avg_r": float(row[3]) if row[3] else 0
            })
        
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/performance-by-direction")
async def get_performance_by_direction():
    """방향별 성과 API"""
    try:
        engine = get_engine()
        query = text("""
            SELECT 
                s.direction,
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE o.result IN ('tp1', 'tp2')) * 100.0 / NULLIF(COUNT(*), 0) as win_rate,
                AVG(o.r_multiple) as avg_r
            FROM signals s
            LEFT JOIN outcomes o ON o.signal_id = s.signal_id
            WHERE o.exit_price IS NOT NULL
            GROUP BY s.direction
            ORDER BY s.direction
        """)
        
        with engine.connect() as conn:
            result = conn.execute(query).fetchall()
        
        data = []
        for row in result:
            data.append({
                "direction": row[0],
                "total": row[1] or 0,
                "win_rate": float(row[2]) if row[2] else 0,
                "avg_r": float(row[3]) if row[3] else 0
            })
        
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """헬스 체크 API"""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {
            "status": "healthy", 
            "timestamp": datetime.now().isoformat(),
            "database": "connected"
        }
    except Exception as e:
        raise HTTPException(
            status_code=503, 
            detail=f"Database connection failed: {str(e)}"
        )


# ============================================================
# 실행
# ============================================================

if __name__ == "__main__":
    import uvicorn
    import argparse
    
    parser = argparse.ArgumentParser(description='Crypto Trading Dashboard API')
    parser.add_argument('--port', type=int, default=8001, help='포트 번호 (기본값: 8001)')
    parser.add_argument('--host', default='0.0.0.0', help='호스트 주소 (기본값: 0.0.0.0)')
    parser.add_argument('--reload', action='store_true', help='자동 재로드 활성화 (개발용)')
    args = parser.parse_args()
    
    print("=" * 60)
    print("🚀 Crypto Trading Dashboard API")
    print("=" * 60)
    print(f"📊 Dashboard: http://localhost:{args.port}")
    print(f"📚 API Docs:  http://localhost:{args.port}/docs")
    print(f"🔧 Host:      {args.host}")
    print(f"🔄 Reload:    {'Enabled' if args.reload else 'Disabled'}")
    print("=" * 60)
    print(f"\n종료: Ctrl+C\n")
    
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info"
    )
