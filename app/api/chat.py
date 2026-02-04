"""
app/api/chat.py - 核心聊天 API

职责：
- OpenAI 兼容的 /v1/chat/completions 接口
- 流式/非流式响应处理
- 模型列表
"""

import json
import os
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

logger = get_logger("API.CHAT")

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


# ================= 核心聊天 API =================

@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    body: ChatRequest,
    authenticated: bool = Depends(verify_auth)
):
    """
    OpenAI 兼容的聊天补全接口
    """
    ctx = request_manager.create_request()
    with logger.context(ctx.request_id):
        logger.info("开始")
        
        # 图片输入校验
        try:
            has_image_declared = False
            has_any_valid_image = False

            for m in body.messages or []:
                content = m.get("content")

                if isinstance(content, str):
                    s = content.strip()
                    if "image_url" in s:
                        has_image_declared = True
                    if "data:image" in s and "base64," in s and not s.endswith("base64,"):
                        has_any_valid_image = True
                    continue

                if isinstance(content, list):
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        if item.get("type") != "image_url":
                            continue

                        has_image_declared = True
                        image_url = item.get("image_url") or {}
                        url = image_url.get("url") if isinstance(image_url, dict) else str(image_url)

                        if isinstance(url, str):
                            u = url.strip()
                            if u.startswith("data:image") and "base64," in u and not u.endswith("base64,"):
                                has_any_valid_image = True
                            elif u.startswith("http://") or u.startswith("https://"):
                                has_any_valid_image = True
                            elif os.path.exists(u):
                                has_any_valid_image = True

            if has_image_declared and not has_any_valid_image:
                raise HTTPException(
                    status_code=400,
                    detail="检测到图片输入，但未收到任何可用图片数据。"
                           "上游发送的是空的 data:image/...;base64, 前缀（或缺失图片 URL/base64）。"
                           "请让上游客户端透传完整 base64 或可访问的图片 URL。"
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"图片输入校验异常（已放行）: {e}")

        # 调试日志
        try:
            raw = json.dumps(body.messages, ensure_ascii=False)
            logger.debug(f"messages_preview={raw[:3000]}")
        except Exception as e:
            logger.debug(f"messages_preview_failed: {e}")

        if body.stream:
            return StreamingResponse(
                _stream_with_lifecycle(request, body, ctx),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        else:
            return await _non_stream_with_lifecycle(request, body, ctx)


async def _stream_with_lifecycle(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext
):
    """流式响应 + 完整生命周期管理"""
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
            chunk_counter = 0
            try:
                gen = browser.execute_workflow(
                    body.messages, 
                    stream=True,
                    task_id=ctx.request_id
                )

                for chunk in gen:
                    chunk_counter += 1
                    
                    # 🔍 探针：记录 chunk 内容摘要
                    chunk_preview = chunk[:100] if isinstance(chunk, str) else str(chunk)[:100]
                    has_images = '"images"' in chunk if isinstance(chunk, str) else False
                    logger.debug(f"[WORKER] Chunk #{chunk_counter}: has_images={has_images}, preview={chunk_preview}")
                    
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
                    except Exception as e:
                        logger.debug(f"关闭生成器异常: {e}")
        
                chunk_queue.put(None)
                logger.debug("工作线程结束")

        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()

        while True:
            if await request.is_disconnected():
                logger.debug("客户端断开")
                ctx.request_cancel("client_disconnected")
                break

            try:
                chunk = await asyncio.to_thread(chunk_queue.get, timeout=0.5)
            except queue.Empty:
                continue

            if chunk is None:
                logger.debug("收到结束标记")
                break

            if isinstance(chunk, tuple) and chunk[0] == "ERROR":
                logger.error(f"错误: {chunk[1]}")
                ctx.mark_failed(chunk[1])
                yield _pack_error(f"执行错误: {chunk[1]}", "internal_error")
                break

            # 🔍 探针：记录发送给客户端的 chunk
            has_images = '"images"' in chunk if isinstance(chunk, str) else False
            if has_images:
                logger.info(f"[SEND] 发送包含图片的 chunk 给客户端")
            
            yield chunk
            await asyncio.sleep(0)

        if not ctx.should_stop() and ctx.status == RequestStatus.RUNNING:
            ctx.mark_completed()

    except asyncio.CancelledError:
        logger.debug("协程取消")
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


async def _non_stream_with_lifecycle(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext
) -> JSONResponse:
    """非流式响应 + 生命周期管理"""
    collected_content = []
    collected_images = []
    error_data = None

    async for chunk in _stream_with_lifecycle(request, body, ctx):
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
                        
                        images = delta.get("images", [])
                        if images:
                            collected_images.extend(images)
                            
                except json.JSONDecodeError:
                    continue

    if error_data:
        return JSONResponse(content=error_data, status_code=500)

    full_content = "".join(collected_content)
    
    # 将图片 base64 转为 Markdown 格式嵌入 content
    if collected_images:
        for idx, img_b64 in enumerate(collected_images):
            if img_b64.startswith("data:"):
                full_content += f"\n![image_{idx}]({img_b64})"
            else:
                full_content += f"\n![image_{idx}](data:image/png;base64,{img_b64})"
    
    message = {
        "role": "assistant",
        "content": full_content
    }
    
    # 🛡️ 兼容：保留 images 字段供特殊客户端使用
    if collected_images:
        message["images"] = collected_images
    
    response = {
        "id": f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }
    }

    return JSONResponse(content=response)


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


def _pack_done() -> str:
    """打包 SSE 结束标记"""
    return "data: [DONE]\n\n"


# ================= 模型列表 =================

@router.get("/v1/models")
async def list_models(authenticated: bool = Depends(verify_auth)):
    """列出可用模型"""
    return {
        "object": "list",
        "data": [
            {
                "id": "web-browser",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "universal-web-api"
            }
        ]
    }


@router.get("/api/pool/status")
async def get_pool_status(authenticated: bool = Depends(verify_auth)):
    """获取标签页池状态"""
    try:
        browser = get_browser(auto_connect=False)
        return browser.get_pool_status()
    except Exception as e:
        return {"error": str(e), "initialized": False}