from app.core.workflow.attachment_monitor import (
    AttachmentMonitor,
    _ATTACHMENT_MONITOR_BOOTSTRAP_JS,
)
from app.models.schemas import (
    get_default_attachment_monitor_config,
    validate_site_config,
)


def test_attachment_monitor_options_include_ignored_busy_markers():
    monitor = AttachmentMonitor(
        tab=None,
        selectors={"input_box": "textarea", "send_btn": "button[type='submit']"},
        config={
            "busy_text_markers": ["loading", "thinking"],
            "ignored_busy_text_markers": ["thinking", "thinking", ""],
        },
    )

    options = monitor._build_options()

    assert options["busyTextMarkers"] == ["loading", "thinking"]
    assert options["ignoredBusyTextMarkers"] == ["thinking"]


def test_default_attachment_monitor_config_exposes_ignored_busy_markers():
    config = get_default_attachment_monitor_config()

    assert config.get("ignored_busy_text_markers", []) == []


def test_site_config_validation_accepts_ignored_busy_marker_lists():
    assert validate_site_config(
        {
            "selectors": {"input_box": "textarea"},
            "workflow": [],
            "file_paste": {
                "attachment_monitor": {
                    "ignored_busy_text_markers": ["thinking"],
                }
            },
        }
    )


def test_default_busy_words_do_not_include_thinking():
    default_busy_words_section = _ATTACHMENT_MONITOR_BOOTSTRAP_JS.split(
        "const defaultBusyWords = [", 1
    )[1].split("];", 1)[0]

    assert '"thinking"' not in default_busy_words_section
