import asyncio
from types import SimpleNamespace

from app.api import tab_routes
from app.core.browser.workflow import BrowserWorkflowMixin
from app.services.request_manager import RequestContext
from app.utils.tab_route_groups import normalize_route_groups


def _group_payload():
    return {
        "id": "arena-image",
        "name": "Arena image",
        "route_domain": "arena.ai",
        "preset_name": "image-preset",
        "allocation_mode": "round_robin",
        "members": [
            {
                "url": "https://arena.ai/c/image-1",
                "url_token": "token-1",
                "tab_index": 2,
            }
        ],
    }


def test_route_group_normalization_supports_mapping_config_and_rejects_bad_ids():
    normalized = normalize_route_groups({
        "Arena-Image": _group_payload(),
        "bad id": {"members": ["https://arena.ai/c/ignored"]},
    })

    assert [group["id"] for group in normalized] == ["arena-image"]
    assert normalized[0]["preset_name"] == "image-preset"
    assert normalized[0]["members"][0]["tab_index"] == 2


def test_tab_pool_config_persists_and_hot_reloads_route_groups(monkeypatch):
    written = {}
    runtime = {}
    config = {
        "tab_pool": {
            "allocation_mode": "round_robin",
            "enabled_route_methods": ["domain", "route_group"],
        }
    }

    monkeypatch.setattr(tab_routes, "_read_browser_config", lambda: config)
    monkeypatch.setattr(
        tab_routes,
        "_write_browser_config_unlocked",
        lambda payload: written.update(payload),
    )

    class _Pool:
        def apply_runtime_config(self, **kwargs):
            runtime.update(kwargs)

    monkeypatch.setattr(
        tab_routes,
        "get_browser",
        lambda auto_connect=False: SimpleNamespace(tab_pool=_Pool()),
    )

    response = asyncio.run(tab_routes.update_tab_pool_config(
        tab_routes.TabPoolConfigRequest(
            allocation_mode="round_robin",
            enabled_route_methods=["domain", "route_group"],
            route_groups=[_group_payload()],
        ),
        authenticated=True,
    ))

    assert response["route_groups"][0]["id"] == "arena-image"
    assert written["tab_pool"]["route_groups"][0]["preset_name"] == "image-preset"
    assert runtime["route_groups"][0]["members"][0]["url"].endswith("/image-1")


def test_route_group_chat_forces_configured_preset_and_uses_group_execution(monkeypatch):
    captured = {}
    group = _group_payload()
    group["live_member_count"] = 4
    group["idle_member_count"] = 1
    group["busy_member_count"] = 3

    class _Pool:
        @staticmethod
        def get_route_groups_snapshot():
            return [group]

    monkeypatch.setattr(
        tab_routes,
        "get_browser",
        lambda auto_connect=False: SimpleNamespace(tab_pool=_Pool()),
    )
    monkeypatch.setattr(
        tab_routes,
        "_resolve_strict_domain_preset",
        lambda route_domain, preset_name: {
            "domain": route_domain,
            "preset_name": preset_name,
        },
    )

    context = RequestContext(request_id="req-group")

    class _RequestManager:
        @staticmethod
        def create_request(*args, **kwargs):
            return context

        @staticmethod
        def record_request_input(ctx, payload, **metadata):
            captured["metadata"] = metadata
            captured["payload"] = payload

    monkeypatch.setattr(tab_routes, "request_manager", _RequestManager())

    async def fake_chat(request, body, ctx, **kwargs):
        captured["body"] = body
        captured["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr(tab_routes, "_chat_with_route_domain", fake_chat)
    body = tab_routes.ChatRequest(
        model="web-browser",
        messages=[{"role": "user", "content": "hello"}],
        preset_name="wrong-preset",
    )

    result = asyncio.run(tab_routes.chat_with_route_group(
        group_id="arena-image",
        request=SimpleNamespace(),
        body=body,
        preset_name=None,
        authenticated=True,
    ))

    assert result == {"ok": True}
    assert captured["body"].preset_name == "image-preset"
    assert captured["kwargs"]["route_group_id"] == "arena-image"
    assert captured["kwargs"]["route_domain"] == "arena.ai"
    assert (
        captured["kwargs"]["resolved_headers"][
            "X-Route-Group-Live-Member-Count"
        ]
        == "4"
    )
    assert captured["kwargs"]["resolved_headers"][
        "X-Route-Group-Idle-Member-Count"
    ] == "1"
    assert captured["kwargs"]["resolved_headers"][
        "X-Route-Group-Busy-Member-Count"
    ] == "3"
    assert captured["metadata"]["route_group"] == "arena-image"


def test_route_group_headers_include_group_and_resolved_domain():
    headers = tab_routes._build_tab_resolution_headers(
        None,
        route_domain="arena.ai",
        route_group="arena-image",
        selector="round_robin",
        route_group_live_member_count=4,
        route_group_idle_member_count=1,
        route_group_busy_member_count=3,
    )

    assert headers["X-Requested-Route-Group"] == "arena-image"
    assert headers["X-Resolved-Route-Group"] == "arena-image"
    assert headers["X-Resolved-Route-Domain"] == "arena.ai"
    assert headers["X-Route-Group-Live-Member-Count"] == "4"
    assert headers["X-Route-Group-Idle-Member-Count"] == "1"
    assert headers["X-Route-Group-Busy-Member-Count"] == "3"


def test_route_group_models_exposes_current_member_counts(monkeypatch):
    group = _group_payload()
    group["live_member_count"] = 4
    group["idle_member_count"] = 1
    group["busy_member_count"] = 3

    class _Pool:
        @staticmethod
        def get_route_groups_snapshot():
            return [group]

        @staticmethod
        def get_tabs_with_index():
            return []

    monkeypatch.setattr(
        tab_routes,
        "get_browser",
        lambda auto_connect=False: SimpleNamespace(tab_pool=_Pool()),
    )

    response = asyncio.run(tab_routes.list_models_with_route_group(
        group_id="arena-image",
        authenticated=True,
    ))

    assert response.headers["X-Route-Group-Live-Member-Count"] == "4"
    assert response.headers["X-Route-Group-Idle-Member-Count"] == "1"
    assert response.headers["X-Route-Group-Busy-Member-Count"] == "3"


def test_route_group_models_exposes_catalog_and_custom_renamed_models(monkeypatch):
    group = _group_payload()
    group["members"] = [
        {"url": "https://arena.ai/c/1", "url_token": "token-1", "tab_index": 1},
        {"url": "https://arena.ai/c/2", "url_token": "token-2", "tab_index": 2},
        {"url": "https://arena.ai/c/3", "url_token": "token-3", "tab_index": 3},
    ]

    tabs = [
        {"persistent_index": 1, "url": "https://arena.ai/c/1", "url_route_token": "token-1", "current_domain": "arena.ai", "exposed_model_name": "arena.ai", "model_name_override_source": ""},
        {"persistent_index": 2, "url": "https://arena.ai/c/2", "url_route_token": "token-2", "current_domain": "arena.ai", "exposed_model_name": "arena.ai", "model_name_override_source": ""},
        {"persistent_index": 3, "url": "https://arena.ai/c/3", "url_route_token": "token-3", "current_domain": "arena.ai", "exposed_model_name": "111", "model_name_override_source": "tab"},
    ]

    class _Pool:
        @staticmethod
        def get_route_groups_snapshot():
            return [group]

        @staticmethod
        def get_tabs_with_index():
            return tabs

    monkeypatch.setattr(
        tab_routes,
        "get_browser",
        lambda auto_connect=False: SimpleNamespace(tab_pool=_Pool()),
    )
    monkeypatch.setattr(
        tab_routes,
        "get_arena_direct_catalog_for_tab",
        lambda config_engine, tab, preset_name=None: {
            "catalog": {"modality": "image", "enabled": True}
        },
    )
    monkeypatch.setattr(
        tab_routes,
        "list_arena_direct_models",
        lambda browser, catalog_config=None: [
            {"arena_model_id": "m1", "name": "gpt-image-2 (medium)", "display_name": "gpt-image-2 (medium)"},
            {"arena_model_id": "m2", "name": "mona-lisa-1", "display_name": "mona-lisa-1"},
        ],
    )

    response = asyncio.run(tab_routes.list_models_with_route_group(
        group_id="arena-image",
        authenticated=True,
    ))

    import json
    data = json.loads(response.body)
    model_ids = [m["id"] for m in data["data"]]
    assert "gpt-image-2 (medium)" in model_ids
    assert "mona-lisa-1" in model_ids
    assert "111" in model_ids
    # 确保默认的 arena.ai 域名未被当作自定义模型暴露
    assert "arena.ai" not in model_ids


def test_tab_pool_manager_session_override_model_name_resolution():
    from app.core.tab_pool_parts.manager import TabPoolManager
    from app.core.tab_pool_parts.session import TabSession

    manager = TabPoolManager.__new__(TabPoolManager)
    manager.model_name_overrides = {
        "urls": {"https://arena.ai/c/url-override": "url-custom-model"},
        "sites": {"custom.site": "site-custom-model"},
    }

    # 1. 显式 TabSession.model_name_override
    session1 = TabSession.__new__(TabSession)
    session1.model_name_override = "111"
    session1.get_cached_route_snapshot = lambda: ("https://arena.ai/c/1", "arena.ai")
    assert manager._get_session_override_model_name(session1) == "111"

    # 2. URL 级别 override
    session2 = TabSession.__new__(TabSession)
    session2.model_name_override = None
    session2.get_cached_route_snapshot = lambda: ("https://arena.ai/c/url-override", "arena.ai")
    assert manager._get_session_override_model_name(session2) == "url-custom-model"

    # 3. Site 级别 override
    session3 = TabSession.__new__(TabSession)
    session3.model_name_override = None
    session3.get_cached_route_snapshot = lambda: ("https://custom.site/chat", "custom.site")
    assert manager._get_session_override_model_name(session3) == "site-custom-model"

    # 4. 无 override
    session4 = TabSession.__new__(TabSession)
    session4.model_name_override = None
    session4.get_cached_route_snapshot = lambda: ("https://arena.ai/c/normal", "arena.ai")
    assert manager._get_session_override_model_name(session4) == ""


def test_browser_workflow_route_group_binds_and_releases_selected_member():
    events = []
    session = SimpleNamespace(id="arena-2")

    class _Pool:
        @staticmethod
        def acquire_by_route_group(group_id, task_id, timeout, allocation_mode, requested_model=None):
            events.append(("acquire", group_id, task_id, timeout, allocation_mode, requested_model))
            return session

    workflow = BrowserWorkflowMixin()
    workflow.tab_pool = _Pool()
    workflow.formatter = SimpleNamespace()
    workflow._should_stop_checker = lambda: False
    workflow._bind_request_tab_id = lambda task_id, selected: events.append(("bind", task_id, selected.id))
    workflow._build_task_ownership_stop_checker = lambda *_args: (lambda: False)
    workflow._execute_workflow_stream = lambda selected, messages, **kwargs: iter(["chunk"])
    workflow._release_workflow_session = lambda selected, **kwargs: events.append(("release", selected.id))

    chunks = list(workflow.execute_workflow_for_route_group(
        "arena-image",
        [{"role": "user", "content": "hello"}],
        task_id="req-group",
        preset_name="image-preset",
        allocation_mode="round_robin",
        requested_model="111",
    ))

    assert chunks == ["chunk"]
    assert events[0] == ("acquire", "arena-image", "req-group", 60, "round_robin", "111")
    assert ("bind", "req-group", "arena-2") in events
    assert events[-1] == ("release", "arena-2")
