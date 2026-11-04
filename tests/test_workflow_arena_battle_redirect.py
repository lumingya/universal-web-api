"""`_abort_on_arena_battle_redirect` 的去重与行为测试。

步骤 6d 从 `_execute_workflow_stream_once` 的两个 except 分支里发现并合并了
一处 13 行 × 2 的重复代码：except (ElementNotFoundError, WorkflowError) 与
except Exception 各有一段"页面是否落在意外 Battle 页面"的判定，除日志里的
一个前缀词外完全相同。

这类重复的危险在于**漂移**：两条路径本应行为一致，却各写一份，改一处漏
一处就会出现"元素找不到时正确终止、通用异常时却继续跑"的不一致。合并后
结构上不可能再漂移，测试则锁定两条路径共用同一实现。
"""

from __future__ import annotations

import inspect
from typing import List, Tuple

import app.core.browser.workflow as workflow_mod
from app.core.config_parts.sse_formatter import SSEFormatter

# arena.ai 上非 direct 的路径即为"意外 Battle 页面"
BATTLE_URL = "https://arena.ai/battle"
DIRECT_URL = "https://arena.ai/direct/some-target"
OTHER_SITE = "https://example.com/chat"


class _FakeTab:
    def __init__(self, url: str = "") -> None:
        self.url = url


class _FakeSession:
    def __init__(self) -> None:
        self.id = "SESSION-1"
        self.tab = _FakeTab()


class _Host(workflow_mod.BrowserWorkflowMixin):
    def __init__(self) -> None:
        self.formatter = SSEFormatter()


def _drive(url: str, log_context: str = "步骤失败时") -> Tuple[List[str], bool]:
    gen = _Host()._abort_on_arena_battle_redirect(
        _FakeSession(), _FakeTab(url), "https://arena.ai/direct/recovery", log_context
    )
    chunks: List[str] = []
    try:
        while True:
            chunks.append(next(gen))
    except StopIteration as stop:
        return chunks, bool(stop.value)


# ---------------------------------------------------------------------------
# 行为
# ---------------------------------------------------------------------------


def test_battle_url_aborts_with_one_error_block():
    chunks, aborted = _drive(BATTLE_URL)
    assert aborted is True
    assert len(chunks) == 1
    assert "arena_direct_unexpected_battle_redirect" in chunks[0]


def test_direct_url_is_not_an_unexpected_battle():
    chunks, aborted = _drive(DIRECT_URL)
    assert aborted is False
    assert chunks == []


def test_other_site_is_not_an_unexpected_battle():
    chunks, aborted = _drive(OTHER_SITE)
    assert aborted is False
    assert chunks == []


def test_missing_tab_url_is_treated_as_empty():
    class _NoUrl:
        pass

    gen = _Host()._abort_on_arena_battle_redirect(_FakeSession(), _NoUrl(), "r", "步骤失败时")
    chunks = list(gen)
    assert chunks == []


def test_log_context_is_passed_through(caplog):
    """两处调用点的日志前缀不同，是合并时唯一需要参数化的部分。"""
    import logging

    with caplog.at_level(logging.WARNING):
        _drive(BATTLE_URL, "通用异常时")
    assert any("通用异常时检测到页面处于意外 Battle 页面" in r.getMessage() for r in caplog.records)


def test_error_payload_carries_the_terminal_code():
    """错误码必须原样透出。

    注意：源码里写的 message 带 "stream_terminal_error:" 前缀，但
    SSEFormatter.pack_error 会先经 error_metadata 归一化，把前缀剥掉，
    因此最终 payload 里是裸码。这条测试锁的是"归一化之后仍然可识别"——
    flow_runtime 对带前缀与裸码两种形式都接受。
    """
    chunks, _ = _drive(BATTLE_URL)
    assert '"code": "arena_direct_unexpected_battle_redirect"' in chunks[0]
    assert "stream_terminal_error:" not in chunks[0], "前缀应已被 error_metadata 归一化掉"


# ---------------------------------------------------------------------------
# 去重契约：两个 except 分支必须共用同一实现
# ---------------------------------------------------------------------------


def test_method_is_a_generator_returning_a_flag():
    fn = workflow_mod.BrowserWorkflowMixin._abort_on_arena_battle_redirect
    assert inspect.isgeneratorfunction(fn)
    assert tuple(inspect.signature(fn).parameters) == (
        "self",
        "session",
        "tab",
        "arena_recovery_url",
        "log_context",
    )


def test_both_except_branches_delegate_to_it():
    src = inspect.getsource(workflow_mod.BrowserWorkflowMixin._execute_workflow_stream_once)
    assert src.count("yield from self._abort_on_arena_battle_redirect(") == 2, (
        "两个 except 分支都应委托给同一方法"
    )
    assert '"步骤失败时"' in src
    assert '"通用异常时"' in src


def test_no_duplicated_battle_detection_left_in_the_caller():
    """两个 except 分支里的重复判定必须消失。

    主函数另有一处 is_arena_unexpected_battle_url() 调用（循环中的
    current_loop_url 检查），那是**不同语义**——它检查执行过程中是否跳转
    到了 Battle 页面，日志文案与处置都不同，不在本次合并范围内。这里只
    针对被合并的那两处"失败时"判定。
    """
    src = inspect.getsource(workflow_mod.BrowserWorkflowMixin._execute_workflow_stream_once)
    assert "检测到页面处于意外 Battle 页面" not in src, "失败时的重复判定应已合并"


def test_both_call_sites_set_workflow_aborted_before_break():
    """返回值必须被用上：置 workflow_aborted 并 break。"""
    src = inspect.getsource(workflow_mod.BrowserWorkflowMixin._execute_workflow_stream_once)
    block = src[src.index("yield from self._abort_on_arena_battle_redirect(") :]
    assert "workflow_aborted = True" in block[:400]
    assert "break" in block[:400]
