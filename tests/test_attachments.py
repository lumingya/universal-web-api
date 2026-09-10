"""Contract, safety, resource lifetime and upload-side-effect regressions."""
import base64
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from app.utils import attachments as a
from app.core.config import WorkflowError
from app.core.workflow.attachment_upload import AttachmentUploadCoordinator, DispatchState
from app.core.browser.prompt import BrowserPromptMixin
from app.core.browser.workflow import BrowserWorkflowMixin
from app.core.workflow.executor_request_transport import WorkflowExecutorRequestTransportMixin
from app.api.chat import _normalize_response_message_content
from app.api.anthropic_routes import _anthropic_messages_to_openai
from app.services.config.engine import ConfigEngine


def data_uri(data=b"hello", mime="text/plain"):
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def file_part(data=b"hello", filename="notes.txt", mime="text/plain"):
    return {"type": "file", "file": {"filename": filename, "file_data": data_uri(data, mime)}}


def messages(*parts):
    return [{"role": "user", "content": list(parts)}]


@pytest.fixture
def batch(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "ROOT", tmp_path / "inputs")
    value = a.AttachmentBatch()
    yield value
    value.close()


def test_responses_audio_preserves_bytes():
    value = _normalize_response_message_content([{"type": "input_audio", "input_audio": {"format": "wav", "data": "QUJD"}}])
    assert value[0]["file"]["file_data"] == "data:audio/wav;base64,QUJD"
    assert value[0]["file"]["filename"] == "audio.wav"


@pytest.mark.parametrize("part", [
    {"type": "input_file", "filename": "a.pdf", "file_data": data_uri(b"%PDF-1.7", "application/pdf")},
    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0="}},
    {"type": "document", "source": {"type": "url", "url": "https://example.com/a.pdf"}},
    {"type": "audio_url", "audio_url": {"url": "https://example.com/a.mp3"}},
    {"type": "video_url", "video_url": "https://example.com/a.mp4"},
    {"type": "document", "source": {"type": "text", "data": "你好"}},
])
def test_protocol_adapters_keep_attachment_structure(part):
    assert _normalize_response_message_content([part])[0]["type"] == "file"
    assert _anthropic_messages_to_openai(messages(part))[0]["content"][0]["type"] == "file"


@pytest.mark.parametrize("part", [
    {"type": "input_file", "file_id": "file-secret"},
    {"type": "file", "file": {"file_data": "/etc/passwd"}},
    {"type": "image_url", "image_url": {"url": "file:///etc/passwd"}},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,"}},
    {"type": "document", "source": {"type": "file", "file_id": "file-secret"}},
    {"type": "input_audio", "input_audio": {"format": "executable", "data": "AAAA"}},
])
def test_bad_sources_are_explicit_http_errors(part):
    with pytest.raises(HTTPException) as error:
        _normalize_response_message_content([part])
    assert error.value.status_code == 400
    assert error.value.detail["code"].startswith("attachment_")


def test_normalization_does_not_mutate_or_reorder():
    source = messages({"type": "text", "text": "before"}, file_part(), {"type": "text", "text": "after"})
    result = a.normalize_messages(source)
    assert result == source and result is not source
    assert a.normalize_messages(result) == result
    assert BrowserPromptMixin()._extract_text_from_content(source[0]["content"]) == "before [附件1: notes.txt] after"


def test_image_placeholder_keeps_original_position():
    content = [{"type": "text", "text": "前"}, {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}, {"type": "text", "text": "后"}]
    assert BrowserPromptMixin()._extract_text_from_content(content) == "前 [图片1] 后"


def test_history_selection_recognizes_documents():
    old = messages(file_part())[0]
    latest = {"role": "user", "content": "总结"}
    assert BrowserWorkflowMixin._select_image_source_messages([old, latest], False) == [old]


def test_file_input_disables_text_only_request_transport():
    executor = WorkflowExecutorRequestTransportMixin()
    executor._context = {"attachments": [object()]}
    assert executor._context_has_non_text_inputs()


def test_prepared_metadata_and_duplicate_references(batch):
    items = batch.prepare(messages(file_part(filename="../notes.txt"), file_part(filename="../notes.txt")))
    assert len(items) == 2
    assert items[0].sha256 == items[1].sha256
    assert items[0].id != items[1].id and items[0].path != items[1].path
    assert items[0].filename == "notes.txt"
    assert [item.part_index for item in items] == [0, 1]
    assert items[0].path.read_bytes() == b"hello"
    directory = batch.directory
    batch.close()
    assert not directory.exists()


def test_partial_failure_is_atomic_and_cleans_files(batch):
    bad = {"type": "file", "file": {"file_data": "data:text/plain;base64,!!!!"}}
    with pytest.raises(a.AttachmentError) as exc:
        batch.prepare(messages(file_part(), bad))
    assert exc.value.part_index == 1
    assert exc.value.code == "attachment_invalid_base64"
    assert not list(a.ROOT.iterdir())


@pytest.mark.parametrize("config,code", [
    ({"enabled": False}, "attachments_disabled"),
    ({"max_count": 1}, "attachment_count_exceeded"),
    ({"allowed_types": ["application/pdf"]}, "attachment_type_unsupported"),
])
def test_capabilities_reject_instead_of_truncating(batch, config, code):
    with pytest.raises(a.AttachmentError) as exc:
        batch.prepare(messages(file_part(), file_part()), config)
    assert exc.value.code == code


def test_actual_stream_size_limit_and_response_closed(batch, monkeypatch):
    class Response:
        headers = {}  # unknown Content-Length
        closed = False
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            yield b"x" * a.MIB
            yield b"x"
        def close(self): self.closed = True
    response = Response()
    monkeypatch.setattr(a, "get_public_remote_resource", lambda *args, **kw: response)
    with pytest.raises(a.AttachmentError, match="大小"):
        batch.prepare(messages({"type": "file", "file": {"file_url": "https://example.com/a.bin"}}), {"max_file_mb": 1})
    assert response.closed


def test_total_limit_and_cancel_cleanup(batch):
    with pytest.raises(a.AttachmentError) as exc:
        batch.prepare(messages(file_part(b"x" * (a.MIB // 2 + 1)), file_part(b"x" * (a.MIB // 2 + 1))), {"max_total_mb": 1})
    assert exc.value.code == "attachment_size_exceeded"
    with pytest.raises(a.AttachmentError) as exc:
        batch.prepare(messages(file_part()), cancelled=lambda: True)
    assert exc.value.code == "attachment_cancelled"


def test_images_use_real_validation_and_detected_mime(batch):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, "PNG")
    item = batch.prepare(messages({"type": "image_url", "image_url": {"url": data_uri(buffer.getvalue(), "image/jpeg")}}))[0]
    assert item.mime == "image/png" and item.is_image
    assert item.path.suffix == ".png"


def test_fake_image_fails(batch):
    with pytest.raises(a.AttachmentError) as exc:
        batch.prepare(messages({"type": "image_url", "image_url": {"url": data_uri(b"not an image", "image/png")}}))
    assert exc.value.code == "attachment_invalid_image"


def test_active_lease_survives_sweep_then_expires(batch):
    batch.prepare(messages(file_part()))
    directory = batch.directory
    (directory / ".expires").write_text("0")
    a.cleanup_attachment_inputs(now=10**12)
    assert directory.exists()
    batch.may_be_reading = True
    batch.close()
    assert directory.exists()
    a.cleanup_attachment_inputs(now=10**12)
    assert not directory.exists()


def test_generator_close_releases_batch(monkeypatch, tmp_path):
    monkeypatch.setattr(a, "ROOT", tmp_path)
    @a.attachment_scope
    def stream(*, _attachment_batch):
        yield _attachment_batch.prepare(messages(file_part()))[0].path
    gen = stream()
    path = next(gen)
    assert path.exists()
    gen.close()
    assert not path.exists()


def test_config_normalization_roundtrip_and_defaults():
    engine = ConfigEngine.__new__(ConfigEngine)
    old = {"enabled": False, "temp_file_type": "chunk", "threshold": 5000}
    effective = engine._validate_file_paste_config(old, include_attachment_defaults=True)
    assert effective["attachments"]["enabled"] is True
    assert effective["temp_file_type"] == "chunk" and effective["enabled"] is False
    effective["attachments"] = {"max_count": 4, "allowed_types": [".PDF"], "transport_order": []}
    saved = engine._validate_file_paste_config(effective)
    assert saved["attachments"]["transport_order"] == []
    assert saved["attachments"]["allowed_types"] == [".pdf"]
    assert engine._validate_file_paste_config(saved) == saved


class Monitor:
    def __init__(self, ready=True): self.calls, self.ready = [], ready
    def begin_tracking(self, **kw): self.calls.append("begin"); return {"ok": True}
    def wait_until_ready(self, **kw): self.calls.append("wait"); return {"success": self.ready}


class Input:
    def __init__(self, accept="", error=False): self.accept, self.error, self.calls = accept, error, []
    def attr(self, key): return self.accept if key == "accept" else None
    def input(self, path):
        self.calls.append(path)
        if self.error: raise TimeoutError("CDP ack lost after upload")


@pytest.fixture
def upload_file(tmp_path):
    path = tmp_path / "hello.txt"
    path.write_text("hello")
    return path


def coordinator(elements, ready=True, **kw):
    tab = SimpleNamespace(eles=lambda *a, **kw: elements, ele=lambda *a, **kw: None)
    return AttachmentUploadCoordinator(tab, monitor=Monitor(ready), **kw)


def test_file_input_dispatch_does_not_duplicate_events(upload_file):
    first, second = Input("image/*"), Input(".txt")
    uploader = coordinator([first, second])
    assert uploader.upload(upload_file)
    assert not first.calls and len(second.calls) == 1
    assert uploader.last_result.state == DispatchState.DISPATCHED
    assert uploader.monitor.calls == ["begin", "wait"]


@pytest.mark.parametrize("ack_error", [False, True])
def test_unknown_or_unconfirmed_never_reuploads(upload_file, ack_error):
    first, second = Input(error=ack_error), Input()
    uploader = coordinator([first, second], ready=False)
    with pytest.raises(WorkflowError, match="attachment_upload_unconfirmed"):
        uploader.upload(upload_file)
    with pytest.raises(WorkflowError, match="attachment_upload_unconfirmed"):
        uploader.upload(upload_file)
    assert len(first.calls) == 1 and not second.calls
    assert uploader.tainted


def test_lost_ack_can_be_confirmed_without_retry(upload_file):
    target = Input(error=True)
    uploader = coordinator([target])
    assert uploader.upload(upload_file)
    assert len(target.calls) == 1 and not uploader.tainted


def test_disabled_or_empty_transports_do_not_dispatch(upload_file):
    for config, code in [({"enabled": False}, "attachments_disabled"), ({"transport_order": []}, "attachment_transport_unavailable")]:
        target = Input()
        with pytest.raises(WorkflowError, match=code):
            coordinator([target], config=config).upload(upload_file)
        assert not target.calls


def test_missing_monitor_never_dispatches(upload_file):
    target = Input()
    uploader = coordinator([target])
    uploader.monitor = None
    with pytest.raises(WorkflowError, match="attachment_monitor_unavailable"):
        uploader.upload(upload_file)
    assert not target.calls


def test_generated_and_user_files_share_budget(upload_file):
    uploader = coordinator([Input()], config={"max_count": 1})
    uploader.upload(upload_file)
    with pytest.raises(WorkflowError, match="attachment_count_exceeded"):
        uploader.upload(upload_file)


def test_cancelled_upload_never_dispatches(upload_file):
    target = Input()
    with pytest.raises(WorkflowError, match="attachment_cancelled"):
        coordinator([target], cancelled=lambda: True).upload(upload_file)
    assert not target.calls


def test_unknown_binary_block_is_not_text_fallback():
    with pytest.raises(HTTPException):
        _normalize_response_message_content([{"type": "custom_binary", "file_data": "data:application/octet-stream;base64,AAAA"}])


def test_generated_file_lease_and_combined_preflight(batch, upload_file):
    declared = batch.prepare(messages(file_part()))
    uploader = coordinator([Input()], config={"max_count": 1})
    uploader.planned_items = declared
    uploader.resource_batch = batch
    adopted = batch.adopt_generated(upload_file)
    assert adopted.exists() and not upload_file.exists()
    with pytest.raises(WorkflowError, match="attachment_count_exceeded"):
        uploader.upload(adopted)
    assert uploader.monitor.calls == [], "combined limit must fail before dispatch"
    assert not batch.may_be_reading


def test_pdf_generation_never_silently_rasterizes(monkeypatch, tmp_path):
    from app.utils import file_paste
    def fail(*args): raise RuntimeError("font failure")
    monkeypatch.setattr(file_paste, "_write_pdf_with_reportlab", fail)
    with pytest.raises(RuntimeError, match="font failure"):
        file_paste._write_temp_pdf("context", str(tmp_path / "context.pdf"))


def test_long_text_failure_does_not_fall_back_to_plain_text():
    from app.core.workflow.text_input import TextInputHandler
    handler = TextInputHandler.__new__(TextInputHandler)
    handler._should_use_file_paste = lambda text: True
    handler._fill_via_file_paste = lambda ele, text: False
    for method in (handler.fill_via_js, handler.fill_via_clipboard_no_click, handler.fill_via_clipboard):
        with pytest.raises(WorkflowError, match="attachment_prepare_failed"):
            method(None, "context")


def test_observer_loss_does_not_confirm_stale_ready_state(monkeypatch):
    from app.core.workflow import attachment_monitor as module
    clock = [0.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + max(seconds, .01)))
    monitor = module.AttachmentMonitor(SimpleNamespace(), check_cancelled_fn=lambda: False)
    state = {"ok": True, "attachmentObserved": True, "attachmentCount": 1, "freshPreview": True}
    monitor.begin_tracking = lambda *args: dict(state)
    states = iter([dict(state)])
    monitor.snapshot = lambda *args: next(states, {})
    result = monitor.wait_until_ready(max_wait=.5, hard_max_wait=.5, idle_timeout=.5,
                                      poll_interval=.05, stable_window=.02, require_fresh_preview=True)
    assert not result['success']
