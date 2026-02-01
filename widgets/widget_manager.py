"""
위젯 매니저 - 위젯 등록 및 관리
Windows CP949 완벽 지원 버전
"""

import importlib
import os
import sys
from typing import Dict, List, Type
from widgets.base_widget import BaseWidget


class WidgetManager:
    """위젯 관리 시스템"""
    
    def __init__(self):
        self.widgets: Dict[str, Type[BaseWidget]] = {}
        self.load_all_widgets()
    
    def load_all_widgets(self):
        """widgets 폴더의 모든 위젯 자동 로드"""
        widgets_dir = "widgets"
        
        for filename in os.listdir(widgets_dir):
            if filename.endswith("_widget.py"):
                module_name = filename[:-3]  # .py 제거
                
                try:
                    # 모듈 동적 import
                    module = importlib.import_module(f"widgets.{module_name}")
                    
                    # 모듈에서 BaseWidget 상속 클래스 찾기
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if (isinstance(attr, type) and 
                            issubclass(attr, BaseWidget) and 
                            attr != BaseWidget):
                            
                            # 위젯 인스턴스 생성 및 등록
                            widget_instance = attr()
                            self.widgets[widget_instance.widget_id] = attr
                            # ASCII만 사용 (Windows CP949 안전)
                            try:
                                print(f"[OK] Widget loaded: {widget_instance.widget_name} ({widget_instance.widget_id})")
                            except UnicodeEncodeError:
                                print(f"[OK] Widget loaded: {widget_instance.widget_id}")
                
                except Exception as e:
                    # ASCII만 사용 (Windows CP949 안전)
                    try:
                        print(f"[FAIL] Widget load failed ({module_name}): {e}")
                    except UnicodeEncodeError:
                        print(f"[FAIL] Widget load failed: {module_name}")
    
    def get_widget(self, widget_id: str) -> BaseWidget:
        """위젯 인스턴스 반환"""
        if widget_id not in self.widgets:
            raise ValueError(f"Widget not found: {widget_id}")
        
        return self.widgets[widget_id]()
    
    def get_all_widgets(self) -> List[Dict]:
        """모든 위젯 정보 반환"""
        widgets_info = []
        
        for widget_id, widget_class in self.widgets.items():
            widget = widget_class()
            widgets_info.append({
                "id": widget.widget_id,
                "name": widget.widget_name,
                "icon": widget.widget_icon,
                "color": widget.widget_color,
                "size": widget.widget_size,
                "category": widget.widget_category
            })
        
        return widgets_info
    
    def get_widget_data(self, widget_id: str) -> Dict:
        """위젯 데이터 조회"""
        widget = self.get_widget(widget_id)
        data = widget.get_data()
        
        return {
            "id": widget.widget_id,
            "name": widget.widget_name,
            "icon": widget.widget_icon,
            "color": widget.widget_color,
            "data": data
        }
    
    def register_widget(self, widget_class: Type[BaseWidget]):
        """수동으로 위젯 등록"""
        widget = widget_class()
        self.widgets[widget.widget_id] = widget_class
        try:
            print(f"[OK] Widget registered: {widget.widget_name} ({widget.widget_id})")
        except UnicodeEncodeError:
            print(f"[OK] Widget registered: {widget.widget_id}")
    
    def unregister_widget(self, widget_id: str):
        """위젯 등록 해제"""
        if widget_id in self.widgets:
            widget = self.widgets[widget_id]()
            del self.widgets[widget_id]
            try:
                print(f"[OK] Widget removed: {widget.widget_name} ({widget_id})")
            except UnicodeEncodeError:
                print(f"[OK] Widget removed: {widget_id}")
        else:
            print(f"[FAIL] Widget not found: {widget_id}")
