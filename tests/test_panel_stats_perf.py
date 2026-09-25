"""P0-7: panel memory stats avoid memory_full_info(); counters stay live under the 5s cache."""

from __future__ import annotations

import time
from types import SimpleNamespace

from app.api import system


class _Proc:
    def __init__(self, info, full=None):
        self._info = info
        self._full = full
        self.full_calls = 0

    def memory_info(self):
        return self._info

    def memory_full_info(self):
        self.full_calls += 1
        return self._full


def test_fast_metric_uses_cheap_fields(monkeypatch):
    monkeypatch.delenv("PANEL_MEMORY_METRIC", raising=False)
    win = _Proc(SimpleNamespace(rss=500, private=300), SimpleNamespace(uss=250))
    linux = _Proc(SimpleNamespace(rss=500, shared=120), SimpleNamespace(uss=260))
    other = _Proc(SimpleNamespace(rss=500), SimpleNamespace(uss=270))
    assert system._get_process_private_memory_bytes(win) == 300
    assert system._get_process_private_memory_bytes(linux) == 380
    assert system._get_process_private_memory_bytes(other) == 500
    assert win.full_calls == linux.full_calls == other.full_calls == 0


def test_uss_and_rss_modes(monkeypatch):
    proc = _Proc(SimpleNamespace(rss=500, shared=120), SimpleNamespace(uss=260))
    monkeypatch.setenv("PANEL_MEMORY_METRIC", "uss")
    assert system._get_process_private_memory_bytes(proc) == 260
    monkeypatch.setenv("PANEL_MEMORY_METRIC", "rss")
    assert system._get_process_private_memory_bytes(proc) == 500


def test_cached_stats_keep_request_counters_live(monkeypatch):
    monkeypatch.setitem(system._SYSTEM_STATS_CACHE, "payload", {"memory_mb": 1.0, "running_count": 0, "total_requests": 1})
    monkeypatch.setitem(system._SYSTEM_STATS_CACHE, "expires_at", time.monotonic() + 5)
    monkeypatch.setattr(system, "_get_live_request_counts", lambda: {"running_count": 3, "queued_count": 2})
    monkeypatch.setattr(system.request_manager, "total_requests", 42, raising=False)
    payload = system._get_system_stats_payload_cached()
    assert payload["memory_mb"] == 1.0
    assert payload["running_count"] == 3 and payload["queued_count"] == 2
    assert payload["total_requests"] == 42
