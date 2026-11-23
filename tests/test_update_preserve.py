import shutil
from zipfile import ZipFile

import pytest

import update_preserve
import updater


def test_load_uses_defaults_until_dashboard_or_legacy_selection_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("UPDATE_PRESERVE", raising=False)

    settings = update_preserve.load_update_preserve_settings(tmp_path / "missing.json")

    assert settings["source"] == "default"
    assert settings["selected_patterns"] == update_preserve.get_default_update_preserve_patterns()


@pytest.mark.parametrize(
    ("update_mode", "legacy", "saved", "expected_browser_config"),
    [
        ("automatic", "logs", ["config"], "local"),
        ("version", "config,logs", [], "release"),
    ],
)
def test_updater_honors_saved_panel_selection(
    tmp_path, monkeypatch, update_mode, legacy, saved, expected_browser_config
):
    install = tmp_path / "installation"
    config = install / "config"
    config.mkdir(parents=True)
    browser_config = config / "browser_config.json"
    browser_config.write_text("local", encoding="utf-8")
    settings = config / "update_settings.json"

    monkeypatch.setattr(updater, "__file__", str(install / "updater.py"))
    monkeypatch.setattr(update_preserve, "UPDATE_SETTINGS_FILE", settings)
    monkeypatch.setenv("UPDATE_PRESERVE", legacy)
    panel = update_preserve.load_update_preserve_settings()
    assert panel["source"] == "legacy_env"
    assert panel["selected_patterns"] == legacy.split(",")
    update_preserve.save_update_preserve_settings(saved)

    archive = tmp_path / "release.zip"
    with ZipFile(archive, "w") as release_zip:
        release_zip.writestr("release/config/browser_config.json", "release")
        release_zip.writestr("release/config/update_settings.json", '{"selected_patterns": ["logs"]}')

    release = {"tag_name": "v1.0.0"}
    monkeypatch.setattr(updater, "fetch_latest_release", lambda repo: release)
    monkeypatch.setattr(updater, "fetch_release_by_tag", lambda repo, tag: release)
    monkeypatch.setattr(
        updater, "get_release_zip_asset",
        lambda release, repo: {"browser_download_url": "unused", "digest": "sha256:unused"},
    )
    monkeypatch.setattr(
        updater, "download_file_robust",
        lambda url, destination, **kwargs: shutil.copyfile(archive, destination),
    )

    if update_mode == "automatic":
        assert updater.check_and_update()
    else:
        assert updater.update_to_version("v1.0.0")

    assert browser_config.read_text(encoding="utf-8") == expected_browser_config
    panel = update_preserve.load_update_preserve_settings()
    assert panel["source"] == "saved"
    assert panel["selected_patterns"] == saved
