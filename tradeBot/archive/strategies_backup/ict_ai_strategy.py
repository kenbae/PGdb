"""
ICT + AI 전략 (호환성 래퍼)

NOTE: 이 파일은 구형입니다. 새로운 코드에서는 ict_strategy.py를 사용하세요.
이 파일은 기존 코드와의 호환성을 위해 유지됩니다.
"""

import pandas as pd
from typing import List, Dict
import logging
import warnings

# 새로운 베이스 클래스 import
from strategies.base import BaseStrategy, TradeSignal
from indicators.order_blocks import OrderBlockDetector
from indicators.fair_value_gaps import FVGDetector
from ai.ollama_analyzer import OLLAMAAnalyzer

logger = logging.getLogger(__name__)

# 호환성 경고
warnings.warn(
    "ICTAIStrategy는 deprecated입니다. ICTStrategy를 사용하세요.",
    DeprecationWarning,
    stacklevel=2
)


class ICTAIStrategy(BaseStrategy):
    """
    ICT + AI 통합 전략
    
    Order Blocks + Fair Value Gaps + OLLAMA AI
    """
    
    def __init__(self, name: str, config: Dict):
        """
        초기화
        
        Args:
            name: 전략 이름
            config: 설정
                {
                    'enabled': True/False,
                    'ob_lookback': 20,
                    'fvg_min_gap_pct': 0.001,
                    'min_confidence': 0.65,
                    'min_risk_reward': 1.5,
                    'atr_multiplier': 2.0,
                    'use_ai': True,
                    'min_ai_confidence': 0.75,
                    'ollama_host': 'http://localhost:11434',
                    'ollama_model': 'qwen2.5:7b-instruct'
                }
        """
        super().__init__(name, config)
        
        # ICT 탐지기
        self.ob_detector = OrderBlockDetector(
            lookback=config.get('ob_lookback', 20)
        )
        
        self.fvg_detector = FVGDetector(
            min_gap_pct=config.get('fvg_min_gap_pct', 0.001)
        )
        
        # AI 분석기 (선택적)
        self.use_ai = config.get('use_ai', True)
        if self.use_ai:
            self.ai_analyzer = OLLAMAAnalyzer(
                host=config.get('ollama_host', 'http://localhost:11434'),
                model=config.get('ollama_model', 'qwen2.5:7b-instruct')
            )
            self.min_ai_confidence = config.get('min_ai_confidence', 0.75)
        else:
            self.ai_analyzer = None
        
        # 임계값
        self.min_confidence = config.get('min_confidence', 0.65)
        self.min_risk_reward = config.get('min_risk_reward', 1.5)
        self.atr_multiplier = config.get('atr_multiplier', 2.0)
        
        logger.info(f"✅ ICTAIStrategy 초기화")
        logger.info(f"   AI 사용: {self.use_ai}")
    
    def analyze(
        self, 
        symbol: str, 
        timeframe: str, 
        df: pd.DataFrame
    ) -> List[TradeSignal]:
        """
        ICT + AI 분석
        
        Args:
            symbol: 심볼
            timeframe: 타임프레임
            df: OHLCV 데이터
        
        Returns:
            신호 리스트
        """
        if len(df) < 50:
            logger.warning(f"⚠️  데이터 부족: {symbol} {timeframe}")
            return []
        
        signals = []
        
        try:
            # 1. Order Blocks
            obs = self.ob_detector.detect(df)
            obs = self.ob_detector.check_touches(obs, df)
            active_obs = self.ob_detector.filter_active(obs)
            
            # 2. Fair Value Gaps
            fvgs = self.fvg_detector.detect(df)
            fvgs = self.fvg_detector.update_fills(fvgs, df)
            active_fvgs = self.fvg_detector.filter_active(fvgs)
            
            logger.info(f"📊 {symbol} {timeframe}: OB {len(active_obs)}개, FVG {len(active_fvgs)}개")
            
            # 3. 현재가 및 ATR
            current_price = float(df.iloc[-1]['close'])
            atr = self._calculate_atr(df)
            
            # 4. 매수 신호
            buy_signal = self._check_buy_signal(
                symbol, timeframe, current_price, 
                active_obs, active_fvgs, atr
            )
            if buy_signal:
                signals.append(buy_signal)
            
            # 5. 매도 신호
            sell_signal = self._check_sell_signal(
                symbol, timeframe, current_price, 
                active_obs, active_fvgs, atr
            )
            if sell_signal:
                signals.append(sell_signal)
            
            # 6. AI 검증 (선택적)
            if self.use_ai and signals:
                signals = self._ai_filter(signals, df)
        
        except Exception as e:
            logger.error(f"❌ {symbol} {timeframe} 분석 실패: {e}")
        
        return signals
    
    def _check_buy_signal(
        self, 
        symbol: str, 
        timeframe: str,
        current_price: float, 
        obs: List, 
        fvgs: List, 
        atr: float
    ) -> TradeSignal:
        """매수 신호 확인"""
        # Bullish OB/FVG
        bullish_obs = [ob for ob in obs if ob.type == 'bullish']
        bullish_fvgs = [fvg for fvg in fvgs if fvg.type == 'bullish']
        
        if not bullish_obs and not bullish_fvgs:
            return None
        
        # 가장 가까운 것
        nearest_ob = self.ob_detector.get_nearest(bullish_obs, current_price, 'bullish') if bullish_obs else None
        nearest_fvg = self.fvg_detector.get_nearest(bullish_fvgs, current_price, 'bullish') if bullish_fvgs else None
        
        # Confluence 확인
        confluence = []
        support_level = None
        
        if nearest_ob:
            distance_pct = abs(current_price - nearest_ob.bottom) / current_price
            if distance_pct < 0.01:  # 1% 이내
                confluence.append('bullish_ob')
                support_level = nearest_ob.bottom
        
        if nearest_fvg:
            fvg_center = (nearest_fvg.top + nearest_fvg.bottom) / 2
            distance_pct = abs(current_price - fvg_center) / current_price
            if distance_pct < 0.01:
                confluence.append('bullish_fvg')
                if support_level:
                    confluence.append('confluence')
                else:
                    support_level = fvg_center
        
        if not confluence:
            return None
        
        # 진입/손절/익절
        entry = current_price
        stop_loss = (support_level if support_level else current_price) - (atr * self.atr_multiplier)
        
        risk = entry - stop_loss
        tp1 = entry + (risk * 1.618)
        tp2 = entry + (risk * 2.618)
        
        # 신뢰도
        confidence = self._calculate_confidence(confluence, nearest_ob, nearest_fvg)
        
        # R/R
        risk_reward = (tp1 - entry) / risk if risk > 0 else 0
        
        # 필터링
        if confidence < self.min_confidence or risk_reward < self.min_risk_reward:
            return None
        
        # 신호 생성
        signal = TradeSignal(
            strategy_name=self.name,
            symbol=symbol,
            timeframe=timeframe,
            signal_type='buy',
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            confidence=confidence,
            risk_reward=risk_reward,
            reasons=confluence,
            metadata={
                'order_block': nearest_ob.__dict__ if nearest_ob else None,
                'fvg': nearest_fvg.__dict__ if nearest_fvg else None
            }
        )
        
        logger.info(f"💚 매수 신호: {symbol} @ ${entry:,.2f} (신뢰도: {confidence:.1%})")
        
        return signal
    
    def _check_sell_signal(
        self, 
        symbol: str, 
        timeframe: str,
        current_price: float, 
        obs: List, 
        fvgs: List, 
        atr: float
    ) -> TradeSignal:
        """매도 신호 확인"""
        # Bearish OB/FVG
        bearish_obs = [ob for ob in obs if ob.type == 'bearish']
        bearish_fvgs = [fvg for fvg in fvgs if fvg.type == 'bearish']
        
        if not bearish_obs and not bearish_fvgs:
            return None
        
        nearest_ob = self.ob_detector.get_nearest(bearish_obs, current_price, 'bearish') if bearish_obs else None
        nearest_fvg = self.fvg_detector.get_nearest(bearish_fvgs, current_price, 'bearish') if bearish_fvgs else None
        
        confluence = []
        resistance_level = None
        
        if nearest_ob:
            distance_pct = abs(current_price - nearest_ob.top) / current_price
            if distance_pct < 0.01:
                confluence.append('bearish_ob')
                resistance_level = nearest_ob.top
        
        if nearest_fvg:
            fvg_center = (nearest_fvg.top + nearest_fvg.bottom) / 2
            distance_pct = abs(current_price - fvg_center) / current_price
            if distance_pct < 0.01:
                confluence.append('bearish_fvg')
                if resistance_level:
                    confluence.append('confluence')
                else:
                    resistance_level = fvg_center
        
        if not confluence:
            return None
        
        entry = current_price
        stop_loss = (resistance_level if resistance_level else current_price) + (atr * self.atr_multiplier)
        
        risk = stop_loss - entry
        tp1 = entry - (risk * 1.618)
        tp2 = entry - (risk * 2.618)
        
        confidence = self._calculate_confidence(confluence, nearest_ob, nearest_fvg)
        risk_reward = (entry - tp1) / risk if risk > 0 else 0
        
        if confidence < self.min_confidence or risk_reward < self.min_risk_reward:
            return None
        
        signal = TradeSignal(
            strategy_name=self.name,
            symbol=symbol,
            timeframe=timeframe,
            signal_type='sell',
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            confidence=confidence,
            risk_reward=risk_reward,
            reasons=confluence,
            metadata={
                'order_block': nearest_ob.__dict__ if nearest_ob else None,
                'fvg': nearest_fvg.__dict__ if nearest_fvg else None
            }
        )
        
        logger.info(f"❤️  매도 신호: {symbol} @ ${entry:,.2f} (신뢰도: {confidence:.1%})")
        
        return signal
    
    def _ai_filter(self, signals: List[TradeSignal], df: pd.DataFrame) -> List[TradeSignal]:
        """AI 검증 필터"""
        if not self.ai_analyzer:
            return signals
        
        results = []  # approved → results로 변경
        
        for signal in signals:
            # 시장 데이터
            market_data = {
                'current_price': signal.entry_price,
                'trend': '상승' if df.iloc[-1]['close'] > df.iloc[-20]['close'] else '하락',
                'volatility': '중간'
            }
            
            # 신호 딕셔너리
            signal_dict = {
                'signal_type': signal.signal_type,
                'symbol': signal.symbol,
                'entry_price': signal.entry_price,
                'stop_loss': signal.stop_loss,
                'take_profit_1': signal.take_profit_1,
                'confidence': signal.confidence,
                'risk_reward': signal.risk_reward,
                'confluence': signal.reasons
            }
            
            # AI 분석
            ai_analysis = self.ai_analyzer.analyze_signal(signal_dict, market_data)
            
            # AI 메타데이터 추가 (승인/거부 모두)
            signal.metadata['ai_analysis'] = {
                'decision': ai_analysis.decision,
                'confidence': ai_analysis.confidence,
                'reasoning': ai_analysis.reasoning,
                'risk_assessment': ai_analysis.risk_assessment,
                'market_context': ai_analysis.market_context,
                'rejected': not (ai_analysis.decision == 'approve' and ai_analysis.confidence >= self.min_ai_confidence)
            }
            
            # 승인 여부 로그
            if ai_analysis.decision == 'approve' and ai_analysis.confidence >= self.min_ai_confidence:
                logger.info(f"✅ AI 승인: {signal.symbol} (AI 신뢰도: {ai_analysis.confidence:.1%})")
            else:
                logger.info(f"❌ AI 거부: {signal.symbol} ({ai_analysis.decision}, {ai_analysis.confidence:.1%})")
                logger.info(f"   이유: {ai_analysis.reasoning}")
            
            # 모든 신호를 반환 (거부된 것도 포함)
            results.append(signal)
        
        return results
    
    def _calculate_confidence(self, confluence: List[str], ob, fvg) -> float:
        """신뢰도 계산"""
        confidence = 0.5
        
        if 'confluence' in confluence:
            confidence += 0.4
        elif len(confluence) == 1:
            confidence += 0.2
        
        if ob:
            confidence += ob.strength * 0.3
        
        if fvg:
            confidence += fvg.strength * 0.3
        
        return min(round(confidence, 3), 1.0)
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """ATR 계산"""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        
        return float(atr)
    
    def get_required_data(self) -> Dict:
        """필요한 데이터 (호환성 유지)"""
        return {
            'min_candles': 100,
            'timeframes': [self.config.get('timeframe', '15m')],
            'indicators': ['atr']
        }

    def get_required_indicators(self) -> List[str]:
        """필요한 지표 목록 (새 인터페이스)"""
        return ['atr']
