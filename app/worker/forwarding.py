"""R2-6：process 模式下 API 进程把 /api/* 面板请求整体转发给 worker（纯 ASGI，流式透传响应）。

worker 看到的连接都来自本机回环地址，为了不让外部请求被当成“本机直连”而绕过按来源判断的限制，
转发时带上 UWAPI_PROXY_SECRET 对应的密钥头与 X-Forwarded-For：http_security 只在“回环来源 + 密钥匹配”时
才采信转发头，worker 因而看到真实的客户端地址。
"""

from __future__ import annotations

import os

from app.worker import URL_ENV, is_api_process

PROXY_SECRET_ENV = "UWAPI_PROXY_SECRET"
PROXY_SECRET_HEADER = b"x-uwa-proxy-secret"
_HOP_BY_HOP = {b"host", b"content-length", b"connection", b"keep-alive", b"transfer-encoding", b"upgrade",
               b"x-forwarded-for", PROXY_SECRET_HEADER}
_RESPONSE_SKIP = {"content-length", "transfer-encoding", "connection", "keep-alive"}


class WorkerForwardingMiddleware:
    def __init__(self, app):
        self.app = app
        self._client = None

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith("/api/") or not is_api_process():
            await self.app(scope, receive, send)
            return
        base = str(os.getenv(URL_ENV, "") or "").rstrip("/")
        if not base:
            await self._reply(send, 503, b'{"detail": "worker \\u672a\\u5c31\\u7eea"}')
            return
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if not message.get("more_body"):
                break
        headers = [(k, v) for k, v in scope.get("headers") or [] if k.lower() not in _HOP_BY_HOP]
        client_host = (scope.get("client") or ("", 0))[0]
        if client_host:
            headers.append((b"x-forwarded-for", client_host.encode("latin-1")))
        secret = str(os.getenv(PROXY_SECRET_ENV, "") or "")
        if secret:
            headers.append((PROXY_SECRET_HEADER, secret.encode("latin-1")))
        query = scope.get("query_string", b"").decode("latin-1")
        url = base + scope["path"] + (f"?{query}" if query else "")
        try:
            request = self._client.build_request(scope.get("method", "GET"), url, headers=headers, content=bytes(body))
            response = await self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            await self._reply(send, 503, f'{{"detail": "worker unavailable: {type(exc).__name__}"}}'.encode())
            return
        try:
            await send({
                "type": "http.response.start",
                "status": response.status_code,
                "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in response.headers.items()
                            if k.lower() not in _RESPONSE_SKIP],
            })
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await response.aclose()

    @staticmethod
    async def _reply(send, status: int, body: bytes) -> None:
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})
