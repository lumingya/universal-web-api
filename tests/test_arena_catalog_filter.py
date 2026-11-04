"""filter_arena_catalog_models 的专项测试。

该函数（122 行 / CC 63）是 Arena 模型目录的明池/暗池过滤入口，此前只有
4 项间接用例（散落在 test_arena_direct_models.py 里）。它的循环体是一条
**过滤流水线**，每一环都是一个可独立验证的谓词：

    1. video 关键词预过滤（含 kling / inkling 特例）
    2. modality 匹配（code / search / video / image / text / 其它）
    3. 构造可搜索字符串（含 aliases）
    4. 暗池规则（开关 → 黑名单 → 白名单/日期）
    5. 明池规则（include / exclude 关键词）
    6. 按 arena_model_id 去重

本文件的用例在拆分前就已全部通过，用于证明后续拆分是纯结构变化。

隔离说明：`load_arena_offline_cache_models()` 会读磁盘缓存，测试中统一
monkeypatch 为空列表，避免用例受本地缓存影响。
"""

import pytest

from app.services import arena_model_catalog as catalog_mod
from app.services.arena_model_catalog import filter_arena_catalog_models


@pytest.fixture(autouse=True)
def no_offline_cache(monkeypatch):
    """切断磁盘缓存，让候选集完全由入参决定。"""
    monkeypatch.setattr(catalog_mod, "load_arena_offline_cache_models", lambda: [])


def model(mid="m1", name="model-one", modality="text", **extra):
    data = {
        "arena_model_id": mid,
        "name": name,
        "public_name": name,
        "display_name": name,
        "modality": modality,
    }
    data.update(extra)
    return data


def cfg(**kw):
    base = {"enabled": True, "modality": "text"}
    base.update(kw)
    return base


def ids(result):
    return [str(m.get("arena_model_id")) for m in result]


# --------------------------------------------------------------------------
# 1. 候选集构造
# --------------------------------------------------------------------------


def test_non_dict_entries_are_skipped():
    out = filter_arena_catalog_models(["not-a-dict", 42, None, model("m1")], cfg())
    assert ids(out) == ["m1"]


def test_entries_without_id_are_skipped():
    out = filter_arena_catalog_models([{"name": "匿名模型"}, model("m1")], cfg())
    assert ids(out) == ["m1"]


def test_duplicate_ids_collapse_to_first():
    first = model("same", name="先到")
    second = model("same", name="后到")
    out = filter_arena_catalog_models([first, second], cfg())
    assert len(out) == 1
    assert out[0]["name"] == "先到"


def test_empty_input_returns_empty_list():
    assert filter_arena_catalog_models([], cfg()) == []
    assert filter_arena_catalog_models(None, cfg()) == []


# --------------------------------------------------------------------------
# 2. modality 过滤
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [("webdev", "code"), ("web", "code"), ("chat", "text")])
def test_modality_aliases_are_canonicalized(value, expected):
    """Arena 页面术语会被归一化到目录术语。"""
    out = filter_arena_catalog_models(
        [model("m1", modality=value), model("m2", modality="other")], cfg(modality=expected)
    )
    assert ids(out) == ["m1"]


def test_video_target_requires_exact_video_modality():
    out = filter_arena_catalog_models(
        [model("v1", modality="video"), model("t1", modality="text")], cfg(modality="video")
    )
    assert ids(out) == ["v1"]


def test_image_target_accepts_keyword_when_modality_missing():
    """modality 为空时，靠名称里的 image 关键词判定。"""
    out = filter_arena_catalog_models(
        [
            model("i1", name="dall-e-3", modality="image"),
            model("i2", name="flux-pro", modality=""),
            model("t1", name="gpt-4o", modality="text"),
        ],
        cfg(modality="image"),
    )
    assert set(ids(out)) == {"i1", "i2"}


def test_text_target_rejects_image_keyword_when_modality_missing():
    """modality 为空但名称含 image 关键词的，不算文本模型。"""
    out = filter_arena_catalog_models(
        [model("f1", name="midjourney-v6", modality=""), model("t1", name="claude", modality="")],
        cfg(modality="text"),
    )
    assert ids(out) == ["t1"]


def test_text_target_rejects_other_modalities():
    out = filter_arena_catalog_models(
        [model("a", modality="code"), model("b", modality="video"), model("c", modality="text")],
        cfg(modality="text"),
    )
    assert ids(out) == ["c"]


# --------------------------------------------------------------------------
# 3. video 关键词预过滤及其 kling / inkling 特例
# --------------------------------------------------------------------------


def test_video_keywords_are_filtered_out_of_text_preset():
    out = filter_arena_catalog_models(
        [model("v1", name="sora-turbo", modality="text"), model("t1", name="claude", modality="text")],
        cfg(modality="text"),
    )
    assert ids(out) == ["t1"]


def test_inkling_is_not_filtered_by_kling_rule():
    """'inkling' 含 'kling' 子串，但不是视频模型——这条特例必须保留。"""
    out = filter_arena_catalog_models(
        [model("i1", name="inkling-alpha", modality="text")], cfg(modality="text")
    )
    assert ids(out) == ["i1"]


def test_real_kling_is_filtered():
    out = filter_arena_catalog_models(
        [model("k1", name="kling-2.0", modality="text")], cfg(modality="text")
    )
    assert ids(out) == []


def test_video_keyword_prefilter_is_skipped_for_video_target():
    """目标是 video 时，video 关键词不应再把模型滤掉。"""
    out = filter_arena_catalog_models(
        [model("v1", name="sora-turbo", modality="video")], cfg(modality="video")
    )
    assert ids(out) == ["v1"]


# --------------------------------------------------------------------------
# 4. 明池规则
# --------------------------------------------------------------------------


def test_include_keywords_act_as_allowlist():
    out = filter_arena_catalog_models(
        [model("a", name="gpt-4o"), model("b", name="claude-3")],
        cfg(include_keywords=["gpt"]),
    )
    assert ids(out) == ["a"]


def test_exclude_keywords_act_as_denylist():
    out = filter_arena_catalog_models(
        [model("a", name="gpt-4o"), model("b", name="claude-3")],
        cfg(exclude_keywords=["gpt"]),
    )
    assert ids(out) == ["b"]


def test_include_and_exclude_combine():
    out = filter_arena_catalog_models(
        [model("a", name="gpt-4o"), model("b", name="gpt-4o-mini"), model("c", name="claude")],
        cfg(include_keywords=["gpt"], exclude_keywords=["mini"]),
    )
    assert ids(out) == ["a"]


def test_aliases_are_searchable():
    """aliases 也应参与关键词匹配。"""
    out = filter_arena_catalog_models(
        [model("a", name="some-model", aliases=["chatgpt"]), model("b", name="other")],
        cfg(include_keywords=["chatgpt"]),
    )
    assert ids(out) == ["a"]


# --------------------------------------------------------------------------
# 5. 暗池规则
# --------------------------------------------------------------------------


def dark(mid="d1", name="internal-test-model", **extra):
    """暗池模型：显式 is_dark_pool=True，且默认 id 与 name 都要传。"""
    return model(mid, name=name, is_dark_pool=True, **extra)


def test_dark_pool_models_hidden_when_disabled():
    out = filter_arena_catalog_models(
        [dark("d1"), model("p1", name="gpt-4o")], cfg(enable_dark_pool=False)
    )
    assert ids(out) == ["p1"]


def test_dark_pool_models_shown_when_enabled():
    out = filter_arena_catalog_models(
        [dark("d1"), model("p1", name="gpt-4o")], cfg(enable_dark_pool=True)
    )
    assert set(ids(out)) == {"d1", "p1"}


def test_dark_blacklist_vetoes_even_when_enabled():
    out = filter_arena_catalog_models(
        [dark("d1", name="secret-internal-x"), dark("d2", name="safe-internal-y")],
        cfg(enable_dark_pool=True, dark_pool_blacklist_keywords=["secret"]),
    )
    assert ids(out) == ["d2"]


def test_dark_whitelist_requires_a_match():
    out = filter_arena_catalog_models(
        [dark("d1", name="alpha-internal"), dark("d2", name="beta-internal")],
        cfg(enable_dark_pool=True, dark_pool_whitelist_keywords=["beta"]),
    )
    assert ids(out) == ["d2"]


def test_public_pool_rules_do_not_apply_to_dark_models():
    """暗池模型走暗池规则，不受明池 include/exclude 影响。"""
    out = filter_arena_catalog_models(
        [dark("d1", name="internal-zzz")],
        cfg(enable_dark_pool=True, include_keywords=["绝对不匹配"]),
    )
    assert ids(out) == ["d1"]


def test_dark_pool_rules_do_not_apply_to_public_models():
    """反过来，明池模型也不受暗池黑名单影响。"""
    out = filter_arena_catalog_models(
        [model("p1", name="gpt-secret")],
        cfg(enable_dark_pool=True, dark_pool_blacklist_keywords=["secret"]),
    )
    assert ids(out) == ["p1"]


def test_explicit_flag_overrides_name_heuristics():
    """is_dark_pool=False 应压过名称里的 internal/test 等启发式特征。"""
    out = filter_arena_catalog_models(
        [model("x1", name="internal-thing", is_dark_pool=False)], cfg(enable_dark_pool=False)
    )
    assert ids(out) == ["x1"]


# --------------------------------------------------------------------------
# 6. 排序与稳定性
# --------------------------------------------------------------------------


def test_result_is_sorted_by_public_id():
    """排序键是公开名（get_arena_direct_model_public_id），不是 arena_model_id。

    这里故意让 id 顺序与名称顺序相反，以证明排序确实按名称走。
    """
    out = filter_arena_catalog_models(
        [model("id-c", name="a"), model("id-a", name="b"), model("id-b", name="c")], cfg()
    )
    assert ids(out) == ["id-c", "id-a", "id-b"]  # 按名称 a / b / c 排


def test_result_items_are_the_original_objects():
    original = model("m1")
    out = filter_arena_catalog_models([original], cfg())
    assert out[0] is original


def test_input_list_is_not_mutated():
    models = [model("m2"), model("m1")]
    snapshot = [dict(m) for m in models]
    filter_arena_catalog_models(models, cfg())
    assert models == snapshot
