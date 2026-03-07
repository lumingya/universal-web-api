"""
app/api/cmd_routes.py - 命令系统 API 路由

职责：
- 命令 CRUD 接口
- 元信息查询
- 手动触发（调试用）
"""

from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, Field

from app.core.config import AppConfig, get_logger
from app.services.command_engine import command_engine

logger = get_logger("API.CMD")

router = APIRouter(tags=["commands"])


# ================= 认证依赖 =================

async def verify_auth(authorization: Optional[str] = Header(None)) -> bool:
    if not AppConfig.is_auth_enabled():
        return True
    if not AppConfig.get_auth_token():
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
    mode: str = Field(default="simple")
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
        "priority": 2
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
    mode: Optional[str] = None
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


class CommandGroupExecuteRequest(BaseModel):
    include_disabled: bool = Field(default=False)


# ================= 路由 =================

@router.get("/api/commands")
async def list_commands(authenticated: bool = Depends(verify_auth)):
    commands = command_engine.list_commands()
    return {"commands": commands, "count": len(commands)}


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
    return cmd


@router.post("/api/commands")
async def create_command(
    body: CommandCreateRequest,
    authenticated: bool = Depends(verify_auth)
):
    cmd_data = body.model_dump()
    cmd = command_engine.add_command(cmd_data)
    return {"success": True, "command": cmd}


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
    cmd = command_engine.update_command(command_id, updates)
    if not cmd:
        raise HTTPException(status_code=404, detail="命令不存在")
    return {"success": True, "command": cmd}


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

        status = pool.get_status()
        idle_tabs = [t for t in status.get("tabs", []) if t.get("status") == "idle"]
        idle_tabs.sort(key=lambda t: t.get("persistent_index", 0))

        if not idle_tabs:
            raise HTTPException(status_code=409, detail="no_idle_tabs")

        executed_tabs: List[int] = []
        skipped_tabs: List[int] = []
        failed_tabs: List[dict] = []

        for tab_info in idle_tabs:
            tab_index = tab_info.get("persistent_index")
            if tab_index is None:
                continue

            session = pool.acquire_by_index(tab_index, f"cmd_test_{command_id}_{tab_index}", timeout=3)
            if not session:
                failed_tabs.append({"tab_index": tab_index, "error": "acquire_failed"})
                continue

            try:
                # Respect command scope even in manual test mode.
                if not command_engine._matches_scope(cmd, session):
                    skipped_tabs.append(tab_index)
                    continue

                command_engine._execute_command(cmd, session)
                executed_tabs.append(tab_index)
            except Exception as e:
                failed_tabs.append({"tab_index": tab_index, "error": str(e)})
            finally:
                pool.release(session.id, check_triggers=False)

        if not executed_tabs:
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
            "message": f"command executed on tabs: {executed_tabs}",
            "executed_tabs": executed_tabs,
            "skipped_tabs": skipped_tabs,
            "failed_tabs": failed_tabs,
            "executed_count": len(executed_tabs),
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

    try:
        from app.core.browser import get_browser
        browser = get_browser(auto_connect=False)
        pool = browser.tab_pool

        status = pool.get_status()
        idle_tabs = [t for t in status.get("tabs", []) if t["status"] == "idle"]

        if not idle_tabs:
            raise HTTPException(status_code=409, detail="没有空闲标签页可用于执行命令组")

        tab_index = idle_tabs[0]["persistent_index"]
        session = pool.acquire_by_index(tab_index, f"group_test_{normalized_name}", timeout=5)

        if not session:
            raise HTTPException(status_code=409, detail="获取标签页失败")

        try:
            result = command_engine.execute_command_group(
                group_name=normalized_name,
                session=session,
                include_disabled=include_disabled,
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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"执行命令组失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
