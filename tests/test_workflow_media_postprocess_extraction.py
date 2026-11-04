"""`_run_media_postprocess` 提取后的契约与隔离行为测试。

该方法是步骤 6b 从 `_execute_workflow_stream_once`（1348 行）尾部提出的
159 行多模态媒体提取块。提出前它被埋在一个 1200 行的生成器里，无法单独
调用、也无法单独测试；提出后这里既锁定它的**参数契约**，也第一次给它
直接的隔离行为覆盖。

方法体与提取前逐字节相同（仅缩进变化），因此本文件用**参数清单**而非
源码快照来锁定契约——后续若有人给方法加参数，说明又一次把外层状态拖了
进来，测试会立刻报警。
"""

from __future__ import annotations

import ast
import inspect
from typing import Any, Dict, List, Optional

import app.core.browser.workflow as workflow_mod
from app.core.config_parts.sse_formatter import SSEFormatter
from app.services.arena_image_generation import ArenaImageGenerationError

# 提取时确定的参数契约。self 不计入。
EXPECTED_PARAMS = (
    "tab",
    "session",
    "executor",
    "extractor",
    "image_config",
    "selectors",
    "result_container_selector",
    "user_images",
    "arena_image_generation_active",
    "should_run_media_postprocess",
    "dom_stream_media_items",
    "stream_media_items",
    "media_generation_state",
    "media_dom_baseline",
    "response_text_hint",
    "request_text_hint",
    "media_postprocess_diag",
    "_combined_stop_checker",
)


class _FakeSession:
    def __init__(self) -> None:
        self.id = "SESSION-1"
        self.tab = object()
        self.current_domain = "example.com"
        self.preset_name = None


class _MediaHost(workflow_mod.BrowserWorkflowMixin):
    """只提供 _run_media_postprocess 用到的 formatter。"""

    def __init__(self) -> None:
        self.formatter = SSEFormatter()


def _drive(**overrides: Any) -> tuple[List[str], bool]:
    """驱动一次媒体后处理，返回 (产出的 SSE 块, 是否中止)。"""
    kwargs: Dict[str, Any] = dict(
        tab=object(),
        session=_FakeSession(),
        executor=object(),
        extractor=None,
        image_config={},
        selectors={},
        result_container_selector="",
        user_images=[],
        arena_image_generation_active=False,
        should_run_media_postprocess=False,
        dom_stream_media_items=[],
        stream_media_items=None,
        media_generation_state=None,
        media_dom_baseline=None,
        response_text_hint="",
        request_text_hint="",
        media_postprocess_diag={},
        _combined_stop_checker=lambda: False,
    )
    kwargs.update(overrides)

    gen = _MediaHost()._run_media_postprocess(**kwargs)
    chunks: List[str] = []
    try:
        while True:
            chunks.append(next(gen))
    except StopIteration as stop:
        return chunks, bool(stop.value)


# ---------------------------------------------------------------------------
# 契约锁定
# ---------------------------------------------------------------------------


def test_method_exists_as_generator_function():
    fn = getattr(workflow_mod.BrowserWorkflowMixin, "_run_media_postprocess", None)
    assert fn is not None, "_run_media_postprocess 应存在于 BrowserWorkflowMixin"
    assert inspect.isgeneratorfunction(fn), "必须是生成器函数（内含 yield）"


def test_parameter_contract_is_unchanged():
    """参数清单即该方法与外层的耦合面，任何增删都必须是有意为之。"""
    fn = workflow_mod.BrowserWorkflowMixin._run_media_postprocess
    actual = tuple(inspect.signature(fn).parameters)[1:]  # 去掉 self
    assert actual == EXPECTED_PARAMS


def test_source_media_markers_left_the_caller():
    """提取后，主函数里不应再残留媒体提取的实现细节。"""
    src = inspect.getsource(workflow_mod.BrowserWorkflowMixin._execute_workflow_stream_once)
    for marker in ("ArenaImageGenerationGuard(", "MD_MEDIA", "_merge_dom_and_stream_media_items("):
        assert marker not in src, f"主函数仍残留媒体提取细节: {marker!r}"


def test_caller_delegates_via_yield_from():
    src = inspect.getsource(workflow_mod.BrowserWorkflowMixin._execute_workflow_stream_once)
    assert "yield from self._run_media_postprocess(" in src
    assert "workflow_aborted = yield from" in src, "返回值应回写 workflow_aborted"


def test_no_new_module_level_imports_were_needed():
    """提取不应引入新的模块级导入（方法体只用了原有名字）。"""
    tree = ast.parse(inspect.getsource(workflow_mod))
    imports = {
        a.asname or a.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    # 方法体引用的外部名都应在模块导入集合里
    for name in ("ArenaImageGenerationError", "filter_reference_images", "validate_generated_images"):
        assert name in imports, f"{name} 未在模块顶层导入"


# ---------------------------------------------------------------------------
# 隔离行为（提取前无法触达的路径）
# ---------------------------------------------------------------------------


def test_skip_path_yields_nothing_and_does_not_abort():
    """不启用图片提取、非 Arena 图片生成时：不产出任何块，也不中止。"""
    chunks, aborted = _drive()
    assert chunks == []
    assert aborted is False


def test_skip_path_ignores_stop_checker_when_nothing_to_do():
    chunks, aborted = _drive(_combined_stop_checker=lambda: True)
    assert chunks == []
    assert aborted is False


def test_media_items_stay_empty_when_extraction_disabled():
    """should_run_media_postprocess=False 时即便有 DOM 项也不应产出媒体块。"""
    chunks, aborted = _drive(
        dom_stream_media_items=[{"media_type": "image", "url": "https://x/y.png"}],
        should_run_media_postprocess=False,
    )
    assert chunks == []
    assert aborted is False


def test_arena_terminal_error_aborts_and_emits_one_error_block(monkeypatch):
    """Arena 终态错误：置 workflow_aborted，产出且仅产出一个错误块。"""
    err = ArenaImageGenerationError("arena_image_failed", "图片生成失败")

    class _Observation:
        is_complete = True
        terminal_error: Optional[Exception] = err

    class _Guard:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def observe(self, _flag: bool) -> _Observation:
            return _Observation()

    monkeypatch.setattr(workflow_mod, "ArenaImageGenerationGuard", _Guard)

    chunks, aborted = _drive(arena_image_generation_active=True)
    assert aborted is True
    assert len(chunks) == 1
    assert "arena_image_failed" in chunks[0]
    assert "图片生成失败" in chunks[0]


def test_arena_guard_not_consulted_when_generation_inactive(monkeypatch):
    """非 Arena 图片生成请求时不应构造 guard（避免无谓的 DOM 访问）。"""
    calls: List[Any] = []

    class _Guard:
        def __init__(self, *a: Any, **k: Any) -> None:
            calls.append(a)

        def observe(self, _flag: bool) -> Any:
            raise AssertionError("不应被调用")

    monkeypatch.setattr(workflow_mod, "ArenaImageGenerationGuard", _Guard)
    _drive(arena_image_generation_active=False)
    assert calls == []


def test_generic_exception_is_swallowed_without_aborting(monkeypatch):
    """普通异常只告警，不改变 workflow_aborted——媒体失败不应打断响应。"""

    class _Guard:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def observe(self, _flag: bool) -> Any:
            raise RuntimeError("boom")

    monkeypatch.setattr(workflow_mod, "ArenaImageGenerationGuard", _Guard)
    chunks, aborted = _drive(arena_image_generation_active=True)
    assert chunks == []
    assert aborted is False


def test_arena_error_message_is_used_not_the_exception_type(monkeypatch):
    """错误块的 message/code 取自异常的 .message/.code，而非 str(exc)。"""
    err = ArenaImageGenerationError("nsfw_rejected", "内容被拒绝")

    class _Observation:
        is_complete = True
        terminal_error: Optional[Exception] = err

    class _Guard:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def observe(self, _flag: bool) -> Any:
            return _Observation()

    monkeypatch.setattr(workflow_mod, "ArenaImageGenerationGuard", _Guard)
    chunks, _ = _drive(arena_image_generation_active=True)
    assert "内容被拒绝" in chunks[0]
    assert "nsfw_rejected" in chunks[0]
