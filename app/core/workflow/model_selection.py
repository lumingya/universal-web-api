"""
app/core/workflow/model_selection.py - 通用模型选择与切换协议处理器

职责：
- 规范化工作流中的 SELECT_MODEL 动作执行接口；
- 提供模型选择处理器的注册与分发机制；
- 实现 Arena Direct 及通用模型的选择算法。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional, Protocol

from app.core.config import ElementNotFoundError, WorkflowError, logger
from app.services.arena_direct_models import resolve_arena_direct_model

ModelSelectionHandlerFunc = Callable[[Any, str, str, Any, Optional[dict], bool], None]

_MODEL_SELECTION_HANDLERS: Dict[str, ModelSelectionHandlerFunc] = {}


def register_model_selection_handler(
    name: str,
    handler: ModelSelectionHandlerFunc,
) -> None:
    """注册专有模型选择处理器。"""
    key = str(name or "").strip().lower()
    if not key:
        raise ValueError("handler name cannot be empty")
    if not callable(handler):
        raise ValueError(f"handler for {name} must be callable")
    _MODEL_SELECTION_HANDLERS[key] = handler
    logger.debug(f"[MODEL_SELECTION] 已注册模型选择处理器: {key}")


def get_model_selection_handler(name: str) -> Optional[ModelSelectionHandlerFunc]:
    """获取指定模型选择处理器。"""
    key = str(name or "").strip().lower()
    return _MODEL_SELECTION_HANDLERS.get(key)


def arena_direct_select_model(
    executor: Any,
    selector: str,
    target_key: str,
    value: Any,
    context: Optional[dict],
    optional: bool,
) -> None:
    """Arena Direct 专有模型选择实现。"""
    requested_model = str((context or {}).get("model") or "").strip()
    if not requested_model:
        logger.debug("[SELECT_MODEL] 请求未指定模型，保持页面当前选择")
        return

    model = resolve_arena_direct_model(
        executor.tab,
        requested_model,
        catalog_config=(context or {}).get("model_catalog"),
    )
    if not model:
        if optional:
            logger.warning(
                "[SELECT_MODEL] Arena Direct 模型已不在当前页面目录，跳过可选步骤: "
                f"model={executor._compact_log_value(requested_model, 100)}"
            )
            return
        raise WorkflowError("arena_direct_model_not_available")

    settings = value if isinstance(value, dict) else {}
    timeout = executor._coerce_float(settings.get("timeout"), 3.0, minimum=0.5)
    trigger_selector = str(
        selector
        or 'button[aria-haspopup="dialog"]:has(span.flex-1.truncate.text-left)'
    ).strip()
    dialog_opened = False

    try:
        with executor._page_interaction_slot("SELECT_MODEL", target_key or "model_select_btn") as acquired:
            if not acquired or executor._check_cancelled():
                return

            triggers = executor._find_visible_elements(trigger_selector)
            trigger = executor._first_positioned_element(
                triggers,
                lambda ele: bool(executor._model_element_label(ele)),
            )
            if trigger is None:
                mode_buttons = executor._find_visible_elements('button[role="combobox"]')
                mode_button = executor._first_positioned_element(
                    mode_buttons,
                    lambda ele: bool(str(getattr(ele, "text", "") or "").strip()),
                )
                if mode_button is None:
                    raise ElementNotFoundError("Arena 模式选择按钮未找到")

                current_mode = str(getattr(mode_button, "text", "") or "").strip()
                if not current_mode.casefold().startswith("direct"):
                    executor._stealth_click_element(
                        mode_button,
                        target_key="arena_mode_select_btn",
                        selector='button[role="combobox"]',
                    )
                    dialog_opened = True

                    direct_option = None
                    mode_deadline = time.time() + timeout
                    while time.time() < mode_deadline and not executor._check_cancelled():
                        for candidate in executor._find_visible_elements('[role="option"]'):
                            label = str(getattr(candidate, "text", "") or "").strip()
                            if (
                                label.casefold().startswith("direct")
                                and executor._get_element_viewport_pos(candidate) is not None
                            ):
                                direct_option = candidate
                                break
                        if direct_option is not None:
                            break
                        time.sleep(0.1)

                    if direct_option is None:
                        raise ElementNotFoundError("Arena Direct 模式选项未找到")

                    executor._stealth_click_element(
                        direct_option,
                        target_key="arena_mode_direct_option",
                        selector='[role="option"]',
                    )
                    time.sleep(0.2)
                    dialog_opened = False

                    refind_deadline = time.time() + timeout
                    while time.time() < refind_deadline and not executor._check_cancelled():
                        triggers = executor._find_visible_elements(trigger_selector)
                        trigger = executor._first_positioned_element(
                            triggers,
                            lambda ele: bool(executor._model_element_label(ele)),
                        )
                        if trigger is not None:
                            break
                        time.sleep(0.05)
                if trigger is None:
                    raise ElementNotFoundError("Arena Direct 模型选择按钮未找到")

            current_label = executor._model_element_label(trigger)
            if executor._model_label_matches(current_label, model):
                logger.info(
                    "[SELECT_MODEL] 页面已是目标模型，跳过切换: "
                    f"model={model['display_name']}"
                )
                return

            executor._stealth_click_element(
                trigger,
                target_key=target_key or "model_select_btn",
                selector=trigger_selector,
            )
            dialog_opened = True

            deadline = time.time() + timeout
            search_input = None
            while time.time() < deadline and not executor._check_cancelled():
                inputs = executor._find_visible_elements('input[placeholder="Search models"]')
                search_input = executor._first_positioned_element(inputs)
                if search_input is not None:
                    break
                time.sleep(0.05)
            if search_input is None:
                raise ElementNotFoundError("Arena Direct 模型搜索框未出现")

            search_text = str(
                model.get("search_name")
                or model.get("display_name")
                or model.get("public_name")
                or model.get("name")
                or ""
            ).strip()
            executor._text_handler.fill_via_clipboard_no_click(search_input, search_text)

            exact_identifiers = {
                str(model.get(key) or "").strip().casefold()
                for key in ("arena_model_id", "name", "search_name", "display_name", "public_name")
                if str(model.get(key) or "").strip()
            }
            if requested_model:
                exact_identifiers.add(str(requested_model).strip().casefold())

            fuzzy_targets = [
                str(model.get(key) or "").strip().casefold()
                for key in ("search_name", "display_name", "public_name", "name")
                if str(model.get(key) or "").strip()
            ]

            option = None
            while time.time() < deadline and not executor._check_cancelled():
                candidates = executor._find_visible_elements('[role="option"]')
                if not candidates:
                    candidates = executor._find_visible_elements(
                        '[data-radix-collection-item], [cmdk-item], [role="listbox"] > *'
                    )

                positioned_candidates = [
                    c for c in candidates
                    if executor._get_element_viewport_pos(c) is not None
                ]

                # --- Pass 1: 精确标识符全等匹配 ---
                for candidate in positioned_candidates:
                    cand_data_val = ""
                    try:
                        cand_data_val = str(candidate.attr("data-value") or "").strip().casefold()
                    except Exception:
                        pass

                    cand_val = ""
                    try:
                        cand_val = str(candidate.attr("value") or "").strip().casefold()
                    except Exception:
                        pass

                    cand_id = ""
                    try:
                        cand_id = str(candidate.attr("id") or "").strip().casefold()
                    except Exception:
                        pass

                    cand_label = executor._model_element_label(candidate).casefold()
                    cand_raw = str(getattr(candidate, "text", "") or getattr(candidate, "raw_text", "") or "").strip().casefold()
                    cand_first_line = cand_raw.splitlines()[0].strip() if cand_raw else ""

                    cand_keys = {k for k in (cand_data_val, cand_val, cand_id, cand_label, cand_raw, cand_first_line) if k}
                    if cand_keys.intersection(exact_identifiers):
                        option = candidate
                        break

                # --- Pass 2: 前缀及词边界匹配 ---
                if option is None:
                    for candidate in positioned_candidates:
                        cand_data_val = ""
                        try:
                            cand_data_val = str(candidate.attr("data-value") or "").strip().casefold()
                        except Exception:
                            pass
                        cand_label = executor._model_element_label(candidate).casefold()
                        cand_raw = str(getattr(candidate, "text", "") or getattr(candidate, "raw_text", "") or "").strip().casefold()
                        cand_first_line = cand_raw.splitlines()[0].strip() if cand_raw else ""

                        for target_name in fuzzy_targets:
                            if not target_name:
                                continue
                            if (
                                cand_data_val == target_name
                                or cand_label == target_name
                                or cand_first_line == target_name
                                or cand_first_line.startswith(target_name)
                                or cand_label.startswith(target_name)
                            ):
                                option = candidate
                                break
                        if option is not None:
                            break

                # --- Pass 3: 包含关系保底匹配 ---
                if option is None:
                    for candidate in positioned_candidates:
                        cand_label = executor._model_element_label(candidate).casefold()
                        cand_raw = str(getattr(candidate, "text", "") or getattr(candidate, "raw_text", "") or "").strip().casefold()
                        cand_first_line = cand_raw.splitlines()[0].strip() if cand_raw else ""

                        for target_name in fuzzy_targets:
                            if not target_name:
                                continue
                            if target_name in cand_first_line or target_name in cand_label:
                                option = candidate
                                break
                        if option is not None:
                            break

                if option is not None:
                    break
                time.sleep(0.05)

            if option is None:
                raise ElementNotFoundError(
                    f"Arena Direct 模型选项未找到: {model['display_name']}"
                )

            executor._stealth_click_element(
                option,
                target_key="arena_model_option",
                selector='[role="option"]',
            )
            dialog_opened = False

            verify_deadline = time.time() + min(timeout, 2.0)
            while time.time() < verify_deadline and not executor._check_cancelled():
                selected_triggers = executor._find_visible_elements(trigger_selector)
                selected = executor._first_positioned_element(
                    selected_triggers,
                    lambda ele: executor._model_label_matches(
                        executor._model_element_label(ele),
                        model,
                    ),
                )
                if selected is not None:
                    logger.info(
                        "[SELECT_MODEL] Arena Direct 模型切换完成: "
                        f"{current_label or '-'} -> {model['display_name']}"
                    )
                    return
                time.sleep(0.08)
            raise WorkflowError("arena_direct_model_switch_unconfirmed")
    except Exception as exc:
        if optional:
            logger.warning(f"[SELECT_MODEL] 可选模型切换失败，已跳过: {exc}")
            return
        raise
    finally:
        if dialog_opened:
            executor._close_arena_model_dialog()


# 注册内置模型选择处理器
register_model_selection_handler("arena_direct", arena_direct_select_model)
register_model_selection_handler("default", arena_direct_select_model)


def execute_model_selection(
    executor: Any,
    selector: str,
    target_key: str,
    value: Any,
    context: Optional[dict],
    optional: bool,
    handler_name: str = "arena_direct",
) -> None:
    """分发并执行模型选择。"""
    handler = get_model_selection_handler(handler_name) or get_model_selection_handler("default")
    if handler is None:
        raise WorkflowError(f"No model selection handler registered for {handler_name}")
    handler(executor, selector, target_key, value, context, optional)


__all__ = [
    "arena_direct_select_model",
    "execute_model_selection",
    "get_model_selection_handler",
    "register_model_selection_handler",
]
