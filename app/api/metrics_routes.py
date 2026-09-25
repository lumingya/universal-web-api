"""R2-7：Prometheus 指标接口。

与 /health 的详细信息同样的访问规则（S7）：严格本机直连，或携带有效的 AUTH_TOKEN / DASHBOARD_AUTH_TOKEN；
其余请求返回 403，避免对外暴露路由与负载信息。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.services.metrics import render_prometheus
from app.utils.diagnostics_access import request_is_privileged

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request):
    if not request_is_privileged(request):
        raise HTTPException(status_code=403, detail="metrics 仅限本机直连或携带有效令牌访问")
    return PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4; charset=utf-8")
