"""R2-7：宽泛异常捕获的“棘轮”——`except Exception` / `except BaseException` / 裸 `except:` 的数量只减不增。

基线在 tests/fixtures/broad_except_ratchet.json（按文件计数）。确有必要的宽泛捕获（例如兜底清理、第三方回调），
在 except 那一行写上 ``# broad-except: 理由`` 即不计入。收窄或删除后运行
``python tests/test_exception_ratchet.py --update`` 把基线降下来。
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "broad_except_ratchet.json"
MARKER = "broad-except:"


def _count(text: str) -> int:
    lines = text.splitlines()
    count = 0
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ExceptHandler):
            broad = node.type is None or (isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException"))
            if broad and MARKER not in lines[node.lineno - 1]:
                count += 1
    return count


def current_counts() -> dict:
    files = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"], cwd=ROOT, text=True
    ).split()
    counts = {}
    for rel in sorted(files):
        if rel.startswith("tests/") or rel.startswith("scripts/"):
            continue
        n = _count((ROOT / rel).read_text(encoding="utf-8-sig"))
        if n:
            counts[rel] = n
    return counts


def test_broad_excepts_do_not_increase():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = current_counts()
    grew = [f"{rel}: {baseline.get(rel, 0)} -> {n}" for rel, n in current.items() if n > baseline.get(rel, 0)]
    assert not grew, (
        "新增了宽泛的异常捕获。请捕获具体的异常类型；确有必要时在 except 行注明 `# broad-except: 理由`：\n"
        + "\n".join(grew)
    )


def test_marker_is_recognised():
    sample = (
        "try:\n    x = 1\nexcept Exception:\n    pass\n"
        "try:\n    y = 2\nexcept Exception:  # broad-except: 回调来自第三方库，任何异常都不能冒泡\n    pass\n"
        "try:\n    z = 3\nexcept ValueError:\n    pass\n"
    )
    assert _count(sample) == 1


if __name__ == "__main__":
    if "--update" in sys.argv:
        counts = current_counts()
        BASELINE.write_text(json.dumps(counts, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"基线已更新：{len(counts)} 个文件，共 {sum(counts.values())} 处宽泛捕获")
