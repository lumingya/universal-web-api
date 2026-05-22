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
    SSEFormatter,
    ElementNotFoundError,
    WorkflowError,
)
from app.core.elements import ElementFinder
from app.core.parsers import ParserRegistry
from app.core.request_transport import (
    REQUEST_TRANSPORT_MODE_PAGE_FETCH,
    execute_request_transport,
    get_default_request_transport_config,
    normalize_request_transport_config,
)
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
from .executor_actions import WorkflowExecutorActionMixin
from .executor_interaction import WorkflowExecutorInteractionMixin
from .executor_request_transport import WorkflowExecutorRequestTransportMixin
from .executor_send import WorkflowExecutorSendMixin


class WorkflowExecutor(
    WorkflowExecutorRequestTransportMixin,
    WorkflowExecutorSendMixin,
    WorkflowExecutorInteractionMixin,
    WorkflowExecutorActionMixin,
):
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
                 site_advanced_config: Dict = None,
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
        self._file_paste_config = file_paste_config or {}
        self._site_advanced_config = site_advanced_config or {}
        self._selectors = selectors or {}
        
        # 🆕 初始化双 Monitor（优先网络，回退 DOM）
        self._network_monitor = None
        self._stream_monitor = None
        self._last_input_element = None
        self._last_input_target_key = ""
        self._last_stream_media_state = {}
        self._last_stream_media_items: list[Dict[str, Any]] = []
        self._request_transport = normalize_request_transport_config(
            (stream_config or {}).get("request_transport")
        )
        self._pending_request_transport_state: Optional[Dict[str, Any]] = None
        self._request_transport_bypass = False
        self._last_request_transport_sent = False
        self._last_send_attempt_state: Dict[str, Any] = {}
        self._input_stability_wait_pending = False
        self._last_fill_completed_at = 0.0
        self._last_fill_text_length = 0
        self._last_fill_after_new_chat = False
        self._workflow_scope_depth = 0
        self._workflow_focus_emulation_active = False
        
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
                    if not str(effective_network_config.get("stream_match_pattern") or "").strip():
                        effective_network_config["stream_match_pattern"] = (
                            network_listen_pattern or merged_pattern
                        )
                    if not str(effective_network_config.get("stream_match_mode") or "").strip():
                        effective_network_config["stream_match_mode"] = "keyword"
                    effective_stream_config["network"] = effective_network_config

                self._network_monitor = create_network_monitor(
                    tab=tab,
                    formatter=self.formatter,
                    stream_config=effective_stream_config,
                    stop_checker=should_stop_checker,
                    event_handler=self._handle_network_event
                )
            except Exception as e:
                logger.warning(f"[Executor] 网络监听器创建失败: {e}")

        # DOM 流式 + 网络异常拦截：启用 event-only 网络监听（独立于 stream_mode）
        elif interception_enabled:
            try:
                logger.debug(
                    "[Executor] DOM 模式下跳过 event-only 网络监听，避免与 DOM 轮询并发抢占同一 CDP 连接 "
                    f"(pattern={interception_pattern or 'http'!r})"
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
        self._attachment_monitor_config = self._build_attachment_monitor_config(
            stream_config=stream_config,
            file_paste_config=file_paste_config,
        )
        self._attachment_monitor = AttachmentMonitor(
            tab=tab,
            selectors=self._selectors,
            config=self._attachment_monitor_config,
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
            attachment_monitor_config=self._attachment_monitor_config,
        )
        
        self._image_handler = ImageInputHandler(
            tab=tab,
            stealth_mode=stealth_mode,
            smart_delay_fn=self._smart_delay,
            check_cancelled_fn=self._check_cancelled,
            attachment_monitor=self._attachment_monitor,
            focus_input_fn=self._focus_last_input_for_attachment_paste,
            selectors=self._selectors,
        )
        
        if self._image_config.get("enabled"):
            logger.debug(f"[IMAGE] 图片提取已启用")
        
        if self.stealth_mode:
            logger.debug("[STEALTH] 低熵模式已启用")

    @staticmethod
    def _coerce_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return True
            if lowered in ("0", "false", "no", "off"):
                return False
        return default

    @staticmethod
    def _coerce_float(value: Any, default: float, minimum: Optional[float] = None) -> float:
        try:
            result = float(value)
        except Exception:
            result = float(default)
        if minimum is not None:
            result = max(float(minimum), result)
        return result

    @staticmethod
    def _coerce_int(value: Any, default: int, minimum: Optional[int] = None) -> int:
        try:
            result = int(value)
        except Exception:
            result = int(default)
        if minimum is not None:
            result = max(int(minimum), result)
        return result

    @staticmethod
    def _normalize_attachment_rule_list(raw_value) -> list:
        if not isinstance(raw_value, list):
            return []
        cleaned = []
        for item in raw_value:
            value = str(item or "").strip()
            if value and value not in cleaned:
                cleaned.append(value)
        return cleaned

    def _build_attachment_monitor_config(
        self,
        *,
        stream_config: Optional[Dict[str, Any]] = None,
        file_paste_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        config = {}
        if isinstance(stream_config, dict):
            raw_config = stream_config.get("attachment_monitor", {}) or {}
            if isinstance(raw_config, dict):
                config.update(raw_config)
        if isinstance(file_paste_config, dict):
            raw_config = file_paste_config.get("attachment_monitor", {}) or {}
            if isinstance(raw_config, dict):
                config.update(raw_config)

        attachment_selectors = self._normalize_attachment_rule_list(
            config.get("attachment_selectors", [])
        )
        legacy_selectors = self._normalize_attachment_rule_list(
            (file_paste_config or {}).get("upload_signal_selectors", [])
        )
        for selector in legacy_selectors:
            if selector not in attachment_selectors:
                attachment_selectors.append(selector)
        if attachment_selectors:
            config["attachment_selectors"] = attachment_selectors

        return config

    def _get_file_paste_send_confirmation_config(self) -> Dict[str, Any]:
        if not isinstance(self._file_paste_config, dict):
            return {}
        raw_config = self._file_paste_config.get("send_confirmation", {}) or {}
        return raw_config if isinstance(raw_config, dict) else {}

    def _get_file_paste_state_probe_config(self) -> Dict[str, Any]:
        if not isinstance(self._file_paste_config, dict):
            return {}
        raw_config = self._file_paste_config.get("state_probe", {}) or {}
        return raw_config if isinstance(raw_config, dict) else {}

    def _get_attachment_monitor_flag(self, key: str, default: bool = False) -> bool:
        raw_value = {}
        if isinstance(self._attachment_monitor_config, dict):
            raw_value = self._attachment_monitor_config
        value = raw_value.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return True
            if lowered in ("0", "false", "no", "off"):
                return False
        return bool(value)

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

    def _focus_last_input_for_attachment_paste(self) -> bool:
        """Re-focus the active composer before Ctrl+V image paste."""
        ele = getattr(self, "_last_input_element", None)
        if ele is None:
            selector = str(self._selectors.get("input_box") or "").strip()
            if selector:
                try:
                    ele = self.finder.find_with_fallback(selector, "input_box")
                except Exception as e:
                    logger.debug(f"[IMAGE] 重新定位输入框失败: {e}")
                    ele = None
                if ele is not None:
                    self._last_input_element = ele
                    self._last_input_target_key = "input_box"

        if ele is None:
            logger.warning("[IMAGE] 粘贴前无法定位输入框")
            return False

        try:
            self._text_handler.focus_to_end(ele)
            return bool(self._text_handler.ensure_input_focus(ele, attempts=2, log_failure=False))
        except Exception as e:
            logger.debug(f"[IMAGE] 粘贴前聚焦输入框失败: {e}")
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
        with self._page_interaction_slot("JS_EXEC", "kimi_capture_prepare") as acquired:
            if not acquired or self._check_cancelled():
                return
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

        hard_timeout = float(
            self._stream_config.get("hard_timeout", 300) or 300
        )
        first_response_timeout = float(
            self._network_config.get("first_response_timeout", hard_timeout) or hard_timeout
        )
        response_interval = float(
            self._network_config.get("response_interval", 0.3) or 0.3
        )
        silence_threshold = float(
            self._network_config.get("silence_threshold", 3) or 3
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
    
    def execute_step(self, action: str, selector: str,
                     target_key: str, value: Any = None,
                     optional: bool = False,
                     context: Dict = None) -> Generator[str, None, None]:
        """执行单个步骤"""
        
        if self._check_cancelled():
            logger.debug(f"步骤 {action} 跳过（已取消）")
            return
        
        self._context = context
        if action in ("STREAM_WAIT", "STREAM_OUTPUT"):
            self._last_stream_media_state = {}
        
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
                    if self._network_monitor is not None:
                        self._prepare_kimi_page_capture()
                        self._network_monitor.pre_start()
                    if self._attempt_request_transport_send():
                        return
                    if self._has_pending_request_transport_prompt():
                        self._ensure_cached_prompt_filled()
                    self._wait_for_attachments_ready_before_send(
                        self._selectors.get("send_btn", "")
                    )
                    if self._network_monitor is not None:
                        self._prepare_kimi_page_capture()
                        self._network_monitor.pre_start()
                        self._network_monitor.mark_send_attempt()
                self._execute_keypress_combo(key)

            elif action == "JS_EXEC":
                self._execute_javascript(value)

            elif action == "READONLY_HINT":
                logger.debug(f"[WORKFLOW_HINT] {str(value or '')[:160]}")
                return

            elif action == "PAGE_FETCH":
                self._last_request_transport_sent = False
                if self._network_monitor is not None:
                    self._prepare_kimi_page_capture()
                    self._network_monitor.pre_start()
                if not self._has_pending_request_transport_prompt():
                    self._stage_request_transport_from_context(
                        selector=selector,
                        target_key=target_key,
                        optional=optional,
                        context=context,
                    )
                if self._attempt_request_transport_send():
                    return
                if self._has_pending_request_transport_prompt():
                    pending = self._pending_request_transport_state or {}
                    if pending.get("selector") or pending.get("target_key"):
                        self._ensure_cached_prompt_filled()
                    else:
                        self._request_transport_bypass = True
                        self._clear_request_transport_state()
                return
            
            elif action == "CLICK":
                # ===== 隐身模式：首次交互前执行人类行为预热 =====
                self._maybe_warmup_page_for_stealth(action, target_key)
                
                if target_key == "send_btn":
                    # 🆕 发送前启动网络监听（如果已配置）
                    if self._network_monitor is not None:
                        self._prepare_kimi_page_capture()
                        self._network_monitor.pre_start()
                    if self._attempt_request_transport_send():
                        return
                    if self._has_pending_request_transport_prompt():
                        self._ensure_cached_prompt_filled()
                    self._wait_for_attachments_ready_before_send(selector)
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
                self._maybe_warmup_page_for_stealth(action, target_key)

                self._execute_coord_click(value, optional)

            elif action == "COORD_SCROLL":
                self._maybe_warmup_page_for_stealth(action, target_key)

                self._execute_coord_scroll(value, optional)
            
            elif action == "FILL_INPUT":
                prompt = context.get("prompt", "") if context else ""
                if self._stage_request_transport_from_context(
                    selector=selector,
                    target_key=target_key,
                    optional=optional,
                    context=context,
                ):
                    return
                self._execute_fill(selector, prompt, target_key, optional)
            
            elif action in ("STREAM_WAIT", "STREAM_OUTPUT"):
                user_input = context.get("prompt", "") if context else ""
                self._last_stream_media_state = {}
                self._last_stream_media_items = []

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
                            self._last_stream_media_state = (
                                self._network_monitor.get_media_generation_state()
                                if self._network_monitor is not None
                                else {}
                            )
                            self._last_stream_media_items = (
                                self._network_monitor.get_stream_media_items()
                                if self._network_monitor is not None
                                else []
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
                        self._last_stream_media_state = {}
                        self._last_stream_media_items = []
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
                        self._last_stream_media_state = {}
                        self._last_stream_media_items = []
                        monitor_used = "dom_fallback"
                
                else:
                    yield from self._stream_monitor.monitor(
                        selector=selector,
                        user_input=user_input,
                        completion_id=self._completion_id
                    )
                    self._last_stream_media_state = {}
                    self._last_stream_media_items = []
                    monitor_used = "dom"
                
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
       
        with self._page_interaction_slot("KEY_PRESS", str(key or "")) as acquired:
            if not acquired or self._check_cancelled():
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

        with self._page_interaction_slot("KEY_PRESS", "+".join(keys)) as acquired:
            if not acquired or self._check_cancelled():
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

        with self._page_interaction_slot("JS_EXEC", "workflow_js") as acquired:
            if not acquired or self._check_cancelled():
                return
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
        normalized_parts = [part for part in normalized_parts if part]
        if not normalized_parts:
            return []

        modifiers = {part for part in normalized_parts if part in {"Ctrl", "Alt", "Meta", "Shift"}}
        if not modifiers:
            return normalized_parts

        allow_uppercase_letters = "Shift" in modifiers
        adjusted_parts = []
        for part in normalized_parts:
            if len(part) == 1 and part.isalpha():
                adjusted_parts.append(part.upper() if allow_uppercase_letters else part.lower())
            else:
                adjusted_parts.append(part)
        return adjusted_parts

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
            return normalized

        if lower_name.startswith("f") and lower_name[1:].isdigit():
            return lower_name.upper()

        return normalized



__all__ = ['WorkflowExecutor']
