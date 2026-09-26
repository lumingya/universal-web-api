"""R2-5：运行时 SQLite 存储（请求历史增量同步、统计）与旧 JSON 文件的自动导入。"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.services.storage.runtime_store import RuntimeStore


def _record(i, status="completed", **extra):
    return {"request_id": f"req-{i}", "created_at": 1000.0 + i, "status": status,
            "prompt": f"p{i}", "response": f"r{i}", "token_estimate": {"prompt": 3, "response": 5}, **extra}


def test_stats_roundtrip(tmp_path):
    store = RuntimeStore(str(tmp_path / "rt.sqlite3"))
    assert store.load_stats() == {}
    store.save_stats({"total_requests": 3, "total_input_tokens": 10})
    store.save_stats({"total_requests": 4})
    assert store.load_stats() == {"total_requests": 4, "total_input_tokens": 10}


def test_sync_history_writes_only_changes_and_deletes_trimmed(tmp_path):
    path = str(tmp_path / "rt.sqlite3")
    store = RuntimeStore(path)
    records = [_record(i) for i in range(5)]
    assert store.sync_history(records) == (5, 0)
    assert store.sync_history(records) == (0, 0)  # 没有变化就不写

    records[4] = _record(4, status="failed")
    records.append(_record(5))
    assert store.sync_history(records[1:]) == (2, 1)  # 改了 1 条、新增 1 条、淘汰 1 条
    assert [r["request_id"] for r in store.load_history(10)] == [f"req-{i}" for i in range(1, 6)]

    # 新进程（新连接）基于数据库现状继续增量同步
    reopened = RuntimeStore(path)
    assert reopened.sync_history(records[1:]) == (0, 0)
    assert [r["request_id"] for r in reopened.query_history(status="failed")] == ["req-4"]
    assert len(reopened.query_history(since=1003.0)) == 3
    assert reopened.load_history(2)[-1]["request_id"] == "req-5"


def test_database_uses_wal_mode(tmp_path):
    path = tmp_path / "rt.sqlite3"
    RuntimeStore(str(path)).save_stats({"total_requests": 1})
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


@pytest.fixture
def manager_factory(tmp_path, monkeypatch):
    from app.services.request_manager import RequestManager

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    monkeypatch.delenv("RUNTIME_DB_PATH", raising=False)
    monkeypatch.setattr(RequestManager, "_run_zombie_sweep_loop", lambda self: None)

    def make():
        manager = RequestManager.__new__(RequestManager)
        if getattr(manager, "_initialized", False):
            manager.flush_pending_saves(timeout=5)  # 排空上一次初始化遗留的保存线程
        manager._initialized = False
        monkeypatch.setattr(RequestManager, "_request_monitor_enabled", lambda self: True)
        monkeypatch.setattr(RequestManager, "_request_monitor_save_to_file", lambda self: True)
        RequestManager.__init__(manager)
        return manager

    return make


def test_legacy_json_files_are_imported_once_and_backed_up(tmp_path, manager_factory):
    config = tmp_path / "config"
    (config / "request_history.json").write_text(
        json.dumps({"records": [_record(1), _record(2)]}), encoding="utf-8")
    (config / "app_stats.json").write_text(
        json.dumps({"total_requests": 7, "total_input_tokens": 70, "total_output_tokens": 700}), encoding="utf-8")

    manager = manager_factory()
    assert [r["request_id"] for r in manager._monitor_history] == ["req-1", "req-2"]
    assert (manager.total_requests, manager.total_input_tokens, manager.total_output_tokens) == (7, 70, 700)
    assert not (config / "request_history.json").exists() and not (config / "app_stats.json").exists()
    assert len(list(config.glob("request_history.json.migrated-*.bak"))) == 1
    assert len(list(config.glob("app_stats.json.migrated-*.bak"))) == 1
    assert (config / "runtime.sqlite3").exists()

    # 重启后从数据库恢复，不依赖旧文件
    manager.total_requests += 1
    manager._save_stats()
    with manager._history_lock:
        manager._monitor_history.append(_record(3))
    manager._save_history()
    restarted = manager_factory()
    assert [r["request_id"] for r in restarted._monitor_history] == ["req-1", "req-2", "req-3"]
    assert restarted.total_requests == 8


def test_runtime_db_path_env_override(tmp_path, manager_factory, monkeypatch):
    target = tmp_path / "elsewhere" / "custom.sqlite3"
    monkeypatch.setenv("RUNTIME_DB_PATH", str(target))
    manager = manager_factory()
    manager._save_stats()
    assert target.exists()
