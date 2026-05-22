"""Git/main-branch compare helpers for config routes."""

import json
import os
import subprocess
from typing import Optional, Dict, Any

from fastapi import HTTPException

from app.api.config_route_models import _normalize_preset_config_payload
from app.services.config_engine import config_engine, ConfigConstants

def _load_git_branch_sites_config(branch_name: str = "main") -> Dict[str, Any]:
    """从 Git 分支中读取 config/sites.json 的已提交版本。"""
    project_root = getattr(ConfigConstants, "_PROJECT_ROOT", "") or os.getcwd()
    relative_config_path = os.path.relpath(
        ConfigConstants.CONFIG_FILE,
        project_root
    ).replace("\\", "/")

    try:
        result = subprocess.run(
            ["git", "show", f"{branch_name}:{relative_config_path}"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        stderr = str(exc.stderr or "").strip()
        detail = stderr or f"无法读取分支 {branch_name} 中的 {relative_config_path}"
        raise HTTPException(status_code=404, detail=detail)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="系统未找到 git 命令")

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"{branch_name} 分支中的 {relative_config_path} 不是合法 JSON: {exc}"
        )

    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail=f"{relative_config_path} 顶层必须是对象")

    return {
        "path": relative_config_path,
        "sites": {
            key: value
            for key, value in payload.items()
            if not str(key).startswith("_")
        }
    }


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
    "prompt_padding": "首尾填充",
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
    branch_payload = _load_git_branch_sites_config("main")
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
        "counts": counts,
        "items": items,
    }


# ================= 认证依赖 =================

__all__ = [
    '_load_git_branch_sites_config',
    '_resolve_branch_preset_config',
    '_build_main_branch_compare_summary',
]
