"""R1-7：选择器脆弱度检查与回退建议。"""

from __future__ import annotations

import pytest

from app.services.selector_quality import fallback_suggestions, lint_selector
from tests._sites import shipped_sites


def _codes(selector):
    return {issue["code"] for issue in lint_selector(selector)["issues"]}


@pytest.mark.parametrize(
    ("selector", "expected_codes"),
    [
        ("textarea", set()),
        ("button[aria-label='Send message']", set()),
        (".ds-button--primary", set()),                       # 语义化 BEM 类名，没有数字
        ("div.bf38813a div[role='button']", {"generated_class"}),
        (".css-1x2y3z4", {"generated_class"}),
        (".Composer_input__a1b2c", {"generated_class"}),      # CSS Modules
        ("/html/body/div[3]/div[2]/button", {"absolute_xpath", "positional_xpath"}),
        ("//span[text()='发起新对话']", {"text_match"}),
        ("div:nth-child(2) > div:nth-child(3) span", {"nth_child_chain"}),
    ],
)
def test_lint_rules(selector, expected_codes):
    assert _codes(selector) == expected_codes


def test_semantic_anchor_scores_higher_than_hashed_class():
    assert lint_selector("[role='textbox'][contenteditable='true']")["score"] == 100
    assert lint_selector("div.bf38813a > div > div")["score"] < 80


def test_known_hashed_class_in_shipped_config_is_flagged():
    sites = shipped_sites()
    for preset in sites["chat.deepseek.com"]["presets"].values():
        send = preset["selectors"].get("send_btn") or ""
        if "bf38813a" in send:
            assert "generated_class" in _codes(send)
            break
    else:
        pytest.skip("DeepSeek 预设已不再使用 bf38813a")


def test_every_shipped_selector_can_be_linted():
    count = 0
    for site, config in shipped_sites().items():
        for preset in (config.get("presets") or {}).values():
            for selector in (preset.get("selectors") or {}).values():
                if isinstance(selector, str) and selector.strip():
                    result = lint_selector(selector)
                    assert 0 <= result["score"] <= 100
                    count += 1
    assert count > 100


class _Probe:
    url = "https://example.com"

    def __init__(self, counts):
        self.counts = counts

    def count(self, selector):
        return self.counts.get(selector, 0)


def test_fallback_suggestions_prefer_unique_matches():
    from app.core.elements import ElementFinder

    fallbacks = ElementFinder.FALLBACK_SELECTORS["input_box"]
    probe = _Probe({fallbacks[0]: 3, fallbacks[1]: 1})
    suggestions = fallback_suggestions("input_box", probe)
    assert [item["selector"] for item in suggestions][:2] == [fallbacks[1], fallbacks[0]]
    assert suggestions[0]["unique"] is True
    assert fallback_suggestions("input_box", _Probe({})) == []


def test_health_report_includes_quality_and_suggestions():
    from app.core.elements import ElementFinder
    from app.services.adapter_health import evaluate_preset

    fallback = ElementFinder.FALLBACK_SELECTORS["input_box"][0]
    report = evaluate_preset(_Probe({fallback: 1}), {"selectors": {"input_box": "div.bf38813a textarea"}},
                             [{"key": "input_box", "required": True}])
    row = report["selectors"][0]
    assert report["status"] == "broken"
    assert "generated_class" in {issue["code"] for issue in row["quality"]["issues"]}
    assert row["suggestions"][0]["selector"] == fallback
