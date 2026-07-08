"""
app/services/config/cache.py - lightweight in-memory cache for config reads.
"""

import threading
import time
from typing import Any, Dict, Tuple


_CACHE_MISS = object()


class ConfigCache:
    """Small TTL cache used only for derived config read results."""

    def __init__(self, ttl: float = 5.0):
        try:
            ttl_value = float(ttl)
        except (TypeError, ValueError):
            ttl_value = 5.0
        self._ttl = max(0.0, ttl_value)
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._lock = threading.RLock()

    def get(self, key: str, default: Any = _CACHE_MISS) -> Any:
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return default

            value, created_at = entry
            if self._ttl and now - created_at > self._ttl:
                self._cache.pop(key, None)
                return default

            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = (value, time.monotonic())

    def invalidate(self, key: str = None) -> None:
        with self._lock:
            if key is None:
                self._cache.clear()
            else:
                self._cache.pop(key, None)
