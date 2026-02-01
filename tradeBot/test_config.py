import sys
sys.path.append('.')

from core.config_loader import get_config

print("=" * 60)
print("Config 테스트")
print("=" * 60)
print()

# Config 로드
config = get_config()

# 검증
if config.validate():
    print("\n✅ 설정 유효!")
    
    # 요약
    config.print_summary()
    
    # 주요 값 확인
    print("\n" + "=" * 60)
    print("주요 설정 확인")
    print("=" * 60)
    print(f"DB Host: {config.get('db.host')}")
    print(f"DB Name: {config.get('db.name')}")
    print(f"Initial Capital: ${config.get('trading.initial_capital', 0):.2f}")
    print(f"Max Positions: {config.get('trading.max_positions', 0)}")
    print(f"OLLAMA Host: {config.get('ollama.host')}")
    
    # API 키 확인
    api_key = config.get('api.binance.live.api_key')
    if api_key:
        print(f"API Key: {api_key[:8]}...✅")
    else:
        print(f"API Key: ❌ 미설정")
    
else:
    print("\n❌ 설정 오류!")