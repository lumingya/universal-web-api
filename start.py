#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台启动入口。

设计目标：
- 与 start.bat 保持一致的核心启动行为
- 为 macOS / Linux 提供可运行的一键入口
- Windows 上也可使用，适合不想依赖批处理脚本的环境
"""

from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import venv
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / "venv"
REQ_HASH_FILE = VENV_DIR / ".req_hash"
REQUIREMENTS_FILE = PROJECT_DIR / "requirements.txt"
DEFAULT_PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
DEFAULT_GITHUB_REPO = "lumingya/universal-web-api"
DEFAULT_PYTHON_INSTALL_VERSION = "3.13.6"

ENV_DEFAULTS = {
    "APP_HOST": "127.0.0.1",
    "APP_PORT": "8199",
    "BROWSER_PORT": "9222",
    "AUTO_UPDATE_ENABLED": "true",
    "GITHUB_REPO": DEFAULT_GITHUB_REPO,
    "PYTHON_INSTALL_VERSION": DEFAULT_PYTHON_INSTALL_VERSION,
    "PROXY_ENABLED": "false",
    "PROXY_ADDRESS": "",
    "PROXY_BYPASS": "localhost,127.0.0.1",
    "PIP_MIRROR_URL": DEFAULT_PIP_MIRROR,
    "BROWSER_PROFILE_DIR": "",
    "BROWSER_PROFILE_NAME": "",
    "PROFILE_CLEAN_ENABLED": "false",
}

REQUIRED_PROJECT_FILES = [
    Path("main.py"),
    Path("app") / "core" / "browser.py",
    Path("app") / "services" / "config_engine.py",
]


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


def _load_env_file(path: Path) -> None:
    if not path.exists():
        _log("[WARN] 未找到 .env 文件，使用默认配置")
        return

    _log("[INFO] 读取 .env 配置文件...")
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value
    _log("[OK] 配置加载完成")


def _apply_env_defaults() -> None:
    for key, value in ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)


def _display_current_config() -> None:
    _log()
    _log("   当前配置:")
    _log(f"        APP_HOST     : {os.getenv('APP_HOST')}")
    _log(f"        APP_PORT     : {os.getenv('APP_PORT')}")
    _log(f"        BROWSER_PORT : {os.getenv('BROWSER_PORT')}")
    _log(f"        AUTO_UPDATE  : {os.getenv('AUTO_UPDATE_ENABLED')}")
    _log(f"        PYTHON_FIXED : {os.getenv('PYTHON_INSTALL_VERSION')}")
    profile_dir = _resolve_profile_dir()
    _log(f"        PROFILE_DIR  : {profile_dir}")
    profile_name = str(os.getenv("BROWSER_PROFILE_NAME", "") or "").strip()
    if profile_name:
        _log(f"        PROFILE_NAME : {profile_name}")
    _log(f"        PROFILE_CLEAN: {os.getenv('PROFILE_CLEAN_ENABLED')}")
    if _env_flag("PROXY_ENABLED"):
        _log(f"        PROXY        : {os.getenv('PROXY_ADDRESS', '')}")
    else:
        _log("        PROXY        : 已禁用")
    _log()


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


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
        kwargs["encoding"] = "utf-8"
        kwargs["errors"] = "replace"
    return subprocess.run(cmd, **kwargs)


def _run_project_python(args: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return _run([str(_venv_python()), *args], check=check, capture=capture)


def _python_install_version() -> str:
    return str(os.getenv("PYTHON_INSTALL_VERSION", DEFAULT_PYTHON_INSTALL_VERSION) or DEFAULT_PYTHON_INSTALL_VERSION).strip()


def _python_install_major_minor() -> str:
    parts = _python_install_version().split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return "3.13"


def _python_install_short() -> str:
    return _python_install_major_minor().replace(".", "")


def _python_install_url() -> str:
    version = _python_install_version()
    return f"https://www.python.org/ftp/python/{version}/python-{version}-amd64.exe"


def _is_windows_store_python() -> bool:
    if not sys.platform.startswith("win"):
        return False
    return "WindowsApps" in str(Path(sys.executable))


def _python_version_ok(python_path: Path) -> bool:
    try:
        result = subprocess.run(
            [
                str(python_path),
                "-c",
                "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _find_installed_fixed_python() -> Path | None:
    if not sys.platform.startswith("win"):
        return None

    short_version = _python_install_short()
    major_minor = _python_install_major_minor()
    local_app_data = os.getenv("LOCALAPPDATA", "")
    program_files = os.getenv("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = [
        Path(local_app_data) / "Programs" / "Python" / f"Python{short_version}" / "python.exe",
        Path(program_files) / f"Python{short_version}" / "python.exe",
        Path(program_files_x86) / f"Python{short_version}" / "python.exe",
    ]

    for candidate in candidates:
        if candidate.exists() and _python_version_ok(candidate):
            return candidate

    py_launcher = shutil.which("py")
    if py_launcher:
        try:
            result = subprocess.run(
                [py_launcher, f"-{major_minor}", "-c", "import sys; print(sys.executable)"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                candidate = Path(result.stdout.strip())
                if candidate.exists() and _python_version_ok(candidate):
                    return candidate
        except Exception:
            pass

    return None


def _download_and_install_fixed_python() -> Path:
    version = _python_install_version()
    url = _python_install_url()
    installer = Path(tempfile.gettempdir()) / f"python-{version}-amd64.exe"

    _log()
    _log(f"[INFO] 正在下载 Python {version}...")
    _log(f"[INFO] 下载来源: {url}")
    try:
        urllib.request.urlretrieve(url, installer)
    except Exception as exc:
        raise RuntimeError(f"Python 安装包下载失败: {exc}") from exc

    if not installer.exists():
        raise RuntimeError(f"Python 安装包不存在: {installer}")

    _log(f"[INFO] 正在静默安装 Python {version}...")
    result = subprocess.run(
        [
            str(installer),
            "/quiet",
            "InstallAllUsers=0",
            "PrependPath=1",
            "Include_launcher=1",
            "Include_pip=1",
            "Include_test=0",
            "SimpleInstall=1",
        ],
        check=False,
    )
    try:
        installer.unlink(missing_ok=True)
    except Exception:
        pass

    if result.returncode not in (0, 3010):
        raise RuntimeError(f"Python 安装失败，安装器退出码: {result.returncode}")

    installed = _find_installed_fixed_python()
    if not installed:
        raise RuntimeError("Python 安装完成后仍未找到可用解释器，请重新打开终端后再运行 start.py")
    return installed


def _offer_python_install(reason: str) -> bool:
    if not sys.platform.startswith("win"):
        return False

    version = _python_install_version()
    _log()
    _log(f"[INFO] {reason}")
    _log()
    _log(f"   可自动下载安装固定版本 Python {version} (64-bit)")
    _log(f"   下载来源: {_python_install_url()}")
    _log("   安装范围: 当前用户")
    _log()
    choice = input(f"是否自动下载并安装 Python {version}？(Y/N): ").strip()
    if choice.lower() != "y":
        _log("[INFO] 已取消自动安装 Python")
        return False

    installed = _download_and_install_fixed_python()
    if Path(sys.executable).resolve() == installed.resolve():
        return True

    _log(f"[OK] Python 已就绪: {installed}")
    _log("[INFO] 正在使用新 Python 重新运行 start.py...")
    os.execv(str(installed), [str(installed), str(Path(__file__).resolve()), *sys.argv[1:]])
    return True


def _check_python_version() -> None:
    _section("检查 Python 环境")
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if _is_windows_store_python():
        if _offer_python_install("检测到 Windows Store Python 占位符"):
            return
        raise RuntimeError("检测到 Windows Store Python 占位符，请关闭应用执行别名或安装完整版 Python")
    if sys.version_info < (3, 8):
        if _offer_python_install(f"Python 版本过低: {version}，最低要求 Python 3.8+"):
            return
        raise RuntimeError(f"Python 版本过低: {version}，最低要求 Python 3.8+")
    _log(f"[OK] Python {version}")
    _log(f"    路径: {sys.executable}")
    _log()


def _run_auto_update() -> None:
    if not _env_flag("AUTO_UPDATE_ENABLED", True):
        _log("[INFO] 自动更新已禁用")
        _log("       本次不会自动应用更新；服务启动后仍会检查新版本并在设置图标提示")
        _log("       如需自动应用更新，请修改 .env 中的 AUTO_UPDATE_ENABLED=true")
        _log()
        return

    _section("自动更新")
    updater_script = PROJECT_DIR / "updater.py"
    if not updater_script.exists():
        _log("[WARN] 未找到 updater.py，跳过自动更新")
        _log()
        return

    _log("[INFO] 检查 GitHub 最新版本...")
    result = _run([sys.executable, "updater.py"], check=False)
    if result.returncode == 0:
        _log("[INFO] 自动更新已应用，继续启动服务...")
    else:
        _log("[WARN] 本次未应用更新，继续启动服务")
    _log()


def _ensure_project_structure() -> None:
    _section("检查项目结构")
    missing = []
    for rel_path in REQUIRED_PROJECT_FILES:
        if not (PROJECT_DIR / rel_path).exists():
            missing.append(str(rel_path))

    sites_config = PROJECT_DIR / "config" / "sites.json"
    if not sites_config.exists():
        _log("[WARN] 缺失: config/sites.json，将自动创建")
        sites_config.parent.mkdir(parents=True, exist_ok=True)
        sites_config.write_text('{"_global": {"selector_definitions": []}}\n', encoding="utf-8")
        _log("[INFO] 已创建空配置文件")
    else:
        _log("[OK] 找到: config/sites.json")

    if missing:
        for item in missing:
            _log(f"[ERROR] 缺失: {item}")
        raise RuntimeError("项目结构不完整，请检查文件是否齐全")

    _log("[OK] 项目结构检查通过")
    _log()


def _ensure_venv() -> None:
    _section("准备虚拟环境")
    python_path = _venv_python()
    if python_path.exists():
        _log("[OK] 虚拟环境已存在")
        _log()
        return

    if VENV_DIR.exists():
        raise RuntimeError("虚拟环境损坏，缺少 Python 解释器。请删除 venv 后重新运行。")

    _log("[INFO] 创建虚拟环境...")
    builder = venv.EnvBuilder(with_pip=True)
    builder.create(str(VENV_DIR))
    if not python_path.exists():
        raise RuntimeError("虚拟环境创建失败，缺少 Python 解释器")
    _log("[OK] 虚拟环境创建成功")
    _log()


def _file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dependencies_ok() -> bool:
    check_script = PROJECT_DIR / "check_deps.py"
    if not check_script.exists():
        return True
    try:
        result = _run_project_python([check_script.name], check=False, capture=True)
    except Exception:
        return False
    return result.returncode == 0


def _ensure_dependencies() -> None:
    _section("检查依赖")
    if not REQUIREMENTS_FILE.exists():
        raise FileNotFoundError("缺少 requirements.txt 文件")

    current_hash = _file_md5(REQUIREMENTS_FILE)
    old_hash = ""
    if REQ_HASH_FILE.exists():
        try:
            old_hash = REQ_HASH_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            old_hash = ""

    if old_hash == current_hash and _dependencies_ok():
        _log("[OK] 依赖已是最新")
        _log()
        return

    _log("[INFO] 安装 Python 依赖包...")
    pip_cmd = [str(_venv_python()), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)]
    install_source = "PyPI"
    result = _run(pip_cmd, check=False)
    if result.returncode != 0:
        mirror = os.getenv("PIP_MIRROR_URL", DEFAULT_PIP_MIRROR).strip() or DEFAULT_PIP_MIRROR
        install_source = mirror
        _log(f"[WARN] 默认 PyPI 安装失败，尝试镜像: {mirror}")
        result = _run(pip_cmd + ["-i", mirror], check=False)
        if result.returncode != 0:
            if REQ_HASH_FILE.exists():
                REQ_HASH_FILE.unlink()
            raise RuntimeError("依赖安装失败")

    pip_check = _run([str(_venv_python()), "-m", "pip", "check"], check=False, capture=True)
    if pip_check.returncode != 0:
        _log("[WARN] pip check 报告依赖冲突，但不影响继续启动")

    REQ_HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    REQ_HASH_FILE.write_text(current_hash, encoding="utf-8")
    _log("[OK] 依赖安装完成")
    _log(f"[INFO] 安装来源: {install_source}")
    _log()


def _maybe_apply_patch() -> None:
    _section("应用 DrissionPage 补丁")
    patch_script = PROJECT_DIR / "patch_drissionpage.py"
    if not patch_script.exists():
        _log("[WARN] 未找到 patch_drissionpage.py，跳过补丁")
        _log()
        return
    result = _run_project_python([patch_script.name], check=False)
    if result.returncode != 0:
        _log("[WARN] 补丁应用失败，网络监听模式可能触发 CF 检测")
        _log("       项目仍可正常运行（DOM 模式不受影响）")
    _log()


def _resolve_profile_dir() -> Path:
    raw = str(os.getenv("BROWSER_PROFILE_DIR", "") or "").strip()
    if raw:
        profile_dir = Path(raw).expanduser()
        if not profile_dir.is_absolute():
            profile_dir = PROJECT_DIR / profile_dir
        return profile_dir
    return PROJECT_DIR / "chrome_profile"


def _maybe_clean_profile(profile_dir: Path) -> None:
    _section("浏览器配置瘦身")
    if not _env_flag("PROFILE_CLEAN_ENABLED"):
        _log(f"[INFO] 已禁用配置瘦身（PROFILE_CLEAN_ENABLED={os.getenv('PROFILE_CLEAN_ENABLED')}）")
        _log()
        return

    clean_script = PROJECT_DIR / "clean_profile.py"
    if not clean_script.exists():
        _log("[WARN] 未找到 clean_profile.py，跳过清理")
        _log()
        return

    _log("[INFO] 执行浏览器配置瘦身...")
    _run_project_python([clean_script.name, str(profile_dir)], check=False)
    _log()


def _debug_port_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.4):
            return True
    except Exception:
        return False


def _windows_browser_candidates() -> list[str]:
    local_app_data = os.getenv("LOCALAPPDATA", "")
    program_files = os.getenv("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = [
        Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(local_app_data) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        Path(program_files) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        Path(local_app_data) / "Vivaldi" / "Application" / "vivaldi.exe",
        Path(program_files) / "Vivaldi" / "Application" / "vivaldi.exe",
        Path(local_app_data) / "Programs" / "Opera" / "opera.exe",
        Path(program_files) / "Opera" / "opera.exe",
    ]
    return [str(path) for path in candidates]


def _platform_browser_candidates() -> list[str]:
    custom = str(os.getenv("BROWSER_PATH", "") or "").strip()
    if custom:
        return [custom]

    if sys.platform.startswith("win"):
        return _windows_browser_candidates()

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
    custom = str(os.getenv("BROWSER_PATH", "") or "").strip()
    for candidate in _platform_browser_candidates():
        if candidate and os.path.exists(candidate):
            return candidate
    if custom:
        _log(f"[WARN] BROWSER_PATH 指定的路径不存在: {custom}")
    return ""


def _launch_browser_if_needed() -> None:
    _section("准备 Chromium 内核浏览器")
    browser_port = int(os.getenv("BROWSER_PORT", "9222") or "9222")
    profile_dir = _resolve_profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    _log(f"[INFO] 浏览器配置目录: {profile_dir}")

    if _debug_port_ready(browser_port):
        _log("[WARN] Debug 端口已被占用，将复用现有浏览器实例")
        _log("[WARN] 如果后台标签页变慢，请关闭浏览器后重新运行启动脚本")
        _log(f"[OK] Debug 端口就绪: {browser_port}")
        _log()
        return

    browser_path = _resolve_browser_path()
    if not browser_path:
        raise RuntimeError("找不到可用的 Chromium 内核浏览器。请安装 Chrome/Edge/Brave/Vivaldi/Opera，或设置 BROWSER_PATH。")

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

    if _env_flag("PROXY_ENABLED"):
        proxy_address = str(os.getenv("PROXY_ADDRESS", "") or "").strip()
        proxy_bypass = str(os.getenv("PROXY_BYPASS", "") or "").strip()
        if proxy_address:
            browser_args.insert(-1, f"--proxy-server={proxy_address}")
            if proxy_bypass:
                browser_args.insert(-1, f"--proxy-bypass-list={proxy_bypass}")
            _log(f"[INFO] 代理已启用: {proxy_address}")

    _log(f"[INFO] 启动浏览器: {browser_path}")
    subprocess.Popen(
        browser_args,
        cwd=str(PROJECT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    _log("[INFO] 等待浏览器远程调试端口就绪...")
    for _ in range(15):
        if _debug_port_ready(browser_port):
            _log(f"[OK] 浏览器启动成功 - 端口 {browser_port}")
            _log()
            return
        time.sleep(1.0)

    raise RuntimeError(
        f"未检测到远程调试端口 {browser_port}，为避免服务误连到错误浏览器，本次启动已中止。"
    )


def _display_version_info() -> None:
    version_file = PROJECT_DIR / "VERSION"
    if not version_file.exists():
        return
    _log("   版本信息:")
    _log("   ----------------------------------------")
    _log(version_file.read_text(encoding="utf-8").strip())
    _log("   ----------------------------------------")
    _log()


def _display_start_summary() -> None:
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = os.getenv("APP_PORT", "8199")
    _log("========================================")
    _log("   服务启动中...")
    _log("========================================")
    _log()
    _log(f"   API 地址:     http://{host}:{port}")
    _log(f"   控制面板:     http://{host}:{port}/")
    _log(f"   API 文档:     http://{host}:{port}/docs")
    _log()
    _log("   项目结构:")
    _log(f"        配置目录:  {PROJECT_DIR / 'config'}")
    _log(f"        静态资源:  {PROJECT_DIR / 'static'}")
    _log()
    if _env_flag("AUTO_UPDATE_ENABLED", True):
        _log("   [WARN] 自动更新: 已启用")
    else:
        _log("   自动更新: 已禁用（仍会启动后检查新版本）")
    _log()
    _log("   按 Ctrl+C 停止服务")
    _log("   配置修改后会自动重启")
    _log("========================================")
    _log()


def _run_service_loop() -> int:
    while True:
        _load_env_file(PROJECT_DIR / ".env")
        completed = _run_project_python(["main.py"], check=False)
        if completed.returncode == 0:
            _log()
            _log("[INFO] 服务已停止")
            return 0
        if completed.returncode == 3:
            _log()
            _log("========================================")
            _log("   检测到配置更新，正在重启服务...")
            _log("========================================")
            time.sleep(2.0)
            continue
        _log()
        _log(f"[ERROR] 服务异常退出 (退出码: {completed.returncode})")
        _log("[INFO] 3 秒后自动重启...")
        time.sleep(3.0)


def main() -> int:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    _log()
    _log("========================================")
    _log("   Universal Web-to-API 启动脚本")
    _log("========================================")
    _log()

    _section("加载配置")
    _load_env_file(PROJECT_DIR / ".env")
    _apply_env_defaults()
    _display_current_config()

    _check_python_version()
    _run_auto_update()
    _ensure_project_structure()
    _ensure_venv()
    _ensure_dependencies()
    _maybe_apply_patch()
    profile_dir = _resolve_profile_dir()
    _maybe_clean_profile(profile_dir)
    _launch_browser_if_needed()
    _display_version_info()
    _display_start_summary()
    return _run_service_loop()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        _log()
        _log("[INFO] 已取消启动")
        raise SystemExit(130)
    except Exception as exc:
        _log()
        _log(f"[ERROR] {exc}")
        raise SystemExit(1)
