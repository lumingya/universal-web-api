"""
app/api/tab_routes.py - 标签页路由

职责：
- /api/tab-pool/tabs - 获取标签页列表
- /tab/{index}/v1/chat/completions - 指定标签页的聊天接口
"""

import json
import time
import asyncio
import queue
import threading
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Header, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from app.core.config import AppConfig, get_logger
from app.core import get_browser
from app.services.request_manager import (
    request_manager,
    RequestContext,
    RequestStatus,
    watch_client_disconnect
)

logger = get_logger("API.TAB")

router = APIRouter()


# ================= 请求模型 =================

class ChatRequest(BaseModel):
    """聊天请求模型"""
    model: str = Field(default="gpt-3.5-turbo")
    messages: list = Field(...)
    stream: Optional[bool] = Field(default=False)
    temperature: Optional[float] = Field(default=0.7, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=1)


# ================= 认证依赖 =================

async def verify_auth(authorization: Optional[str] = Header(None)) -> bool:
    """验证 Bearer Token"""
    if not AppConfig.is_auth_enabled():
        return True

    if not AppConfig.AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="服务配置错误")

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = authorization.replace("Bearer ", "").strip()

    if token != AppConfig.get_auth_token():
        raise HTTPException(
            status_code=401,
            detail="认证令牌无效",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return True


# ================= 标签页池 API =================

@router.get("/api/tab-pool/tabs")
async def get_tab_pool_tabs(authenticated: bool = Depends(verify_auth)):
    """
    获取所有标签页及其持久编号和预设信息
    
    返回格式：
    {
        "tabs": [
            {
                "persistent_index": 1,
                "id": "gpt_1",
                "url": "https://chatgpt.com/",
                "status": "idle",
                "route_prefix": "/tab/1",
                "preset_name": null,
                "available_presets": ["主预设", "无临时聊天"]
            },
            ...
        ],
        "count": 3
    }
    """
    try:
        browser = get_browser(auto_connect=False)
        tabs = browser.tab_pool.get_tabs_with_index()
        
        # 🆕 为每个标签页附加可用预设列表
        try:
            from app.services.config_engine import config_engine
            for tab_info in tabs:
                domain = tab_info.get("current_domain", "")
                if domain:
                    tab_info["available_presets"] = config_engine.list_presets(domain)
                else:
                    tab_info["available_presets"] = []
        except Exception as e:
            logger.debug(f"获取预设列表失败: {e}")
            for tab_info in tabs:
                tab_info["available_presets"] = []
        
        return {
            "tabs": tabs,
            "count": len(tabs)
        }
    except Exception as e:
        logger.error(f"获取标签页列表失败: {e}")
        return {"tabs": [], "count": 0, "error": str(e)}


# ================= 指定标签页的聊天 API =================

@router.post("/tab/{tab_index}/v1/chat/completions")
async def chat_with_tab(
    tab_index: int,
    request: Request,
    body: ChatRequest,
    authenticated: bool = Depends(verify_auth)
):
    """
    使用指定编号的标签页进行聊天
    
    路径参数：
    - tab_index: 持久化标签页编号（1, 2, 3...）
    """
    if tab_index < 1:
        raise HTTPException(status_code=400, detail="标签页编号必须大于 0")
    
    ctx = request_manager.create_request()
    with logger.context(ctx.request_id):
        logger.info(f"开始 (标签页 #{tab_index})")
        
        if body.stream:
            return StreamingResponse(
                _stream_with_tab_index(request, body, ctx, tab_index),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        else:
            return await _non_stream_with_tab_index(request, body, ctx, tab_index)


async def _stream_with_tab_index(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    tab_index: int
):
    """使用指定标签页的流式响应"""
    disconnect_task = None
    worker_thread = None

    try:
        disconnect_task = asyncio.create_task(
            watch_client_disconnect(request, ctx, check_interval=0.3)
        )

        browser = get_browser(auto_connect=False)
        browser.set_stop_checker(ctx.should_stop)

        request_manager.start_request(ctx)

        chunk_queue: queue.Queue = queue.Queue(maxsize=100)

        def worker():
            gen = None
            try:
                # 🔑 使用指定标签页
                gen = browser.execute_workflow_for_tab_index(
                    tab_index,
                    body.messages,
                    stream=True,
                    task_id=ctx.request_id
                )

                for chunk in gen:
                    if ctx.should_stop():
                        logger.info("工作线程检测到取消")
                        break
                    chunk_queue.put(chunk)

            except Exception as e:
                logger.error(f"工作线程异常: {e}")
                chunk_queue.put(("ERROR", str(e)))
            finally:
                if gen is not None:
                    try:
                        gen.close()
                    except Exception:
                        pass
                chunk_queue.put(None)

        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()

        while True:
            if await request.is_disconnected():
                ctx.request_cancel("client_disconnected")
                break

            try:
                chunk = await asyncio.to_thread(chunk_queue.get, timeout=0.5)
            except queue.Empty:
                continue

            if chunk is None:
                break

            if isinstance(chunk, tuple) and chunk[0] == "ERROR":
                ctx.mark_failed(chunk[1])
                yield _pack_error(f"执行错误: {chunk[1]}", "internal_error")
                break

            yield chunk
            await asyncio.sleep(0)

        if not ctx.should_stop() and ctx.status == RequestStatus.RUNNING:
            ctx.mark_completed()

    except asyncio.CancelledError:
        ctx.request_cancel("coroutine_cancelled")
        raise

    except Exception as e:
        logger.error(f"异常: {e}")
        ctx.mark_failed(str(e))
        yield _pack_error(f"执行错误: {str(e)}", "internal_error")

    finally:
        if worker_thread and worker_thread.is_alive():
            ctx.request_cancel("cleanup")
            worker_thread.join(timeout=2.0)

        try:
            while not chunk_queue.empty():
                chunk_queue.get_nowait()
        except:
            pass

        if disconnect_task:
            disconnect_task.cancel()
            try:
                await disconnect_task
            except asyncio.CancelledError:
                pass

        request_manager.finish_request(ctx, success=(ctx.status == RequestStatus.COMPLETED))


async def _non_stream_with_tab_index(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    tab_index: int
) -> JSONResponse:
    """使用指定标签页的非流式响应"""
    collected_content = []
    error_data = None

    async for chunk in _stream_with_tab_index(request, body, ctx, tab_index):
        if isinstance(chunk, str):
            if chunk.startswith("data: [DONE]"):
                continue

            if chunk.startswith("data: "):
                try:
                    data_str = chunk[6:].strip()
                    if not data_str:
                        continue
                    data = json.loads(data_str)

                    if "error" in data:
                        error_data = data
                        break

                    if "choices" in data and data["choices"]:
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            collected_content.append(content)

                except json.JSONDecodeError:
                    continue

    if error_data:
        return JSONResponse(content=error_data, status_code=500)

    full_content = "".join(collected_content)
    response = {
        "id": f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": full_content},
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    }

    return JSONResponse(content=response)


# ================= 预设管理 API =================

class PresetRequest(BaseModel):
    """预设操作请求"""
    preset_name: str = Field(..., min_length=1, max_length=50)


class CreatePresetRequest(BaseModel):
    """创建预设请求"""
    new_name: str = Field(..., min_length=1, max_length=50)
    source_name: Optional[str] = Field(default=None)


class RenamePresetRequest(BaseModel):
    """重命名预设请求"""
    old_name: str = Field(..., min_length=1, max_length=50)
    new_name: str = Field(..., min_length=1, max_length=50)


class TerminateTabRequest(BaseModel):
    """终止标签页当前任务请求"""
    reason: str = Field(default="manual_terminate_from_tab_pool", max_length=120)
    clear_page: bool = Field(default=True)


@router.put("/api/tab-pool/tabs/{tab_index}/preset")
async def set_tab_preset(
    tab_index: int,
    body: PresetRequest,
    authenticated: bool = Depends(verify_auth)
):
    """为指定标签页设置预设"""
    try:
        browser = get_browser(auto_connect=False)
        
        # 空字符串或 "主预设" 都视为恢复默认
        preset_value = body.preset_name if body.preset_name != "主预设" else None
        
        success = browser.tab_pool.set_tab_preset(tab_index, preset_value)
        
        if success:
            return {"success": True, "message": f"标签页 #{tab_index} 已切换到预设: {body.preset_name}"}
        else:
            raise HTTPException(status_code=404, detail=f"标签页 #{tab_index} 不存在")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置标签页预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tab-pool/tabs/{tab_index}/terminate")
async def terminate_tab_task(
    tab_index: int,
    body: TerminateTabRequest,
    authenticated: bool = Depends(verify_auth)
):
    """按标签页编号终止当前任务并释放占用。"""
    if tab_index < 1:
        raise HTTPException(status_code=400, detail="标签页编号必须大于 0")

    try:
        browser = get_browser(auto_connect=False)
        result = browser.tab_pool.terminate_by_index(
            tab_index,
            reason=(body.reason or "manual_terminate_from_tab_pool"),
            clear_page=bool(body.clear_page),
        )
        if not result.get("ok"):
            if result.get("error") == "tab_not_found":
                raise HTTPException(status_code=404, detail=f"标签页 #{tab_index} 不存在")
            raise HTTPException(status_code=400, detail=result.get("error", "terminate_failed"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"终止标签页任务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/presets/{domain}")
async def get_site_presets(
    domain: str,
    authenticated: bool = Depends(verify_auth)
):
    """获取指定站点的所有预设"""
    try:
        from app.services.config_engine import config_engine
        presets = config_engine.list_presets(domain)
        return {"domain": domain, "presets": presets}
    except Exception as e:
        logger.error(f"获取预设列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/presets/{domain}")
async def create_site_preset(
    domain: str,
    body: CreatePresetRequest,
    authenticated: bool = Depends(verify_auth)
):
    """为站点创建新预设（克隆自现有预设）"""
    try:
        from app.services.config_engine import config_engine
        success = config_engine.create_preset(domain, body.new_name, body.source_name)
        
        if success:
            return {"success": True, "message": f"预设 '{body.new_name}' 已创建"}
        else:
            raise HTTPException(status_code=400, detail="创建失败（预设已存在或站点不存在）")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/presets/{domain}/rename")
async def rename_site_preset(
    domain: str,
    body: RenamePresetRequest,
    authenticated: bool = Depends(verify_auth)
):
    """重命名指定预设"""
    try:
        from app.services.config_engine import config_engine
        success = config_engine.rename_preset(domain, body.old_name, body.new_name)

        if success:
            return {
                "success": True,
                "message": f"预设 '{body.old_name}' 已重命名为 '{body.new_name}'",
            }
        else:
            raise HTTPException(status_code=400, detail="重命名失败（预设不存在或新名称已存在）")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重命名预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/presets/{domain}/{preset_name}")
async def delete_site_preset(
    domain: str,
    preset_name: str,
    authenticated: bool = Depends(verify_auth)
):
    """删除指定预设（不能删除最后一个）"""
    try:
        from app.services.config_engine import config_engine
        success = config_engine.delete_preset(domain, preset_name)
        
        if success:
            return {"success": True, "message": f"预设 '{preset_name}' 已删除"}
        else:
            raise HTTPException(status_code=400, detail="删除失败（预设不存在或是最后一个）")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _pack_error(message: str, code: str = "error") -> str:
    """打包 SSE 错误"""
    data = {
        "error": {
            "message": message,
            "type": "execution_error",
            "code": code
        }
    }
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
