"""uv 环境与启动方式的回归保护。

仓库只使用 uv 管理环境：不再有 pip / requirements.txt / 启动脚本。
这些测试不依赖 uv 可执行文件，只检查声明文件与入口逻辑保持同步。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

REMOVED_FILES = (
    "start.py",
    "start.bat",
    "check_deps.py",
    "requirements.txt",
    "requirements-dev.txt",
)

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    tomli = pytest.importorskip("tomli")
    tomllib = tomli


def _load_pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _requirement_name(value: str) -> str:
    value = value.split(";", 1)[0].strip()
    match = re.match(r"^([A-Za-z0-9_.-]+)", value)
    assert match, f"无法解析依赖: {value}"
    return match.group(1).lower().replace("_", "-").replace(".", "-")


def test_pip_and_start_scripts_do_not_exist():
    leftovers = [name for name in REMOVED_FILES if (ROOT / name).exists()]
    assert not leftovers, (
        "环境只允许由 uv 管理，以下文件应保持删除：\n" + "\n".join(leftovers)
    )


def test_pyproject_is_a_uv_project():
    pyproject = _load_pyproject()
    project = pyproject["project"]
    assert project["name"] == "universal-web-api"
    assert project["requires-python"] == ">=3.10"
    assert pyproject["tool"]["uv"]["package"] is False
    assert (ROOT / "uv.lock").is_file()


def test_version_matches_version_file():
    project_version = _load_pyproject()["project"]["version"]
    assert project_version == (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_python_version_is_pinned():
    text = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"3\.\d+", text), text


def test_uv_lock_matches_python_range():
    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert lock_text.startswith("version = 1")
    assert 'requires-python = ">=3.10"' in lock_text


def test_runtime_dependencies_are_declared():
    declared = {_requirement_name(item) for item in _load_pyproject()["project"]["dependencies"]}
    for name in ("fastapi", "uvicorn", "drissionpage", "psutil", "python-dotenv"):
        assert name in declared, name


def test_dev_group_covers_tests_and_tooling():
    declared = {_requirement_name(item) for item in _load_pyproject()["dependency-groups"]["dev"]}
    for name in ("pytest", "httpx", "anyio", "playwright", "openai", "anthropic", "ruff"):
        assert name in declared, name


def test_main_bootstraps_runtime_before_importing_app():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    bootstrap_line = next(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "bootstrap_runtime"
    )
    app_import_line = next(
        node.lineno
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.core")
    )
    assert bootstrap_line < app_import_line, "启动引导必须在导入 app.core 之前执行"


def test_bootstrap_runtime_steps_exist():
    source = (ROOT / "app" / "runtime" / "bootstrap.py").read_text(encoding="utf-8")
    for needle in (
        "def bootstrap_runtime()",
        "def _preflight_environment()",
        "def _run_auto_update()",
        "def apply_drissionpage_patch()",
        "def clean_browser_profile()",
        "def launch_controlled_browser()",
    ):
        assert needle in source, needle


def test_restart_does_not_rely_on_exit_code_3():
    relaunch = (ROOT / "app" / "runtime" / "relaunch.py").read_text(encoding="utf-8")
    assert "os.execv" in relaunch
    for rel in ("main.py", "app/api/system.py"):
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "restart_service" in source, rel
        assert "os._exit(3)" not in source, rel


def test_uv_venv_is_preserved_by_updater():
    from update_preserve import get_default_update_preserve_patterns

    patterns = get_default_update_preserve_patterns()
    assert ".venv" in patterns
    assert "requirements.txt" not in patterns
