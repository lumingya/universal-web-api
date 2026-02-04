"""
app/core/extractors/base.py - 提取器抽象基类

定义内容提取的标准接口，支持策略模式扩展不同的提取算法。
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseExtractor(ABC):
    """
    内容提取器抽象基类
    
    定义从页面元素中提取内容的标准接口。
    不同的提取策略（深度提取、简单提取、API提取等）
    可以通过继承此类来实现。
    """
    
    # ============ 抽象方法（子类必须实现）============
    
    @abstractmethod
    def extract_text(self, element) -> str:
        """
        从元素中提取纯文本
        
        Args:
            element: 页面元素对象
            
        Returns:
            提取的文本内容
        """
        pass
    
    @abstractmethod
    def get_anchor(self, element) -> str:
        """
        获取元素的唯一标识（锚点）
        
        用于在页面变化时追踪同一个元素。
        
        Args:
            element: 页面元素对象
            
        Returns:
            锚点字符串，用于唯一标识该元素
        """
        pass
    
    @abstractmethod
    def find_content_node(self, element) -> Any:
        """
        定位内容子节点
        
        从容器元素中找到实际包含内容的子节点，
        避免读取到按钮、头像等无关元素。
        
        Args:
            element: 父元素对象
            
        Returns:
            内容子节点，如果未找到则返回原元素
        """
        pass
    
    # ============ 元数据接口（子类可选覆盖）============
    
    @classmethod
    def get_id(cls) -> str:
        """
        获取提取器唯一标识符
        
        Returns:
            提取器 ID（默认使用类名转小写）
        """
        return cls.__name__.lower()
    
    @classmethod
    def get_name(cls) -> str:
        """
        获取提取器显示名称
        
        Returns:
            用于 UI 展示的名称
        """
        return cls.__name__
    
    @classmethod
    def get_description(cls) -> str:
        """
        获取提取器描述信息
        
        Returns:
            算法描述（用于帮助用户选择）
        """
        return "未提供描述"


__all__ = ['BaseExtractor']