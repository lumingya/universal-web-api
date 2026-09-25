"""Playwright UI 回归共用的浏览器启动辅助（R0-5）。

- 没装 playwright：调用方先 ``pytest.importorskip("playwright.sync_api")``，整模块自动跳过；
- 装了 playwright 但没下载它自带的 Chromium（开发机常见，也没必要为测试再下载约 150MB）：
  依次回退到本机 Chrome、Edge（``channel="chrome"`` / ``"msedge"``）；
- ``UWAPI_TEST_CHROME`` 指定浏览器可执行文件，``UWAPI_PW_CHANNEL`` 指定 channel（二者优先）；
- 都不可用时 skip 并写明原因，而不是报错。

这些测试只加载本地合成页面（set_content / data URL），不连接正在运行的服务，
也不使用用户的浏览器配置目录，可以和日常实例同时运行。
"""

from __future__ import annotations

import os

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
