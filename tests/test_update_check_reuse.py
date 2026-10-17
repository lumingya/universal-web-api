import json
import time
from unittest.mock import Mock

import pytest
import updater
from app.api import system
from app.runtime import bootstrap


def payload():
    return {'repo': updater.DEFAULT_REPO, 'checked_at': time.time(), 'release': {'tag_name': '3.0.1'}}


@pytest.mark.parametrize('change', [{}, {'checked_at': 0}, {'checked_at': time.time() + 600}, {'repo': 'other/repo'}, {'release': {}}, {'release': None}])
def test_consume(monkeypatch, change):
    data = dict(payload(), **change)
    monkeypatch.setenv('GITHUB_REPO', updater.DEFAULT_REPO)
    monkeypatch.setenv('UWAPI_UPDATE_CHECK_RESULT', json.dumps(data))
    result = system._consume_bootstrap_update_check()
    assert (result is not None) == (not change)
    assert system._consume_bootstrap_update_check() is None


def test_schedule_reuses_without_thread(monkeypatch):
    monkeypatch.setenv('GITHUB_REPO', updater.DEFAULT_REPO)
    monkeypatch.setenv('UWAPI_UPDATE_CHECK_RESULT', json.dumps(payload()))
    monkeypatch.setattr(system, '_startup_update_check_scheduled', False)
    monkeypatch.setattr(system, '_update_check_state', {'checking': False})
    thread = Mock()
    monkeypatch.setattr(system._threading, 'Thread', thread)
    system.schedule_startup_update_check()
    thread.assert_not_called()
    assert system._get_update_check_state()['checked']


def test_missing_result_retries(monkeypatch):
    monkeypatch.delenv('UWAPI_UPDATE_CHECK_RESULT', raising=False)
    monkeypatch.setattr(system, '_startup_update_check_scheduled', False)
    monkeypatch.setattr(system, '_update_check_state', {'checking': False})
    thread = Mock()
    monkeypatch.setattr(system._threading, 'Thread', thread)
    system.schedule_startup_update_check()
    thread.return_value.start.assert_called_once()


def test_updater_file(monkeypatch, tmp_path):
    path = tmp_path / 'result.json'
    monkeypatch.setenv('UWAPI_UPDATE_CHECK_RESULT_FILE', str(path))
    updater._write_update_check_result(updater.DEFAULT_REPO, None)
    assert not path.exists()
    updater._write_update_check_result(updater.DEFAULT_REPO, {'tag_name': '3.0.1'})
    assert json.loads(path.read_text())['release']['tag_name'] == '3.0.1'


def test_bootstrap_handoff(monkeypatch):
    monkeypatch.setenv('AUTO_UPDATE_ENABLED', 'true')
    monkeypatch.setenv('UWAPI_UPDATE_CHECK_RESULT', 'stale')
    def run(*args, **kwargs):
        from pathlib import Path
        Path(kwargs['env']['UWAPI_UPDATE_CHECK_RESULT_FILE']).write_text(json.dumps(payload()))
        return Mock(returncode=1)
    monkeypatch.setattr(bootstrap.subprocess, 'run', run)
    assert not bootstrap._run_auto_update()
    assert json.loads(bootstrap.os.environ['UWAPI_UPDATE_CHECK_RESULT'])['repo'] == updater.DEFAULT_REPO


def test_disabled_clears(monkeypatch):
    monkeypatch.setenv('AUTO_UPDATE_ENABLED', 'false')
    monkeypatch.setenv('UWAPI_UPDATE_CHECK_RESULT', 'stale')
    assert not bootstrap._run_auto_update()
    assert 'UWAPI_UPDATE_CHECK_RESULT' not in bootstrap.os.environ
