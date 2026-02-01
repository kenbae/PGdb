"""
Flask API for Keltner ICT Turtle Backtester
"""

from flask import Flask, jsonify, request, Response, send_file
from flask_cors import CORS
import json
import logging
from datetime import datetime
from keltner_ict_turtle import KeltnerICTTurtle
import psycopg2
from psycopg2.extras import RealDictCursor
import yaml
import subprocess
import os

app = Flask(__name__)
CORS(app)

# 로깅
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config 로드
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)


def get_db_connection():
    """DB 연결"""
    db_config = config.get('db', {})
    return psycopg2.connect(
        host=db_config.get('host', 'localhost'),
        port=db_config.get('port', 5432),
        database=db_config.get('name', 'marketdb'),
        user=db_config.get('user', 'trader'),
        password=db_config.get('password', ''),
        client_encoding='utf8'
    )

def has_candles(symbol: str, tf: str, start_date: datetime, end_date: datetime) -> bool:
    """요청 구간에 캔들이 1개라도 있는지 체크"""
    conn = None
    try:
        conn = get_db_connection()
        q = (
            "SELECT 1 FROM candles \
            WHERE symbol = %s AND tf = %s \
              AND open_time >= %s AND open_time < %s \
            LIMIT 1"
        )
        with conn.cursor() as cur:
            cur.execute(q, (symbol, tf, start_date, end_date))
            return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"데이터 유무 체크 오류: {symbol} {tf}: {e}")
        return False
    finally:
        if conn:
            conn.close()



@app.route('/')
def index():
    """메인 페이지"""
    return send_file('backtest_web.html')


@app.route('/api/symbols')
def get_symbols():
    """심볼 목록 (거래량 순)"""
    try:
        conn = get_db_connection()
        
        # 최근 24시간 평균 거래량 기준 정렬
        query = """
            SELECT DISTINCT ON (symbol) 
                symbol, 
                AVG(volume) OVER (PARTITION BY symbol) as avg_volume
            FROM candles
            WHERE tf = '1h'
              AND open_time >= NOW() - INTERVAL '7 days'
            ORDER BY symbol, avg_volume DESC
        """
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            symbols = cur.fetchall()
        
        conn.close()
        
        # 거래량 순 정렬
        symbols_sorted = sorted(symbols, key=lambda x: x['avg_volume'] or 0, reverse=True)
        
        return jsonify({
            'symbols': [
                {
                    'symbol': s['symbol'],
                    'volume': float(s['avg_volume']) if s['avg_volume'] else 0
                }
                for s in symbols_sorted
            ]
        })
    
    except Exception as e:
        logger.error(f"심볼 조회 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/backtest', methods=['POST'])
def backtest():
    """백테스트 실행 (스트리밍)"""
    try:
        data = request.json
        symbols = data.get('symbols', [])
        timeframes = data.get('timeframes', ['1h'])
        start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d')
        end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d')
        initial_capital = data.get('initial_capital', 10000)
        
        logger.info(f"백테스트 시작: {len(symbols)}개 심볼, {len(timeframes)}개 TF")
        
        def generate():
            """결과 스트리밍"""
            backtester = KeltnerICTTurtle()
            total = len(symbols) * len(timeframes)
            current = 0
            
            for symbol in symbols:
                for tf in timeframes:
                    current += 1
                    
                    # 진행 상황 전송
                    yield f"data: {json.dumps({'type': 'progress', 'current': current, 'total': total})}\n\n"
                    
                    try:
                        # 데이터 유무 체크 (없으면 UI에 표시)
                        if not has_candles(symbol, tf, start_date, end_date):
                            yield f"data: {json.dumps({'type': 'missing', 'symbol': symbol, 'tf': tf, 'reason': 'no_candles_in_range'})}\n\n"
                            continue

                        # 백테스트 실행
                        result = backtester.backtest(
                            symbol=symbol,
                            tf=tf,
                            start_date=start_date,
                            end_date=end_date,
                            initial_capital=initial_capital
                        )

                        if result:
                            # 결과 전송
                            result_data = {
                                'symbol': result.symbol,
                                'tf': tf,
                                'start_date': result.start_date.strftime('%Y-%m-%d'),
                                'end_date': result.end_date.strftime('%Y-%m-%d'),
                                'initial_capital': result.initial_capital,
                                'final_capital': result.final_capital,
                                'total_pnl': result.total_pnl,
                                'total_pnl_pct': result.total_pnl_pct,
                                'total_trades': result.total_trades,
                                'winning_trades': result.winning_trades,
                                'losing_trades': result.losing_trades,
                                'win_rate': result.win_rate,
                                'max_drawdown': result.max_drawdown,
                                'max_drawdown_pct': result.max_drawdown_pct,
                                'sharpe_ratio': result.sharpe_ratio,
                                'profit_factor': result.profit_factor,
                                'avg_win': result.avg_win,
                                'avg_loss': result.avg_loss,
                                'max_win': result.max_win,
                                'max_loss': result.max_loss
                            }

                            yield f"data: {json.dumps({'type': 'result', 'result': result_data})}\n\n"

                    except Exception as e:
                        logger.error(f"{symbol} {tf} 백테스트 오류: {e}")
                        continue
            
            # 완료
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"
        
        return Response(generate(), mimetype='text/event-stream')
    
    except Exception as e:
        logger.error(f"백테스트 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/backfill', methods=['POST'])
def backfill():
    """선택한 심볼/TF 구간의 Binance Vision 데이터를 병렬(8)로 백필"""
    try:
        data = request.json or {}
        symbols = data.get('symbols', [])
        timeframes = data.get('timeframes', [])
        start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d')
        end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d')
        market_type = data.get('market_type', 'futures')

        if not symbols or not timeframes:
            return jsonify({'error': 'symbols/timeframes required'}), 400

        # 월 단위로 커버 (end_date 포함을 위해 월 계산)
        sy, sm = start_date.year, start_date.month
        ey, em = end_date.year, end_date.month

        script_path = os.path.join(os.path.dirname(__file__), 'backfill_binance_vision_parallel.py')
        cmd = [
            'python', script_path,
            '--start-year', str(sy), '--start-month', str(sm),
            '--end-year', str(ey), '--end-month', str(em),
            '--market-type', market_type,
            '--workers', '8',
            '--symbols', *symbols,
            '--tfs', *timeframes
        ]

        logger.info('백필 실행: %s', ' '.join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if proc.returncode != 0:
            logger.error('백필 실패: %s', proc.stderr)
            return jsonify({'ok': False, 'stdout': proc.stdout, 'stderr': proc.stderr}), 500
        return jsonify({'ok': True, 'stdout': proc.stdout})

    except Exception as e:
        logger.error(f"백필 API 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/download/<symbol>/<tf>')
def download_result(symbol, tf):
    """결과 다운로드 (JSON)"""
    # 구현 생략 (필요시 추가)
    pass


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
