"""
app/core/page_capture/kimi_fetch_capture.py - Kimi page-side fetch stream capture.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Generator

from app.core.config import logger
from app.core.network_monitor import NetworkMonitorError, NetworkMonitorTimeout
from app.core.parsers import ParserRegistry

from .base import PageFetchCapture
from .registry import register_page_fetch_capture


class KimiPageFetchCapture(PageFetchCapture):
    """Capture Kimi connect+json streams from the page fetch runtime."""

    parser_id = "kimi"
    monitor_id = "kimi_page"
    mode_name = "Kimi 页面抓流"

    _BOOTSTRAP_JS = r"""
(() => {
  const W = window;
  const KEY = "__KIMI_CAPTURE__";
  const TARGET = "/apiv2/kimi.gateway.chat.v1.ChatService/Chat";

  // 修复(7b)：逐字节 += 字符串拼接是 O(n^2)，改为数组 map + join 一次性拼接
  const toEscapedBytes = (chunk) => {
    return Array.from(chunk, (b) => "\\u00" + b.toString(16).padStart(2, "0")).join("");
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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._capture_token = ""
        self._init_js_id = None
        self._parser = ParserRegistry.get("kimi")
        self.ensure_init_js()

    def prepare(self) -> None:
        self.ensure_init_js()
        token = f"kimi_{uuid.uuid4().hex[:12]}"

        with self._page_interaction_slot("JS_EXEC", "kimi_capture_prepare") as acquired:
            if not acquired or self._check_cancelled():
                return
            install_result = self.tab.run_js(
                f"return {self._BOOTSTRAP_JS.strip()}"
            )
            self.tab.run_js(
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

        self._capture_token = token
        self._sent_content_length = 0
        if install_result is not None:
            logger.debug(f"[Executor] Kimi 页面抓流已准备: {install_result}")

    def ensure_init_js(self) -> None:
        if self._init_js_id:
            return

        # 修复(8)：去重标记挂在 tab 对象属性上做 tab 级去重——
        # 每请求新建实例时，实例级 _init_js_id 无法阻止向同一 tab 重复注册 init script。
        existing_id = getattr(self.tab, "_kimi_fetch_init_js_id", None)
        if existing_id:
            self._init_js_id = existing_id
            return

        try:
            self._init_js_id = self.tab.add_init_js(
                self._BOOTSTRAP_JS.strip()
            )
            try:
                setattr(self.tab, "_kimi_fetch_init_js_id", self._init_js_id)
            except Exception:
                pass
            logger.debug(
                f"[Executor] Kimi 页面抓流已注册 document-start 注入: {self._init_js_id}"
            )
        except Exception as e:
            logger.debug(f"[Executor] Kimi document-start 注入失败: {e}")

    def get_state(self, since_length: int = 0) -> Dict[str, Any]:
        # 修复(7a)：增加 sinceLength 增量参数，页面侧只返回 escapedFullText.slice(sinceLength)
        # 与 totalLength，避免每 0.3s 轮询全量回传导致 O(n^2) 传输；
        # 修复(7c)：requestId 作为请求标识返回，Python 侧据此检测页面自动重试换流。
        try:
            since = max(0, int(since_length or 0))
        except Exception:
            since = 0
        state = self.tab.run_js(
            """
            return (function(token, sinceLength) {
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

              const fullText = target ? (target.escapedFullText || "") : "";
              const totalLength = fullText.length;
              const since = Math.max(0, Math.min(Number(sinceLength) || 0, totalLength));

              return {
                installed: true,
                currentToken: cap.currentToken || null,
                found: !!target,
                requestId: target ? (target.id || "") : "",
                escapedDelta: fullText.slice(since),
                totalLength: totalLength,
                complete: !!(target && target.complete),
                error: target ? (target.error || null) : null,
                chunkCount: target ? (target.chunkCount || 0) : 0,
                startedAt: target ? (target.startedAt || 0) : 0,
                lastChunkAt: target ? (target.lastChunkAt || 0) : 0
              };
            })(arguments[0], arguments[1]);
            """,
            self._capture_token or "",
            since,
        )
        return state if isinstance(state, dict) else {}

    def _clear_page_capture_buffer(self) -> None:
        # 修复(7d)：monitor 结束时尽力清空当前 token 对应请求的页面侧缓冲，失败忽略
        try:
            self.tab.run_js(
                """
                return (function(token) {
                  const cap = window.__KIMI_CAPTURE__;
                  if (!cap || !Array.isArray(cap.requests)) {
                    return { ok: false };
                  }
                  let cleared = 0;
                  for (const item of cap.requests) {
                    if ((!token || item.token === token) && item.escapedFullText) {
                      item.escapedFullText = "";
                      cleared += 1;
                    }
                  }
                  return { ok: true, cleared: cleared };
                })(arguments[0]);
                """,
                self._capture_token or "",
            )
        except Exception as e:
            logger.debug(f"[Executor] Kimi 页面抓流缓冲清理失败（忽略）: {e}")

    def monitor(self, completion_id: str) -> Generator[str, None, None]:
        parser = self._parser
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
        seen_request = False
        # 修复(7c)：锁定首个命中的请求标识；标识变化说明页面自动重试换了流
        locked_request_id = ""
        # 修复(7a)：Python 侧按偏移量增量取回并累积转义文本，避免全量回传
        escaped_buffer = ""

        try:
            while True:
                if self._check_cancelled():
                    logger.debug("[Executor] Kimi 页面抓流被取消")
                    break

                now = time.time()
                if now - phase_start > hard_timeout:
                    raise NetworkMonitorError(f"kimi_page_capture_hard_timeout:{hard_timeout:.1f}s")

                state = self.get_state(len(escaped_buffer))
                if not state.get("installed"):
                    raise NetworkMonitorError("kimi_page_capture_not_installed")

                if state.get("error"):
                    raise NetworkMonitorError(f"kimi_page_capture_error:{state.get('error')}")

                request_id = str(state.get("requestId", "") or "")
                if state.get("found"):
                    if not seen_request:
                        logger.debug(
                            "[Executor] Kimi 页面抓流已命中请求 "
                            f"(request_id={state.get('requestId')}, token={self._capture_token})"
                        )
                        seen_request = True
                        locked_request_id = request_id
                    elif locked_request_id and request_id and request_id != locked_request_id:
                        # 修复(7c)：页面自动重试换了流——重置解析状态与偏移，
                        # 从新流头部重新取数（本次按旧偏移取到的增量作废）
                        logger.warning(
                            "[Executor] Kimi 页面抓流请求标识变化，重置解析状态 "
                            f"({locked_request_id} -> {request_id})"
                        )
                        parser.reset()
                        escaped_buffer = ""
                        locked_request_id = request_id
                        state = self.get_state(0)
                        if state.get("error"):
                            raise NetworkMonitorError(f"kimi_page_capture_error:{state.get('error')}")

                delta = str(state.get("escapedDelta", "") or "")
                if delta:
                    escaped_buffer += delta
                    last_activity = now

                if escaped_buffer:
                    parse_result = parser.parse_chunk(escaped_buffer)
                    if parse_result.get("error"):
                        raise NetworkMonitorError(f"kimi_page_capture_parse_error:{parse_result['error']}")

                    content = parse_result.get("content", "")
                    done = bool(parse_result.get("done")) or bool(state.get("complete"))

                    if content:
                        logger.debug(f"[Executor] Kimi 页面抓流产出: {repr(content)[:240]}")
                        self._sent_content_length += len(content)
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
                    logger.warning(
                        "[Executor] Kimi 页面抓流静默超时 "
                        f"({now - last_activity:.1f}s)"
                    )
                    raise NetworkMonitorTimeout(
                        f"kimi_page_capture_silence_timeout:{silence_threshold:.1f}s"
                    )

                time.sleep(max(0.05, response_interval))
        finally:
            # 修复(7d)：monitor 结束（正常/异常/取消）时清空页面侧缓冲，尽力而为
            self._clear_page_capture_buffer()


register_page_fetch_capture(KimiPageFetchCapture)


__all__ = ["KimiPageFetchCapture"]
