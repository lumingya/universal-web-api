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


# ---------------------------------------------------------------------------
# B4 · n>1 明确拒绝而不是静默只回 1 个 choice
# ---------------------------------------------------------------------------

def test_b4_chat_request_rejects_n_greater_than_one():
    from pydantic import ValidationError

    from app.api.chat import ChatRequest

    msgs = [{"role": "user", "content": "hi"}]
    assert ChatRequest(messages=msgs).n == 1
    assert ChatRequest(messages=msgs, n=1).n == 1
    assert ChatRequest(messages=msgs, n=None).n is None
    with pytest.raises(ValidationError) as exc:
        ChatRequest(messages=msgs, n=3)
    assert "n=1" in str(exc.value)


def test_b4_api_returns_openai_style_422(monkeypatch):
    import asyncio

    import main
    from httpx import ASGITransport, AsyncClient

    for name in ("AUTH_ENABLED", "AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    async def run():
        async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://127.0.0.1:8199") as c:
            return await c.post("/v1/chat/completions",
                                json={"model": "x", "messages": [{"role": "user", "content": "hi"}], "n": 2})

    resp = asyncio.run(run())
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["type"] == "invalid_request_error" and "n=1" in err["message"]


# ---------------------------------------------------------------------------
# H7 · 注解里的 Optional/Any 必须有导入（get_type_hints 不再 NameError）
# ---------------------------------------------------------------------------

def test_h7_annotations_resolvable():
    import typing

    import start
    from app.utils import model_routing

    for mod in (start, model_routing):
        for obj in vars(mod).values():
            if callable(obj) and getattr(obj, "__module__", None) == mod.__name__ and not isinstance(obj, type):
                typing.get_type_hints(obj)  # 旧代码对缺失导入会抛 NameError


# ---------------------------------------------------------------------------
# H8 · chat.py 的 Arena 错误辅助函数只定义一次
# ---------------------------------------------------------------------------

def test_h8_arena_helpers_defined_once():
    import ast

    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "chat.py").read_text(encoding="utf-8")
    names = [n.name for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)]
    for helper in (
        "_is_arena_prompt_rejection", "_is_arena_non_retryable",
        "_is_arena_prompt_rejection_payload", "_is_arena_non_retryable_payload",
        "_arena_prompt_rejection_response", "_arena_non_retryable_response",
    ):
        assert names.count(helper) == 1, helper


# ---------------------------------------------------------------------------
# H10 · stream_monitor 不再有被覆盖的重复方法（保留后一版严格实现）
# ---------------------------------------------------------------------------

def test_h10_stream_monitor_methods_defined_once():
    import ast

    src = (Path(__file__).resolve().parents[1] / "app" / "core" / "stream_monitor.py").read_text(encoding="utf-8")
    cls = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "StreamMonitor")
    names = [n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert names.count("_is_arena_page") == 1
    assert names.count("_arena_native_stop_present") == 1


@pytest.mark.parametrize("url,expected", [
    ("https://lmarena.ai/c/abc", True),
    ("https://arena.ai/c/abc", True),
    ("https://notarena.ai.evil.example/", False),
    ("https://example.com/?next=lmarena.ai", False),
])
def test_h10_is_arena_page_uses_strict_matcher(url, expected):
    from types import SimpleNamespace

    from app.core.stream_monitor import StreamMonitor, is_arena_page_url

    fake = SimpleNamespace(tab=SimpleNamespace(url=url))
    assert StreamMonitor._is_arena_page(fake) == is_arena_page_url(url)
    assert StreamMonitor._is_arena_page(fake) is expected
