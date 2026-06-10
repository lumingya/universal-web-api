"""
app/core/page_capture - page-side provider helpers.

This package groups provider-specific page runtime capabilities while keeping
their dispatch APIs small and generic.
"""

from .base import PageFetchCapture
from .registry import (
    create_page_fetch_capture as _create_page_fetch_capture,
    register_page_fetch_capture,
)
from .request_transport import (
    execute_page_request_transport as _execute_page_request_transport,
    get_page_request_transport_profile as _get_page_request_transport_profile,
    get_page_request_transport_profiles as _get_page_request_transport_profiles,
    register_page_request_transport,
)

_FETCH_CAPTURE_BUILTINS_LOADED = False
_REQUEST_TRANSPORT_BUILTINS_LOADED = False


def _load_builtin_fetch_captures() -> None:
    global _FETCH_CAPTURE_BUILTINS_LOADED
    if _FETCH_CAPTURE_BUILTINS_LOADED:
        return
    from . import kimi_fetch_capture as _kimi_fetch_capture  # noqa: F401

    _FETCH_CAPTURE_BUILTINS_LOADED = True


def _load_builtin_request_transports() -> None:
    global _REQUEST_TRANSPORT_BUILTINS_LOADED
    if _REQUEST_TRANSPORT_BUILTINS_LOADED:
        return
    from . import deepseek_request_transport as _deepseek_request_transport  # noqa: F401

    _REQUEST_TRANSPORT_BUILTINS_LOADED = True


def create_page_fetch_capture(*args, **kwargs):
    _load_builtin_fetch_captures()
    return _create_page_fetch_capture(*args, **kwargs)


def get_page_request_transport_profiles():
    _load_builtin_request_transports()
    return _get_page_request_transport_profiles()


def get_page_request_transport_profile(profile_id):
    _load_builtin_request_transports()
    return _get_page_request_transport_profile(profile_id)


def execute_page_request_transport(*args, **kwargs):
    _load_builtin_request_transports()
    return _execute_page_request_transport(*args, **kwargs)


__all__ = [
    "PageFetchCapture",
    "create_page_fetch_capture",
    "execute_page_request_transport",
    "get_page_request_transport_profile",
    "get_page_request_transport_profiles",
    "register_page_fetch_capture",
    "register_page_request_transport",
]
