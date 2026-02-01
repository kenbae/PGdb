"""
AI 연결 테스트

OLLAMA가 제대로 작동하는지 확인
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import logging
from core.config_loader import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_ollama_connection():
    """OLLAMA 연결 테스트"""
    print("=" * 70)
    print("OLLAMA 연결 테스트")
    print("=" * 70)
    print()
    
    # Config
    config = get_config()
    host = config.get('ollama.host', 'http://localhost:11434')
    model = config.get('ollama.llm_model', 'qwen2.5:7b-instruct')
    
    print(f"호스트: {host}")
    print(f"모델: {model}")
    print()
    
    # 1. 서버 연결 확인
    print("1. 서버 연결 확인...")
    try:
        response = requests.get(f"{host}/api/tags", timeout=5)
        if response.status_code == 200:
            print("   ✅ OLLAMA 서버 연결 성공")
            
            # 모델 목록
            data = response.json()
            models = [m['name'] for m in data.get('models', [])]
            print(f"   설치된 모델: {models}")
            
            if model in models or model.split(':')[0] in [m.split(':')[0] for m in models]:
                print(f"   ✅ {model} 모델 사용 가능")
            else:
                print(f"   ⚠️  {model} 모델이 없습니다")
                print(f"   설치 명령: ollama pull {model}")
        else:
            print(f"   ❌ 연결 실패: {response.status_code}")
            return False
    except Exception as e:
        print(f"   ❌ 연결 실패: {e}")
        print()
        print("OLLAMA가 실행 중인지 확인하세요:")
        print("  1. OLLAMA 앱이 실행 중인가?")
        print("  2. http://localhost:11434 접속 가능한가?")
        return False
    
    print()
    
    # 2. 간단한 테스트 요청
    print("2. AI 응답 테스트...")
    try:
        test_prompt = "Hello, are you working?"
        
        payload = {
            "model": model,
            "prompt": test_prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 50
            }
        }
        
        response = requests.post(
            f"{host}/api/generate",
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            ai_response = data.get('response', '')
            print(f"   ✅ AI 응답 성공")
            print(f"   응답: {ai_response[:100]}...")
        else:
            print(f"   ❌ 응답 실패: {response.status_code}")
            return False
    
    except Exception as e:
        print(f"   ❌ 응답 실패: {e}")
        return False
    
    print()
    
    # 3. 트레이딩 신호 분석 테스트
    print("3. 트레이딩 신호 분석 테스트...")
    try:
        trading_prompt = """당신은 전문 트레이더입니다. 다음 신호를 분석하고 평가해주세요.

**신호:**
- 심볼: BTCUSDT
- 방향: BUY
- 진입가: $92,750
- 손절가: $92,450
- 목표가: $93,235
- 신뢰도: 78.5%
- R/R 비율: 1.62

JSON 형식으로만 응답하세요:
{
    "decision": "approve/reject/uncertain",
    "confidence": 0.0-1.0,
    "reasoning": "판단 근거"
}
"""
        
        payload = {
            "model": model,
            "prompt": trading_prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 200
            }
        }
        
        print("   요청 중...")
        response = requests.post(
            f"{host}/api/generate",
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            ai_response = data.get('response', '')
            print(f"   ✅ 분석 성공")
            print()
            print("   AI 응답:")
            print("-" * 70)
            print(ai_response)
            print("-" * 70)
        else:
            print(f"   ❌ 분석 실패: {response.status_code}")
            return False
    
    except Exception as e:
        print(f"   ❌ 분석 실패: {e}")
        return False
    
    print()
    print("=" * 70)
    print("✅ 모든 테스트 통과!")
    print("=" * 70)
    
    return True


if __name__ == "__main__":
    success = test_ollama_connection()
    
    if not success:
        print()
        print("🔧 해결 방법:")
        print()
        print("1. OLLAMA 앱 실행 확인")
        print("   - Windows: OLLAMA 앱이 트레이에 있는지 확인")
        print("   - 또는: 명령 프롬프트에서 'ollama serve' 실행")
        print()
        print("2. 모델 설치 확인")
        print("   - ollama list")
        print("   - ollama pull qwen2.5:7b-instruct")
        print()
        print("3. 포트 확인")
        print("   - http://localhost:11434 접속 가능한지")
        print()
