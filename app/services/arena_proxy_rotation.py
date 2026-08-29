"""Shared Clash proxy rotation for Arena and other browser automation commands."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

import requests


ARENA_CLASH_API = os.getenv("CLASH_API", os.getenv("ARENA_CLASH_API", "http://127.0.0.1:9097")).rstrip("/")
ARENA_CLASH_SECRET = os.getenv("CLASH_SECRET", os.getenv("ARENA_CLASH_SECRET", "1"))
ARENA_CLASH_SELECTOR = os.getenv("CLASH_SELECTOR", os.getenv("ARENA_CLASH_SELECTOR", "主代理"))

DEFAULT_ARENA_PROXY_POOL = (
    "HK自动选择",
    "JP自动选择",
    "KR自动选择",
    "SG自动选择",
    "TW自动选择",
    "US自动选择",
)


def _get_proxy_pool_from_env() -> Tuple[str, ...]:
    raw = os.getenv("CLASH_PROXY_POOL", os.getenv("ARENA_PROXY_POOL", ""))
    if not raw:
        return DEFAULT_ARENA_PROXY_POOL
    try:
        if raw.startswith("["):
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return tuple(str(x).strip() for x in parsed if str(x).strip())
    except Exception:
        pass
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(parts) if parts else DEFAULT_ARENA_PROXY_POOL


ARENA_PROXY_POOL = _get_proxy_pool_from_env()


def rotate_arena_proxy(
    *,
    tab: Any,
    logger: Any,
    raise_if_cancelled: Optional[Callable[[], None]] = None,
    requests_module: Any = requests,
    sleep: Callable[[float], None] = time.sleep,
    proxy_pool: Optional[Iterable[str]] = None,
    clash_api: Optional[str] = None,
    clash_secret: Optional[str] = None,
    clash_selector: Optional[str] = None,
) -> Dict[str, Any]:
    """Move the Clash selector to the next node in the pool."""
    cancel = raise_if_cancelled or (lambda: None)
    effective_pool = tuple(str(item) for item in (proxy_pool if proxy_pool is not None else ARENA_PROXY_POOL) if str(item).strip())
    api_base = str(clash_api or os.getenv("CLASH_API", os.getenv("ARENA_CLASH_API", ARENA_CLASH_API))).rstrip("/")
    secret = str(clash_secret if clash_secret is not None else os.getenv("CLASH_SECRET", os.getenv("ARENA_CLASH_SECRET", ARENA_CLASH_SECRET)))
    selector_name = str(clash_selector or os.getenv("CLASH_SELECTOR", os.getenv("ARENA_CLASH_SELECTOR", ARENA_CLASH_SELECTOR)))

    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    selector_url = (
        f"{api_base}/proxies/"
        f"{urllib.parse.quote(selector_name, safe='')}"
    )
    try:
        cancel()
        response = requests_module.get(selector_url, headers=headers, timeout=5)
        response.raise_for_status()
        selector = response.json()
        available = [name for name in effective_pool if name in (selector.get("all") or [])]
        if len(available) != len(effective_pool):
            return {
                "ok": False,
                "error": "arena_proxy_pool_incomplete",
                "missing": [name for name in effective_pool if name not in available],
            }

        current = str(selector.get("now") or "")
        next_proxy = (
            available[(available.index(current) + 1) % len(available)]
            if current in available
            else available[0]
        )
        if next_proxy == current:
            return {
                "ok": True,
                "switched": False,
                "selector": selector_name,
                "node": current,
                "pool_size": len(available),
            }

        cancel()
        switch_response = requests_module.put(
            selector_url,
            json={"name": next_proxy},
            headers=headers,
            timeout=5,
        )
        switch_response.raise_for_status()
        try:
            requests_module.delete(
                f"{api_base}/connections",
                headers=headers,
                timeout=5,
            ).raise_for_status()
        except Exception as close_error:
            logger.warning(
                f"[ARENA][IP-ROTATE] closing Clash connections failed: {close_error}"
            )

        logger.info(
            f"[ARENA][IP-ROTATE] {selector_name}: "
            f"{current or '-'} -> {next_proxy}"
        )
        sleep(1.0)
        try:
            tab.refresh()
            sleep(2.0)
        except Exception as refresh_error:
            logger.warning(f"[ARENA][IP-ROTATE] page refresh failed: {refresh_error}")
        return {
            "ok": True,
            "switched": True,
            "selector": selector_name,
            "from": current,
            "to": next_proxy,
            "pool_size": len(available),
        }
    except Exception as error:
        logger.error(f"[ARENA][IP-ROTATE] Clash switch failed: {error}")
        return {"ok": False, "error": str(error)}


# 通用别名
rotate_clash_proxy = rotate_arena_proxy


__all__ = [
    "ARENA_CLASH_API",
    "ARENA_CLASH_SECRET",
    "ARENA_CLASH_SELECTOR",
    "ARENA_PROXY_POOL",
    "DEFAULT_ARENA_PROXY_POOL",
    "rotate_arena_proxy",
    "rotate_clash_proxy",
]
