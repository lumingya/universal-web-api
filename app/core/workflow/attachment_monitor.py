"""
Shared attachment state monitoring for file/image uploads.

This module avoids fixed sleeps by observing composer DOM changes,
attachment previews, pending indicators, and send button busy state.
"""

import json
import time
from typing import Any, Callable, Dict, Iterable, Optional

from app.core.config import BrowserConstants, logger


_ATTACHMENT_MONITOR_BOOTSTRAP_JS = r"""
(() => {
  const KEY = "__ATTACHMENT_MONITOR__";
  const W = window;

  const rootSelectors = [
    ".message-input-wrapper",
    ".message-input-container",
    ".chat-layout-input-container",
    "#dropzone-container",
    "rich-textarea",
    "[class*='message-input']",
    "[class*='input-container']",
    "[class*='input-wrapper']",
    "[class*='composer']",
    "[class*='prompt']",
    "form",
  ];

  const attachmentSelectors = [
    ".file-card-list",
    ".fileitem-btn",
    ".fileitem-file-name",
    ".fileitem-file-name-text",
    ".message-input-column-file",
    "[class*='fileitem']",
    "[class*='attachment']",
    "[class*='upload-preview']",
    "[class*='uploaded-file']",
    "[class*='file-preview']",
    "[class*='preview-file']",
    "[class*='image-preview']",
    "[class*='preview']",
    "[class*='chip']",
    "[data-testid*='attachment']",
    "[data-testid*='preview']",
    "[data-testid*='file']",
    "[data-test-id*='attachment']",
    "[data-test-id*='preview']",
    "[data-test-id*='file']",
    "mat-chip",
    "mat-chip-row",
    ".mat-mdc-chip",
    ".mat-mdc-chip-row",
    "img[src^='blob:']",
    "img[src^='data:image']",
  ];

  const pendingSelectors = [
    "progress",
    "[role='progressbar']",
    "[aria-busy='true']",
    "[class*='uploading']",
    "[class*='pending']",
    "[class*='loading']",
    "[class*='progress']",
    "[class*='processing']",
    "[class*='preparing']",
    "[class*='analyzing']",
    "[class*='reading']",
  ];

  const busyWords = [
    "loading",
    "uploading",
    "sending",
    "processing",
    "preparing",
    "analyzing",
    "generating",
    "thinking",
    "\u8bfb\u53d6",
    "\u5206\u6790",
    "\u5904\u7406\u4e2d",
    "\u4e0a\u4f20\u4e2d",
    "\u751f\u6210\u4e2d",
  ];

  function lower(value) {
    return String(value || "").toLowerCase();
  }

  function safeQuery(selector, root) {
    const normalized = String(selector || "").trim();
    if (!normalized) return null;
    try {
      return (root || document).querySelector(normalized);
    } catch (error) {
      return null;
    }
  }

  function isInputLike(node) {
    if (!node || !node.tagName) return false;
    const tag = String(node.tagName || "").toLowerCase();
    return (
      tag === "textarea" ||
      tag === "input" ||
      !!node.isContentEditable ||
      node.getAttribute("contenteditable") === "true"
    );
  }

  function getInputLength(node) {
    if (!node) return 0;
    try {
      const tag = String(node.tagName || "").toLowerCase();
      if (tag === "textarea" || tag === "input") {
        return String(node.value || "").length;
      }
      if (node.isContentEditable || node.getAttribute("contenteditable") === "true") {
        return String(node.innerText || "").length;
      }
      return String(node.textContent || "").length;
    } catch (error) {
      return 0;
    }
  }

  function joinSelectors(items) {
    const cleaned = [];
    for (const item of items || []) {
      const selector = String(item || "").trim();
      if (selector && !cleaned.includes(selector)) {
        cleaned.push(selector);
      }
    }
    return cleaned.join(",");
  }

  function findInput(opts) {
    const inputSelector = String((opts && opts.inputSelector) || "").trim();
    const direct = safeQuery(inputSelector);
    if (direct) return direct;

    const active = document.activeElement;
    if (isInputLike(active)) return active;
    if (active && typeof active.closest === "function") {
      try {
        const nested = active.closest(
          "textarea, input, [contenteditable='true'], [role='textbox']"
        );
        if (nested && isInputLike(nested)) {
          return nested;
        }
      } catch (error) {}
    }

    return null;
  }

  function findSendButton(root, opts) {
    const sendSelector = String((opts && opts.sendSelector) || "").trim();
    const direct = safeQuery(sendSelector);
    if (direct) return direct;

    const fallbackSelectors = [
      "button[type='submit']",
      "button[aria-label*='send' i]",
      "button[data-testid*='send' i]",
      "button[class*='send' i]",
    ];

    for (const selector of fallbackSelectors) {
      const scoped = safeQuery(selector, root || document);
      if (scoped) return scoped;
    }

    return null;
  }

  function findRoot(input, sendBtn) {
    const anchors = [];
    if (input) anchors.push(input);
    if (sendBtn) anchors.push(sendBtn);

    for (const anchor of anchors) {
      if (!anchor || typeof anchor.closest !== "function") continue;
      for (const selector of rootSelectors) {
        try {
          const match = anchor.closest(selector);
          if (match) return match;
        } catch (error) {}
      }
      try {
        const fallback = anchor.closest("form, section, article, main");
        if (fallback) return fallback;
      } catch (error) {}
      if (anchor.parentElement) {
        return anchor.parentElement;
      }
    }

    for (const selector of rootSelectors) {
      const node = safeQuery(selector);
      if (node) return node;
    }

    return document.body;
  }

  function collectState(opts) {
    const input = findInput(opts);
    let sendBtn = findSendButton(null, opts);
    const root = findRoot(input, sendBtn);
    if (!sendBtn) {
      sendBtn = findSendButton(root, opts);
    }

    const attachmentSelector = joinSelectors(attachmentSelectors);
    const pendingSelector = joinSelectors(pendingSelectors);
    const uploadNodes = root ? Array.from(root.querySelectorAll(attachmentSelector)) : [];
    const pendingNodes = root ? Array.from(root.querySelectorAll(pendingSelector)) : [];
    const fileInputs = Array.from(document.querySelectorAll("input[type='file']"));
    const fileInputCount = fileInputs.reduce((sum, inputNode) => {
      try {
        return sum + (((inputNode.files && inputNode.files.length) || 0));
      } catch (error) {
        return sum;
      }
    }, 0);

    const rootText = lower(root && root.innerText);
    const attachmentText = lower(
      uploadNodes
        .map((node) =>
          [
            node.textContent,
            node.getAttribute && node.getAttribute("aria-label"),
            node.getAttribute && node.getAttribute("title"),
            node.getAttribute && node.getAttribute("data-testid"),
            node.getAttribute && node.getAttribute("data-test-id"),
            node.getAttribute && node.getAttribute("alt"),
          ]
            .filter(Boolean)
            .join(" ")
        )
        .join("\n")
    );
    const previewCount = uploadNodes.filter((node) => {
      try {
        if (!node || !node.tagName) return false;
        const tag = String(node.tagName || "").toLowerCase();
        if (tag === "img") {
          const src = String(node.getAttribute("src") || "");
          return src.startsWith("blob:") || src.startsWith("data:image");
        }
        return !!node.querySelector("img[src^='blob:'], img[src^='data:image']");
      } catch (error) {
        return false;
      }
    }).length;

    const fingerprint = uploadNodes
      .slice(0, 24)
      .map((node) => {
        try {
          const tag = lower(node.tagName || "");
          const cls = lower(node.className || "");
          const text = lower(node.textContent || "").slice(0, 48);
          const alt = lower((node.getAttribute && node.getAttribute("alt")) || "");
          const src = lower((node.getAttribute && node.getAttribute("src")) || "").slice(0, 48);
          return [tag, cls, text, alt, src].join("#");
        } catch (error) {
          return "";
        }
      })
      .filter(Boolean)
      .join("|");

    const sendMeta = sendBtn
      ? lower(
          [
            sendBtn.getAttribute("aria-label"),
            sendBtn.getAttribute("title"),
            sendBtn.getAttribute("data-testid"),
            sendBtn.getAttribute("data-test-id"),
            sendBtn.className,
            sendBtn.innerText,
            sendBtn.textContent,
          ].join(" ")
        )
      : "";

    const sendDisabled = !!sendBtn && (
      !!sendBtn.disabled || sendBtn.getAttribute("aria-disabled") === "true"
    );
    const sendBusy = !!sendBtn && (
      sendBtn.getAttribute("aria-busy") === "true" ||
      busyWords.some((word) => sendMeta.includes(lower(word)))
    );

    const pendingText = busyWords.some((word) => rootText.includes(lower(word)));

    return {
      ok: true,
      rootFound: !!root,
      inputFound: !!input,
      sendFound: !!sendBtn,
      attachmentCount: uploadNodes.length,
      previewCount,
      fileInputCount,
      pendingCount: pendingNodes.length,
      pendingText,
      sendDisabled,
      sendBusy,
      inputLength: getInputLength(input),
      rootText,
      attachmentText,
      attachmentFingerprint: fingerprint,
    };
  }

  function evaluate(state, baseline, expectedNames, mutationCount) {
    const expected = Array.isArray(expectedNames)
      ? expectedNames.map((item) => lower(item)).filter(Boolean)
      : [];
    const matchedExpectedName = expected.some(
      (needle) =>
        needle &&
        (state.attachmentText.includes(needle) || state.rootText.includes(needle))
    );

    const attachmentChanged =
      state.attachmentCount > baseline.attachmentCount ||
      state.previewCount > baseline.previewCount ||
      state.fileInputCount > baseline.fileInputCount ||
      state.attachmentFingerprint !== baseline.attachmentFingerprint;

    const pendingChanged =
      state.pendingCount > baseline.pendingCount ||
      (!!state.pendingText && !baseline.pendingText);

    const sendTransition =
      state.sendDisabled !== baseline.sendDisabled ||
      state.sendBusy !== baseline.sendBusy;

    const attachmentObserved =
      attachmentChanged ||
      matchedExpectedName ||
      ((mutationCount || 0) > 0 &&
        (pendingChanged || sendTransition || state.attachmentCount > 0 || state.previewCount > 0));

    return {
      matchedExpectedName,
      attachmentChanged,
      pendingChanged,
      sendTransition,
      attachmentObserved,
    };
  }

  const monitor = (W[KEY] = W[KEY] || {});

  monitor.disconnect = function() {
    if (monitor.observer) {
      try {
        monitor.observer.disconnect();
      } catch (error) {}
    }
    monitor.observer = null;
    monitor.root = null;
    monitor.send = null;
  };

  monitor.ensure = function() {
    return true;
  };

  monitor.begin = function(opts) {
    monitor.disconnect();
    monitor.options = Object.assign({}, opts || {});
    monitor.startedAt = Date.now();
    monitor.mutationCount = 0;
    monitor.lastMutationAt = monitor.startedAt;
    monitor.lastMutationSummary = "";

    const current = collectState(monitor.options);
    monitor.baseline = current;
    monitor.root = findRoot(findInput(monitor.options), findSendButton(null, monitor.options));
    monitor.send = findSendButton(monitor.root, monitor.options);

    if (monitor.root && typeof MutationObserver === "function") {
      monitor.observer = new MutationObserver((mutations) => {
        monitor.mutationCount += mutations.length;
        monitor.lastMutationAt = Date.now();
        monitor.lastMutationSummary = mutations
          .slice(0, 4)
          .map((item) => item.type || "")
          .join(",");
      });

      try {
        monitor.observer.observe(monitor.root, {
          subtree: true,
          childList: true,
          characterData: true,
          attributes: true,
          attributeFilter: ["class", "style", "aria-busy", "aria-disabled", "disabled", "src"],
        });
      } catch (error) {}

      if (monitor.send && monitor.send !== monitor.root) {
        try {
          monitor.observer.observe(monitor.send, {
            subtree: false,
            childList: false,
            characterData: true,
            attributes: true,
            attributeFilter: ["class", "style", "aria-busy", "aria-disabled", "disabled", "title"],
          });
        } catch (error) {}
      }
    }

    return monitor.snapshot(opts);
  };

  monitor.snapshot = function(opts) {
    const effective = Object.assign({}, monitor.options || {}, opts || {});
    const state = collectState(effective);
    const baseline = monitor.baseline || state;
    const mutationCount = Number(monitor.mutationCount || 0);
    const derived = evaluate(state, baseline, effective.expectedNames || [], mutationCount);
    const now = Date.now();

    return Object.assign({}, state, derived, {
      baselineAttachmentCount: Number(baseline.attachmentCount || 0),
      baselinePreviewCount: Number(baseline.previewCount || 0),
      baselineFileInputCount: Number(baseline.fileInputCount || 0),
      mutationCount,
      idleMs: Math.max(0, now - Number(monitor.lastMutationAt || now)),
      sinceStartMs: Math.max(0, now - Number(monitor.startedAt || now)),
      lastMutationSummary: String(monitor.lastMutationSummary || ""),
    });
  };

  return true;
})();
"""


class AttachmentMonitor:
    """Cross-site attachment state tracking based on DOM signals."""

    def __init__(
        self,
        tab,
        selectors: Optional[Dict[str, Any]] = None,
        check_cancelled_fn: Optional[Callable[[], bool]] = None,
    ):
        self.tab = tab
        self._selectors = selectors or {}
        self._check_cancelled = check_cancelled_fn or (lambda: False)

    def _selector_value(self, key: str) -> str:
        value = self._selectors.get(key)
        return str(value).strip() if value else ""

    def _run_js(self, script: str):
        try:
            return self.tab.run_js(script)
        except Exception as exc:
            logger.debug(f"[ATTACHMENT] JS execution failed: {exc}")
            return None

    def ensure_installed(self) -> bool:
        result = self._run_js(f"return {_ATTACHMENT_MONITOR_BOOTSTRAP_JS.strip()};")
        return bool(result)

    def _build_options(self, expected_names: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        names = []
        for item in expected_names or []:
            value = str(item or "").strip()
            if value:
                names.append(value)
        return {
            "inputSelector": self._selector_value("input_box"),
            "sendSelector": self._selector_value("send_btn"),
            "expectedNames": names,
        }

    def begin_tracking(self, expected_names: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        if not self.ensure_installed():
            return {}
        options = json.dumps(self._build_options(expected_names), ensure_ascii=False)
        result = self._run_js(
            f"return (window.__ATTACHMENT_MONITOR__ && window.__ATTACHMENT_MONITOR__.begin({options})) || null;"
        )
        return result if isinstance(result, dict) else {}

    def snapshot(self, expected_names: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        if not self.ensure_installed():
            return {}
        options = json.dumps(self._build_options(expected_names), ensure_ascii=False)
        result = self._run_js(
            f"return (window.__ATTACHMENT_MONITOR__ && window.__ATTACHMENT_MONITOR__.snapshot({options})) || null;"
        )
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _is_ready_state(state: Dict[str, Any], require_send_enabled: bool) -> bool:
        pending = int(state.get("pendingCount", 0) or 0) > 0 or bool(state.get("pendingText"))
        if pending or bool(state.get("sendBusy")):
            return False
        if require_send_enabled and bool(state.get("sendFound")) and bool(state.get("sendDisabled")):
            return False
        return True

    @staticmethod
    def _has_meaningful_progress(previous: Dict[str, Any], current: Dict[str, Any]) -> bool:
        """Whether the attachment flow is still making observable progress."""
        if not previous:
            return bool(current)
        if not current:
            return False

        comparable_keys = (
            "attachmentCount",
            "previewCount",
            "fileInputCount",
            "pendingCount",
            "pendingText",
            "sendDisabled",
            "sendBusy",
            "matchedExpectedName",
            "attachmentChanged",
            "pendingChanged",
            "sendTransition",
            "attachmentObserved",
            "attachmentFingerprint",
            "rootText",
            "attachmentText",
        )
        for key in comparable_keys:
            if previous.get(key) != current.get(key):
                return True

        try:
            if int(current.get("mutationCount", 0) or 0) > int(previous.get("mutationCount", 0) or 0):
                return True
        except Exception:
            return True

        return False

    @staticmethod
    def summarize(state: Dict[str, Any]) -> str:
        if not state:
            return "no_state"
        return (
            f"attachments={int(state.get('attachmentCount', 0) or 0)}, "
            f"previews={int(state.get('previewCount', 0) or 0)}, "
            f"file_inputs={int(state.get('fileInputCount', 0) or 0)}, "
            f"pending={int(state.get('pendingCount', 0) or 0)}, "
            f"pending_text={bool(state.get('pendingText'))}, "
            f"send_disabled={bool(state.get('sendDisabled'))}, "
            f"send_busy={bool(state.get('sendBusy'))}, "
            f"observed={bool(state.get('attachmentObserved'))}, "
            f"mutations={int(state.get('mutationCount', 0) or 0)}, "
            f"idle_ms={int(state.get('idleMs', 0) or 0)}"
        )

    def wait_until_ready(
        self,
        expected_names: Optional[Iterable[str]] = None,
        *,
        require_observed: bool = True,
        require_send_enabled: bool = False,
        accept_existing: bool = False,
        start_new_tracking: bool = True,
        max_wait: Optional[float] = None,
        poll_interval: Optional[float] = None,
        stable_window: Optional[float] = None,
        label: str = "attachment",
    ) -> Dict[str, Any]:
        wait_timeout = float(max_wait or getattr(BrowserConstants, "ATTACHMENT_READY_MAX_WAIT", 20.0))
        check_interval = float(
            poll_interval or getattr(BrowserConstants, "ATTACHMENT_READY_CHECK_INTERVAL", 0.25)
        )
        settle_window = float(
            stable_window or getattr(BrowserConstants, "ATTACHMENT_READY_STABLE_WINDOW", 0.8)
        )
        idle_timeout = float(
            getattr(BrowserConstants, "ATTACHMENT_READY_IDLE_TIMEOUT", max(wait_timeout, 8.0))
        )
        hard_max_wait = float(
            getattr(
                BrowserConstants,
                "ATTACHMENT_READY_HARD_MAX_WAIT",
                max(wait_timeout * 3.0, wait_timeout + 30.0),
            )
        )
        hard_max_wait = max(wait_timeout, hard_max_wait)

        state = self.begin_tracking(expected_names) if start_new_tracking else self.snapshot(expected_names)
        if not state:
            return {
                "success": False,
                "attachmentObserved": False,
                "activitySeen": False,
                "reason": "monitor_unavailable",
            }

        start = time.time()
        stable_since = None
        observed_once = bool(state.get("attachmentObserved"))
        activity_seen = observed_once or int(state.get("mutationCount", 0) or 0) > 0
        last_state = state
        last_progress_at = start
        idle_deadline = start + max(0.5, idle_timeout)
        hard_deadline = start + max(0.5, hard_max_wait)
        activity_deadline = start + max(0.5, wait_timeout)

        while time.time() <= hard_deadline:
            if self._check_cancelled():
                break

            previous_state = dict(last_state or {})
            state = self.snapshot(expected_names)
            if state:
                last_state = state

            now = time.time()
            progress = self._has_meaningful_progress(previous_state, last_state)
            if progress:
                last_progress_at = now
                idle_deadline = min(hard_deadline, now + max(0.5, idle_timeout))
                if activity_seen or bool(last_state.get("attachmentObserved")):
                    activity_deadline = min(hard_deadline, now + max(0.5, wait_timeout))

            observed = bool(last_state.get("attachmentObserved"))
            if observed:
                observed_once = True
                activity_deadline = min(hard_deadline, now + max(0.5, wait_timeout))

            activity_seen = activity_seen or observed or bool(last_state.get("pendingChanged")) or bool(
                last_state.get("sendTransition")
            ) or int(last_state.get("mutationCount", 0) or 0) > 0

            active_pending = (
                int(last_state.get("pendingCount", 0) or 0) > 0
                or bool(last_state.get("pendingText"))
                or bool(last_state.get("sendBusy"))
            )

            if active_pending and (observed_once or activity_seen):
                last_progress_at = now
                idle_deadline = min(hard_deadline, now + max(0.5, idle_timeout))

            if activity_seen and not progress:
                if active_pending:
                    activity_deadline = min(hard_deadline, now + max(0.5, check_interval * 2.0))

            ready = self._is_ready_state(last_state, require_send_enabled=require_send_enabled)
            attachment_present = (
                int(last_state.get("attachmentCount", 0) or 0) > 0
                or int(last_state.get("previewCount", 0) or 0) > 0
                or int(last_state.get("fileInputCount", 0) or 0) > 0
                or bool(last_state.get("matchedExpectedName"))
            )
            gate_ok = observed_once or (accept_existing and attachment_present) or not require_observed

            if gate_ok and ready:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= settle_window:
                    result = dict(last_state)
                    result.update(
                        {
                            "success": True,
                            "attachmentObserved": observed_once,
                            "activitySeen": activity_seen,
                            "reason": "ready",
                        }
                    )
                    logger.debug(
                        f"[ATTACHMENT] {label} ready after {time.time() - start:.1f}s: {self.summarize(result)}"
                    )
                    return result
            else:
                stable_since = None

            if now >= idle_deadline:
                break

            if now >= activity_deadline and not progress:
                break

            remaining = min(idle_deadline, activity_deadline, hard_deadline) - now
            if remaining <= 0:
                break
            time.sleep(min(check_interval, remaining))

        result = dict(last_state or {})
        result.update(
            {
                "success": False,
                "attachmentObserved": observed_once,
                "activitySeen": activity_seen,
                "reason": "idle_timeout" if time.time() >= idle_deadline else "timeout",
                "waitedSeconds": round(max(0.0, time.time() - start), 3),
                "idleSeconds": round(max(0.0, time.time() - last_progress_at), 3),
            }
        )
        logger.warning(
            f"[ATTACHMENT] {label} not ready after {time.time() - start:.1f}s: {self.summarize(result)} "
            f"(reason={result.get('reason')}, idle={result.get('idleSeconds')})"
        )
        return result


__all__ = ["AttachmentMonitor"]
