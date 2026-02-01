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
import sys
import signal
from datetime import datetime
from pathlib import Path

# Windows 콘솔 UTF-8 인코딩 설정
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    os.environ['PYTHONIOENCODING'] = 'utf-8'
import yaml
import threading
import queue
import logging
import time
from urllib import request as urllib_request, parse as urllib_parse
import socket

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

# 로그 큐 (WebSocket으로 전송하기 위해)
log_queue = queue.Queue(maxsize=1000)

# 커스텀 로그 핸들러
class QueueHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        try:
            log_queue.put_nowait({
                'timestamp': datetime.now().isoformat(),
                'level': record.levelname,
                'message': log_entry
            })
        except queue.Full:
            # 큐가 꽉 차면 오래된 것 제거
            try:
                log_queue.get_nowait()
                log_queue.put_nowait({
                    'timestamp': datetime.now().isoformat(),
                    'level': record.levelname,
                    'message': log_entry
                })
            except:
                pass

# 큐 핸들러 추가
queue_handler = QueueHandler()
queue_handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))

# 루트 로거에 추가
root_logger = logging.getLogger()
root_logger.addHandler(queue_handler)
root_logger.setLevel(logging.INFO)

# uvicorn 로거에도 추가
uvicorn_logger = logging.getLogger("uvicorn")
uvicorn_logger.addHandler(queue_handler)

uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.addHandler(queue_handler)

uvicorn_error_logger = logging.getLogger("uvicorn.error")
uvicorn_error_logger.addHandler(queue_handler)

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
# Telegram 알림 (옵션)
# ============================================================
_TELEGRAM_CACHE = {"loaded_at": 0.0, "settings": None}


def _get_telegram_settings() -> Optional[Dict]:
    """
    process_manager 텔레그램 알림 설정을 읽습니다.
    - 우선순위: ENV > config.yaml
    - 비활성화: PROCESS_MANAGER_TELEGRAM_ENABLED=0/false/no
    """
    enabled_env = os.getenv("PROCESS_MANAGER_TELEGRAM_ENABLED", "").strip().lower()
    if enabled_env in {"0", "false", "no", "off"}:
        return None

    now = time.time()
    if _TELEGRAM_CACHE["settings"] is not None and (now - float(_TELEGRAM_CACHE["loaded_at"])) < 30:
        return _TELEGRAM_CACHE["settings"]

    token = os.getenv("PROCESS_MANAGER_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("PROCESS_MANAGER_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    parse_mode = os.getenv("PROCESS_MANAGER_TELEGRAM_PARSE_MODE")
    disable_preview_env = os.getenv("PROCESS_MANAGER_TELEGRAM_DISABLE_PREVIEW", "").strip().lower()

    disable_preview = True
    if disable_preview_env in {"0", "false", "no", "off"}:
        disable_preview = False

    if not token or not chat_id:
        try:
            cfg = load_config("config.yaml") or {}
            tg = (cfg.get("telegram") or {}) if isinstance(cfg, dict) else {}
            token = token or tg.get("bot_token")
            chat_id = chat_id or tg.get("chat_id")
            parse_mode = parse_mode or tg.get("parse_mode") or "HTML"
            if disable_preview_env == "":
                disable_preview = bool(tg.get("disable_web_page_preview", True))
        except Exception:
            # 설정 로드 실패 시 알림 비활성화
            _TELEGRAM_CACHE["loaded_at"] = now
            _TELEGRAM_CACHE["settings"] = None
            return None

    if not token or not chat_id:
        _TELEGRAM_CACHE["loaded_at"] = now
        _TELEGRAM_CACHE["settings"] = None
        return None

    settings = {
        "bot_token": str(token),
        "chat_id": str(chat_id),
        "parse_mode": str(parse_mode or "HTML"),
        "disable_web_page_preview": bool(disable_preview),
    }
    _TELEGRAM_CACHE["loaded_at"] = now
    _TELEGRAM_CACHE["settings"] = settings
    return settings


def _send_telegram_message(text: str) -> bool:
    """텔레그램 전송 (실패해도 예외를 올리지 않음)."""
    settings = _get_telegram_settings()
    if not settings:
        return False

    try:
        url = f"https://api.telegram.org/bot{settings['bot_token']}/sendMessage"
        payload = {
            "chat_id": settings["chat_id"],
            "text": text,
            "parse_mode": settings.get("parse_mode", "HTML"),
            "disable_web_page_preview": settings.get("disable_web_page_preview", True),
        }
        data = urllib_parse.urlencode(payload).encode("utf-8")
        req = urllib_request.Request(url, data=data, method="POST")
        with urllib_request.urlopen(req, timeout=6) as resp:
            resp.read()
        return True
    except Exception as e:
        # 전송 실패는 로컬 로그로만 남기고, API 응답에는 영향 주지 않음
        log_msg = f"⚠️ Telegram 전송 실패: {e}"
        try:
            log_queue.put_nowait({
                "timestamp": datetime.now().isoformat(),
                "level": "WARNING",
                "message": f"WARNING:root:{log_msg}",
            })
        except Exception:
            pass
        return False


def notify_telegram_async(text: str) -> None:
    """텔레그램 전송을 백그라운드로 실행."""
    if not _get_telegram_settings():
        return
    threading.Thread(target=_send_telegram_message, args=(text,), daemon=True).start()


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
        "command": ["python", "backfill_binance_vision.py"],
        "cwd": ".",
        "env": {},
        "icon": "📥",
        "description": "과거 데이터 수집 (run_ingest 먼저 실행)",
        "symbol_option": True,  # 심볼 옵션 지원
        "symbol_param": "--symbols",  # 심볼 파라미터
        "backfill_options": True,  # 백필 전용 옵션 (년도/월/TF 선택)
        "run_ingest_first": True   # run_ingest 먼저 실행
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
    },
    "backtest": {
        "name": "백테스트 서버",
        "command": ["python", "backtest_api.py"],
        "cwd": ".",
        "env": {},
        "icon": "📈",
        "description": "백테스트 API 서버 (포트 5000)",
        "symbol_option": False,
        "port": 5000
    },
    "tradebot_all": {
        "name": "tradeBot (전체)",
        "command": ["python", "-u", "start_all.py", "all"],
        "cwd": "tradeBot",
        "env": {},
        "icon": "🧩",
        "description": "tradeBot 3서버 일괄 시작/중지 (order→analyzer→web)",
        "symbol_option": False
    },
    "tradebot_order": {
        "name": "tradeBot 주문서버",
        "command": ["python", "-u", "order_server.py"],
        "cwd": "tradeBot",
        "env": {},
        "icon": "💰",
        "description": "주문 실행 서버 (포트 8889)",
        "symbol_option": False,
        "port": 8889
    },
    "tradebot_analyzer": {
        "name": "tradeBot 분석서버",
        "command": ["python", "-u", "servers/analyzer_server.py"],
        "cwd": "tradeBot",
        "env": {},
        "icon": "📊",
        "description": "정시 분석/신호 저장/텔레그램 (포트 8887)",
        "symbol_option": False,
        "port": 8887
    },
    "tradebot_web": {
        "name": "tradeBot 웹서버",
        "command": ["python", "-u", "servers/web_server.py"],
        "cwd": "tradeBot",
        "env": {},
        "icon": "🌐",
        "description": "API + 대시보드 UI (포트 8888)",
        "symbol_option": False,
        "port": 8888
    },
    "data_dashboard": {
        "name": "데이터 현황",
        "command": ["python", "historical_data_api.py"],
        "cwd": ".",
        "env": {},
        "icon": "🗄️",
        "description": "DB 데이터 현황 대시보드 (포트 8002)",
        "symbol_option": False,
        "port": 8002
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
    "llm_report": False,
    "backtest": True,       # 백테스트 서버도 HTTP 로그 필터링
    "tradebot_all": True,
    "tradebot_order": True,
    "tradebot_analyzer": True,
    "tradebot_web": True,
    "data_dashboard": True  # 데이터 현황도 HTTP 로그 필터링
}

# tradeBot 그룹(전체) 제어 대상
TRADEBOT_GROUP = ("tradebot_order", "tradebot_analyzer", "tradebot_web")

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
    backfill_options: bool = False  # 백필 옵션 지원 여부 (년도/월/TF 선택)
    current_backfill_options: Optional[Dict] = None  # 현재 백필 설정
    port: Optional[int] = None  # 웹서비스 포트 (있는 경우)


# 백필 설정 저장
backfill_settings: Dict[str, Dict] = {}


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
            
            # UTF-8 디코딩 보장 (이미 text=True로 열렸지만 안전하게 처리)
            if isinstance(line, bytes):
                line = line.decode('utf-8', errors='replace')
            
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

            # WebSocket으로 전송하기 위해 log_queue에 추가
            try:
                log_level = 'ERROR' if stream_type == 'stderr' else 'INFO'
                log_queue.put_nowait({
                    'timestamp': datetime.now().isoformat(),
                    'level': log_level,
                    'message': f'{log_level}:{process_id}:{line}'
                })
            except queue.Full:
                pass

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

    # tradeBot 전체는 3개 서버 상태를 합산해서 표시 (start_all.py 대신 안정적)
    if process_id == "tradebot_all":
        running = {}
        for pid_key in TRADEBOT_GROUP:
            p = running_processes.get(pid_key)
            if p is not None and p.poll() is None:
                running[pid_key] = p

        if len(running) == 0:
            status = "stopped"
        elif len(running) == len(TRADEBOT_GROUP):
            status = "running"
        else:
            status = "error"  # 일부만 실행중

        # 합산 리소스(가능한 경우)
        total_cpu = 0.0
        total_mem = 0.0
        started_at = None
        uptime = None
        try:
            create_times = []
            for pid_key, p in running.items():
                ps = psutil.Process(p.pid)
                total_cpu += float(ps.cpu_percent(interval=0.05) or 0.0)
                total_mem += float(ps.memory_info().rss / 1024 / 1024)
                create_times.append(ps.create_time())
            if create_times:
                earliest = datetime.fromtimestamp(min(create_times))
                started_at = earliest.strftime("%Y-%m-%d %H:%M:%S")
                uptime_seconds = (datetime.now() - earliest).total_seconds()
                hours = int(uptime_seconds // 3600)
                minutes = int((uptime_seconds % 3600) // 60)
                seconds = int(uptime_seconds % 60)
                uptime = f"{hours}h {minutes}m {seconds}s"
        except Exception:
            pass

        # 상태 요약 문자열
        def _flag(pid_key: str) -> str:
            return "✅" if pid_key in running else "⛔"

        summary = " / ".join([
            f"{_flag('tradebot_order')} order",
            f"{_flag('tradebot_analyzer')} analyzer",
            f"{_flag('tradebot_web')} web",
        ])
        desc = f"{config['description']} ({summary})"

        return ProcessStatus(
            process_id=process_id,
            name=config["name"],
            icon=config["icon"],
            description=desc,
            status=status,
            cpu_percent=round(total_cpu, 1) if running else None,
            memory_mb=round(total_mem, 1) if running else None,
            started_at=started_at,
            uptime=uptime,
            symbol_option=config.get("symbol_option", False),
            backfill_options=config.get("backfill_options", False),
            port=config.get("port")
        )
    
    if proc is None or proc.poll() is not None:
        # 프로세스 종료됨
        return ProcessStatus(
            process_id=process_id,
            name=config["name"],
            icon=config["icon"],
            description=config["description"],
            status="stopped",
            symbol_option=config.get("symbol_option", False),
            backfill_options=config.get("backfill_options", False),
            port=config.get("port")
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
            current_symbols=process_symbols.get(process_id),
            backfill_options=config.get("backfill_options", False),
            current_backfill_options=backfill_settings.get(process_id),
            port=config.get("port")
        )

    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ProcessStatus(
            process_id=process_id,
            name=config["name"],
            icon=config["icon"],
            description=config["description"],
            status="error",
            symbol_option=config.get("symbol_option", False),
            backfill_options=config.get("backfill_options", False),
            port=config.get("port")
        )


def run_ingest_sync(timeframes: str = None) -> bool:
    """
    run_ingest.py 동기 실행 (백필 전에 최신 데이터 수집)

    Args:
        timeframes: 타임프레임 목록 (쉼표 구분, 예: "15m,1h,4h")

    Returns:
        bool: 성공 여부
    """
    try:
        log_msg = "📊 run_ingest 실행 중 (최신 데이터 수집)..."
        print(log_msg)
        log_queue.put_nowait({
            'timestamp': datetime.now().isoformat(),
            'level': 'INFO',
            'message': f'INFO:root:{log_msg}'
        })

        # config.yaml의 timeframes를 임시로 업데이트할 필요 없음
        # run_ingest.py는 config.yaml의 timeframes를 사용

        command = ["python", "run_ingest.py", "--workers", "4"]

        # 동기 실행 (완료까지 대기)
        result = subprocess.run(
            command,
            cwd=".",
            capture_output=True,
            text=True,
            timeout=600  # 10분 타임아웃
        )

        if result.returncode == 0:
            log_msg = "✅ run_ingest 완료"
            print(log_msg)
            log_queue.put_nowait({
                'timestamp': datetime.now().isoformat(),
                'level': 'INFO',
                'message': f'INFO:root:{log_msg}'
            })
            return True
        else:
            log_msg = f"❌ run_ingest 실패: {result.stderr[:200] if result.stderr else 'Unknown error'}"
            print(log_msg)
            log_queue.put_nowait({
                'timestamp': datetime.now().isoformat(),
                'level': 'ERROR',
                'message': f'ERROR:root:{log_msg}'
            })
            return False

    except subprocess.TimeoutExpired:
        log_msg = "⏱️ run_ingest 타임아웃 (10분 초과)"
        print(log_msg)
        log_queue.put_nowait({
            'timestamp': datetime.now().isoformat(),
            'level': 'WARNING',
            'message': f'WARNING:root:{log_msg}'
        })
        return False
    except Exception as e:
        log_msg = f"❌ run_ingest 오류: {e}"
        print(log_msg)
        log_queue.put_nowait({
            'timestamp': datetime.now().isoformat(),
            'level': 'ERROR',
            'message': f'ERROR:root:{log_msg}'
        })
        return False


def start_process(process_id: str, symbols: str = None, backfill_opts: Dict = None) -> bool:
    """프로세스 시작

    Args:
        process_id: 프로세스 ID
        symbols: 심볼 필터 (선택)
        backfill_opts: 백필 옵션 (선택)
            - start_year: 시작 년도
            - start_month: 시작 월
            - timeframes: 타임프레임 목록 (예: "15m,1h,4h")
            - run_ingest: run_ingest 먼저 실행 여부
    """
    if process_id in running_processes and running_processes[process_id].poll() is None:
        return False  # 이미 실행 중

    config = PROCESS_CONFIG.get(process_id)
    if not config:
        raise ValueError(f"Unknown process: {process_id}")

    try:
        # 로그 초기화
        process_logs[process_id] = []
        process_errors[process_id] = []

        # 백필 옵션이 있고 run_ingest_first가 True인 경우 먼저 run_ingest 실행
        if backfill_opts and config.get("run_ingest_first", False) and backfill_opts.get("run_ingest", True):
            run_ingest_success = run_ingest_sync(backfill_opts.get("timeframes"))
            if not run_ingest_success:
                log_msg = "⚠️ run_ingest 실행 실패, 백필 계속 진행"
                print(log_msg)
                log_queue.put_nowait({
                    'timestamp': datetime.now().isoformat(),
                    'level': 'WARNING',
                    'message': f'WARNING:root:{log_msg}'
                })

        # 명령어 구성 (심볼 옵션 포함)
        command = config["command"].copy()

        # 백필 옵션 추가
        if backfill_opts and config.get("backfill_options", False):
            if backfill_opts.get("start_year"):
                command.extend(["--start-year", str(backfill_opts["start_year"])])
            if backfill_opts.get("start_month"):
                command.extend(["--start-month", str(backfill_opts["start_month"])])
            if backfill_opts.get("timeframes"):
                command.extend(["--timeframes", backfill_opts["timeframes"]])

            # 백필 설정 저장
            backfill_settings[process_id] = backfill_opts

            year_str = str(backfill_opts.get('start_year')) if backfill_opts.get('start_year') else '전체'
            month_str = f"{backfill_opts.get('start_month'):02d}" if backfill_opts.get('start_month') else '전체'
            tf_str = backfill_opts.get('timeframes') if backfill_opts.get('timeframes') else 'all'
            log_msg = f"📅 백필 설정: {year_str}-{month_str}, TF={tf_str}"
            print(log_msg)
            log_queue.put_nowait({
                'timestamp': datetime.now().isoformat(),
                'level': 'INFO',
                'message': f'INFO:root:{log_msg}'
            })

        # 심볼 옵션 지원하는 프로세스면 심볼 추가
        if symbols and config.get("symbol_option", False):
            symbol_param = config.get("symbol_param", "--symbols")
            command.extend([symbol_param, symbols])
            process_symbols[process_id] = symbols
            log_msg = f"🎯 심볼 필터 활성화: {symbols}"
            print(log_msg)
            log_queue.put_nowait({
                'timestamp': datetime.now().isoformat(),
                'level': 'INFO',
                'message': f'INFO:root:{log_msg}'
            })
        
        log_msg = f"🚀 {config['name']} 시작 중..."
        print(log_msg)
        log_queue.put_nowait({
            'timestamp': datetime.now().isoformat(),
            'level': 'INFO',
            'message': f'INFO:root:{log_msg}'
        })
        
        log_msg = f"명령어: {' '.join(command)}"
        print(log_msg)
        log_queue.put_nowait({
            'timestamp': datetime.now().isoformat(),
            'level': 'INFO',
            'message': f'INFO:root:{log_msg}'
        })
        
        # 프로세스 시작
        proc = subprocess.Popen(
            command,
            cwd=config["cwd"],
            env={**os.environ, **config["env"], 'PYTHONIOENCODING': 'utf-8'},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',  # 인코딩 오류 시 대체 문자 사용
            bufsize=1  # 라인 버퍼링
        )
        
        running_processes[process_id] = proc
        
        log_msg = f"✅ 프로세스 시작됨: PID {proc.pid}"
        print(log_msg)
        log_queue.put_nowait({
            'timestamp': datetime.now().isoformat(),
            'level': 'INFO',
            'message': f'INFO:root:{log_msg}'
        })
        
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
    log_msg = f"🛑 {config['name']} 중지 중... (PID: {proc.pid})"
    print(log_msg)
    log_queue.put_nowait({
        'timestamp': datetime.now().isoformat(),
        'level': 'INFO',
        'message': f'INFO:root:{log_msg}'
    })
    
    try:
        # 자식 프로세스도 함께 종료
        try:
            parent = psutil.Process(proc.pid)
            children = parent.children(recursive=True)
            
            # 자식 프로세스 먼저 종료
            for child in children:
                log_msg = f"  - 자식 프로세스 종료: {child.pid}"
                print(log_msg)
                log_queue.put_nowait({
                    'timestamp': datetime.now().isoformat(),
                    'level': 'INFO',
                    'message': f'INFO:root:{log_msg}'
                })
            
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
        log_msg = f"✅ {config['name']} 종료 완료"
        print(log_msg)
        log_queue.put_nowait({
            'timestamp': datetime.now().isoformat(),
            'level': 'INFO',
            'message': f'INFO:root:{log_msg}'
        })
        
        return True
        
    except Exception as e:
        log_msg = f"❌ 종료 실패: {e}"
        print(log_msg)
        log_queue.put_nowait({
            'timestamp': datetime.now().isoformat(),
            'level': 'ERROR',
            'message': f'ERROR:root:{log_msg}'
        })
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
    # 백필 옵션
    start_year: Optional[int] = None
    start_month: Optional[int] = None
    timeframes: Optional[str] = None  # 콤마 구분, 예: "15m,1h,4h"
    run_ingest: Optional[bool] = True  # run_ingest 먼저 실행 여부


@app.post("/api/processes/{process_id}/start")
async def start_process_endpoint(process_id: str, request: StartProcessRequest = None):
    """프로세스 시작"""
    if process_id not in PROCESS_CONFIG:
        raise HTTPException(status_code=404, detail="Process not found")

    # tradeBot 전체: order→analyzer→web 순차 시작
    if process_id == "tradebot_all":
        started = {}
        errors = {}
        for pid_key in TRADEBOT_GROUP:
            p = running_processes.get(pid_key)
            if p is not None and p.poll() is None:
                started[pid_key] = True
                continue

            ok = start_process(pid_key)
            started[pid_key] = bool(ok)
            if not ok:
                err_list = process_errors.get(pid_key, [])
                errors[pid_key] = err_list[-1] if err_list else "시작 실패"
                break
            time.sleep(1.5)

        if all(started.get(k) for k in TRADEBOT_GROUP):
            msg = "tradeBot 전체 시작됨 (order→analyzer→web)"
            notify_telegram_async(f"✅ [PM] {msg}")
            return {"status": "success", "message": msg}

        # 실패 시, 이미 켜진 것들은 그대로 두고 에러 반환
        detail = " / ".join([f"{k}: {v}" for k, v in errors.items()]) if errors else "시작 실패"
        notify_telegram_async(f"❌ [PM] tradeBot 전체 시작 실패\n{detail}")
        raise HTTPException(status_code=400, detail=detail)

    symbols = request.symbols if request else None

    # 백필 옵션 추출
    backfill_opts = None
    if request and PROCESS_CONFIG[process_id].get("backfill_options", False):
        backfill_opts = {
            "start_year": request.start_year,
            "start_month": request.start_month,
            "timeframes": request.timeframes,
            "run_ingest": request.run_ingest if request.run_ingest is not None else True
        }

    success = start_process(process_id, symbols=symbols, backfill_opts=backfill_opts)

    if success:
        msg = f"{PROCESS_CONFIG[process_id]['name']} 시작됨"
        if symbols:
            msg += f" (심볼: {symbols})"
        if backfill_opts:
            if backfill_opts.get("start_year"):
                msg += f" ({backfill_opts['start_year']}년"
                if backfill_opts.get("start_month"):
                    msg += f" {backfill_opts['start_month']}월"
                msg += "부터)"
        notify_telegram_async(f"✅ [PM] {msg}")
        return {"status": "success", "message": msg}
    else:
        # 에러 로그 반환
        errors = process_errors.get(process_id, [])
        error_msg = errors[-1] if errors else "시작 실패"
        notify_telegram_async(f"❌ [PM] {PROCESS_CONFIG[process_id]['name']} 시작 실패\n{error_msg}")
        raise HTTPException(status_code=400, detail=error_msg)



@app.post("/api/processes/{process_id}/stop")
async def stop_process_endpoint(process_id: str):
    """프로세스 중지"""
    if process_id not in PROCESS_CONFIG:
        raise HTTPException(status_code=404, detail="Process not found")

    # tradeBot 전체: web→analyzer→order 역순 중지
    if process_id == "tradebot_all":
        stopped_any = False
        stop_errors = []
        for pid_key in reversed(TRADEBOT_GROUP):
            p = running_processes.get(pid_key)
            if p is None or p.poll() is not None:
                continue
            ok = stop_process(pid_key)
            stopped_any = stopped_any or bool(ok)
            if not ok:
                stop_errors.append(pid_key)
            time.sleep(0.8)

        if stop_errors:
            detail = f"일부 중지 실패: {', '.join(stop_errors)}"
            notify_telegram_async(f"⚠️ [PM] tradeBot 전체 중지: {detail}")
            raise HTTPException(status_code=400, detail=detail)

        if stopped_any:
            notify_telegram_async("🛑 [PM] tradeBot 전체 중지됨")
            return {"status": "success", "message": "tradeBot 전체 중지됨"}

        notify_telegram_async("⚠️ [PM] tradeBot 전체 중지 요청: 이미 중지됨")
        raise HTTPException(status_code=400, detail="이미 중지됨")
    
    success = stop_process(process_id)
    
    if success:
        notify_telegram_async(f"🛑 [PM] {PROCESS_CONFIG[process_id]['name']} 중지됨")
        return {"status": "success", "message": f"{PROCESS_CONFIG[process_id]['name']} 중지됨"}
    else:
        notify_telegram_async(f"⚠️ [PM] {PROCESS_CONFIG[process_id]['name']} 중지 요청: 이미 중지됨")
        raise HTTPException(status_code=400, detail="이미 중지됨")


@app.post("/api/processes/{process_id}/restart")
async def restart_process_endpoint(process_id: str):
    """프로세스 재시작"""
    if process_id not in PROCESS_CONFIG:
        raise HTTPException(status_code=404, detail="Process not found")

    # tradeBot 전체: 전체 재시작 (중지→시작)
    if process_id == "tradebot_all":
        # 가능한 것만 중지
        for pid_key in reversed(TRADEBOT_GROUP):
            p = running_processes.get(pid_key)
            if p is not None and p.poll() is None:
                stop_process(pid_key)
                time.sleep(0.8)

        # 재시작
        started = {}
        errors = {}
        for pid_key in TRADEBOT_GROUP:
            ok = start_process(pid_key)
            started[pid_key] = bool(ok)
            if not ok:
                err_list = process_errors.get(pid_key, [])
                errors[pid_key] = err_list[-1] if err_list else "재시작 실패"
                break
            time.sleep(1.5)

        if all(started.get(k) for k in TRADEBOT_GROUP):
            notify_telegram_async("🔄 [PM] tradeBot 전체 재시작됨")
            return {"status": "success", "message": "tradeBot 전체 재시작됨"}

        detail = " / ".join([f"{k}: {v}" for k, v in errors.items()]) if errors else "재시작 실패"
        notify_telegram_async(f"❌ [PM] tradeBot 전체 재시작 실패\n{detail}")
        raise HTTPException(status_code=400, detail=detail)
    
    success = restart_process(process_id)
    
    if success:
        notify_telegram_async(f"🔄 [PM] {PROCESS_CONFIG[process_id]['name']} 재시작됨")
        return {"status": "success", "message": f"{PROCESS_CONFIG[process_id]['name']} 재시작됨"}
    else:
        notify_telegram_async(f"❌ [PM] {PROCESS_CONFIG[process_id]['name']} 재시작 실패")
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


@app.get("/api/test-log")
async def test_log():
    """테스트 로그 생성"""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info("🎉 테스트 로그 - INFO")
    logger.warning("⚠️ 테스트 로그 - WARNING")
    logger.error("❌ 테스트 로그 - ERROR")
    logger.debug("🔍 테스트 로그 - DEBUG")
    
    print("✅ 로그 생성 완료 (콘솔 출력)")
    
    return {
        "message": "테스트 로그 4개 생성됨",
        "queue_size": log_queue.qsize()
    }


# WebSocket 추가
from fastapi import WebSocket, WebSocketDisconnect
import asyncio

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """실시간 로그 WebSocket"""
    await websocket.accept()
    print("로그 WebSocket 연결")
    
    # 환영 메시지 전송
    welcome_msg = {
        'timestamp': datetime.now().isoformat(),
        'level': 'INFO',
        'message': 'INFO:root:로그 스트림 연결됨'
    }
    await websocket.send_json(welcome_msg)
    
    try:
        while True:
            # 큐에서 로그 가져오기 (non-blocking)
            try:
                log_entry = log_queue.get_nowait()
                await websocket.send_json(log_entry)
            except queue.Empty:
                # 큐가 비어있으면 대기
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"로그 전송 실패: {e}")
                break
    except WebSocketDisconnect:
        print("로그 WebSocket 연결 해제")
    except asyncio.CancelledError:
        print("로그 WebSocket 연결 취소됨")
    except Exception as e:
        print(f"로그 WebSocket 오류: {e}")


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

@app.on_event("startup")
async def startup_event():
    """서버 시작 알림 (옵션: 텔레그램)"""
    try:
        hostname = socket.gethostname()
        pid = os.getpid()
        notify_telegram_async(f"🚀 [PM] 프로세스 매니저 서버 시작\nhost={hostname}\npid={pid}")
    except Exception:
        # 알림 실패는 무시
        pass


@app.on_event("shutdown")
async def shutdown_event():
    """서버 종료 시 모든 프로세스 정리"""
    print("\n🛑 프로세스 매니저 종료 중...")
    try:
        hostname = socket.gethostname()
        pid = os.getpid()
        running_names = []
        for pid_key in list(running_processes.keys()):
            cfg = PROCESS_CONFIG.get(pid_key)
            if cfg:
                running_names.append(cfg.get("name", pid_key))
        summary = ", ".join(running_names) if running_names else "없음"
        notify_telegram_async(f"🛑 [PM] 프로세스 매니저 서버 종료 시작\nhost={hostname}\npid={pid}\nrunning={summary}")
    except Exception:
        pass
    
    for process_id in list(running_processes.keys()):
        print(f"  - {PROCESS_CONFIG[process_id]['name']} 종료 중...")
        stop_process(process_id)
    
    print("✅ 모든 프로세스 종료 완료")
    try:
        notify_telegram_async("✅ [PM] 프로세스 매니저 서버 종료 완료")
    except Exception:
        pass


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

