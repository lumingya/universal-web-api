"""
app/core/network_monitor.py - 网络响应拦截监听器

职责：
- 拦截网络请求响应
- 解析增量数据并流式输出
- 支持超时和取消机制
- 失败时触发回退到 DOM 模式
"""

import time
import json
import re
from typing import Generator, Optional, Dict, Callable, Any

from app.core.config import logger, SSEFormatter, BrowserConstants
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
    MAX_LISTEN_RESTARTS = 3                # 监听状态异常后的最大重建次数
    
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
        
        self._listen_pattern = network_config.get("listen_pattern", "")
        self._stream_match_pattern = network_config.get(
            "stream_match_pattern",
            self._listen_pattern,
        )
        self._stream_match_mode = str(
            network_config.get("stream_match_mode", "keyword") or "keyword"
        ).strip().lower()
        self._first_response_timeout = network_config.get(
            "first_response_timeout",
            self.DEFAULT_FIRST_RESPONSE_TIMEOUT
        )
        self._hard_timeout = network_config.get(
            "hard_timeout",
            self.DEFAULT_HARD_TIMEOUT
        )
        self._response_interval = network_config.get(
            "response_interval",
            self.DEFAULT_RESPONSE_INTERVAL
        )
        self._silence_threshold = network_config.get(
            "silence_threshold",
            self.DEFAULT_SILENCE_THRESHOLD
        )
                
        # 监听预启动标记（用于提前启动监听）
        self._pre_started = False
        # 状态追踪
        self._is_listening = False
        self._total_chunks = 0
        self._prefetched_responses = []
        
        logger.debug(
            f"[NetworkMonitor] 初始化完成 "
            f"(pattern={self._listen_pattern!r}, "
            f"parser={parser.get_id()})"
        )

    def _listen_is_active(self) -> bool:
        try:
            return bool(
                hasattr(self.tab, "listen")
                and getattr(self.tab.listen, "listening", False)
            )
        except Exception:
            return False

    def _safe_stop_listen(self):
        try:
            if self._listen_is_active():
                self.tab.listen.stop()
        except Exception:
            pass

    @staticmethod
    def _is_restartable_listen_error(err_text: str) -> bool:
        err_text = str(err_text or "")
        return (
            "监听未启动或已停止" in err_text
            or ("NoneType" in err_text and "is_running" in err_text)
        )

    def _start_listen(self):
        if not self._listen_pattern:
            raise NetworkMonitorError("listen_pattern 未配置")

        self._prefetched_responses = []
        self.tab.listen._reuse_driver = True
        self.tab.listen.start(self._listen_pattern)
        self._pre_started = True
        self._is_listening = True

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

            response = self.tab.listen.wait(timeout=max(0.01, float(timeout or 0.01)))
        except Exception as e:
            err_text = str(e)
            if self._is_restartable_listen_error(err_text):
                try:
                    self._ensure_listening("poll_send_activity_restart")
                except Exception:
                    return {"seen": False, "matched": False, "error": err_text}
                return {"seen": False, "matched": False, "error": err_text}
            return {"seen": False, "matched": False, "error": err_text}

        if response in (None, False):
            return {"seen": False, "matched": False}

        self._prefetched_responses.append(response)
        event = self._extract_event(response)
        matched = False
        try:
            matched = self.parser.get_id() != "event_only" and self._matches_stream_target(event)
        except Exception:
            matched = False

        return {
            "seen": True,
            "matched": matched,
            "event": event,
        }

    def _ensure_listening(self, reason: str):
        if self._is_listening and self._listen_is_active():
            return

        self._is_listening = False
        self._pre_started = False
        self._safe_stop_listen()
        try:
            self._start_listen()
            logger.debug(f"[NetworkMonitor] 已重建监听 ({reason})")
        except Exception as e:
            logger.error(f"[NetworkMonitor] 启动监听失败 ({reason}): {e}")
            raise NetworkMonitorError(f"启动监听失败: {e}")

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
                merged = self._merge_stream_chunks(source_value)
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
                        merged = self._merge_stream_chunks(source_value)
                        if merged:
                            return merged, source_name
                        continue
                    return source_value, source_name
                logger.debug(
                    "[NetworkMonitor][DirectBody] no stream field found in body container: "
                    f"{self._describe_json_container(direct_body)}"
                )
            else:
                logger.debug(
                    "[NetworkMonitor][DirectBody] body is not a JSON container: "
                    f"{self._describe_json_container(direct_body)}"
                )
            return direct_body, "body"

        return None, "empty"

    @staticmethod
    def _merge_stream_chunks(chunks: Any) -> str:
        if not isinstance(chunks, list):
            return ""

        parts = []
        for chunk in chunks:
            data = NetworkMonitor._nested_get(chunk, "data")
            if data in (None, ""):
                continue
            if isinstance(data, (bytes, bytearray)):
                data = data.decode("utf-8", errors="ignore")
            elif not isinstance(data, str):
                data = str(data)
            parts.append(data)
        return "".join(parts)

    @staticmethod
    def _normalize_raw_body(raw_body: Any) -> str:
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
        candidates = (
            self._nested_get(resp, "headers", "content-type"),
            self._nested_get(resp, "headers", "Content-Type"),
            self._nested_get(resp, "headers", "contentType"),
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

    def _wait_for_stream_body(self, response: Any, initial_body: str, initial_source: str) -> tuple[str, str]:
        body = initial_body
        source = initial_source
        if body:
            return body, source

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
            if len(next_body) > previous_len:
                logger.debug(
                    f"[NetworkMonitor] 流响应继续增长 "
                    f"(source={raw_body_source}, size={len(next_body)} chars)"
                )
                return next_body, raw_body_source

            if self._stream_capture_complete(response):
                return next_body, raw_body_source

            time.sleep(0.05)

        return body, source
        
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
        self.parser.reset()
        self._total_chunks = 0
        
        # 兜底：如果 pre_start 未被调用，在此启动（可能错过首包）
        if not self._is_listening or not self._listen_is_active():
            logger.warning(
                "[NetworkMonitor] 监听未预启动，在此启动"
                "（可能错过 requestWillBeSent）"
            )
            self._ensure_listening("monitor_start")
        
        try:
            yield from self._stream_output_phase(completion_id)
        finally:
            # 立即停止：关闭 Network.enable + 释放额外 CDP session
            self._cleanup()
            self._pre_started = False
    
    def _stream_output_phase(self, completion_id: str) -> Generator[str, None, None]:
        """
        流式输出阶段
        """
        phase_start = time.time()
        has_received_response = False
        has_seen_stream_target = False
        last_activity_time = time.time()
        listen_restart_attempts = 0

        while True:
            # 检查全局超时
            if time.time() - phase_start > self._hard_timeout:
                logger.error(f"[NetworkMonitor] 超过最大监听时间 {self._hard_timeout}s，强制退出")
                break

            # 检查取消信号
            if self._should_stop():
                logger.debug("[NetworkMonitor] 监听被取消")
                break

            # 设置超时时间
            timeout = self._first_response_timeout if not has_seen_stream_target else self._response_interval

            # 等待响应
            try:
                if self._prefetched_responses:
                    response = self._prefetched_responses.pop(0)
                else:
                    if not self._listen_is_active():
                        self._ensure_listening("wait_inactive")
                    response = self.tab.listen.wait(timeout=timeout)
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
                    continue
                raise NetworkMonitorError(err_text) from e

            # 检查是否为无效响应
            if response is None or response is False:
                elapsed = time.time() - phase_start
                
                if not has_seen_stream_target:
                    logger.warning(f"[NetworkMonitor] 目标流响应超时 ({elapsed:.1f}s)，触发回退")
                    raise NetworkMonitorTimeout(f"目标流响应超时（{elapsed:.1f}s）")
                
                silence_duration = time.time() - last_activity_time
                if silence_duration > self._silence_threshold:
                    logger.debug(f"[NetworkMonitor] 静默超时 ({silence_duration:.1f}s)，结束监听")
                    break
                continue

            # 标记已收到响应（在读取 body 之前！）
            if not has_received_response:
                has_received_response = True
                logger.debug("[NetworkMonitor] 已捕获到首次响应")
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
                if not has_received_response:
                    has_received_response = True
                    logger.debug("[NetworkMonitor] event-only 已捕获到首个网络事件")
                last_activity_time = time.time()
                continue

            if not self._matches_stream_target(event):
                logger.debug(
                    f"[NetworkMonitor] 非流式目标响应，跳过解析 "
                    f"(url={event.get('url', '')[:100]})"
                )
                continue

            if not has_seen_stream_target:
                has_seen_stream_target = True
                logger.debug("[NetworkMonitor] 已捕获到首个流目标响应")

            logger.debug(
                "[NetworkMonitor] 命中流目标 "
                f"(status={event.get('status')}, method={event.get('method')}, "
                f"url={event.get('url', '')[:120]})"
            )

            if not has_received_response:
                has_received_response = True
                logger.debug("[NetworkMonitor] 已捕获到首个有效响应")
            last_activity_time = time.time()

            # 检查响应对象结构
            if not hasattr(response, 'response'):
                logger.debug(f"[NetworkMonitor] 响应对象结构异常: {type(response).__name__}")
                continue

            # 读取响应体，流式协议优先使用 _stream.fullText
            raw_body, raw_body_source = self._extract_raw_body(response)
            raw_body = self._normalize_raw_body(raw_body)
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

            if not raw_body and is_event_stream:
                raw_body, raw_body_source = self._wait_for_stream_body(
                    response,
                    raw_body,
                    raw_body_source,
                )

            if not raw_body:
                logger.debug(
                    "[NetworkMonitor] 响应体为空，跳过 "
                    f"(stream={is_event_stream}, source={raw_body_source})"
                )
                continue

            logger.debug(
                f"[NetworkMonitor] 捕获响应 "
                f"(source={raw_body_source}, size={len(raw_body)} chars)"
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
                    raw_body = next_body
                    raw_body_source = next_source
                    try:
                        parse_result = self.parser.parse_chunk(raw_body)
                    except Exception as e:
                        logger.warning(f"[NetworkMonitor] 二次解析异常: {e}")
                        continue

            if parse_result.get("error"):
                logger.warning(f"[NetworkMonitor] 解析失败: {parse_result['error']}")
                continue

            # 提取内容
            content = parse_result.get("content", "")
            done = parse_result.get("done", False)

            if content:
                logger.debug(f"[NetworkMonitor] parsed content={_debug_preview(content)}")
                last_activity_time = time.time()
                self._total_chunks += 1
                yield self.formatter.pack_chunk(content, completion_id=completion_id)

            if done:
                logger.debug("[NetworkMonitor] 检测到结束标志，完成监听")
                break

        logger.debug(f"[NetworkMonitor] 监听结束 (chunks={self._total_chunks}, duration={time.time() - phase_start:.1f}s)")
        
    def _cleanup(self):
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
                self._is_listening = False
                self._pre_started = False
                self._prefetched_responses = []
                logger.debug("[NetworkMonitor] 已停止监听（CDP session 已释放）")
            except Exception as e:
                logger.debug(f"[NetworkMonitor] 停止监听失败: {e}")
        
        # 即使 _is_listening 已经是 False，也尝试确保 listen 已停止
        # （防止异常路径导致状态不一致）
        elif self._listen_is_active():
            try:
                self._safe_stop_listen()
                logger.debug("[NetworkMonitor] 补充停止残留监听")
            except Exception:
                pass


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
    'NetworkInterceptionTriggered',
    'create_network_monitor',
]
