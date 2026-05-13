"""
request_manager.py - 请求生命周期管理器（v2.0）

v2.0 改动：
- 移除全局执行锁（锁转移到 TabPoolManager）
- 保留请求追踪、状态管理、取消信号功能
- acquire/release 改为标记状态，不再阻塞
"""

import asyncio
import threading
import json
import os
import time
import uuid
import re
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from collections import OrderedDict

from app.core.config import get_logger, _request_context

logger = get_logger("REQUEST")


class RequestStatus(Enum):
    """请求状态枚举"""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class RequestContext:
    """请求上下文"""
    request_id: str
    status: RequestStatus = RequestStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    
    _cancel_flag: bool = field(default=False, repr=False)
    cancel_reason: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    
    # v2.0 新增：关联的标签页 ID
    tab_id: Optional[str] = None
    monitor: Dict[str, Any] = field(default_factory=dict)
    
    def should_stop(self) -> bool:
        with self._lock:
            return self._cancel_flag
    
    def request_cancel(self, reason: str = "unknown"):
        with self._lock:
            if self._cancel_flag:
                return
            
            self._cancel_flag = True
            self.cancel_reason = reason
            
            if self.status == RequestStatus.RUNNING:
                self.status = RequestStatus.CANCELLED
            
            logger.info(f"[{self.request_id}] 取消 ({reason})")
    
    def mark_running(self, tab_id: str = None):
        with self._lock:
            self.started_at = time.time()
            self.tab_id = tab_id
            if self._cancel_flag:
                self.status = RequestStatus.CANCELLED
            else:
                self.status = RequestStatus.RUNNING
    
    def mark_completed(self):
        with self._lock:
            if self.status == RequestStatus.RUNNING:
                self.status = RequestStatus.COMPLETED
            self.finished_at = time.time()
    
    def mark_failed(self, reason: str = None):
        with self._lock:
            self.status = RequestStatus.FAILED
            self.finished_at = time.time()
            if reason:
                self.cancel_reason = reason
    
    def get_duration(self) -> float:
        end = self.finished_at or time.time()
        start = self.started_at or self.created_at
        return end - start
    
    def is_terminal(self) -> bool:
        return self.status in (
            RequestStatus.COMPLETED,
            RequestStatus.CANCELLED,
            RequestStatus.FAILED
        )


class RequestManager:
    """
    请求管理器（v2.0 - 纯追踪模式）
    
    v2.0 改动：
    - 不再持有执行锁
    - 只负责请求追踪和取消信号
    """
    
    _instance: Optional['RequestManager'] = None
    _instance_lock = threading.Lock()
        
    # 僵尸请求超时时间（秒）- 超过此时间的 RUNNING 请求将被强制清理
    ZOMBIE_TTL = 3600
    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._requests: OrderedDict[str, RequestContext] = OrderedDict()
        self._requests_lock = threading.Lock()
        self._request_counter = 0  # 请求计数器

        self._max_history = 100

        self._stats_file = "config/app_stats.json"
        self._history_file = "config/request_history.json"
        self._monitor_history: List[Dict[str, Any]] = []
        self._history_lock = threading.Lock()
        self._history_save_lock = threading.Lock()
        self.total_requests = 0
        self._load_stats()
        self._load_history()
        
        self._initialized = True
        
        logger.debug("RequestManager 初始化完成")
        
    def _load_stats(self):
        try:
            if os.path.exists(self._stats_file):
                with open(self._stats_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.total_requests = data.get("total_requests", 0)
        except Exception as e:
            logger.debug(f"加载状态失败: {e}")

    def _save_stats(self):
        try:
            os.makedirs(os.path.dirname(self._stats_file), exist_ok=True)
            with open(self._stats_file, "w", encoding="utf-8") as f:
                json.dump({"total_requests": self.total_requests}, f)
        except Exception as e:
            logger.debug(f"保存状态失败: {e}")

    def _load_history(self):
        try:
            if not os.path.exists(self._history_file):
                return
            with open(self._history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            records = data.get("records", data if isinstance(data, list) else [])
            if isinstance(records, list):
                self._monitor_history = [
                    self._normalize_history_record(item) for item in records[-200:]
                    if isinstance(item, dict)
                ]
        except Exception as e:
            logger.debug(f"加载请求历史失败: {e}")
            self._monitor_history = []

    def _save_history(self):
        try:
            with self._history_save_lock:
                os.makedirs(os.path.dirname(self._history_file), exist_ok=True)
                with self._history_lock:
                    records = list(self._monitor_history[-200:])
                tmp_path = self._history_file + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "max_records": 200,
                            "saved_at": time.time(),
                            "records": records,
                        },
                        f,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                os.replace(tmp_path, self._history_file)
        except Exception as e:
            logger.debug(f"保存请求历史失败: {e}")

    @staticmethod
    def _sanitize_text_for_storage(value: Any, max_chars: int = 80000) -> str:
        text = "" if value is None else str(value)
        if not text:
            return ""

        data_uri_pattern = re.compile(
            r"data:(?:image|audio|video)/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\r\n]{1024,}",
            re.IGNORECASE,
        )
        text = data_uri_pattern.sub("[图片占位符]", text)

        long_base64_pattern = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{8192,}={0,2}(?![A-Za-z0-9+/])")
        text = long_base64_pattern.sub("[图片占位符]", text)

        if len(text) > max_chars:
            return text[:max_chars] + f"\n\n[内容已截断，原始长度 {len(text)} 字符]"
        return text

    @classmethod
    def _sanitize_for_storage(cls, value: Any, max_depth: int = 6) -> Any:
        if max_depth < 0:
            return "[内容已折叠]"
        if isinstance(value, str):
            return cls._sanitize_text_for_storage(value)
        if isinstance(value, list):
            return [cls._sanitize_for_storage(item, max_depth - 1) for item in value[:100]]
        if isinstance(value, dict):
            result: Dict[str, Any] = {}
            for key, item in list(value.items())[:100]:
                result[str(key)] = cls._sanitize_for_storage(item, max_depth - 1)
            return result
        return value

    @classmethod
    def _normalize_history_record(cls, record: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(record)

        prompt_text = cls._sanitize_text_for_storage(normalized.get("prompt", ""), max_chars=20000)
        response_text = cls._sanitize_text_for_storage(normalized.get("response", ""), max_chars=30000)
        error_message = cls._sanitize_text_for_storage(normalized.get("error_message", ""), max_chars=10000)
        error_stack = cls._sanitize_text_for_storage(normalized.get("error_stack", ""), max_chars=20000)

        try:
            media_count = int(normalized.get("media_count") or 0)
        except Exception:
            media_count = 0

        normalized["prompt"] = prompt_text
        normalized["response"] = response_text
        normalized["summary"] = cls._sanitize_text_for_storage(
            normalized.get("summary") or response_text[:180],
            max_chars=500,
        )
        normalized["error_message"] = error_message
        normalized["error_stack"] = error_stack or error_message
        normalized["media_count"] = media_count

        status_value = str(normalized.get("status") or "").strip()
        has_meaningful_response = bool(response_text.strip()) or media_count > 0
        if status_value == RequestStatus.COMPLETED.value and not has_meaningful_response:
            normalized["status"] = RequestStatus.FAILED.value
            normalized["success"] = False
            normalized["error_code"] = "empty_response"
            normalized["error_message"] = error_message or "请求流程已结束，但没有捕获到 AI 响应文本或媒体结果。"
            normalized["error_stack"] = normalized["error_stack"] or normalized["error_message"]
        elif status_value == RequestStatus.COMPLETED.value:
            normalized["success"] = True
        elif status_value:
            normalized["success"] = False

        token_estimate = normalized.get("token_estimate")
        if not isinstance(token_estimate, dict):
            prompt_tokens = cls._estimate_tokens(prompt_text)
            response_tokens = cls._estimate_tokens(response_text)
            normalized["token_estimate"] = {
                "prompt": prompt_tokens,
                "response": response_tokens,
                "total": prompt_tokens + response_tokens,
                "chars": len(prompt_text) + len(response_text),
            }

        return normalized

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    item_type = str(item.get("type") or "").strip()
                    if item_type == "text":
                        parts.append(str(item.get("text") or ""))
                    elif item_type == "image_url":
                        parts.append("[图片占位符]")
                    else:
                        parts.append(json.dumps(item, ensure_ascii=False))
                else:
                    parts.append(str(item))
            return "\n".join(part for part in parts if part)
        return json.dumps(content, ensure_ascii=False)

    @classmethod
    def _messages_to_prompt_text(cls, messages: Any) -> str:
        if not isinstance(messages, list):
            return cls._sanitize_text_for_storage(messages)

        lines: List[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "message")
            text = cls._content_to_text(message.get("content"))
            if not text:
                continue
            lines.append(f"{role}: {text}")
        return cls._sanitize_text_for_storage("\n\n".join(lines))

    @classmethod
    def _has_multimodal_payload(cls, value: Any, max_depth: int = 6) -> bool:
        if max_depth < 0:
            return False
        if isinstance(value, str):
            lowered = value.lower()
            return "image_url" in lowered or "data:image" in lowered or "data:video" in lowered or "data:audio" in lowered
        if isinstance(value, list):
            return any(cls._has_multimodal_payload(item, max_depth - 1) for item in value)
        if isinstance(value, dict):
            if str(value.get("type") or "").strip() == "image_url":
                return True
            if any(key in value for key in ("image_url", "images", "media")):
                return True
            return any(cls._has_multimodal_payload(item, max_depth - 1) for item in value.values())
        return False

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        ascii_words = re.findall(r"[A-Za-z0-9_]+", text)
        non_ascii_chars = re.findall(r"[^\x00-\x7F\s]", text)
        punctuation = re.findall(r"[^\w\s]", text, flags=re.UNICODE)
        return max(1, int(len(ascii_words) * 1.25 + len(non_ascii_chars) * 0.8 + len(punctuation) * 0.25))

    @staticmethod
    def _extract_response_text(payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if not isinstance(payload, dict):
            return "" if payload is None else str(payload)

        try:
            choices = payload.get("choices") or []
            if choices:
                first = choices[0] or {}
                message = first.get("message") or {}
                delta = first.get("delta") or {}
                content = message.get("content")
                if content is None:
                    content = delta.get("content")
                return "" if content is None else str(content)
        except Exception:
            pass

        if "error" in payload:
            error = payload.get("error") or {}
            if isinstance(error, dict):
                return str(error.get("message") or "")
        return ""

    @staticmethod
    def _extract_error_code(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        error = payload.get("error")
        if not isinstance(error, dict):
            return ""
        return str(error.get("code") or error.get("type") or "").strip()

    @staticmethod
    def _extract_media_items(payload: Any) -> List[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return []

        media_items: List[Dict[str, Any]] = []
        top_media = payload.get("media")
        if isinstance(top_media, list):
            media_items.extend(item for item in top_media if isinstance(item, dict))

        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] or {}
            delta = first.get("delta") or {}
            message = first.get("message") or {}
            for source in (delta, message):
                if not isinstance(source, dict):
                    continue
                nested_media = source.get("media")
                if isinstance(nested_media, list):
                    media_items.extend(item for item in nested_media if isinstance(item, dict))

        return media_items

    def record_request_input(
        self,
        ctx: RequestContext,
        payload: Any,
        *,
        endpoint: str = "",
        route_domain: str = "",
        tab_index: Optional[int] = None,
        preset_name: Optional[str] = None,
    ) -> None:
        if ctx is None:
            return

        payload_dict = payload if isinstance(payload, dict) else {}
        messages = payload_dict.get("messages") if payload_dict else None
        prompt_text = self._messages_to_prompt_text(messages)
        is_stream = bool(payload_dict.get("stream")) if payload_dict else False
        has_tools = bool(payload_dict.get("tools") or payload_dict.get("functions")) if payload_dict else False
        request_type = "工具调用" if has_tools else ("流式" if is_stream else "非流式")
        model = str(payload_dict.get("model") or "").strip()
        resolved_preset = str(preset_name or payload_dict.get("preset_name") or "").strip()

        with ctx._lock:
            ctx.monitor.update({
                "endpoint": endpoint,
                "route_domain": str(route_domain or "").strip(),
                "target_domain": str(route_domain or "").strip(),
                "tab_index": tab_index,
                "preset_name": resolved_preset,
                "model": model,
                "request_type": request_type,
                "is_stream": is_stream,
                "is_multimodal": self._has_multimodal_payload(messages),
                "prompt": prompt_text,
                "payload": self._sanitize_for_storage(payload_dict),
            })

    def update_request_metadata(self, request_id: str, **metadata: Any) -> bool:
        request_key = str(request_id or "").strip()
        if not request_key:
            return False

        with self._requests_lock:
            ctx = self._requests.get(request_key)

        if not ctx:
            return False

        with ctx._lock:
            for key, value in metadata.items():
                if value is None:
                    continue
                if key == "target_domain" and not str(value or "").strip():
                    continue
                ctx.monitor[key] = self._sanitize_for_storage(value)
        return True

    def capture_response_chunk(self, ctx: RequestContext, chunk: Any) -> None:
        if ctx is None or not isinstance(chunk, str) or not chunk.startswith("data: "):
            return
        data_str = chunk[6:].strip()
        if not data_str or data_str == "[DONE]":
            return

        try:
            payload = json.loads(data_str)
        except Exception:
            return

        text = self._extract_response_text(payload)
        media_items = self._extract_media_items(payload)
        error = payload.get("error") if isinstance(payload, dict) else None

        with ctx._lock:
            if text:
                parts = ctx.monitor.setdefault("response_parts", [])
                if isinstance(parts, list):
                    current_len = sum(len(str(item)) for item in parts)
                    remaining = max(0, 30000 - current_len)
                    if remaining > 0:
                        parts.append(text[:remaining])
            if media_items:
                existing = ctx.monitor.setdefault("media_items", [])
                if isinstance(existing, list):
                    existing.extend(self._sanitize_for_storage(media_items))
                ctx.monitor["has_response_media"] = True
                ctx.monitor["is_multimodal"] = True
            if isinstance(error, dict):
                ctx.monitor["error_message"] = str(error.get("message") or "")
                ctx.monitor["error_code"] = str(error.get("code") or error.get("type") or "execution_error")
                ctx.monitor["error_stack"] = self._sanitize_text_for_storage(json.dumps(error, ensure_ascii=False), max_chars=40000)

    def capture_response_payload(
        self,
        ctx: RequestContext,
        payload: Any,
        *,
        error_code: str = "",
    ) -> None:
        if ctx is None:
            return

        text = self._extract_response_text(payload)
        media_items = self._extract_media_items(payload)
        sanitized_payload = self._sanitize_for_storage(payload)
        payload_error_code = self._extract_error_code(payload)

        with ctx._lock:
            if text:
                ctx.monitor["response_text"] = self._sanitize_text_for_storage(text, max_chars=30000)
            if media_items:
                ctx.monitor["media_items"] = self._sanitize_for_storage(media_items)
                ctx.monitor["has_response_media"] = True
                ctx.monitor["is_multimodal"] = True
            ctx.monitor["response_payload"] = sanitized_payload
            if payload_error_code or error_code:
                ctx.monitor["error_code"] = payload_error_code or error_code

    def capture_error(
        self,
        ctx: RequestContext,
        error: Any,
        *,
        code: str = "execution_error",
        stack: str = "",
    ) -> None:
        if ctx is None:
            return
        message = str(error or "")
        with ctx._lock:
            ctx.monitor["error_message"] = self._sanitize_text_for_storage(message, max_chars=20000)
            ctx.monitor["error_code"] = str(code or "execution_error")
            ctx.monitor["error_stack"] = self._sanitize_text_for_storage(stack or message, max_chars=40000)

    def _append_monitor_history(self, ctx: RequestContext) -> None:
        with ctx._lock:
            if ctx.monitor.get("_history_recorded"):
                return
            ctx.monitor["_history_recorded"] = True
            monitor = dict(ctx.monitor)

        created_at = float(ctx.created_at or time.time())
        started_at = float(ctx.started_at or created_at)
        finished_at = float(ctx.finished_at or time.time())
        queue_ms = max(0, int((started_at - created_at) * 1000))
        generation_ms = max(0, int((finished_at - started_at) * 1000))
        duration_ms = max(0, int((finished_at - created_at) * 1000))

        response_text = monitor.get("response_text")
        if not response_text:
            response_parts = monitor.get("response_parts")
            if isinstance(response_parts, list):
                response_text = "".join(str(item) for item in response_parts)
        response_text = self._sanitize_text_for_storage(response_text, max_chars=30000)
        prompt_text = self._sanitize_text_for_storage(monitor.get("prompt", ""), max_chars=20000)

        prompt_tokens = self._estimate_tokens(prompt_text)
        response_tokens = self._estimate_tokens(response_text)
        media_items = monitor.get("media_items") if isinstance(monitor.get("media_items"), list) else []
        has_meaningful_response = bool(response_text.strip()) or len(media_items) > 0

        status_value = ctx.status.value if isinstance(ctx.status, RequestStatus) else str(ctx.status)
        success = status_value == RequestStatus.COMPLETED.value and has_meaningful_response
        error_message = self._sanitize_text_for_storage(
            monitor.get("error_message") or ctx.cancel_reason or "",
            max_chars=10000,
        )
        error_code = str(monitor.get("error_code") or (status_value if not success else "") or "").strip()

        if status_value == RequestStatus.COMPLETED.value and not has_meaningful_response:
            status_value = RequestStatus.FAILED.value
            success = False
            error_code = "empty_response"
            error_message = "请求流程已结束，但没有捕获到 AI 响应文本或媒体结果。"

        error_stack = self._sanitize_text_for_storage(
            monitor.get("error_stack") or error_message,
            max_chars=20000,
        )

        record = {
            "id": ctx.request_id,
            "status": status_value,
            "success": success,
            "created_at": created_at,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "queue_ms": queue_ms,
            "generation_ms": generation_ms,
            "target_domain": str(monitor.get("target_domain") or monitor.get("route_domain") or "未知域名"),
            "route_domain": str(monitor.get("route_domain") or ""),
            "preset_name": str(monitor.get("preset_name") or "默认预设"),
            "tab_index": monitor.get("tab_index"),
            "tab_id": ctx.tab_id or monitor.get("tab_id") or "",
            "model": str(monitor.get("model") or ""),
            "endpoint": str(monitor.get("endpoint") or ""),
            "request_type": str(monitor.get("request_type") or ""),
            "is_stream": bool(monitor.get("is_stream")),
            "is_multimodal": bool(monitor.get("is_multimodal") or monitor.get("has_response_media")),
            "prompt": prompt_text,
            "response": response_text,
            "summary": response_text[:180],
            "error_code": error_code,
            "error_message": error_message,
            "error_stack": error_stack,
            "media_count": len(media_items),
            "token_estimate": {
                "prompt": prompt_tokens,
                "response": response_tokens,
                "total": prompt_tokens + response_tokens,
                "chars": len(prompt_text) + len(response_text),
            },
        }
        record = self._normalize_history_record(record)

        with self._history_lock:
            self._monitor_history.append(record)
            self._monitor_history = self._monitor_history[-200:]

        threading.Thread(target=self._save_history, daemon=True).start()

    @staticmethod
    def _history_revision_unlocked(records: List[Dict[str, Any]]) -> str:
        if not records:
            return "0::0"
        latest = records[-1] if isinstance(records[-1], dict) else {}
        latest_time = latest.get("finished_at") or latest.get("created_at") or 0
        return f"{len(records)}:{latest.get('id', '')}:{latest_time}"

    @staticmethod
    def _history_preview_text(value: Any, max_chars: int = 220) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > max_chars:
            return text[:max_chars] + "..."
        return text

    @classmethod
    def _to_history_list_record(cls, record: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(record)
        prompt_text = str(item.pop("prompt", "") or "")
        response_text = str(item.pop("response", "") or "")
        error_stack = str(item.pop("error_stack", "") or "")
        item.pop("payload", None)
        item.pop("response_payload", None)
        item.pop("response_parts", None)

        summary = str(item.get("summary") or "").strip()
        if not summary:
            summary = response_text or str(item.get("error_message") or "")
        item["summary"] = cls._history_preview_text(summary, max_chars=240)
        item["prompt_preview"] = cls._history_preview_text(prompt_text, max_chars=240)
        item["response_preview"] = cls._history_preview_text(response_text, max_chars=300)
        item["has_detail"] = bool(prompt_text or response_text or error_stack)
        item["detail_loaded"] = False
        item["detail_text_lengths"] = {
            "prompt": len(prompt_text),
            "response": len(response_text),
            "error_stack": len(error_stack),
        }

        error_message = str(item.get("error_message") or "")
        if len(error_message) > 800:
            item["error_message"] = error_message[:800] + "..."

        return item

    def get_request_history_payload(self, limit: int = 200, include_detail: bool = False) -> Dict[str, Any]:
        try:
            count = max(1, min(200, int(limit or 200)))
        except Exception:
            count = 200
        with self._history_lock:
            history = [
                dict(item)
                for item in self._monitor_history[-count:]
                if isinstance(item, dict)
            ]
            revision = self._history_revision_unlocked(self._monitor_history)

        records = list(reversed(history))
        if not include_detail:
            records = [self._to_history_list_record(item) for item in records]

        return {
            "records": records,
            "count": len(records),
            "max_records": 200,
            "revision": revision,
        }

    def get_request_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self.get_request_history_payload(limit, include_detail=True)["records"]

    def get_request_history_record(self, request_id: str) -> Optional[Dict[str, Any]]:
        request_key = str(request_id or "").strip()
        if not request_key:
            return None

        with self._history_lock:
            for item in reversed(self._monitor_history):
                if not isinstance(item, dict):
                    continue
                if str(item.get("id") or "").strip() == request_key:
                    return dict(item)
        return None
    
    def create_request(self) -> RequestContext:
        """创建新请求"""
        request_id = self._generate_id()
        ctx = RequestContext(request_id=request_id)
        
        with self._requests_lock:
            self._requests[request_id] = ctx
            self._cleanup_old_requests()
        
        # 设置上下文后记录日志
        token = _request_context.set(request_id)
        try:
            logger.info("创建")
        finally:
            _request_context.reset(token)
        
        return ctx
    
    def _generate_id(self) -> str:
        """生成简短的请求 ID"""
        with self._requests_lock:
            self._request_counter += 1
            return f"req-{self._request_counter:03d}"
    
    def _cleanup_old_requests(self):
        """清理旧请求（修复版：不因单个未完成请求阻塞所有清理）"""
        if len(self._requests) <= self._max_history:
            return
        
        now = time.time()
        to_delete = []
        
        for req_id, ctx in list(self._requests.items()):
            # 已终态的可以删除
            if ctx.is_terminal():
                to_delete.append(req_id)
            # 超时的 RUNNING 请求视为僵尸，强制标记失败
            elif ctx.status == RequestStatus.RUNNING:
                started = ctx.started_at or ctx.created_at
                if now - started > self.ZOMBIE_TTL:
                    logger.warning(
                        f"[{req_id}] 僵尸请求 (运行 {now - started:.0f}s)，强制清理"
                    )
                    ctx.mark_failed("zombie_timeout")
                    to_delete.append(req_id)
            
            # 收集足够数量后停止遍历
            if len(self._requests) - len(to_delete) <= self._max_history:
                break
        
        # 批量删除
        for req_id in to_delete:
            del self._requests[req_id]
        
        if to_delete:
            logger.debug(f"清理了 {len(to_delete)} 个旧请求")
    
    def start_request(self, ctx: RequestContext, tab_id: str = None):
        """标记请求开始执行"""
        ctx.mark_running(tab_id)
        with self._requests_lock:
            self.total_requests += 1
            threading.Thread(target=self._save_stats, daemon=True).start()
        # 日志由调用方在上下文中记录，这里不再重复

    def bind_tab(self, request_id: str, tab_id: str) -> bool:
        """为已创建/运行中的请求补绑真实标签页 ID。"""
        if not request_id or not tab_id:
            return False

        with self._requests_lock:
            ctx = self._requests.get(request_id)

        if not ctx or ctx.is_terminal():
            return False

        with ctx._lock:
            previous_tab_id = str(ctx.tab_id or "").strip()
            ctx.tab_id = tab_id
            logger.debug(
                f"[{request_id}] 绑定标签页: {previous_tab_id or '-'} -> {tab_id} "
                f"(status={ctx.status.value}, cancel_reason={ctx.cancel_reason or '-'})"
            )
        return True
    
    def finish_request(self, ctx: RequestContext, success: bool = True):
        """标记请求结束"""
        if ctx.status == RequestStatus.RUNNING:
            if success:
                ctx.mark_completed()
            else:
                ctx.mark_failed()
        
        duration = ctx.get_duration()
        # 设置上下文后记录日志
        token = _request_context.set(ctx.request_id)
        try:
            logger.info(
                f"完成 ({duration:.1f}s, status={ctx.status.value}, "
                f"tab={ctx.tab_id or '-'}, reason={ctx.cancel_reason or '-'})"
            )
        finally:
            _request_context.reset(token)

        try:
            self._append_monitor_history(ctx)
        except Exception as e:
            logger.debug(f"写入请求监控历史失败: {e}")
    
    def cancel_request(self, request_id: str, reason: str = "manual") -> bool:
        """取消指定请求"""
        with self._requests_lock:
            ctx = self._requests.get(request_id)
        
        if not ctx:
            return False
        
        if ctx.is_terminal():
            return False
        
        ctx.request_cancel(reason)
        return True
    
    def get_request(self, request_id: str) -> Optional[RequestContext]:
        with self._requests_lock:
            return self._requests.get(request_id)
    
    def get_running_requests(self, tab_id: str = None) -> list:
        """获取所有正在执行的请求"""
        target_tab_id = str(tab_id or "").strip()
        with self._requests_lock:
            return [
                ctx for ctx in self._requests.values()
                if ctx.status == RequestStatus.RUNNING
                and (not target_tab_id or str(ctx.tab_id or "").strip() == target_tab_id)
            ]
    
    def get_status(self) -> Dict[str, Any]:
        """获取管理器状态"""
        with self._requests_lock:
            status_counts = {}
            for ctx in self._requests.values():
                s = ctx.status.value
                status_counts[s] = status_counts.get(s, 0) + 1
            
            running = [
                {
                    "request_id": ctx.request_id, 
                    "tab_id": ctx.tab_id, 
                    "duration": round(ctx.get_duration(), 1)
                }
                for ctx in self._requests.values()
                if ctx.status == RequestStatus.RUNNING
            ]
            
            return {
                "running_count": len(running),
                "running_requests": running,
                "total_tracked": len(self._requests),
                "status_counts": status_counts
            }
    
    # ================= 兼容旧接口 =================
    
    def is_locked(self) -> bool:
        """兼容旧接口 - 检查是否有正在执行的请求"""
        with self._requests_lock:
            return any(
                ctx.status == RequestStatus.RUNNING 
                for ctx in self._requests.values()
            )
    
    def get_current_request_id(self, tab_id: str = None) -> Optional[str]:
        """兼容旧接口 - 获取当前执行的请求ID（返回第一个）"""
        target_tab_id = str(tab_id or "").strip()
        with self._requests_lock:
            for ctx in self._requests.values():
                if ctx.status == RequestStatus.RUNNING and (
                    not target_tab_id or str(ctx.tab_id or "").strip() == target_tab_id
                ):
                    return ctx.request_id
            return None

    def cancel_current(self, reason: str = "manual", tab_id: str = None) -> bool:
        """取消当前正在执行的请求。传入 tab_id 时仅取消该标签页关联请求。"""
        cancelled = False
        for ctx in self.get_running_requests(tab_id=tab_id):
            if self.cancel_request(ctx.request_id, reason):
                cancelled = True
        return cancelled
    
    def force_release(self, tab_id: str = None) -> bool:
        """兼容旧接口 - 强制取消运行中的请求。传入 tab_id 时仅取消该标签页请求。"""
        return self.cancel_current("force_release", tab_id=tab_id)


# ================= 全局单例 =================

request_manager = RequestManager()


# ================= 辅助函数 =================

async def watch_client_disconnect(request, ctx: RequestContext,
                                   check_interval: float = 0.5):
    """监控客户端连接状态"""
    try:
        while not ctx.is_terminal():
            if await request.is_disconnected():
                ctx.request_cancel("client_disconnected")
                break
            
            await asyncio.sleep(check_interval)
    
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"断开检测异常: {e}")
