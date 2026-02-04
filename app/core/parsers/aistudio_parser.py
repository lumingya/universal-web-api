"""
aistudio_parser.py - Google AI Studio 响应解析器

响应格式特征：
- Content-Type: application/json+protobuf
- 嵌套数组结构
- 增量文本模式
- 包含 thinking 内容（需过滤）
"""

import json
from typing import Dict, Any, List, Optional

from app.core.config import logger
from .base import ResponseParser


class AIStudioParser(ResponseParser):
    """
    Google AI Studio (MakerSuite) 响应解析器
    
    URL 特征: MakerSuiteService/GenerateContent
    响应格式: JSON+Protobuf (嵌套数组)
    """
    
    def __init__(self):
        self._accumulated_content = ""
        self._is_done = False
    
    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        """
        解析响应（返回完整内容的增量）
        """
        result = {
            "content": "",
            "images": [],
            "done": False,
            "error": None
        }
        
        try:
            if isinstance(raw_response, bytes):
                raw_response = raw_response.decode('utf-8', errors='ignore')
            
            # 解析 JSON（DrissionPage 可能已经解析成列表）
            if isinstance(raw_response, str):
                data = json.loads(raw_response)
            elif isinstance(raw_response, list):
                data = raw_response
            else:
                raw_response = str(raw_response)
                data = json.loads(raw_response)
            
            # 提取所有文本块
            content, is_done = self._extract_content(data)
            
            if content:
                # 计算增量
                if len(content) > len(self._accumulated_content):
                    delta = content[len(self._accumulated_content):]
                    result["content"] = delta
                    self._accumulated_content = content
            
            result["done"] = is_done
            
        except json.JSONDecodeError as e:
            logger.debug(f"[AIStudioParser] JSON 解析失败: {e}")
            result["error"] = str(e)
        except Exception as e:
            logger.debug(f"[AIStudioParser] 解析异常: {e}")
            result["error"] = str(e)
        
        return result
    
    def reset(self):
        """重置状态"""
        self._accumulated_content = ""
        self._is_done = False
    
    def _extract_content(self, data: Any) -> tuple:
        """
        从响应数据中提取文本内容
        
        Returns:
            (accumulated_text, is_done)
        """
        try:
            if not isinstance(data, list) or len(data) == 0:
                return "", False
            
            outer = data[0]
            if not isinstance(outer, list):
                return "", False
            
            accumulated = ""
            is_done = False
            
            for block in outer:
                if not isinstance(block, list):
                    continue
                
                # 检查是否是统计块（结束标志之一）
                if self._is_stats_block(block):
                    is_done = True
                    continue
                
                # 提取文本
                text, block_done, is_thinking = self._extract_block_content(block)
                
                # 跳过 thinking 内容
                if is_thinking:
                    continue
                
                if text:
                    accumulated += text
                
                if block_done:
                    is_done = True
            
            return accumulated, is_done
            
        except Exception as e:
            logger.debug(f"[AIStudioParser] _extract_content 异常: {e}")
            return "", False
    
    def _is_stats_block(self, block: list) -> bool:
        """检查是否是统计块（响应结束）"""
        try:
            # 格式: [null, null, null, [timestamp, ...]]
            if len(block) >= 4:
                if block[0] is None and block[1] is None and block[2] is None:
                    if isinstance(block[3], list):
                        return True
            return False
        except:
            return False

    def _extract_block_content(self, block: list) -> tuple:
        """
        从单个块提取内容
        
        Returns:
            (text, is_done, is_thinking)
        """
        try:
            # 路径: block[0][0][0][0][0]
            # thinking: [13 items, None, "**Thinking...", ..., 1]
            # 正常内容: [2 items, None, "文本内容"]
            
            if not isinstance(block, list) or len(block) == 0:
                return "", False, False
            
            level1 = block[0]
            if not isinstance(level1, list) or len(level1) == 0:
                return "", False, False
            
            level2 = level1[0]
            if not isinstance(level2, list) or len(level2) == 0:
                return "", False, False
            
            level3 = level2[0]
            if not isinstance(level3, list) or len(level3) == 0:
                return "", False, False
            
            level4 = level3[0]
            if not isinstance(level4, list) or len(level4) == 0:
                return "", False, False
            
            # 这里就是 content_arr
            content_arr = level4[0]
            if not isinstance(content_arr, list):
                return "", False, False
            
            # 提取文本 (索引 1)
            text = ""
            if len(content_arr) > 1 and isinstance(content_arr[1], str):
                text = content_arr[1]
            
            # 判断是否是 thinking
            # thinking 块特征：len >= 13 且索引 12 == 1
            is_thinking = False
            if len(content_arr) >= 13:
                if len(content_arr) > 12 and content_arr[12] == 1:
                    is_thinking = True
            
            # 检查是否结束
            # 1. level2 的第二个元素是 1
            is_done = False
            if len(level2) > 1 and level2[1] == 1:
                is_done = True
            
            # 2. 有长 token 字符串（索引 13 或 14）
            if len(content_arr) > 13 and isinstance(content_arr[13], str):
                if len(content_arr[13]) > 50:
                    is_done = True
            
            return text, is_done, is_thinking
            
        except Exception as e:
            logger.debug(f"[AIStudioParser] _extract_block_content 异常: {e}")
            return "", False, False
    
    # ============ 元数据 ============
    
    @classmethod
    def get_id(cls) -> str:
        return "aistudio"
    
    @classmethod
    def get_name(cls) -> str:
        return "Google AI Studio"
    
    @classmethod
    def get_description(cls) -> str:
        return "解析 Google AI Studio (MakerSuite) 的 GenerateContent 响应"
    
    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["MakerSuiteService/GenerateContent", "GenerateContent"]


__all__ = ['AIStudioParser']