"""
main.py - FastAPI 应用入口
"""
import asyncio
import logging
logging.getLogger("urllib3").setLevel(logging.WARNING)
import os
import logging
import socket
import sys
import threading
import time
import webbrowser
from urllib.request import urlopen
print(f"[DEBUG] Python: {sys.executable}")
from pathlib import Path
from contextlib import asynccontextmanager
from app.core import get_browser
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
# ================= 导入配置 =================

from app.core.config import AppConfig, get_logger

# ================= 日志配置 =================

# 保留 basicConfig 用于 uvicorn 等第三方库
logging.basicConfig(
    level=getattr(logging, AppConfig.get_log_level()),
    format='%(message)s',
    datefmt='%H:%M:%S'
)

# 使用统一的 SecureLogger
logger = get_logger("MAIN")


def _setup_windows_event_loop_policy():
    """
    在 Windows 上优先使用 SelectorEventLoop，规避 Proactor 在连接断开时
    偶发抛出的 _ProactorBasePipeTransport/_call_connection_lost 噪音栈。
    """
    if not sys.platform.startswith("win"):
        return
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception as e:
        logger.debug(f"设置 WindowsSelectorEventLoopPolicy 失败（忽略）: {e}")


def _install_asyncio_exception_filter():
    """过滤已知且无害的 Windows 连接断开噪音异常。"""
    try:
        loop = asyncio.get_running_loop()
    except Exception:
        return

    default_handler = loop.get_exception_handler()

    def _handler(l, context):
        exc = context.get("exception")
        message = str(context.get("message", "") or "")
        callback = str(context.get("handle", "") or "")
        is_known_reset = isinstance(exc, ConnectionResetError)
        is_proactor_noise = "_ProactorBasePipeTransport._call_connection_lost" in callback or "proactor_events" in message

        if is_known_reset and is_proactor_noise:
            logger.debug(f"[asyncio] 忽略已知连接断开噪音: {exc}")
            return
        if is_known_reset and isinstance(exc, OSError) and getattr(exc, "errno", None) in (10054, 10053):
            logger.debug(f"[asyncio] 忽略连接重置噪音: {exc}")
            return

        if default_handler is not None:
            default_handler(l, context)
        else:
            l.default_exception_handler(context)

    loop.set_exception_handler(_handler)


_setup_windows_event_loop_policy()


# ================= Lifespan =================

def _open_dashboard_non_blocking(dashboard_url: str, initial_delay_sec: float = 1.2):
    """Use the system browser for the local dashboard, not the controlled browser."""
    def _worker():
        try:
            time.sleep(max(0.0, float(initial_delay_sec)))

            # 等待本地 HTTP 服务可达，避免 lifespan 内部自访问卡住。
            ready = False
            for _ in range(12):
                try:
                    with urlopen(dashboard_url, timeout=1.2) as resp:
                        status_code = int(getattr(resp, "status", 200) or 200)
                        if 200 <= status_code < 500:
                            ready = True
                            break
                except Exception:
                    time.sleep(0.5)

            if not ready:
                logger.warning(f"[startup] 控制面板未就绪，跳过自动打开: {dashboard_url}")
                return

            webbrowser.open_new_tab(dashboard_url)
            logger.info(f"[startup] 控制面板已在系统浏览器打开: {dashboard_url}")
        except Exception as e:
            logger.warning(f"[startup] 打开控制面板失败: {e}")

    threading.Thread(
        target=_worker,
        daemon=True,
        name="open-tutorial-non-blocking",
    ).start()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    _install_asyncio_exception_filter()
    logger.info("=" * 60)
    logger.info("Universal Web-to-API 服务启动中...")       
    # 启动时清理临时文件目录
    try:
        from app.utils.file_paste import cleanup_temp_dir
        cleanup_temp_dir()
    except Exception as e:
        logger.debug(f"临时目录清理跳过: {e}")
    logger.info(f"监听地址: http://{AppConfig.get_host()}:{AppConfig.get_port()}")
    logger.info(f"调试模式: {AppConfig.is_debug()}")
    logger.info(f"浏览器端口: {AppConfig.get_browser_port()}")
    logger.info("=" * 60)

    # main.py 中 lifespan 函数的浏览器检查部分

    try:
        
        browser = get_browser(auto_connect=False)
        health = browser.health_check()
    
        if health["connected"]:
            # 检查是否需要打开控制面板（仅当浏览器刚启动、没有实际内容时）
            should_open_dashboard = False
            
            try:
                tab_ids = browser.page.get_tabs()
                
                if len(tab_ids) == 0:
                    # 没有任何标签页
                    should_open_dashboard = True
                elif len(tab_ids) == 1:
                    # 只有一个标签页，检查是否是空白页
                    url = browser.page.url or ""
                    blank_patterns = ("about:blank", "chrome://newtab/", "chrome://new-tab-page/", "")
                    if url in blank_patterns:
                        should_open_dashboard = True
                # 多个标签页 = 用户已在使用，不打开控制面板
                
            except Exception as e:
                logger.debug(f"检查标签页状态失败: {e}")
            
            if should_open_dashboard:
                try:
                    dashboard_url = f"http://{AppConfig.get_host()}:{AppConfig.get_port()}/static/tutorial.html"
                    logger.info(f"[startup] 首次启动，使用系统浏览器打开教程页: {dashboard_url}")
                    _open_dashboard_non_blocking(dashboard_url, initial_delay_sec=1.2)
                except Exception as e:
                    logger.warning(f"⚠️ 无法打开控制面板: {e}")
            else:
                # 显示已连接状态
                pool_info = health.get("tab_pool", {})
                tab_count = pool_info.get('total', 0) if pool_info else 0
                logger.info(f"✅ 浏览器已连接 (检测到 {len(tab_ids) if 'tab_ids' in dir() else '?'} 个现有页面，跳过教程)")
        else:
            logger.warning(f"⚠️ 浏览器未连接: {health.get('error', '未知')}")
        
    except Exception as e:
        logger.warning(f"⚠️ 浏览器检查跳过: {e}")

    # 显式预热命令调度器，避免依赖控制面板接口后才初始化。
    try:
        from app.services.command_engine import command_engine
        command_engine.ensure_scheduler_running()
        logger.info(
            f"[startup] 命令调度器: "
            f"{'running' if command_engine.is_scheduler_running() else 'stopped'}"
        )
    except Exception as e:
        logger.warning(f"[startup] 命令调度器初始化失败: {e}")

    logger.info("")
    logger.info("🚀 服务已就绪！")
    logger.info(f"   Dashboard: http://{AppConfig.get_host()}:{AppConfig.get_port()}/")
    logger.info(f"   健康检查: http://{AppConfig.get_host()}:{AppConfig.get_port()}/health")
    logger.info("")

    yield

    logger.info("服务正在关闭...")
    try:
        browser = get_browser(auto_connect=False)
        browser.close()
    except Exception as e:
        logger.debug(f"关闭浏览器: {e}")

    logger.info("👋 服务已停止")


# ================= FastAPI 应用 =================

app = FastAPI(
    title="Universal Web-to-API",
    description="将任意 AI Web 界面转换为 OpenAI 兼容 API",
    version="2.5.8",
    docs_url="/docs" if AppConfig.DEBUG else None,
    redoc_url="/redoc" if AppConfig.DEBUG else None,
    lifespan=lifespan
)

# CORS 配置
if AppConfig.is_cors_enabled():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=AppConfig.get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def disable_dashboard_cache(request, call_next):
    response = await call_next(request)
    path = request.url.path or ""
    if path in ("/", "/dashboard") or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ================= Dashboard 路由（优先级最高）=================

@app.get("/", include_in_schema=False)
async def root():
    """首页 - Dashboard"""
    if Path("static/index.html").exists():
        return FileResponse("static/index.html")
    # 如果没有 Dashboard，返回 API 信息
    return JSONResponse({
        "service": "Universal Web-to-API",
        "version": "2.5.8",
        "dashboard": "请确保 static/index.html 存在",
        "docs": "/docs"
    })


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    """Dashboard 页面"""
    if Path("static/index.html").exists():
        return FileResponse("static/index.html")
    return JSONResponse(
        status_code=404,
        content={"error": {"message": "Dashboard 未找到，请确保 static/index.html 存在"}}
    )


# ================= 注册 API 路由（在 Dashboard 之后）=================

from app.api import router as api_router
app.include_router(api_router)


# ================= 挂载静态文件 =================

if Path("static").exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")

# 🆕 挂载图片下载目录
download_images_dir = Path("download_images")
download_images_dir.mkdir(exist_ok=True)  # 自动创建目录
app.mount("/download_images", StaticFiles(directory="download_images"), name="download_images")
logger.info(f"📁 图片下载目录: {download_images_dir.absolute()}")


# ================= 异常处理 =================

@app.exception_handler(404)
async def not_found_handler(request, exc):
    return JSONResponse(
        status_code=404,
        content={"error": {"message": "接口不存在", "path": str(request.url.path)}}
    )


@app.exception_handler(500)
async def internal_error_handler(request, exc):
    logger.error(f"内部错误: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": {"message": "服务器内部错误"}}
    )


# ================= 主入口 =================

if __name__ == "__main__":
    import uvicorn

    print("\n" + "=" * 60)
    print("环境变量配置（可选）:")
    print("  APP_HOST=0.0.0.0          # 监听地址")
    print("  APP_PORT=8199             # 监听端口")
    print("  APP_DEBUG=true            # 调试模式")
    print("  BROWSER_PORT=9222         # 浏览器端口")
    print("=" * 60 + "\n")

    uvicorn.run(
        app,
        host=AppConfig.get_host(),
        port=AppConfig.get_port(),
        log_level="warning",  # 隐藏 uvicorn 的 INFO 日志
        access_log=False
    )
