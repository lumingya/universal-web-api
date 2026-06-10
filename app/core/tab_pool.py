"""
app/core/tab_pool.py - compatibility facade for tab pool modules.

The implementation lives in app.core.tab_pool_parts.
"""

from .tab_pool_parts._utils import (
    _POOL_SKIP_URL_CONTAINS,
    _POOL_SKIP_URL_PREFIXES,
    _TAB_HEALTH_CACHE_TTL_SEC,
    _looks_like_transient_local_debug_error,
    _should_skip_pool_url,
)
from .tab_pool_parts.clipboard import _clipboard_lock, get_clipboard_lock
from .tab_pool_parts.manager import TabPoolManager
from .tab_pool_parts.network import _GlobalNetworkInterceptionManager, _GlobalNetworkWorker
from .tab_pool_parts.session import TabSession, TabStatus

__all__ = [
    "TabStatus",
    "TabSession",
    "TabPoolManager",
    "get_clipboard_lock",
]
