"""OpenAI 兼容路由分组（由 app/api/tab_routes.py 拆出）。

- ``shared.py``：公共解析/配置/SSE 辅助与共享的 ``router`` 实例
- ``tab.py``：``/tab/{tab_index}/v1/*``
- ``url.py``：``/url/{route_domain}[/{preset_name}]/v1/*`` 与按模型名路由
- ``group.py``：``/group/{group_id}[/{preset_name}]/v1/*``
- ``tab_url.py``：``/tab-url/{url_token}[/{preset_name}]/v1/*``

所有模块共享 ``shared.router``，URL 与注册顺序保持不变。
"""

from app.api.openai import (
    group,
    shared,
    tab,
    tab_url,
    url,
)

router = shared.router
