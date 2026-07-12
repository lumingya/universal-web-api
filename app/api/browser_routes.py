"""Local integration endpoints for the project-controlled browser."""

from __future__ import annotations

import ipaddress
from typing import Any, Dict
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core import get_browser
from app.core.config import get_logger
from app.api.deps import verify_auth
from app.utils.browser_profile_identity import resolve_tab_browser_profile


logger = get_logger("API.BROWSER")
router = APIRouter(prefix="/api/browser", tags=["browser"])


class OpenProfileUrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=8192)
    profile: Dict[str, Any] = Field(default_factory=dict)


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(str(host or "").split("%", 1)[0]).is_loopback
    except ValueError:
        return str(host or "").casefold() == "localhost"


def _valid_web_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="只允许打开 http/https 链接")
    return value


def _target_info(tab: Any) -> Dict[str, Any]:
    try:
        result = tab.run_cdp("Target.getTargetInfo") or {}
        info = result.get("targetInfo") if isinstance(result, dict) else {}
        return info if isinstance(info, dict) else {}
    except Exception:
        return {}


def _identity_matches(actual: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    for key in ("profile_directory", "profile_path"):
        wanted = str(expected.get(key) or "").strip().casefold()
        if wanted:
            return str(actual.get(key) or "").strip().casefold() == wanted
    wanted_name = str(expected.get("name") or "").strip().casefold()
    return bool(wanted_name and str(actual.get("name") or "").strip().casefold() == wanted_name)


def _find_profile_tab(browser: Any, profile: Dict[str, Any]) -> tuple[Any, str]:
    tabs = list(browser.get_tabs() or [])
    source_tab_id = str(profile.get("source_tab_id") or "").strip()
    expected_context = str(profile.get("browser_context_id") or "").strip()

    for tab in tabs:
        info = _target_info(tab)
        tab_id = str(info.get("targetId") or getattr(tab, "tab_id", "") or "").strip()
        context_id = str(info.get("browserContextId") or "").strip()
        if source_tab_id and tab_id == source_tab_id:
            return tab, context_id
        if expected_context and context_id == expected_context:
            return tab, context_id

    for tab in tabs:
        identity = resolve_tab_browser_profile(tab)
        if _identity_matches(identity, profile):
            info = _target_info(tab)
            return tab, str(info.get("browserContextId") or identity.get("browser_context_id") or "").strip()
    raise HTTPException(status_code=404, detail="受控浏览器中未找到对应用户目录")


def open_url_in_profile(url: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(profile, dict) or not any(
        str(profile.get(key) or "").strip()
        for key in ("profile_directory", "profile_path", "name", "browser_context_id")
    ):
        raise HTTPException(status_code=400, detail="缺少浏览器用户目录标识")

    browser = get_browser(auto_connect=False)
    health = browser.health_check()
    if not isinstance(health, dict) or not health.get("connected"):
        raise HTTPException(status_code=503, detail="项目受控浏览器未连接")

    source_tab, context_id = _find_profile_tab(browser, profile)
    handle = browser.get_browser_handle()
    try:
        kwargs: Dict[str, Any] = {"url": url}
        if context_id:
            kwargs["browserContextId"] = context_id
        created = handle._run_cdp("Target.createTarget", **kwargs) or {}
        target_id = str(created.get("targetId") or "").strip() if isinstance(created, dict) else ""
        if target_id:
            try:
                handle._run_cdp("Target.activateTarget", targetId=target_id)
            except Exception:
                pass
        return {"success": True, "targetId": target_id, "browserContextId": context_id}
    except Exception as error:
        # The default Chrome profile may not expose a context id. Opening from one
        # of its own pages keeps the new tab in that exact profile.
        try:
            script = "window.open(arguments[0], '_blank'); return true;"
            source_tab.run_js(script, url)
            return {"success": True, "targetId": "", "browserContextId": context_id}
        except Exception as fallback_error:
            logger.warning(f"打开用户目录链接失败: {error}; fallback={fallback_error}")
            raise HTTPException(status_code=502, detail="无法在对应用户目录中打开链接") from fallback_error


@router.post("/open-profile-url")
def open_profile_url(
    payload: OpenProfileUrlRequest,
    request: Request,
    _authenticated: bool = Depends(verify_auth),
):
    client_host = request.client.host if request.client else ""
    if not _is_loopback(client_host):
        raise HTTPException(status_code=403, detail="此接口仅允许本机调用")
    return open_url_in_profile(_valid_web_url(payload.url), payload.profile)
