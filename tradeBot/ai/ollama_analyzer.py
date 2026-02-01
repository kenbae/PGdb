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
        
        # 전략명 추출 (metadata에서)
        strategy_name = signal.get('strategy', signal.get('strategy_name', ''))

        # 전략별 분석 가이드
        strategy_guide = self._get_strategy_guide(strategy_name, confluence)

        # 프롬프트
        prompt = f"""당신은 암호화폐 선물 전문 트레이더입니다. 다음 매매 신호를 분석하고 평가해주세요.

**신호 정보:**
- 심볼: {symbol}
- 방향: {signal_type.upper()}
- 진입가: ${entry:,.6f}
- 손절가: ${stop:,.6f}
- 목표가: ${target1:,.6f}
- 신뢰도: {confidence:.1%}
- R/R 비율: {risk_reward:.2f}
- 신호 근거: {', '.join(confluence)}

**시장 상황:**
- 현재가: ${current_price:,.6f}
- 추세: {trend}
- 변동성: {volatility}

**전략 분석 가이드:**
{strategy_guide}

**추가 정보:**
{additional_context or '없음'}

다음 형식으로 JSON 응답을 제공하세요:

{{
    "decision": "approve/reject/uncertain",
    "confidence": 0.0-1.0,
    "reasoning": "판단 근거를 3-5문장으로",
    "risk_assessment": "리스크 평가 (low/medium/high)",
    "market_context": "현재 시장 상황 분석",
    "adjustments": {{
        "entry": null or 조정된 진입가 (숫자만),
        "stop": null or 조정된 손절가 (숫자만),
        "target": null or 조정된 목표가 (숫자만)
    }}
}}

**중요:**
- JSON만 출력하세요 (다른 텍스트 없이)
- 숫자에 $ 기호를 붙이지 마세요 (예: 0.81, NOT $0.81)
- 코드 블록(```)을 사용하지 마세요
- 유효한 JSON 형식만 사용하세요
- 실제 돈이 걸린 상황이므로 신중하게 평가하세요
"""

        return prompt

    def _get_strategy_guide(self, strategy_name: str, confluence: List[str]) -> str:
        """
        전략별 AI 분석 가이드 반환

        Args:
            strategy_name: 전략 이름
            confluence: 신호 근거 목록

        Returns:
            전략별 분석 가이드 문자열
        """
        # confluence에서 전략명 추론 (strategy_name이 없는 경우)
        confluence_str = ' '.join(confluence).lower()

        if strategy_name == 'bb_adaptive_rsi' or 'bb 3σ' in confluence_str or 'adaptive rsi' in confluence_str:
            return """이 신호는 **BB 3σ + Adaptive RSI** 역추세(평균회귀) 전략입니다.
- 볼린저 밴드 3σ 극단 돌파: 통계적으로 드문 가격 이탈, 평균 회귀 가능성 높음
- Adaptive RSI: Kaufman ER 기반으로 시장 효율성에 따라 RSI 민감도 조정
- 횡보장(ER 낮음)에서 더 효과적, 강한 추세(ER 높음)에서는 주의
- 확인 사항: RSI 과매수/과매도 수준, RSI 시그널 크로스 여부, 캔들 패턴(양봉/음봉)
- 리스크: 강한 추세에서는 평균 회귀 실패 가능 → 손절 엄수"""

        elif strategy_name == 'ict' or 'order block' in confluence_str or 'fvg' in confluence_str:
            return """이 신호는 **ICT (Inner Circle Trader)** 전략입니다.
- Order Block: 기관 주문이 집중된 가격대, 지지/저항 역할
- Fair Value Gap (FVG): 유동성 불균형 영역, 가격이 다시 채우러 오는 경향
- Liquidity Sweep: 스탑헌팅 후 반전 가능성
- 확인 사항: 구조적 고점/저점, 유동성 영역, 시장 구조 변화
- 리스크: 거짓 돌파, 유동성 사냥에 당할 가능성"""

        elif strategy_name == 'ema_cross' or 'ema' in confluence_str:
            return """이 신호는 **EMA 크로스오버** 추세추종 전략입니다.
- EMA 크로스: 단기 EMA가 장기 EMA를 교차할 때 진입
- HTF 정렬: 상위 타임프레임과 방향 일치 시 신뢰도 상승
- 확인 사항: 정배열/역배열 상태, RSI 확인, 거래량 동반 여부
- 리스크: 횡보장에서 휩소 발생 가능"""

        elif strategy_name == 'rsi' or 'rsi 과매' in confluence_str:
            return """이 신호는 **RSI** 과매수/과매도 전략입니다.
- RSI 과매도(<30): 매수 기회, 과매수(>70): 매도 기회
- 추세 필터: EMA 추세와 방향 일치 여부 확인
- 확인 사항: 다이버전스 여부, 지지/저항선 근처인지
- 리스크: 강한 추세에서 과매수/과매도 지속 가능"""

        elif strategy_name == 'bollinger' or 'bollinger' in confluence_str:
            return """이 신호는 **볼린저 밴드** 전략입니다.
- Breakout 모드: 밴드 돌파 시 추세 시작 신호
- Reversal 모드: 밴드 터치 후 반전 신호
- 확인 사항: 밴드 폭(스퀴즈 여부), 거래량 동반 여부
- 리스크: 거짓 돌파, 밴드 내 횡보"""

        elif strategy_name == 'keltner_ict_turtle' or 'keltner' in confluence_str or 'donchian' in confluence_str:
            return """이 신호는 **Keltner + ICT + Turtle** 복합 전략입니다.
- Keltner 채널: ATR 기반 변동성 밴드
- Donchian 채널: 기간 내 고점/저점 돌파
- ICT 요소: Order Block, FVG 확인
- 확인 사항: 채널 돌파, 거래량, ICT 구조
- 리스크: 다중 조건 충족 어려움"""

        elif strategy_name == 'pattern_rag' or 'pattern' in confluence_str:
            return """이 신호는 **Pattern RAG** AI 패턴 매칭 전략입니다.
- 과거 유사 패턴 학습 및 코사인 유사도 매칭
- 확인 사항: 유사 패턴 수, 과거 패턴의 결과(승률)
- 리스크: 과거가 미래를 보장하지 않음"""

        else:
            return """일반적인 기술적 분석 신호입니다.
- 신호 근거(confluence)를 기반으로 진입 타당성 평가
- 손절가와 목표가의 R/R 비율 확인
- 현재 시장 상황과 변동성 고려
- 리스크: 예상치 못한 시장 변동"""

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
