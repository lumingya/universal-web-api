"""审查修复回归测试 · 第一批（P1）：S13 / S1 / H1 / S8 / S9 / S12。

全部使用合成数据、进程内 ASGI、临时目录；不依赖真实浏览器或网络。
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.core import http_security as hs


SYNTH_TOKEN = "synthetic-token-DO-NOT-USE-123"


# ---------------------------------------------------------------------------
# S13 · 备份脱敏 + 鉴权
# ---------------------------------------------------------------------------

def test_redact_env_for_backup_strips_secrets_and_credential_urls():
    env = {
        "APP_PORT": 8199,
        "AUTH_ENABLED": True,
        "AUTH_TOKEN": SYNTH_TOKEN,
        "DASHBOARD_AUTH_TOKEN": SYNTH_TOKEN,
        "HELPER_API_KEY": "sk-synthetic",
        "NGROK_AUTH_PASSWORD": "pw",
        "CLASH_SECRET": "s",
        "PROXY_ADDRESS": "http://user:pass@127.0.0.1:7890",
        "PROXY_BYPASS": "localhost,127.0.0.1",
        "HELPER_MODEL": "gemini",
    }
    safe, removed = hs.redact_env_for_backup(env)
    assert SYNTH_TOKEN not in json.dumps(safe)
    assert "user:pass" not in json.dumps(safe)
    assert set(removed) == {
        "AUTH_TOKEN", "DASHBOARD_AUTH_TOKEN", "HELPER_API_KEY",
        "NGROK_AUTH_PASSWORD", "CLASH_SECRET", "PROXY_ADDRESS",
    }
    assert safe["APP_PORT"] == 8199 and safe["AUTH_ENABLED"] is True
    assert safe["PROXY_BYPASS"] == "localhost,127.0.0.1"


def _backup_app():
    from fastapi import FastAPI
    from app.api import system

    app = FastAPI()
    app.include_router(system.router)
    return app


def _request(app, method, url, *, client=("127.0.0.1", 50000), headers=None, json_body=None):
    from httpx import ASGITransport, AsyncClient

    async def run():
        transport = ASGITransport(app=app, client=client)
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8199") as c:
            return await c.request(method, url, headers=headers or {}, json=json_body)

    return asyncio.run(run())


@pytest.fixture
def synthetic_env_dir(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        f"APP_PORT=8199\nAUTH_TOKEN={SYNTH_TOKEN}\nHELPER_API_KEY=sk-synthetic\nHELPER_MODEL=x\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for name in ("AUTH_ENABLED", "AUTH_TOKEN", "DASHBOARD_AUTH_ENABLED", "DASHBOARD_AUTH_TOKEN",
                 hs.PROXY_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_backup_export_never_contains_env_secrets(synthetic_env_dir):
    resp = _request(_backup_app(), "GET", "/api/settings/backup")
    assert resp.status_code == 200
    body = resp.json()
    assert SYNTH_TOKEN not in resp.text and "sk-synthetic" not in resp.text
    assert body["files"]["env"]["HELPER_MODEL"] == "x"
    assert body["secrets_redacted"] is True
    assert "AUTH_TOKEN" in body["redacted_env_keys"]


def test_backup_without_dashboard_auth_rejects_remote_and_tunneled_clients(synthetic_env_dir):
    app = _backup_app()
    remote = _request(app, "GET", "/api/settings/backup", client=("203.0.113.9", 4000))
    assert remote.status_code == 403
    tunneled = _request(app, "GET", "/api/settings/backup",
                        headers={"X-Forwarded-For": "203.0.113.9"})
    assert tunneled.status_code == 403
    imported = _request(app, "POST", "/api/settings/backup", client=("203.0.113.9", 4000),
                        json_body={"files": {"env": {"HELPER_MODEL": "y"}}})
    assert imported.status_code == 403


def test_backup_with_dashboard_auth_requires_token(synthetic_env_dir, monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_ENABLED", "true")
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", SYNTH_TOKEN)
    app = _backup_app()
    assert _request(app, "GET", "/api/settings/backup").status_code == 401
    ok = _request(app, "GET", "/api/settings/backup",
                  client=("203.0.113.9", 4000),
                  headers={"Authorization": f"Bearer {SYNTH_TOKEN}"})
    assert ok.status_code == 200
    assert SYNTH_TOKEN not in ok.text


def test_backup_import_ignores_redaction_placeholders(synthetic_env_dir, monkeypatch):
    from app.api import system

    monkeypatch.setattr(system, "_schedule_service_restart", lambda *_a, **_k: None)
    resp = _request(_backup_app(), "POST", "/api/settings/backup",
                    json_body={"files": {"env": {"AUTH_TOKEN": "***", "HELPER_MODEL": "y"}}})
    assert resp.status_code == 200, resp.text
    text = (synthetic_env_dir / ".env").read_text(encoding="utf-8")
    assert f"AUTH_TOKEN={SYNTH_TOKEN}" in text
    assert "HELPER_MODEL=y" in text


def test_backup_import_rejects_newline_injection(synthetic_env_dir):
    resp = _request(_backup_app(), "POST", "/api/settings/backup",
                    json_body={"files": {"env": {"HELPER_MODEL": "x\nDASHBOARD_AUTH_ENABLED=false"}}})
    assert resp.status_code == 400


def test_frontend_backup_does_not_export_local_tokens():
    from tests._dashboard_js import dashboard_methods_source

    source = dashboard_methods_source()  # R2-8：dashboard-methods 已按主题拆分
    start = source.index("getDashboardPreferencesBackup()")
    end = source.index("applyDashboardPreferencesBackup(", start)
    block = source[start:end]
    assert "getStoredDashboardToken" not in block
    assert "dashboard_token:" not in block and "api_token:" not in block


# ---------------------------------------------------------------------------
# S1 · 控制面默认开放 → Origin 守卫 + CORS 默认不放行
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("origin,host,allowed,forwarded,expected", [
    (None, "127.0.0.1:8199", [], None, True),                       # 非浏览器客户端
    ("http://127.0.0.1:8199", "127.0.0.1:8199", [], None, True),     # 同源面板
    ("http://LOCALHOST:8199", "localhost:8199", [], None, True),
    ("https://evil.example", "127.0.0.1:8199", [], None, False),
    ("null", "127.0.0.1:8199", [], None, False),                     # 沙箱 iframe / file://
    ("http://127.0.0.1:9999", "127.0.0.1:8199", [], None, False),    # 同主机不同端口
    ("https://app.example.com", "127.0.0.1:8199", ["https://app.example.com"], None, True),
    ("https://evil.example", "127.0.0.1:8199", ["*"], None, True),   # 用户显式 *
    ("https://api.example.com", "127.0.0.1:8199", [], "api.example.com", True),  # 反代
    ("https://api.example.com:443", "api.example.com", [], None, True),          # 默认端口
])
def test_origin_is_allowed(origin, host, allowed, forwarded, expected):
    assert hs.origin_is_allowed(origin, host, allowed, forwarded_host=forwarded) is expected


def test_cors_origins_default_is_empty(monkeypatch):
    from app.core.config import AppConfig

    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert AppConfig.get_cors_origins() == []
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example/, https://b.example")
    assert AppConfig.get_cors_origins() == ["https://a.example", "https://b.example"]
    monkeypatch.setenv("CORS_ORIGINS", "*")
    assert AppConfig.get_cors_origins() == ["*"]


def test_main_app_rejects_cross_origin_management_requests(monkeypatch):
    import main
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setattr(main, "_cors_origins", [])

    async def run():
        async with AsyncClient(transport=ASGITransport(app=main.app),
                               base_url="http://127.0.0.1:8199") as c:
            read = await c.get("/api/commands", headers={"Origin": "https://evil.example"})
            write = await c.post("/api/settings/env", content="x",
                                 headers={"Origin": "https://evil.example",
                                          "Content-Type": "text/plain"})
            preflight = await c.options("/api/commands", headers={
                "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
            return read, write, preflight

    read, write, preflight = asyncio.run(run())
    for resp in (read, write, preflight):
        assert resp.status_code == 403
        assert resp.headers.get("access-control-allow-origin") is None


# ---------------------------------------------------------------------------
# H1 · 模板 / 布尔解析 / 启动期拒绝不安全组合
# ---------------------------------------------------------------------------

def test_auth_switch_placeholder_fails_closed(monkeypatch):
    from app.core.config import AppConfig

    monkeypatch.setenv("AUTH_ENABLED", "your-secret-here")
    monkeypatch.delenv("DASHBOARD_AUTH_ENABLED", raising=False)
    assert AppConfig.is_auth_enabled() is True
    assert AppConfig.is_dashboard_auth_enabled() is True
    monkeypatch.setenv("AUTH_ENABLED", "off")
    assert AppConfig.is_auth_enabled() is False


def test_startup_security_errors_matrix():
    ok_local = hs.startup_security_errors(host="127.0.0.1", env={})
    assert ok_local == []

    bad_bool = hs.startup_security_errors(host="127.0.0.1", env={"DASHBOARD_AUTH_ENABLED": "your-secret-here"})
    assert len(bad_bool) == 1 and "DASHBOARD_AUTH_ENABLED" in bad_bool[0]

    public_no_auth = hs.startup_security_errors(host="0.0.0.0", env={})
    assert len(public_no_auth) == 2

    public_enabled_no_token = hs.startup_security_errors(
        host="0.0.0.0", env={"AUTH_ENABLED": "true"})
    assert len(public_enabled_no_token) == 2

    public_ok = hs.startup_security_errors(
        host="0.0.0.0", env={"AUTH_ENABLED": "true", "AUTH_TOKEN": "a", "DASHBOARD_AUTH_TOKEN": "b"})
    assert public_ok == []

    override = hs.startup_security_errors(host="192.168.1.5", env={hs.INSECURE_OVERRIDE_ENV: "true"})
    assert override == []

    assert hs.startup_security_errors(host="::1", env={}) == []
    assert hs.startup_security_errors(host="localhost", env={}) == []


def test_main_lifespan_refuses_public_bind_without_auth(monkeypatch):
    import main

    monkeypatch.setenv("APP_HOST", "0.0.0.0")
    monkeypatch.delenv("UWAPI_PUBLIC_BIND_HOST", raising=False)
    for name in ("AUTH_ENABLED", "AUTH_TOKEN", "DASHBOARD_AUTH_ENABLED", "DASHBOARD_AUTH_TOKEN",
                 hs.INSECURE_OVERRIDE_ENV):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(main.InsecureStartupConfigError):
        main._enforce_secure_startup_config()

    # handoff 代理场景：子进程绑回环，但公开地址由启动器传入
    monkeypatch.setenv("APP_HOST", "127.0.0.1")
    monkeypatch.setenv("UWAPI_PUBLIC_BIND_HOST", "0.0.0.0")
    with pytest.raises(main.InsecureStartupConfigError):
        main._enforce_secure_startup_config()


def test_start_launcher_loads_security_module_without_app_package():
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("_start_under_test", root / "start.py")
    start = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(start)
    module = start._load_http_security_module()
    assert module.startup_security_errors(host="0.0.0.0", env={})


def test_env_example_is_safe_to_copy():
    root = Path(__file__).resolve().parents[1]
    env = {}
    for line in (root / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            assert key.strip() not in env, f"重复键 {key}"
            env[key.strip()] = value.strip()
    assert "your-secret-here" not in env.values()
    assert env["APP_HOST"] == "127.0.0.1"
    assert env["CORS_ORIGINS"] == ""
    assert env["APP_DEBUG"] == "false"
    assert hs.startup_security_errors(env=env) == []
    for key, value in env.items():
        if hs.is_secret_env_key(key):
            assert value == "", f"模板不应包含示例秘密值: {key}"


# ---------------------------------------------------------------------------
# S8 / S9 · 活动内容落盘与同源提供
# ---------------------------------------------------------------------------

from app.utils import media_safety as ms  # noqa: E402

SVG_PAYLOAD = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>' + b" " * 1200
HTML_PAYLOAD = b"<!doctype html><html><script>alert(document.domain)</script></html>" + b" " * 1200


@pytest.mark.parametrize("kind,ctype,url,expected", [
    ("video", "video/custom", "https://cdn.example/clip.html", ".mp4"),   # S9 复现用例
    ("video", "video/mp4", "https://cdn.example/clip.html", ".mp4"),
    ("audio", "audio/x-unknown", "https://cdn.example/a.svg", ".mp3"),
    ("audio", "audio/x-unknown", "https://cdn.example/a.flac", ".flac"),
    ("image", "image/svg+xml", "https://cdn.example/x.svg", None),        # S8
    ("image", "text/html", "https://cdn.example/x.png", None),
    ("video", "image/png", "https://cdn.example/x.mp4", None),            # 大类不符
    ("image", "image/jpeg", None, ".jpg"),
    ("other", "video/mp4", None, None),
])
def test_choose_media_extension_whitelist(kind, ctype, url, expected):
    assert ms.choose_media_extension(kind, ctype, url) == expected


def test_looks_like_active_content_sniffing():
    assert ms.looks_like_active_content(SVG_PAYLOAD)
    assert ms.looks_like_active_content(b"\xef\xbb\xbf  \n<HTML>")
    assert ms.looks_like_active_content(b"<?xml version='1.0'?><svg/>")
    assert not ms.looks_like_active_content(b"\x89PNG\r\n\x1a\n....")
    assert not ms.looks_like_active_content(b"\x00\x00\x00\x18ftypmp42")
    assert not ms.looks_like_active_content(b"ID3\x03\x00")


def _fake_response(content_type, body):
    class Response:
        status_code = 200
        headers = {"Content-Type": content_type}

        def iter_content(self, chunk_size=65536):
            yield body

        def close(self):
            return None

    return Response()


@pytest.fixture
def media_mixin(tmp_path, monkeypatch):
    from app.core.browser import media as media_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(media_module, "build_image_download_request_context",
                        lambda _tab, accept="*/*": ({}, {"Accept": accept}))
    return media_module


def test_s9_html_disguised_as_video_is_not_persisted(media_mixin, tmp_path, monkeypatch):
    monkeypatch.setattr(media_mixin, "get_public_remote_resource",
                        lambda url, **kw: _fake_response("video/custom", HTML_PAYLOAD))
    item = {"kind": "url", "media_type": "video", "url": "https://cdn.example/clip.html"}
    result = media_mixin.BrowserMediaMixin()._persist_remote_media_urls_to_local([item], tab=object())
    assert result[0]["url"] == item["url"]          # 保留远程链接
    saved = list((tmp_path / "download_images").glob("*"))
    assert saved == []                              # 未落盘，且失败清理生效


def test_s9_real_video_with_html_suffix_is_saved_as_mp4(media_mixin, tmp_path, monkeypatch):
    monkeypatch.setattr(media_mixin, "get_public_remote_resource",
                        lambda url, **kw: _fake_response("video/custom", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64))
    item = {"kind": "url", "media_type": "video", "url": "https://cdn.example/clip.html"}
    result = media_mixin.BrowserMediaMixin()._persist_remote_media_urls_to_local([item], tab=object())
    assert result[0]["url"].startswith("/media/") and result[0]["url"].endswith(".mp4")
    assert [p.suffix for p in (tmp_path / "download_images").glob("*")] == [".mp4"]


def test_s8_svg_data_uri_is_not_persisted(media_mixin, tmp_path):
    import base64
    svg_uri = "data:image/svg+xml;base64," + base64.b64encode(SVG_PAYLOAD).decode()
    lying_uri = "data:image/png;base64," + base64.b64encode(SVG_PAYLOAD).decode()
    items = [{"kind": "data_uri", "media_type": "image", "data_uri": svg_uri},
             {"kind": "data_uri", "media_type": "image", "data_uri": lying_uri}]
    result = media_mixin.BrowserMediaMixin()._persist_data_uri_media_to_local(items)
    assert [r["kind"] for r in result] == ["data_uri", "data_uri"]
    assert list((tmp_path / "download_images").glob("*")) == []


def test_s8_foreground_image_fallback_no_longer_maps_svg():
    source = (Path(__file__).resolve().parents[1] / "app/core/browser/media.py").read_text(encoding="utf-8")
    assert '"image/svg+xml": ".svg"' not in source
    assert "image/svg+xml" not in ms.IMAGE_EXT_BY_MIME


@pytest.mark.parametrize("name,expected_type,expected_disp", [
    ("legacy.svg", "application/octet-stream", "attachment"),
    ("legacy.html", "application/octet-stream", "attachment"),
    ("ok.png", "image/png", "inline"),
    ("ok.mp4", "video/mp4", "inline"),
])
def test_media_delivery_policy(name, expected_type, expected_disp):
    assert ms.resolve_media_delivery(Path(name)) == (expected_type, expected_disp)


def test_served_media_is_hardened_for_legacy_active_files():
    import main
    from httpx import ASGITransport, AsyncClient

    base = Path("download_images")
    base.mkdir(exist_ok=True)
    names = {"_review_s8_legacy.svg": SVG_PAYLOAD, "_review_s9_legacy.html": HTML_PAYLOAD,
             "_review_ok.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 32}
    for name, data in names.items():
        (base / name).write_bytes(data)
    try:
        async def run():
            async with AsyncClient(transport=ASGITransport(app=main.app),
                                   base_url="http://127.0.0.1:8199") as c:
                return {
                    "static_svg": await c.get("/download_images/_review_s8_legacy.svg"),
                    "media_html": await c.get("/media/_review_s9_legacy.html"),
                    "static_html": await c.get("/download_images/_review_s9_legacy.html"),
                    "png": await c.get("/download_images/_review_ok.png"),
                }

        res = asyncio.run(run())
        for key in ("static_svg", "media_html", "static_html"):
            r = res[key]
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("application/octet-stream"), key
            assert r.headers["content-disposition"].startswith("attachment"), key
            assert r.headers["x-content-type-options"] == "nosniff"
            assert "sandbox" in r.headers["content-security-policy"]
        png = res["png"]
        assert png.headers["content-type"] == "image/png"
        assert png.headers["x-content-type-options"] == "nosniff"
    finally:
        for name in names:
            (base / name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# S12 · 默认响应调试抓取
# ---------------------------------------------------------------------------

def test_tracked_browser_config_disables_network_debug_capture():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "config/browser_config.json").read_text(encoding="utf-8"))
    assert data["NETWORK_DEBUG_CAPTURE_ENABLED"] is False


@pytest.mark.parametrize("value,expected", [
    (False, False), (True, True), ("false", False), ("False", False), ("0", False),
    ("", False), (None, False), ("off", False), ("true", True), ("on", True), (1, True), (0, False),
    ("garbage", False),
])
def test_network_debug_capture_strict_bool(monkeypatch, value, expected):
    from app.core import network_monitor as nm

    monkeypatch.setattr(nm.BrowserConstants, "get",
                        classmethod(lambda cls, key: value if key == "NETWORK_DEBUG_CAPTURE_ENABLED" else None))
    assert nm.NetworkMonitor._is_network_debug_capture_enabled() is expected


def test_network_debug_dir_retention_deletes_expired_snapshots(tmp_path):
    import os
    import time as _time
    from app.core.network_monitor import trim_network_parser_debug_dir

    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    keep = tmp_path / "active.json"
    for p in (old, new, keep):
        p.write_text("{}", encoding="utf-8")
    past = _time.time() - 3 * 3600
    os.utime(old, (past, past))
    os.utime(keep, (past, past))
    deleted = trim_network_parser_debug_dir(
        target_dir=tmp_path, max_total_bytes=10 * 1024 * 1024,
        exclude_paths={keep}, max_age_seconds=3600,
    )
    assert deleted == 1
    assert not old.exists() and new.exists() and keep.exists()
