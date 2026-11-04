"""``app/api/system`` 的 settings 分组（原 app/api/system.py 的一部分）。"""

import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request

from app import __version__ as APP_VERSION
from app.api.deps import verify_dashboard_auth as verify_auth
from app.api.deps import verify_sensitive_admin_auth
from app.api.system.common import (
    _load_env_config_from_file,
    _read_json_object_or_400,
    logger,
    router,
)
from app.core.config import AppConfig, _replace_file_with_retry, atomic_write_json
from app.core.http_security import redact_env_for_backup, startup_security_errors
from app.paths import CONFIG_DIR
from app.services.command_engine import command_engine
from app.services.config_engine import ConfigConstants, config_engine
from app.services.extractor_manager import extractor_manager
from app.update.preserve import load_update_preserve_settings, save_update_preserve_settings

_REDACTED_PLACEHOLDERS = {"***", "******", "<redacted>", "[redacted]", "__redacted__", "redacted"}

DEFAULT_BROWSER_CONSTANTS: Dict[str, Any] = {
    "CONNECTION_TIMEOUT": 10,
    "MAX_REQUEST_EXECUTE_TIME_SEC": 300.0,
    "STEALTH_DELAY_MIN": 0.03,
    "STEALTH_DELAY_MAX": 0.1,
    "ACTION_DELAY_MIN": 0.06,
    "ACTION_DELAY_MAX": 0.14,
    "STEALTH_PAUSE_PROBABILITY": 0.0,
    "STEALTH_PAUSE_EXTRA_MAX": 0.15,
    "STEALTH_KEY_DOWN_UP_MIN": 0.015,
    "STEALTH_KEY_DOWN_UP_MAX": 0.04,
    "STEALTH_KEY_BETWEEN_MIN": 0.02,
    "STEALTH_KEY_BETWEEN_MAX": 0.06,
    "STEALTH_PASTE_SETTLE_MIN": 0.12,
    "STEALTH_PASTE_SETTLE_MAX": 0.25,
    "STEALTH_SKIP_PASTE_VERIFY": True,
    "STEALTH_SEND_IMAGE_WAIT": 8.0,
    "STEALTH_SEND_IMAGE_RETRY_INTERVAL": 1.2,
    "STEALTH_MOUSE_WARMUP_ENABLED": False,
    "STEALTH_CLICK_STRATEGY": "auto",
    "STEALTH_DOM_CLICK_TARGETS": ["new_chat_btn", "input_box", "send_btn"],
    "DEFAULT_ELEMENT_TIMEOUT": 3,
    "FALLBACK_ELEMENT_TIMEOUT": 1,
    "ELEMENT_CACHE_MAX_AGE": 5.0,
    "LOG_INFO_CUTE_MODE": False,
    "LOG_DEBUG_CUTE_MODE": False,
    "LOG_CONSOLE_ENABLED": True,
    "LOG_FILE_ENABLED": True,
    "LOG_WEB_COLLECTOR_ENABLED": True,
    "LOG_WEB_MAX_RECORDS": 500,
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
    "TEXT_INPUT_CHUNK_SIZE": 30000,
    "GLOBAL_NETWORK_INTERCEPTION_ENABLED": False,
    "GLOBAL_NETWORK_INTERCEPTION_LISTEN_PATTERN": "http",
    "GLOBAL_NETWORK_INTERCEPTION_WAIT_TIMEOUT": 0.5,
    "GLOBAL_NETWORK_INTERCEPTION_RETRY_DELAY": 1.0,
    "GLOBAL_NETWORK_INTERCEPTION_RES_TYPES": "XHR,Fetch,EventSource,Document",
    "NETWORK_DEBUG_CAPTURE_ENABLED": False,
    "NETWORK_DEBUG_CAPTURE_MAX_BODY_CHARS": 50000,
    "NETWORK_DEBUG_CAPTURE_MAX_FILES_PER_REQUEST": 3,
    "NETWORK_DEBUG_CAPTURE_PARSER_FILTER": "",
    "NETWORK_DEBUG_CAPTURE_MAX_TOTAL_MB": 50,
    "NETWORK_DEBUG_CAPTURE_RETENTION_HOURS": 24,
    "CONVERSATION_TIMEOUT_THRESHOLD": 0.0,
    "FORCE_NEW_CONVERSATION": False,
    "REQUEST_MONITOR_ENABLED": True,
    "REQUEST_MONITOR_MAX_RECORDS": 200,
    "REQUEST_MONITOR_DETAIL_ENABLED": True,
    "REQUEST_MONITOR_SAVE_TO_FILE": True,
    "REQUEST_MONITOR_MAX_CAPTURED_RESPONSE_CHARS": 30000,
    # 修复：补齐缺失的入口，否则前端无法通过 /api/settings/browser-constants 配置它们
    "REQUEST_ZOMBIE_TTL": 600,
    "PAGE_INTERACTION_THROTTLE_ENABLED": True,
    "PAGE_INTERACTION_MAX_CONCURRENT": 3,
    "PAGE_INTERACTION_MAX_WAIT": 20.0,
    "DASHBOARD_LOG_POLL_INTERVAL_MS": 1000,
    "DASHBOARD_LOG_BACKGROUND_POLL_INTERVAL_MS": 5000,
    "DASHBOARD_REQUEST_HISTORY_POLL_INTERVAL_MS": 3000,
    "DASHBOARD_SYSTEM_STATS_ENABLED": True,
    "DASHBOARD_SYSTEM_STATS_POLL_INTERVAL_MS": 3000,
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
        "allocation_mode": "first_idle",
        "excluded_urls": [],
        "preserve_error_tabs": False,
        "route_groups": [],
    },
}


def _serialize_env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return ""
    return str(value)


def _write_text_file_atomic(path: Path, lines: list[str]) -> None:
    """原子写入文本文件，避免写入中断时截断原文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    tmp_path: Optional[Path] = None

    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            fd = None
            f.writelines(lines)
            f.flush()
            os.fsync(f.fileno())
        _replace_file_with_retry(tmp_path, path)
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        raise


def _write_env_config_file(new_config: Dict[str, Any]) -> None:
    """写入 .env 文件，尽量保留注释和现有顺序。"""
    env_path = Path(".env")
    lines = []

    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
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

    _write_text_file_atomic(env_path, new_lines)


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _write_json_file(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload)


def _snapshot_file(path: Path) -> Optional[bytes]:
    if not path.exists():
        return None
    return path.read_bytes()


def _restore_file_snapshot(path: Path, snapshot: Optional[bytes]) -> None:
    if snapshot is None:
        try:
            path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    tmp_path: Optional[Path] = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.rollback.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(snapshot)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_file_with_retry(tmp_path, path)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


def _restore_file_snapshots(snapshots: Dict[Path, Optional[bytes]]) -> None:
    for path, snapshot in reversed(list(snapshots.items())):
        try:
            _restore_file_snapshot(path, snapshot)
        except Exception as rollback_error:
            logger.error(f"回滚配置文件失败: {path}: {rollback_error}")


def _schedule_service_restart(delay_seconds: float = 1.0) -> None:
    async def trigger_restart():
        await asyncio.sleep(max(0.1, float(delay_seconds or 1.0)))
        logger.warning("=" * 60)
        logger.warning("配置已更新，服务即将重启...")
        logger.warning("=" * 60)

        from app.runtime.relaunch import restart_service
        restart_service()

    asyncio.create_task(trigger_restart())


def _export_sites_config() -> Dict[str, Any]:
    """R1-2：从 config/sites/ 目录拼出与旧 sites.json 相同结构的字典（备份格式保持不变，旧备份仍可导入）。"""
    from app.services.config.site_store import load_sites_dict

    return load_sites_dict(config_engine.sites_dir)


def _build_settings_backup_bundle() -> Dict[str, Any]:
    sites_local_file = Path(config_engine.local_sites_file)
    commands_file = Path(ConfigConstants.COMMANDS_FILE)
    commands_local_file = Path(ConfigConstants.COMMANDS_LOCAL_FILE)
    browser_config_file = Path("config/browser_config.json")
    extractors_file = Path(extractor_manager.CONFIG_FILE)

    # S13：备份文件会被下载、转存、分享，默认绝不包含 .env 中的令牌/密钥/含凭据的代理地址。
    # 被剔除的键只记录名字，导入时这些键保持目标机器现值不变。
    safe_env, redacted_env_keys = redact_env_for_backup(_load_env_config_from_file())

    return {
        "bundle_version": 1,
        "secrets_redacted": True,
        "redacted_env_keys": redacted_env_keys,
        "exported_at": int(time.time()),
        "app_version": APP_VERSION,
        "files": {
            "sites": _export_sites_config(),
            "sites_local": _read_json_file(sites_local_file, {"default_presets": {}}),
            "commands": _read_json_file(commands_file, {"commands": []}),
            "commands_local": _read_json_file(commands_local_file, {"commands": []}),
            "browser_constants": _read_json_file(browser_config_file, {}),
            "extractors": _read_json_file(extractors_file, extractor_manager.export_config()),
            "update_preserve": load_update_preserve_settings(),
            "env": safe_env,
        },
    }


def _strip_redacted_env_import(env_payload: Dict[str, Any]) -> Dict[str, Any]:
    """导入备份时丢弃脱敏占位符，避免把 "***" 之类写回 .env 覆盖真实令牌。"""
    cleaned: Dict[str, Any] = {}
    for key, value in env_payload.items():
        if isinstance(value, str) and value.strip().lower() in _REDACTED_PLACEHOLDERS:
            continue
        cleaned[key] = value
    return cleaned


def _validate_settings_backup_files(files: Dict[str, Any]) -> Dict[str, Any]:
    """Validate all import sections before any target file is modified."""
    validated: Dict[str, Any] = {}

    if "sites" in files:
        if not isinstance(files["sites"], dict):
            raise HTTPException(status_code=400, detail="sites 配置格式无效")
        validated["sites"] = files["sites"]

    if "sites_local" in files:
        if not isinstance(files["sites_local"], dict):
            raise HTTPException(status_code=400, detail="sites_local 配置格式无效")
        validated["sites_local"] = files["sites_local"]

    if "commands" in files:
        commands_payload = files["commands"]
        if not isinstance(commands_payload, (dict, list)):
            raise HTTPException(status_code=400, detail="commands 配置格式无效")
        validated["commands"] = commands_payload

    if "commands_local" in files:
        commands_local_payload = files["commands_local"]
        if not isinstance(commands_local_payload, dict):
            raise HTTPException(status_code=400, detail="commands_local 配置格式无效")
        validated["commands_local"] = commands_local_payload

    if "browser_constants" in files:
        if not isinstance(files["browser_constants"], dict):
            raise HTTPException(status_code=400, detail="browser_constants 配置格式无效")
        validated["browser_constants"] = files["browser_constants"]

    if "extractors" in files:
        extractors_payload = files["extractors"]
        if not isinstance(extractors_payload, dict):
            raise HTTPException(status_code=400, detail="extractors 配置格式无效")
        if "extractors" not in extractors_payload:
            raise HTTPException(status_code=400, detail="extractors 配置格式无效：缺少 extractors 字段")
        extractors_config = extractors_payload.get("extractors")
        if not isinstance(extractors_config, dict):
            raise HTTPException(status_code=400, detail="extractors 配置格式无效：extractors 必须是对象")
        invalid_entries = [
            extractor_id
            for extractor_id, extractor_config in extractors_config.items()
            if not isinstance(extractor_config, dict)
        ]
        if invalid_entries:
            raise HTTPException(
                status_code=400,
                detail="extractors 配置格式无效：提取器配置项必须是对象",
            )
        validated["extractors"] = extractors_payload

    if "update_preserve" in files:
        preserve_payload = files["update_preserve"]
        if isinstance(preserve_payload, dict):
            selected_patterns = preserve_payload.get("selected_patterns", [])
        else:
            selected_patterns = preserve_payload
        if selected_patterns is None:
            selected_patterns = []
        if not isinstance(selected_patterns, list):
            raise HTTPException(status_code=400, detail="update_preserve 配置格式无效")
        validated["update_preserve"] = selected_patterns

    if "env" in files:
        if not isinstance(files["env"], dict):
            raise HTTPException(status_code=400, detail="env 配置格式无效")
        env_payload = _strip_redacted_env_import(files["env"])
        # 与 POST /api/settings/env 同一套校验：拦截换行注入、非法键名、认证自锁/公开无认证
        _validate_env_config_payload(env_payload)
        validated["env"] = env_payload

    return validated


@router.get("/api/settings/env")
async def get_env_config(authenticated: bool = Depends(verify_auth)):
    """读取 .env 文件配置"""
    try:
        # 修复：config 结构保持不变（前端只读 data.config）；
        # 另附只读 effective 字段暴露 DASHBOARD_AUTH_* 的实际生效值，前端不会回写它。
        return {
            "config": _load_env_config_from_file(),
            "effective": {
                "DASHBOARD_AUTH_ENABLED": AppConfig.is_dashboard_auth_enabled(),
                "DASHBOARD_AUTH_TOKEN_SET": bool(AppConfig.get_dashboard_auth_token()),
            },
        }
    except Exception as e:
        logger.error(f"读取环境配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取失败: {e!s}")


def _env_value_is_true(value: Any) -> bool:
    """与后端 AppConfig._env_bool 一致的真值白名单语义。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _validate_env_config_payload(new_config: Dict[str, Any]) -> None:
    """写入 .env 前校验：拦截非法键名、换行注入，以及会把控制面板锁死的认证组合。"""
    # 修复：键名/值零校验时，值里带 \n 就能往 .env 注入额外行
    # （例如注入 DASHBOARD_AUTH_ENABLED=false 关掉认证）。
    #
    # 注意：前端会把 GET 读到的整份 .env 原样回传，其中可能包含 http_proxy、no_proxy
    # 这类小写的存量键。对这些"读进来又原样送回来、值也没变"的键做键名校验会让用户
    # 完全无法保存任何设置，因此键名规则只对新增/变更的键生效。
    try:
        existing_env: Dict[str, Any] = _load_env_config_from_file()
    except Exception:
        existing_env = {}

    changed_keys: list = []
    for key, value in new_config.items():
        serialized = _serialize_env_value(value)
        if "\r" in serialized or "\n" in serialized:
            raise HTTPException(status_code=400, detail=f"配置值不能包含换行: {key}")
        unchanged_existing = (
            key in existing_env
            and _serialize_env_value(existing_env.get(key)) == serialized
        )
        if unchanged_existing:
            continue
        if not isinstance(key, str) or not re.match(r"^[A-Z][A-Z0-9_]*$", key):
            raise HTTPException(status_code=400, detail=f"非法的配置键名: {key}")
        changed_keys.append(key)

    # R2-4：按环境变量登记表校验类型、范围与枚举取值（只校验新增/修改的键，存量值原样放行）
    from app.core.settings_registry import validate_env_values

    value_errors = validate_env_values(
        {key: _serialize_env_value(new_config[key]) for key in changed_keys}
    )
    if value_errors:
        raise HTTPException(status_code=400, detail="配置值不合法：" + "；".join(value_errors.values()))

    # 修复：认证自锁保护。开启认证却留空令牌时，deps.py 会对每个 /api/* 抛 500，
    # 控制面板此后打不开，只能手改 .env 恢复。按合并后的最终生效值判断。
    merged: Dict[str, Any] = {**existing_env, **new_config}

    auth_enabled = _env_value_is_true(merged.get("AUTH_ENABLED"))
    auth_token = str(merged.get("AUTH_TOKEN") or "").strip()
    if auth_enabled and not auth_token:
        raise HTTPException(status_code=400, detail="启用 API 认证时必须设置 AUTH_TOKEN")

    # DASHBOARD_AUTH_ENABLED 未设置/为空时沿用 AUTH_ENABLED
    dashboard_raw = merged.get("DASHBOARD_AUTH_ENABLED")
    if dashboard_raw is None or (not isinstance(dashboard_raw, bool) and str(dashboard_raw).strip() == ""):
        dashboard_enabled = auth_enabled
    else:
        dashboard_enabled = _env_value_is_true(dashboard_raw)

    dashboard_token = str(merged.get("DASHBOARD_AUTH_TOKEN") or "").strip()
    if dashboard_enabled and not dashboard_token and not auth_token:
        raise HTTPException(
            status_code=400,
            detail="启用控制面板认证时必须设置 DASHBOARD_AUTH_TOKEN 或 AUTH_TOKEN"
        )

    # H1 / S1：认证开关必须是合法布尔值；公开监听必须同时启用认证。
    # 与启动期检查 startup_security_errors 同一套规则，避免保存后服务起不来。
    merged_env = {str(k): _serialize_env_value(v) for k, v in merged.items()}
    security_errors = startup_security_errors(env=merged_env)
    if security_errors:
        raise HTTPException(status_code=400, detail="；".join(security_errors))


@router.post("/api/settings/env")
async def save_env_config(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """保存 .env 配置"""
    try:
        data = await _read_json_object_or_400(request)
        new_config = data.get("config", {})
        if not isinstance(new_config, dict):
            raise HTTPException(status_code=400, detail="环境配置必须是 JSON 对象")
        _validate_env_config_payload(new_config)
        _write_env_config_file(new_config)

        logger.info(f"环境配置已保存: {len(new_config)} 项，准备触发重启...")
        _schedule_service_restart(1.0)

        return {
            "status": "success",
            "message": "环境配置已保存，服务将在 1 秒后重启...",
            "updated_count": len(new_config),
            "will_restart": True
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存环境配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {e!s}")


@router.get("/api/settings/backup")
async def export_settings_backup(authenticated: bool = Depends(verify_sensitive_admin_auth)):
    """导出完整配置备份。"""
    try:
        return _build_settings_backup_bundle()
    except Exception as e:
        logger.error(f"导出配置备份失败: {e}")
        raise HTTPException(status_code=500, detail=f"导出失败: {e!s}")


@router.post("/api/settings/backup")
async def import_settings_backup(
    request: Request,
    authenticated: bool = Depends(verify_sensitive_admin_auth)
):
    """导入完整配置备份。"""
    try:
        data = await _read_json_object_or_400(request)
        allowed_sections = {
            "sites",
            "sites_local",
            "commands",
            "commands_local",
            "browser_constants",
            "extractors",
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

        files = _validate_settings_backup_files(files)
        imported_sections = []
        restart_required = False
        file_snapshots: Dict[Path, Optional[bytes]] = {}

        def remember_file(path: Path) -> Path:
            target = Path(path)
            if target not in file_snapshots:
                file_snapshots[target] = _snapshot_file(target)
            return target

        def write_import_json(path: Path, payload: Any) -> None:
            _write_json_file(remember_file(path), payload)

        def write_import_env(payload: Dict[str, Any]) -> None:
            remember_file(Path(".env"))
            _write_env_config_file(payload)

        def reload_imported_runtime() -> None:
            if "sites" in files or "sites_local" in files:
                config_engine.reload_config()

            if "commands" in files or "commands_local" in files:
                command_engine._refresh_commands_if_changed(force=True)

            if "browser_constants" in files:
                try:
                    from app.core.config import BrowserConstants
                    if hasattr(BrowserConstants, "reload"):
                        BrowserConstants.reload()
                except Exception as reload_error:
                    logger.warning(f"导入后热重载浏览器常量失败: {reload_error}")

            if "extractors" in files:
                extractor_manager.reload_config()

        try:
            if "sites" in files:
                # R1-2：备份里的 sites 是旧 sites.json 结构；按“整体替换”语义写回 config/sites/ 目录。
                # 先记住目录里现有的和即将写入的每个文件，失败时由 _restore_file_snapshots 整体回滚。
                store = config_engine.site_store
                sites_to_save = dict(files["sites"])
                if "_global" not in sites_to_save:
                    sites_to_save["_global"] = config_engine.global_config.to_dict()
                for site_path in store.site_files():
                    remember_file(site_path)
                for site_key in sites_to_save:
                    remember_file(store.file_for(str(site_key)))
                with config_engine._io_lock:
                    store.save(sites_to_save)
                imported_sections.append("sites")

            if "sites_local" in files:
                write_import_json(Path(config_engine.local_sites_file), files["sites_local"])
                imported_sections.append("sites_local")

            if "commands" in files:
                write_import_json(Path(ConfigConstants.COMMANDS_FILE), files["commands"])
                imported_sections.append("commands")

            if "commands_local" in files:
                write_import_json(Path(ConfigConstants.COMMANDS_LOCAL_FILE), files["commands_local"])
                imported_sections.append("commands_local")

            if "browser_constants" in files:
                write_import_json(Path("config/browser_config.json"), files["browser_constants"])
                imported_sections.append("browser_constants")

            if "extractors" in files:
                write_import_json(Path(extractor_manager.CONFIG_FILE), files["extractors"])
                imported_sections.append("extractors")

            if "update_preserve" in files:
                remember_file(Path(CONFIG_DIR) / "update_settings.json")
                save_update_preserve_settings(files["update_preserve"])
                imported_sections.append("update_preserve")

            if "env" in files:
                write_import_env(files["env"])
                imported_sections.append("env")
                restart_required = True
        except Exception:
            _restore_file_snapshots(file_snapshots)
            raise

        try:
            reload_imported_runtime()
        except Exception:
            _restore_file_snapshots(file_snapshots)
            try:
                reload_imported_runtime()
            except Exception as rollback_reload_error:
                logger.warning(f"导入失败回滚后热重载运行时状态失败: {rollback_reload_error}")
            raise

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
        raise HTTPException(status_code=500, detail=f"导入失败: {e!s}")


@router.get("/api/settings/browser-constants")
async def get_browser_constants(authenticated: bool = Depends(verify_auth)):
    """读取浏览器常量配置"""
    try:
        config_path = Path("config/browser_config.json")
        config = _read_json_file(config_path, DEFAULT_BROWSER_CONSTANTS)
        return {"config": config}

    except Exception as e:
        logger.error(f"读取浏览器常量失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取失败: {e!s}")


@router.post("/api/settings/browser-constants")
async def save_browser_constants(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """保存浏览器常量配置"""
    try:
        data = await _read_json_object_or_400(request)
        config = data.get("config", {})
        if not isinstance(config, dict):
            raise HTTPException(status_code=400, detail="浏览器常量配置必须是 JSON 对象")
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
                        allocation_mode=tab_pool_config.get("allocation_mode"),
                        excluded_urls=tab_pool_config.get("excluded_urls"),
                        preserve_error_tabs=tab_pool_config.get("preserve_error_tabs"),
                        route_groups=tab_pool_config.get("route_groups"),
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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存浏览器常量失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {e!s}")


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
        raise HTTPException(status_code=500, detail=f"读取失败: {e!s}")


@router.post("/api/settings/update-preserve")
async def save_update_preserve(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """保存更新白名单配置。"""
    try:
        data = await _read_json_object_or_400(request)
        selected_patterns = data.get("selected_patterns", [])
        if not isinstance(selected_patterns, list):
            raise HTTPException(status_code=400, detail="更新白名单必须是数组")
        result = save_update_preserve_settings(selected_patterns)
        logger.info(f"更新白名单已保存: {len(result.get('selected_patterns', []))} 项")
        return {
            "status": "success",
            "message": "更新白名单已保存，下次更新时生效",
            "selected_patterns": result.get("selected_patterns", []),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存更新白名单失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {e!s}")
