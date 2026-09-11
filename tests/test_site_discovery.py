"""Automatic admission must never persist fallback/search/login configs."""
import copy
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app.services.config.engine import ConfigEngine
from app.services.config.processors import AIAnalyzer
from app.utils.site_discovery import automatic_discovery_allowed, admitted_selectors, selectors_match_chat_html
from app.utils.site_rules import _normalize_rule

GOOD={'input_box':'textarea#composer','send_btn':'button.send','result_container':'.assistant-reply'}
HTML='<textarea id="composer"></textarea><button class="send">Send</button><div class="assistant-reply">Assistant reply</div>'


@pytest.mark.parametrize('domain',['google.com','www.google.com','WWW.GOOGLE.COM.','google.co.jp','www.google.co.uk','accounts.google.com','bing.com','www.baidu.com','localhost','about:blank'])
def test_known_non_chat_hosts_are_not_auto_discovered(domain):
    assert not automatic_discovery_allowed(domain)


@pytest.mark.parametrize('domain',['gemini.google.com','gemini.com','aistudio.google.com','chat.deepseek.com','custom-assistant.org','google.com.attacker.org'])
def test_filter_does_not_match_parent_substrings_or_become_an_ai_vendor_allowlist(domain):
    assert automatic_discovery_allowed(domain)


def test_discovery_rule_only_accepts_boolean_and_keeps_existing_rule_fields():
    assert _normalize_rule({'auto_discovery':False,'stealth_default':True})=={'auto_discovery':False,'stealth_default':True,'route_aliases':[]}
    assert 'auto_discovery' not in _normalize_rule({'auto_discovery':'false'})


@pytest.mark.parametrize('response',[None,{},GOOD,{'is_chat_site':False,**GOOD},{'is_chat_site':'true',**GOOD},{'is_chat_site':True,**GOOD,'result_container':'div'},{'is_chat_site':True,**GOOD,'result_container':'.assistant-reply, body'},{'is_chat_site':True,**GOOD,'input_box':None}])
def test_no_assumed_chat_or_generic_response_container(response):
    assert admitted_selectors(response) is None


def test_admission_requires_real_snapshot_matches_and_strips_metadata():
    result=admitted_selectors({'is_chat_site':True,**GOOD})
    assert result==GOOD
    assert selectors_match_chat_html(result,HTML)
    assert not selectors_match_chat_html({**result,'result_container':'.invented'},HTML)
    assert not selectors_match_chat_html({**result,'input_box':'!!invalid'},HTML)


@pytest.mark.parametrize('is_chat',[True,False,'true',None])
def test_ai_analyzer_classifies_before_returning_selectors_without_extra_request(is_chat):
    analyzer=AIAnalyzer.__new__(AIAnalyzer)
    analyzer.api_key='test-only';analyzer.global_config=None;analyzer.provider='openai'
    analyzer._request_ai=MagicMock(return_value=json.dumps({'is_chat_site':is_chat,**GOOD}))
    result=analyzer.analyze(HTML)
    assert result==(GOOD if is_chat is True else None)
    analyzer._request_ai.assert_called_once()
    prompt=analyzer._request_ai.call_args.args[0]
    assert 'do not assume every page is a chat app' in prompt
    assert 'login/consent' in prompt


def engine():
    obj=ConfigEngine.__new__(ConfigEngine)
    obj.sites={};obj.refresh_if_changed=lambda:None;obj._io_lock=threading.RLock()
    obj.global_config=SimpleNamespace(get_fallback_selectors=lambda:{'input_box':'textarea','send_btn':'button[type=submit]','result_container':'div'})
    obj.html_cleaner=SimpleNamespace(clean=lambda html:html)
    obj.ai_analyzer=SimpleNamespace(analyze=MagicMock(return_value=None))
    obj.validator=SimpleNamespace(validate=lambda value:copy.deepcopy(value))
    obj._guess_stealth=lambda _:False
    obj._save_config=MagicMock(return_value=True)
    obj._cache=SimpleNamespace(get=lambda key,missing:missing,set=lambda *args:None)
    return obj


def test_search_host_skips_lazy_html_and_ai_entirely():
    obj=engine();html=MagicMock(return_value=HTML)
    assert obj.get_site_config('www.google.com',html_content=html) is None
    html.assert_not_called();obj.ai_analyzer.analyze.assert_not_called();obj._save_config.assert_not_called()
    assert obj.list_sites()=={}


@pytest.mark.parametrize('html',[None,'',HTML])
def test_fallback_is_per_call_only_and_never_enters_catalog_or_disk(html):
    obj=engine()
    result=obj.get_site_config('ordinary-page.org',html_content=html)
    assert result['selectors']['result_container']=='div'
    assert obj.list_sites()=={}
    obj._save_config.assert_not_called()
    result['selectors']['result_container']='mutated'
    assert obj.get_site_config('ordinary-page.org')['selectors']['result_container']=='div'


def test_positive_chat_can_be_saved_but_invented_selector_cannot():
    obj=engine();obj.ai_analyzer.analyze.return_value=GOOD
    result=obj.get_site_config('custom-assistant.org',html_content=HTML)
    assert result['selectors']==GOOD
    assert 'custom-assistant.org' in obj.list_sites()
    obj._save_config.assert_called_once()
    second=engine();second.ai_analyzer.analyze.return_value={**GOOD,'result_container':'.invented'}
    second.get_site_config('custom-assistant.org',html_content=HTML)
    assert second.list_sites()=={};second._save_config.assert_not_called()


def test_explicit_manual_config_is_not_blocked_by_automatic_admission_policy():
    obj=engine();preset={'selectors':GOOD,'workflow':[]}
    obj.sites={'google.com':{'presets':{'custom':preset}}}
    obj._cache=SimpleNamespace(get=lambda key,missing:preset)
    assert obj.get_site_config('google.com')==preset
    obj.ai_analyzer.analyze.assert_not_called()
