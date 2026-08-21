"""Arena Direct unexpected battle redirect detection and auto-recovery guard.

This module is exclusively used for arena.ai direct presets.
When a workflow is actively executing on an Arena direct preset and the page
is unexpectedly redirected to the default battle/home page (https://arena.ai),
this guard detects the deviation, navigates back to the correct Direct page
(either https://arena.ai/text/direct if workflow starts a new conversation,
or the pre-accident conversation URL if resuming an existing conversation),
and allows the workflow engine to restart the workflow cleanly.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Mapping, Optional
from urllib.parse import urlparse

from app.core.config import logger
from app.utils.site_url import extract_remote_site_domain, route_domain_matches


ARENA_DIRECT_DEFAULT_ENTRY_URL = "https://arena.ai/text/direct"
ARENA_DIRECT_UNEXPECTED_BATTLE_REDIRECT = "arena_direct_unexpected_battle_redirect"


class ArenaUnexpectedBattleRedirectError(RuntimeError):
    """Raised when an active Arena direct workflow detects the page unexpectedly redirected to battle/root."""

    def __init__(
        self,
        message: str = "Arena direct workflow detected unexpected redirection to battle page",
        *,
        current_url: str = "",
        target_url: str = "",
        initial_url: str = "",
        has_new_chat: bool = False,
    ) -> None:
        super().__init__(message)
        self.current_url = current_url
        self.target_url = target_url
        self.initial_url = initial_url
        self.has_new_chat = has_new_chat


class ArenaDirectRecoveryError(RuntimeError):
    """Raised when navigating back to restore the Arena direct page fails."""


def is_arena_page_url(url: Any) -> bool:
    """Check if the given URL belongs to arena.ai or its subdomains."""
    try:
        raw = str(url or "").strip()
        if not raw:
            return False
        hostname = (urlparse(raw).hostname or "").lower()
        return hostname == "arena.ai" or hostname.endswith(".arena.ai")
    except Exception:
        return False


def is_arena_direct_url(url: Any) -> bool:
    """Check if the given URL is a valid Arena Direct page or conversation page.

    Valid direct pages include:
    - /text/direct, /direct, or their subpaths
    - /c/... (specific direct chat conversation)
    - /image/direct, /code/direct
    """
    try:
        raw = str(url or "").strip()
        if not raw:
            return False
        parsed = urlparse(raw)
        hostname = (parsed.hostname or "").lower()
        if hostname != "arena.ai" and not hostname.endswith(".arena.ai"):
            return False
        path = (parsed.path or "").rstrip("/").lower()
        if path in {"/text/direct", "/direct", "/image/direct", "/code/direct"}:
            return True
        if path.startswith(("/text/direct/", "/direct/", "/image/direct/", "/code/direct/")):
            return True
        if path == "/c" or path.startswith("/c/"):
            return True
        return False
    except Exception:
        return False


def is_arena_unexpected_battle_url(url: Any) -> bool:
    """Check if the URL is on arena.ai but is NOT a direct page (i.e. default battle/home page).

    Examples of battle / root paths:
    - https://arena.ai or https://arena.ai/
    - https://arena.ai/text or https://arena.ai/text/
    - https://arena.ai/text/side-by-side
    - https://arena.ai/side-by-side
    - https://arena.ai/battle
    """
    if not is_arena_page_url(url):
        return False
    return not is_arena_direct_url(url)


def is_arena_direct_preset(
    domain: str,
    preset_name: str = "",
    site_config: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Determine whether the specified preset operates in Arena Direct mode."""
    if not domain:
        return False
    clean_domain = str(domain).strip().lower()
    if not (route_domain_matches("arena.ai", clean_domain) or clean_domain == "arena.ai"):
        return False

    name_lower = str(preset_name or "").strip().lower()

    # Exclude explicit negative keywords
    negative_keywords = ("非直连", "not direct", "indirect", "battle", "双栏")
    if any(neg in name_lower for neg in negative_keywords):
        return False

    if isinstance(site_config, (dict, Mapping)):
        catalog = site_config.get("model_catalog")
        if isinstance(catalog, dict) and str(catalog.get("source", "")).strip().lower() == "arena_direct":
            return True

    if "direct" in name_lower or "直连" in name_lower:
        return True

    return False


def workflow_has_new_chat_step(workflow: Optional[List[Dict[str, Any]]]) -> bool:
    """Check if the given workflow definition contains a new conversation / new chat step."""
    for step in workflow or []:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action", "") or "").strip().upper()
        target = str(step.get("target", "") or "").strip().lower()
        if action in {"NEW_CHAT", "NEW_CONVERSATION"}:
            return True
        if target in {"new_chat_btn", "new_chat", "new_conversation"} or "new_chat" in target:
            return True
    return False


def resolve_arena_direct_recovery_url(
    workflow: Optional[List[Dict[str, Any]]],
    initial_url: Optional[str] = None,
    *,
    skip_new_chat: bool = False,
    fallback_url: str = ARENA_DIRECT_DEFAULT_ENTRY_URL,
) -> str:
    """Determine the recovery URL based on workflow steps and current session continuity.

    - If the workflow starts a brand new conversation (has_new_chat is True and skip_new_chat is False):
      return https://arena.ai/text/direct
    - If the workflow resumes an existing conversation (skip_new_chat is True or workflow has NO new-chat step):
      return initial_url if it is a valid Direct page/conversation URL, falling back to fallback_url.
    """
    has_new_chat = workflow_has_new_chat_step(workflow)
    wants_new_chat = has_new_chat and not skip_new_chat

    if wants_new_chat:
        return fallback_url

    clean_initial = str(initial_url or "").strip()
    if clean_initial and is_arena_direct_url(clean_initial):
        return clean_initial

    return fallback_url


def recover_arena_direct_page(
    tab: Any,
    target_url: str,
    *,
    input_selector: Optional[str] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    timeout_seconds: float = 20.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Navigate the tab to the specified target Direct URL and wait until it is ready."""
    if should_stop is not None and should_stop():
        raise ArenaDirectRecoveryError("Arena direct recovery cancelled before navigation")

    clean_target = str(target_url or "").strip() or ARENA_DIRECT_DEFAULT_ENTRY_URL
    logger.info(f"[ArenaDirectGuard] 正在导航跳转到目标页面以恢复 Direct 会话: {clean_target}")

    start_time = clock()
    deadline = start_time + max(1.0, float(timeout_seconds))

    try:
        tab.get(clean_target)
        wait = getattr(tab, "wait", None)
        if wait is not None and hasattr(wait, "doc_loaded"):
            doc_load_timeout = max(0.5, min(10.0, deadline - clock()))
            try:
                wait.doc_loaded(timeout=doc_load_timeout)
            except Exception:
                pass
    except Exception as exc:
        raise ArenaDirectRecoveryError(f"Arena direct recovery navigation failed: {exc}") from exc

    selector = str(input_selector or "textarea[name=\"message\"], textarea, [contenteditable=\"true\"]")
    probe_script = f"""(() => {{
        try {{
            let el = null;
            try {{
                el = document.querySelector({json.dumps(selector)});
            }} catch (e) {{
                el = document.querySelector('textarea[name="message"], textarea, [contenteditable="true"]');
            }}
            const isVisible = (e) => {{
                if (!e) return false;
                const rect = e.getBoundingClientRect();
                const style = window.getComputedStyle(e);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            }};
            return {{
                documentReady: document.readyState === 'complete' || document.readyState === 'interactive',
                inputVisible: isVisible(el),
                url: window.location.href
            }};
        }} catch (err) {{
            return {{
                documentReady: false,
                inputVisible: false,
                url: window.location.href,
                error: String(err)
            }};
        }}
    }})()"""

    while clock() < deadline:
        if should_stop is not None and should_stop():
            raise ArenaDirectRecoveryError("Arena direct recovery cancelled")
        try:
            raw = tab.run_js(probe_script, timeout=2)
            if isinstance(raw, dict) and raw.get("documentReady"):
                current_url = str(raw.get("url") or "")
                if is_arena_direct_url(current_url) and raw.get("inputVisible"):
                    logger.info(f"[ArenaDirectGuard] 页面与输入框已成功就绪: url={current_url}")
                    return
        except ArenaDirectRecoveryError:
            raise
        except Exception:
            pass
        sleep(0.2)

    # Check if we at least reached a valid direct URL
    try:
        final_url = getattr(tab, "url", "")
        if is_arena_direct_url(final_url):
            logger.warning("[ArenaDirectGuard] 恢复跳转后等待输入框超时，但页面位于 Direct 路由，尝试继续执行工作流")
            return
    except Exception:
        pass

    raise ArenaDirectRecoveryError(f"Arena direct recovery failed: page not restored within {timeout_seconds}s")


__all__ = [
    "ARENA_DIRECT_DEFAULT_ENTRY_URL",
    "ARENA_DIRECT_UNEXPECTED_BATTLE_REDIRECT",
    "ArenaDirectRecoveryError",
    "ArenaUnexpectedBattleRedirectError",
    "is_arena_direct_preset",
    "is_arena_direct_url",
    "is_arena_page_url",
    "is_arena_unexpected_battle_url",
    "recover_arena_direct_page",
    "resolve_arena_direct_recovery_url",
    "workflow_has_new_chat_step",
]
