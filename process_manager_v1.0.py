#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
프로세스 매니저 v2.1 - HTTP 로깅 제어 추가
여러 프로그램을 웹에서 시작/중지/모니터링 + 실시간 로그 확인
"""

import argparse
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional
import subprocess
import psutil
import os
import signal
from datetime import datetime
from pathlib import Path
import yaml
import threading
import queue
import logging

# ============================================================
# 명령줄 인자 파싱
# ============================================================
parser = argparse.ArgumentParser(description='프로세스 매니저')
parser.add_argument('--no-access-log', action='store_true', 
                    help='HTTP 액세스 로그 숨기기 (GET /api/system 등)')
parser.add_argument('--quiet', '-q', action='store_true',
                    help='모든 HTTP 로그 숨기기')
args = parser.parse_args()

# ============================================================
# 로깅 설정
# ============================================================
if args.quiet:
    # 완전히 조용하게 (ERROR만 표시)
    logging.getLogger("uvicorn.access").setLevel(logging.ERROR)
    logging.getLogger("uvicorn.error").setLevel(logging.ERROR)
elif args.no_access_log:
    # 액세스 로그만 끄기
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
else:
    # 기본 (모든 로그 표시)
    pass

# ============================================================
# Config 로더 (multi-encoding support)
# ============================================================
def load_config(path="config.yaml"):
    """
    config.yaml 로드 (여러 인코딩 시도)
    UTF-8, CP949, EUC-KR, UTF-8-BOM 순서로 시도
    """
    encodings = ['utf-8', 'cp949', 'euc-kr', 'utf-8-sig']
    
    for encoding in encodings:
        try:
            with open(path, "r", encoding=encoding) as f:
                config = yaml.safe_load(f)
                return config
        except UnicodeDecodeError:
            continue
        except FileNotFoundError:
            raise FileNotFoundError(f"Config file not found: {path}")
    
    raise ValueError(f"Could not decode {path} with any known encoding: {encodings}")

# ============================================================
# FastAPI App
# ============================================================
app = FastAPI(title="Process Manager", version="2.0.0")

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 설정 파일
# ============================================================

PROCESS_CONFIG = {
    "dashboard": {
        "name": "대시보드",
        "command": ["python", "dashboard_api_modular.py", "--port", "8001"],
        "cwd": ".",
        "env": {},
        "icon": "📊",
        "description": "트레이딩 대시보드 서버 (포트 8001)",
        "symbol_option": False
    },
    "ema_scanner": {
        "name": "EMA 스캐너",
        "command": ["python", "ema_scanner.py", "--timeframe", "30m", "--top", "50"],
        "cwd": ".",
        "env": {},
        "icon": "🔍",
        "description": "EMA 골든/데드 크로스 스캐너",
        "symbol_option": False
    },
    "backfill": {
        "name": "데이터 백필",
        "command": ["python", "backfill_binance_vision.py", "--start-year", "2020"],
        "cwd": ".",
        "env": {},
        "icon": "📥",
        "description": "과거 데이터 수집",
        "symbol_option": False
    },
    "pipeline": {
        "name": "파이프라인",
        "command": ["python", "run_pipeline.py"],
        "cwd": ".",
        "env": {},
        "icon": "⚙️",
        "description": "데이터 수집 → 지표 → 신호",
        "symbol_option": True,  # 심볼 옵션 지원
        "symbol_param": "--symbol"  # 심볼 파라미터 이름
    },
    "outcomes": {
        "name": "신호 결과 평가",
        "command": ["python", "run_outcomes.py"],
        "cwd": ".",
        "env": {},
        "icon": "📊",
        "description": "신호의 승패 및 R-Multiple 계산",
        "symbol_option": True,  # 심볼 옵션 지원
        "symbol_param": "--symbol"  # 심볼 파라미터 이름
    },
    "llm_report": {
        "name": "LLM 보고서",
        "command": ["python", "run_llm_reports.py", "--workers", "8"],
        "cwd": ".",
        "env": {},
        "icon": "🤖",
        "description": "신호에 대한 AI 분석 보고서 생성 (8 workers)",
        "symbol_option": True,
        "symbol_param": "--regenerate-symbol"
    }
}

# 실행 중인 프로세스 저장
running_processes: Dict[str, subprocess.Popen] = {}

# 프로세스별 심볼 설정 저장
process_symbols: Dict[str, str] = {}

# 프로세스 로그 저장 (최근 100줄)
process_logs: Dict[str, List[str]] = {}
process_errors: Dict[str, List[str]] = {}

# ============================================================
# 로그 필터 설정
# ============================================================

# HTTP 로그 필터링 패턴 (ANSI 색상 코드 포함)
HTTP_LOG_PATTERNS = [
    r'\[32mINFO\[0m:.*GET /api/',
    r'\[32mINFO\[0m:.*POST /api/',
    r'\[32mINFO\[0m:.*"GET /api/',
    r'\[32mINFO\[0m:.*"POST /api/',
    r'INFO:.*GET /api/',
    r'INFO:.*POST /api/',
    r'127\.0\.0\.1:\d+ - ".*" \[32m200 OK\[0m',
    r'127\.0\.0\.1:\d+ - ".*" 200 OK',
]

# 로그 필터 활성화 여부 (프로그램별)
LOG_FILTER_ENABLED = {
    "dashboard": True,      # 대시보드는 HTTP 로그 필터링
    "ema_scanner": False,
    "backfill": False,
    "pipeline": False,
    "outcomes": False,
    "llm_report": False
}

# ============================================================
# 프로세스 관리
# ============================================================

class ProcessStatus(BaseModel):
    process_id: str
    name: str
    icon: str
    description: str
    status: str  # running, stopped, error
    pid: Optional[int] = None
    cpu_percent: Optional[float] = None
    memory_mb: Optional[float] = None
    started_at: Optional[str] = None
    uptime: Optional[str] = None
    symbol_option: bool = False  # 심볼 옵션 지원 여부
    current_symbols: Optional[str] = None  # 현재 설정된 심볼


def collect_output(proc, process_id, stream_type='stdout'):
    """프로세스 출력 수집 (별도 스레드)"""
    import re
    
    stream = proc.stdout if stream_type == 'stdout' else proc.stderr
    log_list = process_logs if stream_type == 'stdout' else process_errors
    
    if process_id not in log_list:
        log_list[process_id] = []
    
    try:
        for line in iter(stream.readline, ''):
            if not line:
                break
            
            line = line.strip()
            if not line:
                continue
            
            # HTTP 로그 필터링 (활성화된 경우)
            if LOG_FILTER_ENABLED.get(process_id, False):
                # HTTP 로그 패턴 체크
                skip_log = False
                for pattern in HTTP_LOG_PATTERNS:
                    if re.search(pattern, line):
                        skip_log = True
                        break
                
                if skip_log:
                    continue  # 이 로그는 무시
            
            # 타임스탬프 추가
            timestamp = datetime.now().strftime('%H:%M:%S')
            log_entry = f"[{timestamp}] {line}"
            
            log_list[process_id].append(log_entry)
            
            # 최대 100줄만 유지
            if len(log_list[process_id]) > 100:
                log_list[process_id].pop(0)
            
            # 콘솔에도 출력
            prefix = "ERR" if stream_type == 'stderr' else "OUT"
            print(f"[{process_id}:{prefix}] {line}")
            
    except Exception as e:
        print(f"[{process_id}] 로그 수집 오류: {e}")
    finally:
        stream.close()


def get_process_info(process_id: str, proc: subprocess.Popen) -> ProcessStatus:
    """프로세스 상태 정보 수집"""
    config = PROCESS_CONFIG.get(process_id)
    
    if proc is None or proc.poll() is not None:
        # 프로세스 종료됨
        return ProcessStatus(
            process_id=process_id,
            name=config["name"],
            icon=config["icon"],
            description=config["description"],
            status="stopped",
            symbol_option=config.get("symbol_option", False)
        )
    
    try:
        # psutil로 상세 정보 수집
        ps = psutil.Process(proc.pid)
        
        # CPU, 메모리 사용률
        cpu = ps.cpu_percent(interval=0.1)
        memory = ps.memory_info().rss / 1024 / 1024  # MB
        
        # 시작 시간
        create_time = datetime.fromtimestamp(ps.create_time())
        started_at = create_time.strftime("%Y-%m-%d %H:%M:%S")
        
        # 가동 시간
        uptime_seconds = (datetime.now() - create_time).total_seconds()
        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        seconds = int(uptime_seconds % 60)
        uptime = f"{hours}h {minutes}m {seconds}s"
        
        return ProcessStatus(
            process_id=process_id,
            name=config["name"],
            icon=config["icon"],
            description=config["description"],
            status="running",
            pid=proc.pid,
            cpu_percent=round(cpu, 1),
            memory_mb=round(memory, 1),
            started_at=started_at,
            uptime=uptime,
            symbol_option=config.get("symbol_option", False),
            current_symbols=process_symbols.get(process_id)
        )
        
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ProcessStatus(
            process_id=process_id,
            name=config["name"],
            icon=config["icon"],
            description=config["description"],
            status="error",
            symbol_option=config.get("symbol_option", False)
        )


def start_process(process_id: str, symbols: str = None) -> bool:
    """프로세스 시작"""
    if process_id in running_processes and running_processes[process_id].poll() is None:
        return False  # 이미 실행 중
    
    config = PROCESS_CONFIG.get(process_id)
    if not config:
        raise ValueError(f"Unknown process: {process_id}")
    
    try:
        # 로그 초기화
        process_logs[process_id] = []
        process_errors[process_id] = []
        
        # 명령어 구성 (심볼 옵션 포함)
        command = config["command"].copy()
        
        # 심볼 옵션 지원하는 프로세스면 심볼 추가
        if symbols and config.get("symbol_option", False):
            symbol_param = config.get("symbol_param", "--symbols")
            command.extend([symbol_param, symbols])
            process_symbols[process_id] = symbols
            print(f"\n{'='*60}")
            print(f"🎯 심볼 필터 활성화: {symbols}")
            print(f"{'='*60}\n")
        
        print(f"\n{'='*60}")
        print(f"🚀 {config['name']} 시작 중...")
        print(f"{'='*60}")
        print(f"명령어: {' '.join(command)}")
        print(f"작업 디렉토리: {os.path.abspath(config['cwd'])}")
        print(f"{'='*60}\n")
        
        # 프로세스 시작
        proc = subprocess.Popen(
            command,
            cwd=config["cwd"],
            env={**os.environ, **config["env"]},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1  # 라인 버퍼링
        )
        
        running_processes[process_id] = proc
        
        # 출력 수집 스레드 시작
        stdout_thread = threading.Thread(
            target=collect_output,
            args=(proc, process_id, 'stdout'),
            daemon=True
        )
        stderr_thread = threading.Thread(
            target=collect_output,
            args=(proc, process_id, 'stderr'),
            daemon=True
        )
        
        stdout_thread.start()
        stderr_thread.start()
        
        print(f"✅ {config['name']} 시작됨 - PID: {proc.pid}\n")
        
        return True
        
    except FileNotFoundError as e:
        print(f"❌ 파일을 찾을 수 없습니다: {e}")
        process_errors[process_id] = [f"파일을 찾을 수 없습니다: {e}"]
        return False
    except Exception as e:
        print(f"❌ {config['name']} 시작 실패: {e}")
        import traceback
        traceback.print_exc()
        process_errors[process_id] = [f"시작 실패: {e}"]
        return False


def stop_process(process_id: str) -> bool:
    """프로세스 중지"""
    proc = running_processes.get(process_id)
    
    if proc is None or proc.poll() is not None:
        return False  # 이미 종료됨
    
    config = PROCESS_CONFIG.get(process_id)
    print(f"\n🛑 {config['name']} 중지 중... (PID: {proc.pid})")
    
    try:
        # 자식 프로세스도 함께 종료
        try:
            parent = psutil.Process(proc.pid)
            children = parent.children(recursive=True)
            
            # 자식 프로세스 먼저 종료
            for child in children:
                print(f"  - 자식 프로세스 종료: {child.pid}")
                child.terminate()
            
            # 부모 프로세스 종료
            parent.terminate()
            
            # 5초 대기
            psutil.wait_procs([parent] + children, timeout=5)
            
        except psutil.NoSuchProcess:
            pass
        
        # 강제 종료
        if proc.poll() is None:
            proc.kill()
        
        running_processes.pop(process_id, None)
        print(f"✅ {config['name']} 종료 완료\n")
        
        return True
        
    except Exception as e:
        print(f"❌ 종료 실패: {e}\n")
        return False


def restart_process(process_id: str) -> bool:
    """프로세스 재시작"""
    print(f"\n🔄 재시작 중...")
    stop_process(process_id)
    import time
    time.sleep(1)
    return start_process(process_id)


# ============================================================
# API 엔드포인트
# ============================================================

@app.get("/")
async def root():
    """메인 페이지"""
    html_content = Path("process_manager.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html_content)


@app.get("/api/processes")
async def get_all_processes() -> List[ProcessStatus]:
    """모든 프로세스 상태 조회"""
    statuses = []
    
    for process_id, config in PROCESS_CONFIG.items():
        proc = running_processes.get(process_id)
        status = get_process_info(process_id, proc)
        statuses.append(status)
    
    return statuses


@app.get("/api/processes/{process_id}")
async def get_process_status(process_id: str) -> ProcessStatus:
    """특정 프로세스 상태 조회"""
    if process_id not in PROCESS_CONFIG:
        raise HTTPException(status_code=404, detail="Process not found")
    
    proc = running_processes.get(process_id)
    return get_process_info(process_id, proc)


@app.get("/api/processes/{process_id}/logs")
async def get_process_logs(process_id: str):
    """프로세스 로그 조회"""
    if process_id not in PROCESS_CONFIG:
        raise HTTPException(status_code=404, detail="Process not found")
    
    stdout_logs = process_logs.get(process_id, [])
    stderr_logs = process_errors.get(process_id, [])
    
    return {
        "process_id": process_id,
        "stdout": stdout_logs,
        "stderr": stderr_logs
    }


class StartProcessRequest(BaseModel):
    symbols: Optional[str] = None


@app.post("/api/processes/{process_id}/start")
async def start_process_endpoint(process_id: str, request: StartProcessRequest = None):
    """프로세스 시작"""
    if process_id not in PROCESS_CONFIG:
        raise HTTPException(status_code=404, detail="Process not found")
    
    symbols = request.symbols if request else None
    success = start_process(process_id, symbols=symbols)
    
    if success:
        msg = f"{PROCESS_CONFIG[process_id]['name']} 시작됨"
        if symbols:
            msg += f" (심볼: {symbols})"
        return {"status": "success", "message": msg}
    else:
        # 에러 로그 반환
        errors = process_errors.get(process_id, [])
        error_msg = errors[-1] if errors else "시작 실패"
        raise HTTPException(status_code=400, detail=error_msg)



@app.post("/api/processes/{process_id}/stop")
async def stop_process_endpoint(process_id: str):
    """프로세스 중지"""
    if process_id not in PROCESS_CONFIG:
        raise HTTPException(status_code=404, detail="Process not found")
    
    success = stop_process(process_id)
    
    if success:
        return {"status": "success", "message": f"{PROCESS_CONFIG[process_id]['name']} 중지됨"}
    else:
        raise HTTPException(status_code=400, detail="이미 중지됨")


@app.post("/api/processes/{process_id}/restart")
async def restart_process_endpoint(process_id: str):
    """프로세스 재시작"""
    if process_id not in PROCESS_CONFIG:
        raise HTTPException(status_code=404, detail="Process not found")
    
    success = restart_process(process_id)
    
    if success:
        return {"status": "success", "message": f"{PROCESS_CONFIG[process_id]['name']} 재시작됨"}
    else:
        raise HTTPException(status_code=400, detail="재시작 실패")


@app.get("/api/system")
async def get_system_info():
    """시스템 정보"""
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    # DB 연결 수 조회
    db_connections = get_db_connections()
    
    # GPU 정보 조회
    gpu_info = get_gpu_info()
    
    result = {
        "cpu_percent": cpu_percent,
        "memory_percent": memory.percent,
        "memory_used_gb": round(memory.used / 1024 / 1024 / 1024, 1),
        "memory_total_gb": round(memory.total / 1024 / 1024 / 1024, 1),
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / 1024 / 1024 / 1024, 1),
        "disk_total_gb": round(disk.total / 1024 / 1024 / 1024, 1),
        "db_connections": db_connections.get("total", 0),
        "db_connections_active": db_connections.get("active", 0),
        "db_connections_max": db_connections.get("max", 0)
    }
    
    # GPU 정보 추가 (있으면)
    if gpu_info:
        result.update(gpu_info)
    
    return result


def get_gpu_info():
    """GPU 정보 조회 (NVIDIA GPU)"""
    try:
        import subprocess
        
        # nvidia-smi 실행
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu', '--format=csv,noheader,nounits'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode != 0:
            return None
        
        # 첫 번째 GPU 정보 파싱
        lines = result.stdout.strip().split('\n')
        if not lines or not lines[0]:
            return None
        
        # CSV 파싱: index, name, utilization, memory_used, memory_total, temperature
        parts = [p.strip() for p in lines[0].split(',')]
        if len(parts) < 6:
            return None
        
        gpu_index = parts[0]
        gpu_name = parts[1]
        gpu_util = float(parts[2])
        gpu_mem_used = float(parts[3])
        gpu_mem_total = float(parts[4])
        gpu_temp = float(parts[5])
        
        gpu_mem_percent = (gpu_mem_used / gpu_mem_total * 100) if gpu_mem_total > 0 else 0
        
        return {
            "gpu_available": True,
            "gpu_index": gpu_index,
            "gpu_name": gpu_name,
            "gpu_utilization": round(gpu_util, 1),
            "gpu_memory_used_mb": round(gpu_mem_used, 0),
            "gpu_memory_total_mb": round(gpu_mem_total, 0),
            "gpu_memory_percent": round(gpu_mem_percent, 1),
            "gpu_temperature": round(gpu_temp, 0)
        }
        
    except FileNotFoundError:
        # nvidia-smi 없음 (GPU 없거나 드라이버 미설치)
        return {"gpu_available": False}
    except Exception as e:
        # 기타 에러
        return {"gpu_available": False, "gpu_error": str(e)}


def get_db_connections():
    """데이터베이스 연결 수 조회"""
    engine = None
    try:
        from sqlalchemy import create_engine, text
        
        # config.yaml 로드 (multi-encoding support)
        config = load_config("config.yaml")
        
        db = config['db']
        dsn = f"postgresql://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['name']}"
        
        # pool_size=1로 최소화, max_overflow=0으로 추가 연결 방지
        engine = create_engine(
            dsn, 
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
            pool_recycle=3600
        )
        
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT 
                    COUNT(*) as total_connections,
                    COUNT(*) FILTER (WHERE state = 'active') as active_connections,
                    COUNT(*) FILTER (WHERE state = 'idle') as idle_connections,
                    (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') as max_connections
                FROM pg_stat_activity
                WHERE datname = :dbname
            """), {"dbname": db['name']})
            
            row = result.fetchone()
            
            result_data = {
                "total": row[0] if row else 0,
                "active": row[1] if row else 0,
                "idle": row[2] if row else 0,
                "max": row[3] if row else 0
            }
            
        return result_data
        
    except Exception as e:
        print(f"[DB] Connection count error: {e}")
        return {"total": 0, "active": 0, "idle": 0, "max": 0}
    
    finally:
        # CRITICAL: engine을 명시적으로 dispose하여 연결 풀 해제
        if engine is not None:
            engine.dispose()
            engine = None


# ============================================================
# 시작/종료 이벤트
# ============================================================

@app.on_event("shutdown")
async def shutdown_event():
    """서버 종료 시 모든 프로세스 정리"""
    print("\n🛑 프로세스 매니저 종료 중...")
    
    for process_id in list(running_processes.keys()):
        print(f"  - {PROCESS_CONFIG[process_id]['name']} 종료 중...")
        stop_process(process_id)
    
    print("✅ 모든 프로세스 종료 완료")


# ============================================================
# 실행
# ============================================================

if __name__ == "__main__":
    import uvicorn
    
    # 명령줄 인자 재파싱 (포트 옵션 추가)
    parser.add_argument('--port', type=int, default=8000, help='포트 (기본: 8000)')
    parser.add_argument('--host', default='0.0.0.0', help='호스트 (기본: 0.0.0.0)')
    args = parser.parse_args()
    
    # uvicorn 로그 레벨 설정
    if args.quiet:
        uvicorn_log_level = "error"
        log_status = "❌ HTTP 로그 완전히 숨김"
    elif args.no_access_log:
        uvicorn_log_level = "info"
        log_status = "⚠️  HTTP 액세스 로그 숨김"
    else:
        uvicorn_log_level = "info"
        log_status = "✅ 모든 로그 표시"
    
    print("=" * 60)
    print("🚀 Process Manager v2.1")
    print("=" * 60)
    print(f"📊 Dashboard: http://localhost:{args.port}")
    print(f"📚 API Docs:  http://localhost:{args.port}/docs")
    print(f"🎛️  Processes: {len(PROCESS_CONFIG)} configured")
    print(f"📝 Logs: {log_status}")
    print("=" * 60)
    print("\n종료: Ctrl+C\n")
    
    # 도움말 메시지
    if not args.quiet and not args.no_access_log:
        print("💡 Tip: HTTP 로그 숨기려면 --no-access-log 또는 --quiet 옵션 사용")
        print("   예: python process_manager.py --no-access-log")
        print("   예: python process_manager.py --quiet\n")
    
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=uvicorn_log_level,
        access_log=not args.no_access_log and not args.quiet
    )

