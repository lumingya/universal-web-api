"""`_normalize_image_extraction` 的逐键表征测试。

重构前先锁定行为。它是 `_IMAGE_CONFIG_NORMALIZERS` 里的一个分组归一化器，
签名 `_normalize_image_extraction(config, result, default)`：就地修改 result，
只读取 config 与 default。19 个键各有一段 `if '<key>' in config:`。

真正的风险是**各段之间的细微不一致**——看着像同一模式，细节却不同，重构时最
容易顺手抹平。这些不一致目前都还成立，测试逐条钉住：

- `selector` 系列的空值回退目标不同：`selector` 回退字面量 `"img"`，
  `audio_selector` / `video_selector` 回退 `default[...]`。
- `final_target_strategy` / `latest_visual_column` / `canvas_export_mime` 走
  `str(x or "").strip().lower()`（先 `or ""` 再 strip），而 `mode` 只走
  `str(x).lower()`——**不做 `or ""` 也不 strip**。
  于是 `{"final_target_strategy": " CONTAINER "}` 合法，
  `{"mode": " ALL "}` 不合法，`{"mode": None}` 变成字符串 `"none"`。
- `max_size_mb` 用 `int()`（`"50"` 能过、`50.9` 被截断成 50），
  其余数值字段用 `float()`。
- 五个 bool 字段里，只有 `wait_for_load` 的默认值是 `True`，其余是 `False`。
- `container_selector` 是唯一"键存在就写、假值写 None"的字段。
"""

from __future__ import annotations

import copy

import pytest

from app.models.schemas import get_default_image_extraction_config
from app.services.config.engine_media import _normalize_image_extraction

MODALITIES_ALL_ON = {"image": {"enabled": True}, "audio": {"enabled": True}, "video": {"enabled": True}}
MODALITIES_IMAGE_ONLY = {"image": {"enabled": True}, "audio": {"enabled": False}, "video": {"enabled": False}}


def run(config, result_extra=None):
    """跑一遍归一化，返回 (result, default)。"""
    default = get_default_image_extraction_config()
    result = copy.deepcopy(default)
    if result_extra:
        result.update(copy.deepcopy(result_extra))
    _normalize_image_extraction(config, result, default)
    return result, default


# --------------------------------------------------------------------------
# 前置：未出现的键不动 result
# --------------------------------------------------------------------------


def test_empty_config_leaves_result_untouched():
    result, default = run({})
    assert result == default


def test_unknown_keys_are_ignored():
    result, _ = run({"totally_unknown": "x", "another": [1]})
    assert "totally_unknown" not in result
    assert "another" not in result


# --------------------------------------------------------------------------
# 1-3. selector / audio_selector / video_selector：假值不写、空串回退
# --------------------------------------------------------------------------


@pytest.mark.parametrize("falsy", [None, "", "   ", 0, False, [], {}])
def test_selector_falsy_keeps_default(falsy):
    result, default = run({"selector": falsy})
    assert result["selector"] == default["selector"]


def test_selector_strips_and_falls_back_to_img():
    """空白串被 strip 成空后回退到字面量 "img"。

    默认配置的 selector 恰好也是 "img"，所以这条无法区分"回退到字面量"与
    "保持默认"；真正能区分的是 default 被改掉的场景，见下一个用例。
    """
    result, _ = run({"selector": "   "})
    assert result["selector"] == "img"

    result, _ = run({"selector": "  .gallery img  "})
    assert result["selector"] == ".gallery img"

    result, _ = run({"selector": 42})
    assert result["selector"] == "42"


@pytest.mark.parametrize("key", ["audio_selector", "video_selector"])
@pytest.mark.parametrize("falsy", [None, "", "   ", 0, False])
def test_av_selector_falsy_keeps_default(key, falsy):
    result, default = run({key: falsy})
    assert result[key] == default[key]


@pytest.mark.parametrize("key", ["audio_selector", "video_selector"])
def test_av_selector_empty_after_strip_falls_back_to_default(key):
    """与 selector 不同：这两个回退到 default[key]，不是字面量。"""
    result, default = run({key: "   "})
    assert result[key] == default[key]

    result, _ = run({key: "  audio > source  "})
    assert result[key] == "audio > source"


# --------------------------------------------------------------------------
# 4. container_selector：键存在就写，假值写 None
# --------------------------------------------------------------------------


@pytest.mark.parametrize("falsy", [None, "", 0, False, []])
def test_container_selector_falsy_writes_none(falsy):
    result, _ = run({"container_selector": falsy})
    assert result["container_selector"] is None


def test_container_selector_whitespace_writes_empty_string():
    """`if val` 判的是真值：只有空白的字符串是真值，于是被 strip 成空串而非 None。"""
    result, _ = run({"container_selector": "   "})
    assert result["container_selector"] == ""


def test_container_selector_truthy_is_stripped():
    result, _ = run({"container_selector": "  .chat  "})
    assert result["container_selector"] == ".chat"


# --------------------------------------------------------------------------
# 5-6, 16. 三个枚举字段（走 str(x or "").strip().lower()）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,members",
    [
        ("final_target_strategy", ["container", "latest_reply", "latest_visual_reply"]),
        ("latest_visual_column", ["left", "right"]),
        ("canvas_export_mime", ["image/jpeg", "image/webp", "image/png"]),
    ],
)
def test_enum_fields_normalize_case_and_space(key, members):
    for m in members:
        for raw in [m, m.upper(), "  " + m + "  ", m.capitalize()]:
            result, _ = run({key: raw})
            assert result[key] == m, (key, repr(raw))


@pytest.mark.parametrize(
    "key", ["final_target_strategy", "latest_visual_column", "canvas_export_mime"],
)
def test_enum_fields_reject_unknown(key):
    """非法值不写入：键在默认配置里就保持默认值，不在就干脆不出现。"""
    for raw in ["nope", "", "   ", None, 1, [], {}, "container2"]:
        result, default = run({key: raw})
        if key in default:
            assert result[key] == default[key], (key, repr(raw))
        else:
            assert key not in result, (key, repr(raw))


def test_absent_from_defaults_keys_are_optional():
    """final_target_strategy / latest_visual_column 等不在默认配置里，是可选字段。"""
    default = get_default_image_extraction_config()
    for key in ["final_target_strategy", "latest_visual_column",
                "allow_container_fallback", "arena_image_generation",
                "direct_postprocess_modalities"]:
        assert key not in default, key
        result, _ = run({})
        assert key not in result, key


# --------------------------------------------------------------------------
# 7-9, 12, 14. bool 字段
# --------------------------------------------------------------------------


BOOL_FALSE_DEFAULT = [
    "allow_container_fallback",
    "force_postprocess",
    "arena_image_generation",
    "download_blobs",
]


@pytest.mark.parametrize("key", BOOL_FALSE_DEFAULT)
def test_bool_fields_default_false(key):
    """真值集合 {1,true,yes,y,on} → True；假值集合 {0,false,no,n,off} → False。"""
    for raw in ["true", "TRUE", "1", "yes", "y", "on", "  on  ", 1, 2, -1, 1.5, True]:
        result, _ = run({key: raw})
        assert result[key] is True, (key, repr(raw))
    for raw in ["false", "FALSE", "0", "no", "n", "off", 0, 0.0, False]:
        result, _ = run({key: raw})
        assert result[key] is False, (key, repr(raw))


@pytest.mark.parametrize("key", BOOL_FALSE_DEFAULT)
def test_bool_fields_unrecognized_fall_back_to_false(key):
    """认不出来的值兜底成 False——**不是**回落到 default[key]，也不是"非空即真"。

    `""` / `"abc"` / `None` 都得到 False；字符串 "2" 也得到 False，而整数 2
    按 `!= 0` 判成 True。
    """
    for raw in ["", "  ", "abc", "2", None, "maybe"]:
        result, _ = run({key: raw})
        assert result[key] is False, (key, repr(raw))


def test_download_blobs_unrecognized_overrides_a_true_default():
    """一个值得记下的坑：`download_blobs` 在默认配置里是 True，但归一化器传入的
    兜底值是 False。

    于是 `{"download_blobs": "maybe"}` 会把默认的 True 静默改成 False，而不是
    保持 True。同理 `{"download_blobs": ""}` 也是。
    """
    _, default = run({})
    assert default["download_blobs"] is True, "前提：默认配置里 download_blobs 为 True"

    result, _ = run({"download_blobs": "maybe"})
    assert result["download_blobs"] is False
    result, _ = run({"download_blobs": ""})
    assert result["download_blobs"] is False


def test_wait_for_load_defaults_true():
    """wait_for_load 是唯一默认为 True 的 bool 字段。

    所以"认不出来的值"和"显式假值"的结果在这里分道扬镳：
    `""` / `"abc"` / None 取默认 True，而 `"false"` / `"0"` / 0 取 False。
    """
    # 认不出的**字符串**与 None 取默认 True
    for raw in [None, "", "  ", "abc", "maybe", "2"]:
        result, _ = run({"wait_for_load": raw})
        assert result["wait_for_load"] is True, repr(raw)
    # 但其它非字符串类型走 bool()，不看默认值
    for raw in [[], {}, [0], object()]:
        result, _ = run({"wait_for_load": raw})
        assert result["wait_for_load"] is bool(raw), repr(raw)
    for raw in ["false", "0", "no", "off", 0, False]:
        result, _ = run({"wait_for_load": raw})
        assert result["wait_for_load"] is False, repr(raw)
    for raw in ["true", "1", "yes", "on", 1, True]:
        result, _ = run({"wait_for_load": raw})
        assert result["wait_for_load"] is True, repr(raw)


# --------------------------------------------------------------------------
# 10. direct_postprocess_modalities
# --------------------------------------------------------------------------


@pytest.mark.parametrize("container", [list, tuple, set])
def test_direct_modalities_accepts_any_sequence_container(container):
    result, _ = run(
        {"direct_postprocess_modalities": container(["image", "audio"])},
        result_extra={"modalities": MODALITIES_ALL_ON},
    )
    assert sorted(result["direct_postprocess_modalities"]) == ["audio", "image"]


def test_direct_modalities_normalizes_case_and_space():
    result, _ = run(
        {"direct_postprocess_modalities": ["  IMAGE ", "Audio", "VIDEO"]},
        result_extra={"modalities": MODALITIES_ALL_ON},
    )
    assert sorted(result["direct_postprocess_modalities"]) == ["audio", "image", "video"]


def test_direct_modalities_drops_unknown_members():
    result, _ = run(
        {"direct_postprocess_modalities": ["image", "nope", "", None, 42]},
        result_extra={"modalities": MODALITIES_ALL_ON},
    )
    assert result["direct_postprocess_modalities"] == ["image"]


def test_direct_modalities_drops_disabled_modalities():
    """未被启用的模态会被过滤掉。"""
    result, _ = run(
        {"direct_postprocess_modalities": ["image", "audio", "video"]},
        result_extra={"modalities": MODALITIES_IMAGE_ONLY},
    )
    assert result["direct_postprocess_modalities"] == ["image"]


def test_direct_modalities_dedupes_preserving_first_order():
    result, _ = run(
        {"direct_postprocess_modalities": ["video", "image", "VIDEO", " image "]},
        result_extra={"modalities": MODALITIES_ALL_ON},
    )
    assert result["direct_postprocess_modalities"] == ["video", "image"]


def test_direct_modalities_empty_result_keeps_default():
    """过滤后为空时不写入 result。"""
    for raw in [[], (), set(), ["nope"], None, "image", 42, {}]:
        result, _ = run(
            {"direct_postprocess_modalities": raw},
            result_extra={"modalities": MODALITIES_ALL_ON},
        )
        # 该键不在默认配置里，所以"不做处理"= 键不出现
        assert "direct_postprocess_modalities" not in result, repr(raw)


# --------------------------------------------------------------------------
# 11, 13, 15, 17. 数值字段（区间钳制 + 转换失败保持默认）
# --------------------------------------------------------------------------

# (键, 下界, 上界, 转换器)
NUMERIC_FIELDS = [
    ("debounce_seconds", 0, 30, float),
    ("load_timeout_seconds", 1, 60, float),
    ("max_size_mb", 1, 100, int),
    ("canvas_export_quality", 0.1, 1.0, float),
]


@pytest.mark.parametrize("key,low,high,cast", NUMERIC_FIELDS)
def test_numeric_fields_clamp(key, low, high, cast):
    result, _ = run({key: low - 1000})
    assert result[key] == low, f"{key} 下界"
    result, _ = run({key: high + 1000})
    assert result[key] == high, f"{key} 上界"
    # 区间内取一个整数，避开 int 字段的截断歧义
    mid = int((low + high) // 2) or low
    result, _ = run({key: mid})
    assert result[key] == cast(mid), f"{key} 区间内"


@pytest.mark.parametrize("key,low,high,cast", NUMERIC_FIELDS)
def test_numeric_fields_accept_numeric_strings(key, low, high, cast):
    """int()/float() 会解析字符串，所以 "5" 是合法输入。"""
    raw = 1 if high <= 2 else 5  # canvas_export_quality 上界 1.0，取 1
    result, _ = run({key: str(raw)})
    assert result[key] == cast(raw), (key, raw)


@pytest.mark.parametrize("key,low,high,_cast", NUMERIC_FIELDS)
@pytest.mark.parametrize("bad", ["abc", None, [], {}, "1.2.3"])
def test_numeric_fields_keep_default_on_conversion_failure(key, low, high, _cast, bad):
    result, default = run({key: bad})
    assert result[key] == default[key], (key, repr(bad))


def test_max_size_mb_uses_int_truncation():
    """max_size_mb 走 int()：50.9 被截断成 50（不是四舍五入）。"""
    result, _ = run({"max_size_mb": 50.9})
    assert result["max_size_mb"] == 50
    assert isinstance(result["max_size_mb"], int)

    result, _ = run({"max_size_mb": "50"})
    assert result["max_size_mb"] == 50


def test_debounce_seconds_uses_float():
    """其余数值字段走 float()，不做截断。"""
    result, _ = run({"debounce_seconds": 2.7})
    assert result["debounce_seconds"] == 2.7
    result, _ = run({"canvas_export_quality": 0.55})
    assert result["canvas_export_quality"] == 0.55


def test_debounce_seconds_zero_is_allowed():
    """debounce_seconds 的下界是 0（不是 0.1）。"""
    result, _ = run({"debounce_seconds": 0})
    assert result["debounce_seconds"] == 0


# --------------------------------------------------------------------------
# 18. src_allow_patterns
# --------------------------------------------------------------------------


def test_src_allow_patterns_accepts_list():
    result, _ = run({"src_allow_patterns": ["a", "  b  ", "a", "", "   ", "c"]})
    assert result["src_allow_patterns"] == ["a", "b", "c"]


def test_src_allow_patterns_splits_string_on_newlines_and_semicolons():
    for raw, expected in [
        ("a\nb", ["a", "b"]),
        ("a\r\nb", ["a", "b"]),
        ("a;b", ["a", "b"]),
        ("a\nb;c", ["a", "b", "c"]),
        ("  a ; b  ", ["a", "b"]),
        ("", []),
    ]:
        result, _ = run({"src_allow_patterns": raw})
        assert result["src_allow_patterns"] == expected, repr(raw)


@pytest.mark.parametrize("container", [list, tuple, set])
def test_src_allow_patterns_accepts_any_container(container):
    result, _ = run({"src_allow_patterns": container(["a", "b"])})
    assert sorted(result["src_allow_patterns"]) == ["a", "b"]


def test_src_allow_patterns_empty_result_overwrites_default():
    """空结果**会**被写入（覆盖默认值），而不是跳过。

    默认 src_allow_patterns 本身就是 []，所以要用一个哨兵值把 result 预置成
    非空，才能看出 `"a;b"` 之外的空输入到底有没有写。
    """
    default = get_default_image_extraction_config()
    assert default["src_allow_patterns"] == [], "前提：默认为空列表"

    for raw in ["", "   ", ";", "\n", ["", "  "]]:
        result = copy.deepcopy(default)
        result["src_allow_patterns"] = ["sentinel"]
        _normalize_image_extraction({"src_allow_patterns": raw}, result, default)
        assert result["src_allow_patterns"] == [], repr(raw)


def test_src_allow_patterns_dedupes_after_trim():
    result, _ = run({"src_allow_patterns": [" x ", "x", "y"]})
    assert result["src_allow_patterns"] == ["x", "y"]


@pytest.mark.parametrize("raw", [None, 42, {}, object()])
def test_src_allow_patterns_non_sequence_keeps_default(raw):
    result, default = run({"src_allow_patterns": raw})
    assert result["src_allow_patterns"] == default["src_allow_patterns"]


# --------------------------------------------------------------------------
# 19. mode：唯一一个不做 strip、不做 `or ""` 的枚举字段
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [("all", "all"), ("first", "first"), ("last", "last"), ("ALL", "all"), ("Last", "last")])
def test_mode_accepts_members_case_insensitively(raw, expected):
    result, _ = run({"mode": raw})
    assert result["mode"] == expected


def test_mode_rejects_padded_non_default_member():
    """专门挑一个**不等于默认值**的成员来验证"不 strip"。

    默认 mode 就是 "all"，所以用 `" all "` 验证不出问题（strip 后仍是 "all"，
    与默认相同）。这里用 `" first "` / `" last "`：strip 后会变成合法值，
    而原实现会判为非法。
    """
    _, default = run({})
    assert default["mode"] == "all", "前提：默认 mode 为 all"

    for raw, would_pass_if_stripped in [(" first ", "first"), (" last ", "last"), ("FIRST ", "first")]:
        result, _ = run({"mode": raw})
        assert result["mode"] == default["mode"], repr(raw)
        assert result["mode"] != would_pass_if_stripped, repr(raw)


@pytest.mark.parametrize("raw", [" all ", "all ", " all", "nope", "", None, 1, [], {}])
def test_mode_is_not_stripped_and_rejects_unknown(raw):
    """与 final_target_strategy 等字段不同：mode 既不 strip 也不做 `or ""`。

    所以 `" all "`（带空格）不合法，`None` 会变成字符串 `"none"` 后同样不合法。
    """
    result, default = run({"mode": raw})
    assert result["mode"] == default["mode"], repr(raw)


# --------------------------------------------------------------------------
# 组合：默认值本身必须是自洽的
# --------------------------------------------------------------------------


def test_defaults_survive_a_full_round_trip():
    """把默认值原样喂回去，结果应该与默认值一致（幂等）。"""
    default = get_default_image_extraction_config()
    result = copy.deepcopy(default)
    _normalize_image_extraction(copy.deepcopy(default), result, default)
    assert result == default
