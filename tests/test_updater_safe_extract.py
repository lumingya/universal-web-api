import stat
import zipfile
from pathlib import Path

import pytest

import updater
from updater import (
    _safe_extract_zip,
    backup_current,
    get_release_zip_asset,
    restore_from_backup,
    should_preserve,
)


def _write_zip(path: Path, members) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members:
            if isinstance(name, zipfile.ZipInfo):
                archive.writestr(name, content)
            else:
                archive.writestr(name, content)


def test_safe_extract_zip_writes_regular_members(tmp_path):
    archive_path = tmp_path / "release.zip"
    destination = tmp_path / "extract"
    _write_zip(archive_path, [("project/app/main.py", b"print('ok')")])

    with zipfile.ZipFile(archive_path) as archive:
        _safe_extract_zip(archive, destination)

    assert (destination / "project" / "app" / "main.py").read_bytes() == b"print('ok')"


@pytest.mark.parametrize(
    "member_name",
    ["../outside.txt", "folder/../../outside.txt", "/absolute.txt", "C:/outside.txt"],
)
def test_safe_extract_zip_rejects_escaping_or_absolute_paths(tmp_path, member_name):
    archive_path = tmp_path / "release.zip"
    destination = tmp_path / "extract"
    _write_zip(archive_path, [(member_name, b"unsafe")])

    with zipfile.ZipFile(archive_path) as archive, pytest.raises(ValueError):
        _safe_extract_zip(archive, destination)

    assert not (tmp_path / "outside.txt").exists()


def test_safe_extract_zip_rejects_symbolic_links(tmp_path):
    archive_path = tmp_path / "release.zip"
    destination = tmp_path / "extract"
    link = zipfile.ZipInfo("project/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    _write_zip(archive_path, [(link, "../../outside.txt")])

    with zipfile.ZipFile(archive_path) as archive, pytest.raises(ValueError):
        _safe_extract_zip(archive, destination)

    assert not destination.exists()


def test_restore_from_backup_removes_files_from_partial_update(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("old", encoding="utf-8")
    (project / "app").mkdir()
    (project / "app" / "existing.py").write_text("old", encoding="utf-8")
    (project / "logs").mkdir()
    (project / "logs" / "runtime.log").write_text("keep", encoding="utf-8")

    backup = backup_current(project)
    assert backup is not None

    (project / "main.py").write_text("new", encoding="utf-8")
    (project / "app" / "added.py").write_text("partial", encoding="utf-8")
    (project / "new_top_level.py").write_text("partial", encoding="utf-8")
    (project / "new_package").mkdir()
    (project / "new_package" / "module.py").write_text("partial", encoding="utf-8")

    assert restore_from_backup(project, backup)

    assert (project / "main.py").read_text(encoding="utf-8") == "old"
    assert (project / "app" / "existing.py").read_text(encoding="utf-8") == "old"
    assert not (project / "app" / "added.py").exists()
    assert not (project / "new_top_level.py").exists()
    assert not (project / "new_package").exists()
    assert (project / "logs" / "runtime.log").read_text(encoding="utf-8") == "keep"


def test_preserve_patterns_match_path_boundaries_not_substrings():
    patterns = ["image", "updater.py", "*.pyc"]

    assert should_preserve(Path("image/input.png"), patterns)
    assert should_preserve(Path("updater.py"), patterns)
    assert should_preserve(Path("app/cache/module.pyc"), patterns)
    assert not should_preserve(Path("app/utils/image_handler.py"), patterns)
    assert not should_preserve(Path("static/images/logo.svg"), patterns)


def test_release_asset_requires_exact_name_repo_and_sha256():
    digest = "a" * 64
    release = {
        "tag_name": "v3.0.0",
        "assets": [{
            "name": "universal-web-api-release-v3.0.0.zip",
            "size": 1024,
            "digest": f"sha256:{digest}",
            "browser_download_url": (
                "https://github.com/lumingya/universal-web-api/"
                "releases/download/v3.0.0/universal-web-api-release-v3.0.0.zip"
            ),
        }],
    }

    assert get_release_zip_asset(release, "lumingya/universal-web-api") == release["assets"][0]
    release["assets"][0]["digest"] = ""
    assert get_release_zip_asset(release, "lumingya/universal-web-api") is None


def test_release_asset_name_supports_tag_without_v_and_asset_with_v():
    digest = "b" * 64
    # Tag 是 2.9.7（无 v），Asset 是 universal-web-api-release-v2.9.7.zip（有 v）
    release = {
        "tag_name": "2.9.7",
        "assets": [{
            "name": "universal-web-api-release-v2.9.7.zip",
            "size": 2048,
            "digest": f"sha256:{digest}",
            "browser_download_url": (
                "https://github.com/lumingya/universal-web-api/"
                "releases/download/2.9.7/universal-web-api-release-v2.9.7.zip"
            ),
        }],
    }
    assert get_release_zip_asset(release, "lumingya/universal-web-api") == release["assets"][0]

    # Tag 是 v2.9.7（有 v），Asset 是 universal-web-api-release-2.9.7.zip（无 v）
    release_v_tag = {
        "tag_name": "v2.9.7",
        "assets": [{
            "name": "universal-web-api-release-2.9.7.zip",
            "size": 2048,
            "digest": f"sha256:{digest}",
            "browser_download_url": (
                "https://github.com/lumingya/universal-web-api/"
                "releases/download/v2.9.7/universal-web-api-release-2.9.7.zip"
            ),
        }],
    }
    assert get_release_zip_asset(release_v_tag, "lumingya/universal-web-api") == release_v_tag["assets"][0]

    # 不匹配的文件名
    release_mismatch = {
        "tag_name": "2.9.7",
        "assets": [{
            "name": "other-release-v2.9.7.zip",
            "size": 2048,
            "digest": f"sha256:{digest}",
            "browser_download_url": (
                "https://github.com/lumingya/universal-web-api/"
                "releases/download/2.9.7/other-release-v2.9.7.zip"
            ),
        }],
    }
    assert get_release_zip_asset(release_mismatch, "lumingya/universal-web-api") is None

    # 路径穿越攻击防御测试
    release_traversal = {
        "tag_name": "2.9.7",
        "assets": [{
            "name": "universal-web-api-release-v2.9.7.zip",
            "size": 2048,
            "digest": f"sha256:{digest}",
            "browser_download_url": (
                "https://github.com/lumingya/universal-web-api/"
                "releases/download/../../attacker/repo/releases/download/2.9.7/universal-web-api-release-v2.9.7.zip"
            ),
        }],
    }
    assert get_release_zip_asset(release_traversal, "lumingya/universal-web-api") is None

    # 无效 tag 或入参测试
    assert get_release_zip_asset({"tag_name": "", "assets": []}, "lumingya/universal-web-api") is None
    assert get_release_zip_asset({"tag_name": "v", "assets": []}, "lumingya/universal-web-api") is None
    assert get_release_zip_asset(None, "lumingya/universal-web-api") is None
    assert get_release_zip_asset({}, "") is None


def test_safe_extract_zip_rejects_excessive_compression_ratio(tmp_path):
    archive_path = tmp_path / "bomb.zip"
    destination = tmp_path / "extract"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project/large.bin", b"0" * (2 * 1024 * 1024))

    with zipfile.ZipFile(archive_path) as archive, pytest.raises(ValueError, match="压缩比"):
        _safe_extract_zip(archive, destination)


def test_safe_extract_zip_rejects_member_count_budget(tmp_path, monkeypatch):
    archive_path = tmp_path / "many.zip"
    destination = tmp_path / "extract"
    _write_zip(archive_path, [("one.txt", b"1"), ("two.txt", b"2")])
    monkeypatch.setattr(updater, "MAX_UPDATE_MEMBER_COUNT", 1)

    with zipfile.ZipFile(archive_path) as archive, pytest.raises(ValueError, match="文件数量"):
        _safe_extract_zip(archive, destination)
