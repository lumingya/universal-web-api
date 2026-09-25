"""Shared helper: launch a throwaway (headless by default) Chromium for real-browser tests.

Skipped automatically when no Chromium binary is found. Set
``UWAPI_TEST_CHROME`` to force a binary, ``UWAPI_SKIP_REAL_BROWSER=1`` to skip.
"""

from __future__ import annotations

import glob
import os
import shutil
import socket
import sys
import tempfile

import pytest


def find_chrome():
    explicit = os.getenv("UWAPI_TEST_CHROME")
    if explicit:
        return explicit if os.path.exists(explicit) else None
    candidates = sorted(
        glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")),
        reverse=True,
    )
    for name in ("google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            candidates.append(path)
    candidates.extend(path for path in _platform_browser_paths() if os.path.exists(path))
    return candidates[0] if candidates else None


def _platform_browser_paths():
    """Windows / macOS 的常见安装位置（R0-5：原先只认 Linux 路径，Windows 上真实浏览器测试全部 skip）。"""
    if sys.platform.startswith("win"):
        bases = [os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for base in filter(None, bases):
            yield os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
            yield os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
    elif sys.platform == "darwin":
        yield "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        yield "/Applications/Chromium.app/Contents/MacOS/Chromium"
        yield "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"


def launch_page(headless: bool = True):
    """Generator yielding a ChromiumPage; use from a module-scoped fixture.

    ``headless=False`` needs an X display (e.g. ``xvfb-run``); only headed
    Chromium has real background-tab visibility.
    """
    chrome = find_chrome()
    if not chrome or os.getenv("UWAPI_SKIP_REAL_BROWSER"):
        pytest.skip("no chromium available")
    from DrissionPage import ChromiumOptions, ChromiumPage

    profile = tempfile.mkdtemp(prefix="uwapi-real-")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    co = ChromiumOptions().set_browser_path(chrome)
    co.headless(bool(headless))
    co.set_argument("--no-sandbox")
    co.set_argument("--window-size=1200,900")
    co.set_local_port(port)
    co.set_user_data_path(profile)
    try:
        page = ChromiumPage(co)
    except Exception as exc:  # pragma: no cover - environment dependent
        shutil.rmtree(profile, ignore_errors=True)
        pytest.skip(f"chromium failed to start: {exc}")
    try:
        yield page
    finally:
        try:
            page.quit()
        except Exception:
            pass
        shutil.rmtree(profile, ignore_errors=True)
