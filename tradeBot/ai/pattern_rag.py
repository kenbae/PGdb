# -*- coding: utf-8 -*-
"""
Pattern RAG (Retrieval-Augmented Generation) 시스템

과거 캔들 패턴을 분석하고, 현재 패턴과 유사한 과거 사례를 찾아
OLLAMA에게 제공하여 더 정확한 신호를 생성합니다.

특징:
- 캔들 패턴 특징 추출 (가격, 볼륨, 지표 기반)
- 코사인 유사도 기반 패턴 매칭
- SQLite 기반 패턴 저장
- OLLAMA 연동 신호 생성
"""

import os
import sys
import json
import logging
import sqlite3
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta

# 경로 설정
_current_dir = os.path.dirname(os.path.abspath(__file__))
_tradebot_dir = os.path.dirname(_current_dir)
_pgdb_dir = os.path.dirname(_tradebot_dir)
if _pgdb_dir not in sys.path:
    sys.path.insert(0, _pgdb_dir)

from indicators.technical import TechnicalIndicators

logger = logging.getLogger(__name__)


@dataclass
class PatternFeatures:
    """패턴 특징 벡터"""
    symbol: str
    timeframe: str
    timestamp: datetime

    # 가격 패턴 (정규화된 값)
    price_change_pct: float          # 기간 내 가격 변화율
    high_low_range: float            # 고가-저가 범위 (ATR 대비)
    close_position: float            # 캔들 내 종가 위치 (0-1)
    body_ratio: float                # 몸통 비율 (시가-종가 / 고가-저가)

    # 추세 패턴
    ema_trend: float                 # EMA 추세 강도 (-1 ~ 1)
    ema_alignment: int               # EMA 정배열(1), 역배열(-1), 혼합(0)
    price_vs_ema: float              # 가격 vs EMA 위치

    # 모멘텀 패턴
    rsi_value: float                 # RSI (0-100 → 0-1로 정규화)
    rsi_divergence: float            # RSI 다이버전스 (-1 ~ 1)

    # 볼륨 패턴
    volume_ratio: float              # 평균 대비 볼륨 비율
    volume_trend: float              # 볼륨 추세

    # 변동성 패턴
    atr_ratio: float                 # ATR / 가격 비율
    bb_position: float               # 볼린저 밴드 내 위치 (0-1)

    # 결과 (학습 데이터용)
    future_return: Optional[float] = None   # N캔들 후 수익률
    future_direction: Optional[int] = None  # 1=상승, -1=하락, 0=횡보

    def to_vector(self) -> np.ndarray:
        """특징을 벡터로 변환"""
        return np.array([
            self.price_change_pct,
            self.high_low_range,
            self.close_position,
            self.body_ratio,
            self.ema_trend,
            self.ema_alignment,
            self.price_vs_ema,
            self.rsi_value,
            self.rsi_divergence,
            self.volume_ratio,
            self.volume_trend,
            self.atr_ratio,
            self.bb_position
        ])

    @staticmethod
    def vector_size() -> int:
        """벡터 크기"""
        return 13


@dataclass
class SimilarPattern:
    """유사 패턴"""
    pattern: PatternFeatures
    similarity: float
    future_return: float
    future_direction: int


class PatternExtractor:
    """패턴 특징 추출기"""

    def __init__(self, lookback: int = 20, future_bars: int = 10):
        """
        Args:
            lookback: 패턴 분석에 사용할 과거 캔들 수
            future_bars: 미래 수익률 계산에 사용할 캔들 수
        """
        self.lookback = lookback
        self.future_bars = future_bars

    def extract_features(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        index: int = -1
    ) -> Optional[PatternFeatures]:
        """
        특정 시점의 패턴 특징 추출

        Args:
            df: OHLCV 데이터프레임 (지표 포함)
            symbol: 심볼
            timeframe: 타임프레임
            index: 추출할 인덱스 (-1은 마지막)

        Returns:
            PatternFeatures 또는 None
        """
        try:
            # Decimal 타입을 float로 변환 (PostgreSQL DECIMAL 컬럼 대응)
            numeric_cols = ['open', 'high', 'low', 'close', 'volume']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = df[col].astype(float)

            # 지표가 없으면 계산
            if 'ema20' not in df.columns:
                df = TechnicalIndicators.calculate_all(df, inplace=False)

            # 인덱스 처리
            if index < 0:
                index = len(df) + index

            if index < self.lookback or index >= len(df):
                return None

            # 현재 캔들
            current = df.iloc[index]

            # 과거 데이터
            past_df = df.iloc[index - self.lookback:index + 1]

            # 1. 가격 패턴
            price_change_pct = (current['close'] - past_df.iloc[0]['close']) / past_df.iloc[0]['close']

            atr = current.get('atr', 0)
            if pd.isna(atr) or atr == 0:
                atr = past_df['high'].max() - past_df['low'].min()

            high_low_range = (current['high'] - current['low']) / atr if atr > 0 else 0

            close_position = (current['close'] - current['low']) / (current['high'] - current['low']) if current['high'] != current['low'] else 0.5

            body = abs(current['close'] - current['open'])
            wick = current['high'] - current['low']
            body_ratio = body / wick if wick > 0 else 0

            # 2. 추세 패턴
            ema20 = current.get('ema20', current['close'])
            ema50 = current.get('ema50', current['close'])
            ema200 = current.get('ema200', current['close'])

            if pd.isna(ema20): ema20 = current['close']
            if pd.isna(ema50): ema50 = current['close']
            if pd.isna(ema200): ema200 = current['close']

            # EMA 추세 강도
            ema_trend = (ema20 - ema50) / ema50 if ema50 > 0 else 0
            ema_trend = np.clip(ema_trend * 10, -1, 1)  # 정규화

            # EMA 정배열/역배열
            if ema20 > ema50 > ema200:
                ema_alignment = 1
            elif ema20 < ema50 < ema200:
                ema_alignment = -1
            else:
                ema_alignment = 0

            # 가격 vs EMA
            price_vs_ema = (current['close'] - ema50) / ema50 if ema50 > 0 else 0
            price_vs_ema = np.clip(price_vs_ema * 10, -1, 1)

            # 3. 모멘텀 패턴
            rsi = current.get('rsi', 50)
            if pd.isna(rsi):
                rsi = 50
            rsi_value = rsi / 100  # 0-1로 정규화

            # RSI 다이버전스 (간단 버전)
            past_rsi = past_df['rsi'].dropna()
            if len(past_rsi) >= 5:
                price_slope = (current['close'] - past_df.iloc[-5]['close']) / past_df.iloc[-5]['close']
                rsi_slope = (rsi - past_rsi.iloc[-5]) / 100 if len(past_rsi) >= 5 else 0
                rsi_divergence = np.clip((price_slope - rsi_slope) * 10, -1, 1)
            else:
                rsi_divergence = 0

            # 4. 볼륨 패턴
            avg_volume = past_df['volume'].mean()
            volume_ratio = current['volume'] / avg_volume if avg_volume > 0 else 1
            volume_ratio = min(volume_ratio, 5)  # 최대 5배로 제한

            # 볼륨 추세
            recent_vol = past_df['volume'].tail(5).mean()
            older_vol = past_df['volume'].head(5).mean()
            volume_trend = (recent_vol - older_vol) / older_vol if older_vol > 0 else 0
            volume_trend = np.clip(volume_trend, -1, 1)

            # 5. 변동성 패턴
            atr_ratio = atr / current['close'] if current['close'] > 0 else 0
            atr_ratio = min(atr_ratio * 100, 1)  # 정규화

            bb_upper = current.get('bb_upper', current['high'])
            bb_lower = current.get('bb_lower', current['low'])
            if pd.isna(bb_upper): bb_upper = current['high']
            if pd.isna(bb_lower): bb_lower = current['low']

            bb_range = bb_upper - bb_lower
            bb_position = (current['close'] - bb_lower) / bb_range if bb_range > 0 else 0.5

            # 6. 미래 수익률 (학습용)
            future_return = None
            future_direction = None

            if index + self.future_bars < len(df):
                future_close = df.iloc[index + self.future_bars]['close']
                future_return = (future_close - current['close']) / current['close']

                if future_return > 0.01:
                    future_direction = 1
                elif future_return < -0.01:
                    future_direction = -1
                else:
                    future_direction = 0

            # timestamp
            timestamp = current.name if isinstance(current.name, datetime) else datetime.now()

            return PatternFeatures(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=timestamp,
                price_change_pct=float(price_change_pct),
                high_low_range=float(high_low_range),
                close_position=float(close_position),
                body_ratio=float(body_ratio),
                ema_trend=float(ema_trend),
                ema_alignment=int(ema_alignment),
                price_vs_ema=float(price_vs_ema),
                rsi_value=float(rsi_value),
                rsi_divergence=float(rsi_divergence),
                volume_ratio=float(volume_ratio),
                volume_trend=float(volume_trend),
                atr_ratio=float(atr_ratio),
                bb_position=float(bb_position),
                future_return=float(future_return) if future_return is not None else None,
                future_direction=int(future_direction) if future_direction is not None else None
            )

        except Exception as e:
            logger.error(f"패턴 추출 실패: {e}")
            return None

    def extract_all(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        skip_last: int = 0
    ) -> List[PatternFeatures]:
        """
        전체 데이터프레임에서 패턴 추출

        Args:
            df: OHLCV 데이터프레임
            symbol: 심볼
            timeframe: 타임프레임
            skip_last: 마지막 N개 제외 (미래 데이터 없는 경우)

        Returns:
            패턴 리스트
        """
        patterns = []

        # Decimal 타입을 float로 변환 (PostgreSQL DECIMAL 컬럼 대응)
        df = df.copy()  # SettingWithCopyWarning 방지
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = df[col].astype(float)

        # 지표 계산
        df = TechnicalIndicators.calculate_all(df, inplace=False)

        end_idx = len(df) - skip_last - self.future_bars

        for i in range(self.lookback, end_idx):
            pattern = self.extract_features(df, symbol, timeframe, i)
            if pattern and pattern.future_return is not None:
                patterns.append(pattern)

        logger.info(f"{symbol} {timeframe}: {len(patterns)}개 패턴 추출")

        return patterns


class PatternDatabase:
    """패턴 데이터베이스"""

    def __init__(self, db_path: str = None):
        """
        Args:
            db_path: SQLite 데이터베이스 경로
        """
        if db_path is None:
            db_path = os.path.join(_tradebot_dir, 'data', 'patterns.db')

        self.db_path = db_path

        # 디렉토리 생성
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        # 테이블 생성
        self._create_tables()

    def _create_tables(self):
        """테이블 생성"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    vector TEXT NOT NULL,
                    features TEXT NOT NULL,
                    future_return REAL,
                    future_direction INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, timeframe, timestamp)
                )
            ''')

            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_patterns_symbol_tf
                ON patterns(symbol, timeframe)
            ''')

            conn.commit()

    def save_patterns(self, patterns: List[PatternFeatures]) -> int:
        """
        패턴 저장

        Args:
            patterns: 패턴 리스트

        Returns:
            저장된 패턴 수
        """
        saved = 0

        with sqlite3.connect(self.db_path) as conn:
            for pattern in patterns:
                try:
                    vector = pattern.to_vector().tolist()
                    features = asdict(pattern)

                    # timestamp 처리
                    ts = pattern.timestamp
                    if isinstance(ts, datetime):
                        ts = ts.isoformat()

                    conn.execute('''
                        INSERT OR REPLACE INTO patterns
                        (symbol, timeframe, timestamp, vector, features, future_return, future_direction)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        pattern.symbol,
                        pattern.timeframe,
                        ts,
                        json.dumps(vector),
                        json.dumps(features, default=str),
                        pattern.future_return,
                        pattern.future_direction
                    ))
                    saved += 1
                except Exception as e:
                    logger.warning(f"패턴 저장 실패: {e}")

            conn.commit()

        logger.info(f"{saved}개 패턴 저장됨")
        return saved

    def find_similar(
        self,
        query_pattern: PatternFeatures,
        top_k: int = 10,
        same_symbol: bool = True
    ) -> List[SimilarPattern]:
        """
        유사 패턴 검색

        Args:
            query_pattern: 쿼리 패턴
            top_k: 상위 K개 반환
            same_symbol: 같은 심볼만 검색

        Returns:
            유사 패턴 리스트
        """
        query_vector = query_pattern.to_vector()

        with sqlite3.connect(self.db_path) as conn:
            # 패턴 조회
            if same_symbol:
                cursor = conn.execute('''
                    SELECT vector, features, future_return, future_direction
                    FROM patterns
                    WHERE symbol = ? AND timeframe = ? AND future_return IS NOT NULL
                ''', (query_pattern.symbol, query_pattern.timeframe))
            else:
                cursor = conn.execute('''
                    SELECT vector, features, future_return, future_direction
                    FROM patterns
                    WHERE timeframe = ? AND future_return IS NOT NULL
                ''', (query_pattern.timeframe,))

            results = []

            for row in cursor:
                vector = np.array(json.loads(row[0]))
                features = json.loads(row[1])
                future_return = row[2]
                future_direction = row[3]

                # 코사인 유사도 계산
                similarity = self._cosine_similarity(query_vector, vector)

                # PatternFeatures 복원
                pattern = PatternFeatures(
                    symbol=features['symbol'],
                    timeframe=features['timeframe'],
                    timestamp=datetime.fromisoformat(features['timestamp']) if isinstance(features['timestamp'], str) else features['timestamp'],
                    price_change_pct=features['price_change_pct'],
                    high_low_range=features['high_low_range'],
                    close_position=features['close_position'],
                    body_ratio=features['body_ratio'],
                    ema_trend=features['ema_trend'],
                    ema_alignment=features['ema_alignment'],
                    price_vs_ema=features['price_vs_ema'],
                    rsi_value=features['rsi_value'],
                    rsi_divergence=features['rsi_divergence'],
                    volume_ratio=features['volume_ratio'],
                    volume_trend=features['volume_trend'],
                    atr_ratio=features['atr_ratio'],
                    bb_position=features['bb_position'],
                    future_return=future_return,
                    future_direction=future_direction
                )

                results.append(SimilarPattern(
                    pattern=pattern,
                    similarity=similarity,
                    future_return=future_return,
                    future_direction=future_direction
                ))

            # 유사도 순 정렬
            results.sort(key=lambda x: x.similarity, reverse=True)

            return results[:top_k]

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """코사인 유사도 계산"""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return float(np.dot(a, b) / (norm_a * norm_b))

    def get_stats(self, symbol: str = None, timeframe: str = None) -> Dict:
        """
        데이터베이스 통계

        Args:
            symbol: 필터링할 심볼
            timeframe: 필터링할 타임프레임

        Returns:
            통계 딕셔너리
        """
        with sqlite3.connect(self.db_path) as conn:
            query = "SELECT COUNT(*) FROM patterns WHERE 1=1"
            params = []

            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)

            if timeframe:
                query += " AND timeframe = ?"
                params.append(timeframe)

            cursor = conn.execute(query, params)
            total = cursor.fetchone()[0]

            # 방향별 통계
            query_up = query + " AND future_direction = 1"
            cursor = conn.execute(query_up, params)
            up_count = cursor.fetchone()[0]

            query_down = query + " AND future_direction = -1"
            cursor = conn.execute(query_down, params)
            down_count = cursor.fetchone()[0]

            return {
                'total_patterns': total,
                'up_patterns': up_count,
                'down_patterns': down_count,
                'neutral_patterns': total - up_count - down_count
            }


class PatternRAGSignalGenerator:
    """RAG 기반 신호 생성기"""

    def __init__(
        self,
        ollama_host: str = 'http://localhost:11434',
        ollama_model: str = 'qwen2.5:7b-instruct',
        db_path: str = None,
        top_k: int = 10
    ):
        """
        Args:
            ollama_host: OLLAMA 서버 주소
            ollama_model: 모델 이름
            db_path: 패턴 DB 경로
            top_k: 유사 패턴 검색 개수
        """
        self.ollama_host = ollama_host
        self.ollama_model = ollama_model
        self.top_k = top_k

        self.extractor = PatternExtractor()
        self.db = PatternDatabase(db_path)

        logger.info(f"PatternRAG 초기화: model={ollama_model}, top_k={top_k}")

    def learn_from_data(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str
    ) -> int:
        """
        과거 데이터로부터 패턴 학습 (저장)

        Args:
            df: OHLCV 데이터프레임
            symbol: 심볼
            timeframe: 타임프레임

        Returns:
            저장된 패턴 수
        """
        logger.info(f"{symbol} {timeframe} 패턴 학습 시작...")

        # 패턴 추출
        patterns = self.extractor.extract_all(df, symbol, timeframe)

        # 저장
        saved = self.db.save_patterns(patterns)

        logger.info(f"{symbol} {timeframe} 학습 완료: {saved}개 패턴")

        return saved

    def generate_signal(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        current_price: float = None,
        skip_ai: bool = False
    ) -> Dict:
        """
        RAG 기반 신호 생성

        Args:
            df: 현재 OHLCV 데이터프레임
            symbol: 심볼
            timeframe: 타임프레임
            current_price: 현재가 (없으면 마지막 종가)
            skip_ai: AI 분석 스킵 여부 (백테스트 시 True 권장)

        Returns:
            신호 딕셔너리
        """
        # 1. 현재 패턴 추출
        current_pattern = self.extractor.extract_features(df, symbol, timeframe, -1)

        if current_pattern is None:
            return {
                'signal': 'hold',
                'confidence': 0,
                'reason': '패턴 추출 실패',
                'similar_patterns': 0
            }

        # 2. 유사 패턴 검색
        similar_patterns = self.db.find_similar(current_pattern, self.top_k)

        if len(similar_patterns) == 0:
            return {
                'signal': 'hold',
                'confidence': 0,
                'reason': '유사 패턴 없음 (데이터 학습 필요)',
                'similar_patterns': 0
            }

        # 3. 통계 분석
        stats = self._analyze_similar_patterns(similar_patterns)

        # 4. AI 분석 (선택적)
        if skip_ai:
            # AI 없이 통계만으로 신호 생성
            ai_analysis = {}
        else:
            ai_analysis = self._call_ollama(current_pattern, similar_patterns, stats)

        # 5. 최종 신호 생성
        signal = self._create_final_signal(
            current_pattern,
            similar_patterns,
            stats,
            ai_analysis,
            current_price or df.iloc[-1]['close'],
            skip_ai=skip_ai
        )

        return signal

    def _analyze_similar_patterns(self, patterns: List[SimilarPattern]) -> Dict:
        """유사 패턴 통계 분석"""
        if not patterns:
            return {}

        returns = [p.future_return for p in patterns]
        directions = [p.future_direction for p in patterns]
        similarities = [p.similarity for p in patterns]

        # 가중 평균 (유사도 기반)
        weighted_return = sum(r * s for r, s in zip(returns, similarities)) / sum(similarities)

        up_count = sum(1 for d in directions if d == 1)
        down_count = sum(1 for d in directions if d == -1)

        return {
            'avg_return': np.mean(returns) * 100,
            'weighted_return': weighted_return * 100,
            'std_return': np.std(returns) * 100,
            'up_ratio': up_count / len(patterns),
            'down_ratio': down_count / len(patterns),
            'avg_similarity': np.mean(similarities),
            'max_return': max(returns) * 100,
            'min_return': min(returns) * 100
        }

    def _call_ollama(
        self,
        current: PatternFeatures,
        similar: List[SimilarPattern],
        stats: Dict
    ) -> Dict:
        """OLLAMA 분석 호출"""
        import requests

        # 프롬프트 생성
        prompt = self._create_prompt(current, similar, stats)

        try:
            response = requests.post(
                f"{self.ollama_host}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 500
                    }
                },
                timeout=30
            )

            if response.status_code != 200:
                logger.error(f"OLLAMA 에러: {response.status_code}")
                return {'error': 'OLLAMA 호출 실패'}

            text = response.json().get('response', '')

            # JSON 파싱
            return self._parse_ollama_response(text)

        except Exception as e:
            logger.error(f"OLLAMA 호출 실패: {e}")
            return {'error': str(e)}

    def _create_prompt(
        self,
        current: PatternFeatures,
        similar: List[SimilarPattern],
        stats: Dict
    ) -> str:
        """OLLAMA 프롬프트 생성"""
        # 현재 패턴 설명
        current_desc = f"""
**현재 패턴 ({current.symbol} {current.timeframe}):**
- 가격 변화: {current.price_change_pct*100:.2f}%
- RSI: {current.rsi_value*100:.1f}
- EMA 정배열: {'예' if current.ema_alignment == 1 else '아니오' if current.ema_alignment == -1 else '혼합'}
- EMA 대비 가격: {'+' if current.price_vs_ema > 0 else ''}{current.price_vs_ema*100:.1f}%
- 볼륨 비율: {current.volume_ratio:.1f}x
- BB 위치: {current.bb_position*100:.0f}%
"""

        # 유사 패턴 설명
        similar_desc = "**유사한 과거 패턴 (최근 10개):**\n"
        for i, sp in enumerate(similar[:5], 1):
            direction = "상승" if sp.future_direction == 1 else "하락" if sp.future_direction == -1 else "횡보"
            similar_desc += f"{i}. 유사도 {sp.similarity:.2f} → {direction} ({sp.future_return*100:+.2f}%)\n"

        # 통계 설명
        stats_desc = f"""
**유사 패턴 통계:**
- 평균 수익률: {stats.get('avg_return', 0):.2f}%
- 가중 평균 수익률: {stats.get('weighted_return', 0):.2f}%
- 상승 확률: {stats.get('up_ratio', 0)*100:.0f}%
- 하락 확률: {stats.get('down_ratio', 0)*100:.0f}%
- 평균 유사도: {stats.get('avg_similarity', 0):.2f}
"""

        prompt = f"""당신은 암호화폐 트레이딩 전문가입니다.
과거 유사 패턴 데이터를 기반으로 현재 상황을 분석하고 매매 신호를 제안해주세요.

{current_desc}

{similar_desc}

{stats_desc}

다음 JSON 형식으로 응답하세요:

{{
    "signal": "buy/sell/hold",
    "confidence": 0.0-1.0,
    "reasoning": "판단 근거 (2-3문장)",
    "risk_level": "low/medium/high",
    "expected_return": 예상 수익률 (숫자, 예: 2.5),
    "stop_loss_pct": 권장 손절 비율 (숫자, 예: 1.5),
    "take_profit_pct": 권장 익절 비율 (숫자, 예: 3.0)
}}

**중요:**
- JSON만 출력하세요 (다른 텍스트 없이)
- 유사 패턴의 승률과 수익률을 기반으로 판단하세요
- 유사도가 낮으면 신뢰도를 낮추세요
"""

        return prompt

    def _parse_ollama_response(self, text: str) -> Dict:
        """OLLAMA 응답 파싱"""
        try:
            # JSON 추출
            start = text.find('{')
            end = text.rfind('}') + 1

            if start == -1 or end == 0:
                return {'error': 'JSON not found'}

            json_str = text[start:end]
            return json.loads(json_str)

        except Exception as e:
            logger.error(f"응답 파싱 실패: {e}")
            return {'error': str(e)}

    def _create_final_signal(
        self,
        current: PatternFeatures,
        similar: List[SimilarPattern],
        stats: Dict,
        ai_analysis: Dict,
        current_price: float,
        skip_ai: bool = False
    ) -> Dict:
        """최종 신호 생성"""
        # 통계 기반 값
        up_ratio = stats.get('up_ratio', 0.5)
        avg_return = stats.get('avg_return', 0)
        avg_similarity = stats.get('avg_similarity', 0)

        if skip_ai:
            # === AI 없이 통계만으로 신호 생성 (백테스트용) ===
            # 상승 확률이 높고 평균 수익률이 양수면 매수
            if up_ratio >= 0.65 and avg_return > 0.5:
                final_signal = 'buy'
                confidence = up_ratio * avg_similarity
            # 하락 확률이 높고 평균 수익률이 음수면 매도
            elif up_ratio <= 0.35 and avg_return < -0.5:
                final_signal = 'sell'
                confidence = (1 - up_ratio) * avg_similarity
            else:
                final_signal = 'hold'
                confidence = 0.5

            ai_reasoning = f"통계 기반: 상승확률 {up_ratio*100:.0f}%, 평균수익률 {avg_return:.2f}%"

            # 기본 SL/TP (ATR 기반 대신 고정 비율)
            sl_pct = 2.0
            tp_pct = 4.0
            risk_level = 'medium' if 0.4 <= up_ratio <= 0.6 else 'low' if up_ratio > 0.7 or up_ratio < 0.3 else 'high'

        else:
            # === AI 분석 포함 (실시간용) ===
            ai_signal = ai_analysis.get('signal', 'hold')
            ai_confidence = ai_analysis.get('confidence', 0.5)
            ai_reasoning = ai_analysis.get('reasoning', '')

            # 최종 신호 결정 (AI + 통계)
            if ai_signal == 'buy' and up_ratio > 0.6:
                final_signal = 'buy'
                confidence = (ai_confidence + up_ratio) / 2
            elif ai_signal == 'sell' and up_ratio < 0.4:
                final_signal = 'sell'
                confidence = (ai_confidence + (1 - up_ratio)) / 2
            else:
                final_signal = 'hold'
                confidence = 0.5

            sl_pct = ai_analysis.get('stop_loss_pct', 2.0)
            tp_pct = ai_analysis.get('take_profit_pct', 4.0)
            risk_level = ai_analysis.get('risk_level', 'medium')

        # SL/TP 계산
        if final_signal == 'buy':
            stop_loss = current_price * (1 - sl_pct / 100)
            take_profit = current_price * (1 + tp_pct / 100)
        elif final_signal == 'sell':
            stop_loss = current_price * (1 + sl_pct / 100)
            take_profit = current_price * (1 - tp_pct / 100)
        else:
            stop_loss = None
            take_profit = None

        return {
            'signal': final_signal,
            'confidence': round(confidence, 3),
            'entry_price': current_price,
            'stop_loss': round(stop_loss, 8) if stop_loss else None,
            'take_profit': round(take_profit, 8) if take_profit else None,
            'risk_level': risk_level,
            'reasoning': ai_reasoning,
            'similar_patterns': len(similar),
            'pattern_stats': {
                'up_ratio': round(up_ratio, 2),
                'avg_return': round(avg_return, 2),
                'avg_similarity': round(avg_similarity, 2)
            },
            'ai_analysis': ai_analysis if not skip_ai else {},
            'timestamp': datetime.now().isoformat()
        }


# 테스트
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=" * 70)
    print("Pattern RAG 시스템 테스트")
    print("=" * 70)
    print()

    # 테스트 데이터 생성 (실제로는 DB에서 가져옴)
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=500, freq='1h')

    # 가상의 OHLCV 데이터
    close = 50000 + np.cumsum(np.random.randn(500) * 100)
    high = close + np.abs(np.random.randn(500) * 50)
    low = close - np.abs(np.random.randn(500) * 50)
    open_price = close + np.random.randn(500) * 30
    volume = np.random.randint(100, 1000, 500)

    df = pd.DataFrame({
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    }, index=dates)

    # RAG 생성기
    rag = PatternRAGSignalGenerator(
        ollama_host='http://localhost:11434',
        ollama_model='qwen2.5:7b-instruct'
    )

    # 1. 학습
    print("1. 패턴 학습 중...")
    saved = rag.learn_from_data(df.iloc[:-50], 'BTCUSDT', '1h')
    print(f"   저장된 패턴: {saved}개")
    print()

    # 2. DB 통계
    stats = rag.db.get_stats('BTCUSDT', '1h')
    print("2. 패턴 DB 통계:")
    print(f"   총 패턴: {stats['total_patterns']}")
    print(f"   상승 패턴: {stats['up_patterns']}")
    print(f"   하락 패턴: {stats['down_patterns']}")
    print()

    # 3. 신호 생성
    print("3. 신호 생성 중...")
    signal = rag.generate_signal(df.iloc[-100:], 'BTCUSDT', '1h')

    print()
    print("=" * 70)
    print("생성된 신호")
    print("=" * 70)
    print(f"신호: {signal['signal'].upper()}")
    print(f"신뢰도: {signal['confidence']:.1%}")
    print(f"진입가: ${signal['entry_price']:,.2f}")
    if signal['stop_loss']:
        print(f"손절가: ${signal['stop_loss']:,.2f}")
        print(f"익절가: ${signal['take_profit']:,.2f}")
    print(f"리스크: {signal['risk_level']}")
    print()
    print(f"근거: {signal['reasoning']}")
    print()
    print("패턴 통계:")
    print(f"  유사 패턴 수: {signal['similar_patterns']}개")
    print(f"  상승 확률: {signal['pattern_stats']['up_ratio']*100:.0f}%")
    print(f"  평균 수익률: {signal['pattern_stats']['avg_return']:.2f}%")
    print(f"  평균 유사도: {signal['pattern_stats']['avg_similarity']:.2f}")
