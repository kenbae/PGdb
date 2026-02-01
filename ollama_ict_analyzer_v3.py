"""
OLLAMA ICT Analysis System v3.1
- 차트 렌더링 버그 수정
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import yaml
import requests
import json
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import os
import logging
import sys
import traceback

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(f'ict_analysis_{datetime.now().strftime("%Y%m%d")}.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class OLLAMAICTAnalyzer:
    def __init__(self, config_path="config.yaml"):
        logger.info("=" * 70)
        logger.info("OLLAMA ICT Analyzer v3.1 초기화")
        logger.info("=" * 70)
        
        self.config = self.load_config(config_path)
        self.db_conn = None
        self.ollama_url = "http://localhost:11434/api/generate"
        self.model = "qwen2.5:7b-instruct"
        # self.model = "gemma3:4b"
        # self.model = "llama3.2:latest"
        
        logger.info(f"사용 모델: {self.model}")
        self.test_ollama_connection()
        
    def load_config(self, config_path):
        """설정 파일 로드"""
        encodings = ['utf-8', 'utf-8-sig', 'cp949', 'euc-kr']
        for encoding in encodings:
            try:
                with open(config_path, 'r', encoding=encoding) as f:
                    return yaml.safe_load(f)
            except (UnicodeDecodeError, UnicodeError):
                continue
        raise ValueError("config.yaml을 읽을 수 없습니다.")
    
    def test_ollama_connection(self):
        """OLLAMA 연결 테스트"""
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=5)
            response.raise_for_status()
            models = response.json().get('models', [])
            logger.info(f"✅ OLLAMA 연결 성공 (모델: {len(models)}개)")
            model_names = [m['name'] for m in models]
            if self.model not in model_names and models:
                self.model = models[0]['name']
            logger.info(f"✅ 사용 모델: {self.model}")
        except Exception as e:
            logger.error(f"❌ OLLAMA 연결 실패: {e}")
            raise
    
    def get_db_connection(self):
        """DB 연결"""
        if not self.db_conn or self.db_conn.closed:
            db_config = self.config.get('db', {})
            self.db_conn = psycopg2.connect(
                host=db_config.get('host', 'localhost'),
                port=db_config.get('port', 5432),
                database=db_config.get('name', 'marketdb'),
                user=db_config.get('user', 'trader'),
                password=db_config.get('password', ''),
                client_encoding='utf8'
            )
            logger.info("✅ DB 연결 성공")
        return self.db_conn
    
    def get_available_symbols(self):
        """사용 가능한 심볼 목록"""
        conn = self.get_db_connection()
        query = """
            SELECT symbol, COUNT(*) as total, COUNT(DISTINCT tf) as tfs,
                   MIN(open_time) as earliest, MAX(open_time) as latest
            FROM candles GROUP BY symbol ORDER BY total DESC
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            return cur.fetchall()
    
    def select_symbol_interactive(self):
        """인터랙티브 심볼 선택"""
        print("\n" + "=" * 70)
        print("심볼 선택")
        print("=" * 70)
        symbols = self.get_available_symbols()
        
        print("\n📊 사용 가능한 심볼:")
        for i, s in enumerate(symbols, 1):
            print(f"{i:2d}. {s['symbol']:15s} | {s['total']:>10,} 캔들")
        
        while True:
            choice = input(f"\n심볼 선택 (1-{len(symbols)}): ").strip()
            if choice.isdigit() and 0 < int(choice) <= len(symbols):
                symbol = symbols[int(choice)-1]['symbol']
                break
        
        print("\n⏱️ 타임프레임: 1.1h  2.4h  3.1d")
        tf_map = {'1': '1h', '2': '4h', '3': '1d'}
        tf = tf_map.get(input("선택 (1-3): ").strip(), '1h')
        
        print("\n📅 기간: 1.30일  2.60일  3.90일")
        days_map = {'1': 30, '2': 60, '3': 90}
        days = days_map.get(input("선택 (1-3): ").strip(), 60)
        
        return symbol, tf, days
    
    def fetch_historical_data(self, symbol, tf, days):
        """과거 데이터 로드"""
        logger.info(f"데이터 로드: {symbol} {tf} ({days}일)")
        conn = self.get_db_connection()
        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)
        
        query = """
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE symbol = %s AND tf = %s 
              AND open_time >= %s AND open_time <= %s
            ORDER BY open_time ASC
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (symbol, tf, start_time, end_time))
            data = cur.fetchall()
        
        df = pd.DataFrame(data)
        logger.info(f"✅ {len(df):,}개 캔들 로드")
        return df
    
    def calculate_indicators(self, df):
        """기술적 지표 계산"""
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        df['rsi'] = 100 - (100 / (1 + gain / loss))
        
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = true_range.rolling(window=14).mean()
        
        return df
    
    def identify_structure(self, df):
        """ICT 구조 식별"""
        swing_period = 5
        
        df['swing_high'] = df['high'].rolling(window=swing_period*2+1, center=True).apply(
            lambda x: x[swing_period] == max(x), raw=True
        )
        df['swing_low'] = df['low'].rolling(window=swing_period*2+1, center=True).apply(
            lambda x: x[swing_period] == min(x), raw=True
        )
        
        df['bullish_ob'] = ((df['close'] > df['open']) & 
                            (df['close'].shift(1) < df['open'].shift(1)) &
                            (df['close'].shift(-1) > df['close']))
        df['bearish_ob'] = ((df['close'] < df['open']) & 
                            (df['close'].shift(1) > df['open'].shift(1)) &
                            (df['close'].shift(-1) < df['close']))
        
        df['bullish_fvg'] = (df['low'].shift(-1) > df['high'].shift(1))
        df['bearish_fvg'] = (df['high'].shift(-1) < df['low'].shift(1))
        
        return df
    
    def calculate_fibonacci(self, df):
        """피보나치 레벨"""
        recent = df.tail(50)
        high = recent['high'].max()
        low = recent['low'].min()
        range_val = high - low
        
        return {
            '0.0': low,
            '23.6': low + range_val * 0.236,
            '38.2': low + range_val * 0.382,
            '50.0': low + range_val * 0.5,
            '61.8': low + range_val * 0.618,
            '78.6': low + range_val * 0.786,
            '100.0': high
        }
    
    def prepare_prompt(self, df, symbol, tf):
        """프롬프트 생성"""
        current = df['close'].iloc[-1]
        prev = df['close'].iloc[-2]
        change = ((current - prev) / prev) * 100
        
        return f"""너는 ICT 전문가야. {symbol} {tf}을 분석해줘:

Current: ${current:.2f} ({change:+.2f}%)
EMA 20: ${df['ema_20'].iloc[-1]:.2f}
EMA 50: ${df['ema_50'].iloc[-1]:.2f}
RSI: {df['rsi'].iloc[-1]:.2f}

한국어로 다음의 분석을 최대한 상세하게 해줘. 그리고 과거 데이터를 토대로 가장 수익률이 높은 타점을 알려줘:
1. Market Structure
2. Order Blocks
3. Fair Value Gaps
4. Liquidity (BSL/SSL)
5. OTE Levels
6. Trading Setup
"""
    
    def query_ollama(self, prompt):
        """OLLAMA 쿼리"""
        logger.info(f"OLLAMA 분석 시작...")
        try:
            start = datetime.now()
            response = requests.post(
                self.ollama_url,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.7, "num_predict": 3000}
                },
                timeout=300
            )
            response.raise_for_status()
            elapsed = (datetime.now() - start).total_seconds()
            analysis = response.json().get('response', '')
            logger.info(f"✅ 분석 완료 ({elapsed:.2f}초)")
            return analysis
        except Exception as e:
            logger.error(f"❌ OLLAMA 실패: {e}")
            return "분석 실패"
    
    def generate_html(self, symbol, tf, df, analysis, fib_levels, output_path):
        """HTML 생성"""
        logger.info(f"HTML 생성: {output_path}")
        
        current = df['close'].iloc[-1]
        prev = df['close'].iloc[-2]
        change = ((current - prev) / prev) * 100
        
        # 차트 데이터
        chart_data = df.tail(100).copy()
        
        # 데이터를 JSON으로 변환
        dates = chart_data['open_time'].dt.strftime('%Y-%m-%d %H:%M').tolist()
        opens = chart_data['open'].tolist()
        highs = chart_data['high'].tolist()
        lows = chart_data['low'].tolist()
        closes = chart_data['close'].tolist()
        ema20 = chart_data['ema_20'].fillna(0).tolist()
        ema50 = chart_data['ema_50'].fillna(0).tolist()
        
        # Order Blocks
        bullish_obs = chart_data[chart_data['bullish_ob'] == True]
        bearish_obs = chart_data[chart_data['bearish_ob'] == True]
        
        # OB shapes 생성
        ob_shapes = []
        for _, row in bullish_obs.iterrows():
            ob_shapes.append({
                'type': 'rect',
                'x0': row['open_time'].strftime('%Y-%m-%d %H:%M'),
                'x1': (row['open_time'] + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M'),
                'y0': float(row['low']),
                'y1': float(row['high']),
                'fillcolor': 'rgba(16, 185, 129, 0.2)',
                'line': {'color': 'rgba(16, 185, 129, 0.8)', 'width': 1},
                'layer': 'below'
            })
        
        for _, row in bearish_obs.iterrows():
            ob_shapes.append({
                'type': 'rect',
                'x0': row['open_time'].strftime('%Y-%m-%d %H:%M'),
                'x1': (row['open_time'] + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M'),
                'y0': float(row['close']),
                'y1': float(row['open']),
                'fillcolor': 'rgba(239, 68, 68, 0.2)',
                'line': {'color': 'rgba(239, 68, 68, 0.8)', 'width': 1},
                'layer': 'below'
            })
        
        # Fib shapes
        fib_shapes = []
        x0 = dates[0]
        x1 = dates[-1]
        
        # OTE Zone
        fib_shapes.append({
            'type': 'rect',
            'x0': x0,
            'x1': x1,
            'y0': fib_levels['61.8'],
            'y1': fib_levels['78.6'],
            'fillcolor': 'rgba(245, 158, 11, 0.1)',
            'line': {'color': 'rgba(245, 158, 11, 0.5)', 'width': 1},
            'layer': 'below'
        })
        
        # Fib lines
        for level, price in fib_levels.items():
            fib_shapes.append({
                'type': 'line',
                'x0': x0,
                'x1': x1,
                'y0': price,
                'y1': price,
                'line': {'color': 'rgba(156, 163, 175, 0.5)', 'width': 1, 'dash': 'dash'},
                'layer': 'below'
            })
        
        all_shapes = ob_shapes + fib_shapes
        
        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ICT Analysis - {symbol}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
</head>
<body class="bg-gray-100">
    <div class="container mx-auto p-4 max-w-7xl">
        <!-- Header -->
        <div class="bg-white rounded-lg shadow p-6 mb-6">
            <h1 class="text-4xl font-bold mb-2">🎯 ICT Analysis Report</h1>
            <div class="text-gray-600">{symbol} | {tf} | {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
        </div>
        
        <!-- Stats -->
        <div class="grid grid-cols-4 gap-4 mb-6">
            <div class="bg-white rounded shadow p-4">
                <div class="text-sm text-gray-600">Current Price</div>
                <div class="text-3xl font-bold">${current:.2f}</div>
            </div>
            <div class="bg-white rounded shadow p-4">
                <div class="text-sm text-gray-600">Change</div>
                <div class="text-3xl font-bold {'text-green-600' if change > 0 else 'text-red-600'}">{change:+.2f}%</div>
            </div>
            <div class="bg-white rounded shadow p-4">
                <div class="text-sm text-gray-600">RSI</div>
                <div class="text-3xl font-bold">{df['rsi'].iloc[-1]:.2f}</div>
            </div>
            <div class="bg-white rounded shadow p-4">
                <div class="text-sm text-gray-600">ATR</div>
                <div class="text-3xl font-bold">${df['atr'].iloc[-1]:.2f}</div>
            </div>
        </div>
        
        <!-- Legend -->
        <div class="bg-white rounded shadow p-4 mb-6">
            <h3 class="font-bold mb-2">📊 Chart Legend</h3>
            <div class="flex flex-wrap gap-4">
                <div class="flex items-center">
                    <div class="w-8 h-4 mr-2" style="background: rgba(16, 185, 129, 0.3);"></div>
                    <span>Bullish OB</span>
                </div>
                <div class="flex items-center">
                    <div class="w-8 h-4 mr-2" style="background: rgba(239, 68, 68, 0.3);"></div>
                    <span>Bearish OB</span>
                </div>
                <div class="flex items-center">
                    <div class="w-8 h-4 mr-2" style="background: rgba(245, 158, 11, 0.2);"></div>
                    <span>OTE Zone</span>
                </div>
            </div>
        </div>
        
        <!-- Chart -->
        <div class="bg-white rounded shadow p-6 mb-6">
            <h2 class="text-2xl font-bold mb-4">📈 Price Chart with ICT Structure</h2>
            <div id="chart" style="width:100%; height:700px;"></div>
        </div>
        
        <!-- ICT Stats -->
        <div class="grid grid-cols-2 gap-6 mb-6">
            <div class="bg-white rounded shadow p-4">
                <h3 class="font-bold text-lg mb-2">🟢 Bullish Structures</h3>
                <div>Order Blocks: <span class="font-bold">{len(bullish_obs)}</span></div>
            </div>
            <div class="bg-white rounded shadow p-4">
                <h3 class="font-bold text-lg mb-2">🔴 Bearish Structures</h3>
                <div>Order Blocks: <span class="font-bold">{len(bearish_obs)}</span></div>
            </div>
        </div>
        
        <!-- Fib Levels -->
        <div class="bg-white rounded shadow p-4 mb-6">
            <h3 class="font-bold text-lg mb-2">📐 Fibonacci Levels</h3>
            <div class="grid grid-cols-4 gap-4">
                {' '.join([f'<div><span class="text-gray-600">{k}%:</span> <span class="font-bold">${v:.2f}</span></div>' for k, v in fib_levels.items()])}
            </div>
        </div>
        
        <!-- Analysis -->
        <div class="bg-white rounded shadow p-6">
            <h2 class="text-2xl font-bold mb-4">🤖 OLLAMA Analysis</h2>
            <pre class="whitespace-pre-wrap bg-gray-900 text-gray-100 p-4 rounded">{analysis}</pre>
        </div>
    </div>
    
    <script>
        // 차트 데이터
        const dates = {json.dumps(dates)};
        const opens = {json.dumps(opens)};
        const highs = {json.dumps(highs)};
        const lows = {json.dumps(lows)};
        const closes = {json.dumps(closes)};
        const ema20 = {json.dumps(ema20)};
        const ema50 = {json.dumps(ema50)};
        const shapes = {json.dumps(all_shapes)};
        
        // Candlestick
        const candlestick = {{
            x: dates,
            open: opens,
            high: highs,
            low: lows,
            close: closes,
            type: 'candlestick',
            name: 'Price',
            increasing: {{line: {{color: '#10b981'}}}},
            decreasing: {{line: {{color: '#ef4444'}}}}
        }};
        
        // EMA 20
        const emaTrace20 = {{
            x: dates,
            y: ema20,
            type: 'scatter',
            mode: 'lines',
            name: 'EMA 20',
            line: {{color: '#f59e0b', width: 2}}
        }};
        
        // EMA 50
        const emaTrace50 = {{
            x: dates,
            y: ema50,
            type: 'scatter',
            mode: 'lines',
            name: 'EMA 50',
            line: {{color: '#3b82f6', width: 2}}
        }};
        
        const data = [candlestick, emaTrace20, emaTrace50];
        
        const layout = {{
            title: '{symbol} {tf}',
            xaxis: {{
                title: 'Time',
                rangeslider: {{visible: false}}
            }},
            yaxis: {{
                title: 'Price (USD)'
            }},
            shapes: shapes,
            height: 700,
            template: 'plotly_white'
        }};
        
        Plotly.newPlot('chart', data, layout);
    </script>
</body>
</html>"""
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        logger.info(f"✅ HTML 생성 완료")
        return output_path
    
    def analyze(self, symbol=None, tf=None, days=None, output_path=None):
        """전체 분석"""
        try:
            if not symbol:
                symbol, tf, days = self.select_symbol_interactive()
            
            df = self.fetch_historical_data(symbol, tf, days)
            if df.empty:
                logger.error("❌ 데이터 없음")
                return None
            
            df = self.calculate_indicators(df)
            df = self.identify_structure(df)
            fib_levels = self.calculate_fibonacci(df)
            
            prompt = self.prepare_prompt(df, symbol, tf)
            analysis = self.query_ollama(prompt)
            
            if not output_path:
                output_path = f"ict_{symbol}_{tf}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            
            report = self.generate_html(symbol, tf, df, analysis, fib_levels, output_path)
            
            logger.info(f"✅ 완료! {report}")
            return report
            
        except Exception as e:
            logger.error(f"❌ 오류: {e}")
            logger.debug(traceback.format_exc())
            return None
        finally:
            if self.db_conn:
                self.db_conn.close()


def main():
    try:
        analyzer = OLLAMAICTAnalyzer()
        report = analyzer.analyze()
        
        if report:
            print(f"\n✅ 완료! {report}")
            import webbrowser
            webbrowser.open(f"file://{os.path.abspath(report)}")
    except KeyboardInterrupt:
        print("\n중단됨")
    except Exception as e:
        logger.error(f"❌ 오류: {e}")


if __name__ == "__main__":
    main()
