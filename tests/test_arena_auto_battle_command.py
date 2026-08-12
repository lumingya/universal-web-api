import ast
import copy
import json
import time
import urllib.parse
from pathlib import Path

import pytest

from app.services.arena_proxy_rotation import (
    ARENA_CLASH_SELECTOR,
    ARENA_PROXY_POOL,
    rotate_arena_proxy,
)


COMMANDS_PATH = Path(__file__).parents[1] / "config" / "commands.json"


class _Logger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _Response:
    def __init__(self, payload=None):
        self._payload = payload or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Requests:
    def __init__(self):
        self.puts = []
        self.deletes = []

    def get(self, url, **kwargs):
        return _Response(
            {
                "now": "JP自动选择",
                "all": [
                    "HK自动选择",
                    "JP自动选择",
                    "KR自动选择",
                    "SG自动选择",
                    "TW自动选择",
                    "US自动选择",
                ],
            }
        )

    def put(self, url, **kwargs):
        self.puts.append((url, kwargs))
        return _Response()

    def delete(self, url, **kwargs):
        self.deletes.append((url, kwargs))
        return _Response()


def _arena_auto_battle_script():
    payload = json.loads(COMMANDS_PATH.read_text(encoding="utf-8"))
    command = next(
        item for item in payload["commands"] if item.get("id") == "cmd_arena_auto_battle"
    )
    return command["script"]


def _command_by_id(command_id):
    payload = json.loads(COMMANDS_PATH.read_text(encoding="utf-8"))
    return next(
        item for item in payload["commands"] if item.get("id") == command_id
    )


def test_arena_auto_battle_script_has_valid_python_syntax():
    ast.parse(_arena_auto_battle_script())


def test_auto_battle_embeds_the_cf_commands_probe_with_explicit_return():
    module = ast.parse(_arena_auto_battle_script())
    assignment = next(
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_CAPTCHA_PROBE_JS"
            for target in node.targets
        )
    )
    embedded_probe = ast.literal_eval(assignment.value)
    command_probe = _command_by_id("cmd_cf_pagecheck_arena_verify")["trigger"]["probe_js"]

    assert embedded_probe == f"return {command_probe}"


def test_auto_battle_captcha_redirect_continues_later_rounds():
    script = _arena_auto_battle_script()

    assert "_check_captcha_guard()" in script
    assert "arena_captcha_redirected" in script
    assert "will recheck for up to 20 seconds" in script
    assert "if str(e) == \"arena_captcha_redirected\":" in script
    assert "人机验证跳转 {redirect_url}，继续后续轮次" in script


def test_auto_battle_captcha_guard_redirects_after_twenty_seconds():
    module = ast.parse(_arena_auto_battle_script())
    selected_nodes = []
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name)
            and target.id in {"_CAPTCHA_PROBE_JS", "_captcha_watch_state"}
            for target in node.targets
        ):
            selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_check_captcha_guard":
            selected_nodes.append(node)

    harness = ast.Module(body=selected_nodes, type_ignores=[])
    ast.fix_missing_locations(harness)
    ticks = iter([100.0, 121.0])
    navigations = []

    class Clock:
        @staticmethod
        def time():
            return next(ticks)

    class Tab:
        @staticmethod
        def run_js(code):
            return {"hit": True, "summary": "captcha element: iframe"}

        @staticmethod
        def get(url):
            navigations.append(url)

    redirect_url = "https://example.test/recover"
    namespace = {
        "time": Clock(),
        "tab": Tab(),
        "logger": _Logger(),
        "redirect_url": redirect_url,
    }
    exec(compile(harness, "<arena-captcha-guard-test>", "exec"), namespace)

    namespace["_check_captcha_guard"]()
    with pytest.raises(RuntimeError, match="arena_captcha_redirected"):
        namespace["_check_captcha_guard"]()

    assert navigations == [redirect_url]


def test_auto_battle_redirect_url_is_configurable():
    command = _command_by_id("cmd_arena_auto_battle")
    script = command["script"]
    fields = {
        field["key"]: field for field in command["advanced_ui"]["fields"]
    }

    ast.parse(script)
    assert fields["redirect_url"]["default"] == "https://arena.ai/code"
    assert "redirect_url" in command["advanced_ui"]["values"]
    assert "redirect_url = _as_text(config.get('redirect_url')" in script
    assert script.count("tab.get(redirect_url)") == 2
    assert script.count("location.replace(arguments[0])") == 2


@pytest.mark.parametrize(
    "url",
    [
        "https://arena.ai/code",
        "https://arena.ai/code/",
        "https://arena.ai/text",
        "https://arena.ai/direct?mode=side-by-side",
    ],
)
def test_arena_new_chat_landing_urls_allow_unchanged_url(url):
    module = ast.parse(_arena_auto_battle_script())
    helper = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_looks_like_new_chat_landing_url"
    )
    harness = ast.Module(body=[helper], type_ignores=[])
    ast.fix_missing_locations(harness)
    namespace = {"redirect_url": "https://example.test/recover"}
    exec(compile(harness, "<arena-new-chat-url-test>", "exec"), namespace)

    assert namespace["_looks_like_new_chat_landing_url"](url) is True


def test_configured_redirect_url_is_a_new_chat_landing_url():
    module = ast.parse(_arena_auto_battle_script())
    helper = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_looks_like_new_chat_landing_url"
    )
    harness = ast.Module(body=[helper], type_ignores=[])
    ast.fix_missing_locations(harness)
    namespace = {"redirect_url": "https://example.test/custom-recovery"}
    exec(compile(harness, "<arena-custom-redirect-url-test>", "exec"), namespace)

    assert namespace["_looks_like_new_chat_landing_url"](
        "https://example.test/custom-recovery/"
    ) is True


def test_arena_session_url_is_not_a_new_chat_landing_url():
    script = _arena_auto_battle_script()
    module = ast.parse(script)
    helper = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_looks_like_new_chat_landing_url"
    )
    harness = ast.Module(body=[helper], type_ignores=[])
    ast.fix_missing_locations(harness)
    namespace = {"redirect_url": "https://example.test/recover"}
    exec(compile(harness, "<arena-session-url-test>", "exec"), namespace)

    assert namespace["_looks_like_new_chat_landing_url"](
        "https://arena.ai/c/019f4bde-5f3c-7a37-b96e-579a7e320b66"
    ) is False
    assert "input_length >= 0" in script


def test_arena_auto_battle_rotates_regions_every_twenty_sent_requests():
    script = _arena_auto_battle_script()

    assert "ip_rotate_every = 20" in script
    assert "sent_request_count += 1" in script
    assert "sent_request_count % ip_rotate_every == 0" in script


def test_auto_battle_confirms_submission_before_waiting_for_reply():
    script = _arena_auto_battle_script()

    assert "def _wait_for_submission_confirmation" in script
    assert "def _submit_prompt_with_retry" in script
    assert "input_length == 0" in script
    assert "_submit_prompt_with_retry(input_box, baseline_signature, attempts=3)" in script
    assert "三次发送均未确认成功" in script
    assert script.index("if not _submit_prompt_with_retry") < script.index(
        "sent_request_count += 1"
    )
    assert script.index("sent_request_count += 1") < script.index(
        "reply_info = _wait_for_reply_done"
    )


def test_auto_battle_uses_enter_before_button_fallback_for_submission():
    script = _arena_auto_battle_script()
    submit_start = script.index("def _submit_prompt_with_retry")
    submit_end = script.index("def _rate_limit_reason", submit_start)
    submit_script = script[submit_start:submit_end]

    assert submit_script.index('input_box.input("\\n")') < submit_script.index(
        "form.requestSubmit()"
    )
    assert "if attempt < 2:" in submit_script
    assert "_wait_for_submission_confirmation" in submit_script


def test_arena_proxy_rotation_uses_configured_clash_region_groups():
    module = ast.parse(_arena_auto_battle_script())
    selected_nodes = []
    for node in module.body:
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if names & {
                "_CLASH_API",
                "_CLASH_SECRET",
                "_CLASH_SELECTOR",
                "_CLASH_PROXY_POOL",
            }:
                selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_rotate_arena_proxy":
            selected_nodes.append(node)

    harness = ast.Module(body=selected_nodes, type_ignores=[])
    ast.fix_missing_locations(harness)
    requests = _Requests()
    refreshes = []
    namespace = {
        "requests": requests,
        "time": time,
        "urllib": urllib,
        "raise_if_cancelled": lambda: None,
        "logger": _Logger(),
        "tab": type("Tab", (), {"refresh": lambda self: refreshes.append(True)})(),
    }
    exec(compile(harness, "<arena-proxy-rotation-test>", "exec"), namespace)
    original_sleep = time.sleep
    time.sleep = lambda seconds: None

    try:
        result = namespace["_rotate_arena_proxy"]()
    finally:
        time.sleep = original_sleep

    assert result["ok"] is True
    assert result["from"] == "JP自动选择"
    assert result["to"] == "KR自动选择"
    assert requests.puts[0][1]["json"] == {"name": "KR自动选择"}
    assert requests.deletes[0][0].endswith("/connections")
    assert refreshes == [True]


def test_standalone_arena_proxy_command_reuses_auto_battle_pool():
    command = _command_by_id("cmd_arena_rotate_ip_standalone")
    script = command["script"]

    assert "from app.services.arena_proxy_rotation import rotate_arena_proxy" in script
    assert "raise_if_cancelled=raise_if_cancelled" in script
    assert command["trigger"]["domain"] == "arena.ai"
    assert command["trigger"]["periodic_enabled"] is False

    module = ast.parse(script)
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dumps"
        for node in ast.walk(module)
    )


def test_shared_arena_proxy_rotation_uses_the_auto_battle_pool():
    requests = _Requests()
    refreshes = []
    result = rotate_arena_proxy(
        tab=type("Tab", (), {"refresh": lambda self: refreshes.append(True)})(),
        logger=_Logger(),
        requests_module=requests,
        sleep=lambda _seconds: None,
    )

    assert ARENA_CLASH_SELECTOR == "主代理"
    assert ARENA_PROXY_POOL == (
        "HK自动选择",
        "JP自动选择",
        "KR自动选择",
        "SG自动选择",
        "TW自动选择",
        "US自动选择",
    )
    assert result["ok"] is True
    assert result["from"] == "JP自动选择"
    assert result["to"] == "KR自动选择"
    assert result["pool_size"] == len(ARENA_PROXY_POOL)
    assert requests.puts[0][1]["json"] == {"name": "KR自动选择"}
    assert refreshes == [True]


def test_three_consecutive_new_chat_failures_navigate_to_code_and_continue():
    script = _arena_auto_battle_script()
    module = ast.parse(script)
    threshold = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "new_chat_failure_streak"
        and any(isinstance(operator, ast.GtE) for operator in node.test.ops)
    )

    assert "new_chat_failure_streak += 1" in script
    assert "new_chat_failure_streak >= 3" in script
    assert "new_chat_failure_streak = 0" in script
    assert "tab.get(redirect_url)" in script
    assert "location.replace(arguments[0])" in script
    assert "navigating to {redirect_url} and continuing" in script
    assert "_sleep(3.0, 0.2)\n                continue" in script
    assert any(isinstance(node, ast.Continue) for node in ast.walk(threshold))
    assert not any(isinstance(node, ast.Break) for node in ast.walk(threshold))


def test_thought_for_visible_text_is_excluded_case_insensitively():
    module = ast.parse(_arena_auto_battle_script())
    wanted_functions = {
        "_config_text",
        "_split_filter_tokens",
        "_claude_hit_filter_reason",
    }
    wanted_assignments = {
        "_CLAUDE_REQUIRED_MARKER",
        "_CLAUDE_REQUIRED_TOKEN",
        "_CLAUDE_EXCLUDED_TOKENS",
    }
    selected_nodes = []
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            selected_nodes.append(node)
        elif isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if names & wanted_assignments:
                selected_nodes.append(node)

    harness = ast.Module(body=selected_nodes, type_ignores=[])
    ast.fix_missing_locations(harness)
    namespace = {
        "config": {
            "claude_required_marker": "Claude",
            "claude_required_token": "-0.69",
            "claude_excluded_tokens": "opus, sonnet, 3.5, 4.5, thought for",
        }
    }
    exec(compile(harness, "<arena-filter-test>", "exec"), namespace)

    reason = namespace["_claude_hit_filter_reason"](
        "Thought for 1 second. I am Claude and the token is -0.69."
    )

    assert reason == "excluded token: thought for"


class _DetectorResponse:
    def __init__(self, payload, error=None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


def _arena_detector_filter_harness(response):
    module = ast.parse(_arena_auto_battle_script())
    selected_nodes = [
        node
        for node in module.body
        if (
            isinstance(node, ast.FunctionDef)
            and node.name in {"_arena_detector_api_url", "_arena_detector_accepts"}
        )
        or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_arena_detector_state"
                for target in node.targets
            )
        )
    ]
    harness = ast.Module(body=selected_nodes, type_ignores=[])
    ast.fix_missing_locations(harness)

    class DetectorRequests:
        calls = []

        @classmethod
        def post(cls, url, **kwargs):
            cls.calls.append((url, kwargs))
            return response

    namespace = {
        "requests": DetectorRequests,
        "logger": _Logger(),
        "config": {
            "arena_detector_enabled": True,
            "arena_detector_model_keyword": "claude-fable-5",
        },
        "run_prompt_text": "identify this model",
    }
    exec(compile(harness, "<arena-detector-filter-test>", "exec"), namespace)
    return namespace, DetectorRequests


def test_arena_detector_accepts_fable_in_top_five():
    predictions = [
        {"model": model}
        for model in ["model-1", "model-2", "model-3", "model-4", "claude-fable-5"]
    ]
    namespace, requests_mock = _arena_detector_filter_harness(
        _DetectorResponse({"success": True, "predictions": predictions})
    )

    result = namespace["_arena_detector_accepts"]("Claude response -0.69")

    assert result["accepted"] is True
    assert requests_mock.calls[0][1]["json"] == {
        "prompt": "identify this model",
        "response": "Claude response -0.69",
    }


def test_arena_detector_rejects_when_fable_is_outside_top_five():
    predictions = [
        {"model": model}
        for model in [
            "model-1",
            "model-2",
            "model-3",
            "model-4",
            "model-5",
            "claude-fable-5",
        ]
    ]
    namespace, _ = _arena_detector_filter_harness(
        _DetectorResponse({"success": True, "predictions": predictions})
    )

    result = namespace["_arena_detector_accepts"]("Claude response -0.69")

    assert result["accepted"] is False
    assert "top 5 missing keyword claude-fable-5" in result["reason"]


def test_arena_detector_model_matching_uses_configured_case_insensitive_substring():
    predictions = [{"model": "Claude-Fable-5"}]
    namespace, _ = _arena_detector_filter_harness(
        _DetectorResponse({"success": True, "predictions": predictions})
    )
    namespace["config"]["arena_detector_model_keyword"] = "FABLE"

    result = namespace["_arena_detector_accepts"]("Claude response -0.69")

    assert result["accepted"] is True


def test_arena_detector_api_failure_retains_result_and_marks_unavailable():
    namespace, _ = _arena_detector_filter_harness(
        _DetectorResponse({}, error=ConnectionError("offline"))
    )

    result = namespace["_arena_detector_accepts"]("Claude response -0.69")

    assert result["accepted"] is True
    assert "API unavailable" in result["reason"]
    assert namespace["_arena_detector_state"]["unavailable"] is True


def test_arena_detector_can_be_disabled_without_an_api_request():
    namespace, requests_mock = _arena_detector_filter_harness(
        _DetectorResponse({"success": True, "predictions": []})
    )
    namespace["config"]["arena_detector_enabled"] = False

    result = namespace["_arena_detector_accepts"]("Claude response -0.69")

    assert result["accepted"] is True
    assert result["skipped"] is True
    assert requests_mock.calls == []


def test_arena_detector_enable_switch_is_exposed_in_advanced_options():
    command = _command_by_id("cmd_arena_auto_battle")
    fields = {field["key"]: field for field in command["advanced_ui"]["fields"]}

    assert fields["arena_detector_enabled"]["type"] == "boolean"
    assert fields["arena_detector_enabled"]["default"] is True
    assert command["advanced_ui"]["values"]["arena_detector_enabled"] is True
    assert fields["arena_detector_model_keyword"]["type"] == "text"
    assert fields["arena_detector_model_keyword"]["default"] == "claude-fable-5"
    assert command["advanced_ui"]["values"]["arena_detector_model_keyword"] == "claude-fable-5"


def test_arena_detector_unavailable_marker_is_added_to_final_output():
    script = _arena_auto_battle_script()

    assert '"（api不可用）" if _arena_detector_state.get(\'unavailable\')' in script
    assert "_arena_detector_output_suffix" in script


def test_arena_detector_gate_runs_after_text_filters_before_url_collection():
    script = _arena_auto_battle_script()

    assert script.index("filter_reason = _claude_hit_filter_reason(text)") < script.index(
        "detector_result = _arena_detector_accepts(text)"
    )
    assert script.index("detector_result = _arena_detector_accepts(text)") < script.index(
        "_claude_hit_urls.append(hit_url)"
    )


def test_thought_for_page_probe_uses_dom_and_accessibility_fallbacks():
    script = _arena_auto_battle_script()

    assert "_THOUGHT_FOR_PROBE_JS" in script
    assert "el.shadowRoot" in script
    assert "el.getAttribute('aria-label')" in script
    assert "Accessibility.getFullAXTree" in script

    module = ast.parse(script)
    selected_nodes = []
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_THOUGHT_FOR_PROBE_JS"
            for target in node.targets
        ):
            selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_page_has_thought_for_marker":
            selected_nodes.append(node)
    harness = ast.Module(body=selected_nodes, type_ignores=[])
    ast.fix_missing_locations(harness)

    class Tab:
        @staticmethod
        def run_js(code):
            return False

        @staticmethod
        def run_cdp(command):
            return {"nodes": [{"name": {"value": "Thought for 3 seconds"}}]}

    namespace = {"tab": Tab(), "logger": _Logger()}
    exec(compile(harness, "<thought-for-page-probe-test>", "exec"), namespace)

    assert namespace["_page_has_thought_for_marker"]() is True


def test_thought_for_page_marker_revokes_an_early_url_hit():
    script = _arena_auto_battle_script()

    assert "_claude_rejected_urls = set()" in script
    assert "_claude_rejected_urls.add(url)" in script
    assert "while url in _claude_hit_urls:" in script
    assert "_claude_hit_urls.remove(url)" in script
    assert "skipped and revoked URL: excluded page marker=thought for" in script

    module = ast.parse(script)
    check_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_check_visible_claude_marker"
    )
    candidate_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_candidate_response_texts"
    )
    harness = ast.Module(body=[candidate_function, check_function], type_ignores=[])
    ast.fix_missing_locations(harness)
    url = "https://arena.ai/c/thought-for-result"
    hit_urls = [url]
    rejected_urls = set()
    namespace = {
        "_page_has_thought_for_marker": lambda: True,
        "_page_response_info": lambda: {},
        "_current_url": lambda: url,
        "_claude_hit_urls": hit_urls,
        "_claude_rejected_urls": rejected_urls,
        "_text_matches_required_marker": lambda text: True,
        "_log_claude_hit": lambda *args, **kwargs: True,
        "_short_value": lambda value, limit: value,
        "_auto_battle_batch_id": "test-batch",
        "logger": _Logger(),
    }
    exec(compile(harness, "<thought-for-revoke-test>", "exec"), namespace)

    result = namespace["_check_visible_claude_marker"](
        {"visible_text": "Claude -0.69", "url": url}
    )

    assert result is False
    assert hit_urls == []
    assert rejected_urls == {url}


def test_two_column_filter_accepts_clean_side_when_other_side_is_excluded():
    module = ast.parse(_arena_auto_battle_script())
    check_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_check_visible_claude_marker"
    )
    candidate_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_candidate_response_texts"
    )
    harness = ast.Module(body=[candidate_function, check_function], type_ignores=[])
    ast.fix_missing_locations(harness)
    checked_texts = []

    def log_hit(text, **kwargs):
        checked_texts.append(text)
        return "opus" not in text.lower() and "Claude" in text and "-0.69" in text

    namespace = {
        "_page_response_info": lambda: {},
        "_current_url": lambda: "https://arena.ai/c/two-columns",
        "_page_has_thought_for_marker": lambda: True,
        "_claude_rejected_urls": set(),
        "_claude_hit_urls": [],
        "_text_matches_required_marker": lambda text: "Claude" in text,
        "_log_claude_hit": log_hit,
        "_short_value": lambda value, limit: value,
        "_auto_battle_batch_id": "test-batch",
        "logger": _Logger(),
    }
    exec(compile(harness, "<two-column-filter-test>", "exec"), namespace)

    result = namespace["_check_visible_claude_marker"](
        {
            "url": "https://arena.ai/c/two-columns",
            "visible_text": "Claude opus Claude -0.69",
            "response_sides": [
                "I am Claude opus and -0.69",
                "I am Claude and the answer is -0.69",
            ],
        }
    )

    assert result is True
    assert checked_texts == [
        "I am Claude opus and -0.69",
        "I am Claude and the answer is -0.69",
    ]


def test_two_column_filter_does_not_combine_required_terms_across_sides():
    module = ast.parse(_arena_auto_battle_script())
    candidate_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_candidate_response_texts"
    )
    namespace = {}
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[candidate_function], type_ignores=[])), "<candidate-sides-test>", "exec"),
        namespace,
    )

    texts = namespace["_candidate_response_texts"](
        {
            "visible_text": "Claude -0.69",
            "response_sides": ["I am Claude", "The answer is -0.69"],
        }
    )

    assert texts == ["I am Claude", "The answer is -0.69"]


def test_auto_battle_emits_batch_round_and_summary_diagnostics():
    script = _arena_auto_battle_script()

    assert "[Auto-Battle][BATCH] batch=" in script
    assert "[Auto-Battle][ROUND-DIAG] batch=" in script
    assert "[Auto-Battle][THOUGHT-FOR-DIAG] batch=" in script
    assert "[Auto-Battle][SUMMARY] batch=" in script
    assert "_auto_battle_stats = {" in script
    assert "_diag_inc('captcha_failed')" in script
    assert "_diag_inc('rate_limited')" in script
    assert "_diag_inc('timed_out')" in script
    assert "_diag_inc('completed')" in script
    assert "'reason': 'captcha_redirected'" in script
    assert "'reason': 'new_chat_failed'" in script
    assert "'reason': 'send_failed'" in script


def test_auto_battle_round_diagnostics_include_store_model_metadata():
    script = _arena_auto_battle_script()

    assert "info['snapshot_ok'] = True" in script
    assert "info['snapshot_error'] = str(snapshot_error)" in script
    for key in (
        "conversation_id",
        "model_a",
        "model_b",
        "model_id_a",
        "model_id_b",
        "message_id_a",
        "message_id_b",
        "status_a",
        "status_b",
    ):
        assert repr(key) in script

    assert "page_thought_for = _page_has_thought_for_marker()" in script
    assert "data['page_thought_for'] = page_thought_for" in script
    assert "preview={_short_value(text, 260)}" in script
    assert "best_model={payload.get('best_model') or '-'}" in script
    assert "predictions={prediction_details}" in script


def test_arena_network_stream_is_split_into_independent_sides():
    module = ast.parse(_arena_auto_battle_script())
    split_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_split_arena_network_sides"
    )
    namespace = {"json": json}
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[split_function], type_ignores=[])), "<network-sides-test>", "exec"),
        namespace,
    )

    texts = namespace["_split_arena_network_sides"](
        'a0:"Claude opus"\nb0:"Claude"\nb0:" -0.69"\nad:{"finishReason":"stop"}'
    )

    assert texts == ["Claude opus", "Claude -0.69"]


@pytest.mark.parametrize(
    "message",
    [
        "You've reached a rate limit. Please try again in a moment.",
        "You’ve reached a rate limit. Please try again in a moment.",
        "You have reached a rate limit. Please try again in a moment.",
    ],
)
def test_rate_limit_text_triggers_immediate_proxy_rotation(message):
    module = ast.parse(_arena_auto_battle_script())
    reason_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_rate_limit_reason"
    )
    harness = ast.Module(body=[reason_function], type_ignores=[])
    ast.fix_missing_locations(harness)
    namespace = {}
    exec(compile(harness, "<arena-rate-limit-test>", "exec"), namespace)

    assert namespace["_rate_limit_reason"](message)

    script = _arena_auto_battle_script()
    assert "_rotate_if_rate_limited(info, source='reply-wait')" in script
    assert "_rotate_arena_proxy()" in script
    assert "not reply_info.get('rate_limited')" in script
    rate_limit_guard = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "not reply_info.get('rate_limited')"
    )
    guarded_calls = {
        node.func.id
        for node in ast.walk(ast.Module(body=rate_limit_guard.body, type_ignores=[]))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "record_arena_rule_candidates" in guarded_calls
    assert "_check_visible_claude_marker" in guarded_calls


@pytest.mark.parametrize(
    ("raise_statement", "expected_status"),
    [
        ("raise RuntimeError('python_script_cancelled')", "cancelled"),
        ("raise ValueError('connection lost')", "failed"),
    ],
)
def test_interruption_returns_urls_collected_so_far(raise_statement, expected_status):
    module = ast.parse(_arena_auto_battle_script())
    loop_index, loop = next(
        (index, node)
        for index, node in enumerate(module.body)
        if isinstance(node, ast.For)
    )
    loop = copy.deepcopy(loop)
    guarded_body = next(node for node in loop.body if isinstance(node, ast.Try))
    guarded_body.body = ast.parse(
        "_claude_hit_urls.append('https://arena.ai/c/partial')\n" + raise_statement
    ).body

    harness = ast.Module(body=[loop, *copy.deepcopy(module.body[loop_index + 1 :])], type_ignores=[])
    ast.fix_missing_locations(harness)
    end_statuses = []
    namespace = {
        "total_runs": 3,
        "_claude_hit_urls": [],
        "_claude_rejected_urls": set(),
        "_claude_filter_summary": lambda: "test filter",
        "_arena_detector_state": {"unavailable": False},
        "_auto_battle_batch_id": "test-batch",
        "_auto_battle_stats": {},
        "_diag_inc": lambda *args, **kwargs: None,
        "begin_command_loop": lambda *args: None,
        "end_command_loop": end_statuses.append,
        "time": time,
        "logger": _Logger(),
    }

    exec(compile(harness, "<arena-auto-battle-test>", "exec"), namespace)

    assert "https://arena.ai/c/partial" in namespace["result"]
    assert end_statuses == [expected_status]
