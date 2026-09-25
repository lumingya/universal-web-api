"""R0-5：测试入库与测试基础设施的回归保护。

- tests/ 下的测试文件不能被 .gitignore 忽略（原白名单导致 12 个测试从未入库，路线图 N3）；
- 依赖 playwright 的模块必须先 ``pytest.importorskip``，保证没装 playwright 时 ``pytest`` 也能完整收集；
- pyproject.toml 注册了 browser / real_browser / local_fixture 标记。
"""

from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def _git(*args: str) -> subprocess.CompletedProcess:
    if not shutil.which("git"):
        pytest.skip("git not installed")
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)


def test_no_test_file_is_gitignored():
    candidates = sorted(
        path.relative_to(ROOT).as_posix()
        for pattern in ("test_*.py", "_*.py", "e2e_*.py", "browser_*.py", "conftest.py")
        for path in TESTS.glob(pattern)
    )
    assert candidates, "tests/ 下没有找到测试文件"
    out = _git("check-ignore", "--no-index", *candidates)
    ignored = [line for line in out.stdout.splitlines() if line.strip()]
    assert not ignored, "这些测试文件被 .gitignore 忽略了，提交时会丢失：\n" + "\n".join(ignored)


def test_gitignore_has_no_tests_whitelist():
    lines = [line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "tests/*" not in lines and "/tests/*" not in lines, "tests/ 不要再用“全部忽略 + 白名单”"


def _module_level_playwright_import_guarded(tree: ast.Module) -> tuple[bool, bool]:
    """返回 (模块顶层是否导入 playwright, 导入前是否已 importorskip)。"""
    guarded = False
    for node in tree.body:
        if isinstance(node, (ast.Expr, ast.Assign)) and "importorskip" in ast.dump(node):
            guarded = True
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("playwright"):
            return True, guarded
        if isinstance(node, ast.Import) and any(alias.name.startswith("playwright") for alias in node.names):
            return True, guarded
    return False, guarded


@pytest.mark.parametrize("path", sorted(TESTS.glob("test_*.py")), ids=lambda p: p.name)
def test_playwright_modules_skip_cleanly_without_playwright(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports_playwright, guarded = _module_level_playwright_import_guarded(tree)
    if imports_playwright:
        assert guarded, f"{path.name} 在模块顶层导入 playwright，需先 pytest.importorskip('playwright.sync_api')"


def test_markers_registered(pytestconfig):
    names = {line.split(":", 1)[0].strip() for line in pytestconfig.getini("markers")}
    assert {"browser", "real_browser", "local_fixture"} <= names
