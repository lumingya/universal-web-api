"""
app/core/network_monitor.py - 网络响应拦截监听器

职责：
- 拦截网络请求响应
- 解析增量数据并流式输出
- 支持超时和取消机制
- 失败时触发回退到 DOM 模式
"""

import time
from typing import Generator, Optional, Dict, Callable

from app.core.config import logger, SSEFormatter, BrowserConstants
from app.core.parsers import ParserRegistry, ResponseParser


# ================= 自定义异常 =================

class NetworkMonitorTimeout(Exception):
    """网络监听超时异常（触发回退到 DOM 模式）"""
    pass


class NetworkMonitorError(Exception):
    """网络监听错误异常"""
    pass


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
    DEFAULT_FIRST_RESPONSE_TIMEOUT = 5.0   # 首次响应超时（触发回退）
    DEFAULT_HARD_TIMEOUT = 300             # 全局硬超时
    DEFAULT_RESPONSE_INTERVAL = 0.5        # 响应轮询间隔
    DEFAULT_SILENCE_THRESHOLD = 3.0        # 静默超时（无新数据）
    
    def __init__(self, tab, formatter: SSEFormatter,
                 parser: ResponseParser,
                 stop_checker: Optional[Callable[[], bool]] = None,
                 stream_config: Optional[Dict] = None):
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
        
    def pre_start(self):
        """
        在发送动作之前启动网络监听
        
        v5.11 改进：
        - 恢复实际启动（延迟启动会错过 requestWillBeSent 事件）
        - 但调用时机从 FILL_INPUT 延后到 CLICK send_btn / KEY_PRESS Enter 之前
        - 暴露窗口：从"发送前一刻"到"回复结束"，而非"输入开始"到"回复结束"
        
        调用时机：仅在 CLICK send_btn 或 KEY_PRESS Enter 之前
        """
        if self._pre_started or self._is_listening:
            return
        
        if not self._listen_pattern:
            logger.warning("[NetworkMonitor] listen_pattern 未配置")
            return
        
        try:
            # 启用复用模式：使用 tab 主连接，不创建额外 CDP session
            self.tab.listen._reuse_driver = True
            self.tab.listen.start(self._listen_pattern)
            self._pre_started = True
            self._is_listening = True
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
        if not self._is_listening:
            logger.warning(
                "[NetworkMonitor] 监听未预启动，在此启动"
                "（可能错过 requestWillBeSent）"
            )
            try:
                self.tab.listen.start(self._listen_pattern)
                self._is_listening = True
            except Exception as e:
                logger.error(f"[NetworkMonitor] 启动监听失败: {e}")
                raise NetworkMonitorError(f"启动监听失败: {e}")
        
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
            response = self.tab.listen.wait(timeout=timeout)

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

            # 检查响应对象结构
            if not hasattr(response, 'response') or not hasattr(response.response, 'body'):
                logger.debug(f"[NetworkMonitor] 响应对象结构异常: {type(response).__name__}")
                continue

            # 标记已收到响应（在读取 body 之前！）
            if not has_received_response:
                has_received_response = True
                logger.debug("[NetworkMonitor] 已捕获到首次响应")
            last_activity_time = time.time()

            # 读取响应体
            raw_body = response.response.body
            if isinstance(raw_body, (bytes, bytearray)):
                try:
                    raw_body = raw_body.decode('utf-8', errors='ignore')
                except Exception:
                    raw_body = raw_body.decode('utf-8', 'replace')

            if not raw_body:
                logger.debug("[NetworkMonitor] 响应体为空，跳过")
                continue

            logger.debug(f"[NetworkMonitor] 捕获响应 (size={len(raw_body)} bytes)")

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
                self.tab.listen.stop()
                self._is_listening = False
                logger.debug("[NetworkMonitor] 已停止监听（CDP session 已释放）")
            except Exception as e:
                logger.debug(f"[NetworkMonitor] 停止监听失败: {e}")
        
        # 即使 _is_listening 已经是 False，也尝试确保 listen 已停止
        # （防止异常路径导致状态不一致）
        elif hasattr(self.tab, 'listen') and self.tab.listen.listening:
            try:
                self.tab.listen.stop()
                logger.debug("[NetworkMonitor] 补充停止残留监听")
            except Exception:
                pass


# ================= 工厂函数 =================

def create_network_monitor(tab, formatter: SSEFormatter,
                           stream_config: Dict,
                           stop_checker: Optional[Callable[[], bool]] = None) -> NetworkMonitor:
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
    if not parser_id:
        raise ValueError("network.parser 未配置")
    
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
        stream_config=stream_config
    )


__all__ = [
    'NetworkMonitor',
    'NetworkMonitorTimeout',
    'NetworkMonitorError',
    'create_network_monitor',
]