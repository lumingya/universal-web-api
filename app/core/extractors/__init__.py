"""
app/core/extractors - 内容提取器模块

提供不同的页面内容提取策略。
"""

from app.core.extractors.base import BaseExtractor
from app.core.extractors.registry import ExtractorRegistry 
from app.core.extractors.deep_mode import DeepBrowserExtractor
from app.core.extractors.dom_mode import DOMDirectExtractor
from app.core.extractors.hybrid_mode import HybridExtractor
from app.core.extractors.image_extractor import ImageExtractor, image_extractor  # 🆕 新增


# 自动注册所有提取器
ExtractorRegistry.register_class(DeepBrowserExtractor)
ExtractorRegistry.register_class(DOMDirectExtractor)
ExtractorRegistry.register_class(HybridExtractor)


__all__ = [
    'BaseExtractor', 
    'ExtractorRegistry',  
    'DeepBrowserExtractor',
    'DOMDirectExtractor',
    'HybridExtractor',
    'ImageExtractor',    
    'image_extractor',    
]