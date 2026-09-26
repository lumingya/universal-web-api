"""R2-8：在 pytest 中运行前端单元测试（node --test tests/js）；没有 Node 时跳过。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_frontend_unit_tests_pass():
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 Node.js")
    files = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.mjs"))
    assert files, "tests/js 下没有前端测试"
    result = subprocess.run([node, "--test", *files], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (result.stdout[-3000:] + result.stderr[-2000:])
