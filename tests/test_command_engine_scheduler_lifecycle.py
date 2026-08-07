import threading
from types import SimpleNamespace
from unittest import mock

from app.services.command_engine import CommandEngine


def test_command_engine_import_style_construction_does_not_start_scheduler(monkeypatch):
    monkeypatch.delenv("CMD_ENGINE_AUTO_START", raising=False)

    with mock.patch.object(CommandEngine, "_start_periodic_scheduler") as start:
        engine = CommandEngine()

    try:
        start.assert_not_called()
        assert engine.is_scheduler_running() is False
    finally:
        engine.shutdown()


def test_scheduler_auto_start_remains_available_as_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("CMD_ENGINE_AUTO_START", "true")

    with mock.patch.object(CommandEngine, "_start_periodic_scheduler") as start:
        engine = CommandEngine()

    try:
        start.assert_called_once_with()
    finally:
        engine.shutdown()


def test_scheduler_start_is_singleton_under_concurrent_watchdogs(monkeypatch):
    monkeypatch.delenv("CMD_ENGINE_AUTO_START", raising=False)
    engine = CommandEngine()
    callers = [threading.Thread(target=engine.ensure_scheduler_running) for _ in range(12)]

    try:
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(timeout=2)

        scheduler_threads = [
            thread
            for thread in threading.enumerate()
            if thread.name == "cmd-periodic-checker"
        ]
        assert len(scheduler_threads) == 1
        assert engine.is_scheduler_running() is True
    finally:
        engine.shutdown()


def test_scheduler_cannot_restart_after_shutdown(monkeypatch):
    monkeypatch.delenv("CMD_ENGINE_AUTO_START", raising=False)
    engine = CommandEngine()

    engine.ensure_scheduler_running()
    assert engine.is_scheduler_running() is True
    original_thread = engine._periodic_thread

    engine.shutdown()
    assert engine.ensure_scheduler_running() is False
    assert engine._periodic_thread is original_thread
    assert engine.is_scheduler_running() is False


def test_shutdown_rejects_new_deferred_workflow_timer(monkeypatch):
    monkeypatch.delenv("CMD_ENGINE_AUTO_START", raising=False)
    engine = CommandEngine()
    session = mock.Mock(id="tab-1")
    engine.shutdown()

    with mock.patch("app.services.command_engine_runtime.threading.Timer") as timer:
        assert engine.schedule_deferred_workflow_commands(session, delay_sec=0.25) is False

    timer.assert_not_called()


def test_probe_only_page_check_keeps_configured_interval(monkeypatch):
    monkeypatch.delenv("CMD_ENGINE_AUTO_START", raising=False)
    engine = CommandEngine()
    engine._observer_keywords_by_session["tab-1"] = {"resource error"}

    try:
        timing = engine._get_periodic_trigger_timing(
            {
                "type": "page_check",
                "value": "",
                "probe_js": "return true",
                "periodic_interval_sec": 30,
                "periodic_jitter_sec": 5,
            },
            "tab-1",
        )
        assert timing == (30.0, 5.0)
    finally:
        engine.shutdown()


def test_keyword_page_check_can_use_observer_fast_path(monkeypatch):
    monkeypatch.delenv("CMD_ENGINE_AUTO_START", raising=False)
    engine = CommandEngine()
    engine._observer_keywords_by_session["tab-1"] = {"resource error"}

    try:
        timing = engine._get_periodic_trigger_timing(
            {
                "type": "page_check",
                "value": "resource error",
                "periodic_interval_sec": 8,
                "periodic_jitter_sec": 2,
            },
            "tab-1",
        )
        assert timing == (1.5, 0.0)
    finally:
        engine.shutdown()


def test_empty_keyword_set_cleans_stale_page_observer(monkeypatch):
    monkeypatch.delenv("CMD_ENGINE_AUTO_START", raising=False)
    engine = CommandEngine()
    session = SimpleNamespace(id="tab-1")
    engine._observer_keywords_by_session[session.id] = {"resource error"}

    try:
        with mock.patch.object(engine, "_run_page_check_js", return_value=True) as run_js:
            engine._clear_page_check_observer(session)
            engine._clear_page_check_observer(session)

        run_js.assert_called_once()
        assert "disconnect" in run_js.call_args.args[1]
        assert session.id not in engine._observer_keywords_by_session
        assert session._pc_observer_empty_cleanup_done is True
    finally:
        engine.shutdown()


def test_page_refresh_errors_do_not_trigger_page_check_backoff(monkeypatch):
    monkeypatch.delenv("CMD_ENGINE_AUTO_START", raising=False)
    engine = CommandEngine()
    session = SimpleNamespace(id="tab-1")
    engine._observer_keywords_by_session[session.id] = {"resource error"}

    try:
        refresh_error = (
            "页面被刷新，请操作前尝试等待页面刷新或加载完成。\n"
            "版本: 4.1.1.2"
        )
        engine._record_page_check_js_failure(session, refresh_error, "observer_install")

        assert session._pc_js_failures == 0
        assert session._pc_js_backoff_until == 0.0
        assert session._pc_refresh_grace_until > 0.0
        assert session.id not in engine._observer_keywords_by_session
    finally:
        engine.shutdown()


def test_page_check_observer_waits_for_document_after_refresh(monkeypatch):
    monkeypatch.delenv("CMD_ENGINE_AUTO_START", raising=False)
    engine = CommandEngine()
    session = SimpleNamespace(id="tab-1")
    refresh_error = RuntimeError("页面被刷新，请操作前尝试等待页面刷新或加载完成。")

    try:
        with mock.patch.object(
            engine,
            "_run_page_check_js",
            side_effect=refresh_error,
        ) as run_js:
            engine._ensure_page_check_observer(session, {"resource error"})
            engine._ensure_page_check_observer(session, {"resource error"})

        run_js.assert_called_once()
        assert session._pc_js_failures == 0
    finally:
        engine.shutdown()
