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
from contextlib import contextmanager
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
from app.core.request_transport import (
    REQUEST_TRANSPORT_MODE_PAGE_FETCH,
    execute_request_transport,
    get_default_request_transport_config,
    normalize_request_transport_config,
)
from app.utils.human_mouse import (
    smooth_move_mouse,
    idle_drift,
    human_scroll,
    human_scroll_path,
    cdp_precise_click,
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


class _PageInteractionGate:
    """Throttle active page interactions across tabs to reduce renderer spikes."""

    def __init__(self):
        self._condition = threading.Condition()
        self._active_count = 0
        self._next_slot_at = 0.0

    @contextmanager
    def hold(
        self,
        *,
        label: str,
        session_id: str = "",
        max_concurrent: int = 1,
        timeout: float = 20.0,
        min_interval: float = 0.25,
        cancel_checker: Optional[Callable[[], bool]] = None,
    ):
        acquired = self._acquire(
            label=label,
            session_id=session_id,
            max_concurrent=max_concurrent,
            timeout=timeout,
            cancel_checker=cancel_checker,
        )
        try:
            yield acquired
        finally:
            if acquired:
                self._release(min_interval=min_interval)

    def _acquire(
        self,
        *,
        label: str,
        session_id: str,
        max_concurrent: int,
        timeout: float,
        cancel_checker: Optional[Callable[[], bool]] = None,
    ) -> bool:
        limit = max(1, int(max_concurrent or 1))
        wait_timeout = max(0.0, float(timeout or 0.0))
        deadline = time.time() + wait_timeout if wait_timeout > 0 else None

        while True:
            if cancel_checker and cancel_checker():
                return False

            with self._condition:
                now = time.time()
                if self._active_count < limit and now >= self._next_slot_at:
                    self._active_count += 1
                    return True

                if deadline is not None:
                    remaining = deadline - now
                    if remaining <= 0:
                        self._active_count += 1
                        logger.warning(
                            f"[INTERACT] throttle wait exceeded for {session_id or '-'}:{label}, "
                            "continuing fail-open"
                        )
                        return True
                else:
                    remaining = 0.25

                next_gap = max(0.0, self._next_slot_at - now)
                wait_for = min(max(0.05, next_gap), remaining) if deadline is not None else max(0.05, next_gap)
                self._condition.wait(timeout=max(0.05, min(wait_for, 0.25)))

    def _release(self, *, min_interval: float):
        with self._condition:
            if self._active_count > 0:
                self._active_count -= 1
            self._next_slot_at = max(self._next_slot_at, time.time() + max(0.0, float(min_interval or 0.0)))
            self._condition.notify_all()


_PAGE_INTERACTION_GATE = _PageInteractionGate()


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
        self._file_paste_config = file_paste_config or {}
        self._selectors = selectors or {}
        
        # 🆕 初始化双 Monitor（优先网络，回退 DOM）
        self._network_monitor = None
        self._stream_monitor = None
        self._last_input_element = None
        self._last_input_target_key = ""
        self._last_stream_media_state = {}
        self._request_transport = normalize_request_transport_config(
            (stream_config or {}).get("request_transport")
        )
        self._pending_request_transport_state: Optional[Dict[str, Any]] = None
        self._request_transport_bypass = False
        self._last_request_transport_sent = False
        self._last_send_attempt_state: Dict[str, Any] = {}
        
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
                interception_cfg.setdefault("silence_threshold", 2)
                interception_cfg.setdefault("response_interval", 0.3)
                effective_interception_stream_config = {
                    "hard_timeout": float(
                        (stream_config or {}).get("hard_timeout", 300) or 300
                    ),
                    "network": interception_cfg,
                }

                self._network_monitor = create_network_monitor(
                    tab=tab,
                    formatter=self.formatter,
                    stream_config=effective_interception_stream_config,
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
        )
        
        if extractor:
            logger.debug(f"WorkflowExecutor 使用提取器: {extractor.get_id()}")
        
        if self._image_config.get("enabled"):
            logger.debug(f"[IMAGE] 图片提取已启用")
        
        if self.stealth_mode:
            logger.debug("[STEALTH] 隐身模式已启用")

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

    def _get_request_transport_mode(self) -> str:
        return str(self._request_transport.get("mode") or get_default_request_transport_config().get("mode") or "workflow").strip().lower()

    def _get_request_transport_profile(self) -> str:
        return str(self._request_transport.get("profile") or "").strip()

    def _get_request_transport_options(self) -> Dict[str, Any]:
        options = self._request_transport.get("options") or {}
        return dict(options) if isinstance(options, dict) else {}

    def _get_request_transport_fallback_mode(self) -> str:
        fallback_mode = str(self._get_request_transport_options().get("fallback_mode") or "workflow").strip().lower()
        return fallback_mode if fallback_mode in {"workflow", "error"} else "workflow"

    def _request_transport_enabled(self) -> bool:
        return (
            not self._request_transport_bypass
            and self._get_request_transport_mode() == REQUEST_TRANSPORT_MODE_PAGE_FETCH
            and bool(self._get_request_transport_profile())
            and self._stream_mode == "network"
            and self._network_monitor is not None
        )

    def _context_has_non_text_inputs(self, prompt: str = "") -> bool:
        context = getattr(self, "_context", None) or {}
        if bool(context.get("images")):
            return True
        try:
            if prompt and self._text_handler._should_use_file_paste(prompt):
                return True
        except Exception:
            pass
        return False

    def _can_stage_request_transport(self, prompt: str = "") -> bool:
        effective_prompt = str(prompt or "").strip()
        if not effective_prompt:
            return False
        if not self._request_transport_enabled():
            return False
        if self._context_has_non_text_inputs(effective_prompt):
            return False
        return True

    def _queue_request_transport_prompt(
        self,
        *,
        selector: str,
        target_key: str,
        optional: bool,
        prompt: str,
    ) -> None:
        self._pending_request_transport_state = {
            "selector": str(selector or ""),
            "target_key": str(target_key or ""),
            "optional": bool(optional),
            "prompt": str(prompt or ""),
        }

    def _has_pending_request_transport_prompt(self) -> bool:
        return bool(
            isinstance(self._pending_request_transport_state, dict)
            and str(self._pending_request_transport_state.get("prompt") or "").strip()
        )

    def _clear_request_transport_state(self) -> None:
        self._pending_request_transport_state = None

    def consume_last_request_transport_sent(self) -> bool:
        sent = bool(self._last_request_transport_sent)
        self._last_request_transport_sent = False
        return sent

    def _reset_request_transport_monitor_if_needed(self) -> None:
        if self._network_monitor is None:
            return
        try:
            self._network_monitor._cleanup()
        except Exception as e:
            logger.debug(f"[REQUEST_TRANSPORT] 清理网络监听失败（忽略）: {e}")

    def _ensure_cached_prompt_filled(self) -> None:
        pending = self._pending_request_transport_state or {}
        prompt = str(pending.get("prompt") or "").strip()
        selector = str(pending.get("selector") or "")
        target_key = str(pending.get("target_key") or "")
        optional = bool(pending.get("optional", False))
        if not prompt:
            return

        self._request_transport_bypass = True
        try:
            self._execute_fill(selector, prompt, target_key, optional)
        finally:
            self._request_transport_bypass = False
            self._clear_request_transport_state()

    def _stage_request_transport_from_context(
        self,
        *,
        selector: str = "",
        target_key: str = "",
        optional: bool = False,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        prompt = str((context or {}).get("prompt") or "").strip()
        if not self._can_stage_request_transport(prompt):
            return False
        self._queue_request_transport_prompt(
            selector=selector,
            target_key=target_key,
            optional=optional,
            prompt=prompt,
        )
        logger.debug(
            f"[REQUEST_TRANSPORT] 已缓存页面直发 prompt "
            f"(profile={self._get_request_transport_profile()}, chars={len(prompt)})"
        )
        return True

    def _consume_request_transport_followup_steps(self, workflow: list, current_index: int) -> int:
        if current_index < 0 or current_index >= len(workflow):
            return current_index
        if str(workflow[current_index].get("action") or "").strip() != "PAGE_FETCH":
            return current_index

        next_index = current_index + 1
        consumed = 0
        while next_index < len(workflow):
            next_step = workflow[next_index] if isinstance(workflow[next_index], dict) else {}
            next_action = str(next_step.get("action") or "").strip()
            next_target = str(next_step.get("target") or "").strip()
            if next_action in {"FILL_INPUT", "KEY_PRESS"}:
                consumed += 1
                next_index += 1
                continue
            if next_action == "CLICK" and next_target == "send_btn":
                consumed += 1
                next_index += 1
                continue
            if next_action == "WAIT":
                lookahead = next_index + 1
                while lookahead < len(workflow):
                    lookahead_step = workflow[lookahead] if isinstance(workflow[lookahead], dict) else {}
                    lookahead_action = str(lookahead_step.get("action") or "").strip()
                    lookahead_target = str(lookahead_step.get("target") or "").strip()
                    if lookahead_action == "WAIT":
                        lookahead += 1
                        continue
                    break
                if (
                    lookahead < len(workflow)
                    and (
                        lookahead_action in {"FILL_INPUT", "KEY_PRESS"}
                        or (lookahead_action == "CLICK" and lookahead_target == "send_btn")
                    )
                ):
                    consumed += 1
                    next_index += 1
                    continue
            break

        if consumed > 0:
            logger.debug(
                f"[REQUEST_TRANSPORT] 页面直发成功后跳过 {consumed} 个回退步骤"
            )
        return next_index - 1

    def _attempt_request_transport_send(self) -> bool:
        if not self._has_pending_request_transport_prompt():
            return False
        if not self._request_transport_enabled():
            return False

        pending = self._pending_request_transport_state or {}
        prompt = str(pending.get("prompt") or "").strip()
        if not prompt:
            return False

        result = execute_request_transport(
            self.tab,
            self._request_transport,
            prompt=prompt,
            consume_response=False,
        )
        if result.get("ok"):
            logger.info(
                "[REQUEST_TRANSPORT] 页面直发已触发 "
                f"(profile={self._get_request_transport_profile()}, status={result.get('status')}, "
                f"session_id={result.get('session_id') or '-'})"
            )
            self._clear_request_transport_state()
            self._last_request_transport_sent = True
            return True

        fallback_mode = self._get_request_transport_fallback_mode()
        logger.warning(
            "[REQUEST_TRANSPORT] 页面直发失败: "
            f"profile={self._get_request_transport_profile()}, "
            f"error={result.get('error')}, status={result.get('status')}, "
            f"fallback={fallback_mode}"
        )
        if fallback_mode == "workflow":
            self._reset_request_transport_monitor_if_needed()
            return False

        self._clear_request_transport_state()
        raise WorkflowError(
            f"request_transport_failed:{result.get('error') or result.get('status') or 'unknown'}"
        )

    def _get_page_interaction_settings(self) -> Dict[str, Any]:
        return {
            "enabled": self._coerce_bool(
                BrowserConstants.get("PAGE_INTERACTION_THROTTLE_ENABLED"),
                True,
            ),
            "max_concurrent": self._coerce_int(
                BrowserConstants.get("PAGE_INTERACTION_MAX_CONCURRENT"),
                3,
                minimum=1,
            ),
            "max_wait": self._coerce_float(
                BrowserConstants.get("PAGE_INTERACTION_MAX_WAIT"),
                20.0,
                minimum=0.0,
            ),
            "min_interval": self._coerce_float(
                BrowserConstants.get("PAGE_INTERACTION_MIN_INTERVAL"),
                0.25,
                minimum=0.0,
            ),
            "ready_timeout": self._coerce_float(
                BrowserConstants.get("PAGE_INTERACTION_READY_TIMEOUT"),
                1.5,
                minimum=0.0,
            ),
            "stable_samples": self._coerce_int(
                BrowserConstants.get("PAGE_INTERACTION_STABLE_SAMPLES"),
                2,
                minimum=1,
            ),
            "sample_interval": self._coerce_float(
                BrowserConstants.get("PAGE_INTERACTION_SAMPLE_INTERVAL"),
                0.12,
                minimum=0.02,
            ),
            "rect_tolerance": self._coerce_int(
                BrowserConstants.get("PAGE_INTERACTION_RECT_TOLERANCE"),
                3,
                minimum=0,
            ),
        }

    @contextmanager
    def _page_interaction_slot(self, action: str, target_key: str = ""):
        settings = self._get_page_interaction_settings()
        if not settings["enabled"]:
            yield True
            return

        label = f"{action}:{target_key}" if target_key else str(action or "interaction")
        session_id = str(getattr(self.session, "id", "") or "")
        with _PAGE_INTERACTION_GATE.hold(
            label=label,
            session_id=session_id,
            max_concurrent=settings["max_concurrent"],
            timeout=settings["max_wait"],
            min_interval=settings["min_interval"],
            cancel_checker=self._check_cancelled,
        ) as acquired:
            yield acquired

    @staticmethod
    def _is_rect_stable(previous: Dict[str, Any], current: Dict[str, Any], tolerance: int) -> bool:
        if not previous or not current:
            return False
        previous_rect = previous.get("rect") or {}
        current_rect = current.get("rect") or {}
        for key in ("x", "y", "width", "height"):
            if abs(int(previous_rect.get(key, 0)) - int(current_rect.get(key, 0))) > tolerance:
                return False
        return True

    def _sample_element_interactable_state(self, ele) -> Dict[str, Any]:
        try:
            state = ele.run_js(
                """
                try {
                    const el = this;
                    if (!el || !el.isConnected) {
                        return { interactable: false, connected: false };
                    }
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const text = [
                        el.getAttribute('aria-label'),
                        el.getAttribute('title'),
                        el.getAttribute('data-testid'),
                        el.className,
                        el.innerText,
                        el.textContent
                    ].join(' ').toLowerCase();
                    const disabled = !!el.disabled || el.getAttribute('aria-disabled') === 'true';
                    const hidden = style.display === 'none'
                        || style.visibility === 'hidden'
                        || Number(style.opacity || '1') < 0.05;
                    const pointerEventsNone = style.pointerEvents === 'none';
                    const busy = el.getAttribute('aria-busy') === 'true'
                        || /loading|pending|sending|uploading|disable(?:d)?/.test(text);
                    const sizeOk = rect.width >= 1 && rect.height >= 1;
                    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
                    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
                    const viewportOk = rect.bottom >= 0
                        && rect.right >= 0
                        && rect.top <= viewportHeight
                        && rect.left <= viewportWidth;
                    return {
                        interactable: !disabled && !hidden && !pointerEventsNone && !busy && sizeOk && viewportOk,
                        connected: true,
                        disabled,
                        hidden,
                        busy,
                        pointerEventsNone,
                        rect: {
                            x: Math.round(rect.x || 0),
                            y: Math.round(rect.y || 0),
                            width: Math.round(rect.width || 0),
                            height: Math.round(rect.height || 0)
                        }
                    };
                } catch (error) {
                    return {
                        interactable: false,
                        connected: false,
                        error: String(error && error.message ? error.message : error || '')
                    };
                }
                """
            )
        except Exception as e:
            return {
                "interactable": False,
                "connected": False,
                "error": str(e),
            }
        return state if isinstance(state, dict) else {"interactable": False, "connected": False}

    def _wait_for_element_interactable(self, ele, target_key: str = "") -> None:
        settings = self._get_page_interaction_settings()
        timeout = settings["ready_timeout"]
        if timeout <= 0:
            return

        stable_needed = settings["stable_samples"]
        sample_interval = settings["sample_interval"]
        tolerance = settings["rect_tolerance"]
        stable_count = 0
        last_state: Optional[Dict[str, Any]] = None
        deadline = time.time() + timeout

        while time.time() < deadline:
            if self._check_cancelled():
                return

            current_state = self._sample_element_interactable_state(ele)
            if current_state.get("interactable"):
                stable_count = stable_count + 1 if self._is_rect_stable(last_state, current_state, tolerance) else 1
                if stable_count >= stable_needed:
                    return
            else:
                stable_count = 0

            last_state = current_state
            time.sleep(sample_interval)

        logger.debug_throttled(
            f"interaction.wait.{target_key or 'element'}",
            f"[INTERACT] 元素稳定等待超时: target={target_key or '-'}, state={last_state}",
            interval_sec=5.0,
        )

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
    
    def _smart_delay(self, min_sec: float = None, max_sec: float = None):
        """
        隐身模式下的短延迟。

        目标是保留动作衔接的自然感，不再为了“像人”而故意放慢。
        """
        if not self.stealth_mode:
            return

        if min_sec is None:
            min_sec = BrowserConstants.get("STEALTH_DELAY_MIN")
        if max_sec is None:
            max_sec = BrowserConstants.get("STEALTH_DELAY_MAX")

        min_sec = max(0.0, float(min_sec or 0.0))
        max_sec = max(min_sec, float(max_sec or 0.0))
        if max_sec <= 0:
            return

        spread = max_sec - min_sec
        if spread <= 0:
            total_delay = min_sec
        else:
            mean = min_sec + spread * 0.38
            std = max(0.004, spread / 5)
            total_delay = random.gauss(mean, std)
            total_delay = max(min_sec, min(total_delay, max_sec))

        pause_prob = float(BrowserConstants.get("STEALTH_PAUSE_PROBABILITY") or 0.0)
        pause_max = max(0.0, float(BrowserConstants.get("STEALTH_PAUSE_EXTRA_MAX") or 0.0))
        if pause_prob > 0 and pause_max > 0 and random.random() < pause_prob:
            extra = random.uniform(min(0.03, pause_max), pause_max)
            total_delay = min(total_delay + extra, max_sec + pause_max)
            logger.debug(f"[STEALTH] 随机停顿 +{extra:.2f}s")

        elapsed = 0.0
        step = 0.02
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
                if self.stealth_mode and not getattr(self, '_page_warmed_up', False):
                    self._warmup_page_for_stealth()
                    self._page_warmed_up = True
                
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
                if self.stealth_mode and not getattr(self, '_page_warmed_up', False):
                    self._warmup_page_for_stealth()
                    self._page_warmed_up = True

                self._execute_coord_click(value, optional)

            elif action == "COORD_SCROLL":
                if self.stealth_mode and not getattr(self, '_page_warmed_up', False):
                    self._warmup_page_for_stealth()
                    self._page_warmed_up = True

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
                        self._last_stream_media_state = {}
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

    def _execute_click(self, selector: str, target_key: str, optional: bool):
        """执行点击操作（v5.7 隐身模式人类化点击）"""
        if self._check_cancelled():
            return

        last_error = None
        found_element = False
        for attempt in range(2):
            try:
                with self._page_interaction_slot("CLICK", target_key) as acquired:
                    if not acquired or self._check_cancelled():
                        return

                    ele = self.finder.find_with_fallback(selector, target_key)
                    if not ele:
                        break
                    found_element = True

                    self._wait_for_element_interactable(ele, target_key)

                    if self.stealth_mode:
                        self._stealth_click_element(ele)
                    else:
                        if self._check_cancelled():
                            return
                        ele.click()

                self._smart_delay(
                    BrowserConstants.ACTION_DELAY_MIN,
                    BrowserConstants.ACTION_DELAY_MAX
                )
                return

            except Exception as click_err:
                last_error = click_err
                logger.debug(f"点击异常: {click_err}")
                if attempt == 0 and target_key != "send_btn":
                    logger.warning(
                        f"[CLICK] {target_key or selector} 点击失败，等待后重试一次: {click_err}"
                    )
                    time.sleep(0.12)
                    continue
                break

        if found_element:
            if target_key == "send_btn":
                logger.warning(f"[CLICK] 发送按钮点击失败，降级到 Enter 键: {last_error}")
                self._execute_keypress("Enter")
            elif self.stealth_mode and last_error is not None:
                raise last_error
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
            with self._page_interaction_slot("COORD_CLICK", "coord_click") as acquired:
                if not acquired or self._check_cancelled():
                    return
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

    def _execute_coord_scroll(self, value: Any, optional: bool):
        """执行坐标滚轮滑动。"""
        if self._check_cancelled():
            return

        if not isinstance(value, dict):
            if optional:
                logger.warning("[COORD_SCROLL] 缺少滑动配置，已跳过")
                return
            raise WorkflowError("coord_scroll_missing_value")

        try:
            start_x = int(value.get("start_x"))
            start_y = int(value.get("start_y"))
            end_x = int(value.get("end_x"))
            end_y = int(value.get("end_y"))
        except Exception:
            if optional:
                logger.warning(f"[COORD_SCROLL] 坐标无效，已跳过: {value}")
                return
            raise WorkflowError("coord_scroll_invalid_position")

        try:
            with self._page_interaction_slot("COORD_SCROLL", "coord_scroll") as acquired:
                if not acquired or self._check_cancelled():
                    return
                if self.stealth_mode:
                    self._human_scroll_at(start_x, start_y, end_x, end_y)
                else:
                    self._direct_scroll_at(start_x, start_y, end_x, end_y)

            self._smart_delay(
                BrowserConstants.ACTION_DELAY_MIN,
                BrowserConstants.ACTION_DELAY_MAX
            )
        except Exception:
            if optional:
                logger.warning(
                    f"[COORD_SCROLL] 滑动失败，已跳过: "
                    f"({start_x}, {start_y}) -> ({end_x}, {end_y})"
                )
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
        time.sleep(random.uniform(0.01, 0.04))
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
                duration=random.uniform(0.02, 0.05),
                center_pos=self._mouse_pos,
                check_cancelled=self._check_cancelled,
                drift_radius=random.uniform(0.8, 1.8),
                freq_hz=random.uniform(7.0, 11.0)
            )
        else:
            time.sleep(random.uniform(0.015, 0.035))

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
            time.sleep(random.uniform(0.03, 0.08))
            success = cdp_precise_click(
                tab=self.tab,
                x=x,
                y=y,
                check_cancelled=self._check_cancelled
            )

        if not success:
            raise WorkflowError("coord_click_failed")

        self._mouse_pos = (x, y)

    def _direct_scroll_at(self, start_x: int, start_y: int, end_x: int, end_y: int):
        """普通模式下执行坐标滚轮滑动。"""
        total_dx = end_x - start_x
        total_dy = end_y - start_y
        logger.debug(
            f"[COORD_SCROLL] normal wheel scroll: "
            f"({start_x}, {start_y}) -> ({end_x}, {end_y})"
        )

        steps = max(3, min(12, int(max(abs(total_dx), abs(total_dy)) / 90) + 1))
        prev_dx = 0
        prev_dy = 0

        for i in range(1, steps + 1):
            if self._check_cancelled():
                return

            t = i / steps
            anchor_x = int(round(start_x + total_dx * t))
            anchor_y = int(round(start_y + total_dy * t))
            scroll_dx = int(round(total_dx * t)) - prev_dx
            scroll_dy = int(round(total_dy * t)) - prev_dy

            self.tab.run_cdp(
                'Input.dispatchMouseEvent',
                type='mouseMoved',
                x=anchor_x,
                y=anchor_y,
                button='none',
                buttons=0,
                modifiers=0,
                pointerType='mouse'
            )
            self.tab.run_cdp(
                'Input.dispatchMouseEvent',
                type='mouseWheel',
                x=anchor_x,
                y=anchor_y,
                deltaX=scroll_dx,
                deltaY=scroll_dy,
                button='none',
                buttons=0,
                pointerType='mouse'
            )

            prev_dx += scroll_dx
            prev_dy += scroll_dy

            if i < steps:
                time.sleep(random.uniform(0.02, 0.06))

        self._mouse_pos = (end_x, end_y)

    def _human_scroll_at(self, start_x: int, start_y: int, end_x: int, end_y: int):
        """隐身模式下执行人类化坐标滚轮滑动。"""
        logger.debug(
            f"[COORD_SCROLL] stealth wheel scroll: "
            f"({start_x}, {start_y}) -> ({end_x}, {end_y})"
        )

        start_pos = self._ensure_mouse_origin()
        self._mouse_pos = smooth_move_mouse(
            tab=self.tab,
            from_pos=start_pos,
            to_pos=(start_x, start_y),
            check_cancelled=self._check_cancelled
        )

        if self._check_cancelled():
            return

        if random.random() < 0.6:
            self._mouse_pos = idle_drift(
                tab=self.tab,
                duration=random.uniform(0.02, 0.05),
                center_pos=self._mouse_pos,
                check_cancelled=self._check_cancelled,
                drift_radius=random.uniform(0.8, 1.8),
                freq_hz=random.uniform(7.0, 10.0)
            )
        else:
            time.sleep(random.uniform(0.015, 0.035))

        if self._check_cancelled():
            return

        self._mouse_pos = human_scroll_path(
            tab=self.tab,
            from_pos=(start_x, start_y),
            to_pos=(end_x, end_y),
            check_cancelled=self._check_cancelled
        )
    
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
        
        # 3. 极短停顿/微漂移，让点击衔接自然但不拖节奏
        if random.random() < 0.6:
            self._mouse_pos = idle_drift(
                tab=self.tab,
                duration=random.uniform(0.02, 0.05),
                center_pos=self._mouse_pos,
                check_cancelled=self._check_cancelled,
                drift_radius=random.uniform(0.8, 1.6),
                freq_hz=random.uniform(7.0, 11.0)
            )
        else:
            time.sleep(random.uniform(0.015, 0.035))
        
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
            time.sleep(random.uniform(0.04, 0.10))
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
                phase_flags = AttachmentMonitor.derive_phase_flags(
                    state,
                    require_send_enabled=True,
                    require_attachment_present=self._get_attachment_monitor_flag(
                        "require_attachment_present",
                        False,
                    ),
                    require_upload_signal_before_ready=self._get_attachment_monitor_flag(
                        "require_upload_signal_before_ready",
                        False,
                    ),
                )
                state.update(phase_flags)
                state["ready"] = bool(phase_flags.get("upload_ready"))
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
                    "upload_started": False,
                    "uploading": False,
                    "attachment_present": False,
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
                    || /disable(?:d)?|loading|uploading|sending/.test(String(sendBtn.className || '').toLowerCase())
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
                "upload_started": False,
                "uploading": False,
                "attachment_present": False,
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
            recent_attachment_age = self._recent_attachment_age_seconds()
            recent_file_upload = False
            recent_image_upload = False
            confirmed_file_upload = False
            try:
                recent_file_upload = bool(self._text_handler.has_recent_attachment_upload())
            except Exception:
                recent_file_upload = False
            try:
                recent_image_upload = bool(self._image_handler.has_recent_attachment_upload())
            except Exception:
                recent_image_upload = False
            if not recent_image_upload:
                context = getattr(self, "_context", None) or {}
                recent_image_upload = bool(context.get("images"))
            if recent_file_upload:
                try:
                    confirmed_file_upload = bool(self._text_handler.has_confirmed_upload_signal())
                except Exception:
                    confirmed_file_upload = False

            reuse_existing_tracking = recent_attachment_age is not None
            require_attachment_confirmation = recent_image_upload or (
                recent_file_upload and not confirmed_file_upload
            )
            if reuse_existing_tracking:
                logger.debug(
                    "[SEND] Recent attachment upload detected before submit; "
                    f"reusing existing attachment tracking (age={recent_attachment_age:.1f}s)"
                )
            if recent_file_upload and confirmed_file_upload and not recent_image_upload:
                logger.debug(
                    "[SEND] Recent file-paste upload was strongly confirmed; "
                    "send gate will only wait for pending/busy signals"
                )
            result = self._attachment_monitor.wait_until_ready(
                require_observed=require_attachment_confirmation,
                require_send_enabled=True,
                accept_existing=not require_attachment_confirmation,
                start_new_tracking=not reuse_existing_tracking,
                max_wait=max_wait,
                poll_interval=check_interval,
                stable_window=stable_window,
                require_attachment_present=require_attachment_confirmation,
                label="send-gate",
            )
            if result.get("success"):
                return

            continue_once = self._get_attachment_monitor_flag(
                "continue_once_on_unconfirmed_send",
                True,
            )
            if not continue_once:
                logger.warning(
                    "[SEND] Attachment readiness was not confirmed before submit; blocking send "
                    f"({AttachmentMonitor.summarize(result)})"
                )
                raise WorkflowError("attachment_ready_unconfirmed_before_send")
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
            "attachment_sensitivity": "medium",
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
        file_paste_config = self._get_file_paste_send_confirmation_config()
        if isinstance(file_paste_config, dict):
            raw_config = {
                **(raw_config if isinstance(raw_config, dict) else {}),
                **file_paste_config,
            }

        if isinstance(raw_config, dict):
            config.update(raw_config)

        return config

    def _get_raw_send_confirmation_config(self) -> Dict[str, Any]:
        """Return only the site-provided send confirmation overrides."""
        raw_config: Dict[str, Any] = {}
        if isinstance(self._stream_config, dict):
            legacy_config = self._stream_config.get("send_confirmation", {}) or {}
            if isinstance(legacy_config, dict):
                raw_config.update(legacy_config)
        file_paste_config = self._get_file_paste_send_confirmation_config()
        if isinstance(file_paste_config, dict):
            raw_config.update(file_paste_config)
        return raw_config

    def _get_send_confirmation_window(
        self,
        key: str,
        fallback: float,
        *,
        min_value: float = 0.0,
        max_value: Optional[float] = None,
        raw_only: bool = False,
    ) -> float:
        """Read a numeric send confirmation option with clamping."""
        config = self._get_raw_send_confirmation_config() if raw_only else self._get_send_confirmation_config()
        try:
            value = float(config.get(key, fallback))
        except (TypeError, ValueError):
            value = float(fallback)

        value = max(min_value, value)
        if max_value is not None:
            value = min(value, max_value)
        return value

    def _get_send_confirmation_flag(
        self,
        key: str,
        fallback: bool = True,
        *,
        raw_only: bool = False,
    ) -> bool:
        """Read a boolean send confirmation option."""
        config = self._get_raw_send_confirmation_config() if raw_only else self._get_send_confirmation_config()
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

    def _get_send_confirmation_int(
        self,
        key: str,
        fallback: int,
        *,
        min_value: int = 0,
        max_value: Optional[int] = None,
        raw_only: bool = False,
    ) -> int:
        config = self._get_raw_send_confirmation_config() if raw_only else self._get_send_confirmation_config()
        try:
            value = int(config.get(key, fallback))
        except (TypeError, ValueError):
            value = int(fallback)
        value = max(min_value, value)
        if max_value is not None:
            value = min(value, max_value)
        return value

    def _get_attachment_send_confirmation_profile(self) -> Dict[str, Any]:
        """Resolve the 3-level attachment send sensitivity profile."""
        raw_value = str(
            self._get_raw_send_confirmation_config().get("attachment_sensitivity")
            or self._get_send_confirmation_config().get("attachment_sensitivity")
            or "medium"
        ).strip().lower()
        level = raw_value if raw_value in {"low", "medium", "high"} else "medium"

        profiles = {
            "low": {
                "attachment_observe_window": 4.0,
                "trust_network_activity": True,
                "trust_generating_indicator": True,
                "trust_send_disabled_with_input_shrink": False,
            },
            "medium": {
                "attachment_observe_window": 6.0,
                "trust_network_activity": True,
                "trust_generating_indicator": True,
                "trust_send_disabled_with_input_shrink": True,
            },
            "high": {
                "attachment_observe_window": 8.0,
                "trust_network_activity": True,
                "trust_generating_indicator": True,
                "trust_send_disabled_with_input_shrink": True,
            },
        }
        return {
            "level": level,
            **profiles[level],
        }

    @staticmethod
    def _to_query_selector(selector: Any) -> str:
        """Convert a configured selector into querySelector-compatible CSS when possible."""
        value = str(selector or "").strip()
        if not value:
            return ""

        lowered = value.lower()
        if lowered.startswith("css:"):
            return value[4:].strip()

        if lowered.startswith(("xpath:", "tag:")) or value.startswith("@") or "@@" in value:
            return ""

        return value

    def _probe_send_post_click_state(self, send_selector: str = "") -> Dict[str, Any]:
        """Passively inspect whether the page has transitioned into generating state."""
        selector_json = json.dumps(self._to_query_selector(send_selector), ensure_ascii=False)
        generating_selector = ""
        if isinstance(self._selectors, dict):
            generating_selector = self._to_query_selector(
                self._selectors.get("generating_indicator", "")
            )
        generating_selector_json = json.dumps(generating_selector, ensure_ascii=False)
        js = f"""
        return (function() {{
            try {{
                const sendSelector = {selector_json};
                const configuredGeneratingSelector = {generating_selector_json};
                const indicators = [
                    configuredGeneratingSelector,
                    'button[aria-label*="Stop"]',
                    'button[aria-label*="stop"]',
                    'button[aria-label*="停止"]',
                    '[data-state="streaming"]',
                    '.stop-generating'
                ].filter(Boolean);

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
                    || /disable(?:d)?|loading|uploading|sending/.test(sendMeta)
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
        trust_network_activity: Optional[bool] = None,
        trust_generating_indicator: Optional[bool] = None,
        trust_send_disabled_with_input_shrink: Optional[bool] = None,
    ) -> bool:
        """Observe post-click send signals without issuing another click."""
        observe_window = self._get_send_confirmation_window(
            "attachment_observe_window",
            getattr(BrowserConstants, "ATTACHMENT_SEND_OBSERVE_WINDOW", 6.0),
            min_value=0.0,
            max_value=60.0,
        ) if max_wait is None else float(max_wait)
        if trust_network_activity is None:
            trust_network_activity = self._get_send_confirmation_flag(
                "trust_network_activity",
                True,
            )
        if trust_generating_indicator is None:
            trust_generating_indicator = self._get_send_confirmation_flag(
                "trust_generating_indicator",
                True,
            )
        if trust_send_disabled_with_input_shrink is None:
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

    def _probe_attachment_state_probe(self, state: Optional[Dict[str, Any]], stage: str) -> Dict[str, Any]:
        if self._attachment_monitor is None:
            return {
                "enabled": False,
                "ok": False,
                "hit": False,
                "result": {},
                "summary": "",
            }
        try:
            return self._attachment_monitor.run_state_probe(state=state, stage=stage)
        except Exception as e:
            logger.debug(f"[SEND] 附件 state probe 执行失败 ({stage}): {e}")
            return {
                "enabled": True,
                "ok": False,
                "hit": False,
                "result": {},
                "summary": str(e)[:240],
            }

    def _build_send_attempt_state(
        self,
        *,
        send_selector: str,
        before_len: int,
        after_len: int,
        baseline_attachment_state: Optional[Dict[str, Any]] = None,
        attachment_state: Optional[Dict[str, Any]] = None,
        network_state: Optional[Dict[str, Any]] = None,
        post_click_state: Optional[Dict[str, Any]] = None,
        probe_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base_attachment_state = dict(baseline_attachment_state or {})
        current_attachment_state = dict(attachment_state or {})
        attachment_delta = {
            "attachmentCount": int(current_attachment_state.get("attachmentCount", 0) or 0)
            - int(base_attachment_state.get("attachmentCount", 0) or 0),
            "previewCount": int(current_attachment_state.get("previewCount", 0) or 0)
            - int(base_attachment_state.get("previewCount", 0) or 0),
            "fileInputCount": int(current_attachment_state.get("fileInputCount", 0) or 0)
            - int(base_attachment_state.get("fileInputCount", 0) or 0),
        }
        attachment_changed = bool(
            current_attachment_state.get("attachmentFingerprint")
            and current_attachment_state.get("attachmentFingerprint") != base_attachment_state.get("attachmentFingerprint")
        ) or any(delta != 0 for delta in attachment_delta.values())
        attachment_disappeared = bool(
            AttachmentMonitor._attachment_present(base_attachment_state)
            and not AttachmentMonitor._attachment_present(current_attachment_state)
        )

        state = {
            "before_input_length": int(before_len or 0),
            "after_input_length": int(after_len or 0),
            "input_shrunk": int(after_len or 0) < int(before_len or 0),
            "attachment_before": base_attachment_state,
            "attachment_after": current_attachment_state,
            "attachment_delta": attachment_delta,
            "attachment_changed_after_send": attachment_changed,
            "attachment_disappeared_after_send": attachment_disappeared,
            "network": dict(network_state or {}),
            "post_click": dict(post_click_state or {}),
            "probe": dict(probe_info or {}),
        }

        accepted_signals = []
        if state["input_shrunk"]:
            accepted_signals.append("input_shrunk")
        if bool((state["network"] or {}).get("matched")):
            accepted_signals.append("network_activity")
        if bool((state["post_click"] or {}).get("generating")):
            accepted_signals.append("generating")
        if bool((state["post_click"] or {}).get("sendLooksLikeStop")):
            accepted_signals.append("send_became_stop")
        if bool((state["post_click"] or {}).get("sendDisabled")) and state["input_shrunk"]:
            accepted_signals.append("send_disabled_with_input_shrink")
        if attachment_changed:
            accepted_signals.append("attachment_changed")
        if attachment_disappeared:
            accepted_signals.append("attachment_disappeared")
        probe_result = (probe_info or {}).get("result") if isinstance(probe_info, dict) else {}
        if isinstance(probe_result, dict) and bool(probe_result.get("accepted")):
            accepted_signals.append("probe_accepted")
        if isinstance(probe_result, dict) and bool(probe_result.get("confirmed")):
            accepted_signals.append("probe_confirmed")
        if isinstance(probe_result, dict) and bool(probe_result.get("retry")):
            state["probe_retry"] = True
        if isinstance(probe_result, dict) and bool(probe_result.get("uploading")):
            state["probe_uploading"] = True
        if isinstance(probe_result, dict) and bool(probe_result.get("ready")):
            state["probe_ready"] = True

        state["accepted_signals"] = accepted_signals
        state["accepted"] = bool(accepted_signals)
        return state

    def _should_retry_attachment_send(
        self,
        attempt_state: Dict[str, Any],
        *,
        retry_index: int,
        max_retry_count: int,
    ) -> bool:
        if retry_index >= max_retry_count:
            return False

        retry_on_unconfirmed = self._get_send_confirmation_flag(
            "retry_on_unconfirmed_send",
            True,
            raw_only=True,
        )
        if not retry_on_unconfirmed:
            return False

        if self._get_send_confirmation_flag("retry_block_if_generating", True, raw_only=True):
            if bool((attempt_state.get("post_click") or {}).get("generating")):
                return False

        if self._get_send_confirmation_flag("retry_block_on_stop_button", True, raw_only=True):
            if bool((attempt_state.get("post_click") or {}).get("sendLooksLikeStop")):
                return False

        probe_result = (attempt_state.get("probe") or {}).get("result") if isinstance(attempt_state.get("probe"), dict) else {}
        if isinstance(probe_result, dict):
            if probe_result.get("shouldRetry") is False:
                return False
            if probe_result.get("retry") is True:
                return True
            if probe_result.get("accepted") is True or probe_result.get("confirmed") is True:
                return False

        if bool((attempt_state.get("network") or {}).get("matched")):
            return False

        if bool(attempt_state.get("accepted")):
            if self._get_send_confirmation_flag("accept_attachment_change", False, raw_only=True):
                if "attachment_changed" in (attempt_state.get("accepted_signals") or []):
                    return False
            if self._get_send_confirmation_flag("accept_attachment_disappear", False, raw_only=True):
                if "attachment_disappeared" in (attempt_state.get("accepted_signals") or []):
                    return False
            if self._get_send_confirmation_flag("accept_probe_confirmation", True, raw_only=True):
                if any(
                    signal in (attempt_state.get("accepted_signals") or [])
                    for signal in ("probe_accepted", "probe_confirmed")
                ):
                    return False
            if any(
                signal in (attempt_state.get("accepted_signals") or [])
                for signal in ("input_shrunk", "generating", "send_became_stop", "network_activity")
            ):
                return False

        return True

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
        avoid_repeat_click = self._has_recent_attachment_upload()
        attachment_profile = self._get_attachment_send_confirmation_profile()
        max_retry_count = self._get_send_confirmation_int(
            "max_retry_count",
            2,
            min_value=0,
            max_value=10,
            raw_only=True,
        )
        retry_interval = self._get_send_confirmation_window(
            "retry_interval",
            getattr(BrowserConstants, "IMAGE_SEND_RETRY_INTERVAL", 0.6),
            min_value=0.0,
            max_value=max_wait,
            raw_only=True,
        )
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
            attachment_profile["attachment_observe_window"],
            min_value=0.0,
            max_value=max_wait,
            raw_only=True,
        )
        attachment_trust_network_activity = self._get_send_confirmation_flag(
            "trust_network_activity",
            attachment_profile["trust_network_activity"],
            raw_only=True,
        )
        attachment_trust_generating_indicator = self._get_send_confirmation_flag(
            "trust_generating_indicator",
            attachment_profile["trust_generating_indicator"],
            raw_only=True,
        )
        attachment_trust_send_disabled_with_input_shrink = self._get_send_confirmation_flag(
            "trust_send_disabled_with_input_shrink",
            attachment_profile["trust_send_disabled_with_input_shrink"],
            raw_only=True,
        )

        before_len = self._safe_get_input_len_by_key("input_box")
        baseline_attachment_state = self._probe_attachment_readiness(selector) if avoid_repeat_click else {}
        self._execute_click(selector, target_key, optional)

        time.sleep(0.25)
        after_len = self._safe_get_input_len_by_key("input_box")

        if self._is_send_success(before_len, after_len):
            logger.info("发送成功")
            return

        if avoid_repeat_click:
            network_probe = {"matched": False}
            if attachment_trust_network_activity and self._network_monitor is not None:
                try:
                    network_probe = self._network_monitor.poll_send_activity(timeout=min(0.3, attachment_observe_window)) or {"matched": False}
                except Exception as e:
                    logger.debug_throttled(
                        "send.attachment_network_probe_failed",
                        f"[SEND] 附件发送网络探测失败: {e}",
                        interval_sec=5.0,
                    )
            post_click_state = self._probe_send_post_click_state(selector)
            attachment_state_after_send = self._probe_attachment_readiness(selector)
            probe_info = self._probe_attachment_state_probe(attachment_state_after_send, "after_send")
            attempt_state = self._build_send_attempt_state(
                send_selector=selector,
                before_len=before_len,
                after_len=after_len,
                baseline_attachment_state=baseline_attachment_state,
                attachment_state=attachment_state_after_send,
                network_state=network_probe,
                post_click_state=post_click_state,
                probe_info=probe_info,
            )
            self._last_send_attempt_state = attempt_state

            accept_unconfirmed = False
            if self._get_send_confirmation_flag("accept_attachment_change", False, raw_only=True):
                accept_unconfirmed = accept_unconfirmed or bool(attempt_state.get("attachment_changed_after_send"))
            if self._get_send_confirmation_flag("accept_attachment_disappear", False, raw_only=True):
                accept_unconfirmed = accept_unconfirmed or bool(attempt_state.get("attachment_disappeared_after_send"))
            if self._get_send_confirmation_flag("accept_probe_confirmation", True, raw_only=True):
                accept_unconfirmed = accept_unconfirmed or any(
                    signal in (attempt_state.get("accepted_signals") or [])
                    for signal in ("probe_accepted", "probe_confirmed")
                )

            if self._observe_send_without_retry(
                selector,
                before_len,
                max_wait=attachment_observe_window,
                trust_network_activity=attachment_trust_network_activity,
                trust_generating_indicator=attachment_trust_generating_indicator,
                trust_send_disabled_with_input_shrink=attachment_trust_send_disabled_with_input_shrink,
            ):
                logger.info(
                    f"发送成功（附件场景，已避免重复点击发送按钮，sensitivity={attachment_profile['level']}）"
                )
            elif accept_unconfirmed:
                logger.info(
                    f"发送成功（附件场景，通过补充信号确认，signals={attempt_state.get('accepted_signals') or []}）"
                )
            else:
                if max_retry_count > 0 and self._should_retry_attachment_send(
                    attempt_state,
                    retry_index=0,
                    max_retry_count=max_retry_count,
                ):
                    logger.warning(
                        "[SEND] 附件发送场景未确认成功，进入附件重试 "
                        f"(signals={attempt_state.get('accepted_signals') or []}, "
                        f"sensitivity={attachment_profile['level']})"
                    )

                    for retry_index in range(1, max_retry_count + 1):
                        if self._check_cancelled():
                            return

                        wait_for = max(0.0, min(retry_interval, max_wait))
                        if wait_for > 0:
                            time.sleep(wait_for)

                        pre_retry_probe_window = min(
                            max(0.0, retry_probe_window),
                            max(0.0, max_wait),
                        )
                        if pre_retry_probe_window > 0 and self._observe_send_without_retry(
                            selector,
                            before_len,
                            max_wait=pre_retry_probe_window,
                            trust_network_activity=attachment_trust_network_activity,
                            trust_generating_indicator=attachment_trust_generating_indicator,
                            trust_send_disabled_with_input_shrink=attachment_trust_send_disabled_with_input_shrink,
                        ):
                            logger.info(f"发送成功（附件重试前观察确认，第 {retry_index} 轮）")
                            return

                        self._execute_click(selector, target_key, optional)
                        time.sleep(0.25)
                        retry_after_len = self._safe_get_input_len_by_key("input_box")
                        retry_network_probe = {"matched": False}
                        if attachment_trust_network_activity and self._network_monitor is not None:
                            try:
                                retry_network_probe = self._network_monitor.poll_send_activity(
                                    timeout=min(0.3, attachment_observe_window)
                                ) or {"matched": False}
                            except Exception as e:
                                logger.debug_throttled(
                                    "send.attachment_network_retry_probe_failed",
                                    f"[SEND] 附件重试网络探测失败: {e}",
                                    interval_sec=5.0,
                                )
                        retry_post_click_state = self._probe_send_post_click_state(selector)
                        retry_attachment_state = self._probe_attachment_readiness(selector)
                        retry_probe_info = self._probe_attachment_state_probe(
                            retry_attachment_state,
                            f"retry_{retry_index}",
                        )
                        retry_attempt_state = self._build_send_attempt_state(
                            send_selector=selector,
                            before_len=before_len,
                            after_len=retry_after_len,
                            baseline_attachment_state=baseline_attachment_state,
                            attachment_state=retry_attachment_state,
                            network_state=retry_network_probe,
                            post_click_state=retry_post_click_state,
                            probe_info=retry_probe_info,
                        )
                        self._last_send_attempt_state = retry_attempt_state

                        if self._observe_send_without_retry(
                            selector,
                            before_len,
                            max_wait=min(retry_observe_window, max_wait),
                            trust_network_activity=attachment_trust_network_activity,
                            trust_generating_indicator=attachment_trust_generating_indicator,
                            trust_send_disabled_with_input_shrink=attachment_trust_send_disabled_with_input_shrink,
                        ):
                            logger.info(f"发送成功（附件重试后观察确认，第 {retry_index} 轮）")
                            return

                        if not self._should_retry_attachment_send(
                            retry_attempt_state,
                            retry_index=retry_index,
                            max_retry_count=max_retry_count,
                        ):
                            logger.warning(
                                "[SEND] 附件重试停止：不再满足重试条件 "
                                f"(signals={retry_attempt_state.get('accepted_signals') or []})"
                            )
                            return

                    logger.warning(
                        "[SEND] 附件发送重试已达到上限，停止自动补点 "
                        f"(max_retry_count={max_retry_count})"
                    )
                else:
                    logger.warning(
                        "[SEND] 附件发送场景未观察到明确成功信号，跳过自动补点以避免误点停止按钮 "
                        f"(sensitivity={attachment_profile['level']})"
                    )
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
        
        max_wait = float(BrowserConstants.get('STEALTH_SEND_IMAGE_WAIT') or 8.0)
        retry_interval = float(BrowserConstants.get('STEALTH_SEND_IMAGE_RETRY_INTERVAL') or 1.2)
        
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
        页面预热（极速简化版）

        仅建立一个合理的鼠标起点，避免首个动作过于突兀，
        不再为了“拟人”加入明显的停顿和扫视。
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
            
            # 仅保留极短缓冲，避免首个动作过于生硬
            self._idle_wait(random.uniform(0.08, 0.18))
            
            if self._check_cancelled():
                return
            
            # 最多一次轻微修正，保持动作连贯
            move_count = 1 if random.random() < 0.45 else 0
            for i in range(move_count):
                if self._check_cancelled():
                    return
                
                # 小幅移动（仅做起手姿态修正）
                dx = random.randint(-int(vw * 0.08), int(vw * 0.08))
                dy = random.randint(-int(vh * 0.06), int(vh * 0.06))
                target_x = max(50, min(vw - 50, self._mouse_pos[0] + dx))
                target_y = max(50, min(vh - 50, self._mouse_pos[1] + dy))
                
                self._mouse_pos = smooth_move_mouse(
                    tab=self.tab,
                    from_pos=self._mouse_pos,
                    to_pos=(target_x, target_y),
                    check_cancelled=self._check_cancelled
                )
                
                self._idle_wait(random.uniform(0.04, 0.10))

            self._idle_wait(random.uniform(0.05, 0.12))
            
            logger.debug(f"[STEALTH] 页面预热完成（{move_count} 次移动）")
            
        except Exception as e:
            logger.debug(f"[STEALTH] 页面预热异常（可忽略）: {e}")
    
    # ================= 输入框填充 =================
    
    def _execute_fill(self, selector: str, text: str, target_key: str, optional: bool):
        """填充输入框（v5.7 隐身增强版）"""
        if self._check_cancelled():
            return

        with self._page_interaction_slot("FILL_INPUT", target_key) as acquired:
            if not acquired or self._check_cancelled():
                return

            ele = self.finder.find_with_fallback(selector, target_key)
            if not ele:
                if not optional:
                    raise ElementNotFoundError("找不到输入框")
                return

            self._last_input_element = ele
            self._last_input_target_key = target_key or ""
            self._text_handler.set_active_input_context(selector=selector, target_key=target_key)

            self._wait_for_element_interactable(ele, target_key)

            if self.stealth_mode:
                self._stealth_click_element(ele)
                time.sleep(random.uniform(0.04, 0.10))
                self._text_handler.fill_via_clipboard_no_click(ele, text)
            else:
                self._text_handler.fill_via_js(ele, text)

            if hasattr(self, '_context') and self._context:
                images = self._context.get('images', [])
                if images:
                    if not self._image_handler.paste_images(images):
                        raise WorkflowError("image_paste_unconfirmed")
        
        # ===== 隐身模式：粘贴后仅保留极短缓冲，避免节奏被故意拖慢 =====
        if self.stealth_mode and len(text) > 0:
            base_delay = random.uniform(0.10, 0.22)
            extra_delay = min(0.22, (len(text) / 12000.0) * random.uniform(0.04, 0.08))
            total_review = min(base_delay + extra_delay, 0.45)

            logger.debug(f"[STEALTH] 粘贴后阅读延迟 {total_review:.1f}s (文本长度={len(text)})")
            self._idle_wait(total_review)


__all__ = ['WorkflowExecutor']
