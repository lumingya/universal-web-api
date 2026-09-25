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

# 修复 S1/H1：布尔环境变量必须显式解析。
# 旧实现用「值是否落在真值白名单」判断，于是 `.env.example` 里的
# `AUTH_ENABLED=your-secret-here` 会被静默判成 false，把认证整个关掉。
_TRUE_LITERALS = frozenset({"true", "1", "yes", "on", "y", "t"})
_FALSE_LITERALS = frozenset({"false", "0", "no", "off", "n", "f"})

class InsecureStartupConfigError(ValueError):
    """启动期安全配置错误：不允许静默回退，必须让部署者看到并修正。"""


def parse_bool_literal(value: Any) -> Optional[bool]:
    """严格解析布尔字面量；无法识别时返回 None（由调用方决定 fail-closed 策略）。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "":
        return None
    if text in _TRUE_LITERALS:
        return True
    if text in _FALSE_LITERALS:
        return False
    return None


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
        parsed = parse_bool_literal(value)
        if parsed is None:
            return bool(default)
        return parsed

    @staticmethod
    def _env_bool_secure(name: str, default: bool = False) -> bool:
        """安全开关专用：值无法解析为布尔时 fail-closed（视为启用保护），而不是静默关闭。

        修复 S1/H1：`AUTH_ENABLED=your-secret-here` 过去等价于 `false`，
        复制 `.env.example` 的部署会在自以为开了认证的情况下完全裸奔。
        现在这种值会被当成「开启」，并在启动校验里报错要求修正。
        """
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return bool(default)
        parsed = parse_bool_literal(raw)
        if parsed is None:
            return True
        return parsed

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
        return AppConfig._env_bool_secure("AUTH_ENABLED", False)

    @staticmethod
    def get_auth_token() -> str:
        return os.getenv("AUTH_TOKEN", "").strip()

    @staticmethod
    def is_dashboard_auth_enabled() -> bool:
        value = os.getenv("DASHBOARD_AUTH_ENABLED")
        if value is None or str(value).strip() == "":
            return AppConfig.is_auth_enabled()
        return AppConfig._env_bool_secure("DASHBOARD_AUTH_ENABLED", False)

    @staticmethod
    def get_dashboard_auth_token() -> str:
        token = os.getenv("DASHBOARD_AUTH_TOKEN", "").strip()
        return token or AppConfig.get_auth_token()
    
    # ===== CORS 配置 =====
    @staticmethod
    def is_cors_enabled() -> bool:
        return AppConfig._env_bool("CORS_ENABLED", True)

    @staticmethod
    def get_default_cors_origins() -> List[str]:
        """默认只放行本机同端口来源。

        修复 S1：默认 `*` 意味着任意第三方网页都能跨源读取管理接口。
        控制面板与 API 由同一个服务提供，属于同源请求，根本不需要 CORS 放行，
        因此把默认值收敛到回环来源，既安全又不影响开箱即用。
        """
        port = AppConfig.get_port()
        return [
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
            f"http://[::1]:{port}",
        ]

    @staticmethod
    def get_cors_origins() -> List[str]:
        raw = os.getenv("CORS_ORIGINS")
        if raw is None or str(raw).strip() == "":
            return AppConfig.get_default_cors_origins()
        origins = str(raw).strip()
        if origins == "*":
            return ["*"]
        parsed = [o.strip() for o in origins.split(",") if o.strip()]
        return parsed or AppConfig.get_default_cors_origins()

    # ===== 启动期安全校验（修复 S1 / H1）=====
    @staticmethod
    def is_loopback_bind(host: Optional[str] = None) -> bool:
        """服务是否只绑定在本机回环地址上。"""
        value = str(host if host is not None else AppConfig.get_host()).strip().lower()
        value = value.strip("[]")
        if not value:
            return False
        if value in ("127.0.0.1", "::1", "localhost"):
            return True
        return value.startswith("127.")

    @staticmethod
    def collect_security_config_errors() -> List[str]:
        """收集会导致服务在不安全状态下启动的配置错误。

        返回空列表表示通过。调用方（start.py / main.py 启动流程）应在非空时
        直接拒绝启动，而不是静默回退到「认证关闭 + CORS 全开」。
        """
        errors: List[str] = []

        # 1) 安全开关必须是合法布尔值，不能是 `your-secret-here` 之类的占位符
        for flag in ("AUTH_ENABLED", "DASHBOARD_AUTH_ENABLED", "CORS_ENABLED"):
            raw = os.getenv(flag)
            if raw is None or str(raw).strip() == "":
                continue
            if parse_bool_literal(raw) is None:
                errors.append(
                    f"{flag} 的值不是合法布尔量（当前为 {raw!r}）。"
                    f"请填写 true 或 false；令牌应写在对应的 *_TOKEN 变量里。"
                )

        public_bind = not AppConfig.is_loopback_bind()

        # 2) 对外绑定时必须配置管理令牌，否则控制面板等于对整个网络开放
        if public_bind:
            if AppConfig.is_dashboard_auth_enabled():
                if not AppConfig.get_dashboard_auth_token():
                    errors.append(
                        "DASHBOARD_AUTH_ENABLED=true 但 DASHBOARD_AUTH_TOKEN/AUTH_TOKEN 为空，"
                        "无法校验任何请求。请设置一个强令牌。"
                    )
            else:
                errors.append(
                    f"APP_HOST={AppConfig.get_host()} 会把服务暴露到本机以外，"
                    "但控制面板认证是关闭的。请设置 DASHBOARD_AUTH_ENABLED=true 与 "
                    "DASHBOARD_AUTH_TOKEN，或把 APP_HOST 改回 127.0.0.1。"
                )
            if AppConfig.is_auth_enabled() and not AppConfig.get_auth_token():
                errors.append(
                    "AUTH_ENABLED=true 但 AUTH_TOKEN 为空，对外服务接口无法校验请求。"
                )

        # 3) 通配 CORS 与对外绑定的组合，等于任何网页都能代用户操作本服务
        if public_bind and AppConfig.is_cors_enabled() and "*" in AppConfig.get_cors_origins():
            errors.append(
                "CORS_ORIGINS=* 与对外绑定同时开启：任意第三方网页都可跨源调用本服务。"
                "请把 CORS_ORIGINS 限定为确切来源，或设置 CORS_ENABLED=false。"
            )

        # 4) 修复 S2 / S3：把「在服务进程内执行任意代码」的能力与对外绑定组合起来，
        #    等于把远程代码执行直接挂到网络上。这两个开关本身是合法的运维功能，
        #    但必须与「只有可信主体能连上」这个前提绑定。
        for flag, description in (
            (
                "CMD_ALLOW_UNSAFE_PYTHON_COMMANDS",
                "命令引擎的 Python 脚本会以非沙箱模式执行（可访问完整 builtins 与浏览器对象）",
            ),
            (
                "PARSER_INSTALL_ENABLED",
                "运行时解析器安装会把任意源码写入 app/core/parsers/ 并立即 import",
            ),
        ):
            if public_bind and parse_bool_literal(os.getenv(flag)) is True:
                errors.append(
                    f"{flag}=true 与对外绑定（APP_HOST={AppConfig.get_host()}）同时开启："
                    f"{description}，等于把远程代码执行暴露到网络上。"
                    f"请把 APP_HOST 改回 127.0.0.1，或关闭 {flag}。"
                )

        return errors

    @staticmethod
    def assert_secure_startup_config() -> None:
        """启动期强校验，不通过直接抛 ConfigurationError（fail-closed）。"""
        errors = AppConfig.collect_security_config_errors()
        if errors:
            details = "\n".join(f"  - {item}" for item in errors)
            raise InsecureStartupConfigError(
                "检测到不安全的启动配置，已拒绝启动：\n"
                f"{details}\n"
                "如确需在隔离网络中临时跳过此检查，可设置 UWA_ALLOW_INSECURE_STARTUP=true（不推荐）。"
            )

    @staticmethod
    def is_insecure_startup_allowed() -> bool:
        return AppConfig._env_bool("UWA_ALLOW_INSECURE_STARTUP", False)


    # ===== 浏览器配置 =====
    @staticmethod
    def get_browser_port() -> int:
        return AppConfig._env_int("BROWSER_PORT", 9222)

    @staticmethod
    def is_auto_open_browser_enabled() -> bool:
        return AppConfig._env_bool("AUTO_OPEN_BROWSER", True)
    
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
