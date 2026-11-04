"""`_handle_workflow_interrupt` 的隔离测试。

该方法由步骤 6c 从 `_execute_workflow_stream_once` 主循环里提出（原 62 行
内联块）。它决定命令中断后工作流的走向，且有 5 条出口，是提取过程中
唯一一处把 break/continue 改写成显式返回值的变换——因此需要比"字节相同"
更强的证据：本文件逐条覆盖那 5 条出口。

出口语义（见 _InterruptOutcome）：
  aborted=True               -> 调用方 break
  handled=True               -> 调用方 continue（step_index 可能已前移）
  两者皆 False               -> 调用方继续本轮步骤（落到下一条语句）
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional, Tuple

import app.core.browser.workflow as workflow_mod
from app.core.browser.workflow import _InterruptOutcome
from app.core.config_parts.sse_formatter import SSEFormatter


class _FakeSession:
    def __init__(self) -> None:
        self.id = "SESSION-1"
        self.tab = object()


class _FakeCommandEngine:
    """只实现中断处理所需的两个方法。"""

    def __init__(self, result: Dict[str, Any]) -> None:
        self._result = result
        self.calls: List[Any] = []

    def handle_pending_workflow_interrupts(self, session: Any) -> Dict[str, Any]:
        self.calls.append(session)
        return self._result


class _FakeExecutor:
    def __init__(self, generating: bool = False, raise_on_generating: bool = False) -> None:
        self._generating = generating
        self._raise = raise_on_generating
        self.rebuild_calls: List[Tuple[str, bool]] = []

    def page_looks_generating(self, selector: str) -> bool:
        if self._raise:
            raise RuntimeError("page probe failed")
        return self._generating

    def rebuild_network_listener_after_external_interruption(
        self, reason: str, allow_dom_image_resume: bool = False
    ) -> None:
        self.rebuild_calls.append((reason, allow_dom_image_resume))


class _InterruptHost(workflow_mod.BrowserWorkflowMixin):
    def __init__(
        self,
        *,
        next_stream_index: Optional[int] = None,
        resume_index: Optional[int] = None,
    ) -> None:
        self.formatter = SSEFormatter()
        self._next_stream_index = next_stream_index
        self._resume_index = resume_index
        self.stream_lookups: List[int] = []
        self.resume_lookups: List[int] = []

    def _find_next_stream_step_index(self, workflow: List[Dict[str, Any]], step_index: int) -> Optional[int]:
        self.stream_lookups.append(step_index)
        return self._next_stream_index

    def _find_resume_step_after_interrupt(self, workflow: List[Dict[str, Any]], step_index: int) -> int:
        self.resume_lookups.append(step_index)
        return self._resume_index if self._resume_index is not None else step_index


DEFAULT_RESULT: Dict[str, Any] = {"handled": False, "abort": False, "message": ""}


def _drive(
    result: Dict[str, Any] = DEFAULT_RESULT,
    *,
    command_engine: Optional[Any] = None,
    step_index: int = 2,
    workflow: Optional[List[Dict[str, Any]]] = None,
    flow_program: Any = None,
    executor: Optional[Any] = None,
    selectors: Optional[Dict[str, Any]] = None,
    host: Optional[_InterruptHost] = None,
) -> Tuple[List[str], _InterruptOutcome]:
    """驱动一次中断处理，返回 (产出的 SSE 块, 出口)。"""
    engine = command_engine if command_engine is not None else _FakeCommandEngine(result)
    gen = (host or _InterruptHost())._handle_workflow_interrupt(
        _FakeSession(),
        engine,
        step_index,
        workflow if workflow is not None else [{"action": f"step{i}"} for i in range(6)],
        flow_program,
        executor if executor is not None else _FakeExecutor(),
        selectors if selectors is not None else {"send_btn": "#send"},
    )
    chunks: List[str] = []
    try:
        while True:
            chunks.append(next(gen))
    except StopIteration as stop:
        return chunks, stop.value


# ---------------------------------------------------------------------------
# 出口 1：未处理的中断 —— 不影响本轮步骤
# ---------------------------------------------------------------------------


def test_unhandled_interrupt_falls_through():
    chunks, outcome = _drive({"handled": False, "abort": False})
    assert chunks == []
    assert outcome.aborted is False
    assert outcome.handled is False, "未处理时必须落到下一条语句，而不是 continue"
    assert outcome.step_index == 2


def test_missing_command_engine_uses_neutral_result():
    class _None:
        pass

    chunks, outcome = _drive(command_engine=None)
    assert chunks == []
    assert (outcome.aborted, outcome.handled) == (False, False)


def test_session_is_passed_to_the_command_engine():
    engine = _FakeCommandEngine(DEFAULT_RESULT)
    _drive(command_engine=engine)
    assert len(engine.calls) == 1


# ---------------------------------------------------------------------------
# 出口 2：命令要求中止 —— 产出错误块后 break
# ---------------------------------------------------------------------------


def test_abort_yields_one_error_block_and_flags_abort():
    chunks, outcome = _drive({"handled": False, "abort": True, "message": "用户取消"})
    assert outcome.aborted is True
    assert outcome.abort_message == "用户取消"
    assert len(chunks) == 1
    assert "workflow_interrupted" in chunks[0]


def test_abort_falls_back_to_default_message():
    _, outcome = _drive({"handled": False, "abort": True, "message": ""})
    assert outcome.abort_message == "工作流已被命令打断"


def test_abort_keeps_step_index_unchanged():
    _, outcome = _drive({"handled": False, "abort": True, "message": "x"}, step_index=4)
    assert outcome.step_index == 4


def test_abort_takes_precedence_over_handled():
    """abort 与 handled 同时为真时，abort 优先（原块的 if 顺序）。"""
    chunks, outcome = _drive({"handled": True, "abort": True, "message": "停"})
    assert outcome.aborted is True
    assert len(chunks) == 1


# ---------------------------------------------------------------------------
# 出口 3/4：已处理 —— 跳过本轮，step_index 可能前移
# ---------------------------------------------------------------------------


def test_handled_without_jump_continues_at_same_index():
    _, outcome = _drive({"handled": True, "abort": False}, step_index=3)
    assert (outcome.aborted, outcome.handled) == (False, True)
    assert outcome.step_index == 3


def test_handled_jumps_to_stream_step_when_page_is_generating():
    host = _InterruptHost(next_stream_index=5)
    _, outcome = _drive(
        {"handled": True, "abort": False},
        step_index=2,
        executor=_FakeExecutor(generating=True),
        host=host,
    )
    assert outcome.handled is True
    assert outcome.step_index == 5, "页面已在生成时应跳到监听步骤"
    assert host.stream_lookups == [2]


def test_handled_jumps_to_resume_step_when_no_stream_detected():
    host = _InterruptHost(resume_index=4)
    _, outcome = _drive({"handled": True, "abort": False}, step_index=2, host=host)
    assert outcome.step_index == 4
    assert host.resume_lookups == [2]


def test_stream_jump_is_skipped_when_page_is_not_generating():
    host = _InterruptHost(next_stream_index=5)
    _, outcome = _drive(
        {"handled": True, "abort": False},
        step_index=2,
        executor=_FakeExecutor(generating=False),
        host=host,
    )
    assert host.stream_lookups == [], "页面未在生成时不应查找监听步骤"


def test_flow_program_suppresses_both_lookups():
    """有 flow_program 时不走"恢复/跳过"探测，step_index 原样返回。"""
    host = _InterruptHost(next_stream_index=5, resume_index=4)
    _, outcome = _drive(
        {"handled": True, "abort": False},
        step_index=2,
        flow_program=object(),
        executor=_FakeExecutor(generating=True),
        host=host,
    )
    assert (host.stream_lookups, host.resume_lookups) == ([], [])
    assert outcome.step_index == 2
    assert outcome.handled is True


def test_same_index_is_not_treated_as_a_jump():
    """目标索引与当前相同时不应被当作跳转（原块用 != 判断）。"""
    host = _InterruptHost(next_stream_index=2)
    _, outcome = _drive(
        {"handled": True, "abort": False}, step_index=2, executor=_FakeExecutor(generating=True), host=host
    )
    assert outcome.step_index == 2


# ---------------------------------------------------------------------------
# 恢复期的容错（原块用 try/except 吞掉探测异常）
# ---------------------------------------------------------------------------


def test_generating_probe_failure_degrades_to_resume_lookup():
    host = _InterruptHost(resume_index=4)
    _, outcome = _drive(
        {"handled": True, "abort": False},
        step_index=1,
        executor=_FakeExecutor(raise_on_generating=True),
        host=host,
    )
    assert host.stream_lookups == []
    assert outcome.step_index == 4


def test_listener_rebuild_failure_is_swallowed():
    class _Boom(_FakeExecutor):
        def rebuild_network_listener_after_external_interruption(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("rebuild failed")

    _, outcome = _drive({"handled": True, "abort": False}, executor=_Boom())
    assert outcome.handled is True


def test_rebuild_allows_dom_image_resume_for_stream_steps():
    ex = _FakeExecutor()
    workflow = [{"action": "FILL_INPUT"}, {"action": "CLICK"}, {"action": "STREAM_OUTPUT"}]
    _drive({"handled": True, "abort": False}, step_index=2, workflow=workflow, executor=ex)
    assert ex.rebuild_calls == [("workflow_interrupt_resume", True)]


def test_rebuild_disallows_dom_image_resume_for_non_stream_steps():
    ex = _FakeExecutor()
    workflow = [{"action": "FILL_INPUT"}, {"action": "CLICK"}]
    _drive({"handled": True, "abort": False}, step_index=1, workflow=workflow, executor=ex)
    assert ex.rebuild_calls == [("workflow_interrupt_resume", False)]


# ---------------------------------------------------------------------------
# 契约锁定
# ---------------------------------------------------------------------------


def test_outcome_is_a_four_field_namedtuple():
    assert _InterruptOutcome._fields == ("aborted", "handled", "step_index", "abort_message")


def test_method_is_a_generator_returning_an_outcome():
    fn = workflow_mod.BrowserWorkflowMixin._handle_workflow_interrupt
    assert inspect.isgeneratorfunction(fn)
    params = tuple(inspect.signature(fn).parameters)
    assert params[0] == "self"
    assert params[1:] == (
        "session",
        "command_engine",
        "step_index",
        "workflow",
        "flow_program",
        "executor",
        "selectors",
    )


def test_caller_applies_the_outcome():
    """主循环必须按出口语义应用控制流，否则提取就失真了。"""
    src = inspect.getsource(workflow_mod.BrowserWorkflowMixin._execute_workflow_stream_once)
    assert "yield from self._handle_workflow_interrupt(" in src
    for needed in ("if outcome.aborted:", "workflow_aborted = True", "break", "if outcome.handled:", "continue"):
        assert needed in src, f"调用方缺少: {needed!r}"
