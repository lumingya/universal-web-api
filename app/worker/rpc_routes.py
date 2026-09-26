"""R2-6：worker 进程提供给 API 进程的内部接口（只在 UWAPI_WORKER_ROLE=worker 时注册）。"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import threading
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from app.core.config import get_logger
from app.worker import TOKEN_ENV, TOKEN_HEADER
from app.worker.protocol import (
    BROWSER_METHODS,
    EXECUTE_KWARGS,
    EXECUTE_METHODS,
    HEARTBEAT_SEC,
    TAB_POOL_ATTRS,
    TAB_POOL_METHODS,
)

logger = get_logger("WORKER")
router = APIRouter()
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _authorize(request: Request) -> None:
    expected = str(os.getenv(TOKEN_ENV, "") or "")
    supplied = str(request.headers.get(TOKEN_HEADER, "") or "")
    client = getattr(request.client, "host", "") if request.client else ""
    if not expected or not hmac.compare_digest(expected, supplied) or client not in _LOOPBACK:
        raise HTTPException(status_code=403, detail="worker 内部接口仅限本机 API 进程访问")


def _browser():
    from app.core.browser import get_browser

    return get_browser(auto_connect=False)


@router.get("/internal/worker/health")
async def worker_health() -> Dict[str, Any]:
    return {"ok": True, "role": "worker", "pid": os.getpid()}


@router.post("/internal/worker/execute")
async def worker_execute(request: Request):
    _authorize(request)
    body = await request.json()
    method = str(body.get("method") or "")
    kwargs = body.get("kwargs") or {}
    if method not in EXECUTE_METHODS or not isinstance(kwargs, dict) or set(kwargs) - EXECUTE_KWARGS:
        raise HTTPException(status_code=400, detail="不支持的执行方法或参数")

    stop = threading.Event()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    target = getattr(_browser(), method)

    def pump() -> None:
        generator = None
        try:
            generator = target(**kwargs, stop_checker=stop.is_set)
            for chunk in generator:
                loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
                if stop.is_set():
                    break
        except Exception as exc:  # broad-except: 执行错误原样作为 error 行传回 API 进程
            logger.warning(f"worker 执行失败: {exc}")
            loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))
        finally:
            if generator is not None:
                try:
                    generator.close()  # 在迭代它的同一线程里关闭，触发工作流的清理
                except Exception:  # broad-except: 收尾操作
                    pass
            loop.call_soon_threadsafe(queue.put_nowait, ("end", None))

    async def stream():
        thread = threading.Thread(target=pump, name=f"worker-exec-{kwargs.get('task_id') or ''}", daemon=True)
        thread.start()
        try:
            while True:
                try:
                    kind, value = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SEC)
                except asyncio.TimeoutError:
                    yield b'{"hb": 1}\n'
                    continue
                if kind == "end":
                    break
                line = {"chunk": value} if kind == "chunk" else {"error": value}
                yield (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")
        finally:
            stop.set()  # 正常结束或 API 进程断开连接：都通知工作流停止

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@router.post("/internal/worker/tab_pool")
async def worker_tab_pool(request: Request):
    _authorize(request)
    body = await request.json()
    method = str(body.get("method") or "")
    if method not in TAB_POOL_METHODS:
        raise HTTPException(status_code=400, detail=f"不允许远程调用的标签页池方法: {method}")
    pool = _browser().tab_pool
    result = await asyncio.to_thread(getattr(pool, method), *(body.get("args") or []), **(body.get("kwargs") or {}))
    return {"result": jsonable_encoder(result)}


@router.get("/internal/worker/tab_pool_attrs")
async def worker_tab_pool_attrs(request: Request):
    _authorize(request)
    pool = _browser().tab_pool
    attrs = {name: getattr(pool, name, None) for name in TAB_POOL_ATTRS if name != "route_groups"}
    getter = getattr(pool, "get_route_groups_snapshot", None)
    attrs["route_groups"] = getter() if callable(getter) else getattr(pool, "route_groups", None)
    return jsonable_encoder(attrs)


@router.post("/internal/worker/browser")
async def worker_browser(request: Request):
    _authorize(request)
    body = await request.json()
    method = str(body.get("method") or "")
    if method not in BROWSER_METHODS:
        raise HTTPException(status_code=400, detail=f"不允许远程调用的浏览器方法: {method}")
    result = await asyncio.to_thread(getattr(_browser(), method))
    return {"result": jsonable_encoder(result)}
