"""Conservative automatic site admission; explicit user configs are not restricted."""
from __future__ import annotations
import re
from typing import Any
from app.utils.site_rules import get_site_rule
from app.utils.site_url import extract_remote_site_domain, normalize_route_domain


def automatic_discovery_allowed(domain: str) -> bool:
    host = normalize_route_domain(domain)
    if not host or extract_remote_site_domain('https://' + host) is None:
        return False
    # www is an alias, not a reason to broaden a deny rule to all subdomains.
    # google.com is a search page; gemini.google.com is a separate application.
    rule = get_site_rule(host)
    if 'auto_discovery' not in rule and host.startswith('www.'):
        rule = get_site_rule(host[4:])
    return rule.get('auto_discovery', True) is not False


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
