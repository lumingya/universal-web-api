"""R1-1：站点适配器 Schema v1。

只校验“形状”：类型、必填项、嵌套关系，以及 default_preset 必须指向已有预设。
语义校验（工作流 DSL、选择器能否命中等）仍由 ``flow_runtime.validate_workflow`` 和
ConfigEngine 的各个 ``_validate_*`` 负责。为了不误伤现有配置和用户自建预设，未知字段一律放行，
预设内部也不设必填项。

单站点文件（R1-2，``config/sites/<站点>.json``）的外层信封::

    {"schema_version": 1, "site": "chat.deepseek.com", "adapter_version": "2026.09.26",
     "min_app_version": "3.0.0", "last_verified": "2026-09-26", "config": {...}}

``config`` 与旧版 ``config/sites.json`` 中对应节点的内容完全相同。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from jsonschema import Draft202012Validator

SITE_FILE_SCHEMA_VERSION = 1
GLOBAL_KEY = "_global"

_OBJECT = {"type": "object"}

WORKFLOW_STEP_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["action"],
    "properties": {
        "action": {"type": "string", "minLength": 1},
        "target": {"type": ["string", "null"]},
        "optional": {"type": "boolean"},
        "label": {"type": "string"},
        "selector": {"type": "string"},
        "execution": _OBJECT,
        "flow_version": {"type": "integer"},
        "retry_safe": {"type": "boolean"},
    },
}

PRESET_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "selectors": {"type": "object", "additionalProperties": {"type": ["string", "null"]}},
        "workflow": {"type": "array", "items": WORKFLOW_STEP_SCHEMA},
        "stealth": {"type": "boolean"},
        "stream_config": _OBJECT,
        "image_extraction": _OBJECT,
        "file_paste": _OBJECT,
        "prompt_padding": _OBJECT,
        "advanced": _OBJECT,
    },
}

SITE_CONFIG_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["presets"],
    "properties": {
        "presets": {"type": "object", "minProperties": 1, "additionalProperties": PRESET_SCHEMA},
        "default_preset": {"type": "string", "minLength": 1},
        "advanced": _OBJECT,
    },
}

GLOBAL_CONFIG_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "selector_definitions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["key"],
                "properties": {
                    "key": {"type": "string", "minLength": 1},
                    "description": {"type": "string"},
                    "enabled": {"type": "boolean"},
                    "required": {"type": "boolean"},
                },
            },
        },
    },
}

SITE_FILE_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema_version", "site", "config"],
    "properties": {
        "schema_version": {"const": SITE_FILE_SCHEMA_VERSION},
        "site": {"type": "string", "minLength": 1},
        "adapter_version": {"type": "string"},
        "min_app_version": {"type": "string", "pattern": r"^\d+(\.\d+){0,3}$"},
        "last_verified": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "config": _OBJECT,
    },
}

_VALIDATORS = {
    name: Draft202012Validator(schema)
    for name, schema in (
        ("site", SITE_CONFIG_SCHEMA),
        ("global", GLOBAL_CONFIG_SCHEMA),
        ("file", SITE_FILE_SCHEMA),
    )
}


def _format_path(prefix: str, parts: Iterable[Any]) -> str:
    path = prefix
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def _schema_errors(kind: str, payload: Any, prefix: str) -> List[str]:
    errors = sorted(_VALIDATORS[kind].iter_errors(payload), key=lambda e: list(map(str, e.absolute_path)))
    return [f"{_format_path(prefix, error.absolute_path)}: {error.message}" for error in errors]


def site_config_errors(site: str, config: Any) -> List[str]:
    """校验单个站点（或 ``_global``）的配置，返回可读的路径级错误列表；空列表表示通过。"""
    prefix = f"sites[{site}]"
    if site == GLOBAL_KEY:
        return _schema_errors("global", config, prefix)
    errors = _schema_errors("site", config, prefix)
    if not errors and isinstance(config, dict):
        default = config.get("default_preset")
        presets = config.get("presets") or {}
        if default and default not in presets:
            errors.append(f"{prefix}.default_preset: '{default}' 不在 presets 中（可选：{', '.join(presets)}）")
    return errors


def site_file_errors(payload: Any) -> List[str]:
    """校验单站点文件（信封 + config）。"""
    errors = _schema_errors("file", payload, "file")
    if errors:
        return errors
    return site_config_errors(payload["site"], payload["config"])


def sites_errors(sites: Any) -> Dict[str, List[str]]:
    """校验整份站点配置字典（旧 sites.json 结构），返回 {站点: [错误...]}，只包含有错误的站点。"""
    if not isinstance(sites, dict):
        return {"*": [f"sites: 应为对象，实际为 {type(sites).__name__}"]}
    result: Dict[str, List[str]] = {}
    for site, config in sites.items():
        errors = site_config_errors(str(site), config)
        if errors:
            result[str(site)] = errors
    return result
