"""
대시보드 위젯 시스템 - 기본 클래스
각 위젯은 독립적인 Python 모듈로 작동
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from sqlalchemy import create_engine, text
import yaml


class BaseWidget(ABC):
    """모든 위젯의 기본 클래스"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self.load_config(config_path)
        self.engine = self.get_engine()
        
    def load_config(self, path: str) -> Dict:
        """설정 파일 로드"""
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    
    def get_engine(self):
        """데이터베이스 엔진 생성"""
        db = self.config['db']
        dsn = f"postgresql://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['name']}"
        return create_engine(dsn)
    
    @abstractmethod
    def get_data(self) -> Dict[str, Any]:
        """위젯 데이터 반환 (각 위젯에서 구현)"""
        pass
    
    @property
    @abstractmethod
    def widget_id(self) -> str:
        """위젯 고유 ID"""
        pass
    
    @property
    @abstractmethod
    def widget_name(self) -> str:
        """위젯 표시 이름"""
        pass
    
    @property
    @abstractmethod
    def widget_icon(self) -> str:
        """위젯 아이콘"""
        pass
    
    @property
    def widget_color(self) -> str:
        """위젯 색상 (기본값)"""
        return "blue"
    
    @property
    def widget_size(self) -> str:
        """위젯 크기 (small, medium, large)"""
        return "medium"
    
    @property
    def widget_category(self) -> str:
        """위젯 카테고리 (stats, chart, table, custom)"""
        return "stats"
