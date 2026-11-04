"""`_normalize_models` 的表征测试。

Arena 直连模型目录的规范化入口（CC 56 / 98 行），98 行里 89 行是那个大循环。
重构前它只有 1 个测试，且只覆盖了 modality 判定——别名推导、去重、四个名字字段
的回退链全部没有用例。

它是纯函数（只额外读一次 `_load_alias_overrides()` 的配置），所以可以直接
喂列表、断言输出。测试按循环体内的实际分段组织：

1. 前置守卫与跳过条件（非列表 / 非 dict / 缺 id 或缺 name）
2. 去重：按 id 与 name 各自**小写**去重
3. 四个名字字段的回退链（name / public_name / display_name / search_name）
4. 别名推导：基础四项 + 覆盖配置 + 三种派生（括号前缀 / -high / -preview）
5. modality：规范化映射（webdev→code 等）+ 关键词兜底 + 默认 text
6. provider / organization 的回退
"""

from __future__ import annotations

import pytest

from app.services.arena_direct_models import _normalize_models

# 已知的生图关键词（用于验证兜底判定，取自实现里的关键词表）
IMAGE_KEYWORDS = [
    "gpt-image", "mona-lisa", "luna-lisa", "lina-alpha", "lina-f-alpha",
    "silver_halide", "flux", "seedream", "seededit", "imagen", "z-image",
    "midjourney", "dall-e", "recraft", "krea", "grok-imagine",
]


@pytest.fixture
def no_overrides(monkeypatch):
    """把别名覆盖配置清空，让测试只依赖被测逻辑本身。"""
    monkeypatch.setattr("app.services.arena_direct_models._load_alias_overrides", lambda: {})


@pytest.fixture
def overrides(monkeypatch):
    """安装一份可控的别名覆盖配置。"""

    def _install(mapping):
        monkeypatch.setattr("app.services.arena_direct_models._load_alias_overrides", lambda: mapping)

    return _install


def one(**kw):
    """构造单元素输入并取回唯一结果。

    模板默认带 arena_model_id 与 name；传 `name=None` 之类可以把该键**删掉**，
    用来测"只给 display_name""缺 id"这类场景。
    """
    raw = {"arena_model_id": "m1", "name": "model-one"}
    for key, value in kw.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    (result,) = _normalize_models([raw])
    return result


# --------------------------------------------------------------------------
# 1. 前置守卫与跳过条件
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "x", 1, {}, {"a": 1}, (), object(), 1.5, True])
def test_non_list_returns_empty(raw):
    assert _normalize_models(raw) == []


def test_empty_list_returns_empty():
    assert _normalize_models([]) == []


@pytest.mark.parametrize("entry", [None, "x", 1, [], ["a"], (), object(), 1.5])
def test_non_dict_entries_are_skipped(entry):
    assert _normalize_models([entry]) == []


@pytest.mark.parametrize(
    "entry",
    [
        {"arena_model_id": "m1"},              # 缺 name
        {"name": "n1"},                         # 缺 id
        {"arena_model_id": "", "name": "n1"},   # id 空串
        {"arena_model_id": "m1", "name": ""},   # name 空串
        {"arena_model_id": "   ", "name": "n1"},  # strip 后为空
        {"arena_model_id": None, "name": None},
        {},
    ],
)
def test_entries_missing_id_or_name_are_skipped(entry):
    assert _normalize_models([entry]) == []


def test_id_and_name_are_stripped():
    got = one(arena_model_id="  m1  ", name="  Model One  ")
    assert got["arena_model_id"] == "m1"
    assert got["name"] == "Model One"


def test_non_string_id_and_name_are_stringified():
    got = one(arena_model_id=42, name=7)
    assert got["arena_model_id"] == "42"
    assert got["name"] == "7"


# --------------------------------------------------------------------------
# 2. 去重：按 id 与 name 各自小写去重
# --------------------------------------------------------------------------


def test_duplicate_id_is_dropped():
    got = _normalize_models([
        {"arena_model_id": "m1", "name": "first"},
        {"arena_model_id": "M1", "name": "second"},
    ])
    assert [m["name"] for m in got] == ["first"]


def test_duplicate_name_is_dropped_even_with_different_id():
    got = _normalize_models([
        {"arena_model_id": "m1", "name": "same"},
        {"arena_model_id": "m2", "name": "SAME"},
    ])
    assert [m["arena_model_id"] for m in got] == ["m1"]


def test_distinct_entries_are_all_kept_in_order():
    got = _normalize_models([
        {"arena_model_id": "a", "name": "A"},
        {"arena_model_id": "b", "name": "B"},
        {"arena_model_id": "c", "name": "C"},
    ])
    assert [m["arena_model_id"] for m in got] == ["a", "b", "c"]


def test_dedupe_key_is_the_resolved_name_including_fallbacks():
    """去重键是**回退后**的 name，不是 name 字段本身。

    第一条没给 name，但 display_name 会回退成 name，所以它不会被跳过，
    并且与第三条按小写同名而把第三条挤掉。
    """
    got = _normalize_models([
        {"arena_model_id": "m1", "display_name": "shared"},   # name -> "shared"
        {"arena_model_id": "m2", "display_name": "shared", "name": "other"},
        {"arena_model_id": "m3", "name": "SHARED"},           # 与 m1 同名，被去重
    ])
    assert [m["arena_model_id"] for m in got] == ["m1", "m2"]
    assert got[0]["name"] == "shared"


# --------------------------------------------------------------------------
# 3. 四个名字字段的回退链
# --------------------------------------------------------------------------


def test_name_prefers_name_then_display_name_then_public_name():
    assert one(name="n", display_name="d", public_name="p")["name"] == "n"
    assert one(name=None, display_name="d", public_name="p")["name"] == "d"
    assert one(name=None, public_name="p")["name"] == "p"


def test_public_name_prefers_public_name_then_display_name_then_name():
    assert one(name="n", display_name="d", public_name="p")["public_name"] == "p"
    assert one(name="n", display_name="d", public_name=None)["public_name"] == "d"
    assert one(name="n", display_name=None, public_name=None)["public_name"] == "n"


def test_display_name_prefers_display_name_then_public_name_then_name():
    assert one(name="n", display_name="d", public_name="p")["display_name"] == "d"
    assert one(name="n", display_name=None, public_name="p")["display_name"] == "p"
    assert one(name="n", display_name=None, public_name=None)["display_name"] == "n"


def test_search_name_defaults_to_display_name(no_overrides):
    got = one(name="n", display_name="d", public_name="p")
    assert got["search_name"] == "d"
    got = one(name="n", display_name=None, public_name="p")
    assert got["search_name"] == "p"
    got = one(name="n", display_name=None, public_name=None)
    assert got["search_name"] == "n"


def test_output_has_exactly_the_expected_keys(no_overrides):
    assert set(one()) == {
        "arena_model_id", "name", "public_name", "display_name", "search_name",
        "aliases", "provider", "organization", "modality",
    }


# --------------------------------------------------------------------------
# 4. 别名推导
# --------------------------------------------------------------------------


def test_aliases_start_from_the_four_names_deduped(no_overrides):
    got = one(name="n", public_name="p", display_name="d")
    # 无覆盖时 search_name == display_name，故去重后为 n, p, d
    assert got["aliases"] == ["n", "p", "d"]


def test_aliases_dedupe_is_case_insensitive(no_overrides):
    got = one(name="Model", public_name="model", display_name="MODEL")
    assert got["aliases"] == ["Model"]


def test_aliases_drop_empty_entries(no_overrides):
    got = one(name="n", public_name="", display_name=None)
    assert got["aliases"] == ["n"]


def test_aliases_include_override_aliases(overrides):
    overrides({"n": {"aliases": ["n-alt", "N-ALT", "n-extra"]}})
    got = one(name="n")
    assert got["aliases"] == ["n", "n-alt", "n-extra"]


def test_override_aliases_ignore_non_list(overrides):
    overrides({"n": {"aliases": "not-a-list"}})
    got = one(name="n")
    assert got["aliases"] == ["n"]


def test_override_search_name_wins(overrides):
    overrides({"n": {"search_name": "override-search"}})
    got = one(name="n", display_name="d", public_name="p")
    assert got["search_name"] == "override-search"
    assert "override-search" in got["aliases"]


@pytest.mark.parametrize(
    "candidate,expected_base",
    [
        ("Model (Preview)", "Model"),          # 括号前缀
        ("model-high", "model"),               # -high 后缀
        ("model-preview", "model"),            # -preview 后缀
    ],
)
def test_derived_aliases_from_display_name(no_overrides, candidate, expected_base):
    got = one(name="other", display_name=candidate)
    assert expected_base in got["aliases"]


def test_derived_aliases_cover_all_four_candidate_names(no_overrides):
    """派生别名对 display_name / public_name / search_name / name 四个都做。"""
    got = one(name="alpha-high", public_name="beta-preview", display_name="gamma (x)")
    aliases = {a.casefold() for a in got["aliases"]}
    for base in ["alpha", "beta", "gamma"]:
        assert base in aliases, base


def test_derived_aliases_do_not_duplicate_existing(no_overrides):
    """基础别名按 (name, public_name, display_name, search_name) 的顺序收集，
    所以这里 public_name 先于 display_name 出现。"""
    got = one(name="model", display_name="model-high", public_name="model-preview")
    assert got["aliases"].count("model") == 1
    assert got["aliases"] == ["model", "model-preview", "model-high"]


def test_derived_alias_of_empty_base_is_dropped(no_overrides):
    """`-high` 单独成词时去掉后缀后为空，不应产生空别名。"""
    got = one(name="-high")
    assert "" not in got["aliases"]
    assert got["aliases"] == ["-high"]


def test_paren_derivation_uses_text_before_first_paren(no_overrides):
    got = one(name="Model (v2) (beta)")
    assert "Model" in got["aliases"]


# --------------------------------------------------------------------------
# 5. modality
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("text", "text"), ("image", "image"), ("code", "code"), ("audio", "audio"),
        ("webdev", "code"), ("web", "code"), ("chat", "text"),
        ("TEXT", "text"), (" Image ", "image"), (None, "text"), ("", "text"),
        ("video", "video"),
    ],
)
def test_modality_canonicalization(raw, expected):
    assert one(modality=raw)["modality"] == expected


@pytest.mark.parametrize("keyword", IMAGE_KEYWORDS)
def test_image_keyword_fallback(no_overrides, keyword):
    """没有显式 modality 时，按名字里的关键词判定为 image。"""
    assert one(name=f"my-{keyword}-model")["modality"] == "image"


def test_explicit_modality_beats_keywords():
    assert one(name="dall-e-3", modality="text")["modality"] == "text"


def test_keywords_match_against_all_name_fields(no_overrides):
    """关键词检查的字符串是 `{name} {display_name} {public_name} {arena_model_id}`，
    四个字段任一命中即可。"""
    for field in ["name", "display_name", "public_name", "arena_model_id"]:
        got = one(**{field: "x-flux-y"})
        assert got["modality"] == "image", field


def test_unknown_model_defaults_to_text(no_overrides):
    assert one()["modality"] == "text"


def test_keyword_match_is_case_insensitive(no_overrides):
    assert one(name="DALL-E-3")["modality"] == "image"
    assert one(name="Recraft-V4")["modality"] == "image"


# --------------------------------------------------------------------------
# 6. provider / organization
# --------------------------------------------------------------------------


def test_provider_defaults_to_empty_string():
    assert one()["provider"] == ""
    assert one(provider="  OpenAI  ")["provider"] == "OpenAI"


def test_organization_prefers_organization_then_provider_then_default():
    assert one(organization="Org", provider="Prov")["organization"] == "Org"
    assert one(provider="Prov")["organization"] == "Prov"
    assert one()["organization"] == "arena.ai"


def test_organization_is_stripped():
    assert one(organization="  Org  ")["organization"] == "Org"


# --------------------------------------------------------------------------
# 组合行为
# --------------------------------------------------------------------------


def test_mixed_input_keeps_only_valid_unique_entries(no_overrides):
    got = _normalize_models([
        "not-a-dict",
        {"arena_model_id": "a", "name": "A"},
        {"arena_model_id": "b"},          # 缺 name
        {"arena_model_id": "A", "name": "a2"},  # id 重复
        {"arena_model_id": "c", "name": "A"},   # name 重复
        {"arena_model_id": "d", "name": "D", "modality": "image"},
    ])
    assert [(m["arena_model_id"], m["name"]) for m in got] == [("a", "A"), ("d", "D")]


def test_result_is_a_fresh_dict_not_the_input_object():
    raw = {"arena_model_id": "a", "name": "A"}
    (got,) = _normalize_models([raw])
    assert got is not raw
    got["name"] = "mutated"
    assert raw["name"] == "A"
