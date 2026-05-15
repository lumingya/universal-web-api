#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台启动入口。

设计目标：
- 不替换现有 start.bat，确保 Windows 用户仍可继续使用原一键脚本
- 为 macOS / Linux 提供可运行的基础启动入口
- Windows 上也可以使用，但推荐继续使用 start.bat 以保留全部既有体验
"""

from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
import sys
import time
import venv
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / "venv"
REQ_HASH_FILE = VENV_DIR / ".req_hash"
REQUIREMENTS_FILE = PROJECT_DIR / "requirements.txt"
DEFAULT_PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _log(message: str) -> None:
    print(message, flush=True)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _ensure_venv() -> None:
    python_path = _venv_python()
    if python_path.exists():
        return

    _log("[STEP] 创建虚拟环境")
    builder = venv.EnvBuilder(with_pip=True)
    builder.create(str(VENV_DIR))


def _run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    kwargs = {
        "cwd": str(PROJECT_DIR),
        "check": check,
        "text": True,
        "env": env,
    }
    if capture:
        kwargs["capture_output"] = True
    return subprocess.run(cmd, **kwargs)


def _run_project_python(args: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return _run([str(_venv_python()), *args], check=check, capture=capture)


def _file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dependencies_ok() -> bool:
    try:
        result = _run_project_python(["check_deps.py"], check=False, capture=True)
    except Exception:
        return False
    return result.returncode == 0


def _ensure_dependencies() -> None:
    if not REQUIREMENTS_FILE.exists():
        raise FileNotFoundError("requirements.txt not found")

    current_hash = _file_md5(REQUIREMENTS_FILE)
    old_hash = ""
    if REQ_HASH_FILE.exists():
        try:
            old_hash = REQ_HASH_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            old_hash = ""

    if old_hash == current_hash and _dependencies_ok():
        _log("[OK] 依赖已是最新")
        return

    _log("[STEP] 安装依赖")
    pip_cmd = [str(_venv_python()), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)]
    result = _run(pip_cmd, check=False)
    if result.returncode != 0:
        mirror = os.getenv("PIP_MIRROR_URL", DEFAULT_PIP_MIRROR).strip() or DEFAULT_PIP_MIRROR
        _log(f"[WARN] 默认 PyPI 安装失败，尝试镜像: {mirror}")
        result = _run(pip_cmd + ["-i", mirror], check=False)
        if result.returncode != 0:
            raise RuntimeError("依赖安装失败")

    REQ_HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    REQ_HASH_FILE.write_text(current_hash, encoding="utf-8")


def _maybe_apply_patch() -> None:
    patch_script = PROJECT_DIR / "patch_drissionpage.py"
    if not patch_script.exists():
        return
    _log("[STEP] 应用 DrissionPage 补丁")
    _run_project_python([patch_script.name], check=False)


def _debug_port_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.4):
            return True
    except Exception:
        return False


def _platform_browser_candidates() -> list[str]:
    custom = str(os.getenv("BROWSER_PATH", "") or "").strip()
    if custom:
        return [custom]

    if sys.platform == "darwin":
        return [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
            "/Applications/Opera.app/Contents/MacOS/Opera",
        ]

    if sys.platform.startswith("linux"):
        names = [
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "microsoft-edge",
            "brave-browser",
            "vivaldi",
            "opera",
        ]
        resolved = []
        for name in names:
            path = shutil.which(name)
            if path:
                resolved.append(path)
        return resolved

    return []


def _resolve_browser_path() -> str:
    for candidate in _platform_browser_candidates():
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


def _launch_browser_if_needed() -> None:
    browser_port = int(os.getenv("BROWSER_PORT", "9222") or "9222")
    if _debug_port_ready(browser_port):
        _log(f"[OK] 复用已有浏览器调试端口: {browser_port}")
        return

    browser_path = _resolve_browser_path()
    if not browser_path:
        _log("[WARN] 未找到可自动启动的 Chromium 浏览器，请手动启动并开启远程调试端口")
        return

    profile_dir_raw = str(os.getenv("BROWSER_PROFILE_DIR", "") or "").strip()
    if profile_dir_raw:
        profile_dir = Path(profile_dir_raw)
        if not profile_dir.is_absolute():
            profile_dir = PROJECT_DIR / profile_dir
    else:
        profile_dir = PROJECT_DIR / "chrome_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    browser_args = [
        browser_path,
        f"--remote-debugging-port={browser_port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-backgrounding-occluded-windows",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-features=CalculateNativeWinOcclusion,AutomaticTabDiscarding,TabFreeze,IntensiveWakeUpThrottling",
        "about:blank",
    ]

    profile_name = str(os.getenv("BROWSER_PROFILE_NAME", "") or "").strip()
    if profile_name:
        browser_args.insert(-1, f"--profile-directory={profile_name}")

    if str(os.getenv("PROXY_ENABLED", "false")).strip().lower() == "true":
        proxy_address = str(os.getenv("PROXY_ADDRESS", "") or "").strip()
        proxy_bypass = str(os.getenv("PROXY_BYPASS", "") or "").strip()
        if proxy_address:
            browser_args.insert(-1, f"--proxy-server={proxy_address}")
            if proxy_bypass:
                browser_args.insert(-1, f"--proxy-bypass-list={proxy_bypass}")

    _log(f"[STEP] 启动浏览器: {browser_path}")
    subprocess.Popen(
        browser_args,
        cwd=str(PROJECT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(15):
        if _debug_port_ready(browser_port):
            _log(f"[OK] 浏览器调试端口就绪: {browser_port}")
            return
        time.sleep(1.0)

    _log(f"[WARN] 浏览器调试端口未在预期时间内就绪: {browser_port}")


def _run_service_loop() -> int:
    _log("[STEP] 启动服务")
    while True:
        completed = _run_project_python(["main.py"], check=False)
        if completed.returncode == 0:
            return 0
        if completed.returncode == 3:
            _log("[INFO] 检测到配置更新，正在重启服务...")
            time.sleep(2.0)
            continue
        _log(f"[WARN] 服务异常退出，3 秒后重启 (exit={completed.returncode})")
        time.sleep(3.0)


def main() -> int:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    _load_env_file(PROJECT_DIR / ".env")

    _log("[INFO] Windows 用户仍可继续使用 start.bat；start.py 用于跨平台启动。")
    _ensure_venv()
    _ensure_dependencies()
    _maybe_apply_patch()
    _launch_browser_if_needed()
    return _run_service_loop()


if __name__ == "__main__":
    raise SystemExit(main())
