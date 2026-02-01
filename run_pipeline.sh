#!/bin/bash

# 파이프라인 실행 스크립트
# ingest -> indicators -> signals 순서로 실행

set -e  # 에러 발생 시 중단

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

# 로그 디렉토리 생성
mkdir -p "$LOG_DIR"

# 로그 파일 경로
LOG_FILE="$LOG_DIR/pipeline_${TIMESTAMP}.log"

echo "========================================" | tee -a "$LOG_FILE"
echo "파이프라인 시작: $(date)" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

# 1. Ingest (데이터 수집)
echo "" | tee -a "$LOG_FILE"
echo "[1/3] 데이터 수집 시작..." | tee -a "$LOG_FILE"
python3 "$SCRIPT_DIR/run_ingest.py" 2>&1 | tee -a "$LOG_FILE"
if [ $? -eq 0 ]; then
    echo "✓ 데이터 수집 완료" | tee -a "$LOG_FILE"
else
    echo "✗ 데이터 수집 실패" | tee -a "$LOG_FILE"
    exit 1
fi

# 2. Indicators (지표 계산)
echo "" | tee -a "$LOG_FILE"
echo "[2/3] 지표 계산 시작..." | tee -a "$LOG_FILE"
python3 "$SCRIPT_DIR/run_indicators.py" 2>&1 | tee -a "$LOG_FILE"
if [ $? -eq 0 ]; then
    echo "✓ 지표 계산 완료" | tee -a "$LOG_FILE"
else
    echo "✗ 지표 계산 실패" | tee -a "$LOG_FILE"
    exit 1
fi

# 3. Signals (신호 생성)
echo "" | tee -a "$LOG_FILE"
echo "[3/3] 신호 생성 시작..." | tee -a "$LOG_FILE"
python3 "$SCRIPT_DIR/run_signals.py" 2>&1 | tee -a "$LOG_FILE"
if [ $? -eq 0 ]; then
    echo "✓ 신호 생성 완료" | tee -a "$LOG_FILE"
else
    echo "✗ 신호 생성 실패" | tee -a "$LOG_FILE"
    exit 1
fi

echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "파이프라인 완료: $(date)" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

# 오래된 로그 파일 정리 (30일 이상)
find "$LOG_DIR" -name "pipeline_*.log" -mtime +30 -delete 2>/dev/null || true

exit 0
