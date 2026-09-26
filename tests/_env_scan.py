"""R2-4：静态扫描代码里读取的环境变量名（测试用来保证“代码读到的变量都在配置登记表里”）。

覆盖的读取方式：
- ``os.getenv("X")`` / ``os.environ.get("X")`` / ``os.environ["X"]`` / ``os.environ.setdefault("X")``，
  也包括 ``import os as _os`` 之类的别名，以及名为 env/environ 的映射的 ``.get("X")``；
- 转发参数给上述读取的辅助函数（如 ``_env_int("X", 5)``、``_env_positive_float(env, "X", 10)``），
  自动识别辅助函数，并按参数位置取出变量名；
- 通过模块级字符串常量间接传入的变量名（如 ``INSECURE_OVERRIDE_ENV = "ALLOW_INSECURE_PUBLIC_BIND"``）。
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
_ENV_MAPPING_NAMES = {"env", "environ", "environment", "_env"}


def _python_files() -> List[Path]:
    # 包含尚未提交但未被忽略的新文件（例如刚拆分出来的模块），扫描结果不依赖 git 暂存状态
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"], cwd=ROOT, text=True
    )
    return [ROOT / f for f in output.split() if not f.startswith("tests/")]


def _is_env_read(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "getenv"
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "getenv":
        return True
    target = ast.unparse(func.value)
    if func.attr in ("get", "setdefault", "pop") and (target.endswith("environ") or target.split(".")[-1] in _ENV_MAPPING_NAMES):
        return True
    return False


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def scan_env_reads() -> Dict[str, List[dict]]:
    trees = {path: ast.parse(path.read_text(encoding="utf-8-sig")) for path in _python_files()}

    # 模块级字符串常量：NAME_CONST = "ENV_NAME"
    constants: Dict[Path, Dict[str, str]] = {}
    for path, tree in trees.items():
        table = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name) and NAME.match(node.value.value):
                        table[target.id] = node.value.value
        constants[path] = table

    # 跨模块常量：from app.worker import URL_ENV 之类，按模块路径解析到定义处的常量
    by_module = {}
    for path in trees:
        rel = path.relative_to(ROOT).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        by_module[".".join(parts)] = constants[path]
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module in by_module and node.level == 0:
                for alias in node.names:
                    value = by_module[node.module].get(alias.name)
                    if value:
                        constants[path].setdefault(alias.asname or alias.name, value)

    # 第一遍：识别把某个参数转发给环境变量读取的辅助函数，记录参数位置
    helpers: Dict[str, int] = {}
    for tree in trees.values():
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = [a.arg for a in fn.args.args]
            for node in ast.walk(fn):
                key = None
                if isinstance(node, ast.Call) and _is_env_read(node) and node.args and isinstance(node.args[0], ast.Name):
                    key = node.args[0].id
                elif isinstance(node, ast.Subscript) and ast.unparse(node.value).endswith("environ") and isinstance(node.slice, ast.Name):
                    key = node.slice.id
                if key in params:
                    index = params.index(key)
                    if params and params[0] in ("self", "cls"):
                        index -= 1
                    helpers[fn.name] = index

    def literal(node: ast.AST, path: Path):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and NAME.match(node.value):
            return node.value
        if isinstance(node, ast.Name):
            return constants[path].get(node.id)
        return None

    uses: Dict[str, List[dict]] = defaultdict(list)
    for path, tree in trees.items():
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node)
                index = 0 if _is_env_read(node) else helpers.get(name)
                if index is None or index < 0 or len(node.args) <= index:
                    continue
                var = literal(node.args[index], path)
                if var:
                    default = ast.unparse(node.args[index + 1]) if len(node.args) > index + 1 else None
                    uses[var].append({"file": rel, "line": node.lineno, "reader": name, "default": default})
            elif isinstance(node, ast.Subscript) and ast.unparse(node.value).endswith("environ"):
                var = literal(node.slice, path)
                if var:
                    uses[var].append({"file": rel, "line": node.lineno, "reader": "environ[]", "default": None})
    return dict(uses)
