"""
lmarena_parser.py - Lmarena (LMSYS) 响应解析器

响应格式特征：
- Vercel AI SDK 流式格式 (Custom Stream)
- 格式为 [prefix]:[json_data]
- a0: 文本增量 (JSON String)
- a2: 心跳/状态
- ad: 结束元数据 (包含 finishReason)
"""

import json
from typing import Dict, Any, List

from app.core.config import logger
from .base import ResponseParser


class LmarenaParser(ResponseParser):
    """
    Lmarena (LMSYS Chatbot Arena) 响应解析器
    
    URL 特征: /nextjs-api/stream/create-evaluation
    响应格式: Custom Stream (Line-based prefix:JSON)
    """
    
    def __init__(self):
        self._accumulated_content = ""
        self._last_raw_length = 0
    
    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        """
        解析 Lmarena 流式响应
        """
        result = {
            "content": "",
            "images": [],
            "done": False,
            "error": None
        }
        
        try:
            # 确保是字符串
            if isinstance(raw_response, bytes):
                raw_response = raw_response.decode('utf-8', errors='ignore')
            
            if not isinstance(raw_response, str):
                raw_response = str(raw_response)
            
            # 只处理新增部分
            current_len = len(raw_response)
            if current_len <= self._last_raw_length:
                return result
            
            new_data = raw_response[self._last_raw_length:]
            self._last_raw_length = current_len
            
            # 解析新增的数据块
            delta_content, is_done = self._parse_stream_chunk(new_data)
            
            if delta_content:
                result["content"] = delta_content
                self._accumulated_content += delta_content
            
            if is_done:
                result["done"] = True
            
        except Exception as e:
            logger.debug(f"[LmarenaParser] 解析异常: {e}")
            result["error"] = str(e)
        
        return result
    
    def _parse_stream_chunk(self, chunk: str) -> (str, bool):
        """解析数据块，提取文本增量和状态"""
        content = ""
        is_done = False
        
        # 简单按行分割（假设 chunk 包含完整行或 Vercel SDK 会 flush 完整行）
        lines = chunk.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 1. 文本增量 (a0:)
            # 格式示例: a0:"你好！"
            if line.startswith('a0:'):
                try:
                    json_str = line[3:] # 去掉 'a0:' 前缀
                    text_chunk = json.loads(json_str)
                    if isinstance(text_chunk, str):
                        content += text_chunk
                except json.JSONDecodeError:
                    continue
            
            # 2. 结束/元数据信号 (ad:)
            # 格式示例: ad:{"finishReason":"stop"}
            elif line.startswith('ad:'):
                try:
                    json_str = line[3:]
                    data = json.loads(json_str)
                    if isinstance(data, dict) and data.get("finishReason"):
                        is_done = True
                except json.JSONDecodeError:
                    continue
            
            # 3. 其他类型 (a2: 心跳等，忽略)
            # 格式示例: a2:[{"type":"heartbeat"}]
            
        return content, is_done
    
    def reset(self):
        """重置状态"""
        self._accumulated_content = ""
        self._last_raw_length = 0
    
    @classmethod
    def get_id(cls) -> str:
        return "lmarena"
    
    @classmethod
    def get_name(cls) -> str:
        return "Lmarena (LMSYS)"
    
    @classmethod
    def get_description(cls) -> str:
        return "解析 Lmarena 的 API 响应（Vercel AI SDK Custom Stream）"
    
    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["**/nextjs-api/stream/create-evaluation**"]


__all__ = ['LmarenaParser']