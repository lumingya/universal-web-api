from io import BytesIO

import pytest
from PIL import Image

import app.services.arena_image_generation as arena_image_generation
from app.core.stream_monitor import (
    StreamContext,
    StreamMonitor,
    _is_pending_image_status_text,
)
from app.core.network_monitor import NetworkMonitorError
from app.core.config import WorkflowError
from app.core.workflow.executor import WorkflowExecutor
from app.services.arena_image_generation import (
    ARENA_NATIVE_STOP_SELECTOR,
    ARENA_IMAGE_GENERATION_FAILED_CODE,
    ARENA_IMAGE_UNCHANGED_CODE,
    ARENA_PROMPT_REJECTED_CODE,
    ARENA_RESULT_BASELINE_PROPERTY,
    ArenaImageGenerationError,
    ArenaImageGenerationGuard,
    capture_arena_result_baseline,
    get_arena_generation_status,
    is_arena_image_generation_request,
    is_arena_page_url,
    validate_generated_images,
)


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


def test_latest_visual_reply_uses_the_parser_target_side_when_other_side_is_lower():
    class _Element:
        def __init__(self, anchor, *, bottom, left):
            self.anchor = anchor
            self.rect = {
                "top": max(0, bottom - 200),
                "bottom": bottom,
                "left": left,
                "width": 400,
                "height": 200,
            }

        def run_js(self, _script):
            return self.rect

    class _Extractor:
        @staticmethod
        def get_anchor(element):
            return element.anchor

    left_reply = _Element("left", bottom=18_727, left=285)
    right_reply = _Element("right", bottom=473, left=789)
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._image_config = {
        "final_target_strategy": "latest_visual_reply",
        "latest_visual_column": "left",
        "_parser_id": "lmarena_image_side_right",
    }
    monitor._last_visual_reply_log_info = None
    monitor.extractor = _Extractor()

    selected, anchor = monitor._select_candidate_element([left_reply, right_reply])

    assert selected is right_reply
    assert anchor == "right"


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


def test_page_level_reference_is_not_rendered_arena_image_evidence():
    baseline = {
        "image_count": 0,
        "image_references": [],
        "page_image_references": [],
    }
    current = {
        "image_count": 0,
        "image_references": [],
        "page_image_references": ["https://example.test/generated.png"],
    }

    assert StreamMonitor._snapshot_has_new_image(baseline, current) is True
    assert StreamMonitor._snapshot_has_new_rendered_image(baseline, current) is False

    current["image_count"] = 1
    current["image_references"] = ["https://example.test/generated.png"]
    assert StreamMonitor._snapshot_has_new_rendered_image(baseline, current) is True


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
    monitor.tab = type("Tab", (), {"url": "https://arena.ai/c/example"})()
    monitor._expect_image_output = True
    monitor._network_fallback_reason = "目标流提前关闭且未产出有效结果"

    assert monitor._uses_interrupted_image_recovery() is True


def test_workflow_dom_resume_enables_ten_second_arena_recovery_path():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = type("Tab", (), {"url": "https://arena.ai/image"})()
    monitor._expect_image_output = True
    monitor._network_fallback_reason = "workflow_interrupt_dom_resume"
    monitor._recovery_mode = "workflow_dom_resume"

    assert monitor._uses_interrupted_image_recovery() is True
    assert monitor._uses_interrupted_stream_recovery() is True


def test_heartbeat_only_stream_timeout_enables_bounded_image_recovery():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = type("Tab", (), {"url": "https://arena.ai/c/example"})()
    monitor._expect_image_output = True
    monitor._network_fallback_reason = "目标流未产出有效正文（15.0s）"

    assert monitor._uses_interrupted_image_recovery() is True


def test_incomplete_stream_timeout_enables_generic_recovery_for_text_and_images():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = type("Tab", (), {"url": "https://arena.ai/c/example"})()
    monitor._expect_image_output = False
    monitor._network_fallback_reason = "目标流未完整结束（6.3s）"

    assert monitor._uses_interrupted_stream_recovery() is True


def test_active_stop_recovery_has_a_bounded_no_progress_grace():
    assert StreamMonitor._active_generation_refresh_allowed(
        True, True, 29.9, 30.0
    ) is False
    assert StreamMonitor._active_generation_refresh_allowed(
        True, True, 30.0, 30.0
    ) is True
    assert StreamMonitor._active_generation_refresh_allowed(
        False, True, 0.0, 30.0
    ) is True


def test_active_text_generation_does_not_reload_during_network_silence(monkeypatch):
    clock = [0.0]

    def fake_time():
        clock[0] += 0.5
        return clock[0]

    class FakeTab:
        url = "https://arena.ai/c/example"

        def __init__(self):
            self.refresh_calls = []
            self.stop_states = iter([True] * 20 + [False])

        def run_js(self, _script, *args):
            if args and str(args[0]).startswith(ARENA_NATIVE_STOP_SELECTOR):
                return next(self.stop_states, False)
            return None

        def refresh(self, **kwargs):
            self.refresh_calls.append(kwargs)

    tab = FakeTab()
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = tab
    monitor.session = type("Session", (), {"notify_navigated": lambda *_args, **_kwargs: None})()
    monitor.formatter = type(
        "Formatter",
        (),
        {"pack_chunk": lambda _self, text, completion_id=None: text},
    )()
    monitor._should_stop = lambda: False
    monitor._hard_timeout = 300
    monitor._image_config = {
        "enabled": False,
        "modalities": {"image": {"enabled": False}},
        "dom_active_stop_recovery_grace_seconds": 60,
    }
    monitor._stream_config = {}
    monitor._expect_image_output = False
    monitor._network_fallback_reason = "目标流未完整结束（6.5s）"
    monitor._recovery_mode = ""
    monitor._stream_recovery_exhausted = False
    monitor._image_recovery_exhausted = False
    monitor._stream_recovery_refresh_done = False
    monitor._stream_recovery_refresh_attempts = 0
    monitor._generating_checker = None
    monitor._arena_image_guard = None
    monitor._prefetch_snapshot_image_urls = lambda _snapshot: None

    def snapshot(is_generating):
        return {
            "groups_count": 1,
            "anchor": "reply-1",
            "text": "partial response",
            "text_len": 16,
            "is_generating": is_generating,
            "image_count": 0,
            "has_images": False,
            "image_urls": [],
            "image_references": [],
            "page_image_urls": [],
            "page_image_references": [],
        }

    snapshot_calls = [0]

    def get_snapshot(*_args):
        snapshot_calls[0] += 1
        return snapshot(snapshot_calls[0] <= 20)

    monitor._get_snapshot_prefer_anchor = get_snapshot
    monitor._final_settle_and_output = lambda *_args, **_kwargs: iter(["final-text"])

    ctx = StreamContext()
    ctx.baseline_snapshot = snapshot(False)

    monkeypatch.setattr("app.core.stream_monitor.time.time", fake_time)
    monkeypatch.setattr("app.core.stream_monitor.time.sleep", lambda _seconds: None)

    assert list(monitor._stream_output_phase("main", ctx)) == ["final-text"]
    assert tab.refresh_calls == []


def test_generic_empty_stream_timeout_does_not_enable_interrupted_recovery():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = type("Tab", (), {"url": "https://arena.ai/c/example"})()
    monitor._expect_image_output = False
    monitor._network_fallback_reason = "目标流响应超时（5.0s）"

    assert monitor._uses_interrupted_stream_recovery() is False


def test_interrupted_recovery_requires_reload_and_final_content():
    assert StreamMonitor._interrupted_recovery_confirmed(
        True,
        refresh_done=False,
        still_generating=False,
        has_output=True,
        post_refresh_settled=True,
    ) is False
    assert StreamMonitor._interrupted_recovery_confirmed(
        True,
        refresh_done=True,
        still_generating=True,
        has_output=True,
        post_refresh_settled=True,
    ) is False
    assert StreamMonitor._interrupted_recovery_confirmed(
        True,
        refresh_done=True,
        still_generating=False,
        has_output=True,
        post_refresh_settled=False,
    ) is False
    assert StreamMonitor._interrupted_recovery_confirmed(
        True,
        refresh_done=True,
        still_generating=False,
        has_output=True,
        post_refresh_settled=True,
    ) is True
    assert StreamMonitor._interrupted_recovery_confirmed(
        False,
        refresh_done=False,
        still_generating=True,
        has_output=False,
        post_refresh_settled=False,
    ) is True


def test_text_recovery_complete_before_refresh_behavior():
    assert StreamMonitor._text_recovery_complete_before_refresh(
        interrupted=True,
        expect_image_output=False,
        still_generating=False,
        recovery_output_seen=True,
        refresh_done=False,
    ) is True
    # 页面刷新后，绝不能走 before_refresh 早退分支，必须由 post_refresh_settled 把关
    assert StreamMonitor._text_recovery_complete_before_refresh(
        interrupted=True,
        expect_image_output=False,
        still_generating=False,
        recovery_output_seen=True,
        refresh_done=True,
    ) is False


def test_text_recovery_can_confirm_without_new_output_after_reload():
    has_output = StreamMonitor._recovery_output_ready(
        recovery_output_seen=False,
        expect_image_output=False,
        page_ready=True,
    )

    assert has_output is True
    assert StreamMonitor._interrupted_recovery_confirmed(
        interrupted=True,
        refresh_done=True,
        still_generating=False,
        has_output=has_output,
    ) is True


def test_image_recovery_still_requires_output_after_reload():
    assert StreamMonitor._recovery_output_ready(
        recovery_output_seen=False,
        expect_image_output=True,
        page_ready=True,
    ) is False


def test_interrupted_recovery_keeps_output_evidence_after_dom_collapse():
    assert StreamMonitor._remember_recovery_output(
        True,
        current_text_len=0,
        active_turn_baseline_len=0,
        current_image_count=0,
        baseline_image_count=0,
        current_has_new_image=False,
        sent_content_length=0,
        network_sent_content_length=0,
    ) is True


def test_post_refresh_recovery_accepts_remembered_output_after_dom_collapse():
    output_seen = StreamMonitor._remember_recovery_output(
        previously_seen=True,
        current_text_len=0,
        active_turn_baseline_len=0,
        current_image_count=0,
        baseline_image_count=0,
        current_has_new_image=False,
        sent_content_length=0,
        network_sent_content_length=0,
    )
    assert StreamMonitor._interrupted_recovery_confirmed(
        interrupted=True,
        refresh_done=True,
        still_generating=False,
        has_output=output_seen,
    ) is True


def test_recovery_observation_does_not_advance_sent_offset():
    ctx = StreamContext()
    ctx.sent_content_length = 7

    ctx.remember_observed_text("already sent plus held tail")

    assert ctx.sent_content_length == 7
    assert ctx.max_seen_text == "already sent plus held tail"


def test_arena_recovery_checks_visible_native_stop_button():
    class _Tab:
        url = "https://arena.ai/c/example"

        def __init__(self):
            self.script = ""
            self.selector = ""

        def run_js(self, script, selector):
            self.script = script
            self.selector = selector
            return True

    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = _Tab()

    assert monitor._arena_native_stop_present() is True
    assert monitor.tab.selector == StreamMonitor.ARENA_NATIVE_STOP_SELECTOR
    assert "getBoundingClientRect" in monitor.tab.script
    assert "getComputedStyle" in monitor.tab.script
    assert 'button[aria-label="Stop generation"]' in StreamMonitor.ARENA_NATIVE_STOP_SELECTOR
    assert 'data-arena-hard-stop-overlay="true"' in StreamMonitor.ARENA_NATIVE_STOP_SELECTOR


def test_arena_recovery_ignores_hidden_native_stop_button():
    class _Tab:
        url = "https://arena.ai/c/example"

        @staticmethod
        def run_js(_script, _selector):
            return False

    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = _Tab()

    assert monitor._arena_native_stop_present() is False


def test_arena_stop_present_recognizes_hard_stop_runtime_and_overlay_buttons():
    captured = {}

    class _Tab:
        url = "https://arena.ai/c/example"

        def run_js(self, script, selector):
            captured["script"] = script
            captured["selector"] = selector
            return True

    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = _Tab()

    assert monitor._arena_native_stop_present() is True
    assert "__arenaHardStop" in captured["script"]
    assert "hasOverlayStopButton" in captured["script"]
    assert "pendingAssistantCount" not in captured["script"]
    assert "data-arena-hard-stop-overlay" in captured["script"]


def test_arena_generation_status_ignores_active_fetch_without_stop_or_spinner():
    captured = {}

    class _Tab:
        def run_js(self, script, selector):
            captured["script"] = script
            captured["selector"] = selector
            return {
                "has_stop": False,
                "has_generating_text": False,
                "has_spin_canvas": False,
                "still_generating": False,
                "active": [{"done": False, "ageMs": 56_000}],
            }

    status = get_arena_generation_status(_Tab())

    assert status["has_stop"] is False
    assert status["still_generating"] is False
    assert "st.active" not in captured["script"]
    assert "ageMs < 180000" not in captured["script"]



def test_final_settle_uses_last_complete_snapshot_after_refresh_collapse(monkeypatch):
    clock = [0.0]

    def fake_time():
        clock[0] += 0.8
        return clock[0]

    class _Formatter:
        @staticmethod
        def pack_chunk(content, completion_id=None):
            return f"{completion_id}:{content}"

    empty_snapshot = {
        "groups_count": 1,
        "anchor": "reply",
        "text": "",
        "text_len": 0,
        "image_count": 0,
        "image_urls": [],
        "page_image_urls": [],
    }
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._should_stop = lambda: False
    monitor._get_snapshot_prefer_anchor = lambda *_args: dict(empty_snapshot)
    monitor._get_active_turn_text = lambda _selector: ""
    monitor._network_fallback_reason = "目标流未收到完成标志"
    monitor._image_extraction_enabled = False
    monitor._image_recovery_exhausted = False
    monitor._final_complete_text = ""
    monitor._final_image_urls = []
    monitor._final_images = []
    monitor.formatter = _Formatter()
    monitor.tab = type("Tab", (), {"url": "https://arena.ai/c/example"})()
    monkeypatch.setattr("app.core.stream_monitor.time.time", fake_time)
    monkeypatch.setattr("app.core.stream_monitor.time.sleep", lambda _seconds: None)

    ctx = StreamContext()
    ctx.output_target_anchor = "reply"
    ctx.sent_content_length = 8
    ctx.network_sent_content_length = 8
    ctx.max_seen_text = "already-held-tail"

    chunks = list(
        monitor._final_settle_and_output(
            "main .result",
            ctx,
            completion_id="completion-test",
        )
    )

    assert chunks == ["completion-test:held-tail"]
    assert ctx.sent_content_length == len("already-held-tail")


def test_interrupted_arena_stream_holds_deltas_and_stops_after_one_completed_refresh(monkeypatch):
    clock = [0.0]

    def fake_time():
        clock[0] += 1.0
        return clock[0]

    class _Tab:
        url = "https://arena.ai/c/example"

        def __init__(self):
            self.native_stop_states = [True, False]
            self.refresh_calls = []

        def run_js(self, _script, _selector):
            return self.native_stop_states.pop(0)

        def refresh(self, **kwargs):
            self.refresh_calls.append(kwargs)

    class _Formatter:
        def __init__(self):
            self.chunks = []

        def pack_chunk(self, content, completion_id=None):
            self.chunks.append((content, completion_id))
            return f"{completion_id}:{content}"

    def snapshot(text, *, groups_count=1, anchor="reply"):
        return {
            "groups_count": groups_count,
            "anchor": anchor,
            "text": text,
            "text_len": len(text),
            "is_generating": False,
            "image_count": 0,
            "has_images": False,
            "image_urls": [],
            "image_references": [],
            "page_image_urls": [],
            "page_image_references": [],
        }

    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = _Tab()
    monitor.formatter = _Formatter()
    monitor._should_stop = lambda: False
    monitor._hard_timeout = 300
    monitor._image_config = {"dom_image_interrupted_refresh_interval_seconds": 0.1}
    monitor._stream_config = {"stream_recovery_post_refresh_settle_seconds": 0.0}
    monitor._expect_image_output = False
    monitor._network_fallback_reason = "目标流未收到完成标志"
    monitor._stream_recovery_exhausted = False
    monitor._image_recovery_exhausted = False
    monitor._generating_checker = None
    snapshots = iter(
        [
            snapshot("already-sent-held-tail"),
            snapshot("already-sent-held-tail"),
            snapshot("already-sent-held-tail"),
        ]
    )
    monitor._get_snapshot_prefer_anchor = lambda *_args: next(snapshots)
    final_context = {}

    def final_settle(_selector, ctx, completion_id=None):
        final_context["sent"] = ctx.sent_content_length
        final_context["max_seen"] = ctx.max_seen_text
        yield f"{completion_id}:final-tail"

    monitor._final_settle_and_output = final_settle
    monkeypatch.setattr("app.core.stream_monitor.time.time", fake_time)
    monkeypatch.setattr("app.core.stream_monitor.time.sleep", lambda _seconds: None)

    ctx = StreamContext()
    ctx.baseline_snapshot = snapshot("")
    ctx.sent_content_length = len("already-")
    ctx.network_sent_content_length = len("already-")

    chunks = list(
        monitor._stream_output_phase(
            "main .result",
            ctx,
            completion_id="completion-test",
        )
    )

    assert chunks == ["completion-test:final-tail"]
    assert monitor.tab.refresh_calls == [{"ignore_cache": True}]
    assert monitor.formatter.chunks == []
    assert final_context == {
        "sent": len("already-"),
        "max_seen": "already-sent-held-tail",
    }


def test_interrupted_text_stream_skips_refresh_after_native_stop_disappears(monkeypatch):
    clock = [0.0]

    def fake_time():
        clock[0] += 1.0
        return clock[0]

    class _Tab:
        url = "https://arena.ai/c/example"

        def __init__(self):
            self.refresh_calls = []

        @staticmethod
        def run_js(_script, _selector):
            return False

        def refresh(self, **kwargs):
            self.refresh_calls.append(kwargs)

    def snapshot(text, *, groups_count=1, anchor="reply"):
        return {
            "groups_count": groups_count,
            "anchor": anchor,
            "text": text,
            "text_len": len(text),
            "is_generating": False,
            "image_count": 0,
            "has_images": False,
            "image_urls": [],
            "image_references": [],
            "page_image_urls": [],
            "page_image_references": [],
        }

    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = _Tab()
    monitor._should_stop = lambda: False
    monitor._hard_timeout = 300
    monitor._image_config = {"dom_image_interrupted_refresh_interval_seconds": 0.1}
    monitor._expect_image_output = False
    monitor._network_fallback_reason = "目标流未产出有效正文（25.0s）"
    monitor._stream_recovery_exhausted = False
    monitor._image_recovery_exhausted = False
    monitor._generating_checker = None
    snapshots = iter(
        [
            snapshot("already-sent-held-tail"),
            snapshot("already-sent-held-tail"),
            snapshot("already-sent-held-tail"),
        ]
    )
    monitor._get_snapshot_prefer_anchor = lambda *_args: next(snapshots)
    monitor._final_settle_and_output = lambda _selector, _ctx, completion_id=None: iter(
        [f"{completion_id}:final-tail"]
    )
    monkeypatch.setattr("app.core.stream_monitor.time.time", fake_time)
    monkeypatch.setattr("app.core.stream_monitor.time.sleep", lambda _seconds: None)

    ctx = StreamContext()
    ctx.baseline_snapshot = snapshot("")
    ctx.sent_content_length = len("already-")
    ctx.network_sent_content_length = len("already-")

    chunks = list(
        monitor._stream_output_phase(
            "main .result",
            ctx,
            completion_id="completion-test",
        )
    )

    assert chunks == ["completion-test:final-tail"]
    assert monitor.tab.refresh_calls == []
    assert ctx.max_seen_text == "already-sent-held-tail"


def test_interrupted_recovery_holds_for_reference_without_rendered_image():
    assert StreamMonitor._should_hold_interrupted_image_recovery(
        True,
        current_image_count=0,
        baseline_image_count=0,
        has_rendered_image=False,
    ) is True


def test_interrupted_recovery_releases_after_image_renders():
    assert StreamMonitor._should_hold_interrupted_image_recovery(
        True,
        current_image_count=1,
        baseline_image_count=0,
        has_rendered_image=True,
    ) is False


def test_generic_dom_monitor_does_not_enable_interrupted_recovery():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = type("Tab", (), {"url": "https://arena.ai/c/example"})()
    monitor._expect_image_output = True
    monitor._network_fallback_reason = ""

    assert monitor._uses_interrupted_image_recovery() is False


def test_non_arena_pages_do_not_enable_interrupted_stream_recovery():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = type("Tab", (), {"url": "https://example.test/chat"})()
    monitor._network_fallback_reason = "目标流未完整结束（6.3s）"

    assert monitor._uses_interrupted_stream_recovery() is False


def test_non_arena_pages_skip_interrupted_recovery_refresh(monkeypatch):
    monitor = StreamMonitor.__new__(StreamMonitor)
    refresh_calls = []
    monitor.tab = type(
        "Tab",
        (),
        {
            "url": "https://example.test/chat",
            "refresh": lambda self, **kwargs: refresh_calls.append(kwargs),
        },
    )()

    assert monitor._refresh_interrupted_stream_page() is False
    assert refresh_calls == []


def test_arena_pages_enable_interrupted_stream_recovery():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = type("Tab", (), {"url": "https://arena.ai/c/example"})()
    monitor._network_fallback_reason = "目标流未完整结束（6.3s）"

    assert monitor._uses_interrupted_stream_recovery() is True


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


def test_interrupted_image_resume_preserves_original_baseline_and_prefetched_url():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._expect_image_output = True
    monitor._prefetched_image_urls = {"https://example.test/generated.png"}
    monitor._final_image_urls = []
    ctx = StreamContext()
    ctx.baseline_snapshot = {
        "groups_count": 2,
        "text_len": 58,
        "image_count": 0,
        "image_urls": [],
    }
    ctx.images_detected = True
    ctx.sent_content_length = 19
    ctx.network_sent_content_length = 7
    monitor._stream_ctx = ctx

    state = monitor.capture_interrupted_image_resume_state()

    assert state == {
        "baseline_snapshot": ctx.baseline_snapshot,
        "sent_content_length": 19,
        "image_urls": ["https://example.test/generated.png"],
    }
    assert state["baseline_snapshot"] is not ctx.baseline_snapshot


def test_interrupted_image_resume_starts_dom_recovery_before_image_is_rendered():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._expect_image_output = True
    monitor._prefetched_image_urls = set()
    monitor._final_image_urls = []
    ctx = StreamContext()
    ctx.baseline_snapshot = {
        "groups_count": 2,
        "text_len": 0,
        "image_count": 0,
        "image_urls": [],
    }
    ctx.images_detected = False
    ctx.sent_content_length = 0
    ctx.network_sent_content_length = 0
    monitor._stream_ctx = ctx

    state = monitor.capture_interrupted_image_resume_state()

    assert state == {
        "baseline_snapshot": ctx.baseline_snapshot,
        "sent_content_length": 0,
        "image_urls": [],
    }


def test_interrupted_network_image_resume_uses_pending_send_baseline_without_stream_context():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._image_config = {
        "enabled": True,
        "arena_image_generation": True,
    }
    monitor._image_extraction_enabled = True
    monitor._stream_ctx = None
    monitor._pending_send_baseline = {
        "groups_count": 3,
        "text_len": 0,
        "image_count": 0,
        "_captured_after_send": True,
    }

    state = monitor.capture_interrupted_image_resume_state()

    assert state == {
        "baseline_snapshot": monitor._pending_send_baseline,
        "sent_content_length": 0,
        "image_urls": [],
    }
    assert state["baseline_snapshot"] is not monitor._pending_send_baseline


def test_executor_resumes_prefetched_image_from_dom_without_rebuilding_network():
    class _RecoverableStreamMonitor:
        def __init__(self):
            self.monitor_calls = []
            self.baseline_cleared = False

        def capture_interrupted_image_resume_state(self):
            return {
                "baseline_snapshot": {
                    "groups_count": 2,
                    "text_len": 58,
                    "image_count": 0,
                },
                "sent_content_length": 19,
                "image_urls": ["https://example.test/generated.png"],
            }

        def monitor(self, **kwargs):
            self.monitor_calls.append(kwargs)
            yield "dom-image-result"

        def clear_send_baseline(self):
            self.baseline_cleared = True

    class _NetworkMonitor:
        def __init__(self):
            self.rebuild_calls = []

        def rebuild_after_external_interruption(self, reason):
            self.rebuild_calls.append(reason)

        def monitor(self, **_kwargs):
            raise AssertionError("network monitor must not run for a recoverable image")

    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor._stream_monitor = _RecoverableStreamMonitor()
    executor._network_monitor = _NetworkMonitor()
    executor._pending_interrupted_image_dom_resume = None
    executor._should_stop = lambda: False
    executor._current_step_execution = {}
    executor._stream_mode = "network"
    executor._intercept_only_mode = False
    executor._page_fetch_capture = None
    executor._completion_id = "completion-test"
    executor._last_stream_media_state = {}
    executor._last_stream_media_items = []

    executor.rebuild_network_listener_after_external_interruption(
        "workflow_interrupt_resume",
        allow_dom_image_resume=True,
    )
    chunks = list(
        executor.execute_step(
            action="STREAM_WAIT",
            selector="main .result",
            target_key="result_container",
            context={"prompt": "generate an image"},
        )
    )

    assert chunks == ["dom-image-result"]
    assert executor._network_monitor.rebuild_calls == []
    assert executor._stream_monitor.baseline_cleared is True
    assert executor._stream_monitor.monitor_calls == [
        {
            "selector": "main .result",
            "user_input": "generate an image",
            "completion_id": "completion-test",
            "baseline_snapshot": {
                "groups_count": 2,
                "text_len": 58,
                "image_count": 0,
            },
                "sent_content_length": 19,
                "fallback_reason": "workflow_interrupt_dom_resume",
                "recovery_mode": "workflow_dom_resume",
                "resume_image_urls": ["https://example.test/generated.png"],
        }
    ]


def test_executor_does_not_confirm_exhausted_arena_recovery_after_network_error():
    class _ExhaustedStreamMonitor:
        def monitor(self, **_kwargs):
            if False:
                yield None

        def stream_recovery_exhausted(self):
            return True

        def clear_send_baseline(self):
            raise AssertionError("an exhausted recovery must not be cleared as success")

    class _NetworkMonitor:
        def monitor(self, **_kwargs):
            raise NetworkMonitorError("connection closed")

    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor._stream_monitor = _ExhaustedStreamMonitor()
    executor._network_monitor = _NetworkMonitor()
    executor._pending_interrupted_image_dom_resume = None
    executor._should_stop = lambda: False
    executor._current_step_execution = {}
    executor._stream_mode = "network"
    executor._intercept_only_mode = False
    executor._page_fetch_capture = None
    executor._completion_id = "completion-test"
    executor._last_stream_media_state = {}
    executor._last_stream_media_items = []

    with pytest.raises(WorkflowError, match="stream_recovery_exhausted"):
        list(
            executor.execute_step(
                action="STREAM_WAIT",
                selector="main .result",
                target_key="result_container",
                context={"prompt": "generate an image"},
            )
        )


class _ArenaImageGuardTab:
    url = "https://arena.ai/image"

    def __init__(self, *, stop_present=False, terminal_error=None):
        self.stop_present = stop_present
        self.terminal_error = terminal_error
        self.run_js_script = ""
        self.run_js_args = None

    def ele(self, _selector, timeout=0):
        return object() if self.stop_present else None

    def run_js(self, _script, *_args):
        self.run_js_script = _script
        self.run_js_args = _args
        if _args and _args[0] == ARENA_NATIVE_STOP_SELECTOR:
            return self.stop_present
        return self.terminal_error


class _ArenaImageVisibleStopTab:
    url = "https://arena.ai/image"

    def __init__(self, stop_visible):
        self.stop_visible = stop_visible
        self.stop_script = ""
        self.stop_selector = ""

    def run_js(self, script, *args):
        if args:
            self.stop_script = script
            self.stop_selector = args[0]
            return self.stop_visible
        return None


def _arena_png_bytes(color):
    stream = BytesIO()
    Image.new("RGB", (8, 8), color=color).save(stream, format="PNG")
    return stream.getvalue()


def test_arena_image_guard_requires_native_stop_absent_and_terminal_result():
    tab = _ArenaImageGuardTab(stop_present=True)
    guard = ArenaImageGenerationGuard(tab)

    assert guard.observe(has_new_image=True).is_complete is False
    tab.stop_present = False
    assert guard.observe(has_new_image=False).is_complete is False
    assert guard.observe(has_new_image=True).is_complete is True


def test_arena_image_guard_does_not_complete_while_native_stop_is_visible():
    tab = _ArenaImageVisibleStopTab(stop_visible=True)
    observation = ArenaImageGenerationGuard(tab).observe(has_new_image=True)

    assert observation.is_complete is False
    assert tab.stop_selector == ARENA_NATIVE_STOP_SELECTOR
    assert "getBoundingClientRect" in tab.stop_script
    assert "getComputedStyle" in tab.stop_script


def test_arena_image_guard_allows_completion_after_native_stop_is_hidden():
    tab = _ArenaImageVisibleStopTab(stop_visible=False)
    observation = ArenaImageGenerationGuard(tab).observe(has_new_image=True)

    assert observation.is_complete is True


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"code": ARENA_PROMPT_REJECTED_CODE}, ARENA_PROMPT_REJECTED_CODE),
        ({"code": ARENA_IMAGE_GENERATION_FAILED_CODE}, ARENA_IMAGE_GENERATION_FAILED_CODE),
    ],
)
def test_arena_image_guard_maps_terminal_errors_to_non_retryable_422(payload, code):
    tab = _ArenaImageGuardTab(stop_present=False, terminal_error=payload)
    observation = ArenaImageGenerationGuard(
        tab,
        result_selector="main .result",
    ).observe(has_new_image=False)

    assert observation.is_complete is True
    assert observation.terminal_error.code == code
    assert observation.terminal_error.status_code == 422
    assert observation.terminal_error.retryable is False
    assert tab.run_js_args == ("main .result", "", "")


def test_arena_image_guard_selects_latest_result_by_visual_position():
    tab = _ArenaImageGuardTab(stop_present=False, terminal_error=None)

    ArenaImageGenerationGuard(
        tab,
        result_selector="main .result",
    ).detect_terminal_error()

    assert "candidates[candidates.length - 1]" not in tab.run_js_script
    assert "getBoundingClientRect().bottom" in tab.run_js_script


def test_arena_result_baseline_marks_existing_response_nodes():
    class _Tab:
        def __init__(self):
            self.script = ""
            self.args = ()

        def run_js(self, script, *args):
            self.script = script
            self.args = args
            return {"ok": True, "node_count": 4, "marked_count": 4}

    tab = _Tab()
    baseline = capture_arena_result_baseline(tab, "main .result")

    assert baseline["node_count"] == 4
    assert baseline["marked_count"] == 4
    assert baseline["property"] == ARENA_RESULT_BASELINE_PROPERTY
    assert tab.args == (
        "main .result",
        baseline["token"],
        ARENA_RESULT_BASELINE_PROPERTY,
    )
    assert "marker.text" not in tab.script
    assert "text: normalize" in tab.script


def test_arena_image_guard_filters_unchanged_baseline_results():
    tab = _ArenaImageGuardTab(stop_present=False, terminal_error=None)

    ArenaImageGenerationGuard(
        tab,
        result_selector="main .result",
        baseline_token="request-token",
        baseline_property=ARENA_RESULT_BASELINE_PROPERTY,
    ).detect_terminal_error()

    assert tab.run_js_args == (
        "main .result",
        "request-token",
        ARENA_RESULT_BASELINE_PROPERTY,
    )
    assert "marker.token !== baselineToken" in tab.run_js_script
    assert "normalize(marker.text)" in tab.run_js_script


def test_arena_image_guard_uses_twenty_second_refresh_default():
    assert ArenaImageGenerationGuard.refresh_interval_seconds({}) == 20.0
    assert ArenaImageGenerationGuard.refresh_interval_seconds(
        {"dom_image_interrupted_refresh_interval_seconds": 30}
    ) == 20.0
    assert ArenaImageGenerationGuard.max_refreshes(
        {"arena_image_max_refreshes": 3}, 300
    ) == 3
    assert ArenaImageGenerationGuard.max_refreshes({}, 300) == 16


def test_arena_image_guard_scope_excludes_normal_uploaded_image_chat():
    image_config = {"enabled": True, "modalities": {"image": {"enabled": True}}}

    assert is_arena_image_generation_request(
        "https://arena.ai/image", "edit this", image_config, ["reference.png"]
    ) is False
    assert is_arena_image_generation_request(
        "https://arena.ai/c/example", "describe this", image_config, ["reference.png"]
    ) is False
    assert is_arena_image_generation_request(
        "https://example.test/image", "generate image", image_config, []
    ) is False
    assert is_arena_page_url("https://notarena.ai.example.test/image") is False


def test_arena_image_guard_requires_explicit_template_marker():
    image_config = {
        "enabled": True,
        "arena_image_generation": True,
        "modalities": {"image": {"enabled": True}},
    }

    assert is_arena_image_generation_request(
        "https://arena.ai/c/example", "describe this", image_config, []
    ) is True
    assert is_arena_image_generation_request(
        "https://example.test/image", "generate image", image_config, []
    ) is False


def test_arena_image_to_image_runtime_flag_does_not_require_prompt_marker():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._image_config = {
        "enabled": True,
        "_arena_image_generation_active": True,
    }
    monitor._image_extraction_enabled = True

    assert monitor._looks_like_expected_image_output("make it brighter") is True


def test_stream_monitor_requires_explicit_image_template_marker():
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._image_config = {
        "enabled": True,
        "modalities": {"image": {"enabled": True}},
    }
    monitor._image_extraction_enabled = True

    assert monitor._looks_like_expected_image_output("generate an image") is False

    monitor._image_config["arena_image_generation"] = True
    assert monitor._looks_like_expected_image_output("ordinary text request") is True


def test_arena_reused_uploaded_image_is_non_retryable(monkeypatch):
    images = {"uploaded": _arena_png_bytes("red")}
    monkeypatch.setattr(
        arena_image_generation,
        "_read_local_image",
        lambda path: images.get(str(path), b""),
    )

    with pytest.raises(ArenaImageGenerationError) as raised:
        validate_generated_images(
            ["uploaded"],
            [{"media_type": "image", "local_path": "uploaded"}],
        )

    assert raised.value.code == ARENA_IMAGE_UNCHANGED_CODE
    assert raised.value.status_code == 422
    assert raised.value.retryable is False


def test_arena_different_generated_image_is_accepted(monkeypatch):
    images = {
        "uploaded": _arena_png_bytes("red"),
        "generated": _arena_png_bytes("blue"),
    }
    monkeypatch.setattr(
        arena_image_generation,
        "_read_local_image",
        lambda path: images.get(str(path), b""),
    )

    validate_generated_images(
        ["uploaded"],
        [{"media_type": "image", "local_path": "generated"}],
    )


def test_arena_last_allowed_refresh_is_observed_before_recovery_ends(monkeypatch):
    clock = [0.0]

    def fake_time():
        clock[0] += 1.0
        return clock[0]

    def snapshot(*, image_count=0, page_reference=False):
        image_urls = ["https://arena.ai/generated.png"] if image_count else []
        page_image_urls = ["https://arena.ai/pending.png"] if page_reference else []
        return {
            "groups_count": 1,
            "anchor": "reply",
            "text": "",
            "text_len": 0,
            "is_generating": False,
            "image_count": image_count,
            "has_images": bool(image_count),
            "image_urls": image_urls,
            "image_references": image_urls,
            "page_image_urls": page_image_urls,
            "page_image_references": page_image_urls,
        }

    tab = _ArenaImageGuardTab(stop_present=False)
    tab.refresh_calls = []
    tab.refresh = lambda **kwargs: tab.refresh_calls.append(kwargs)

    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = tab
    monitor.formatter = object()
    monitor._should_stop = lambda: False
    monitor._hard_timeout = 300
    monitor._image_config = {
        "enabled": True,
        "modalities": {"image": {"enabled": True}},
        "arena_image_refresh_interval_seconds": 1,
        "arena_image_max_refreshes": 1,
    }
    monitor._expect_image_output = True
    monitor._network_fallback_reason = "workflow_interrupt_dom_resume"
    monitor._recovery_mode = "workflow_dom_resume"
    monitor._stream_recovery_exhausted = False
    monitor._image_recovery_exhausted = False
    monitor._generating_checker = None
    monitor._arena_image_guard = ArenaImageGenerationGuard(tab)
    monitor._prefetch_snapshot_image_urls = lambda _snapshot: None
    snapshots = iter([
        snapshot(),
        snapshot(page_reference=True),
        snapshot(image_count=1),
    ])
    monitor._get_snapshot_prefer_anchor = lambda *_args: next(snapshots)
    monitor._final_settle_and_output = (
        lambda *_args, **_kwargs: iter(["final-image-result"])
    )

    ctx = StreamContext()
    ctx.baseline_snapshot = snapshot()
    ctx.baseline_image_count = 0
    ctx.baseline_image_references = set()

    monkeypatch.setattr("app.core.stream_monitor.time.time", fake_time)
    monkeypatch.setattr("app.core.stream_monitor.time.sleep", lambda _seconds: None)

    assert list(monitor._stream_output_phase("main", ctx)) == ["final-image-result"]
    assert tab.refresh_calls == [{"ignore_cache": True}]
    assert monitor.stream_recovery_exhausted() is False


def test_text_recovery_avoids_premature_exit_with_historical_bubbles(monkeypatch):
    class FakeTab:
        def __init__(self):
            self.refresh_calls = []
            self.url = "https://arena.ai/chat"
        def refresh(self, **kwargs):
            self.refresh_calls.append(kwargs)

    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor.tab = FakeTab()
    monitor.formatter = object()
    monitor._should_stop = lambda: False
    monitor._hard_timeout = 300
    monitor._image_config = {
        "enabled": False,
        "modalities": {"image": {"enabled": False}},
        "dom_image_interrupted_refresh_interval_seconds": 1,
    }
    monitor._expect_image_output = False
    monitor._network_fallback_reason = "workflow_interrupt_dom_resume"
    monitor._recovery_mode = "workflow_dom_resume"
    monitor._stream_recovery_exhausted = False
    monitor._image_recovery_exhausted = False
    monitor._generating_checker = None
    monitor._arena_image_guard = None
    # 模拟刷新后的多次探测
    # 第1次调用（initial_snap获取基线）：历史状态有 2 个气泡
    # 第2次调用（第一轮循环开头）：检测到 2 个历史气泡（与基线一致，未就绪，随后触发刷新）
    # 第3次调用（第二轮循环开头）：新气泡产生，检测到 3 个气泡（大于基线，有变化，设置 has_output=True，但本轮因时序保护被防静默防早退机制拦截）
    # 第4次调用（第三轮循环开头）：新气泡无变化（groups_count=3），稳定次数增加，确认已完整生成，顺利安全退出
    snapshots = iter([
        {
            "groups_count": 2,
            "anchor": "reply-history-2",
            "text": "old chat text",
            "text_len": 13,
            "is_generating": False,
        },
        {
            "groups_count": 2,
            "anchor": "reply-history-2",
            "text": "old chat text",
            "text_len": 13,
            "is_generating": False,
        },
        {
            "groups_count": 3,
            "anchor": "reply-new",
            "text": "new reply text",
            "text_len": 14,
            "is_generating": False,
        },
        {
            "groups_count": 3,
            "anchor": "reply-new",
            "text": "new reply text",
            "text_len": 14,
            "is_generating": False,
        }
    ])
    monitor._get_snapshot_prefer_anchor = lambda *_args: next(snapshots)
    monitor._final_settle_and_output = (
        lambda *_args, **_kwargs: iter(["final-text-result"])
    )

    ctx = StreamContext()
    ctx.baseline_snapshot = {
        "groups_count": 2,
        "anchor": "reply-history-2",
        "text": "old chat text",
        "text_len": 13,
    }
    ctx.baseline_image_count = 0
    ctx.baseline_image_references = set()

    # 手动让 stream_recovery_refresh_done=True 以触发就绪判定分支
    # 由于我们在 _stream_output_phase 里第一轮会跳过（继续等待），在第二轮才会因为 groups_count > 2 且 no stop 退出
    # 这里 mock time.time 避免真的触发超时逻辑
    clock = [0.0]
    def fake_time():
        clock[0] += 1.0
        return clock[0]
    monkeypatch.setattr("app.core.stream_monitor.time.time", fake_time)
    monkeypatch.setattr("app.core.stream_monitor.time.sleep", lambda _seconds: None)

    # 手动将已刷新的状态注入
    # 第一次进入 _stream_output_phase 时，我们在 monitor 主逻辑中会进行轮询。
    # 为了模拟 stream_recovery_refresh_done 的条件，我们需要把它在第一个循环周期前或者由代码分支正常更新
    # 实际上，在 _stream_output_phase 的 while 循环中，一开始 stream_recovery_refresh_done 是 False
    # 但是因为我们传入的 fallback_reason="workflow_interrupt_dom_resume" 会被认为是断流恢复
    # 如果它的刷新判定被执行，就会将 stream_recovery_refresh_done 置为 True 并 continue。
    # 这里为了使逻辑精简，我们调用它并验证最终是否能正常通过第二轮的就绪确认返回结果。

    # 因为我们的 _get_snapshot_prefer_anchor 只 mock 了两次，如果没在两轮内退出就会报 StopIteration 错误。
    # 如果正常退出，则证明成功退出了！
    results = list(monitor._stream_output_phase("main", ctx))
    assert results == ["final-text-result"]
