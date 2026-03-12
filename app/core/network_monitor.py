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

    def _start_listen(self):
        if not self._listen_pattern:
            raise NetworkMonitorError("listen_pattern 未配置")

        self.tab.listen._reuse_driver = True
        self.tab.listen.start(self._listen_pattern)
        self._pre_started = True
        self._is_listening = True

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
            if isinstance(direct_body, dict):
                stream_full_text = self._nested_get(direct_body, "_stream", "fullText")
                if stream_full_text not in (None, ""):
                    return stream_full_text, "body._stream.fullText"
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
        last_activity_time = time.time()

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
            timeout = self._first_response_timeout if not has_received_response else self._response_interval
            
            # 等待响应
            try:
                if not self._listen_is_active():
                    self._ensure_listening("wait_inactive")
                response = self.tab.listen.wait(timeout=timeout)
            except Exception as e:
                err_text = str(e)
                if "监听未启动或已停止" in err_text and not has_received_response:
                    logger.warning("[NetworkMonitor] wait 前监听已失效，尝试重建后重试")
                    self._ensure_listening("wait_restart")
                    continue
                raise NetworkMonitorError(err_text) from e

            # 检查是否为无效响应
            if response is None or response is False:
                elapsed = time.time() - phase_start
                
                if not has_received_response:
                    logger.warning(f"[NetworkMonitor] 首次响应超时 ({elapsed:.1f}s)，触发回退")
                    raise NetworkMonitorTimeout(f"首次响应超时（{elapsed:.1f}s）")
                
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

            event = self._extract_event(response)
            if self._dispatch_event(event):
                logger.warning(
                    "[NetworkMonitor] 命中网络异常拦截，主动中断监听 "
                    f"(status={event.get('status')}, url={event.get('url', '')[:100]})"
                )
                raise NetworkInterceptionTriggered("network_intercepted")

            # 检查响应对象结构
            if not hasattr(response, 'response'):
                logger.debug(f"[NetworkMonitor] 响应对象结构异常: {type(response).__name__}")
                continue

            # 读取响应体，流式协议优先使用 _stream.fullText
            raw_body, raw_body_source = self._extract_raw_body(response)
            raw_body = self._normalize_raw_body(raw_body)

            if not raw_body:
                logger.debug("[NetworkMonitor] 响应体为空，跳过")
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
