"""Playwright UI 回归共用的浏览器启动辅助（R0-5）。

- 没装 playwright：调用方先 ``pytest.importorskip("playwright.sync_api")``，整模块自动跳过；
- 装了 playwright 但没下载它自带的 Chromium（开发机常见，也没必要为测试再下载约 150MB）：
  依次回退到本机 Chrome、Edge（``channel="chrome"`` / ``"msedge"``）；
- ``UWAPI_TEST_CHROME`` 指定浏览器可执行文件，``UWAPI_PW_CHANNEL`` 指定 channel（二者优先）；
- 都不可用时 skip 并写明原因，而不是报错。

这些测试只加载本地合成页面（set_content / data URL），不连接正在运行的服务，
也不使用用户的浏览器配置目录，可以和日常实例同时运行。

Windows 注意：main.py 在导入时会把全局事件循环策略设为 WindowsSelectorEventLoopPolicy
（生产环境的有意选择），而 Selector 循环不能创建子进程，Playwright 启动 driver 会抛
NotImplementedError。所以统一用本模块的 ``sync_playwright()``：只在启动 Playwright 的瞬间
切换到 Proactor 策略，随后立即恢复原策略。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys

import pytest


def launch_attempts() -> list[dict]:
    explicit = os.getenv("UWAPI_TEST_CHROME", "").strip()
    if explicit:
        return [{"executable_path": explicit}]
    channel = os.getenv("UWAPI_PW_CHANNEL", "").strip()
    if channel:
        return [{"channel": channel}]
    return [{}, {"channel": "chrome"}, {"channel": "msedge"}]


def launch_chromium(chromium, **kwargs):
    """``chromium`` 为 ``playwright.chromium``；其余参数原样传给 ``launch``。"""
    errors = []
    for extra in launch_attempts():
        try:
            return chromium.launch(**{**kwargs, **extra})
        except Exception as exc:  # pragma: no cover - 取决于本机浏览器
            label = extra.get("channel") or extra.get("executable_path") or "playwright 自带 Chromium"
            first_line = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
            errors.append(f"{label}: {first_line[:160]}")
    pytest.skip("没有可供 Playwright 使用的 Chromium -> " + " | ".join(errors))


@contextlib.contextmanager
def sync_playwright():
    """``playwright.sync_api.sync_playwright`` 的包装，见模块说明中的 Windows 注意事项。"""
    from playwright.sync_api import sync_playwright as _sync_playwright

    manager = _sync_playwright()
    previous = None
    if sys.platform == "win32":
        current = asyncio.get_event_loop_policy()
        if not isinstance(current, asyncio.WindowsProactorEventLoopPolicy):
            previous = current
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        playwright = manager.__enter__()  # 事件循环在这里创建，之后策略变化不影响它
    finally:
        if previous is not None:
            asyncio.set_event_loop_policy(previous)
    try:
        yield playwright
    finally:
        manager.__exit__(None, None, None)

