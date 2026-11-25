import json
from app.core.browser.prompt import BrowserPromptMixin
from app.core.browser.workflow import BrowserWorkflowMixin
from app.core.config_parts.browser_constants import BrowserConstants


class _DummyCore(BrowserPromptMixin, BrowserWorkflowMixin):
    pass


def test_upload_history_images_defaults_to_false(tmp_path, monkeypatch):
    cfg_file = tmp_path / "browser_config.json"
    cfg_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(BrowserConstants, "_config", {})
    monkeypatch.setattr(BrowserConstants, "_config_file", cfg_file)

    core = _DummyCore()
    assert core._get_upload_history_images_flag() is False

    # 即使仅落盘修改 config/browser_config.json（未调用 reload），兜底读取也应生效
    cfg_file.write_text(json.dumps({"UPLOAD_HISTORY_IMAGES": True}), encoding="utf-8")
    assert core._get_upload_history_images_flag() is True

    # 显式设为 false 时不应被默认 True 覆盖
    cfg_file.write_text(json.dumps({"UPLOAD_HISTORY_IMAGES": False}), encoding="utf-8")
    assert core._get_upload_history_images_flag(default=True) is False


def test_select_image_source_messages_skips_older_history_when_disabled():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "first"}, {"type": "image_url", "image_url": {"url": "https://example.com/1.png"}}]},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": [{"type": "text", "text": "second"}, {"type": "image_url", "image_url": {"url": "https://example.com/2.png"}}]},
    ]
    selected_disabled = BrowserWorkflowMixin._select_image_source_messages(messages, upload_history=False)
    assert selected_disabled == [messages[2]]

    selected_enabled = BrowserWorkflowMixin._select_image_source_messages(messages, upload_history=True)
    assert selected_enabled == messages
