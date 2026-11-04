"""`_execute_workflow_stream_once` 入口守卫的行为锁定测试。

该函数（app/core/browser/workflow.py，1300+ 行）在进入主循环前有 6 条
"致命前置错误"分支，每条都 yield 一个带 code 的错误块后 return。它们是
整个函数里最容易回归、也最容易在拆分时被改坏的部分，因此先在此锁定。

宿主类 BrowserWorkflowMixin 的其余依赖（config_engine、executor 等）在这些
分支上一概不触及，所以测试只需提供 formatter 与 _should_stop_checker。
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

import pytest

import app.core.browser.workflow as workflow_mod
from app.core.config_parts.sse_formatter import SSEFormatter

_RAISE = object()  # 让 FakeTab.url 抛异常的哨兵

_CODE_RE = re.compile(r'"code":\s*"([^"]+)"')


class _FakeTab:
    """只提供入口守卫所需的 url / tab_id。"""

    def __init__(self, url: Any = "") -> None:
        self._url = url
        self.tab_id = "TAB-1"

    @property
    def url(self) -> str:
        if self._url is _RAISE:
            raise RuntimeError("tab gone")
        return self._url


class _FakeSession:
    def __init__(self, tab: _FakeTab) -> None:
        self.tab = tab
        self.id = "SESSION-1"
        self.current_domain: Optional[str] = None
        self.preset_name: Optional[str] = None
        self.errors: List[str] = []

    def mark_error(self, code: str) -> None:
        self.errors.append(code)


class _WorkflowHost(workflow_mod.BrowserWorkflowMixin):
    """最小宿主：入口守卫只用到 formatter、停止检查器与页面状态检查。"""

    def __init__(self, *, stopped: bool = False, page_ready: bool = True) -> None:
        self.formatter = SSEFormatter()
        self._stopped = stopped
        self._page_ready = page_ready

    def _should_stop_checker(self) -> bool:
        return self._stopped

    def _check_page_status(self, tab: Any) -> dict:
        if self._page_ready:
            return {"ready": True, "reason": ""}
        return {"ready": False, "reason": "页面状态桩：未就绪"}


def _run(
    url: Any = "", *, stopped: bool = False, page_ready: bool = True
) -> tuple[List[str], _FakeSession]:
    """驱动一次工作流，返回 (错误码序列, session)。"""
    session = _FakeSession(_FakeTab(url))
    host = _WorkflowHost(stopped=stopped, page_ready=page_ready)
    chunks = list(host._execute_workflow_stream_once(session, []))
    codes = [m.group(1) for c in chunks if (m := _CODE_RE.search(c))]
    return codes, session


# --------------------------------------------------------------------------
# 6 条致命前置分支：每条都应产出唯一 code，并紧跟 finish
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected_code",
    [
        ("", "no_page"),
        ("about:blank", "blank_page"),
        ("chrome://newtab/", "blank_page"),
        ("chrome-error://neterror", "page_error"),
        ("about:neterror?e=x", "page_error"),
        ("file:///etc/hosts", "invalid_url"),
    ],
)
def test_entry_guard_yields_expected_code(url: str, expected_code: str) -> None:
    codes, _ = _run(url)
    assert codes == [expected_code]


def test_cancelled_by_stop_checker() -> None:
    # 停止检查器先于 tab.url 求值，因此即便页面是合法的也必须被取消
    codes, _ = _run("https://example.com/", stopped=True)
    assert codes == ["cancelled"]


def test_tab_access_failure_is_cancelled_and_recorded() -> None:
    codes, session = _run(_RAISE)
    assert codes == ["tab_closed"]
    assert session.errors == ["tab_access_failed"]


def test_url_parse_failure_leaves_domain_unset() -> None:
    codes, session = _run("file:///etc/hosts")
    assert codes == ["invalid_url"]
    # 解析失败时不应污染 session 上的域名
    assert session.current_domain is None


def test_page_not_ready_is_the_guard_after_the_entry_guards() -> None:
    """page_not_ready 排在 6 条入口守卫之后：页面未就绪时才轮到它。"""
    codes, _ = _run("https://example.com/", page_ready=False)
    assert codes == ["page_not_ready"]


def test_valid_remote_url_passes_all_entry_guards() -> None:
    """合法远程 URL 不产出任何入口错误码，且解析出的域名写到 session。

    页面状态桩设为"未就绪"，让函数在紧随其后的 page_not_ready 处停下——
    这既证明了 6 条入口守卫全部放行，也不会误触更深的依赖。
    """
    codes, session = _run("https://example.com/some/path", page_ready=False)
    assert codes == ["page_not_ready"]
    assert session.current_domain is not None
    assert "example.com" in session.current_domain
    assert session.errors == []  # 未走到 tab 失败分支


# --------------------------------------------------------------------------
# 守卫的判定顺序（拆分时最容易被无意改变的部分）
# --------------------------------------------------------------------------


def test_stop_checker_is_evaluated_before_tab_access() -> None:
    """取消优先于标签页访问：即便 tab 已失效，也应报 cancelled。"""
    codes, session = _run(_RAISE, stopped=True)
    assert codes == ["cancelled"]
    assert session.errors == []  # 未走到 tab 访问，不应记录 tab_access_failed


def test_blank_page_is_checked_before_chrome_error() -> None:
    # 两个分支条件互斥，这里确保空白页 URL 不会被误判为 page_error
    codes, _ = _run("about:blank")
    assert codes == ["blank_page"]


def test_every_guard_produces_exactly_one_error_then_finishes() -> None:
    """所有守卫分支都应是 pack_error + pack_finish 后 return，不多不少。"""
    for url in ("", "about:blank", "chrome-error://x", "file:///x"):
        chunks = list(_WorkflowHost()._execute_workflow_stream_once(_FakeSession(_FakeTab(url)), []))
        assert len(chunks) == 2, f"{url!r} 应只产出 2 个 chunk，实际 {len(chunks)}"
        assert "[DONE]" in chunks[1]
