"""`ConfigMediaMixin._validate_file_paste_config` 的规范化行为表征测试。

这个函数是**规范化器**而非校验器：它从默认值出发，把 config 里出现的键
逐个归一化后写入结果。它 CC 53 / 120 行，重构前只有几条迁移相关的用例
（test_attachments / test_input_chunking），10 个标量键的归一化规则基本
没有直接覆盖。

下面按"每个键给若干有代表性的输入 + 期望的归一化结果"把现状钉住，
全部在重构**前**跑通。
"""

import pytest

from app.services.config.engine_media import ConfigMediaMixin


class _Engine(ConfigMediaMixin):
    pass


def norm(config, **kwargs):
    return _Engine()._validate_file_paste_config(config, **kwargs)


# --------------------------------------------------------------------------
# enabled / reacquire_input_after_upload：强制转 bool
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
        ("1", True),
        ("0", False),
        ("yes", True),
        ("no", False),
        ("", False),
        (None, False),
        (1, True),
        (0, False),
        ([], False),
    ],
)
def test_enabled_is_coerced_to_bool(raw, expected):
    assert norm({"enabled": raw})["enabled"] is expected


def test_enabled_absent_keeps_default():
    assert norm({})["enabled"] is False


def test_reacquire_input_after_upload_is_coerced_to_bool():
    assert norm({"reacquire_input_after_upload": "yes"})["reacquire_input_after_upload"] is True
    assert norm({"reacquire_input_after_upload": "0"})["reacquire_input_after_upload"] is False
    assert norm({})["reacquire_input_after_upload"] is False


# --------------------------------------------------------------------------
# threshold：int，钳制在 [1000, 10000000]
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (5000, 5000),
        ("5000", 5000),
        (1000, 1000),
        (10000000, 10000000),
        (500, 1000),           # 低于下界 -> 抬到下界
        (-1, 1000),
        (99999999, 10000000),  # 高于上界 -> 压到上界
        (50000.9, 50000),      # 先 int() 再钳制
    ],
)
def test_threshold_is_clamped_int(raw, expected):
    assert norm({"threshold": raw})["threshold"] == expected


@pytest.mark.parametrize("raw", ["abc", None, [], {}, object()])
def test_threshold_unparseable_keeps_default(raw):
    """转换失败时保持默认值，不抛异常。"""
    assert norm({"threshold": raw})["threshold"] == 50000


# --------------------------------------------------------------------------
# temp_file_type：归一化后必须是枚举内成员，否则保持默认
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("txt", "txt"),
        ("pdf", "pdf"),
        ("chunk", "chunk"),
        ("error", "error"),
        ("  TXT  ", "txt"),
        (".PDF", "pdf"),
    ],
)
def test_temp_file_type_normalized_and_filtered(raw, expected):
    assert norm({"temp_file_type": raw})["temp_file_type"] == expected


@pytest.mark.parametrize("raw", ["docx", "", "  ", None, 1, []], ids=repr)
def test_temp_file_type_outside_enum_keeps_default(raw):
    assert norm({"temp_file_type": raw})["temp_file_type"] == "txt"


# --------------------------------------------------------------------------
# 引导文本：截断到 500 字符；hint_text 还会联动三个分类型字段
# --------------------------------------------------------------------------


def test_hint_text_is_truncated_to_500():
    result = norm({"hint_text": "x" * 900})
    assert len(result["hint_text"]) == 500


def test_hint_text_empty_becomes_empty_string():
    assert norm({"hint_text": "   "})["hint_text"] == ""


def test_hint_text_propagates_to_matching_type_only():
    """temp_file_type 决定 hint_text 联动到哪个分类型字段，其余回落默认。"""
    result = norm({"hint_text": "H", "temp_file_type": "txt"})
    assert result["txt_hint_text"] == "H"
    assert result["pdf_hint_text"] == "完全专注于文件内容"
    assert result["error_hint_text"] == "输入文本长度超过限制，已中止发送"

    result = norm({"hint_text": "H", "temp_file_type": "pdf"})
    assert result["pdf_hint_text"] == "H"
    assert result["txt_hint_text"] == "完全专注于文件内容"

    result = norm({"hint_text": "H", "temp_file_type": "error"})
    assert result["error_hint_text"] == "H"
    assert result["txt_hint_text"] == "完全专注于文件内容"


def test_explicit_typed_hint_text_wins_over_hint_text_propagation():
    """显式给出的分类型文本不会被 hint_text 的联动覆盖。"""
    result = norm({"hint_text": "H", "temp_file_type": "txt", "txt_hint_text": "EXPLICIT"})
    assert result["txt_hint_text"] == "EXPLICIT"


@pytest.mark.parametrize("key", ["txt_hint_text", "pdf_hint_text", "error_hint_text"])
def test_typed_hint_texts_are_truncated(key):
    assert len(norm({key: "y" * 800})[key]) == 500


@pytest.mark.parametrize("key", ["txt_hint_text", "pdf_hint_text", "error_hint_text"])
def test_typed_hint_texts_empty_becomes_empty_string(key):
    assert norm({key: "  "})[key] == ""


# --------------------------------------------------------------------------
# post_upload_input_selector：字符串截断 500
# --------------------------------------------------------------------------


def test_post_upload_input_selector_truncated():
    assert len(norm({"post_upload_input_selector": "s" * 700})["post_upload_input_selector"]) == 500


def test_post_upload_input_selector_none_becomes_empty():
    assert norm({"post_upload_input_selector": None})["post_upload_input_selector"] == ""


# --------------------------------------------------------------------------
# 三个数值窗口：转换失败保持默认，成功则钳制
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, raw, expected",
    [
        ("post_upload_settle", 5, 5.0),
        ("post_upload_settle", "2.5", 2.5),
        ("post_upload_settle", -3, 0.0),        # 下界
        ("post_upload_settle", 999, 30.0),      # 上界
        ("upload_signal_timeout", 10, 10.0),
        ("upload_signal_timeout", "3", 3.0),
        ("upload_signal_timeout", 0.1, 0.5),    # 下界 0.5
        ("upload_signal_timeout", 9999, 120.0),  # 上界 120
        ("upload_signal_grace", 7, 7.0),
        ("upload_signal_grace", "0.25", 0.25),
        ("upload_signal_grace", -1, 0.0),       # 下界 0
        ("upload_signal_grace", 9999, 120.0),   # 上界 120
    ],
)
def test_numeric_windows_are_clamped(key, raw, expected):
    assert norm({key: raw})[key] == expected


@pytest.mark.parametrize("key", ["post_upload_settle", "upload_signal_timeout", "upload_signal_grace"])
@pytest.mark.parametrize("raw", ["abc", None, [], {}, object()])
def test_numeric_windows_unparseable_keep_default(key, raw):
    defaults = {"post_upload_settle": 0.0, "upload_signal_timeout": 2.5, "upload_signal_grace": 3.0}
    assert norm({key: raw})[key] == defaults[key]


# --------------------------------------------------------------------------
# state_probe 子段
# --------------------------------------------------------------------------


def test_state_probe_defaults_present():
    assert norm({})["state_probe"] == {"enabled": False, "code": ""}


def test_state_probe_enabled_coerced():
    assert norm({"state_probe": {"enabled": "yes"}})["state_probe"]["enabled"] is True


def test_state_probe_code_truncated_to_20000():
    result = norm({"state_probe": {"code": "c" * 30000}})
    assert len(result["state_probe"]["code"]) == 20000


def test_state_probe_non_dict_keeps_default():
    assert norm({"state_probe": "x"})["state_probe"] == {"enabled": False, "code": ""}


def test_state_probe_partial_override_keeps_other_default():
    result = norm({"state_probe": {"enabled": True}})
    assert result["state_probe"] == {"enabled": True, "code": ""}


# --------------------------------------------------------------------------
# 整体行为
# --------------------------------------------------------------------------


def test_non_dict_config_yields_defaults():
    assert norm("not a dict") == norm({})
    assert norm(None) == norm({})


def test_result_is_idempotent():
    """规范化后的结果再跑一次不变（迁移相关的既有约定）。"""
    once = norm({"enabled": True, "threshold": "12345", "hint_text": "H"})
    assert norm(once) == once


def test_unknown_keys_are_ignored():
    result = norm({"unknown_key": object(), "another": [1, 2]})
    assert "unknown_key" not in result
    assert "another" not in result


def test_attachments_key_absent_without_flag():
    assert "attachments" not in norm({})
