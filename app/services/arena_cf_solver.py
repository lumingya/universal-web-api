"""
app/services/arena_cf_solver.py - Arena Cloudflare Turnstile / 5s 盾自动过盾服务（兼容适配层）

说明：
本模块已重构并委托底层通用过盾引擎 `app.services.cf_turnstile_solver`，
在此处保留 Arena 专有的 URL 校验规则与兼容别名。
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Dict, Optional, Tuple

from app.core.config import logger as default_logger
from app.utils.human_mouse import cdp_precise_click, smooth_move_mouse
from app.services.cf_turnstile_solver import (
    DEFAULT_FIND_CLICK_POINT_JS as _FIND_CLICK_POINT_JS,
    DEFAULT_PAGE_READY_PROBE_JS as _PAGE_READY_PROBE_JS,
    _interruptible_sleep,
    _urls_match,
    _wait_for_challenge_resolved,
    _wait_for_clickable_point,
    solve_turnstile_challenge,
)

_DEFAULT_MAX_ATTEMPTS = 3


def _is_valid_arena_url(url: Optional[str]) -> bool:
    if not url or not isinstance(url, str):
        return False
    u = url.lower().strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    if "challenges.cloudflare.com" in u or "challenge-platform" in u:
        return False
    if "arena.ai" in u or "lmarena.ai" in u:
        return True
    return False


def _get_occupied_url(tab: Any, session: Any = None) -> str:
    """提取本次请求在过盾前绑定的实际占用 URL。"""
    if session is not None:
        occupied = getattr(session, "_request_occupied_url", None)
        if occupied and _is_valid_arena_url(occupied):
            return str(occupied).strip()
        last_known = getattr(session, "last_known_url", None)
        if last_known and _is_valid_arena_url(last_known):
            return str(last_known).strip()
    try:
        tab_url = str(getattr(tab, "url", "") or "").strip()
        if tab_url and _is_valid_arena_url(tab_url):
            return tab_url
    except Exception:
        pass
    return "https://arena.ai/code"


def solve_arena_turnstile_challenge(
    tab: Any,
    session: Any = None,
    logger: Any = None,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    raise_if_cancelled: Optional[Callable[[], None]] = None,
    fallback_coords: Optional[Tuple[int, int]] = None,
    click_timeout_sec: float = 6.0,
    resolve_timeout_sec: float = 8.0,
) -> Dict[str, Any]:
    """
    自动处理 Arena 页面 Cloudflare Turnstile / 5s 盾的核心流程。
    """
    # Fetch module-level smooth_move_mouse and cdp_precise_click in case patched by tests
    current_module = sys.modules.get(__name__)
    cur_smooth_move = getattr(current_module, "smooth_move_mouse", smooth_move_mouse)
    cur_cdp_click = getattr(current_module, "cdp_precise_click", cdp_precise_click)

    return solve_turnstile_challenge(
        tab=tab,
        session=session,
        is_valid_url_fn=_is_valid_arena_url,
        max_attempts=max_attempts,
        raise_if_cancelled=raise_if_cancelled,
        fallback_coords=fallback_coords,
        click_timeout_sec=click_timeout_sec,
        resolve_timeout_sec=resolve_timeout_sec,
        smooth_move_fn=cur_smooth_move,
        cdp_click_fn=cur_cdp_click,
        logger=logger or default_logger,
    )


__all__ = [
    "_FIND_CLICK_POINT_JS",
    "_PAGE_READY_PROBE_JS",
    "_get_occupied_url",
    "_interruptible_sleep",
    "_is_valid_arena_url",
    "_urls_match",
    "_wait_for_challenge_resolved",
    "_wait_for_clickable_point",
    "cdp_precise_click",
    "smooth_move_mouse",
    "solve_arena_turnstile_challenge",
]
