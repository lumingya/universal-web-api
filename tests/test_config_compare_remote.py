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
        if url.endswith("config/sites/index.json"):
            return _FakeResponse(404)  # 旧布局分支：没有 index.json，回退单文件
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
        lambda url, **kwargs: _FakeResponse(404) if url.endswith("index.json") else next(responses),
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


# ---------------------------------------------------------------------------
# R1-2：官方配置改为 config/sites/index.json + 每站点一个文件
# ---------------------------------------------------------------------------

def _envelope_bytes(site, config):
    return json.dumps({"schema_version": 1, "site": site, "config": config}, ensure_ascii=False).encode("utf-8")


class _BytesResponse(_FakeResponse):
    def __init__(self, status_code, content=b"", headers=None):
        super().__init__(status_code, None, headers)
        self._content = content


def _index_layout(files):
    import hashlib

    index = {"schema_version": 1, "sites": {}}
    for site, (name, raw) in files.items():
        index["sites"][site] = {"file": name, "file_sha256": hashlib.sha256(raw).hexdigest()}
    return json.dumps(index).encode("utf-8")


def test_official_config_index_layout_assembles_and_validates(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path, ["2026-09-26T01:00:00Z", "2026-09-26T01:05:00Z"])
    files = {
        "_global": ("_global.json", _envelope_bytes("_global", {"selector_definitions": []})),
        "example.com": ("example.com.json", _envelope_bytes("example.com", {"presets": {"主预设": {"selectors": {}}}})),
    }
    index_bytes = _index_layout(files)
    seen = []
    index_responses = iter([_BytesResponse(200, index_bytes, {"ETag": '"idx-1"'}), _BytesResponse(304)])

    def fake_get(url, **kwargs):
        seen.append((url, kwargs))
        if url.endswith("config/sites/index.json"):
            return next(index_responses)
        for name, raw in files.values():
            if url.endswith("config/sites/" + name):
                return _BytesResponse(200, raw)
        raise AssertionError(url)

    monkeypatch.setattr(config_compare_support, "get_public_remote_resource", fake_get)
    downloaded = config_compare_support._load_official_sites_config()
    validated = config_compare_support._load_official_sites_config()

    assert downloaded["source"]["status"] == "remote"
    assert downloaded["source"]["path"] == "config/sites/index.json"
    assert downloaded["sites"] == {"example.com": {"presets": {"主预设": {"selectors": {}}}}}
    assert validated["source"]["status"] == "validated_cache"
    assert seen[-1][1]["headers"]["If-None-Match"] == '"idx-1"'
    assert not any("config/sites.json" in url for url, _ in seen)


def test_official_config_index_sha_mismatch_is_rejected(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path, ["2026-09-26T02:00:00Z"])
    good = _envelope_bytes("example.com", {"presets": {}})
    index_bytes = _index_layout({"example.com": ("example.com.json", good)})
    tampered = _envelope_bytes("example.com", {"presets": {"evil": {}}})

    def fake_get(url, **kwargs):
        if url.endswith("index.json"):
            return _BytesResponse(200, index_bytes)
        return _BytesResponse(200, tampered)

    monkeypatch.setattr(config_compare_support, "get_public_remote_resource", fake_get)
    with pytest.raises(HTTPException) as exc:
        config_compare_support._load_official_sites_config()
    assert exc.value.status_code == 503
    assert "sha256" in str(exc.value.detail)

