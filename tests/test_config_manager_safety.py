import asyncio
import copy
import inspect
import threading

import pytest
from fastapi import HTTPException

from app.api import config_routes
from app.services.config.engine import (
    ConfigEngine,
    get_default_file_paste_config,
    get_default_image_extraction_config,
    get_default_prompt_padding_config,
)
from app.services.config.managers import ImagePresetsManager


def test_image_preset_fuzzy_match_requires_hostname_boundary(tmp_path):
    manager = ImagePresetsManager(str(tmp_path / "missing.json"))
    manager.presets = {
        "com": {"image_extraction": {"enabled": False}},
        "example.com": {"image_extraction": {"enabled": True}},
    }

    assert manager.get_preset("chat.example.com") == {"enabled": True}
    assert manager.get_preset("EXAMPLE.COM.") == {"enabled": True}
    manager.presets = {
        "example.com": {"image_extraction": {"enabled": True}},
    }
    assert manager.get_preset("notexample.com") is None
    assert manager.get_preset("example.com.evil.test") is None


def test_list_sites_returns_detached_snapshot():
    engine = ConfigEngine.__new__(ConfigEngine)
    engine._io_lock = threading.RLock()
    engine.sites = {"example.com": {"presets": {"default": {"enabled": True}}}}
    engine.refresh_if_changed = lambda: None
    original = copy.deepcopy(engine.sites)

    listed = engine.list_sites()
    listed["example.com"]["presets"]["default"]["enabled"] = False

    assert engine.sites == original


def test_bulk_config_save_rejects_non_object_site():
    request = config_routes.ConfigUpdateRequest(
        config={"example.com": ["not", "an", "object"]}
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(config_routes.save_config(request, authenticated=True))

    assert exc_info.value.status_code == 400
    assert "example.com" in str(exc_info.value.detail)


def test_workflow_editor_mutations_require_dashboard_auth():
    for endpoint in (
        config_routes.inject_workflow_editor,
        config_routes.clear_editor_cache,
    ):
        parameter = inspect.signature(endpoint).parameters["authenticated"]
        assert parameter.default.dependency is config_routes.verify_auth


def test_config_migration_keeps_first_target_deadline_settings():
    engine = ConfigEngine.__new__(ConfigEngine)
    network = {
        "listen_pattern": "/nextjs-api/stream/",
        "parser": "lmarena",
        "first_response_timeout": 12,
    }
    engine.sites = {
        "arena.ai": {
            "presets": {
                "test": {
                    "stream_config": {"network": network},
                    "image_extraction": get_default_image_extraction_config(),
                    "file_paste": get_default_file_paste_config(),
                    "prompt_padding": get_default_prompt_padding_config(),
                }
            }
        }
    }
    engine._save_config = lambda: True

    engine.migrate_site_configs()

    assert network["first_response_timeout"] == 12
