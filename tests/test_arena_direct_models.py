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
            "modality": "text",
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
            "id": "claude-sonnet-4-6",
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


def test_catalog_identity_wins_over_stale_alias_from_earlier_model():
    models = [
        {
            "arena_model_id": "glm-52-id",
            "name": "glm-5.2",
            "search_name": "glm-5.2 (max)",
            "aliases": ["glm-5.1", "glm-5.2", "glm-5.2 (max)"],
        },
        {
            "arena_model_id": "glm-51-id",
            "name": "glm-5.1",
            "search_name": "glm-5.1",
            "aliases": ["glm-5.1"],
        },
    ]

    resolved = match_arena_direct_model(models, "glm-5.1")

    assert resolved["arena_model_id"] == "glm-51-id"
    assert resolved["name"] == "glm-5.1"


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

    chat_result = get_arena_direct_catalog_for_tab(
        config_engine,
        {**direct_tab, "url": "https://arena.ai/c/019f93c5-0ec7-70d9-9851-7089931253db"},
    )
    assert chat_result is not None
    assert chat_result["preset_name"] == "direct"

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
        },
        domain="arena.ai",
    )

    assert "model_catalog" not in normalized
    cat = normalize_model_catalog_config({
        "enabled": True,
        "source": "arena_direct",
        "include_keywords": "glm, claude\nglm",
        "exclude_keywords": "image\npreview",
    })
    assert cat["include_keywords"] == ["glm", "claude"]
    assert cat["exclude_keywords"] == ["image", "preview"]


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


def test_arena_catalog_tab_selection_uses_pool_round_robin():
    class _TabPool:
        allocation_mode = "round_robin"

    browser = SimpleNamespace(tab_pool=_TabPool())
    candidates = [
        {"persistent_index": 7, "status": "idle"},
        {"persistent_index": 8, "status": "idle"},
    ]

    first = chat_api._select_arena_catalog_tab(browser, candidates, preset_name="direct")
    second = chat_api._select_arena_catalog_tab(browser, candidates, preset_name="direct")

    assert first["persistent_index"] == 7
    assert second["persistent_index"] == 8


def test_global_chat_catalog_route_skips_excluded_tab(monkeypatch):
    excluded_url = "https://arena.ai/c/excluded"

    class _TabPool:
        @staticmethod
        def get_tabs_with_index():
            return [
                {
                    "persistent_index": 7,
                    "status": "idle",
                    "url": excluded_url,
                    "preset_name": "direct",
                    "current_domain": "arena.ai",
                    "route_domain": "arena.ai",
                },
                {
                    "persistent_index": 8,
                    "status": "idle",
                    "url": "https://arena.ai/c/allowed",
                    "preset_name": "direct",
                    "current_domain": "arena.ai",
                    "route_domain": "arena.ai",
                },
            ]

        @staticmethod
        def is_url_excluded(url):
            return url == excluded_url

    class _Browser:
        tab_pool = _TabPool()

    monkeypatch.setattr(chat_api, "get_browser", lambda auto_connect=False: _Browser())
    catalog_tabs = []

    def _get_catalog(_config_engine, tab, preset_name=None):
        catalog_tabs.append(tab["persistent_index"])
        return {"catalog": {"enabled": True}}

    monkeypatch.setattr(chat_api, "get_arena_direct_catalog_for_tab", _get_catalog)
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
    assert catalog_tabs == [8]
    assert routed["tab_index"] == 8


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
    def __init__(self, text="", data_value="", raw_text=""):
        self.text = text
        self.raw_text = raw_text
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
            return [self.search] if getattr(self, "dialog_open", False) else []
        if selector in ('[role="option"]', '[role="option"][data-value]'):
            return [self.option] if getattr(self, "dialog_open", False) else []
        return []

    @staticmethod
    def _get_element_viewport_pos(_element):
        return (100, 100)

    def _stealth_click_element(self, element, **_kwargs):
        self.clicks.append(element)
        if element is self.trigger:
            self.dialog_open = True
        elif element is self.option or getattr(element, "attr", None):
            self.dialog_open = False
            cand_text = (
                getattr(element, "text", "")
                or getattr(element, "raw_text", "")
                or getattr(element, "_data_value", "")
                or "claude-sonnet-4-6"
            )
            if cand_text:
                self.trigger.text = cand_text.splitlines()[0].strip()

    def _close_arena_model_dialog(self):
        self.dialog_open = False


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
        if selector in ('[role="option"]', '[role="option"][data-value]'):
            return [self.option] if (self.direct_mode and self.dialog_open) else [self.direct_option]
        if selector == 'input[placeholder="Search models"]':
            return [self.search] if (self.direct_mode and self.dialog_open) else []
        return super()._find_visible_elements(selector)

    def _stealth_click_element(self, element, **kwargs):
        self.clicks.append(element)
        if element is self.direct_option:
            self.direct_mode = True
            self.mode_button.text = "Direct"
        elif element is self.trigger:
            self.dialog_open = True
        else:
            self.dialog_open = False
            cand_text = (
                getattr(element, "text", "")
                or getattr(element, "raw_text", "")
                or getattr(element, "_data_value", "")
                or "claude-sonnet-4-6"
            )
            if cand_text:
                self.trigger.text = cand_text.splitlines()[0].strip()


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


class _ProviderIconTriggerHarness(_ActionHarness):
    def __init__(self, current_label):
        super().__init__(current_label=current_label)
        self.trigger.text = f"Anthropic{current_label}"
        self.trigger.raw_text = current_label

    def _stealth_click_element(self, element, **_kwargs):
        super()._stealth_click_element(element, **_kwargs)
        if element is self.option:
            self.trigger.text = "Anthropicclaude-sonnet-4-6"
            self.trigger.raw_text = "claude-sonnet-4-6"


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


def test_page_model_check_rejects_stale_alias_of_different_model():
    target_model = {
        "name": "glm-5.2",
        "public_name": "glm-5.2 (max)",
        "display_name": "glm-5.2 (max)",
        "search_name": "glm-5.2 (max)",
        "aliases": ["glm-5.1", "glm-5.2", "glm-5.2 (max)"],
    }

    assert not WorkflowExecutorActionMixin._model_label_matches("glm-5.1", target_model)
    assert WorkflowExecutorActionMixin._model_label_matches("glm-5.2 (max)", target_model)


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


def test_select_model_uses_visible_text_without_provider_icon_title(resolved_model):
    harness = _ProviderIconTriggerHarness(current_label="claude-sonnet-4-6")

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


def test_select_model_confirms_switch_with_provider_icon_title(resolved_model):
    harness = _ProviderIconTriggerHarness(current_label="Max")

    harness._execute_select_model(
        selector=MODEL_TRIGGER_SELECTOR,
        target_key="model_select_btn",
        value={"timeout": 1},
        context={"model": "claude-sonnet-4-6-vertex"},
        optional=False,
    )

    assert harness.clicks == [harness.trigger, harness.option]


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
    from app.services.arena_model_catalog import get_arena_model_catalog
    sites_path = Path(__file__).parents[1] / "config" / "sites.json"
    sites = json.loads(sites_path.read_text(encoding="utf-8"))
    preset = sites["arena.ai"]["presets"]["主预设-直连模式"]
    actions = [step["action"] for step in preset["workflow"]]

    assert preset["selectors"]["model_select_btn"] == MODEL_TRIGGER_SELECTOR
    cat = get_arena_model_catalog("arena.ai", "主预设-直连模式")
    assert cat["enabled"] is True
    assert cat["source"] == "arena_direct"
    assert "model_catalog" not in preset
    assert 'href="/code"' in preset["selectors"]["new_chat_btn"]
    assert actions.index("SELECT_MODEL") < actions.index("FILL_INPUT")


def test_is_arena_direct_url_with_presets():
    from app.services.arena_direct_models import _is_arena_direct_url

    # /text/direct 静态路径无论是否有 catalog_preset 均返回 True
    assert _is_arena_direct_url("https://arena.ai/text/direct") is True
    assert _is_arena_direct_url("https://arena.ai/text/direct/sub") is True

    # /c/ 会话路径当未提供 catalog_preset 时默认返回 True
    assert _is_arena_direct_url("https://arena.ai/c/019f93c5-0ec7-70d9-9851-7089931253db") is True

    # /c/ 会话路径当提供了生效的 arena_direct 预设时返回 True
    direct_preset = {
        "model_catalog": {
            "enabled": True,
            "source": "arena_direct",
        }
    }
    assert _is_arena_direct_url("https://arena.ai/c/019f93c5-0ec7-70d9-9851-7089931253db", catalog_preset=direct_preset) is True

    # /c/ 会话路径当提供的预设未启用 catalog 或来源不符时返回 False
    disabled_preset = {
        "model_catalog": {
            "enabled": False,
            "source": "arena_direct",
        }
    }
    assert _is_arena_direct_url("https://arena.ai/c/019f93c5-0ec7-70d9-9851-7089931253db", catalog_preset=disabled_preset) is False

    # 畸形或非 Arena URL 防御
    assert _is_arena_direct_url("about:blank") is False
    assert _is_arena_direct_url("https://gemini.google.com/app") is False


def test_arena_universal_direct_image_preset_workflow():
    sites_path = Path(__file__).parents[1] / "config" / "sites.json"
    sites = json.loads(sites_path.read_text(encoding="utf-8"))
    preset = sites["arena.ai"]["presets"]["万能直连-通用生图"]

    selectors = preset["selectors"]
    assert selectors.get("new_chat_btn") is None
    assert "Stop generation" not in selectors.get("send_btn", "")
    assert selectors.get("retry button") is not None
    assert selectors.get("stop_btn") is not None

    workflow = preset["workflow"]
    assert len(workflow) == 6
    assert workflow[0]["action"] == "CLICK" and workflow[0]["target"] == "retry button" and workflow[0]["optional"] is True
    assert workflow[0]["execution"]["click_mode"] == "dom_safe"
    assert workflow[1]["action"] == "CLICK" and workflow[1]["target"] == "stop_btn" and workflow[1]["optional"] is True
    assert workflow[1]["execution"]["click_mode"] == "dom_safe"
    assert workflow[2]["action"] == "JS_EXEC" and "arena_payload_interceptor.js" in workflow[2]["target"]
    assert workflow[3]["action"] == "FILL_INPUT" and workflow[3]["target"] == "input_box"
    assert workflow[4]["action"] == "CLICK" and workflow[4]["target"] == "send_btn"
    assert workflow[5]["action"] == "STREAM_WAIT" and workflow[5]["target"] == "result_container"


def test_arena_universal_direct_text_preset_workflow():
    sites_path = Path(__file__).parents[1] / "config" / "sites.json"
    sites = json.loads(sites_path.read_text(encoding="utf-8"))
    presets = sites.get("arena.ai", {}).get("presets", {})
    if "万能直连-通用文本" not in presets:
        return
    preset = presets["万能直连-通用文本"]
    selectors = preset["selectors"]
    assert selectors.get("new_chat_btn") is not None
    assert selectors.get("input_box") is not None
    assert selectors.get("send_btn") is not None

    workflow = preset["workflow"]
    assert len(workflow) == 6
    assert workflow[0]["action"] == "CLICK" and workflow[0]["target"] == "new_chat_btn"
    assert workflow[1]["action"] == "WAIT"
    assert workflow[2]["action"] == "JS_EXEC" and "arena_payload_interceptor.js" in workflow[2]["target"]
    assert workflow[3]["action"] == "FILL_INPUT" and workflow[3]["target"] == "input_box"
    assert workflow[4]["action"] == "CLICK" and workflow[4]["target"] == "send_btn"
    assert workflow[5]["action"] == "STREAM_WAIT" and workflow[5]["target"] == "result_container"


def test_filter_models_respects_explicit_modality_over_keyword():
    models = [
        {
            "arena_model_id": "text-model-1",
            "name": "qwen-image-reasoning",
            "public_name": "qwen-image-reasoning",
            "display_name": "qwen-image-reasoning",
            "modality": "text",
        },
        {
            "arena_model_id": "image-model-1",
            "name": "recraft-v4.1-pro",
            "public_name": "recraft-v4.1-pro",
            "display_name": "recraft-v4.1-pro",
            "modality": "image",
        },
        {
            "arena_model_id": "code-model-1",
            "name": "paloma",
            "public_name": "paloma",
            "display_name": "paloma",
            "modality": "code",
        },
        {
            "arena_model_id": "search-model-1",
            "name": "o3-search",
            "public_name": "o3-search",
            "display_name": "o3-search",
            "modality": "search",
        },
    ]

    text_filtered = arena_direct_models._filter_models(models, {"enabled": True, "modality": "text"})
    assert [m["name"] for m in text_filtered] == ["qwen-image-reasoning"]

    image_filtered = arena_direct_models._filter_models(models, {"enabled": True, "modality": "image"})
    assert [m["name"] for m in image_filtered] == ["recraft-v4.1-pro"]

    code_filtered = arena_direct_models._filter_models(models, {"enabled": True, "modality": "code"})
    assert [m["name"] for m in code_filtered] == ["paloma"]

    search_filtered = arena_direct_models._filter_models(models, {"enabled": True, "modality": "search"})
    assert [m["name"] for m in search_filtered] == ["o3-search"]


def test_arena_catalog_tab_does_not_cross_route_code_model_to_text_preset(monkeypatch):
    class _TabPool:
        allocation_mode = "first_idle"

        @staticmethod
        def is_url_excluded(_url):
            return False

    browser = SimpleNamespace(tab_pool=_TabPool())
    tabs = [{
        "persistent_index": 1,
        "status": "idle",
        "url": "https://arena.ai/text/direct",
        "preset_name": "主预设-直连模式",
    }]
    paloma = {
        "arena_model_id": "01a031e9-1cad-76d4-917d-8b9895f77c3c",
        "name": "paloma",
        "public_name": "paloma",
        "display_name": "paloma",
        "modality": "code",
    }

    monkeypatch.setattr(
        chat_api,
        "get_arena_direct_catalog_for_tab",
        lambda _config_engine, _tab: {
            "preset_name": "主预设-直连模式",
            "catalog": {"enabled": True, "source": "arena_direct", "modality": "text"},
        },
    )
    monkeypatch.setattr(
        chat_api,
        "list_arena_direct_models",
        lambda _browser, catalog_config=None: [paloma]
        if (catalog_config or {}).get("modality") == "code"
        else [],
    )

    assert chat_api._match_arena_catalog_tab(browser, object(), tabs, "paloma") is None


def test_collect_model_entries_respects_tab_preset_isolation(monkeypatch):
    from app.api.chat import _collect_model_entries

    class _MockPool:
        def __init__(self, tabs):
            self._tabs = tabs

        def get_tabs_with_index(self):
            return self._tabs

    class _MockBrowser:
        def __init__(self, tabs):
            self.tab_pool = _MockPool(tabs)

    monkeypatch.setattr(
        "app.api.chat.list_arena_direct_models",
        lambda browser, catalog_config=None: [
            {
                "arena_model_id": "019fe39a-b4c5-7442-850f-26c39b95b3ca",
                "name": "mona-lisa-1",
                "public_name": "mona-lisa-1",
                "display_name": "mona-lisa-1",
                "modality": "image",
                "provider": "arena.ai",
                "organization": "arena.ai",
            }
        ] if (catalog_config or {}).get("modality") == "image" else [
            {
                "arena_model_id": "019c6d29-a30c-7e20-9bd0-6650af926623",
                "name": "claude-sonnet-4-6",
                "public_name": "claude-sonnet-4-6",
                "display_name": "claude-sonnet-4-6",
                "modality": "text",
                "provider": "anthropic",
                "organization": "anthropic",
            }
        ],
    )

    # 1. 只有通用生图预设标签页时，只暴露生图模型，不融入文本模型
    monkeypatch.setattr("app.api.chat.get_browser", lambda **_: _MockBrowser([
        {
            "route_domain": "arena.ai",
            "url": "https://arena.ai/image/direct",
            "preset_name": "万能直连-通用生图",
            "persistent_index": 1,
            "status": "idle",
        }
    ]))
    entries = _collect_model_entries()
    model_ids = [e["id"] for e in entries]
    assert "mona-lisa-1" in model_ids
    assert "claude-sonnet-4-6" not in model_ids

    # 2. 只有主预设（文本）标签页时，只暴露文本模型，不融入生图模型
    monkeypatch.setattr("app.api.chat.get_browser", lambda **_: _MockBrowser([
        {
            "route_domain": "arena.ai",
            "url": "https://arena.ai/text/direct",
            "preset_name": "主预设-直连模式",
            "persistent_index": 1,
            "status": "idle",
        }
    ]))
    entries = _collect_model_entries()
    model_ids = [e["id"] for e in entries]
    assert "claude-sonnet-4-6" in model_ids
    assert "mona-lisa-1" not in model_ids

    # 3. 两个标签页分别打开文本与生图预设时，两者模型均能按活跃标签页聚合暴露
    monkeypatch.setattr("app.api.chat.get_browser", lambda **_: _MockBrowser([
        {
            "route_domain": "arena.ai",
            "url": "https://arena.ai/text/direct",
            "preset_name": "主预设-直连模式",
            "persistent_index": 1,
            "status": "idle",
        },
        {
            "route_domain": "arena.ai",
            "url": "https://arena.ai/image/direct",
            "preset_name": "万能直连-通用生图",
            "persistent_index": 2,
            "status": "idle",
        }
    ]))
    entries = _collect_model_entries()
    model_ids = [e["id"] for e in entries]
    assert "claude-sonnet-4-6" in model_ids
    assert "mona-lisa-1" in model_ids


def test_match_arena_direct_model_with_derived_suffixes():
    models = [
        {
            "arena_model_id": "019fb6ba-031f-7e6b-ac0b-33dc6569bbc4",
            "name": "gemini-3.7-flash-high",
            "public_name": "gemini-3.7-flash-high",
            "display_name": "gemini-3.7-flash-high",
            "aliases": ["gemini-3.7-flash-high", "gemini-3.7-flash"],
        },
        {
            "arena_model_id": "019d5e8d-d53e-75f3-bcf5-815ae0cf202a",
            "name": "glm-5.1",
            "public_name": "glm-5.1",
            "display_name": "glm-5.1",
            "aliases": ["glm-5.1"],
        },
        {
            "arena_model_id": "019ebf6a-94d4-7649-b704-1dbbd5eb0942",
            "name": "glm-5.2",
            "public_name": "glm-5.2",
            "display_name": "glm-5.2",
            "aliases": ["glm-5.2", "glm-5.2 (max)"],
        },
        {
            "arena_model_id": "019fe39a-b4c5-7442-850f-26c39b95b3ca",
            "name": "mona-lisa-1",
            "public_name": "mona-lisa-1",
            "display_name": "mona-lisa-1",
            "aliases": ["mona-lisa-1"],
        },
    ]

    matched_gemini = arena_direct_models.match_arena_direct_model(models, "gemini-3.7-flash")
    assert matched_gemini is not None
    assert matched_gemini["display_name"] == "gemini-3.7-flash-high"

    matched_glm51 = arena_direct_models.match_arena_direct_model(models, "glm-5.1")
    assert matched_glm51 is not None
    assert matched_glm51["display_name"] == "glm-5.1"

    matched_glm52 = arena_direct_models.match_arena_direct_model(models, "glm-5.2")
    assert matched_glm52 is not None
    assert matched_glm52["display_name"] == "glm-5.2"

    matched_mona = arena_direct_models.match_arena_direct_model(models, "mona-lisa")
    assert matched_mona is not None
    assert matched_mona["display_name"] == "mona-lisa-1"


def test_select_model_matches_option_by_display_name_when_internal_name_is_uuid(monkeypatch):
    model = {
        "arena_model_id": "019c7820-5480-78b6-9fef-04c0d7004054",
        "name": "019c7820-5480-78b6-9fef-04c0d7004054",
        "public_name": "gemini-3.1-pro-preview",
        "display_name": "gemini-3.1-pro-preview",
        "search_name": "gemini-3.1-pro-preview",
        "aliases": ["gemini-3.1-pro-preview", "gemini-3.1-pro"],
    }
    monkeypatch.setattr(
        "app.core.workflow.executor_actions.resolve_arena_direct_model",
        lambda _tab, _requested, catalog_config=None: model,
    )

    harness = _ActionHarness(current_label="gemini-3.7-flash-high")
    harness.option = _Element(data_value="gemini-3.1-pro-preview", text="gemini-3.1-pro-preview")

    harness._execute_select_model(
        selector=MODEL_TRIGGER_SELECTOR,
        target_key="model_select_btn",
        value={"timeout": 1},
        context={"model": "gemini-3.1-pro-preview"},
        optional=False,
    )

    assert harness.clicks == [harness.trigger, harness.option]
    assert harness._text_handler.calls == [(harness.search, "gemini-3.1-pro-preview")]


def test_select_model_matches_option_with_text_and_icons_without_data_value(monkeypatch):
    model = {
        "arena_model_id": "019c7820-5480-78b6-9fef-04c0d7004054",
        "name": "019c7820-5480-78b6-9fef-04c0d7004054",
        "public_name": "gemini-3.1-pro-preview",
        "display_name": "gemini-3.1-pro-preview",
        "search_name": "gemini-3.1-pro-preview",
        "aliases": ["gemini-3.1-pro-preview"],
    }
    monkeypatch.setattr(
        "app.core.workflow.executor_actions.resolve_arena_direct_model",
        lambda _tab, _requested, catalog_config=None: model,
    )

    harness = _ActionHarness(current_label="gemini-3.7-flash-high")
    # Option has empty data-value, but has multiline text containing display name and icon descriptions
    harness.option = _Element(data_value="", text="gemini-3.1-pro-preview\nVision Document")

    harness._execute_select_model(
        selector=MODEL_TRIGGER_SELECTOR,
        target_key="model_select_btn",
        value={"timeout": 1},
        context={"model": "gemini-3.1-pro-preview"},
        optional=False,
    )

    assert harness.clicks == [harness.trigger, harness.option]


def test_model_label_matches_truncated_and_provider_prefixed_labels():
    target = {
        "name": "andwise-evfd",
        "display_name": "gemini-3.7-flash-high",
        "public_name": "gemini-3.7-flash-high",
        "search_name": "gemini-3.7-flash-high",
        "aliases": ["gemini-3.7-flash-high", "gemini-3.7-flash"],
    }

    # Truncated button text on UI (e.g. gemini-3.7-flash-...)
    assert WorkflowExecutorActionMixin._model_label_matches("gemini-3.7-flash-...", target)
    assert WorkflowExecutorActionMixin._model_label_matches("gemini-3.7-flash-…", target)
    # Non-truncated shorter name must not match target high model
    assert not WorkflowExecutorActionMixin._model_label_matches("gemini-3.7-flash", target)

    # Provider icon text combined (e.g. Google gemini-3.7-flash-high)
    assert WorkflowExecutorActionMixin._model_label_matches("Google gemini-3.7-flash-high", target)
    assert WorkflowExecutorActionMixin._model_label_matches("Google\ngemini-3.7-flash-high", target)

    # Different model should not match
    assert not WorkflowExecutorActionMixin._model_label_matches("gemini-3.1-pro-preview", target)


def test_collect_model_entries_does_not_expose_synthetic_preview_alias(monkeypatch):
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
                "arena_model_id": "019c7820-5480-78b6-9fef-04c0d7004054",
                "name": "gemini-3.1-pro-preview",
                "public_name": "gemini-3.1-pro-preview",
                "display_name": "gemini-3.1-pro-preview",
                "search_name": "gemini-3.1-pro-preview",
                "aliases": ["gemini-3.1-pro-preview", "gemini-3.1-pro"],
                "provider": "google",
                "organization": "google",
            }
        ],
    )

    entries = chat_api._collect_model_entries()
    model_ids = [item["id"] for item in entries]

    assert "gemini-3.1-pro-preview" in model_ids
    assert "gemini-3.1-pro" not in model_ids


def test_model_label_matches_rejects_prefix_match_when_not_truncated():
    gpt_4o_mini = {
        "name": "gpt-4o-mini",
        "display_name": "gpt-4o-mini",
        "public_name": "gpt-4o-mini",
    }
    # Non-truncated button showing "gpt-4o" MUST NOT match target "gpt-4o-mini"
    assert not WorkflowExecutorActionMixin._model_label_matches("gpt-4o", gpt_4o_mini)

    # Truncated button with ellipsis DOES match
    assert WorkflowExecutorActionMixin._model_label_matches("gpt-4o-min...", gpt_4o_mini)
    assert WorkflowExecutorActionMixin._model_label_matches("gpt-4o-min…", gpt_4o_mini)


def test_select_model_prefers_exact_match_over_earlier_longer_candidate(monkeypatch):
    target_model = {
        "arena_model_id": "target-uuid",
        "name": "gpt-4o",
        "display_name": "gpt-4o",
        "public_name": "gpt-4o",
        "search_name": "gpt-4o",
    }
    monkeypatch.setattr(
        "app.core.workflow.executor_actions.resolve_arena_direct_model",
        lambda _tab, _requested, catalog_config=None: target_model,
    )

    class _MultiCandidateHarness(_ActionHarness):
        def __init__(self):
            super().__init__(current_label="claude-sonnet-4-6")
            self.cand_mini = _Element(data_value="gpt-4o-mini", text="gpt-4o-mini")
            self.cand_exact = _Element(data_value="gpt-4o", text="gpt-4o")

        def _find_visible_elements(self, selector):
            if selector == MODEL_TRIGGER_SELECTOR:
                return [self.trigger]
            if selector == 'input[placeholder="Search models"]':
                return [self.search]
            if selector in ('[role="option"]', '[role="option"][data-value]'):
                # Longer prefix item appears FIRST
                return [self.cand_mini, self.cand_exact]
            return []

    harness = _MultiCandidateHarness()
    harness._execute_select_model(
        selector=MODEL_TRIGGER_SELECTOR,
        target_key="model_select_btn",
        value={"timeout": 1},
        context={"model": "gpt-4o"},
        optional=False,
    )

    # Pass 1 exact match must select cand_exact, not cand_mini
    assert harness.clicks == [harness.trigger, harness.cand_exact]


def test_select_model_prefers_search_name_over_display_name(monkeypatch):
    target_model = {
        "arena_model_id": "target-uuid",
        "name": "claude-sonnet-4-6-vertex",
        "display_name": "claude-sonnet-4-6",
        "search_name": "claude-visible-override",
    }
    monkeypatch.setattr(
        "app.core.workflow.executor_actions.resolve_arena_direct_model",
        lambda _tab, _requested, catalog_config=None: target_model,
    )

    harness = _ActionHarness(current_label="Max")
    harness.option = _Element(data_value="claude-visible-override", text="claude-visible-override")

    harness._execute_select_model(
        selector=MODEL_TRIGGER_SELECTOR,
        target_key="model_select_btn",
        value={"timeout": 1},
        context={"model": "claude-sonnet-4-6-vertex"},
        optional=False,
    )

    assert harness._text_handler.calls == [(harness.search, "claude-visible-override")]





def test_model_label_matches_provider_prefix_whitelist_and_short_names():
    gpt_4 = {
        "name": "gpt-4",
        "display_name": "gpt-4",
        "public_name": "gpt-4",
    }
    # Known provider attached directly without space
    assert WorkflowExecutorActionMixin._model_label_matches("OpenAIgpt-4", gpt_4)
    assert WorkflowExecutorActionMixin._model_label_matches("openai gpt-4", gpt_4)

    # Unknown prefix (like minigpt-4) must NOT match
    assert not WorkflowExecutorActionMixin._model_label_matches("minigpt-4", gpt_4)

    # Short name o1
    o1 = {
        "name": "o1",
        "display_name": "o1",
        "public_name": "o1",
    }
    assert WorkflowExecutorActionMixin._model_label_matches("OpenAIo1", o1)
    assert not WorkflowExecutorActionMixin._model_label_matches("demoo1", o1)


def test_arena_image_direct_models_auto_refresh_and_merge():
    # 模拟文本标签页和生图标签页
    class _ImageTab:
        def run_js(self, script, timeout=None):
            return [
                {
                    "arena_model_id": "image-uuid-1",
                    "name": "flux-2-pro",
                    "public_name": "flux-2-pro",
                    "display_name": "flux-2-pro",
                    "provider": "bfl",
                    "organization": "black-forest-labs",
                    "modality": "image",
                }
            ]

    class _ImageSession:
        status = SimpleNamespace(value="idle")
        persistent_index = 2
        tab = _ImageTab()

        @staticmethod
        def get_cached_route_snapshot():
            return "https://arena.ai/image/direct", "arena.ai"

    class _MockBrowser:
        class _TabPool:
            @staticmethod
            def get_sessions_snapshot():
                return [_ImageSession()]
        tab_pool = _TabPool()

    # 验证生图模态过滤能正确获取生图模型
    image_models = list_arena_direct_models(
        _MockBrowser(),
        force=True,
        catalog_config={"enabled": True, "modality": "image", "include_keywords": [], "exclude_keywords": []}
    )
    assert len(image_models) >= 1
    assert any(m["name"] == "flux-2-pro" for m in image_models)

def test_arena_model_catalog_save_failure_and_migration_safety(monkeypatch):
    from app.services import arena_model_catalog
    import pytest

    # 1. set_arena_model_catalog 在写入失败时必须抛出异常
    monkeypatch.setattr(
        arena_model_catalog,
        "save_arena_model_catalog_data",
        lambda _data: False,
    )
    with pytest.raises(IOError):
        arena_model_catalog.set_arena_model_catalog(
            domain="arena.ai",
            preset_name="主预设",
            catalog_config={"enabled": True},
        )

    # 2. migrate_and_cleanup_sites_model_catalog 在独立文件写入失败时不得清空 sites 中的 model_catalog
    class _MockEngine:
        def __init__(self):
            self.sites = {
                "arena.ai": {
                    "presets": {
                        "新预设": {
                            "model_catalog": {
                                "enabled": True,
                                "source": "arena_direct",
                            }
                        }
                    }
                }
            }
            self.saved = False

        def save_config(self):
            self.saved = True
            return True

    engine = _MockEngine()
    monkeypatch.setattr(
        arena_model_catalog,
        "load_arena_model_catalog_data",
        lambda: {},
    )
    res = arena_model_catalog.migrate_and_cleanup_sites_model_catalog(engine)
    assert res is False
    assert "model_catalog" in engine.sites["arena.ai"]["presets"]["新预设"]
    assert engine.saved is False


def test_non_arena_model_catalog_preservation():
    from app.api.config_route_models import _normalize_preset_config_payload

    # 1. domain=None 时，model_catalog 必须保留
    payload = {"model_catalog": {"enabled": True, "source": "custom"}}
    norm_none = _normalize_preset_config_payload(payload, domain=None)
    assert "model_catalog" in norm_none
    assert norm_none["model_catalog"]["source"] == "custom"

    # 2. 非 Arena 站点时，model_catalog 必须保留
    norm_other = _normalize_preset_config_payload(payload, domain="custom-site.com")
    assert "model_catalog" in norm_other
    assert norm_other["model_catalog"]["source"] == "custom"

    # 3. Arena 站点时，model_catalog 从 sites.json 预设结构中剔除（存独立文件）
    norm_arena = _normalize_preset_config_payload(payload, domain="arena.ai")
    assert "model_catalog" not in norm_arena


def test_dark_pool_different_configs_not_incorrectly_deduped(monkeypatch):
    import app.api.chat as chat_api
    from app.services.arena_model_catalog import normalize_arena_model_catalog_config

    class _MockTabPool:
        def get_tabs_with_index(self):
            return [
                {
                    "route_domain": "arena.ai",
                    "url": "https://arena.ai/text/direct",
                    "preset_name": "预设A-明池",
                    "persistent_index": 1,
                    "status": "idle",
                },
                {
                    "route_domain": "arena.ai",
                    "url": "https://arena.ai/text/direct",
                    "preset_name": "预设B-暗池",
                    "persistent_index": 2,
                    "status": "idle",
                }
            ]

    class _MockBrowser:
        tab_pool = _MockTabPool()

    # 两个预设具有相同 modality/include/exclude，但暗池设置不同
    cat_a = normalize_arena_model_catalog_config({
        "enabled": True,
        "modality": "text",
        "enable_dark_pool": False,
    })
    cat_b = normalize_arena_model_catalog_config({
        "enabled": True,
        "modality": "text",
        "enable_dark_pool": True,
        "dark_pool_since": "2026-01-01",
        "dark_pool_whitelist_keywords": ["secret"],
    })

    def mock_get_catalog(_engine, tab):
        if tab.get("preset_name") == "预设A-明池":
            return {"preset_name": "预设A-明池", "catalog": cat_a}
        return {"preset_name": "预设B-暗池", "catalog": cat_b}

    captured_catalogs = []
    monkeypatch.setattr(chat_api, "get_arena_direct_catalog_for_tab", mock_get_catalog)
    monkeypatch.setattr(
        chat_api,
        "list_arena_direct_models",
        lambda _browser, catalog_config=None, **_: captured_catalogs.append(catalog_config) or [],
    )
    monkeypatch.setattr(chat_api, "get_browser", lambda **_: _MockBrowser())

    chat_api._collect_model_entries()
    assert len(captured_catalogs) == 2
    assert any(c.get("enable_dark_pool") is False for c in captured_catalogs)
    assert any(c.get("enable_dark_pool") is True for c in captured_catalogs)


def test_match_arena_catalog_tab_per_tab_isolation(monkeypatch):
    import app.api.chat as chat_api
    from types import SimpleNamespace

    class _TabA:
        def run_js(self, _script, timeout=None):
            return [{"arena_model_id": "uuid-a", "name": "model-a", "display_name": "model-a", "modality": "text"}]

    class _TabB:
        def run_js(self, _script, timeout=None):
            return [{"arena_model_id": "uuid-b", "name": "model-b", "display_name": "model-b", "modality": "text"}]

    class _SessionA:
        persistent_index = 1
        id = "session-1"
        status = SimpleNamespace(value="idle")
        tab = _TabA()
        @staticmethod
        def get_cached_route_snapshot():
            return "https://arena.ai/text/direct", "arena.ai"

    class _SessionB:
        persistent_index = 2
        id = "session-2"
        status = SimpleNamespace(value="idle")
        tab = _TabB()
        @staticmethod
        def get_cached_route_snapshot():
            return "https://arena.ai/text/direct", "arena.ai"

    class _TabPool:
        @staticmethod
        def is_url_excluded(_url):
            return False
        @staticmethod
        def get_sessions_snapshot():
            return [_SessionA(), _SessionB()]

    browser = SimpleNamespace(tab_pool=_TabPool())
    tabs = [
        {"persistent_index": 1, "status": "idle", "url": "https://arena.ai/text/direct", "preset_name": "预设A"},
        {"persistent_index": 2, "status": "idle", "url": "https://arena.ai/text/direct", "preset_name": "预设B"},
    ]

    monkeypatch.setattr(
        chat_api,
        "get_arena_direct_catalog_for_tab",
        lambda _engine, tab: {
            "preset_name": tab["preset_name"],
            "catalog": {"enabled": True, "source": "arena_direct", "modality": "text"},
        },
    )

    # 标签页 1 只应匹配到 model-a，标签页 2 只应匹配到 model-b
    matched_a = chat_api._match_arena_catalog_tab(browser, object(), tabs, "model-a")
    assert matched_a is not None
    assert matched_a["tab"]["persistent_index"] == 1
    assert matched_a["model"]["name"] == "model-a"

    matched_b = chat_api._match_arena_catalog_tab(browser, object(), tabs, "model-b")
    assert matched_b is not None
    assert matched_b["tab"]["persistent_index"] == 2
    assert matched_b["model"]["name"] == "model-b"

