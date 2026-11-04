"""P0-7: cute_translator runs lazily (once per rendered record) with an LRU cache."""

from __future__ import annotations

import logging

import pytest

from app.core.config_parts import cute_translator as ct
from app.core.config_parts import log_formatters as lf
from app.core.config_parts.browser_constants import BrowserConstants
from app.core.config_parts.secure_logger import SecureLogger

_EAGER = {
    "INFO": ct._cuteify_info_message,
    "SUCCESS": ct._cuteify_info_message,
    "DEBUG": ct._cuteify_debug_message,
    "WARNING": ct._cuteify_warning_message,
    "ERROR": ct._cuteify_error_message,
}

_MESSAGES = [
    "完成 (3.21s)",
    "开始 (标签页 #2)",
    "开始 (域名路由 arena.ai)",
    "发送成功 (重试1.5s)",
    "[FILE_PASTE] 文件粘贴完成 (1234 字符)",
    "[STEALTH] 发送重试 #3",
    "[STEALTH] 随机停顿 +0.8s",
    "请求超时 timeout after 30s",
    "连接已取消 cancelled",
    "plain english message",
    "x" * 5000,
]


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def cute_on(monkeypatch):
    real_get = BrowserConstants.get
    monkeypatch.setattr(
        BrowserConstants,
        "get",
        classmethod(lambda cls, key, *a, **k: True if key in ("LOG_INFO_CUTE_MODE", "LOG_DEBUG_CUTE_MODE") else real_get(key, *a, **k)),
    )
    ct._cuteify_cached.cache_clear()
    yield


@pytest.mark.parametrize("level_key", sorted(_EAGER))
def test_lazy_display_matches_eager_translation(cute_on, level_key):
    for name in ("REQUEST", "STREAM", "TabPool"):
        for msg in _MESSAGES:
            eager = _EAGER[level_key](name, msg)
            assert ct.cuteify_display_message(level_key, name, msg) == eager

            record = logging.LogRecord(name, logging.INFO, "", 0, msg, (), None)
            record.codex_original_message_text = msg
            record.codex_logger_name = name
            record.codex_kind = level_key
            record.codex_display_pending = True
            reference = logging.LogRecord(name, logging.INFO, "", 0, msg, (), None)
            reference.codex_original_message_text = msg
            reference.codex_logger_name = name
            reference.codex_display_message_text = eager
            assert lf._record_display_message(record) == lf._record_display_message(reference)
            assert record.codex_display_pending is False


def test_translation_runs_once_per_record_and_is_cached(cute_on, monkeypatch):
    calls = []
    real = ct._cuteify_info_message
    monkeypatch.setattr(ct, "_cuteify_info_message", lambda n, m: (calls.append(m), real(n, m))[1])
    ct._cuteify_cached.cache_clear()

    log = SecureLogger("LAZYTEST")
    capture = _Capture()
    saved_handlers = list(log._logger.handlers)
    for handler in saved_handlers:  # only our capture handler: nothing renders the record
        log._logger.removeHandler(handler)
    log._logger.addHandler(capture)
    try:
        log.info("完成 (1.00s)")
        record = capture.records[-1]
        assert calls == []  # nothing rendered yet -> nothing translated
        first = lf._record_display_message(record)
        second = lf._record_display_message(record)
        assert first == second and len(calls) == 1
        log.info("完成 (1.00s)")
        lf._record_display_message(capture.records[-1])
        assert len(calls) == 1  # LRU hit for the repeated message
    finally:
        log._logger.removeHandler(capture)
        for handler in saved_handlers:
            log._logger.addHandler(handler)


def test_cute_mode_off_returns_original(monkeypatch):
    real_get = BrowserConstants.get
    monkeypatch.setattr(
        BrowserConstants,
        "get",
        classmethod(lambda cls, key, *a, **k: False if key in ("LOG_INFO_CUTE_MODE", "LOG_DEBUG_CUTE_MODE") else real_get(key, *a, **k)),
    )
    assert ct.cuteify_display_message("INFO", "REQUEST", "完成 (3.21s)") == "完成 (3.21s)"


def test_all_debug_matchers_are_registered_in_order():
    """守卫：_cuteify_debug_part_* 必须全部登记进 _CUTE_DEBUG_MATCHERS。

    _cuteify_debug_message 原是 619 行 / 圈复杂度 127 的单体函数，已按顺序**逐字**
    拆成多段（见 docs/plans/REFACTOR_PLAN-2026-10.md 步骤 8）。拆分后本函数只做
    分发：按顺序尝试 _CUTE_DEBUG_MATCHERS，第一个非 None 即命中。

    因此**漏登记某一段 = 静默丢掉一整批翻译规则**，且不会报错、只会让日志变回原文，
    极易在后续改动中被忽略。这里锁住两件事：
    - 每个 _cuteify_debug_part_N 都在表里（不许漏）；
    - 表里顺序与编号一致（匹配顺序直接影响命中结果）。
    """
    parts = {
        int(name.rsplit("_", 1)[1]): fn
        for name, fn in vars(ct).items()
        if name.startswith("_cuteify_debug_part_") and callable(fn)
    }
    assert parts, "没有找到任何 _cuteify_debug_part_* 分段函数"

    expected = tuple(parts[i] for i in sorted(parts))
    assert ct._CUTE_DEBUG_MATCHERS == expected, (
        "_CUTE_DEBUG_MATCHERS 与 _cuteify_debug_part_* 不一致："
        "要么有分段漏登记，要么顺序被打乱（匹配顺序会影响命中结果）"
    )


def test_debug_message_still_translates_after_split(monkeypatch):
    """拆分后行为不变：断言精确译文，而不是"包含某个词"。

    期望值取自拆分前实现的真实输出；拆分时另用 52 条语料做过 before/after 差分
    （21 条命中翻译，0 处不一致），这里只留下若干代表例做常驻守卫。
    """
    monkeypatch.setattr(BrowserConstants, "get", staticmethod(lambda key, default=None: True))

    cases = {
        "[STEALTH] 发送重试 #3":
            "小鹿发现消息没动静，又轻轻点了一下发送按钮（第 3 次）喵",
        "标签页 abc123 分配编号 #7":
            "小鹿给标签页 abc123 贴上了固定编号 #7 喵",
        "输入验证通过 (len=12, diff=+3)":
            "小鹿核对过输入啦，长度 12，和目标差了 +3 个字符喵",
        "[NetworkMonitor] 检测到结束标志，完成监听":
            "小鹿看到结束标记啦，这轮监听可以收尾了喵",
    }
    for raw, expected in cases.items():
        assert ct._cuteify_debug_message("TEST", raw) == expected, (
            f"译文变了，说明某段规则错位或丢失：{raw!r}"
        )
