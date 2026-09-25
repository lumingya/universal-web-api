"""R1-7：选择器韧性——静态脆弱度检查与修复建议（只提供机制，不自动改动任何已配置的选择器）。

- ``lint_selector``：给选择器打 0–100 分并列出问题。生成的哈希类名、很长的 nth-child 链、
  绝对 XPath、过长的后代链和依赖界面语言的文本匹配都会扣分；aria-label、role、placeholder、
  data-testid、name、type 等语义锚点会加分；
- ``fallback_suggestions``：当配置的必需元素找不到时，用运行时本来就会使用的
  ``ElementFinder.FALLBACK_SELECTORS`` 在页面上试一遍，报告哪些能命中。这些命中可以作为修复的起点，
  同时说明运行时是否还能靠回退勉强工作。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_CLASS_TOKEN = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")
_GENERATED_CLASS_PATTERNS = (
    re.compile(r"^(css|sc|jsx|emotion|styled|tw)-[0-9a-zA-Z_-]{4,}$"),   # CSS-in-JS 生成类
    re.compile(r"^[a-f0-9]{6,}$"),                                        # 纯十六进制哈希（如 bf38813a）
    re.compile(r"^_?[a-zA-Z]{1,4}[_-]?[0-9][0-9a-zA-Z]{4,}$"),            # 字母+数字混合的短哈希
    re.compile(r"^.+__[0-9a-zA-Z]{5,}$"),                                 # CSS Modules：name__hash
    re.compile(r"^.+_[0-9a-zA-Z]{5}$"),                                   # CSS Modules：name_hash5
)
_SEMANTIC_ANCHORS = ("aria-label", "role=", "placeholder", "data-testid", "data-test", "name=", "type=", "contenteditable")


def _is_generated_class(token: str) -> bool:
    has_digit = any(ch.isdigit() for ch in token)
    return has_digit and any(pattern.match(token) for pattern in _GENERATED_CLASS_PATTERNS)


def lint_selector(selector: str) -> Dict[str, Any]:
    raw = str(selector or "").strip()
    body = raw[4:] if raw.startswith("css:") else raw
    issues: List[Dict[str, str]] = []
    score = 100

    def issue(code: str, message: str, penalty: int) -> None:
        nonlocal score
        issues.append({"code": code, "message": message})
        score -= penalty

    is_xpath = body.startswith(("xpath:", "/", "("))
    if is_xpath:
        expr = body[6:] if body.startswith("xpath:") else body
        if re.match(r"^/(html|body)\b", expr):
            issue("absolute_xpath", "绝对 XPath 路径，页面结构一变就失效", 45)
        if len(re.findall(r"\[\d+\]", expr)) >= 2:
            issue("positional_xpath", "依赖多个位置下标 [n]", 20)
        if "text()" in expr or "contains(." in expr:
            issue("text_match", "依赖界面文字，切换语言或改文案会失效", 10)
    else:
        generated = [token for token in _CLASS_TOKEN.findall(body) if _is_generated_class(token)]
        if generated:
            issue("generated_class", f"疑似构建工具生成的类名：{', '.join(sorted(set(generated))[:3])}", min(40, 20 * len(set(generated))))
        if body.count(":nth-child") + body.count(":nth-of-type") >= 2:
            issue("nth_child_chain", "多个 nth-child/nth-of-type，依赖元素顺序", 20)
        first_group = body.split(",")[0]
        if len(re.findall(r"\s+(?![^\[]*\])|>", first_group)) >= 5:
            issue("long_chain", "后代/子代链过长", 15)
        if ":has-text(" in body or ":contains(" in body:
            issue("text_match", "依赖界面文字", 10)

    anchors = [anchor.rstrip("=") for anchor in _SEMANTIC_ANCHORS if anchor in body]
    if anchors:
        score += 10
    return {"selector": raw, "score": max(0, min(100, score)), "issues": issues, "semantic_anchors": anchors}


def fallback_suggestions(key: str, probe: Any, limit: int = 5) -> List[Dict[str, Any]]:
    """用运行时回退选择器在页面上试探，返回能命中的候选（唯一命中优先）。"""
    from app.core.elements import ElementFinder

    suggestions = []
    for selector in ElementFinder.FALLBACK_SELECTORS.get(key, []):
        count = probe.count(selector)
        if count > 0:
            suggestions.append({"selector": selector, "count": count, "unique": count == 1,
                                "score": lint_selector(selector)["score"]})
    suggestions.sort(key=lambda item: (not item["unique"], -item["score"], item["count"]))
    return suggestions[:limit]
