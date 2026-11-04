"""R3-3：类型检查的“棘轮”——协议层的 mypy 报错数只减不增。

背景见 docs/review/HEALTH_ASSESSMENT-2026-10.md（P1-3）：项目 11.6 万行、
大量 TypedDict 与 pydantic 模型，却**完全没有静态类型检查**。协议层
（OpenAI / Anthropic 兼容接口）正是数据结构最容易漂移的地方，优先覆盖。

范围限定为协议层，且只统计这些文件自身的报错（``--follow-imports=silent``）：
    app/models/schemas.py · app/api/openai/ · app/api/anthropic_routes.py
    app/api/openai_responses_support.py

为什么用 silent 而不是把整个 app/ 纳入：全量跟进导入会得到 2063 条报错，
其中 **1415 条是 attr-defined**，绝大多数来自项目的 **mixin 组合模式**——
如 WorkflowExecutorSendMixin 直接使用 self._attachment_monitor / self.tab，
而这些属性由最终组合类里的其它 mixin 提供；单独分析某个 mixin 时 mypy 看不到。
这是真实的结构性结论（mixin 之间靠隐式契约耦合），但它属于步骤 6/7 拆分
巨型类时才好处理的问题，不应该在这个阶段淹没协议层的真实报错。

用法（与既有棘轮一致）：
    python tests/test_typecheck_ratchet.py            # 查看当前报错
    python tests/test_typecheck_ratchet.py --update   # 下调基线
"""

from __future__ import annotations

import collections
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "mypy_baseline.json"

TARGETS = [
    "app/models/schemas.py",
    "app/api/openai/",
    "app/api/anthropic_routes.py",
    "app/api/openai_responses_support.py",
]

_LINE = re.compile(r"^(app/[^:]+):\d+: error: ")


def run_mypy() -> dict[str, int]:
    """返回 {文件: 报错数}；未安装 mypy 时抛 Skipped。"""
    pytest.importorskip("mypy", reason="未安装 mypy（uv sync 会带上 dev 依赖）")
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", *TARGETS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    counts: dict[str, int] = collections.Counter()
    for line in proc.stdout.splitlines():
        m = _LINE.match(line)
        if m:
            counts[m.group(1)] += 1
    return dict(counts)


def test_type_errors_do_not_increase():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = run_mypy()
    problems = []
    for rel, hits in sorted(current.items()):
        if rel not in baseline:
            problems.append(f"新文件：{rel}（{hits} 条）")
        elif hits > baseline[rel]:
            problems.append(f"{rel}: {baseline[rel]} -> {hits}")
    assert not problems, (
        "协议层类型检查报错变多了。请修正新引入的类型问题；\n"
        "确有必要的豁免请加 `# type: ignore[代码]` 并注明理由，而不是上调基线。\n\n"
        + "\n".join(f"  - {p}" for p in problems)
    )


def test_baseline_is_populated():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert baseline, "基线为空，请先运行 --update 生成"
    current = run_mypy()
    dropped = sum(baseline.get(rel, 0) - hits for rel, hits in current.items())
    removed = sum(v for rel, v in baseline.items() if rel not in current)
    if dropped or removed:
        print(
            f"\n[棘轮] 类型报错已减少 {dropped + removed} 条，"
            "运行 `python tests/test_typecheck_ratchet.py --update` 锁定进度。"
        )


if __name__ == "__main__":
    counts = run_mypy()
    total = sum(counts.values())
    if "--update" in sys.argv:
        BASELINE.write_text(
            json.dumps(dict(sorted(counts.items())), ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        print(f"基线已更新：{len(counts)} 个文件、{total} 条报错")
    else:
        print(f"协议层 mypy 报错：{len(counts)} 个文件、{total} 条")
        for rel, hits in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"    {hits:>4}  {rel}")
        print("\n用法：--update 更新基线")
