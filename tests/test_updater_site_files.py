"""R1-2 / R1-3：更新器逐站点合并 config/sites/*.json。

用户没改过的站点文件（与旧 index.json 的 config_sha256 一致）整体替换为新版；改过的保留本地值并补新字段。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import updater
from update_preserve import normalize_update_preserve_patterns


def _site(selector, **extra):
    preset = {"selectors": {"input_box": selector}, "workflow": [{"action": "CLICK", "target": "send_btn"}], **extra}
    return {"presets": {"主预设": preset}, "default_preset": "主预设"}


def _envelope(site, config, version="2026.09.26"):
    return {"schema_version": 1, "site": site, "adapter_version": version, "min_app_version": "3.0.0", "config": config}


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _index(*entries):
    return {"schema_version": 1, "sites": {site: {"config_sha256": updater._site_config_sha256(cfg)} for site, cfg in entries}}


def test_unmodified_site_is_replaced(tmp_path):
    old = _site("textarea.v1")
    _write(tmp_path / "dst" / "a.json", _envelope("a.example", old, "2026.09.01"))
    _write(tmp_path / "src" / "a.json", _envelope("a.example", _site("textarea.v2")))
    result = updater.merge_site_file(tmp_path / "src" / "a.json", tmp_path / "dst" / "a.json", _index(("a.example", old)))
    assert result == "replaced"
    saved = json.loads((tmp_path / "dst" / "a.json").read_text(encoding="utf-8"))
    assert saved["config"]["presets"]["主预设"]["selectors"]["input_box"] == "textarea.v2"
    assert saved["adapter_version"] == "2026.09.26"


def test_modified_site_keeps_local_values_and_gains_new_fields(tmp_path):
    old = _site("textarea.v1")
    _write(tmp_path / "dst" / "a.json", _envelope("a.example", _site("textarea.mine")))
    _write(tmp_path / "src" / "a.json", _envelope("a.example", _site("textarea.v2", stealth=True)))
    result = updater.merge_site_file(tmp_path / "src" / "a.json", tmp_path / "dst" / "a.json", _index(("a.example", old)))
    assert result == "merged"
    preset = json.loads((tmp_path / "dst" / "a.json").read_text(encoding="utf-8"))["config"]["presets"]["主预设"]
    assert preset["selectors"]["input_box"] == "textarea.mine"
    assert preset["stealth"] is True


def test_missing_old_index_falls_back_to_merge(tmp_path):
    _write(tmp_path / "dst" / "a.json", _envelope("a.example", _site("textarea.v1")))
    _write(tmp_path / "src" / "a.json", _envelope("a.example", _site("textarea.v2")))
    assert updater.merge_site_file(tmp_path / "src" / "a.json", tmp_path / "dst" / "a.json", {}) == "merged"


def test_unreadable_local_file_is_backed_up_then_replaced(tmp_path):
    (tmp_path / "dst").mkdir()
    (tmp_path / "dst" / "a.json").write_text("{ half edited", encoding="utf-8")
    _write(tmp_path / "src" / "a.json", _envelope("a.example", _site("textarea.v2")))
    assert updater.merge_site_file(tmp_path / "src" / "a.json", tmp_path / "dst" / "a.json", {}) == "replaced"
    backups = list((tmp_path / "dst").glob("a.json.unreadable-*.bak"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "{ half edited"


def test_extract_and_update_reads_old_index_before_overwriting_it(tmp_path):
    project = tmp_path / "project"
    shipped_v1 = _site("textarea.v1")
    _write(project / "config" / "sites" / "a.example.json", _envelope("a.example", shipped_v1, "2026.09.01"))
    _write(project / "config" / "sites" / "b.example.json", _envelope("b.example", _site("textarea.user")))
    _write(project / "config" / "sites" / "index.json",
           _index(("a.example", shipped_v1), ("b.example", _site("textarea.b.v1"))))

    release = tmp_path / "release.zip"
    new_index = _index(("a.example", _site("textarea.v2")), ("b.example", _site("textarea.b.v2")))
    with zipfile.ZipFile(release, "w") as zf:
        root = "universal-web-api-3.1.0/config/sites/"
        zf.writestr(root + "index.json", json.dumps(new_index))  # 故意先写 index，验证旧清单已提前读取
        zf.writestr(root + "a.example.json", json.dumps(_envelope("a.example", _site("textarea.v2"))))
        zf.writestr(root + "b.example.json", json.dumps(_envelope("b.example", _site("textarea.b.v2", stealth=True))))
        zf.writestr(root + "c.example.json", json.dumps(_envelope("c.example", _site("#c"))))

    assert updater.extract_and_update(release, project, preserve=[]) is True

    def selector(name):
        payload = json.loads((project / "config" / "sites" / name).read_text(encoding="utf-8"))
        return payload["config"]["presets"]["主预设"]
    assert selector("a.example.json")["selectors"]["input_box"] == "textarea.v2"  # 未改动 -> 新版
    assert selector("b.example.json")["selectors"]["input_box"] == "textarea.user"  # 改过 -> 保留本地
    assert selector("b.example.json")["stealth"] is True  # ……并补入新字段
    assert selector("c.example.json")["selectors"]["input_box"] == "#c"  # 新站点
    assert json.loads((project / "config" / "sites" / "index.json").read_text(encoding="utf-8")) == new_index


def test_legacy_sites_json_preserve_setting_maps_to_sites_dir():
    assert "config/sites" in normalize_update_preserve_patterns(["config/sites.json"])
    assert updater.should_preserve(Path("config/sites/a.example.json"), ["config/sites"])
    assert not updater.should_preserve(Path("config/sites.local.json"), ["config/sites"])
