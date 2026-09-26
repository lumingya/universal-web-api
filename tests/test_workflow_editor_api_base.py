"""页面内工作流编辑器必须回调服务实际监听的端口（APP_PORT），而不是未文档化的 PORT（旧默认 9099）。"""

from __future__ import annotations

from app.core.workflow_editor import WorkflowEditorInjector


class _FakeTab:
    tab_id = "tab-1"

    def __init__(self):
        self.scripts = []

    def run_js(self, script, *args, **kwargs):
        self.scripts.append(script)
        if "location.hostname" in script:
            return "chat.example.com"
        return None


def test_injected_api_base_follows_app_port(monkeypatch):
    monkeypatch.setenv("APP_PORT", "8765")
    monkeypatch.setenv("PORT", "9099")  # 旧变量即便存在也不再生效
    assert WorkflowEditorInjector._api_base() == "http://127.0.0.1:8765"

    tab = _FakeTab()
    WorkflowEditorInjector.inject(tab, site_config={}, target_domain="chat.example.com")
    injected = "\n".join(str(s) for s in tab.scripts)
    assert "__WORKFLOW_EDITOR_API_BASE__" in injected
    assert "http://127.0.0.1:8765" in injected
    assert "127.0.0.1:9099" not in injected


def test_default_port_is_8199(monkeypatch):
    monkeypatch.delenv("APP_PORT", raising=False)
    assert WorkflowEditorInjector._api_base() == "http://127.0.0.1:8199"
