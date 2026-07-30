from app.core.stream_monitor import (
    StreamContext,
    StreamMonitor,
    _is_pending_image_status_text,
)
from app.core.workflow.executor import WorkflowExecutor


class _ImageInfoElement:
    def __init__(self, result):
        self.result = result
        self.script = ""

    def run_js(self, script, *args):
        self.script = script
        self.args = args
        return self.result


def test_dom_image_probe_stays_enabled_for_on_signal_policy():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._image_extraction_enabled = True
    monitor._image_config = {
        "modalities": {
            "image": {"enabled": True, "run_policy": "on_signal"},
        }
    }
    monitor._expect_image_output = False

    assert monitor._should_probe_dom_images() is True


def test_blob_image_counts_as_signal_without_remote_prefetch_url():
    monitor = StreamMonitor.__new__(StreamMonitor)
    element = _ImageInfoElement(
        {
            "count": 1,
            "urls": [],
            "references": ["blob:https://example.test/generated-image"],
        }
    )

    result = monitor._extract_image_info(element)

    assert result == {
        "count": 1,
        "urls": [],
        "references": ["blob:https://example.test/generated-image"],
    }
    assert "blob:" in element.script
    assert "data:image" in element.script
    assert element.args == ("", "", False)


def test_same_image_count_with_new_url_is_detected_as_fresh_output():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._prefetch_snapshot_image_urls = lambda snapshot: 0
    ctx = StreamContext()
    baseline = {
        "groups_count": 4,
        "text_len": 0,
        "image_count": 1,
        "image_urls": ["https://example.test/old.png"],
    }
    current = {
        "groups_count": 4,
        "text_len": 0,
        "is_generating": False,
        "image_count": 1,
        "image_urls": ["https://example.test/new.png"],
    }

    started, reason = monitor._detect_ai_start(baseline, current, ctx)

    assert started is True
    assert "检测到新图片" in reason
    assert ctx.images_detected is True


def test_signed_url_refresh_for_same_r2_object_is_not_a_new_image():
    baseline = {
        "image_count": 1,
        "image_urls": [
            "https://bucket.r2.cloudflarestorage.com/result.png?X-Amz-Signature=old"
        ],
    }
    current = {
        "image_count": 1,
        "image_urls": [
            "https://bucket.r2.cloudflarestorage.com/result.png?X-Amz-Signature=new"
        ],
    }

    assert StreamMonitor._snapshot_has_new_image(baseline, current) is False


def test_same_count_same_url_is_not_a_new_image():
    baseline = {"image_count": 1, "image_urls": ["https://example.test/result.png"]}
    current = {"image_count": 1, "image_urls": ["https://example.test/result.png"]}

    assert StreamMonitor._snapshot_has_new_image(baseline, current) is False


def test_new_page_level_image_is_detected_outside_selected_reply():
    baseline = {
        "image_count": 0,
        "page_image_references": ["https://example.test/old.png"],
    }
    current = {
        "image_count": 0,
        "page_image_references": [
            "https://example.test/old.png",
            "https://example.test/generated.png",
        ],
    }

    assert StreamMonitor._snapshot_has_new_image(baseline, current) is True


def test_image_generation_placeholder_is_not_meaningful_output():
    assert _is_pending_image_status_text("Generating image...") is True
    assert _is_pending_image_status_text("正在创建您的图片") is True
    assert _is_pending_image_status_text("Here is the generated image you requested.") is False


def test_pending_image_status_keeps_dom_monitor_in_recovery_wait():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._expect_image_output = True
    ctx = StreamContext()
    ctx.sent_content_length = 19

    no_progress, pending = monitor._classify_image_wait_state(
        ctx,
        "Generating image...",
        has_output=True,
        current_has_new_image=False,
    )

    assert no_progress is True
    assert pending is True

    no_progress_after_refresh, pending_after_refresh = monitor._classify_image_wait_state(
        ctx,
        "",
        has_output=False,
        current_has_new_image=False,
    )

    assert no_progress_after_refresh is True
    assert pending_after_refresh is False


def test_incomplete_closed_stream_enables_bounded_image_recovery():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._expect_image_output = True
    monitor._network_fallback_reason = "目标流提前关闭且未产出有效结果"

    assert monitor._uses_interrupted_image_recovery() is True


def test_heartbeat_only_stream_timeout_enables_bounded_image_recovery():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._expect_image_output = True
    monitor._network_fallback_reason = "目标流未产出有效正文（15.0s）"

    assert monitor._uses_interrupted_image_recovery() is True


def test_generic_dom_monitor_does_not_enable_interrupted_recovery():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._expect_image_output = True
    monitor._network_fallback_reason = ""

    assert monitor._uses_interrupted_image_recovery() is False


def test_stalled_image_restart_stops_then_clicks_retry(monkeypatch):
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor._selectors = {
        "stop_btn": "button.stop",
        "retry button": "button.retry",
    }
    calls = []
    executor._stop_stalled_image_generation = lambda: calls.append("stop") or True
    executor._execute_click = (
        lambda selector, target_key, optional: calls.append((selector, target_key, optional)) or True
    )
    monkeypatch.setattr("app.core.workflow.executor.time.sleep", lambda _seconds: None)

    assert executor._restart_stalled_image_generation() is True
    assert calls == ["stop", ("button.retry", "retry button", True)]


def test_arena_pages_enable_stalled_image_recovery_defaults():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = type("Tab", (), {"url": "https://arena.ai/c/example"})()

    assert monitor._uses_arena_image_recovery_defaults() is True


def test_non_arena_pages_do_not_enable_stalled_image_recovery_defaults():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = type("Tab", (), {"url": "https://example.test/chat"})()

    assert monitor._uses_arena_image_recovery_defaults() is False


def test_similar_hostname_does_not_enable_arena_recovery_defaults():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = type("Tab", (), {"url": "https://notarena.ai.example.test/chat"})()

    assert monitor._uses_arena_image_recovery_defaults() is False
