import asyncio
import json

import pytest
from fastapi import HTTPException

import app.api.system as system


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _extractors_payload():
    return {
        "extractors": {
            "deep_mode_v1": {
                "id": "deep_mode_v1",
                "name": "Deep",
                "description": "Deep extractor",
                "class": "DeepBrowserExtractor",
                "module": "app.core.extractors.deep_mode",
                "enabled": True,
                "config": {},
            },
        },
        "default": "deep_mode_v1",
        "version": "1.1",
    }


def test_settings_backup_exports_extractors_config(tmp_path, monkeypatch):
    extractors_file = tmp_path / "extractors.json"
    payload = _extractors_payload()
    extractors_file.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(system.extractor_manager, "CONFIG_FILE", str(extractors_file))

    bundle = system._build_settings_backup_bundle()

    assert bundle["files"]["extractors"] == payload


def test_settings_backup_validates_extractors_shape():
    with pytest.raises(HTTPException) as exc_info:
        system._validate_settings_backup_files({"extractors": {"extractors": []}})

    assert exc_info.value.status_code == 400


def test_settings_backup_import_writes_and_reloads_extractors(tmp_path, monkeypatch):
    extractors_file = tmp_path / "extractors.json"
    reload_calls = []
    payload = _extractors_payload()
    monkeypatch.setattr(system.extractor_manager, "CONFIG_FILE", str(extractors_file))
    monkeypatch.setattr(
        system.extractor_manager,
        "reload_config",
        lambda: reload_calls.append("reloaded"),
    )

    result = asyncio.run(
        system.import_settings_backup(
            FakeRequest({"files": {"extractors": payload}}),
            authenticated=True,
        )
    )

    assert result["success"] is True
    assert result["imported_sections"] == ["extractors"]
    assert reload_calls == ["reloaded"]
    assert json.loads(extractors_file.read_text(encoding="utf-8")) == payload
