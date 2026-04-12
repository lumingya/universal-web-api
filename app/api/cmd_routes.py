"""
app/api/cmd_routes.py - 命令系统 API 路由

职责：
- 命令 CRUD 接口
- 元信息查询
- 手动触发（调试用）
"""

import json
import threading
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, Field

from app.core.config import AppConfig, get_logger
from app.services.command_engine import command_engine
from app.services.request_manager import request_manager, RequestStatus

logger = get_logger("API.CMD")

router = APIRouter(tags=["commands"])


SECRET_PLACEHOLDER = "__SECRET_REDACTED__"
SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "auth_token",
    "api_key",
    "x_api_key",
}


def _normalize_secret_key(key: object) -> str:
    return str(key or "").strip().lower().replace("-", "_")


def _try_parse_json_text(value: object):
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or stripped[:1] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except Exception:
        return None


def _redact_command_secrets(data):
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            normalized = _normalize_secret_key(key)
            if normalized in SENSITIVE_KEYS and value not in (None, ""):
                result[key] = SECRET_PLACEHOLDER
            else:
                result[key] = _redact_command_secrets(value)
        return result
    if isinstance(data, list):
        return [_redact_command_secrets(item) for item in data]
    parsed = _try_parse_json_text(data)
    if isinstance(parsed, (dict, list)):
        redacted = _redact_command_secrets(parsed)
        return json.dumps(redacted, ensure_ascii=False, indent=2)
    return data


def _restore_secret_placeholders(updates, existing):
    if isinstance(updates, dict) and isinstance(existing, dict):
        result = {}
        for key, value in updates.items():
            normalized = _normalize_secret_key(key)
            existing_value = existing.get(key)
            if normalized in SENSITIVE_KEYS and value == SECRET_PLACEHOLDER:
                result[key] = existing_value
            else:
                result[key] = _restore_secret_placeholders(value, existing_value)
        return result
    if isinstance(updates, list) and isinstance(existing, list):
        result = []
        for idx, value in enumerate(updates):
            existing_value = existing[idx] if idx < len(existing) else None
            result.append(_restore_secret_placeholders(value, existing_value))
        return result
    parsed_updates = _try_parse_json_text(updates)
    parsed_existing = _try_parse_json_text(existing)
    if isinstance(parsed_updates, (dict, list)) and isinstance(parsed_existing, type(parsed_updates)):
        restored = _restore_secret_placeholders(parsed_updates, parsed_existing)
        return json.dumps(restored, ensure_ascii=False, indent=2)
    return updates


# ================= 认证依赖 =================

async def verify_auth(authorization: Optional[str] = Header(None)) -> bool:
    if not AppConfig.is_auth_enabled():
        return True
    if not AppConfig.AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="服务配置错误")
    if not authorization:
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    token = authorization.replace("Bearer ", "").strip()
    if token != AppConfig.get_auth_token():
        raise HTTPException(status_code=401, detail="认证令牌无效")
    return True


# ================= 请求模型 =================

class CommandCreateRequest(BaseModel):
    name: str = Field(default="新命令", max_length=100)
    enabled: bool = Field(default=True)
    log_enabled: bool = Field(default=True)
    log_level: str = Field(default="GLOBAL")
    mode: str = Field(default="simple")
    stop_on_error: bool = Field(default=False)
    trigger: dict = Field(default_factory=lambda: {
        "type": "request_count", "value": 10,
        "command_id": "",
        "action_ref": "",
        "match_rule": "equals",
        "expected_value": "",
        "match_mode": "keyword",
        "status_codes": "403,429,500,502,503,504",
        "abort_on_match": True,
        "scope": "all", "domain": "", "tab_index": None,
        "priority": 2,
        "stable_for_sec": 0,
        "check_while_busy_workflow": True
    })
    actions: list = Field(default_factory=lambda: [
        {"type": "clear_cookies"},
        {"type": "refresh_page"},
    ])
    group_name: str = Field(default="")
    script: str = Field(default="")
    script_lang: str = Field(default="javascript")


class CommandUpdateRequest(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    log_enabled: Optional[bool] = None
    log_level: Optional[str] = None
    mode: Optional[str] = None
    stop_on_error: Optional[bool] = None
    trigger: Optional[dict] = None
    actions: Optional[list] = None
    group_name: Optional[str] = None
    script: Optional[str] = None
    script_lang: Optional[str] = None


class CommandReorderRequest(BaseModel):
    command_ids: List[str]


class CommandGroupAssignRequest(BaseModel):
    command_ids: List[str]
    group_name: str = Field(default="", max_length=100)


class CommandBatchEnabledRequest(BaseModel):
    command_ids: List[str]
    enabled: bool = Field(default=True)


class CommandGroupEnabledRequest(BaseModel):
    enabled: bool = Field(default=True)


class CommandGroupRenameRequest(BaseModel):
    new_group_name: str = Field(default="", max_length=100)


class CommandGroupExecuteRequest(BaseModel):
    include_disabled: bool = Field(default=False)
    acquire_policy: str = Field(default="inherit_session")


# ================= 路由 =================

@router.get("/api/commands")
async def list_commands(authenticated: bool = Depends(verify_auth)):
    commands = command_engine.list_commands()
    return {"commands": _redact_command_secrets(commands), "count": len(commands)}


@router.get("/api/commands/meta")
async def get_meta(authenticated: bool = Depends(verify_auth)):
    return {
        "trigger_types": command_engine.get_trigger_types(),
        "action_types": command_engine.get_action_types(),
    }


@router.get("/api/commands/states")
async def get_trigger_states(authenticated: bool = Depends(verify_auth)):
    return {"states": command_engine.get_trigger_states()}


@router.get("/api/commands/{command_id}")
async def get_command(command_id: str, authenticated: bool = Depends(verify_auth)):
    cmd = command_engine.get_command(command_id)
    if not cmd:
        raise HTTPException(status_code=404, detail="命令不存在")
    return _redact_command_secrets(cmd)


@router.post("/api/commands")
async def create_command(
    body: CommandCreateRequest,
    authenticated: bool = Depends(verify_auth)
):
    cmd_data = body.model_dump()
    cmd = command_engine.add_command(cmd_data)
    return {"success": True, "command": _redact_command_secrets(cmd)}


@router.put("/api/commands/reorder")
async def reorder_commands(
    body: CommandReorderRequest,
    authenticated: bool = Depends(verify_auth)
):
    success = command_engine.reorder_commands(body.command_ids)
    return {"success": success}


@router.get("/api/command-groups")
async def list_command_groups(authenticated: bool = Depends(verify_auth)):
    groups = command_engine.list_command_groups()
    return {"groups": groups, "count": len(groups)}


@router.put("/api/command-groups")
async def assign_command_group(
    body: CommandGroupAssignRequest,
    authenticated: bool = Depends(verify_auth)
):
    updated = command_engine.set_commands_group(body.command_ids, body.group_name)
    return {"success": True, "updated": updated, "group_name": (body.group_name or "").strip()}


@router.put("/api/commands/enabled")
async def update_commands_enabled(
    body: CommandBatchEnabledRequest,
    authenticated: bool = Depends(verify_auth)
):
    updated = command_engine.set_commands_enabled(body.command_ids, body.enabled)
    return {"success": True, "updated": updated, "enabled": body.enabled}


@router.put("/api/command-groups/{group_name}/enabled")
async def update_command_group_enabled(
    group_name: str,
    body: CommandGroupEnabledRequest,
    authenticated: bool = Depends(verify_auth)
):
    normalized_name = (group_name or "").strip()
    updated = command_engine.set_group_enabled(normalized_name, body.enabled)
    return {
        "success": True,
        "updated": updated,
        "group_name": normalized_name,
        "enabled": body.enabled,
    }


@router.put("/api/command-groups/{group_name}/rename")
async def rename_command_group(
    group_name: str,
    body: CommandGroupRenameRequest,
    authenticated: bool = Depends(verify_auth)
):
    source_name = (group_name or "").strip()
    target_name = (body.new_group_name or "").strip()
    if not source_name or not target_name:
        raise HTTPException(status_code=400, detail="命令组名称不能为空")
    if source_name == target_name:
        return {"success": True, "updated": 0, "group_name": source_name, "new_group_name": target_name}

    existing_names = {item.get("name") for item in command_engine.list_command_groups()}
    if target_name in existing_names:
        raise HTTPException(status_code=400, detail=f"命令组已存在：{target_name}")

    updated = command_engine.rename_group(source_name, target_name)
    return {
        "success": True,
        "updated": updated,
        "group_name": source_name,
        "new_group_name": target_name,
    }


@router.delete("/api/command-groups/{group_name}")
async def disband_command_group(
    group_name: str,
    authenticated: bool = Depends(verify_auth)
):
    updated = command_engine.disband_group(group_name)
    return {"success": True, "updated": updated, "group_name": group_name}


@router.put("/api/commands/{command_id}")
async def update_command(
    command_id: str,
    body: CommandUpdateRequest,
    authenticated: bool = Depends(verify_auth)
):
    updates = body.model_dump(exclude_none=True)
    existing = command_engine.get_command(command_id)
    if not existing:
        raise HTTPException(status_code=404, detail="命令不存在")
    updates = _restore_secret_placeholders(updates, existing)
    cmd = command_engine.update_command(command_id, updates)
    if not cmd:
        raise HTTPException(status_code=404, detail="命令不存在")
    return {"success": True, "command": _redact_command_secrets(cmd)}


@router.delete("/api/commands/{command_id}")
async def delete_command(command_id: str, authenticated: bool = Depends(verify_auth)):
    success = command_engine.delete_command(command_id)
    if not success:
        raise HTTPException(status_code=404, detail="命令不存在")
    return {"success": True}


@router.post("/api/commands/{command_id}/test")
async def test_command(command_id: str, authenticated: bool = Depends(verify_auth)):
    """Manual trigger command test on all idle tabs that match command scope."""
    cmd = command_engine.get_command(command_id)
    if not cmd:
        raise HTTPException(status_code=404, detail="command_not_found")

    try:
        from app.core.browser import get_browser

        browser = get_browser(auto_connect=False)
        pool = browser.tab_pool

        sessions = []
        if hasattr(pool, "get_sessions_snapshot"):
            sessions = pool.get_sessions_snapshot()
        idle_sessions = [
            session for session in sessions
            if getattr(getattr(session, "status", None), "value", "") == "idle"
        ]
        idle_sessions.sort(key=lambda item: getattr(item, "persistent_index", 0))

        if not idle_sessions:
            raise HTTPException(status_code=409, detail="no_idle_tabs")

        scheduled_tabs: List[int] = []
        skipped_tabs: List[int] = []
        failed_tabs: List[dict] = []

        def _run_command_in_background(target_session, target_ctx):
            try:
                setattr(target_session, "_command_request_id", target_ctx.request_id)
                request_manager.start_request(target_ctx, tab_id=target_session.id)
                with command_engine._command_logging_context(cmd):
                    command_engine._execute_command(cmd, target_session)
                if not target_ctx.should_stop() and target_ctx.status == RequestStatus.RUNNING:
                    target_ctx.mark_completed()
            except Exception as e:
                logger.error(f"manual command test worker failed(tab={getattr(target_session, 'persistent_index', '?')}): {e}")
                target_ctx.mark_failed(str(e))
            finally:
                request_manager.finish_request(target_ctx, success=(target_ctx.status == RequestStatus.COMPLETED))
                setattr(target_session, "_command_request_id", None)
                try:
                    pool.release(target_session.id, check_triggers=False)
                except Exception as release_error:
                    logger.debug(f"manual command test release failed: {release_error}")

        for candidate in idle_sessions:
            tab_index = getattr(candidate, "persistent_index", None)
            if tab_index is None:
                continue

            try:
                # Respect command scope even in manual test mode.
                if not command_engine._matches_scope(cmd, candidate):
                    skipped_tabs.append(tab_index)
                    continue

                ctx = request_manager.create_request()
                session = pool.acquire_by_index(tab_index, ctx.request_id, timeout=3)
                if not session:
                    failed_tabs.append({"tab_index": tab_index, "error": "acquire_failed"})
                    continue

                worker = threading.Thread(
                    target=_run_command_in_background,
                    args=(session, ctx),
                    daemon=True,
                    name=f"cmd-test-{command_id[:8]}-{tab_index}",
                )
                worker.start()
                scheduled_tabs.append(tab_index)
            except Exception as e:
                failed_tabs.append({"tab_index": tab_index, "error": str(e)})

        if not scheduled_tabs:
            if skipped_tabs and not failed_tabs:
                raise HTTPException(
                    status_code=409,
                    detail=f"no_idle_tabs_match_scope, skipped={skipped_tabs}",
                )
            raise HTTPException(
                status_code=500,
                detail=f"command_test_failed, failed={failed_tabs}, skipped={skipped_tabs}",
            )

        return {
            "success": True,
            "message": f"command scheduled on tabs: {scheduled_tabs}",
            "executed_tabs": scheduled_tabs,
            "skipped_tabs": skipped_tabs,
            "failed_tabs": failed_tabs,
            "executed_count": len(scheduled_tabs),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"manual command test failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/command-groups/{group_name}/execute")
async def execute_command_group(
    group_name: str,
    body: Optional[CommandGroupExecuteRequest] = None,
    authenticated: bool = Depends(verify_auth)
):
    normalized_name = (group_name or "").strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="命令组名称不能为空")

    include_disabled = bool(body.include_disabled) if body else False
    acquire_policy = str(body.acquire_policy or "inherit_session").strip() if body else "inherit_session"

    try:
        from app.core.browser import get_browser
        browser = get_browser(auto_connect=False)
        pool = browser.tab_pool

        status = pool.get_status()
        idle_tabs = [t for t in status.get("tabs", []) if t["status"] == "idle"]
        idle_tabs.sort(key=lambda t: t.get("persistent_index", 0))

        if not idle_tabs:
            raise HTTPException(status_code=409, detail="没有空闲标签页可用于执行命令组")

        skipped_tabs: List[dict] = []
        acquire_failures: List[int] = []

        for tab_info in idle_tabs:
            tab_index = tab_info["persistent_index"]
            session = pool.acquire_by_index(tab_index, f"group_test_{normalized_name}_{tab_index}", timeout=5)
            if not session:
                acquire_failures.append(tab_index)
                continue

            try:
                plan = command_engine.preview_command_group(
                    group_name=normalized_name,
                    session=session,
                    include_disabled=include_disabled,
                )
                if not plan.get("fully_runnable", False):
                    skipped_tabs.append({
                        "tab_index": tab_index,
                        "runnable_count": plan.get("runnable_count", 0),
                        "scope_skipped": plan.get("scope_skipped", 0),
                    })
                    continue

                result = command_engine.execute_command_group(
                    group_name=normalized_name,
                    session=session,
                    include_disabled=include_disabled,
                    acquire_policy=acquire_policy,
                )
                if not result.get("ok"):
                    raise HTTPException(status_code=400, detail=result.get("error", "命令组执行失败"))
                return {
                    "success": True,
                    "message": f"命令组已在标签页 #{tab_index} 执行",
                    "tab_index": tab_index,
                    **result,
                }
            finally:
                pool.release(session.id, check_triggers=False)

        detail = {
            "error": "no_idle_tabs_match_group_scope",
            "skipped_tabs": skipped_tabs,
            "acquire_failures": acquire_failures,
        }
        raise HTTPException(status_code=409, detail=detail)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"执行命令组失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
