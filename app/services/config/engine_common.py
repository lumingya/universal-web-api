"""ConfigEngine 及其 mixin 共用的模块级定义（R2-3 拆分时从 engine.py 原样迁出，engine.py 仍重新导出）。"""

import os
import copy
import re
from app.core.config import get_logger
from typing import Dict, List, Any
from app.models.schemas import (
    WorkflowStep,
    get_default_attachment_monitor_config,
    get_default_send_confirmation_config,
)
from app.core.request_transport import get_default_request_transport_config


logger = get_logger("CFG_ENG")


# ================= 常量配置 =================

class ConfigConstants:
    """配置引擎常量"""
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    # R1-2：站点配置的事实来源是 config/sites/ 目录（每站点一个文件）；CONFIG_FILE 只作为旧版单文件的迁移来源
    CONFIG_FILE = os.getenv("SITES_CONFIG_FILE", os.path.join(_PROJECT_ROOT, "config", "sites.json"))
    SITES_DIR = os.getenv("SITES_CONFIG_DIR", os.path.join(_PROJECT_ROOT, "config", "sites"))
    SITES_LOCAL_FILE = os.getenv("SITES_LOCAL_FILE", os.path.join(_PROJECT_ROOT, "config", "sites.local.json"))
    COMMANDS_FILE = os.getenv("COMMANDS_CONFIG_FILE", os.path.join(_PROJECT_ROOT, "config", "commands.json"))
    COMMANDS_LOCAL_FILE = os.getenv("COMMANDS_LOCAL_FILE", os.path.join(_PROJECT_ROOT, "config", "commands.local.json"))
    IMAGE_PRESETS_FILE = os.path.join(_PROJECT_ROOT, "config", "image_presets.json")

    MAX_HTML_CHARS = int(os.getenv("MAX_HTML_CHARS", "120000"))
    TEXT_TRUNCATE_LENGTH = 80

    AI_MAX_RETRIES = 3
    AI_RETRY_BASE_DELAY = 1.0
    AI_RETRY_MAX_DELAY = 10.0
    AI_REQUEST_TIMEOUT = 120


_MISSING = object()


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Parse bool-like config values without treating every non-empty string as true."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
        return default
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


# ================= 预设常量 =================

DEFAULT_PRESET_NAME = "主预设"

# 预设内包含的配置字段（用于迁移和校验）
PRESET_FIELDS = [
    "selectors", "workflow", "stream_config",
    "image_extraction", "file_paste", "prompt_padding", "stealth",
    "extractor_id", "extractor_verified"
]

# 默认工作流
DEFAULT_WORKFLOW: List[WorkflowStep] = [
    {"action": "CLICK", "target": "new_chat_btn", "optional": True, "value": None},
    {"action": "WAIT", "target": "", "optional": False, "value": "0.5"},
    {"action": "FILL_INPUT", "target": "input_box", "optional": False, "value": None},
    {"action": "CLICK", "target": "send_btn", "optional": True, "value": None},
    {"action": "KEY_PRESS", "target": "Enter", "optional": True, "value": None},
    {"action": "STREAM_WAIT", "target": "result_container", "optional": False, "value": None}
]

def get_default_stream_config() -> Dict[str, Any]:
    """获取默认流式配置"""
    return {
        "mode": "dom",              # dom / network
        "request_transport": get_default_request_transport_config(),
        "hard_timeout": 300,        # 全局硬超时（秒）
        "send_confirmation": get_default_send_confirmation_config(),
        "attachment_monitor": get_default_attachment_monitor_config(),

        # 网络监听配置（可选）
        "network": None
    }


def get_default_network_config() -> Dict[str, Any]:
    """获取默认网络监听配置"""
    return {
        "listen_pattern": "",           # URL 匹配模式（必填）
        "stream_match_mode": "keyword", # 流目标匹配模式（keyword / regex）
        "stream_match_pattern": "",     # 流目标匹配表达式（为空时回退到 listen_pattern）
        "parser": "",                   # 解析器 ID（必填）
        "first_response_timeout": 300.0, # 等待首个匹配目标流量的最大时间
        "silence_threshold": 3.0,       # 静默超时（秒）
        "response_interval": 0.5        # 轮询间隔（秒）
    }


def _merge_config_patch(base: Any, patch: Any) -> Any:
    """Deep-merge object patches while letting scalars/lists replace existing values."""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return copy.deepcopy(patch)

    merged = dict(base)
    for key, value in patch.items():
        existing = base.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            merged[key] = _merge_config_patch(existing, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


_SITE_STARTUP_JS_PATTERNS = (
    re.compile(r"""location\.(?:assign|replace)\(\s*['"]([^'"]+)['"]\s*\)""", re.IGNORECASE),
    re.compile(r"""location\.href\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE),
)
