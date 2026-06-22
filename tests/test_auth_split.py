import asyncio

import pytest
from fastapi import HTTPException

from app.api import deps
from app.core.config import AppConfig


def _clear_auth_env(monkeypatch):
    for key in (
        "AUTH_ENABLED",
        "AUTH_TOKEN",
        "DASHBOARD_AUTH_ENABLED",
        "DASHBOARD_AUTH_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


def test_dashboard_auth_falls_back_to_service_auth_for_legacy_env(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_TOKEN", "legacy-secret")

    assert AppConfig.is_dashboard_auth_enabled() is True
    assert AppConfig.get_dashboard_auth_token() == "legacy-secret"
    assert asyncio.run(deps.verify_dashboard_auth("Bearer legacy-secret")) is True


def test_dashboard_auth_uses_separate_token_when_configured(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_TOKEN", "service-secret")
    monkeypatch.setenv("DASHBOARD_AUTH_ENABLED", "true")
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "panel-secret")

    assert asyncio.run(deps.verify_dashboard_auth("Bearer panel-secret")) is True
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(deps.verify_dashboard_auth("Bearer service-secret"))
    assert exc_info.value.status_code == 401


def test_service_auth_does_not_accept_dashboard_token(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_TOKEN", "service-secret")
    monkeypatch.setenv("DASHBOARD_AUTH_ENABLED", "true")
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "panel-secret")

    assert asyncio.run(deps.verify_service_auth("Bearer service-secret")) is True
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(deps.verify_service_auth("Bearer panel-secret"))
    assert exc_info.value.status_code == 401
