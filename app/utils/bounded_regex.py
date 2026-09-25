"""带时间预算的正则匹配（修复 H12）。

标准库 `re` 没有任何执行超时：一个嵌套量词的模式配上稍长的输入，
回溯时间就会指数级爆炸，而 `re.search()` 会一直占着调用线程。
命令引擎的网络事件匹配允许用户自定义 `match_mode: "regex"`，
URL 又来自被访问页面，两边都不可信，正好凑齐 ReDoS 的条件。

工作流侧（`app/core/workflow/flow_runtime.py`）早就用 `regex` 库的
`timeout=` 参数做了 25ms 预算，这里把同样的策略抽成公共实现，
供命令引擎等其它调用点复用。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

try:  # pragma: no cover - requirements.txt 已声明该依赖
    import regex as _regex
except ImportError:  # pragma: no cover
    _regex = None


#: 单次匹配的时间预算（秒），与工作流条件求值保持一致
DEFAULT_REGEX_TIMEOUT = 0.025
#: 允许的最大模式长度
MAX_PATTERN_CHARS = 512
#: 允许的最大输入长度（URL 远小于此值；超长输入直接截断而不是硬失败）
MAX_INPUT_CHARS = 8192


class RegexBudgetExceeded(Exception):
    """模式或输入超出限制，或匹配超过时间预算。"""


@lru_cache(maxsize=512)
def _compile(pattern: str, flags: int):
    if _regex is None:
        return re.compile(pattern, flags)
    return _regex.compile(pattern, flags)


def bounded_search(
    pattern: Any,
    text: Any,
    *,
    flags: int = 0,
    timeout: float = DEFAULT_REGEX_TIMEOUT,
    max_pattern_chars: int = MAX_PATTERN_CHARS,
    max_input_chars: int = MAX_INPUT_CHARS,
) -> bool:
    """在时间预算内执行 `search`，返回是否命中。

    * 模式过长 → `RegexBudgetExceeded`（拒绝而不是硬扛）
    * 输入过长 → 截断到 `max_input_chars` 后再匹配（URL 场景下尾部信息价值低，
      截断比直接拒绝更不容易误伤正常规则）
    * 超时 → `RegexBudgetExceeded`
    * 模式非法 → 原样抛 `re.error`，由调用方决定回退策略
    """
    pattern_text = str(pattern or "")
    if len(pattern_text) > max_pattern_chars:
        raise RegexBudgetExceeded(
            f"regex_pattern_too_long:{len(pattern_text)}>{max_pattern_chars}"
        )

    subject = str(text or "")
    if len(subject) > max_input_chars:
        subject = subject[:max_input_chars]

    try:
        compiled = _compile(pattern_text, int(flags))
    except Exception as exc:  # regex.error 与 re.error 都归一到 re.error
        raise re.error(str(exc)) from exc

    if _regex is None:
        # 没有 regex 库时退化为无超时匹配：长度上限仍然生效，
        # 至少把最容易触发指数回溯的超长输入挡在外面。
        return bool(compiled.search(subject))

    try:
        return bool(compiled.search(subject, timeout=timeout))
    except TimeoutError as exc:
        raise RegexBudgetExceeded(f"regex_timeout:{timeout}s") from exc


__all__ = [
    "DEFAULT_REGEX_TIMEOUT",
    "MAX_INPUT_CHARS",
    "MAX_PATTERN_CHARS",
    "RegexBudgetExceeded",
    "bounded_search",
]
