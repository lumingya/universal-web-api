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
    source = (Path(__file__).resolve().parents[1] / "static/js/dashboard-methods.js").read_text(encoding="utf-8")
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


def test_main_app_rejects_cross_origin_management_requests():
    import main
    from httpx import ASGITransport, AsyncClient

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
