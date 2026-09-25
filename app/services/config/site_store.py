"""R1-2：站点配置目录存储（``config/sites/<站点>.json``）。

旧版把全部站点放在一个 271KB 的 ``config/sites.json`` 里；现在每个站点（以及 ``_global``）一个文件，
外层是 R1-1 定义的信封（见 ``site_schema.py``）。本模块负责：

- 读取目录并拼回与旧 ``sites.json`` **完全相同**的字典（``{"_global": {...}, "<域名>": {...}}``），
  所以 ConfigEngine 以外的代码无需改动；
- 只重写内容有变化的站点文件（两阶段：先写全部临时文件，再逐个 ``os.replace``），保留信封元数据；
  被删除的站点移入 ``.trash/``，不直接删除；
- 自动迁移旧的 ``config/sites.json``：目录为空时直接拆分；目录里已有发布版默认文件时（通过更新器升级的场景），
  按更新器 ``merge_site_records`` 的语义合并（同一站点优先保留本地值、补入发布版新增字段）；
  迁移后旧文件改名为 ``sites.json.migrated-<时间>.bak``；
- 读不懂的文件（JSON 损坏或信封不完整）只告警跳过，并且**永远不会被保存逻辑覆盖或移入回收站**；
  热重载时这类站点沿用上一次成功加载的配置。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import get_logger
from app.services.config.site_schema import SITE_FILE_SCHEMA_VERSION, site_file_errors

logger = get_logger("SITE_STORE")

INDEX_FILE_NAME = "index.json"
TRASH_DIR_NAME = ".trash"
DEFAULT_MIN_APP_VERSION = "3.0.0"
_ENVELOPE_ORDER = ("schema_version", "site", "adapter_version", "min_app_version", "last_verified")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def canonical_config_text(config: Any) -> str:
    """与格式、键顺序无关的规范化文本，用于变更检测与摘要（R1-3 的 config_sha256 也基于它）。"""
    return json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def site_filename(site: str) -> str:
    """站点键 -> 文件名。保留 ``[A-Za-z0-9._-]``，其余字节百分号编码；避开 Windows 保留名与 index.json。"""
    encoded = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in "._-")) else "".join(f"%{b:02X}" for b in ch.encode("utf-8"))
        for ch in str(site)
    )
    if not encoded or encoded.startswith(".") or encoded.split(".")[0].lower() in _WINDOWS_RESERVED | {"index"}:
        encoded = "%5F" + encoded
    return f"{encoded}.json"


def build_envelope(site: str, config: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    meta = dict(meta or {})
    payload: Dict[str, Any] = {"schema_version": SITE_FILE_SCHEMA_VERSION, "site": site}
    for key in _ENVELOPE_ORDER[2:]:
        if meta.get(key) not in (None, ""):
            payload[key] = meta[key]
    for key, value in meta.items():  # 其余元数据（例如 R1-3 的 origin）原样保留
        if key not in payload and key != "config":
            payload[key] = value
    payload["config"] = config
    return payload


def _dump(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


class SiteStore:
    """``config/sites/`` 目录的读写与旧版迁移。线程安全由调用方（ConfigEngine 的 ``_io_lock``）保证。"""

    def __init__(self, sites_dir: os.PathLike | str, legacy_file: os.PathLike | str):
        self.sites_dir = Path(sites_dir)
        self.legacy_file = Path(legacy_file)
        self._files: Dict[str, Path] = {}
        self._meta: Dict[str, Dict[str, Any]] = {}
        self._on_disk: Dict[str, str] = {}
        self._broken: Dict[Path, str] = {}
        self._broken_sites: Dict[Path, str] = {}  # 损坏文件 -> 上次成功加载时对应的站点

    # ------------------------------------------------------------------ 查询
    def site_files(self) -> List[Path]:
        if not self.sites_dir.is_dir():
            return []
        return sorted(
            path for path in self.sites_dir.glob("*.json")
            if path.is_file() and path.name != INDEX_FILE_NAME and not path.name.startswith(".")
        )

    def exists(self) -> bool:
        return bool(self.site_files()) or self.legacy_file.is_file()

    def signature(self) -> Tuple:
        """目录内容签名（文件名、mtime、大小），用于热重载的变更检测。"""
        entries = []
        for path in self.site_files():
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((path.name, stat.st_mtime_ns, stat.st_size))
        if self.legacy_file.is_file():
            stat = self.legacy_file.stat()
            entries.append(("<legacy>", stat.st_mtime_ns, stat.st_size))
        return tuple(entries)

    def metadata(self, site: str) -> Dict[str, Any]:
        return dict(self._meta.get(site) or {})

    def broken_files(self) -> Dict[str, str]:
        return {str(path): reason for path, reason in self._broken.items()}

    def file_for(self, site: str) -> Path:
        return self._files.get(site) or (self.sites_dir / site_filename(site))

    # ------------------------------------------------------------------ 读取
    def read_envelope(self, path: Path) -> Dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or not isinstance(payload.get("site"), str) or not isinstance(payload.get("config"), dict):
            raise ValueError("不是有效的站点文件（缺少 site/config 字段）")
        return payload

    def load(self, previous: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """读取全部站点，返回旧 sites.json 结构的字典；目录与旧文件都不存在时返回 None。

        ``previous`` 是上一次加载的结果：热重载时，暂时读不懂的文件沿用其中对应站点的配置。
        """
        if self.legacy_file.is_file():
            self.migrate_legacy()
        files = self.site_files()
        if not files:
            return None

        data: Dict[str, Any] = {}
        files_map: Dict[str, Path] = {}
        meta: Dict[str, Dict[str, Any]] = {}
        on_disk: Dict[str, str] = {}
        broken: Dict[Path, str] = {}
        previous_sites_by_path = {path: site for site, path in self._files.items()}
        previous_sites_by_path.update(self._broken_sites)
        broken_sites: Dict[Path, str] = {}

        for path in files:
            try:
                payload = self.read_envelope(path)
            except Exception as exc:  # JSON 损坏、编码错误、信封不完整
                broken[path] = str(exc)
                logger.error(f"站点配置文件无法读取，已跳过且不会被覆盖: {path.name}: {exc}")
                old_site = previous_sites_by_path.get(path)
                if old_site:
                    broken_sites[path] = old_site
                    if previous and old_site in previous:
                        data[old_site] = previous[old_site]
                        logger.warning(f"站点 {old_site} 暂时沿用上一次成功加载的配置")
                continue
            site = payload["site"]
            if site in files_map:
                broken[path] = f"与 {files_map[site].name} 重复声明站点 {site}"
                logger.error(f"站点配置文件重复声明 {site}，已忽略: {path.name}")
                continue
            for error in site_file_errors(payload)[:5]:
                logger.warning(f"站点配置 Schema 告警（{path.name}）: {error}")
            data[site] = payload["config"]
            files_map[site] = path
            meta[site] = {key: value for key, value in payload.items() if key != "config"}
            on_disk[site] = canonical_config_text(payload["config"])

        self._files, self._meta, self._on_disk = files_map, meta, on_disk
        self._broken, self._broken_sites = broken, broken_sites
        return data

    # ------------------------------------------------------------------ 保存
    def save(self, full_config: Dict[str, Any]) -> List[str]:
        """保存旧 sites.json 结构的字典，返回实际重写的站点列表。"""
        self.sites_dir.mkdir(parents=True, exist_ok=True)
        pending: List[Tuple[str, Path, Path, Dict[str, Any], str]] = []
        taken = {path for path in self._files.values()} | set(self._broken)
        try:
            for site, config in full_config.items():
                if not isinstance(config, dict):
                    continue
                text = canonical_config_text(config)
                path = self._files.get(site)
                if path is not None and self._on_disk.get(site) == text and path.is_file():
                    continue
                if path is None:
                    path = self._unique_path(site, taken)
                    taken.add(path)
                if path in self._broken:
                    logger.error(f"站点 {site} 对应的文件 {path.name} 无法解析，为避免覆盖用户手改内容，本次跳过保存")
                    continue
                payload = build_envelope(site, config, self._meta.get(site) or self._default_meta())
                tmp = path.with_name(path.name + ".tmp")
                with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(_dump(payload))
                    handle.flush()
                    os.fsync(handle.fileno())
                pending.append((site, path, tmp, payload, text))
        except Exception:
            for _, _, tmp, _, _ in pending:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise

        written = []
        for site, path, tmp, payload, text in pending:
            os.replace(tmp, path)
            self._files[site] = path
            self._on_disk[site] = text
            self._meta[site] = {key: value for key, value in payload.items() if key != "config"}
            written.append(site)

        for site in [s for s in self._files if s not in full_config]:
            path = self._files.pop(site)
            self._on_disk.pop(site, None)
            self._meta.pop(site, None)
            if path in self._broken or not path.is_file():
                continue
            trash = self.sites_dir / TRASH_DIR_NAME
            trash.mkdir(exist_ok=True)
            target = trash / f"{path.stem}.{_timestamp()}.json"
            os.replace(path, target)
            logger.info(f"站点 {site} 已删除，配置文件移入回收站: {target}")
        return written

    def _default_meta(self) -> Dict[str, Any]:
        return {"min_app_version": DEFAULT_MIN_APP_VERSION}

    def _unique_path(self, site: str, taken: set) -> Path:
        base = self.sites_dir / site_filename(site)
        path, counter = base, 2
        while path in taken or (path.exists() and path not in self._files.values()):
            path = base.with_name(f"{base.stem}~{counter}.json")
            counter += 1
        return path

    # ------------------------------------------------------------------ 迁移
    def migrate_legacy(self) -> Optional[Path]:
        """把旧的 config/sites.json 迁移进目录，返回备份文件路径；旧文件无法解析时保持原样并返回 None。"""
        try:
            raw = self.legacy_file.read_text(encoding="utf-8-sig").strip()
            legacy = json.loads(raw) if raw else {}
            if not isinstance(legacy, dict):
                raise ValueError("顶层不是对象")
        except Exception as exc:
            logger.error(f"旧版站点配置 {self.legacy_file} 无法解析，暂不迁移（原文件保持不变）: {exc}")
            return None

        from updater import merge_site_records  # 与更新器合并 sites.json 的语义保持一致

        self.sites_dir.mkdir(parents=True, exist_ok=True)
        existing: Dict[str, Tuple[Path, Dict[str, Any]]] = {}
        for path in self.site_files():
            try:
                payload = self.read_envelope(path)
            except Exception:
                continue
            existing[payload["site"]] = (path, payload)

        taken = {path for path, _ in existing.values()}
        merged_count = created_count = 0
        for site, config in legacy.items():
            if not isinstance(config, dict) or (site.startswith("_") and site != "_global"):
                continue
            if site in existing:
                path, payload = existing[site]
                payload = dict(payload)
                payload["config"] = merge_site_records(config, payload["config"])
                merged_count += 1
            else:
                path = self._unique_path(site, taken)
                taken.add(path)
                payload = build_envelope(site, config, self._default_meta())
                created_count += 1
            _write_text_atomic(path, _dump(payload))

        backup = self.legacy_file.with_name(f"{self.legacy_file.name}.migrated-{_timestamp()}.bak")
        os.replace(self.legacy_file, backup)
        logger.warning(
            f"已把旧版站点配置迁移到 {self.sites_dir}：新建 {created_count} 个、与发布版合并 {merged_count} 个；"
            f"原文件已备份为 {backup.name}"
        )
        return backup


def load_sites_dict(sites_dir: os.PathLike | str) -> Dict[str, Any]:
    """只读地拼出目录里的全部站点（不迁移、不告警），供测试、脚本与导出使用。"""
    store = SiteStore(sites_dir, Path(sites_dir) / "__no_legacy__.json")
    return store.load() or {}
