import time

from app.services.config.cache import ConfigCache


_MISS = object()


def test_config_cache_preserves_none_until_invalidated():
    cache = ConfigCache(ttl=5.0)

    cache.set("default", None)

    assert cache.get("default", _MISS) is None
    cache.invalidate("default")
    assert cache.get("default", _MISS) is _MISS


def test_config_cache_expires_entries():
    cache = ConfigCache(ttl=0.01)
    cache.set("site", {"value": 1})

    assert cache.get("site", _MISS) == {"value": 1}
    time.sleep(0.02)
    assert cache.get("site", _MISS) is _MISS
