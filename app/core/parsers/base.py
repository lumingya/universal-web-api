"""
app/core/parsers/base.py - 响应解析器基类

定义网络响应解析的标准接口（支持增量响应）
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any


class ResponseParser(ABC):
    """
    响应解析器抽象基类
    
    职责：
    - 解析网络拦截到的原始响应体
    - 支持增量响应（流式传输）
    - 提取文本内容和图片
    """
    
    # ============ 抽象方法（子类必须实现）============
    
    @abstractmethod
    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        """
        解析单次响应数据（增量）
        
        Args:
            raw_response: 原始响应体（字符串）
        
        Returns:
            {
                "content": str,           # 本次增量的文本内容
                "images": List[Dict],     # 本次增量的图片（可选）
                "done": bool,             # 是否为最后一块数据
                "error": Optional[str]    # 解析错误信息
            }
        
        注意：
        - content 为空字符串表示本次无新增文本
        - images 为空列表表示本次无新增图片
        - done=True 表示流式传输结束
        - error 不为 None 表示解析失败
        """
        pass
    
    @abstractmethod
    def reset(self):
        """
        重置解析器状态
        
        用于新一轮对话开始时清空累积状态
        """
        pass
    
    # ============ 可选覆盖方法 ============
    
    def validate_response(self, raw_response: str) -> bool:
        """
        验证响应格式是否匹配此解析器
        
        Args:
            raw_response: 原始响应体
        
        Returns:
            True 表示格式匹配，False 表示不匹配
        """
        try:
            result = self.parse_chunk(raw_response)
            return result.get("error") is None
        except Exception:
            return False
    
    # ============ 元数据接口 ============
    
    @classmethod
    def get_id(cls) -> str:
        """解析器唯一标识符"""
        return cls.__name__.lower().replace('parser', '')
    
    @classmethod
    def get_name(cls) -> str:
        """解析器显示名称"""
        return cls.__name__
    
    @classmethod
    def get_description(cls) -> str:
        """解析器描述"""
        return "未提供描述"
    
    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        """
        返回此解析器支持的 URL 匹配模式
        
        Returns:
            URL 子串列表（用于 page.listen.start()）
        """
        return []


__all__ = ['ResponseParser']