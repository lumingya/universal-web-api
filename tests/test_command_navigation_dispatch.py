import time
from types import SimpleNamespace
from unittest import mock
import pytest

from app.services.command_engine import CommandEngine
from app.core.page_lifecycle import notify_page_navigated


def test_is_command_applicable_on_navigation():
    engine = CommandEngine()
    try:
        # Disabled command
        assert engine._is_command_applicable_on_navigation({"enabled": False, "trigger": {"type": "page_check"}}) is False

        # Explicit check_on_navigation
        assert engine._is_command_applicable_on_navigation({"enabled": True, "trigger": {"type": "request_count", "check_on_navigation": True}}) is True
        assert engine._is_command_applicable_on_navigation({"enabled": True, "check_on_navigation": True, "trigger": {"type": "request_count"}}) is True

        # Explicit check_on_page_load
        assert engine._is_command_applicable_on_navigation({"enabled": True, "trigger": {"type": "request_count", "check_on_page_load": True}}) is True
        assert engine._is_command_applicable_on_navigation({"enabled": True, "check_on_page_load": True, "trigger": {"type": "request_count"}}) is True

        # page_check trigger is naturally applicable
        assert engine._is_command_applicable_on_navigation({"enabled": True, "trigger": {"type": "page_check", "value": "error"}}) is True

        # bootstrap_on_session_ready action is applicable
        assert engine._is_command_applicable_on_navigation({
            "enabled": True,
            "trigger": {"type": "request_count"},
            "actions": [{"type": "run_js_file", "bootstrap_on_session_ready": True}]
        }) is True

        # Ordinary request_count without special flags is not applicable
        assert engine._is_command_applicable_on_navigation({
            "enabled": True,
            "trigger": {"type": "request_count", "value": 10},
            "actions": [{"type": "refresh_page"}]
        }) is False
    finally:
        engine.shutdown()


def test_notify_tab_navigated_resets_backoff_and_caches():
    engine = CommandEngine()
    session = SimpleNamespace(
        id="tab-42",
        status=SimpleNamespace(value="idle"),
        current_domain="example.com",
        persistent_index=1,
        _pc_js_failures=3,
        _pc_js_backoff_until=time.time() + 30.0,
        _pc_refresh_grace_until=time.time() + 5.0,
        _pc_snapshot_cached=(time.time(), "stale snapshot text"),
        _last_pc_observer_check_at=time.time(),
        _pc_observer_empty_cleanup_done=True,
    )
    engine._observer_keywords_by_session["tab-42"] = {"stop fix"}
    engine._compact_haystack_cache = ("old", "old")

    cmd1 = {
        "id": "cmd-page-check",
        "name": "Page Check Cmd",
        "enabled": True,
        "trigger": {"type": "page_check", "value": "stop fix", "periodic_interval_sec": 30.0}
    }
    cmd2 = {
        "id": "cmd-bootstrap",
        "name": "Bootstrap Cmd",
        "enabled": True,
        "trigger": {"type": "request_count", "value": 5},
        "actions": [{"type": "run_js_file", "bootstrap_on_session_ready": True}]
    }
    cmd3 = {
        "id": "cmd-periodic-other",
        "name": "Other Cmd",
        "enabled": True,
        "trigger": {"type": "idle_timeout", "value": 300}
    }

    engine._periodic_next_run[("cmd-page-check", "tab-42")] = time.time() + 28.0
    engine._periodic_next_run[("cmd-bootstrap", "tab-42")] = time.time() + 25.0
    engine._periodic_next_run[("cmd-periodic-other", "tab-42")] = time.time() + 50.0

    try:
        with mock.patch.object(engine, "_load_commands_for_checks", return_value=[cmd1, cmd2, cmd3]), \
             mock.patch.object(engine, "submit_background_task") as submit_bg:

            engine.notify_tab_navigated(session, reason="test_reload", async_dispatch=True)

        # 1. Backoff and session caches reset
        assert session._pc_js_failures == 0
        assert session._pc_js_backoff_until == 0.0
        assert session._pc_refresh_grace_until == 0.0
        assert session._pc_snapshot_cached is None
        assert session._last_pc_observer_check_at == 0.0
        assert session._pc_observer_empty_cleanup_done is False

        # 2. Engine caches reset
        assert "tab-42" not in engine._observer_keywords_by_session
        assert engine._compact_haystack_cache is None

        # 3. Applicable commands reset periodic next run to 0.0 immediately
        assert engine._periodic_next_run[("cmd-page-check", "tab-42")] == 0.0
        assert engine._periodic_next_run[("cmd-bootstrap", "tab-42")] == 0.0
        assert engine._periodic_next_run[("cmd-periodic-other", "tab-42")] > 0.0

        # 4. Async dispatch submitted
        submit_bg.assert_called_once_with(engine._dispatch_tab_navigated_evaluation, session, "test_reload")
    finally:
        engine.shutdown()


def test_notify_tab_navigated_sync_dispatch_triggers_matching_command():
    engine = CommandEngine()
    session = SimpleNamespace(
        id="tab-101",
        status=SimpleNamespace(value="idle"),
        current_domain="arena.ai",
        persistent_index=1,
        tab=SimpleNamespace(url="https://arena.ai/chat"),
    )

    cmd = {
        "id": "cmd_arena_stop_fix_runtime",
        "name": "Arena Stop Fix Runtime",
        "enabled": True,
        "trigger": {
            "type": "page_check",
            "value": "arena",
            "fire_mode": "level",
            "cooldown_sec": 0,
            "reset_latch_on_failure": False,
        },
        "actions": [{
            "type": "run_js_file",
            "file_path": "stop_fix.js",
            "bootstrap_on_session_ready": True
        }]
    }

    try:
        with mock.patch.object(engine, "_load_commands_for_checks", return_value=[cmd]), \
             mock.patch.object(engine, "_sync_session_bootstrap_js_files") as sync_bootstrap, \
             mock.patch.object(engine, "_ensure_page_check_observer") as ensure_observer, \
             mock.patch.object(engine, "_should_trigger", return_value=True) as should_trigger, \
             mock.patch.object(engine, "_execute_command_async") as exec_async:

            engine.notify_tab_navigated(session, reason="page_reload", async_dispatch=False)

        sync_bootstrap.assert_called_once_with([cmd], session)
        ensure_observer.assert_called_once_with(session, {"arena"})
        should_trigger.assert_called_once_with(cmd, session)
        exec_async.assert_called_once()
        assert exec_async.call_args.args[0]["id"] == "cmd_arena_stop_fix_runtime"
        assert exec_async.call_args.args[1] is session
    finally:
        engine.shutdown()


def test_reset_page_check_latch_on_navigation_ignores_reset_latch_on_failure_false():
    engine = CommandEngine()
    session = SimpleNamespace(id="tab-1")
    cmd = {
        "id": "cmd-test-latch",
        "name": "Test Latch Cmd",
        "trigger": {
            "type": "page_check",
            "value": "error dialog",
            "reset_latch_on_failure": False,  # normally suppresses latch reset
        }
    }

    key = ("cmd-test-latch", "tab-1")
    engine._trigger_states[key] = {
        "page_key": "error dialog",
        "page_hit": True,
        "page_stable": True,
        "page_hit_since": 100.0,
    }

    try:
        # Non-navigation failure reason does not reset when reset_latch_on_failure is False
        engine._reset_page_check_latch(cmd, session, reason="normal_failure")
        assert engine._trigger_states[key]["page_hit"] is True

        # Navigation reason overrides reset_latch_on_failure: False and resets latch
        engine._reset_page_check_latch(cmd, session, reason="navigated:page_reload")
        assert engine._trigger_states[key]["page_hit"] is False
        assert engine._trigger_states[key]["page_stable"] is False
        assert engine._trigger_states[key]["page_hit_since"] == 0.0
    finally:
        engine.shutdown()


def test_notify_page_navigated_helper_with_tab_and_session():
    mock_session = SimpleNamespace(id="tab-999")
    mock_session.notify_navigated = mock.Mock()

    # 1. Target is session itself
    notify_page_navigated(mock_session, reason="test_session")
    mock_session.notify_navigated.assert_called_once_with(reason="test_session")

    # 2. Target is DrissionPage tab with _session attribute
    mock_session.notify_navigated.reset_mock()
    mock_tab = SimpleNamespace(_session=mock_session)
    notify_page_navigated(mock_tab, reason="test_tab")
    mock_session.notify_navigated.assert_called_once_with(reason="test_tab")


def test_tab_session_notify_navigated_delegates_to_command_engine():
    from app.core.tab_pool_parts.session import TabSession

    session = TabSession(id="tab-session-test", tab=mock.Mock())
    with mock.patch("app.services.command_engine.command_engine.notify_tab_navigated") as notify_mock:
        session.notify_navigated(reason="manual_f5")
        notify_mock.assert_called_once_with(session, reason="manual_f5")
