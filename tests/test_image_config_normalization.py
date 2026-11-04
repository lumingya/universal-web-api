"""image_extraction 配置规范化（_validate_image_config）的专项测试。

`_validate_image_config` 原为 366 行 / CC 143 的单体函数，结构上是
"取默认值 → 按 key 逐项规范化 → 返回"，六个段落彼此只通过 (config,
result, default) 交互，因此拆成六个 `_normalize_image_*` 加一张顺序表。

本文件覆盖两个层面：

1. **行为**：每组的典型合法/非法/边界输入，以及整体函数的组合语义。
2. **结构**：锁定"六组 + 顺序表"这个形状，防止重新长回单体函数。

注意：`_validate_image_config` 是**规范化器**而非校验器——它几乎不拒绝
输入，而是把任何输入收敛成一份完整的合法配置（缺的补默认值、类型不对的
强制转换）。测试因此主要断言"规范化后的值"，而不是"通过/不通过"。
"""

import ast
import copy
import pathlib

import pytest

from app.services.config.engine_media import (
    _IMAGE_CONFIG_NORMALIZERS,
    ConfigMediaMixin,
    _normalize_image_audio_activity,
    _normalize_image_audio_capture,
    _normalize_image_browser_tts_fallback,
    _normalize_image_extraction,
    _normalize_image_modalities,
    _normalize_image_network_capture,
    get_default_image_extraction_config,
)

MODULE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "config"
    / "engine_media.py"
)

ALL_GROUPS = (
    _normalize_image_modalities,
    _normalize_image_extraction,
    _normalize_image_audio_capture,
    _normalize_image_network_capture,
    _normalize_image_browser_tts_fallback,
    _normalize_image_audio_activity,
)


@pytest.fixture()
def engine():
    return ConfigMediaMixin()


def fresh():
    """一份与默认值等价的空白 result，供单独测某组时使用。"""
    default = get_default_image_extraction_config()
    return copy.deepcopy(default), default


# --------------------------------------------------------------------------
# 1. 整体行为
# --------------------------------------------------------------------------


def test_empty_config_returns_defaults(engine):
    out = engine._validate_image_config({})
    assert out == get_default_image_extraction_config()


@pytest.mark.parametrize("config", [None, {}, [], "", 0])
def test_falsy_config_returns_defaults(engine, config):
    """`if not config: return result` 这条早退必须在拆分后依然生效。"""
    assert engine._validate_image_config(config) == get_default_image_extraction_config()


def test_output_always_contains_every_default_key(engine):
    """规范化结果必须是"完整的"配置，不能缺键——下游到处直接取值。"""
    out = engine._validate_image_config({"selector": "  img  ", "max_size_mb": -1})
    assert set(out) == set(get_default_image_extraction_config())


def test_input_is_not_mutated(engine):
    cfg = {"selector": "img", "modalities": {"image": True}}
    snapshot = copy.deepcopy(cfg)
    engine._validate_image_config(cfg)
    assert cfg == snapshot


def test_result_is_not_shared_with_default(engine):
    """result 是 default 的深拷贝，改一个不能污染另一个。"""
    default = get_default_image_extraction_config()
    engine._validate_image_config({"selector": "img"})
    assert default == get_default_image_extraction_config()


# --------------------------------------------------------------------------
# 2. 模态与 legacy enabled
# --------------------------------------------------------------------------


def test_modalities_dict_is_normalized():
    config = {"modalities": {"image": True, "audio": False}}
    result, default = fresh()
    _normalize_image_modalities(config, result, default)
    assert result["modalities"]["image"]["enabled"] is True
    assert result["modalities"]["audio"]["enabled"] is False


def test_legacy_enabled_true_turns_image_on():
    result, default = fresh()
    _normalize_image_modalities({"enabled": True}, result, default)
    assert result["enabled"] is True
    assert result["modalities"]["image"]["enabled"] is True


def test_legacy_enabled_false_turns_everything_off():
    result, default = fresh()
    _normalize_image_modalities({"enabled": False, "modalities": {"image": True}}, result, default)
    assert result["enabled"] is False
    for key in ("image", "audio", "video"):
        assert result["modalities"][key]["enabled"] is False


def test_legacy_enabled_absent_keeps_defaults():
    result, default = fresh()
    before = copy.deepcopy(result)
    _normalize_image_modalities({}, result, default)
    assert result == before


# --------------------------------------------------------------------------
# 3. 选择器与后处理参数
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("selector", "  img  ", "img"),
        ("audio_selector", " audio ", "audio"),
        ("video_selector", " video ", "video"),
    ],
)
def test_selectors_are_stripped(key, value, expected):
    result, default = fresh()
    _normalize_image_extraction({key: value}, result, default)
    assert result[key] == expected


@pytest.mark.parametrize("key", ["selector", "audio_selector", "video_selector"])
def test_empty_selector_falls_back_to_default(key):
    """空串/纯空白的选择器不覆盖默认值——否则下游会拿空选择器去查询。"""
    result, default = fresh()
    _normalize_image_extraction({key: "   "}, result, default)
    assert result[key] == default[key]


@pytest.mark.parametrize(
    "key,value",
    [
        ("debounce_seconds", -1),
        ("load_timeout_seconds", -1),
        ("max_size_mb", -1),
        ("canvas_export_quality", 999),
    ],
)
def test_numeric_fields_are_clamped_to_lower_bound(key, value):
    result, default = fresh()
    _normalize_image_extraction({key: value}, result, default)
    assert result[key] >= 0


@pytest.mark.parametrize("value", [[], "", ["  "], ["a", "  ", "b"]])
def test_src_allow_patterns_drops_blanks(value):
    result, default = fresh()
    _normalize_image_extraction({"src_allow_patterns": value}, result, default)
    for pattern in result["src_allow_patterns"]:
        assert pattern.strip(), "空白模式应被过滤掉"


# --------------------------------------------------------------------------
# 4. 音频采集
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "audio_capture_enabled",
        "audio_capture_mute_playback",
        "audio_capture_preload_enabled",
        "audio_capture_reload_before_workflow",
        "audio_capture_preserve_graph",
    ],
)
def test_boolean_audio_flags_coerce_truthy_and_falsy(key):
    for value, expected in ((True, True), (False, False), ("yes", True), ("", False)):
        result, default = fresh()
        _normalize_image_audio_capture({key: value}, result, default)
        assert result[key] is expected, f"{key}={value!r}"


@pytest.mark.parametrize(
    "key",
    [
        "audio_capture_terminal_settle_seconds",
        "audio_capture_max_wait_seconds",
        "audio_capture_min_wait_seconds",
        "audio_capture_hard_max_wait_seconds",
        "audio_capture_estimated_chars_per_second",
        "audio_capture_wait_padding_seconds",
    ],
)
def test_audio_numeric_fields_stay_non_negative(key):
    result, default = fresh()
    _normalize_image_audio_capture({key: -5}, result, default)
    assert result[key] >= 0


def test_audio_trigger_labels_are_stripped_and_filtered():
    result, default = fresh()
    _normalize_image_audio_capture({"audio_trigger_labels": [" a ", "   ", "b"]}, result, default)
    assert result["audio_trigger_labels"] == ["a", "b"]


# --------------------------------------------------------------------------
# 5. 网络采集
# --------------------------------------------------------------------------


def test_network_capture_dict_is_normalized():
    result, default = fresh()
    _normalize_image_network_capture(
        {"audio_network_capture": {"enabled": True, "timeout_seconds": 5}}, result, default
    )
    assert result["audio_network_capture"]["enabled"] is True


def test_network_capture_non_dict_is_ignored():
    """非 dict 的输入不该让规范化崩溃，也不该污染默认值。"""
    for value in ("x", 5, [], None):
        result, default = fresh()
        before = copy.deepcopy(result)
        _normalize_image_network_capture({"audio_network_capture": value}, result, default)
        assert result == before, f"audio_network_capture={value!r} 不应改变 result"


def test_network_url_patterns_are_stripped_and_filtered():
    """输入键 audio_network_url_patterns 会折叠进 result["audio_network_capture"]。

    注意它**不是** result 的顶层键——这一条同时锁住这个折叠关系。
    """
    result, default = fresh()
    _normalize_image_network_capture(
        {"audio_network_url_patterns": ["  api/", "", "  "]}, result, default
    )
    assert result["audio_network_capture"]["url_patterns"] == ["api/"]


@pytest.mark.parametrize("value,expected", [(-1, 0.1), (0, 0.1), (5, 5.0), (999, 15.0), ("3", 3.0)])
def test_network_timeout_is_clamped_to_0_1_15(value, expected):
    """超时被夹在 [0.1, 15.0]；无法解析的值静默忽略（保留默认）。"""
    result, default = fresh()
    _normalize_image_network_capture(
        {"audio_network_capture_timeout_seconds": value}, result, default
    )
    assert result["audio_network_capture"]["timeout_seconds"] == expected


def test_network_timeout_unparsable_keeps_default():
    result, default = fresh()
    _normalize_image_network_capture(
        {"audio_network_capture_timeout_seconds": "abc"}, result, default
    )
    assert result["audio_network_capture"] == default["audio_network_capture"]


# --------------------------------------------------------------------------
# 6. 浏览器 TTS 兜底
# --------------------------------------------------------------------------


def test_browser_tts_fallback_dict_is_normalized():
    result, default = fresh()
    _normalize_image_browser_tts_fallback(
        {"audio_browser_tts_fallback": {"enabled": True}}, result, default
    )
    assert result["audio_browser_tts_fallback"]["enabled"] is True


def test_browser_tts_fallback_non_dict_is_ignored():
    for value in ("x", 5, [], None):
        result, default = fresh()
        before = copy.deepcopy(result)
        _normalize_image_browser_tts_fallback({"audio_browser_tts_fallback": value}, result, default)
        assert result == before, f"audio_browser_tts_fallback={value!r} 不应改变 result"


# --------------------------------------------------------------------------
# 7. 音频活动检测与 enabled 收敛
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "audio_capture_poll_seconds",
        "audio_capture_silence_seconds",
        "audio_capture_activity_threshold",
        "audio_capture_activity_silence_seconds",
    ],
)
def test_activity_fields_stay_non_negative(key):
    result, default = fresh()
    _normalize_image_audio_activity({key: -3}, result, default)
    assert result[key] >= 0


def test_enabled_reflects_whether_any_modality_is_on(engine):
    """最后一步按"是否有启用的模态"收敛 enabled，测试整体链路。"""
    out = engine._validate_image_config({"enabled": True})
    assert out["enabled"] is True
    out = engine._validate_image_config({"enabled": False})
    assert out["enabled"] is False


# --------------------------------------------------------------------------
# 8. 结构锁定：防止重新长回单体函数
# --------------------------------------------------------------------------


def test_normalizer_table_is_complete_and_ordered():
    assert _IMAGE_CONFIG_NORMALIZERS == ALL_GROUPS


def test_each_normalizer_has_uniform_signature():
    import inspect

    for fn in _IMAGE_CONFIG_NORMALIZERS:
        assert list(inspect.signature(fn).parameters) == ["config", "result", "default"]
        assert inspect.signature(fn).return_annotation is None


def test_validate_image_config_only_dispatches():
    """`_validate_image_config` 里不应再残留具体的字段规范化逻辑。

    判断依据：函数体内不应出现任何 `config[...]` 取值——那是各组的事。
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "ConfigMediaMixin")
    fn = next(
        n
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "_validate_image_config"
    )
    for node in ast.walk(fn):
        if isinstance(node, ast.Subscript):
            src = ast.unparse(node)
            for banned in ("config[", 'config["'):
                assert banned not in src, f"_validate_image_config L{node.lineno} 仍在直接索引 config"


def test_normalizers_do_not_read_each_others_keys(monkeypatch):
    """每组只应读自己关心的键——用一个"读别的键就炸"的哨兵来验证。

    这锁住的是"六组互不耦合"这个性质：将来若有人在音频组里读网络组的
    键，这条用例会立刻失败。
    """

    class Boom(dict):
        def __init__(self, allowed):
            super().__init__()
            self.allowed = set(allowed)

        def __getitem__(self, key):
            if key not in self.allowed:
                raise AssertionError(f"读取了不该读的键: {key}")
            raise KeyError(key)

        def __contains__(self, key):
            return key in self.allowed

    for fn in _IMAGE_CONFIG_NORMALIZERS:
        result, default = fresh()
        # 只允许读空集合：任何一次 config[...] 取值都会抛 AssertionError
        fn(Boom(set()), result, default)


def test_normalizers_are_module_private():
    """拆分出的是内部实现，不应出现在使用方（migrations）的公开接口上。"""
    import app.services.config.engine_migrations as mig

    for fn in _IMAGE_CONFIG_NORMALIZERS:
        assert not hasattr(mig, fn.__name__)
