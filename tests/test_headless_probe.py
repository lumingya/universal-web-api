"""app/base/headless_probe.py 的专项测试。

该函数原本**逐字重复两份**：``app/core/browser/connection.py`` 里的
``_inspect_headless_or_windowless_browser`` 与 ``app/runtime/browser_launcher.py``
里的 ``inspect_headless_or_windowless_browser``，88 行、相似度 98.9%，仅函数
名差一个下划线前缀。两份都在使用中（connection 2 处、launcher 1 处）。

合并的原因是它会漂移：无头判定逻辑一旦调整，改一处、漏一处，症状是"启动
时误判端口被无头浏览器占用"，极难定位。

判定顺序（本文件按此顺序覆盖）：
    1. psutil 扫进程命令行，命中 --headless      → headless_arg（最快路径）
    2. GET /json/version，Browser/UA 含 headless → headless
    3. GET /json 目标列表，无 page 类型           → no_startup_window / no_pages
    4. 以上都不成立                               → None（视为正常前台浏览器）

测试策略：不启真实浏览器。用假的 opener 注入 HTTP 响应，用 monkeypatch 替
换 psutil.process_iter，从而把三种判定路径与各种异常分支都覆盖到。
"""

from __future__ import annotations

import json as jsonlib
import urllib.error

import pytest

from app.base.headless_probe import inspect_headless_or_windowless_browser as probe


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return jsonlib.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """按 URL 返回预设响应；未登记的 URL 抛 URLError。"""

    def __init__(self, routes=None, fail_all=False):
        self.routes = routes or {}
        self.fail_all = fail_all
        self.requested: list[str] = []

    def open(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.requested.append(url)
        if self.fail_all:
            raise urllib.error.URLError("连接被拒")
        for frag, payload in self.routes.items():
            if frag in url:
                if isinstance(payload, Exception):
                    raise payload
                return FakeResponse(payload)
        raise urllib.error.URLError(f"未登记的 URL: {url}")


class FakeProc:
    """psutil.process_iter() 返回的进程对象需要有 .info 属性。

    用裸 dict 会触发源码内层的 `except Exception: continue`，导致用例
    "静默通过"——这正是这类测试最容易踩的坑。
    """

    def __init__(self, pid, cmdline):
        self.info = {"pid": pid, "cmdline": cmdline}


def procs(*cmdlines):
    """按若干命令行构造 fake 进程列表。"""
    return [FakeProc(i + 1, list(c)) for i, c in enumerate(cmdlines)]


@pytest.fixture()
def no_psutil(monkeypatch):
    """让 psutil 不可用，走纯 HTTP 判定路径。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "psutil":
            raise ImportError("测试中禁用 psutil")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)


@pytest.fixture()
def patch_opener(monkeypatch):
    """把 urllib.request.build_opener 换成可注入的假实现。"""
    created = {}

    def _install(opener):
        monkeypatch.setattr("urllib.request.build_opener", lambda *a, **k: opener)
        created["opener"] = opener
        return opener

    return _install


# --------------------------------------------------------------------------
# 1. 无法连接 / 端口未开放
# --------------------------------------------------------------------------


def test_unreachable_port_returns_none(no_psutil, patch_opener):
    patch_opener(FakeOpener(fail_all=True))
    assert probe(9222) is None


def test_unreachable_port_still_none_when_psutil_sees_headless(monkeypatch, patch_opener):
    """命令行路径优先：即便端口不通，只要参数里有 --headless 就判定无头。"""
    patch_opener(FakeOpener(fail_all=True))
    monkeypatch.setattr(
        "psutil.process_iter",
        lambda attrs: procs(["chrome", "--remote-debugging-port=9222", "--headless=new"]),
    )
    result = probe(9222)
    assert result is not None
    assert result["reason"] == "headless_arg"


# --------------------------------------------------------------------------
# 2. 命令行路径（headless_arg）
# --------------------------------------------------------------------------


def test_headless_arg_is_detected_from_cmdline(monkeypatch, patch_opener):
    patch_opener(FakeOpener(fail_all=True))
    monkeypatch.setattr(
        "psutil.process_iter",
        lambda attrs: procs(["chrome", "--remote-debugging-port=9222", "--headless"]),
    )
    assert probe(9222)["reason"] == "headless_arg"


def test_other_ports_are_ignored(monkeypatch, patch_opener):
    """只认 --remote-debugging-port=<目标端口> 的进程。"""
    patch_opener(FakeOpener(routes={"/json/version": {"Browser": "Chrome/1"}}))
    monkeypatch.setattr(
        "psutil.process_iter",
        lambda attrs: procs(["chrome", "--remote-debugging-port=9999", "--headless"]),
    )
    assert probe(9222) is None


def test_chromium_child_processes_are_skipped(monkeypatch, patch_opener):
    """--type= 的子进程（renderer/gpu）不该被当成主进程。"""
    patch_opener(FakeOpener(routes={"/json/version": {"Browser": "Chrome/1"}}))
    monkeypatch.setattr(
        "psutil.process_iter",
        lambda attrs: procs(
            ["chrome", "--remote-debugging-port=9222", "--type=renderer", "--headless"]
        ),
    )
    assert probe(9222) is None


def test_psutil_failure_is_tolerated(monkeypatch, patch_opener):
    """psutil 抛异常不应影响后续 HTTP 判定。"""
    patch_opener(FakeOpener(routes={"/json/version": {"Browser": "HeadlessChrome/120"}}))

    def boom(attrs):
        raise RuntimeError("psutil 炸了")

    monkeypatch.setattr("psutil.process_iter", boom)
    assert probe(9222)["reason"] == "headless"


# --------------------------------------------------------------------------
# 3. /json/version 路径（headless）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"Browser": "HeadlessChrome/120.0"},
        {"User-Agent": "Mozilla/5.0 HeadlessChrome/120.0"},
        {"Browser": "Chrome/120", "User-Agent": "headless"},
    ],
)
def test_headless_detected_from_version_endpoint(no_psutil, patch_opener, payload):
    patch_opener(FakeOpener(routes={"/json/version": payload}))
    result = probe(9222)
    assert result["reason"] == "headless"
    assert "无头" in result["detail"]


def test_non_headless_version_falls_through_to_targets(no_psutil, patch_opener):
    opener = patch_opener(
        FakeOpener(
            routes={
                "/json/version": {"Browser": "Chrome/120.0.0.0"},
                "/json": [{"type": "page", "url": "about:blank"}],
            }
        )
    )
    assert probe(9222) is None
    # 必须真的去查了 targets（证明 /json/version 未命中时确实会继续往下走）
    assert any(u.endswith("/json") for u in opener.requested)


# --------------------------------------------------------------------------
# 4. /json 目标列表路径（no_pages / no_startup_window）
# --------------------------------------------------------------------------


def test_no_page_targets_reports_no_pages(no_psutil, patch_opener):
    patch_opener(
        FakeOpener(
            routes={
                "/json/version": {"Browser": "Chrome/120"},
                "/json": [{"type": "background_page"}, {"type": "service_worker"}],
            }
        )
    )
    result = probe(9222)
    assert result["reason"] == "no_pages"
    assert "background_page" in result["detail"]


def test_empty_target_list_reports_no_pages(no_psutil, patch_opener):
    patch_opener(
        FakeOpener(routes={"/json/version": {"Browser": "Chrome/120"}, "/json": []})
    )
    result = probe(9222)
    assert result["reason"] == "no_pages"
    assert "无任何活动目标" in result["detail"]


def test_no_startup_window_flag_changes_reason(monkeypatch, patch_opener):
    patch_opener(
        FakeOpener(routes={"/json/version": {"Browser": "Chrome/120"}, "/json": []})
    )
    monkeypatch.setattr(
        "psutil.process_iter",
        lambda attrs: procs(["chrome", "--remote-debugging-port=9222", "--no-startup-window"]),
    )
    result = probe(9222)
    assert result["reason"] == "no_startup_window"


def test_page_targets_present_means_foreground_browser(no_psutil, patch_opener):
    patch_opener(
        FakeOpener(
            routes={
                "/json/version": {"Browser": "Chrome/120"},
                "/json": [{"type": "background_page"}, {"type": "page", "url": "x"}],
            }
        )
    )
    assert probe(9222) is None


# --------------------------------------------------------------------------
# 5. 健壮性与契约
# --------------------------------------------------------------------------


def test_targets_endpoint_failure_is_tolerated(no_psutil, patch_opener):
    """targets 查询失败应回退为 None，而不是抛异常。"""
    patch_opener(
        FakeOpener(
            routes={
                "/json/version": {"Browser": "Chrome/120"},
                "/json": urllib.error.URLError("炸"),
            }
        )
    )
    assert probe(9222) is None


def test_malformed_targets_are_ignored(no_psutil, patch_opener):
    patch_opener(
        FakeOpener(
            routes={
                "/json/version": {"Browser": "Chrome/120"},
                "/json": ["not-a-dict", None, 42],
            }
        )
    )
    assert probe(9222)["reason"] == "no_pages"


def test_non_list_targets_payload_is_tolerated(no_psutil, patch_opener):
    patch_opener(
        FakeOpener(routes={"/json/version": {"Browser": "Chrome/120"}, "/json": {"oops": 1}})
    )
    assert probe(9222) is None


@pytest.mark.parametrize("port", [9222, "9222"])
def test_port_accepts_int_and_str(no_psutil, patch_opener, port):
    patch_opener(FakeOpener(routes={"/json/version": {"Browser": "HeadlessChrome/1"}}))
    assert probe(port)["reason"] == "headless"


def test_result_shape_is_stable(no_psutil, patch_opener):
    """调用方用 result.get("reason") 做分支，键名与类型必须稳定。"""
    patch_opener(FakeOpener(routes={"/json/version": {"Browser": "HeadlessChrome/1"}}))
    result = probe(9222)
    assert set(result) == {"reason", "detail"}
    assert isinstance(result["reason"], str)
    assert isinstance(result["detail"], str)


def test_reason_values_are_from_known_set(no_psutil, patch_opener):
    """browser_launcher 里对 reason 做了集合判断，取值不能随意新增。"""
    known = {"headless", "headless_arg", "no_startup_window", "no_pages"}
    # 只验证这里能构造出的几种，剩余取值留给集成路径
    patch_opener(FakeOpener(routes={"/json/version": {"Browser": "HeadlessChrome/1"}}))
    assert probe(9222)["reason"] in known
    patch_opener(FakeOpener(routes={"/json/version": {"Browser": "Chrome/1"}, "/json": []}))
    assert probe(9222)["reason"] in known
