"""
모든 서버 시작 스크립트

사용법:
    python start_all.py           # 모든 서버 시작
    python start_all.py web       # 웹 서버만
    python start_all.py analyzer  # 분석 서버만
    python start_all.py order     # 주문 서버만
"""

import os
import sys
import subprocess
import time
import signal
import argparse
from datetime import datetime

# 현재 디렉토리 설정
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 프로세스 목록
processes = {}


def log(msg):
    """로그 출력"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def start_web_server():
    """웹 서버 시작 (포트 8888)"""
    log("🌐 웹 서버 시작 중...")
    # 직접 파일 경로로 실행 (stdout 캡처 안 함 - 콘솔에 직접 출력)
    proc = subprocess.Popen(
        [sys.executable, "servers/web_server.py"],
        cwd=os.path.dirname(os.path.abspath(__file__))
    )
    processes['web'] = proc
    log(f"✅ 웹 서버 시작 (PID: {proc.pid}, 포트: 8888)")
    return proc


def start_analyzer_server():
    """분석 서버 시작"""
    log("📊 분석 서버 시작 중...")
    proc = subprocess.Popen(
        [sys.executable, "servers/analyzer_server.py"],
        cwd=os.path.dirname(os.path.abspath(__file__))
    )
    processes['analyzer'] = proc
    log(f"✅ 분석 서버 시작 (PID: {proc.pid})")
    return proc


def start_order_server():
    """주문 서버 시작 (포트 8889)"""
    log("💰 주문 서버 시작 중...")
    proc = subprocess.Popen(
        [sys.executable, "order_server.py"],
        cwd=os.path.dirname(os.path.abspath(__file__))
    )
    processes['order'] = proc
    log(f"✅ 주문 서버 시작 (PID: {proc.pid}, 포트: 8889)")
    return proc


def stop_all():
    """모든 서버 종료"""
    log("🛑 모든 서버 종료 중...")
    for name, proc in processes.items():
        if proc and proc.poll() is None:
            proc.terminate()
            log(f"  - {name} 서버 종료 (PID: {proc.pid})")
    log("👋 모든 서버 종료 완료")


def main():
    parser = argparse.ArgumentParser(description='서버 시작 스크립트')
    parser.add_argument('server', nargs='?', choices=['web', 'analyzer', 'order', 'all'],
                       default='all', help='시작할 서버 (기본: all)')
    args = parser.parse_args()

    # Ctrl+C 핸들러
    def signal_handler(sig, frame):
        log("\n⚠️ 종료 신호 수신")
        stop_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    log("=" * 50)
    log("🚀 자동매매 시스템 시작")
    log("=" * 50)

    try:
        if args.server == 'all':
            # 순서대로 시작: 주문 → 분석 → 웹
            start_order_server()
            time.sleep(2)
            start_analyzer_server()
            time.sleep(2)
            start_web_server()
        elif args.server == 'web':
            start_web_server()
        elif args.server == 'analyzer':
            start_analyzer_server()
        elif args.server == 'order':
            start_order_server()

        log("")
        log("=" * 50)
        log("✅ 서버 시작 완료")
        log("")
        log("📍 접속 주소:")
        if 'web' in processes:
            log("   대시보드: http://localhost:8888")
            log("   신호 페이지: http://localhost:8888/signals")
        if 'order' in processes:
            log("   주문 서버: http://localhost:8889")
        log("")
        log("🛑 종료: Ctrl+C")
        log("=" * 50)

        # 프로세스 모니터링
        while True:
            for name, proc in list(processes.items()):
                if proc and proc.poll() is not None:
                    log(f"⚠️ {name} 서버 종료됨 (종료코드: {proc.returncode})")
                    del processes[name]

            if not processes:
                log("모든 서버가 종료되었습니다")
                break

            time.sleep(5)

    except KeyboardInterrupt:
        pass
    finally:
        stop_all()


if __name__ == "__main__":
    main()
