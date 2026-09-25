"""Conservative automatic site admission; explicit user configs are not restricted."""
from __future__ import annotations
import re
from typing import Any
from app.utils.site_rules import get_site_rule
from app.utils.site_url import extract_remote_site_domain, normalize_route_domain


# 修复 B1：搜索引擎主域默认可被自动发现，于是一个搜索结果页可能被当成聊天站点
# 存进 sites.json。这里按「可注册域的主标签」识别搜索引擎，从而同时覆盖
# google.com / google.co.jp / google.co.uk 等全部 ccTLD 变体，
# 而不需要为每个国家域各写一条规则。
_SEARCH_ENGINE_LABELS = frozenset({
    'google', 'bing', 'baidu', 'yahoo', 'yandex', 'duckduckgo', 'sogou',
    'so', 'naver', 'daum', 'ask', 'ecosia', 'startpage', 'qwant', 'seznam',
    'searx', 'mojeek', 'brave',
})

# 公共后缀里常见的短标签。用它判断「search-label 之后是不是只剩后缀」，
# 从而把 google.com.attacker.org 这类仿冒域排除在规则之外。
_PUBLIC_SUFFIX_LABELS = frozenset({
    'com', 'net', 'org', 'edu', 'gov', 'mil', 'int', 'info', 'biz', 'name',
    'co', 'ne', 'or', 'ac', 'go', 'gr', 'nom', 'web', 'store', 'site', 'online',
})

# 即便属于「可自动发现」的站点，这些子域也只是登录 / 账号 / 内容页，不是聊天应用。
# 与 AI 分析提示词里的 login/consent 判断保持一致。
_NON_CHAT_SUBDOMAIN_LABELS = frozenset({
    'accounts', 'account', 'myaccount', 'login', 'signin', 'sign-in', 'auth',
    'oauth', 'sso', 'id', 'passport', 'register', 'signup',
    'mail', 'news', 'maps', 'images', 'video', 'translate', 'books', 'scholar',
    'drive', 'docs', 'photos', 'calendar', 'play', 'store', 'shopping',
    'ads', 'analytics', 'support', 'help', 'policies', 'about', 'developers',
})


def _is_public_suffix_label(label: str) -> bool:
    if label in _PUBLIC_SUFFIX_LABELS:
        return True
    # 绝大多数 ccTLD / 二级公共后缀都是 2-3 个字母
    return len(label) <= 3 and label.isalpha()


def _search_engine_subdomain_labels(host: str):
    """若 host 属于某个搜索引擎的可注册域，返回它前面的子域标签列表，否则返回 None。

    - ``google.com``          → ``[]``（主域本身）
    - ``www.google.co.uk``    → ``['www']``
    - ``gemini.google.com``   → ``['gemini']``
    - ``google.com.attacker.org`` → ``None``（`attacker` 不是公共后缀标签，
      说明可注册域是 attacker.org，与 google 无关）
    """
    labels = [part for part in str(host or '').split('.') if part]
    for index, label in enumerate(labels):
        if label not in _SEARCH_ENGINE_LABELS:
            continue
        tail = labels[index + 1:]
        if not tail:
            continue
        if all(_is_public_suffix_label(item) for item in tail):
            return labels[:index]
    return None


def _search_engine_discovery_allowed(host: str) -> bool:
    """搜索引擎域名下，只有看起来像独立应用的子域才允许自动发现。"""
    prefix_labels = _search_engine_subdomain_labels(host)
    if prefix_labels is None:
        return True
    if not prefix_labels:
        # 主域本身就是搜索页
        return False
    if prefix_labels == ['www']:
        return False
    # gemini.google.com / aistudio.google.com 这类独立应用子域保持可发现，
    # accounts.google.com 这类登录/内容页则排除。
    return not any(label in _NON_CHAT_SUBDOMAIN_LABELS for label in prefix_labels)


def automatic_discovery_allowed(domain: str) -> bool:
    host = normalize_route_domain(domain)
    if not host or extract_remote_site_domain('https://' + host) is None:
        return False
    # www is an alias, not a reason to broaden a deny rule to all subdomains.
    # google.com is a search page; gemini.google.com is a separate application.
    rule = get_site_rule(host)
    if 'auto_discovery' not in rule and host.startswith('www.'):
        rule = get_site_rule(host[4:])
    if 'auto_discovery' in rule:
        # 显式配置（含 sites.local.json 覆盖）始终优先于启发式规则
        return rule['auto_discovery'] is not False
    return _search_engine_discovery_allowed(host)


def specific_chat_selectors(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if any(v is not None and not isinstance(v, str) for v in value.values()):
        return False
    for key in ('input_box', 'result_container'):
        if not str(value.get(key) or '').strip():
            return False
    # Never promote fallback containers (body/div/*) to evidence of an AI reply.
    for part in value['result_container'].lower().removeprefix('css:').split(','):
        if re.fullmatch(r'(?:html|body|main|div|section|article|p|span|\*|#root|#app)', part.strip()):
            return False
    return True


def admitted_selectors(analysis: Any) -> dict | None:
    """An explicit boolean classification AND usable selectors are required."""
    if not isinstance(analysis, dict) or analysis.get('is_chat_site') is not True:
        return None
    selectors = {k: v for k, v in analysis.items() if k != 'is_chat_site'}
    return selectors if specific_chat_selectors(selectors) else None


def selectors_match_chat_html(selectors: Any, html: str) -> bool:
    """Both core selectors must actually match the analyzed snapshot, not be invented."""
    if not specific_chat_selectors(selectors):
        return False
    from bs4 import BeautifulSoup
    try:
        document = BeautifulSoup(html, 'html.parser')
        return all(document.select_one(selectors[key]) is not None
                   for key in ('input_box', 'result_container'))
    except Exception:
        return False
