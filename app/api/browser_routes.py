"""Local integration endpoints for the project-controlled browser."""

from __future__ import annotations

import ipaddress
import os
import secrets
from typing import Any, Dict
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import request_looks_proxied
from app.core import get_browser
from app.core.config import get_logger
from app.core.config_parts.env_config import AppConfig, parse_bool_literal
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


#: 即使解析不出 IP 也必须视为内部目标的主机名后缀
_INTERNAL_HOST_SUFFIXES = (
    "localhost",
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home.arpa",
)
#: 云厂商实例元数据服务，绝不允许被诱导打开
_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata", "instance-data"})


def _env_flag(name: str, default: bool) -> bool:
    parsed = parse_bool_literal(os.environ.get(name))
    return default if parsed is None else parsed


def _host_is_internal(host: str) -> bool:
    candidate = str(host or "").strip().strip("[]").lower().rstrip(".")
    if not candidate:
        return True
    if candidate in _METADATA_HOSTS:
        return True
    try:
        # IP 字面量：只要不是全球可路由地址就算内部（含回环 / 私网 / 链路本地 / 元数据地址）
        return not ipaddress.ip_address(candidate).is_global
    except ValueError:
        pass
    return any(
        candidate == suffix.lstrip(".") or candidate.endswith(suffix)
        for suffix in _INTERNAL_HOST_SUFFIXES
    )


def _valid_web_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="只允许打开 http/https 链接")

    # 修复 S4：URL 由调用方完全控制，会在**用户自己的浏览器配置文件**里打开，
    # 因此可以携带用户既有 Cookie 命中内网管理面板 / 云元数据服务。
    if "@" in (parsed.netloc or ""):
        raise HTTPException(status_code=400, detail="链接不得内嵌凭据")
    if _host_is_internal(parsed.hostname or "") and not _env_flag(
        "BROWSER_OPEN_URL_ALLOW_INTERNAL_TARGETS", False
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "拒绝打开指向内网 / 本机 / 元数据地址的链接。"
                "确需此行为请设置 BROWSER_OPEN_URL_ALLOW_INTERNAL_TARGETS=true。"
            ),
        )
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


# Link Drawer 只保存路由元数据，不保存管理密钥。该接口由第三方页面上的
# 用户脚本跨源调用，因此**不能**套用 verify_admin_access 的同源限制；
# 这里改为对「回环即授权」这一假设本身做加固（修复 S4）。
@router.post("/open-profile-url")
def open_profile_url(
    payload: OpenProfileUrlRequest,
    request: Request,
):
    client_host = request.client.host if request.client else ""
    if not _is_loopback(client_host):
        raise HTTPException(status_code=403, detail="此接口仅允许本机调用")

    # 修复 S4（真实代理链核实）：本机反代 / 隧道会把外来请求以回环地址转交进来，
    # 上面的检查形同虚设。带转发痕迹的请求一律拒绝——我们无法核实这条链。
    if request_looks_proxied(request):
        logger.warning("拒绝带代理转发头的 open-profile-url 请求（无法确认调用方在本机）")
        raise HTTPException(
            status_code=403,
            detail="检测到代理转发头，无法确认调用方位于本机，已拒绝",
        )

    # 修复 S4（单独强制管理认证）：配置了管理令牌就必须出示。
    # 未配置令牌时维持原有的「仅本机」开箱即用行为。
    expected = AppConfig.get_dashboard_auth_token()
    if expected and not _env_flag("BROWSER_OPEN_URL_ALLOW_UNAUTHENTICATED_LOCAL", False):
        supplied = str(request.headers.get("authorization") or "").strip()
        if supplied.lower().startswith("bearer "):
            supplied = supplied[7:].strip()
        if not supplied:
            supplied = str(request.headers.get("x-api-key") or "").strip()
        if not secrets.compare_digest(supplied, str(expected)):
            raise HTTPException(
                status_code=401,
                detail=(
                    "已配置 DASHBOARD_AUTH_TOKEN，调用本接口需携带该令牌。"
                    "若 Link Drawer 无法携带令牌，可设置 "
                    "BROWSER_OPEN_URL_ALLOW_UNAUTHENTICATED_LOCAL=true 保留本机免认证。"
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )

    return open_url_in_profile(_valid_web_url(payload.url), payload.profile)
