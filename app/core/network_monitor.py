"""
app/core/network_monitor.py - 网络响应拦截监听器

职责：
- 拦截网络请求响应
- 解析增量数据并流式输出
- 支持超时和取消机制
- 失败时触发回退到 DOM 模式
"""

import time
import logging
import json
import re
from collections import deque
from typing import Generator, Optional, Dict, Callable, Any
from pathlib import Path

from app.core.config import (
    logger,
    SSEFormatter,
    BrowserConstants,
    sanitize_sensitive_data,
)
from app.core.background_image_downloader import (
    background_image_downloader,
    build_image_download_request_context,
    normalize_remote_image_url,
)
from app.core.parsers import ParserRegistry, ResponseParser


def _debug_preview(value: Any, limit: int = 240) -> str:
    text = repr(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


# ================= 自定义异常 =================

class NetworkMonitorTimeout(Exception):
    """网络监听超时异常（触发回退到 DOM 模式）"""
    pass


class NetworkMonitorError(Exception):
    """网络监听错误异常"""
    pass


class NetworkInterceptionTriggered(NetworkMonitorError):
    """网络拦截命中后主动中断当前监听。"""
    pass


class NetworkMonitorTerminalError(NetworkMonitorError):
    """解析器确认当前目标流已失败，应立即终止工作流而非回退 DOM。"""
    pass


class _EventOnlyParser(ResponseParser):
    """仅用于消费网络事件，不输出任何流式内容。"""

    @classmethod
    def get_id(cls) -> str:
        return "event_only"

    def reset(self):
        return None

    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        return {
            "content": "",
            "images": [],
            "done": False,
            "error": None,
        }


# ================= 网络监听器 =================

class NetworkMonitor:
    """
    网络响应拦截监听器
    
    核心流程：
    1. 启动网络监听（page.listen.start）
    2. 循环等待响应（page.listen.wait）
    3. 解析响应增量（parser.parse_chunk）
    4. 流式输出（yield SSE chunk）
    5. 检测结束条件（超时/done/取消）
    
    回退机制：
    - 首次响应超时（5s）→ 抛出 NetworkMonitorTimeout
    - executor 捕获后切换到 StreamMonitor
    """
    
    # 默认超时配置
    DEFAULT_FIRST_RESPONSE_TIMEOUT = 300.0   # 首次响应超时（触发回退）
    DEFAULT_HARD_TIMEOUT = 300             # 全局硬超时
    DEFAULT_RESPONSE_INTERVAL = 0.5        # 响应轮询间隔
    DEFAULT_SILENCE_THRESHOLD = 3.0        # 静默超时（无新数据）
    DEFAULT_FIRST_CONTENT_TIMEOUT = 15.0   # 命中目标流后，等待首个有效正文的宽限
    DEFAULT_INITIAL_TARGET_BODY_WAIT = 4.0  # 首个目标响应空 body 时的补等宽限
    MAX_LISTEN_RESTARTS = 3                # 监听状态异常后的最大重建次数
    MAX_PREFETCHED_RESPONSES = 64          # 发送后预取响应缓存上限，避免噪声网络无限堆积
    MAX_STREAM_CHUNK_MERGE_CACHE = 8       # 活跃流 chunks 合并缓存上限，避免长流重复全量 join
    MAX_RAW_BODY_SIGNATURE_EDGE = 512      # 媒体状态缓存只保留首尾签名片段，避免挂住完整 body
    CANCEL_CHECK_SLICE = 1.0              # 长等待期间的取消检查切片（秒）
    ACTIVE_STREAM_RESPONSE_POLL_TIMEOUT = 0.01  # 锁定 SSE 后仅快速扫队列，不阻塞吐出
    LISTEN_RESTART_BACKOFF = 0.1          # 异常重建后的最小退避，避免忙循环
    
    def __init__(self, tab, formatter: SSEFormatter,
                 parser: ResponseParser,
                 stop_checker: Optional[Callable[[], bool]] = None,
                 stream_config: Optional[Dict] = None,
                 event_handler: Optional[Callable[[Dict[str, Any]], bool]] = None):
        """
        初始化网络监听器
        
        Args:
            tab: DrissionPage 标签页对象
            formatter: SSE 格式化器
            parser: 响应解析器
            stop_checker: 取消检查函数
            stream_config: 流式配置
        """
        self.tab = tab
        self.formatter = formatter
        self.parser = parser
        self._should_stop = stop_checker or (lambda: False)
        self._event_handler = event_handler
        
        # 从配置中加载参数
        self._stream_config = stream_config or {}
        network_config = self._stream_config.get("network", {})
        top_level_hard_timeout = self._stream_config.get(
            "hard_timeout",
            self.DEFAULT_HARD_TIMEOUT
        )

        self._listen_pattern = network_config.get("listen_pattern", "")
        self._stream_match_pattern = network_config.get(
            "stream_match_pattern",
            self._listen_pattern,
        )
        self._stream_match_mode = str(
            network_config.get("stream_match_mode", "keyword") or "keyword"
        ).strip().lower()
        self._hard_timeout = network_config.get(
            "hard_timeout",
            top_level_hard_timeout
        )
        self._first_response_timeout = network_config.get(
            "first_response_timeout",
            self._hard_timeout
        )
        self._response_interval = network_config.get(
            "response_interval",
            self.DEFAULT_RESPONSE_INTERVAL
        )
        self._silence_threshold = network_config.get(
            "silence_threshold",
            self.DEFAULT_SILENCE_THRESHOLD
        )
        first_content_timeout = network_config.get(
            "first_content_timeout",
            max(
                self.DEFAULT_FIRST_CONTENT_TIMEOUT,
                float(self._silence_threshold or self.DEFAULT_SILENCE_THRESHOLD) * 4.0,
            ),
        )
        try:
            first_content_timeout = float(first_content_timeout)
        except Exception:
            first_content_timeout = max(
                self.DEFAULT_FIRST_CONTENT_TIMEOUT,
                float(self._silence_threshold or self.DEFAULT_SILENCE_THRESHOLD) * 4.0,
            )
        self._first_content_timeout = min(
            max(first_content_timeout, float(self._silence_threshold or self.DEFAULT_SILENCE_THRESHOLD)),
            max(float(self._hard_timeout or self.DEFAULT_HARD_TIMEOUT), 1.0),
        )
        initial_target_body_wait = network_config.get(
            "initial_target_body_wait",
            max(
                self.DEFAULT_INITIAL_TARGET_BODY_WAIT,
                float(self._silence_threshold or self.DEFAULT_SILENCE_THRESHOLD)
                + float(self._response_interval or self.DEFAULT_RESPONSE_INTERVAL),
            ),
        )
        try:
            initial_target_body_wait = float(initial_target_body_wait)
        except Exception:
            initial_target_body_wait = max(
                self.DEFAULT_INITIAL_TARGET_BODY_WAIT,
                float(self._silence_threshold or self.DEFAULT_SILENCE_THRESHOLD)
                + float(self._response_interval or self.DEFAULT_RESPONSE_INTERVAL),
            )
        self._initial_target_body_wait = min(
            max(initial_target_body_wait, max(float(self._response_interval or 0.5), 0.2)),
            max(float(self._hard_timeout or self.DEFAULT_HARD_TIMEOUT), 1.0),
        )
                
        # 监听预启动标记（用于提前启动监听）
        self._pre_started = False
        # 状态追踪
        self._is_listening = False
        self._cdp_session_listening = False
        self._total_chunks = 0
        self._total_content_chars = 0
        self._reset_prefetched_responses()
        self._reset_stream_chunk_merge_cache()
        self._debug_capture_counter = 0
        self._debug_capture_session_key = f"{int(time.time() * 1000)}_{id(self):x}"
        self._debug_capture_written_stages = set()
        self._debug_capture_has_content_snapshot = False
        self._last_stream_event: Dict[str, Any] = {}
        self._last_stream_raw_body: str = ""
        self._last_stream_raw_body_len: int = 0
        self._last_stream_raw_body_signature = None
        self._last_stream_parse_result: Dict[str, Any] = {}
        self._reset_media_generation_state_cache()
        self._last_stream_media_items: list[Dict[str, Any]] = []
        self._prefetched_image_urls: set[str] = set()
        self._send_attempt_baseline_targets = 0
        self._send_attempt_baseline_requests = 0
        self._send_attempt_marked_at = 0.0
        
        logger.debug(
            f"[NetworkMonitor] 初始化完成 "
            f"(pattern={self._listen_pattern!r}, "
            f"parser={parser.get_id()}, "
            f"first_content_timeout={self._first_content_timeout})"
        )

    def _handle_parse_result(self, parse_result: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(parse_result, dict):
            return {
                "content": "",
                "images": [],
                "done": False,
                "error": None,
            }

        error_text = str(parse_result.get("error") or "").strip()
        if not error_text:
            return parse_result

        logger.warning(f"[NetworkMonitor] 解析失败: {error_text}")
        should_abort = False
        try:
            should_abort = bool(self.parser.should_abort_on_error())
        except Exception:
            should_abort = False

        if should_abort:
            raise NetworkMonitorTerminalError(error_text)

        return {
            **parse_result,
            "content": "",
            "done": False,
        }

    def _should_fallback_to_dom_on_empty_stream(self) -> bool:
        try:
            return bool(self.parser.should_fallback_to_dom_when_no_visible_content())
        except Exception:
            return False

    @staticmethod
    def _parse_result_has_unclosed_render_output(parse_result: Dict[str, Any]) -> bool:
        return bool(
            isinstance(parse_result, dict)
            and parse_result.get("unclosed_render_output")
        )

    @staticmethod
    def _extract_http_status(event: Dict[str, Any]) -> int:
        try:
            return int(event.get("status") or 0)
        except Exception:
            return 0

    @staticmethod
    def _extract_http_error_detail(raw_body: str) -> str:
        text = str(raw_body or "").strip()
        if not text:
            return ""

        try:
            data = json.loads(text)
        except Exception:
            data = None

        if isinstance(data, dict):
            for key in ("message", "error", "detail", "title", "reason"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return " ".join(value.split())[:240]
                if isinstance(value, dict):
                    nested = value.get("message") or value.get("detail")
                    if isinstance(nested, str) and nested.strip():
                        return " ".join(nested.split())[:240]

        return " ".join(text.split())[:240]

    @classmethod
    def _build_http_status_error_text(cls, event: Dict[str, Any], raw_body: str = "") -> str:
        status_code = cls._extract_http_status(event)
        if status_code <= 0:
            return ""

        reason_map = {
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            408: "Request Timeout",
            409: "Conflict",
            422: "Unprocessable Entity",
            429: "Too Many Requests",
            500: "Internal Server Error",
            502: "Bad Gateway",
            503: "Service Unavailable",
            504: "Gateway Timeout",
        }
        reason = reason_map.get(status_code, "HTTP Error")
        detail = cls._extract_http_error_detail(raw_body)
        if detail:
            return f"HTTP {status_code} {reason}: {detail}"
        return f"HTTP {status_code} {reason}"

    @staticmethod
    def _should_preserve_listener_after_terminal_error(error: Any) -> bool:
        """Keep Network listeners alive for challenge flows that auto-replay the request."""
        detail = str(error or "").strip().lower()
        return (
            "429" in detail
            or "403" in detail
            or "503" in detail
            or "too many requests" in detail
            or "rate limit" in detail
            or "forbidden" in detail
            or "service unavailable" in detail
        )

    def _listen_is_active(self) -> bool:
        try:
            listen = getattr(self.tab, "listen", None)
            driver = getattr(listen, "_driver", None) if listen is not None else None
            return bool(
                listen is not None
                and getattr(listen, "listening", False)
                and driver is not None
                and getattr(driver, "is_running", False)
            )
        except Exception:
            return False

    def _force_reset_listen_state(self):
        listen = getattr(self.tab, "listen", None)
        if listen is None:
            return

        try:
            setattr(listen, "listening", False)
        except Exception:
            pass

        try:
            if hasattr(listen, "_network_enabled"):
                setattr(listen, "_network_enabled", False)
        except Exception:
            pass

        try:
            if hasattr(listen, "_driver"):
                setattr(listen, "_driver", None)
        except Exception:
            pass

        try:
            clear = getattr(listen, "clear", None)
            if callable(clear):
                clear()
        except Exception:
            pass

    def _safe_stop_listen(self):
        listen = getattr(self.tab, "listen", None)
        if listen is None:
            return

        try:
            if getattr(listen, "listening", False):
                listen.stop()
        except Exception:
            self._force_reset_listen_state()
            return

        try:
            clear = getattr(listen, "clear", None)
            if callable(clear):
                clear()
        except Exception:
            pass

    @staticmethod
    def _is_restartable_listen_error(err_text: str) -> bool:
        err_text = str(err_text or "")
        return (
            "监听未启动或已停止" in err_text
            or ("NoneType" in err_text and "is_running" in err_text)
        )

    def _sleep_after_listen_restart(self, attempts: int) -> None:
        delay = min(0.5, self.LISTEN_RESTART_BACKOFF * max(1, int(attempts or 1)))
        time.sleep(delay)

    def _reset_prefetched_responses(self) -> None:
        self._prefetched_responses = deque(maxlen=self.MAX_PREFETCHED_RESPONSES)

    def _reset_stream_chunk_merge_cache(self) -> None:
        self._stream_chunk_merge_cache: Dict[int, Dict[str, Any]] = {}

    def _reset_media_generation_state_cache(self) -> None:
        self._last_media_generation_state: Dict[str, Any] = {}
        self._last_media_generation_state_body_signature = None
        self._last_media_generation_state_raw_body = ""
        self._last_media_generation_state_parse_result: Dict[str, Any] = {}
        self._last_media_generation_state_cached: bool = False

    def _reset_parser_state(self) -> None:
        reset = getattr(self.parser, "reset", None)
        if not callable(reset):
            return
        try:
            reset()
        except Exception as e:
            logger.debug(f"[NetworkMonitor] 重置解析器状态失败（忽略）: {e}")

    def _raw_body_cache_signature(self, raw_body: Any):
        text = str(raw_body or "")
        length = len(text)
        edge = max(1, int(self.MAX_RAW_BODY_SIGNATURE_EDGE))
        if length <= edge * 2:
            return (length, text)
        return (length, hash(text), text[:edge], text[-edge:])

    def _get_media_generation_state_cached(
        self,
        raw_body: Any,
        parse_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        body_signature = self._raw_body_cache_signature(raw_body)
        if (
            self._last_media_generation_state_cached
            and body_signature == getattr(self, "_last_media_generation_state_body_signature", None)
            and parse_result == self._last_media_generation_state_parse_result
        ):
            return dict(self._last_media_generation_state)

        media_state = self.parser.get_media_generation_state(
            raw_response=raw_body,
            parse_result=parse_result,
        )
        normalized = dict(media_state) if isinstance(media_state, dict) else {}
        self._last_media_generation_state = normalized
        self._last_media_generation_state_body_signature = body_signature
        self._last_media_generation_state_raw_body = ""
        self._last_media_generation_state_parse_result = dict(parse_result or {})
        self._last_media_generation_state_cached = True
        return dict(normalized)

    def _raw_body_preview(self, raw_body: Any) -> str:
        text = str(raw_body or "")
        edge = max(1, int(self.MAX_RAW_BODY_SIGNATURE_EDGE))
        if len(text) <= edge * 2:
            return text
        return f"{text[:edge]}...{text[-edge:]}"

    def _remember_last_stream_result(
        self,
        event: Dict[str, Any],
        raw_body: Any,
        parse_result: Dict[str, Any],
    ) -> None:
        self._last_stream_event = dict(event or {})
        text = str(raw_body or "")
        self._last_stream_raw_body = self._raw_body_preview(text)
        self._last_stream_raw_body_len = len(text)
        self._last_stream_raw_body_signature = self._raw_body_cache_signature(text)
        self._last_stream_parse_result = dict(parse_result or {})

    def _remember_prefetched_response(self, response: Any) -> None:
        if response is None or response is False:
            return

        self._prefetched_responses.append(response)

    def _raise_for_http_error_status(
        self,
        event: Dict[str, Any],
        raw_body: str,
        raw_body_source: str = "",
        is_event_stream: bool = False,
    ) -> None:
        status_code = self._extract_http_status(event)
        if status_code < 400:
            return

        error_text = self._build_http_status_error_text(event, raw_body)
        self._write_parser_debug_dump(
            raw_body,
            event,
            {
                "content": "",
                "images": [],
                "done": False,
                "error": error_text or f"HTTP {status_code}",
            },
            raw_body_source or "http_error",
            is_event_stream,
            force_stage="http_error",
            extra_payload={
                "http_status": status_code,
                "recoverable_listener_preserve": self._should_preserve_listener_after_terminal_error(
                    error_text or f"HTTP {status_code}"
                ),
            },
        )
        logger.warning(
            "[NetworkMonitor] 目标流返回异常状态码，终止工作流 "
            f"(status={status_code}, url={event.get('url', '')[:120]}, "
            f"body_len={len(raw_body or '')})"
        )
        raise NetworkMonitorTerminalError(error_text or f"HTTP {status_code}")

    def _pop_prefetched_response(self) -> Optional[Any]:
        if not self._prefetched_responses:
            return None
        return self._prefetched_responses.popleft()

    def _start_listen(self):
        if not self._listen_pattern:
            raise NetworkMonitorError("listen_pattern 未配置")

        self._reset_prefetched_responses()
        self._reset_stream_chunk_merge_cache()
        self.tab.listen._reuse_driver = True
        self.tab.listen.start(self._listen_pattern)
        if not self._listen_is_active():
            raise NetworkMonitorError("监听启动后未进入活动状态")
        self._pre_started = True
        self._is_listening = True

    def _read_listen_counters(self) -> Dict[str, int]:
        listen = getattr(self.tab, "listen", None)
        if listen is None:
            return {
                "running_targets": 0,
                "running_requests": 0,
                "queued_packets": 0,
            }

        try:
            running_targets = int(getattr(listen, "_running_targets", 0) or 0)
        except Exception:
            running_targets = 0

        try:
            running_requests = int(getattr(listen, "_running_requests", 0) or 0)
        except Exception:
            running_requests = 0

        queued_packets = 0
        try:
            caught = getattr(listen, "_caught", None)
            if caught is not None and hasattr(caught, "qsize"):
                queued_packets = int(caught.qsize() or 0)
        except Exception:
            queued_packets = 0

        return {
            "running_targets": max(0, running_targets),
            "running_requests": max(0, running_requests),
            "queued_packets": max(0, queued_packets),
        }

    def mark_send_attempt(self):
        """Record the listener baseline immediately before a submit action."""
        if not self._listen_pattern:
            return

        try:
            if not self._listen_is_active():
                self._ensure_listening("mark_send_attempt")
        except Exception as e:
            logger.debug(f"[NetworkMonitor] 记录发送基线失败: {e}")
            return

        snapshot = self._read_listen_counters()
        self._send_attempt_baseline_targets = snapshot["running_targets"]
        self._send_attempt_baseline_requests = snapshot["running_requests"]
        self._send_attempt_marked_at = time.time()
        logger.debug(
            "[NetworkMonitor] 已记录发送基线 "
            f"(targets={snapshot['running_targets']}, "
            f"requests={snapshot['running_requests']}, "
            f"queued={snapshot['queued_packets']})"
        )

    def rebuild_after_external_interruption(self, reason: str = "external_interrupt") -> None:
        """Restart listening after a command/captcha interruption may have replayed the request."""
        if not self._listen_pattern:
            return
        self._send_attempt_baseline_targets = 0
        self._send_attempt_baseline_requests = 0
        self._send_attempt_marked_at = 0.0
        if self._listen_is_active():
            self._is_listening = True
            self._pre_started = True
            counters = self._read_listen_counters()
            logger.debug(
                f"[NetworkMonitor] 外部中断后保留现有监听 ({reason or 'external_interrupt'}, "
                f"targets={counters['running_targets']}, requests={counters['running_requests']}, "
                f"queued={counters['queued_packets']})"
            )
            return
        self._is_listening = False
        self._pre_started = False
        self._safe_stop_listen()
        self._force_reset_listen_state()
        self._start_listen()
        logger.debug(f"[NetworkMonitor] 已重建监听 ({reason or 'external_interrupt'})")
        self.mark_send_attempt()

    def poll_send_activity(self, timeout: float = 0.25) -> Dict[str, Any]:
        """
        发送后短窗口里轻量探测一次网络活动。

        如果拿到了响应对象，会先缓存起来，避免后续 monitor() 丢掉首个事件。
        """
        if not self._listen_pattern:
            return {"seen": False, "matched": False}

        try:
            if not self._listen_is_active():
                self._ensure_listening("poll_send_activity")
        except Exception as e:
            err_text = str(e)
            if self._is_restartable_listen_error(err_text):
                try:
                    self._ensure_listening("poll_send_activity_restart")
                except Exception:
                    return {"seen": False, "matched": False, "error": err_text}
                self._sleep_after_listen_restart(1)
                return {"seen": False, "matched": False, "error": err_text}
            return {"seen": False, "matched": False, "error": err_text}

        baseline_targets = int(getattr(self, "_send_attempt_baseline_targets", 0) or 0)
        deadline = time.time() + max(0.01, float(timeout or 0.01))
        saw_any_response = False
        last_event: Dict[str, Any] = {}
        listen_restart_attempts = 0

        while True:
            counters = self._read_listen_counters()
            if counters["running_targets"] > baseline_targets:
                return {
                    "seen": True,
                    "matched": True,
                    "source": "request_started",
                    **counters,
                }

            remaining = deadline - time.time()
            if remaining <= 0:
                return {
                    "seen": saw_any_response,
                    "matched": False,
                    "source": "timeout",
                    "event": last_event,
                    **counters,
                }

            wait_timeout = min(0.05, max(0.01, remaining))
            try:
                response = self.tab.listen.wait(timeout=wait_timeout)
            except Exception as e:
                err_text = str(e)
                if self._is_restartable_listen_error(err_text):
                    listen_restart_attempts += 1
                    if listen_restart_attempts > self.MAX_LISTEN_RESTARTS:
                        return {
                            "seen": saw_any_response,
                            "matched": False,
                            "error": (
                                f"监听状态恢复失败（已重试 {self.MAX_LISTEN_RESTARTS} 次）: {err_text}"
                            ),
                            **counters,
                        }
                    try:
                        self._ensure_listening("poll_send_activity_wait_restart")
                    except Exception:
                        return {"seen": saw_any_response, "matched": False, "error": err_text, **counters}
                    self._sleep_after_listen_restart(listen_restart_attempts)
                    continue
                return {"seen": saw_any_response, "matched": False, "error": err_text, **counters}
            if response is None or response is False:
                continue

            saw_any_response = True
            self._remember_prefetched_response(response)
            event = self._extract_event(response)
            last_event = event
            matched = False
            try:
                matched = self.parser.get_id() != "event_only" and self._matches_stream_target(event)
            except Exception:
                matched = False

            counters = self._read_listen_counters()
            if matched:
                return {
                    "seen": True,
                    "matched": True,
                    "source": "response_packet",
                    "event": event,
                    **counters,
                }

    def _ensure_listening(self, reason: str):
        if self._is_listening and self._listen_is_active():
            return

        self._is_listening = False
        self._pre_started = False
        self._safe_stop_listen()
        self._force_reset_listen_state()
        try:
            self._start_listen()
            logger.debug(f"[NetworkMonitor] 已重建监听 ({reason})")
        except Exception as e:
            logger.error(f"[NetworkMonitor] 启动监听失败 ({reason}): {e}")
            raise NetworkMonitorError(f"启动监听失败: {e}")

    def _wait_for_response(self, timeout: float) -> Any:
        wait_budget = max(0.01, float(timeout or 0.01))
        remaining = wait_budget

        while remaining > 0:
            if self._should_stop():
                return False

            try:
                if hasattr(self.tab, "states") and not self.tab.states.is_alive:
                    logger.warning("[NetworkMonitor] 检测到标签页已被关闭，强行退出网络监听")
                    return False
            except Exception:
                pass

            step_timeout = min(remaining, self.CANCEL_CHECK_SLICE)
            if not self._listen_is_active():
                self._ensure_listening("wait_inactive")

            response = self.tab.listen.wait(timeout=step_timeout)
            if response not in (None, False):
                return response

            remaining -= step_timeout

        return False

    def _extract_event(self, response: Any) -> Dict[str, Any]:
        req = getattr(response, "request", None)
        resp = getattr(response, "response", None)

        url = (
            getattr(req, "url", None)
            or getattr(resp, "url", None)
            or getattr(response, "url", None)
            or ""
        )
        method = (
            getattr(req, "method", None)
            or getattr(response, "method", None)
            or ""
        )
        status = (
            getattr(resp, "status", None)
            or getattr(resp, "status_code", None)
            or getattr(response, "status", None)
            or 0
        )

        try:
            status = int(status)
        except Exception:
            status = 0

        return {
            "url": str(url or ""),
            "method": str(method or "").upper(),
            "status": status,
            "timestamp": time.time(),
        }

    def _dispatch_event(self, event: Dict[str, Any]) -> bool:
        if not self._event_handler:
            return False
        try:
            return bool(self._event_handler(event))
        except Exception as e:
            logger.debug(f"[NetworkMonitor] 事件回调异常（忽略）: {e}")
            return False

    def _matches_stream_target(self, event: Dict[str, Any]) -> bool:
        pattern = str(self._stream_match_pattern or "").strip()
        if not pattern:
            return True

        url = str(event.get("url", "") or "")
        if self._stream_match_mode == "regex":
            try:
                return bool(re.search(pattern, url, flags=re.IGNORECASE))
            except re.error:
                logger.debug(
                    f"[NetworkMonitor] 无效 stream_match_pattern 正则，回退关键字匹配: {pattern}"
                )

        return pattern.lower() in url.lower()

    @staticmethod
    def _nested_get(container: Any, *path: str) -> Any:
        current = container
        for key in path:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = getattr(current, key, None)
        return current

    def _extract_raw_body(self, response: Any) -> tuple[Any, str]:
        resp = getattr(response, "response", None)
        if resp is None:
            return None, "missing_response"

        for source_name, source_value in (
            ("response._stream.fullText", self._nested_get(resp, "_stream", "fullText")),
            ("response.stream.fullText", self._nested_get(resp, "stream", "fullText")),
            ("response._stream.chunks", self._nested_get(resp, "_stream", "chunks")),
            ("response.stream.chunks", self._nested_get(resp, "stream", "chunks")),
            ("event._stream.fullText", self._nested_get(response, "_stream", "fullText")),
            ("event.stream.fullText", self._nested_get(response, "stream", "fullText")),
            ("event._stream.chunks", self._nested_get(response, "_stream", "chunks")),
            ("event.stream.chunks", self._nested_get(response, "stream", "chunks")),
        ):
            if source_value in (None, "", [], ()):
                continue
            if source_name.endswith(".chunks"):
                merged = self._merge_stream_chunks_cached(source_value)
                if merged:
                    return merged, source_name
                continue
            return source_value, source_name

        direct_body = getattr(resp, "body", None)
        if direct_body not in (None, "", b"", bytearray()):
            direct_body_container = self._coerce_json_container(direct_body)
            if isinstance(direct_body_container, dict):
                for source_name, source_value in (
                    ("body._stream.fullText", self._nested_get(direct_body_container, "_stream", "fullText")),
                    ("body.stream.fullText", self._nested_get(direct_body_container, "stream", "fullText")),
                    ("body._stream.chunks", self._nested_get(direct_body_container, "_stream", "chunks")),
                    ("body.stream.chunks", self._nested_get(direct_body_container, "stream", "chunks")),
                ):
                    if source_value in (None, "", [], ()):
                        continue
                    if source_name.endswith(".chunks"):
                        merged = self._merge_stream_chunks_cached(source_value)
                        if merged:
                            return merged, source_name
                        continue
                    return source_value, source_name
                logger.debug(
                    "[NetworkMonitor][DirectBody] no stream field found in body container: "
                    f"{self._describe_json_container(direct_body)}"
                )
            else:
                direct_body_text = self._normalize_raw_body(direct_body)
                if not self._looks_like_sse_payload(direct_body_text):
                    logger.debug(
                        "[NetworkMonitor][DirectBody] body is not a JSON container: "
                        f"{self._describe_json_container(direct_body)}"
                    )
            return direct_body, "body"

        return None, "empty"

    @classmethod
    def _normalize_stream_chunk_data(cls, chunk: Any) -> str:
        data = cls._nested_get(chunk, "data")
        if data in (None, ""):
            return ""
        if isinstance(data, (bytes, bytearray)):
            return data.decode("utf-8", errors="ignore")
        if not isinstance(data, str):
            return str(data)
        return data

    @staticmethod
    def _merge_stream_chunks(chunks: Any) -> str:
        if not isinstance(chunks, list):
            return ""

        parts = []
        for chunk in chunks:
            data = NetworkMonitor._normalize_stream_chunk_data(chunk)
            if not data:
                continue
            parts.append(data)
        return "".join(parts)

    def _merge_stream_chunks_cached(self, chunks: Any) -> str:
        if not isinstance(chunks, list):
            return ""
        if not chunks:
            return ""

        cache = getattr(self, "_stream_chunk_merge_cache", None)
        if not isinstance(cache, dict):
            self._reset_stream_chunk_merge_cache()
            cache = self._stream_chunk_merge_cache

        chunk_key = id(chunks)
        chunk_len = len(chunks)
        entry = cache.get(chunk_key)

        if (
            entry
            and entry.get("chunks") is chunks
            and entry.get("chunk_len", 0) <= chunk_len
            and self._cached_stream_chunks_prefix_is_valid(entry, chunks)
        ):
            cached_len = int(entry.get("chunk_len", 0) or 0)
            if cached_len == chunk_len:
                return str(entry.get("body", "") or "")

            parts = [str(entry.get("body", "") or "")]
            for chunk in chunks[cached_len:]:
                parts.append(self._normalize_stream_chunk_data(chunk))
            body = "".join(parts)
        else:
            body = self._merge_stream_chunks(chunks)

        cache[chunk_key] = {
            "chunks": chunks,
            "chunk_len": chunk_len,
            "tail_marker": self._stream_chunk_tail_marker(chunks),
            "body": body,
        }
        self._trim_stream_chunk_merge_cache(cache)
        return body

    @classmethod
    def _stream_chunk_tail_marker(cls, chunks: list[Any]) -> tuple[int, int, int, str]:
        if not chunks:
            return (0, 0, 0, "")
        tail = chunks[-1]
        return cls._stream_chunk_marker(tail)

    @classmethod
    def _stream_chunk_marker(cls, chunk: Any) -> tuple[int, int, int, str]:
        data = cls._nested_get(chunk, "data")
        try:
            data_len = len(data) if data is not None else 0
        except Exception:
            data_len = 0
        return (id(chunk), id(data), data_len, type(data).__name__)

    def _cached_stream_chunks_prefix_is_valid(
        self,
        entry: Dict[str, Any],
        chunks: list[Any],
    ) -> bool:
        cached_len = int(entry.get("chunk_len", 0) or 0)
        if cached_len <= 0:
            return True
        if len(chunks) < cached_len:
            return False
        return entry.get("tail_marker") == self._stream_chunk_marker(chunks[cached_len - 1])

    def _trim_stream_chunk_merge_cache(self, cache: Dict[int, Dict[str, Any]]) -> None:
        max_entries = max(1, int(self.MAX_STREAM_CHUNK_MERGE_CACHE or 1))
        overflow = len(cache) - max_entries
        if overflow <= 0:
            return
        for key in list(cache.keys())[:overflow]:
            cache.pop(key, None)

    @staticmethod
    def _normalize_raw_body(raw_body: Any) -> str:
        if raw_body is None:
            return ""
        if isinstance(raw_body, str):
            return raw_body
        if isinstance(raw_body, (bytes, bytearray)):
            try:
                return bytes(raw_body).decode("utf-8", errors="ignore")
            except Exception:
                return bytes(raw_body).decode("utf-8", "replace")
        if isinstance(raw_body, (dict, list)):
            return json.dumps(raw_body, ensure_ascii=False)
        return str(raw_body)

    @staticmethod
    def _coerce_json_container(value: Any) -> Any:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    parsed = json.loads(stripped)
                except Exception:
                    return None
                return parsed if isinstance(parsed, dict) else None
        return None

    @staticmethod
    def _describe_json_container(value: Any) -> str:
        container = NetworkMonitor._coerce_json_container(value)
        if not isinstance(container, dict):
            return f"type={type(value).__name__}, preview={_debug_preview(value, 320)}"

        def _keys_of(obj: Any) -> list[str]:
            if isinstance(obj, dict):
                return [str(k) for k in list(obj.keys())[:8]]
            return []

        message = container.get("message")
        body = container.get("body")
        message_content = message.get("content") if isinstance(message, dict) else None
        message_content_len = len(message_content) if isinstance(message_content, str) else 0

        return (
            f"keys={_keys_of(container)}, "
            f"stream_keys={_keys_of(container.get('stream'))}, "
            f"_stream_keys={_keys_of(container.get('_stream'))}, "
            f"body_keys={_keys_of(body)}, "
            f"message_keys={_keys_of(message)}, "
            f"message_content_len={message_content_len}, "
            f"preview={_debug_preview(container, 320)}"
        )

    @staticmethod
    def _looks_like_sse_payload(value: Any) -> bool:
        if not isinstance(value, str):
            return False

        stripped = value.lstrip("\ufeff\r\n\t ")
        if not stripped:
            return False

        return (
            stripped.startswith("id:")
            or stripped.startswith("event:")
            or stripped.startswith("data:")
            or "\nevent:" in stripped
            or "\ndata:" in stripped
        )

    def _extract_content_type(self, response: Any) -> str:
        resp = getattr(response, "response", None)
        resp_payload = getattr(resp, "_response", None)
        resp_headers = resp_payload.get("headers") if isinstance(resp_payload, dict) else None
        candidates = (
            self._nested_get(resp_headers, "content-type"),
            self._nested_get(resp_headers, "Content-Type"),
            self._nested_get(resp_headers, "contentType"),
            getattr(resp, "content_type", None),
            getattr(resp, "contentType", None),
            self._nested_get(response, "headers", "content-type"),
            self._nested_get(response, "headers", "Content-Type"),
            self._nested_get(response, "headers", "contentType"),
            getattr(response, "content_type", None),
            getattr(response, "contentType", None),
        )
        for value in candidates:
            if value:
                return str(value).strip().lower()
        return ""

    def _is_event_stream_response(self, response: Any) -> bool:
        content_type = self._extract_content_type(response)
        if "text/event-stream" in content_type:
            return True

        direct_body = self._coerce_json_container(
            self._nested_get(getattr(response, "response", None), "body")
        )

        for source_name, source_value in (
            ("response._stream", self._nested_get(getattr(response, "response", None), "_stream")),
            ("response.stream", self._nested_get(getattr(response, "response", None), "stream")),
            ("event._stream", self._nested_get(response, "_stream")),
            ("event.stream", self._nested_get(response, "stream")),
            ("body._stream", self._nested_get(direct_body, "_stream") if isinstance(direct_body, dict) else None),
            ("body.stream", self._nested_get(direct_body, "stream") if isinstance(direct_body, dict) else None),
        ):
            if source_value not in (None, "", [], ()):
                logger.debug(f"[NetworkMonitor] 检测到流响应结构: {source_name}")
                return True
        return False

    def _stream_capture_complete(self, response: Any) -> bool:
        direct_body = self._coerce_json_container(
            self._nested_get(getattr(response, "response", None), "body")
        )
        for value in (
            self._nested_get(getattr(response, "response", None), "_stream", "complete"),
            self._nested_get(getattr(response, "response", None), "stream", "complete"),
            self._nested_get(response, "_stream", "complete"),
            self._nested_get(response, "stream", "complete"),
            self._nested_get(direct_body, "_stream", "complete") if isinstance(direct_body, dict) else None,
            self._nested_get(direct_body, "stream", "complete") if isinstance(direct_body, dict) else None,
        ):
            if value is not None:
                return bool(value)
        return False

    def _wait_for_stream_body(
        self,
        response: Any,
        initial_body: str,
        initial_source: str,
        wait_budget: Optional[float] = None,
    ) -> tuple[str, str]:
        body = initial_body
        source = initial_source
        if body:
            return body, source

        if wait_budget is None:
            wait_budget = min(max(float(self._response_interval or 0.5), 0.2), 1.5)
        else:
            try:
                wait_budget = max(float(wait_budget), 0.2)
            except Exception:
                wait_budget = min(max(float(self._response_interval or 0.5), 0.2), 1.5)
        deadline = time.time() + wait_budget

        while time.time() < deadline:
            if self._should_stop():
                break

            raw_body, raw_body_source = self._extract_raw_body(response)
            body = self._normalize_raw_body(raw_body)
            if body:
                logger.debug(
                    f"[NetworkMonitor] 流响应正文已就绪 "
                    f"(source={raw_body_source}, size={len(body)} chars)"
                )
                return body, raw_body_source

            if self._stream_capture_complete(response):
                break

            time.sleep(0.05)

        return body, source

    def _wait_for_stream_progress(self, response: Any, current_body: str, current_source: str) -> tuple[str, str]:
        body = current_body or ""
        source = current_source
        wait_budget = min(max(float(self._response_interval or 0.5), 0.2), 1.5)
        deadline = time.time() + wait_budget
        previous_len = len(body)

        while time.time() < deadline:
            if self._should_stop():
                break

            raw_body, raw_body_source = self._extract_raw_body(response)
            next_body = self._normalize_raw_body(raw_body)
            if self._is_stream_body_replacement(body, next_body):
                return next_body, raw_body_source

            if len(next_body) > previous_len:
                return next_body, raw_body_source

            if self._stream_capture_complete(response):
                return body, source

            time.sleep(0.05)

        return body, source

    @staticmethod
    def _is_stream_body_replacement(previous_body: str, next_body: str) -> bool:
        previous_body = previous_body or ""
        next_body = next_body or ""
        if not previous_body or not next_body or next_body == previous_body:
            return False
        return not next_body.startswith(previous_body)

    def _handle_stream_body_replacement(
        self,
        previous_body: str,
        next_body: str,
        source: str,
    ) -> bool:
        if not self._is_stream_body_replacement(previous_body, next_body):
            return False

        if self._total_chunks > 0:
            logger.warning(
                "[NetworkMonitor] 流响应原始快照发生非前缀替换，回退到 DOM 补齐 "
                f"(old_len={len(previous_body or '')}, new_len={len(next_body or '')}, "
                f"source={source}, chunks={self._total_chunks})"
            )
            raise NetworkMonitorTimeout("目标流响应快照发生非前缀替换")

        logger.warning(
            "[NetworkMonitor] 流响应原始快照发生非前缀替换，重置解析器后按新快照解析 "
            f"(old_len={len(previous_body or '')}, new_len={len(next_body or '')}, "
            f"source={source})"
        )
        try:
            self.parser.reset()
        except Exception as e:
            logger.debug(f"[NetworkMonitor] 重置解析器失败（忽略）: {e}")
        return True

    def _write_parser_debug_dump(
        self,
        raw_body: str,
        event: Dict[str, Any],
        parse_result: Dict[str, Any],
        raw_body_source: str,
        is_event_stream: bool,
        *,
        force_stage: str = "",
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._is_network_debug_capture_enabled():
            return

        try:
            parser_id = str(self.parser.get_id() or "").strip().lower()
            parser_filter = self._get_network_debug_capture_parser_filter()
            if parser_filter and parser_id != parser_filter:
                return

            capture_stage = str(force_stage or "").strip() or self._select_network_debug_capture_stage(parse_result)
            if not capture_stage:
                return
            if force_stage:
                if capture_stage in self._debug_capture_written_stages:
                    return
                max_files = self._get_network_debug_capture_max_files_per_request()
                if self._debug_capture_counter >= max_files:
                    return

            self._debug_capture_counter += 1
            max_chars = self._get_network_debug_capture_max_body_chars()
            raw_body_text = str(raw_body or "")
            truncated = len(raw_body_text) > max_chars
            stored_body = raw_body_text[:max_chars] if truncated else raw_body_text
            stored_body = str(sanitize_sensitive_data(stored_body))
            has_content = bool(str(parse_result.get("content", "") or ""))

            parser_debug = None
            if hasattr(self.parser, "export_debug_data"):
                try:
                    parser_debug = sanitize_sensitive_data(
                        self.parser.export_debug_data(raw_body_text)
                    )
                except Exception as parser_exc:
                    parser_debug = {"error": str(parser_exc)}

            content_preview = str(parse_result.get("content", "") or "")[:800]

            payload = {
                "captured_at": int(time.time()),
                "parser": parser_id,
                "capture_session": self._debug_capture_session_key,
                "capture_index": self._debug_capture_counter,
                "capture_stage": capture_stage,
                "event": {
                    "url": sanitize_sensitive_data(str(event.get("url", "") or "")),
                    "method": str(event.get("method", "") or ""),
                    "status": int(event.get("status", 0) or 0),
                    "timestamp": float(event.get("timestamp", 0) or 0),
                },
                "source": str(raw_body_source or ""),
                "is_event_stream": bool(is_event_stream),
                "raw_body_len": len(raw_body_text),
                "raw_body_truncated": truncated,
                "raw_body": stored_body,
                "parse_result": {
                    "content_len": len(str(parse_result.get("content", "") or "")),
                    "content_preview": sanitize_sensitive_data(content_preview),
                    "done": bool(parse_result.get("done", False)),
                    "error": sanitize_sensitive_data(str(parse_result.get("error") or "")),
                    "image_count": len(parse_result.get("images", []) or []),
                },
                "parser_debug": parser_debug,
            }
            if extra_payload:
                payload["diagnostics"] = sanitize_sensitive_data(dict(extra_payload))

            dump_dir = Path("logs") / "network_parser_debug"
            dump_dir.mkdir(parents=True, exist_ok=True)
            filename = (
                f"{self._debug_capture_session_key}_{self._debug_capture_counter:03d}_"
                f"{capture_stage}_{parser_id or 'unknown'}.json"
            )
            dump_path = dump_dir / filename
            dump_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            self._debug_capture_written_stages.add(capture_stage)
            if has_content:
                self._debug_capture_has_content_snapshot = True
            logger.debug_throttled(
                f"network.parser_debug_dump.{id(self)}",
                "[NetworkMonitor] 网络解析调试快照已写入 "
                f"({dump_path}, stage={capture_stage}, parser={parser_id}, "
                f"body_len={len(raw_body_text)}, truncated={truncated})",
                interval_sec=3.0,
            )
        except Exception as exc:
            logger.debug(f"[NetworkMonitor] 写入网络解析调试快照失败: {exc}")

    def _select_network_debug_capture_stage(self, parse_result: Dict[str, Any]) -> str:
        error_text = str(parse_result.get("error") or "").strip()
        has_content = bool(str(parse_result.get("content", "") or ""))
        done = bool(parse_result.get("done", False))

        if error_text:
            stage = "error"
        elif done:
            stage = "done"
        elif self._debug_capture_counter == 0:
            stage = "initial"
        elif has_content and not self._debug_capture_has_content_snapshot:
            stage = "first_content"
        else:
            return ""

        if stage in self._debug_capture_written_stages:
            return ""

        max_files = self._get_network_debug_capture_max_files_per_request()
        remaining_slots = max_files - self._debug_capture_counter
        if remaining_slots <= 0:
            return ""

        # 预留最后一个槽位给结束态，避免中途流式增量把限额写满。
        if stage == "first_content" and remaining_slots <= 1:
            return ""

        return stage

    @staticmethod
    def _is_network_debug_capture_enabled() -> bool:
        try:
            return bool(BrowserConstants.get("NETWORK_DEBUG_CAPTURE_ENABLED"))
        except Exception:
            return False

    @staticmethod
    def _get_network_debug_capture_max_body_chars() -> int:
        try:
            value = int(BrowserConstants.get("NETWORK_DEBUG_CAPTURE_MAX_BODY_CHARS"))
            return max(2000, value)
        except Exception:
            return 50000

    @staticmethod
    def _get_network_debug_capture_max_files_per_request() -> int:
        try:
            value = int(BrowserConstants.get("NETWORK_DEBUG_CAPTURE_MAX_FILES_PER_REQUEST"))
            return max(2, value)
        except Exception:
            return 3

    @staticmethod
    def _get_network_debug_capture_parser_filter() -> str:
        try:
            return str(BrowserConstants.get("NETWORK_DEBUG_CAPTURE_PARSER_FILTER") or "").strip().lower()
        except Exception:
            return ""
        
    def pre_start(self):
        """
        在发送动作之前启动网络监听
        
        v5.11 改进：
        - 恢复实际启动（延迟启动会错过 requestWillBeSent 事件）
        - 但调用时机从 FILL_INPUT 延后到 CLICK send_btn / KEY_PRESS Enter 之前
        - 暴露窗口：从"发送前一刻"到"回复结束"，而非"输入开始"到"回复结束"
        
        调用时机：仅在 CLICK send_btn 或 KEY_PRESS Enter 之前
        """
        if self._pre_started and self._listen_is_active():
            return
        
        if not self._listen_pattern:
            logger.warning("[NetworkMonitor] listen_pattern 未配置")
            return
        
        try:
            # 启用复用模式：使用 tab 主连接，不创建额外 CDP session
            self._ensure_listening("pre_start")
            logger.debug(f"[NetworkMonitor] 发送前启动监听 - 复用模式 (pattern={self._listen_pattern!r})")
        except Exception as e:
            logger.error(f"[NetworkMonitor] 预启动失败: {e}")
    
    def monitor(self, selector: str = None, user_input: str = "",
                completion_id: Optional[str] = None) -> Generator[str, None, None]:
        """
        监听网络响应并流式输出
        
        v5.11：
        - pre_start 已在发送前启动监听
        - 此处仅做兜底检查（正常不应走到未启动的情况）
        - 响应结束后立即 stop()，最小化暴露窗口
        
        Args:
            selector: 选择器（兼容参数，实际不使用）
            user_input: 用户输入（用于日志）
            completion_id: 完成 ID
        
        Yields:
            SSE 格式的数据块
        
        Raises:
            NetworkMonitorTimeout: 首次响应超时（触发回退）
            NetworkMonitorError: 其他网络监听错误
        """
        if not self._listen_pattern:
            raise NetworkMonitorError("listen_pattern 未配置")
        
        if completion_id is None:
            completion_id = SSEFormatter._generate_id()
        
        # 重置解析器状态
        self._reset_parser_state()
        self._total_chunks = 0
        self._total_content_chars = 0
        self._last_stream_event = {}
        self._last_stream_raw_body = ""
        self._last_stream_raw_body_len = 0
        self._last_stream_raw_body_signature = None
        self._last_stream_parse_result = {}
        self._reset_media_generation_state_cache()
        self._last_stream_media_items = []
        self._prefetched_image_urls = set()
        
        # 兜底：如果 pre_start 未被调用，在此启动（可能错过首包）
        if not self._is_listening or not self._listen_is_active():
            logger.warning(
                "[NetworkMonitor] 监听未预启动，在此启动"
                "（可能错过 requestWillBeSent）"
            )
            self._ensure_listening("monitor_start")
        
        preserve_listener = False
        try:
            yield from self._stream_output_phase(completion_id)
        except NetworkMonitorTerminalError as e:
            preserve_listener = self._should_preserve_listener_after_terminal_error(e)
            if preserve_listener:
                logger.warning(
                    "[NetworkMonitor] 目标流触发可恢复验证/限流状态，保留监听等待页面自动重发"
                )
            raise
        finally:
            if preserve_listener:
                self._is_listening = self._listen_is_active()
                self._pre_started = self._is_listening
            else:
                # 立即停止：关闭 Network.enable + 释放额外 CDP session
                self._cleanup()
                self._pre_started = False
    
    def _stream_output_phase(self, completion_id: str) -> Generator[str, None, None]:
        """
        流式输出阶段
        """
        phase_start = time.time()
        try:
            hard_timeout = max(0.01, float(self._hard_timeout or self.DEFAULT_HARD_TIMEOUT))
        except Exception:
            hard_timeout = float(self.DEFAULT_HARD_TIMEOUT)
        hard_timeout_message = f"网络监听超过最大时间（{hard_timeout:.1f}s）"
        has_received_response = False
        has_seen_stream_target = False
        last_activity_time = time.time()
        listen_restart_attempts = 0
        total_responses = 0
        non_target_skips = 0
        empty_body_skips = 0
        stream_target_hits = 0
        active_stream_response = None
        active_stream_event: Dict[str, Any] = {}
        active_stream_body = ""
        active_stream_body_source = ""
        completed_by_done = False
        completion_reason = "unknown"

        while True:
            # 检查全局超时
            now = time.time()
            elapsed = now - phase_start
            remaining_hard_timeout = hard_timeout - elapsed
            if remaining_hard_timeout <= 0:
                logger.error(f"[NetworkMonitor] 超过最大监听时间 {hard_timeout:.1f}s，触发回退")
                raise NetworkMonitorTimeout(hard_timeout_message)

            # 检查取消信号
            if self._should_stop():
                logger.debug("[NetworkMonitor] 监听被取消")
                completion_reason = "cancelled"
                break

            # 设置超时时间
            if active_stream_response is not None:
                timeout = self.ACTIVE_STREAM_RESPONSE_POLL_TIMEOUT
            else:
                timeout = self._first_response_timeout if not has_seen_stream_target else self._response_interval
            try:
                timeout = float(timeout)
            except Exception:
                timeout = self.DEFAULT_RESPONSE_INTERVAL
            timeout = min(max(0.01, timeout), max(0.01, remaining_hard_timeout))

            # 等待响应
            try:
                if self._prefetched_responses:
                    response = self._pop_prefetched_response()
                else:
                    response = self._wait_for_response(timeout)
            except Exception as e:
                err_text = str(e)
                if self._is_restartable_listen_error(err_text):
                    listen_restart_attempts += 1
                    if listen_restart_attempts > self.MAX_LISTEN_RESTARTS:
                        raise NetworkMonitorError(
                            f"监听状态恢复失败（已重试 {self.MAX_LISTEN_RESTARTS} 次）: {err_text}"
                        ) from e
                    logger.warning(
                        "[NetworkMonitor] wait 期间监听状态失效，尝试重建后重试 "
                        f"({listen_restart_attempts}/{self.MAX_LISTEN_RESTARTS})"
                    )
                    self._ensure_listening("wait_restart")
                    self._sleep_after_listen_restart(listen_restart_attempts)
                    continue
                raise NetworkMonitorError(err_text) from e

            # 检查是否为无效响应
            if response is None or response is False:
                elapsed = time.time() - phase_start
                if elapsed >= hard_timeout:
                    logger.error(f"[NetworkMonitor] 超过最大监听时间 {hard_timeout:.1f}s，触发回退")
                    raise NetworkMonitorTimeout(hard_timeout_message)

                if not has_seen_stream_target:
                    logger.warning(f"[NetworkMonitor] 目标流响应超时 ({elapsed:.1f}s)，触发回退")
                    raise NetworkMonitorTimeout(f"目标流响应超时（{elapsed:.1f}s）")

                if active_stream_response is not None:
                    next_body, next_source = self._wait_for_stream_progress(
                        active_stream_response,
                        active_stream_body,
                        active_stream_body_source,
                    )
                    if next_body and next_body != active_stream_body:
                        self._handle_stream_body_replacement(
                            active_stream_body,
                            next_body,
                            next_source,
                        )
                        active_stream_body = next_body
                        active_stream_body_source = next_source
                        last_activity_time = time.time()
                        logger.debug_throttled(
                            f"network.active_stream_growth.{id(self)}",
                            "[NetworkMonitor] 流响应增长中 "
                            f"(当前大小={len(next_body)} 字节)",
                            interval_sec=10.0,
                        )
                        try:
                            parse_result = self.parser.parse_chunk(active_stream_body)
                        except Exception as e:
                            logger.warning(f"[NetworkMonitor] 活跃流二次解析异常: {e}")
                            continue

                        self._write_parser_debug_dump(
                            active_stream_body,
                            active_stream_event,
                            parse_result,
                            active_stream_body_source,
                            True,
                        )
                        parse_result = self._handle_parse_result(parse_result)
                        if parse_result.get("error"):
                            continue

                        self._remember_last_stream_result(
                            active_stream_event,
                            active_stream_body,
                            parse_result,
                        )
                        try:
                            self._record_parse_result_media(parse_result)
                        except Exception as media_exc:
                            logger.debug(f"[NetworkMonitor] 媒体结果记录失败（忽略）: {media_exc}")
                        try:
                            media_state = self._get_media_generation_state_cached(
                                active_stream_body,
                                parse_result,
                            )
                        except Exception as parser_exc:
                            logger.debug(f"[NetworkMonitor] 媒体状态提取失败（忽略）: {parser_exc}")
                        content = parse_result.get("content", "")
                        done = parse_result.get("done", False)
                        if content:
                            self._total_chunks += 1
                            self._total_content_chars += len(content)
                            yield self.formatter.pack_chunk(content, completion_id=completion_id)

                        if done:
                            completed_by_done = True
                            completion_reason = "done"
                            logger._logger.log(logging.DEBUG - 5, "[NetworkMonitor] 检测到结束标志，完成监听")
                            break

                        continue

                    if self._stream_capture_complete(active_stream_response):
                        if self._total_chunks == 0 and self._should_fallback_to_dom_on_empty_stream():
                            logger.warning(
                                "[NetworkMonitor] 流响应已结束但仍无有效正文，回退到 DOM 监听 "
                                f"(source={active_stream_body_source}, body_len={len(active_stream_body or '')})"
                            )
                            raise NetworkMonitorTimeout("目标流未产出有效正文")
                        if (
                            self._total_chunks > 0
                            and not completed_by_done
                            and self._should_fallback_to_dom_on_empty_stream()
                        ):
                            logger.warning(
                                "[NetworkMonitor] 流响应已结束但未收到完成标志，回退到 DOM 补齐 "
                                f"(source={active_stream_body_source}, chunks={self._total_chunks}, "
                                f"body_len={len(active_stream_body or '')})"
                            )
                            raise NetworkMonitorTimeout("目标流未收到完成标志")
                        completion_reason = "capture_complete"
                        logger.debug("[NetworkMonitor] 活跃流响应已完成，结束监听")
                        break

                silence_duration = time.time() - last_activity_time
                effective_silence_threshold = float(self._silence_threshold)
                if (
                    active_stream_response is not None
                    and self._total_chunks == 0
                    and not self._stream_capture_complete(active_stream_response)
                ):
                    effective_silence_threshold = max(
                        float(self._first_content_timeout),
                        float(self._silence_threshold),
                    )
                    logger.debug_throttled(
                        f"network.wait_first_content.{id(self)}",
                        "[NetworkMonitor] 等待流式文本解析产出 "
                        f"(已捕获流长度={len(active_stream_body or '')}, "
                        f"已静默等待={silence_duration:.1f}s/上限={effective_silence_threshold:.1f}s)",
                        interval_sec=10.0,
                    )

                if silence_duration > effective_silence_threshold:
                    if (
                        self._total_chunks > 0
                        and self._parse_result_has_unclosed_render_output(
                            self._last_stream_parse_result
                        )
                        and self._should_fallback_to_dom_on_empty_stream()
                    ):
                        body_len = len(active_stream_body or "") or int(
                            self._last_stream_raw_body_len or 0
                        )
                        logger.warning(
                            "[NetworkMonitor] 流式响应静默但前端 render 输出包装未闭合，回退到 DOM 补齐 "
                            f"(idle={silence_duration:.1f}s, chunks={self._total_chunks}, "
                            f"body_len={body_len})"
                        )
                        raise NetworkMonitorTimeout(
                            f"目标流 render 输出包装未完整结束（{silence_duration:.1f}s）"
                        )
                    if (
                        active_stream_response is not None
                        and self._total_chunks == 0
                        and self._should_fallback_to_dom_on_empty_stream()
                    ):
                        logger.warning(
                            "[NetworkMonitor] 首段等待超时且仍无有效正文，回退到 DOM 监听 "
                            f"(idle={silence_duration:.1f}s, limit={effective_silence_threshold:.1f}s, "
                            f"body_len={len(active_stream_body or '')})"
                        )
                        raise NetworkMonitorTimeout(
                            f"目标流未产出有效正文（{silence_duration:.1f}s）"
                        )
                    if (
                        active_stream_response is not None
                        and self._total_chunks > 0
                        and self._should_fallback_to_dom_on_empty_stream()
                    ):
                        if active_stream_response is not None and not completed_by_done:
                            logger.warning(
                                "[NetworkMonitor] 流式响应静默但未收到完成标志，回退到 DOM 补齐 "
                                f"(idle={silence_duration:.1f}s, chunks={self._total_chunks}, "
                                f"body_len={len(active_stream_body or '')})"
                            )
                            raise NetworkMonitorTimeout(
                                f"目标流未完整结束（{silence_duration:.1f}s）"
                            )
                    completion_reason = "silence"
                    logger.debug(f"[NetworkMonitor] 静默超时 ({silence_duration:.1f}s)，结束监听")
                    break
                continue

            # 标记已收到响应（在读取 body 之前！）
            if not has_received_response:
                has_received_response = True
                logger._logger.log(logging.DEBUG - 5, "[NetworkMonitor] 已捕获到首次响应")
            total_responses += 1
            last_activity_time = time.time()
            listen_restart_attempts = 0

            event = self._extract_event(response)
            if self._dispatch_event(event):
                logger.warning(
                    "[NetworkMonitor] 命中网络异常拦截，主动中断监听 "
                    f"(status={event.get('status')}, url={event.get('url', '')[:100]})"
                )
                raise NetworkInterceptionTriggered("network_intercepted")

            if self.parser.get_id() == "event_only":
                if total_responses == 1:
                    logger._logger.log(logging.DEBUG - 5, "[NetworkMonitor] event-only 已捕获到首个网络事件")
                last_activity_time = time.time()
                continue

            if not self._matches_stream_target(event):
                non_target_skips += 1
                logger.debug_throttled(
                    "network.non_target_response",
                    f"[NetworkMonitor] 非流式目标响应，跳过解析 "
                    f"(count={non_target_skips}, url={event.get('url', '')[:100]})",
                    interval_sec=5.0,
                )
                continue

            if not has_seen_stream_target:
                has_seen_stream_target = True
                logger._logger.log(logging.DEBUG - 5, "[NetworkMonitor] 已捕获到首个流目标响应")
            stream_target_hits += 1

            logger._logger.log(
                logging.DEBUG - 5,
                "[NetworkMonitor] 命中流目标 "
                f"(status={event.get('status')}, method={event.get('method')}, "
                f"url={event.get('url', '')[:120]}, count={stream_target_hits})"
            )

            if stream_target_hits == 1:
                logger._logger.log(logging.DEBUG - 5, "[NetworkMonitor] 已捕获到首个有效流响应")
            last_activity_time = time.time()
            active_stream_response = None
            active_stream_event = dict(event or {})
            active_stream_body = ""
            active_stream_body_source = ""

            # 检查响应对象结构
            response_obj = getattr(response, "response", None)
            if response_obj is None:
                logger.debug(f"[NetworkMonitor] 响应对象结构异常: {type(response).__name__}")
                continue

            # 读取响应体，流式协议优先使用 _stream.fullText
            raw_body, raw_body_source = self._extract_raw_body(response)
            raw_body = self._normalize_raw_body(raw_body)
            self._raise_for_http_error_status(event, raw_body, raw_body_source)

            if getattr(response_obj, "_response", None) is None and not raw_body:
                logger.warning(
                    "[NetworkMonitor] 目标流响应缺少响应元数据和响应体，回退到 DOM 监听 "
                    f"(status={event.get('status')}, url={event.get('url', '')[:120]})"
                )
                raise NetworkMonitorError("incomplete_target_response")
            if self.parser.get_id() == "doubao" and raw_body_source == "body":
                if self._looks_like_sse_payload(raw_body):
                    logger.debug(
                        "[NetworkMonitor][DoubaoDebug] body source contains raw SSE payload, "
                        "continue parsing in network mode"
                    )
                else:
                    logger.debug(
                        "[NetworkMonitor][DoubaoDebug] body-only response summary: "
                        f"{self._describe_json_container(raw_body)}"
                    )
                    logger.warning(
                        "[NetworkMonitor] 豆包网络响应仅返回 body 包装结果，回退到 DOM 监听"
                    )
                    raise NetworkMonitorError("doubao_body_only_response")
            is_event_stream = self._is_event_stream_response(response)
            should_probe_initial_target_body = (
                stream_target_hits == 1 and self._total_chunks == 0
            )

            if not raw_body and (is_event_stream or should_probe_initial_target_body):
                wait_budget = (
                    self._initial_target_body_wait
                    if should_probe_initial_target_body
                    else None
                )
                raw_body, raw_body_source = self._wait_for_stream_body(
                    response,
                    raw_body,
                    raw_body_source,
                    wait_budget=wait_budget,
                )
                if raw_body and not is_event_stream and self._looks_like_sse_payload(raw_body):
                    is_event_stream = True

            self._raise_for_http_error_status(
                event,
                raw_body,
                raw_body_source,
                is_event_stream,
            )

            if not raw_body:
                empty_body_skips += 1
                if should_probe_initial_target_body:
                    body_wait_elapsed = max(
                        0.0,
                        time.time() - float(event.get("timestamp", 0.0) or 0.0),
                    )
                    logger.warning(
                        "[NetworkMonitor] 首个流目标响应正文未在宽限期内就绪，回退到 DOM 监听 "
                        f"(wait={body_wait_elapsed:.1f}s, status={event.get('status')}, "
                        f"url={event.get('url', '')[:120]}, source={raw_body_source})"
                    )
                    raise NetworkMonitorTimeout(
                        f"目标流响应正文未就绪（{body_wait_elapsed:.1f}s）"
                    )
                logger.debug_throttled(
                    "network.empty_body",
                    "[NetworkMonitor] 响应体为空，跳过 "
                    f"(count={empty_body_skips}, stream={is_event_stream}, source={raw_body_source})",
                    interval_sec=5.0,
                )
                continue

            if stream_target_hits == 1:
                logger.info(
                    "[NetworkMonitor] 成功锁定流目标响应 "
                    f"(status={event.get('status')}, method={event.get('method')}, "
                    f"url={event.get('url', '')[:120]}, 初始长度={len(raw_body)} 字符)"
                )
            else:
                logger.debug_throttled(
                    "network.body_captured",
                    f"[NetworkMonitor] 持续捕获流响应分块 "
                    f"(targets={stream_target_hits}, source={raw_body_source}, 长度={len(raw_body)} 字符)",
                    interval_sec=5.0,
                )

            # 解析响应
            try:
                parse_result = self.parser.parse_chunk(raw_body)
            except Exception as e:
                logger.warning(f"[NetworkMonitor] 解析异常: {e}")
                continue

            if (
                is_event_stream
                and not parse_result.get("content")
                and not parse_result.get("done", False)
                and not parse_result.get("error")
            ):
                next_body, next_source = self._wait_for_stream_progress(
                    response,
                    raw_body,
                    raw_body_source,
                )
                if next_body and next_body != raw_body:
                    self._handle_stream_body_replacement(
                        raw_body,
                        next_body,
                        next_source,
                    )
                    raw_body = next_body
                    raw_body_source = next_source
                    last_activity_time = time.time()
                    try:
                        parse_result = self.parser.parse_chunk(raw_body)
                    except Exception as e:
                        logger.warning(f"[NetworkMonitor] 二次解析异常: {e}")
                        continue

            self._write_parser_debug_dump(
                raw_body,
                event,
                parse_result,
                raw_body_source,
                is_event_stream,
            )
            parse_result = self._handle_parse_result(parse_result)
            if parse_result.get("error"):
                continue
            self._remember_last_stream_result(event, raw_body, parse_result)
            try:
                self._record_parse_result_media(parse_result)
            except Exception as media_exc:
                logger.debug(f"[NetworkMonitor] 媒体结果记录失败（忽略）: {media_exc}")
            try:
                media_state = self._get_media_generation_state_cached(
                    raw_body,
                    parse_result,
                )
            except Exception as parser_exc:
                logger.debug(f"[NetworkMonitor] 媒体状态提取失败（忽略）: {parser_exc}")
            if is_event_stream:
                active_stream_response = response
                active_stream_event = dict(event or {})
                active_stream_body = str(raw_body or "")
                active_stream_body_source = raw_body_source

            # 提取内容
            content = parse_result.get("content", "")
            done = parse_result.get("done", False)

            if content:
                last_activity_time = time.time()
                self._total_chunks += 1
                self._total_content_chars += len(content)
                yield self.formatter.pack_chunk(content, completion_id=completion_id)

            if done:
                completed_by_done = True
                completion_reason = "done"
                logger._logger.log(logging.DEBUG - 5, "[NetworkMonitor] 检测到结束标志，完成监听")
                break

        reason_text = {
            "done": "检测到结束标志",
            "capture_complete": "流响应捕获完成",
            "silence": "静默超时",
            "cancelled": "监听被取消",
            "unknown": "循环结束",
        }.get(completion_reason, completion_reason)
        logger.info(
            "[NetworkMonitor] 网络流监听完成 "
            f"(reason={reason_text}, 历时={time.time() - phase_start:.1f}s, "
            f"捕获响应={total_responses}, 产出文本块={self._total_chunks}, "
            f"提取字符数={self._total_content_chars})"
        )

    def get_media_generation_state(self) -> Dict[str, Any]:
        return dict(self._last_media_generation_state or {})

    def get_stream_media_items(self) -> list[Dict[str, Any]]:
        return [dict(item) for item in (self._last_stream_media_items or []) if isinstance(item, dict)]

    def get_total_content_chars(self) -> int:
        try:
            return max(0, int(self._total_content_chars or 0))
        except Exception:
            return 0

    def _prefetch_image_url(self, url: str) -> bool:
        normalized = normalize_remote_image_url(url)
        if not normalized or normalized in self._prefetched_image_urls:
            return False

        cookies_dict, headers = build_image_download_request_context(self.tab)
        result = background_image_downloader.start_download(
            normalized,
            cookies=cookies_dict,
            headers=headers,
        )
        if result:
            self._prefetched_image_urls.add(normalized)
            return True
        return False

    def _record_parse_result_media(self, parse_result: Dict[str, Any]) -> None:
        raw_items = parse_result.get("images")
        if not isinstance(raw_items, list) or not raw_items:
            return

        seen = {
            (
                str(item.get("media_type") or "image").strip().lower(),
                str(item.get("url") or item.get("data_uri") or "").strip(),
            )
            for item in (self._last_stream_media_items or [])
            if isinstance(item, dict)
        }

        for raw_item in raw_items:
            if isinstance(raw_item, str):
                normalized = {
                    "media_type": "image",
                    "kind": "url",
                    "url": raw_item,
                    "data_uri": None,
                    "mime": None,
                    "byte_size": None,
                    "source": f"{self.parser.get_id()}_stream",
                }
            elif isinstance(raw_item, dict):
                normalized = dict(raw_item)
            else:
                continue

            media_type = str(normalized.get("media_type") or "image").strip().lower() or "image"
            if normalized.get("data_uri"):
                normalized["kind"] = "data_uri"
                normalized["url"] = None
                ref = str(normalized.get("data_uri") or "").strip()
            else:
                normalized["kind"] = "url"
                normalized["data_uri"] = None
                ref = str(normalized.get("url") or normalized.get("src") or "").strip()
                normalized["url"] = ref or None

            if not ref:
                continue

            key = (media_type, ref)
            if key in seen:
                continue

            seen.add(key)
            normalized["media_type"] = media_type
            normalized.pop("src", None)
            normalized.setdefault("mime", None)
            normalized.setdefault("byte_size", None)
            normalized.setdefault("source", f"{self.parser.get_id()}_stream")
            self._last_stream_media_items.append(normalized)
            if media_type == "image" and normalized.get("kind") == "url":
                self._prefetch_image_url(normalized.get("url"))

    def _clear_cached_results(self, *, include_media: bool = False) -> None:
        self._reset_prefetched_responses()
        self._last_stream_event = {}
        self._last_stream_raw_body = ""
        self._last_stream_raw_body_len = 0
        self._last_stream_raw_body_signature = None
        self._last_stream_parse_result = {}
        self._prefetched_image_urls = set()
        if include_media:
            self._reset_media_generation_state_cache()
            self._last_stream_media_items = []

    def cleanup(self) -> None:
        self._cleanup(include_media=True)

    def _cleanup(self, *, include_media: bool = False):
        """
        清理：停止网络监听并释放额外的 CDP session

        tab.listen.stop() 内部会：
        1. 移除所有 Network.* 事件回调
        2. 关闭独立的 Driver 连接（释放额外的 CDP session）

        这会关闭 Target.attachToTarget 创建的额外 session，
        消除 Network.enable 的全局副作用。
        """
        if self._is_listening:
            try:
                self._safe_stop_listen()
                logger._logger.log(logging.DEBUG - 5, "[NetworkMonitor] 已停止监听（listen 已释放）")
            except Exception as e:
                logger.debug(f"[NetworkMonitor] 停止监听失败: {e}")
            finally:
                self._is_listening = False
                self._pre_started = False

            try:
                if getattr(self, "_cdp_session_listening", False):
                    network = getattr(self.tab, "network", None)
                    stop_interception = getattr(network, "stop_interception", None)
                    if callable(stop_interception):
                        stop_interception()
                    self._cdp_session_listening = False
                    logger._logger.log(logging.DEBUG - 5, "[NetworkMonitor] 已停止 CDP interception")
            except Exception as e:
                logger.debug(f"[NetworkMonitor] 停止 CDP interception 失败: {e}")

        # 即使 _is_listening 已经是 False，也尝试确保 listen 已停止
        # （防止异常路径导致状态不一致）
        elif self._listen_is_active():
            try:
                self._safe_stop_listen()
                logger.debug("[NetworkMonitor] 补充停止残留监听")
            except Exception:
                pass

        self._clear_cached_results(include_media=include_media)
        self._reset_parser_state()


# ================= 工厂函数 =================

def create_network_monitor(tab, formatter: SSEFormatter,
                           stream_config: Dict,
                           stop_checker: Optional[Callable[[], bool]] = None,
                           event_handler: Optional[Callable[[Dict[str, Any]], bool]] = None) -> NetworkMonitor:
    """
    创建网络监听器（工厂函数）
    
    Args:
        tab: DrissionPage 标签页
        formatter: SSE 格式化器
        stream_config: 流式配置（必须包含 network.parser）
        stop_checker: 取消检查函数
    
    Returns:
        NetworkMonitor 实例
    
    Raises:
        ValueError: 配置缺失或解析器不存在
    """
    network_config = stream_config.get("network", {})
    
    # 获取解析器 ID
    parser_id = network_config.get("parser")
    event_only = bool(network_config.get("event_only", False))
    if not parser_id:
        if event_only and event_handler is not None:
            parser = _EventOnlyParser()
        else:
            raise ValueError("network.parser 未配置")
    else:
        # 获取解析器实例
        try:
            parser = ParserRegistry.get(parser_id)
        except ValueError as e:
            raise ValueError(f"解析器不存在: {e}")
    
    return NetworkMonitor(
        tab=tab,
        formatter=formatter,
        parser=parser,
        stop_checker=stop_checker,
        stream_config=stream_config,
        event_handler=event_handler,
    )


__all__ = [
    'NetworkMonitor',
    'NetworkMonitorTimeout',
    'NetworkMonitorError',
    'NetworkMonitorTerminalError',
    'NetworkInterceptionTriggered',
    'create_network_monitor',
]
