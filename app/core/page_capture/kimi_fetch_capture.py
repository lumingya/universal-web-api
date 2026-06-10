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

        try:
            self._init_js_id = self.tab.add_init_js(
                self._BOOTSTRAP_JS.strip()
            )
            logger.debug(
                f"[Executor] Kimi 页面抓流已注册 document-start 注入: {self._init_js_id}"
            )
        except Exception as e:
            logger.debug(f"[Executor] Kimi document-start 注入失败: {e}")

    def get_state(self) -> Dict[str, Any]:
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
            self._capture_token or "",
        )
        return state if isinstance(state, dict) else {}

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
        last_raw_len = 0
        seen_request = False

        while True:
            if self._check_cancelled():
                logger.debug("[Executor] Kimi 页面抓流被取消")
                break

            now = time.time()
            if now - phase_start > hard_timeout:
                raise NetworkMonitorError(f"kimi_page_capture_hard_timeout:{hard_timeout:.1f}s")

            state = self.get_state()
            if not state.get("installed"):
                raise NetworkMonitorError("kimi_page_capture_not_installed")

            if state.get("error"):
                raise NetworkMonitorError(f"kimi_page_capture_error:{state.get('error')}")

            raw_response = str(state.get("escapedFullText", "") or "")
            if state.get("found"):
                if not seen_request:
                    logger.debug(
                        "[Executor] Kimi 页面抓流已命中请求 "
                        f"(request_id={state.get('requestId')}, token={self._capture_token})"
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


register_page_fetch_capture(KimiPageFetchCapture)


__all__ = ["KimiPageFetchCapture"]
