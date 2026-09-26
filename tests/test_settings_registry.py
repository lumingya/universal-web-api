"""R2-4：环境变量登记表是唯一来源——代码、.env.example、控制面板三方与它保持一致。"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import shutil
import subprocess

import pytest

from app.core import settings_registry as reg
from tests._env_scan import ROOT, scan_env_reads

# 登记了但代码不通过环境变量函数读取的项（附原因）
READ_OUTSIDE_ENV_FUNCTIONS = {"CURRENT_VERSION": "更新器直接读取 .env 文件行（VERSION 文件缺失时的兜底）"}


@pytest.fixture(scope="module")
def code_reads():
    return scan_env_reads()


def test_every_env_var_read_in_code_is_registered(code_reads):
    declared = set(reg.BY_NAME) | set(reg.ALIAS_TO_NAME) | set(reg.EXTERNAL_ENV)
    missing = sorted(set(code_reads) - declared)
    assert not missing, (
        "以下环境变量在代码里被读取，但没有登记到 app/core/settings_registry.py："
        f"{missing}（登记后运行 python scripts/build_env_example.py）"
    )


def test_every_registered_setting_is_read_somewhere(code_reads):
    unused = sorted(set(reg.BY_NAME) - set(code_reads) - set(READ_OUTSIDE_ENV_FUNCTIONS))
    assert not unused, f"登记表里的这些变量代码已不再读取，请删除或说明：{unused}"


def test_registry_entries_are_well_formed():
    labels = dict(reg.CATEGORIES)
    for setting in reg.SETTINGS:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", setting.name)
        assert setting.type in {"bool", "int", "float", "str", "enum", "secret"}, setting.name
        assert setting.category in labels, setting.name
        assert setting.description.strip(), setting.name
        if setting.type == "enum":
            assert setting.choices and (setting.default is None or setting.default in setting.choices), setting.name
        if setting.default is not None and setting.type in {"bool", "int", "float", "enum"}:
            assert reg.parse_value(setting, setting.default) == setting.default, setting.name
    assert len({s.name for s in reg.SETTINGS}) == len(reg.SETTINGS)
    assert not set(reg.ALIAS_TO_NAME) & set(reg.BY_NAME)


def test_env_example_is_generated_and_current():
    spec = importlib.util.spec_from_file_location("build_env_example", ROOT / "scripts" / "build_env_example.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0, ".env.example 已过期：python scripts/build_env_example.py"
    assert subprocess.run(["git", "check-ignore", "-q", "scripts/build_env_example.py"], cwd=ROOT).returncode == 1


def test_secrets_are_never_filled_in_example():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for setting in reg.SETTINGS:
        if setting.secret:
            match = re.search(rf"^#? ?{setting.name}=(.*)$", text, re.M)
            assert match and match.group(1) == "", setting.name


def test_code_literal_defaults_match_registry(code_reads):
    mismatches = []
    for name, setting in reg.BY_NAME.items():
        if setting.type not in ("bool", "int", "float") or setting.default is None:
            continue
        for use in code_reads.get(name, []):
            if use["default"] is None:
                continue
            try:
                value = ast.literal_eval(use["default"])
            except Exception:
                try:
                    value = eval(use["default"], {"__builtins__": {}}, {})  # 例如 5 * 1024 * 1024
                except Exception:
                    continue
            try:
                parsed = reg.parse_value(setting, value)
            except ValueError:
                mismatches.append(f"{name} @ {use['file']}:{use['line']} 默认值 {use['default']} 无法解析")
                continue
            if parsed != setting.default:
                mismatches.append(f"{name} @ {use['file']}:{use['line']} 代码默认 {parsed!r} ≠ 登记 {setting.default!r}")
    assert not mismatches, "\n".join(mismatches)


@pytest.fixture(scope="module")
def dashboard_env_schema():
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 node 读取 static/js/dashboard-schema.js")
    script = (
        "global.window={};require(process.argv[1]);"
        "process.stdout.write(JSON.stringify(window.ENV_CONFIG_SCHEMA))"
    )
    output = subprocess.check_output([node, "-e", script, str(ROOT / "static" / "js" / "dashboard-schema.js")])
    return json.loads(output)


def test_dashboard_env_schema_matches_registry(dashboard_env_schema):
    type_map = {"switch": {"bool"}, "number": {"int", "float"}, "select": {"enum"}, "password": {"secret"}, "text": {"str"}}
    problems = []
    for section, definition in dashboard_env_schema.items():
        for key, item in (definition.get("items") or {}).items():
            setting = reg.find(key)
            if setting is None:
                problems.append(f"{key}（面板分组 {section}）未登记")
                continue
            if setting.type not in type_map.get(item.get("type"), {setting.type}):
                problems.append(f"{key} 面板类型 {item.get('type')} 与登记类型 {setting.type} 不符")
            if item.get("options"):
                options = tuple(str(o["value"] if isinstance(o, dict) else o) for o in item["options"])
                if tuple(setting.choices) != options:
                    problems.append(f"{key} 面板选项 {options} 与登记 {setting.choices} 不符")
            if "default" in item and setting.type in ("bool", "int", "float", "enum") and setting.default is not None:
                if reg.parse_value(setting, item["default"]) != setting.default:
                    problems.append(f"{key} 面板默认 {item['default']!r} 与登记 {setting.default!r} 不符")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        ("AUTH_ENABLED", "TRUE", True), ("AUTH_ENABLED", "off", False), ("AUTH_ENABLED", "", False),
        ("APP_PORT", "8300", 8300), ("APP_PORT", "8300.0", 8300), ("LOG_LEVEL", "debug", "DEBUG"),
        ("CDP_NETWORK_MAX_TOTAL_BUFFER_MB", "12.5", 12.5), ("GITHUB_REPO", " owner/repo ", "owner/repo"),
    ],
)
def test_parse_value_accepts(name, raw, expected):
    assert reg.parse_value(reg.BY_NAME[name], raw) == expected


@pytest.mark.parametrize(
    ("name", "raw", "fragment"),
    [
        ("AUTH_ENABLED", "your-secret-here", "true/false"), ("APP_PORT", "abc", "数字"),
        ("APP_PORT", "80.5", "整数"), ("APP_PORT", "70000", "不能大于"), ("LOG_LEVEL", "verbose", "之一"),
        ("TOOL_CALLING_MAX_ARGUMENT_DEPTH", "1", "不能小于"),
    ],
)
def test_parse_value_rejects(name, raw, fragment):
    with pytest.raises(ValueError, match=fragment):
        reg.parse_value(reg.BY_NAME[name], raw)


def test_get_setting_supports_aliases_and_falls_back_on_invalid():
    assert reg.get_setting("CLASH_API", {"ARENA_CLASH_API": "http://127.0.0.1:1"}) == "http://127.0.0.1:1"
    assert reg.get_setting("APP_PORT", {"APP_PORT": "not-a-number"}) == 8199
    assert reg.get_setting("APP_PORT", {}) == 8199
    with pytest.raises(KeyError):
        reg.get_setting("NOT_REGISTERED_ANYWHERE")


def test_dashboard_save_rejects_invalid_values_but_keeps_existing(monkeypatch):
    from fastapi import HTTPException

    from app.api import system

    monkeypatch.setattr(system, "_load_env_config_from_file", lambda: {"MAX_TABS": "oops"})
    with pytest.raises(HTTPException) as exc:
        system._validate_env_config_payload({"APP_PORT": "abc"})
    assert exc.value.status_code == 400 and "APP_PORT" in exc.value.detail
    with pytest.raises(HTTPException):
        system._validate_env_config_payload({"LOG_LEVEL": "loud"})
    # 存量的非法值原样回传时不拦截（否则用户什么都保存不了）；合法修改正常通过
    system._validate_env_config_payload({"MAX_TABS": "oops", "APP_PORT": "8300", "LOG_LEVEL": "debug"})
