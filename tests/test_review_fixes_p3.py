"""审查修复回归测试（P3）。对应 docs/review/CODE_REVIEW_REPORT.md 第五节。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# B6 · 非有限数值配置不再 OverflowError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "Infinity", "1e400"])
def test_b6_non_finite_env_falls_back_to_default(monkeypatch, raw):
    from app.core.tab_pool_parts.idle_maintenance import IdleMaintenanceConfig

    monkeypatch.setenv("BROWSER_CDP_RECYCLE_AFTER_REQUESTS", raw)
    monkeypatch.setenv("BROWSER_CDP_RECYCLE_DOM_NODES", raw)
    monkeypatch.setenv("BROWSER_CDP_RECYCLE_INTERVAL_SEC", raw)
    cfg = IdleMaintenanceConfig.from_env()
    assert cfg.recycle_after_requests == 20
    assert cfg.recycle_dom_nodes == 150000
    assert cfg.recycle_interval_sec == 1800.0


def test_b6_zero_still_disables_and_valid_values_kept(monkeypatch):
    from app.core.tab_pool_parts.idle_maintenance import IdleMaintenanceConfig

    monkeypatch.setenv("BROWSER_CDP_RECYCLE_AFTER_REQUESTS", "0")
    monkeypatch.setenv("BROWSER_CDP_RECYCLE_DOM_NODES", "5000")
    cfg = IdleMaintenanceConfig.from_env()
    assert cfg.recycle_after_requests == 0
    assert cfg.recycle_dom_nodes == 5000
