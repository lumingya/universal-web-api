"""R2-1：驱动迁移的“棘轮”——业务代码里直接调用 DrissionPage 的次数只能减少，不能增加。

基线在 tests/fixtures/driver_ratchet.json：每个文件、每种调用的当前次数。
- 次数超过基线或出现新文件：测试失败（新代码请通过 app.core.driver 访问浏览器）；
- 迁移后次数下降：测试仍然通过，但会提示运行
  ``python tests/test_driver_ratchet.py --update`` 把基线降下来，锁定进度。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "driver_ratchet.json"
PATTERNS = {
    "run_js": re.compile(r"\.run_js(?:_loaded)?\("),
    "run_cdp": re.compile(r"\.(?:_)?run_cdp\("),
    "ele": re.compile(r"\.eles?\("),
    "import": re.compile(r"^\s*(?:from DrissionPage|import DrissionPage)", re.M),
}


def current_counts() -> dict:
    files = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "app/*.py"], cwd=ROOT, text=True
    ).split()
    counts = {}
    for rel in sorted(files):
        if rel.startswith("app/core/driver/"):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8-sig")
        per_file = {kind: len(pattern.findall(text)) for kind, pattern in PATTERNS.items()}
        per_file = {kind: n for kind, n in per_file.items() if n}
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
