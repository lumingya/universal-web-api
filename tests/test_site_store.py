"""R1-2：config/sites/ 目录存储、旧版 sites.json 自动迁移与 ConfigEngine 集成。"""

from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path

import pytest

from app.services.config.site_store import (
    SiteStore,
    canonical_config_text,
    load_sites_dict,
    site_filename,
)
from tests._sites import ROOT, shipped_sites


def _site(selector: str = "textarea", extra=None) -> dict:
    preset = {"selectors": {"input_box": selector, "send_btn": None}, "workflow": [{"action": "CLICK", "target": "send_btn"}]}
    if extra:
        preset.update(extra)
    return {"presets": {"主预设": preset}, "default_preset": "主预设"}


def _write_legacy(path: Path, sites: dict) -> None:
    path.write_text(json.dumps(sites, ensure_ascii=False, indent=2), encoding="utf-8")


def _envelope(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- 迁移


def test_fresh_migration_splits_legacy_and_keeps_backup(tmp_path):
    legacy = tmp_path / "sites.json"
    sites = {"_global": {"selector_definitions": []}, "a.example": _site(), "b.example": _site("#b")}
    _write_legacy(legacy, sites)
    store = SiteStore(tmp_path / "sites", legacy)

    loaded = store.load()

    assert loaded == sites
    assert not legacy.exists()
    backups = list(tmp_path.glob("sites.json.migrated-*.bak"))
    assert len(backups) == 1 and json.loads(backups[0].read_text(encoding="utf-8")) == sites
    assert {p.name for p in store.site_files()} == {"_global.json", "a.example.json", "b.example.json"}
    assert _envelope(tmp_path / "sites" / "a.example.json")["schema_version"] == 1


def test_upgrade_migration_merges_like_the_updater(tmp_path):
    """旧更新器把新发布的 config/sites/*.json 拷进来，但保留了用户改过的旧 sites.json。"""
    sites_dir = tmp_path / "sites"
    shipped = SiteStore(sites_dir, tmp_path / "none.json")
    new_default = _site("textarea.v2", extra={"stealth": True})
    shipped.save({"a.example": new_default, "new.example": _site("#new")})

    legacy = tmp_path / "sites.json"
    user_config = _site("textarea.user")  # 用户改过的选择器
    _write_legacy(legacy, {"a.example": user_config, "custom.example": _site("#mine")})

    merged = SiteStore(sites_dir, legacy).load()

    from updater import merge_site_records

    assert merged["a.example"] == merge_site_records(user_config, new_default)
    assert merged["a.example"]["presets"]["主预设"]["selectors"]["input_box"] == "textarea.user"  # 本地优先
    assert merged["a.example"]["presets"]["主预设"]["stealth"] is True  # 补入发布版新增字段
    assert merged["new.example"] == _site("#new")  # 发布版独有站点保留
    assert merged["custom.example"] == _site("#mine")  # 本地独有站点保留
    assert not legacy.exists()


def test_unparseable_legacy_is_left_untouched(tmp_path):
    legacy = tmp_path / "sites.json"
    legacy.write_text("{ broken", encoding="utf-8")
    store = SiteStore(tmp_path / "sites", legacy)
    assert store.load() is None
    assert legacy.read_text(encoding="utf-8") == "{ broken"


# --------------------------------------------------------------------------- 保存


def test_save_rewrites_only_changed_sites_and_trashes_removed(tmp_path):
    store = SiteStore(tmp_path / "sites", tmp_path / "none.json")
    sites = {"_global": {"selector_definitions": []}, "a.example": _site(), "b.example": _site("#b")}
    assert sorted(store.save(sites)) == ["_global", "a.example", "b.example"]
    b_path = tmp_path / "sites" / "b.example.json"
    b_stat = b_path.stat()
    time.sleep(0.01)

    changed = copy.deepcopy(sites)
    changed["a.example"]["presets"]["主预设"]["selectors"]["input_box"] = "#changed"
    del changed["_global"]
    assert store.save(changed) == ["a.example"]

    assert b_path.stat().st_mtime_ns == b_stat.st_mtime_ns  # 未变化的站点不重写
    assert not (tmp_path / "sites" / "_global.json").exists()
    trashed = list((tmp_path / "sites" / ".trash").glob("_global.*.json"))
    assert len(trashed) == 1
    assert load_sites_dict(tmp_path / "sites") == changed
    assert not list((tmp_path / "sites").glob("*.tmp"))


def test_save_keeps_envelope_metadata(tmp_path):
    sites_dir = tmp_path / "sites"
    sites_dir.mkdir()
    path = sites_dir / "a.example.json"
    path.write_text(json.dumps({"schema_version": 1, "site": "a.example", "adapter_version": "2026.09.01",
                                "min_app_version": "3.0.0", "origin": {"x": 1}, "config": _site()}), encoding="utf-8")
    store = SiteStore(sites_dir, tmp_path / "none.json")
    loaded = store.load()
    loaded["a.example"]["presets"]["主预设"]["selectors"]["input_box"] = "#new"
    store.save(loaded)
    saved = _envelope(path)
    assert saved["adapter_version"] == "2026.09.01" and saved["origin"] == {"x": 1}
    assert saved["config"]["presets"]["主预设"]["selectors"]["input_box"] == "#new"


def test_broken_file_is_skipped_never_overwritten_or_trashed(tmp_path):
    store = SiteStore(tmp_path / "sites", tmp_path / "none.json")
    sites = {"a.example": _site(), "b.example": _site("#b")}
    store.save(sites)
    previous = store.load()
    broken = tmp_path / "sites" / "b.example.json"
    broken.write_text('{"schema_version": 1, "site": "b.example", "config": {', encoding="utf-8")  # 用户手改到一半

    reloaded = store.load(previous=previous)
    assert reloaded["b.example"] == previous["b.example"]  # 热重载沿用上一次的配置
    assert str(broken) in store.broken_files()

    store.save({"a.example": _site("#a2")})  # b.example 不在字典里，也不能被移入回收站
    assert broken.read_text(encoding="utf-8").endswith("{")
    assert not (tmp_path / "sites" / ".trash").exists()


def test_signature_changes_when_a_file_changes(tmp_path):
    store = SiteStore(tmp_path / "sites", tmp_path / "none.json")
    store.save({"a.example": _site()})
    before = store.signature()
    path = tmp_path / "sites" / "a.example.json"
    time.sleep(0.01)
    path.write_text(path.read_text(encoding="utf-8").replace("textarea", "textarea.x"), encoding="utf-8")
    assert store.signature() != before


@pytest.mark.parametrize(
    ("site", "expected"),
    [
        ("chat.deepseek.com", "chat.deepseek.com.json"),
        ("_global", "_global.json"),
        ("localhost:8080", "localhost%3A8080.json"),
        ("例子.com", "%E4%BE%8B%E5%AD%90.com.json"),
        ("con", "%5Fcon.json"),
        ("index", "%5Findex.json"),
    ],
)
def test_site_filename_is_safe_on_windows(site, expected):
    assert site_filename(site) == expected


def test_same_filename_collision_gets_unique_name(tmp_path):
    store = SiteStore(tmp_path / "sites", tmp_path / "none.json")
    store.save({"a.example": _site()})
    (tmp_path / "sites" / "b.example.json").write_text("{}", encoding="utf-8")  # 占位的无效文件
    store.load()
    store.save({"a.example": _site(), "b.example": _site("#b")})
    assert (tmp_path / "sites" / "b.example~2.json").exists()


# --------------------------------------------------------------------------- 仓库内置配置与 index


def test_index_builder_script_is_tracked_by_git():
    """scripts/ 默认被 .gitignore 忽略（白名单制）；曾因此漏提交 build_sites_index.py，导致干净克隆里本测试失败。"""
    import shutil
    import subprocess

    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    result = subprocess.run(["git", "check-ignore", "-q", "scripts/build_sites_index.py"], cwd=ROOT)
    assert result.returncode == 1, "scripts/build_sites_index.py 被 .gitignore 忽略了，请加入 scripts 白名单"


def test_shipped_sites_dir_is_the_only_source_and_index_is_current():
    assert not (ROOT / "config" / "sites.json").exists(), "拆分后仓库里不应再有旧的单文件 sites.json"
    sites = shipped_sites()
    assert "_global" in sites and len(sites) >= 10

    import importlib.util

    spec = importlib.util.spec_from_file_location("build_sites_index", ROOT / "scripts" / "build_sites_index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0, "config/sites/index.json 已过期：python scripts/build_sites_index.py"
    sample = {"b": [1, {"z": 1, "a": "中"}], "a": None}
    assert module._canonical(sample) == canonical_config_text(sample)


def test_every_shipped_file_declares_versions():
    for path in sorted((ROOT / "config" / "sites").glob("*.json")):
        if path.name == "index.json":
            continue
        payload = _envelope(path)
        assert payload["adapter_version"], path.name
        assert payload["min_app_version"], path.name
        assert path.name == site_filename(payload["site"]), path.name


# --------------------------------------------------------------------------- ConfigEngine 集成


def test_config_engine_migrates_saves_and_hot_reloads(tmp_path, monkeypatch):
    from app.services.config import engine as engine_module

    base = shipped_sites()
    legacy = tmp_path / "sites.json"
    _write_legacy(legacy, {"_global": base["_global"], "chatgpt.com": base["chatgpt.com"]})
    monkeypatch.setattr(engine_module.ConfigConstants, "CONFIG_FILE", str(legacy))
    monkeypatch.setattr(engine_module.ConfigConstants, "SITES_DIR", str(tmp_path / "sites"))
    monkeypatch.setattr(engine_module.ConfigConstants, "SITES_LOCAL_FILE", str(tmp_path / "sites.local.json"))
    monkeypatch.setattr(engine_module.ConfigConstants, "COMMANDS_FILE", str(tmp_path / "commands.json"))
    monkeypatch.setattr(engine_module.ConfigConstants, "COMMANDS_LOCAL_FILE", str(tmp_path / "commands.local.json"))

    engine = engine_module.ConfigEngine()

    assert "chatgpt.com" in engine.sites
    assert not legacy.exists() and (tmp_path / "sites" / "chatgpt.com.json").exists()

    global_file = tmp_path / "sites" / "_global.json"
    global_mtime = global_file.stat().st_mtime_ns
    preset = next(iter(engine.sites["chatgpt.com"]["presets"]))
    engine.sites["chatgpt.com"]["presets"][preset]["selectors"]["input_box"] = "textarea#saved-by-test"
    assert engine.save_config() is True
    on_disk = _envelope(tmp_path / "sites" / "chatgpt.com.json")
    assert on_disk["config"]["presets"][preset]["selectors"]["input_box"] == "textarea#saved-by-test"
    assert global_file.stat().st_mtime_ns == global_mtime

    # 外部编辑（例如用户直接改文件）后，refresh_if_changed 触发热重载
    time.sleep(0.01)
    on_disk["config"]["presets"][preset]["selectors"]["input_box"] = "textarea#edited-outside"
    (tmp_path / "sites" / "chatgpt.com.json").write_text(json.dumps(on_disk, ensure_ascii=False), encoding="utf-8")
    os.utime(tmp_path / "sites" / "chatgpt.com.json", None)
    engine.refresh_if_changed()
    assert engine.sites["chatgpt.com"]["presets"][preset]["selectors"]["input_box"] == "textarea#edited-outside"
