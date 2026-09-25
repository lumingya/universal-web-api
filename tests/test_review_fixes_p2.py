"""审查修复回归测试 · 第二批（P2）。

全部使用合成数据、临时目录与桩对象；不依赖真实浏览器、CDP 或外网。
"""

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# B8 · 命令配置短暂损坏不清空 last-known-good
# ---------------------------------------------------------------------------

def _bump_mtime(path: Path, delta: float) -> None:
    st = path.stat()
    os.utime(path, (st.st_atime + delta, st.st_mtime + delta))


def test_b8_corrupt_commands_json_keeps_last_known_good(tmp_path, monkeypatch):
    from app.services.command_engine import CommandEngine

    monkeypatch.delenv("CMD_ENGINE_AUTO_START", raising=False)
    commands_file = tmp_path / "commands.json"
    local_file = tmp_path / "commands.local.json"
    command = {
        "id": "cmd-1", "name": "inject", "enabled": True,
        "trigger": {"type": "request_count", "value": 1},
        "actions": [{"type": "run_js_file", "file_path": "custom_scripts/_review_b8.js"}],
    }
    commands_file.write_text(json.dumps({"commands": [command]}), encoding="utf-8")

    engine = CommandEngine()
    try:
        engine._commands_file = str(commands_file)
        engine._commands_local_file = str(local_file)
        cleanup_calls = []
        monkeypatch.setattr(engine, "_cleanup_disabled_run_js_file_actions",
                            lambda actions: cleanup_calls.append(list(actions)))

        engine._refresh_commands_if_changed(force=True)
        assert [c["id"] for c in engine._commands_cache] == ["cmd-1"]
        good_mtime = engine._commands_mtime

        # 编辑器半写入 → 损坏 JSON
        commands_file.write_text('{"commands": [ {"id": "cmd-1", ', encoding="utf-8")
        _bump_mtime(commands_file, 5)
        engine._refresh_commands_if_changed()
        assert [c["id"] for c in engine._commands_cache] == ["cmd-1"]
        assert engine._commands_mtime == good_mtime          # 未推进 mtime
        assert all(not actions for actions in cleanup_calls)  # 没有产生清理动作

        # 同一损坏文件不会反复读取
        with mock.patch.object(engine, "_read_commands_file", wraps=engine._read_commands_file) as reader:
            engine._refresh_commands_if_changed()
            reader.assert_not_called()

        # 结构错误（不是 list）同样视为读失败
        commands_file.write_text('{"commands": {"oops": 1}}', encoding="utf-8")
        _bump_mtime(commands_file, 10)
        engine._refresh_commands_if_changed()
        assert [c["id"] for c in engine._commands_cache] == ["cmd-1"]

        # 修复为合法新配置 → 正常切换
        command2 = dict(command, id="cmd-2", name="other", actions=[])
        commands_file.write_text(json.dumps({"commands": [command2]}), encoding="utf-8")
        _bump_mtime(commands_file, 15)
        engine._refresh_commands_if_changed()
        assert [c["id"] for c in engine._commands_cache] == ["cmd-2"]
        # 对照：合法切换确实会产生 run_js_file 清理动作（证明上面的「无清理」断言有效）
        assert cleanup_calls and cleanup_calls[-1]

        # 合法的空配置仍然会清空（与读失败区分）
        commands_file.write_text(json.dumps({"commands": []}), encoding="utf-8")
        _bump_mtime(commands_file, 20)
        engine._refresh_commands_if_changed()
        assert engine._commands_cache == []
    finally:
        engine.shutdown()


# ---------------------------------------------------------------------------
# B5 · 解冻失败不交付 BUSY 标签页
# ---------------------------------------------------------------------------

class _FakeCdpTab:
    def __init__(self, fail: bool):
        self.fail = fail
        self.calls = []

    def run_cdp(self, method, **params):
        self.calls.append((method, params))
        if method == "Page.setWebLifecycleState" and self.fail:
            raise RuntimeError("synthetic CDP failure")
        return {}


def _frozen_session(fail: bool):
    from app.core.tab_pool_parts.session import TabSession

    session = TabSession(id="tab-b5", tab=_FakeCdpTab(fail))
    session._uwapi_frozen = True
    session._uwapi_frozen_at = time.time() - 120
    return session


def test_b5_unfreeze_failure_does_not_hand_out_session():
    from app.core.tab_pool_parts.session import TabStatus

    session = _frozen_session(fail=True)
    assert session.acquire("task-1") is False
    assert session.status == TabStatus.IDLE
    assert session.current_task_id is None
    assert session.request_count == 0
    assert session._uwapi_frozen is True            # 保留待确认冻结态

    # 冷却期内直接跳过，不再发 CDP
    calls_before = len(session.tab.calls)
    assert session.acquire_for_command("cmd-1") is False
    assert len(session.tab.calls) == calls_before

    # 冷却期过后 CDP 恢复正常 → 可正常交付并清除冻结
    session._uwapi_resume_failed_at = time.time() - 60
    session.tab.fail = False
    assert session.acquire("task-2") is True
    assert session.status == TabStatus.BUSY
    assert session._uwapi_frozen is False
    assert session.request_count == 1


def test_b5_command_acquire_rolls_back_without_touching_request_count():
    from app.core.tab_pool_parts.session import TabStatus

    session = _frozen_session(fail=True)
    session.request_count = 7
    assert session.acquire_for_command("cmd-1") is False
    assert session.status == TabStatus.IDLE and session.request_count == 7


def test_b5_unfrozen_session_acquire_is_unchanged():
    from app.core.tab_pool_parts.session import TabSession, TabStatus

    session = TabSession(id="tab-ok", tab=_FakeCdpTab(fail=True))
    assert session.acquire("task") is True
    assert session.status == TabStatus.BUSY
    assert session.tab.calls == []   # 未冻结时不发任何 CDP


# ---------------------------------------------------------------------------
# B2 · 旧版顶层数组历史恢复
# ---------------------------------------------------------------------------

def _bare_request_manager(tmp_path, monkeypatch, max_records=200):
    from app.services.request_manager import RequestManager

    rm = object.__new__(RequestManager)
    rm._history_file = str(tmp_path / "request_history.json")
    rm._monitor_history = []
    rm._history_revision_cache = None
    rm.total_input_tokens = 0
    rm.total_output_tokens = 0
    monkeypatch.setattr(rm, "_request_monitor_enabled", lambda: True, raising=False)
    monkeypatch.setattr(rm, "_request_monitor_max_records", lambda: max_records, raising=False)
    return rm


def _history_record(i):
    return {"request_id": f"req-{i}", "created_at": 1000.0 + i, "prompt": f"p{i}",
            "response": f"r{i}", "status": "completed",
            "token_estimate": {"prompt": 3, "response": 5}}


def test_b2_legacy_top_level_array_history_is_restored(tmp_path, monkeypatch):
    rm = _bare_request_manager(tmp_path, monkeypatch)
    Path(rm._history_file).write_text(json.dumps([_history_record(1), _history_record(2), "junk"]),
                                      encoding="utf-8")
    rm._load_history()
    assert [r["request_id"] for r in rm._monitor_history] == ["req-1", "req-2"]
    assert rm.total_input_tokens == 6 and rm.total_output_tokens == 10


def test_b2_object_format_still_works_and_zero_limit_clears(tmp_path, monkeypatch):
    rm = _bare_request_manager(tmp_path, monkeypatch)
    Path(rm._history_file).write_text(json.dumps({"records": [_history_record(1)]}), encoding="utf-8")
    rm._load_history()
    assert [r["request_id"] for r in rm._monitor_history] == ["req-1"]

    rm0 = _bare_request_manager(tmp_path, monkeypatch, max_records=0)
    rm0._load_history()
    assert rm0._monitor_history == []   # lst[-0:] 陷阱


# ---------------------------------------------------------------------------
# B1 · 搜索引擎主域禁自动发现，AI 子域保留
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain,allowed", [
    ("google.de", False), ("www.google.com.br", False), ("cn.bing.com", False), ("yandex.ru", False),
    ("duckduckgo.com", False), ("www.baidu.com", False),
    ("gemini.google.com", True), ("aistudio.google.com", True), ("google.com.attacker.org", True),
    ("notgoogle.com", True), ("copilot.microsoft.com", True),
])
def test_b1_builtin_search_hosts(domain, allowed):
    from app.utils.site_discovery import automatic_discovery_allowed

    assert automatic_discovery_allowed(domain) is allowed


def test_b1_explicit_rule_overrides_builtin(monkeypatch):
    from app.utils import site_discovery

    monkeypatch.setattr(site_discovery, "get_site_rule",
                        lambda host: {"auto_discovery": True} if host == "google.de" else {})
    assert site_discovery.automatic_discovery_allowed("google.de") is True


# ---------------------------------------------------------------------------
# B3 · Responses 续接历史字节预算 + 主体隔离
# ---------------------------------------------------------------------------

@pytest.fixture
def responses_state(monkeypatch):
    from app.api import chat

    monkeypatch.setattr(chat, "_responses_state_by_id", chat.OrderedDict())
    monkeypatch.setattr(chat, "_responses_state_total_bytes", 0)
    return chat


def _payload(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def test_b3_entry_over_budget_is_not_stored_and_resume_returns_413(responses_state, monkeypatch):
    chat = responses_state
    monkeypatch.setattr(chat, "RESPONSES_STATE_MAX_ENTRY_BYTES", 1024)
    chat._store_responses_state("resp_big", [{"role": "user", "content": "x" * 5000}],
                                _payload("ok"), enabled=True)
    assert chat._responses_state_total_bytes == 0
    with pytest.raises(chat.HTTPException) as exc:
        chat._load_responses_state("resp_big")
    assert exc.value.status_code == 413


def test_b3_total_budget_evicts_lru(responses_state, monkeypatch):
    chat = responses_state
    monkeypatch.setattr(chat, "RESPONSES_STATE_MAX_ENTRY_BYTES", 10_000)
    monkeypatch.setattr(chat, "RESPONSES_STATE_MAX_TOTAL_BYTES", 5_000)
    for i in range(5):
        chat._store_responses_state(f"resp_{i}", [{"role": "user", "content": "y" * 1500}],
                                    _payload("ok"), enabled=True)
    assert chat._responses_state_total_bytes <= 5_000
    assert "resp_0" not in chat._responses_state_by_id
    assert "resp_4" in chat._responses_state_by_id
    assert sum(e[3] for e in chat._responses_state_by_id.values()) == chat._responses_state_total_bytes
    history = chat._load_responses_state("resp_4")
    assert history[-1]["role"] == "assistant"


def test_b3_state_is_bound_to_principal(responses_state):
    chat = responses_state

    class Req:
        def __init__(self, headers):
            self.headers = headers

    alice = chat._responses_principal_from_request(Req({"authorization": "Bearer alice-token"}))
    bob = chat._responses_principal_from_request(Req({"x-api-key": "bob-token"}))
    anon = chat._responses_principal_from_request(Req({}))
    assert alice and bob and alice != bob and anon == ""
    assert "alice-token" not in alice

    chat._store_responses_state("resp_a", [{"role": "user", "content": "secret"}],
                                _payload("ok"), enabled=True, principal=alice)
    assert chat._load_responses_state("resp_a", alice)[0]["content"] == "secret"
    for other in (bob, anon):
        with pytest.raises(chat.HTTPException) as exc:
            chat._load_responses_state("resp_a", other)
        assert exc.value.status_code == 404


def test_b3_store_false_keeps_nothing(responses_state):
    chat = responses_state
    chat._store_responses_state("resp_x", [{"role": "user", "content": "hi"}], _payload("ok"), enabled=False)
    assert "resp_x" not in chat._responses_state_by_id
