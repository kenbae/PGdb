"""
OLLAMA ICT Analysis System v3.2
- Qwen2.5:7b-instruct 최적화 (한글 강화)
- 명령줄 파라미터 지원
- 30분봉 추가, 기간 직접 입력
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
        logger.info("OLLAMA ICT Analyzer v3.2 초기화 (Qwen2.5 최적화)")
        logger.info("=" * 70)
        
        self.config = self.load_config(config_path)
        self.db_conn = None
        self.ollama_url = "http://localhost:11434/api/generate"
        self.model = "qwen2.5:7b-instruct"  # Qwen2.5 사용
        
        logger.info(f"사용 모델: {self.model} (한글 최적화)")
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
            
            if self.model not in model_names:
                logger.warning(f"⚠️ '{self.model}' 미설치")
                if models:
                    self.model = models[0]['name']
                    logger.info(f"대체 모델 사용: {self.model}")
            else:
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
        print("📊 심볼 선택")
        print("=" * 70)
        symbols = self.get_available_symbols()
        
        print("\n사용 가능한 심볼:")
        for i, s in enumerate(symbols[:20], 1):  # 상위 20개만 표시
            print(f"{i:2d}. {s['symbol']:15s} | {s['total']:>10,} 캔들 | {s['tfs']} TF")
        
        if len(symbols) > 20:
            print(f"... 외 {len(symbols) - 20}개 심볼")
        
        while True:
            choice = input(f"\n심볼 선택 (1-{len(symbols)}) 또는 심볼명 직접 입력: ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(symbols):
                    symbol = symbols[idx]['symbol']
                    break
            else:
                symbol = choice.upper()
                if any(s['symbol'] == symbol for s in symbols):
                    break
            print("❌ 다시 입력하세요")
        
        print("\n" + "=" * 70)
        print("⏱️ 타임프레임 선택")
        print("=" * 70)
        print("1. 15m  (15분)")
        print("2. 30m  (30분)  ⭐")
        print("3. 1h   (1시간)")
        print("4. 4h   (4시간)")
        print("5. 1d   (1일)")
        print("6. 직접 입력")
        
        tf_map = {'1': '15m', '2': '30m', '3': '1h', '4': '4h', '5': '1d'}
        while True:
            tf_choice = input("\n선택 (1-6): ").strip()
            if tf_choice in tf_map:
                tf = tf_map[tf_choice]
                break
            elif tf_choice == '6':
                tf = input("타임프레임 입력 (예: 5m, 15m, 1h, 4h, 1d): ").strip().lower()
                if tf in ['1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d', '1w']:
                    break
                print("❌ 유효한 타임프레임을 입력하세요")
            else:
                print("❌ 1-6 중 선택하세요")
        
        print("\n" + "=" * 70)
        print("📅 분석 기간")
        print("=" * 70)
        print("1. 30일")
        print("2. 60일  (권장)")
        print("3. 90일")
        print("4. 180일")
        print("5. 직접 입력")
        
        days_map = {'1': 30, '2': 60, '3': 90, '4': 180}
        while True:
            days_choice = input("\n선택 (1-5): ").strip()
            if days_choice in days_map:
                days = days_map[days_choice]
                break
            elif days_choice == '5':
                try:
                    days = int(input("일수 입력 (예: 45, 120, 365): ").strip())
                    if days > 0:
                        break
                    print("❌ 양수를 입력하세요")
                except ValueError:
                    print("❌ 숫자를 입력하세요")
            else:
                print("❌ 1-5 중 선택하세요")
        
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
        logger.info("기술적 지표 계산...")
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        df['rsi'] = 100 - (100 / (1 + gain / loss))
        
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = true_range.rolling(window=14).mean()
        
        logger.info("✅ 지표 계산 완료")
        return df
    
    def identify_structure(self, df):
        """ICT 구조 식별"""
        logger.info("ICT 구조 분석...")
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
        
        logger.info("✅ 구조 분석 완료")
        return df
    
    def calculate_fibonacci(self, df):
        """피보나치 레벨"""
        logger.info("피보나치 레벨 계산...")
        recent = df.tail(50)
        high = recent['high'].max()
        low = recent['low'].min()
        range_val = high - low
        
        fib = {
            '0.0': low,
            '23.6': low + range_val * 0.236,
            '38.2': low + range_val * 0.382,
            '50.0': low + range_val * 0.5,
            '61.8': low + range_val * 0.618,
            '78.6': low + range_val * 0.786,
            '100.0': high
        }
        
        logger.info(f"✅ 피보나치: ${low:.2f} ~ ${high:.2f}")
        return fib
    
    def prepare_prompt_qwen(self, df, symbol, tf):
        """Qwen2.5 최적화 프롬프트 (한글 강화)"""
        logger.info("Qwen2.5 한글 프롬프트 생성...")
        
        current = df['close'].iloc[-1]
        prev = df['close'].iloc[-2]
        change = ((current - prev) / prev) * 100
        recent_high = df['high'].tail(30).max()
        recent_low = df['low'].tail(30).min()
        
        # Qwen2.5는 한글을 잘 이해하므로 한글로 상세하게 작성
        prompt = f"""당신은 ICT (Inner Circle Trader) 전문 트레이더입니다. 다음 시장 데이터를 분석하고 한국어로 상세한 트레이딩 전략을 제시하세요.
반드시 한국어로 작성해야 합니다.
        
## 📊 기본 정보
- 심볼: {symbol}
- 타임프레임: {tf}
- 현재가: ${current:.2f}
- 전일 대비: {change:+.2f}%
- 분석 시각: {df['open_time'].iloc[-1]}

## 📈 가격 범위 (최근 30 캔들)
- 최고가: ${recent_high:.2f}
- 최저가: ${recent_low:.2f}
- 가격 범위: ${recent_high - recent_low:.2f} ({((recent_high - recent_low) / recent_low * 100):.2f}%)

## 🎯 기술적 지표
- EMA 20: ${df['ema_20'].iloc[-1]:.2f}
- EMA 50: ${df['ema_50'].iloc[-1]:.2f}
- EMA 200: ${df['ema_200'].iloc[-1]:.2f}
- RSI (14): {df['rsi'].iloc[-1]:.2f}
- ATR (14): ${df['atr'].iloc[-1]:.2f}

## 💹 현재 EMA 포지션
- 현재가 vs EMA 20: {'상위' if current > df['ema_20'].iloc[-1] else '하위'} (${abs(current - df['ema_20'].iloc[-1]):.2f} 차이)
- 현재가 vs EMA 50: {'상위' if current > df['ema_50'].iloc[-1] else '하위'} (${abs(current - df['ema_50'].iloc[-1]):.2f} 차이)
- EMA 배열: {'정배열' if df['ema_20'].iloc[-1] > df['ema_50'].iloc[-1] else '역배열'}

## 📊 RSI 분석
- 현재 RSI: {df['rsi'].iloc[-1]:.2f}
- 상태: {'과매수 (70 이상)' if df['rsi'].iloc[-1] > 70 else '과매도 (30 이하)' if df['rsi'].iloc[-1] < 30 else '중립 (30-70)'}

## 📉 최근 가격 움직임 (최근 10개 캔들)
{df.tail(10)[['open_time', 'open', 'high', 'low', 'close', 'volume']].to_string()}

## 🎯 요청 분석 사항

다음 항목들을 **구체적이고 실행 가능한 수준**으로 분석해주세요:

### 1. 시장 구조 분석 (Market Structure)
- 현재 추세 방향 (상승/하락/횡보)
- BOS (Break of Structure) 발생 여부 및 위치
- CHoCH (Change of Character) 발생 여부
- Higher High / Higher Low 또는 Lower High / Lower Low 패턴
- 추세 강도 및 지속 가능성

### 2. Order Blocks (주문 블록)
- 주요 Bullish Order Block 위치 및 가격대
- 주요 Bearish Order Block 위치 및 가격대
- 각 Order Block의 신뢰도 및 중요도
- 테스트 여부 및 반응 가능성

### 3. Fair Value Gaps (공정가치 갭)
- 미채움 Bullish FVG 위치 및 가격 범위
- 미채움 Bearish FVG 위치 및 가격 범위
- 채워질 확률이 높은 FVG 우선순위
- FVG 50% / 100% 채움 시나리오

### 4. 유동성 분석 (Liquidity Pools)
- BSL (Buy-Side Liquidity) 위치 및 가격
- SSL (Sell-Side Liquidity) 위치 및 가격
- Equal Highs / Equal Lows 식별
- 유동성 사냥 가능성 및 시나리오

### 5. OTE (Optimal Trade Entry) 레벨
- 피보나치 61.8% - 78.6% 영역 가격대
- 현재가가 Discount Zone인지 Premium Zone인지
- 최적 진입 가격 및 근거
- 리스크/리워드 비율

### 6. Kill Zones (거래 시간대)
- 현재 시간대 분석 (아시아/런던/뉴욕)
- 최적 거래 시간대 추천
- 각 세션별 예상 변동성

### 7. Power of Three 단계
- 현재 단계: Accumulation / Manipulation / Distribution
- 각 단계별 특징 및 증거
- 다음 단계 예측 및 시기

### 8. 구체적인 트레이딩 셋업

**롱 포지션 (상승 매수):**
- 진입가: $X.XX (구체적 가격)
- 손절가: $X.XX (구체적 가격)
- 목표가 1: $X.XX (1:2 R/R)
- 목표가 2: $X.XX (1:3 R/R)
- 목표가 3: $X.XX (1:5 R/R)
- 진입 근거 (3가지 이상)
- 손절 근거
- 포지션 사이즈 권장

**숏 포지션 (하락 매도):**
- 진입가: $X.XX (구체적 가격)
- 손절가: $X.XX (구체적 가격)
- 목표가 1: $X.XX (1:2 R/R)
- 목표가 2: $X.XX (1:3 R/R)
- 목표가 3: $X.XX (1:5 R/R)
- 진입 근거 (3가지 이상)
- 손절 근거
- 포지션 사이즈 권장

### 9. 리스크 관리
- 추천 리스크 비율 (계좌의 X%)
- 최대 손실 허용 금액
- 분할 진입 전략
- 트레일링 스탑 전략

### 10. 시나리오별 대응 전략
- 시나리오 A: 상승 시 대응
- 시나리오 B: 하락 시 대응
- 시나리오 C: 횡보 시 대응

모든 가격은 **구체적인 숫자($X,XXX.XX 형식)**로 제시하고, **명확한 근거**를 함께 설명해주세요.
전문 트레이더에게 조언하듯이 실전에서 바로 적용 가능한 수준의 상세한 분석을 부탁드립니다.
"""
        
        logger.info("✅ 프롬프트 생성 완료")
        return prompt
    
    def query_ollama(self, prompt):
        """OLLAMA 쿼리 (Qwen2.5 최적화)"""
        logger.info(f"OLLAMA 분석 시작 (모델: {self.model})...")
        
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,        # 창의성과 정확성 균형
                "top_p": 0.9,              # 다양성
                "top_k": 40,               # 선택 폭
                "num_predict": 5000,       # 긴 응답 (Qwen2.5는 긴 응답 가능)
                "num_ctx": 8192,           # 큰 컨텍스트 윈도우
                "repeat_penalty": 1.1      # 반복 방지
            }
        }
        
        try:
            logger.info("요청 전송 중...")
            start = datetime.now()
            
            response = requests.post(
                self.ollama_url,
                json=payload,
                timeout=600  # Qwen2.5는 더 오래 걸릴 수 있음 (10분)
            )
            response.raise_for_status()
            
            elapsed = (datetime.now() - start).total_seconds()
            result = response.json()
            analysis = result.get('response', '')
            
            logger.info(f"✅ 분석 완료 ({elapsed:.2f}초, {len(analysis)} 글자)")
            logger.info(f"토큰 수: {result.get('eval_count', 'N/A')}")
            
            return analysis
            
        except requests.exceptions.Timeout:
            logger.error("❌ 타임아웃 (10분 초과)")
            return "분석 타임아웃 - 재시도하거나 더 짧은 기간으로 시도하세요."
        except Exception as e:
            logger.error(f"❌ OLLAMA 실패: {e}")
            return f"분석 실패: {str(e)}"
    
    def generate_html(self, symbol, tf, df, analysis, fib_levels, output_path):
        """HTML 생성"""
        logger.info(f"HTML 생성: {output_path}")
        
        current = df['close'].iloc[-1]
        prev = df['close'].iloc[-2]
        change = ((current - prev) / prev) * 100
        
        # 차트 데이터
        chart_data = df.tail(100).copy()
        
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
        
        # OB shapes
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
            'fillcolor': 'rgba(245, 158, 11, 0.15)',
            'line': {'color': 'rgba(245, 158, 11, 0.6)', 'width': 2},
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
    <title>ICT Analysis - {symbol} {tf}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
</head>
<body class="bg-gray-100">
    <div class="container mx-auto p-4 max-w-7xl">
        <!-- Header -->
        <div class="bg-gradient-to-r from-blue-600 to-purple-600 rounded-lg shadow-lg p-6 mb-6 text-white">
            <h1 class="text-4xl font-bold mb-2">🎯 ICT Analysis Report</h1>
            <div class="text-blue-100">
                {symbol} | {tf} | Qwen2.5 분석 | {datetime.now().strftime('%Y-%m-%d %H:%M')}
            </div>
        </div>
        
        <!-- Stats -->
        <div class="grid grid-cols-4 gap-4 mb-6">
            <div class="bg-white rounded-lg shadow p-6">
                <div class="text-sm text-gray-600 mb-1">Current Price</div>
                <div class="text-3xl font-bold text-gray-900">${current:.2f}</div>
            </div>
            <div class="bg-white rounded-lg shadow p-6">
                <div class="text-sm text-gray-600 mb-1">Change</div>
                <div class="text-3xl font-bold {'text-green-600' if change > 0 else 'text-red-600'}">{change:+.2f}%</div>
            </div>
            <div class="bg-white rounded-lg shadow p-6">
                <div class="text-sm text-gray-600 mb-1">RSI</div>
                <div class="text-3xl font-bold {'text-red-600' if df['rsi'].iloc[-1] > 70 else 'text-green-600' if df['rsi'].iloc[-1] < 30 else 'text-gray-900'}">{df['rsi'].iloc[-1]:.2f}</div>
            </div>
            <div class="bg-white rounded-lg shadow p-6">
                <div class="text-sm text-gray-600 mb-1">ATR</div>
                <div class="text-3xl font-bold text-gray-900">${df['atr'].iloc[-1]:.2f}</div>
            </div>
        </div>
        
        <!-- Legend -->
        <div class="bg-white rounded-lg shadow p-4 mb-6">
            <h3 class="font-bold mb-3 text-lg">📊 Chart Legend (범례)</h3>
            <div class="flex flex-wrap gap-6">
                <div class="flex items-center">
                    <div class="w-10 h-5 mr-2 rounded" style="background: rgba(16, 185, 129, 0.3); border: 1px solid rgba(16, 185, 129, 0.8);"></div>
                    <span class="text-sm">Bullish Order Block</span>
                </div>
                <div class="flex items-center">
                    <div class="w-10 h-5 mr-2 rounded" style="background: rgba(239, 68, 68, 0.3); border: 1px solid rgba(239, 68, 68, 0.8);"></div>
                    <span class="text-sm">Bearish Order Block</span>
                </div>
                <div class="flex items-center">
                    <div class="w-10 h-5 mr-2 rounded" style="background: rgba(245, 158, 11, 0.2); border: 2px solid rgba(245, 158, 11, 0.6);"></div>
                    <span class="text-sm">OTE Zone (61.8-78.6%)</span>
                </div>
                <div class="flex items-center">
                    <div class="w-10 h-0.5 mr-2" style="background: rgba(156, 163, 175, 0.5); border-top: 1px dashed;"></div>
                    <span class="text-sm">Fibonacci Levels</span>
                </div>
            </div>
        </div>
        
        <!-- Chart -->
        <div class="bg-white rounded-lg shadow-lg p-6 mb-6">
            <h2 class="text-2xl font-bold mb-4">📈 Price Chart with ICT Structure</h2>
            <div id="chart" style="width:100%; height:700px;"></div>
        </div>
        
        <!-- ICT Stats -->
        <div class="grid grid-cols-2 gap-6 mb-6">
            <div class="bg-gradient-to-br from-green-50 to-green-100 rounded-lg shadow p-6">
                <h3 class="font-bold text-xl mb-3 text-green-800">🟢 Bullish Structures</h3>
                <div class="space-y-2">
                    <div class="flex justify-between">
                        <span class="text-gray-700">Order Blocks:</span>
                        <span class="font-bold text-green-700">{len(bullish_obs)}개</span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-gray-700">Fair Value Gaps:</span>
                        <span class="font-bold text-green-700">{len(chart_data[chart_data['bullish_fvg'] == True])}개</span>
                    </div>
                </div>
            </div>
            <div class="bg-gradient-to-br from-red-50 to-red-100 rounded-lg shadow p-6">
                <h3 class="font-bold text-xl mb-3 text-red-800">🔴 Bearish Structures</h3>
                <div class="space-y-2">
                    <div class="flex justify-between">
                        <span class="text-gray-700">Order Blocks:</span>
                        <span class="font-bold text-red-700">{len(bearish_obs)}개</span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-gray-700">Fair Value Gaps:</span>
                        <span class="font-bold text-red-700">{len(chart_data[chart_data['bearish_fvg'] == True])}개</span>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Fib Levels -->
        <div class="bg-white rounded-lg shadow p-6 mb-6">
            <h3 class="font-bold text-xl mb-4">📐 Fibonacci OTE Levels</h3>
            <div class="grid grid-cols-4 gap-4">
                {' '.join([f'<div class="bg-gray-50 p-3 rounded"><span class="text-gray-600 text-sm">{k}%:</span> <span class="font-bold text-lg">${v:.2f}</span></div>' for k, v in fib_levels.items()])}
            </div>
            <div class="mt-4 p-4 bg-yellow-50 border-l-4 border-yellow-500 rounded">
                <p class="text-sm text-yellow-800">
                    <strong>OTE Zone:</strong> ${fib_levels['61.8']:.2f} ~ ${fib_levels['78.6']:.2f} 
                    (최적 진입 영역 - Optimal Trade Entry)
                </p>
            </div>
        </div>
        
        <!-- Analysis -->
        <div class="bg-white rounded-lg shadow-lg p-6">
            <h2 class="text-2xl font-bold mb-4 flex items-center">
                <span class="mr-2">🤖</span> 
                Qwen2.5 ICT 분석 
                <span class="ml-2 text-sm font-normal text-gray-500">(한글 최적화)</span>
            </h2>
            <div class="prose max-w-none">
                <pre class="whitespace-pre-wrap bg-gray-900 text-gray-100 p-6 rounded-lg text-sm leading-relaxed">{analysis}</pre>
            </div>
        </div>
        
        <!-- Footer -->
        <div class="mt-6 text-center text-gray-600 text-sm">
            <p>Generated by OLLAMA ICT Analyzer v3.2 with Qwen2.5:7b-instruct</p>
            <p class="mt-1">⚠️ 이 분석은 참고용이며 투자 권유가 아닙니다. 투자 결정은 본인의 책임입니다.</p>
        </div>
    </div>
    
    <script>
        const dates = {json.dumps(dates)};
        const opens = {json.dumps(opens)};
        const highs = {json.dumps(highs)};
        const lows = {json.dumps(lows)};
        const closes = {json.dumps(closes)};
        const ema20 = {json.dumps(ema20)};
        const ema50 = {json.dumps(ema50)};
        const shapes = {json.dumps(all_shapes)};
        
        const candlestick = {{
            x: dates,
            open: opens,
            high: highs,
            low: lows,
            close: closes,
            type: 'candlestick',
            name: 'Price',
            increasing: {{line: {{color: '#10b981', width: 1}}}},
            decreasing: {{line: {{color: '#ef4444', width: 1}}}}
        }};
        
        const emaTrace20 = {{
            x: dates,
            y: ema20,
            type: 'scatter',
            mode: 'lines',
            name: 'EMA 20',
            line: {{color: '#f59e0b', width: 2}}
        }};
        
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
            title: '{{
                text: '{symbol} {tf} - ICT Analysis',
                font: {{size: 20}}
            }}',
            xaxis: {{
                title: 'Time',
                rangeslider: {{visible: false}},
                gridcolor: '#e5e7eb'
            }},
            yaxis: {{
                title: 'Price (USD)',
                gridcolor: '#e5e7eb'
            }},
            shapes: shapes,
            height: 700,
            template: 'plotly_white',
            hovermode: 'x unified'
        }};
        
        const config = {{
            responsive: true,
            displayModeBar: true,
            modeBarButtonsToRemove: ['lasso2d', 'select2d']
        }};
        
        Plotly.newPlot('chart', data, layout, config);
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
            # 파라미터가 없으면 인터랙티브 모드
            if not symbol:
                symbol, tf, days = self.select_symbol_interactive()
            else:
                logger.info(f"파라미터 모드: {symbol} {tf} {days}일")
            
            df = self.fetch_historical_data(symbol, tf, days)
            if df.empty:
                logger.error("❌ 데이터 없음")
                return None
            
            df = self.calculate_indicators(df)
            df = self.identify_structure(df)
            fib_levels = self.calculate_fibonacci(df)
            
            prompt = self.prepare_prompt_qwen(df, symbol, tf)
            analysis = self.query_ollama(prompt)
            
            if not output_path:
                output_path = f"ict_{symbol}_{tf}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            
            report = self.generate_html(symbol, tf, df, analysis, fib_levels, output_path)
            
            logger.info("=" * 70)
            logger.info(f"✅ 분석 완료! 리포트: {report}")
            logger.info("=" * 70)
            
            return report
            
        except Exception as e:
            logger.error(f"❌ 오류: {e}")
            logger.debug(traceback.format_exc())
            return None
        finally:
            if self.db_conn:
                self.db_conn.close()


def main():
    """메인 실행"""
    parser = argparse.ArgumentParser(
        description='OLLAMA ICT Analyzer v3.2 (Qwen2.5 최적화)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python ollama_ict_analyzer.py                           # 인터랙티브 모드
  python ollama_ict_analyzer.py BTCUSDT 1h 60           # 파라미터 모드
  python ollama_ict_analyzer.py ETHUSDT 30m 90          # 30분봉 90일
  python ollama_ict_analyzer.py XRPUSDT 4h 180          # 4시간봉 180일
        """
    )
    
    parser.add_argument('symbol', nargs='?', help='심볼 (예: BTCUSDT, ETHUSDT)')
    parser.add_argument('tf', nargs='?', help='타임프레임 (예: 15m, 30m, 1h, 4h, 1d)')
    parser.add_argument('days', nargs='?', type=int, help='기간 (일 단위, 예: 30, 60, 90)')
    parser.add_argument('-o', '--output', help='출력 파일명')
    
    args = parser.parse_args()
    
    try:
        analyzer = OLLAMAICTAnalyzer()
        
        report = analyzer.analyze(
            symbol=args.symbol,
            tf=args.tf,
            days=args.days,
            output_path=args.output
        )
        
        if report:
            print(f"\n✅ 완료! {report}")
            import webbrowser
            webbrowser.open(f"file://{os.path.abspath(report)}")
        else:
            print("\n❌ 분석 실패")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n\n사용자가 중단했습니다.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ 오류: {e}")
        logger.debug(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
