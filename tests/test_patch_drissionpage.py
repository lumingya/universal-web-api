from patch_drissionpage import (
    STREAM_CAPTURE_GUARD_MARKER,
    STREAM_CAPTURE_SNIPPET,
    ensure_stream_capture_patch,
    has_stream_capture_patch,
)


def test_stream_capture_patch_is_idempotent():
    original = "class Listener:\n    pass\n"

    patched, added = ensure_stream_capture_patch(original)

    assert added is True
    assert has_stream_capture_patch(patched) is True
    assert STREAM_CAPTURE_GUARD_MARKER in patched

    patched_again, added_again = ensure_stream_capture_patch(patched)

    assert added_again is False
    assert patched_again == patched


def test_stream_capture_snippet_mentions_cdp_stream_hooks():
    assert "Network.streamResourceContent" in STREAM_CAPTURE_SNIPPET
    assert "Network.dataReceived" in STREAM_CAPTURE_SNIPPET
