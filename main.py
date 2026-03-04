"""
main.py - FastAPI 应用入口
"""
import logging
logging.getLogger("urllib3").setLevel(logging.WARNING)
import os
import logging
import sys
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


# ================= Lifespan =================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
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
            # 检查是否需要打开教程页（仅当浏览器刚启动、没有实际内容时）
            should_open_tutorial = False
            
            try:
                tab_ids = browser.page.get_tabs()
                
                if len(tab_ids) == 0:
                    # 没有任何标签页
                    should_open_tutorial = True
                elif len(tab_ids) == 1:
                    # 只有一个标签页，检查是否是空白页
                    url = browser.page.url or ""
                    blank_patterns = ("about:blank", "chrome://newtab/", "chrome://new-tab-page/", "")
                    if url in blank_patterns:
                        should_open_tutorial = True
                # 多个标签页 = 用户已在使用，不打开教程
                
            except Exception as e:
                logger.debug(f"检查标签页状态失败: {e}")
            
            if should_open_tutorial:
                try:
                    tutorial_url = f"http://{AppConfig.get_host()}:{AppConfig.get_port()}/static/tutorial.html"
                    logger.info(f"📚 首次启动，打开教程页: {tutorial_url}")
                    browser.page.get(tutorial_url, retry=0, show_errmsg=False)
                except Exception as e:
                    logger.warning(f"⚠️ 无法打开教程页: {e}")
            else:
                # 显示已连接状态
                pool_info = health.get("tab_pool", {})
                tab_count = pool_info.get('total', 0) if pool_info else 0
                logger.info(f"✅ 浏览器已连接 (检测到 {len(tab_ids) if 'tab_ids' in dir() else '?'} 个现有页面，跳过教程)")
        else:
            logger.warning(f"⚠️ 浏览器未连接: {health.get('error', '未知')}")
        
    except Exception as e:
        logger.warning(f"⚠️ 浏览器检查跳过: {e}")

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
    version="2.5.6",
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


# ================= Dashboard 路由（优先级最高）=================

@app.get("/", include_in_schema=False)
async def root():
    """首页 - Dashboard"""
    if Path("static/index.html").exists():
        return FileResponse("static/index.html")
    # 如果没有 Dashboard，返回 API 信息
    return JSONResponse({
        "service": "Universal Web-to-API",
        "version": "2.5.6",
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
