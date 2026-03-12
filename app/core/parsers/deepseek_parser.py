"""
deepseek_parser.py - DeepSeek 响应解析器

响应格式特征：
- SSE (Server-Sent Events) 流式响应
- 增量文本通过 data: {"v": "..."} 传递
- 结束标志: event: finish 或 event: close
"""

import json
from typing import Dict, Any, List, Tuple

from app.core.config import logger
from .base import ResponseParser


class DeepSeekParser(ResponseParser):
    """
    DeepSeek API 响应解析器
    
    URL 特征: /api/v0/chat/completion
    响应格式: SSE (text/event-stream)
    """
    
    def __init__(self):
        self._accumulated_content = ""
        self._last_raw_length = 0
        self._is_streaming = False
    
    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        """
        解析SSE响应流（返回增量）
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
            
            # 解析新增的 SSE 事件
            delta_content, chunk_done = self._parse_sse_chunk(new_data)
            
            if delta_content:
                result["content"] = delta_content
                self._accumulated_content += delta_content
            
            if chunk_done:
                result["done"] = True
            
            # 兜底检测结束标志
            if "event: finish" in new_data or "event: close" in new_data:
                result["done"] = True
            
            # 兜底检测状态完成标志
            if '"status"' in new_data and '"FINISHED"' in new_data:
                result["done"] = True
            
        except Exception as e:
            logger.debug(f"[DeepSeekParser] 解析异常: {e}")
            result["error"] = str(e)
        
        return result
    
    def _parse_sse_chunk(self, chunk: str) -> Tuple[str, bool]:
        """解析 SSE 数据块，返回 (文本增量, 是否完成)。"""
        content = ""
        done = False
        current_event = None
        
        lines = chunk.split('\n')
        
        for line in lines:
            line = line.strip()
            
            if not line:
                current_event = None
                continue
            
            # 识别事件类型
            if line.startswith('event:'):
                current_event = line[6:].strip()
                continue
            
            # 跳过非数据行
            if not line.startswith('data:'):
                continue
            
            # 跳过特殊事件的数据
            if current_event in ('ready', 'update_session', 'title'):
                continue
            if current_event in ('finish', 'close'):
                done = True
                continue
            
            # 解析数据行
            data_str = line[5:].strip()
            if not data_str or data_str == '{}':
                continue
            
            try:
                data = json.loads(data_str)
                extracted, item_done = self._extract_content(data)
                if extracted:
                    content += extracted
                if item_done:
                    done = True
            except json.JSONDecodeError:
                continue
        
        return content, done
    
    def _extract_content(self, data: Dict[str, Any]) -> Tuple[str, bool]:
        """从数据事件中提取文本内容和完成状态。"""
        content = ""
        done = False
        path = str(data.get("p", "") or "")
        operation = str(data.get("o", "") or "")
        
        # 格式1: 路径追加 {"p": "response/fragments/-1/content", "o": "APPEND", "v": "..."}
        if operation == "APPEND" and "content" in path and isinstance(data.get("v"), str):
            return data["v"], False
        
        # 格式2: 状态更新 {"p":"response/status","o":"SET","v":"FINISHED"}
        if operation == "SET" and path.endswith("status") and str(data.get("v", "") or "").upper() == "FINISHED":
            return "", True
        
        # 格式3: 批量操作 {"p": "response", "o": "BATCH", "v": [...]}
        if operation == "BATCH" and isinstance(data.get("v"), list):
            for op in data["v"]:
                if isinstance(op, dict):
                    sub_content, sub_done = self._extract_batch_content(op)
                    if sub_content:
                        content += sub_content
                    if sub_done:
                        done = True
            return content, done
        
        # 格式4: 初始响应 {"v": {"response": {..., "fragments": [...]}}}
        if "v" in data and isinstance(data["v"], dict):
            response = data["v"].get("response", {})
            fragments = response.get("fragments", [])
            for frag in fragments:
                if isinstance(frag, dict) and "content" in frag:
                    content += frag["content"]
            status = str(response.get("status", "") or "").upper()
            if status == "FINISHED":
                done = True
            return content, done
        
        # 格式5: 纯增量 {"v": "文本"}
        # 仅在不是状态/路径补丁时才视为真实文本。
        if "v" in data and isinstance(data["v"], str) and not path and not operation:
            return data["v"], False
        
        return content, done
    
    def _extract_batch_content(self, op: Dict[str, Any]) -> Tuple[str, bool]:
        """从批量操作中提取内容和完成状态。"""
        path = op.get("p", "")
        operation = op.get("o", "")
        value = op.get("v")
        
        # fragments 追加操作
        if path == "fragments" and operation == "APPEND" and isinstance(value, list):
            content = ""
            for frag in value:
                if isinstance(frag, dict) and "content" in frag:
                    content += frag["content"]
            return content, False
        
        # 内容追加
        if "content" in path and operation == "APPEND" and isinstance(value, str):
            return value, False
        
        # 完成状态批量更新
        if operation == "SET" and str(path).endswith("status") and str(value or "").upper() == "FINISHED":
            return "", True
        
        # quasi_status 也可作为完成信号
        if operation == "SET" and str(path).endswith("quasi_status") and str(value or "").upper() == "FINISHED":
            return "", True
        
        return "", False
    
    def reset(self):
        """重置状态"""
        self._accumulated_content = ""
        self._last_raw_length = 0
        self._is_streaming = False
    
    @classmethod
    def get_id(cls) -> str:
        return "deepseek"
    
    @classmethod
    def get_name(cls) -> str:
        return "DeepSeek API"
    
    @classmethod
    def get_description(cls) -> str:
        return "解析 DeepSeek 的 API 响应（SSE流式）"
    
    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["**/api/v0/chat/completion**"]


__all__ = ['DeepSeekParser']
