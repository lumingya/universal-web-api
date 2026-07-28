import json

import pytest
import requests
from fastapi import HTTPException

from app.api import config_compare_support


class _FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self.headers = dict(headers or {})
        self._content = b"" if payload is None else json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def iter_content(self, chunk_size):
        del chunk_size
        if self._content:
            yield self._content

    def close(self):
        self.closed = True


def _prepare(monkeypatch, tmp_path, timestamps):
    monkeypatch.setattr(
        config_compare_support,
        "OFFICIAL_CONFIG_CACHE_DIR",
        tmp_path / "official-cache",
    )
    monkeypatch.setattr(
        config_compare_support,
        "_official_config_relative_path",
        lambda: "config/sites.json",
    )
    timestamp_iter = iter(timestamps)
    monkeypatch.setattr(
        config_compare_support,
        "_utc_now_text",
        lambda: next(timestamp_iter),
    )
    monkeypatch.setenv("GITHUB_REPO", "example/project")


def test_official_config_download_then_etag_validation(monkeypatch, tmp_path):
    _prepare(
        monkeypatch,
        tmp_path,
        ["2026-07-28T01:00:00Z", "2026-07-28T01:05:00Z"],
    )
    official_payload = {
        "_meta": {"version": 1},
        "example.com": {"presets": {"主预设": {"selectors": {}}}},
    }
    requests_seen = []
    responses = iter(
        [
            _FakeResponse(
                200,
                official_payload,
                {
                    "ETag": '"config-v1"',
                    "Last-Modified": "Tue, 28 Jul 2026 01:00:00 GMT",
                },
            ),
            _FakeResponse(304),
        ]
    )

    def fake_get(url, **kwargs):
        requests_seen.append((url, kwargs))
        return next(responses)

    monkeypatch.setattr(config_compare_support, "get_public_remote_resource", fake_get)

    downloaded = config_compare_support._load_official_sites_config()
    validated = config_compare_support._load_official_sites_config()

    assert downloaded["source"]["status"] == "remote"
    assert downloaded["source"]["stale"] is False
    assert downloaded["sites"] == {"example.com": official_payload["example.com"]}
    assert validated["source"]["status"] == "validated_cache"
    assert validated["source"]["checked_at"] == "2026-07-28T01:05:00Z"
    assert requests_seen[1][1]["headers"]["If-None-Match"] == '"config-v1"'
    assert requests_seen[1][1]["headers"]["If-Modified-Since"] == (
        "Tue, 28 Jul 2026 01:00:00 GMT"
    )


def test_official_config_uses_stale_cache_when_network_fails(monkeypatch, tmp_path):
    _prepare(
        monkeypatch,
        tmp_path,
        ["2026-07-28T02:00:00Z", "2026-07-28T02:10:00Z"],
    )
    official_payload = {"example.com": {"presets": {}}}
    responses = iter([_FakeResponse(200, official_payload, {"ETag": '"v1"'})])
    monkeypatch.setattr(
        config_compare_support,
        "get_public_remote_resource",
        lambda *args, **kwargs: next(responses),
    )
    config_compare_support._load_official_sites_config()

    def fail_request(*args, **kwargs):
        raise requests.ConnectionError("network unavailable")

    monkeypatch.setattr(
        config_compare_support,
        "get_public_remote_resource",
        fail_request,
    )
    fallback = config_compare_support._load_official_sites_config()

    assert fallback["sites"] == official_payload
    assert fallback["source"]["status"] == "cache_fallback"
    assert fallback["source"]["stale"] is True
    assert fallback["source"]["fetched_at"] == "2026-07-28T02:00:00Z"
    assert "network unavailable" in fallback["source"]["warning"]


def test_official_config_without_cache_returns_503(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path, ["2026-07-28T03:00:00Z"])

    def fail_request(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(
        config_compare_support,
        "get_public_remote_resource",
        fail_request,
    )

    with pytest.raises(HTTPException) as exc_info:
        config_compare_support._load_official_sites_config()

    assert exc_info.value.status_code == 503
    assert "本地没有可用缓存" in str(exc_info.value.detail)
    assert "超时" in str(exc_info.value.detail)
