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
