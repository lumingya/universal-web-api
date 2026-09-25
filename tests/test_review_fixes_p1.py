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
