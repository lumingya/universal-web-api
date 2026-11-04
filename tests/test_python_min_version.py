"""R0-2：保证仓库里的 Python 代码能在最低支持版本（3.10）上被解析。

背景（路线图 N1）：代码里曾出现只有 3.12+ 才能解析的写法，而 README 承诺支持 3.10/3.11。
现在最低版本由 pyproject.toml 的 requires-python 统一声明，uv 按 .python-version 选择解释器。

检查方式：
- 在 3.10/3.11 上直接 compile() 每个文件，原生语法检查即可发现问题；
- 在 3.12+ 上先 ast.parse(feature_version=MIN_PYTHON)（能拦住 except*、type 语句、泛型参数等），
  再用 tokenize 找出 f-string 替换字段里的 PEP 701 专属写法：与外层同种的引号、反斜杠、注释。
  （实测 feature_version 拦不住 PEP 701 写法，所以需要这一步。）
"""
from __future__ import annotations

import ast
import io
import os
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 10)
_FALLBACK_DIRS = ("app", "tests", "scripts")
_PREFIX_CHARS = "rRbBfFuUtT"


def _tracked_python_files() -> list[Path]:
    """优先用 git ls-files（不会扫到本地未跟踪脚本、venv）；无 git 时退回固定源码目录。"""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.py"],
            cwd=ROOT, capture_output=True, check=True, timeout=30,
        ).stdout.decode("utf-8", errors="replace")
        files = [ROOT / name for name in out.split("\0") if name]
        if files:
            return sorted(path for path in files if path.is_file())
    except (OSError, subprocess.SubprocessError):
        pass
    files = [path for path in ROOT.glob("*.py") if path.is_file()]
    for dirname in _FALLBACK_DIRS:
        base = ROOT / dirname
        if base.is_dir():
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                files.extend(Path(dirpath) / f for f in filenames if f.endswith(".py"))
    return sorted(files)


def _quote_of(token_text: str) -> str:
    body = token_text.lstrip(_PREFIX_CHARS)
    return body[:3] if body[:3] in ('"""', "'''") else body[:1]


def _conflicts(inner_quote: str, enclosing: list[str]) -> bool:
    for outer in enclosing:
        if len(outer) == 1 and outer in inner_quote:
            return True  # 单引号类外层：内层用同种引号会提前结束外层字符串（3.12 前）
        if len(outer) == 3 and inner_quote == outer:
            return True
    return False


def pep701_only_constructs(source: str) -> list[tuple[int, str]]:
    """返回 [(行号, 原因)]。只能在 3.12+ 调用（依赖 FSTRING_START 等 token）。"""
    problems: list[tuple[int, str]] = []
    stack: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.FSTRING_START:
            quote = _quote_of(tok.string)
            if stack and _conflicts(quote, stack):
                problems.append((tok.start[0], "嵌套 f-string 使用了与外层相同的引号"))
            stack.append(quote)
        elif tok.type == tokenize.FSTRING_END:
            if stack:
                stack.pop()
        elif not stack:
            continue
        elif tok.type == tokenize.STRING:
            if _conflicts(_quote_of(tok.string), stack):
                problems.append((tok.start[0], "f-string 表达式里的字符串使用了与外层相同的引号"))
            if "\\" in tok.string:
                problems.append((tok.start[0], "f-string 表达式部分包含反斜杠"))
        elif tok.type == tokenize.COMMENT:
            problems.append((tok.start[0], "f-string 表达式部分包含注释"))
        elif tok.type == tokenize.FSTRING_MIDDLE and len(stack) >= 2 and "\\" in tok.string:
            problems.append((tok.start[0], "嵌套 f-string 的字面部分包含反斜杠"))
    return problems


def _syntax_problems(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8-sig")
    rel = path.relative_to(ROOT).as_posix()
    if sys.version_info < (3, 12):
        try:
            compile(source, rel, "exec", dont_inherit=True)
        except SyntaxError as exc:
            return [f"{rel}:{exc.lineno}: {exc.msg}"]
        return []
    try:
        ast.parse(source, filename=rel, feature_version=MIN_PYTHON)
    except SyntaxError as exc:
        return [f"{rel}:{exc.lineno}: {exc.msg}（Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 不支持）"]
    return [f"{rel}:{line}: {reason}（PEP 701，仅 3.12+）" for line, reason in pep701_only_constructs(source)]


def test_all_tracked_python_files_parse_on_min_python():
    files = _tracked_python_files()
    assert len(files) > 50, f"只找到 {len(files)} 个 Python 文件，文件发现逻辑可能失效"
    problems = [problem for path in files for problem in _syntax_problems(path)]
    assert not problems, "以下写法在最低支持版本上无法解析：\n" + "\n".join(problems)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="tokenize 的 FSTRING_* token 需要 3.12+")
@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("p = {}\nx = f\"{', '.join(f'{p['a']}' for _ in [1])}\"\n", True),   # 旧启动脚本的写法
        ("x = f\"{d[\"k\"]}\"\n", True),
        ("x = f\"{'\\n'.join(a)}\"\n", True),
        ("x = f\"{a # c\n}\"\n", True),
        ("x = f\"{d['k']}\"\n", False),
        ("x = f'{d[\"k\"]}'\n", False),
        ("x = f\"\"\"{d[\"k\"]}\"\"\"\n", False),
        ("x = f\"a\\nb{c}\"\n", False),
        ("x = f\"{x!r:>{width}}\"\n", False),
    ],
)
def test_pep701_detector(snippet, expected):
    assert bool(pep701_only_constructs(snippet)) is expected


def test_pyproject_declares_same_min_python():
    """pyproject.toml 的 requires-python 必须与测试里的最低版本一致。"""
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10
        import tomli as tomllib

    with (ROOT / "pyproject.toml").open("rb") as handle:
        requires = tomllib.load(handle)["project"]["requires-python"]
    major, minor = MIN_PYTHON
    assert requires == f">={major}.{minor}", requires
