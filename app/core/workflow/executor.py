"""
app/core/workflow/executor.py - 工作流执行器

职责：
- 工作流步骤编排
- 点击、等待等基础操作
- 可靠发送（图片上传场景）
- 与 StreamMonitor 协同
"""

import copy
import json
import time
import random
import threading
import uuid
from typing import Generator, Dict, Any, Callable, Optional

from app.core.config import (
    logger,
    BrowserConstants,
    SSEFormatter,
    ElementNotFoundError,
    WorkflowError,
)
from app.core.elements import ElementFinder
from app.core.parsers import ParserRegistry
from app.utils.human_mouse import smooth_move_mouse, idle_drift, human_scroll, cdp_precise_click
from app.core.stream_monitor import StreamMonitor
from app.core.network_monitor import (
    create_network_monitor,
    NetworkMonitorTimeout,
    NetworkMonitorError,
    NetworkMonitorTerminalError,
    NetworkInterceptionTriggered,
)

from .attachment_monitor import AttachmentMonitor
from .text_input import TextInputHandler
from .image_input import ImageInputHandler


# ================= 工作流执行器 =================

class WorkflowExecutor:
    """工作流执行器"""

    _KIMI_CAPTURE_BOOTSTRAP_JS = r"""
(() => {
  const W = window;
  const KEY = "__KIMI_CAPTURE__";
  const TARGET = "/apiv2/kimi.gateway.chat.v1.ChatService/Chat";

  const toEscapedBytes = (chunk) => {
    let out = "";
    for (let i = 0; i < chunk.length; i += 1) {
      out += "\\u00" + chunk[i].toString(16).padStart(2, "0");
    }
    return out;
  };

  const cap = W[KEY] = W[KEY] || {
    installed: false,
    seq: 0,
    requests: [],
    currentToken: null,
    maxRequests: 12
  };

  if (cap.installed) {
    return { installed: true, patched: false, requests: cap.requests.length };
  }

  if (typeof W.fetch !== "function") {
    return { installed: false, reason: "fetch_missing" };
  }

  const originalFetch = W.fetch.bind(W);
  cap.installed = true;
  cap.installedAt = Date.now();

  W.fetch = async function(input, init) {
    const response = await originalFetch(input, init);

    try {
      const url = input && typeof input === "object" && "url" in input
        ? String(input.url || "")
        : String(input || "");

      if (!url.includes(TARGET)) {
        return response;
      }

      const request = {
        id: "kimi_" + (++cap.seq),
        url,
        token: cap.currentToken || null,
        startedAt: Date.now(),
        lastChunkAt: 0,
        chunkCount: 0,
        escapedFullText: "",
        complete: false,
        error: null,
        contentType: response.headers ? (response.headers.get("content-type") || "") : ""
      };

      cap.requests.push(request);
      while (cap.requests.length > (cap.maxRequests || 12)) {
        cap.requests.shift();
      }

      const cloned = response.clone();
      if (cloned.body && typeof cloned.body.getReader === "function") {
        const reader = cloned.body.getReader();
        (async () => {
          try {
            while (true) {
              const { done, value } = await reader.read();
              if (done) {
                request.complete = true;
                request.endedAt = Date.now();
                break;
              }
              if (!value) {
                continue;
              }
              request.chunkCount += 1;
              request.lastChunkAt = Date.now();
              request.escapedFullText += toEscapedBytes(value);
            }
          } catch (error) {
            request.error = String(error && error.message ? error.message : error);
            request.complete = true;
            request.endedAt = Date.now();
          }
        })();
      } else {
        request.complete = true;
        request.endedAt = Date.now();
      }
    } catch (error) {
      cap.lastHookError = String(error && error.message ? error.message : error);
    }

    return response;
  };

  return { installed: true, patched: true, requests: cap.requests.length };
})();
"""
    
    def __init__(self, tab, stealth_mode: bool = False, 
                 should_stop_checker: Callable[[], bool] = None,
                 extractor = None,
                 image_config: Dict = None,
                 stream_config: Dict = None,
                 file_paste_config: Dict = None,
                 selectors: Dict = None,
                 session = None):
        self.tab = tab
        self.session = session
        self.stealth_mode = stealth_mode
        self.finder = ElementFinder(tab)
        self.formatter = SSEFormatter()
        
        self._should_stop = should_stop_checker or (lambda: False)
        self._extractor = extractor
        self._image_config = image_config or {}  
        self._stream_config = stream_config or {}
        self._selectors = selectors or {}
        
        # 🆕 初始化双 Monitor（优先网络，回退 DOM）
        self._network_monitor = None
        self._stream_monitor = None
        self._last_input_element = None
        self._last_input_target_key = ""
        
        # 检查是否启用网络监听模式
        self._stream_mode = stream_config.get("mode", "dom") if stream_config else "dom"
        network_config = stream_config.get("network", {}) if stream_config else {}
        self._network_config = network_config
        self._intercept_only_mode = False
        self._use_kimi_page_capture = (
            self._stream_mode == "network"
            and str(network_config.get("parser", "") or "").strip().lower() == "kimi"
        )
        self._kimi_capture_token: Optional[str] = None
        self._kimi_capture_init_js_id: Optional[str] = None
        self._kimi_page_parser = ParserRegistry.get("kimi") if self._use_kimi_page_capture else None
        if self._use_kimi_page_capture:
            self._ensure_kimi_page_capture_init_js()

        interception_enabled = False
        interception_pattern = ""
        if self.session is not None:
            try:
                from app.services.command_engine import command_engine
                interception_enabled = command_engine.has_network_interception_for_session(self.session)
                if interception_enabled:
                    interception_pattern = command_engine.get_network_listen_pattern(self.session)
            except Exception as e:
                logger.debug(f"[Executor] 读取网络拦截命令失败（忽略）: {e}")

        # 正常网络流式：使用 parser 解析增量
        if self._stream_mode == "network" and network_config and network_config.get("parser"):
            try:
                effective_stream_config = stream_config
                if interception_enabled:
                    network_listen_pattern = str(network_config.get("listen_pattern") or "").strip()
                    merged_pattern = self._merge_network_listen_patterns(
                        network_listen_pattern,
                        interception_pattern,
                    )
                    effective_stream_config = copy.deepcopy(stream_config or {})
                    effective_network_config = dict(effective_stream_config.get("network") or {})
                    effective_network_config["listen_pattern"] = merged_pattern
                    effective_network_config["stream_match_pattern"] = (
                        network_listen_pattern or merged_pattern
                    )
                    effective_network_config["stream_match_mode"] = "keyword"
                    effective_stream_config["network"] = effective_network_config

                self._network_monitor = create_network_monitor(
                    tab=tab,
                    formatter=self.formatter,
                    stream_config=effective_stream_config,
                    stop_checker=should_stop_checker,
                    event_handler=self._handle_network_event
                )
                logger.debug(
                    f"[Executor] 网络监听器已启用 "
                    f"(parser={network_config.get('parser')}, "
                    f"listen_pattern={effective_stream_config.get('network', {}).get('listen_pattern')!r})"
                )
            except Exception as e:
                logger.warning(f"[Executor] 网络监听器创建失败: {e}")

        # DOM 流式 + 网络异常拦截：启用 event-only 网络监听（独立于 stream_mode）
        elif interception_enabled:
            try:
                interception_cfg = dict(network_config or {})
                if not interception_cfg.get("listen_pattern"):
                    interception_cfg["listen_pattern"] = interception_pattern or "http"
                interception_cfg["event_only"] = True
                interception_cfg.setdefault("first_response_timeout", 300)
                interception_cfg.setdefault("silence_threshold", 2)
                interception_cfg.setdefault("response_interval", 0.3)

                self._network_monitor = create_network_monitor(
                    tab=tab,
                    formatter=self.formatter,
                    stream_config={"network": interception_cfg},
                    stop_checker=should_stop_checker,
                    event_handler=self._handle_network_event
                )
                self._intercept_only_mode = True
                logger.debug(
                    "[Executor] 网络异常拦截已启用（event-only） "
                    f"(pattern={interception_cfg.get('listen_pattern')!r})"
                )
            except Exception as e:
                logger.warning(f"[Executor] 网络异常拦截监听创建失败: {e}")
        
        # 始终创建 DOM 监听器（作为回退）
        self._stream_monitor = StreamMonitor(
            tab=tab,
            finder=self.finder,
            formatter=self.formatter,
            stop_checker=should_stop_checker,
            extractor=extractor,
            image_config=image_config,
            stream_config=stream_config
        )
        
        self._completion_id = SSEFormatter._generate_id()
                
        # 🆕 隐身模式鼠标位置追踪（CDP 绝对坐标）
        self._mouse_pos = None
        self._attachment_monitor = AttachmentMonitor(
            tab=tab,
            selectors=self._selectors,
            check_cancelled_fn=self._check_cancelled,
        )
        # 初始化输入处理器
        self._text_handler = TextInputHandler(
            tab=tab,
            stealth_mode=stealth_mode,
            smart_delay_fn=self._smart_delay,
            check_cancelled_fn=self._check_cancelled,
            file_paste_config=file_paste_config,
            selectors=self._selectors,
            attachment_monitor=self._attachment_monitor,
        )
        
        self._image_handler = ImageInputHandler(
            tab=tab,
            stealth_mode=stealth_mode,
            smart_delay_fn=self._smart_delay,
            check_cancelled_fn=self._check_cancelled,
            attachment_monitor=self._attachment_monitor,
        )
        
        if extractor:
            logger.debug(f"WorkflowExecutor 使用提取器: {extractor.get_id()}")
        
        if self._image_config.get("enabled"):
            logger.debug(f"[IMAGE] 图片提取已启用")
        
        if self.stealth_mode:
            logger.debug("[STEALTH] 隐身模式已启用")

    def _handle_network_event(self, event: Dict[str, Any]) -> bool:
        """
        将网络事件上报给命令引擎。
        返回 True 表示命中拦截条件，应立即中断当前监听。
        """
        if not self.session:
            return False
        try:
            from app.services.command_engine import command_engine
            matched = bool(command_engine.handle_network_event(self.session, event))
            if matched:
                # 让 DOM/STREAM 流程也能立即停下来（与 stream_mode 无关）
                try:
                    from app.services.request_manager import request_manager
                    request_manager.cancel_current("network_intercepted", tab_id=self.session.id)
                except Exception:
                    pass
                try:
                    if hasattr(self.tab, "stop_loading"):
                        self.tab.stop_loading()
                    self.tab.run_js("if (window.stop) { window.stop(); }")
                except Exception:
                    pass
            return matched
        except Exception as e:
            logger.debug(f"[Executor] 网络事件上报失败（忽略）: {e}")
            return False

    @staticmethod
    def _merge_network_listen_patterns(primary: str, secondary: str) -> str:
        first = str(primary or "").strip()
        second = str(secondary or "").strip()

        if not first:
            return second or "http"
        if not second:
            return first

        first_lower = first.lower()
        second_lower = second.lower()
        if first_lower == "http" or second_lower == "http":
            return "http"
        if first_lower == second_lower:
            return first
        if first_lower in second_lower:
            return first
        if second_lower in first_lower:
            return second
        return "http"

    def _prepare_kimi_page_capture(self) -> None:
        if not self._use_kimi_page_capture:
            return

        self._ensure_kimi_page_capture_init_js()
        token = f"kimi_{uuid.uuid4().hex[:12]}"
        install_result = self.tab.run_js(
            f"return {self._KIMI_CAPTURE_BOOTSTRAP_JS.strip()}"
        )
        reset_result = self.tab.run_js(
            """
            return (function(token) {
              const cap = window.__KIMI_CAPTURE__ = window.__KIMI_CAPTURE__ || {};
              cap.currentToken = token;
              cap.requests = [];
              cap.lastResetAt = Date.now();
              return { ok: true, token: cap.currentToken };
            })(arguments[0]);
            """,
            token,
        )
        self._kimi_capture_token = token
        if install_result is not None:
            logger.debug(f"[Executor] Kimi 页面抓流已准备: {install_result}")

    def _ensure_kimi_page_capture_init_js(self) -> None:
        if not self._use_kimi_page_capture or self._kimi_capture_init_js_id:
            return

        try:
            self._kimi_capture_init_js_id = self.tab.add_init_js(
                self._KIMI_CAPTURE_BOOTSTRAP_JS.strip()
            )
            logger.debug(
                f"[Executor] Kimi 页面抓流已注册 document-start 注入: {self._kimi_capture_init_js_id}"
            )
        except Exception as e:
            logger.debug(f"[Executor] Kimi document-start 注入失败: {e}")

    def _get_kimi_page_capture_state(self) -> Dict[str, Any]:
        state = self.tab.run_js(
            """
            return (function(token) {
              const cap = window.__KIMI_CAPTURE__;
              if (!cap) {
                return { installed: false, found: false };
              }

              const requests = Array.isArray(cap.requests) ? cap.requests : [];
              let target = null;

              for (let i = requests.length - 1; i >= 0; i -= 1) {
                const item = requests[i];
                if (!token || item.token === token) {
                  target = item;
                  break;
                }
              }

              return {
                installed: true,
                currentToken: cap.currentToken || null,
                found: !!target,
                requestId: target ? (target.id || "") : "",
                escapedFullText: target ? (target.escapedFullText || "") : "",
                complete: !!(target && target.complete),
                error: target ? (target.error || null) : null,
                chunkCount: target ? (target.chunkCount || 0) : 0,
                startedAt: target ? (target.startedAt || 0) : 0,
                lastChunkAt: target ? (target.lastChunkAt || 0) : 0
              };
            })(arguments[0]);
            """,
            self._kimi_capture_token or "",
        )
        return state if isinstance(state, dict) else {}

    def _monitor_kimi_page_capture(
        self,
        completion_id: str,
    ) -> Generator[str, None, None]:
        if not self._use_kimi_page_capture or self._kimi_page_parser is None:
            raise NetworkMonitorError("kimi_page_capture_disabled")

        parser = self._kimi_page_parser
        parser.reset()

        first_response_timeout = float(
            self._network_config.get("first_response_timeout", 30) or 30
        )
        response_interval = float(
            self._network_config.get("response_interval", 0.3) or 0.3
        )
        silence_threshold = float(
            self._network_config.get("silence_threshold", 3) or 3
        )
        hard_timeout = float(
            self._stream_config.get("hard_timeout", 300) or 300
        )

        phase_start = time.time()
        last_activity = phase_start
        last_raw_len = 0
        seen_request = False

        while True:
            if self._check_cancelled():
                logger.debug("[Executor] Kimi 页面抓流被取消")
                break

            now = time.time()
            if now - phase_start > hard_timeout:
                raise NetworkMonitorError(f"kimi_page_capture_hard_timeout:{hard_timeout:.1f}s")

            state = self._get_kimi_page_capture_state()
            if not state.get("installed"):
                raise NetworkMonitorError("kimi_page_capture_not_installed")

            if state.get("error"):
                raise NetworkMonitorError(f"kimi_page_capture_error:{state.get('error')}")

            raw_response = str(state.get("escapedFullText", "") or "")
            if state.get("found"):
                if not seen_request:
                    logger.debug(
                        "[Executor] Kimi 页面抓流已命中请求 "
                        f"(request_id={state.get('requestId')}, token={self._kimi_capture_token})"
                    )
                seen_request = True

            if len(raw_response) > last_raw_len:
                last_activity = now
                last_raw_len = len(raw_response)

            if raw_response:
                parse_result = parser.parse_chunk(raw_response)
                if parse_result.get("error"):
                    raise NetworkMonitorError(f"kimi_page_capture_parse_error:{parse_result['error']}")

                content = parse_result.get("content", "")
                done = bool(parse_result.get("done")) or bool(state.get("complete"))

                if content:
                    logger.debug(f"[Executor] Kimi 页面抓流产出: {repr(content)[:240]}")
                    yield self.formatter.pack_chunk(content, completion_id=completion_id)

                if done:
                    logger.debug("[Executor] Kimi 页面抓流完成")
                    break

            elif seen_request and state.get("complete"):
                logger.debug("[Executor] Kimi 页面抓流请求已结束但无有效内容")
                break

            if not seen_request and (now - phase_start) > first_response_timeout:
                raise NetworkMonitorTimeout(f"kimi_page_capture_first_response_timeout:{first_response_timeout:.1f}s")

            if seen_request and (now - last_activity) > silence_threshold:
                logger.debug(
                    "[Executor] Kimi 页面抓流静默超时 "
                    f"({now - last_activity:.1f}s)"
                )
                break

            time.sleep(max(0.05, response_interval))
    
    # ================= 控制方法 =================
    
    def _check_cancelled(self) -> bool:
        """检查是否被取消"""
        return self._should_stop()
    
    def _smart_delay(self, min_sec: float = None, max_sec: float = None):
        """
        智能延迟（v5.5 增强版）
        
        改进：
        - 正态分布（更像人类）
        - 10% 概率额外停顿（模拟走神）
        - 可被取消中断
        """
        if not self.stealth_mode:
            return
        
        min_sec = min_sec or BrowserConstants.STEALTH_DELAY_MIN
        max_sec = max_sec or BrowserConstants.STEALTH_DELAY_MAX
        
        # 正态分布参数
        mean = (min_sec + max_sec) / 2
        std = (max_sec - min_sec) / 4
        
        # 生成延迟时间
        total_delay = random.gauss(mean, std)
        
        # 限制范围
        total_delay = max(min_sec, min(total_delay, max_sec))
        
        # 10% 概率"走神"（额外停顿）
        pause_prob = getattr(BrowserConstants, 'STEALTH_PAUSE_PROBABILITY', 0.1)
        pause_max = getattr(BrowserConstants, 'STEALTH_PAUSE_EXTRA_MAX', 0.8)
        
        if random.random() < pause_prob:
            extra = random.uniform(0.2, pause_max)
            total_delay = min(total_delay + extra, 1.0)  # 不超过 1s
            logger.debug(f"[STEALTH] 随机停顿 +{extra:.2f}s")
        
        # 可中断的等待
        elapsed = 0
        step = 0.05
        
        while elapsed < total_delay:
            if self._check_cancelled():
                return
            time.sleep(min(step, total_delay - elapsed))
            elapsed += step
    
    # ================= 隐身模式辅助方法 =================
    
    def _idle_wait(self, duration: float):
        """
        带微漂移的空闲等待（隐身模式专用）
        
        如果有已知鼠标位置，等待期间产生微小漂移事件；
        否则退化为纯 sleep（仍可中断）。
        """
        if self._mouse_pos is not None:
            self._mouse_pos = idle_drift(
                tab=self.tab,
                duration=duration,
                center_pos=self._mouse_pos,
                check_cancelled=self._check_cancelled
            )
        else:
            elapsed = 0
            step = 0.1
            while elapsed < duration:
                if self._check_cancelled():
                    return
                time.sleep(min(step, duration - elapsed))
                elapsed += step
    
    def _stealth_move_to_element(self, ele):
        """
        隐身模式下平滑移动鼠标到元素附近
        
        通过 DrissionPage 原生属性获取坐标，不注入 JS。
        如果坐标获取失败，跳过移动（后续 click 自带定位）。
        """
        if self._mouse_pos is None:
            return
        
        target = self._get_element_viewport_pos(ele)
        if target is None:
            return
        
        # 随机偏移（不精确命中中心）
        tx = target[0] + random.randint(-8, 8)
        ty = target[1] + random.randint(-5, 5)
        
        try:
            self._mouse_pos = smooth_move_mouse(
                tab=self.tab,
                from_pos=self._mouse_pos,
                to_pos=(tx, ty),
                check_cancelled=self._check_cancelled
            )
        except Exception as e:
            logger.debug(f"[STEALTH] 平滑移动异常（可忽略）: {e}")
    
    def _get_element_viewport_pos(self, ele) -> Optional[tuple]:
        """
        获取元素视口坐标（不注入 JS）
        
        依次尝试多种 DrissionPage 原生属性。
        对于可见的固定位置元素（如聊天输入框），
        页面坐标近似等于视口坐标。
        """
        try:
            r = ele.rect
            
            # 尝试 viewport 相关属性
            for attr in ('viewport_midpoint', 'viewport_click_point'):
                pos = getattr(r, attr, None)
                if pos and len(pos) >= 2:
                    return (int(pos[0]), int(pos[1]))
            
            # midpoint（页面坐标，对可见元素近似视口坐标）
            pos = getattr(r, 'midpoint', None)
            if pos and len(pos) >= 2:
                return (int(pos[0]), int(pos[1]))
            
            # click_point
            pos = getattr(r, 'click_point', None)
            if pos and len(pos) >= 2:
                return (int(pos[0]), int(pos[1]))
            
            # location + size 计算中心
            loc = getattr(r, 'location', None)
            size = getattr(r, 'size', None)
            if loc and size and len(loc) >= 2 and len(size) >= 2:
                return (int(loc[0] + size[0] / 2), int(loc[1] + size[1] / 2))
        except Exception:
            pass
        
        return None
    
    def _get_viewport_size(self) -> tuple:
        """获取视口尺寸（不注入 JS）"""
        try:
            r = self.tab.rect
            for attr in ('viewport_size', 'size'):
                s = getattr(r, attr, None)
                if s and len(s) >= 2 and s[0] > 100:
                    return (int(s[0]), int(s[1]))
        except Exception:
            pass
        return (1200, 800)
    
    # ================= 步骤执行 =================
    
    def execute_step(self, action: str, selector: str,
                     target_key: str, value: Any = None,
                     optional: bool = False,
                     context: Dict = None) -> Generator[str, None, None]:
        """执行单个步骤"""
        
        if self._check_cancelled():
            logger.debug(f"步骤 {action} 跳过（已取消）")
            return
        
        logger.debug(f"执行: {action} -> {target_key}")
        self._context = context
        
        try:
            if action == "WAIT":
                wait_time = float(value or 0.5)
                elapsed = 0
                while elapsed < wait_time:
                    if self._check_cancelled():
                        return
                    time.sleep(min(0.1, wait_time - elapsed))
                    elapsed += 0.1
            
            elif action == "KEY_PRESS":
                key = target_key or value
                # 包含 Enter 的按键（Enter、Ctrl+Enter 等）可能触发提交
                if self._combo_contains_submit_key(key):
                    self._wait_for_attachments_ready_before_send(
                        self._selectors.get("send_btn", "")
                    )
                    if self._network_monitor is not None:
                        self._prepare_kimi_page_capture()
                        self._network_monitor.pre_start()
                self._execute_keypress_combo(key)

            elif action == "JS_EXEC":
                self._execute_javascript(value)
            
            elif action == "CLICK":
                # ===== 隐身模式：首次交互前执行人类行为预热 =====
                if self.stealth_mode and not getattr(self, '_page_warmed_up', False):
                    self._warmup_page_for_stealth()
                    self._page_warmed_up = True
                
                if target_key == "send_btn":
                    self._wait_for_attachments_ready_before_send(selector)
                    # 🆕 发送前启动网络监听（如果已配置）
                    if self._network_monitor is not None:
                        self._prepare_kimi_page_capture()
                        self._network_monitor.pre_start()
                    
                    self._execute_click_send_reliably(
                        selector=selector,
                        target_key=target_key,
                        optional=optional,
                    )
                else:
                    self._execute_click(selector, target_key, optional)

            elif action == "COORD_CLICK":
                if self.stealth_mode and not getattr(self, '_page_warmed_up', False):
                    self._warmup_page_for_stealth()
                    self._page_warmed_up = True

                self._execute_coord_click(value, optional)
            
            elif action == "FILL_INPUT":
                
                prompt = context.get("prompt", "") if context else ""
                self._execute_fill(selector, prompt, target_key, optional)
            
            elif action in ("STREAM_WAIT", "STREAM_OUTPUT"):
                user_input = context.get("prompt", "") if context else ""
                
                # 网络流式输出与网络异常拦截解耦：
                # - mode=network: 走网络流式（可回退 DOM）
                # - mode!=network 且启用拦截: 后台消费网络事件，前台仍走 DOM
                monitor_used = None
                use_network_stream = (
                    self._network_monitor is not None
                    and not self._intercept_only_mode
                    and self._stream_mode == "network"
                )

                if use_network_stream:
                    try:
                        if self._use_kimi_page_capture:
                            logger.debug("[Executor] 尝试 Kimi 页面抓流模式")
                            yield from self._monitor_kimi_page_capture(
                                completion_id=self._completion_id
                            )
                            monitor_used = "kimi_page"
                        else:
                            logger.debug("[Executor] 尝试网络监听模式")
                            yield from self._network_monitor.monitor(
                                selector=selector,
                                user_input=user_input,
                                completion_id=self._completion_id
                            )
                            monitor_used = "network"

                    except NetworkInterceptionTriggered as e:
                        logger.warning(f"[Executor] 网络拦截已触发: {e}")
                        raise WorkflowError("network_intercepted")

                    except NetworkMonitorTerminalError as e:
                        logger.error(f"[Executor] 目标流已确认失败，终止工作流: {e}")
                        raise WorkflowError(f"stream_terminal_error:{e}")
                    
                    except NetworkMonitorTimeout as e:
                        logger.warning(
                            f"[Executor] 网络监听超时，回退到 DOM 模式: {e}"
                        )
                        # 回退到 DOM 监听
                        yield from self._stream_monitor.monitor(
                            selector=selector,
                            user_input=user_input,
                            completion_id=self._completion_id
                        )
                        monitor_used = "dom_fallback"
                    
                    except NetworkMonitorError as e:
                        logger.error(
                            f"[Executor] 网络监听错误，回退到 DOM 模式: {e}"
                        )
                        # 回退到 DOM 监听
                        yield from self._stream_monitor.monitor(
                            selector=selector,
                            user_input=user_input,
                            completion_id=self._completion_id
                        )
                        monitor_used = "dom_fallback"
                
                else:
                    event_thread = None

                    # DOM 模式下，若启用了网络拦截，则后台消费事件
                    if self._network_monitor is not None and self._intercept_only_mode:
                        def _consume_events():
                            try:
                                for _ in self._network_monitor.monitor(
                                    selector=selector,
                                    user_input=user_input,
                                    completion_id=self._completion_id,
                                ):
                                    if self._check_cancelled():
                                        break
                            except (
                                NetworkInterceptionTriggered,
                                NetworkMonitorTimeout,
                                NetworkMonitorTerminalError,
                                NetworkMonitorError,
                            ):
                                pass
                            except Exception as e:
                                logger.debug(f"[Executor] 后台网络事件监听结束: {e}")

                        event_thread = threading.Thread(
                            target=_consume_events,
                            daemon=True,
                            name="net-intercept-bg",
                        )
                        event_thread.start()

                    # 未配置网络监听，直接使用 DOM 监听
                    try:
                        yield from self._stream_monitor.monitor(
                            selector=selector,
                            user_input=user_input,
                            completion_id=self._completion_id
                        )
                        monitor_used = "dom"
                    finally:
                        if event_thread is not None:
                            try:
                                self._network_monitor._cleanup()
                            except Exception:
                                pass
                            event_thread.join(timeout=0.2)
                
                if monitor_used:
                    logger.debug(f"[Executor] 监听完成 (mode={monitor_used})")
            
            else:
                logger.debug(f"未知动作: {action}")
        
        except ElementNotFoundError as e:
            if self._check_cancelled():
                logger.info(f"[Executor] step cancelled after element lookup failure [{action}]: {e}")
                return
            if not optional:
                yield self.formatter.pack_error(f"元素未找到: {str(e)}")
                raise
        
        except Exception as e:
            if self._check_cancelled():
                logger.info(f"[Executor] step cancelled; suppressing exception [{action}]: {e}")
                return
            logger.error(f"步骤执行失败 [{action}]: {e}")
            if not optional:
                yield self.formatter.pack_error(f"执行失败: {str(e)}")
                raise
    
    def _execute_keypress(self, key: str):
        """执行按键操作（隐身模式人类化时序）"""
        if self._check_cancelled():
            return
       
        
        if self.stealth_mode:
            self.tab.actions.key_down(key)
            time.sleep(random.uniform(0.05, 0.13))
            self.tab.actions.key_up(key)
        else:
            self.tab.actions.key_down(key).key_up(key)
        
        self._smart_delay(0.1, 0.2)
    
    def _execute_keypress_combo(self, key: Any):
        """执行按键动作，支持组合键。"""
        if self._check_cancelled():
            return

        keys = self._parse_key_combo(key)
        if not keys:
            return

        if self.stealth_mode:
            for item in keys:
                self.tab.actions.key_down(item)
                time.sleep(random.uniform(0.03, 0.09))
            time.sleep(random.uniform(0.05, 0.13))
            for item in reversed(keys):
                self.tab.actions.key_up(item)
                time.sleep(random.uniform(0.02, 0.08))
        else:
            for item in keys:
                self.tab.actions.key_down(item)
            for item in reversed(keys):
                self.tab.actions.key_up(item)

        self._smart_delay(0.1, 0.2)

    def _execute_javascript(self, code: Any):
        """在当前页面执行 JavaScript。"""
        if self._check_cancelled():
            return

        script = str(code or "").strip()
        if not script:
            raise WorkflowError("js_exec_empty")

        result = self.tab.run_js(script)
        logger.debug(f"[JS_EXEC] 执行完成: {str(result)[:120]}")

    def _combo_contains_submit_key(self, key: Any) -> bool:
        return any(item == "Enter" for item in self._parse_key_combo(key))

    def _parse_key_combo(self, key: Any) -> list[str]:
        raw = str(key or "").strip()
        if not raw:
            return []

        parts = [part.strip() for part in raw.split("+") if part.strip()]
        normalized_parts = [self._normalize_key_name(part) for part in parts]
        return [part for part in normalized_parts if part]

    def _normalize_key_name(self, key: str) -> str:
        normalized = str(key or "").strip()
        if not normalized:
            return ""

        key_map = {
            "ctrl": "Ctrl",
            "control": "Ctrl",
            "cmd": "Meta",
            "command": "Meta",
            "meta": "Meta",
            "win": "Meta",
            "windows": "Meta",
            "shift": "Shift",
            "alt": "Alt",
            "option": "Alt",
            "enter": "Enter",
            "return": "Enter",
            "esc": "Escape",
            "escape": "Escape",
            "tab": "Tab",
            "space": "Space",
            "spacebar": "Space",
            "backspace": "Backspace",
            "delete": "Delete",
            "del": "Delete",
            "insert": "Insert",
            "home": "Home",
            "end": "End",
            "pageup": "PageUp",
            "pagedown": "PageDown",
            "up": "ArrowUp",
            "down": "ArrowDown",
            "left": "ArrowLeft",
            "right": "ArrowRight",
            "arrowup": "ArrowUp",
            "arrowdown": "ArrowDown",
            "arrowleft": "ArrowLeft",
            "arrowright": "ArrowRight",
        }

        lower_name = normalized.lower()
        if lower_name in key_map:
            return key_map[lower_name]

        if len(normalized) == 1:
            return normalized.upper()

        if lower_name.startswith("f") and lower_name[1:].isdigit():
            return lower_name.upper()

        return normalized

    def _execute_click(self, selector: str, target_key: str, optional: bool):
        """执行点击操作（v5.7 隐身模式人类化点击）"""
        if self._check_cancelled():
            return
        
        ele = self.finder.find_with_fallback(selector, target_key)
        
        if ele:
            try:
                if self.stealth_mode:
                    # 发送按钮前额外犹豫（50% 概率，带微漂移）
                    if target_key == "send_btn" and random.random() < 0.5:
                        hesitate = random.uniform(0.5, 1.2)
                        logger.debug(f"[STEALTH] 发送前犹豫 {hesitate:.2f}s")
                        self._idle_wait(hesitate)
                    
                    # 🆕 人类化点击（平滑移动 + CDP mousedown/mouseup 带间隔）
                    self._stealth_click_element(ele)
                else:
                    if self._check_cancelled():
                        return
                    ele.click()
                
                self._smart_delay(
                    BrowserConstants.ACTION_DELAY_MIN,
                    BrowserConstants.ACTION_DELAY_MAX
                )
            
            except Exception as click_err:
                logger.debug(f"点击异常: {click_err}")
                if target_key == "send_btn":
                    logger.warning(f"[CLICK] 发送按钮点击失败，降级到 Enter 键: {click_err}")
                    self._execute_keypress("Enter")
                elif self.stealth_mode:
                    # 隐身模式下非发送按钮点击失败，向上抛出（不偷偷用 ele.click）
                    raise
        
        elif target_key == "send_btn":
            self._execute_keypress("Enter")
        
        elif not optional:
            raise ElementNotFoundError(f"点击目标未找到: {selector}")

    def _execute_coord_click(self, value: Any, optional: bool):
        """执行坐标点击动作。"""
        if self._check_cancelled():
            return

        if not isinstance(value, dict):
            if optional:
                logger.warning("[COORD_CLICK] 缺少坐标配置，已跳过")
                return
            raise WorkflowError("coord_click_missing_value")

        try:
            x = int(value.get("x"))
            y = int(value.get("y"))
        except Exception:
            if optional:
                logger.warning(f"[COORD_CLICK] 坐标无效，已跳过: {value}")
                return
            raise WorkflowError("coord_click_invalid_position")

        radius = max(0, int(value.get("random_radius", 0) or 0))
        click_x = x + random.randint(-radius, radius) if radius > 0 else x
        click_y = y + random.randint(-radius, radius) if radius > 0 else y

        try:
            self._human_cdp_click_at(click_x, click_y)
            self._smart_delay(
                BrowserConstants.ACTION_DELAY_MIN,
                BrowserConstants.ACTION_DELAY_MAX
            )
        except Exception:
            if optional:
                logger.warning(f"[COORD_CLICK] 点击失败，已跳过: ({click_x}, {click_y})")
                return
            raise

    def _ensure_mouse_origin(self) -> tuple:
        """
        确保存在一个页面内鼠标起点。

        只使用 CDP mouseMoved 建立当前位置，不走 tab.actions / ele.click。
        """
        if self._mouse_pos is not None:
            return self._mouse_pos

        from app.utils.human_mouse import _dispatch_mouse_move

        vw, vh = self._get_viewport_size()
        origin_x = random.randint(max(40, int(vw * 0.18)), max(60, int(vw * 0.42)))
        origin_y = random.randint(max(40, int(vh * 0.16)), max(60, int(vh * 0.45)))

        _dispatch_mouse_move(self.tab, origin_x, origin_y)
        self._mouse_pos = (origin_x, origin_y)
        time.sleep(random.uniform(0.03, 0.10))
        return self._mouse_pos

    def _flash_click_marker(self, x: int, y: int):
        """在页面上短暂标记实际点击坐标，便于排查坐标系问题。"""
        try:
            self.tab.run_js(
                """
                const x = arguments[0];
                const y = arguments[1];
                const id = '__coord_click_debug_marker__';
                document.getElementById(id)?.remove();
                const dot = document.createElement('div');
                dot.id = id;
                Object.assign(dot.style, {
                    position: 'fixed',
                    left: `${x - 6}px`,
                    top: `${y - 6}px`,
                    width: '12px',
                    height: '12px',
                    borderRadius: '9999px',
                    background: 'rgba(255, 59, 48, 0.95)',
                    border: '2px solid #fff',
                    boxShadow: '0 0 0 2px rgba(255, 59, 48, 0.35)',
                    zIndex: '2147483647',
                    pointerEvents: 'none'
                });
                document.body.appendChild(dot);
                setTimeout(() => dot.remove(), 900);
                """,
                x,
                y
            )
        except Exception:
            pass

    def _human_cdp_click_at(self, x: int, y: int):
        """
        使用 human_mouse 轨迹移动，并以 CDP 精确点击结束。

        链路固定为：
        页面内某处起点 -> smooth_move_mouse -> 短暂停顿/微漂移 -> cdp_precise_click
        """
        if self._check_cancelled():
            return

        self._flash_click_marker(x, y)
        logger.debug(f"[COORD_CLICK] viewport click at ({x}, {y})")

        start_pos = self._ensure_mouse_origin()

        self._mouse_pos = smooth_move_mouse(
            tab=self.tab,
            from_pos=start_pos,
            to_pos=(x, y),
            check_cancelled=self._check_cancelled
        )

        if self._check_cancelled():
            return

        if random.random() < 0.65:
            self._mouse_pos = idle_drift(
                tab=self.tab,
                duration=random.uniform(0.04, 0.12),
                center_pos=self._mouse_pos,
                check_cancelled=self._check_cancelled,
                drift_radius=random.uniform(1.2, 2.8),
                freq_hz=random.uniform(6.0, 10.0)
            )
        else:
            time.sleep(random.uniform(0.04, 0.10))

        if self._check_cancelled():
            return

        success = cdp_precise_click(
            tab=self.tab,
            x=x,
            y=y,
            check_cancelled=self._check_cancelled
        )
        if not success:
            logger.warning(f"[CDP_CLICK] 首次坐标点击失败，重试一次: ({x}, {y})")
            time.sleep(random.uniform(0.08, 0.18))
            success = cdp_precise_click(
                tab=self.tab,
                x=x,
                y=y,
                check_cancelled=self._check_cancelled
            )

        if not success:
            raise WorkflowError("coord_click_failed")

        self._mouse_pos = (x, y)
    
    def _stealth_click_element(self, ele):
        """
        隐身模式人类化点击（v5.9 — 彻底消灭 ele.click() 降级路径）
        
        关键：
        - 所有路径均使用 cdp_precise_click（force=0.5），绝不降级到 ele.click()
        - 坐标获取失败时，尝试 JS 获取 getBoundingClientRect 作为最后手段
        - 若坐标完全无法获取，抛出异常由上层处理（而非偷偷用 ele.click() 触发 CF）
        """
        if self._check_cancelled():
            return
        
        # 1. 获取元素坐标（多重尝试）
        target = self._get_element_viewport_pos(ele)
        
        if target is None:
            # 最后手段：通过 JS 获取坐标（仅在原生属性全部失败时）
            try:
                rect = ele.run_js(
                    "const r = this.getBoundingClientRect();"
                    "return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)}"
                )
                if rect and rect.get('x') and rect.get('y'):
                    target = (int(rect['x']), int(rect['y']))
                    logger.debug(f"[STEALTH] 原生属性获取坐标失败，JS getBoundingClientRect 获取: {target}")
            except Exception as e:
                logger.debug(f"[STEALTH] JS 坐标获取也失败: {e}")
        
        if target is None:
            # 🔴 绝不降级到 ele.click()，抛出异常
            raise Exception("[STEALTH] 无法获取元素坐标，拒绝使用 ele.click()（会触发 CF）")
        
        # 随机偏移（不精确命中中心）
        click_x = target[0] + random.randint(-6, 6)
        click_y = target[1] + random.randint(-4, 4)
        
        # 2. 平滑移动鼠标到目标
        if self._mouse_pos is not None:
            self._mouse_pos = smooth_move_mouse(
                tab=self.tab,
                from_pos=self._mouse_pos,
                to_pos=(click_x, click_y),
                check_cancelled=self._check_cancelled
            )
        else:
            from app.utils.human_mouse import _dispatch_mouse_move
            _dispatch_mouse_move(self.tab, click_x, click_y)
            self._mouse_pos = (click_x, click_y)
        
        if self._check_cancelled():
            return
        
        # 3. 短暂停顿（模拟"确认要点击"）
        time.sleep(random.uniform(0.05, 0.15))
        
        # 4. 精确 CDP 点击（含 force=0.5 修复）
        success = cdp_precise_click(
            tab=self.tab,
            x=click_x,
            y=click_y,
            check_cancelled=self._check_cancelled
        )
        
        if not success:
            # 🔴 CDP 点击失败也不降级到 ele.click()，而是重试一次
            logger.warning("[STEALTH] CDP 精确点击失败，重试一次...")
            time.sleep(random.uniform(0.1, 0.3))
            success = cdp_precise_click(
                tab=self.tab,
                x=click_x,
                y=click_y,
                check_cancelled=self._check_cancelled
            )
            if not success:
                raise Exception("[STEALTH] CDP 精确点击两次均失败，拒绝降级到 ele.click()")
        
        # 更新鼠标位置
        self._mouse_pos = (click_x, click_y)
        
        logger.debug(f"[STEALTH] 人类化点击完成: ({click_x}, {click_y})")
    
    # ================= 可靠发送 =================

    def _probe_attachment_readiness(self, send_selector: str = "") -> Dict[str, Any]:
        """Inspect whether attachments are still uploading and whether send looks available."""
        if self._attachment_monitor is not None:
            try:
                state = self._attachment_monitor.snapshot()
                if not isinstance(state, dict):
                    state = {}
                state = dict(state)
                state["ready"] = AttachmentMonitor._is_ready_state(
                    state,
                    require_send_enabled=True,
                )
                return state
            except Exception as e:
                logger.debug(f"[SEND] 附件状态探测失败: {e}")
                return {
                    "ok": False,
                    "attachmentCount": 0,
                    "pendingCount": 0,
                    "pendingText": False,
                    "sendFound": False,
                    "sendDisabled": False,
                    "sendBusy": False,
                    "ready": True,
                }
        selector_json = json.dumps((send_selector or "").strip(), ensure_ascii=False)
        js = f"""
        return (function() {{
            try {{
                const sendSelector = {selector_json};
                const root = document.querySelector(
                    '.message-input-wrapper, .message-input-container, .chat-layout-input-container, '
                    + '#dropzone-container, form:has(button[type="submit"]), '
                    + '[class*="message-input"], [class*="input-container"], [class*="input-wrapper"]'
                );
                if (!root) {{
                    return {{
                        ok: true,
                        attachmentCount: 0,
                        pendingCount: 0,
                        pendingText: false,
                        sendFound: false,
                        sendDisabled: false,
                        ready: true,
                        skipped: 'no_input_root'
                    }};
                }}

                const attachmentSelectors = [
                    '.file-card-list',
                    '.fileitem-btn',
                    '.fileitem-file-name',
                    '.fileitem-file-name-text',
                    '.message-input-column-file',
                    '[class*="fileitem"]',
                    '[class*="image-preview"]',
                    '[data-testid*="attachment"]',
                    '[data-testid*="preview"]',
                    'img[src^="blob:"]',
                    'img[src^="data:image"]'
                ].join(',');

                const pendingSelectors = [
                    'progress',
                    '[role="progressbar"]',
                    '[aria-busy="true"]',
                    '[class*="uploading"]',
                    '[class*="pending"]'
                ].join(',');

                const attachmentCount = root.querySelectorAll(attachmentSelectors).length;
                const pendingCount = root.querySelectorAll(pendingSelectors).length;
                const rootText = String(root.innerText || '').toLowerCase();
                const pendingText = /上传中|处理中|loading|uploading|processing|preparing/.test(rootText);

                let sendBtn = null;
                if (sendSelector) {{
                    try {{
                        sendBtn = document.querySelector(sendSelector);
                    }} catch (e) {{}}
                }}

                const sendDisabled = !!sendBtn && (
                    !!sendBtn.disabled
                    || sendBtn.getAttribute('aria-disabled') === 'true'
                    || /disabled|loading|uploading|sending/.test(String(sendBtn.className || '').toLowerCase())
                );

                return {{
                    ok: true,
                    attachmentCount,
                    pendingCount,
                    pendingText,
                    sendFound: !!sendBtn,
                    sendDisabled,
                    ready: pendingCount === 0 && !pendingText && (!sendBtn || !sendDisabled)
                }};
            }} catch (error) {{
                return {{
                    ok: false,
                    attachmentCount: 0,
                    pendingCount: 0,
                    pendingText: false,
                    sendFound: false,
                    sendDisabled: false,
                    ready: true,
                    error: String(error && error.message ? error.message : error)
                }};
            }}
        }})();
        """

        try:
            return self.tab.run_js(js) or {}
        except Exception as e:
            logger.debug(f"[SEND] 附件状态探测失败: {e}")
            return {
                "ok": False,
                "attachmentCount": 0,
                "pendingCount": 0,
                "pendingText": False,
                "sendFound": False,
                "sendDisabled": False,
                "ready": True,
            }

    def _recent_attachment_age_seconds(self) -> Optional[float]:
        """Seconds since the newest attachment upload completed, if known."""
        timestamps = []

        for handler, attr in (
            (getattr(self, "_text_handler", None), "_recent_file_upload_at"),
            (getattr(self, "_image_handler", None), "_recent_image_upload_at"),
        ):
            try:
                ts = float(getattr(handler, attr, 0.0) or 0.0)
            except Exception:
                ts = 0.0
            if ts > 0:
                timestamps.append(ts)

        if not timestamps:
            return None
        return max(0.0, time.time() - max(timestamps))

    def _wait_for_attachments_ready_before_send(self, send_selector: str = ""):
        """Wait for file/image uploads to settle before attempting submit."""
        if not self._should_wait_for_attachments_before_send():
            return

        if self._attachment_monitor is not None:
            max_wait = getattr(BrowserConstants, "ATTACHMENT_READY_MAX_WAIT", 20.0)
            check_interval = getattr(BrowserConstants, "ATTACHMENT_READY_CHECK_INTERVAL", 0.35)
            stable_window = getattr(BrowserConstants, "ATTACHMENT_READY_STABLE_WINDOW", 0.8)
            result = self._attachment_monitor.wait_until_ready(
                require_observed=False,
                require_send_enabled=True,
                accept_existing=True,
                max_wait=max_wait,
                poll_interval=check_interval,
                stable_window=stable_window,
                label="send-gate",
            )
            if result.get("success"):
                return

            logger.warning(
                "[SEND] Attachment readiness was not confirmed before submit; continuing once "
                f"({AttachmentMonitor.summarize(result)})"
            )
            return

        max_wait = getattr(BrowserConstants, "ATTACHMENT_READY_MAX_WAIT", 20.0)
        check_interval = getattr(BrowserConstants, "ATTACHMENT_READY_CHECK_INTERVAL", 0.35)
        settle_floor = getattr(BrowserConstants, "ATTACHMENT_POST_UPLOAD_SETTLE", 1.8)
        try:
            settle_floor = max(
                settle_floor,
                self._text_handler.get_post_upload_settle_seconds(settle_floor)
            )
        except Exception:
            pass

        upload_age = self._recent_attachment_age_seconds()
        if upload_age is not None and upload_age < settle_floor:
            remaining = settle_floor - upload_age
            logger.debug(f"[SEND] 附件刚上传完成，额外等待解析稳定 {remaining:.1f}s")
            elapsed = 0.0
            while elapsed < remaining:
                if self._check_cancelled():
                    return
                step = min(check_interval, remaining - elapsed)
                time.sleep(step)
                elapsed += step

        state = self._probe_attachment_readiness(send_selector)
        if state.get("ready", True):
            return

        logger.debug(
            "[SEND] 检测到附件仍在处理，发送前等待 "
            f"(attachments={state.get('attachmentCount', 0)}, "
            f"pending={state.get('pendingCount', 0)}, "
            f"send_disabled={state.get('sendDisabled', False)})"
        )

        elapsed = 0.0
        while elapsed < max_wait:
            if self._check_cancelled():
                return

            sleep_for = min(check_interval, max_wait - elapsed)
            time.sleep(sleep_for)
            elapsed += sleep_for

            state = self._probe_attachment_readiness(send_selector)
            if state.get("ready", True):
                logger.debug(
                    "[SEND] 附件已就绪，继续发送 "
                    f"(waited={elapsed:.1f}s, attachments={state.get('attachmentCount', 0)})"
                )
                return

        logger.warning(
            "[SEND] 等待附件就绪超时，继续尝试发送 "
            f"(attachments={state.get('attachmentCount', 0)}, "
            f"pending={state.get('pendingCount', 0)}, "
            f"send_disabled={state.get('sendDisabled', False)})"
        )

    def _should_wait_for_attachments_before_send(self) -> bool:
        """Only wait when this request actually attached files or images."""
        try:
            if self._text_handler.has_recent_attachment_upload():
                return True
        except Exception:
            pass

        try:
            if self._image_handler.has_recent_attachment_upload():
                return True
        except Exception:
            pass

        context = getattr(self, "_context", None) or {}
        return bool(context.get("images"))

    def _has_recent_attachment_upload(self) -> bool:
        """Whether the current turn recently attached files/images before sending."""
        try:
            if self._text_handler.has_recent_attachment_upload():
                return True
        except Exception:
            pass

        try:
            if self._image_handler.has_recent_attachment_upload():
                return True
        except Exception:
            pass

        context = getattr(self, "_context", None) or {}
        return bool(context.get("images"))

    def _get_send_confirmation_config(self) -> Dict[str, Any]:
        """Return the merged send confirmation strategy for the current site."""
        config = {
            "post_click_observe_window": float(
                getattr(BrowserConstants, "SEND_POST_CLICK_OBSERVE_WINDOW", 1.8)
            ),
            "pre_retry_probe_window": 0.12,
            "retry_observe_window": float(
                getattr(BrowserConstants, "SEND_RETRY_OBSERVE_WINDOW", 0.9)
            ),
            "attachment_observe_window": float(
                getattr(BrowserConstants, "ATTACHMENT_SEND_OBSERVE_WINDOW", 6.0)
            ),
            "trust_network_activity": True,
            "trust_generating_indicator": True,
            "trust_send_disabled_with_input_shrink": True,
        }

        raw_config = {}
        if isinstance(self._stream_config, dict):
            raw_config = self._stream_config.get("send_confirmation", {}) or {}

        if isinstance(raw_config, dict):
            config.update(raw_config)

        return config

    def _get_send_confirmation_window(
        self,
        key: str,
        fallback: float,
        *,
        min_value: float = 0.0,
        max_value: Optional[float] = None,
    ) -> float:
        """Read a numeric send confirmation option with clamping."""
        config = self._get_send_confirmation_config()
        try:
            value = float(config.get(key, fallback))
        except (TypeError, ValueError):
            value = float(fallback)

        value = max(min_value, value)
        if max_value is not None:
            value = min(value, max_value)
        return value

    def _get_send_confirmation_flag(self, key: str, fallback: bool = True) -> bool:
        """Read a boolean send confirmation option."""
        config = self._get_send_confirmation_config()
        raw_value = config.get(key, fallback)

        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            lowered = raw_value.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return True
            if lowered in ("0", "false", "no", "off"):
                return False
        return bool(raw_value)

    def _probe_send_post_click_state(self, send_selector: str = "") -> Dict[str, Any]:
        """Passively inspect whether the page has transitioned into generating state."""
        selector_json = json.dumps((send_selector or "").strip(), ensure_ascii=False)
        js = f"""
        return (function() {{
            try {{
                const sendSelector = {selector_json};
                const indicators = [
                    'button[aria-label*="Stop"]',
                    'button[aria-label*="stop"]',
                    'button[aria-label*="停止"]',
                    '[data-state="streaming"]',
                    '.stop-generating'
                ];

                function lowered(value) {{
                    return String(value || '').toLowerCase();
                }}

                function isVisible(node) {{
                    if (!node) return false;
                    const style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                    if (style && (style.display === 'none' || style.visibility === 'hidden')) {{
                        return false;
                    }}
                    const rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                    return !rect || (rect.width > 0 && rect.height > 0);
                }}

                let sendBtn = null;
                if (sendSelector) {{
                    try {{
                        sendBtn = document.querySelector(sendSelector);
                    }} catch (e) {{}}
                }}

                const sendMeta = sendBtn ? [
                    sendBtn.getAttribute('aria-label'),
                    sendBtn.getAttribute('title'),
                    sendBtn.getAttribute('data-testid'),
                    sendBtn.className,
                    sendBtn.innerText,
                    sendBtn.textContent
                ].map(lowered).join(' ') : '';

                const generatingIndicator = indicators.some(selector => {{
                    try {{
                        const node = document.querySelector(selector);
                        return isVisible(node);
                    }} catch (e) {{
                        return false;
                    }}
                }});

                const sendLooksLikeStop = !!sendMeta && (
                    /\\bstop\\b|\\bstopping\\b|\\bcancel\\b|\\babort\\b/.test(sendMeta)
                    || /停止|中止|取消/.test(sendMeta)
                );

                const sendDisabled = !!sendBtn && (
                    !!sendBtn.disabled
                    || sendBtn.getAttribute('aria-disabled') === 'true'
                    || /disabled|loading|uploading|sending/.test(sendMeta)
                );

                return {{
                    ok: true,
                    sendFound: !!sendBtn,
                    sendDisabled,
                    sendLooksLikeStop,
                    generating: generatingIndicator || sendLooksLikeStop
                }};
            }} catch (error) {{
                return {{
                    ok: false,
                    sendFound: false,
                    sendDisabled: false,
                    sendLooksLikeStop: false,
                    generating: false,
                    error: String(error && error.message ? error.message : error)
                }};
            }}
        }})();
        """

        try:
            return self.tab.run_js(js) or {}
        except Exception as e:
            logger.debug(f"[SEND] 发送后状态探测失败: {e}")
            return {
                "ok": False,
                "sendFound": False,
                "sendDisabled": False,
                "sendLooksLikeStop": False,
                "generating": False,
            }

    def _observe_send_without_retry(
        self,
        send_selector: str,
        before_len: int,
        *,
        max_wait: Optional[float] = None,
    ) -> bool:
        """Observe post-click send signals without issuing another click."""
        observe_window = self._get_send_confirmation_window(
            "attachment_observe_window",
            getattr(BrowserConstants, "ATTACHMENT_SEND_OBSERVE_WINDOW", 6.0),
            min_value=0.0,
            max_value=60.0,
        ) if max_wait is None else float(max_wait)
        trust_network_activity = self._get_send_confirmation_flag(
            "trust_network_activity",
            True,
        )
        trust_generating_indicator = self._get_send_confirmation_flag(
            "trust_generating_indicator",
            True,
        )
        trust_send_disabled_with_input_shrink = self._get_send_confirmation_flag(
            "trust_send_disabled_with_input_shrink",
            True,
        )
        if observe_window <= 0:
            return False
        poll_interval = 0.25
        elapsed = 0.0
        last_len = before_len

        while elapsed < observe_window:
            if self._check_cancelled():
                return True

            step = min(poll_interval, observe_window - elapsed)
            network_state = {"matched": False}
            if self._network_monitor is not None:
                try:
                    network_state = self._network_monitor.poll_send_activity(timeout=step) or {"matched": False}
                except Exception as e:
                    logger.debug_throttled(
                        "send.network_pre_read_failed",
                        f"[SEND] 网络活动预读失败: {e}",
                        interval_sec=5.0,
                    )
                    time.sleep(step)
            else:
                time.sleep(step)
            elapsed += step

            if trust_network_activity and network_state.get("matched"):
                logger.debug("[SEND] 已通过网络监听捕获到发送后的目标流事件")
                return True

            current_len = self._safe_get_input_len_by_key("input_box")
            if self._is_send_success(before_len, current_len) or self._is_send_success(last_len, current_len):
                return True

            state = self._probe_send_post_click_state(send_selector)
            if trust_generating_indicator and state.get("generating"):
                return True

            if (
                trust_send_disabled_with_input_shrink
                and state.get("sendDisabled")
                and current_len < before_len
            ):
                return True

            last_len = current_len

        return False

    def _execute_click_send_reliably(self, selector: str, target_key: str, optional: bool):
        """
        可靠发送（v5.6 隐身模式增强版）

        - 隐身模式：零 JS 注入，盲等待+重试
        - 普通模式：保持 JS 检查逻辑
        """
        if self._check_cancelled():
            return

        # ===== 隐身模式：无 JS 注入路径 =====
        if self.stealth_mode:
            self._execute_click_send_stealth(selector, target_key, optional)
            return

        # ===== 普通模式：原有逻辑 =====
        max_wait = getattr(BrowserConstants, "IMAGE_SEND_MAX_WAIT", 12.0)
        retry_interval = getattr(BrowserConstants, "IMAGE_SEND_RETRY_INTERVAL", 0.6)
        avoid_repeat_click = self._has_recent_attachment_upload()
        send_observe_window = self._get_send_confirmation_window(
            "post_click_observe_window",
            getattr(BrowserConstants, "SEND_POST_CLICK_OBSERVE_WINDOW", 1.8),
            min_value=0.0,
            max_value=max_wait,
        )
        retry_probe_window = self._get_send_confirmation_window(
            "pre_retry_probe_window",
            0.12,
            min_value=0.0,
            max_value=max_wait,
        )
        retry_observe_window = self._get_send_confirmation_window(
            "retry_observe_window",
            getattr(BrowserConstants, "SEND_RETRY_OBSERVE_WINDOW", 0.9),
            min_value=0.0,
            max_value=max_wait,
        )
        attachment_observe_window = self._get_send_confirmation_window(
            "attachment_observe_window",
            getattr(BrowserConstants, "ATTACHMENT_SEND_OBSERVE_WINDOW", 6.0),
            min_value=0.0,
            max_value=max_wait,
        )

        before_len = self._safe_get_input_len_by_key("input_box")
        self._execute_click(selector, target_key, optional)

        time.sleep(0.25)
        after_len = self._safe_get_input_len_by_key("input_box")

        if self._is_send_success(before_len, after_len):
            logger.info("发送成功")
            return

        if avoid_repeat_click:
            if self._observe_send_without_retry(
                selector,
                before_len,
                max_wait=attachment_observe_window,
            ):
                logger.info("发送成功（附件场景，已避免重复点击发送按钮）")
            else:
                logger.warning("[SEND] 附件发送场景未观察到明确成功信号，跳过自动补点以避免误点停止按钮")
            return

        if self._observe_send_without_retry(selector, before_len, max_wait=send_observe_window):
            logger.info("发送成功（首次点击后观察确认）")
            return

        logger.warning(f"[SEND] 发送未成功，进入重试窗口 max_wait={max_wait}s")

        deadline = time.time() + max_wait
        while time.time() < deadline:
            if self._check_cancelled():
                return

            remaining = max(0.0, deadline - time.time())
            step = min(retry_interval, remaining)
            if step <= 0:
                break
            time.sleep(step)

            remaining = max(0.0, deadline - time.time())
            if remaining > 0 and self._observe_send_without_retry(
                selector,
                before_len,
                max_wait=min(retry_probe_window, remaining),
            ):
                elapsed = max_wait - max(0.0, deadline - time.time())
                logger.info(f"发送成功（重试前观察确认，elapsed={elapsed:.1f}s）")
                return

            self._execute_click(selector, target_key, optional)

            if time.time() < deadline:
                time.sleep(min(0.25, max(0.0, deadline - time.time())))
            new_len = self._safe_get_input_len_by_key("input_box")

            if self._is_send_success(after_len, new_len) or self._is_send_success(before_len, new_len):
                elapsed = max_wait - max(0.0, deadline - time.time())
                logger.info(f"发送成功 (重试{elapsed:.1f}s)")
                return

            remaining = max(0.0, deadline - time.time())
            if remaining > 0 and self._observe_send_without_retry(
                selector,
                before_len,
                max_wait=min(retry_observe_window, remaining),
            ):
                elapsed = max_wait - max(0.0, deadline - time.time())
                logger.info(f"发送成功（重试后观察确认，elapsed={elapsed:.1f}s）")
                return

            after_len = new_len

        logger.error("[SEND] 发送重试超时")
        if not optional:
            raise WorkflowError("send_btn_click_failed_due_to_uploading")

    def _execute_click_send_stealth(self, selector: str, target_key: str, optional: bool):
        """
        隐身模式发送（零 JS 注入）
        
        - 无图片：直接点击
        - 有图片：盲等待+重试
        """
        has_images = False
        if hasattr(self, '_context') and self._context:
            has_images = bool(self._context.get('images'))
        
        if not has_images:
            self._execute_click(selector, target_key, optional)
            logger.info("[STEALTH] 发送完成（无图片）")
            return
        
        max_wait = getattr(BrowserConstants, 'STEALTH_SEND_IMAGE_WAIT', 8.0)
        retry_interval = getattr(BrowserConstants, 'STEALTH_SEND_IMAGE_RETRY_INTERVAL', 1.5)
        
        logger.info(f"[STEALTH] 有图片，发送后等待上传 (max_wait={max_wait}s)")
        
        self._execute_click(selector, target_key, optional)
        
        elapsed = 0.0
        retry_count = 0
        while elapsed < max_wait:
            if self._check_cancelled():
                return
            
            wait_step = min(retry_interval, max_wait - elapsed)
            wait_step = wait_step * random.uniform(0.8, 1.2)
            time.sleep(wait_step)
            elapsed += wait_step
            
            retry_count += 1
            try:
                self._execute_click(selector, target_key, True)
                if retry_count <= 3 or retry_count % 3 == 0 or elapsed >= max_wait:
                    logger.debug(
                        f"[STEALTH] 发送重试 #{retry_count} (elapsed={elapsed:.1f}s)"
                    )
            except Exception:
                pass
        
        logger.info(f"[STEALTH] 发送完成（图片模式，重试 {retry_count} 次）")
    
    def _safe_get_input_len_by_key(self, target_key: str) -> int:
        """读取输入框当前长度"""
        try:
            candidates = []

            if target_key and target_key == getattr(self, "_last_input_target_key", ""):
                last_ele = getattr(self, "_last_input_element", None)
                if last_ele:
                    candidates.append(last_ele)

            selector = ""
            if isinstance(self._selectors, dict):
                selector = str(self._selectors.get(target_key, "") or "").strip()

            if selector or target_key:
                try:
                    ele = self.finder.find_with_fallback(selector, target_key, timeout=0.2)
                except Exception:
                    ele = None
                if ele:
                    candidates.append(ele)

            try:
                active_ele = self.tab.run_js("return document.activeElement")
            except Exception:
                active_ele = None
            if active_ele:
                candidates.append(active_ele)

            for ele in candidates:
                try:
                    n = self.tab.run_js("""
                        try {
                            const el = arguments[0];
                            const tag = (el.tagName || '').toLowerCase();
                            if (tag === 'textarea' || tag === 'input') return (el.value || '').length;
                            if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') return (el.innerText || '').length;
                            return (el.textContent || '').length;
                        } catch(e){ return 0; }
                    """, ele)
                except Exception:
                    continue
                if n is not None:
                    return int(n)

            return 0
        except Exception:
            return 0
    
    def _is_send_success(self, before_len: int, after_len: int) -> bool:
        """判断是否发送成功"""
        try:
            if after_len == 0 and before_len > 0:
                return True
            if before_len <= 0:
                return False
            if after_len <= int(before_len * 0.4):
                return True
            return False
        except Exception:
            return False
            # ================= 隐身模式页面预热 =================
    
    def _warmup_page_for_stealth(self):
        """
        页面预热（v5.8 — 简化版，降低行为指纹风险）
        
        改进：
        - 修复死代码（_dispatch_mouse_move = None 覆盖导入）
        - 减少随机扫视次数（1-2 次，真实用户打开熟悉页面不会大量扫视）
        - 移除随机滚动（在已登录的对话页面滚动不自然）
        - 保留微漂移（等待期间的手部抖动仍有价值）
        """
        logger.debug("[STEALTH] 执行页面预热")
        
        try:
            from app.utils.human_mouse import _dispatch_mouse_move
            
            vw, vh = self._get_viewport_size()
            
            # 初始化鼠标位置（视口中上部，模拟"刚把鼠标放到页面"）
            init_x = vw // 2 + random.randint(-80, 80)
            init_y = int(vh * 0.3) + random.randint(-40, 40)
            self._mouse_pos = (init_x, init_y)
            _dispatch_mouse_move(self.tab, init_x, init_y)
            
            # 短暂停顿（模拟"看到页面内容"）
            self._idle_wait(random.uniform(0.4, 0.9))
            
            if self._check_cancelled():
                return
            
            # 1-2 次轻微移动（模拟目光扫过，不是大幅扫视）
            move_count = random.randint(1, 2)
            for i in range(move_count):
                if self._check_cancelled():
                    return
                
                # 小幅移动（不超过视口 30%）
                dx = random.randint(-int(vw * 0.15), int(vw * 0.15))
                dy = random.randint(-int(vh * 0.12), int(vh * 0.12))
                target_x = max(50, min(vw - 50, self._mouse_pos[0] + dx))
                target_y = max(50, min(vh - 50, self._mouse_pos[1] + dy))
                
                self._mouse_pos = smooth_move_mouse(
                    tab=self.tab,
                    from_pos=self._mouse_pos,
                    to_pos=(target_x, target_y),
                    check_cancelled=self._check_cancelled
                )
                
                # 微漂移停留
                self._idle_wait(random.uniform(0.3, 0.6))
            
            # 最后停顿
            self._idle_wait(random.uniform(0.3, 0.7))
            
            logger.debug(f"[STEALTH] 页面预热完成（{move_count} 次移动）")
            
        except Exception as e:
            logger.debug(f"[STEALTH] 页面预热异常（可忽略）: {e}")
    
    # ================= 输入框填充 =================
    
    def _execute_fill(self, selector: str, text: str, target_key: str, optional: bool):
        """填充输入框（v5.7 隐身增强版）"""
        if self._check_cancelled():
            return

        ele = self.finder.find_with_fallback(selector, target_key)
        if not ele:
            if not optional:
                raise ElementNotFoundError("找不到输入框")
            return

        self._last_input_element = ele
        self._last_input_target_key = target_key or ""

        # 🆕 隐身模式：人类化点击聚焦输入框 + 剪贴板粘贴
        if self.stealth_mode:
            self._stealth_click_element(ele)
            time.sleep(random.uniform(0.1, 0.25))
            self._text_handler.fill_via_clipboard_no_click(ele, text)
        else:
            self._text_handler.fill_via_js(ele, text)   
        
        # 粘贴图片
        if hasattr(self, '_context') and self._context:
            images = self._context.get('images', [])
            if images:
                self._image_handler.paste_images(images)
        
        # ===== 隐身模式：粘贴后模拟"人类阅读/检查"延迟（带微漂移）=====
        if self.stealth_mode and len(text) > 0:
            base_delay = random.uniform(1.0, 2.0)
            extra_per_chunk = len(text) / 5000.0
            extra_delay = extra_per_chunk * random.uniform(0.3, 0.6)
            total_review = min(base_delay + extra_delay, 3.0)
            
            logger.debug(f"[STEALTH] 粘贴后阅读延迟 {total_review:.1f}s (文本长度={len(text)})")
            
            # 🆕 等待期间保持微漂移（消灭"事件沙漠"）
            self._idle_wait(total_review)


__all__ = ['WorkflowExecutor']
