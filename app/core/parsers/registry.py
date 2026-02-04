"""
app/core/parsers/registry.py - 解析器注册中心

提供解析器的动态注册、查找、实例化功能
"""

import importlib
from typing import Dict, Type, Optional, List

from app.core.config import logger
from .base import ResponseParser


class ParserRegistry:
    """
    解析器注册中心
    
    职责：
    1. 管理所有可用的响应解析器
    2. 提供注册机制
    3. 根据 ID 获取解析器实例
    """
    
    _parsers: Dict[str, Type[ResponseParser]] = {}
    
    @classmethod
    def register_class(cls, parser_class: Type[ResponseParser], parser_id: Optional[str] = None):
        """
        注册解析器类
        
        Args:
            parser_class: 解析器类
            parser_id: 解析器 ID（可选，默认使用 get_id()）
        """
        pid = parser_id or parser_class.get_id()
        cls._parsers[pid] = parser_class
        logger.info(f"已注册响应解析器: {pid} -> {parser_class.__name__}")
    
    @classmethod
    def get(cls, parser_id: str) -> ResponseParser:
        """
        获取解析器实例
        
        Args:
            parser_id: 解析器 ID
        
        Returns:
            解析器实例
        
        Raises:
            ValueError: 解析器 ID 不存在
        """
        if parser_id not in cls._parsers:
            raise ValueError(
                f"未知的解析器 ID: {parser_id}，可用的解析器: {list(cls._parsers.keys())}"
            )
        
        parser_class = cls._parsers[parser_id]
        return parser_class()
    
    @classmethod
    def exists(cls, parser_id: str) -> bool:
        """检查解析器是否存在"""
        return parser_id in cls._parsers
    
    @classmethod
    def list_all(cls) -> List[Dict[str, str]]:
        """
        列出所有已注册的解析器
        
        Returns:
            解析器信息列表
        """
        result = []
        for pid, parser_class in cls._parsers.items():
            info = {
                "id": pid,
                "name": parser_class.get_name(),
                "description": parser_class.get_description(),
                "patterns": parser_class.get_supported_patterns(),
            }
            result.append(info)
        
        return result
    
    @classmethod
    def load_from_module(cls, module_path: str, class_name: str, parser_id: str):
        """
        动态加载并注册解析器类
        
        Args:
            module_path: 模块路径（如 "app.core.parsers.custom"）
            class_name: 类名（如 "CustomParser"）
            parser_id: 注册的 ID
        
        Raises:
            ImportError: 模块不存在
            AttributeError: 类不存在
        """
        try:
            module = importlib.import_module(module_path)
            parser_class = getattr(module, class_name)
            
            if not issubclass(parser_class, ResponseParser):
                raise TypeError(f"{class_name} 不是 ResponseParser 的子类")
            
            cls.register_class(parser_class, parser_id)
            logger.info(f"动态加载解析器成功: {module_path}.{class_name} -> {parser_id}")
        
        except Exception as e:
            logger.error(f"动态加载解析器失败: {module_path}.{class_name} - {e}")
            raise


__all__ = ['ParserRegistry']