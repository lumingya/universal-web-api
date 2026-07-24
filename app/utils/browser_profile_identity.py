"""Resolve the Chrome profile display name that owns a specific tab."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict


_CACHE: Dict[str, Dict[str, str]] = {}
_CACHE_LOCK = threading.RLock()
_GENERIC_PROFILE_NAMES = {
    "default",
    "person 1",
    "profile 1",
    "your chrome",
    "您的 chrome",
}


def _profile_display_name(profile_path: Path) -> Dict[str, str]:
    profile_directory = profile_path.name
    user_data_dir = profile_path.parent
    info: Dict[str, Any] = {}
    try:
        with (user_data_dir / "Local State").open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        info_cache = ((payload or {}).get("profile") or {}).get("info_cache") or {}
        candidate = info_cache.get(profile_directory)
        if isinstance(candidate, dict):
            info = candidate
    except (OSError, ValueError, TypeError):
        info = {}

    configured_name = str(info.get("name") or "").strip()
    candidates = []
    if configured_name and configured_name.casefold() not in _GENERIC_PROFILE_NAMES:
        candidates.append(configured_name)
    candidates.extend([
        str(info.get("shortcut_name") or "").strip(),
        str(info.get("gaia_given_name") or "").strip(),
        str(info.get("gaia_name") or "").strip(),
        configured_name,
        profile_directory,
    ])
    display_name = next((item for item in candidates if item), "profile")
    return {
        "name": display_name,
        "profile_directory": profile_directory,
        "profile_path": str(profile_path),
        "user_data_dir": str(user_data_dir),
        "source": "chrome_version",
    }


def _target_infos(browser: Any) -> list[Dict[str, Any]]:
    result = browser._run_cdp("Target.getTargets") or {}
    items = result.get("targetInfos") if isinstance(result, dict) else []
    return [item for item in (items or []) if isinstance(item, dict)]


def _resolve_via_profile_page(tab: Any, timeout: float = 3.0) -> Dict[str, str]:
    browser = getattr(tab, "browser", None)
    source_id = str(getattr(tab, "tab_id", "") or "").strip()
    if browser is None or not source_id or not hasattr(browser, "_run_cdp"):
        return {}

    source_info = tab.run_cdp("Target.getTargetInfo") or {}
    target_info = source_info.get("targetInfo") if isinstance(source_info, dict) else {}
    context_id = str((target_info or {}).get("browserContextId") or "").strip()
    cache_key = context_id or source_id
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached:
            return dict(cached)

    temp_target_id = ""
    popup_token = ""
    try:
        deadline = time.time() + max(0.5, float(timeout or 3.0))

        # Creating the probe through CDP gives us its exact target id.  The old
        # window.open() flow had to rediscover the popup via Target.getTargets;
        # if that lookup failed or raced, the id stayed empty and an orphaned
        # about:blank tab was left behind.
        create_args: Dict[str, Any] = {"url": "about:blank"}
        if context_id:
            create_args["browserContextId"] = context_id
        try:
            created = browser._run_cdp("Target.createTarget", **create_args) or {}
            if isinstance(created, dict):
                temp_target_id = str(created.get("targetId") or "").strip()
        except Exception:
            temp_target_id = ""

        # Some older Chromium/DrissionPage combinations cannot create a target
        # in a supplied browser context. Retain the popup fallback, but keep a
        # WindowProxy so it can still be closed when target discovery fails.
        if not temp_target_id:
            existing_ids = {str(item.get("targetId") or "") for item in _target_infos(browser)}
            popup_token = f"profile-probe-{threading.get_ident()}-{time.time_ns()}"
            opened = tab.run_js(
                """
                const token = arguments[0];
                const child = window.open('about:blank', token);
                window.__profileProbeWindows = window.__profileProbeWindows || {};
                window.__profileProbeWindows[token] = child;
                return child !== null;
                """,
                popup_token,
            )
            if opened is False:
                return {}
            while time.time() < deadline and not temp_target_id:
                for item in _target_infos(browser):
                    target_id = str(item.get("targetId") or "").strip()
                    if (
                        target_id
                        and target_id not in existing_ids
                        and str(item.get("type") or "").lower() == "page"
                        and str(item.get("openerId") or "") == source_id
                    ):
                        temp_target_id = target_id
                        break
                if not temp_target_id:
                    time.sleep(0.05)
        if not temp_target_id:
            return {}

        temp_tab = None
        while time.time() < deadline and temp_tab is None:
            try:
                temp_tab = browser.get_tab(temp_target_id)
            except Exception:
                time.sleep(0.05)
        if temp_tab is None:
            return {}
        temp_tab.run_cdp("Page.navigate", url="chrome://version")
        profile_path_text = ""
        while time.time() < deadline and not profile_path_text:
            try:
                profile_path_text = str(
                    temp_tab.run_js(
                        "return document.querySelector('#profile_path')?.textContent || ''"
                    )
                    or ""
                ).strip()
            except Exception:
                profile_path_text = ""
            if not profile_path_text:
                time.sleep(0.05)
        if not profile_path_text:
            return {}

        identity = _profile_display_name(Path(profile_path_text))
        identity["browser_context_id"] = context_id
        identity["source_tab_id"] = source_id
        with _CACHE_LOCK:
            _CACHE[cache_key] = dict(identity)
        return identity
    except Exception:
        return {}
    finally:
        if temp_target_id:
            try:
                browser._run_cdp("Target.closeTarget", targetId=temp_target_id)
            except Exception:
                pass
        if popup_token:
            try:
                tab.run_js(
                    """
                    const token = arguments[0];
                    const store = window.__profileProbeWindows || {};
                    const child = store[token];
                    if (child && !child.closed) child.close();
                    delete store[token];
                    return true;
                    """,
                    popup_token,
                )
            except Exception:
                pass


def resolve_tab_browser_profile(tab: Any) -> Dict[str, str]:
    """Resolve only the browser profile that owns ``tab``."""
    for _ in range(2):
        identity = _resolve_via_profile_page(tab)
        if identity.get("name"):
            return identity
    return {}
