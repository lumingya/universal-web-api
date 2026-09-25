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
