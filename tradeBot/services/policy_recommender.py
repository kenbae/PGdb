"""
Policy Recommender

2단계: 최적 진입/청산 정책 및 파라미터 추천 시스템
"""

import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from collections import defaultdict

logger = logging.getLogger(__name__)


class PolicyRecommender:
    """정책 모델 추천 시스템"""

    def __init__(self):
        """
        초기화
        """
        self.policy_patterns = {}  # 시장 상태별 정책 패턴
        self.parameter_recommendations = {}  # 파라미터 추천
        self.trained = False

    def train(
        self,
        dataset: List[Dict],
        min_samples: int = 10
    ) -> Dict:
        """
        학습 데이터셋으로부터 정책 패턴 학습

        Args:
            dataset: 학습 데이터셋 (SignalActionMatcher.build_training_dataset 결과)
            min_samples: 최소 샘플 수

        Returns:
            학습 결과 통계
        """
        logger.info(f"🎯 정책 모델 학습 시작: {len(dataset)}개 샘플")

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

        # 정책 패턴 분석
        policy_data = []
        
        for sample in matched_samples:
            signal = sample.get('input', {}).get('signal', {})
            output = sample.get('output', {})
            market_state = sample.get('input', {}).get('market_state', {})

            # 정책 특성 추출
            policy_features = self._extract_policy_features(signal, output, market_state)
            if policy_features:
                policy_data.append(policy_features)

        # 시장 상태별 정책 패턴 학습
        state_policies = self._learn_state_policies(policy_data)

        # 전략별 정책 패턴 학습
        strategy_policies = self._learn_strategy_policies(policy_data)

        # 파라미터 추천 학습
        parameter_recs = self._learn_parameter_recommendations(policy_data)

        self.policy_patterns = {
            'by_state': state_policies,
            'by_strategy': strategy_policies
        }
        self.parameter_recommendations = parameter_recs
        self.trained = True

        logger.info(f"✅ 정책 모델 학습 완료: {len(policy_data)}개 샘플, {len(state_policies)}개 시장 상태 패턴")

        return {
            'trained': True,
            'samples': len(matched_samples),
            'policy_samples': len(policy_data),
            'state_patterns': len(state_policies),
            'strategy_patterns': len(strategy_policies),
            'parameter_recommendations': len(parameter_recs)
        }

    def _extract_policy_features(
        self,
        signal: Dict,
        output: Dict,
        market_state: Dict
    ) -> Optional[Dict]:
        """
        정책 특성 추출

        Args:
            signal: 신호 데이터
            output: 출력 데이터 (사용자 액션)
            market_state: 시장 상태

        Returns:
            정책 특성 딕셔너리
        """
        try:
            # 시장 상태 키
            price_action = market_state.get('price_action', {})
            market_key = self._get_market_key(price_action)

            # 성과 지표
            pnl = output.get('pnl', 0) if output.get('pnl') is not None else 0
            pnl_percent = output.get('pnl_percent', 0) if output.get('pnl_percent') is not None else 0
            is_win = pnl > 0

            # 타이밍 특성
            signal_time = signal.get('created_at')
            enter_time = output.get('enter_timing')
            exit_time = output.get('exit_timing')

            enter_delay = None
            exit_delay = None

            if signal_time and enter_time:
                if isinstance(signal_time, str):
                    signal_time = datetime.fromisoformat(signal_time.replace('Z', '+00:00'))
                if isinstance(enter_time, str):
                    enter_time = datetime.fromisoformat(enter_time.replace('Z', '+00:00'))
                enter_delay = (enter_time - signal_time).total_seconds() / 60

            if enter_time and exit_time:
                if isinstance(enter_time, str):
                    enter_time = datetime.fromisoformat(enter_time.replace('Z', '+00:00'))
                if isinstance(exit_time, str):
                    exit_time = datetime.fromisoformat(exit_time.replace('Z', '+00:00'))
                exit_delay = (exit_time - enter_time).total_seconds() / 60

            # 가격 특성
            entry_price = output.get('enter_price')
            exit_price = output.get('exit_price')
            signal_entry = signal.get('entry_price')
            stop_loss = signal.get('stop_loss')
            take_profit_1 = signal.get('take_profit_1')

            # 가격 차이 (신호 가격 대비 실제 진입 가격)
            price_diff_pct = None
            if entry_price and signal_entry:
                price_diff_pct = ((entry_price - signal_entry) / signal_entry) * 100

            # 리스크 리워드 비율
            risk_reward = signal.get('risk_reward', 0)

            return {
                'market_key': market_key,
                'strategy_name': signal.get('strategy_name'),
                'symbol': signal.get('symbol'),
                'timeframe': signal.get('timeframe'),
                'signal_type': signal.get('signal_type'),
                'pnl': pnl,
                'pnl_percent': pnl_percent,
                'is_win': is_win,
                'enter_delay': enter_delay,
                'exit_delay': exit_delay,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'signal_entry': signal_entry,
                'stop_loss': stop_loss,
                'take_profit_1': take_profit_1,
                'price_diff_pct': price_diff_pct,
                'risk_reward': risk_reward,
                'confidence': signal.get('confidence', 0),
                'indicators': market_state.get('indicators', {})
            }
        except Exception as e:
            logger.debug(f"정책 특성 추출 실패: {e}")
            return None

    def _get_market_key(self, price_action: Dict) -> str:
        """시장 상태 키 생성"""
        trend = price_action.get('trend', 'sideways')
        volatility = price_action.get('volatility', 'normal')
        rsi_status = price_action.get('rsi_status', 'neutral')
        return f"{trend}_{volatility}_{rsi_status}"

    def _learn_state_policies(
        self,
        policy_data: List[Dict]
    ) -> Dict:
        """
        시장 상태별 정책 패턴 학습

        Returns:
            시장 상태별 최적 정책
        """
        state_groups = defaultdict(list)

        for data in policy_data:
            market_key = data.get('market_key')
            if market_key:
                state_groups[market_key].append(data)

        policies = {}
        for market_key, samples in state_groups.items():
            if len(samples) < 5:  # 최소 5개 샘플
                continue

            # 승률 계산
            wins = sum(1 for s in samples if s.get('is_win', False))
            win_rate = (wins / len(samples)) * 100

            # 평균 PnL
            pnls = [s.get('pnl_percent', 0) for s in samples if s.get('pnl_percent') is not None]
            avg_pnl = float(np.mean(pnls)) if pnls else 0.0

            # 최적 진입 타이밍
            enter_delays = [s.get('enter_delay') for s in samples if s.get('enter_delay') is not None]
            optimal_enter_delay = float(np.median(enter_delays)) if enter_delays else None

            # 최적 청산 타이밍
            exit_delays = [s.get('exit_delay') for s in samples if s.get('exit_delay') is not None]
            optimal_exit_delay = float(np.median(exit_delays)) if exit_delays else None

            # 최적 가격 차이 (신호 가격 대비)
            price_diffs = [s.get('price_diff_pct') for s in samples if s.get('price_diff_pct') is not None and s.get('is_win')]
            optimal_price_diff = float(np.median(price_diffs)) if price_diffs else None

            # 평균 리스크 리워드
            risk_rewards = [s.get('risk_reward', 0) for s in samples if s.get('risk_reward')]
            avg_risk_reward = float(np.mean(risk_rewards)) if risk_rewards else None

            policies[market_key] = {
                'win_rate': win_rate,
                'avg_pnl_percent': avg_pnl,
                'sample_count': len(samples),
                'optimal_enter_delay_minutes': optimal_enter_delay,
                'optimal_exit_delay_minutes': optimal_exit_delay,
                'optimal_price_diff_percent': optimal_price_diff,
                'avg_risk_reward': avg_risk_reward,
                'recommendation': self._generate_recommendation(win_rate, avg_pnl, len(samples))
            }

        return policies

    def _learn_strategy_policies(
        self,
        policy_data: List[Dict]
    ) -> Dict:
        """
        전략별 정책 패턴 학습

        Returns:
            전략별 최적 정책
        """
        strategy_groups = defaultdict(list)

        for data in policy_data:
            strategy = data.get('strategy_name')
            if strategy:
                strategy_groups[strategy].append(data)

        policies = {}
        for strategy, samples in strategy_groups.items():
            if len(samples) < 5:
                continue

            wins = sum(1 for s in samples if s.get('is_win', False))
            win_rate = (wins / len(samples)) * 100

            pnls = [s.get('pnl_percent', 0) for s in samples if s.get('pnl_percent') is not None]
            avg_pnl = float(np.mean(pnls)) if pnls else 0.0

            enter_delays = [s.get('enter_delay') for s in samples if s.get('enter_delay') is not None]
            optimal_enter_delay = float(np.median(enter_delays)) if enter_delays else None

            policies[strategy] = {
                'win_rate': win_rate,
                'avg_pnl_percent': avg_pnl,
                'sample_count': len(samples),
                'optimal_enter_delay_minutes': optimal_enter_delay,
                'recommendation': self._generate_recommendation(win_rate, avg_pnl, len(samples))
            }

        return policies

    def _learn_parameter_recommendations(
        self,
        policy_data: List[Dict]
    ) -> Dict:
        """
        파라미터 추천 학습

        Returns:
            시장 상태별 최적 파라미터
        """
        recommendations = {}

        # 시장 상태별 최적 파라미터
        state_groups = defaultdict(list)
        for data in policy_data:
            market_key = data.get('market_key')
            if market_key:
                state_groups[market_key].append(data)

        for market_key, samples in state_groups.items():
            if len(samples) < 5:
                continue

            # 승률이 높은 샘플만 필터링
            winning_samples = [s for s in samples if s.get('is_win', False) and s.get('pnl_percent', 0) > 0]

            if len(winning_samples) < 3:
                continue

            # 최적 파라미터 추출
            indicators = [s.get('indicators', {}) for s in winning_samples]
            
            rsi_values = [ind.get('rsi') for ind in indicators if ind.get('rsi') is not None]
            avg_rsi = float(np.mean(rsi_values)) if rsi_values else None

            recommendations[market_key] = {
                'optimal_rsi_range': {
                    'min': float(np.percentile(rsi_values, 25)) if rsi_values else None,
                    'max': float(np.percentile(rsi_values, 75)) if rsi_values else None,
                    'avg': avg_rsi
                },
                'sample_count': len(winning_samples)
            }

        return recommendations

    def _generate_recommendation(
        self,
        win_rate: float,
        avg_pnl: float,
        sample_count: int
    ) -> str:
        """추천 메시지 생성"""
        if sample_count < 10:
            return "데이터 부족"
        
        if win_rate >= 60 and avg_pnl > 2:
            return "강력 추천"
        elif win_rate >= 55 and avg_pnl > 1:
            return "추천"
        elif win_rate >= 50 and avg_pnl > 0:
            return "보통"
        elif win_rate >= 45:
            return "주의"
        else:
            return "비추천"

    def recommend_policy(
        self,
        signal: Dict,
        market_state: Dict
    ) -> Dict:
        """
        신호에 대한 정책 추천

        Args:
            signal: 신호 데이터
            market_state: 시장 상태 데이터

        Returns:
            정책 추천 {
                'should_enter': bool,
                'confidence': float,
                'enter_timing_minutes': float,
                'exit_timing_minutes': float,
                'price_adjustment_percent': float,
                'risk_reward': float,
                'recommendation': str,
                'reasoning': str
            }
        """
        if not self.trained:
            return {
                'should_enter': True,
                'confidence': 0.0,
                'enter_timing_minutes': 5.0,
                'exit_timing_minutes': 240.0,
                'price_adjustment_percent': 0.0,
                'risk_reward': 2.0,
                'recommendation': '데이터 부족',
                'reasoning': '학습된 정책 패턴이 없습니다.'
            }

        price_action = market_state.get('price_action', {})
        market_key = self._get_market_key(price_action)
        strategy_name = signal.get('strategy_name')

        # 시장 상태별 정책 확인
        state_policy = self.policy_patterns.get('by_state', {}).get(market_key)
        strategy_policy = self.policy_patterns.get('by_strategy', {}).get(strategy_name) if strategy_name else None

        # 우선순위: 시장 상태 정책 > 전략 정책 > 기본값
        policy = state_policy or strategy_policy

        if policy:
            win_rate = policy.get('win_rate', 50)
            avg_pnl = policy.get('avg_pnl_percent', 0)
            recommendation = policy.get('recommendation', '보통')

            should_enter = win_rate >= 50 and avg_pnl >= 0
            confidence = min(1.0, (win_rate / 100.0) * 0.7 + (min(avg_pnl, 5) / 5.0) * 0.3)

            reasoning = f"시장 상태 '{market_key}'에서 승률 {win_rate:.1f}%, 평균 수익률 {avg_pnl:.2f}%"

            return {
                'should_enter': should_enter,
                'confidence': confidence,
                'enter_timing_minutes': policy.get('optimal_enter_delay_minutes', 5.0),
                'exit_timing_minutes': policy.get('optimal_exit_delay_minutes', 240.0),
                'price_adjustment_percent': policy.get('optimal_price_diff_percent', 0.0),
                'risk_reward': policy.get('avg_risk_reward', 2.0),
                'recommendation': recommendation,
                'reasoning': reasoning,
                'win_rate': win_rate,
                'avg_pnl_percent': avg_pnl,
                'market_key': market_key
            }
        else:
            # 기본 정책
            return {
                'should_enter': True,
                'confidence': 0.3,
                'enter_timing_minutes': 5.0,
                'exit_timing_minutes': 240.0,
                'price_adjustment_percent': 0.0,
                'risk_reward': 2.0,
                'recommendation': '데이터 부족',
                'reasoning': f"시장 상태 '{market_key}'에 대한 학습 데이터가 부족합니다."
            }

    def recommend_parameters(
        self,
        market_state: Dict
    ) -> Dict:
        """
        시장 상태에 따른 최적 파라미터 추천

        Args:
            market_state: 시장 상태 데이터

        Returns:
            파라미터 추천 {
                'rsi_range': {...},
                'volatility_threshold': float,
                'trend_strength': str
            }
        """
        if not self.trained:
            return {
                'rsi_range': None,
                'volatility_threshold': None,
                'trend_strength': None
            }

        price_action = market_state.get('price_action', {})
        market_key = self._get_market_key(price_action)

        param_rec = self.parameter_recommendations.get(market_key)
        if param_rec:
            return {
                'rsi_range': param_rec.get('optimal_rsi_range'),
                'volatility_threshold': None,  # 추후 추가
                'trend_strength': price_action.get('trend')
            }

        return {
            'rsi_range': None,
            'volatility_threshold': None,
            'trend_strength': price_action.get('trend')
        }

    def get_policy_summary(self) -> Dict:
        """
        정책 모델 요약 정보

        Returns:
            정책 모델 요약
        """
        if not self.trained:
            return {
                'trained': False,
                'message': '정책 모델이 학습되지 않았습니다.'
            }

        state_policies = self.policy_patterns.get('by_state', {})
        strategy_policies = self.policy_patterns.get('by_strategy', {})

        # 최고 성과 시장 상태
        best_states = sorted(
            state_policies.items(),
            key=lambda x: x[1].get('win_rate', 0) * x[1].get('avg_pnl_percent', 0),
            reverse=True
        )[:5]

        # 최고 성과 전략
        best_strategies = sorted(
            strategy_policies.items(),
            key=lambda x: x[1].get('win_rate', 0) * x[1].get('avg_pnl_percent', 0),
            reverse=True
        )[:5]

        return {
            'trained': True,
            'state_patterns': len(state_policies),
            'strategy_patterns': len(strategy_policies),
            'best_states': [
                {
                    'market_key': key,
                    'win_rate': policy.get('win_rate', 0),
                    'avg_pnl_percent': policy.get('avg_pnl_percent', 0),
                    'recommendation': policy.get('recommendation', '')
                }
                for key, policy in best_states
            ],
            'best_strategies': [
                {
                    'strategy': key,
                    'win_rate': policy.get('win_rate', 0),
                    'avg_pnl_percent': policy.get('avg_pnl_percent', 0),
                    'recommendation': policy.get('recommendation', '')
                }
                for key, policy in best_strategies
            ]
        }
