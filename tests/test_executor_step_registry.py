"""`WorkflowExecutor.execute_step` 注册表化的契约与分派测试。

execute_step 原为 474 行 / CC 115，主体是一条按 action 分派的 if/elif 链，
最大的 STREAM_WAIT/STREAM_OUTPUT 分支有 241 行。步骤 10 把它改成查
`_STEP_HANDLERS` 表 + 11 个 `_step_<action>` 方法（CC 115 -> 32）。

与 `_execute_action` 的注册表相比，这里有两个容易踩的差异，本文件逐一锁定：

1. **处理器不都是生成器。** 11 个里只有 `_step_stream` 会 yield 事件块，
   其余 10 个只是执行副作用后返回 None。直接 `yield from handler(...)`
   会抛 `TypeError: 'NoneType' object is not iterable`——这正是本次改动
   第一个版本跑出来的真实故障，故有专门的回归测试。
2. **未知动作不是错误。** 原 else 分支只记一行 debug 日志后正常结束，
   与 `_execute_action` 返回错误载荷的语义不同，不要"顺手统一"。
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any, Dict, Generator, List, Tuple

import pytest

import app.core.workflow.executor as executor_mod
from app.core.workflow.executor import _STEP_HANDLERS, WorkflowExecutor

HANDLERS = _STEP_HANDLERS
EXPECTED_PARAMS = ["action", "selector", "target_key", "value", "optional", "context", "execution"]


class _LoggerStub:
    """收集日志文案的极简 logger 桩。"""

    def __init__(self, sink: List[str]) -> None:
        self._sink = sink

    def _record(self, message: str) -> None:
        self._sink.append(str(message))

    def debug(self, message: str, *a: Any, **k: Any) -> None:
        self._record(message)

    def info(self, message: str, *a: Any, **k: Any) -> None:
        self._record(message)

    def warning(self, message: str, *a: Any, **k: Any) -> None:
        self._record(message)

    def error(self, message: str, *a: Any, **k: Any) -> None:
        self._record(message)

    def exception(self, message: str, *a: Any, **k: Any) -> None:
        self._record(message)


class _DispatchHost:
    """只替换 handler、不动其它逻辑的宿主，用于验证分派本身。"""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, tuple]] = []
        self.events: List[str] = []
        # execute_step 的前言会读写并在 finally 里恢复这个属性
        self._current_step_execution: Dict[str, Any] = {}
        for name in sorted(set(HANDLERS.values())):
            setattr(self, name, self._make_plain(name))

    def _make_plain(self, handler_name: str):
        def _handler(*args: Any) -> None:
            self.calls.append((handler_name, args))

        return _handler

    # 复用 execute_step 的分派逻辑，但不触碰真实步骤实现
    _check_cancelled = staticmethod(lambda: False)
    execute_step = WorkflowExecutor.execute_step


class _FakeExecutor(WorkflowExecutor):
    def __init__(self) -> None:
        # 绕过 __init__ 的真实例化（需要浏览器标签页）
        self._current_step_execution: Dict[str, Any] = {}

    def _check_cancelled(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# 契约
# ---------------------------------------------------------------------------


def test_every_registered_handler_name_exists():
    missing = [n for n in sorted(set(HANDLERS.values())) if not hasattr(WorkflowExecutor, n)]
    assert missing == [], f"注册表指向不存在的方法: {missing}"


def test_every_handler_has_the_uniform_signature():
    for name in sorted(set(HANDLERS.values())):
        params = [p for p in inspect.signature(getattr(WorkflowExecutor, name)).parameters if p != "self"]
        assert params == EXPECTED_PARAMS, f"{name} 签名为 {params}"


def test_stream_actions_share_one_handler():
    assert HANDLERS["STREAM_WAIT"] == HANDLERS["STREAM_OUTPUT"] == "_step_stream"


def test_registry_covers_twelve_actions():
    assert len(HANDLERS) == 12


def test_only_the_stream_handler_is_a_generator():
    """这条不准轻易变动：分派代码依赖"非流式处理器返回 None"这一事实。"""
    gens = sorted(
        n for n in set(HANDLERS.values()) if inspect.isgeneratorfunction(getattr(WorkflowExecutor, n))
    )
    assert gens == ["_step_stream"]


# ---------------------------------------------------------------------------
# 分派行为
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", sorted(HANDLERS))
def test_every_action_is_dispatched_to_its_registered_handler(action: str):
    host = _DispatchHost()
    list(
        host.execute_step(
            action=action, selector="s", target_key="t", value="v", optional=False, context={}, execution={}
        )
    )
    assert len(host.calls) == 1
    assert host.calls[0][0] == HANDLERS[action]


def test_arguments_are_forwarded_in_order():
    host = _DispatchHost()
    list(host.execute_step(action="WAIT", selector="S", target_key="T", value="V", optional=True, context={"c": 1}, execution={"e": 2}))
    _, args = host.calls[0]
    assert args == ("WAIT", "S", "T", "V", True, {"c": 1}, {"e": 2})


def test_plain_handler_returning_none_does_not_break_dispatch():
    """回归：第一版直接 `yield from handler(...)`，对返回 None 的
    非流式处理器抛了 TypeError。这是当时跑出来的真实故障。"""
    host = _DispatchHost()
    chunks = list(host.execute_step(action="JS_EXEC", selector="", target_key="", value="1", context={}))
    assert chunks == []
    assert host.calls[0][0] == "_step_js_exec"


def test_generator_handler_events_are_forwarded():
    """流式步骤产出的事件块必须逐个透传出去。"""
    host = _DispatchHost()

    def _stream(*_args: Any) -> Generator[str, None, None]:
        yield "data: one\n\n"
        yield "data: two\n\n"

    host._step_stream = _stream
    chunks = list(host.execute_step(action="STREAM_WAIT", selector="", target_key="", value=None, context={}))
    assert chunks == ["data: one\n\n", "data: two\n\n"]


@pytest.mark.parametrize("action", ["STREAM_WAIT", "STREAM_OUTPUT"])
def test_both_stream_actions_forward_events(action: str):
    host = _DispatchHost()
    host._step_stream = lambda *_a: iter(["data: x\n\n"])
    assert list(host.execute_step(action=action, selector="", target_key="", value=None, context={})) == [
        "data: x\n\n"
    ]


# ---------------------------------------------------------------------------
# 未知动作：只记日志，不是错误
# ---------------------------------------------------------------------------


def test_unknown_action_is_silent_and_produces_nothing(monkeypatch):
    """未知动作只记一行 debug 后正常结束——不是错误，也不产出事件块。

    项目的 logger 自带 handler 且不向 root 传播，pytest caplog 抓不到，
    因此直接桩掉模块里的 logger。
    """
    logged: List[str] = []
    monkeypatch.setattr(executor_mod, "logger", _LoggerStub(logged))

    host = _DispatchHost()
    chunks = list(host.execute_step(action="NO_SUCH_ACTION", selector="", target_key="", value=None, context={}))
    assert chunks == []
    assert host.calls == []
    assert any("未知动作" in m and "NO_SUCH_ACTION" in m for m in logged)


@pytest.mark.parametrize("bad_action", [None, 123, ["WAIT"], {"a": 1}])
def test_non_string_action_does_not_raise(bad_action: Any):
    """dict 查找要求键可哈希；原 if/elif 链只会失配后落到 else。"""
    host = _DispatchHost()
    chunks = list(host.execute_step(action=bad_action, selector="", target_key="", value=None, context={}))
    assert chunks == []
    assert host.calls == []


# ---------------------------------------------------------------------------
# 结构
# ---------------------------------------------------------------------------


def test_no_if_elif_chain_left_in_execute_step():
    import textwrap as tw

    fn = WorkflowExecutor.execute_step
    tree = ast.parse(tw.dedent(inspect.getsource(fn))).body[0]
    stmts = [s for s in tree.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    code = "\n".join(ast.unparse(s) for s in stmts)
    assert "elif" not in code, "步骤派发链应已完全改为查表"
    assert "_STEP_HANDLERS" in code


def test_handlers_are_declared_at_module_level():
    assert isinstance(HANDLERS, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in HANDLERS.items())


def test_no_step_handler_is_left_unreferenced():
    """定义了却没登记在表里的 _step_* 方法是死代码。"""
    defined = {n for n in dir(WorkflowExecutor) if n.startswith("_step_")}
    registered = set(HANDLERS.values())
    assert defined == registered, f"未登记: {sorted(defined - registered)}；登记但缺失: {sorted(registered - defined)}"


def test_dispatch_block_is_small():
    """分派本身必须保持很小。

    execute_step 整体仍有 ~100 行，但那是四个 except 分支的错误处理，
    与派发无关；这里只量 try 体里的派发部分，防止有人把步骤逻辑搬回来。
    """
    src = textwrap.dedent(inspect.getsource(WorkflowExecutor.execute_step)).split("\n")
    start = next(i for i, l in enumerate(src) if l.strip() == "try:")
    end = next(i for i, l in enumerate(src[start:], start) if l.strip().startswith("except "))
    body = [l for l in src[start + 1 : end] if l.strip() and not l.strip().startswith("#")]
    assert len(body) <= 12, f"派发块回涨到 {len(body)} 行"
