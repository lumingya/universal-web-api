"""
app/core/config_parts/env_config.py - 环境变量与基础文件工具模块
"""
import os
import json
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"


def atomic_write_json(path: str | Path, payload: Any) -> None:
    """Atomically write JSON to disk using a same-directory temporary file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    tmp_path: Optional[Path] = None

    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=str(target.parent),
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            fd = None
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        _replace_file_with_retry(tmp_path, target)
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


def _replace_file_with_retry(source: str | Path, dest: str | Path) -> None:
    """Replace a file, retrying transient Windows sharing violations."""
    source_path = Path(source)
    dest_path = Path(dest)
    attempts = 3 if os.name == "nt" else 1
    delay = 0.02
    for attempt in range(attempts):
        try:
            os.replace(source_path, dest_path)
            return
        except PermissionError:
            if attempt >= attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2


class classproperty:
    """Allow config access via both `AppConfig.X` and `app_config.X`."""

    def __init__(self, fget):
        self.fget = fget

    def __get__(self, obj, owner=None):
        return self.fget(owner)


def load_dotenv(env_file: str = ".env", override: bool = False):
    """
    手动加载 .env 文件（不依赖 python-dotenv）
    """
    env_path = Path(env_file)
    if not env_path.exists():
        return
    
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, _, value = line.partition('=')
                    key = key.strip()
                    value = value.strip()
                    if (value.startswith('"') and value.endswith('"')) or \
                       (value.startswith("'") and value.endswith("'")):
                        value = value[1:-1]
                    if key:
                        if override or key not in os.environ:
                            os.environ[key] = value
    except Exception as e:
        print(f"[Config] 加载 .env 失败: {e}")


load_dotenv(override=os.getenv("UWAPI_DOTENV_OVERRIDE", "").lower() in ("1", "true", "yes", "on"))


class AppConfig:
    """应用配置（从环境变量读取）"""

    @staticmethod
    def _env_bool(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None or str(value).strip() == "":
            return bool(default)
        return str(value).strip().lower() in ("true", "1", "yes", "on")

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        """安全读取整数环境变量：空值/非法值一律回落默认值，避免服务启动期崩溃。"""
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return int(default)
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        """安全读取浮点环境变量：空值/非法值一律回落默认值，避免服务启动期崩溃。"""
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return float(default)
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            return float(default)

    # ===== 服务配置 =====
    @staticmethod
    def get_host() -> str:
        return os.getenv("APP_HOST", "127.0.0.1")
    
    @staticmethod
    def get_port() -> int:
        return AppConfig._env_int("APP_PORT", 8199)
    
    @staticmethod
    def is_debug() -> bool:
        return os.getenv("APP_DEBUG", "false").lower() in ("true", "1", "yes")
    
    @staticmethod
    def get_log_level() -> str:
        return os.getenv("LOG_LEVEL", "INFO").upper()

    # ===== 认证配置 =====
    @staticmethod
    def is_auth_enabled() -> bool:
        return AppConfig._env_bool("AUTH_ENABLED", False)

    @staticmethod
    def get_auth_token() -> str:
        return os.getenv("AUTH_TOKEN", "").strip()

    @staticmethod
    def is_dashboard_auth_enabled() -> bool:
        value = os.getenv("DASHBOARD_AUTH_ENABLED")
        if value is None or str(value).strip() == "":
            return AppConfig.is_auth_enabled()
        return AppConfig._env_bool("DASHBOARD_AUTH_ENABLED", False)

    @staticmethod
    def get_dashboard_auth_token() -> str:
        token = os.getenv("DASHBOARD_AUTH_TOKEN", "").strip()
        return token or AppConfig.get_auth_token()
    
    # ===== CORS 配置 =====
    @staticmethod
    def is_cors_enabled() -> bool:
        return os.getenv("CORS_ENABLED", "true").lower() in ("true", "1", "yes")
    
    @staticmethod
    def get_cors_origins() -> List[str]:
        origins = os.getenv("CORS_ORIGINS", "*")
        if origins == "*":
            return ["*"]
        return [o.strip() for o in origins.split(",") if o.strip()]
    
    # ===== 浏览器配置 =====
    @staticmethod
    def get_browser_port() -> int:
        return AppConfig._env_int("BROWSER_PORT", 9222)
    
    # ===== Dashboard 配置 =====
    @staticmethod
    def is_dashboard_enabled() -> bool:
        return os.getenv("DASHBOARD_ENABLED", "true").lower() in ("true", "1", "yes")
    
    @staticmethod
    def get_dashboard_file() -> str:
        return os.getenv("DASHBOARD_FILE", "static/index.html")

    # ===== 定时服务重启守护 =====
    @staticmethod
    def is_scheduled_restart_enabled() -> bool:
        return AppConfig._env_bool("SCHEDULED_RESTART_ENABLED", False)

    @staticmethod
    def get_scheduled_restart_interval_seconds() -> int:
        return max(60, AppConfig._env_int("SCHEDULED_RESTART_INTERVAL_SECONDS", 10800))

    @staticmethod
    def get_scheduled_restart_drain_timeout_seconds() -> int:
        return max(0, AppConfig._env_int("SCHEDULED_RESTART_DRAIN_TIMEOUT_SECONDS", 1800))

    @staticmethod
    def get_scheduled_restart_tab_state_policy() -> str:
        """Reserved for future tab-state cleanup; preserve is currently the only policy."""
        return os.getenv("SCHEDULED_RESTART_TAB_STATE_POLICY", "preserve").strip().lower() or "preserve"
    
    # ===== AI 分析配置 =====
    @staticmethod
    def get_helper_api_key() -> str:
        return os.getenv("HELPER_API_KEY", "")
    
    @staticmethod
    def get_helper_base_url() -> str:
        return os.getenv("HELPER_BASE_URL", "")
    
    @staticmethod
    def get_helper_model() -> str:
        return os.getenv("HELPER_MODEL", "gpt-4")
        
    @staticmethod
    def get_helper_api_provider() -> str:
        return os.getenv("HELPER_API_PROVIDER", "auto").lower()
    
    @staticmethod
    def get_max_html_chars() -> int:
        return AppConfig._env_int("MAX_HTML_CHARS", 120000)

    @staticmethod
    def get_canvas_image_max_size() -> int:
        try:
            value = AppConfig._env_int("CANVAS_IMAGE_MAX_SIZE", 1024)
        except Exception:
            value = 1024
        return max(1, value)

    # ===== 错误标签页 AI 恢复配置 =====
    @staticmethod
    def is_tab_recovery_enabled() -> bool:
        return AppConfig._env_bool("TAB_RECOVERY_ENABLED", False)

    @staticmethod
    def get_tab_recovery_api_url() -> str:
        return os.getenv("TAB_RECOVERY_API_URL", "").strip()

    @staticmethod
    def get_tab_recovery_api_key() -> str:
        return os.getenv("TAB_RECOVERY_API_KEY", "").strip()

    @staticmethod
    def get_tab_recovery_model() -> str:
        return os.getenv("TAB_RECOVERY_MODEL", "gpt-4o").strip() or "gpt-4o"

    @staticmethod
    def get_tab_recovery_max_attempts() -> int:
        try:
            value = AppConfig._env_int("TAB_RECOVERY_MAX_ATTEMPTS", 1)
        except Exception:
            value = 1
        return max(0, value)

    @staticmethod
    def get_tab_recovery_timeout_sec() -> float:
        try:
            value = AppConfig._env_float("TAB_RECOVERY_TIMEOUT_SEC", 120.0)
        except Exception:
            value = 120.0
        return max(1.0, value)

    @staticmethod
    def get_tab_recovery_worker_exit_wait_sec() -> float:
        try:
            value = AppConfig._env_float("TAB_RECOVERY_WORKER_EXIT_WAIT_SEC", 600.0)
        except Exception:
            value = 600.0
        return max(0.0, value)

    @staticmethod
    def is_tab_recovery_refresh_on_unknown() -> bool:
        return AppConfig._env_bool("TAB_RECOVERY_REFRESH_ON_UNKNOWN", True)

    # ===== 配置文件路径 =====
    @staticmethod
    def get_sites_config_file() -> str:
        return os.getenv("SITES_CONFIG_FILE", "config/sites.json")

    # ===== 便捷属性（支持类/实例两种访问方式）=====
    @classproperty
    def HOST(cls) -> str:
        return cls.get_host()

    @classproperty
    def PORT(cls) -> int:
        return cls.get_port()

    @classproperty
    def DEBUG(cls) -> bool:
        return cls.is_debug()

    @classproperty
    def LOG_LEVEL(cls) -> str:
        return cls.get_log_level()

    @classproperty
    def AUTH_TOKEN(cls) -> str:
        return cls.get_auth_token()

    @classproperty
    def DASHBOARD_AUTH_TOKEN(cls) -> str:
        return cls.get_dashboard_auth_token()


# 创建全局配置实例
app_config = AppConfig()
