"""启动并校验受控 Chromium。

这些函数原先是 ``start.py`` 的一部分；现在由 ``app.runtime.bootstrap`` 在
``uv run python main.py`` 启动、导入应用之前调用，因此只依赖标准库和 psutil。

环境变量（均已在 app/core/settings_registry.py 登记）：
- ``BROWSER_AUTO_LAUNCH``：是否自动拉起受控浏览器，默认 true；
- ``BROWSER_PATH`` / ``BROWSER_PORT`` / ``BROWSER_PROFILE_DIR`` / ``BROWSER_PROFILE_NAME``；
- ``BROWSER_MEMORY_SAVER`` / ``BROWSER_PROCESS_MODEL``；
- ``PROXY_ENABLED`` / ``PROXY_ADDRESS`` / ``PROXY_BYPASS``。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _log(message: str = "") -> None:
    print(message, flush=True)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _section(title: str) -> None:
    _log(f"[STEP] {title}")
    _log("----------------------------------------")


class BrowserLaunchError(RuntimeError):
    """受控浏览器无法启动或无法安全接管时抛出。"""


def resolve_profile_dir() -> Path:
    raw = str(os.getenv("BROWSER_PROFILE_DIR", "") or "").strip()
    if raw:
        profile_dir = Path(raw).expanduser()
        if not profile_dir.is_absolute():
            profile_dir = PROJECT_ROOT / profile_dir
        return profile_dir
    return PROJECT_ROOT / "chrome_profile"


def debug_port_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.4):
            return True
    except Exception:
        return False


def check_chromium_debug_endpoint(port: int, timeout: float = 0.5) -> tuple[bool, dict[str, Any]]:
    """
    检查指定端口是否为可响应且合法的 Chromium 远程调试端点。
    返回 (is_chromium, version_info)。
    """
    version_url = f"http://127.0.0.1:{int(port)}/json/version"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(version_url, headers={"User-Agent": "UWAPI-Diagnostics"})
        with opener.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            if isinstance(data, dict) and ("Browser" in data or "webSocketDebuggerUrl" in data):
                return True, data
    except Exception:
        pass
    return False, {}


def get_port_processes(port: int) -> list[dict[str, Any]]:
    """获取占用指定 TCP 端口（处于 LISTENING 状态）的进程信息列表。"""
    port_int = int(port)
    pids: set[int] = set()

    # 1. 在 Windows 上优先使用 netstat -ano（支持 IPv4 与 IPv6 Dual-stack 监听）
    if sys.platform == "win32":
        try:
            output = subprocess.check_output(
                ["netstat", "-ano"],
                text=True,
                errors="ignore",
                stderr=subprocess.DEVNULL,
            )
            pattern = re.compile(
                rf"^\s*TCP\s+\[?[\w\.:]+\]?:({port_int})\s+\S+\s+LISTENING\s+(\d+)",
                re.IGNORECASE | re.MULTILINE,
            )
            for match in pattern.finditer(output):
                try:
                    pids.add(int(match.group(2)))
                except (ValueError, TypeError):
                    pass
        except Exception:
            pass

    # 2. 尝试使用 psutil 获取连接
    if not pids:
        try:
            import psutil
            for conn in psutil.net_connections(kind="tcp"):
                if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port_int:
                    if conn.pid:
                        pids.add(conn.pid)
        except Exception:
            pass

    # 3. 获取进程详情
    results = []
    for pid in sorted(pids):
        if pid <= 0:
            continue
        info = {"pid": pid, "name": f"PID:{pid}", "cmdline": ""}
        try:
            import psutil
            proc = psutil.Process(pid)
            info["name"] = proc.name()
            try:
                cmd = proc.cmdline()
                info["cmdline"] = " ".join(cmd) if cmd else ""
            except Exception:
                pass
        except Exception:
            pass
        results.append(info)
    return results


def terminate_port_processes(port: int, procs: list[dict[str, Any]]) -> bool:
    """终止占用指定端口的进程及其子进程树，并等待端口完全释放。"""
    pids = [p["pid"] for p in procs if p.get("pid", 0) > 0]
    if not pids:
        return not debug_port_ready(port)

    for pid in pids:
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                pass
        else:
            try:
                import signal
                os.kill(pid, getattr(signal, "SIGKILL", 9))
            except Exception:
                pass
        try:
            import psutil
            proc = psutil.Process(pid)
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            proc.kill()
        except Exception:
            pass

    # 轮询等待端口释放（最多 5 秒）
    for _ in range(10):
        time.sleep(0.5)
        if not debug_port_ready(port):
            return True
    return False


def is_interactive() -> bool:
    """安全检查当前环境是否为可交互终端（防御 sys.stdin 为 None）。"""
    try:
        return bool(sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty())
    except Exception:
        return False


def ask_user_yes_no(prompt: str, default: bool = False) -> bool:
    """在交互式终端中询问用户 (y/n)，返回 True 表示确认终止。"""
    if not is_interactive():
        return default
    try:
        reply = input(prompt).strip().lower()
        if not reply:
            return default
        return reply in ("y", "yes")
    except EOFError:
        _log()
        return default
    except KeyboardInterrupt:
        _log()
        raise


def inspect_headless_or_windowless_browser(port: int, timeout: float = 0.5) -> Optional[dict[str, str]]:
    """
    探测指定端口的 Chromium 浏览器是否处于无头(Headless)或无窗口/缺少页面标签页状态。
    返回 None 表示不是无头（或者端口未开放/非 Chromium）。
    """
    import json
    import urllib.request

    port_int = int(port)
    # 显式使用空 ProxyHandler，避免系统或环境变量代理(如 HTTP_PROXY)拦截 loopback 探测
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # 1. 检查进程启动参数 (若 psutil 可用，优先确认是否明确声明了 --headless 或 --no-startup-window)
    cmd_has_headless = False
    cmd_has_no_startup_window = False
    try:
        import psutil
        target_arg = f"--remote-debugging-port={port_int}"
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline_list = proc.info.get("cmdline") or []
                # 过滤 Chromium 子进程（renderer/gpu/utility 等子进程必然包含 --type=，主进程不包含）
                if any(str(arg).startswith("--type=") for arg in cmdline_list):
                    continue
                cmdline = " ".join(cmdline_list)
                if target_arg in cmdline:
                    cmd_lower = cmdline.lower()
                    if "--headless" in cmd_lower:
                        cmd_has_headless = True
                    if "--no-startup-window" in cmd_lower:
                        cmd_has_no_startup_window = True
                    if cmd_has_headless and cmd_has_no_startup_window:
                        break
            except Exception:
                continue
    except Exception:
        pass

    if cmd_has_headless:
        return {"reason": "headless_arg", "detail": "浏览器启动参数显式包含 --headless"}

    # 2. 检查 /json/version
    version_url = f"http://127.0.0.1:{port_int}/json/version"
    try:
        req = urllib.request.Request(version_url, headers={"User-Agent": "UWAPI-Diagnostics"})
        with opener.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return None

    browser_desc = str(data.get("Browser", "") or "")
    user_agent = str(data.get("User-Agent", "") or "")
    if "headless" in browser_desc.lower() or "headless" in user_agent.lower():
        return {
            "reason": "headless",
            "detail": f"检测到无头浏览器 ({browser_desc or user_agent})",
        }

    # 3. 检查 /json 目标列表 (targets)
    targets_url = f"http://127.0.0.1:{port_int}/json"
    try:
        req = urllib.request.Request(targets_url, headers={"User-Agent": "UWAPI-Diagnostics"})
        with opener.open(req, timeout=timeout) as resp:
            targets = json.loads(resp.read().decode("utf-8", errors="ignore"))
            if isinstance(targets, list):
                pages = [
                    t for t in targets
                    if isinstance(t, dict) and str(t.get("type", "")).lower() == "page"
                ]
                if not pages:
                    if cmd_has_no_startup_window:
                        return {
                            "reason": "no_startup_window",
                            "detail": "浏览器带有 --no-startup-window 参数且当前无前台可视页面",
                        }
                    other_types = sorted(list({
                        str(t.get("type", "unknown"))
                        for t in targets if isinstance(t, dict) and t.get("type")
                    }))
                    detail_types = ", ".join(other_types) if other_types else "无任何活动目标"
                    return {
                        "reason": "no_pages",
                        "detail": f"无可视标签页（当前后台目标: {detail_types}）",
                    }
    except Exception:
        pass

    return None


def focus_browser_window_for_port(port: int) -> bool:
    """Best-effort Windows foreground restore for an already-running browser."""
    if not sys.platform.startswith("win"):
        return False

    try:
        import ctypes
        from ctypes import wintypes
        import psutil
    except Exception as e:
        _log(f"[DEBUG] 跳过浏览器唤起：{e}")
        return False

    target_arg = f"--remote-debugging-port={int(port)}"
    target_pid = 0
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if target_arg in cmdline:
                target_pid = int(proc.info["pid"])
                break
        except Exception:
            continue

    if not target_pid:
        return False

    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    hwnd_box = {"value": 0}
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    @enum_proc
    def _enum_windows(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != target_pid:
            return True

        title_len = user32.GetWindowTextLengthW(hwnd)
        if title_len > 0:
            hwnd_box["value"] = int(hwnd)
            if user32.IsWindowVisible(hwnd):
                return False
        elif hwnd_box["value"] == 0:
            hwnd_box["value"] = int(hwnd)
        return True

    try:
        user32.EnumWindows(_enum_windows, 0)
        hwnd = int(hwnd_box["value"] or 0)
        if not hwnd:
            return False
        user32.ShowWindowAsync(hwnd, SW_RESTORE)
        time.sleep(0.15)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception as e:
        _log(f"[DEBUG] 唤起浏览器窗口失败: {e}")
        return False


def windows_browser_candidates() -> list[str]:
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


def platform_browser_candidates() -> list[str]:
    custom = str(os.getenv("BROWSER_PATH", "") or "").strip()
    if custom:
        return [custom]

    if sys.platform.startswith("win"):
        return windows_browser_candidates()

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


def resolve_browser_path() -> str:
    custom = str(os.getenv("BROWSER_PATH", "") or "").strip()
    for candidate in platform_browser_candidates():
        if candidate and os.path.exists(candidate):
            return candidate
    if custom:
        _log(f"[WARN] BROWSER_PATH 指定的路径不存在: {custom}")
    return ""


def browser_process_model_args(raw_value: str) -> list:
    """BROWSER_PROCESS_MODEL（P0-7，可选）：
    - 空 / default：Chromium 默认（每个站点实例一个渲染进程）
    - per-site：--process-per-site，同站点的标签页共用渲染进程
    - limit:N：--renderer-process-limit=N
    实测可省 10%～20% 内存；同站点标签页共用进程后会互相拖慢，高并发时不要开。
    """
    value = str(raw_value or "").strip().lower()
    if not value or value in {"default", "none", "off", "false", "0"}:
        return []
    if value in {"per-site", "per_site", "process-per-site"}:
        return ["--process-per-site"]
    if value.startswith("limit:") or value.startswith("limit="):
        try:
            limit = int(value[6:].strip())
        except ValueError:
            limit = 0
        if limit >= 1:
            return [f"--renderer-process-limit={limit}"]
    _log(f"[WARN] 无法识别 BROWSER_PROCESS_MODEL={raw_value!r}，已忽略（可选 per-site / limit:N）")
    return []


def ensure_controlled_browser() -> bool:
    """确保受控 Chromium 可用。

    返回 True 表示复用了已有浏览器或成功启动；False 表示显式关闭了自动拉起。
    端口被非受控进程占用等无法安全继续的情况抛出 BrowserLaunchError。
    """
    if not _env_flag("BROWSER_AUTO_LAUNCH", True):
        _log("[INFO] BROWSER_AUTO_LAUNCH=false，跳过自动拉起受控浏览器")
        _log("       请自行启动带 --remote-debugging-port=<BROWSER_PORT> 的 Chromium")
        _log()
        return False

    _section("准备 Chromium 内核浏览器")
    browser_port = int(os.getenv("BROWSER_PORT", "9222") or "9222")
    profile_dir = resolve_profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    _log(f"[INFO] 浏览器配置目录: {profile_dir}")

    while debug_port_ready(browser_port):
        port_procs = get_port_processes(browser_port)
        proc_desc = ", ".join(f"{p['name']} (PID: {p['pid']})" for p in port_procs) if port_procs else "未知进程"

        is_chromium, _ = check_chromium_debug_endpoint(browser_port)
        headless_probe = None
        if is_chromium:
            for attempt in range(3):
                headless_probe = inspect_headless_or_windowless_browser(browser_port)
                if not headless_probe:
                    break
                # 明确的 --headless 或无头标识无需重试
                if headless_probe.get("reason") in {"headless", "headless_arg"}:
                    break
                # 若仅是暂时未检测到 page 目标，等待 0.4s 重试（给新打开的浏览器创建主页留出缓冲）
                if attempt < 2:
                    time.sleep(0.4)

        # 场景 1：端口被占用，但不是正常的受控前台浏览器（非 Chromium 调试服务，或处于无头/无窗口状态）
        if not is_chromium or headless_probe:
            if not is_chromium:
                reason = "该端口被其他非 Chromium 调试服务占用"
            else:
                reason = f"该浏览器处于无头或无窗口状态（{headless_probe['detail']}）"

            _log(f"[WARN] 检测到浏览器端口 {browser_port} 已被占用，但无法作为受控前台浏览器使用！")
            _log(f"[WARN] 状态原因: {reason}")
            _log(f"[WARN] 占用进程: {proc_desc}")

            if not is_interactive():
                raise RuntimeError(
                    f"检测到端口 {browser_port} 被占用（{proc_desc}，{reason}）。"
                    "当前处于非交互环境，无法询问用户，请手动结束占用进程后重试。"
                )

            should_kill = ask_user_yes_no(
                f"是否终止占用端口 {browser_port} 的进程？(y/N): ",
                default=False,
            )
            if should_kill:
                _log(f"[INFO] 正在终止占用端口 {browser_port} 的进程 ({proc_desc})...")
                if terminate_port_processes(browser_port, port_procs):
                    _log(f"[OK] 进程已终止，端口 {browser_port} 已释放。准备重新启动受控浏览器...")
                    _log()
                    break
                else:
                    _log(f"[ERROR] 终止进程后，端口 {browser_port} 仍被占用，请检查权限或手动在任务管理器中结束。")
                    raise RuntimeError(f"端口 {browser_port} 无法释放，本次启动中止。")
            else:
                _log("[INFO] 已取消操作，本次启动中止。")
                raise BrowserLaunchError("用户取消了受控浏览器启动")

        # 场景 2：已有正常的前台可视 Chromium 实例
        _log(f"[WARN] Debug 端口已被占用（进程: {proc_desc}）")
        if is_interactive():
            should_kill = ask_user_yes_no(
                f"检测到已有运行中的浏览器，是否终止它并全新启动？(y/N, 默认复用): ",
                default=False,
            )
            if should_kill:
                _log(f"[INFO] 正在终止现有浏览器进程 ({proc_desc})...")
                if terminate_port_processes(browser_port, port_procs):
                    _log(f"[OK] 现有浏览器已关闭，端口 {browser_port} 已释放。准备全新启动浏览器...")
                    _log()
                    break
                else:
                    _log(f"[ERROR] 关闭现有浏览器失败，端口 {browser_port} 仍被占用。")
                    raise RuntimeError(f"端口 {browser_port} 无法释放，本次启动中止。")

        _log("[WARN] 将复用现有浏览器实例")
        _log("[WARN] 浏览器启动参数只在新进程生效；如需应用内存节省模式，请先关闭现有浏览器")
        if focus_browser_window_for_port(browser_port):
            _log("[INFO] 已唤起现有浏览器窗口")
        _log(f"[OK] Debug 端口就绪: {browser_port}")
        _log()
        return True

    browser_path = resolve_browser_path()
    if not browser_path:
        raise BrowserLaunchError(
            "找不到可用的 Chromium 内核浏览器。请安装 Chrome/Edge/Brave/Vivaldi/Opera，或设置 BROWSER_PATH。"
        )

    browser_args = [
        browser_path,
        f"--remote-debugging-port={browser_port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]

    # The previous launcher explicitly disabled Chromium's background memory
    # controls. Keep that behavior as the default for compatibility, while
    # allowing a validated opt-in memory saver mode.
    if _env_flag("BROWSER_MEMORY_SAVER", False):
        browser_args.insert(
            -1,
            "--enable-features=CalculateNativeWinOcclusion,AutomaticTabDiscarding,TabFreeze,IntensiveWakeUpThrottling",
        )
        _log("[INFO] Chromium 内存节省模式已启用（后台标签页允许降频/冻结/回收）")
    else:
        browser_args[4:4] = [
            "--disable-backgrounding-occluded-windows",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-features=CalculateNativeWinOcclusion,AutomaticTabDiscarding,TabFreeze,IntensiveWakeUpThrottling",
        ]
        _log("[INFO] Chromium 常驻后台模式已启用（BROWSER_MEMORY_SAVER=false）")

    process_model_args = browser_process_model_args(os.getenv("BROWSER_PROCESS_MODEL", ""))
    for extra_arg in process_model_args:
        browser_args.insert(-1, extra_arg)
    if process_model_args:
        _log(f"[INFO] Chromium 进程模型: {' '.join(process_model_args)}（并发标签页较多时不建议开启）")

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
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    _log("[INFO] 等待浏览器远程调试端口就绪...")
    for _ in range(15):
        if debug_port_ready(browser_port):
            _log(f"[OK] 浏览器启动成功 - 端口 {browser_port}")
            if focus_browser_window_for_port(browser_port):
                _log("[INFO] 已唤起新启动的浏览器窗口")
            _log()
            return True
        time.sleep(1.0)

    port_procs = get_port_processes(browser_port)
    proc_hint = ""
    if port_procs:
        # 注意：不要在 f-string 里嵌套同种引号（PEP 701 语法仅 3.12+ 可解析，会让 3.10/3.11 直接 SyntaxError）
        proc_desc = ", ".join(f"{p['name']}(PID:{p['pid']})" for p in port_procs)
        proc_hint = f"（当前该端口被以下进程占用: {proc_desc}）"
    raise BrowserLaunchError(
        f"未检测到远程调试端口 {browser_port}{proc_hint}，为避免服务误连到错误浏览器，本次启动已中止。"
    )

__all__ = [
    "BrowserLaunchError",
    "ensure_controlled_browser",
    "resolve_profile_dir",
    "resolve_browser_path",
    "debug_port_ready",
    "check_chromium_debug_endpoint",
    "browser_process_model_args",
]
