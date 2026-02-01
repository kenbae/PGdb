"""
OLLAMA ICT Analysis System v3.3
- 차트 렌더링 버그 완전 수정
- Qwen2.5 최적화
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
import argparse

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
        logger.info("OLLAMA ICT Analyzer v3.3 초기화")
        logger.info("=" * 70)
        
        self.config = self.load_config(config_path)
        self.db_conn = None
        self.ollama_url = "http://localhost:11434/api/generate"
        self.model = "qwen2.5:7b-instruct"
        
        logger.info(f"사용 모델: {self.model}")
        self.test_ollama_connection()
        
    def load_config(self, config_path):
        encodings = ['utf-8', 'utf-8-sig', 'cp949', 'euc-kr']
        for encoding in encodings:
            try:
                with open(config_path, 'r', encoding=encoding) as f:
                    return yaml.safe_load(f)
            except (UnicodeDecodeError, UnicodeError):
                continue
        raise ValueError("config.yaml을 읽을 수 없습니다.")
    
    def test_ollama_connection(self):
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
        print("\n" + "=" * 70)
        print("📊 심볼 선택")
        print("=" * 70)
        symbols = self.get_available_symbols()
        
        print("\n사용 가능한 심볼:")
        for i, s in enumerate(symbols[:20], 1):
            print(f"{i:2d}. {s['symbol']:15s} | {s['total']:>10,} 캔들")
        
        while True:
            choice = input(f"\n심볼 선택 (1-{len(symbols)}) 또는 심볼명: ").strip()
            if choice.isdigit() and 0 < int(choice) <= len(symbols):
                symbol = symbols[int(choice)-1]['symbol']
                break
            else:
                symbol = choice.upper()
                if any(s['symbol'] == symbol for s in symbols):
                    break
        
        print("\n⏱️ 타임프레임: 1.15m  2.30m  3.1h  4.4h  5.1d  6.직접입력")
        tf_map = {'1': '15m', '2': '30m', '3': '1h', '4': '4h', '5': '1d'}
        while True:
            tf_choice = input("선택 (1-6): ").strip()
            if tf_choice in tf_map:
                tf = tf_map[tf_choice]
                break
            elif tf_choice == '6':
                tf = input("타임프레임 (예: 5m, 15m, 1h): ").strip().lower()
                if tf in ['1m', '5m', '15m', '30m', '1h', '4h', '1d']:
                    break
        
        print("\n📅 기간: 1.30일  2.60일  3.90일  4.180일  5.직접입력")
        days_map = {'1': 30, '2': 60, '3': 90, '4': 180}
        while True:
            days_choice = input("선택 (1-5): ").strip()
            if days_choice in days_map:
                days = days_map[days_choice]
                break
            elif days_choice == '5':
                try:
                    days = int(input("일수 입력: ").strip())
                    if days > 0:
                        break
                except ValueError:
                    pass
        
        return symbol, tf, days
    
    def fetch_historical_data(self, symbol, tf, days):
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
        
        if len(df) == 0:
            logger.error("❌ 데이터가 없습니다!")
        else:
            logger.info(f"기간: {df['open_time'].min()} ~ {df['open_time'].max()}")
            logger.info(f"가격: ${df['low'].min():.2f} ~ ${df['high'].max():.2f}")
        
        return df
    
    def calculate_indicators(self, df):
        logger.info("지표 계산...")
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
        
        logger.info("✅ 지표 완료")
        return df
    
    def identify_structure(self, df):
        logger.info("구조 분석...")
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
        
        logger.info("✅ 구조 완료")
        return df
    
    def calculate_fibonacci(self, df):
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
    
    def prepare_prompt_qwen(self, df, symbol, tf):
        current = df['close'].iloc[-1]
        prev = df['close'].iloc[-2]
        change = ((current - prev) / prev) * 100
        
        return f"""당신은 ICT 전문가입니다. {symbol} {tf} 분석:

현재가: ${current:.2f} ({change:+.2f}%)
EMA 20: ${df['ema_20'].iloc[-1]:.2f}
EMA 50: ${df['ema_50'].iloc[-1]:.2f}
RSI: {df['rsi'].iloc[-1]:.2f}

다음을 한국어로 분석하세요:
1. 시장 구조 (추세, BOS, CHoCH)
2. Order Blocks (구체적 가격)
3. Fair Value Gaps
4. 유동성 (BSL/SSL)
5. OTE 레벨
6. 트레이딩 셋업 (진입/손절/목표가)

구체적인 가격을 제시하고 실행 가능한 전략을 작성하세요.
"""
    
    def query_ollama(self, prompt):
        logger.info("OLLAMA 분석 시작...")
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
        logger.info(f"HTML 생성: {output_path}")
        
        current = df['close'].iloc[-1]
        prev = df['close'].iloc[-2]
        change = ((current - prev) / prev) * 100
        
        # 차트 데이터 (최근 100개)
        chart_data = df.tail(100).copy()
        logger.info(f"차트 데이터: {len(chart_data)}개 캔들")
        
        # 안전하게 데이터 변환
        try:
            dates = [d.strftime('%Y-%m-%d %H:%M') for d in chart_data['open_time']]
            opens = [float(x) for x in chart_data['open']]
            highs = [float(x) for x in chart_data['high']]
            lows = [float(x) for x in chart_data['low']]
            closes = [float(x) for x in chart_data['close']]
            ema20_vals = [float(x) if pd.notna(x) else None for x in chart_data['ema_20']]
            ema50_vals = [float(x) if pd.notna(x) else None for x in chart_data['ema_50']]
            
            logger.info(f"✅ 데이터 변환 완료: {len(dates)}개")
        except Exception as e:
            logger.error(f"❌ 데이터 변환 실패: {e}")
            raise
        
        # Order Blocks
        bullish_obs = chart_data[chart_data['bullish_ob'] == True]
        bearish_obs = chart_data[chart_data['bearish_ob'] == True]
        
        # Shapes 생성
        shapes = []
        
        # Bullish OBs
        for _, row in bullish_obs.iterrows():
            shapes.append({
                'type': 'rect',
                'x0': row['open_time'].strftime('%Y-%m-%d %H:%M'),
                'x1': (row['open_time'] + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M'),
                'y0': float(row['low']),
                'y1': float(row['high']),
                'fillcolor': 'rgba(16, 185, 129, 0.2)',
                'line': {'width': 0},
                'layer': 'below'
            })
        
        # Bearish OBs
        for _, row in bearish_obs.iterrows():
            shapes.append({
                'type': 'rect',
                'x0': row['open_time'].strftime('%Y-%m-%d %H:%M'),
                'x1': (row['open_time'] + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M'),
                'y0': float(row['close']),
                'y1': float(row['open']),
                'fillcolor': 'rgba(239, 68, 68, 0.2)',
                'line': {'width': 0},
                'layer': 'below'
            })
        
        # OTE Zone
        if len(dates) > 0:
            shapes.append({
                'type': 'rect',
                'x0': dates[0],
                'x1': dates[-1],
                'y0': fib_levels['61.8'],
                'y1': fib_levels['78.6'],
                'fillcolor': 'rgba(245, 158, 11, 0.1)',
                'line': {'color': 'rgba(245, 158, 11, 0.5)', 'width': 2},
                'layer': 'below'
            })
        
        logger.info(f"Shapes 생성: {len(shapes)}개")
        
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{symbol} {tf} - ICT Analysis</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <script src="https://cdn.tailwindcss.com">
    </script>
</head>
<body class="bg-gray-100 p-4">
    <div class="max-w-7xl mx-auto">
        <div class="bg-blue-600 text-white p-6 rounded-lg mb-4">
            <h1 class="text-3xl font-bold">🎯 ICT Analysis</h1>
            <div>{symbol} | {tf} | {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
        </div>
        
        <div class="grid grid-cols-4 gap-4 mb-4">
            <div class="bg-white p-4 rounded shadow">
                <div class="text-sm text-gray-600">Price</div>
                <div class="text-2xl font-bold">${current:.2f}</div>
            </div>
            <div class="bg-white p-4 rounded shadow">
                <div class="text-sm text-gray-600">Change</div>
                <div class="text-2xl font-bold {'text-green-600' if change > 0 else 'text-red-600'}">{change:+.2f}%</div>
            </div>
            <div class="bg-white p-4 rounded shadow">
                <div class="text-sm text-gray-600">RSI</div>
                <div class="text-2xl font-bold">{df['rsi'].iloc[-1]:.2f}</div>
            </div>
            <div class="bg-white p-4 rounded shadow">
                <div class="text-sm text-gray-600">ATR</div>
                <div class="text-2xl font-bold">${df['atr'].iloc[-1]:.2f}</div>
            </div>
        </div>
        
        <div class="bg-white p-4 rounded shadow mb-4">
            <h2 class="text-xl font-bold mb-4">📈 Chart</h2>
            <div id="chart"></div>
        </div>
        
        <div class="bg-white p-4 rounded shadow">
            <h2 class="text-xl font-bold mb-4">🤖 Analysis</h2>
            <pre class="whitespace-pre-wrap bg-gray-900 text-gray-100 p-4 rounded">{analysis}</pre>
        </div>
    </div>
    
    <script>
        console.log('=== 차트 초기화 ===');
        
        const chartData = {{
            dates: {json.dumps(dates)},
            opens: {json.dumps(opens)},
            highs: {json.dumps(highs)},
            lows: {json.dumps(lows)},
            closes: {json.dumps(closes)},
            ema20: {json.dumps(ema20_vals)},
            ema50: {json.dumps(ema50_vals)}
        }};
        
        console.log('데이터 개수:', chartData.dates.length);
        
        if (chartData.dates.length === 0) {{
            document.getElementById('chart').innerHTML = '<p class="text-red-500">데이터 없음</p>';
        }} else {{
            const trace1 = {{
                x: chartData.dates,
                open: chartData.opens,
                high: chartData.highs,
                low: chartData.lows,
                close: chartData.closes,
                type: 'candlestick',
                name: 'Price',
                increasing: {{line: {{color: '#10b981'}}}},
                decreasing: {{line: {{color: '#ef4444'}}}}
            }};
            
            const trace2 = {{
                x: chartData.dates,
                y: chartData.ema20,
                type: 'scatter',
                mode: 'lines',
                name: 'EMA 20',
                line: {{color: '#f59e0b', width: 2}},
                connectgaps: true
            }};
            
            const trace3 = {{
                x: chartData.dates,
                y: chartData.ema50,
                type: 'scatter',
                mode: 'lines',
                name: 'EMA 50',
                line: {{color: '#3b82f6', width: 2}},
                connectgaps: true
            }};
            
            const layout = {{
                height: 600,
                xaxis: {{rangeslider: {{visible: false}}}},
                yaxis: {{title: 'Price'}},
                shapes: {json.dumps(shapes)},
                hovermode: 'x'
            }};
            
            Plotly.newPlot('chart', [trace1, trace2, trace3], layout);
            console.log('✅ 차트 완료');
        }}
    </script>
</body>
</html>"""
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        logger.info(f"✅ HTML 저장 완료")
        return output_path
    
    def analyze(self, symbol=None, tf=None, days=None, output_path=None):
        try:
            if not symbol:
                symbol, tf, days = self.select_symbol_interactive()
            
            df = self.fetch_historical_data(symbol, tf, days)
            if df.empty:
                return None
            
            df = self.calculate_indicators(df)
            df = self.identify_structure(df)
            fib_levels = self.calculate_fibonacci(df)
            
            prompt = self.prepare_prompt_qwen(df, symbol, tf)
            analysis = self.query_ollama(prompt)
            
            if not output_path:
                output_path = f"ict_{symbol}_{tf}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            
            report = self.generate_html(symbol, tf, df, analysis, fib_levels, output_path)
            logger.info(f"✅ 완료: {report}")
            return report
            
        except Exception as e:
            logger.error(f"❌ 오류: {e}")
            traceback.print_exc()
            return None
        finally:
            if self.db_conn:
                self.db_conn.close()


def main():
    parser = argparse.ArgumentParser(description='ICT Analyzer')
    parser.add_argument('symbol', nargs='?', help='심볼')
    parser.add_argument('tf', nargs='?', help='타임프레임')
    parser.add_argument('days', nargs='?', type=int, help='기간')
    parser.add_argument('-o', '--output', help='출력파일')
    args = parser.parse_args()
    
    try:
        analyzer = OLLAMAICTAnalyzer()
        report = analyzer.analyze(args.symbol, args.tf, args.days, args.output)
        
        if report:
            print(f"\n✅ {report}")
            import webbrowser
            webbrowser.open(f"file://{os.path.abspath(report)}")
    except KeyboardInterrupt:
        print("\n중단")
    except Exception as e:
        print(f"\n❌ 오류: {e}")


if __name__ == "__main__":
    main()
