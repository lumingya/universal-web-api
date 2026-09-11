"""Exercise the real encoding path; never replace set_input_atomic with a mock."""
import base64
import re
from unittest.mock import Mock

import pytest
from app.core.workflow.text_input import TextInputHandler


def handler():
    return TextInputHandler(None, False, lambda *args: None, lambda: False)


@pytest.mark.parametrize('mode', ['overwrite', 'append'])
@pytest.mark.parametrize('text', ['', 'plain text', '中文 😀\r\n第二行', '"quotes" \\ ${literal} </script>\n第三行'])
def test_atomic_input_encodes_utf8_and_reaches_element(mode, text):
    element = Mock()
    element.run_js.return_value = True
    assert handler().set_input_atomic(element, text, mode=mode) is True
    element.run_js.assert_called_once()
    script = element.run_js.call_args.args[0]
    encoded = re.search(r'const b64 = "([A-Za-z0-9+/=]*)";', script).group(1)
    assert base64.b64decode(encoded, validate=True).decode('utf-8') == text.replace('\r\n', '\n')
    assert f'const isAppend = {str(mode == "append").lower()};' in script


@pytest.mark.parametrize('raises', [False, True])
def test_atomic_input_does_not_hide_element_failure(raises):
    element = Mock()
    element.run_js.return_value = False
    if raises:
        element.run_js.side_effect = RuntimeError('element detached')
    assert handler().set_input_atomic(element, '中文输入') is False
    element.run_js.assert_called_once()


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.mark.parametrize('mode', ['overwrite', 'append'])
@pytest.mark.parametrize('selector', ['textarea', '[contenteditable]'])
def test_atomic_input_really_writes_unicode_in_browser(browser, selector, mode):
    page = browser.new_page()
    try:
        page.set_content('<textarea>原文</textarea><div contenteditable="true">原文</div>')
        locator = page.locator(selector)
        class Element:
            def run_js(self, script):
                return locator.evaluate('(el, code) => (new Function(code)).call(el)', script)
        text = '第一行 😀\r\n第二行 "引号" \\ ${literal} </script>'
        assert handler().set_input_atomic(Element(), text, mode=mode) is True
        expected = ('原文' if mode == 'append' else '') + text.replace('\r\n', '\n')
        actual = locator.input_value() if selector == 'textarea' else locator.inner_text()
        if selector == 'textarea':
            assert actual == expected
        else:
            # Chromium exposes rich-text paragraph boundaries as double newlines.
            # Accept that existing DOM behavior, but preserve every content character.
            # Exact encoding and newline normalization are covered above.
            assert actual in (expected, expected.replace('\n', '\n\n'))
        assert page.locator('script').count() == 0
    finally:
        page.close()
