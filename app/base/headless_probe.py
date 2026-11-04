"""探测 Chromium 是否处于无头（headless）或无窗口状态。

从 ``app/core/browser/connection.py`` 与 ``app/runtime/browser_launcher.py``
里的两份**逐字重复**的实现合并而来（两份 88 行、相似度 98.9%，仅函数名差
一个下划线前缀）。放在 app/base 的原因与约束见该包的 docstring。

只依赖标准库与可选的 psutil。
"""

from typing import Dict, Optional


def inspect_headless_or_windowless_browser(port: int, timeout: float = 0.5) -> Optional[Dict[str, str]]:
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
