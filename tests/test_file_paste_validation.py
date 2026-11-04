"""`_validate_file_paste` 的字段级表征测试。

重构前先锁定行为。校验器是纯布尔谓词：给一个 `file_paste` 段，回答"合不合法"。
真正的风险是**逐字段的规则**：哪些键必须是 bool、哪些必须是 int（注意
`isinstance(True, int)` 为真，所以 `threshold: True` 是合法的）、枚举集合有哪
几个成员、两个子段各自接受哪些键。这些规则一旦被改坏，现有的 14 个粗粒度用例
多半发现不了——它们只碰了 `send_confirmation` 里的 `retry_interval`。

这里按"每个字段 × 典型合法值 × 典型非法值"穷举，把规则逐条钉死。
"""

from __future__ import annotations

import pytest

from app.models.schemas import _validate_file_paste, validate_site_config


def base_config(file_paste):
    """最小合法骨架（与 test_site_config_validation 保持一致）再叠加 file_paste 段。

    带上 selectors / workflow 是为了让 `validate_site_config` 的分派路径也能跑通，
    从而在"直接调用校验器"与"走整体分派"之间做交叉验证。
    """
    return {"selectors": {}, "workflow": [], "file_paste": file_paste}


# --------------------------------------------------------------------------
# 标量字段：类型检查
# --------------------------------------------------------------------------

# (键, 合法值列表, 非法值列表)
SCALAR_TYPE_CASES = [
    ("enabled", [True, False], ["yes", 1, 0, None, [], {}]),
    ("threshold", [0, 120000, -5, True], ["120000", 1.5, None, [], {}]),
    ("hint_text", ["", "x", "上传文件"], [1, None, [], {}, True]),
    ("txt_hint_text", ["", "x"], [1, None, [], {}]),
    ("pdf_hint_text", ["", "x"], [1, None, [], {}]),
    ("error_hint_text", ["", "x"], [1, None, [], {}]),
    ("reacquire_input_after_upload", [True, False], ["yes", 1, None, []]),
    ("post_upload_input_selector", ["", "textarea", "div > input"], [1, None, [], {}]),
    ("post_upload_settle", [0, 1.5, -1, True, False], ["1.5", None, [], {}]),
    ("upload_signal_timeout", [0, 30.0, -1], ["30", None, [], {}]),
    ("upload_signal_grace", [0, 0.5, 100], ["0.5", None, [], {}]),
]

# temp_file_type 是枚举，规则与其他标量不同，单独处理
TEMP_FILE_TYPE_VALID = ["txt", "pdf", "chunk", "error"]
TEMP_FILE_TYPE_INVALID = ["zip", "png", "", "docx", "TXTX"]


@pytest.mark.parametrize("key,valid,invalid", SCALAR_TYPE_CASES)
def test_scalar_field_accepts_declared_type(key, valid, invalid):
    """每个标量字段：声明的类型通过，其他类型一律拒绝。"""
    for value in valid:
        assert _validate_file_paste(base_config({key: value})) is True, f"{key}={value!r} 应合法"
    for value in invalid:
        assert _validate_file_paste(base_config({key: value})) is False, f"{key}={value!r} 应非法"


@pytest.mark.parametrize("value", TEMP_FILE_TYPE_VALID)
def test_temp_file_type_enum_members_accepted(value):
    for candidate in [value, value.upper(), " " + value + " ", value.capitalize()]:
        assert _validate_file_paste(base_config({"temp_file_type": candidate})) is True, repr(candidate)


@pytest.mark.parametrize("value", TEMP_FILE_TYPE_INVALID + [[], {}, None, 1, True])
def test_temp_file_type_non_members_rejected(value):
    assert _validate_file_paste(base_config({"temp_file_type": value})) is False, repr(value)


def test_fields_are_optional():
    """字段全部可选；空段合法；未出现的键不做检查。"""
    for config in [{}, {"enabled": True}, {"hint_text": "x"}, {"threshold": 1, "temp_file_type": "pdf"}]:
        assert _validate_file_paste(base_config(config)) is True, repr(config)


def test_unknown_keys_are_ignored():
    """校验器只认已知字段；额外键不参与校验。"""
    assert _validate_file_paste(base_config({"totally_unknown": object(), "another": [1, 2]})) is True


def test_first_violation_wins_regardless_of_position():
    """多个字段同时非法时结果仍为 False（谓词是合取，与顺序无关）。"""
    bad = {
        "enabled": "yes",
        "threshold": "x",
        "temp_file_type": "zip",
        "hint_text": 1,
        "upload_signal_timeout": None,
    }
    assert _validate_file_paste(base_config(bad)) is False


# --------------------------------------------------------------------------
# state_probe 子段
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state_probe",
    [
        {},
        {"enabled": True},
        {"enabled": False},
        {"code": ""},
        {"code": "return 1"},
        {"enabled": True, "code": "x"},
        {"unknown": object()},
    ],
)
def test_state_probe_valid(state_probe):
    assert _validate_file_paste(base_config({"state_probe": state_probe})) is True, repr(state_probe)


@pytest.mark.parametrize(
    "state_probe",
    [
        "x",
        [],
        1,
        None,
        True,
        {"enabled": "yes"},
        {"enabled": 1},
        {"code": 1},
        {"code": None},
        {"code": []},
    ],
)
def test_state_probe_invalid(state_probe):
    assert _validate_file_paste(base_config({"state_probe": state_probe})) is False, repr(state_probe)


# --------------------------------------------------------------------------
# send_confirmation 子段：逐字段穷举
# --------------------------------------------------------------------------

SEND_CONFIRMATION_SPEC = {
    "numeric": [
        "post_click_observe_window",
        "pre_retry_probe_window",
        "retry_observe_window",
        "attachment_observe_window",
        "retry_interval",
        "retry_cooldown_window",
    ],
    "int": ["max_retry_count"],
    "string": ["retry_key_combo"],
    "bool": [
        "retry_on_unconfirmed_send",
        "accept_attachment_change",
        "accept_attachment_disappear",
        "accept_probe_confirmation",
        "retry_block_on_stop_button",
        "retry_block_if_generating",
        "trust_network_activity",
        "trust_generating_indicator",
        "trust_send_disabled_with_input_shrink",
    ],
    "enum": {
        "attachment_sensitivity": ({"low", "medium", "high"}, {"LOW", " Medium ", "HIGH"}),
        "retry_action": ({"click_send_btn", "key_press"}, {"CLICK_SEND_BTN", " Key_Press "}),
    },
}


@pytest.mark.parametrize("key", SEND_CONFIRMATION_SPEC["numeric"])
def test_send_confirmation_numeric_fields(key):
    for value in [0, 1, 1.5, -2, True, False]:
        cfg = base_config({"send_confirmation": {key: value}})
        assert _validate_file_paste(cfg) is True, f"{key}={value!r}"
    for value in ["1.5", None, [], {}, object()]:
        cfg = base_config({"send_confirmation": {key: value}})
        assert _validate_file_paste(cfg) is False, f"{key}={value!r}"


@pytest.mark.parametrize("key", SEND_CONFIRMATION_SPEC["int"])
def test_send_confirmation_int_fields(key):
    for value in [0, 3, -1, True]:
        cfg = base_config({"send_confirmation": {key: value}})
        assert _validate_file_paste(cfg) is True, f"{key}={value!r}"
    for value in [1.5, "3", None, [], {}]:
        cfg = base_config({"send_confirmation": {key: value}})
        assert _validate_file_paste(cfg) is False, f"{key}={value!r}"


@pytest.mark.parametrize("key", SEND_CONFIRMATION_SPEC["string"])
def test_send_confirmation_string_fields(key):
    for value in ["", "ctrl+enter", "x"]:
        cfg = base_config({"send_confirmation": {key: value}})
        assert _validate_file_paste(cfg) is True, f"{key}={value!r}"
    for value in [1, None, [], {}, True]:
        cfg = base_config({"send_confirmation": {key: value}})
        assert _validate_file_paste(cfg) is False, f"{key}={value!r}"


@pytest.mark.parametrize("key", SEND_CONFIRMATION_SPEC["bool"])
def test_send_confirmation_bool_fields(key):
    """bool 字段必须是真正的 bool——注意 int 不是 bool。"""
    for value in [True, False]:
        cfg = base_config({"send_confirmation": {key: value}})
        assert _validate_file_paste(cfg) is True, f"{key}={value!r}"
    for value in [1, 0, "true", None, [], {}]:
        cfg = base_config({"send_confirmation": {key: value}})
        assert _validate_file_paste(cfg) is False, f"{key}={value!r}"


@pytest.mark.parametrize("key", list(SEND_CONFIRMATION_SPEC["enum"]))
def test_send_confirmation_enum_fields(key):
    """枚举成员大小写与首尾空白被规范化。"""
    allowed, normalized_ok = SEND_CONFIRMATION_SPEC["enum"][key]
    for value in sorted(allowed) + sorted(normalized_ok):
        cfg = base_config({"send_confirmation": {key: value}})
        assert _validate_file_paste(cfg) is True, f"{key}={value!r}"
    for value in ["nope", "", "medium2", None, 1, []]:
        cfg = base_config({"send_confirmation": {key: value}})
        assert _validate_file_paste(cfg) is False, f"{key}={value!r}"


@pytest.mark.parametrize("section", ["x", [], 1, None, True])
def test_send_confirmation_must_be_dict(section):
    assert _validate_file_paste(base_config({"send_confirmation": section})) is False


# --------------------------------------------------------------------------
# attachment_monitor 子段（重构前完全未被覆盖）
# --------------------------------------------------------------------------


# attachment_monitor 是可选段（默认配置里没有），字段清单在此显式列出。
ATTACHMENT_MONITOR_SPEC = {
    "numeric": ["idle_timeout", "hard_max_wait"],
    "bool": [
        "require_attachment_present",
        "require_upload_signal_before_ready",
        "continue_once_on_unconfirmed_send",
    ],
    "list_of_str": [
        "root_selectors",
        "attachment_selectors",
        "pending_selectors",
        "status_selectors",
        "content_exclusion_selectors",
        "busy_text_markers",
        "ignored_busy_text_markers",
        "send_button_disabled_markers",
    ],
}


@pytest.mark.parametrize("section", ["x", [], 1, None, True, 1.5])
def test_attachment_monitor_must_be_dict(section):
    assert _validate_file_paste(base_config({"attachment_monitor": section})) is False


def test_attachment_monitor_empty_dict_is_valid():
    assert _validate_file_paste(base_config({"attachment_monitor": {}})) is True


@pytest.mark.parametrize("key", ATTACHMENT_MONITOR_SPEC["numeric"])
def test_attachment_monitor_numeric_fields(key):
    for value in [0, 1, 1.5, -2, True, False]:
        cfg = base_config({"attachment_monitor": {key: value}})
        assert _validate_file_paste(cfg) is True, f"{key}={value!r}"
    for value in ["1.5", None, [], {}, object()]:
        cfg = base_config({"attachment_monitor": {key: value}})
        assert _validate_file_paste(cfg) is False, f"{key}={value!r}"


@pytest.mark.parametrize("key", ATTACHMENT_MONITOR_SPEC["bool"])
def test_attachment_monitor_bool_fields(key):
    for value in [True, False]:
        cfg = base_config({"attachment_monitor": {key: value}})
        assert _validate_file_paste(cfg) is True, f"{key}={value!r}"
    for value in [1, 0, "true", None, [], {}]:
        cfg = base_config({"attachment_monitor": {key: value}})
        assert _validate_file_paste(cfg) is False, f"{key}={value!r}"


@pytest.mark.parametrize("key", ATTACHMENT_MONITOR_SPEC["list_of_str"])
def test_attachment_monitor_list_of_str_fields(key):
    # 严格 list：tuple 虽然是序列，但被拒
    for value in [[], ["a"], ["a", "b"], ["a", ""]]:
        cfg = base_config({"attachment_monitor": {key: value}})
        assert _validate_file_paste(cfg) is True, f"{key}={value!r}"
    for value in ["a", None, 1, {}, [1], ["a", 2], [None], ("a", "b")]:
        cfg = base_config({"attachment_monitor": {key: value}})
        assert _validate_file_paste(cfg) is False, f"{key}={value!r}"


def test_attachment_monitor_unknown_keys_ignored():
    assert _validate_file_paste(base_config({"attachment_monitor": {"nope": object()}})) is True


# --------------------------------------------------------------------------
# 顶层分派
# --------------------------------------------------------------------------


@pytest.mark.parametrize("section", ["x", [], 1, None, True, 1.5, ""])
def test_file_paste_must_be_dict(section):
    assert _validate_file_paste(base_config(section)) is False


def test_validator_agrees_with_validate_site_config():
    """直接调用与走分派路径的结论必须一致。"""
    samples = [
        {},
        {"enabled": True},
        {"enabled": "yes"},
        {"threshold": 1.5},
        {"temp_file_type": "pdf"},
        {"temp_file_type": "zip"},
        {"state_probe": {"code": "x"}},
        {"state_probe": {"code": 1}},
        {"send_confirmation": {"max_retry_count": 2}},
        {"send_confirmation": {"max_retry_count": 2.5}},
        {"send_confirmation": {"retry_action": "key_press"}},
        {"send_confirmation": {"retry_action": "nope"}},
        {"attachment_monitor": {}},
    ]
    for file_paste in samples:
        cfg = base_config(file_paste)
        assert _validate_file_paste(cfg) is validate_site_config(cfg), repr(file_paste)
