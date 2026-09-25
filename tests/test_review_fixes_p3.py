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


# ---------------------------------------------------------------------------
# H13 · 未被引用的存储 mixin 已删除，CRUD 只在 CommandEngine 中维护
# ---------------------------------------------------------------------------

def test_h13_dead_storage_mixin_removed():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "app" / "services" / "command_engine_storage.py").exists()
    from app.services.command_engine import CommandEngine

    for name in ("_save_commands", "_load_commands", "add_command", "update_command", "delete_command"):
        assert callable(getattr(CommandEngine, name, None)), name


# ---------------------------------------------------------------------------
# T2 · 测试用到的 httpx 必须写进 requirements-dev.txt
# ---------------------------------------------------------------------------

def test_t2_httpx_declared_in_dev_requirements():
    import re

    text = (Path(__file__).resolve().parents[1] / "requirements-dev.txt").read_text(encoding="utf-8")
    assert re.search(r"(?m)^httpx\b", text)


# ---------------------------------------------------------------------------
# S14 · 教程页搜索结果 / TOC 拼 innerHTML 前转义
# ---------------------------------------------------------------------------

def test_s14_tutorial_search_escapes_user_input():
    html = (Path(__file__).resolve().parents[1] / "static" / "tutorial" / "index.html").read_text(encoding="utf-8")
    assert "const escHtml=" in html
    assert "${searchInput.value.trim()}" not in html
    assert "${escHtml(searchInput.value.trim())}" in html
    assert "${escHtml(h.textContent)}" in html
    for raw in ("${s.title}", "${s.subs.slice(0,60)}", "${h.textContent}</a>"):
        assert raw not in html, raw


# ---------------------------------------------------------------------------
# S7 · 公开 /health 与引导数据最小化；本机直连 / 有效令牌才给详情（不强制令牌）
# ---------------------------------------------------------------------------

class _FakeBrowser:
    def __init__(self, connected=True):
        self._connected = connected
        self.browser_handle = object() if connected else None
        self.health_calls = 0

    def health_check(self):
        self.health_calls += 1
        return {"status": "healthy", "connected": self._connected, "port": 9222,
                "tab_pool": {"total": 3}, "error": None}


def _s7_get(monkeypatch, path, client_host, headers=None, env=None, browser=None):
    import asyncio

    import main
    from app.api import system
    from httpx import ASGITransport, AsyncClient

    for name in ("AUTH_TOKEN", "DASHBOARD_AUTH_TOKEN", "AUTH_ENABLED", "TRUST_PROXY_HEADERS"):
        monkeypatch.delenv(name, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    fake = browser or _FakeBrowser()
    monkeypatch.setattr(system, "get_browser", lambda *a, **k: fake)

    async def run():
        transport = ASGITransport(app=main.app, client=(client_host, 40000))
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            return await c.get(path, headers=headers or {})

    return asyncio.run(run()), fake


def test_s7_remote_anonymous_health_is_minimal_and_passive(monkeypatch):
    resp, fake = _s7_get(monkeypatch, "/health", "203.0.113.9")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "healthy" and body["browser"] == {"connected": True}
    assert "dashboard_auth_enabled" in body["config"]
    for leaked in ("version", "request_manager"):
        assert leaked not in body
    assert "sites_loaded" not in body["config"]
    assert fake.health_calls == 0  # 不触发浏览器连接


def test_s7_remote_anonymous_health_keeps_503_when_disconnected(monkeypatch):
    resp, _ = _s7_get(monkeypatch, "/health", "203.0.113.9", browser=_FakeBrowser(connected=False))
    assert resp.status_code == 503 and resp.json()["browser"] == {"connected": False}


def test_s7_local_direct_health_gets_details(monkeypatch):
    resp, fake = _s7_get(monkeypatch, "/health", "127.0.0.1")
    body = resp.json()
    assert "version" in body and "request_manager" in body
    assert body["browser"]["tab_pool"] == {"total": 3}
    assert fake.health_calls == 1


def test_s7_local_but_forwarded_is_treated_as_remote(monkeypatch):
    resp, _ = _s7_get(monkeypatch, "/health", "127.0.0.1", headers={"X-Forwarded-For": "198.51.100.7"})
    assert "version" not in resp.json()


@pytest.mark.parametrize("header", [
    {"Authorization": "Bearer svc-token"},
    {"X-API-Key": "svc-token"},
    {"Authorization": "Bearer dash-token"},
])
def test_s7_remote_with_valid_token_gets_details(monkeypatch, header):
    env = {"AUTH_TOKEN": "svc-token", "DASHBOARD_AUTH_TOKEN": "dash-token"}
    resp, _ = _s7_get(monkeypatch, "/health", "203.0.113.9", headers=header, env=env)
    assert "version" in resp.json()


def test_s7_remote_with_wrong_token_still_minimal_not_401(monkeypatch):
    env = {"AUTH_TOKEN": "svc-token"}
    resp, _ = _s7_get(monkeypatch, "/health", "203.0.113.9", headers={"Authorization": "Bearer nope"}, env=env)
    assert resp.status_code == 200 and "version" not in resp.json()


def test_s7_guide_data_local_ok_remote_forbidden(monkeypatch):
    resp, _ = _s7_get(monkeypatch, "/api/startup/controlled-browser-guide-data", "127.0.0.1")
    assert resp.status_code == 200 and "sites" in resp.json()
    resp, _ = _s7_get(monkeypatch, "/api/startup/controlled-browser-guide-data", "203.0.113.9")
    assert resp.status_code == 403 and "sites" not in resp.json()
    resp, _ = _s7_get(monkeypatch, "/api/startup/controlled-browser-guide-data", "203.0.113.9",
                      headers={"Authorization": "Bearer t"}, env={"AUTH_TOKEN": "t"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# H14 · 站点配置 / 完整备份导入有文件大小上限
# ---------------------------------------------------------------------------

def test_h14_import_handlers_check_file_size():
    js = (Path(__file__).resolve().parents[1] / "static" / "js" / "dashboard-methods.js").read_text(encoding="utf-8")
    assert "SITE_CONFIG_IMPORT_MAX_BYTES" in js and "SETTINGS_BACKUP_IMPORT_MAX_BYTES" in js
    for handler, const in (("handleImportFile(event)", "SITE_CONFIG_IMPORT_MAX_BYTES"),
                           ("handleSettingsBackupImportFile(event)", "SETTINGS_BACKUP_IMPORT_MAX_BYTES")):
        body = js.split(handler, 1)[1]
        assert body.index(f"importFileSizeError(file, {const}") < body.index("new FileReader()")


def test_h14_size_error_helper_behaviour():
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    root = Path(__file__).resolve().parents[1]
    script = (
        "global.window={};global.localStorage={getItem(){return null}};"
        f"require({json.dumps(str(root / 'static/js/dashboard-methods.js'))});"
        "const f=window.importFileSizeError;"
        "console.log(JSON.stringify([f({size:1024},8*1024*1024,'配置文件'),f({size:9*1024*1024},8*1024*1024,'配置文件')]));"
    )
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    ok, too_big = json.loads(out.stdout.strip().splitlines()[-1])
    assert ok == ""
    assert "过大" in too_big and "8" in too_big


# ---------------------------------------------------------------------------
# H2 · README 版本号与 VERSION 一致，Markdown 相对链接指向存在的文件
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("readme", ["README.md", "README.zh-CN.md", "README.en.md"])
def test_h2_readme_version_and_local_links(readme):
    import re

    root = Path(__file__).resolve().parents[1]
    path = root / readme
    if not path.exists():
        pytest.skip(f"{readme} missing")
    text = path.read_text(encoding="utf-8")
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    assert f"**{version}**" in text
    for target in re.findall(r"\]\(\./([^)#\s]+)\)", text):
        assert (root / target).exists(), f"{readme} -> {target}"


# ---------------------------------------------------------------------------
# H3 · .gitignore 不再忽略已跟踪文件；白名单只列存在的测试
# ---------------------------------------------------------------------------

def _git(*args):
    import shutil
    import subprocess

    if not shutil.which("git"):
        pytest.skip("git not installed")
    root = Path(__file__).resolve().parents[1]
    if not (root / ".git").exists():
        pytest.skip("not a git checkout")
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30)


def test_h3_no_tracked_file_is_ignored():
    out = _git("ls-files", "-ci", "--exclude-standard")
    assert out.returncode == 0 and out.stdout.strip() == ""


def test_h3_test_whitelist_entries_exist():
    root = Path(__file__).resolve().parents[1]
    text = (root / ".gitignore").read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("!tests/") and "*" not in line:
            assert (root / line[1:]).exists(), line


# ---------------------------------------------------------------------------
# H11 · 随仓库分发的 browser_config.json 不含个人 Arena 会话 URL
# ---------------------------------------------------------------------------

def test_h11_shipped_browser_config_has_no_personal_sessions():
    import re

    root = Path(__file__).resolve().parents[1]
    raw = (root / "config" / "browser_config.json").read_text(encoding="utf-8")
    assert not re.search(r"https?://[^\"]*/c/[0-9a-f]{8}-[0-9a-f]{4}-", raw, re.I)
    cfg = json.loads(raw)
    assert cfg["tab_pool"]["route_groups"] == []
    assert isinstance(cfg["tab_pool"]["excluded_urls"], list)
