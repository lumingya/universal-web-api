"""R3-2：分层的“棘轮”——反向依赖（下层 import 上层）的数量只减不增。

背景见 docs/review/HEALTH_ASSESSMENT-2026-10.md（P0-1）：`app.core` 与 `app.services`
互相 import（services→core 87 处、core→services 54 处），两者实际是同一个东西，
`app/` 下 38% 的模块因此处在导入环里。

本项目已有的 `test_structure.py::test_admin_layer_dependency_direction_is_one_way`
证明了团队认可“分层方向需要守护”，本测试把同样的思路推广到两对最严重的反向依赖：

- ``app.core`` 不得 import ``app.services``（基础设施不应依赖业务层）
- ``app.utils`` 不得 import ``app.core`` / ``app.services`` / ``app.api``（工具层应为叶子）

关于第二条的现状说明：当前 app/utils 对 app.core 的 11 处依赖，绝大部分指向
``core.config``（logger/get_logger）、``core.driver``、``core.http_security``
这三个**本应属于最底层的原语**。把它们下沉到一个真正的叶子层（例如 ``app/base/``）
后，utils→base 即为合法，届时本规则可收紧为“禁止 import 整个 app.core”。
在此之前，本规则的作用是**先锁住不让增长**，再随下沉工作逐项消减。

与 test_driver_ratchet.py 同一用法：
- 出现新文件、或某文件次数超过基线 → 测试失败；
- 次数下降 → 测试仍通过，提示运行 ``python tests/test_layering_ratchet.py --update``
  下调基线，锁定进度。

统计范围包含函数内延迟 import（PLC0415）——那正是绕过分层的手法，不能放过。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "layering_ratchet.json"

# (被扫描的子树, 禁止 import 的前缀, 规则名)
RULES = [
    ("app/core", ("app.services",), "core_must_not_import_services"),
    ("app/utils", ("app.core", "app.services", "app.api"), "utils_must_stay_a_leaf"),
]


def _iter_python_files(subtree: str) -> list[Path]:
    """直接遍历目录。

    不用 `git ls-files 'app/core/**/*.py'`：git 的 `**` 需要 :(glob) magic，
    普通 pathspec 下匹配不到深层子目录，会**漏掉文件**让基线偏小
    （实测漏了 stream_monitor.py / network_monitor.py / chat_job.py / page_lifecycle.py）。
    """
    root = ROOT / subtree
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _count_violations(subtree: str, forbidden: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in _iter_python_files(subtree):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        hits = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(forbidden):
                    hits += 1
            elif isinstance(node, ast.Import):
                hits += sum(1 for a in node.names if a.name.startswith(forbidden))
        if hits:
            counts[path.relative_to(ROOT).as_posix()] = hits
    return counts


def current_state() -> dict:
    return {name: _count_violations(subtree, forbidden) for subtree, forbidden, name in RULES}


def test_no_new_layering_violation():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = current_state()
    problems: list[str] = []
    for rule, counts in current.items():
        known = baseline.get(rule, {})
        for rel, hits in sorted(counts.items()):
            if rel not in known:
                problems.append(f"[{rule}] 新文件：{rel}（{hits} 处）")
            elif hits > known[rel]:
                problems.append(f"[{rule}] {rel}: {known[rel]} -> {hits}")
    assert not problems, (
        "出现了新的反向依赖（下层 import 上层）。\n"
        "正确做法：把被依赖的符号下沉到更低层的模块，或用接口/回调注入反转依赖方向；\n"
        "不要新增函数内 delay import 绕过——那正是本棘轮要收敛的对象。\n\n"
        + "\n".join(f"  - {p}" for p in problems)
    )


def test_baseline_still_matches_reality():
    """守卫：基线非空；若有下降则提示 --update（与既有棘轮一致）。"""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = current_state()
    assert baseline, "基线为空，请先运行 --update 生成"
    for rule, counts in current.items():
        known = baseline.get(rule, {})
        dropped = sum(known.get(rel, 0) - hits for rel, hits in counts.items() if rel in known)
        removed = sum(v for rel, v in known.items() if rel not in counts)
        if dropped or removed:
            print(
                f"\n[棘轮] {rule} 已减少 {dropped + removed} 处，"
                "运行 `python tests/test_layering_ratchet.py --update` 锁定进度。"
            )


if __name__ == "__main__":
    state = current_state()
    if "--update" in sys.argv:
        BASELINE.write_text(
            json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for rule, counts in state.items():
            print(f"{rule}: {len(counts)} 个文件、{sum(counts.values())} 处 → 基线已更新")
    else:
        for rule, counts in state.items():
            print(f"{rule}: {len(counts)} 个文件、{sum(counts.values())} 处")
            for rel, hits in sorted(counts.items(), key=lambda kv: -kv[1]):
                print(f"    {hits:>3}  {rel}")
        print("\n用法：--update 更新基线")
