"""Shared Clash proxy rotation for Arena commands."""

from __future__ import annotations

import time
import urllib.parse
from typing import Any, Callable, Dict, Iterable, Optional

import requests


ARENA_CLASH_API = "http://127.0.0.1:9097"
ARENA_CLASH_SECRET = "1"
ARENA_CLASH_SELECTOR = "主代理"
ARENA_PROXY_POOL = (
    "HK自动选择",
    "JP自动选择",
    "KR自动选择",
    "SG自动选择",
    "TW自动选择",
    "US自动选择",
)


def rotate_arena_proxy(
    *,
    tab: Any,
    logger: Any,
    raise_if_cancelled: Optional[Callable[[], None]] = None,
    requests_module: Any = requests,
    sleep: Callable[[float], None] = time.sleep,
    proxy_pool: Iterable[str] = ARENA_PROXY_POOL,
) -> Dict[str, Any]:
    """Move the Arena Clash selector to the next node in the shared pool."""
    cancel = raise_if_cancelled or (lambda: None)
    pool = tuple(str(item) for item in proxy_pool if str(item).strip())
    headers = {"Content-Type": "application/json"}
    if ARENA_CLASH_SECRET:
        headers["Authorization"] = f"Bearer {ARENA_CLASH_SECRET}"

    selector_url = (
        f"{ARENA_CLASH_API}/proxies/"
        f"{urllib.parse.quote(ARENA_CLASH_SELECTOR, safe='')}"
    )
    try:
        cancel()
        response = requests_module.get(selector_url, headers=headers, timeout=5)
        response.raise_for_status()
        selector = response.json()
        available = [name for name in pool if name in (selector.get("all") or [])]
        if len(available) != len(pool):
            return {
                "ok": False,
                "error": "arena_proxy_pool_incomplete",
                "missing": [name for name in pool if name not in available],
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
                "selector": ARENA_CLASH_SELECTOR,
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
                f"{ARENA_CLASH_API}/connections",
                headers=headers,
                timeout=5,
            ).raise_for_status()
        except Exception as close_error:
            logger.warning(
                f"[ARENA][IP-ROTATE] closing Clash connections failed: {close_error}"
            )

        logger.info(
            f"[ARENA][IP-ROTATE] {ARENA_CLASH_SELECTOR}: "
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
            "selector": ARENA_CLASH_SELECTOR,
            "from": current,
            "to": next_proxy,
            "pool_size": len(available),
        }
    except Exception as error:
        logger.error(f"[ARENA][IP-ROTATE] Clash switch failed: {error}")
        return {"ok": False, "error": str(error)}


__all__ = [
    "ARENA_CLASH_API",
    "ARENA_CLASH_SECRET",
    "ARENA_CLASH_SELECTOR",
    "ARENA_PROXY_POOL",
    "rotate_arena_proxy",
]
