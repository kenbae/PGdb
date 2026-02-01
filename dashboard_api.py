"""
FastAPI 기반 암호화폐 트레이딩 대시보드
http://localhost:8000 에서 실행
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from typing import Optional, List
import yaml
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

app = FastAPI(title="Crypto Trading Dashboard", version="1.0.0")

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
# API Endpoints
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    """메인 대시보드 페이지"""
    return """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crypto Trading Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { font-family: 'Inter', sans-serif; }
        .card { background: white; border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .metric { font-size: 2rem; font-weight: bold; }
        .metric-label { color: #6B7280; font-size: 0.875rem; }
    </style>
</head>
<body class="bg-gray-100">
    <div class="container mx-auto px-4 py-8">
        <!-- Header -->
        <div class="mb-8">
            <h1 class="text-4xl font-bold text-gray-800">📊 Crypto Trading Dashboard</h1>
            <p class="text-gray-600 mt-2">실시간 트레이딩 성과 분석</p>
        </div>

        <!-- 통계 카드 -->
        <div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
            <div class="card">
                <div class="metric-label">총 신호</div>
                <div class="metric text-blue-600" id="total-signals">-</div>
            </div>
            <div class="card">
                <div class="metric-label">승률</div>
                <div class="metric text-green-600" id="win-rate">-</div>
            </div>
            <div class="card">
                <div class="metric-label">평균 R-Multiple</div>
                <div class="metric text-purple-600" id="avg-r">-</div>
            </div>
            <div class="card">
                <div class="metric-label">총 수익</div>
                <div class="metric text-orange-600" id="total-profit">-</div>
            </div>
        </div>

        <!-- 차트 영역 -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
            <div class="card">
                <h2 class="text-xl font-semibold mb-4">결과 분포</h2>
                <canvas id="resultChart"></canvas>
            </div>
            <div class="card">
                <h2 class="text-xl font-semibold mb-4">일별 수익</h2>
                <canvas id="profitChart"></canvas>
            </div>
        </div>

        <!-- 최근 신호 테이블 -->
        <div class="card">
            <h2 class="text-xl font-semibold mb-4">최근 신호</h2>
            <div class="overflow-x-auto">
                <table class="min-w-full divide-y divide-gray-200" id="signals-table">
                    <thead class="bg-gray-50">
                        <tr>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">시간</th>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">심볼</th>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">방향</th>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">결과</th>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">R-Multiple</th>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">점수</th>
                        </tr>
                    </thead>
                    <tbody class="bg-white divide-y divide-gray-200" id="signals-body">
                        <tr><td colspan="6" class="px-6 py-4 text-center text-gray-500">로딩 중...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        // API 호출 함수들
        async function fetchStats() {
            const response = await fetch('/api/stats');
            const data = await response.json();
            
            document.getElementById('total-signals').textContent = data.total_signals || 0;
            document.getElementById('win-rate').textContent = data.win_rate ? data.win_rate.toFixed(1) + '%' : '-';
            document.getElementById('avg-r').textContent = data.avg_r ? data.avg_r.toFixed(2) + 'R' : '-';
            document.getElementById('total-profit').textContent = data.total_profit ? data.total_profit.toFixed(2) + 'R' : '-';
        }

        async function fetchResultDistribution() {
            const response = await fetch('/api/result-distribution');
            const data = await response.json();
            
            const ctx = document.getElementById('resultChart').getContext('2d');
            new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: data.labels,
                    datasets: [{
                        data: data.values,
                        backgroundColor: ['#10B981', '#3B82F6', '#EF4444', '#9CA3AF']
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: { position: 'bottom' }
                    }
                }
            });
        }

        async function fetchDailyProfit() {
            const response = await fetch('/api/daily-profit');
            const data = await response.json();
            
            const ctx = document.getElementById('profitChart').getContext('2d');
            new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.dates,
                    datasets: [{
                        label: '누적 R-Multiple',
                        data: data.cumulative_profit,
                        borderColor: '#8B5CF6',
                        backgroundColor: 'rgba(139, 92, 246, 0.1)',
                        fill: true,
                        tension: 0.4
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: { display: false }
                    },
                    scales: {
                        y: { beginAtZero: true }
                    }
                }
            });
        }

        async function fetchRecentSignals() {
            const response = await fetch('/api/recent-signals?limit=20');
            const data = await response.json();
            
            const tbody = document.getElementById('signals-body');
            tbody.innerHTML = '';
            
            data.forEach(signal => {
                const row = document.createElement('tr');
                
                const resultColors = {
                    'tp2': 'text-green-600 font-semibold',
                    'tp1': 'text-blue-600',
                    'sl': 'text-red-600',
                    'none': 'text-gray-500'
                };
                
                const resultEmojis = {
                    'tp2': '✓✓',
                    'tp1': '✓',
                    'sl': '✗',
                    'none': '○'
                };
                
                row.innerHTML = `
                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        ${new Date(signal.open_time).toLocaleString('ko-KR')}
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">${signal.symbol}</td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm">
                        <span class="px-2 py-1 rounded ${signal.direction === 'long' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}">
                            ${signal.direction.toUpperCase()}
                        </span>
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm ${resultColors[signal.result] || ''}">
                        ${resultEmojis[signal.result] || ''} ${signal.result ? signal.result.toUpperCase() : '-'}
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm ${signal.r_multiple > 0 ? 'text-green-600' : 'text-red-600'}">
                        ${signal.r_multiple ? signal.r_multiple.toFixed(2) + 'R' : '-'}
                    </td>
                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">${signal.score || '-'}</td>
                `;
                tbody.appendChild(row);
            });
        }

        // 페이지 로드 시 데이터 가져오기
        window.addEventListener('DOMContentLoaded', () => {
            fetchStats();
            fetchResultDistribution();
            fetchDailyProfit();
            fetchRecentSignals();
            
            // 1분마다 자동 새로고침
            setInterval(fetchStats, 60000);
            setInterval(fetchRecentSignals, 60000);
        });
    </script>
</body>
</html>
    """


@app.get("/api/stats")
async def get_stats():
    """전체 통계"""
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
    """결과 분포"""
    try:
        engine = get_engine()
        query = text("""
            SELECT result, COUNT(*) as count
            FROM outcomes
            WHERE exit_price IS NOT NULL
            GROUP BY result
            ORDER BY result
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
    """일별 수익"""
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
    """최근 신호 목록"""
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
    """심볼 목록"""
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
    """심볼별 통계"""
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


@app.get("/health")
async def health_check():
    """헬스 체크"""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "healthy", "timestamp": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database connection failed: {str(e)}")


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
