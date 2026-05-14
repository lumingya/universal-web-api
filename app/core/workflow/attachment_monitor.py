"""
Shared attachment state monitoring for file/image uploads.

This module avoids fixed sleeps by observing composer DOM changes,
attachment previews, pending indicators, and send button busy state.
"""

import copy
import json
import time
from typing import Any, Callable, Dict, Iterable, Optional

from app.core.config import BrowserConstants, logger


_ATTACHMENT_MONITOR_BOOTSTRAP_JS = r"""
(() => {
  const KEY = "__ATTACHMENT_MONITOR__";
  const W = window;

  const defaultRootSelectors = [
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

  const defaultAttachmentSelectors = [
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

  const defaultPendingSelectors = [
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

  const defaultBusyWords = [
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

  function getInputText(node) {
    if (!node) return "";
    try {
      const tag = String(node.tagName || "").toLowerCase();
      if (tag === "textarea" || tag === "input") {
        return String(node.value || "");
      }
      if (node.isContentEditable || node.getAttribute("contenteditable") === "true") {
        return String(node.innerText || node.textContent || "");
      }
      return String(node.textContent || "");
    } catch (error) {
      return "";
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

  function mergeUnique(defaultItems, extraItems) {
    const merged = [];
    for (const source of [defaultItems || [], extraItems || []]) {
      for (const item of source) {
        const value = String(item || "").trim();
        if (value && !merged.includes(value)) {
          merged.push(value);
        }
      }
    }
    return merged;
  }

  function prioritizeUnique(primaryItems, fallbackItems) {
    const merged = [];
    for (const source of [primaryItems || [], fallbackItems || []]) {
      for (const item of source) {
        const value = String(item || "").trim();
        if (value && !merged.includes(value)) {
          merged.push(value);
        }
      }
    }
    return merged;
  }

  function includesAny(text, items) {
    const haystack = lower(text);
    return (items || []).some((item) => {
      const needle = lower(item);
      return needle && haystack.includes(needle);
    });
  }

  function compactText(value, maxLen) {
    const normalized = String(value || "").replace(/\s+/g, " ").trim();
    if (!normalized) return "";
    const limit = Math.max(16, Number(maxLen || 0) || 120);
    return normalized.length > limit ? normalized.slice(0, limit) + "…" : normalized;
  }

  function describeNode(node) {
    if (!node || !node.tagName) return "";
    const parts = [lower(node.tagName || "")];
    const cls = compactText(node.className || "", 80);
    const aria = compactText(node.getAttribute && node.getAttribute("aria-label"), 60);
    const role = compactText(node.getAttribute && node.getAttribute("role"), 40);
    const title = compactText(node.getAttribute && node.getAttribute("title"), 60);
    if (cls) parts.push("class=" + cls);
    if (aria) parts.push("aria=" + aria);
    if (role) parts.push("role=" + role);
    if (title) parts.push("title=" + title);
    return parts.join("|");
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

  function findRoot(input, sendBtn, rootSelectors) {
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

  function getRootStatusText(root, input) {
    if (!root) return "";
    try {
      const clone = root.cloneNode(true);
      const removalSelectors = [
        "textarea",
        "input",
        "[contenteditable='true']",
        "[role='textbox']",
        ".ql-editor",
      ];
      for (const selector of removalSelectors) {
        try {
          clone.querySelectorAll(selector).forEach((node) => node.remove());
        } catch (error) {}
      }
      const text = String(clone.innerText || clone.textContent || "");
      if (text.trim()) return text;
    } catch (error) {}

    const raw = String((root && (root.innerText || root.textContent)) || "");
    const inputText = getInputText(input).trim();
    if (!inputText) return raw;
    return raw.replace(inputText, " ");
  }

  function collectState(opts) {
    const rootSelectors = prioritizeUnique(opts && opts.rootSelectors, defaultRootSelectors);
    const useDefaultAttachmentSelectors = !(opts && opts.useDefaultAttachmentSelectors === false);
    const attachmentSelectors = useDefaultAttachmentSelectors
      ? mergeUnique(defaultAttachmentSelectors, opts && opts.attachmentSelectors)
      : prioritizeUnique(opts && opts.attachmentSelectors, []);
    const pendingSelectors = mergeUnique(defaultPendingSelectors, opts && opts.pendingSelectors);
    const busyWords = mergeUnique(defaultBusyWords, opts && opts.busyTextMarkers);
    const disabledMarkers = mergeUnique(
      ["disabled", "unavailable", "inactive", "readonly", "upload failed"],
      opts && opts.sendButtonDisabledMarkers
    );
    const input = findInput(opts);
    let sendBtn = findSendButton(null, opts);
    const root = findRoot(input, sendBtn, rootSelectors);
    if (!sendBtn) {
      sendBtn = findSendButton(root, opts);
    }

    const attachmentSelector = joinSelectors(attachmentSelectors);
    const pendingSelector = joinSelectors(pendingSelectors);
    const uploadNodes = (root && attachmentSelector) ? Array.from(root.querySelectorAll(attachmentSelector)) : [];
    const pendingNodes = (root && pendingSelector) ? Array.from(root.querySelectorAll(pendingSelector)) : [];
    const fileInputs = Array.from(document.querySelectorAll("input[type='file']"));
    const fileInputCount = fileInputs.reduce((sum, inputNode) => {
      try {
        return sum + (((inputNode.files && inputNode.files.length) || 0));
      } catch (error) {
        return sum;
      }
    }, 0);

    const rawRootText = getRootStatusText(root, input);
    const rootText = lower(rawRootText);
    const rawAttachmentText = uploadNodes
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
      .join("\n");
    const attachmentText = lower(rawAttachmentText);
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

    const matchedBusyWords = busyWords.filter((word) => {
      const needle = lower(word);
      return needle && rootText.includes(needle);
    }).slice(0, 8);

    const matchedDisabledMarkers = disabledMarkers.filter((word) => {
      const needle = lower(word);
      return needle && sendMeta.includes(needle);
    }).slice(0, 8);

    const sendDisabled = !!sendBtn && (
      !!sendBtn.disabled
      || sendBtn.getAttribute("aria-disabled") === "true"
      || matchedDisabledMarkers.length > 0
    );
    const sendBusy = !!sendBtn && (
      sendBtn.getAttribute("aria-busy") === "true" ||
      includesAny(sendMeta, busyWords)
    );

    const pendingText = matchedBusyWords.length > 0;

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
      rootSummary: describeNode(root),
      inputSummary: describeNode(input),
      sendSummary: describeNode(sendBtn),
      rootTextSample: compactText(rawRootText, 160),
      attachmentTextSample: compactText(rawAttachmentText, 160),
      matchedBusyWords,
      matchedDisabledMarkers,
      pendingNodeSummary: pendingNodes.slice(0, 4).map(describeNode).filter(Boolean),
      attachmentNodeSummary: uploadNodes.slice(0, 6).map(describeNode).filter(Boolean),
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

    const observationReasons = [];
    if (attachmentChanged) observationReasons.push("attachment_changed");
    if (matchedExpectedName) observationReasons.push("expected_name_matched");
    if (pendingChanged) observationReasons.push("pending_changed");
    if (sendTransition) observationReasons.push("send_transition");
    if ((mutationCount || 0) > 0) observationReasons.push("mutation:" + String(mutationCount));

    return {
      matchedExpectedName,
      attachmentChanged,
      pendingChanged,
      sendTransition,
      attachmentObserved,
      observationReasons,
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
    monitor.root = findRoot(
      findInput(monitor.options),
      findSendButton(null, monitor.options),
      prioritizeUnique(monitor.options && monitor.options.rootSelectors, defaultRootSelectors)
    );
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
        config: Optional[Dict[str, Any]] = None,
        check_cancelled_fn: Optional[Callable[[], bool]] = None,
    ):
        self.tab = tab
        self._selectors = selectors or {}
        self._config = config or {}
        self._check_cancelled = check_cancelled_fn or (lambda: False)

    def _selector_value(self, key: str) -> str:
        value = self._selectors.get(key)
        return str(value).strip() if value else ""

    def _config_list(self, key: str):
        raw_value = self._config.get(key) if isinstance(self._config, dict) else None
        if not isinstance(raw_value, list):
            return []
        cleaned = []
        for item in raw_value:
            value = str(item or "").strip()
            if value and value not in cleaned:
                cleaned.append(value)
        return cleaned

    def _config_flag(self, key: str, default: bool = False) -> bool:
        raw_value = self._config.get(key, default) if isinstance(self._config, dict) else default
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            lowered = raw_value.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return True
            if lowered in ("0", "false", "no", "off"):
                return False
        return bool(raw_value)

    def _config_float(self, key: str, default: float) -> float:
        raw_value = self._config.get(key, default) if isinstance(self._config, dict) else default
        try:
            return float(raw_value)
        except Exception:
            return float(default)

    def _config_dict(self, key: str) -> Dict[str, Any]:
        raw_value = self._config.get(key) if isinstance(self._config, dict) else None
        return copy.deepcopy(raw_value) if isinstance(raw_value, dict) else {}

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
            "rootSelectors": self._config_list("root_selectors"),
            "useDefaultAttachmentSelectors": self._config_flag("use_default_attachment_selectors", True),
            "attachmentSelectors": self._config_list("attachment_selectors"),
            "pendingSelectors": self._config_list("pending_selectors"),
            "busyTextMarkers": self._config_list("busy_text_markers"),
            "sendButtonDisabledMarkers": self._config_list("send_button_disabled_markers"),
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

    def run_state_probe(self, state: Optional[Dict[str, Any]] = None, stage: str = "") -> Dict[str, Any]:
        probe_config = self._config_dict("state_probe")
        if not probe_config or not bool(probe_config.get("enabled")):
            return {
                "enabled": False,
                "ok": False,
                "hit": False,
                "result": {},
                "summary": "",
            }

        code = str(probe_config.get("code") or "").strip()
        if not code:
            return {
                "enabled": True,
                "ok": False,
                "hit": False,
                "result": {},
                "summary": "empty_probe_code",
            }

        payload = {
            "stage": str(stage or "").strip(),
            "monitorState": state if isinstance(state, dict) else {},
        }
        try:
            result = self.tab.run_js(code, payload)
        except Exception as exc:
            message = str(exc)
            logger.debug(f"[ATTACHMENT] state probe failed ({stage or 'unknown'}): {message}")
            return {
                "enabled": True,
                "ok": False,
                "hit": False,
                "result": {},
                "summary": message[:240],
            }

        parsed = result if isinstance(result, dict) else {"value": result}
        summary = str(
            parsed.get("summary", parsed.get("reason", parsed.get("value", ""))) or ""
        ).strip()
        hit = False
        if isinstance(parsed, dict):
            if "hit" in parsed:
                hit = bool(parsed.get("hit"))
            elif any(key in parsed for key in ("accepted", "ready", "uploading", "retry")):
                hit = any(bool(parsed.get(key)) for key in ("accepted", "ready", "uploading", "retry"))
            else:
                hit = bool(parsed)

        return {
            "enabled": True,
            "ok": True,
            "hit": hit,
            "result": parsed,
            "summary": summary[:240],
        }

    @classmethod
    def derive_phase_flags(
        cls,
        state: Dict[str, Any],
        *,
        require_send_enabled: bool = False,
        require_attachment_present: bool = False,
        require_upload_signal_before_ready: bool = False,
    ) -> Dict[str, Any]:
        safe_state = dict(state or {})
        attachment_present = cls._attachment_present(safe_state)
        upload_started = bool(
            safe_state.get("attachmentObserved")
            or safe_state.get("attachmentChanged")
            or safe_state.get("pendingChanged")
            or safe_state.get("sendTransition")
            or int(safe_state.get("mutationCount", 0) or 0) > 0
            or attachment_present
        )
        uploading = bool(
            int(safe_state.get("pendingCount", 0) or 0) > 0
            or bool(safe_state.get("pendingText"))
            or bool(safe_state.get("sendBusy"))
        )
        ready = cls._is_ready_state(safe_state, require_send_enabled=require_send_enabled)

        if require_attachment_present and not attachment_present:
            ready = False
        if require_upload_signal_before_ready and not upload_started:
            ready = False

        return {
            "attachment_present": attachment_present,
            "upload_started": upload_started,
            "uploading": uploading,
            "upload_ready": bool(ready),
        }

    @staticmethod
    def _is_ready_state(state: Dict[str, Any], require_send_enabled: bool) -> bool:
        pending_count = int(state.get("pendingCount", 0) or 0)
        pending_text = bool(state.get("pendingText"))
        attachment_present = AttachmentMonitor._attachment_present(state)
        corroborated_pending_text = pending_text and (
            pending_count > 0
            or bool(state.get("sendBusy"))
            or bool(state.get("sendDisabled"))
            or attachment_present
        )
        pending = pending_count > 0 or corroborated_pending_text
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
        phase_flags = AttachmentMonitor.derive_phase_flags(state)
        parts = [
            f"attachments={int(state.get('attachmentCount', 0) or 0)}, "
            f"previews={int(state.get('previewCount', 0) or 0)}, "
            f"file_inputs={int(state.get('fileInputCount', 0) or 0)}, "
            f"pending={int(state.get('pendingCount', 0) or 0)}, "
            f"pending_text={bool(state.get('pendingText'))}, "
            f"send_disabled={bool(state.get('sendDisabled'))}, "
            f"send_busy={bool(state.get('sendBusy'))}, "
            f"observed={bool(state.get('attachmentObserved'))}, "
            f"upload_started={bool(phase_flags.get('upload_started'))}, "
            f"upload_ready={bool(phase_flags.get('upload_ready'))}, "
            f"mutations={int(state.get('mutationCount', 0) or 0)}, "
            f"idle_ms={int(state.get('idleMs', 0) or 0)}"
        ]

        matched_busy = state.get("matchedBusyWords") or []
        if matched_busy:
            parts.append(f"busy_words={matched_busy}")

        matched_disabled = state.get("matchedDisabledMarkers") or []
        if matched_disabled:
            parts.append(f"disabled_markers={matched_disabled}")

        pending_nodes = state.get("pendingNodeSummary") or []
        if pending_nodes:
            parts.append(f"pending_nodes={pending_nodes}")

        attachment_nodes = state.get("attachmentNodeSummary") or []
        if attachment_nodes:
            parts.append(f"attachment_nodes={attachment_nodes}")

        observed_by = state.get("observationReasons") or []
        if observed_by:
            parts.append(f"observed_by={observed_by}")

        mutation_types = str(state.get("lastMutationSummary") or "").strip()
        if mutation_types:
            parts.append(f"mutation_types={mutation_types}")

        root_text = str(state.get("rootTextSample") or "").strip()
        if root_text and (bool(state.get("pendingText")) or int(state.get("attachmentCount", 0) or 0) == 0):
            parts.append(f"root_text={root_text!r}")

        send_summary = str(state.get("sendSummary") or "").strip()
        if send_summary:
            parts.append(f"send={send_summary!r}")

        root_summary = str(state.get("rootSummary") or "").strip()
        if root_summary:
            parts.append(f"root={root_summary!r}")

        return ", ".join(parts)

    @classmethod
    def explain_not_ready(
        cls,
        state: Dict[str, Any],
        *,
        require_observed: bool,
        require_send_enabled: bool,
        require_attachment_present: bool,
        require_upload_signal_before_ready: bool,
        observed_once: bool,
        accept_existing: bool,
    ) -> str:
        if not state:
            return "no_state"

        reasons = []
        phase_flags = cls.derive_phase_flags(
            state,
            require_send_enabled=require_send_enabled,
            require_attachment_present=require_attachment_present,
            require_upload_signal_before_ready=require_upload_signal_before_ready,
        )
        attachment_present = bool(phase_flags.get("attachment_present"))

        if require_attachment_present and not attachment_present:
            reasons.append("attachment_missing")

        if require_upload_signal_before_ready and not bool(phase_flags.get("upload_started")):
            reasons.append("upload_not_started")

        if require_observed and not (observed_once or (accept_existing and attachment_present)):
            reasons.append("attachment_not_observed")

        if int(state.get("pendingCount", 0) or 0) > 0:
            reasons.append("pending_nodes_present")

        if bool(state.get("pendingText")):
            busy_words = state.get("matchedBusyWords") or []
            pending_text_blocks = (
                int(state.get("pendingCount", 0) or 0) > 0
                or bool(state.get("sendBusy"))
                or bool(state.get("sendDisabled"))
                or attachment_present
            )
            prefix = "pending_text_blocking" if pending_text_blocks else "pending_text_raw"
            if busy_words:
                reasons.append(f"{prefix}:{busy_words}")
            else:
                reasons.append(prefix)

        if bool(state.get("sendBusy")):
            reasons.append("send_busy")

        if require_send_enabled and bool(state.get("sendFound")) and bool(state.get("sendDisabled")):
            disabled_markers = state.get("matchedDisabledMarkers") or []
            if disabled_markers:
                reasons.append(f"send_disabled:{disabled_markers}")
            else:
                reasons.append("send_disabled")

        if not reasons:
            reasons.append("not_stable_yet")

        return "; ".join(reasons)

    @staticmethod
    def _attachment_present(state: Dict[str, Any]) -> bool:
        return (
            int(state.get("attachmentCount", 0) or 0) > 0
            or int(state.get("previewCount", 0) or 0) > 0
            or int(state.get("fileInputCount", 0) or 0) > 0
            or bool(state.get("matchedExpectedName"))
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
        require_attachment_present: Optional[bool] = None,
        require_upload_signal_before_ready: Optional[bool] = None,
        idle_timeout: Optional[float] = None,
        hard_max_wait: Optional[float] = None,
        label: str = "attachment",
    ) -> Dict[str, Any]:
        wait_timeout = float(max_wait or getattr(BrowserConstants, "ATTACHMENT_READY_MAX_WAIT", 20.0))
        check_interval = float(
            poll_interval or getattr(BrowserConstants, "ATTACHMENT_READY_CHECK_INTERVAL", 0.25)
        )
        settle_window = float(
            stable_window or getattr(BrowserConstants, "ATTACHMENT_READY_STABLE_WINDOW", 0.8)
        )
        if idle_timeout is None:
            idle_timeout = float(
                self._config_float(
                    "idle_timeout",
                    getattr(BrowserConstants, "ATTACHMENT_READY_IDLE_TIMEOUT", max(wait_timeout, 8.0)),
                )
            )
        else:
            idle_timeout = float(idle_timeout)
        if hard_max_wait is None:
            hard_max_wait = float(
                self._config_float(
                    "hard_max_wait",
                    getattr(
                        BrowserConstants,
                        "ATTACHMENT_READY_HARD_MAX_WAIT",
                        max(wait_timeout * 3.0, wait_timeout + 30.0),
                    ),
                )
            )
        else:
            hard_max_wait = float(hard_max_wait)
        hard_max_wait = max(wait_timeout, hard_max_wait)
        require_attachment_present = (
            self._config_flag("require_attachment_present", False)
            if require_attachment_present is None
            else bool(require_attachment_present)
        )
        require_upload_signal_before_ready = (
            self._config_flag("require_upload_signal_before_ready", False)
            if require_upload_signal_before_ready is None
            else bool(require_upload_signal_before_ready)
        )

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

            phase_flags = self.derive_phase_flags(
                last_state,
                require_send_enabled=require_send_enabled,
                require_attachment_present=require_attachment_present,
                require_upload_signal_before_ready=require_upload_signal_before_ready,
            )
            ready = bool(phase_flags.get("upload_ready"))
            attachment_present = bool(phase_flags.get("attachment_present"))
            upload_started = bool(phase_flags.get("upload_started"))
            presence_ok = attachment_present or not require_attachment_present
            gate_ok = presence_ok and (
                observed_once or (accept_existing and attachment_present) or not require_observed
            )
            if require_upload_signal_before_ready and not upload_started:
                gate_ok = False

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
        blockers = self.explain_not_ready(
            result,
            require_observed=require_observed,
            require_send_enabled=require_send_enabled,
            require_attachment_present=require_attachment_present,
            require_upload_signal_before_ready=require_upload_signal_before_ready,
            observed_once=observed_once,
            accept_existing=accept_existing,
        )
        logger.warning(
            f"[ATTACHMENT] {label} not ready after {time.time() - start:.1f}s: {self.summarize(result)} "
            f"(reason={result.get('reason')}, idle={result.get('idleSeconds')}, blockers={blockers})"
        )
        return result


__all__ = ["AttachmentMonitor"]
