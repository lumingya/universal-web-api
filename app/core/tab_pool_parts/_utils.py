from typing import Any

from app.utils.site_url import is_remote_site_url


_POOL_SKIP_URL_PREFIXES = (
    "about:",
    "chrome://",
    "chrome-devtools://",
    "devtools://",
    "edge://",
    "brave://",
    "javascript:",
    "data:",
    "blob:",
)

_POOL_SKIP_URL_CONTAINS = (
    "chrome-error://",
    "about:neterror",
)

_TAB_HEALTH_CACHE_TTL_SEC = 5.0


def _looks_like_transient_local_debug_error(error: Any) -> bool:
    text = str(error or "").strip().lower()
    if not text:
        return False
    if "winerror 10048" in text:
        return True
    if "max retries exceeded" in text and "127.0.0.1" in text and "/json" in text:
        return True
    if "failed to establish a new connection" in text and "127.0.0.1" in text:
        return True
    return False


def _should_skip_pool_url(url: str) -> bool:
    raw = str(url or "").strip()
    if not raw:
        return True

    lowered = raw.lower()
    if lowered.startswith(_POOL_SKIP_URL_PREFIXES):
        return True
    if any(marker in lowered for marker in _POOL_SKIP_URL_CONTAINS):
        return True

    return not is_remote_site_url(lowered)


