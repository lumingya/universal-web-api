"""R3-1：导入环的“棘轮”——处在循环依赖中的模块数量只减不增。

背景见 docs/review/HEALTH_ASSESSMENT-2026-10.md：基线时 app/ 下 93/245 个模块处在
导入环里，最大的强连通分量有 63 个模块，全靠 690 处函数内延迟 import 在运行时撑住。
这种结构下任何模块的导入顺序变化都可能在运行时炸掉，而且无法单独测试某个模块。

本测试用 AST 重建 app/ 的**模块级**导入图，再用 Tarjan 求强连通分量（SCC）：
- 有新模块进入环、或环/分量变大 → 测试失败；
- 有模块离开环 → 测试仍然通过，但会提示运行 ``python tests/test_import_cycle_ratchet.py --update``
  把基线降下来，锁定进度（与 test_exception_ratchet.py / test_driver_ratchet.py 同一用法）。

只统计**模块级** import。函数内的延迟 import 不计入，它们恰恰是被本棘轮逼着减少的对象
（由 ruff PLC0415 单独跟踪，见 pyproject.toml 的 lint 配置）。

常用命令：
    python tests/test_import_cycle_ratchet.py --print   # 打印当前所有纠缠分量
    python tests/test_import_cycle_ratchet.py --update  # 调低基线
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "import_cycle_ratchet.json"

# 只扫描 app/：tests/ 与根目录脚本不参与产品依赖图。
SCAN_ROOTS = ("app",)


def _iter_app_modules() -> dict[str, Path]:
    """返回 {模块名: 文件路径}，覆盖已跟踪与未跟踪的 .py（与 test_exception_ratchet 一致）。"""
    try:
        files = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
            cwd=ROOT,
            text=True,
        ).split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [
            str(p.relative_to(ROOT))
            for root in SCAN_ROOTS
            for p in (ROOT / root).rglob("*.py")
            if "__pycache__" not in p.parts
        ]

    modules: dict[str, Path] = {}
    for rel in files:
        rel_path = Path(rel)
        if rel_path.parts[0] not in SCAN_ROOTS:
            continue
        if "__pycache__" in rel_path.parts:
            continue
        mod = rel[:-3].replace("/", ".")
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        modules[mod] = ROOT / rel
    return modules


def _resolve_relative(base: str, node: ast.ImportFrom) -> str | None:
    if not node.level or not node.module:
        return None
    parts = base.split(".")
    # 从包内部发起的相对导入 level=1 表示“当前包”，因此先弹出一层
    for _ in range(node.level - 1):
        if parts:
            parts.pop()
    return ".".join(parts + [node.module]) if parts else node.module


def _build_graph(modules: dict[str, Path]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {m: set() for m in modules}
    for mod, path in modules.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        is_init = path.name == "__init__.py"
        base = mod if is_init else (mod.rsplit(".", 1)[0] if "." in mod else "")

        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    resolved = _resolve_relative(base, node)
                    if resolved:
                        targets.append(resolved)
                elif node.module and node.module.split(".")[0] in SCAN_ROOTS:
                    targets.append(node.module)
            elif isinstance(node, ast.Import):
                targets.extend(a.name for a in node.names if a.name.split(".")[0] in SCAN_ROOTS)

            for target in targets:
                # `app.core.driver.base` 这类子模块导入也算依赖其父包 `app.core.driver`
                if target in modules:
                    graph[mod].add(target)
                else:
                    parent = target.rsplit(".", 1)[0] if "." in target else ""
                    if parent in modules:
                        graph[mod].add(parent)
    return graph


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan 迭代版（避免深递归；app/ 的模块图宽度大于深度）。"""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0

    for root in graph:
        if root in index:
            continue
        work: list[tuple[str, iter]] = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)

        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                result.append(component)
    return result


def current_state() -> dict:
    modules = _iter_app_modules()
    graph = _build_graph(modules)
    components = [c for c in _strongly_connected_components(graph) if len(c) > 1]
    components.sort(key=len, reverse=True)
    in_cycle = sorted({m for comp in components for m in comp})
    return {
        "modules_in_cycle": len(in_cycle),
        "cycles": len(components),
        "largest_component": len(components[0]) if components else 0,
        "modules": in_cycle,
        "components": [sorted(c) for c in components],
    }


def test_no_new_module_enters_a_cycle():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = current_state()
    new_modules = sorted(set(current["modules"]) - set(baseline["modules"]))
    assert not new_modules, (
        "下列模块新进入了导入环。请改为把被依赖的符号下沉到叶子模块，"
        "或用接口/回调注入反转依赖方向，不要用函数内 import 绕过：\n"
        + "\n".join(f"  - {m}" for m in new_modules)
        + "\n\n背景见 docs/review/HEALTH_ASSESSMENT-2026-10.md（P0-1）。"
    )


def test_cycle_counts_do_not_grow():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = current_state()
    problems = []
    for key in ("modules_in_cycle", "cycles", "largest_component"):
        if current[key] > baseline[key]:
            problems.append(f"{key}: {baseline[key]} -> {current[key]}")
    assert not problems, (
        "导入环规模变大了：\n" + "\n".join(f"  - {p}" for p in problems)
    )


def test_baseline_is_reachable():
    """守卫：基线与真实扫描一致（防止有人手改 json 而没跑 --update）。"""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = current_state()
    if current["modules_in_cycle"] < baseline["modules_in_cycle"]:
        print(
            f"\n[棘轮] 环中模块数已下降 {baseline['modules_in_cycle']} -> {current['modules_in_cycle']}，"
            "运行 `python tests/test_import_cycle_ratchet.py --update` 锁定进度。"
        )
    assert baseline["modules"], "基线为空，请先运行 --update 生成"


if __name__ == "__main__":
    state = current_state()
    if "--print" in sys.argv:
        print(f"环中模块 {state['modules_in_cycle']} · 环 {state['cycles']} · 最大分量 {state['largest_component']}")
        for comp in state["components"]:
            print(f"\n=== {len(comp)} 个模块 ===")
            for m in comp:
                print(f"  {m}")
    elif "--update" in sys.argv:
        payload = {
            "modules_in_cycle": state["modules_in_cycle"],
            "cycles": state["cycles"],
            "largest_component": state["largest_component"],
            "modules": state["modules"],
            "components": state["components"],
        }
        BASELINE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"基线已更新：{state['modules_in_cycle']} 个模块在环中、"
            f"{state['cycles']} 个环、最大分量 {state['largest_component']}"
        )
    else:
        print(f"环中模块 {state['modules_in_cycle']} · 环 {state['cycles']} · 最大分量 {state['largest_component']}")
        print("用法：--print 查看纠缠分量，--update 更新基线")
