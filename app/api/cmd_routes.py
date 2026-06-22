"""
app/api/cmd_routes.py - 命令系统 API 路由

职责：
- 命令 CRUD 接口
- 元信息查询
- 手动触发（调试用）
"""

import json
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from app.core.config import get_logger
from app.api.deps import verify_dashboard_auth as verify_auth
from app.services.command_engine import command_engine
from app.services.request_manager import request_manager, RequestStatus

logger = get_logger("API.CMD")

router = APIRouter(tags=["commands"])


SECRET_PLACEHOLDER = "__SECRET_REDACTED__"
SENSITIVE_KEY_HINTS = {
    "access_token",
    "authorization",
    "auth_token",
    "api_key",
    "apikey",
    "client_credentials",
    "credential",
    "credentials",
    "credentials_json",
    "csrf",
    "github_token",
    "password",
    "passwd",
    "private_key",
    "private_pem",
    "refresh_token",
    "secret",
    "service_account",
    "service_account_json",
    "session",
    "token",
    "x_api_key",
    "x_github_token",
}
SENSITIVE_KEY_SUFFIXES = (
    "_access_token",
    "_auth_token",
    "_token",
    "_api_key",
    "_apikey",
    "_credential",
    "_credentials",
    "_credentials_json",
    "_github_token",
    "_password",
    "_passwd",
    "_private_key",
    "_private_pem",
    "_secret",
    "_service_account",
    "_service_account_json",
)


def _normalize_secret_key(key: object) -> str:
    return str(key or "").strip().lower().replace("-", "_")


def _is_command_secret_key(key: object) -> bool:
    normalized = _normalize_secret_key(key)
    return normalized in SENSITIVE_KEY_HINTS or normalized.endswith(SENSITIVE_KEY_SUFFIXES)


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


def _restore_list_item_key(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("id", "action_id", "command_id", "name"):
        value = str(item.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return ""


def _redact_command_secrets(data):
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if _is_command_secret_key(key) and value not in (None, ""):
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
            existing_value = existing.get(key)
            if _is_command_secret_key(key) and value == SECRET_PLACEHOLDER:
                result[key] = existing_value
            else:
                result[key] = _restore_secret_placeholders(value, existing_value)
        return result
    if isinstance(updates, list) and isinstance(existing, list):
        result = []
        existing_by_key = {
            key: value
            for value in existing
            for key in [_restore_list_item_key(value)]
            if key
        }
        for idx, value in enumerate(updates):
            item_key = _restore_list_item_key(value)
            if item_key and item_key in existing_by_key:
                existing_value = existing_by_key[item_key]
            elif item_key:
                existing_value = None
            else:
                existing_value = existing[idx] if idx < len(existing) else None
            result.append(_restore_secret_placeholders(value, existing_value))
        return result
    parsed_updates = _try_parse_json_text(updates)
    parsed_existing = _try_parse_json_text(existing)
    if isinstance(parsed_updates, (dict, list)) and isinstance(parsed_existing, type(parsed_updates)):
        restored = _restore_secret_placeholders(parsed_updates, parsed_existing)
        return json.dumps(restored, ensure_ascii=False, indent=2)
    return updates


def _find_unresolved_secret_placeholders(data, path: str = "") -> List[str]:
    found: List[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            key_path = str(key or "")
            next_path = f"{path}.{key_path}" if path else key_path
            if _is_command_secret_key(key) and value == SECRET_PLACEHOLDER:
                found.append(next_path)
                continue
            found.extend(_find_unresolved_secret_placeholders(value, next_path))
        return found
    if isinstance(data, list):
        for index, value in enumerate(data):
            next_path = f"{path}[{index}]" if path else f"[{index}]"
            found.extend(_find_unresolved_secret_placeholders(value, next_path))
        return found

    parsed = _try_parse_json_text(data)
    if isinstance(parsed, (dict, list)):
        json_path = f"{path}<json>" if path else "<json>"
        found.extend(_find_unresolved_secret_placeholders(parsed, json_path))
    return found


def _reject_unresolved_secret_placeholders(data) -> None:
    unresolved = _find_unresolved_secret_placeholders(data)
    if not unresolved:
        return
    preview = ", ".join(unresolved[:5])
    if len(unresolved) > 5:
        preview = f"{preview}, ... (+{len(unresolved) - 5})"
    raise HTTPException(
        status_code=400,
        detail=f"unresolved_secret_placeholder: {preview}",
    )


def _raise_command_save_failed() -> None:
    raise HTTPException(status_code=500, detail="命令保存失败")


def _finish_manual_command_request(ctx, success: bool) -> None:
    try:
        request_manager.finish_request(ctx, success=success)
    except Exception as exc:
        logger.debug(f"manual command request finish failed: {exc}")


def _release_manual_command_session(pool, session, *, rollback_request_count: bool = False) -> None:
    try:
        kwargs = {"check_triggers": False}
        if rollback_request_count:
            kwargs["rollback_request_count"] = True
        pool.release(session.id, **kwargs)
    except Exception as release_error:
        logger.debug(f"manual command release failed: {release_error}")


def _coerce_tab_index(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _idle_tabs_for_manual_command(pool) -> List[dict]:
    items: List[dict] = []
    snapshot_failed = False
    if hasattr(pool, "get_sessions_snapshot"):
        try:
            sessions = pool.get_sessions_snapshot()
        except Exception:
            sessions = []
            snapshot_failed = True
        for session in sessions or []:
            if getattr(getattr(session, "status", None), "value", "") != "idle":
                continue
            tab_index = _coerce_tab_index(getattr(session, "persistent_index", None))
            if tab_index is None:
                continue
            items.append({"tab_index": tab_index, "session": session})
    if not hasattr(pool, "get_sessions_snapshot") or snapshot_failed:
        status = pool.get_status()
        for item in status.get("tabs", []) if isinstance(status, dict) else []:
            if item.get("status") != "idle":
                continue
            tab_index = _coerce_tab_index(item.get("persistent_index"))
            if tab_index is None:
                continue
            items.append({"tab_index": tab_index, "session": None})

    items.sort(key=lambda item: item["tab_index"])
    return items


def _manual_command_matches_scope(cmd, session) -> bool:
    return command_engine._matches_scope(cmd, session)


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
    _reject_unresolved_secret_placeholders(cmd_data)
    cmd = command_engine.add_command(cmd_data)
    if not cmd:
        _raise_command_save_failed()
    return {"success": True, "command": _redact_command_secrets(cmd)}


@router.put("/api/commands/reorder")
async def reorder_commands(
    body: CommandReorderRequest,
    authenticated: bool = Depends(verify_auth)
):
    success = command_engine.reorder_commands(body.command_ids)
    if not success:
        _raise_command_save_failed()
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
    if updated < 0:
        _raise_command_save_failed()
    return {"success": True, "updated": updated, "group_name": (body.group_name or "").strip()}


@router.put("/api/commands/enabled")
async def update_commands_enabled(
    body: CommandBatchEnabledRequest,
    authenticated: bool = Depends(verify_auth)
):
    updated = command_engine.set_commands_enabled(body.command_ids, body.enabled)
    if updated < 0:
        _raise_command_save_failed()
    return {"success": True, "updated": updated, "enabled": body.enabled}


@router.put("/api/command-groups/{group_name}/enabled")
async def update_command_group_enabled(
    group_name: str,
    body: CommandGroupEnabledRequest,
    authenticated: bool = Depends(verify_auth)
):
    normalized_name = (group_name or "").strip()
    updated = command_engine.set_group_enabled(normalized_name, body.enabled)
    if updated < 0:
        _raise_command_save_failed()
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
    if updated < 0:
        _raise_command_save_failed()
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
    if updated < 0:
        _raise_command_save_failed()
    return {"success": True, "updated": updated, "group_name": group_name}


@router.put("/api/commands/{command_id}")
async def update_command(
    command_id: str,
    body: CommandUpdateRequest,
    authenticated: bool = Depends(verify_auth)
):
    updates = body.model_dump(exclude_none=True)
    existing = command_engine.get_command_config(command_id)
    if not existing:
        raise HTTPException(status_code=404, detail="命令不存在")
    updates = _restore_secret_placeholders(updates, existing)
    _reject_unresolved_secret_placeholders(updates)
    cmd = command_engine.update_command(command_id, updates)
    if not cmd:
        _raise_command_save_failed()
    return {"success": True, "command": _redact_command_secrets(cmd)}


@router.delete("/api/commands/{command_id}")
async def delete_command(command_id: str, authenticated: bool = Depends(verify_auth)):
    existing = command_engine.get_command_config(command_id)
    if not existing:
        raise HTTPException(status_code=404, detail="命令不存在")
    success = command_engine.delete_command(command_id)
    if not success:
        _raise_command_save_failed()
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

        idle_tabs = _idle_tabs_for_manual_command(pool)

        if not idle_tabs:
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
                try:
                    _finish_manual_command_request(
                        target_ctx,
                        success=(target_ctx.status == RequestStatus.COMPLETED),
                    )
                finally:
                    setattr(target_session, "_command_request_id", None)
                    _release_manual_command_session(pool, target_session)

        for tab_info in idle_tabs:
            tab_index = tab_info["tab_index"]
            candidate_session = tab_info.get("session")
            ctx = None
            session = None
            try:
                # Respect command scope even in manual test mode.
                if candidate_session is not None and not _manual_command_matches_scope(cmd, candidate_session):
                    skipped_tabs.append(tab_index)
                    continue

                ctx = request_manager.create_request()
                session = pool.acquire_by_index(tab_index, ctx.request_id, timeout=3)
                if not session:
                    ctx.mark_failed("acquire_failed")
                    _finish_manual_command_request(ctx, success=False)
                    failed_tabs.append({"tab_index": tab_index, "error": "acquire_failed"})
                    continue

                if not _manual_command_matches_scope(cmd, session):
                    skipped_tabs.append(tab_index)
                    _release_manual_command_session(pool, session, rollback_request_count=True)
                    _finish_manual_command_request(ctx, success=False)
                    continue

                command_engine.submit_background_task(_run_command_in_background, session, ctx)
                scheduled_tabs.append(tab_index)
            except Exception as e:
                if ctx is not None and not ctx.is_terminal():
                    ctx.mark_failed(str(e))
                    _finish_manual_command_request(ctx, success=False)
                if session is not None and getattr(getattr(session, "status", None), "value", "") == "busy":
                    _release_manual_command_session(pool, session, rollback_request_count=True)
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

        idle_tabs = _idle_tabs_for_manual_command(pool)

        if not idle_tabs:
            raise HTTPException(status_code=409, detail="没有空闲标签页可用于执行命令组")

        skipped_tabs: List[dict] = []
        acquire_failures: List[int] = []

        for tab_info in idle_tabs:
            tab_index = tab_info["tab_index"]
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
                if int(plan.get("runnable_count", 0) or 0) <= 0:
                    skipped_tabs.append({
                        "tab_index": tab_index,
                        "runnable_count": plan.get("runnable_count", 0),
                        "scope_skipped": plan.get("scope_skipped", 0),
                    })
                    continue

                effective_acquire_policy = (
                    "inherit_session" if acquire_policy == "require_acquire" else acquire_policy
                )
                result = command_engine.execute_command_group(
                    group_name=normalized_name,
                    session=session,
                    include_disabled=include_disabled,
                    acquire_policy=effective_acquire_policy,
                    prepared_plan=plan,
                )
                if not result.get("ok") and not result.get("partial_ok"):
                    raise HTTPException(status_code=400, detail=result.get("error", "命令组执行失败"))
                result["requested_acquire_policy"] = acquire_policy
                message = f"命令组已在标签页 #{tab_index} 执行"
                if result.get("partial_ok") and not result.get("ok"):
                    message += "（部分命令被跳过或执行失败）"
                return {
                    "success": True,
                    "message": message,
                    "tab_index": tab_index,
                    **result,
                }
            finally:
                _release_manual_command_session(pool, session)

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
