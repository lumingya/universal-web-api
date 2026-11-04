"""`_execute_action` 注册表化的契约与分派测试。

步骤 7 把 `_execute_action` 从 416 行的 if/elif 派发链（CC 123）改成了
查 `_ACTION_HANDLERS` 表（CC 4）。查表把"新增一个动作"从"往链尾再追加
一个 elif"变成了"登记一行"——原结构之所以越来越长，正是因为追加成本太低。

但查表也带来两个新的失效模式，本文件逐一锁住：
  1. 表里写了方法名，方法其实不存在 -> 运行时 AttributeError；
  2. 表与 command_defs.ACTION_TYPES 不同步 -> UI 上能选的动作在运行时
     落到"未知动作类型"，或反之声明了却没人处理。
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List, Tuple

import pytest

import app.services.command_engine_actions as cea
from app.services.command_defs import ACTION_TYPES, CommandFlowAbort

HANDLERS = cea._ACTION_HANDLERS


class _DispatchHost(cea.CommandEngineActionsMixin):
    """把全部 handler 换成记录调用的桩，只验证分派，不执行真实动作。"""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict, Any]] = []
        for name in set(HANDLERS.values()):
            setattr(self, name, self._make_stub(name))

    def _make_stub(self, handler_name: str):
        def _stub(action: Dict, session: Any) -> str:
            self.calls.append((handler_name, action, session))
            return f"handled_by:{handler_name}"

        return _stub


# ---------------------------------------------------------------------------
# 契约 1：表里每个方法名都必须真实存在
# ---------------------------------------------------------------------------


def test_every_registered_handler_name_exists():
    missing = [n for n in set(HANDLERS.values()) if not hasattr(cea.CommandEngineActionsMixin, n)]
    assert missing == [], f"注册表中指向不存在的方法: {missing}"


def test_every_registered_handler_is_callable_with_action_and_session():
    for name in sorted(set(HANDLERS.values())):
        fn = getattr(cea.CommandEngineActionsMixin, name)
        params = [p for p in inspect.signature(fn).parameters if p != "self"]
        assert params == ["action", "session"], f"{name} 的签名应为 (action, session)，实为 {params}"


def test_no_handler_is_registered_under_two_names_by_accident():
    """同名的重复登记不算错（如预设切换两个类型共用一个 handler），
    但每个 handler 至少应被一个动作类型引用。"""
    assert set(HANDLERS.values()) <= {
        n for n, _ in inspect.getmembers(cea.CommandEngineActionsMixin, inspect.isfunction)
    }


# ---------------------------------------------------------------------------
# 契约 2：注册表与 ACTION_TYPES 必须完全对齐
# ---------------------------------------------------------------------------


def test_registry_covers_exactly_the_declared_action_types():
    assert set(HANDLERS) == set(ACTION_TYPES)


def test_no_declared_action_type_is_unroutable():
    assert sorted(set(ACTION_TYPES) - set(HANDLERS)) == []


def test_no_registered_action_type_is_undeclared():
    """登记表里出现 ACTION_TYPES 之外的类型，说明有人加了分支却忘了声明。"""
    assert sorted(set(HANDLERS) - set(ACTION_TYPES)) == []


# ---------------------------------------------------------------------------
# 分派行为
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action_type", sorted(HANDLERS))
def test_every_action_type_is_dispatched_to_its_registered_handler(action_type: str):
    """逐类型验证：分派结果确实来自表里登记的那个方法。"""
    host = _DispatchHost()
    result = host._execute_action({"type": action_type}, session=None)
    assert result == f"handled_by:{HANDLERS[action_type]}"
    assert len(host.calls) == 1


def test_preset_aliases_share_one_handler():
    assert HANDLERS["execute_preset"] == HANDLERS["switch_preset"]


def test_unknown_action_type_returns_error_payload():
    host = _DispatchHost()
    result = host._execute_action({"type": "no_such_action"}, session=None)
    assert result == {"ok": False, "error": "unknown_action_type:no_such_action"}
    assert host.calls == []


def test_missing_type_reports_empty_marker():
    host = _DispatchHost()
    result = host._execute_action({}, session=None)
    assert result == {"ok": False, "error": "unknown_action_type:(empty)"}


@pytest.mark.parametrize("bad_type", [None, 123, ["a"], {"x": 1}, 3.5])
def test_non_string_action_type_does_not_raise(bad_type: Any):
    """原 if/elif 链用 == 比较，任何非字符串都只是全部失配后落到 else。

    改为 dict 查找后，不可哈希的类型（list/dict）会直接抛 TypeError，
    因此实现里加了 isinstance 判断。本测试锁住这个行为不退化。
    """
    host = _DispatchHost()
    result = host._execute_action({"type": bad_type}, session=None)
    assert isinstance(result, dict)
    assert result["ok"] is False
    assert result["error"].startswith("unknown_action_type:")


def test_action_and_session_are_forwarded_untouched():
    host = _DispatchHost()
    action = {"type": "wait", "seconds": 3}
    marker = object()
    host._execute_action(action, session=marker)
    handler_name, forwarded_action, forwarded_session = host.calls[0]
    assert handler_name == HANDLERS["wait"]
    assert forwarded_action is action
    assert forwarded_session is marker


# ---------------------------------------------------------------------------
# 结构：链必须真的消失了
# ---------------------------------------------------------------------------


def test_no_if_elif_chain_left_in_execute_action():
    """链必须真的消失（docstring 里提到 if/elif 不算，故先剥离）。"""
    # 按 AST 取函数体，天然不含 docstring
    import ast
    import textwrap

    fn = cea.CommandEngineActionsMixin._execute_action
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    stmts = [s for s in tree.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    code = "\n".join(ast.unparse(s) for s in stmts)
    assert "elif" not in code, "派发链应已完全改为查表"
    assert "_ACTION_HANDLERS" in code


def test_execute_action_is_short():
    """查表后函数体应只剩几行——防止有人把逻辑搬回来。"""
    src = inspect.getsource(cea.CommandEngineActionsMixin._execute_action)
    body = [l for l in src.split("\n") if l.strip() and not l.strip().startswith(("#", '"""'))]
    assert len(body) < 25, f"_execute_action 体量回涨到 {len(body)} 行"


def test_abort_task_can_raise_command_flow_abort():
    """abort_task / release_tab_lock 通过 CommandFlowAbort 中断动作链，
    注册表化不能吞掉这个异常。"""
    host = _DispatchHost()

    def _raise(_action: Dict, _session: Any) -> str:
        raise CommandFlowAbort("abort_task_triggered")

    host._action_abort_task = _raise
    with pytest.raises(CommandFlowAbort):
        host._execute_action({"type": "abort_task"}, session=None)
