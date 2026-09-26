"""R2-1：驱动迁移的“棘轮”——业务代码里直接调用 DrissionPage 的次数只能减少，不能增加。

第 6 步（收口）已完成：基线为空，app/ 中除 app/core/driver/ 以外不允许任何直接调用
（run_js / run_cdp / ele / eles / .actions / .listen / import DrissionPage）。

基线在 tests/fixtures/driver_ratchet.json：每个文件、每种调用的当前次数（按 AST 统计；
接收者是驱动对象的调用——driver_for_tab(...).run_js(...)、session.driver.find(...)——不计入）。
- 次数超过基线或出现新文件：测试失败（新代码请通过 app.core.driver 访问浏览器）；
- 迁移后次数下降：测试仍然通过，但会提示运行
  ``python tests/test_driver_ratchet.py --update`` 把基线降下来，锁定进度。
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "driver_ratchet.json"
IMPORT_PATTERN = re.compile(r"^\s*(?:from DrissionPage|import DrissionPage)", re.M)
_CALL_KINDS = {"run_js": "run_js", "run_js_loaded": "run_js", "run_cdp": "run_cdp", "_run_cdp": "run_cdp",
               "ele": "ele", "eles": "ele"}


def _is_driver_receiver(node: ast.AST) -> bool:
    """接收者是驱动对象：driver_for_tab(...) 的返回值，或名字里带 driver 的变量/属性（session.driver、self.driver）。"""
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
        return name in ("driver_for_tab", "as_element", "browser_driver")
    if isinstance(node, ast.Name):
        return "driver" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "driver" in node.attr.lower()
    return False


def _count_file(text: str) -> dict:
    counts: dict = {}
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _CALL_KINDS:
            if not _is_driver_receiver(node.func.value):
                kind = _CALL_KINDS[node.func.attr]
                counts[kind] = counts.get(kind, 0) + 1
        # DrissionPage 专有的键鼠动作链与网络监听：非驱动对象上的 .actions / .listen 访问
        elif isinstance(node, ast.Attribute) and node.attr in ("actions", "listen") and not _is_driver_receiver(node.value):
            receiver = ast.unparse(node.value)
            if receiver.split(".")[-1] in ("tab", "page", "tab_obj", "target_tab", "source_tab") or receiver in ("tab", "page"):
                counts[node.attr] = counts.get(node.attr, 0) + 1
    imports = len(IMPORT_PATTERN.findall(text))
    if imports:
        counts["import"] = imports
    return counts


def current_counts() -> dict:
    files = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "app/*.py"], cwd=ROOT, text=True
    ).split()
    counts = {}
    for rel in sorted(files):
        if rel.startswith("app/core/driver/"):
            continue
        per_file = _count_file((ROOT / rel).read_text(encoding="utf-8-sig"))
        if per_file:
            counts[rel] = per_file
    return counts


def test_direct_drissionpage_calls_do_not_increase():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = current_counts()
    regressions = []
    for rel, per_file in current.items():
        for kind, n in per_file.items():
            allowed = baseline.get(rel, {}).get(kind, 0)
            if n > allowed:
                regressions.append(f"{rel}: {kind} {allowed} -> {n}")
    assert not regressions, (
        "业务代码新增了直接调用 DrissionPage 的地方，请改用 app.core.driver（driver_for_tab / TabSession.driver）：\n"
        + "\n".join(regressions)
    )
    improved = [
        rel for rel, per_file in baseline.items()
        if any(current.get(rel, {}).get(kind, 0) < n for kind, n in per_file.items())
    ]
    if improved:
        print(f"迁移有进展（{len(improved)} 个文件调用次数下降），可运行 python tests/test_driver_ratchet.py --update 锁定")


if __name__ == "__main__":
    if "--update" in sys.argv:
        counts = current_counts()
        BASELINE.write_text(json.dumps(counts, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        total = sum(sum(v.values()) for v in counts.values())
        print(f"基线已更新：{len(counts)} 个文件，共 {total} 处直接调用")


def test_ratchet_ignores_calls_through_the_driver():
    sample = (
        "tab.run_js('a')\n"
        "driver_for_tab(tab).run_js('b')\n"
        "session.driver.find_all('x')\n"
        "self.driver.run_cdp('Page.reload')\n"
        "self.tab.ele('css:x')\n"
        "from DrissionPage import ChromiumPage\n"
        "as_element(ele).run_js('c')\n"
        "browser_driver(browser).run_cdp('Target.getTargets')\n"
        "self.tab.actions.move_to(ele)\n"
        "driver_for_tab(self.tab).actions.click()\n"
        "self.tab.listen.start('x')\n"
    )
    assert _count_file(sample) == {"run_js": 1, "ele": 1, "import": 1, "actions": 1, "listen": 1}


def test_migration_is_closed_no_direct_drissionpage_usage_outside_driver():
    """收口：基线必须保持为空，业务代码一律经由 app.core.driver 访问浏览器。"""
    assert json.loads(BASELINE.read_text(encoding="utf-8")) == {}
    assert current_counts() == {}

