"""Conservative automatic site admission; explicit user configs are not restricted."""
from __future__ import annotations
import re
from typing import Any
from app.utils.site_rules import get_site_rule
from app.utils.site_url import extract_remote_site_domain, normalize_route_domain

# B1：搜索引擎的各国主域（google.de、google.com.br …）数量太多，不逐个写进 site_rules.json。
# 只做**整串精确匹配**，gemini.google.com / aistudio.google.com 等子域不受影响；
# google.com.attacker.org 这类后缀伪装也不会命中。site_rules.json 中显式的
# auto_discovery 值（含 true）优先于内置规则。
_BUILTIN_NON_CHAT_HOST_PATTERNS = (
    re.compile(r'^google\.(?:com?\.)?[a-z]{2,3}$'),
    re.compile(r'^(?:[a-z]{2}\.)?bing\.com$'),
    re.compile(r'^yandex\.(?:com?\.)?[a-z]{2,3}$'),
)


def _builtin_non_chat_host(host: str) -> bool:
    return any(pattern.fullmatch(host) for pattern in _BUILTIN_NON_CHAT_HOST_PATTERNS)


def automatic_discovery_allowed(domain: str) -> bool:
    host = normalize_route_domain(domain)
    if not host or extract_remote_site_domain('https://' + host) is None:
        return False
    # www is an alias, not a reason to broaden a deny rule to all subdomains.
    # google.com is a search page; gemini.google.com is a separate application.
    rule = get_site_rule(host)
    lookup_host = host
    if 'auto_discovery' not in rule and host.startswith('www.'):
        rule = get_site_rule(host[4:])
        lookup_host = host[4:]
    if 'auto_discovery' in rule:
        return rule['auto_discovery'] is not False
    return not _builtin_non_chat_host(lookup_host)


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
