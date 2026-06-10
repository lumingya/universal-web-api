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
import math
import hashlib
import copy
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from collections import OrderedDict

from app.core.config import get_logger, _request_context
from app.services.sse_utils import iter_sse_payloads
from app.utils.site_url import get_canonical_route_domain

logger = get_logger("REQUEST")
MAX_CAPTURED_RESPONSE_CHARS = 30000
MAX_SSE_CHUNK_BUFFER_CHARS = 262144


def _get_positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        return default
    return value if value > 0 else default


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
    _sse_chunk_buffer: str = field(default="", repr=False)
    
    # v2.0 新增：关联的标签页 ID
    tab_id: Optional[str] = None
    monitor: Dict[str, Any] = field(default_factory=dict)
    
    def should_stop(self) -> bool:
        with self._lock:
            return self._cancel_flag
    
    def request_cancel(self, reason: str = "unknown"):
        with self._lock:
            if self.status in (
                RequestStatus.COMPLETED,
                RequestStatus.CANCELLED,
                RequestStatus.FAILED,
            ):
                return

            if self._cancel_flag:
                return

            self._cancel_flag = True
            self.cancel_reason = reason
            if self.finished_at is None:
                self.finished_at = time.time()
            self.status = RequestStatus.CANCELLED

            logger.info(f"[{self.request_id}] 取消 ({reason})")

    def mark_worker_stop_requested(self, reason: str = "worker_stop_requested"):
        with self._lock:
            self._cancel_flag = True
            self.cancel_reason = reason
            if self.finished_at is None:
                self.finished_at = time.time()

    def mark_running(self, tab_id: str = None):
        with self._lock:
            self.started_at = time.time()
            self.finished_at = None
            self.tab_id = tab_id
            if self._cancel_flag:
                self.status = RequestStatus.CANCELLED
                if self.finished_at is None or self.finished_at < self.started_at:
                    self.finished_at = self.started_at
            else:
                self.status = RequestStatus.RUNNING
    
    def mark_completed(self):
        with self._lock:
            if self.status == RequestStatus.RUNNING or (
                self.status == RequestStatus.CANCELLED
                and self.cancel_reason in {
                    "audio_media_fast_return",
                    "stop_sequence",
                    "stream_done",
                }
            ):
                self.status = RequestStatus.COMPLETED
            self.finished_at = time.time()
    
    def mark_failed(self, reason: str = None):
        with self._lock:
            self._cancel_flag = True
            self.status = RequestStatus.FAILED
            self.finished_at = time.time()
            if reason:
                self.cancel_reason = reason
    
    def get_duration(self) -> float:
        with self._lock:
            end = self.finished_at or time.time()
            start = self.started_at or self.created_at
        return max(0.0, end - start)
    
    def is_terminal(self) -> bool:
        with self._lock:
            return self.status in (
                RequestStatus.COMPLETED,
                RequestStatus.CANCELLED,
                RequestStatus.FAILED,
            )

    def is_running_for_tab(self, tab_id: str = None) -> bool:
        target_tab_id = str(tab_id or "").strip()
        with self._lock:
            if self.status != RequestStatus.RUNNING:
                return False
            if target_tab_id and str(self.tab_id or "").strip() != target_tab_id:
                return False
            return True

    def status_summary(self, now: Optional[float] = None) -> Dict[str, Any]:
        current_time = time.time() if now is None else float(now)
        with self._lock:
            status = self.status
            tab_id = self.tab_id
            if status == RequestStatus.RUNNING:
                duration_end = self.finished_at or current_time
                duration_start = self.started_at or self.created_at
                duration = max(0.0, duration_end - duration_start)
            else:
                duration = 0.0
        return {
            "request_id": self.request_id,
            "status": status,
            "tab_id": tab_id,
            "duration": duration,
        }

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        current_time = time.time() if now is None else float(now)
        with self._lock:
            status = self.status
            created_at = self.created_at
            started_at = self.started_at
            finished_at = self.finished_at
            tab_id = self.tab_id
            cancel_reason = self.cancel_reason
        duration_end = finished_at or current_time
        duration_start = started_at or created_at
        return {
            "request_id": self.request_id,
            "status": status,
            "created_at": created_at,
            "started_at": started_at,
            "finished_at": finished_at,
            "tab_id": tab_id,
            "cancel_reason": cancel_reason,
            "duration": max(0.0, duration_end - duration_start),
            "is_terminal": status in (
                RequestStatus.COMPLETED,
                RequestStatus.CANCELLED,
                RequestStatus.FAILED,
            ),
        }


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
    ZOMBIE_TTL = _get_positive_int_env("REQUEST_ZOMBIE_TTL", 600)

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
        self._stats_save_lock = threading.Lock()
        self._save_schedule_lock = threading.Lock()
        self._history_revision_cache: Optional[tuple[tuple[Any, ...], str]] = None
        self._stats_save_requested = False
        self._history_save_requested = False
        self._stats_save_worker: Optional[threading.Thread] = None
        self._history_save_worker: Optional[threading.Thread] = None

        self.total_requests = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._cleanup_stale_temp_files()
        self._load_stats()
        self._load_history()
        
        self._initialized = True
        
        logger.debug("RequestManager 初始化完成")
        
    def _load_stats(self):
        try:
            if os.path.exists(self._stats_file):
                with open(self._stats_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.total_requests = self._coerce_token_count(data.get("total_requests", 0))
                    self.total_input_tokens = self._coerce_token_count(data.get("total_input_tokens", 0))
                    self.total_output_tokens = self._coerce_token_count(data.get("total_output_tokens", 0))
        except Exception as e:
            logger.debug(f"加载状态失败: {e}")

    def _save_stats(self):
        tmp_path = self._stats_file + ".tmp"
        try:
            with self._stats_save_lock:
                os.makedirs(os.path.dirname(self._stats_file), exist_ok=True)
                payload = {
                    "total_requests": self.total_requests,
                    "total_input_tokens": self.total_input_tokens,
                    "total_output_tokens": self.total_output_tokens,
                }
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self._stats_file)
        except Exception as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            logger.debug(f"保存状态失败: {e}")

    def _cleanup_stale_temp_files(self):
        for path in (self._stats_file + ".tmp", self._history_file + ".tmp"):
            try:
                if os.path.exists(path):
                    os.remove(path)
                    logger.debug(f"清理遗留临时文件: {path}")
            except Exception as e:
                logger.debug(f"清理遗留临时文件失败: {path}, {e}")

    def _load_history(self):
        try:
            if not os.path.exists(self._history_file):
                return
            with open(self._history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            records = data.get("records", data if isinstance(data, list) else [])
            if isinstance(records, list):
                raw_records = [item for item in records if isinstance(item, dict)]
                if self._history_records_are_ordered(raw_records):
                    raw_records = raw_records[-200:]
                normalized_records = [
                    self._normalize_history_record(item) for item in raw_records
                ]
                self._monitor_history = self._sort_history_records(normalized_records)[-200:]
                
                # 如果没有持久化的 token 统计，从已有的 200 条历史请求中求和做初次填充
                if self.total_input_tokens == 0 and self.total_output_tokens == 0:
                    for record in self._monitor_history:
                        estimate = record.get("token_estimate") or {}
                        self.total_input_tokens += estimate.get("prompt", 0)
                        self.total_output_tokens += estimate.get("response", 0)
        except Exception as e:
            logger.debug(f"加载请求历史失败: {e}")
            self._monitor_history = []

    def _save_history(self):
        tmp_path = self._history_file + ".tmp"
        try:
            with self._history_save_lock:
                os.makedirs(os.path.dirname(self._history_file), exist_ok=True)
                with self._history_lock:
                    records = list(self._monitor_history[-200:])
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
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self._history_file)
        except Exception as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            logger.debug(f"保存请求历史失败: {e}")

    def _schedule_stats_save(self) -> None:
        worker_to_start = None
        with self._save_schedule_lock:
            self._stats_save_requested = True
            if self._stats_save_worker and self._stats_save_worker.is_alive():
                return
            worker_to_start = threading.Thread(
                target=self._run_stats_save_worker,
                daemon=True,
                name="request-stats-save",
            )
            self._stats_save_worker = worker_to_start

        worker_to_start.start()

    def _run_stats_save_worker(self) -> None:
        while True:
            with self._save_schedule_lock:
                if not self._stats_save_requested:
                    self._stats_save_worker = None
                    return
                self._stats_save_requested = False
            self._save_stats()

    def _schedule_history_save(self) -> None:
        worker_to_start = None
        with self._save_schedule_lock:
            self._history_save_requested = True
            if self._history_save_worker and self._history_save_worker.is_alive():
                return
            worker_to_start = threading.Thread(
                target=self._run_history_save_worker,
                daemon=True,
                name="request-history-save",
            )
            self._history_save_worker = worker_to_start

        worker_to_start.start()

    def _run_history_save_worker(self) -> None:
        while True:
            with self._save_schedule_lock:
                if not self._history_save_requested:
                    self._history_save_worker = None
                    return
                self._history_save_requested = False
            self._save_history()

    @staticmethod
    def _sanitize_text_for_storage(value: Any, max_chars: int = 80000) -> str:
        text = "" if value is None else str(value)
        if not text:
            return ""

        if "[内容已截断，原始长度" in text:
            return text

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

        canonical_route_domain = cls._canonical_monitor_domain(normalized.get("route_domain"))
        canonical_target_domain = cls._canonical_monitor_domain(normalized.get("target_domain"))
        if canonical_route_domain:
            normalized["route_domain"] = canonical_route_domain
        if canonical_target_domain:
            normalized["target_domain"] = canonical_target_domain
        elif canonical_route_domain:
            normalized["target_domain"] = canonical_route_domain

        status_value = str(normalized.get("status") or "").strip()
        has_meaningful_response = bool(response_text.strip()) or media_count > 0
        stop_sequence_completed = (
            status_value == RequestStatus.COMPLETED.value
            and (
                str(normalized.get("error_code") or "").strip() == "stop_sequence"
                or str(normalized.get("error_message") or "").strip() == "stop_sequence"
                or str(normalized.get("cancel_reason") or "").strip()
                in {"audio_media_fast_return", "stop_sequence", "stream_done"}
            )
        )
        if (
            status_value == RequestStatus.COMPLETED.value
            and not has_meaningful_response
            and not stop_sequence_completed
        ):
            normalized["status"] = RequestStatus.FAILED.value
            normalized["success"] = False
            normalized["error_code"] = "empty_response"
            normalized["error_message"] = error_message or "请求流程已结束，但没有捕获到 AI 响应文本或媒体结果。"
            normalized["error_stack"] = normalized["error_stack"] or normalized["error_message"]
        elif status_value == RequestStatus.COMPLETED.value:
            normalized["success"] = True
            if stop_sequence_completed and str(normalized.get("error_code") or "").strip() in {"", "stop_sequence"}:
                normalized["error_code"] = ""
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
        else:
            prompt_tokens = cls._coerce_token_count(token_estimate.get("prompt"))
            response_tokens = cls._coerce_token_count(token_estimate.get("response"))
            chars = cls._coerce_token_count(token_estimate.get("chars"))
            if not chars:
                chars = len(prompt_text) + len(response_text)
            normalized["token_estimate"] = {
                "prompt": prompt_tokens,
                "response": response_tokens,
                "total": prompt_tokens + response_tokens,
                "chars": chars,
            }

        normalized["history_key"] = cls._make_history_key(normalized)

        return normalized

    @staticmethod
    def _canonical_monitor_domain(value: Any) -> str:
        text = str(value or "").strip()
        if not text or text == "未知域名":
            return ""
        try:
            return get_canonical_route_domain(text) or text
        except Exception:
            return text

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
                    if item_type in {"text", "input_text", "output_text"}:
                        parts.append(str(item.get("text") or ""))
                    elif item_type in {"image_url", "input_image", "output_image"}:
                        parts.append("[图片占位符]")
                    elif item_type in {"input_audio", "audio_url", "output_audio"}:
                        parts.append("[音频占位符]")
                    elif item_type in {"input_video", "video_url", "output_video"}:
                        parts.append("[视频占位符]")
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
            if str(value.get("type") or "").strip() in {
                "image_url",
                "input_image",
                "output_image",
                "input_audio",
                "audio_url",
                "output_audio",
                "input_video",
                "video_url",
                "output_video",
            }:
                return True
            if any(
                key in value
                for key in (
                    "image_url",
                    "images",
                    "media",
                    "input_audio",
                    "audio_url",
                    "input_video",
                    "video_url",
                )
            ):
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
    def _coerce_token_count(value: Any) -> int:
        try:
            count = int(value)
        except Exception:
            return 0
        return max(0, count)

    @classmethod
    def _history_record_token_counts(cls, record: Dict[str, Any]) -> tuple[int, int]:
        if not isinstance(record, dict):
            return (0, 0)
        token_estimate = record.get("token_estimate")
        if not isinstance(token_estimate, dict):
            return (0, 0)
        return (
            cls._coerce_token_count(token_estimate.get("prompt")),
            cls._coerce_token_count(token_estimate.get("response")),
        )

    @staticmethod
    def _coerce_timestamp(value: Any) -> float:
        try:
            timestamp = float(value)
        except Exception:
            return 0.0
        if not math.isfinite(timestamp) or timestamp <= 0:
            return 0.0
        if timestamp > 1_000_000_000_000:
            return timestamp / 1000.0
        return timestamp

    @classmethod
    def _history_sort_value(cls, record: Dict[str, Any]) -> float:
        if not isinstance(record, dict):
            return 0.0
        return (
            cls._coerce_timestamp(record.get("finished_at"))
            or cls._coerce_timestamp(record.get("started_at"))
            or cls._coerce_timestamp(record.get("created_at"))
        )

    @classmethod
    def _make_history_key(cls, record: Dict[str, Any]) -> str:
        request_id = str(record.get("id") or "").strip() or "request"
        created_at = cls._coerce_timestamp(record.get("created_at"))
        finished_at = cls._coerce_timestamp(record.get("finished_at"))
        return f"{request_id}:{created_at:.6f}:{finished_at:.6f}"

    @classmethod
    def _sort_history_records(cls, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(records, key=cls._history_order_key)

    @classmethod
    def _history_records_are_ordered(cls, records: List[Dict[str, Any]]) -> bool:
        previous_key: Optional[tuple[float, float, str]] = None
        for record in records:
            current_key = cls._history_order_key(record)
            if previous_key is not None and current_key < previous_key:
                return False
            previous_key = current_key
        return True

    @classmethod
    def _history_order_key(cls, record: Dict[str, Any]) -> tuple[float, float, str]:
        if not isinstance(record, dict):
            return (0.0, 0.0, "")
        return (
            cls._history_sort_value(record),
            cls._coerce_timestamp(record.get("created_at")),
            str(record.get("history_key") or record.get("id") or ""),
        )

    @classmethod
    def _append_sorted_history_record_unlocked(
        cls,
        records: List[Dict[str, Any]],
        record: Dict[str, Any],
        *,
        max_records: int = 200,
    ) -> None:
        key = cls._history_order_key(record)
        left = 0
        right = len(records)
        while left < right:
            mid = (left + right) // 2
            if cls._history_order_key(records[mid]) <= key:
                left = mid + 1
            else:
                right = mid

        records.insert(left, record)
        overflow = len(records) - max(1, int(max_records or 200))
        if overflow > 0:
            del records[:overflow]

    def _replace_monitor_history_record_unlocked(
        self,
        record: Dict[str, Any],
        *,
        previous_history_key: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Replace a previously recorded monitor item, preserving sorted order."""
        target_key = str(previous_history_key or "").strip()
        target_id = str(record.get("id") or "").strip()
        target_created_at = self._coerce_timestamp(record.get("created_at"))

        replace_index: Optional[int] = None
        if target_key:
            for index, item in enumerate(self._monitor_history):
                if str(item.get("history_key") or "").strip() == target_key:
                    replace_index = index
                    break

        if replace_index is None and target_id:
            for index in range(len(self._monitor_history) - 1, -1, -1):
                item = self._monitor_history[index]
                if str(item.get("id") or "").strip() != target_id:
                    continue
                item_created_at = self._coerce_timestamp(item.get("created_at"))
                if (
                    not target_created_at
                    or not item_created_at
                    or abs(item_created_at - target_created_at) < 0.001
                ):
                    replace_index = index
                    break

        previous_record = None
        if replace_index is not None:
            previous_record = self._monitor_history.pop(replace_index)

        self._append_sorted_history_record_unlocked(self._monitor_history, record)
        self._history_revision_cache = None
        return previous_record

    @staticmethod
    def _extract_response_text(payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if not isinstance(payload, dict):
            return "" if payload is None else str(payload)
        if isinstance(payload.get("response"), dict):
            nested_text = RequestManager._extract_response_text(payload["response"])
            if nested_text:
                return nested_text

        responses_event_text = RequestManager._extract_responses_event_text(payload)
        if responses_event_text:
            return responses_event_text

        try:
            choices = payload.get("choices") or []
            if choices:
                first = choices[0] or {}
                message = first.get("message") or {}
                delta = first.get("delta") or {}
                content = message.get("content")
                if content is None:
                    content = delta.get("content")
                text = RequestManager._content_to_text(content)
                if text:
                    return text
                tool_text = RequestManager._extract_tool_calls_text(
                    message.get("tool_calls") or delta.get("tool_calls")
                )
                if tool_text:
                    return tool_text
        except Exception:
            pass

        responses_text = RequestManager._extract_responses_output_text(payload)
        if responses_text:
            return responses_text

        anthropic_text = RequestManager._extract_anthropic_content_text(payload)
        if anthropic_text:
            return anthropic_text

        if "error" in payload:
            error = payload.get("error") or {}
            if isinstance(error, dict):
                return str(error.get("message") or "")
        return ""

    @staticmethod
    def _extract_tool_calls_text(tool_calls: Any) -> str:
        if not isinstance(tool_calls, list):
            return ""

        parts: List[str] = []
        for index, item in enumerate(tool_calls):
            if not isinstance(item, dict):
                continue
            function_data = item.get("function") if isinstance(item.get("function"), dict) else {}
            name = str(
                function_data.get("name")
                or item.get("name")
                or item.get("tool_name")
                or ""
            ).strip()
            call_id = str(item.get("id") or "").strip()
            label = name or call_id or f"tool_call_{index}"
            parts.append(f"[tool_call] {label}")
        return "\n".join(parts)

    @staticmethod
    def _extract_error_code(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        error = payload.get("error")
        if not isinstance(error, dict):
            return ""
        return str(error.get("code") or error.get("type") or "").strip()

    @staticmethod
    def _extract_error_payload(payload: Any) -> Dict[str, str]:
        if not isinstance(payload, dict):
            return {}
        if isinstance(payload.get("response"), dict):
            nested_error = RequestManager._extract_error_payload(payload["response"])
            if nested_error:
                return nested_error
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            message = error.strip()
            return {
                "message": message,
                "code": "execution_error",
                "stack": message,
            }
        if not isinstance(error, dict):
            return {}
        message = str(error.get("message") or "").strip()
        code = str(error.get("code") or error.get("type") or "execution_error").strip()
        stack = json.dumps(error, ensure_ascii=False)
        return {
            "message": message,
            "code": code or "execution_error",
            "stack": stack,
        }

    @staticmethod
    def _extract_media_items(payload: Any) -> List[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        if isinstance(payload.get("response"), dict):
            return RequestManager._extract_media_items(payload["response"])

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
                content_media = RequestManager._extract_chat_content_media(source.get("content"))
                if content_media:
                    media_items.extend(content_media)

        media_items.extend(RequestManager._extract_responses_event_media(payload))
        media_items.extend(RequestManager._extract_responses_output_media(payload))
        return media_items

    @staticmethod
    def _extract_chat_content_media(content: Any) -> List[Dict[str, Any]]:
        if isinstance(content, dict):
            content = [content]
        if not isinstance(content, list):
            return []

        media_items: List[Dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").strip()
            ref = ""
            media_type = ""
            if part_type in {"image_url", "input_image", "output_image"}:
                image_value = part.get("image_url") or part.get("url") or {}
                ref = str(
                    image_value.get("url") if isinstance(image_value, dict) else image_value
                ).strip()
                media_type = "image"
            elif part_type in {"input_audio", "audio_url", "output_audio"}:
                audio_value = part.get("audio_url") or part.get("input_audio") or part.get("url") or {}
                ref = str(
                    audio_value.get("url") if isinstance(audio_value, dict) else audio_value
                ).strip()
                media_type = "audio"
            elif part_type in {"input_video", "video_url", "output_video"}:
                video_value = part.get("video_url") or part.get("url") or {}
                ref = str(
                    video_value.get("url") if isinstance(video_value, dict) else video_value
                ).strip()
                media_type = "video"
            if not ref:
                continue

            media_item: Dict[str, Any] = {"media_type": media_type}
            if ref.startswith("data:"):
                media_item["data_uri"] = ref
            else:
                media_item["url"] = ref
            detail = str(part.get("detail") or "").strip()
            if detail:
                media_item["detail"] = detail
            mime = str(part.get("mime_type") or part.get("mime") or "").strip()
            if mime:
                media_item["mime"] = mime
            label = str(part.get("label") or "").strip()
            if label:
                media_item["label"] = label
            media_items.append(media_item)
        return media_items

    @staticmethod
    def _extract_responses_output_text(payload: Dict[str, Any]) -> str:
        output = payload.get("output")
        if not isinstance(output, list):
            return ""

        parts: List[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_type = str(part.get("type") or "").strip()
                    if part_type == "output_text":
                        text = str(part.get("text") or "")
                        if text:
                            parts.append(text)
                    elif part_type in {"output_image", "output_audio", "output_video"}:
                        ref = RequestManager._extract_media_ref(
                            part.get("image_url") or part.get("audio_url") or part.get("video_url")
                        )
                        if ref:
                            parts.append(ref)
            elif item.get("type") == "function_call":
                name = str(item.get("name") or "").strip()
                if name:
                    parts.append(f"[function_call] {name}")
        return "\n".join(parts)

    @staticmethod
    def _extract_responses_event_text(payload: Dict[str, Any]) -> str:
        event_type = str(payload.get("type") or "").strip()
        if event_type == "response.output_text.delta":
            return str(payload.get("delta") or "")
        if event_type == "response.content_part.added":
            part = payload.get("part")
            if not isinstance(part, dict):
                return ""
            part_type = str(part.get("type") or "").strip()
            if part_type == "output_text":
                return str(part.get("text") or "")
        if event_type == "response.function_call_arguments.done":
            name = str(payload.get("name") or "").strip()
            if name:
                return f"[function_call] {name}"
        return ""

    @staticmethod
    def _extract_responses_event_media(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        event_type = str(payload.get("type") or "").strip()
        if event_type != "response.content_part.added":
            return []

        part = payload.get("part")
        if not isinstance(part, dict):
            return []

        return RequestManager._extract_responses_output_media({
            "output": [{
                "content": [part],
            }],
        })

    @staticmethod
    def _extract_anthropic_content_text(payload: Dict[str, Any]) -> str:
        content = payload.get("content")
        if not isinstance(content, list):
            return ""

        parts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip()
            if item_type == "text":
                text = str(item.get("text") or "")
                if text:
                    parts.append(text)
            elif item_type == "tool_use":
                name = str(item.get("name") or "").strip()
                if name:
                    parts.append(f"[tool_use] {name}")
        return "\n".join(parts)

    @staticmethod
    def _extract_media_ref(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("url") or value.get("data_uri") or "").strip()
        return str(value or "").strip()

    @staticmethod
    def _extract_responses_output_media(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        output = payload.get("output")
        if not isinstance(output, list):
            return []

        media_items: List[Dict[str, Any]] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "").strip()
                if part_type == "output_image":
                    ref = RequestManager._extract_media_ref(part.get("image_url"))
                    media_type = "image"
                elif part_type == "output_audio":
                    ref = RequestManager._extract_media_ref(part.get("audio_url"))
                    media_type = "audio"
                elif part_type == "output_video":
                    ref = RequestManager._extract_media_ref(part.get("video_url"))
                    media_type = "video"
                else:
                    continue
                if not ref:
                    continue
                media_item: Dict[str, Any] = {"media_type": media_type}
                if ref.startswith("data:"):
                    media_item["data_uri"] = ref
                else:
                    media_item["url"] = ref
                mime = str(part.get("mime_type") or "").strip()
                if mime:
                    media_item["mime"] = mime
                label = str(part.get("label") or "").strip()
                if label:
                    media_item["label"] = label
                media_items.append(media_item)
        return media_items

    @staticmethod
    def _iter_sse_payloads(chunk: str) -> List[Dict[str, Any]]:
        return iter_sse_payloads(chunk)

    def _iter_sse_payloads_for_context(
        self,
        ctx: RequestContext,
        chunk: str,
    ) -> List[Dict[str, Any]]:
        """Parse complete SSE frames while preserving split-frame tails per request."""
        if not isinstance(chunk, str):
            return []

        text = chunk.replace("\r\n", "\n").replace("\r", "\n")
        with ctx._lock:
            combined = f"{ctx._sse_chunk_buffer}{text}"
            if "\n\n" not in combined:
                ctx._sse_chunk_buffer = combined[-MAX_SSE_CHUNK_BUFFER_CHARS:]
                return []

            segments = combined.split("\n\n")
            complete_segments = segments[:-1]
            tail = segments[-1]
            ctx._sse_chunk_buffer = tail[-MAX_SSE_CHUNK_BUFFER_CHARS:] if tail else ""

        if not complete_segments:
            return []
        return self._iter_sse_payloads("\n\n".join(complete_segments) + "\n\n")

    def _flush_sse_payloads_for_context(self, ctx: RequestContext) -> List[Dict[str, Any]]:
        """Parse a final unterminated SSE frame before request history is written."""
        if ctx is None or not hasattr(ctx, "_sse_chunk_buffer"):
            return []

        with ctx._lock:
            tail = ctx._sse_chunk_buffer
            ctx._sse_chunk_buffer = ""

        if not tail:
            return []
        return self._iter_sse_payloads(f"{tail}\n\n")

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

        # 计算完整、未截断的 Prompt 估算 Token
        raw_prompt_for_tokens = ""
        if isinstance(messages, list):
            raw_prompt_for_tokens = "\n\n".join(
                f"{msg.get('role', 'message')}: {self._content_to_text(msg.get('content'))}"
                for msg in messages if isinstance(msg, dict)
            )
        else:
            raw_prompt_for_tokens = str(messages or "")
        prompt_tokens = self._estimate_tokens(raw_prompt_for_tokens)

        with ctx._lock:
            monitor_route_domain = self._canonical_monitor_domain(route_domain)
            ctx.monitor.update({
                "endpoint": endpoint,
                "route_domain": monitor_route_domain,
                "target_domain": monitor_route_domain,
                "tab_index": tab_index,
                "preset_name": resolved_preset,
                "model": model,
                "request_type": request_type,
                "is_stream": is_stream,
                "is_multimodal": self._has_multimodal_payload(messages),
                "prompt": prompt_text,
                "prompt_tokens": prompt_tokens,
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
                if key in {"target_domain", "route_domain"}:
                    value = self._canonical_monitor_domain(value)
                    if not value and key == "route_domain":
                        continue
                ctx.monitor[key] = self._sanitize_for_storage(value)
        return True

    def capture_response_chunk(self, ctx: RequestContext, chunk: Any) -> None:
        if ctx is None or not isinstance(chunk, str):
            return

        payloads = self._iter_sse_payloads_for_context(ctx, chunk)
        if not payloads:
            return

        self._capture_response_payloads(ctx, payloads)

    def _capture_response_payloads(
        self,
        ctx: RequestContext,
        payloads: List[Dict[str, Any]],
    ) -> None:
        if ctx is None or not payloads:
            return

        with ctx._lock:
            for payload in payloads:
                error = payload.get("error") if isinstance(payload, dict) else None
                payload_error = self._extract_error_payload(payload)
                text = "" if payload_error else self._extract_response_text(payload)
                media_items = self._extract_media_items(payload)

                if text:
                    parts = ctx.monitor.setdefault("response_parts", [])
                    if isinstance(parts, list):
                        current_len = ctx.monitor.get("response_parts_chars")
                        if not isinstance(current_len, int):
                            current_len = sum(len(str(item)) for item in parts)
                        current_len = max(0, current_len)
                        remaining = max(0, MAX_CAPTURED_RESPONSE_CHARS - current_len)
                        if remaining > 0:
                            captured = text[:remaining]
                            parts.append(captured)
                            ctx.monitor["response_parts_chars"] = current_len + len(captured)
                        else:
                            ctx.monitor["response_parts_chars"] = current_len
                if media_items:
                    existing = ctx.monitor.setdefault("media_items", [])
                    if isinstance(existing, list):
                        existing.extend(self._sanitize_for_storage(media_items))
                    ctx.monitor["has_response_media"] = True
                    ctx.monitor["is_multimodal"] = True
                if payload_error:
                    error_message = str(payload_error.get("message") or "")
                    if error_message:
                        ctx.monitor["error_message"] = self._sanitize_text_for_storage(error_message, max_chars=20000)
                    ctx.monitor["error_code"] = str(payload_error.get("code") or "execution_error")
                    ctx.monitor["error_stack"] = self._sanitize_text_for_storage(
                        payload_error.get("stack") or error_message,
                        max_chars=40000,
                    )

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
        payload_error = self._extract_error_payload(payload)
        payload_error_code = str(payload_error.get("code") or self._extract_error_code(payload)).strip()

        # 计算完整、未截断的 Completion 估算 Token
        response_tokens = self._estimate_tokens(text)

        with ctx._lock:
            if text:
                ctx.monitor["response_text"] = self._sanitize_text_for_storage(text, max_chars=30000)
            if media_items:
                ctx.monitor["media_items"] = self._sanitize_for_storage(media_items)
                ctx.monitor["has_response_media"] = True
                ctx.monitor["is_multimodal"] = True
            ctx.monitor["response_payload"] = sanitized_payload
            ctx.monitor["response_tokens"] = response_tokens
            if payload_error:
                error_message = str(payload_error.get("message") or "")
                if error_message:
                    ctx.monitor["error_message"] = self._sanitize_text_for_storage(error_message, max_chars=20000)
                ctx.monitor["error_code"] = payload_error_code or str(error_code or "execution_error")
                ctx.monitor["error_stack"] = self._sanitize_text_for_storage(
                    payload_error.get("stack") or error_message,
                    max_chars=40000,
                )
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

    def _compact_context_monitor_after_history_append(
        self,
        monitor: Dict[str, Any],
        *,
        prompt_text: str,
        response_text: str,
        prompt_tokens: int,
        response_tokens: int,
    ) -> None:
        """Release heavy request payload references after history has a durable summary."""
        monitor["prompt"] = prompt_text
        monitor["prompt_tokens"] = prompt_tokens
        if response_text:
            monitor["response_text"] = response_text
        monitor["response_tokens"] = response_tokens
        for key in (
            "payload",
            "response_payload",
            "response_parts",
            "response_parts_chars",
            "media_items",
        ):
            monitor.pop(key, None)

    def _append_monitor_history(self, ctx: RequestContext) -> None:
        tail_payloads = self._flush_sse_payloads_for_context(ctx)
        if tail_payloads:
            self._capture_response_payloads(ctx, tail_payloads)

        with ctx._lock:
            already_recorded = bool(ctx.monitor.get("_history_recorded"))
            previous_history_key = str(ctx.monitor.get("_history_key") or "")
            if not already_recorded:
                ctx.monitor["_history_recorded"] = True
            monitor = dict(ctx.monitor)

        snapshot = ctx.snapshot()
        created_at = float(snapshot["created_at"] or time.time())
        started_at = float(snapshot["started_at"] or created_at)
        finished_at = float(snapshot["finished_at"] or time.time())
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

        prompt_tokens = monitor.get("prompt_tokens")
        if prompt_tokens is None:
            prompt_tokens = self._estimate_tokens(prompt_text)
        else:
            prompt_tokens = self._coerce_token_count(prompt_tokens)

        response_tokens = monitor.get("response_tokens")
        if response_tokens is None:
            response_tokens = self._estimate_tokens(response_text)
        else:
            response_tokens = self._coerce_token_count(response_tokens)
        media_items = monitor.get("media_items") if isinstance(monitor.get("media_items"), list) else []
        has_meaningful_response = bool(response_text.strip()) or len(media_items) > 0

        status = snapshot["status"]
        status_value = status.value if isinstance(status, RequestStatus) else str(status)
        stop_sequence_completed = (
            status_value == RequestStatus.COMPLETED.value
            and str(snapshot["cancel_reason"] or "").strip()
            in {"audio_media_fast_return", "stop_sequence", "stream_done"}
        )
        success = (
            status_value == RequestStatus.COMPLETED.value
            and (has_meaningful_response or stop_sequence_completed)
        )
        error_message = self._sanitize_text_for_storage(
            monitor.get("error_message") or snapshot["cancel_reason"] or "",
            max_chars=10000,
        )
        error_code = str(monitor.get("error_code") or (status_value if not success else "") or "").strip()
        if success and stop_sequence_completed:
            error_message = ""
            error_code = ""

        if (
            status_value == RequestStatus.COMPLETED.value
            and not has_meaningful_response
            and not stop_sequence_completed
        ):
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
            "target_domain": self._canonical_monitor_domain(monitor.get("target_domain") or monitor.get("route_domain")) or "未知域名",
            "route_domain": self._canonical_monitor_domain(monitor.get("route_domain")),
            "preset_name": str(monitor.get("preset_name") or "默认预设"),
            "tab_index": monitor.get("tab_index"),
            "tab_id": snapshot["tab_id"] or monitor.get("tab_id") or "",
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
            "cancel_reason": str(snapshot["cancel_reason"] or ""),
            "media_count": len(media_items),
            "token_estimate": {
                "prompt": prompt_tokens,
                "response": response_tokens,
                "total": prompt_tokens + response_tokens,
                "chars": len(prompt_text) + len(response_text),
            },
        }
        record = self._normalize_history_record(record)
        with ctx._lock:
            ctx.monitor["_history_key"] = str(record.get("history_key") or "")
            self._compact_context_monitor_after_history_append(
                ctx.monitor,
                prompt_text=prompt_text,
                response_text=response_text,
                prompt_tokens=prompt_tokens,
                response_tokens=response_tokens,
            )

        previous_record = None
        with self._history_lock:
            if already_recorded:
                previous_record = self._replace_monitor_history_record_unlocked(
                    record,
                    previous_history_key=previous_history_key,
                )
            else:
                self._append_sorted_history_record_unlocked(self._monitor_history, record)
                self._history_revision_cache = None

        if not already_recorded:
            prompt_delta = prompt_tokens
            response_delta = response_tokens
        else:
            previous_prompt_tokens, previous_response_tokens = self._history_record_token_counts(
                previous_record or {}
            )
            prompt_delta = prompt_tokens - previous_prompt_tokens
            response_delta = response_tokens - previous_response_tokens

        if prompt_delta or response_delta:
            with self._requests_lock:
                self.total_input_tokens = max(0, self.total_input_tokens + prompt_delta)
                self.total_output_tokens = max(0, self.total_output_tokens + response_delta)
                self._schedule_stats_save()

        self._schedule_history_save()

    @staticmethod
    def _history_revision_unlocked(records: List[Dict[str, Any]]) -> str:
        if not records:
            return "0::0"
        latest = max(
            (item for item in records if isinstance(item, dict)),
            key=RequestManager._history_sort_value,
            default={},
        )
        latest_time = RequestManager._history_sort_value(latest)
        latest_key = latest.get("history_key") or RequestManager._make_history_key(latest)
        digest = hashlib.blake2s(digest_size=8)
        for item in records:
            if not isinstance(item, dict):
                continue
            for part in RequestManager._history_revision_parts(item):
                digest.update(str(part).encode("utf-8", errors="replace"))
                digest.update(b"\x1f")
            digest.update(b"\x1e")
        return f"{len(records)}:{latest_key}:{latest_time}:{digest.hexdigest()}"

    @staticmethod
    def _history_revision_cache_key(records: List[Dict[str, Any]]) -> tuple[Any, ...]:
        if not records:
            return (0,)
        return tuple(
            (
                id(item),
                RequestManager._history_revision_cache_parts(item),
            )
            for item in records
            if isinstance(item, dict)
        )

    @staticmethod
    def _history_revision_text_cache_marker(value: Any) -> tuple[int, int]:
        if value is None or value == "":
            return (0, 0)
        text = str(value)
        return (id(value), len(text))

    @staticmethod
    def _history_revision_cache_parts(record: Dict[str, Any]) -> tuple[Any, ...]:
        token_estimate = record.get("token_estimate")
        if not isinstance(token_estimate, dict):
            token_estimate = {}
        return (
            record.get("history_key") or RequestManager._make_history_key(record),
            RequestManager._history_sort_value(record),
            record.get("status"),
            record.get("success"),
            record.get("target_domain"),
            record.get("route_domain"),
            record.get("preset_name"),
            record.get("tab_index"),
            record.get("tab_id"),
            record.get("model"),
            record.get("endpoint"),
            record.get("request_type"),
            record.get("is_stream"),
            record.get("is_multimodal"),
            record.get("duration_ms"),
            record.get("queue_ms"),
            record.get("generation_ms"),
            RequestManager._history_revision_text_cache_marker(record.get("summary")),
            record.get("error_code"),
            RequestManager._history_revision_text_cache_marker(record.get("error_message")),
            RequestManager._history_revision_text_cache_marker(record.get("prompt")),
            RequestManager._history_revision_text_cache_marker(record.get("response")),
            RequestManager._history_revision_text_cache_marker(record.get("error_stack")),
            record.get("media_count"),
            token_estimate.get("prompt"),
            token_estimate.get("response"),
            token_estimate.get("total"),
            token_estimate.get("chars"),
        )

    def _get_history_revision_cached(self, records: List[Dict[str, Any]]) -> str:
        cache_key = self._history_revision_cache_key(records)
        cache = getattr(self, "_history_revision_cache", None)
        if cache and cache[0] == cache_key:
            return cache[1]
        revision = self._history_revision_unlocked(records)
        self._history_revision_cache = (cache_key, revision)
        return revision

    @staticmethod
    def _history_revision_text_part(value: Any) -> str:
        text = str(value or "")
        if not text:
            return ""
        digest = hashlib.blake2s(text.encode("utf-8", errors="replace"), digest_size=8).hexdigest()
        return f"{len(text)}:{digest}"

    @staticmethod
    def _history_revision_parts(record: Dict[str, Any]) -> tuple[Any, ...]:
        token_estimate = record.get("token_estimate")
        if not isinstance(token_estimate, dict):
            token_estimate = {}
        return (
            record.get("history_key") or RequestManager._make_history_key(record),
            RequestManager._history_sort_value(record),
            record.get("status"),
            record.get("success"),
            record.get("target_domain"),
            record.get("route_domain"),
            record.get("preset_name"),
            record.get("tab_index"),
            record.get("tab_id"),
            record.get("model"),
            record.get("endpoint"),
            record.get("request_type"),
            record.get("is_stream"),
            record.get("is_multimodal"),
            record.get("duration_ms"),
            record.get("queue_ms"),
            record.get("generation_ms"),
            record.get("summary"),
            record.get("error_code"),
            RequestManager._history_revision_text_part(record.get("error_message")),
            RequestManager._history_revision_text_part(record.get("prompt")),
            RequestManager._history_revision_text_part(record.get("response")),
            RequestManager._history_revision_text_part(record.get("error_stack")),
            record.get("media_count"),
            token_estimate.get("prompt"),
            token_estimate.get("response"),
            token_estimate.get("total"),
            token_estimate.get("chars"),
        )

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
        token_estimate = item.get("token_estimate")
        if isinstance(token_estimate, dict):
            item["token_estimate"] = dict(token_estimate)
        prompt_text = str(item.pop("prompt", "") or "")
        response_text = str(item.pop("response", "") or "")
        error_stack = str(item.pop("error_stack", "") or "")
        item.pop("payload", None)
        item.pop("response_payload", None)
        item.pop("response_parts", None)
        item.pop("response_parts_chars", None)

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

    def get_request_history_payload(
        self,
        limit: int = 200,
        include_detail: bool = False,
        if_revision: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            count = max(1, min(200, int(limit or 200)))
        except Exception:
            count = 200
        requested_revision = str(if_revision or "").strip()
        with self._history_lock:
            history_refs_all = [
                item
                for item in self._monitor_history[-count:]
                if isinstance(item, dict)
            ]
            if not self._history_records_are_ordered(self._monitor_history):
                history_refs_all = [
                    item
                    for item in self._monitor_history
                    if isinstance(item, dict)
                ]
                history_refs_all = self._sort_history_records(history_refs_all)
            history_refs = history_refs_all[-count:]
            revision = self._get_history_revision_cached(history_refs)
            if not include_detail and requested_revision and requested_revision == revision:
                return {
                    "records": [],
                    "count": 0,
                    "max_records": 200,
                    "revision": revision,
                    "not_modified": True,
                }
            history = (
                [copy.deepcopy(item) for item in history_refs]
                if include_detail
                else list(history_refs)
            )

        if include_detail:
            records = list(reversed(history))
        else:
            records = [self._to_history_list_record(item) for item in reversed(history)]

        return {
            "records": records,
            "count": len(records),
            "max_records": 200,
            "revision": revision,
            "not_modified": False,
        }

    def get_request_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self.get_request_history_payload(limit, include_detail=True)["records"]

    def get_request_history_record(self, request_id: str) -> Optional[Dict[str, Any]]:
        request_key = str(request_id or "").strip()
        if not request_key:
            return None

        with self._history_lock:
            if self._history_records_are_ordered(self._monitor_history):
                history_iterable = reversed(self._monitor_history)
            else:
                history = [
                    item
                    for item in self._monitor_history
                    if isinstance(item, dict)
                ]
                history_iterable = reversed(self._sort_history_records(history))

            id_match = None
            for item in history_iterable:
                if not isinstance(item, dict):
                    continue
                if str(item.get("history_key") or "").strip() == request_key:
                    return copy.deepcopy(item)
                if id_match is None and str(item.get("id") or "").strip() == request_key:
                    id_match = item
            if id_match is not None:
                return copy.deepcopy(id_match)
        return None
    
    def create_request(self) -> RequestContext:
        """创建新请求"""
        request_id = self._generate_id()
        ctx = RequestContext(request_id=request_id)
        cleanup_history_contexts: List[RequestContext] = []

        with self._requests_lock:
            self._requests[request_id] = ctx
            cleanup_history_contexts = self._cleanup_old_requests()

        for stale_ctx in cleanup_history_contexts:
            try:
                self._append_monitor_history(stale_ctx)
            except Exception as e:
                logger.debug(f"写入清理请求监控历史失败: {e}")

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
    
    def _cleanup_old_requests(self) -> List[RequestContext]:
        """清理旧请求（修复版：不因单个未完成请求阻塞所有清理）"""
        now = time.time()
        over_capacity = len(self._requests) > self._max_history
        to_delete = []
        history_contexts: List[RequestContext] = []

        for req_id, ctx in list(self._requests.items()):
            snapshot = ctx.snapshot(now)
            status = snapshot["status"]
            # 已终态的可以删除
            if snapshot["is_terminal"]:
                if over_capacity and len(self._requests) - len(to_delete) > self._max_history:
                    to_delete.append(req_id)
                    history_contexts.append(ctx)
            # 超时的 RUNNING 请求视为僵尸，强制标记失败
            elif status == RequestStatus.RUNNING:
                started = snapshot["started_at"] or snapshot["created_at"]
                if now - started > self.ZOMBIE_TTL:
                    logger.warning(
                        f"[{req_id}] 僵尸请求 (运行 {now - started:.0f}s)，强制清理"
                    )
                    ctx.mark_failed("zombie_timeout")
                    to_delete.append(req_id)
                    history_contexts.append(ctx)
            elif status == RequestStatus.QUEUED:
                queued_at = snapshot["created_at"] or now
                if now - queued_at > self.ZOMBIE_TTL:
                    logger.warning(
                        f"[{req_id}] 排队请求超时 (等待 {now - queued_at:.0f}s)，强制清理"
                    )
                    ctx.mark_failed("queued_timeout")
                    to_delete.append(req_id)
                    history_contexts.append(ctx)

        # 批量删除
        for req_id in to_delete:
            del self._requests[req_id]
        
        if to_delete:
            logger.debug(f"清理了 {len(to_delete)} 个旧请求")

        return history_contexts
    
    def start_request(self, ctx: RequestContext, tab_id: str = None):
        """标记请求开始执行"""
        ctx.mark_running(tab_id)
        with self._requests_lock:
            self.total_requests += 1
            self._schedule_stats_save()
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
        with ctx._lock:
            if ctx.status in (
                RequestStatus.COMPLETED,
                RequestStatus.FAILED,
            ):
                pass
            elif ctx._cancel_flag or ctx.status == RequestStatus.CANCELLED:
                ctx.status = RequestStatus.CANCELLED
            elif ctx.status == RequestStatus.RUNNING:
                ctx.status = RequestStatus.COMPLETED if success else RequestStatus.FAILED
            elif ctx.status not in (
                RequestStatus.COMPLETED,
                RequestStatus.FAILED,
            ):
                ctx.status = RequestStatus.COMPLETED if success else RequestStatus.FAILED
            if ctx.finished_at is None:
                ctx.finished_at = time.time()

        snapshot = ctx.snapshot()
        # 设置上下文后记录日志
        token = _request_context.set(ctx.request_id)
        try:
            logger.info(
                f"完成 ({snapshot['duration']:.1f}s, status={snapshot['status'].value}, "
                f"tab={snapshot['tab_id'] or '-'}, reason={snapshot['cancel_reason'] or '-'})"
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
        try:
            self._append_monitor_history(ctx)
        except Exception as e:
            logger.debug(f"写入取消请求监控历史失败: {e}")
        return True

    def get_request(self, request_id: str) -> Optional[RequestContext]:
        with self._requests_lock:
            return self._requests.get(request_id)
    
    def get_running_requests(self, tab_id: str = None) -> list:
        """获取所有正在执行的请求"""
        with self._requests_lock:
            contexts = list(self._requests.values())
        return [ctx for ctx in contexts if ctx.is_running_for_tab(tab_id)]

    def get_status(self) -> Dict[str, Any]:
        """获取管理器状态"""
        with self._requests_lock:
            contexts = list(self._requests.values())

        status_counts = {}
        running = []
        now = time.time()
        for ctx in contexts:
            summary = ctx.status_summary(now)
            status = summary["status"]
            status_value = status.value if isinstance(status, RequestStatus) else str(status)
            status_counts[status_value] = status_counts.get(status_value, 0) + 1
            if status == RequestStatus.RUNNING:
                running.append({
                    "request_id": summary["request_id"],
                    "tab_id": summary["tab_id"],
                    "duration": round(summary["duration"], 1),
                })

        return {
            "running_count": len(running),
            "running_requests": running,
            "total_tracked": len(contexts),
            "status_counts": status_counts,
        }

    # ================= 兼容旧接口 =================

    def is_locked(self) -> bool:
        """兼容旧接口 - 检查是否有正在执行的请求"""
        with self._requests_lock:
            contexts = list(self._requests.values())
        return any(ctx.is_running_for_tab() for ctx in contexts)

    def get_current_request_id(self, tab_id: str = None) -> Optional[str]:
        """兼容旧接口 - 获取当前执行的请求ID（返回第一个）"""
        with self._requests_lock:
            contexts = list(self._requests.values())
        for ctx in contexts:
            if ctx.is_running_for_tab(tab_id):
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
