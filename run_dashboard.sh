#!/bin/bash

# ============================================================
# Streamlit Dashboard 실행 스크립트 (Linux)
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"

# 로그 디렉토리 생성
mkdir -p "$LOG_DIR"

echo "Starting Streamlit Dashboard..."
echo "Access at: http://localhost:8000"
echo "Logs: $LOG_DIR/streamlit.log"
echo ""

# Streamlit 실행
cd "$SCRIPT_DIR"
streamlit run dashboard.py \
    --server.port 8000 \
    --server.address 0.0.0.0 \
    --server.headless true \
    >> "$LOG_DIR/streamlit.log" 2>&1 &

STREAMLIT_PID=$!
echo "Streamlit PID: $STREAMLIT_PID"
echo $STREAMLIT_PID > "$LOG_DIR/streamlit.pid"

# 프로세스 시작 확인
sleep 2
if ps -p $STREAMLIT_PID > /dev/null; then
    echo "✓ Dashboard started successfully!"
else
    echo "✗ Failed to start dashboard. Check logs:"
    tail "$LOG_DIR/streamlit.log"
    exit 1
fi
