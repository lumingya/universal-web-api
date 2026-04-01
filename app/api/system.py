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
from typing import Optional, Any, Dict

from app import __version__ as APP_VERSION
from fastapi import APIRouter, Request, HTTPException, Header, Depends
from fastapi.responses import JSONResponse

from app.core.config import AppConfig, get_logger, log_collector
from app.core import get_browser, BrowserConnectionError
from app.services.config_engine import config_engine, ConfigConstants
from app.services.command_engine import command_engine
from app.services.request_manager import request_manager
from update_preserve import load_update_preserve_settings, save_update_preserve_settings

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


def _load_env_config_from_file() -> Dict[str, Any]:
    """读取 .env 文件配置。"""
    env_path = Path(".env")
    config: Dict[str, Any] = {}

    if not env_path.exists():
        return config

    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if value.lower() == "true":
                value = True
            elif value.lower() == "false":
                value = False
            elif value.isdigit():
                value = int(value)
            elif re.match(r"^\d+\.\d+$", value):
                value = float(value)

            config[key] = value

    return config


def _serialize_env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return ""
    return str(value)


def _write_env_config_file(new_config: Dict[str, Any]) -> None:
    """写入 .env 文件，尽量保留注释和现有顺序。"""
    env_path = Path(".env")
    lines = []

    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    new_lines = []
    existing_keys = set()

    for line in lines:
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue

        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            existing_keys.add(key)

            if key in new_config:
                value = _serialize_env_value(new_config[key])
                new_lines.append(f"{key}={value}\n")
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    missing_items = []
    for key, value in (new_config or {}).items():
        if key in existing_keys:
            continue

        serialized = _serialize_env_value(value)
        if serialized == "":
            continue

        missing_items.append((key, serialized))

    if missing_items:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] = new_lines[-1] + "\n"
        if new_lines and new_lines[-1].strip():
            new_lines.append("\n")

        for key, value in missing_items:
            new_lines.append(f"{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _write_json_file(path: Path, payload: Any) -> None:
    tmp_path = Path(str(path) + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.flush()
    tmp_path.replace(path)


def _schedule_service_restart(delay_seconds: float = 1.0) -> None:
    async def trigger_restart():
        await asyncio.sleep(max(0.1, float(delay_seconds or 1.0)))
        logger.warning("=" * 60)
        logger.warning("配置已更新，服务即将重启...")
        logger.warning("=" * 60)

        import os
        os._exit(3)

    asyncio.create_task(trigger_restart())


def _build_settings_backup_bundle() -> Dict[str, Any]:
    sites_file = Path(config_engine.config_file)
    sites_local_file = Path(config_engine.local_sites_file)
    commands_file = Path(ConfigConstants.COMMANDS_FILE)
    commands_local_file = Path(ConfigConstants.COMMANDS_LOCAL_FILE)
    browser_config_file = Path("config/browser_config.json")

    return {
        "bundle_version": 1,
        "exported_at": int(time.time()),
        "app_version": APP_VERSION,
        "files": {
            "sites": _read_json_file(sites_file, {}),
            "sites_local": _read_json_file(sites_local_file, {"default_presets": {}}),
            "commands": _read_json_file(commands_file, {"commands": []}),
            "commands_local": _read_json_file(commands_local_file, {"commands": []}),
            "browser_constants": _read_json_file(browser_config_file, {}),
            "update_preserve": load_update_preserve_settings(),
            "env": _load_env_config_from_file(),
        },
    }


DEFAULT_BROWSER_CONSTANTS: Dict[str, Any] = {
    "CONNECTION_TIMEOUT": 10,
    "STEALTH_DELAY_MIN": 0.1,
    "STEALTH_DELAY_MAX": 0.3,
    "ACTION_DELAY_MIN": 0.15,
    "ACTION_DELAY_MAX": 0.3,
    "DEFAULT_ELEMENT_TIMEOUT": 3,
    "FALLBACK_ELEMENT_TIMEOUT": 1,
    "ELEMENT_CACHE_MAX_AGE": 5.0,
    "LOG_INFO_CUTE_MODE": False,
    "LOG_DEBUG_CUTE_MODE": False,
    "STREAM_CHECK_INTERVAL_MIN": 0.1,
    "STREAM_CHECK_INTERVAL_MAX": 1.0,
    "STREAM_CHECK_INTERVAL_DEFAULT": 0.3,
    "STREAM_SILENCE_THRESHOLD": 8.0,
    "STREAM_MAX_TIMEOUT": 600,
    "STREAM_INITIAL_WAIT": 180,
    "STREAM_CONTENT_SHRINK_TOLERANCE": 3,
    "STREAM_STABLE_COUNT_THRESHOLD": 8,
    "STREAM_SILENCE_THRESHOLD_FALLBACK": 12,
    "MAX_MESSAGE_LENGTH": 100000,
    "MAX_MESSAGES_COUNT": 100,
    "GLOBAL_NETWORK_INTERCEPTION_ENABLED": False,
    "GLOBAL_NETWORK_INTERCEPTION_LISTEN_PATTERN": "http",
    "GLOBAL_NETWORK_INTERCEPTION_WAIT_TIMEOUT": 0.5,
    "GLOBAL_NETWORK_INTERCEPTION_RETRY_DELAY": 1.0,
    "COMMAND_PERIODIC_CHECK_ENABLED": True,
    "COMMAND_PERIODIC_CHECK_INTERVAL_SEC": 8.0,
    "COMMAND_PERIODIC_CHECK_JITTER_SEC": 2.0,
    "UPLOAD_HISTORY_IMAGES": False,
    "tab_pool": {
        "max_tabs": 5,
        "min_tabs": 1,
        "idle_timeout": 300,
        "acquire_timeout": 60,
        "stuck_timeout": 180,
    },
}


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
        "version": APP_VERSION,
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
async def get_logs(
    since: float = 0,
    after_seq: int = 0,
    authenticated: bool = Depends(verify_auth),
):
    """获取日志"""
    logs, next_seq = log_collector.get_recent(since=since, after_seq=after_seq)
    return {"logs": logs, "timestamp": time.time(), "next_seq": next_seq}


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
        return {"config": _load_env_config_from_file()}
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
        _write_env_config_file(new_config)

        logger.info(f"环境配置已保存: {len(new_config)} 项，准备触发重启...")
        _schedule_service_restart(1.0)

        return {
            "status": "success",
            "message": "环境配置已保存，服务将在 1 秒后重启...",
            "updated_count": len(new_config),
            "will_restart": True
        }

    except Exception as e:
        logger.error(f"保存环境配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


@router.get("/api/settings/backup")
async def export_settings_backup(authenticated: bool = Depends(verify_auth)):
    """导出完整配置备份。"""
    try:
        return _build_settings_backup_bundle()
    except Exception as e:
        logger.error(f"导出配置备份失败: {e}")
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")


@router.post("/api/settings/backup")
async def import_settings_backup(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """导入完整配置备份。"""
    try:
        data = await request.json()
        allowed_sections = {
            "sites",
            "sites_local",
            "commands",
            "commands_local",
            "browser_constants",
            "update_preserve",
            "env",
        }

        raw_files = data.get("files") if isinstance(data, dict) else None
        if isinstance(raw_files, dict):
            files = {key: value for key, value in raw_files.items() if key in allowed_sections}
        elif isinstance(data, dict):
            files = {key: value for key, value in data.items() if key in allowed_sections}
        else:
            files = {}

        if not files:
            raise HTTPException(status_code=400, detail="备份文件格式无效")

        imported_sections = []
        restart_required = False

        if "sites" in files:
            if not isinstance(files["sites"], dict):
                raise HTTPException(status_code=400, detail="sites 配置格式无效")
            _write_json_file(Path(config_engine.config_file), files["sites"])
            imported_sections.append("sites")

        if "sites_local" in files:
            if not isinstance(files["sites_local"], dict):
                raise HTTPException(status_code=400, detail="sites_local 配置格式无效")
            _write_json_file(Path(config_engine.local_sites_file), files["sites_local"])
            imported_sections.append("sites_local")

        if "commands" in files:
            commands_payload = files["commands"]
            if not isinstance(commands_payload, (dict, list)):
                raise HTTPException(status_code=400, detail="commands 配置格式无效")
            _write_json_file(Path(ConfigConstants.COMMANDS_FILE), commands_payload)
            imported_sections.append("commands")

        if "commands_local" in files:
            commands_local_payload = files["commands_local"]
            if not isinstance(commands_local_payload, dict):
                raise HTTPException(status_code=400, detail="commands_local 配置格式无效")
            _write_json_file(Path(ConfigConstants.COMMANDS_LOCAL_FILE), commands_local_payload)
            imported_sections.append("commands_local")

        if "browser_constants" in files:
            if not isinstance(files["browser_constants"], dict):
                raise HTTPException(status_code=400, detail="browser_constants 配置格式无效")
            _write_json_file(Path("config/browser_config.json"), files["browser_constants"])
            imported_sections.append("browser_constants")
            try:
                from app.core.config import BrowserConstants
                if hasattr(BrowserConstants, "reload"):
                    BrowserConstants.reload()
            except Exception as reload_error:
                logger.warning(f"导入后热重载浏览器常量失败: {reload_error}")

        if "update_preserve" in files:
            preserve_payload = files["update_preserve"]
            if isinstance(preserve_payload, dict):
                selected_patterns = preserve_payload.get("selected_patterns", [])
            else:
                selected_patterns = preserve_payload
            save_update_preserve_settings(selected_patterns or [])
            imported_sections.append("update_preserve")

        if "env" in files:
            if not isinstance(files["env"], dict):
                raise HTTPException(status_code=400, detail="env 配置格式无效")
            _write_env_config_file(files["env"])
            imported_sections.append("env")
            restart_required = True

        if "sites" in files or "sites_local" in files:
            config_engine.reload_config()

        if "commands" in files or "commands_local" in files:
            command_engine._refresh_commands_if_changed(force=True)

        logger.info(f"完整配置备份已导入: {', '.join(imported_sections)}")

        if restart_required:
            _schedule_service_restart(1.0)

        return {
            "success": True,
            "message": "完整配置备份已导入",
            "imported_sections": imported_sections,
            "will_restart": restart_required,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"导入配置备份失败: {e}")
        raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")


@router.get("/api/settings/browser-constants")
async def get_browser_constants(authenticated: bool = Depends(verify_auth)):
    """读取浏览器常量配置"""
    try:
        config_path = Path("config/browser_config.json")
        config = _read_json_file(config_path, DEFAULT_BROWSER_CONSTANTS)
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
        _write_json_file(Path("config/browser_config.json"), config)

        try:
            from app.core.config import BrowserConstants
            if hasattr(BrowserConstants, 'reload'):
                BrowserConstants.reload()
                logger.info("浏览器常量已热重载")
            else:
                logger.warning("BrowserConstants 不支持热重载，需重启服务")
        except Exception as reload_error:
            logger.warning(f"热重载失败: {reload_error}")

        tab_pool_synced = False
        try:
            tab_pool_config = config.get("tab_pool") or {}
            if isinstance(tab_pool_config, dict):
                import app.core.browser as browser_module

                browser_instance = getattr(browser_module, "_browser_instance", None)
                live_tab_pool = getattr(browser_instance, "_tab_pool", None) if browser_instance else None
                if live_tab_pool is not None:
                    live_tab_pool.apply_runtime_config(
                        max_tabs=tab_pool_config.get("max_tabs"),
                        min_tabs=tab_pool_config.get("min_tabs"),
                        idle_timeout=tab_pool_config.get("idle_timeout"),
                        acquire_timeout=tab_pool_config.get("acquire_timeout"),
                        stuck_timeout=tab_pool_config.get("stuck_timeout"),
                    )
                    tab_pool_synced = True
                    logger.info("运行中的标签页池配置已同步")
        except Exception as sync_error:
            logger.warning(f"同步标签页池运行时配置失败: {sync_error}")

        logger.info(f"浏览器常量已保存: {len(config)} 项")

        return {
            "status": "success",
            "message": "浏览器常量已保存",
            "updated_count": len(config),
            "tab_pool_synced": tab_pool_synced,
        }

    except Exception as e:
        logger.error(f"保存浏览器常量失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


@router.get("/api/settings/update-preserve")
async def get_update_preserve_settings(authenticated: bool = Depends(verify_auth)):
    """读取更新白名单配置。"""
    try:
        data = load_update_preserve_settings()
        return {
            "options": data.get("options", []),
            "selected_patterns": data.get("selected_patterns", []),
        }
    except Exception as e:
        logger.error(f"读取更新白名单失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取失败: {str(e)}")


@router.post("/api/settings/update-preserve")
async def save_update_preserve(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """保存更新白名单配置。"""
    try:
        data = await request.json()
        selected_patterns = data.get("selected_patterns", [])
        result = save_update_preserve_settings(selected_patterns)
        logger.info(f"更新白名单已保存: {len(result.get('selected_patterns', []))} 项")
        return {
            "status": "success",
            "message": "更新白名单已保存，下次更新时生效",
            "selected_patterns": result.get("selected_patterns", []),
        }
    except Exception as e:
        logger.error(f"保存更新白名单失败: {e}")
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
            logger.debug(f"[DEBUG] query={query_selector}, result={type(elements)}, len={len(elements) if elements else 0}")

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

            if highlight:
                try:
                    tab.set.activate()
                except Exception as e:
                    logger.debug(f"激活调试标签页失败: {e}")

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
                            ele.run_js("""
                                const token = `selector-test-${Date.now()}-${Math.random().toString(36).slice(2)}`;
                                const previous = {
                                    outline: this.style.outline,
                                    outlineOffset: this.style.outlineOffset,
                                    boxShadow: this.style.boxShadow,
                                    transition: this.style.transition,
                                };

                                this.dataset.selectorTestHighlightToken = token;
                                this.style.transition = 'outline .15s ease, box-shadow .15s ease';
                                this.style.outline = '3px solid rgba(239, 68, 68, 0.95)';
                                this.style.outlineOffset = '2px';
                                this.style.boxShadow = '0 0 0 6px rgba(251, 191, 36, 0.45)';
                                this.scrollIntoView({behavior: 'smooth', block: 'center', inline: 'center'});

                                setTimeout(() => {
                                    if (this.dataset.selectorTestHighlightToken !== token) {
                                        return;
                                    }
                                    this.style.outline = previous.outline;
                                    this.style.outlineOffset = previous.outlineOffset;
                                    this.style.boxShadow = previous.boxShadow;
                                    this.style.transition = previous.transition;
                                    delete this.dataset.selectorTestHighlightToken;
                                }, 5000);
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
    if not AppConfig.DEBUG:
        raise HTTPException(status_code=403, detail="调试功能未启用")
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
async def cancel_current(
    tab_id: Optional[str] = None,
    authenticated: bool = Depends(verify_auth),
):
    """取消当前正在执行的请求"""
    if not AppConfig.DEBUG:
        raise HTTPException(status_code=403, detail="调试功能未启用")

    running_requests = request_manager.get_running_requests(tab_id=tab_id)
    if not running_requests:
        return {"cancelled": False, "message": "没有正在执行的请求", "tab_id": tab_id}

    if not tab_id and len(running_requests) > 1:
        raise HTTPException(
            status_code=400,
            detail="存在多个运行中的请求，请指定 tab_id",
        )

    current_id = running_requests[0].request_id
    success = request_manager.cancel_current("manual_cancel", tab_id=tab_id)

    return {
        "cancelled": success,
        "request_id": current_id,
        "tab_id": tab_id or running_requests[0].tab_id,
    }
