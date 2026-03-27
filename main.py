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
from typing import Any, Dict
from urllib.request import urlopen
from pathlib import Path
from contextlib import asynccontextmanager
from app import __version__ as APP_VERSION
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
logger.debug(f"[startup] Python executable: {sys.executable}")


_STARTUP_EMPTY_URLS = ("", "about:blank", "chrome://newtab/", "chrome://new-tab-page/")
_GUIDE_SITE_ORDER = [
    "chatgpt.com",
    "chat.deepseek.com",
    "gemini.google.com",
    "claude.ai",
    "www.kimi.com",
    "chat.qwen.ai",
    "grok.com",
    "www.doubao.com",
    "aistudio.google.com",
    "arena.ai",
]


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

def _open_startup_page_non_blocking(page_url: str, page_name: str, initial_delay_sec: float = 1.2):
    """Use the system browser for a local startup page, not the controlled browser."""
    def _worker():
        try:
            time.sleep(max(0.0, float(initial_delay_sec)))

            # 等待本地 HTTP 服务可达，避免 lifespan 内部自访问卡住。
            if not _wait_for_local_page(page_url):
                logger.warning(f"[startup] {page_name}未就绪，跳过自动打开: {page_url}")
                return

            webbrowser.open_new_tab(page_url)
            logger.info(f"[startup] {page_name}已在系统浏览器打开: {page_url}")
        except Exception as e:
            logger.warning(f"[startup] 打开{page_name}失败: {e}")

    threading.Thread(
        target=_worker,
        daemon=True,
        name="open-startup-page-non-blocking",
    ).start()


def _wait_for_local_page(page_url: str, attempts: int = 12, interval_sec: float = 0.5) -> bool:
    for _ in range(max(1, int(attempts))):
        try:
            with urlopen(page_url, timeout=1.2) as resp:
                status_code = int(getattr(resp, "status", 200) or 200)
                if 200 <= status_code < 500:
                    return True
        except Exception:
            time.sleep(max(0.0, float(interval_sec)))
    return False


def _resolve_local_startup_host() -> str:
    host = str(AppConfig.get_host() or "").strip()
    if host in ("", "0.0.0.0", "::", "[::]", "::0"):
        return "127.0.0.1"
    return host


def _get_local_startup_base_url() -> str:
    return f"http://{_resolve_local_startup_host()}:{AppConfig.get_port()}"


def _should_open_startup_pages(browser) -> bool:
    try:
        tab_ids = browser.page.get_tabs()
        if len(tab_ids) == 0:
            return True
        if len(tab_ids) == 1:
            url = ""
            try:
                url = browser.page.url or ""
            except Exception:
                pass
            return url in _STARTUP_EMPTY_URLS
    except Exception as e:
        logger.debug(f"检查标签页状态失败: {e}")
    return False


def _open_controlled_browser_page_non_blocking(
    browser,
    page_url: str,
    page_name: str,
    initial_delay_sec: float = 1.0,
):
    """Navigate the controlled browser only if it still stays on the initial blank page."""

    def _worker():
        try:
            time.sleep(max(0.0, float(initial_delay_sec)))

            if not _wait_for_local_page(page_url):
                logger.warning(f"[startup] {page_name}未就绪，跳过自动打开: {page_url}")
                return

            if not _should_open_startup_pages(browser):
                logger.info(f"[startup] 受控浏览器已离开空白页，跳过打开{page_name}")
                return

            target_tab = None
            try:
                target_tab = browser.page.latest_tab
            except Exception:
                target_tab = None
            if target_tab is None:
                target_tab = browser.page

            target_tab.get(page_url)
            logger.info(f"[startup] {page_name}已在受控浏览器打开: {page_url}")
        except Exception as e:
            logger.warning(f"[startup] 打开{page_name}失败: {e}")

    threading.Thread(
        target=_worker,
        daemon=True,
        name="open-controlled-browser-page-non-blocking",
    ).start()


def _build_controlled_browser_guide_data() -> Dict[str, Any]:
    from app.services.config_engine import config_engine

    all_sites = config_engine.list_sites()
    local_patterns = ("127.0.0.1", "localhost", "0.0.0.0", "::1")
    priority = {domain: index for index, domain in enumerate(_GUIDE_SITE_ORDER)}

    domains = []
    for domain in all_sites.keys():
        normalized = str(domain or "").strip()
        lowered = normalized.lower()
        if not normalized or normalized.startswith("_"):
            continue
        if any(pattern in lowered for pattern in local_patterns):
            continue
        domains.append(normalized)

    domains.sort(key=lambda item: (priority.get(item, len(priority)), item))
    base_url = _get_local_startup_base_url()

    return {
        "dashboard_url": f"{base_url}/",
        "guide_url": f"{base_url}/static/controlled-browser-guide.html",
        "sites": [
            {
                "domain": domain,
                "url": f"https://{domain}",
            }
            for domain in domains
        ],
    }
def _resolve_dashboard_path():
    configured = (AppConfig.get_dashboard_file() or "").strip()
    candidates = []

    if configured:
        configured_path = Path(configured)
        if configured_path.is_absolute():
            candidates.append(configured_path)
        else:
            candidates.append(configured_path)
            candidates.append(Path("static") / configured_path)

    candidates.append(Path("static/index.html"))

    seen = set()
    for candidate in candidates:
        normalized = str(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)

        if candidate.exists() and candidate.is_file():
            return candidate

    return None


def _dashboard_info_response():
    return JSONResponse({
        "service": "Universal Web-to-API",
        "version": APP_VERSION,
        "dashboard": "disabled",
        "docs": "/docs"
    })

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
            if _should_open_startup_pages(browser):
                try:
                    base_url = _get_local_startup_base_url()
                    tutorial_url = f"{base_url}/static/tutorial.html"
                    guide_url = f"{base_url}/static/controlled-browser-guide.html"
                    logger.info(f"[startup] 首次启动，使用系统浏览器打开教程页: {tutorial_url}")
                    _open_startup_page_non_blocking(
                        tutorial_url,
                        page_name="教程页",
                        initial_delay_sec=1.2,
                    )
                    logger.info(f"[startup] 首次启动，准备在受控浏览器打开引导页: {guide_url}")
                    _open_controlled_browser_page_non_blocking(
                        browser,
                        guide_url,
                        page_name="受控浏览器引导页",
                        initial_delay_sec=0.8,
                    )
                except Exception as e:
                    logger.warning(f"⚠️ 无法打开教程页: {e}")
            else:
                # 显示已连接状态
                try:
                    existing_tab_count = len(browser.page.get_tabs())
                except Exception:
                    existing_tab_count = "?"
                logger.info(f"✅ 浏览器已连接 (检测到 {existing_tab_count} 个现有页面，跳过教程)")
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
    if AppConfig.is_dashboard_enabled():
        logger.info(f"   Dashboard: http://{AppConfig.get_host()}:{AppConfig.get_port()}/")
    else:
        logger.info("   Dashboard: disabled")
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
    version=APP_VERSION,
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
    if path in ("/", "/dashboard", "/marketplace") or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ================= Dashboard 路由（优先级最高）=================

@app.get("/", include_in_schema=False)
async def root():
    """首页 - Dashboard"""
    if not AppConfig.is_dashboard_enabled():
        return _dashboard_info_response()

    dashboard_path = _resolve_dashboard_path()
    if dashboard_path:
        return FileResponse(dashboard_path)
    # 如果没有 Dashboard，返回 API 信息
    return JSONResponse({
        "service": "Universal Web-to-API",
        "version": APP_VERSION,
        "dashboard": "请确保 DASHBOARD_FILE 指向的文件存在",
        "docs": "/docs"
    })


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    """Dashboard 页面"""
    if not AppConfig.is_dashboard_enabled():
        return JSONResponse(
            status_code=404,
            content={"error": {"message": "Dashboard 已禁用"}}
        )

    dashboard_path = _resolve_dashboard_path()
    if dashboard_path:
        return FileResponse(dashboard_path)
    return JSONResponse(
        status_code=404,
        content={"error": {"message": "Dashboard 未找到，请确保 DASHBOARD_FILE 指向的文件存在"}}
    )


@app.get("/marketplace", include_in_schema=False)
async def marketplace_page():
    """插件市场独立页面"""
    page_path = Path("static/marketplace.html")
    if page_path.exists() and page_path.is_file():
        return FileResponse(page_path)
    return JSONResponse(
        status_code=404,
        content={"error": {"message": "插件市场页面未找到"}}
    )


@app.get("/api/startup/controlled-browser-guide-data", include_in_schema=False)
async def controlled_browser_guide_data():
    try:
        return JSONResponse(_build_controlled_browser_guide_data())
    except Exception as e:
        logger.warning(f"[startup] 生成受控浏览器引导页数据失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": {"message": "无法加载受控浏览器引导页数据"}}
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
