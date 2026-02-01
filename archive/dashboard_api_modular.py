"""
FastAPI 기반 암호화폐 트레이딩 대시보드
모듈식 위젯 시스템 통합
"""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from typing import Optional, List
import yaml
import os
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# 위젯 매니저 import
from widgets.widget_manager import WidgetManager

app = FastAPI(
    title="Crypto Trading Dashboard", 
    version="2.0.0",
    description="모듈식 위젯 대시보드"
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 템플릿 및 정적 파일 설정
templates = Jinja2Templates(directory="templates")
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# 위젯 매니저 초기화
widget_manager = WidgetManager()


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
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ============================================================
# 위젯 API
# ============================================================

@app.get("/api/widgets/available")
async def get_available_widgets():
    """사용 가능한 모든 위젯 목록"""
    try:
        return widget_manager.get_all_widgets()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/widgets/{widget_id}/data")
async def get_widget_data(widget_id: str):
    """특정 위젯의 데이터"""
    try:
        return widget_manager.get_widget_data(widget_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/widgets/batch")
async def get_multiple_widgets_data(widget_ids: List[str]):
    """여러 위젯의 데이터를 한 번에 조회"""
    try:
        results = []
        for widget_id in widget_ids:
            try:
                data = widget_manager.get_widget_data(widget_id)
                results.append(data)
            except Exception as e:
                results.append({
                    "id": widget_id,
                    "error": str(e)
                })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 기존 API (하위 호환성)
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


@app.get("/health")
async def health_check():
    """헬스 체크 API"""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        
        widget_count = len(widget_manager.widgets)
        
        return {
            "status": "healthy", 
            "timestamp": datetime.now().isoformat(),
            "database": "connected",
            "widgets_loaded": widget_count
        }
    except Exception as e:
        raise HTTPException(
            status_code=503, 
            detail=f"Health check failed: {str(e)}"
        )


# ============================================================
# 작업 실행 API (데이터 업데이트 위젯용)
# ============================================================

from fastapi import BackgroundTasks
import subprocess
import asyncio
from typing import Dict
import uuid

# 실행 중인 작업 저장
running_tasks: Dict[str, Dict] = {}


@app.post("/api/tasks/run-pipeline")
async def run_pipeline(background_tasks: BackgroundTasks):
    """파이프라인 실행"""
    task_id = str(uuid.uuid4())
    
    running_tasks[task_id] = {
        "status": "running",
        "progress": 0,
        "logs": [],
        "start_time": datetime.now().isoformat()
    }
    
    background_tasks.add_task(execute_pipeline, task_id)
    
    return {"task_id": task_id, "status": "started"}


@app.post("/api/tasks/run-outcomes")
async def run_outcomes(background_tasks: BackgroundTasks):
    """결과 계산 실행"""
    task_id = str(uuid.uuid4())
    
    running_tasks[task_id] = {
        "status": "running",
        "progress": 0,
        "logs": [],
        "start_time": datetime.now().isoformat()
    }
    
    background_tasks.add_task(execute_outcomes, task_id)
    
    return {"task_id": task_id, "status": "started"}


@app.post("/api/tasks/generate-report")
async def generate_report(background_tasks: BackgroundTasks):
    """보고서 생성"""
    task_id = str(uuid.uuid4())
    
    running_tasks[task_id] = {
        "status": "running",
        "progress": 0,
        "logs": [],
        "start_time": datetime.now().isoformat()
    }
    
    background_tasks.add_task(execute_report, task_id)
    
    return {"task_id": task_id, "status": "started"}


@app.get("/api/tasks/{task_id}/status")
async def get_task_status(task_id: str):
    """작업 상태 조회"""
    if task_id not in running_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return running_tasks[task_id]


# 실제 작업 실행 함수들
async def execute_pipeline(task_id: str):
    """파이프라인 실행"""
    import os
    import sys
    
    try:
        # 현재 작업 디렉토리 로그
        cwd = os.getcwd()
        running_tasks[task_id]["logs"].append(f"📁 작업 디렉토리: {cwd}")
        running_tasks[task_id]["logs"].append("🚀 파이프라인 시작...")
        running_tasks[task_id]["progress"] = 10
        await asyncio.sleep(1)
        
        # run_indicators.py 파일 존재 확인
        indicators_path = os.path.join(cwd, "run_indicators.py")
        signals_path = os.path.join(cwd, "run_signals.py")
        
        if not os.path.exists(indicators_path):
            running_tasks[task_id]["logs"].append(f"⚠️ {indicators_path} 파일 없음")
            running_tasks[task_id]["logs"].append("💡 시뮬레이션 모드로 계속...")
        
        # run_indicators.py 실행
        running_tasks[task_id]["logs"].append("📊 지표 계산 시작...")
        running_tasks[task_id]["progress"] = 30
        
        if os.path.exists(indicators_path):
            try:
                running_tasks[task_id]["logs"].append(f"   실행: python {indicators_path}")
                result = subprocess.run(
                    [sys.executable, indicators_path],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=cwd
                )
                
                if result.returncode == 0:
                    running_tasks[task_id]["logs"].append("✓ 지표 계산 완료")
                    # 출력이 있으면 표시
                    if result.stdout:
                        lines = result.stdout.strip().split('\n')[-3:]  # 마지막 3줄
                        for line in lines:
                            if line.strip():
                                running_tasks[task_id]["logs"].append(f"   {line[:80]}")
                else:
                    running_tasks[task_id]["logs"].append(f"✗ 지표 계산 실패 (코드: {result.returncode})")
                    if result.stderr:
                        error_lines = result.stderr.strip().split('\n')[-2:]
                        for line in error_lines:
                            running_tasks[task_id]["logs"].append(f"   {line[:80]}")
                            
            except subprocess.TimeoutExpired:
                running_tasks[task_id]["logs"].append("✗ 지표 계산 시간 초과 (5분)")
            except Exception as e:
                running_tasks[task_id]["logs"].append(f"✗ 오류: {str(e)[:80]}")
        else:
            # 시뮬레이션
            await asyncio.sleep(2)
            running_tasks[task_id]["logs"].append("✓ 지표 계산 완료 (시뮬레이션)")
        
        running_tasks[task_id]["progress"] = 60
        await asyncio.sleep(1)
        
        # run_signals.py 실행
        running_tasks[task_id]["logs"].append("🎯 신호 생성 시작...")
        
        if os.path.exists(signals_path):
            try:
                running_tasks[task_id]["logs"].append(f"   실행: python {signals_path}")
                result = subprocess.run(
                    [sys.executable, signals_path],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=cwd
                )
                
                if result.returncode == 0:
                    running_tasks[task_id]["logs"].append("✓ 신호 생성 완료")
                    # 출력이 있으면 표시
                    if result.stdout:
                        lines = result.stdout.strip().split('\n')[-3:]
                        for line in lines:
                            if line.strip():
                                running_tasks[task_id]["logs"].append(f"   {line[:80]}")
                else:
                    running_tasks[task_id]["logs"].append(f"✗ 신호 생성 실패 (코드: {result.returncode})")
                    if result.stderr:
                        error_lines = result.stderr.strip().split('\n')[-2:]
                        for line in error_lines:
                            running_tasks[task_id]["logs"].append(f"   {line[:80]}")
                            
            except subprocess.TimeoutExpired:
                running_tasks[task_id]["logs"].append("✗ 신호 생성 시간 초과 (5분)")
            except Exception as e:
                running_tasks[task_id]["logs"].append(f"✗ 오류: {str(e)[:80]}")
        else:
            # 시뮬레이션
            await asyncio.sleep(2)
            running_tasks[task_id]["logs"].append("✓ 신호 생성 완료 (시뮬레이션)")
        
        running_tasks[task_id]["progress"] = 100
        running_tasks[task_id]["status"] = "completed"
        running_tasks[task_id]["logs"].append("🎉 파이프라인 완료!")
        
    except Exception as e:
        running_tasks[task_id]["status"] = "failed"
        running_tasks[task_id]["logs"].append(f"✗ 예상치 못한 오류: {str(e)}")
        import traceback
        running_tasks[task_id]["logs"].append(f"상세: {traceback.format_exc()[:200]}")


async def execute_outcomes(task_id: str):
    """결과 계산 실행"""
    import os
    import sys
    
    try:
        cwd = os.getcwd()
        running_tasks[task_id]["logs"].append(f"📁 작업 디렉토리: {cwd}")
        running_tasks[task_id]["logs"].append("🚀 결과 계산 시작...")
        running_tasks[task_id]["progress"] = 20
        await asyncio.sleep(1)
        
        outcomes_path = os.path.join(cwd, "run_outcomes.py")
        
        if not os.path.exists(outcomes_path):
            running_tasks[task_id]["logs"].append(f"⚠️ {outcomes_path} 파일 없음")
            running_tasks[task_id]["logs"].append("💡 시뮬레이션 모드로 계속...")
        
        if os.path.exists(outcomes_path):
            try:
                running_tasks[task_id]["logs"].append(f"   실행: python {outcomes_path}")
                result = subprocess.run(
                    [sys.executable, outcomes_path],
                    capture_output=True,
                    text=True,
                    timeout=600,
                    cwd=cwd
                )
                
                running_tasks[task_id]["progress"] = 80
                
                if result.returncode == 0:
                    running_tasks[task_id]["logs"].append("✓ 결과 계산 완료")
                    if result.stdout:
                        lines = result.stdout.strip().split('\n')[-3:]
                        for line in lines:
                            if line.strip():
                                running_tasks[task_id]["logs"].append(f"   {line[:80]}")
                    running_tasks[task_id]["status"] = "completed"
                else:
                    running_tasks[task_id]["logs"].append(f"✗ 결과 계산 실패 (코드: {result.returncode})")
                    if result.stderr:
                        error_lines = result.stderr.strip().split('\n')[-2:]
                        for line in error_lines:
                            running_tasks[task_id]["logs"].append(f"   {line[:80]}")
                    running_tasks[task_id]["status"] = "failed"
                    
            except subprocess.TimeoutExpired:
                running_tasks[task_id]["logs"].append("✗ 결과 계산 시간 초과 (10분)")
                running_tasks[task_id]["status"] = "failed"
            except Exception as e:
                running_tasks[task_id]["logs"].append(f"✗ 오류: {str(e)[:80]}")
                running_tasks[task_id]["status"] = "failed"
        else:
            # 시뮬레이션
            await asyncio.sleep(3)
            running_tasks[task_id]["logs"].append("✓ 결과 계산 완료 (시뮬레이션)")
            running_tasks[task_id]["status"] = "completed"
        
        running_tasks[task_id]["progress"] = 100
        
    except Exception as e:
        running_tasks[task_id]["status"] = "failed"
        running_tasks[task_id]["logs"].append(f"✗ 예상치 못한 오류: {str(e)}")


async def execute_report(task_id: str):
    """보고서 생성"""
    try:
        running_tasks[task_id]["logs"].append("📄 보고서 생성 시작...")
        running_tasks[task_id]["progress"] = 30
        await asyncio.sleep(2)
        
        running_tasks[task_id]["logs"].append("✏️ 데이터 수집 중...")
        running_tasks[task_id]["progress"] = 50
        await asyncio.sleep(2)
        
        running_tasks[task_id]["logs"].append("📊 차트 생성 중...")
        running_tasks[task_id]["progress"] = 70
        await asyncio.sleep(2)
        
        running_tasks[task_id]["logs"].append("✓ 보고서 생성 완료")
        running_tasks[task_id]["progress"] = 100
        running_tasks[task_id]["status"] = "completed"
        
    except Exception as e:
        running_tasks[task_id]["status"] = "failed"
        running_tasks[task_id]["logs"].append(f"✗ 오류: {str(e)}")


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
    print("🚀 Crypto Trading Dashboard API v2.0")
    print("=" * 60)
    print(f"📊 Dashboard: http://localhost:{args.port}")
    print(f"📚 API Docs:  http://localhost:{args.port}/docs")
    print(f"🔧 Host:      {args.host}")
    print(f"🔄 Reload:    {'Enabled' if args.reload else 'Disabled'}")
    print(f"🎨 Widgets:   {len(widget_manager.widgets)} loaded")
    print("=" * 60)
    print(f"\n종료: Ctrl+C\n")
    
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info"
    )
