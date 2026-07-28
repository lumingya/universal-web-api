"""Official main-branch compare helpers for config routes."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import quote

import requests

from fastapi import HTTPException

from app.api.config_route_models import _normalize_preset_config_payload
from app.services.config_engine import config_engine, ConfigConstants
from app.utils.remote_resource import (
    UnsafeRemoteResourceError,
    get_public_remote_resource,
)


DEFAULT_OFFICIAL_REPO = "lumingya/universal-web-api"
OFFICIAL_CONFIG_CACHE_DIR = (
    Path(getattr(ConfigConstants, "_PROJECT_ROOT", "") or os.getcwd())
    / "temp"
    / "official_config_cache"
)
OFFICIAL_CONFIG_CACHE_FILE = "sites-main.json"
OFFICIAL_CONFIG_META_FILE = "sites-main.meta.json"
MAX_OFFICIAL_CONFIG_BYTES = 16 * 1024 * 1024
_OFFICIAL_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_OFFICIAL_CONFIG_CACHE_LOCK = threading.Lock()


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _official_config_relative_path() -> str:
    project_root = getattr(ConfigConstants, "_PROJECT_ROOT", "") or os.getcwd()
    return os.path.relpath(ConfigConstants.CONFIG_FILE, project_root).replace("\\", "/")


def _resolve_official_repo() -> str:
    repo = str(os.getenv("GITHUB_REPO", DEFAULT_OFFICIAL_REPO) or "").strip()
    if not _OFFICIAL_REPO_PATTERN.fullmatch(repo):
        raise ValueError("GITHUB_REPO 格式无效，应为 owner/repo")
    return repo


def _official_config_url(repo: str, branch_name: str, relative_path: str) -> str:
    encoded_branch = quote(str(branch_name or "main").strip() or "main", safe="")
    encoded_path = quote(relative_path, safe="/")
    return f"https://raw.githubusercontent.com/{repo}/{encoded_branch}/{encoded_path}"


def _cache_paths() -> tuple[Path, Path]:
    return (
        OFFICIAL_CONFIG_CACHE_DIR / OFFICIAL_CONFIG_CACHE_FILE,
        OFFICIAL_CONFIG_CACHE_DIR / OFFICIAL_CONFIG_META_FILE,
    )


def _read_json_object(path: Path) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = -1
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _read_official_config_cache(
    repo: str,
    branch_name: str,
    relative_path: str,
) -> Optional[Dict[str, Any]]:
    config_path, meta_path = _cache_paths()
    payload = _read_json_object(config_path)
    metadata = _read_json_object(meta_path)
    if payload is None or metadata is None:
        return None
    if (
        metadata.get("repository") != repo
        or metadata.get("branch") != branch_name
        or metadata.get("path") != relative_path
    ):
        return None
    return {"payload": payload, "metadata": metadata}


def _parse_official_config_bytes(content: bytes, relative_path: str) -> Dict[str, Any]:
    if len(content) > MAX_OFFICIAL_CONFIG_BYTES:
        raise ValueError("官方配置文件超过大小限制")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"官方 {relative_path} 不是合法 UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"官方 {relative_path} 顶层必须是对象")
    return payload


def _read_limited_response(response: requests.Response) -> bytes:
    content_length = str(response.headers.get("Content-Length") or "").strip()
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = 0
        if declared_length > MAX_OFFICIAL_CONFIG_BYTES:
            raise ValueError("官方配置文件超过大小限制")

    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_OFFICIAL_CONFIG_BYTES:
            raise ValueError("官方配置文件超过大小限制")
        chunks.append(chunk)
    return b"".join(chunks)


def _source_payload(
    metadata: Dict[str, Any],
    *,
    status: str,
    stale: bool,
    warning: str = "",
) -> Dict[str, Any]:
    return {
        "repository": str(metadata.get("repository") or ""),
        "branch": str(metadata.get("branch") or "main"),
        "path": str(metadata.get("path") or "config/sites.json"),
        "url": str(metadata.get("url") or ""),
        "status": status,
        "stale": bool(stale),
        "fetched_at": str(metadata.get("fetched_at") or ""),
        "checked_at": str(metadata.get("checked_at") or ""),
        "warning": warning,
    }


def _build_sites_payload(payload: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "path": source["path"],
        "sites": {
            key: value
            for key, value in payload.items()
            if not str(key).startswith("_")
        },
        "source": source,
    }


def _fetch_failure_text(exc: Exception) -> str:
    if isinstance(exc, requests.Timeout):
        return "连接官方仓库超时"
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"官方仓库返回 HTTP {exc.response.status_code}"
    if isinstance(exc, UnsafeRemoteResourceError):
        return "官方仓库地址未通过网络安全校验"
    return str(exc).strip() or exc.__class__.__name__


def _load_official_sites_config(branch_name: str = "main") -> Dict[str, Any]:
    """实时校验并读取官方分支配置，网络失败时回退到最近一次有效缓存。"""
    branch = str(branch_name or "main").strip() or "main"
    relative_path = _official_config_relative_path()
    checked_at = _utc_now_text()

    try:
        repo = _resolve_official_repo()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    url = _official_config_url(repo, branch, relative_path)

    with _OFFICIAL_CONFIG_CACHE_LOCK:
        cached = _read_official_config_cache(repo, branch, relative_path)
        cached_meta = dict(cached["metadata"]) if cached else {}
        headers = {
            "Accept": "application/json",
            "User-Agent": "Universal-Web-API-Config-Compare/1.0",
        }
        if cached_meta.get("etag"):
            headers["If-None-Match"] = str(cached_meta["etag"])
        if cached_meta.get("last_modified"):
            headers["If-Modified-Since"] = str(cached_meta["last_modified"])

        response = None
        try:
            response = get_public_remote_resource(
                url,
                headers=headers,
                timeout=(5, 15),
                stream=True,
            )

            if response.status_code == 304 and cached:
                cached_meta["checked_at"] = checked_at
                _, meta_path = _cache_paths()
                _atomic_write_json(meta_path, cached_meta)
                source = _source_payload(
                    cached_meta,
                    status="validated_cache",
                    stale=False,
                )
                return _build_sites_payload(cached["payload"], source)

            response.raise_for_status()
            content = _read_limited_response(response)
            payload = _parse_official_config_bytes(content, relative_path)
            metadata = {
                "repository": repo,
                "branch": branch,
                "path": relative_path,
                "url": url,
                "etag": str(response.headers.get("ETag") or ""),
                "last_modified": str(response.headers.get("Last-Modified") or ""),
                "fetched_at": checked_at,
                "checked_at": checked_at,
            }
            config_path, meta_path = _cache_paths()
            _atomic_write_json(config_path, payload)
            _atomic_write_json(meta_path, metadata)
            source = _source_payload(metadata, status="remote", stale=False)
            return _build_sites_payload(payload, source)
        except (
            OSError,
            ValueError,
            requests.RequestException,
            UnsafeRemoteResourceError,
        ) as exc:
            failure = _fetch_failure_text(exc)
            if cached:
                cached_meta["checked_at"] = checked_at
                source = _source_payload(
                    cached_meta,
                    status="cache_fallback",
                    stale=True,
                    warning=f"获取官方最新配置失败，当前使用本地缓存：{failure}",
                )
                return _build_sites_payload(cached["payload"], source)
            raise HTTPException(
                status_code=503,
                detail=f"无法获取官方配置，且本地没有可用缓存：{failure}",
            ) from exc
        finally:
            if response is not None:
                response.close()


def _resolve_branch_preset_config(
    site_config: Dict[str, Any],
    requested_preset_name: Optional[str] = None,
) -> Dict[str, Any]:
    """从分支中的站点配置里解析最合适的预设。"""
    if not isinstance(site_config, dict):
        raise HTTPException(status_code=500, detail="站点配置格式无效")

    presets = site_config.get("presets")
    if not isinstance(presets, dict) or not presets:
        return {
            "preset_name": str(requested_preset_name or "主预设").strip() or "主预设",
            "config": _normalize_preset_config_payload(site_config),
            "match_mode": "legacy_flat",
        }

    requested = str(requested_preset_name or "").strip()
    if requested:
        resolved = config_engine._resolve_preset_alias_key(requested, presets)
        if resolved in presets:
            return {
                "preset_name": resolved,
                "config": _normalize_preset_config_payload(presets[resolved]),
                "match_mode": "exact",
            }

    default_preset = str(site_config.get("default_preset") or "").strip()
    if default_preset in presets:
        return {
            "preset_name": default_preset,
            "config": _normalize_preset_config_payload(presets[default_preset]),
            "match_mode": "default",
        }

    if "主预设" in presets:
        return {
            "preset_name": "主预设",
            "config": _normalize_preset_config_payload(presets["主预设"]),
            "match_mode": "main_preset",
        }

    first_key = next(iter(presets))
    return {
        "preset_name": first_key,
        "config": _normalize_preset_config_payload(presets[first_key]),
        "match_mode": "first",
    }


_PRESET_COMPARE_FIELD_ORDER = [
    "selectors",
    "workflow",
    "stream_config",
    "image_extraction",
    "file_paste",
    "prompt_padding",
    "stealth",
    "extractor_id",
    "extractor_verified",
]

_PRESET_COMPARE_FIELD_LABELS = {
    "selectors": "选择器",
    "workflow": "工作流",
    "stream_config": "流式配置",
    "image_extraction": "图片提取",
    "file_paste": "文件粘贴",
    "prompt_padding": "开头注入",
    "stealth": "低熵模式",
    "extractor_id": "提取器",
    "extractor_verified": "提取器验证",
}


def _stable_compare_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _extract_site_presets_for_compare(site_config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(site_config, dict):
        return {}

    presets = site_config.get("presets")
    if isinstance(presets, dict) and presets:
        normalized = {}
        for preset_name, preset_config in presets.items():
            if not isinstance(preset_config, dict):
                continue
            normalized[str(preset_name)] = _normalize_preset_config_payload(preset_config)
        if normalized:
            return normalized

    try:
        fallback_name = str(site_config.get("default_preset") or "主预设").strip() or "主预设"
        return {
            fallback_name: _normalize_preset_config_payload(site_config)
        }
    except HTTPException:
        return {}


def _get_preset_compare_keys(local_config: Dict[str, Any], main_config: Dict[str, Any]) -> list[str]:
    remaining = set(local_config.keys()) | set(main_config.keys())
    ordered = []

    for key in _PRESET_COMPARE_FIELD_ORDER:
        if key in remaining:
            ordered.append(key)
            remaining.remove(key)

    ordered.extend(sorted(remaining, key=lambda item: str(item)))
    return ordered


def _collect_preset_different_fields(local_config: Dict[str, Any], main_config: Dict[str, Any]) -> list[str]:
    different_fields = []
    for key in _get_preset_compare_keys(local_config, main_config):
        local_has = key in local_config
        main_has = key in main_config
        if not local_has or not main_has:
            different_fields.append(key)
            continue
        if _stable_compare_dump(local_config[key]) != _stable_compare_dump(main_config[key]):
            different_fields.append(key)
    return different_fields


def _build_main_branch_compare_summary() -> Dict[str, Any]:
    config_engine.refresh_if_changed()
    branch_payload = _load_official_sites_config("main")
    local_sites = {
        key: value
        for key, value in config_engine.sites.items()
        if not str(key).startswith("_") and isinstance(value, dict)
    }
    main_sites = branch_payload["sites"]

    items = []
    counts = {
        "same": 0,
        "different": 0,
        "local_only_preset": 0,
        "local_only_site": 0,
        "main_only_preset": 0,
        "main_only_site": 0,
    }

    for domain in sorted(local_sites.keys(), key=lambda item: str(item)):
        local_site = local_sites[domain]
        local_presets = _extract_site_presets_for_compare(local_site)
        main_site = main_sites.get(domain)
        main_presets = _extract_site_presets_for_compare(main_site) if isinstance(main_site, dict) else {}
        matched_main_presets = set()

        for local_preset_name in sorted(local_presets.keys(), key=lambda item: str(item)):
            local_preset_config = local_presets[local_preset_name]
            item = {
                "domain": domain,
                "local_preset_name": local_preset_name,
                "main_preset_name": "",
                "local_exists": True,
                "main_exists": bool(main_site),
                "match_mode": "",
                "different_fields": [],
                "different_field_labels": [],
                "difference_count": 0,
                "detail_available": True,
                "summary_text": "",
                "status": "same",
            }

            if not main_site:
                item["status"] = "local_only_site"
                item["difference_count"] = 1
                item["summary_text"] = "main 分支中没有这个站点"
                counts["local_only_site"] += 1
                items.append(item)
                continue

            resolved_main_preset_name = config_engine._resolve_preset_alias_key(local_preset_name, main_presets)
            if resolved_main_preset_name not in main_presets:
                item["status"] = "local_only_preset"
                item["difference_count"] = 1
                item["summary_text"] = "main 分支中没有同名预设"
                counts["local_only_preset"] += 1
                items.append(item)
                continue

            matched_main_presets.add(resolved_main_preset_name)
            main_preset_config = main_presets[resolved_main_preset_name]
            different_fields = _collect_preset_different_fields(local_preset_config, main_preset_config)

            item["main_preset_name"] = resolved_main_preset_name
            item["match_mode"] = "exact" if resolved_main_preset_name == local_preset_name else "alias"
            item["different_fields"] = different_fields
            item["different_field_labels"] = [
                _PRESET_COMPARE_FIELD_LABELS.get(field, field)
                for field in different_fields
            ]
            item["difference_count"] = len(different_fields)

            if different_fields:
                item["status"] = "different"
                item["summary_text"] = f"{len(different_fields)} 项字段与官方预设不同"
                counts["different"] += 1
            else:
                item["status"] = "same"
                item["summary_text"] = "与官方预设一致"
                counts["same"] += 1

            items.append(item)

        for main_preset_name in sorted(main_presets.keys(), key=lambda item: str(item)):
            if main_preset_name in matched_main_presets:
                continue
            counts["main_only_preset"] += 1

    for domain in sorted(set(main_sites.keys()) - set(local_sites.keys()), key=lambda item: str(item)):
        main_presets = _extract_site_presets_for_compare(main_sites.get(domain))
        counts["main_only_site"] += max(1, len(main_presets))

    status_priority = {
        "different": 0,
        "local_only_preset": 1,
        "local_only_site": 2,
        "same": 3,
    }
    items.sort(
        key=lambda item: (
            status_priority.get(str(item.get("status") or ""), 99),
            str(item.get("domain") or ""),
            str(item.get("local_preset_name") or ""),
        )
    )

    return {
        "branch": "main",
        "path": branch_payload["path"],
        "source": branch_payload["source"],
        "counts": counts,
        "items": items,
    }


# ================= 认证依赖 =================

__all__ = [
    '_load_official_sites_config',
    '_resolve_branch_preset_config',
    '_build_main_branch_compare_summary',
]
