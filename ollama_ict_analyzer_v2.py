"""
OLLAMA ICT Analysis System v2.0
- Qwen2.5:7b-instruct 최적화
- 심볼 선택 인터랙티브 메뉴
- 상세 디버그 로깅
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import yaml
import requests
import json
from datetime import datetime, timedelta
import pandas as pd
from jinja2 import Template
import os
import logging
import sys
import traceback

# 로깅 설정
logging.basicConfig(
    level=logging.DEBUG,
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
        logger.info("OLLAMA ICT Analyzer v2.0 초기화")
        logger.info("=" * 70)
        
        self.config = self.load_config(config_path)
        self.db_conn = None
        self.ollama_url = "http://localhost:11434/api/generate"
        # self.model = "qwen2.5:7b-instruct"
        self.model = "gemma3:4b"
        # self.model = "llama3.2:latest"
        
        logger.info(f"OLLAMA URL: {self.ollama_url}")
        logger.info(f"사용 모델: {self.model}")
        
        self.test_ollama_connection()
        
    def load_config(self, config_path):
        """설정 파일 로드"""
        logger.debug(f"설정 파일 로드: {config_path}")
        encodings = ['utf-8', 'utf-8-sig', 'cp949', 'euc-kr']
        for encoding in encodings:
            try:
                with open(config_path, 'r', encoding=encoding) as f:
                    config = yaml.safe_load(f)
                logger.info(f"✅ 설정 파일 로드 성공 ({encoding})")
                return config
            except (UnicodeDecodeError, UnicodeError):
                continue
        raise ValueError("config.yaml을 읽을 수 없습니다.")
    
    def test_ollama_connection(self):
        """OLLAMA 연결 테스트"""
        logger.info("OLLAMA 연결 테스트...")
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=5)
            response.raise_for_status()
            models = response.json().get('models', [])
            logger.info(f"✅ OLLAMA 연결 성공 (모델: {len(models)}개)")
            model_names = [m['name'] for m in models]
            logger.info(f"설치된 모델: {', '.join(model_names)}")
            if self.model not in model_names:
                logger.warning(f"⚠️ '{self.model}' 미설치. 첫 모델 사용")
                if models:
                    self.model = models[0]['name']
            logger.info(f"✅ 사용 모델: {self.model}")
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ OLLAMA 연결 실패: {e}")
            raise
    
    def get_db_connection(self):
        """DB 연결"""
        if not self.db_conn or self.db_conn.closed:
            db_config = self.config.get('db', {})
            logger.debug(f"DB 연결: {db_config.get('host')}:{db_config.get('port')}/{db_config.get('name')}")
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
        logger.info("심볼 목록 조회...")
        conn = self.get_db_connection()
        query = """
            SELECT symbol, COUNT(*) as total, COUNT(DISTINCT tf) as tfs,
                   MIN(open_time) as earliest, MAX(open_time) as latest
            FROM candles GROUP BY symbol ORDER BY total DESC
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            symbols = cur.fetchall()
        logger.info(f"✅ {len(symbols)}개 심볼 발견")
        return symbols
    
    def select_symbol_interactive(self):
        """인터랙티브 심볼 선택"""
        print("\n" + "=" * 70)
        print("심볼 선택")
        print("=" * 70)
        symbols = self.get_available_symbols()
        if not symbols:
            logger.error("❌ 심볼 없음")
            return None, None, None
        
        print("\n📊 사용 가능한 심볼:")
        for i, s in enumerate(symbols, 1):
            print(f"{i:2d}. {s['symbol']:15s} | {s['total']:>10,} 캔들 | "
                  f"{s['tfs']} TF | {s['latest'].strftime('%Y-%m-%d')}")
        
        while True:
            choice = input(f"\n심볼 선택 (1-{len(symbols)}) 또는 심볼명: ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(symbols):
                    symbol = symbols[idx]['symbol']
                    break
            else:
                symbol = choice.upper()
                if any(s['symbol'] == symbol for s in symbols):
                    break
            print("❌ 다시 입력")
        
        print("\n⏱️ 타임프레임: 1.1h  2.4h  3.1d  4.30m  5.15m")
        tf_map = {'1': '1h', '2': '4h', '3': '1d', '4': '30m', '5': '15m'}
        while True:
            tf_choice = input("선택 (1-5) 또는 직접 입력: ").strip()
            if tf_choice in tf_map:
                tf = tf_map[tf_choice]
                break
            elif tf_choice in ['1m', '5m', '15m', '30m', '1h', '4h', '1d']:
                tf = tf_choice
                break
            print("❌ 다시 입력")
        
        print("\n📅 기간: 1.30일  2.60일  3.90일  4.180일  5.직접입력")
        days_map = {'1': 30, '2': 60, '3': 90, '4': 180}
        while True:
            days_choice = input("선택 (1-5): ").strip()
            if days_choice in days_map:
                days = days_map[days_choice]
                break
            elif days_choice == '5':
                try:
                    days = int(input("일수: ").strip())
                    if days > 0:
                        break
                except ValueError:
                    pass
            print("❌ 다시 입력")
        
        logger.info(f"✅ 선택: {symbol} {tf} {days}일")
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
        logger.info("지표 계산...")
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        df['rsi'] = 100 - (100 / (1 + gain / loss))
        
        df['bb_middle'] = df['close'].rolling(window=20).mean()
        bb_std = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['bb_middle'] + (bb_std * 2)
        df['bb_lower'] = df['bb_middle'] - (bb_std * 2)
        
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = true_range.rolling(window=14).mean()
        
        logger.info("✅ 지표 완료")
        return df
    
    def identify_structure(self, df):
        """ICT 구조 식별"""
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
    
    def prepare_prompt(self, df, symbol, tf):
        """프롬프트 생성"""
        logger.info("프롬프트 생성...")
        current = df['close'].iloc[-1]
        prev = df['close'].iloc[-2]
        change = ((current - prev) / prev) * 100
        recent_high = df['high'].tail(30).max()
        recent_low = df['low'].tail(30).min()
        
        prompt = f"""당신은 ICT 전문가입니다. 다음을 한국어로 분석하세요:

## 기본 정보
- 심볼: {symbol}
- 타임프레임: {tf}
- 현재가: ${current:.2f}
- 변화: {change:+.2f}%
- 날짜: {df['open_time'].iloc[-1]}

## 가격 범위
- 고점: ${recent_high:.2f}
- 저점: ${recent_low:.2f}
- 범위: ${recent_high - recent_low:.2f}

## 기술적 지표
- EMA 20: ${df['ema_20'].iloc[-1]:.2f}
- EMA 50: ${df['ema_50'].iloc[-1]:.2f}
- EMA 200: ${df['ema_200'].iloc[-1]:.2f}
- RSI: {df['rsi'].iloc[-1]:.2f}
- ATR: ${df['atr'].iloc[-1]:.2f}

## 최근 10 캔들
{df.tail(10)[['open_time', 'open', 'high', 'low', 'close']].to_string()}

## 분석 요청
다음을 명확히 분석하세요:
1. 시장 구조 (추세, BOS, CHoCH)
2. Order Blocks (주요 수요/공급 영역)
3. Fair Value Gaps (되돌림 영역)
4. 유동성 풀 (BSL/SSL)
5. OTE (최적 진입점)
6. Kill Zones (거래 시간대)
7. Power of Three (현재 단계)
8. 트레이딩 셋업 (진입/손절/목표)

구체적이고 실행 가능한 분석을 제공하세요.
"""
        logger.info("✅ 프롬프트 완료")
        return prompt
    
    def query_ollama(self, prompt):
        """OLLAMA 쿼리"""
        logger.info(f"OLLAMA 분석 시작 (모델: {self.model})...")
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9,
                "num_predict": 4000,
                "num_ctx": 8192
            }
        }
        
        try:
            start = datetime.now()
            response = requests.post(self.ollama_url, json=payload, timeout=300)
            response.raise_for_status()
            elapsed = (datetime.now() - start).total_seconds()
            
            result = response.json()
            analysis = result.get('response', '')
            logger.info(f"✅ 분석 완료 ({elapsed:.2f}초, {len(analysis)} 글자)")
            return analysis
        except Exception as e:
            logger.error(f"❌ OLLAMA 실패: {e}")
            return None
    
    def generate_html(self, symbol, tf, df, analysis, output_path):
        """HTML 리포트 생성"""
        logger.info(f"HTML 생성: {output_path}")
        
        current = df['close'].iloc[-1]
        prev = df['close'].iloc[-2]
        change = ((current - prev) / prev) * 100
        
        chart_data = df.tail(100)
        
        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>ICT Analysis - {symbol}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
</head>
<body class="bg-gray-100 p-8">
    <div class="max-w-7xl mx-auto">
        <div class="bg-white rounded-lg shadow p-6 mb-6">
            <h1 class="text-4xl font-bold mb-2">🎯 ICT Analysis Report</h1>
            <div class="text-gray-600">{symbol} | {tf} | {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
        </div>
        
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
        
        <div class="bg-white rounded shadow p-6 mb-6">
            <h2 class="text-2xl font-bold mb-4">Price Chart</h2>
            <div id="chart"></div>
        </div>
        
        <div class="bg-white rounded shadow p-6">
            <h2 class="text-2xl font-bold mb-4">🤖 OLLAMA ICT Analysis</h2>
            <pre class="whitespace-pre-wrap bg-gray-900 text-gray-100 p-4 rounded">{analysis if analysis else '분석 실패'}</pre>
        </div>
    </div>
    
    <script>
        var trace = {{
            x: {list(chart_data['open_time'].dt.strftime('%Y-%m-%d %H:%M'))},
            close: {list(chart_data['close'])},
            high: {list(chart_data['high'])},
            low: {list(chart_data['low'])},
            open: {list(chart_data['open'])},
            type: 'candlestick',
            increasing: {{line: {{color: '#10b981'}}}},
            decreasing: {{line: {{color: '#ef4444'}}}}
        }};
        var ema20 = {{
            x: {list(chart_data['open_time'].dt.strftime('%Y-%m-%d %H:%M'))},
            y: {list(chart_data['ema_20'])},
            type: 'scatter',
            mode: 'lines',
            name: 'EMA 20',
            line: {{color: '#f59e0b'}}
        }};
        var ema50 = {{
            x: {list(chart_data['open_time'].dt.strftime('%Y-%m-%d %H:%M'))},
            y: {list(chart_data['ema_50'])},
            type: 'scatter',
            mode: 'lines',
            name: 'EMA 50',
            line: {{color: '#3b82f6'}}
        }};
        Plotly.newPlot('chart', [trace, ema20, ema50], {{
            height: 600,
            xaxis: {{rangeslider: {{visible: false}}}},
            yaxis: {{title: 'Price (USD)'}},
            template: 'plotly_white'
        }});
    </script>
</body>
</html>"""
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        logger.info(f"✅ HTML 생성 완료: {output_path}")
        return output_path
    
    def analyze(self, symbol=None, tf=None, days=None, output_path=None):
        """전체 분석 실행"""
        logger.info("=" * 70)
        logger.info("분석 시작")
        logger.info("=" * 70)
        
        try:
            # 심볼 선택
            if not symbol:
                symbol, tf, days = self.select_symbol_interactive()
                if not symbol:
                    return None
            
            # 데이터 로드
            df = self.fetch_historical_data(symbol, tf, days)
            if df.empty:
                logger.error("❌ 데이터 없음")
                return None
            
            # 지표 계산
            df = self.calculate_indicators(df)
            df = self.identify_structure(df)
            
            # 프롬프트 생성
            prompt = self.prepare_prompt(df, symbol, tf)
            
            # OLLAMA 분석
            analysis = self.query_ollama(prompt)
            
            # HTML 생성
            if not output_path:
                output_path = f"ict_{symbol}_{tf}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            
            report = self.generate_html(symbol, tf, df, analysis, output_path)
            
            logger.info("=" * 70)
            logger.info(f"✅ 분석 완료! 리포트: {report}")
            logger.info("=" * 70)
            
            return report
            
        except Exception as e:
            logger.error(f"❌ 분석 실패: {e}")
            logger.debug(traceback.format_exc())
            return None
        
        finally:
            if self.db_conn:
                self.db_conn.close()


def main():
    """메인 실행"""
    try:
        analyzer = OLLAMAICTAnalyzer()
        report = analyzer.analyze()
        
        if report:
            print(f"\n✅ 완료! 브라우저에서 {report} 파일을 여세요.")
            import webbrowser
            webbrowser.open(f"file://{os.path.abspath(report)}")
        else:
            print("\n❌ 분석 실패")
            
    except KeyboardInterrupt:
        print("\n\n사용자가 중단했습니다.")
    except Exception as e:
        logger.error(f"❌ 오류: {e}")
        logger.debug(traceback.format_exc())


if __name__ == "__main__":
    main()
