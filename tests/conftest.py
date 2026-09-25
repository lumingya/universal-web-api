"""T1：干净克隆也能跑的测试入口。

部分回归测试依赖**不随仓库分发**的本地资源（运行时生成/用户私有的
``config/commands.json`` 中的 Arena 命令、``config/commands.local.json``、
``custom_scripts/examples/`` 示例脚本、``js/*.user.js`` 用户脚本）。这些资源不在时，测试失败并不代表产品回归。

这里做两件事：

1. 静态识别依赖这些资源的测试（测试函数本身、或它调用的同模块辅助函数/方法里
   出现了资源标记），统一打上 ``local_fixture`` 标记——CI 可用
   ``pytest -m "not local_fixture"`` 只跑纯逻辑子集；
2. 资源确实缺失时把这些测试标成 skip 并写明缺什么；资源齐全（作者本地环境）时照常运行。
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Set

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _arena_commands_available() -> bool:
    path = PROJECT_ROOT / "config" / "commands.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    commands = payload.get("commands") if isinstance(payload, dict) else None
    return isinstance(commands, list) and any(
        isinstance(item, dict) and str(item.get("id") or "").startswith("cmd_arena")
        for item in commands
    )


# 资源 ID -> (资源可用性检查, 说明)；RESOURCE_PATTERNS 给出源码里识别该资源的字符串
LOCAL_RESOURCES = {
    "arena_commands": (
        _arena_commands_available,
        "config/commands.json 中的 Arena 命令（本地运行时配置，未随仓库分发）",
    ),
    "local_command_overrides": (
        lambda: (PROJECT_ROOT / "config" / "commands.local.json").is_file(),
        "config/commands.local.json（本地命令覆盖，未随仓库分发）",
    ),
    "arena_payload_interceptor": (
        lambda: (PROJECT_ROOT / "custom_scripts" / "examples" / "arena_payload_interceptor.js").is_file(),
        "custom_scripts/examples/arena_payload_interceptor.js（custom_scripts/ 已被 .gitignore 忽略）",
    ),
    "arena_image_window_userscript": (
        lambda: (PROJECT_ROOT / "js" / "arena-conversation-image-window.user.js").is_file(),
        "js/arena-conversation-image-window.user.js（本地用户脚本，未随仓库分发）",
    ),
}


RESOURCE_PATTERNS = {
    # 只认指向仓库 config/commands.json 的写法；tmp_path / "commands.json" 之类的自建夹具不算
    "arena_commands": ("COMMANDS_PATH", '"config" / "commands.json"'),
    "local_command_overrides": ('"config" / "commands.local.json"',),
    "arena_payload_interceptor": (
        "examples/arena_payload_interceptor.js",
        '== "arena_payload_interceptor.js"',
    ),
    "arena_image_window_userscript": ("arena-conversation-image-window",),
}


def _markers_in(text: str) -> Set[str]:
    return {
        resource
        for resource, patterns in RESOURCE_PATTERNS.items()
        if any(pattern in text for pattern in patterns)
    }


@lru_cache(maxsize=None)
def _module_taint(module_file: str) -> Dict[str, Set[str]]:
    """返回 {函数/方法名: 其（传递）依赖的资源标记集合}。"""
    try:
        source = Path(module_file).read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception:
        return {}

    bodies: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            segment = ast.get_source_segment(source, node) or ""
            bodies[node.name] = bodies.get(node.name, "") + "\n" + segment

    taint: Dict[str, Set[str]] = {name: _markers_in(body) for name, body in bodies.items()}
    # unittest：setUp/setUpClass 读取了本地资源并存到 self.X / cls.X 时，
    # 用到 self.X 的测试方法同样依赖该资源。
    for cls_node in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        methods = [n for n in cls_node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for setup in (m for m in methods if m.name in ("setUp", "setUpClass")):
            setup_markers = _markers_in(ast.get_source_segment(source, setup) or "")
            if not setup_markers:
                continue
            attrs = {
                target.attr
                for stmt in ast.walk(setup)
                if isinstance(stmt, (ast.Assign, ast.AnnAssign))
                for target in (stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target])
                if isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id in ("self", "cls")
            }
            for method in methods:
                segment = ast.get_source_segment(source, method) or ""
                if any(f"self.{attr}" in segment or f"cls.{attr}" in segment for attr in attrs):
                    taint.setdefault(method.name, set()).update(setup_markers)

    changed = True
    while changed:
        changed = False
        for name, body in bodies.items():
            for other, markers in taint.items():
                if other == name or not markers or markers <= taint[name]:
                    continue
                if other in body:  # 调用了依赖本地资源的辅助函数/方法
                    taint[name] |= markers
                    changed = True
    return taint


def _item_markers(item: pytest.Item) -> Set[str]:
    function = getattr(item, "function", None)
    if function is None:
        return set()
    try:
        module_file = inspect.getsourcefile(function)
        body = textwrap.dedent(inspect.getsource(function))
    except (OSError, TypeError):
        return set()
    markers = _markers_in(body)
    taint = _module_taint(module_file or "")
    markers |= taint.get(function.__name__, set())
    for helper, helper_markers in taint.items():
        if helper != function.__name__ and helper_markers and helper in body:
            markers |= helper_markers
    return markers


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "local_fixture: 依赖未随仓库分发的本地资源；缺失时自动 skip（CI 可用 -m 'not local_fixture' 排除）",
    )


def pytest_collection_modifyitems(config: pytest.Config, items) -> None:
    availability: Dict[str, Optional[bool]] = {}
    for item in items:
        markers = _item_markers(item)
        if not markers:
            continue
        item.add_marker(pytest.mark.local_fixture)
        missing = []
        for marker in sorted(markers):
            if availability.get(marker) is None:
                check, _ = LOCAL_RESOURCES[marker]
                try:
                    availability[marker] = bool(check())
                except Exception:
                    availability[marker] = False
            if not availability[marker]:
                missing.append(LOCAL_RESOURCES[marker][1])
        if missing:
            item.add_marker(pytest.mark.skip(reason="需要本地环境资源：" + "；".join(missing)))
