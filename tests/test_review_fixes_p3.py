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


# ---------------------------------------------------------------------------
# B7 · 脚本热加载：mtime 相同但内容被替换时必须重读
# ---------------------------------------------------------------------------

def _loader():
    from app.core.workflow import script_loader as sl

    cls = next(v for v in vars(sl).values()
               if isinstance(v, type) and hasattr(v, "load_script_content") and v.__module__ == sl.__name__)
    return cls()


def test_b7_same_mtime_replaced_content_is_reloaded(tmp_path):
    loader = _loader()
    script = tmp_path / "a.js"
    script.write_text("first", encoding="utf-8")
    old = time.time_ns() - 10_000_000_000  # 10s 前，避开 racy 窗口
    os.utime(script, ns=(old, old))
    assert loader.load_script_content(script) == "first"
    # 同一 mtime、不同内容且长度不同
    script.write_text("later-content", encoding="utf-8")
    os.utime(script, ns=(old, old))
    assert loader.load_script_content(script) == "later-content"


def test_b7_same_mtime_same_size_atomic_replace_is_reloaded(tmp_path):
    loader = _loader()
    script = tmp_path / "a.js"
    script.write_text("AAAA", encoding="utf-8")
    old = time.time_ns() - 10_000_000_000
    os.utime(script, ns=(old, old))
    assert loader.load_script_content(script) == "AAAA"
    replacement = tmp_path / "a.js.tmp"
    replacement.write_text("BBBB", encoding="utf-8")
    os.utime(replacement, ns=(old, old))
    os.replace(replacement, script)  # 新 inode，mtime/size 均相同
    assert loader.load_script_content(script) == "BBBB"


def test_b7_unchanged_file_served_from_cache(tmp_path, monkeypatch):
    loader = _loader()
    script = tmp_path / "a.js"
    script.write_text("cached", encoding="utf-8")
    old = time.time_ns() - 10_000_000_000
    os.utime(script, ns=(old, old))
    assert loader.load_script_content(script) == "cached"
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("should hit cache"))
    assert loader.load_script_content(script) == "cached"
