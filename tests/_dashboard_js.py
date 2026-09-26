"""测试共用：dashboard-methods 在 R2-8 拆分到 static/js/dashboard/ 后，按 index.html 的加载顺序拼出完整源码。

读源码文本做断言的测试用 dashboard_methods_source()；需要在 Node 里执行的测试用 dashboard_methods_bundle()
（写到临时目录的拼接文件，各分组都是自包含的 IIFE，顺序拼接后执行与分别加载等价）。
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def dashboard_method_files() -> list:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    srcs = re.findall(r'<script (?:type="module" )?src="/static/js/([^"?]+)\?[^"]*"></script>', html)
    end = srcs.index("dashboard-methods.js")
    parts = [s for s in srcs[:end] if s.startswith("dashboard/")]
    return [ROOT / "static" / "js" / s for s in parts] + [ROOT / "static" / "js" / "dashboard-methods.js"]


def dashboard_methods_source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in dashboard_method_files())


def dashboard_methods_bundle() -> Path:
    target = Path(tempfile.gettempdir()) / "uwapi-tests" / "dashboard-methods.bundle.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dashboard_methods_source(), encoding="utf-8")
    return target
