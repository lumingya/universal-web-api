"""面板管理类 API（admin 层）。

把「控制面板用」的端点从协议/编排路由里分离出来：这些接口的鉴权模型、
调用方（控制面板）与生命周期都和 ``/v1/*`` 完全不同。

当前包含：

- ``pool.py``:    ``/api/tab-pool/*`` 与 ``/api/pool/status``
- ``presets.py``: ``/api/presets/*``

约定：URL 保持不变（客户端与前端已按这些路径写死），只调整代码归属。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.admin import pool, presets

router = APIRouter()
router.include_router(pool.router)
router.include_router(presets.router)

__all__ = ["router"]
