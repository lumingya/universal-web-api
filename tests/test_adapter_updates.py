"""R1-3：站点适配器在线更新（检查状态、安全更新、冲突合并、校验失败中止、API 接线）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.services.adapter_updates import AdapterUpdater, config_sha256


def _site(selector, **extra):
    preset = {"selectors": {"input_box": selector}, "workflow": [{"action": "CLICK", "target": "send_btn"}], **extra}
    return {"presets": {"主预设": preset}, "default_preset": "主预设"}


def _envelope_text(site, config, version):
    payload = {"schema_version": 1, "site": site, "adapter_version": version, "min_app_version": "3.0.0", "config": config}
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


class _Repo:
    """内存中的“官方仓库”：{相对路径: bytes}。"""

    def __init__(self):
        self.files = {}

    def publish(self, sites, *, min_app=None):
        index = {"schema_version": 1, "sites": {}}
        for site, (config, version) in sites.items():
            name = f"{site}.json"
            raw = _envelope_text(site, config, version).encode("utf-8")
            self.files[f"config/sites/{name}"] = raw
            index["sites"][site] = {"file": name, "adapter_version": version,
                                    "min_app_version": (min_app or {}).get(site, "3.0.0"),
                                    "config_sha256": config_sha256(config),
                                    "file_sha256": hashlib.sha256(raw).hexdigest()}
        self.files["config/sites/index.json"] = json.dumps(index).encode("utf-8")
        return index

    def fetch(self, relative_path):
        return self.files[relative_path]


def _install(sites_dir: Path, repo: _Repo, sites):
    """模拟“安装某个发布版”：写入站点文件与安装清单。"""
    index = repo.publish(sites)
    sites_dir.mkdir(parents=True, exist_ok=True)
    for site, (config, version) in sites.items():
        (sites_dir / f"{site}.json").write_text(_envelope_text(site, config, version), encoding="utf-8")
    (sites_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")


def _config(sites_dir, site):
    return json.loads((sites_dir / f"{site}.json").read_text(encoding="utf-8"))["config"]


@pytest.fixture
def env(tmp_path):
    repo = _Repo()
    sites_dir = tmp_path / "sites"
    _install(sites_dir, repo, {
        "a.example": (_site("v1"), "2026.09.01"),
        "b.example": (_site("v1"), "2026.09.01"),
        "c.example": (_site("v1"), "2026.09.01"),
    })
    # 用户改了 b.example；本地另有自建站点
    b = json.loads((sites_dir / "b.example.json").read_text(encoding="utf-8"))
    b["config"]["presets"]["主预设"]["selectors"]["input_box"] = "mine"
    (sites_dir / "b.example.json").write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    (sites_dir / "mine.example.json").write_text(_envelope_text("mine.example", _site("x"), ""), encoding="utf-8")
    # 官方发布新版：a、b 更新，c 不变，新增 d，e 需要更高版本
    repo.publish({
        "a.example": (_site("v2"), "2026.10.01"),
        "b.example": (_site("v2", stealth=True), "2026.10.01"),
        "c.example": (_site("v1"), "2026.09.01"),
        "d.example": (_site("d1"), "2026.10.01"),
        "e.example": (_site("e1"), "2026.10.01"),
    }, min_app={"e.example": "9.0.0"})
    reloads = []
    updater = AdapterUpdater(sites_dir, repo.fetch, app_version="3.0.0", reload=lambda: reloads.append(1))
    return updater, repo, sites_dir, reloads


def test_check_reports_each_status(env):
    updater, _, _, _ = env
    status = {row["site"]: row["status"] for row in updater.check()["sites"]}
    assert status == {
        "a.example": "update_available",
        "b.example": "conflict",
        "c.example": "up_to_date",
        "d.example": "new_site",
        "e.example": "requires_newer_app",
        "mine.example": "local_only",
    }


def test_apply_defaults_to_safe_updates_only(env):
    updater, _, sites_dir, reloads = env
    result = updater.apply()
    assert sorted(item["site"] for item in result["applied"]) == ["a.example", "d.example"]
    assert _config(sites_dir, "a.example")["presets"]["主预设"]["selectors"]["input_box"] == "v2"
    assert _config(sites_dir, "d.example")["presets"]["主预设"]["selectors"]["input_box"] == "d1"
    assert _config(sites_dir, "b.example")["presets"]["主预设"]["selectors"]["input_box"] == "mine"  # 冲突不动
    assert reloads == [1]
    after = {row["site"]: row["status"] for row in updater.check()["sites"]}
    assert after["a.example"] == "up_to_date" and after["d.example"] == "up_to_date"


def test_conflict_requires_opt_in_and_merges_local_first(env):
    updater, _, sites_dir, _ = env
    skipped = updater.apply(["b.example"])
    assert skipped["applied"] == [] and skipped["skipped"][0]["status"] == "conflict"

    result = updater.apply(["b.example"], include_conflicts=True)
    assert result["applied"][0]["site"] == "b.example"
    preset = _config(sites_dir, "b.example")["presets"]["主预设"]
    assert preset["selectors"]["input_box"] == "mine"  # 本地优先
    assert preset["stealth"] is True  # 补入官方新增字段
    index = json.loads((sites_dir / "index.json").read_text(encoding="utf-8"))
    assert index["sites"]["b.example"]["adapter_version"] == "2026.10.01"


def test_requires_newer_app_is_never_applied(env):
    updater, _, sites_dir, _ = env
    result = updater.apply(["e.example"], include_conflicts=True)
    assert result["applied"] == [] and result["skipped"][0]["status"] == "requires_newer_app"
    assert not (sites_dir / "e.example.json").exists()


def test_tampered_official_file_aborts_without_writing(env):
    updater, repo, sites_dir, reloads = env
    repo.files["config/sites/a.example.json"] = repo.files["config/sites/a.example.json"].replace(b"v2", b"evil")
    with pytest.raises(ValueError, match="sha256"):
        updater.apply()
    assert _config(sites_dir, "a.example")["presets"]["主预设"]["selectors"]["input_box"] == "v1"
    assert reloads == []


def test_api_routes_are_wired(monkeypatch, env):
    import asyncio

    import main
    from httpx import ASGITransport, AsyncClient
    from app.services import adapter_updates

    updater, _, _, _ = env
    monkeypatch.setattr(adapter_updates, "default_updater", lambda branch="main": updater)
    for name in ("AUTH_ENABLED", "AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    async def run():
        async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://127.0.0.1:8199") as client:
            checked = await client.get("/api/adapters/updates")
            applied = await client.post("/api/adapters/updates/apply", json={})
        return checked, applied

    checked, applied = asyncio.run(run())
    assert checked.status_code == 200 and checked.json()["summary"]["update_available"] == 1
    assert applied.status_code == 200
    assert sorted(item["site"] for item in applied.json()["applied"]) == ["a.example", "d.example"]
