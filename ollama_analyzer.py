"""
OLLAMA AI 분석기

ICT 신호를 AI로 검증 및 강화
"""

import requests
import json
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class AIAnalysis:
    """AI 분석 결과"""
    decision: str  # 'approve', 'reject', 'uncertain'
    confidence: float  # AI 신뢰도 (0-1)
    reasoning: str  # AI의 판단 근거
    risk_assessment: str  # 리스크 평가
    market_context: str  # 시장 상황 분석
    
    # 조정
    adjusted_entry: Optional[float] = None
    adjusted_stop: Optional[float] = None
    adjusted_target: Optional[float] = None
    
    # 메타
    model: str = 'qwen2.5:7b-instruct'
    analyzed_at: datetime = None


class OLLAMAAnalyzer:
    """
    OLLAMA AI 분석기
    
    Features:
    - ICT 신호 검증
    - 시장 컨텍스트 분석
    - 리스크 평가
    - 진입/손절/익절 조정
    """
    
    def __init__(
        self,
        host: str = 'http://localhost:11434',
        model: str = 'qwen2.5:7b-instruct',
        temperature: float = 0.3,
        timeout: int = 30
    ):
        """
        초기화
        
        Args:
            host: OLLAMA 서버 주소
            model: 모델 이름
            temperature: 창의성 (0-1, 낮을수록 일관적)
            timeout: 타임아웃 (초)
        """
        self.host = host
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        
        # 연결 테스트
        if not self._test_connection():
            logger.warning(f"⚠️  OLLAMA 연결 실패: {host}")
        else:
            logger.info(f"✅ OLLAMA 연결: {host} ({model})")
    
    def _test_connection(self) -> bool:
        """연결 테스트"""
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"❌ OLLAMA 연결 실패: {e}")
            return False
    
    def analyze_signal(
        self,
        signal: Dict,
        market_data: Dict,
        additional_context: Optional[str] = None
    ) -> AIAnalysis:
        """
        ICT 신호 분석
        
        Args:
            signal: ICT 신호 정보
            market_data: 시장 데이터 (최근 캔들 등)
            additional_context: 추가 컨텍스트
        
        Returns:
            AI 분석 결과
        """
        # 프롬프트 생성
        prompt = self._create_prompt(signal, market_data, additional_context)
        
        # AI 호출
        response = self._call_ollama(prompt)
        
        if not response:
            # AI 실패 시 기본 분석
            return AIAnalysis(
                decision='uncertain',
                confidence=0.5,
                reasoning='AI 분석 실패',
                risk_assessment='알 수 없음',
                market_context='분석 불가',
                model=self.model,
                analyzed_at=datetime.now()
            )
        
        # 응답 파싱
        analysis = self._parse_response(response)
        
        return analysis
    
    def _create_prompt(
        self,
        signal: Dict,
        market_data: Dict,
        additional_context: Optional[str]
    ) -> str:
        """
        분석 프롬프트 생성
        
        Args:
            signal: ICT 신호
            market_data: 시장 데이터
            additional_context: 추가 컨텍스트
        
        Returns:
            프롬프트 문자열
        """
        # 신호 정보
        signal_type = signal.get('signal_type', 'unknown')
        symbol = signal.get('symbol', 'UNKNOWN')
        entry = signal.get('entry_price', 0)
        stop = signal.get('stop_loss', 0)
        target1 = signal.get('take_profit_1', 0)
        confidence = signal.get('confidence', 0)
        risk_reward = signal.get('risk_reward', 0)
        confluence = signal.get('confluence', [])
        
        # 시장 데이터
        current_price = market_data.get('current_price', entry)
        trend = market_data.get('trend', '알 수 없음')
        volatility = market_data.get('volatility', '보통')
        
        # 프롬프트
        prompt = f"""You are a professional trader. Analyze and evaluate the following ICT signal.

**CRITICAL: You MUST respond ONLY in English. Do NOT use Korean, Chinese, or any other language.**

**Signal Information:**
- Symbol: {symbol}
- Direction: {signal_type.upper()}
- Entry Price: ${entry:,.2f}
- Stop Loss: ${stop:,.2f}
- Take Profit: ${target1:,.2f}
- ICT Confidence: {confidence:.1%}
- R/R Ratio: {risk_reward:.2f}
- Confluence: {', '.join(confluence)}

**Market Situation:**
- Current Price: ${current_price:,.2f}
- Trend: {trend}
- Volatility: {volatility}

**Additional Information:**
{additional_context or 'None'}

Provide a JSON response in the following format:

{{
    "decision": "approve/reject/uncertain",
    "confidence": 0.0-1.0,
    "reasoning": "Your reasoning in 3-5 sentences IN ENGLISH ONLY",
    "risk_assessment": "Risk assessment (low/medium/high)",
    "market_context": "Current market situation analysis IN ENGLISH ONLY",
    "adjustments": {{
        "entry": null or adjusted entry price (number only),
        "stop": null or adjusted stop loss (number only),
        "target": null or adjusted target price (number only)
    }}
}}

**IMPORTANT:**
- Output ONLY valid JSON (no other text, no code blocks, no markdown)
- Do NOT use $ symbol in numbers (e.g., 0.81, NOT $0.81)
- Do NOT use code blocks (```)
- Use ONLY valid JSON format
- Write ALL text in ENGLISH ONLY (no Korean, Chinese, or other languages)
- Consider ICT concepts (Order Blocks, Fair Value Gaps)
- Real money is at stake - evaluate carefully
"""
        
        return prompt
    
    def _call_ollama(self, prompt: str) -> Optional[str]:
        """
        OLLAMA API 호출
        
        Args:
            prompt: 프롬프트
        
        Returns:
            응답 텍스트
        """
        try:
            url = f"{self.host}/api/generate"
            
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": 500
                }
            }
            
            logger.debug(f"🤖 OLLAMA 요청 중...")
            
            response = requests.post(
                url,
                json=payload,
                timeout=self.timeout
            )
            
            if response.status_code != 200:
                logger.error(f"❌ OLLAMA 에러: {response.status_code}")
                return None
            
            data = response.json()
            text = data.get('response', '')
            
            logger.debug(f"✅ OLLAMA 응답: {len(text)}자")
            
            return text
        
        except Exception as e:
            logger.error(f"❌ OLLAMA 호출 실패: {e}")
            return None
    
    def _parse_response(self, response: str) -> AIAnalysis:
        """
        AI 응답 파싱
        
        Args:
            response: AI 응답
        
        Returns:
            AIAnalysis 객체
        """
        try:
            # 1차: JSON 추출 (앞뒤 텍스트 제거)
            start = response.find('{')
            end = response.rfind('}') + 1
            
            if start == -1 or end == 0:
                raise ValueError("JSON not found in response")
            
            json_str = response[start:end]
            
            # 2차: 코드 블록 제거 (```json ... ```)
            json_str = json_str.replace('```json', '').replace('```', '')
            
            # 3차: $ 기호 제거 (숫자 앞의 $ 제거)
            import re
            json_str = re.sub(r'\$\s*(\d+\.?\d*)', r'\1', json_str)
            
            # 4차: 줄바꿈 및 공백 정리
            json_str = json_str.strip()
            
            # 5차: JSON 파싱
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError as je:
                # JSON 에러 시 로그 출력
                logger.error(f"❌ JSON 파싱 에러: {je}")
                logger.error(f"문제 JSON: {json_str[:500]}")
                
                # 수동으로 추출 시도
                data = self._manual_parse(json_str)
                if not data:
                    raise je
            
            # AIAnalysis 생성
            analysis = AIAnalysis(
                decision=str(data.get('decision', 'uncertain')).lower(),
                confidence=float(data.get('confidence', 0.5)),
                reasoning=str(data.get('reasoning', '파싱 오류로 인한 불확실')),
                risk_assessment=str(data.get('risk_assessment', 'medium')).lower(),
                market_context=str(data.get('market_context', '분석 불가')),
                adjusted_entry=data.get('adjustments', {}).get('entry') if isinstance(data.get('adjustments'), dict) else None,
                adjusted_stop=data.get('adjustments', {}).get('stop') if isinstance(data.get('adjustments'), dict) else None,
                adjusted_target=data.get('adjustments', {}).get('target') if isinstance(data.get('adjustments'), dict) else None,
                model=self.model,
                analyzed_at=datetime.now()
            )
            
            logger.info(f"🤖 AI 분석: {analysis.decision.upper()} (신뢰도: {analysis.confidence:.1%})")
            
            return analysis
        
        except Exception as e:
            logger.error(f"❌ 응답 파싱 실패: {e}")
            logger.debug(f"응답: {response[:500]}...")
            
            # 기본 분석
            return AIAnalysis(
                decision='uncertain',
                confidence=0.5,
                reasoning=f'응답 파싱 실패: {str(e)}',
                risk_assessment='unknown',
                market_context='분석 실패',
                model=self.model,
                analyzed_at=datetime.now()
            )
    
    def _manual_parse(self, text: str) -> Optional[Dict]:
        """
        수동 파싱 (JSON 파싱 실패 시 백업)
        
        Args:
            text: 파싱할 텍스트
        
        Returns:
            파싱된 딕셔너리 또는 None
        """
        try:
            import re
            
            result = {}
            
            # decision 추출
            decision_match = re.search(r'"decision"\s*:\s*"(\w+)"', text)
            if decision_match:
                result['decision'] = decision_match.group(1)
            
            # confidence 추출
            conf_match = re.search(r'"confidence"\s*:\s*([\d.]+)', text)
            if conf_match:
                result['confidence'] = float(conf_match.group(1))
            
            # reasoning 추출
            reason_match = re.search(r'"reasoning"\s*:\s*"([^"]+)"', text)
            if reason_match:
                result['reasoning'] = reason_match.group(1)
            
            # risk_assessment 추출
            risk_match = re.search(r'"risk_assessment"\s*:\s*"(\w+)"', text)
            if risk_match:
                result['risk_assessment'] = risk_match.group(1)
            
            # market_context 추출
            market_match = re.search(r'"market_context"\s*:\s*"([^"]+)"', text)
            if market_match:
                result['market_context'] = market_match.group(1)
            
            logger.warning(f"⚠️  수동 파싱 사용: {result}")
            
            return result if result else None
        
        except Exception as e:
            logger.error(f"❌ 수동 파싱 실패: {e}")
            return None
    
    def batch_analyze(
        self,
        signals: List[Dict],
        market_data: Dict
    ) -> List[AIAnalysis]:
        """
        여러 신호 일괄 분석
        
        Args:
            signals: 신호 리스트
            market_data: 시장 데이터
        
        Returns:
            분석 결과 리스트
        """
        results = []
        
        for signal in signals:
            analysis = self.analyze_signal(signal, market_data)
            results.append(analysis)
        
        return results


# 테스트
if __name__ == "__main__":
    import sys
    import os
    
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from core.config_loader import get_config
    
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 70)
    print("OLLAMA AI 분석 테스트")
    print("=" * 70)
    print()
    
    # Config
    config = get_config()
    
    # OLLAMA
    analyzer = OLLAMAAnalyzer(
        host=config.get('ollama.host', 'http://localhost:11434'),
        model=config.get('ollama.llm_model', 'qwen2.5:7b-instruct')
    )
    
    # 테스트 신호
    test_signal = {
        'signal_type': 'buy',
        'symbol': 'BTCUSDT',
        'entry_price': 92750.0,
        'stop_loss': 92450.0,
        'take_profit_1': 93235.0,
        'confidence': 0.785,
        'risk_reward': 2.15,
        'confluence': ['bullish_ob', 'bullish_fvg', 'confluence']
    }
    
    # 시장 데이터
    test_market = {
        'current_price': 92750.0,
        'trend': '상승 추세',
        'volatility': '중간'
    }
    
    print("테스트 신호:")
    print(f"  방향: {test_signal['signal_type'].upper()}")
    print(f"  진입: ${test_signal['entry_price']:,.2f}")
    print(f"  손절: ${test_signal['stop_loss']:,.2f}")
    print(f"  목표: ${test_signal['take_profit_1']:,.2f}")
    print(f"  신뢰도: {test_signal['confidence']:.1%}")
    print(f"  R/R: {test_signal['risk_reward']:.2f}")
    print()
    
    print("AI 분석 중...")
    print()
    
    # 분석
    analysis = analyzer.analyze_signal(test_signal, test_market)
    
    print("=" * 70)
    print("AI 분석 결과")
    print("=" * 70)
    print()
    print(f"결정: {analysis.decision.upper()}")
    print(f"신뢰도: {analysis.confidence:.1%}")
    print(f"리스크: {analysis.risk_assessment}")
    print()
    print(f"근거:")
    print(f"  {analysis.reasoning}")
    print()
    print(f"시장 분석:")
    print(f"  {analysis.market_context}")
    print()
    
    if analysis.adjusted_entry:
        print(f"조정:")
        print(f"  진입: ${analysis.adjusted_entry:,.2f}")
        print(f"  손절: ${analysis.adjusted_stop:,.2f}")
        print(f"  목표: ${analysis.adjusted_target:,.2f}")
