"""应用启动引导：在导入应用代码之前完成环境准备。

``uv run python main.py`` 启动时，``main.py`` 会在导入 ``app`` 之前调用
:func:`bootstrap_runtime`，顺序与原 ``start.py`` 一致：

1. 设置 UTF-8 环境并加载 ``.env``；
2. 校验当前解释器来自项目 ``.venv``（uv 管理的唯一环境）；
3. 按 ``AUTO_UPDATE_ENABLED`` 检查并应用更新，更新后同步 uv 环境；
4. 以子进程应用 DrissionPage 补丁（保证随后导入的是补丁后的源码）；
5. 按 ``PROFILE_CLEAN_ENABLED`` 清理浏览器缓存；
6. 按 ``BROWSER_AUTO_LAUNCH`` 拉起受控 Chromium。

本模块只依赖标准库、python-dotenv 和项目内的独立脚本，避免在补丁生效前
导入 DrissionPage。
"""

from __future__ import annotations

import json
import tempfile
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_VENV = PROJECT_ROOT / ".venv"
PYPROJECT_FILE = PROJECT_ROOT / "pyproject.toml"
UV_LOCK_FILE = PROJECT_ROOT / "uv.lock"

_PYTHON_PROXY_SCHEMES = frozenset({"http", "https", "socks4", "socks4a", "socks5", "socks5h"})


def _log(message: str = "") -> None:
    print(message, flush=True)


def _section(title: str) -> None:
    _log(f"[STEP] {title}")
    _log("----------------------------------------")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def normalize_python_proxy_url(value: str) -> str:
    """把浏览器代理地址转换为 requests/urllib 兼容的代理 URL。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"

    try:
        parsed = urlsplit(raw)
        scheme = str(parsed.scheme or "").lower()
        if scheme not in _PYTHON_PROXY_SCHEMES or not parsed.hostname:
            return ""
        # 访问 .port 会校验 :not-a-port 这类非法值
        _ = parsed.port
    except (TypeError, ValueError):
        return ""

    # requests 必须让 SOCKS 代理负责 DNS：socks5 会在本地解析，重新引入 fake-DNS 问题。
    if scheme == "socks5":
        scheme = "socks5h"

    return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _replace_env_value(env, key: str, value: str) -> None:
    """不区分大小写地替换环境变量。"""
    for existing_key in tuple(env):
        if existing_key.lower() == key.lower():
            env.pop(existing_key, None)
    env[key] = value


def _merged_no_proxy(*values: str) -> str:
    entries = []
    seen = set()
    for raw_value in values:
        for entry in str(raw_value or "").split(","):
            normalized = entry.strip()
            key = normalized.lower()
            if normalized and key not in seen:
                seen.add(key)
                entries.append(normalized)
    return ",".join(entries)


def apply_proxy_environment() -> None:
    """PROXY_ENABLED=true 时把代理写入进程环境，供 requests/urllib/httpx 使用。

    浏览器由 ``browser_launcher`` 通过 ``--proxy-server`` 单独配置；两者共用
    ``PROXY_ADDRESS`` / ``PROXY_BYPASS``。
    """
    if not _env_flag("PROXY_ENABLED"):
        return
    proxy_url = normalize_python_proxy_url(os.getenv("PROXY_ADDRESS", ""))
    if not proxy_url:
        return

    _replace_env_value(os.environ, "HTTP_PROXY", proxy_url)
    _replace_env_value(os.environ, "HTTPS_PROXY", proxy_url)

    existing_no_proxy = ",".join(
        str(value or "")
        for key, value in os.environ.items()
        if key.lower() == "no_proxy"
    )
    bypass = _merged_no_proxy(existing_no_proxy, os.getenv("PROXY_BYPASS", ""))
    if bypass:
        _replace_env_value(os.environ, "NO_PROXY", bypass)


def _apply_utf8_defaults() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _load_env() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        _log("[WARN] 未找到 .env 文件，使用默认配置；可复制 .env.example 为 .env 后修改")
        return
    try:
        from dotenv import load_dotenv

        override = _env_flag("UWAPI_DOTENV_OVERRIDE", False)
        load_dotenv(dotenv_path=env_file, override=override, encoding="utf-8-sig")
        _log(f"[OK] 已加载 {env_file.name}")
    except Exception as exc:  # broad-except: 环境文件损坏时继续使用系统环境变量
        _log(f"[WARN] 读取 .env 失败: {exc}")


def _preflight_environment() -> None:
    """要求使用项目 .venv：环境只由 uv 管理。"""
    try:
        in_project_venv = Path(sys.prefix).resolve() == PROJECT_VENV.resolve()
    except OSError:
        in_project_venv = False
    if in_project_venv:
        _log(f"[OK] uv 环境: {sys.prefix}")
        return

    if PROJECT_VENV.exists():
        _log("[ERROR] 检测到项目 .venv，但当前 Python 不是它：")
        _log(f"        当前解释器: {sys.executable}")
        _log("        请使用 uv 启动，不要直接调用系统 Python：")
        _log("            uv run python main.py")
        raise SystemExit(1)

    _log("[ERROR] 未找到项目 .venv，依赖尚未安装。")
    _log("        请先执行：")
    _log("            uv sync")
    _log("            uv run python main.py")
    raise SystemExit(1)


def _find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    names = ("uv.exe", "uv") if os.name == "nt" else ("uv",)
    candidates = [
        Path.home() / ".local" / "bin" / name for name in names
    ] + [
        Path.home() / ".cargo" / "bin" / name for name in names
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def sync_runtime_environment() -> bool:
    """按 ``uv.lock`` 安装运行依赖；用于自动更新后刷新环境。"""
    uv_executable = _find_uv()
    if not uv_executable:
        return False

    cmd = [uv_executable, "sync", "--no-dev"]
    if UV_LOCK_FILE.exists():
        cmd.append("--locked")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=False)
    if result.returncode != 0 and "--locked" in cmd:
        _log("[WARN] uv sync --locked 失败，尝试按 pyproject.toml 重新解析...")
        result = subprocess.run(
            [uv_executable, "sync", "--no-dev"], cwd=str(PROJECT_ROOT), check=False
        )
    return result.returncode == 0


def _run_auto_update() -> bool:
    """按 AUTO_UPDATE_ENABLED 应用更新；返回是否真的更新了文件。"""
    os.environ.pop("UWAPI_UPDATE_CHECK_RESULT", None)
    if not _env_flag("AUTO_UPDATE_ENABLED", True):
        _log("[INFO] 自动更新已禁用（AUTO_UPDATE_ENABLED=false）")
        _log("       服务启动后仍会检查新版本并在控制面板提示")
        _log()
        return False

    updater = PROJECT_ROOT / "updater.py"
    if not updater.exists():
        _log("[WARN] 未找到 updater.py，跳过自动更新")
        _log()
        return False

    _section("自动更新")
    _log("[INFO] 检查 GitHub 最新版本...")
    # 文件句柄在子进程启动前关闭，兼容 Windows 文件锁。
    with tempfile.TemporaryDirectory(prefix="uwapi-update-") as temp_dir:
        result_file = Path(temp_dir) / "result.json"
        env = dict(os.environ, UWAPI_UPDATE_CHECK_RESULT_FILE=str(result_file))
        result = subprocess.run(
            [sys.executable, str(updater)], cwd=str(PROJECT_ROOT), check=False, env=env
        )
        try:
            payload = json.loads(result_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                os.environ["UWAPI_UPDATE_CHECK_RESULT"] = json.dumps(payload)
        except (OSError, ValueError):
            pass
    applied = result.returncode == 0
    if applied:
        _log("[INFO] 更新已应用，正在同步 uv 环境...")
        if not sync_runtime_environment():
            _log("[ERROR] 更新后无法自动同步依赖。")
            _log("        请手动执行 `uv sync` 后重新启动：uv run python main.py")
            raise SystemExit(1)
        _log("[OK] uv 环境已同步")
    else:
        _log("[WARN] 本次未应用更新，继续启动")
    _log()
    return applied


def apply_drissionpage_patch() -> bool:
    """在独立进程中应用补丁，避免当前进程先导入未打补丁的 DrissionPage。"""
    _section("应用 DrissionPage 补丁")
    patch_script = PROJECT_ROOT / "patch_drissionpage.py"
    if not patch_script.exists():
        _log("[WARN] 未找到 patch_drissionpage.py，跳过补丁")
        _log()
        return False

    result = subprocess.run(
        [sys.executable, str(patch_script)], cwd=str(PROJECT_ROOT), check=False
    )
    if result.returncode != 0:
        _log("[WARN] 补丁应用失败，网络监听模式可能触发 CF 检测")
        _log("       项目仍可正常运行（DOM 模式不受影响）")
        _log()
        return False
    _log()
    return True


def clean_browser_profile() -> None:
    _section("浏览器配置瘦身")
    if not _env_flag("PROFILE_CLEAN_ENABLED", False):
        _log("[INFO] 已禁用配置瘦身（PROFILE_CLEAN_ENABLED=false）")
        _log()
        return

    from app.runtime.browser_launcher import resolve_profile_dir

    try:
        from clean_profile import safe_slim_profile

        safe_slim_profile(resolve_profile_dir())
    except Exception as exc:  # broad-except: 清理是可选优化，失败不应阻止启动
        _log(f"[WARN] 配置瘦身失败: {exc}")
    _log()


def launch_controlled_browser() -> bool:
    from app.runtime.browser_launcher import BrowserLaunchError, ensure_controlled_browser

    try:
        return ensure_controlled_browser()
    except BrowserLaunchError as exc:
        _log(f"[ERROR] {exc}")
        raise SystemExit(1) from exc


def bootstrap_runtime() -> None:
    """完整启动引导；仅在 ``main.py`` 作为脚本运行时调用。"""
    _apply_utf8_defaults()
    _load_env()
    apply_proxy_environment()

    _log()
    _log("========================================")
    _log("   Universal Web-to-API")
    _log("========================================")
    _log()

    _section("检查 uv 环境")
    _preflight_environment()
    _log()

    _run_auto_update()
    apply_drissionpage_patch()
    clean_browser_profile()
    launch_controlled_browser()

    _log(f"[OK] 运行时准备完成，启动服务: http://{os.getenv('APP_HOST', '127.0.0.1')}:{os.getenv('APP_PORT', '8199')}")
    _log()


__all__ = [
    "apply_proxy_environment",
    "bootstrap_runtime",
    "normalize_python_proxy_url",
    "apply_drissionpage_patch",
    "clean_browser_profile",
    "launch_controlled_browser",
    "sync_runtime_environment",
]
