from unittest.mock import Mock

import pytest

from app.core.browser import connection


@pytest.fixture
def core(monkeypatch):
    obj = connection.BrowserConnectionMixin()
    obj.port = 9222
    obj._connected = True
    obj._tab_pool = None
    obj._last_watchdog_health = 'alive'
    obj._last_debug_port_error = 'timed out'
    obj._watchdog_stop = Mock()
    obj._watchdog_stop.is_set.return_value = False
    obj._watchdog_stop.wait.return_value = False
    obj._get_watchdog_tab_snapshot = Mock(return_value='idle')
    obj._is_debug_port_open = Mock(return_value=False)
    obj._probe_browser_connection = Mock(return_value=False)
    monkeypatch.setattr(connection, 'logger', Mock())
    return obj


def test_transient_failure(core):
    core._probe_browser_connection.side_effect = [False, True]
    core._run_connection_watchdog_tick()
    connection.logger.error.assert_not_called()
    core._is_debug_port_open.assert_called_with(timeout=1.0)
    core._watchdog_stop.wait.assert_called_once_with(0.8)


def test_persistent_failure_and_recovery(core):
    core._run_connection_watchdog_tick()
    core._run_connection_watchdog_tick()
    connection.logger.error.assert_called_once()
    assert 'probe_error' not in connection.logger.error.call_args.args[0]
    core._probe_browser_connection.return_value = True
    core._is_debug_port_open.return_value = True
    core._run_connection_watchdog_tick()
    assert core._last_watchdog_health == 'alive'
    connection.logger.info.assert_called_once()


def test_port_only(core):
    core._is_debug_port_open.return_value = True
    core._run_connection_watchdog_tick()
    connection.logger.warning.assert_called_once()
    connection.logger.error.assert_not_called()


@pytest.mark.parametrize('already_stopped', [True, False])
def test_stop(core, already_stopped):
    core._watchdog_stop.is_set.return_value = already_stopped
    core._watchdog_stop.wait.return_value = True
    core._run_connection_watchdog_tick()
    connection.logger.error.assert_not_called()
    assert core._probe_browser_connection.call_count == (0 if already_stopped else 1)
