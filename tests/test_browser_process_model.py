"""P0-7: optional BROWSER_PROCESS_MODEL launcher flag."""

import pytest

from app.core.settings_registry import find
from app.runtime.browser_launcher import browser_process_model_args


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", []),
        ("default", []),
        ("per-site", ["--process-per-site"]),
        ("PER_SITE", ["--process-per-site"]),
        ("limit:2", ["--renderer-process-limit=2"]),
        ("limit=4", ["--renderer-process-limit=4"]),
        ("limit:0", []),
        ("limit:x", []),
        ("bogus", []),
    ],
)
def test_browser_process_model_args(value, expected):
    assert browser_process_model_args(value) == expected


def test_process_model_not_enabled_by_default():
    setting = find("BROWSER_PROCESS_MODEL")
    assert setting is not None and setting.default == ""
