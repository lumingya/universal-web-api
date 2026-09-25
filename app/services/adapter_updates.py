"""R1-3：站点适配器在线更新（不发整包也能热更新站点配置）。

依据三份信息逐站点比较：

- 官方 ``config/sites/index.json``：官方当前版本的规范化摘要（config_sha256）、文件摘要、版本号；
- 本地 ``config/sites/index.json``：本地**安装时**附带的清单，代表“用户拿到的原始版本”；
- 本地站点文件的当前内容：与安装清单不同，说明用户改过。

状态：
- ``up_to_date``：本地内容与官方一致；
- ``update_available``：官方有变化，且用户没改过本地文件，可以安全自动应用；
- ``conflict``：官方有变化，且用户也改过（或来源未知），需要显式确认，按“本地优先”合并；
- ``new_site``：官方新增、本地没有；
- ``local_only``：只在本地存在；
- ``requires_newer_app``：官方版本要求更高的 min_app_version。

应用更新时逐个下载并按 ``file_sha256`` 校验、Schema 校验，写入后更新本地安装清单中对应条目，最后热重载。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.core.config import get_logger
from app.services.config.site_schema import site_file_errors
from app.services.config.site_store import SiteStore, canonical_config_text

logger = get_logger("ADAPTER_UPD")

INDEX_NAME = "index.json"
SAFE_STATUSES = ("update_available", "new_site")
_FILE_PATTERN = re.compile(r"^[A-Za-z0-9._%~-]+\.json$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")

FetchBytes = Callable[[str], bytes]


def config_sha256(config: Any) -> str:
    return hashlib.sha256(canonical_config_text(config).encode("utf-8")).hexdigest()


def _version_tuple(value: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", str(value or ""))[:4]) or (0,)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class AdapterUpdater:
    def __init__(
        self,
        sites_dir: os.PathLike | str,
        fetch_bytes: FetchBytes,
        *,
        app_version: str,
        reload: Optional[Callable[[], None]] = None,
        lock: Any = None,
    ):
        """``fetch_bytes(relative_path)`` 返回官方仓库中该路径的原始字节（由调用方负责安全抓取与大小限制）。"""
        self.sites_dir = Path(sites_dir)
        self.fetch_bytes = fetch_bytes
        self.app_version = str(app_version or "0")
        self.reload = reload
        self.lock = lock

    # ------------------------------------------------------------------ 读取
    def _local_index(self) -> Dict[str, Any]:
        try:
            payload = json.loads((self.sites_dir / INDEX_NAME).read_text(encoding="utf-8-sig"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _local_envelopes(self) -> Dict[str, Dict[str, Any]]:
        store = SiteStore(self.sites_dir, self.sites_dir / "__no_legacy__.json")
        result = {}
        for path in store.site_files():
            try:
                payload = store.read_envelope(path)
            except Exception:
                continue
            result[payload["site"]] = {"path": path, "payload": payload}
        return result

    def _official_index(self) -> Dict[str, Any]:
        raw = self.fetch_bytes(f"config/sites/{INDEX_NAME}")
        index = json.loads(raw.decode("utf-8-sig"))
        entries = index.get("sites") if isinstance(index, dict) else None
        if not isinstance(entries, dict):
            raise ValueError("官方 index.json 缺少 sites 清单")
        for site, entry in entries.items():
            if not isinstance(entry, dict) or not _FILE_PATTERN.fullmatch(str(entry.get("file") or "")) \
                    or not _SHA_PATTERN.fullmatch(str(entry.get("file_sha256") or "")) \
                    or not _SHA_PATTERN.fullmatch(str(entry.get("config_sha256") or "")):
                raise ValueError(f"官方 index.json 中站点 {site} 的条目无效")
        return index

    # ------------------------------------------------------------------ 检查
    def check(self) -> Dict[str, Any]:
        official = self._official_index()["sites"]
        installed = (self._local_index().get("sites") or {})
        local = self._local_envelopes()
        rows: List[Dict[str, Any]] = []
        for site in sorted(set(official) | set(local)):
            official_entry = official.get(site)
            local_item = local.get(site)
            row = {
                "site": site,
                "local_version": (local_item or {}).get("payload", {}).get("adapter_version", ""),
                "official_version": (official_entry or {}).get("adapter_version", ""),
                "min_app_version": (official_entry or {}).get("min_app_version", ""),
            }
            if official_entry is None:
                row["status"] = "local_only"
            else:
                too_new = _version_tuple(official_entry.get("min_app_version")) > _version_tuple(self.app_version)
                if local_item is None:
                    row["status"] = "requires_newer_app" if too_new else "new_site"
                else:
                    local_hash = config_sha256(local_item["payload"]["config"])
                    installed_hash = (installed.get(site) or {}).get("config_sha256")
                    if local_hash == official_entry["config_sha256"]:
                        row["status"] = "up_to_date"
                    elif too_new:
                        row["status"] = "requires_newer_app"
                    elif installed_hash and local_hash == installed_hash:
                        row["status"] = "update_available"
                    else:
                        row["status"] = "conflict"
                    row["locally_modified"] = not (installed_hash and local_hash == installed_hash)
            rows.append(row)
        summary: Dict[str, int] = {}
        for row in rows:
            summary[row["status"]] = summary.get(row["status"], 0) + 1
        return {"app_version": self.app_version, "sites": rows, "summary": summary}

    # ------------------------------------------------------------------ 应用
    def apply(self, sites: Optional[Iterable[str]] = None, *, include_conflicts: bool = False) -> Dict[str, Any]:
        """应用更新。``sites`` 为空时应用全部安全更新（update_available / new_site）。"""
        from updater import merge_site_records  # 与更新器、迁移使用同一合并语义

        official_index = self._official_index()
        report = self.check()
        status_by_site = {row["site"]: row["status"] for row in report["sites"]}
        allowed = set(SAFE_STATUSES) | ({"conflict"} if include_conflicts else set())
        targets = list(sites) if sites else [s for s, st in status_by_site.items() if st in SAFE_STATUSES]

        applied, skipped = [], []
        pending_writes: List[tuple] = []
        local = self._local_envelopes()
        for site in targets:
            status = status_by_site.get(site)
            if status not in allowed:
                skipped.append({"site": site, "status": status or "unknown"})
                continue
            entry = official_index["sites"][site]
            raw = self.fetch_bytes(f"config/sites/{entry['file']}")
            if hashlib.sha256(raw).hexdigest() != entry["file_sha256"]:
                raise ValueError(f"官方站点文件 {entry['file']} 的 sha256 与 index.json 不一致，已中止")
            payload = json.loads(raw.decode("utf-8-sig"))
            errors = site_file_errors(payload)
            if errors or payload.get("site") != site:
                raise ValueError(f"官方站点文件 {entry['file']} 未通过校验: {errors[:3] or 'site 字段不符'}")
            if status == "conflict":
                merged = dict(payload)
                merged["config"] = merge_site_records(local[site]["payload"]["config"], payload["config"])
                text = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
            else:
                text = raw.decode("utf-8-sig")
            target = local[site]["path"] if site in local else self.sites_dir / entry["file"]
            pending_writes.append((site, target, text, entry, status))

        def write_all():
            if not pending_writes:
                return
            local_index = self._local_index() or {"schema_version": 1, "sites": {}}
            local_index.setdefault("sites", {})
            for site, target, text, entry, status in pending_writes:
                _atomic_write(target, text)
                local_index["sites"][site] = dict(entry)
                applied.append({"site": site, "status": status, "version": entry.get("adapter_version", "")})
            local_index["sites"] = dict(sorted(local_index["sites"].items()))
            _atomic_write(self.sites_dir / INDEX_NAME, json.dumps(local_index, indent=2, ensure_ascii=False) + "\n")

        if self.lock is not None:
            with self.lock:
                write_all()
        else:
            write_all()
        if applied and self.reload is not None:
            self.reload()
        logger.info(f"站点适配器更新：应用 {len(applied)} 个，跳过 {len(skipped)} 个")
        return {"applied": applied, "skipped": skipped}


def default_updater(branch: str = "main") -> AdapterUpdater:
    """使用全局 config_engine 与官方仓库（GITHUB_REPO）的更新器。"""
    from app import __version__ as app_version
    from app.api import config_compare_support as support
    from app.services.config_engine import config_engine
    from app.utils.remote_resource import get_public_remote_resource

    repo = support._resolve_official_repo()

    def fetch_bytes(relative_path: str) -> bytes:
        url = support._official_config_url(repo, branch, relative_path)
        response = get_public_remote_resource(
            url, headers={"Accept": "application/json", "User-Agent": "Universal-Web-API-Adapter-Update/1.0"},
            timeout=(5, 15), stream=True,
        )
        try:
            response.raise_for_status()
            return support._read_limited_response(response)
        finally:
            response.close()

    return AdapterUpdater(
        config_engine.sites_dir, fetch_bytes, app_version=app_version,
        reload=config_engine.reload_config, lock=config_engine._io_lock,
    )
