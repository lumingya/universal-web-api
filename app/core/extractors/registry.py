"""
app/core/extractors/registry.py - 提取器注册中心

提供提取器的动态注册、查找、实例化功能。
支持从配置文件动态加载提取器类。
"""

import importlib
import logging
from typing import Dict, Type, Optional, List

from app.core.extractors.base import BaseExtractor


logger = logging.getLogger(__name__)


class ExtractorRegistry:
    """
    提取器注册中心
    
    职责：
    1. 管理所有可用的提取器类
    2. 提供装饰器注册机制
    3. 根据 ID 获取提取器实例
    4. 支持动态导入（从配置文件加载）
    """
    
    _extractors: Dict[str, Type[BaseExtractor]] = {}
    _default_extractor_id: str = "deep_mode_v1"
    
    @classmethod
    def register(cls, extractor_id: Optional[str] = None):
        """
        装饰器：注册提取器类
        
        用法：
            @ExtractorRegistry.register("custom_v1")
            class CustomExtractor(BaseExtractor):
                pass
        
        Args:
            extractor_id: 提取器 ID（可选，默认使用类的 get_id()）
        """
        def decorator(extractor_class: Type[BaseExtractor]):
            # 确定 ID
            eid = extractor_id
            if eid is None:
                if hasattr(extractor_class, 'get_id'):
                    eid = extractor_class.get_id()
                else:
                    eid = extractor_class.__name__.lower()
            
            # 注册
            cls._extractors[eid] = extractor_class
            logger.debug(f"已注册提取器: {eid} -> {extractor_class.__name__}")
            
            return extractor_class
        
        return decorator
    
    @classmethod
    def register_class(cls, extractor_class: Type[BaseExtractor], extractor_id: Optional[str] = None):
        """
        直接注册提取器类（非装饰器方式）
        
        Args:
            extractor_class: 提取器类
            extractor_id: 提取器 ID（可选）
        """
        eid = extractor_id
        if eid is None:
            if hasattr(extractor_class, 'get_id'):
                eid = extractor_class.get_id()
            else:
                eid = extractor_class.__name__.lower()
        
        cls._extractors[eid] = extractor_class
        logger.info(f"已注册提取器: {eid} -> {extractor_class.__name__}")
    
    @classmethod
    def get(cls, extractor_id: Optional[str] = None) -> BaseExtractor:
        """
        获取提取器实例
        
        Args:
            extractor_id: 提取器 ID（None 则返回默认提取器）
        
        Returns:
            提取器实例
        
        Raises:
            ValueError: 提取器 ID 不存在
        """
        eid = extractor_id or cls._default_extractor_id
        
        if eid not in cls._extractors:
            raise ValueError(
                f"未知的提取器 ID: {eid}，可用的提取器: {list(cls._extractors.keys())}"
            )
        
        extractor_class = cls._extractors[eid]
        return extractor_class()
    
    @classmethod
    def get_class(cls, extractor_id: str) -> Type[BaseExtractor]:
        """
        获取提取器类（不实例化）
        
        Args:
            extractor_id: 提取器 ID
        
        Returns:
            提取器类
        
        Raises:
            ValueError: 提取器 ID 不存在
        """
        if extractor_id not in cls._extractors:
            raise ValueError(f"未知的提取器: {extractor_id}")
        
        return cls._extractors[extractor_id]
    
    @classmethod
    def list_all(cls) -> List[Dict[str, str]]:
        """
        列出所有已注册的提取器
        
        Returns:
            提取器信息列表，每项包含 id/name/description
        """
        result = []
        for eid, extractor_class in cls._extractors.items():
            info = {
                "id": eid,
                "name": extractor_class.get_name() if hasattr(extractor_class, 'get_name') else extractor_class.__name__,
                "description": extractor_class.get_description() if hasattr(extractor_class, 'get_description') else "",
            }
            result.append(info)
        
        return result
    
    @classmethod
    def exists(cls, extractor_id: str) -> bool:
        """
        检查提取器是否存在
        
        Args:
            extractor_id: 提取器 ID
        
        Returns:
            是否存在
        """
        return extractor_id in cls._extractors
    
    @classmethod
    def set_default(cls, extractor_id: str):
        """
        设置默认提取器
        
        Args:
            extractor_id: 提取器 ID
        
        Raises:
            ValueError: 提取器不存在
        """
        if not cls.exists(extractor_id):
            raise ValueError(f"无法设置默认提取器：{extractor_id} 不存在")
        
        cls._default_extractor_id = extractor_id
        logger.info(f"默认提取器已设置为: {extractor_id}")
    
    @classmethod
    def get_default_id(cls) -> str:
        """获取默认提取器 ID"""
        return cls._default_extractor_id
    
    @classmethod
    def load_from_module(cls, module_path: str, class_name: str, extractor_id: str):
        """
        动态加载并注册提取器类
        
        用于从配置文件加载自定义提取器
        
        Args:
            module_path: 模块路径（如 "app.core.extractors.custom"）
            class_name: 类名（如 "CustomExtractor"）
            extractor_id: 注册的 ID
        
        Raises:
            ImportError: 模块不存在
            AttributeError: 类不存在
        """
        try:
            module = importlib.import_module(module_path)
            extractor_class = getattr(module, class_name)
            
            # 验证是否为 BaseExtractor 子类
            if not issubclass(extractor_class, BaseExtractor):
                raise TypeError(f"{class_name} 不是 BaseExtractor 的子类")
            
            cls.register_class(extractor_class, extractor_id)
            logger.info(f"动态加载提取器成功: {module_path}.{class_name} -> {extractor_id}")
        
        except Exception as e:
            logger.error(f"动态加载提取器失败: {module_path}.{class_name} - {e}")
            raise


__all__ = ['ExtractorRegistry']