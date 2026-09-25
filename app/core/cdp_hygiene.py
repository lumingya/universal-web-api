"""
app/core/cdp_hygiene.py - DrissionPage 运行期 CDP 资源卫生补丁

背景（见 docs/performance_refactor.md）：
- DrissionPage 的 ``run_js`` 始终以 ``returnByValue=False`` 调用，返回对象/数组时
  会在渲染进程里生成 RemoteObject，并且从不 ``Runtime.releaseObject``。
  V8 inspector 对这些对象持强引用，直到整页导航或 CDP 会话断开——已经从页面上
  移除的旧 DOM 会被一直钉在内存里，强制 GC 也回收不掉。
- ``Network.enable`` 不带缓冲上限，渲染进程会为每个 XHR/Fetch 响应保留副本。

本模块只做**运行期**的 monkeypatch（不改写磁盘上的库源码），且全部幂等：

1. ``parse_js_result`` 包装：非节点的对象/数组结果在取回后立即 ``releaseObject``。
   节点结果（会被包装成 ChromiumElement 继续使用）保持不变。
2. ``Driver.run`` 包装：``Network.enable`` 未显式给出缓冲上限时自动补上
   ``maxTotalBufferSize`` / ``maxResourceBufferSize``（可用环境变量调整或关闭）。
3. 提供 ``run_js_json``：在页面内 ``JSON.stringify`` 后只返回一个字符串，
   既不生成 RemoteObject，也省掉 DrissionPage 额外的一次 ``JSON.stringify`` 往返。

环境变量：
- ``CDP_HYGIENE_ENABLED``（默认 true）：总开关。
- ``CDP_RELEASE_JS_OBJECTS``（默认 true）：是否释放 run_js 的对象结果。
- ``CDP_NETWORK_MAX_TOTAL_BUFFER_MB``（默认 32，0 = 不注入）
- ``CDP_NETWORK_MAX_RESOURCE_BUFFER_MB``（默认 16，0 = 不注入）
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Optional

from app.core.config import logger

_INSTALL_LOCK = threading.Lock()
_INSTALLED = False
_STATS_LOCK = threading.Lock()
_STATS: Dict[str, int] = {
    "released_js_objects": 0,
    "release_errors": 0,
    "network_enable_limited": 0,
}

_RELEASE_SKIP_CLASSES = frozenset({"HTMLDocument"})


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_mb(name: str, default: float) -> int:
    raw = os.getenv(name)
    try:
        value = float(raw) if raw not in (None, "") else float(default)
    except (TypeError, ValueError):
        value = float(default)
    if value <= 0:
        return 0
    return int(value * 1024 * 1024)


def _bump(key: str, amount: int = 1) -> None:
    with _STATS_LOCK:
        _STATS[key] = int(_STATS.get(key, 0) or 0) + amount


def get_stats() -> Dict[str, Any]:
    with _STATS_LOCK:
        data = dict(_STATS)
    data["installed"] = _INSTALLED
    return data


def network_enable_limits() -> Dict[str, int]:
    """Buffer limits injected into ``Network.enable`` (empty dict = disabled)."""
    limits: Dict[str, int] = {}
    total = _env_mb("CDP_NETWORK_MAX_TOTAL_BUFFER_MB", 32)
    resource = _env_mb("CDP_NETWORK_MAX_RESOURCE_BUFFER_MB", 16)
    if total > 0:
        limits["maxTotalBufferSize"] = total
    if resource > 0:
        if total > 0:
            resource = min(resource, total)
        limits["maxResourceBufferSize"] = resource
    return limits


def _should_release(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    if result.get("type") != "object":
        return None
    object_id = result.get("objectId")
    if not object_id:
        return None
    subtype = result.get("subtype")
    if subtype in ("node", "null"):
        # 节点会被包装成 ChromiumElement 继续使用，不能释放
        return None
    if result.get("className") in _RELEASE_SKIP_CLASSES:
        return None
    return str(object_id)


def _release_object(page: Any, object_id: str) -> None:
    try:
        driver = getattr(page, "driver", None) or getattr(page, "_driver", None)
        if driver is None:
            return
        driver.run("Runtime.releaseObject", objectId=object_id, _timeout=2)
        _bump("released_js_objects")
    except Exception:
        _bump("release_errors")


def _patch_parse_js_result() -> bool:
    try:
        from DrissionPage._elements import chromium_element as ce
    except Exception as exc:  # pragma: no cover - DrissionPage missing
        logger.debug(f"[CDP_HYGIENE] DrissionPage 不可用，跳过 run_js 补丁: {exc}")
        return False

    original = getattr(ce, "parse_js_result", None)
    if original is None:
        return False
    if getattr(original, "_uwapi_release_wrapper", False):
        return True

    def parse_js_result(page, ele, result, end_time):  # noqa: D401 - same signature
        object_id = _should_release(result)
        try:
            return original(page, ele, result, end_time)
        finally:
            if object_id:
                _release_object(page, object_id)

    parse_js_result._uwapi_release_wrapper = True  # type: ignore[attr-defined]
    parse_js_result._uwapi_original = original  # type: ignore[attr-defined]
    ce.parse_js_result = parse_js_result
    return True


def _patch_network_enable() -> bool:
    try:
        from DrissionPage._base.driver import Driver
    except Exception as exc:  # pragma: no cover - DrissionPage missing
        logger.debug(f"[CDP_HYGIENE] DrissionPage Driver 不可用，跳过 Network.enable 补丁: {exc}")
        return False

    original = Driver.run
    if getattr(original, "_uwapi_network_limit_wrapper", False):
        return True

    # 签名与各版本原版保持透传：4.0.x–4.1.0.x 是 run(self, _method, **kwargs)，kwargs 原样进 params；
    # 4.1.1.x 才有独立的 sessionId 形参。不能在这里声明 sessionId=None 再转发，否则老版本会把
    # "sessionId": null 塞进每条 CDP 命令的 params。
    def run(self, _method, *args, **kwargs):
        if _method == "Network.enable":
            limits = network_enable_limits()
            injected = False
            for key, value in limits.items():
                if key not in kwargs:
                    kwargs[key] = value
                    injected = True
            if injected:
                _bump("network_enable_limited")
        return original(self, _method, *args, **kwargs)

    run._uwapi_network_limit_wrapper = True  # type: ignore[attr-defined]
    run._uwapi_original = original  # type: ignore[attr-defined]
    Driver.run = run
    return True


def install() -> bool:
    """Install all runtime patches once. Safe to call repeatedly."""
    global _INSTALLED
    if _INSTALLED:
        return True
    if not _env_bool("CDP_HYGIENE_ENABLED", True):
        return False
    with _INSTALL_LOCK:
        if _INSTALLED:
            return True
        applied = []
        if _env_bool("CDP_RELEASE_JS_OBJECTS", True) and _patch_parse_js_result():
            applied.append("release_js_objects")
        if network_enable_limits() and _patch_network_enable():
            applied.append("network_buffer_limits")
        _INSTALLED = True
        if applied:
            logger.debug(f"[CDP_HYGIENE] 已安装运行期补丁: {', '.join(applied)}")
        return True


# ---------------------------------------------------------------------------
# JSON 化的 run_js：只返回原始字符串，不生成 RemoteObject
# ---------------------------------------------------------------------------

_JSON_WRAPPER_TEMPLATE = (
    "function(){"
    "const __uwapiResult = (function(){\n%s\n}).apply(this, arguments);"
    "if (__uwapiResult && typeof __uwapiResult.then === 'function') {"
    "return __uwapiResult.then(function(v){ return JSON.stringify(v === undefined ? null : v); });"
    "}"
    "return JSON.stringify(__uwapiResult === undefined ? null : __uwapiResult);"
    "}"
)


def wrap_js_json(script_body: str) -> str:
    """Wrap a ``run_js`` body so that its return value is JSON-stringified in page."""
    return _JSON_WRAPPER_TEMPLATE % str(script_body or "")


def decode_js_json(raw: Any, default: Any = None) -> Any:
    """Decode a JSON string produced by :func:`wrap_js_json`.

    Test doubles and legacy call sites may still hand back already-decoded
    Python objects; those are returned unchanged.
    """
    if raw is None:
        return default
    if isinstance(raw, (dict, list, bool, int, float)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return default
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return default
    return default


def run_js_json(target: Any, script_body: str, *args: Any, timeout: Optional[float] = None, default: Any = None) -> Any:
    """Run ``script_body`` on a tab/element and return the decoded JSON value."""
    wrapped = wrap_js_json(script_body)
    if timeout is None:
        raw = target.run_js(wrapped, *args)
    else:
        raw = target.run_js(wrapped, *args, timeout=timeout)
    return decode_js_json(raw, default)


__all__ = [
    "decode_js_json",
    "get_stats",
    "install",
    "network_enable_limits",
    "run_js_json",
    "wrap_js_json",
]
