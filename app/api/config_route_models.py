"""Request models and preset normalization helpers for config routes."""

import copy
from typing import Optional, Dict, Any, List

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.models.schemas import ADVANCED_FIELDS, PRESET_ADVANCED_FIELDS, SITE_ADVANCED_FIELDS


class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""
    config: Dict[str, Any] = Field(...)


class SiteAdvancedConfigRequest(BaseModel):
    """站点级高级配置更新请求。"""
    preset_name: Optional[str] = Field(default=None)
    advanced: Optional[Dict[str, Any]] = Field(default=None)
    independent_cookies: bool = Field(default=False)
    independent_cookies_auto_takeover: bool = Field(default=False)
    input_box_stability_wait_enabled: bool = Field(default=False)
    input_box_stability_wait_after_new_chat_only: bool = Field(default=True)
    input_box_stability_wait_timeout: float = Field(default=1.5, ge=0.1, le=10.0)
    url_transition_wait_on_new_chat: bool = Field(default=False)
    url_transition_wait_patterns: List[str] = Field(default_factory=list)
    send_confirmation_check_enabled: bool = Field(default=False)
    send_confirmation_check_timeout: float = Field(default=1.5, ge=0.1, le=10.0)


class PresetConfigUpdateRequest(BaseModel):
    """单个预设完整配置更新请求。"""
    preset_name: Optional[str] = Field(default=None)
    config: Dict[str, Any] = Field(...)
    replace: bool = Field(default=False)


def _get_model_fields_set(model: BaseModel) -> set:
    fields_set = getattr(model, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(model, "__fields_set__", set())
    return set(fields_set or set())


def _extract_site_advanced_update_payload(
    body: SiteAdvancedConfigRequest,
    *,
    preset_scope: bool = False,
    allow_inherited_site_fields: bool = False,
) -> Dict[str, Any]:
    """提取高级配置更新 payload，兼容扁平字段与 GET 返回的 advanced 包裹形状。"""
    provided_fields = _get_model_fields_set(body)
    payload: Dict[str, Any] = {}

    nested_advanced = body.advanced
    if "advanced" in provided_fields and nested_advanced is not None:
        if not isinstance(nested_advanced, dict) or isinstance(nested_advanced, list):
            raise HTTPException(status_code=400, detail="advanced 必须是对象")
        if preset_scope:
            invalid_nested_site_fields = [
                key for key in SITE_ADVANCED_FIELDS
                if key in nested_advanced
            ]
            if invalid_nested_site_fields and not allow_inherited_site_fields:
                joined = ", ".join(sorted(invalid_nested_site_fields))
                raise HTTPException(status_code=400, detail=f"预设级高级配置不能包含站点级字段: {joined}")
        allowed_nested_fields = PRESET_ADVANCED_FIELDS if preset_scope else ADVANCED_FIELDS
        payload.update({
            key: copy.deepcopy(value)
            for key, value in nested_advanced.items()
            if key in allowed_nested_fields
        })

    if preset_scope:
        invalid_site_fields = [
            key for key in SITE_ADVANCED_FIELDS
            if key in provided_fields
        ]
        if invalid_site_fields:
            joined = ", ".join(sorted(invalid_site_fields))
            raise HTTPException(
                status_code=400,
                detail=f"预设级高级配置不能包含站点级字段: {joined}",
            )

    flat_payload = {
        "independent_cookies": bool(body.independent_cookies),
        "independent_cookies_auto_takeover": bool(body.independent_cookies_auto_takeover),
        "input_box_stability_wait_enabled": bool(body.input_box_stability_wait_enabled),
        "input_box_stability_wait_after_new_chat_only": bool(body.input_box_stability_wait_after_new_chat_only),
        "input_box_stability_wait_timeout": float(body.input_box_stability_wait_timeout),
        "url_transition_wait_on_new_chat": bool(body.url_transition_wait_on_new_chat),
        "url_transition_wait_patterns": [
            str(pattern or "").strip()
            for pattern in (body.url_transition_wait_patterns or [])
            if str(pattern or "").strip()
        ],
        "send_confirmation_check_enabled": bool(body.send_confirmation_check_enabled),
        "send_confirmation_check_timeout": float(body.send_confirmation_check_timeout),
    }
    payload.update({
        key: value
        for key, value in flat_payload.items()
        if key in provided_fields
    })

    return payload


def _normalize_preset_config_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """校验并规范化单个预设配置对象。"""
    if not isinstance(payload, dict) or isinstance(payload, list):
        raise HTTPException(status_code=400, detail="config 必须是对象")

    reserved_site_fields = {"presets", "default_preset"}
    invalid_fields = [key for key in reserved_site_fields if key in payload]
    if invalid_fields:
        joined = ", ".join(invalid_fields)
        raise HTTPException(
            status_code=400,
            detail=f"这里只接受单个预设配置对象，不能包含站点级字段: {joined}",
        )

    normalized = copy.deepcopy(payload)

    selectors = normalized.get("selectors")
    if selectors is None:
        normalized["selectors"] = {}
    elif not isinstance(selectors, dict) or isinstance(selectors, list):
        raise HTTPException(status_code=400, detail="selectors 必须是对象")

    workflow = normalized.get("workflow")
    if workflow is None:
        normalized["workflow"] = []
    elif not isinstance(workflow, list):
        raise HTTPException(status_code=400, detail="workflow 必须是数组")

    advanced = normalized.get("advanced")
    if advanced is not None and (
        not isinstance(advanced, dict) or isinstance(advanced, list)
    ):
        raise HTTPException(status_code=400, detail="advanced 必须是对象")
    if isinstance(advanced, dict):
        invalid_advanced_fields = [
            key for key in advanced.keys()
            if key in SITE_ADVANCED_FIELDS
        ]
        if invalid_advanced_fields:
            joined = ", ".join(invalid_advanced_fields)
            raise HTTPException(
                status_code=400,
                detail=f"预设 advanced 不能包含站点级字段: {joined}",
            )

    normalized["stealth"] = bool(normalized.get("stealth", False))
    return normalized


def _merge_preset_config_payload(
    base_payload: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """将局部预设配置叠加到现有预设上，保留未显式提供的字段。"""
    if not isinstance(base_payload, dict):
        base_payload = {}
    if not isinstance(payload, dict) or isinstance(payload, list):
        raise HTTPException(status_code=400, detail="config 必须是对象")

    return _merge_dict_patch(base_payload, payload)


def _merge_dict_patch(base_payload: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base_payload)
    for key, value in payload.items():
        existing = base_payload.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_dict_patch(existing, value)
            continue
        merged[key] = copy.deepcopy(value)
    return merged

__all__ = [
    'ConfigUpdateRequest',
    'SiteAdvancedConfigRequest',
    'PresetConfigUpdateRequest',
    '_extract_site_advanced_update_payload',
    '_get_model_fields_set',
    '_normalize_preset_config_payload',
    '_merge_preset_config_payload',
]
