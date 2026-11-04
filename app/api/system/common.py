"""``app/api/system`` 公共辅助：JSON/文件读写、原子写、.env 读写与重启调度。

原先全部堆在 ``app/api/system.py``，四组端点（探活/设置/更新/诊断）都要用。
"""

import json
import re
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from app.core.config import get_logger

router = APIRouter()
logger = get_logger("API.SYSTEM")

_NUMERIC_ENV_KEYS = {
    "APP_PORT", "BROWSER_PORT", "MAX_HTML_CHARS", "CANVAS_IMAGE_MAX_SIZE",
    "TOOL_CALLING_INTERNAL_RETRY_MAX", "TOOL_CALLING_MAX_TOOL_RESULT_CHARS",
    "TAB_RECOVERY_MAX_ATTEMPTS", "TAB_RECOVERY_TIMEOUT_SEC",
    "TAB_RECOVERY_WORKER_EXIT_WAIT_SEC",
    "REQUEST_ZOMBIE_TTL", "LOG_MAX_BYTES", "LOG_BACKUP_COUNT",
    "MAX_TABS", "MIN_TABS",
    "CMD_ASYNC_MAX_WORKERS", "CMD_PAGE_CHECK_FAILURE_BACKOFF_SEC",
    "CMD_PAGE_CHECK_JS_TIMEOUT_SEC", "CMD_PERIODIC_KEEPALIVE_INTERVAL_SEC",
    "CMD_REQUEST_PRIORITY_BASELINE", "CMD_TAB_POOL_REFRESH_INTERVAL_SEC",
    "TEMP_CLEANUP_MIN_AGE_SECONDS", "PASTE_TEMP_CLEANUP_MIN_AGE_SECONDS",
    "ARENA_EVENT_BRIDGE_COMMAND_TEXT_LIMIT", "ARENA_EVENT_BRIDGE_DEDUPE_MAX_ENTRIES",
    "SCHEDULED_RESTART_INTERVAL_SECONDS", "SCHEDULED_RESTART_DRAIN_TIMEOUT_SECONDS",
}

_DYNAMIC_SELECTOR_CLASS_RE = re.compile(r"^(?:_?[A-Fa-f0-9_-]{8,}|[A-Za-z0-9_-]{16,})$")


async def _read_json_object_or_400(request: Request) -> Dict[str, Any]:
    """读取 JSON 请求体，并要求顶层必须是对象。"""
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="请求体必须是有效 JSON")
    except ValueError:
        raise HTTPException(status_code=400, detail="请求体必须是有效 JSON")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    return data


def _load_env_config_from_file() -> Dict[str, Any]:
    """读取 .env 文件配置。"""
    env_path = Path(".env")
    config: Dict[str, Any] = {}

    if not env_path.exists():
        return config

    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if value.lower() == "true":
                value = True
            elif value.lower() == "false":
                value = False
            elif key in _NUMERIC_ENV_KEYS and value.isdigit():
                value = int(value)
            elif key in _NUMERIC_ENV_KEYS and re.match(r"^\d+\.\d+$", value):
                value = float(value)

            config[key] = value

    # 修复：不再凭空合成 DASHBOARD_AUTH_* —— 前端会把合成值原样回写，
    # 把「留空即沿用 AUTH_*」的动态回退语义固化成硬编码副本。
    # 生效值改由 GET /api/settings/env 的只读 effective 字段返回。

    return config
