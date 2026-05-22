"""Request models and preset normalization helpers for config routes."""

import copy
from typing import Optional, Dict, Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""
    config: Dict[str, Any] = Field(...)


class SiteAdvancedConfigRequest(BaseModel):
    """站点级高级配置更新请求。"""
    independent_cookies: bool = Field(default=False)
    independent_cookies_auto_takeover: bool = Field(default=False)
    input_box_stability_wait_enabled: bool = Field(default=False)
    input_box_stability_wait_after_new_chat_only: bool = Field(default=True)
    input_box_stability_wait_timeout: float = Field(default=1.5)


class PresetConfigUpdateRequest(BaseModel):
    """单个预设完整配置更新请求。"""
    preset_name: Optional[str] = Field(default=None)
    config: Dict[str, Any] = Field(...)


def _normalize_preset_config_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """校验并规范化单个预设配置对象。"""
    if not isinstance(payload, dict) or isinstance(payload, list):
        raise HTTPException(status_code=400, detail="config 必须是对象")

    reserved_site_fields = {"presets", "default_preset", "advanced"}
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

    normalized["stealth"] = bool(normalized.get("stealth", False))
    return normalized

__all__ = [
    'ConfigUpdateRequest',
    'SiteAdvancedConfigRequest',
    'PresetConfigUpdateRequest',
    '_normalize_preset_config_payload',
]
