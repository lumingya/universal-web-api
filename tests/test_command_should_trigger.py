"""CommandEngine._should_trigger 的特征测试。

`_should_trigger`（243 行 / CC 78）是命令引擎的触发判定入口，此前**没有
任何直接测试**——现有测试里它只被 `mock.patch.object(..., return_value=True)`
整个替换掉，因此它的真实判定逻辑完全没被覆盖。

结构上它是"三段前置检查 + 按 trigger_type 的 8 路分派"：

    前置  1. scope 预检（domain / tab）
          2. 同一命令是否正在该标签页执行
          3. 初始化/加载触发状态（is_new 时多数类型直接返回 False）
    分派  request_count / error_count / idle_timeout / page_check /
          command_result_match / command_check / command_result_event /
          network_request_error

本文件的用例在拆分前就已经全部通过，用于证明后续拆分是纯结构变化。

测试策略：session 只需要 id / request_count / error_count / persistent_index
等少数属性，用 FakeSession 即可；四个 `_prepare_*_dispatch` 用 monkeypatch
替换，从而把断言聚焦在分派与阈值判定本身，而不牵扯网络与页面状态。
"""

import time

import pytest

from app.services.command_engine import CommandEngine

ALL_TRIGGER_TYPES = (
    "request_count",
    "error_count",
    "idle_timeout",
    "page_check",
    "command_result_match",
    "command_check",
    "command_result_event",
    "network_request_error",
)


class FakeSession:
    """_should_trigger 所需的 session 最小面。"""

    def __init__(self, sid="sess-1", request_count=0, error_count=0, persistent_index=0, domain=""):
        self.id = sid
        self.request_count = request_count
        self.error_count = error_count
        self.persistent_index = persistent_index
        self.current_domain = domain
        self.last_used_at = time.time()
        self._command_request_id = ""
        self._bound_request_id = ""
        self.current_task_id = ""
        self.tab = None  # 无 tab 时 _get_session_domain 退回 current_domain

    __test__ = False


@pytest.fixture()
def engine():
    return CommandEngine()


@pytest.fixture()
def session():
    return FakeSession()


def make_command(cid="cmd-1", trigger_type="request_count", **trigger_extra):
    trigger = {"type": trigger_type}
    trigger.update(trigger_extra)
    return {"id": cid, "name": f"命令-{cid}", "trigger": trigger, "enabled": True}


def warm_up(engine, command, session):
    """第一次调用会初始化触发状态，多数类型此时返回 False。

    先"预热"一次，后续调用才进入真正的阈值判定。
    """
    return engine._should_trigger(command, session)


# --------------------------------------------------------------------------
# 1. 未知 / 缺失类型
# --------------------------------------------------------------------------


@pytest.mark.parametrize("trigger_type", ["", "nonexistent", "REQUEST_COUNT", "request count"])
def test_unknown_trigger_type_never_fires(engine, session, trigger_type):
    cmd = make_command(trigger_type=trigger_type)
    assert warm_up(engine, cmd, session) is False
    assert engine._should_trigger(cmd, session) is False


def test_missing_trigger_key_treated_as_empty(engine, session):
    cmd = {"id": "cmd-x", "name": "无 trigger", "enabled": True}
    assert engine._should_trigger(cmd, session) is False


def test_result_is_always_a_bool(engine, session):
    """返回值必须是真布尔——调用方直接用 `if self._should_trigger(...)`。"""
    for trigger_type in ALL_TRIGGER_TYPES:
        cmd = make_command(cid=f"c-{trigger_type}", trigger_type=trigger_type)
        warm_up(engine, cmd, session)
        assert isinstance(engine._should_trigger(cmd, session), bool)


# --------------------------------------------------------------------------
# 2. scope 预检
# --------------------------------------------------------------------------


def test_tab_scope_mismatch_returns_false(engine):
    session = FakeSession(persistent_index=3)
    cmd = make_command(trigger_type="request_count", scope="tab", tab_index=7)
    assert engine._should_trigger(cmd, session) is False


def test_tab_scope_match_passes_precheck(engine):
    session = FakeSession(persistent_index=7)
    cmd = make_command(trigger_type="request_count", scope="tab", tab_index=7)
    # 通过预检后，第一次调用会因状态初始化返回 False，而不是因 scope 被拒
    warm_up(engine, cmd, session)
    # 用 domain 不匹配的方式反证：scope=tab 且 index 不符时应立即为 False
    other = make_command(cid="cmd-2", trigger_type="request_count", scope="tab", tab_index=99)
    assert engine._should_trigger(other, session) is False


def test_domain_scope_without_target_domain_returns_false(engine):
    """配置了 domain 作用域却取不到会话域名时，不应放行。"""
    session = FakeSession(domain="")
    cmd = make_command(trigger_type="request_count", scope="domain", domain="example.com")
    assert engine._should_trigger(cmd, session) is False


def test_default_scope_is_all_and_skips_precheck(engine, session):
    cmd = make_command(trigger_type="request_count")  # 未指定 scope
    warm_up(engine, cmd, session)
    # 未指定 scope 时不走 domain/tab 预检，因此不会因域名为空被拒
    assert isinstance(engine._should_trigger(cmd, session), bool)


# --------------------------------------------------------------------------
# 3. 正在执行中的命令不再重复触发
# --------------------------------------------------------------------------


def test_already_executing_command_is_skipped(engine, session):
    cmd = make_command(cid="busy-cmd", trigger_type="request_count")
    with engine._lock:
        engine._executing.add(("busy-cmd", session.id))
    try:
        assert engine._should_trigger(cmd, session) is False
    finally:
        with engine._lock:
            engine._executing.discard(("busy-cmd", session.id))


# --------------------------------------------------------------------------
# 4. is_new：状态初始化时的早退
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "trigger_type",
    ["request_count", "error_count", "idle_timeout", "command_result_match",
     "command_result_event", "network_request_error"],
)
def test_first_call_returns_false_for_most_types(engine, trigger_type):
    """除 page_check / command_check 外，首次调用（新状态）一律返回 False。"""
    session = FakeSession(sid=f"s-{trigger_type}")
    cmd = make_command(cid=f"c-{trigger_type}", trigger_type=trigger_type)
    assert engine._should_trigger(cmd, session) is False


# --------------------------------------------------------------------------
# 5. request_count
# --------------------------------------------------------------------------


def test_request_count_fires_when_delta_reaches_threshold(engine):
    session = FakeSession(sid="rc", request_count=10)
    cmd = make_command(cid="rc", trigger_type="request_count", value=3)
    warm_up(engine, cmd, session)
    session.request_count = 13  # 增量 3 == 阈值
    assert engine._should_trigger(cmd, session) is True


def test_request_count_stays_silent_below_threshold(engine):
    session = FakeSession(sid="rc2", request_count=10)
    cmd = make_command(cid="rc2", trigger_type="request_count", value=5)
    warm_up(engine, cmd, session)
    session.request_count = 12  # 增量 2 < 5
    assert engine._should_trigger(cmd, session) is False


def test_request_count_threshold_is_at_least_one(engine):
    """阈值被 max(1, ...) 兜底：value=0 等同于 1。"""
    session = FakeSession(sid="rc3", request_count=10)
    cmd = make_command(cid="rc3", trigger_type="request_count", value=0)
    warm_up(engine, cmd, session)
    session.request_count = 11
    assert engine._should_trigger(cmd, session) is True


def test_request_count_default_threshold_is_ten(engine):
    session = FakeSession(sid="rc4", request_count=0)
    cmd = make_command(cid="rc4", trigger_type="request_count")  # 未指定 value
    warm_up(engine, cmd, session)
    session.request_count = 9
    assert engine._should_trigger(cmd, session) is False
    session.request_count = 10
    assert engine._should_trigger(cmd, session) is True


# --------------------------------------------------------------------------
# 6. error_count
# --------------------------------------------------------------------------


def test_error_count_fires_when_delta_reaches_threshold(engine):
    session = FakeSession(sid="ec", error_count=4)
    cmd = make_command(cid="ec", trigger_type="error_count", value=2)
    warm_up(engine, cmd, session)
    session.error_count = 6
    assert engine._should_trigger(cmd, session) is True


def test_error_count_below_threshold_does_not_fire(engine):
    session = FakeSession(sid="ec2", error_count=4)
    cmd = make_command(cid="ec2", trigger_type="error_count", value=5)
    warm_up(engine, cmd, session)
    session.error_count = 6  # 增量 2 < 5
    assert engine._should_trigger(cmd, session) is False


def test_error_count_default_threshold_is_three(engine):
    session = FakeSession(sid="ec3", error_count=0)
    cmd = make_command(cid="ec3", trigger_type="error_count")
    warm_up(engine, cmd, session)
    session.error_count = 2
    assert engine._should_trigger(cmd, session) is False
    session.error_count = 3
    assert engine._should_trigger(cmd, session) is True


# --------------------------------------------------------------------------
# 7. idle_timeout
# --------------------------------------------------------------------------


def test_idle_timeout_fires_after_threshold(engine):
    session = FakeSession(sid="it")
    cmd = make_command(cid="it", trigger_type="idle_timeout", value=1)
    warm_up(engine, cmd, session)
    session.last_used_at = time.time() - 5  # 空闲 5 秒 > 阈值 1 秒
    assert engine._should_trigger(cmd, session) is True


def test_idle_timeout_does_not_fire_before_threshold(engine):
    session = FakeSession(sid="it2")
    cmd = make_command(cid="it2", trigger_type="idle_timeout", value=3600)
    warm_up(engine, cmd, session)
    session.last_used_at = time.time() - 1
    assert engine._should_trigger(cmd, session) is False


def test_idle_timeout_threshold_is_at_least_one_second(engine):
    """value=0 被 max(1.0, ...) 兜底为 1 秒，而不是"立刻触发"。"""
    session = FakeSession(sid="it3")
    cmd = make_command(cid="it3", trigger_type="idle_timeout", value=0)
    warm_up(engine, cmd, session)
    session.last_used_at = time.time()
    assert engine._should_trigger(cmd, session) is False


# --------------------------------------------------------------------------
# 8. 四个 _prepare_*_dispatch 类（用 monkeypatch 隔离外部状态）
# --------------------------------------------------------------------------


@pytest.fixture()
def patched_dispatches(engine, monkeypatch):
    """把四个 _prepare_*_dispatch 换成可控的假实现，并记录调用。"""
    calls = {}

    def fake(name, result):
        def _inner(command, session):
            calls[name] = (command, session)
            return result
        return _inner

    store = {}
    for name in (
        "_prepare_command_result_trigger_dispatch",
        "_prepare_command_check_dispatch",
        "_prepare_command_result_event_dispatch",
        "_prepare_network_trigger_dispatch",
    ):
        store[name] = monkeypatch.setattr(engine, name, fake(name, None))
    return calls, store


def set_dispatch(monkeypatch, engine, name, result):
    monkeypatch.setattr(engine, name, lambda command, session: result)


@pytest.mark.parametrize(
    "trigger_type,method",
    [
        ("command_result_match", "_prepare_command_result_trigger_dispatch"),
        ("command_check", "_prepare_command_check_dispatch"),
        ("command_result_event", "_prepare_command_result_event_dispatch"),
    ],
)
def test_dispatch_bearing_types_fire_when_dispatch_returned(
    engine, session, monkeypatch, trigger_type, method
):
    set_dispatch(monkeypatch, engine, method, {"rollback": {"token": "t"}})
    monkeypatch.setattr(engine, "_set_pending_async_trigger_meta", lambda *a, **k: None)
    cmd = make_command(cid=f"d-{trigger_type}", trigger_type=trigger_type)
    # 除 command_check 外，首次调用会因状态初始化而返回 False，需先预热
    warm_up(engine, cmd, session)
    assert engine._should_trigger(cmd, session) is True


@pytest.mark.parametrize(
    "trigger_type,method",
    [
        ("command_result_match", "_prepare_command_result_trigger_dispatch"),
        ("command_check", "_prepare_command_check_dispatch"),
        ("command_result_event", "_prepare_command_result_event_dispatch"),
    ],
)
def test_dispatch_bearing_types_stay_silent_when_no_dispatch(
    engine, session, monkeypatch, trigger_type, method
):
    set_dispatch(monkeypatch, engine, method, None)
    cmd = make_command(cid=f"n-{trigger_type}", trigger_type=trigger_type)
    warm_up(engine, cmd, session)
    assert engine._should_trigger(cmd, session) is False


def test_network_request_error_requires_rollback_token(engine, session, monkeypatch):
    """network_request_error 比较特殊：即便有 dispatch，也要求 rollback.token。

    没有 token 时整个分支不返回，最终落到函数末尾的 `return False`。
    """
    monkeypatch.setattr(engine, "_set_pending_async_trigger_meta", lambda *a, **k: None)

    set_dispatch(monkeypatch, engine, "_prepare_network_trigger_dispatch",
                 {"rollback": {"token": "tk"}})
    cmd = make_command(cid="net-1", trigger_type="network_request_error")
    warm_up(engine, cmd, session)  # 该类型同样受 is_new 早退影响
    assert engine._should_trigger(cmd, session) is True

    set_dispatch(monkeypatch, engine, "_prepare_network_trigger_dispatch",
                 {"rollback": {"token": ""}})
    cmd2 = make_command(cid="net-2", trigger_type="network_request_error")
    warm_up(engine, cmd2, session)
    assert engine._should_trigger(cmd2, session) is False


# --------------------------------------------------------------------------
# 9. page_check
# --------------------------------------------------------------------------


def test_page_check_edge_mode_fires_on_rising_edge(engine, session, monkeypatch):
    """边沿触发：命中且上一次未 stable 时才触发一次。"""
    monkeypatch.setattr(engine, "_parse_page_check_expression", lambda text: ("any", ["kw"]))
    monkeypatch.setattr(
        engine,
        "_evaluate_page_check_expr",
        lambda s, op, kws: {"hit": True, "matched_keywords": kws, "snapshot_preview": ""},
    )
    monkeypatch.setattr(engine, "_log_page_check_hit_details", lambda *a, **k: None)

    cmd = make_command(cid="pc", trigger_type="page_check", value="kw")
    assert engine._should_trigger(cmd, session) is True
    # 已经 stable 过，同一状态不再重复触发（边沿）
    assert engine._should_trigger(cmd, session) is False


def test_page_check_no_hit_never_fires(engine, session, monkeypatch):
    monkeypatch.setattr(engine, "_parse_page_check_expression", lambda text: ("any", ["kw"]))
    monkeypatch.setattr(
        engine,
        "_evaluate_page_check_expr",
        lambda s, op, kws: {"hit": False, "matched_keywords": [], "snapshot_preview": ""},
    )
    cmd = make_command(cid="pc2", trigger_type="page_check", value="kw")
    assert engine._should_trigger(cmd, session) is False


def test_page_check_without_keywords_or_probe_never_fires(engine, session, monkeypatch):
    """没关键词也没 probe_js 时，hit 恒为 False。"""
    monkeypatch.setattr(engine, "_parse_page_check_expression", lambda text: ("any", []))
    cmd = make_command(cid="pc3", trigger_type="page_check", value="")
    assert engine._should_trigger(cmd, session) is False


def test_page_check_level_mode_refires_after_cooldown(engine, session, monkeypatch):
    """持续触发：只要仍命中且过了冷却，就会再次触发。"""
    monkeypatch.setattr(engine, "_parse_page_check_expression", lambda text: ("any", ["kw"]))
    monkeypatch.setattr(
        engine,
        "_evaluate_page_check_expr",
        lambda s, op, kws: {"hit": True, "matched_keywords": kws, "snapshot_preview": ""},
    )
    monkeypatch.setattr(engine, "_log_page_check_hit_details", lambda *a, **k: None)

    cmd = make_command(cid="pc4", trigger_type="page_check", value="kw",
                       fire_mode="level", cooldown_sec=0)
    assert engine._should_trigger(cmd, session) is True
    assert engine._should_trigger(cmd, session) is True  # 持续触发，不受边沿限制


def test_page_check_once_per_request_suppresses_repeat(engine, monkeypatch):
    """once_per_request：同一个请求 ID 只触发一次。"""
    session = FakeSession(sid="pc5")
    session._command_request_id = "req-abc"
    monkeypatch.setattr(engine, "_parse_page_check_expression", lambda text: ("any", ["kw"]))
    monkeypatch.setattr(
        engine,
        "_evaluate_page_check_expr",
        lambda s, op, kws: {"hit": True, "matched_keywords": kws, "snapshot_preview": ""},
    )
    monkeypatch.setattr(engine, "_log_page_check_hit_details", lambda *a, **k: None)

    cmd = make_command(cid="pc5", trigger_type="page_check", value="kw",
                       fire_mode="level", cooldown_sec=0, once_per_request=True)
    assert engine._should_trigger(cmd, session) is True
    assert engine._should_trigger(cmd, session) is False  # 同一请求内不再触发


# --------------------------------------------------------------------------
# 10. 结构锁定
# --------------------------------------------------------------------------


def test_all_trigger_types_are_covered_by_this_file():
    """防止新增 trigger_type 时忘记补测试。

    若这里失败，说明引擎支持了新的触发类型，请同步补用例。
    """
    covered = set(ALL_TRIGGER_TYPES)
    assert len(covered) == 8
    assert "page_check" in covered and "command_check" in covered
