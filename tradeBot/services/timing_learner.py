"""
Timing Learner

2단계: 신호 생성 후 최적 진입/청산 타이밍 학습 모델
"""

import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from collections import defaultdict

logger = logging.getLogger(__name__)


class TimingLearner:
    """타이밍 학습 모델"""

    def __init__(self):
        """
        초기화
        """
        self.enter_timing_patterns = {}  # 시장 상태별 진입 타이밍 패턴
        self.exit_timing_patterns = {}  # 시장 상태별 청산 타이밍 패턴
        self.trained = False

    def train(
        self,
        dataset: List[Dict],
        min_samples: int = 10
    ) -> Dict:
        """
        학습 데이터셋으로부터 타이밍 패턴 학습

        Args:
            dataset: 학습 데이터셋 (SignalActionMatcher.build_training_dataset 결과)
            min_samples: 최소 샘플 수 (패턴 학습을 위한)

        Returns:
            학습 결과 통계
        """
        logger.info(f"🎓 타이밍 학습 시작: {len(dataset)}개 샘플")

        # 매칭된 데이터만 사용
        matched_samples = [
            d for d in dataset
            if d.get('output') and d.get('output').get('enter_timing')
        ]

        if len(matched_samples) < min_samples:
            logger.warning(f"⚠️ 학습 샘플 부족: {len(matched_samples)}개 (최소 {min_samples}개 필요)")
            return {
                'trained': False,
                'samples': len(matched_samples),
                'message': f'학습 샘플 부족 (최소 {min_samples}개 필요)'
            }

        # 진입 타이밍 분석
        enter_timings = []
        exit_timings = []
        market_states = []

        for sample in matched_samples:
            signal = sample.get('input', {}).get('signal', {})
            output = sample.get('output', {})
            market_state = sample.get('input', {}).get('market_state', {})

            signal_time = signal.get('created_at')
            enter_time = output.get('enter_timing')
            exit_time = output.get('exit_timing')

            if not signal_time or not enter_time:
                continue

            # 시간 파싱
            try:
                if isinstance(signal_time, str):
                    signal_time = datetime.fromisoformat(signal_time.replace('Z', '+00:00'))
                if isinstance(enter_time, str):
                    enter_time = datetime.fromisoformat(enter_time.replace('Z', '+00:00'))

                # 진입까지의 시간 차이 (분)
                enter_delay_minutes = (enter_time - signal_time).total_seconds() / 60
                if enter_delay_minutes < 0 or enter_delay_minutes > 1440:  # 24시간 이내
                    continue

                enter_timings.append(enter_delay_minutes)

                # 청산 타이밍
                if exit_time:
                    if isinstance(exit_time, str):
                        exit_time = datetime.fromisoformat(exit_time.replace('Z', '+00:00'))
                    exit_delay_minutes = (exit_time - enter_time).total_seconds() / 60
                    if exit_delay_minutes > 0 and exit_delay_minutes < 10080:  # 7일 이내
                        exit_timings.append(exit_delay_minutes)

                # 시장 상태 키 생성
                price_action = market_state.get('price_action', {})
                market_key = self._get_market_key(price_action)
                market_states.append({
                    'key': market_key,
                    'enter_delay': enter_delay_minutes,
                    'exit_delay': exit_delay_minutes if exit_time else None,
                    'pnl': output.get('pnl'),
                    'pnl_percent': output.get('pnl_percent')
                })

            except Exception as e:
                logger.debug(f"시간 파싱 실패: {e}")
                continue

        # 진입 타이밍 패턴 학습
        enter_stats = self._analyze_timing(enter_timings, 'enter')
        
        # 청산 타이밍 패턴 학습
        exit_stats = self._analyze_timing(exit_timings, 'exit') if exit_timings else {}

        # 시장 상태별 패턴 학습
        state_patterns = self._learn_state_patterns(market_states)

        self.enter_timing_patterns = enter_stats
        self.exit_timing_patterns = exit_stats
        self.state_patterns = state_patterns
        self.trained = True

        logger.info(f"✅ 타이밍 학습 완료: 진입 {len(enter_timings)}개, 청산 {len(exit_timings)}개")

        return {
            'trained': True,
            'samples': len(matched_samples),
            'enter_samples': len(enter_timings),
            'exit_samples': len(exit_timings),
            'enter_stats': enter_stats,
            'exit_stats': exit_stats,
            'state_patterns': len(state_patterns)
        }

    def _analyze_timing(
        self,
        timings: List[float],
        timing_type: str
    ) -> Dict:
        """
        타이밍 데이터 분석

        Args:
            timings: 시간 차이 리스트 (분)
            timing_type: 'enter' or 'exit'

        Returns:
            통계 정보
        """
        if not timings:
            return {}

        timings_array = np.array(timings)
        
        return {
            'mean': float(np.mean(timings_array)),
            'median': float(np.median(timings_array)),
            'std': float(np.std(timings_array)),
            'min': float(np.min(timings_array)),
            'max': float(np.max(timings_array)),
            'percentile_25': float(np.percentile(timings_array, 25)),
            'percentile_75': float(np.percentile(timings_array, 75)),
            'count': len(timings)
        }

    def _get_market_key(self, price_action: Dict) -> str:
        """
        시장 상태 키 생성

        Args:
            price_action: 가격 액션 정보

        Returns:
            시장 상태 키 (예: "uptrend_normal_neutral")
        """
        trend = price_action.get('trend', 'sideways')
        volatility = price_action.get('volatility', 'normal')
        rsi_status = price_action.get('rsi_status', 'neutral')
        
        return f"{trend}_{volatility}_{rsi_status}"

    def _learn_state_patterns(
        self,
        market_states: List[Dict]
    ) -> Dict:
        """
        시장 상태별 패턴 학습

        Args:
            market_states: 시장 상태 데이터 리스트

        Returns:
            시장 상태별 패턴
        """
        patterns = defaultdict(lambda: {
            'enter_delays': [],
            'exit_delays': [],
            'pnls': [],
            'pnl_percents': []
        })

        for state in market_states:
            key = state.get('key')
            if not key:
                continue

            if state.get('enter_delay') is not None:
                patterns[key]['enter_delays'].append(state['enter_delay'])
            if state.get('exit_delay') is not None:
                patterns[key]['exit_delays'].append(state['exit_delay'])
            if state.get('pnl') is not None:
                patterns[key]['pnls'].append(state['pnl'])
            if state.get('pnl_percent') is not None:
                patterns[key]['pnl_percents'].append(state['pnl_percent'])

        # 통계 계산
        result = {}
        for key, data in patterns.items():
            if len(data['enter_delays']) < 3:  # 최소 3개 샘플
                continue

            result[key] = {
                'enter_timing': {
                    'mean': float(np.mean(data['enter_delays'])),
                    'median': float(np.median(data['enter_delays'])),
                    'count': len(data['enter_delays'])
                },
                'exit_timing': {
                    'mean': float(np.mean(data['exit_delays'])) if data['exit_delays'] else None,
                    'median': float(np.median(data['exit_delays'])) if data['exit_delays'] else None,
                    'count': len(data['exit_delays'])
                } if data['exit_delays'] else None,
                'avg_pnl': float(np.mean(data['pnls'])) if data['pnls'] else None,
                'avg_pnl_percent': float(np.mean(data['pnl_percents'])) if data['pnl_percents'] else None,
                'win_rate': self._calculate_win_rate(data['pnls']) if data['pnls'] else None
            }

        return result

    def _calculate_win_rate(self, pnls: List[float]) -> float:
        """승률 계산"""
        if not pnls:
            return 0.0
        wins = sum(1 for pnl in pnls if pnl > 0)
        return (wins / len(pnls)) * 100

    def predict_enter_timing(
        self,
        market_state: Dict,
        default_minutes: float = 5.0
    ) -> Dict:
        """
        최적 진입 타이밍 예측

        Args:
            market_state: 시장 상태 정보
            default_minutes: 기본값 (분)

        Returns:
            예측된 진입 타이밍 정보
        """
        if not self.trained:
            return {
                'recommended_minutes': default_minutes,
                'confidence': 0.0,
                'source': 'default'
            }

        price_action = market_state.get('price_action', {})
        market_key = self._get_market_key(price_action)

        # 시장 상태별 패턴이 있으면 사용
        if market_key in self.state_patterns:
            pattern = self.state_patterns[market_key]
            timing = pattern.get('enter_timing', {})
            return {
                'recommended_minutes': timing.get('median', default_minutes),
                'confidence': min(1.0, timing.get('count', 0) / 20.0),  # 샘플 수에 따른 신뢰도
                'source': 'state_pattern',
                'market_key': market_key,
                'stats': timing
            }

        # 전체 통계 사용
        if self.enter_timing_patterns:
            return {
                'recommended_minutes': self.enter_timing_patterns.get('median', default_minutes),
                'confidence': 0.5,
                'source': 'global_stats',
                'stats': self.enter_timing_patterns
            }

        return {
            'recommended_minutes': default_minutes,
            'confidence': 0.0,
            'source': 'default'
        }

    def predict_exit_timing(
        self,
        market_state: Dict,
        enter_time: datetime,
        default_minutes: float = 240.0  # 4시간
    ) -> Dict:
        """
        최적 청산 타이밍 예측

        Args:
            market_state: 시장 상태 정보
            enter_time: 진입 시간
            default_minutes: 기본값 (분)

        Returns:
            예측된 청산 타이밍 정보
        """
        if not self.trained:
            return {
                'recommended_minutes': default_minutes,
                'confidence': 0.0,
                'source': 'default'
            }

        price_action = market_state.get('price_action', {})
        market_key = self._get_market_key(price_action)

        # 시장 상태별 패턴이 있으면 사용
        if market_key in self.state_patterns:
            pattern = self.state_patterns[market_key]
            timing = pattern.get('exit_timing')
            if timing:
                return {
                    'recommended_minutes': timing.get('median', default_minutes),
                    'confidence': min(1.0, timing.get('count', 0) / 20.0),
                    'source': 'state_pattern',
                    'market_key': market_key,
                    'stats': timing
                }

        # 전체 통계 사용
        if self.exit_timing_patterns:
            return {
                'recommended_minutes': self.exit_timing_patterns.get('median', default_minutes),
                'confidence': 0.5,
                'source': 'global_stats',
                'stats': self.exit_timing_patterns
            }

        return {
            'recommended_minutes': default_minutes,
            'confidence': 0.0,
            'source': 'default'
        }

    def get_recommendations(
        self,
        signal: Dict,
        market_state: Dict
    ) -> Dict:
        """
        신호에 대한 종합 추천

        Args:
            signal: 신호 데이터
            market_state: 시장 상태 데이터

        Returns:
            추천 정보 {
                'enter_timing': {...},
                'exit_timing': {...},
                'should_enter': bool,
                'confidence': float
            }
        """
        enter_pred = self.predict_enter_timing(market_state)
        exit_pred = self.predict_exit_timing(market_state, datetime.now())

        # 진입 여부 판단 (시장 상태별 승률 기반)
        should_enter = True
        confidence = enter_pred.get('confidence', 0.0)

        market_key = self._get_market_key(market_state.get('price_action', {}))
        if market_key in self.state_patterns:
            pattern = self.state_patterns[market_key]
            win_rate = pattern.get('win_rate')
            avg_pnl = pattern.get('avg_pnl_percent')
            
            # 승률이 50% 미만이거나 평균 손실이면 진입 비추천
            if win_rate and win_rate < 50:
                should_enter = False
                confidence = 0.3
            elif avg_pnl and avg_pnl < 0:
                should_enter = False
                confidence = 0.3
            else:
                confidence = min(1.0, (win_rate or 50) / 100.0)

        return {
            'enter_timing': enter_pred,
            'exit_timing': exit_pred,
            'should_enter': should_enter,
            'confidence': confidence,
            'market_key': market_key
        }
