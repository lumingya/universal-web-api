"""R1-1：站点适配器 Schema v1 的回归测试。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services.config.site_schema import (
    SITE_FILE_SCHEMA_VERSION,
    site_config_errors,
    site_file_errors,
    sites_errors,
)

ROOT = Path(__file__).resolve().parents[1]


def _shipped_sites() -> dict:
    """R1-2 之后内置配置位于 config/sites/；在那之前（或旧检出）回退到 config/sites.json。"""
    site_dir = ROOT / "config" / "sites"
    if site_dir.is_dir() and any(site_dir.glob("*.json")):
        sites = {}
        for path in sorted(site_dir.glob("*.json")):
            if path.name == "index.json":
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            sites[payload["site"]] = payload["config"]
        return sites
    return json.loads((ROOT / "config" / "sites.json").read_text(encoding="utf-8-sig"))


def test_shipped_site_configs_pass_schema():
    sites = _shipped_sites()
    assert "_global" in sites and len(sites) > 5
    assert sites_errors(sites) == {}


def _a_site():
    sites = _shipped_sites()
    name = next(key for key in sites if key != "_global")
    return name, copy.deepcopy(sites[name])


def test_wrong_types_are_reported_with_paths():
    name, config = _a_site()
    preset = next(iter(config["presets"]))
    config["presets"][preset]["workflow"][0]["action"] = 3
    config["presets"][preset]["stealth"] = "yes"
    errors = site_config_errors(name, config)
    assert any(f"sites[{name}].presets.{preset}.workflow[0].action" in e for e in errors), errors
    assert any(f"sites[{name}].presets.{preset}.stealth" in e for e in errors), errors


def test_default_preset_must_exist():
    name, config = _a_site()
    config["default_preset"] = "不存在的预设"
    errors = site_config_errors(name, config)
    assert errors and "default_preset" in errors[0]


def test_unknown_fields_are_allowed():
    name, config = _a_site()
    config["future_field"] = {"anything": True}
    preset = next(iter(config["presets"]))
    config["presets"][preset]["new_option"] = [1, 2, 3]
    assert site_config_errors(name, config) == []


def test_site_without_presets_is_rejected():
    assert site_config_errors("example.com", {"default_preset": "x"})


@pytest.mark.parametrize(
    ("patch", "fragment"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"min_app_version": "v3"}, "min_app_version"),
        ({"last_verified": "2026/09/26"}, "last_verified"),
    ],
)
def test_site_file_envelope(patch, fragment):
    name, config = _a_site()
    payload = {"schema_version": SITE_FILE_SCHEMA_VERSION, "site": name, "adapter_version": "1",
               "min_app_version": "3.0.0", "last_verified": "2026-09-26", "config": config}
    assert site_file_errors(payload) == []
    payload.update(patch)
    errors = site_file_errors(payload)
    assert errors and fragment in errors[0]


def test_global_selector_definitions_shape():
    sites = _shipped_sites()
    broken = copy.deepcopy(sites["_global"])
    broken["selector_definitions"][0].pop("key")
    assert site_config_errors("_global", broken)
