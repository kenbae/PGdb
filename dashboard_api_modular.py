"""
FastAPI 기반 암호화폐 트레이딩 대시보드
모듈식 위젯 시스템 통합
"""

from fastapi import FastAPI, HTTPException, Query, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from typing import Optional, List
from pydantic import BaseModel
import yaml
import os
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# 위젯 매니저 import
from widgets.widget_manager import WidgetManager

# EMA 스캐너 매니저 import
from ema_scanner_manager import ema_scanner_manager

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
    return templates.TemplateResponse(request, "dashboard.html")


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
async def get_result_distribution(symbol: Optional[str] = None):
    """결과 분포 API"""
    try:
        engine = get_engine()
        
        query_str = """
            SELECT o.result, COUNT(*) as count
            FROM outcomes o
        """
        
        if symbol:
            query_str += """
            JOIN signals s ON s.signal_id = o.signal_id
            WHERE o.exit_price IS NOT NULL AND s.symbol = :symbol
            """
        else:
            query_str += """
            WHERE o.exit_price IS NOT NULL
            """
        
        query_str += """
            GROUP BY o.result
            ORDER BY 
                CASE o.result
                    WHEN 'tp2' THEN 1
                    WHEN 'tp1' THEN 2
                    WHEN 'sl' THEN 3
                    WHEN 'none' THEN 4
                    ELSE 5
                END
        """
        
        query = text(query_str)
        params = {"symbol": symbol} if symbol else {}
        
        with engine.connect() as conn:
            result = conn.execute(query, params).fetchall()
        
        labels = []
        values = []
        for row in result:
            labels.append(row[0].upper() if row[0] else 'UNKNOWN')
            values.append(row[1])
        
        return {"labels": labels, "values": values}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/daily-profit")
async def get_daily_profit(
    days: int = Query(30, ge=1, le=365),
    symbol: Optional[str] = None
):
    """일별 수익 API"""
    try:
        engine = get_engine()
        
        query_str = """
            SELECT 
                DATE(o.exit_time) as date,
                SUM(o.r_multiple) as daily_profit
            FROM outcomes o
        """
        
        if symbol:
            query_str += """
            JOIN signals s ON s.signal_id = o.signal_id
            WHERE o.exit_price IS NOT NULL
              AND o.exit_time >= CURRENT_DATE - INTERVAL ':days days'
              AND s.symbol = :symbol
            """
        else:
            query_str += """
            WHERE o.exit_price IS NOT NULL
              AND o.exit_time >= CURRENT_DATE - INTERVAL ':days days'
            """
        
        query_str += """
            GROUP BY DATE(o.exit_time)
            ORDER BY date
        """
        
        query = text(query_str)
        params = {"days": days}
        if symbol:
            params["symbol"] = symbol
        
        with engine.connect() as conn:
            result = conn.execute(query, params).fetchall()
        
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
                s.stop_loss,
                s.take_profit_1,
                s.take_profit_2,
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
                "signal_id": str(row[0]),  # uuid → str 변환
                "symbol": row[1],
                "tf": row[2],
                "open_time": row[3].isoformat() if row[3] else None,
                "direction": row[4],
                "entry": float(row[5]) if row[5] else None,
                "stop_loss": float(row[6]) if row[6] else None,
                "take_profit_1": float(row[7]) if row[7] else None,
                "take_profit_2": float(row[8]) if row[8] else None,
                "score": row[9],
                "result": row[10],
                "r_multiple": float(row[11]) if row[11] else None,
                "exit_time": row[12].isoformat() if row[12] else None
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
# LLM 보고서 API
# ============================================================

@app.get("/api/llm-report/{signal_id}")
async def get_llm_report(signal_id: str):  # int → str (uuid 처리)
    """특정 신호의 LLM 보고서 조회"""
    try:
        import uuid as uuid_module
        
        # UUID 유효성 검사
        try:
            uuid_module.UUID(signal_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"유효하지 않은 signal_id 형식입니다: {signal_id} (UUID 형식이어야 합니다)"
            )
        
        engine = get_engine()
        
        # signals 테이블 구조:
        # - signal_id (uuid) ← 중요!
        # - symbol, tf, open_time (llm_reports와 매칭 키)
        
        query = text("""
            SELECT 
                lr.id as report_id,
                lr.event_key,
                lr.response_json,
                lr.created_at,
                lr.symbol,
                lr.tf,
                lr.open_time,
                s.direction,
                s.entry,
                s.take_profit_1 as tp1,
                s.take_profit_2 as tp2,
                s.stop_loss as sl,
                s.score,
                s.signal_id
            FROM signals s
            LEFT JOIN llm_reports lr ON 
                lr.symbol = s.symbol 
                AND lr.tf = s.tf 
                AND lr.open_time = s.open_time
            WHERE s.signal_id = CAST(:signal_id AS uuid)
            ORDER BY lr.id DESC
            LIMIT 1
        """)
        
        with engine.connect() as conn:
            result = conn.execute(query, {"signal_id": signal_id}).fetchone()
            
            if not result:
                raise HTTPException(
                    status_code=404, 
                    detail=f"신호를 찾을 수 없습니다 (signal_id: {signal_id})"
                )
            
            # response_json이 None인 경우 (보고서 없음)
            if not result[2]:
                raise HTTPException(
                    status_code=404, 
                    detail=f"보고서를 찾을 수 없습니다 (signal_id: {signal_id})"
                )
            
            # response_json에서 데이터 추출
            response_json = result[2] if result[2] else {}
            
            # 보고서 텍스트 생성 (Markdown 형식)
            report_text = ""
            
            if isinstance(response_json, dict):
                # 제목
                if "headline" in response_json:
                    report_text += f"# {response_json['headline']}\n\n"
                
                # 신뢰도와 바이어스
                if "confidence" in response_json or "bias" in response_json:
                    report_text += "## 📊 기본 정보\n\n"
                    if "confidence" in response_json:
                        confidence = response_json["confidence"]
                        report_text += f"- **신뢰도**: {confidence:.0%}\n"
                    if "bias" in response_json:
                        bias = response_json["bias"]
                        bias_emoji = {"bull": "🐂", "bear": "🐻", "neutral": "⚖️"}.get(bias, "")
                        bias_kr = {"bull": "상승", "bear": "하락", "neutral": "중립"}.get(bias, bias)
                        report_text += f"- **시장 전망**: {bias_emoji} {bias_kr}\n"
                    report_text += "\n"
                
                # 가격 레벨
                if "levels" in response_json:
                    levels = response_json["levels"]
                    report_text += "## 💰 주요 가격 레벨\n\n"
                    if "entry" in levels:
                        report_text += f"- **진입가**: `{levels['entry']}`\n"
                    if "tp" in levels:
                        report_text += f"- **목표가**: `{levels['tp']}`\n"
                    if "sl" in levels:
                        report_text += f"- **손절가**: `{levels['sl']}`\n"
                    report_text += "\n"
                
                # 트레이딩 계획
                if "plan" in response_json and response_json["plan"]:
                    report_text += "## 📋 트레이딩 계획\n\n"
                    for item in response_json["plan"]:
                        report_text += f"- {item}\n"
                    report_text += "\n"
                
                # 근거
                if "evidence" in response_json and response_json["evidence"]:
                    report_text += "## ✓ 진입 근거\n\n"
                    for item in response_json["evidence"]:
                        report_text += f"- {item}\n"
                    report_text += "\n"
                
                # 리스크
                if "risks" in response_json and response_json["risks"]:
                    report_text += "## ⚠️ 리스크 요인\n\n"
                    for item in response_json["risks"]:
                        report_text += f"- {item}\n"
                    report_text += "\n"
                
                # 태그
                if "tags" in response_json and response_json["tags"]:
                    report_text += "## 🏷️ 태그\n\n"
                    tags_str = ", ".join([f"`{tag}`" for tag in response_json["tags"]])
                    report_text += f"{tags_str}\n"
            
            # 결과를 딕셔너리로 변환
            report = {
                "report_id": result[0],
                "signal_id": signal_id,
                "report_text": report_text,
                "response_json": response_json,
                "created_at": result[3].isoformat() if result[3] else None,
                "signal": {
                    "symbol": result[4],
                    "tf": result[5],
                    "direction": result[7],
                    "open_time": result[6].isoformat() if result[6] else None,
                    "entry": float(result[8]) if result[8] else None,
                    "tp1": float(result[9]) if result[9] else None,
                    "tp2": float(result[10]) if result[10] else None,
                    "sl": float(result[11]) if result[11] else None,
                    "score": result[12]
                }
            }
            
            return report
            
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(
            status_code=500, 
            detail=f"보고서 조회 실패: {str(e)}\n{traceback.format_exc()}"
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
    """파이프라인 실행 - run_pipeline.py 사용"""
    import os
    import sys
    
    try:
        # 현재 작업 디렉토리 로그
        cwd = os.getcwd()
        running_tasks[task_id]["logs"].append(f"📁 작업 디렉토리: {cwd}")
        running_tasks[task_id]["logs"].append("🚀 파이프라인 시작...")
        running_tasks[task_id]["progress"] = 10
        await asyncio.sleep(1)
        
        # run_pipeline.py 파일 존재 확인
        pipeline_path = os.path.join(cwd, "run_pipeline.py")
        
        if not os.path.exists(pipeline_path):
            running_tasks[task_id]["logs"].append(f"⚠️ {pipeline_path} 파일 없음")
            running_tasks[task_id]["logs"].append("💡 개별 스크립트로 실행...")
            
            # 폴백: 개별 스크립트 실행
            await execute_pipeline_fallback(task_id, cwd)
            return
        
        # run_pipeline.py 실행
        running_tasks[task_id]["logs"].append("📦 통합 파이프라인 실행...")
        running_tasks[task_id]["logs"].append(f"   실행: python {pipeline_path} --steps indicators signals")
        running_tasks[task_id]["progress"] = 20
        
        try:
            # UTF-8 환경 변수 설정
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUTF8'] = '1'
            
            # indicators와 signals만 실행 (ingest 제외)
            result = subprocess.run(
                [sys.executable, pipeline_path, "--steps", "indicators", "signals"],
                capture_output=True,
                text=True,
                timeout=600,  # 10분
                cwd=cwd,
                env=env,
                encoding='utf-8',
                errors='replace'
            )
            
            # stdout 출력 (실시간 로그)
            if result.stdout:
                stdout_lines = result.stdout.strip().split('\n')
                running_tasks[task_id]["logs"].append(f"   📤 파이프라인 출력 ({len(stdout_lines)}줄):")
                
                # 진행률 업데이트를 위한 키워드 감지
                for i, line in enumerate(stdout_lines):
                    if line.strip():
                        try:
                            running_tasks[task_id]["logs"].append(f"      {line[:120]}")
                        except:
                            running_tasks[task_id]["logs"].append(f"      [출력 표시 불가]")
                        
                        # 진행률 업데이트
                        if "지표 계산" in line or "indicators" in line.lower():
                            running_tasks[task_id]["progress"] = 40
                        elif "신호 생성" in line or "signals" in line.lower():
                            running_tasks[task_id]["progress"] = 70
                        elif "완료" in line or "Complete" in line:
                            running_tasks[task_id]["progress"] = 90
            else:
                running_tasks[task_id]["logs"].append("   📤 출력 없음")
            
            # stderr 출력 (경고/오류)
            if result.stderr:
                stderr_lines = result.stderr.strip().split('\n')
                running_tasks[task_id]["logs"].append(f"   ⚠️ 경고/오류 ({len(stderr_lines)}줄):")
                for line in stderr_lines[:15]:  # 처음 15줄
                    if line.strip():
                        try:
                            running_tasks[task_id]["logs"].append(f"      {line[:120]}")
                        except:
                            running_tasks[task_id]["logs"].append(f"      [오류 표시 불가]")
            
            # 리턴 코드 확인
            if result.returncode == 0:
                running_tasks[task_id]["logs"].append("✓ 파이프라인 완료")
                running_tasks[task_id]["status"] = "completed"
            else:
                running_tasks[task_id]["logs"].append(f"✗ 파이프라인 실패 (종료 코드: {result.returncode})")
                running_tasks[task_id]["status"] = "failed"
                        
        except subprocess.TimeoutExpired:
            running_tasks[task_id]["logs"].append("✗ 파이프라인 시간 초과 (10분)")
            running_tasks[task_id]["status"] = "failed"
        except Exception as e:
            running_tasks[task_id]["logs"].append(f"✗ 오류: {str(e)[:100]}")
            running_tasks[task_id]["status"] = "failed"
        
        running_tasks[task_id]["progress"] = 100
        running_tasks[task_id]["logs"].append("🎉 작업 완료!")
        
    except Exception as e:
        running_tasks[task_id]["status"] = "failed"
        running_tasks[task_id]["logs"].append(f"✗ 예상치 못한 오류: {str(e)}")
        import traceback
        running_tasks[task_id]["logs"].append(f"상세: {traceback.format_exc()[:300]}")


async def execute_pipeline_fallback(task_id: str, cwd: str):
    """폴백: 개별 스크립트 실행"""
    import sys
    
    indicators_path = os.path.join(cwd, "run_indicators.py")
    signals_path = os.path.join(cwd, "run_signals.py")
    
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
            
            if result.stdout:
                lines = result.stdout.strip().split('\n')[-5:]
                for line in lines:
                    if line.strip():
                        running_tasks[task_id]["logs"].append(f"      {line[:100]}")
            
            if result.returncode == 0:
                running_tasks[task_id]["logs"].append("✓ 지표 계산 완료")
            else:
                running_tasks[task_id]["logs"].append(f"✗ 지표 계산 실패")
        except Exception as e:
            running_tasks[task_id]["logs"].append(f"✗ 오류: {str(e)[:80]}")
    
    running_tasks[task_id]["progress"] = 60
    
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
            
            if result.stdout:
                lines = result.stdout.strip().split('\n')[-5:]
                for line in lines:
                    if line.strip():
                        running_tasks[task_id]["logs"].append(f"      {line[:100]}")
            
            if result.returncode == 0:
                running_tasks[task_id]["logs"].append("✓ 신호 생성 완료")
            else:
                running_tasks[task_id]["logs"].append(f"✗ 신호 생성 실패")
        except Exception as e:
            running_tasks[task_id]["logs"].append(f"✗ 오류: {str(e)[:80]}")
    
    running_tasks[task_id]["progress"] = 100
    running_tasks[task_id]["status"] = "completed"


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
                
                # UTF-8 환경 변수 설정
                env = os.environ.copy()
                env['PYTHONIOENCODING'] = 'utf-8'
                env['PYTHONUTF8'] = '1'
                
                result = subprocess.run(
                    [sys.executable, outcomes_path],
                    capture_output=True,
                    text=True,
                    timeout=600,
                    cwd=cwd,
                    env=env,
                    encoding='utf-8',
                    errors='replace'
                )
                
                running_tasks[task_id]["progress"] = 80
                
                # stdout 출력
                if result.stdout:
                    stdout_lines = result.stdout.strip().split('\n')
                    running_tasks[task_id]["logs"].append(f"   📤 출력 ({len(stdout_lines)}줄):")
                    if len(stdout_lines) <= 10:
                        for line in stdout_lines:
                            if line.strip():
                                try:
                                    running_tasks[task_id]["logs"].append(f"      {line[:100]}")
                                except:
                                    running_tasks[task_id]["logs"].append(f"      [출력 표시 불가]")
                    else:
                        for line in stdout_lines[:5]:
                            if line.strip():
                                try:
                                    running_tasks[task_id]["logs"].append(f"      {line[:100]}")
                                except:
                                    running_tasks[task_id]["logs"].append(f"      [출력 표시 불가]")
                        running_tasks[task_id]["logs"].append(f"      ... ({len(stdout_lines)-10}줄 생략) ...")
                        for line in stdout_lines[-5:]:
                            if line.strip():
                                try:
                                    running_tasks[task_id]["logs"].append(f"      {line[:100]}")
                                except:
                                    running_tasks[task_id]["logs"].append(f"      [출력 표시 불가]")
                else:
                    running_tasks[task_id]["logs"].append("   📤 출력 없음")
                
                # stderr 출력
                if result.stderr:
                    stderr_lines = result.stderr.strip().split('\n')
                    running_tasks[task_id]["logs"].append(f"   ⚠️ 경고/오류 ({len(stderr_lines)}줄):")
                    for line in stderr_lines[:10]:
                        if line.strip():
                            try:
                                running_tasks[task_id]["logs"].append(f"      {line[:100]}")
                            except:
                                running_tasks[task_id]["logs"].append(f"      [오류 표시 불가]")
                
                # 리턴 코드 확인
                if result.returncode == 0:
                    running_tasks[task_id]["logs"].append("✓ 결과 계산 완료")
                    running_tasks[task_id]["status"] = "completed"
                else:
                    running_tasks[task_id]["logs"].append(f"✗ 결과 계산 실패 (종료 코드: {result.returncode})")
                    running_tasks[task_id]["status"] = "failed"
                    
            except subprocess.TimeoutExpired:
                running_tasks[task_id]["logs"].append("✗ 결과 계산 시간 초과 (10분)")
                running_tasks[task_id]["status"] = "failed"
            except Exception as e:
                running_tasks[task_id]["logs"].append(f"✗ 오류: {str(e)[:100]}")
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
    """보고서 생성 - run_llm_reports.py 실행"""
    import os
    import sys
    
    try:
        cwd = os.getcwd()
        running_tasks[task_id]["logs"].append(f"📁 작업 디렉토리: {cwd}")
        running_tasks[task_id]["logs"].append("📄 보고서 생성 시작...")
        running_tasks[task_id]["progress"] = 10
        await asyncio.sleep(0.5)
        
        # run_llm_reports.py 파일 존재 확인
        reports_path = os.path.join(cwd, "run_llm_reports.py")
        
        if not os.path.exists(reports_path):
            running_tasks[task_id]["logs"].append(f"⚠️ {reports_path} 파일 없음")
            running_tasks[task_id]["logs"].append("💡 시뮬레이션 모드로 계속...")
            
            # 시뮬레이션
            await asyncio.sleep(2)
            running_tasks[task_id]["logs"].append("📊 데이터 수집 중...")
            running_tasks[task_id]["progress"] = 30
            await asyncio.sleep(2)
            
            running_tasks[task_id]["logs"].append("✏️ 보고서 작성 중...")
            running_tasks[task_id]["progress"] = 70
            await asyncio.sleep(2)
            
            running_tasks[task_id]["logs"].append("✓ 보고서 생성 완료 (시뮬레이션)")
            running_tasks[task_id]["progress"] = 100
            running_tasks[task_id]["status"] = "completed"
            return
        
        # run_llm_reports.py 실행
        running_tasks[task_id]["logs"].append("📝 LLM 보고서 생성 실행...")
        running_tasks[task_id]["logs"].append(f"   실행: python {reports_path}")
        running_tasks[task_id]["logs"].append("   ⚠️ 주의: API 호출로 인해 시간이 걸릴 수 있습니다 (최대 5분)")
        running_tasks[task_id]["progress"] = 20
        await asyncio.sleep(0.5)
        
        try:
            # UTF-8 환경 변수 설정 (Windows 인코딩 오류 방지)
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUTF8'] = '1'
            
            # 프로세스 실행을 비동기로 처리
            running_tasks[task_id]["logs"].append("   🚀 프로세스 시작...")
            running_tasks[task_id]["progress"] = 25
            
            # asyncio.create_subprocess_exec 사용 (비동기)
            process = await asyncio.create_subprocess_exec(
                sys.executable, reports_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env
            )
            
            # 타임아웃을 5분으로 축소하고 비동기로 대기
            running_tasks[task_id]["logs"].append("   ⏳ 실행 중... (최대 5분)")
            running_tasks[task_id]["progress"] = 30
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=300.0  # 5분으로 축소
                )
                
                # stdout 처리
                if stdout:
                    stdout_text = stdout.decode('utf-8', errors='replace')
                    stdout_lines = stdout_text.strip().split('\n')
                    running_tasks[task_id]["logs"].append(f"   📤 보고서 출력 ({len(stdout_lines)}줄):")
                    
                    # 진행률 업데이트를 위한 키워드 감지
                    for line in stdout_lines[-20:]:  # 마지막 20줄만 표시
                        if line.strip():
                            try:
                                safe_line = line[:120]
                                running_tasks[task_id]["logs"].append(f"      {safe_line}")
                            except:
                                running_tasks[task_id]["logs"].append(f"      [출력 표시 불가]")
                            
                            # 진행률 업데이트
                            if "수집" in line or "로드" in line or "Loading" in line.lower():
                                running_tasks[task_id]["progress"] = 40
                            elif "분석" in line or "Analyzing" in line.lower():
                                running_tasks[task_id]["progress"] = 60
                            elif "생성" in line or "Generating" in line.lower():
                                running_tasks[task_id]["progress"] = 80
                            elif "저장" in line or "Saving" in line.lower():
                                running_tasks[task_id]["progress"] = 90
                            elif "완료" in line or "Complete" in line.lower():
                                running_tasks[task_id]["progress"] = 95
                else:
                    running_tasks[task_id]["logs"].append("   📤 출력 없음")
                
                # stderr 처리
                if stderr:
                    stderr_text = stderr.decode('utf-8', errors='replace')
                    stderr_lines = stderr_text.strip().split('\n')
                    if any(line.strip() for line in stderr_lines):
                        running_tasks[task_id]["logs"].append(f"   ⚠️ 경고/오류 ({len(stderr_lines)}줄):")
                        for line in stderr_lines[:10]:  # 최대 10줄만
                            if line.strip():
                                try:
                                    safe_line = line[:120]
                                    running_tasks[task_id]["logs"].append(f"      {safe_line}")
                                except:
                                    running_tasks[task_id]["logs"].append(f"      [오류 표시 불가]")
                
                # 리턴 코드 확인
                if process.returncode == 0:
                    running_tasks[task_id]["logs"].append("✓ 보고서 생성 완료")
                    running_tasks[task_id]["status"] = "completed"
                else:
                    running_tasks[task_id]["logs"].append(f"✗ 보고서 생성 실패 (종료 코드: {process.returncode})")
                    running_tasks[task_id]["status"] = "failed"
                
            except asyncio.TimeoutError:
                running_tasks[task_id]["logs"].append("✗ 보고서 생성 시간 초과 (5분)")
                running_tasks[task_id]["logs"].append("   프로세스를 강제 종료합니다...")
                try:
                    process.kill()
                    await process.wait()
                except:
                    pass
                running_tasks[task_id]["status"] = "failed"
                        
        except Exception as e:
            running_tasks[task_id]["logs"].append(f"✗ 실행 오류: {str(e)[:200]}")
            running_tasks[task_id]["status"] = "failed"
            import traceback
            error_trace = traceback.format_exc()
            running_tasks[task_id]["logs"].append(f"   상세: {error_trace[:500]}")
        
        running_tasks[task_id]["progress"] = 100
        
    except Exception as e:
        running_tasks[task_id]["status"] = "failed"
        running_tasks[task_id]["logs"].append(f"✗ 예상치 못한 오류: {str(e)[:200]}")
        import traceback
        running_tasks[task_id]["logs"].append(f"   {traceback.format_exc()[:500]}")


# ============================================================
# EMA 스캐너 관리 API
# ============================================================

# Request 모델
class EmaScannerStartRequest(BaseModel):
    timeframe: str = "15m"
    top: int = 50
    llm_alert: bool = False

class EmaScannerRestartRequest(BaseModel):
    timeframe: str = "15m"
    top: int = 50
    llm_alert: bool = False


@app.get("/api/ema-scanner/status")
async def get_ema_scanner_status():
    """EMA 스캐너 상태 조회"""
    try:
        return ema_scanner_manager.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ema-scanner/start")
async def start_ema_scanner(request: EmaScannerStartRequest = None):
    """EMA 스캐너 시작"""
    try:
        # 기본값 사용
        if request is None:
            request = EmaScannerStartRequest()
        
        result = ema_scanner_manager.start_scanner(
            request.timeframe, 
            request.top, 
            request.llm_alert
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ema-scanner/stop")
async def stop_ema_scanner():
    """EMA 스캐너 중지"""
    try:
        result = ema_scanner_manager.stop_scanner()
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ema-scanner/restart")
async def restart_ema_scanner(request: EmaScannerRestartRequest = None):
    """EMA 스캐너 재시작"""
    try:
        # 기본값 사용
        if request is None:
            request = EmaScannerRestartRequest()
        
        result = ema_scanner_manager.restart_scanner(
            request.timeframe, 
            request.top, 
            request.llm_alert
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    
    # Windows CP949 안전 출력
    try:
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
    except UnicodeEncodeError:
        # CP949 환경에서는 이모지 없이 출력
        print("=" * 60)
        print("Crypto Trading Dashboard API v2.0")
        print("=" * 60)
        print(f"Dashboard: http://localhost:{args.port}")
        print(f"API Docs:  http://localhost:{args.port}/docs")
        print(f"Host:      {args.host}")
        print(f"Reload:    {'Enabled' if args.reload else 'Disabled'}")
        print(f"Widgets:   {len(widget_manager.widgets)} loaded")
        print("=" * 60)
        print(f"\nExit: Ctrl+C\n")
    
    if args.reload:
        # reload 옵션 사용 시 import string으로 전달
        uvicorn.run(
            "dashboard_api_modular:app",
            host=args.host,
            port=args.port,
            reload=True,
            log_level="info"
        )
    else:
        # reload 없을 때는 app 객체 직접 전달
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            reload=False,
            log_level="info"
        )
