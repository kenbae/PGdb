"""
Config Loader (경로 수정 버전)

PGdb/config.yaml
PGdb/tradeBot/core/config_loader.py

경로 구조:
PGdb/
├── config.yaml              ← 여기
├── tradeBot/
│   ├── core/
│   │   └── config_loader.py ← 여기
│   ├── exchanges/
│   └── strategies/
"""

import yaml
import os
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv


class Config:
    """
    설정 관리 클래스
    
    Usage:
        config = Config()
        db_host = config.get('db.host')
        api_key = config.get('api.binance.live.api_key')
    """
    
    def __init__(self, config_path: str = None):
        """
        초기화
        
        Args:
            config_path: config.yaml 경로 (None이면 자동 탐색)
        """
        # .env 로드 (tradeBot 폴더 또는 PGdb 폴더)
        self._load_env_files()
        
        # config.yaml 경로 찾기
        if config_path is None:
            config_path = self._find_config_file()
        
        self.config_path = config_path
        
        # config.yaml 로드
        self.config = self._load_yaml(config_path)

        # .env 값으로 오버라이드
        self._override_from_env()

        print(f"[OK] Config loaded: {config_path}")
    
    def _load_env_files(self):
        """
        .env 파일 로드
        
        우선순위:
        1. tradeBot/.env
        2. PGdb/.env
        """
        possible_env_paths = [
            ".env",                    # 현재 디렉토리 (tradeBot)
            "../.env",                 # 상위 디렉토리 (PGdb)
            "tradeBot/.env",           # PGdb/tradeBot/.env
        ]
        
        for env_path in possible_env_paths:
            if os.path.exists(env_path):
                load_dotenv(env_path)
                print(f"[OK] .env loaded: {env_path}")
                break
    
    def _find_config_file(self) -> str:
        """
        config.yaml 파일 찾기
        
        탐색 순서:
        1. PGdb/config.yaml (상위 폴더)
        2. tradeBot/config.yaml (현재 폴더)
        3. config/config.yaml (하위 폴더)
        
        Returns:
            config.yaml 경로
        """
        # 현재 스크립트 위치
        current_dir = Path(__file__).parent
        
        # 가능한 경로들
        possible_paths = [
            # PGdb/config.yaml (상위 폴더)
            current_dir.parent.parent / "config.yaml",
            
            # tradeBot/config.yaml (현재 폴더)
            current_dir.parent / "config.yaml",
            
            # config/config.yaml (하위 폴더)
            current_dir.parent / "config" / "config.yaml",
            
            # 절대 경로 시도
            Path("config.yaml"),
            Path("../config.yaml"),
            Path("../../config.yaml"),
        ]
        
        for path in possible_paths:
            if path.exists():
                return str(path.resolve())
        
        # 못 찾으면 기본값
        print("⚠️ config.yaml을 찾을 수 없습니다. 기본 경로 사용")
        return "config.yaml"
    
    def _load_yaml(self, path: str) -> Dict[str, Any]:
        """YAML 파일 로드"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            print(f"⚠️ {path} 파일을 찾을 수 없습니다.")
            return {}
        except Exception as e:
            print(f"❌ YAML 로드 실패: {e}")
            return {}
    
    def _override_from_env(self):
        """환경 변수로 설정 오버라이드"""
        # Trading Bot 설정 추가 (없으면)
        if 'trading' not in self.config:
            self.config['trading'] = {}
        
        # API 설정 추가 (없으면)
        if 'api' not in self.config:
            self.config['api'] = {}
        if 'binance' not in self.config['api']:
            self.config['api']['binance'] = {}
        if 'live' not in self.config['api']['binance']:
            self.config['api']['binance']['live'] = {}
        
        # Live API 키
        live_key = os.getenv('BINANCE_LIVE_API_KEY')
        live_secret = os.getenv('BINANCE_LIVE_API_SECRET')
        
        if live_key:
            self.config['api']['binance']['live']['api_key'] = live_key
        if live_secret:
            self.config['api']['binance']['live']['api_secret'] = live_secret
        
        # 환경 변수 매핑
        env_mappings = {
            'INITIAL_CAPITAL': ('trading', 'initial_capital', float),
            'MAX_POSITIONS': ('trading', 'max_positions', int),
            'MAX_LEVERAGE': ('trading', 'max_leverage', int),
            'RISK_PER_TRADE': ('trading', 'risk_per_trade', float),
            'MAX_DAILY_LOSS': ('trading', 'max_daily_loss', float),
            'OLLAMA_URL': ('ollama', 'host', str),
            'OLLAMA_MODEL': ('ollama', 'llm_model', str),
        }
        
        for env_key, (section, key, type_func) in env_mappings.items():
            value = os.getenv(env_key)
            if value:
                if section not in self.config:
                    self.config[section] = {}
                self.config[section][key] = type_func(value)
    
    def get(self, path: str, default: Any = None) -> Any:
        """
        중첩된 설정 값 가져오기
        
        Args:
            path: 점으로 구분된 경로 (예: 'db.host')
            default: 기본값
        
        Returns:
            설정 값 또는 기본값
        """
        keys = path.split('.')
        value = self.config
        
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        
        return value
    
    def get_section(self, section: str) -> Dict[str, Any]:
        """
        섹션 전체 가져오기
        
        Args:
            section: 섹션 이름 (예: 'db', 'trading')
        
        Returns:
            섹션 딕셔너리
        """
        return self.config.get(section, {})
    
    def set(self, path: str, value: Any):
        """
        설정 값 변경
        
        Args:
            path: 점으로 구분된 경로
            value: 새 값
        """
        keys = path.split('.')
        current = self.config
        
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        current[keys[-1]] = value
    
    def validate(self) -> bool:
        """
        필수 설정 검증
        
        Returns:
            유효하면 True
        """
        required_keys = [
            'db.host',
            'db.port',
            'db.name',
            'db.user',
        ]
        
        missing = []
        for key in required_keys:
            if self.get(key) is None:
                missing.append(key)
        
        if missing:
            print(f"❌ 필수 설정 누락: {', '.join(missing)}")
            return False
        
        print("✅ 설정 검증 완료")
        return True
    
    def print_summary(self):
        """설정 요약 출력"""
        print("\n" + "=" * 60)
        print("Configuration Summary")
        print("=" * 60)
        
        # Database
        print(f"\n📊 Database:")
        print(f"   Host: {self.get('db.host')}")
        print(f"   Port: {self.get('db.port')}")
        print(f"   Name: {self.get('db.name')}")
        print(f"   User: {self.get('db.user')}")
        
        # Trading
        print(f"\n💰 Trading:")
        print(f"   Capital: ${self.get('trading.initial_capital', 0):.2f}")
        print(f"   Max Positions: {self.get('trading.max_positions', 0)}")
        print(f"   Max Leverage: {self.get('trading.max_leverage', 0)}x")
        print(f"   Risk/Trade: {self.get('trading.risk_per_trade', 0)*100:.1f}%")
        print(f"   Daily Loss Limit: {self.get('trading.max_daily_loss', 0)*100:.1f}%")
        
        # OLLAMA
        print(f"\n🤖 OLLAMA:")
        print(f"   Host: {self.get('ollama.host')}")
        print(f"   Model: {self.get('ollama.llm_model')}")
        
        # Telegram
        print(f"\n📱 Telegram:")
        telegram_token = self.get('telegram.bot_token')
        if telegram_token:
            print(f"   Enabled: ✅ ({telegram_token[:10]}...)")
        else:
            print(f"   Enabled: ❌")
        
        # API Keys
        print(f"\n🔑 API Keys:")
        api_key = self.get('api.binance.live.api_key')
        if api_key:
            print(f"   Binance: ✅ ({api_key[:8]}...{api_key[-4:]})")
        else:
            print(f"   Binance: ❌ 미설정")
        
        print("\n" + "=" * 60)


# 싱글톤 인스턴스
_config_instance = None

def get_config() -> Config:
    """
    글로벌 Config 인스턴스 반환
    
    Returns:
        Config 인스턴스
    """
    global _config_instance
    
    if _config_instance is None:
        _config_instance = Config()
    
    return _config_instance


# 테스트
if __name__ == "__main__":
    print("=" * 60)
    print("Config Loader 테스트")
    print("=" * 60)
    print()
    
    # Config 로드
    config = Config()
    
    # 검증
    config.validate()
    
    # 요약 출력
    config.print_summary()
    
    # 개별 값 접근
    print("\n" + "=" * 60)
    print("개별 값 접근 테스트")
    print("=" * 60)
    print(f"DB Host: {config.get('db.host')}")
    print(f"DB Port: {config.get('db.port')}")
    print(f"DB Name: {config.get('db.name')}")
    print(f"Initial Capital: ${config.get('trading.initial_capital', 100):.2f}")
    print(f"Max Positions: {config.get('trading.max_positions', 2)}")
    print(f"OLLAMA Host: {config.get('ollama.host')}")
    print()
