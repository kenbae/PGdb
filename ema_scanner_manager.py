"""
EMA Scanner Process Manager API
웹에서 EMA 스캐너를 시작/중지할 수 있는 API
"""

import subprocess
import psutil
import os
import signal
from typing import Optional
from fastapi import HTTPException


class EmaScannerManager:
    """EMA 스캐너 프로세스 관리"""
    
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.pid_file = "/tmp/ema_scanner.pid"
    
    def get_status(self) -> dict:
        """스캐너 상태 확인"""
        pid = self._get_running_pid()
        
        if pid and self._is_process_running(pid):
            return {
                "running": True,
                "pid": pid,
                "status": "active"
            }
        else:
            return {
                "running": False,
                "pid": None,
                "status": "stopped"
            }
    
    def start_scanner(self, timeframe: str = "15m", top: int = 50, llm_alert: bool = False) -> dict:
        """스캐너 시작"""
        # 이미 실행 중인지 확인
        pid = self._get_running_pid()
        if pid and self._is_process_running(pid):
            return {
                "success": False,
                "message": "Scanner is already running",
                "pid": pid
            }
        
        # ema_scanner.py 파일 존재 확인
        if not os.path.exists("ema_scanner.py"):
            return {
                "success": False,
                "message": "ema_scanner.py not found in current directory",
                "pid": None,
                "detail": f"Current directory: {os.getcwd()}"
            }
        
        # 스캐너 실행
        try:
            cmd = ["python", "ema_scanner.py", 
                   "--timeframe", timeframe,
                   "--top", str(top),
                   "--scan-every-sec", "60"]
            
            if llm_alert:
                cmd.append("--llm-alert")
            
            print(f"[DEBUG] Starting scanner with command: {' '.join(cmd)}")
            
            # 백그라운드 실행
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True  # 독립 프로세스 그룹
            )
            
            # PID 저장
            with open(self.pid_file, 'w') as f:
                f.write(str(self.process.pid))
            
            print(f"[DEBUG] Scanner started with PID: {self.process.pid}")
            
            return {
                "success": True,
                "message": "Scanner started successfully",
                "pid": self.process.pid,
                "config": {
                    "timeframe": timeframe,
                    "top": top,
                    "llm_alert": llm_alert
                }
            }
        
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to start scanner: {str(e)}",
                "pid": None
            }
    
    def stop_scanner(self) -> dict:
        """스캐너 중지"""
        pid = self._get_running_pid()
        
        if not pid:
            return {
                "success": False,
                "message": "Scanner is not running",
                "pid": None
            }
        
        try:
            # 프로세스 종료
            if self._is_process_running(pid):
                os.kill(pid, signal.SIGTERM)
                
                # PID 파일 삭제
                if os.path.exists(self.pid_file):
                    os.remove(self.pid_file)
                
                return {
                    "success": True,
                    "message": "Scanner stopped successfully",
                    "pid": pid
                }
            else:
                # 프로세스가 이미 죽었으면 PID 파일만 삭제
                if os.path.exists(self.pid_file):
                    os.remove(self.pid_file)
                
                return {
                    "success": False,
                    "message": "Scanner process not found",
                    "pid": pid
                }
        
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to stop scanner: {str(e)}",
                "pid": pid
            }
    
    def restart_scanner(self, timeframe: str = "15m", top: int = 50, llm_alert: bool = False) -> dict:
        """스캐너 재시작"""
        # 먼저 중지
        stop_result = self.stop_scanner()
        
        # 잠시 대기
        import time
        time.sleep(1)
        
        # 다시 시작
        start_result = self.start_scanner(timeframe, top, llm_alert)
        
        return {
            "success": start_result["success"],
            "message": f"Stopped: {stop_result['message']}, Started: {start_result['message']}",
            "pid": start_result.get("pid")
        }
    
    def _get_running_pid(self) -> Optional[int]:
        """PID 파일에서 PID 읽기"""
        if not os.path.exists(self.pid_file):
            return None
        
        try:
            with open(self.pid_file, 'r') as f:
                pid = int(f.read().strip())
            return pid
        except Exception:
            return None
    
    def _is_process_running(self, pid: int) -> bool:
        """프로세스가 실행 중인지 확인"""
        try:
            process = psutil.Process(pid)
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False


# 전역 매니저 인스턴스
ema_scanner_manager = EmaScannerManager()


# ============================================================
# FastAPI 엔드포인트 (dashboard_api_modular.py에 추가)
# ============================================================
"""
from ema_scanner_manager import ema_scanner_manager

@app.get("/api/ema-scanner/status")
async def get_ema_scanner_status():
    '''EMA 스캐너 상태 조회'''
    return ema_scanner_manager.get_status()

@app.post("/api/ema-scanner/start")
async def start_ema_scanner(
    timeframe: str = "15m",
    top: int = 50,
    llm_alert: bool = False
):
    '''EMA 스캐너 시작'''
    result = ema_scanner_manager.start_scanner(timeframe, top, llm_alert)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result

@app.post("/api/ema-scanner/stop")
async def stop_ema_scanner():
    '''EMA 스캐너 중지'''
    result = ema_scanner_manager.stop_scanner()
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result

@app.post("/api/ema-scanner/restart")
async def restart_ema_scanner(
    timeframe: str = "15m",
    top: int = 50,
    llm_alert: bool = False
):
    '''EMA 스캐너 재시작'''
    result = ema_scanner_manager.restart_scanner(timeframe, top, llm_alert)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result
"""
