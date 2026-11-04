"""R4：复杂度“棘轮”——高复杂度函数的数量与各自的圈复杂度只减不增。

背景见 docs/review/HEALTH_ASSESSMENT-2026-10.md 与
docs/plans/REFACTOR_PLAN-2026-10.md：本项目最大的结构性问题是少数巨型函数
（`_execute_workflow_stream_once` CC 357、`_execute_action` CC 123 等），
它们处在 60+ 模块的导入环里，覆盖率极低，改一处就要理解上千行。

前面几步已经把它们逐个降级，但**没有守护的话，进度会被日常改动慢慢吃掉**：
往某个大函数里再加两个 if，CC 就从 30 变 32，没人会注意到。本棘轮的作用
就是让这类增长立刻失败。

设计取舍
--------
- 只跟踪 CC >= THRESHOLD（默认 30）的函数。全量记录上千个函数会让基线
  变成噪音，也失去"重点守护"的意义；CC < 30 的函数增长不阻断，但计入
  总数提示。
- 使用 radon 的 Python API（`radon.complexity.cc_visit`）而非子进程，
  避免每个用例都付出一次解释器启动开销。
- 复杂度的"合理值"因函数而异，本棘轮**不做绝对阈值判断**（不设"CC 必须
  < 10"这类规则），只做相对基线判断——这与其它几条棘轮一致：先锁住不让
  增长，再随重构逐步下调。

用法（与 test_layering_ratchet.py 一致）
--------------------------------------
- 某函数 CC 超过基线、或出现新的超标函数 → 测试失败；
- CC 下降 → 测试仍通过，提示运行
  ``python tests/test_complexity_ratchet.py --update`` 下调基线，锁定进度。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from radon.complexity import cc_visit

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "complexity_ratchet.json"

# 低于此值的函数不纳入基线（噪音太大），但仍计入总数供参考
THRESHOLD = 30


def _iter_python_files() -> list[Path]:
    root = ROOT / "app"
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _qualified_name(block) -> str:
    """优先用 class.method 形式，避免不同类下的同名方法互相覆盖。"""
    if getattr(block, "classname", None):
        return f"{block.classname}.{block.name}"
    return block.name


def current_state() -> dict[str, int]:
    """返回 {文件路径::限定名: CC}，只包含 CC >= THRESHOLD 的函数。"""
    state: dict[str, int] = {}
    for path in _iter_python_files():
        rel = path.relative_to(ROOT).as_posix()
        try:
            source = path.read_text(encoding="utf-8-sig")
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        try:
            blocks = cc_visit(source)
        except Exception:
            # 语法错误等由 ruff / 其它用例负责，这里跳过以免误报
            continue
        for block in blocks:
            if block.complexity >= THRESHOLD:
                state[f"{rel}::{_qualified_name(block)}"] = block.complexity
    return dict(sorted(state.items(), key=lambda kv: (-kv[1], kv[0])))


def test_no_new_or_worsened_complexity():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = current_state()
    problems: list[str] = []

    for key, cc in current.items():
        if key not in baseline:
            problems.append(f"新超标函数：{key}（CC {cc}）")
        elif cc > baseline[key]:
            problems.append(f"{key}: CC {baseline[key]} -> {cc}")

    over = len(current)
    over_before = len(baseline)
    if over > over_before:
        problems.append(
            f"CC >= {THRESHOLD} 的函数数量增加：{over_before} -> {over}"
        )

    assert not problems, (
        "复杂度出现增长。\n"
        "正确做法：把新增的分支提取成独立函数/查表，而不是继续往原函数里加；\n"
        "确有必要的复杂分支请加注释说明理由。\n\n"
        + "\n".join(f"  - {p}" for p in problems)
    )


def test_baseline_still_matches_reality():
    """守卫：基线非空；若有下降则提示 --update（与既有棘轮一致）。"""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = current_state()
    assert baseline, "基线为空，请先运行 --update 生成"

    dropped = {
        k: (baseline[k], current[k])
        for k in baseline
        if k in current and current[k] < baseline[k]
    }
    gone = [k for k in baseline if k not in current]
    if dropped or gone:
        print(
            f"\n[棘轮] 复杂度已下降：{len(dropped)} 个函数降低、{len(gone)} 个已达标，"
            "运行 `python tests/test_complexity_ratchet.py --update` 锁定进度。"
        )
        for k, (old, new) in sorted(dropped.items(), key=lambda kv: kv[1][0] - kv[1][1],
                                    reverse=True)[:10]:
            print(f"    {k}: CC {old} -> {new}")
        for k in gone[:10]:
            print(f"    {k}: CC {baseline[k]} -> 已降至 {THRESHOLD} 以下")


if __name__ == "__main__":
    state = current_state()
    if "--update" in sys.argv:
        BASELINE.write_text(
            json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"已更新基线：{len(state)} 个函数 CC >= {THRESHOLD}")
    else:
        print(f"当前 CC >= {THRESHOLD} 的函数：{len(state)} 个")
        for k, v in list(state.items())[:20]:
            print(f"  {v:4d}  {k}")
