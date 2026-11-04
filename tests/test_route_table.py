"""API 路由表棘轮：重构（拆文件、换 router 组织方式）不得改变对外 URL。

基线文件：``tests/fixtures/route_table.json``（路径 -> 允许的方法集合）。

为什么需要它：``app/api/`` 下的路由分布在多个模块里，拆分/搬迁时最容易出的错是
路径写错、方法漏加、或者两个 router 注册了同一路径互相遮蔽。这个测试在**不启动服务**的
情况下把 ``app.routes`` 的真实注册结果导出成表，与基线逐项比较。

更新基线（只在确实要改对外 API 时）：

    python tests/test_route_table.py --update
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "route_table.json"

# 这些路径由框架/静态挂载产生，不属于路由表比较范围（挂载点单独验证）。
IGNORED_PREFIXES = ("/static", "/download_images")


def current_route_table() -> dict[str, list[str]]:
    import main

    table: dict[str, list[str]] = {}
    for route in main.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        if path.startswith(IGNORED_PREFIXES):
            continue
        table.setdefault(path, set()).update(m.upper() for m in methods)
    return {path: sorted(methods) for path, methods in sorted(table.items())}


def test_route_table_matches_baseline():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["routes"]
    current = current_route_table()

    missing = sorted(set(baseline) - set(current))
    added = sorted(set(current) - set(baseline))
    changed = sorted(
        f"{path}: {baseline[path]} -> {current[path]}"
        for path in set(baseline) & set(current)
        if baseline[path] != current[path]
    )

    assert not missing, "这些 URL 在重构后消失了：\n" + "\n".join(missing)
    assert not added, (
        "出现了基线里没有的 URL。若是有意新增，请运行 `python tests/test_route_table.py --update`：\n"
        + "\n".join(added)
    )
    assert not changed, "这些 URL 允许的方法变了：\n" + "\n".join(changed)


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/messages", "/v1/models", "/health"])
def test_core_endpoints_are_registered(path):
    assert path in current_route_table()


def test_no_duplicate_method_registration():
    """同一 (path, method) 只能注册一次，否则前一条会被静默遮蔽。"""
    import main

    seen: dict[tuple[str, str], str] = {}
    duplicates = []
    for route in main.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or path.startswith(IGNORED_PREFIXES):
            continue
        endpoint = getattr(route, "endpoint", None)
        label = f"{getattr(endpoint, '__module__', '?')}.{getattr(endpoint, '__name__', '?')}"
        for method in methods:
            key = (path, method.upper())
            if key in seen:
                duplicates.append(f"{key[1]} {path}: {seen[key]} 与 {label}")
            else:
                seen[key] = label
    assert not duplicates, "同一路径被注册了多次：\n" + "\n".join(duplicates)


if __name__ == "__main__":
    # 直接以脚本方式运行时，把仓库根加入 sys.path，`import main` 才可用
    sys.path.insert(0, str(ROOT))
    if "--update" in sys.argv:
        payload = {
            "_comment": "app.routes 的对外 URL 快照；重构时不应变化。更新：python tests/test_route_table.py --update",
            "routes": current_route_table(),
        }
        BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"路由基线已更新：{len(payload['routes'])} 条路径 -> {BASELINE}")
