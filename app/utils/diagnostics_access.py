"""S7：公开健康/引导接口的「详细信息」访问判定。

设计取舍（用户要求：不能把所有接口都改成必须带令牌，否则大量第三方工具会坏）：

- ``/health`` 永远可匿名访问，匿名远程调用者只拿到最小响应（服务存活、浏览器是否已连接、
  是否需要登录），HTTP 状态码语义不变（浏览器未连接 → 503），监控/探活脚本照常可用；
- 详细诊断（版本、端口、标签池、请求队列、站点数量……）以及会主动连接浏览器的
  ``browser.health_check()`` 只对「特权调用者」开放；
- 特权调用者 = 严格本机直连（回环地址且无任何反代/隧道转发头，同 S4/S6）
  或携带有效的服务令牌 ``AUTH_TOKEN`` / 面板令牌 ``DASHBOARD_AUTH_TOKEN``
  （``Authorization: Bearer`` 或 ``X-API-Key``）。
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from app.core.http_security import is_trusted_local_request
from app.utils.media_access import _candidates, _token_matches


def is_privileged_diagnostics_request(
    client_host: Optional[str],
    headers: Any,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    env = os.environ if env is None else env
    candidates = _candidates(headers)
    if candidates and (
        _token_matches(env.get("AUTH_TOKEN", ""), candidates)
        or _token_matches(env.get("DASHBOARD_AUTH_TOKEN", ""), candidates)
    ):
        return True
    return is_trusted_local_request(client_host, headers, env)


def request_is_privileged(request: Any, env: Optional[Mapping[str, str]] = None) -> bool:
    client = getattr(request, "client", None)
    client_host = getattr(client, "host", None) if client else None
    return is_privileged_diagnostics_request(client_host, getattr(request, "headers", None), env)
