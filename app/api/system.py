"""
app/api/system.py - 系统功能 API

职责：
- 健康检查
- 日志管理
- 环境配置
- 浏览器常量
- 调试接口
"""

import json
import re
import time
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Header, Depends
from fastapi.responses import JSONResponse

from app.core.config import AppConfig, get_logger, log_collector
from app.core import get_browser, BrowserConnectionError
from app.services.config_engine import config_engine
from app.services.request_manager import request_manager

logger = get_logger("API.SYSTEM")

router = APIRouter()


# ================= 认证依赖 =================

async def verify_auth(authorization: Optional[str] = Header(None)) -> bool:
    """验证 Bearer Token"""
    if not AppConfig.is_auth_enabled():
        return True

    if not AppConfig.AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="服务配置错误")

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = authorization.replace("Bearer ", "").strip()

    if token != AppConfig.get_auth_token():
        raise HTTPException(
            status_code=401,
            detail="认证令牌无效",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return True


# ================= 健康检查 =================

@router.get("/health")
async def health_check():
    """服务健康检查"""
    try:
        browser = get_browser(auto_connect=False)
        browser_health = browser.health_check()
    except Exception as e:
        browser_health = {"connected": False, "error": str(e)}

    rm_status = request_manager.get_status()

    response = {
        "service": "healthy",
        "version": "2.0.0",
        "browser": browser_health,
        "request_manager": rm_status,
        "config": {
            "sites_loaded": len(config_engine.sites),
            "auth_enabled": AppConfig.is_auth_enabled()
        },
        "timestamp": int(time.time())
    }

    status_code = 200 if browser_health.get("connected") else 503
    return JSONResponse(content=response, status_code=status_code)


# ================= 日志 API =================

@router.get("/api/logs")
async def get_logs(since: float = 0, authenticated: bool = Depends(verify_auth)):
    """获取日志"""
    logs = log_collector.get_recent(since)
    return {"logs": logs, "timestamp": time.time()}


@router.delete("/api/logs")
async def clear_logs(authenticated: bool = Depends(verify_auth)):
    """清除日志"""
    log_collector.clear()
    return {"status": "success"}


# ================= 环境配置 API =================

@router.get("/api/settings/env")
async def get_env_config(authenticated: bool = Depends(verify_auth)):
    """读取 .env 文件配置"""
    try:
        env_path = Path(".env")
        config = {}

        if env_path.exists():
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()

                    if not line or line.startswith('#'):
                        continue

                    if '=' not in line:
                        continue

                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()

                    if value.lower() == 'true':
                        value = True
                    elif value.lower() == 'false':
                        value = False
                    elif value.isdigit():
                        value = int(value)
                    elif re.match(r'^\d+\.\d+$', value):
                        value = float(value)

                    config[key] = value

        return {"config": config}

    except Exception as e:
        logger.error(f"读取环境配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取失败: {str(e)}")


@router.post("/api/settings/env")
async def save_env_config(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """保存 .env 配置"""
    try:
        data = await request.json()
        new_config = data.get("config", {})

        env_path = Path(".env")
        lines = []

        if env_path.exists():
            with open(env_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

        new_lines = []

        for line in lines:
            stripped = line.strip()

            if not stripped or stripped.startswith('#'):
                new_lines.append(line)
                continue

            if '=' in stripped:
                key = stripped.split('=', 1)[0].strip()

                if key in new_config:
                    value = new_config[key]

                    if isinstance(value, bool):
                        value = 'true' if value else 'false'
                    elif isinstance(value, (int, float)):
                        value = str(value)

                    new_lines.append(f"{key}={value}\n")
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        with open(env_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

        logger.info(f"环境配置已保存: {len(new_config)} 项，准备触发重启...")

        async def trigger_restart():
            await asyncio.sleep(1.0)
            logger.warning("=" * 60)
            logger.warning("配置已更新，服务即将重启...")
            logger.warning("=" * 60)
            
            import os
            os._exit(3)
        
        asyncio.create_task(trigger_restart())

        return {
            "status": "success",
            "message": "环境配置已保存，服务将在 1 秒后重启...",
            "updated_count": len(new_config),
            "will_restart": True
        }

    except Exception as e:
        logger.error(f"保存环境配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


@router.get("/api/settings/browser-constants")
async def get_browser_constants(authenticated: bool = Depends(verify_auth)):
    """读取浏览器常量配置"""
    try:
        config_path = Path("config/browser_config.json")

        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        else:
            config = {
                'DEFAULT_PORT': 9222,
                'CONNECTION_TIMEOUT': 10,
                'STEALTH_DELAY_MIN': 0.1,
                'STEALTH_DELAY_MAX': 0.3,
                'ACTION_DELAY_MIN': 0.15,
                'ACTION_DELAY_MAX': 0.3,
                'DEFAULT_ELEMENT_TIMEOUT': 3,
                'FALLBACK_ELEMENT_TIMEOUT': 1,
                'ELEMENT_CACHE_MAX_AGE': 5.0,
                'STREAM_CHECK_INTERVAL_MIN': 0.1,
                'STREAM_CHECK_INTERVAL_MAX': 1.0,
                'STREAM_CHECK_INTERVAL_DEFAULT': 0.3,
                'STREAM_SILENCE_THRESHOLD': 8.0,
                'STREAM_MAX_TIMEOUT': 600,
                'STREAM_INITIAL_WAIT': 180,
                'STREAM_RERENDER_WAIT': 0.5,
                'STREAM_CONTENT_SHRINK_TOLERANCE': 3,
                'STREAM_MIN_VALID_LENGTH': 10,
                'STREAM_STABLE_COUNT_THRESHOLD': 8,
                'STREAM_SILENCE_THRESHOLD_FALLBACK': 12,
                'MAX_MESSAGE_LENGTH': 100000,
                'MAX_MESSAGES_COUNT': 100,
                'STREAM_INITIAL_ELEMENT_WAIT': 10,
                'STREAM_MAX_ABNORMAL_COUNT': 5,
                'STREAM_MAX_ELEMENT_MISSING': 10,
                'STREAM_CONTENT_SHRINK_THRESHOLD': 0.3,
            }

        return {"config": config}

    except Exception as e:
        logger.error(f"读取浏览器常量失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取失败: {str(e)}")


@router.post("/api/settings/browser-constants")
async def save_browser_constants(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """保存浏览器常量配置"""
    try:
        data = await request.json()
        config = data.get("config", {})

        config_path = Path("config/browser_config.json")

        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        try:
            from app.core import BrowserConstants
            if hasattr(BrowserConstants, 'reload'):
                BrowserConstants.reload()
                logger.info("浏览器常量已热重载")
            else:
                logger.warning("BrowserConstants 不支持热重载，需重启服务")
        except Exception as reload_error:
            logger.warning(f"热重载失败: {reload_error}")

        logger.info(f"浏览器常量已保存: {len(config)} 项")

        return {
            "status": "success",
            "message": "浏览器常量已保存",
            "updated_count": len(config)
        }

    except Exception as e:
        logger.error(f"保存浏览器常量失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


# ================= 调试 API =================

@router.post("/api/debug/test-selector")
async def test_selector(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """测试选择器是否有效"""
    if not AppConfig.is_debug():
        raise HTTPException(status_code=403, detail="调试功能未启用")

    try:
        data = await request.json()
        selector = data.get("selector", "")
        timeout = data.get("timeout", 2)
        highlight = data.get("highlight", False)

        if not selector:
            raise HTTPException(status_code=400, detail="缺少 selector")

        browser = get_browser()

        with browser.get_temporary_tab() as tab:
            if selector.startswith(('tag:', '@', 'xpath:', 'css:')) or '@@' in selector:
                query_selector = selector
            else:
                query_selector = f'css:{selector}'

            elements = tab.eles(query_selector, timeout=timeout)
            logger.info(f"[DEBUG] query={query_selector}, result={type(elements)}, len={len(elements) if elements else 0}")

            if not elements:
                return {"success": False, "count": 0, "message": "元素未找到"}

            if not isinstance(elements, list):
                elements = [elements]

            valid_elements = []
            for ele in elements:
                try:
                    if ele and hasattr(ele, 'tag'):
                        valid_elements.append(ele)
                except:
                    pass

            if not valid_elements:
                return {"success": False, "count": 0, "message": "元素未找到或无效"}

            result = {
                "success": True,
                "count": len(valid_elements),
                "elements": []
            }

            for idx, ele in enumerate(valid_elements):
                try:
                    ele_info = {
                        "index": idx,
                        "tag": ele.tag if hasattr(ele, 'tag') else "unknown",
                        "text": ""
                    }

                    try:
                        text = ele.text
                        if text:
                            ele_info["text"] = text[:100]
                    except:
                        pass

                    try:
                        attrs = {}
                        for attr in ['id', 'class', 'name', 'data-testid', 'aria-label']:
                            val = ele.attr(attr)
                            if val:
                                attrs[attr] = val[:50] if isinstance(val, str) else str(val)[:50]
                        if attrs:
                            ele_info["attributes"] = attrs
                    except:
                        pass

                    result["elements"].append(ele_info)

                    if highlight:
                        try:
                            css_selector = selector if not selector.startswith(('tag:', '@', 'xpath:', 'css:')) else selector.replace('css:', '')
                            tab.run_js(f"""
                                (function() {{
                                    try {{
                                        const elements = document.querySelectorAll('{css_selector}');
                                        if (elements[{idx}]) {{
                                            const el = elements[{idx}];
                                            el.style.outline = '3px solid red';
                                            el.style.outlineOffset = '2px';
                                            el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                                            setTimeout(() => {{
                                                el.style.outline = '';
                                                el.style.outlineOffset = '';
                                            }}, 5000);
                                        }}
                                    }} catch(e) {{
                                        console.error('高亮失败:', e);
                                    }}
                                }})();
                            """)
                        except Exception as e:
                            logger.debug(f"高亮失败: {e}")

                except Exception as e:
                    logger.debug(f"处理元素 {idx} 失败: {e}")
                    continue

            if result["elements"]:
                first = result["elements"][0]
                result["tag"] = first.get("tag", "unknown")
                result["text"] = first.get("text", "")
                result["attributes"] = first.get("attributes", {})

            return result

    except BrowserConnectionError as e:
        return {"success": False, "count": 0, "message": f"浏览器未连接: {str(e)}"}

    except Exception as e:
        logger.error(f"测试选择器失败: {e}", exc_info=True)
        return {"success": False, "count": 0, "message": str(e)}


@router.get("/api/debug/request-status")
async def request_status(authenticated: bool = Depends(verify_auth)):
    """查看请求管理器状态"""
    return request_manager.get_status()


@router.post("/api/debug/force-release")
async def force_release(authenticated: bool = Depends(verify_auth)):
    """强制释放锁"""
    if not AppConfig.DEBUG:
        raise HTTPException(status_code=403, detail="调试功能未启用")

    was_locked = request_manager.is_locked()
    released = request_manager.force_release()
    is_now_locked = request_manager.is_locked()

    logger.warning(f"手动解锁: was={was_locked}, released={released}, now={is_now_locked}")

    return {
        "was_locked": was_locked,
        "released": released,
        "is_now_locked": is_now_locked
    }


@router.post("/api/debug/cancel-current")
async def cancel_current(authenticated: bool = Depends(verify_auth)):
    """取消当前正在执行的请求"""
    current_id = request_manager.get_current_request_id()

    if not current_id:
        return {"cancelled": False, "message": "没有正在执行的请求"}

    success = request_manager.cancel_current("manual_cancel")

    return {
        "cancelled": success,
        "request_id": current_id
    }