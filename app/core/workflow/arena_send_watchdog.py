"""Arena-only confirmation gate for the first matching stream request.

The network listener is started before the send action.  This module owns the
period between that action and the normal ``NetworkMonitor.monitor()`` call so
that Arena's first-target deadline starts only after the page proves the send
was accepted.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.parse import urlparse

from app.core.config import logger


ARENA_SEND_NO_TARGET_AFTER_RETRY = "arena_send_no_target_after_retry"
ARENA_NATIVE_STOP_SELECTOR = 'button[aria-label="Stop generation"]'
ARENA_UPLOAD_CARD_SELECTOR = (
    'div.group.relative.overflow-hidden:has(img[src^="blob:"])'
    ':has(button[aria-label="Remove file"]):has(svg.animate-spin)'
)


class ArenaConfirmedSendNoTarget(RuntimeError):
    """Arena accepted the send but no matching target stream arrived in time."""


class ArenaSendUnconfirmed(RuntimeError):
    """The page never supplied enough evidence that the message was accepted."""


class ArenaSendWatchdogCancelled(RuntimeError):
    """The request was cancelled or interrupted while waiting for confirmation."""


class ArenaSendRetryRefreshError(RuntimeError):
    """The required refresh after an Arena watchdog failure did not recover."""


def is_arena_page_url(url: Any) -> bool:
    """Match only arena.ai and its subdomains, never legacy lmarena.ai."""
    try:
        hostname = (urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        return False
    return hostname == "arena.ai" or hostname.endswith(".arena.ai")


@dataclass(frozen=True)
class ArenaSendSnapshot:
    document_ready: bool = False
    input_visible: bool = False
    input_empty: bool = False
    stop_visible: bool = False
    upload_in_progress: bool = False
    page_error: bool = False

    @property
    def confirms_send(self) -> bool:
        return (
            self.document_ready
            and self.input_visible
            and self.input_empty
            and self.stop_visible
            and not self.upload_in_progress
            and not self.page_error
        )


class ArenaSendWatchdog:
    """Wait for strict Arena send evidence before applying a fixed 12s deadline."""

    POLL_INTERVAL_SECONDS = 0.2
    CONFIRMED_TARGET_TIMEOUT_SECONDS = 12.0
    UNCONFIRMED_SEND_TIMEOUT_SECONDS = 90.0

    def __init__(
        self,
        *,
        tab: Any,
        network_monitor: Any,
        selectors: Optional[Mapping[str, Any]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        workflow_interrupted: Optional[Callable[[], bool]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
        confirmed_target_timeout_seconds: float = CONFIRMED_TARGET_TIMEOUT_SECONDS,
        unconfirmed_send_timeout_seconds: float = UNCONFIRMED_SEND_TIMEOUT_SECONDS,
    ) -> None:
        self.tab = tab
        self.network_monitor = network_monitor
        self.selectors = dict(selectors or {})
        self.should_stop = should_stop or (lambda: False)
        self.workflow_interrupted = workflow_interrupted or (lambda: False)
        self.clock = clock
        self.sleep = sleep
        self.poll_interval_seconds = max(0.01, float(poll_interval_seconds))
        self.confirmed_target_timeout_seconds = max(
            0.01, float(confirmed_target_timeout_seconds)
        )
        self.unconfirmed_send_timeout_seconds = max(
            self.poll_interval_seconds,
            float(unconfirmed_send_timeout_seconds),
        )

    @classmethod
    def applies_to(cls, tab: Any) -> bool:
        return is_arena_page_url(getattr(tab, "url", ""))

    def wait_for_target(self) -> bool:
        """Return once a target is observed, or raise a precise watchdog error.

        ``poll_send_activity()`` caches any response object it reads.  The later
        normal network monitor therefore parses the exact same first target.
        """
        if not self.applies_to(self.tab):
            return False

        started_at = self.clock()
        unconfirmed_deadline = started_at + self.unconfirmed_send_timeout_seconds
        confirmed_at: Optional[float] = None
        stable_snapshots = 0
        next_snapshot_at = started_at

        while True:
            self._raise_if_cancelled()
            now = self.clock()
            confirmed_deadline = (
                confirmed_at + self.confirmed_target_timeout_seconds
                if confirmed_at is not None
                else None
            )
            deadline_reached = now >= unconfirmed_deadline or (
                confirmed_deadline is not None and now >= confirmed_deadline
            )

            poll_timeout = min(
                self.poll_interval_seconds,
                max(0.01, unconfirmed_deadline - now),
            )
            if confirmed_deadline is not None:
                poll_timeout = min(
                    poll_timeout,
                    max(0.01, confirmed_deadline - now),
                )
            activity = self._poll_network(poll_timeout)
            if bool(activity.get("matched")):
                logger.debug("[ArenaWatchdog] matching target observed before deadline")
                return True

            now = self.clock()
            if confirmed_at is not None and now >= confirmed_deadline:
                raise ArenaConfirmedSendNoTarget(
                    "Arena send was confirmed but no matching target stream arrived "
                    f"within {self.confirmed_target_timeout_seconds:.1f} seconds"
                )
            if deadline_reached or now >= unconfirmed_deadline:
                raise ArenaSendUnconfirmed(
                    "Arena send confirmation was not observed within 90 seconds"
                )

            if now >= next_snapshot_at:
                snapshot = self._read_snapshot()
                if snapshot.confirms_send:
                    stable_snapshots += 1
                    if stable_snapshots >= 2 and confirmed_at is None:
                        confirmed_at = self.clock()
                        logger.info(
                            "[ArenaWatchdog] send confirmed by two stable DOM snapshots; "
                            "starting fixed target-stream deadline"
                        )
                else:
                    stable_snapshots = 0
                next_snapshot_at = now + self.poll_interval_seconds

            now = self.clock()
            if confirmed_at is not None and (
                now - confirmed_at >= self.confirmed_target_timeout_seconds
            ):
                raise ArenaConfirmedSendNoTarget(
                    "Arena send was confirmed but no matching target stream arrived "
                    f"within {self.confirmed_target_timeout_seconds:.1f} seconds"
                )

            wait_until = min(next_snapshot_at, unconfirmed_deadline)
            if confirmed_at is not None:
                wait_until = min(
                    wait_until,
                    confirmed_at + self.confirmed_target_timeout_seconds,
                )
            remaining = wait_until - self.clock()
            if remaining > 0:
                self.sleep(remaining)

    def _poll_network(self, timeout: float) -> Dict[str, Any]:
        try:
            activity = self.network_monitor.poll_send_activity(timeout=timeout)
        except Exception as exc:
            logger.debug(f"[ArenaWatchdog] target poll failed: {exc}")
            return {"seen": False, "matched": False, "error": str(exc)}
        return activity if isinstance(activity, dict) else {"seen": False, "matched": False}

    def _raise_if_cancelled(self) -> None:
        try:
            stopped = bool(self.should_stop())
        except Exception:
            stopped = False
        try:
            interrupted = bool(self.workflow_interrupted())
        except Exception:
            interrupted = False
        if stopped or interrupted:
            raise ArenaSendWatchdogCancelled("Arena send watchdog cancelled")

    def _read_snapshot(self) -> ArenaSendSnapshot:
        input_selector = str(self.selectors.get("input_box") or "")
        stop_selector = str(self.selectors.get("stop_btn") or "")
        script = self._snapshot_script(input_selector, stop_selector)
        try:
            raw = self.tab.run_js(script, timeout=2)
        except Exception as exc:
            logger.debug(f"[ArenaWatchdog] DOM snapshot failed: {exc}")
            return ArenaSendSnapshot(page_error=True)
        if not isinstance(raw, dict):
            return ArenaSendSnapshot(page_error=True)
        return ArenaSendSnapshot(
            document_ready=bool(raw.get("documentReady")),
            input_visible=bool(raw.get("inputVisible")),
            input_empty=bool(raw.get("inputEmpty")),
            stop_visible=bool(raw.get("stopVisible")),
            upload_in_progress=bool(raw.get("uploadInProgress")),
            page_error=bool(raw.get("pageError")),
        )

    @staticmethod
    def _snapshot_script(input_selector: str, stop_selector: str) -> str:
        selectors = json.dumps(
            {
                "input": input_selector,
                "stop": stop_selector,
                "nativeStop": ARENA_NATIVE_STOP_SELECTOR,
            },
            ensure_ascii=True,
        )
        return f"""
return (() => {{
  const selectors = {selectors};
  const visible = (element) => {{
    if (!element) return false;
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && style.display !== 'none'
      && style.visibility !== 'hidden' && style.opacity !== '0';
  }};
  const find = (selector) => {{
    if (!selector) return null;
    try {{ return document.querySelector(selector); }} catch (_) {{ return null; }}
  }};
  const input = find(selectors.input);
  const inputValue = input
    ? String(typeof input.value === 'string' ? input.value : (input.innerText || input.textContent || ''))
    : '';
  const hasPendingUpload = Array.from(document.querySelectorAll('img[src^="blob:"]')).some((image) => {{
    const card = image.closest('div.group.relative.overflow-hidden') || image.parentElement;
    return !!card
      && !!card.querySelector('button[aria-label="Remove file"]')
      && Array.from(card.querySelectorAll('svg.animate-spin')).some(visible);
  }});
  const errorSelectors = [
    '[role="alert"]', '[aria-live="assertive"]', '[data-testid*="error" i]',
    '[class*="error" i]', '[class*="destructive" i]', '[data-state="error"]',
    '[data-status="error"]', '[role="dialog"]', '[class*="captcha" i]', '[id*="captcha" i]'
  ];
  const hasVerificationElement = Array.from(document.querySelectorAll(
    'iframe[src*="recaptcha" i], iframe[src*="hcaptcha" i], iframe[src*="challenges.cloudflare.com" i], .g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey]'
  )).some(visible);
  const errorText = Array.from(document.querySelectorAll(errorSelectors.join(',')))
    .filter(visible)
    .map((element) => String(element.innerText || element.textContent || element.getAttribute('title') || '').toLowerCase())
    .join(' ');
  const pageError = hasVerificationElement || /response failed|terms of use|captcha|security verification|verify you are human|access denied|something went wrong/.test(errorText);
  return {{
    documentReady: location.hostname === 'arena.ai' || location.hostname.endsWith('.arena.ai')
      ? document.readyState === 'complete'
      : false,
    inputVisible: visible(input),
    inputEmpty: inputValue.trim().length === 0,
    stopVisible: visible(find(selectors.stop)) || visible(find(selectors.nativeStop)),
    uploadInProgress: hasPendingUpload,
    pageError
  }};
}})();
"""


def refresh_arena_page_for_retry(
    tab: Any,
    *,
    input_selector: str,
    should_stop: Optional[Callable[[], bool]] = None,
    timeout_seconds: float = 30.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Refresh Arena and wait until the configured input is available again."""
    if not is_arena_page_url(getattr(tab, "url", "")):
        raise ArenaSendRetryRefreshError("Arena retry refresh requested on a non-Arena page")

    try:
        tab.refresh(ignore_cache=True)
        wait = getattr(tab, "wait", None)
        if wait is not None and hasattr(wait, "doc_loaded"):
            wait.doc_loaded(timeout=min(15.0, max(1.0, timeout_seconds)))
    except Exception as exc:
        raise ArenaSendRetryRefreshError(f"Arena retry refresh failed: {exc}") from exc

    deadline = clock() + max(1.0, float(timeout_seconds))
    selector = str(input_selector or "textarea, [contenteditable=\"true\"]")
    while clock() < deadline:
        try:
            if should_stop is not None and should_stop():
                raise ArenaSendWatchdogCancelled("Arena retry refresh cancelled")
            raw = tab.run_js(
                ArenaSendWatchdog._snapshot_script(selector, ""),
                timeout=2,
            )
            if isinstance(raw, dict) and raw.get("documentReady") and raw.get("inputVisible"):
                return
        except ArenaSendWatchdogCancelled:
            raise
        except Exception:
            pass
        sleep(0.2)
    raise ArenaSendRetryRefreshError("Arena retry refresh did not restore the input box")


__all__ = [
    "ARENA_NATIVE_STOP_SELECTOR",
    "ARENA_SEND_NO_TARGET_AFTER_RETRY",
    "ARENA_UPLOAD_CARD_SELECTOR",
    "ArenaConfirmedSendNoTarget",
    "ArenaSendRetryRefreshError",
    "ArenaSendSnapshot",
    "ArenaSendUnconfirmed",
    "ArenaSendWatchdog",
    "ArenaSendWatchdogCancelled",
    "is_arena_page_url",
    "refresh_arena_page_for_retry",
]
