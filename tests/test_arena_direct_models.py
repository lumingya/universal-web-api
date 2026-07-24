import contextlib
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.arena_direct_models as arena_direct_models
from app.api import chat as chat_api
from app.api import tab_routes as tab_routes_api
from app.api.config_route_models import _normalize_preset_config_payload
from app.core.workflow.executor_actions import WorkflowExecutorActionMixin
from app.services.arena_direct_models import (
    ARENA_DIRECT_MODEL_PREFIX,
    build_arena_direct_model_id,
    build_openai_model_entries,
    get_arena_direct_catalog_for_tab,
    get_arena_direct_model_public_id,
    get_model_catalog_preset,
    is_arena_direct_model_id,
    list_arena_direct_models,
    match_arena_direct_model,
    normalize_model_catalog_config,
    parse_arena_direct_model_id,
    read_arena_direct_models_from_tab,
    resolve_arena_direct_model,
)
from app.utils.model_routing import inspect_model_route


MODEL_UUID = "019c6d29-a30c-7e20-9bd0-6650af926623"
MODEL_TRIGGER_SELECTOR = 'button[aria-haspopup="dialog"]:has(span.flex-1.truncate.text-left)'


class _CatalogTab:
    def run_js(self, script, timeout=None):
        assert '"initialModels":' in script
        assert timeout == 3.0
        return [
            {
                "arena_model_id": MODEL_UUID,
                "name": "claude-sonnet-4-6-vertex",
                "public_name": "claude-sonnet-4-6",
                "display_name": "claude-sonnet-4-6",
                "provider": "googleVertexAnthropic",
                "organization": "anthropic",
            },
            {
                "arena_model_id": MODEL_UUID,
                "name": "duplicate-id",
                "public_name": "duplicate",
            },
            {
                "arena_model_id": "second-id",
                "name": "claude-sonnet-4-6-vertex",
                "public_name": "duplicate-name",
            },
            {"arena_model_id": "", "name": "invalid"},
        ]


class _DirectSession:
    status = SimpleNamespace(value="idle")
    persistent_index = 1
    tab = _CatalogTab()

    @staticmethod
    def get_cached_route_snapshot():
        return "https://arena.ai/text/direct", "arena.ai"


class _DirectBrowser:
    class _TabPool:
        @staticmethod
        def get_sessions_snapshot():
            return [_DirectSession()]

    tab_pool = _TabPool()


def test_arena_direct_model_ids_are_stable_and_route_through_arena():
    model_id = build_arena_direct_model_id(MODEL_UUID)

    assert model_id == f"{ARENA_DIRECT_MODEL_PREFIX}{MODEL_UUID}"
    assert parse_arena_direct_model_id(model_id.upper()) == MODEL_UUID.upper()
    assert is_arena_direct_model_id(model_id)

    route = inspect_model_route(
        model_id,
        [
            {
                "current_domain": "arena.ai",
                "route_domain": "arena.ai",
                "exposed_model_name": "arena.ai",
            }
        ],
    )
    assert route["route_domain"] == "arena.ai"
    assert route["match_type"] == "prefix"


def test_catalog_normalization_deduplicates_and_builds_openai_entries():
    models = read_arena_direct_models_from_tab(_CatalogTab())

    assert models == [
        {
            "arena_model_id": MODEL_UUID,
            "name": "claude-sonnet-4-6-vertex",
            "public_name": "claude-sonnet-4-6",
            "display_name": "claude-sonnet-4-6",
            "search_name": "claude-sonnet-4-6",
            "aliases": ["claude-sonnet-4-6-vertex", "claude-sonnet-4-6"],
            "provider": "googleVertexAnthropic",
            "organization": "anthropic",
        }
    ]


def test_local_alias_overrides_are_applied_to_search_and_resolution(tmp_path, monkeypatch):
    override_path = tmp_path / "arena_model_aliases.local.json"
    override_path.write_text(
        json.dumps(
            {
                "models": {
                    "claude-sonnet-4-6-vertex": {
                        "search_name": "claude-visible",
                        "aliases": ["legacy-claude"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(arena_direct_models, "ARENA_MODEL_ALIAS_OVERRIDES_PATH", override_path)
    monkeypatch.setattr(arena_direct_models, "_cache_snapshot", lambda: (10**12, []))
    monkeypatch.setattr(arena_direct_models, "_replace_cache", lambda models: models)

    models = read_arena_direct_models_from_tab(_CatalogTab())
    resolved = resolve_arena_direct_model(_CatalogTab(), "legacy-claude")

    assert models[0]["search_name"] == "claude-visible"
    assert "legacy-claude" in models[0]["aliases"]
    assert resolved["name"] == "claude-sonnet-4-6-vertex"

    entries = build_openai_model_entries(models, created=123)
    assert entries == [
        {
            "id": "claude-visible",
            "object": "model",
            "type": "model",
            "created": 123,
            "owned_by": "anthropic",
            "display_name": "claude-sonnet-4-6",
        }
    ]


def test_catalog_filters_by_readable_model_metadata(monkeypatch):
    models = [
        {
            "arena_model_id": "glm-id",
            "name": "glm-5.2",
            "public_name": "GLM 5.2",
            "display_name": "GLM 5.2",
            "provider": "zhipu",
            "organization": "zhipu",
        },
        {
            "arena_model_id": "image-id",
            "name": "glm-image-preview",
            "public_name": "GLM Image Preview",
            "display_name": "GLM Image Preview",
            "provider": "zhipu",
            "organization": "zhipu",
        },
    ]
    monkeypatch.setattr(
        "app.services.arena_direct_models._cache_snapshot",
        lambda: (10**12, models),
    )

    filtered = list_arena_direct_models(
        _DirectBrowser(),
        catalog_config={
            "enabled": True,
            "include_keywords": ["glm"],
            "exclude_keywords": ["image"],
        },
    )

    assert [item["name"] for item in filtered] == ["glm-5.2"]


def test_cached_catalog_is_hidden_without_an_active_arena_direct_session(monkeypatch):
    monkeypatch.setattr(
        "app.services.arena_direct_models._cache_snapshot",
        lambda: (
            10**12,
            [{"arena_model_id": "jaguar-id", "name": "jaguar"}],
        ),
    )

    assert list_arena_direct_models(object()) == []


def test_plain_mapping_name_resolves_to_private_arena_uuid(monkeypatch):
    models = [
        {
            "arena_model_id": MODEL_UUID,
            "name": "glm-5.2",
            "public_name": "GLM 5.2",
            "display_name": "GLM 5.2",
            "provider": "zhipu",
            "organization": "zhipu",
        }
    ]
    monkeypatch.setattr(
        "app.services.arena_direct_models._cache_snapshot",
        lambda: (10**12, models),
    )

    resolved = resolve_arena_direct_model(object(), "glm-5.2")

    assert resolved["arena_model_id"] == MODEL_UUID
    assert resolved["name"] == "glm-5.2"


def test_visible_search_name_is_exported_while_internal_name_remains_compatible(monkeypatch):
    models = [
        {
            "arena_model_id": "jaguar-id",
            "name": "jaguar",
            "public_name": "mistral-large-3",
            "display_name": "mistral-large-3",
            "search_name": "mistral-large-3",
            "aliases": ["jaguar", "mistral-large-3"],
            "provider": "mistral",
            "organization": "mistral",
        }
    ]
    monkeypatch.setattr(
        "app.services.arena_direct_models._cache_snapshot",
        lambda: (10**12, models),
    )

    entries = build_openai_model_entries(models, created=123)

    assert get_arena_direct_model_public_id(models[0]) == "mistral-large-3"
    assert entries[0]["id"] == "mistral-large-3"
    assert match_arena_direct_model(models, "mistral-large-3")["name"] == "jaguar"
    assert resolve_arena_direct_model(object(), "jaguar")["name"] == "jaguar"


def test_catalog_preset_is_discovered_from_config_instead_of_fixed_name():
    class _ConfigEngine:
        sites = {
            "arena.ai": {
                "presets": {
                    "anything-user-defined": {
                        "model_catalog": {
                            "enabled": True,
                            "source": "arena_direct",
                            "exclude_keywords": "image, preview",
                        }
                    }
                }
            }
        }

        @staticmethod
        def refresh_if_changed():
            return None

    result = get_model_catalog_preset(_ConfigEngine(), "arena.ai")

    assert result["preset_name"] == "anything-user-defined"
    assert result["catalog"] == normalize_model_catalog_config(
        {
            "enabled": True,
            "source": "arena_direct",
            "exclude_keywords": "image, preview",
        }
    )


def test_tab_catalog_requires_live_direct_page_and_enabled_effective_preset():
    class _ConfigEngine:
        presets = {
            "direct": {
                "model_catalog": {
                    "enabled": True,
                    "source": "arena_direct",
                }
            },
            "disabled": {
                "model_catalog": {
                    "enabled": False,
                    "source": "arena_direct",
                }
            },
        }

        @staticmethod
        def refresh_if_changed():
            return None

        @staticmethod
        def get_default_preset(_domain):
            return "direct"

        def _get_site_data_readonly(self, _domain, preset_name=None):
            return self.presets.get(preset_name or "direct")

    config_engine = _ConfigEngine()
    direct_tab = {
        "status": "idle",
        "url": "https://arena.ai/text/direct",
        "preset_name": None,
        "terminating": False,
    }

    result = get_arena_direct_catalog_for_tab(config_engine, direct_tab)

    assert result["preset_name"] == "direct"
    assert result["catalog"]["enabled"] is True
    assert get_arena_direct_catalog_for_tab(
        config_engine,
        {**direct_tab, "status": "closed"},
    ) is None
    assert get_arena_direct_catalog_for_tab(
        config_engine,
        {**direct_tab, "terminating": True},
    ) is None
    assert get_arena_direct_catalog_for_tab(
        config_engine,
        {**direct_tab, "url": "https://arena.ai/"},
    ) is None
    assert get_arena_direct_catalog_for_tab(
        config_engine,
        {**direct_tab, "url": "https://gemini.google.com/app"},
    ) is None
    assert get_arena_direct_catalog_for_tab(
        config_engine,
        {**direct_tab, "preset_name": "disabled"},
    ) is None


def test_preset_config_normalizes_catalog_keyword_text():
    normalized = _normalize_preset_config_payload(
        {
            "selectors": {},
            "workflow": [],
            "model_catalog": {
                "enabled": True,
                "source": "arena_direct",
                "include_keywords": "glm, claude\nglm",
                "exclude_keywords": "image\npreview",
            },
        }
    )

    assert normalized["model_catalog"] == {
        "enabled": True,
        "source": "arena_direct",
        "include_keywords": ["glm", "claude"],
        "exclude_keywords": ["image", "preview"],
    }


def test_global_model_list_merges_arena_direct_models(monkeypatch):
    class _TabPool:
        @staticmethod
        def get_tabs_with_index():
            return [
                {
                    "status": "idle",
                    "url": "https://arena.ai/text/direct",
                    "preset_name": "direct",
                    "current_domain": "arena.ai",
                    "route_domain": "arena.ai",
                    "exposed_model_name": "arena.ai",
                }
            ]

    class _Browser:
        tab_pool = _TabPool()

    monkeypatch.setattr(chat_api, "get_browser", lambda auto_connect=False: _Browser())
    monkeypatch.setattr(
        chat_api,
        "get_arena_direct_catalog_for_tab",
        lambda _config_engine, _tab, preset_name=None: {"catalog": {"enabled": True}},
    )
    monkeypatch.setattr(
        chat_api,
        "list_arena_direct_models",
        lambda _browser, catalog_config=None: [
            {
                "arena_model_id": MODEL_UUID,
                "name": "claude-sonnet-4-6-vertex",
                "public_name": "claude-sonnet-4-6",
                "display_name": "claude-sonnet-4-6",
                "organization": "anthropic",
            }
        ],
    )

    entries = chat_api._collect_model_entries()

    assert any(item["id"] == "claude-sonnet-4-6" for item in entries)
    assert not any(item["id"].startswith(ARENA_DIRECT_MODEL_PREFIX) for item in entries)
    assert not {"arena", "arena.ai", "lmarena", "lmarena.ai", "www.arena.ai"}.intersection(
        item["id"] for item in entries
    )


def test_global_chat_routes_plain_catalog_model_to_arena(monkeypatch):
    class _TabPool:
        @staticmethod
        def get_tabs_with_index():
            return [
                {
                    "persistent_index": 7,
                    "status": "idle",
                    "url": "https://arena.ai/text/direct",
                    "preset_name": "direct",
                    "current_domain": "arena.ai",
                    "route_domain": "arena.ai",
                }
            ]

    class _Browser:
        tab_pool = _TabPool()

    monkeypatch.setattr(chat_api, "get_browser", lambda auto_connect=False: _Browser())
    monkeypatch.setattr(
        chat_api,
        "get_arena_direct_catalog_for_tab",
        lambda _config_engine, _tab, preset_name=None: {"catalog": {"enabled": True}},
    )
    monkeypatch.setattr(
        chat_api,
        "list_arena_direct_models",
        lambda _browser, catalog_config=None: [
            {
                "name": "jaguar",
                "search_name": "mistral-large-3",
                "aliases": ["jaguar", "mistral-large-3"],
            }
        ],
    )
    route_logs = []
    monkeypatch.setattr(chat_api.logger, "info", route_logs.append)

    routed = {}

    async def _route(**kwargs):
        routed.update(kwargs)
        return {"route_domain": kwargs["route_domain"], "model": kwargs["body"].model}

    monkeypatch.setattr(tab_routes_api, "chat_with_route_domain", _route)

    result = asyncio.run(
        chat_api.chat_completions(
            request=SimpleNamespace(),
            body=chat_api.ChatRequest(
                model="mistral-large-3",
                messages=[{"role": "user", "content": "hello"}],
            ),
            authenticated=True,
        )
    )

    assert result == {"route_domain": "arena.ai", "model": "mistral-large-3"}
    assert routed["tab_index"] == 7
    matched_log = next(item for item in route_logs if item.startswith("模型路由命中:"))
    assert "matched_id='mistral-large-3'" in matched_log
    assert "available=['mistral-large-3']" in matched_log
    assert "'arena.ai'" not in matched_log


def test_exposed_model_route_ignores_tabs_excluded_from_dynamic_routing():
    class _TabPool:
        excluded_urls = [
            "https://arena.ai/c/one",
            "https://arena.ai/c/two",
            "https://arena.ai/c/three",
        ]

        @staticmethod
        def get_tabs_with_index():
            return [
                {
                    "persistent_index": index,
                    "status": "idle",
                    "url": f"https://arena.ai/c/{name}",
                    "current_domain": "arena.ai",
                    "exposed_model_name": "arena.ai",
                }
                for index, name in enumerate(("one", "two", "three", "four"), 1)
            ]

    browser = SimpleNamespace(tab_pool=_TabPool())
    assert [
        item["persistent_index"]
        for item in tab_routes_api._get_tabs_by_exposed_model_name(browser, "arena.ai")
    ] == [4]


def test_sillytavern_models_aliases_are_registered():
    paths = {route.path for route in tab_routes_api.router.routes}

    assert "/url/{route_domain}/models" in paths
    assert "/url/{route_domain}/{preset_name}/models" in paths


class _Element:
    def __init__(self, text="", data_value=""):
        self.text = text
        self._data_value = data_value

    def attr(self, name):
        return self._data_value if name == "data-value" else ""


class _TextHandler:
    def __init__(self):
        self.calls = []

    def fill_via_clipboard_no_click(self, element, text):
        self.calls.append((element, text))


class _ActionHarness(WorkflowExecutorActionMixin):
    def __init__(self, current_label):
        self.trigger = _Element(text=current_label)
        self.search = _Element()
        self.option = _Element(data_value="claude-sonnet-4-6-vertex")
        self._text_handler = _TextHandler()
        self.clicks = []
        self.tab = object()

    @contextlib.contextmanager
    def _page_interaction_slot(self, *_args, **_kwargs):
        yield True

    def _check_cancelled(self):
        return False

    @staticmethod
    def _coerce_float(value, default, minimum=0.0):
        return max(minimum, float(value if value is not None else default))

    @staticmethod
    def _compact_log_value(value, _max_len=100):
        return str(value)

    def _find_visible_elements(self, selector):
        if selector == MODEL_TRIGGER_SELECTOR:
            return [self.trigger]
        if selector == 'input[placeholder="Search models"]':
            return [self.search]
        if selector == '[role="option"][data-value]':
            return [self.option]
        return []

    @staticmethod
    def _get_element_viewport_pos(_element):
        return (100, 100)

    def _stealth_click_element(self, element, **_kwargs):
        self.clicks.append(element)
        if element is self.option:
            self.trigger.text = "claude-sonnet-4-6"

    def _close_arena_model_dialog(self):
        raise AssertionError("successful selection should not need dialog cleanup")


class _BattleActionHarness(_ActionHarness):
    def __init__(self):
        super().__init__(current_label="Max")
        self.mode_button = _Element(text="Battle Mode")
        self.direct_option = _Element(text="Direct Chat with 1 model at a time")
        self.direct_mode = False

    def _find_visible_elements(self, selector):
        if selector == MODEL_TRIGGER_SELECTOR:
            return [self.trigger] if self.direct_mode else []
        if selector == 'button[role="combobox"]':
            return [self.mode_button]
        if selector == '[role="option"]':
            return [self.direct_option]
        return super()._find_visible_elements(selector)

    def _stealth_click_element(self, element, **kwargs):
        self.clicks.append(element)
        if element is self.direct_option:
            self.direct_mode = True
            self.mode_button.text = "Direct"
        elif element is self.option:
            self.trigger.text = "claude-sonnet-4-6"


class _DuplicateTriggerHarness(_ActionHarness):
    def __init__(self):
        super().__init__(current_label="claude-sonnet-4-6")
        self.stale_trigger = _Element(text="Max")

    def _find_visible_elements(self, selector):
        if selector == MODEL_TRIGGER_SELECTOR:
            return [self.stale_trigger, self.trigger]
        return super()._find_visible_elements(selector)

    def _get_element_viewport_pos(self, element):
        if element is self.stale_trigger:
            return None
        return super()._get_element_viewport_pos(element)


@pytest.fixture
def resolved_model(monkeypatch):
    model = {
        "arena_model_id": MODEL_UUID,
        "name": "claude-sonnet-4-6-vertex",
        "public_name": "claude-sonnet-4-6",
        "display_name": "claude-sonnet-4-6",
    }
    monkeypatch.setattr(
        "app.core.workflow.executor_actions.resolve_arena_direct_model",
        lambda _tab, _requested, catalog_config=None: model,
    )
    return model


def test_select_model_is_zero_interaction_when_already_selected(resolved_model):
    harness = _ActionHarness(current_label="claude-sonnet-4-6")

    harness._execute_select_model(
        selector=MODEL_TRIGGER_SELECTOR,
        target_key="model_select_btn",
        value={"timeout": 1},
        context={"model": "claude-sonnet-4-6-vertex"},
        optional=False,
    )

    assert harness.clicks == []
    assert harness._text_handler.calls == []


def test_select_model_ignores_stale_duplicate_before_current_model_check(resolved_model):
    harness = _DuplicateTriggerHarness()

    harness._execute_select_model(
        selector=MODEL_TRIGGER_SELECTOR,
        target_key="model_select_btn",
        value={"timeout": 1},
        context={"model": "claude-sonnet-4-6-vertex"},
        optional=False,
    )

    assert harness.clicks == []
    assert harness._text_handler.calls == []


def test_select_model_uses_one_menu_click_and_one_exact_option_click(resolved_model):
    harness = _ActionHarness(current_label="Max")

    harness._execute_select_model(
        selector=MODEL_TRIGGER_SELECTOR,
        target_key="model_select_btn",
        value={"timeout": 1},
        context={"model": "claude-sonnet-4-6-vertex"},
        optional=False,
    )

    assert harness.clicks == [harness.trigger, harness.option]
    assert harness._text_handler.calls == [
        (harness.search, "claude-sonnet-4-6")
    ]


def test_select_model_switches_battle_to_direct_before_selecting(resolved_model):
    harness = _BattleActionHarness()

    harness._execute_select_model(
        selector=MODEL_TRIGGER_SELECTOR,
        target_key="model_select_btn",
        value={"timeout": 1},
        context={"model": "claude-sonnet-4-6-vertex"},
        optional=False,
    )

    assert harness.clicks == [
        harness.mode_button,
        harness.direct_option,
        harness.trigger,
        harness.option,
    ]


def test_arena_main_direct_workflow_selects_model_before_filling_prompt():
    sites_path = Path(__file__).parents[1] / "config" / "sites.json"
    sites = json.loads(sites_path.read_text(encoding="utf-8"))
    preset = sites["arena.ai"]["presets"]["主预设-直连模式"]
    actions = [step["action"] for step in preset["workflow"]]

    assert preset["selectors"]["model_select_btn"] == MODEL_TRIGGER_SELECTOR
    assert preset["model_catalog"]["enabled"] is True
    assert preset["model_catalog"]["source"] == "arena_direct"
    assert 'href="/code"' in preset["selectors"]["new_chat_btn"]
    assert actions.index("SELECT_MODEL") < actions.index("FILL_INPUT")
