import json
from types import SimpleNamespace

import pytest

from app.core.browser.workflow import BrowserWorkflowMixin
from app.core.workflow.input_chunking import (
    InputChunkingError,
    PromptChunk,
    plan_prompt_chunks,
    render_chunk_instruction,
)
from app.services.config.engine import ConfigEngine


def _no_instruction(_index: int, _total: int) -> str:
    return ""


def _content_chunk(text: str) -> str:
    return "data: " + json.dumps(
        {"choices": [{"delta": {"content": text}}]},
        ensure_ascii=False,
    ) + "\n\n"


def _error_chunk(message: str) -> str:
    return "data: " + json.dumps(
        {"error": {"message": message, "code": "test_error"}},
        ensure_ascii=False,
    ) + "\n\n"


class _Formatter:
    @staticmethod
    def pack_finish() -> str:
        return "data: [DONE]\n\n"


class _WorkflowHarness(BrowserWorkflowMixin):
    def __init__(self, plan, responses):
        self.plan = plan
        self.responses = responses
        self.calls = []
        self.formatter = _Formatter()
        self._should_stop_checker = lambda: False

    def _plan_workflow_input_chunks(self, *_args, **_kwargs):
        return self.plan

    def _execute_workflow_stream_once(self, _session, _messages, **kwargs):
        self.calls.append(kwargs)
        yield from self.responses[kwargs["_prepared_prompt"]]


class _PlannerHarness(BrowserWorkflowMixin):
    def _get_config_engine(self):
        return SimpleNamespace(
            get_site_config=lambda *_args, **_kwargs: {
                "file_paste": {
                    "enabled": True,
                    "threshold": 1000,
                    "temp_file_type": "chunk",
                },
                "prompt_padding": {"enabled": True},
            }
        )

    @staticmethod
    def _build_prompt_from_messages(messages):
        return "|".join(str(message.get("content") or "") for message in messages)

    @staticmethod
    def _apply_prompt_padding(prompt, _config):
        return "padding:" + prompt


def _workflow_plan() -> list[PromptChunk]:
    return [
        PromptChunk(1, 2, "first", "wait", "first\n\nwait"),
        PromptChunk(2, 2, "second", "answer", "second\n\nanswer"),
    ]


def test_examples_use_small_early_chunks_when_instructions_are_preaccounted():
    first = plan_prompt_chunks(
        "x" * 134999,
        120000,
        instruction_renderer=_no_instruction,
    )
    second = plan_prompt_chunks(
        "x" * 239998,
        120000,
        instruction_renderer=_no_instruction,
    )

    assert [len(chunk.prompt) for chunk in first] == [60000, 74999]
    assert [len(chunk.prompt) for chunk in second] == [119999, 119999]


def test_real_instructions_are_counted_and_content_round_trips():
    content = "内容" * 67499 + "尾"
    chunks = plan_prompt_chunks(content, 120000)

    assert len(chunks) == 2
    assert len(chunks[0].prompt) == 60000
    assert all(len(chunk.prompt) <= 120000 for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks) == content
    assert "第 1/2 部分" in chunks[0].instruction
    assert "最后一部分" in chunks[-1].instruction


def test_instruction_budget_can_require_an_additional_chunk_near_capacity():
    chunks = plan_prompt_chunks("x" * 239998, 120000)

    assert len(chunks) == 3
    assert all(len(chunk.prompt) <= 120000 for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks) == "x" * 239998


def test_no_split_at_limit_and_tiny_limit_is_rejected():
    unchanged = plan_prompt_chunks("界" * 1000, 1000)
    assert len(unchanged) == 1
    assert unchanged[0].prompt == "界" * 1000

    with pytest.raises(InputChunkingError):
        plan_prompt_chunks("x" * 100, 10)


def test_intermediate_reply_is_hidden_and_only_final_reply_is_returned():
    plan = _workflow_plan()
    harness = _WorkflowHarness(
        plan,
        {
            plan[0].prompt: [_content_chunk("understood"), "data: [DONE]\n\n"],
            plan[1].prompt: [_content_chunk("final answer"), "data: [DONE]\n\n"],
        },
    )
    session = SimpleNamespace(id="chunk-session")

    output = list(harness._execute_workflow_stream(session, [{"role": "user", "content": "all"}]))

    assert output == [_content_chunk("final answer"), "data: [DONE]\n\n"]
    assert len(harness.calls) == 2
    assert harness.calls[0]["_chunk_continuation"] is False
    assert harness.calls[1]["_chunk_continuation"] is True
    assert harness.calls[0]["_include_message_images"] is True
    assert harness.calls[1]["_include_message_images"] is False
    assert harness.calls[0]["allow_media_postprocess"] is False
    assert harness.calls[1]["allow_media_postprocess"] is True


def test_intermediate_error_stops_following_chunks_and_is_returned():
    plan = _workflow_plan()
    error = _error_chunk("first block failed")
    harness = _WorkflowHarness(
        plan,
        {
            plan[0].prompt: [_content_chunk("partial"), error, "data: [DONE]\n\n"],
            plan[1].prompt: [_content_chunk("must not run"), "data: [DONE]\n\n"],
        },
    )
    session = SimpleNamespace(id="chunk-session")

    output = list(harness._execute_workflow_stream(session, [{"role": "user", "content": "all"}]))

    assert output == [error, "data: [DONE]\n\n"]
    assert len(harness.calls) == 1


def test_rendered_instruction_numbers_track_all_parts():
    assert "第 9/10 部分" in render_chunk_instruction(9, 10)
    assert "第 10/10 部分" in render_chunk_instruction(10, 10)


def test_planner_counts_all_prepared_messages_and_padding():
    harness = _PlannerHarness()
    session = SimpleNamespace(
        id="planner-session",
        preset_name="preset",
        tab=SimpleNamespace(url="https://example.com/chat", html="<html></html>"),
    )
    messages = [
        {"role": "system", "content": "s" * 700},
        {"role": "user", "content": "u" * 400},
    ]

    chunks = harness._plan_workflow_input_chunks(
        session,
        messages,
        preset_name="preset",
        requested_model=None,
    )

    assert len(chunks) == 2
    assert "".join(chunk.content for chunk in chunks) == "padding:" + "s" * 700 + "|" + "u" * 400


def test_config_validation_accepts_chunk_strategy():
    engine = object.__new__(ConfigEngine)
    validated = engine._validate_file_paste_config(
        {"enabled": True, "threshold": 120000, "temp_file_type": "CHUNK"}
    )

    assert validated["enabled"] is True
    assert validated["threshold"] == 120000
    assert validated["temp_file_type"] == "chunk"


def test_generic_site_schema_accepts_chunk_strategy():
    from app.models.schemas import validate_site_config

    assert validate_site_config(
        {
            "selectors": {},
            "workflow": [],
            "file_paste": {
                "enabled": True,
                "threshold": 120000,
                "temp_file_type": "chunk",
            },
        }
    ) is True
