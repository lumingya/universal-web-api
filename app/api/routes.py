"""
app/api/routes.py - API 路由聚合入口

职责：
- 聚合所有子路由模块
- 保持向后兼容（外部仍使用 from app.api.routes import router）
"""

from fastapi import APIRouter

# 导入子路由
from app.api.chat import router as chat_router
from app.api.anthropic_routes import router as anthropic_router
from app.api.config_routes import router as config_router
from app.api.system import router as system_router
from app.api.tab_routes import router as tab_router
from app.api.cmd_routes import router as cmd_router  # 🆕 命令系统

# 创建主路由器
router = APIRouter()

# 聚合所有子路由
router.include_router(chat_router)
router.include_router(anthropic_router)
router.include_router(config_router)
router.include_router(system_router)
router.include_router(tab_router)
router.include_router(cmd_router)  # 🆕
