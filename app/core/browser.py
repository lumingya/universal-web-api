# app/core/browser.py - Entry point forwarding to app.core.browser package

from app.core.browser.main import BrowserCore, get_browser, browser

__all__ = [
    'BrowserCore',
    'get_browser',
    'browser',
]
