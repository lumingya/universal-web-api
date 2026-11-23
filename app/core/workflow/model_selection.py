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
from app.services.arena_direct_models import resolve_arena_direct_model as _service_resolve_arena_direct_model


def resolve_arena_direct_model(tab: Any, requested: str, catalog_config: Optional[dict] = None) -> Optional[dict]:
    """解析 Arena Direct 模型，优先使用 executor_actions 模块中的解析器（兼容测试 mock）。"""
    try:
        import app.core.workflow.executor_actions as ea
        fn = getattr(ea, "resolve_arena_direct_model", None)
        if callable(fn) and fn is not resolve_arena_direct_model:
            return fn(tab, requested, catalog_config=catalog_config)
    except Exception:
        pass
    return _service_resolve_arena_direct_model(tab, requested, catalog_config=catalog_config)

ModelSelectionHandlerFunc = Callable[[Any, str, str, Any, Optional[dict], bool], None]

_MODEL_SELECTION_HANDLERS: Dict[str, ModelSelectionHandlerFunc] = {}


def _select_model_via_mention(executor: Any, model: Dict[str, Any], timeout: float = 3.0) -> bool:
    """通过在输入框键入 @ 并在 Mention 候选列表中点击目标模型。"""
    try:
        from app.core.driver import driver_for_tab
        driver = driver_for_tab(executor.tab)
    except Exception:
        driver = None

    input_box_selector = 'form div[contenteditable="true"], div[contenteditable="true"], textarea[name="message"]'
    input_candidates = executor._find_visible_elements(input_box_selector)
    input_box = executor._first_positioned_element(input_candidates)
    if input_box is None:
        logger.warning("[SELECT_MODEL:MENTION] 输入框未找到")
        return False

    # 1. 聚焦输入框
    try:
        if driver:
            driver.run_js("arguments[0].focus(); arguments[0].click();", input_box)
        else:
            executor.tab.run_js("arguments[0].focus(); arguments[0].click();", input_box)
    except Exception as exc:
        logger.debug(f"[SELECT_MODEL:MENTION] 聚焦输入框失败: {exc}")

    time.sleep(0.15)

    # 2. 键入 @
    try:
        if driver:
            driver.run_cdp("Input.dispatchKeyEvent", type="keyDown", text="@", unmodifiedText="@", key="@")
            driver.run_cdp("Input.dispatchKeyEvent", type="keyUp", text="@", unmodifiedText="@", key="@")
        else:
            executor.tab.actions.type("@")
    except Exception as exc:
        logger.debug(f"[SELECT_MODEL:MENTION] 派发 @ 按键失败: {exc}")
        return False

    # 3. 等待 Mention 浮层出现
    deadline = time.time() + timeout
    mention_container = None
    while time.time() < deadline and not executor._check_cancelled():
        wrappers = executor._find_visible_elements('[data-radix-popper-content-wrapper], [cmdk-root], [role="listbox"]')
        positioned = [w for w in wrappers if executor._get_element_viewport_pos(w) is not None]
        if positioned:
            mention_container = positioned[0]
            break
        time.sleep(0.06)

    if mention_container is None:
        logger.warning("[SELECT_MODEL:MENTION] Mention 浮层超时未出现")
        return False

    # 4. 匹配目标模型
    exact_identifiers = {
        str(model.get(key) or "").strip().casefold()
        for key in ("arena_model_id", "name", "search_name", "display_name", "public_name")
        if str(model.get(key) or "").strip()
    }
    fuzzy_targets = [
        str(model.get(key) or "").strip().casefold()
        for key in ("search_name", "display_name", "public_name", "name")
        if str(model.get(key) or "").strip()
    ]

    option = None
    search_deadline = time.time() + timeout
    while time.time() < search_deadline and not executor._check_cancelled():
        candidates = executor._find_visible_elements(
            '[role="option"], [cmdk-item], [data-radix-collection-item], [data-radix-popper-content-wrapper] button, [role="listbox"] [role="option"]'
        )
        positioned_cands = [c for c in candidates if executor._get_element_viewport_pos(c) is not None]

        # Pass 1: 精确匹配
        for cand in positioned_cands:
            c_text = str(getattr(cand, "text", "") or getattr(cand, "raw_text", "") or "").strip().casefold()
            c_model_id = str(cand.attr("data-model-id") or "").strip().casefold()
            c_data_val = str(cand.attr("data-value") or "").strip().casefold()
            first_line = c_text.splitlines()[0].strip() if c_text else ""
            if {c_text, first_line, c_model_id, c_data_val}.intersection(exact_identifiers):
                option = cand
                break

        # Pass 2: 前缀及包含匹配
        if option is None:
            for cand in positioned_cands:
                c_text = str(getattr(cand, "text", "") or getattr(cand, "raw_text", "") or "").strip().casefold()
                first_line = c_text.splitlines()[0].strip() if c_text else ""
                for target in fuzzy_targets:
                    if not target:
                        continue
                    if c_text == target or first_line == target or first_line.startswith(target) or target in first_line:
                        option = cand
                        break
                if option is not None:
                    break

        if option is not None:
            break
        time.sleep(0.06)

    if option is None:
        logger.warning(f"[SELECT_MODEL:MENTION] Mention 列表中未找到模型项: {model.get('display_name')}")
        try:
            if driver:
                driver.run_cdp("Input.dispatchKeyEvent", type="keyDown", key="Escape")
                driver.run_cdp("Input.dispatchKeyEvent", type="keyUp", key="Escape")
        except Exception:
            pass
        return False

    # 5. 点击选项
    try:
        if driver:
            driver.run_js("arguments[0].click()", option)
        else:
            executor._stealth_click_element(option, target_key="arena_model_option", selector='[role="option"]')
    except Exception as exc:
        logger.debug(f"[SELECT_MODEL:MENTION] 点击模型候选项失败: {exc}")
        return False

    time.sleep(0.3)
    logger.info(f"[SELECT_MODEL:MENTION] 通过 Mention 成功选中模型: {model.get('display_name')}")
    return True


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
    raw_selector = str(
        selector
        or 'button[aria-haspopup="dialog"]:has(span.flex-1.truncate.text-start), button[aria-haspopup="dialog"]:has(span.flex-1.truncate.text-left), button[aria-haspopup="dialog"]:has(span.flex-1.truncate)'
    ).strip()
    trigger_selector = raw_selector
    dialog_opened = False

    try:
        with executor._page_interaction_slot("SELECT_MODEL", target_key or "model_select_btn") as acquired:
            if not acquired or executor._check_cancelled():
                return

            # 若新建会话后存在待等待标记，优先等待输入框稳定
            if getattr(executor, "_input_stability_wait_pending", False):
                stability_deadline = time.time() + 2.5
                while time.time() < stability_deadline and not executor._check_cancelled():
                    inps = executor._find_visible_elements('form div[contenteditable="true"], div[contenteditable="true"], textarea[name="message"]')
                    if inps and executor._get_element_viewport_pos(inps[0]) is not None:
                        break
                    time.sleep(0.08)

            if hasattr(executor, "_is_arena_model_active_in_fiber") and executor._is_arena_model_active_in_fiber(model):
                logger.info(
                    "[SELECT_MODEL] 页面已是目标模型(Fiber匹配)，跳过切换: "
                    f"model={model['display_name']}"
                )
                return

            if hasattr(executor, "_get_active_arena_model_via_fiber"):
                active_model = executor._get_active_arena_model_via_fiber()
                if active_model:
                    active_id = str(active_model.get("id") or "").strip().lower()
                    target_id = str(model.get("arena_model_id") or "").strip().lower()
                    active_name = str(active_model.get("displayName") or active_model.get("name") or "").strip().lower()
                    target_name = str(model.get("display_name") or model.get("name") or "").strip().lower()
                    if (active_id and target_id and active_id == target_id) or (active_name and target_name and active_name == target_name):
                        logger.info(
                            "[SELECT_MODEL] 页面已是目标模型(Fiber匹配)，跳过切换: "
                            f"model={model['display_name']}"
                        )
                        return

            def _is_model_trigger_element(ele: Any) -> bool:
                if ele is None:
                    return False
                aria = str(getattr(ele, "attrs", {}).get("aria-label", "") or "").strip().lower()
                if aria in {"add files", "mode", "modality: text", "attach", "send message", "stop generation"}:
                    return False
                if aria == "model" or "model" in aria:
                    return True
                text = str(getattr(ele, "text", "") or getattr(ele, "raw_text", "") or "").strip().lower()
                if text == "model":
                    return True
                return bool(executor._model_element_label(ele))

            triggers = executor._find_visible_elements(trigger_selector)
            trigger = executor._first_positioned_element(
                triggers,
                _is_model_trigger_element,
            )

            # fallback 1: 检查带 Model 语义的选择器
            if trigger is None:
                fallback_selector = (
                    'button[aria-label="Model"], '
                    'button[aria-haspopup="dialog"][aria-label="Model"], '
                    'form button[aria-haspopup="dialog"], '
                    'button[data-slot="tooltip-trigger"][aria-haspopup="dialog"]'
                )
                fallback_triggers = executor._find_visible_elements(fallback_selector)
                trigger = executor._first_positioned_element(
                    fallback_triggers,
                    _is_model_trigger_element,
                )

            # fallback 2: 旧模式下拉框切换 direct
            if trigger is None:
                mode_buttons = executor._find_visible_elements('button[role="combobox"]')
                mode_button = executor._first_positioned_element(
                    mode_buttons,
                    lambda ele: bool(str(getattr(ele, "text", "") or "").strip()),
                )
                if mode_button is not None:
                    current_mode = str(getattr(mode_button, "text", "") or "").strip()
                    if not (current_mode.casefold().startswith("direct") or current_mode.casefold().startswith("chat")):
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
                            time.sleep(0.05)
                        if direct_option is not None:
                            executor._stealth_click_element(
                                direct_option,
                                target_key="arena_mode_direct_option",
                                selector='[role="option"]',
                            )
                            time.sleep(0.1)
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
                    for cand in executor._find_visible_elements('button[aria-haspopup="dialog"], form button'):
                        c_text = str(getattr(cand, "text", "") or "").strip().lower()
                        c_aria = str(getattr(cand, "attrs", {}).get("aria-label", "") or "").strip().lower()
                        if c_text == "model" or c_aria == "model" or "model" in c_aria:
                            if executor._get_element_viewport_pos(cand) is not None:
                                trigger = cand
                                break

            current_label = executor._model_element_label(trigger) if trigger else ""
            # 检查当前是否已经是目标单模型（排除双模型并列图标状态）
            def _is_single_clean_target_trigger(trig: Any) -> bool:
                if trig is None:
                    return False
                lbl = executor._model_element_label(trig)
                if not executor._model_label_matches(lbl, model):
                    return False
                # 检查子 SVG 数量：单模型 pill 通常为 2 个 SVG（1个模型图标 + 1个下拉箭头）
                # 双模型对比通常为 3 个 SVG（2个模型图标 + 1个下拉箭头）且文本可能为空
                try:
                    svg_list = trig.eles('tag:svg')
                    if len(svg_list) > 2:
                        return False
                except Exception:
                    pass
                return True

            if trigger and _is_single_clean_target_trigger(trigger):
                logger.info(
                    "[SELECT_MODEL] 页面已是目标单模型，跳过切换: "
                    f"model={model['display_name']}"
                )
                return

            trigger_success = False
            if trigger is not None:
                executor._stealth_click_element(
                    trigger,
                    target_key=target_key or "model_select_btn",
                    selector=trigger_selector,
                )
                dialog_opened = True

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

                def _is_item_target(c: Any) -> bool:
                    c_data_val = str(c.attr("data-value") or "").strip().casefold()
                    c_model_id = str(c.attr("data-model-id") or "").strip().casefold()
                    c_id = str(c.attr("id") or "").strip().casefold()
                    c_label = executor._model_element_label(c).casefold()
                    c_raw = str(getattr(c, "text", "") or getattr(c, "raw_text", "") or "").strip().casefold()
                    c_first = c_raw.splitlines()[0].strip() if c_raw else ""
                    cand_keys = {k for k in (c_data_val, c_model_id, c_id, c_label, c_raw, c_first) if k}
                    if cand_keys.intersection(exact_identifiers):
                        return True
                    for target in fuzzy_targets:
                        if target and (c_data_val == target or c_label == target or c_first == target or c_first.startswith(target) or c_label.startswith(target) or target in c_first or target in c_label):
                            return True
                    return False

                # 排他选模机制：直接在页面上下文中原子化完成反选与目标选定，避免逐项 DOM 遍历等待
                try:
                    import json as _json
                    js_select_code = f"""
                    (async () => {{
                        const sleep = (ms) => new Promise(r => setTimeout(r, ms));
                        const targetId = {_json.dumps(str(model.get('arena_model_id') or '').lower())};
                        const exactList = {_json.dumps([s.lower() for s in exact_identifiers if s])};
                        const fuzzyList = {_json.dumps([s.lower() for s in fuzzy_targets if s])};

                        const isTarget = (opt) => {{
                            const mid = String(opt.getAttribute('data-model-id') || '').trim().toLowerCase();
                            const val = String(opt.getAttribute('data-value') || '').trim().toLowerCase();
                            const txt = String(opt.innerText || '').trim().toLowerCase();
                            const first = txt.split('\\n')[0].trim();
                            if (targetId && mid === targetId) return true;
                            if (exactList.includes(mid) || exactList.includes(val) || exactList.includes(txt) || exactList.includes(first)) return true;
                            for (const f of fuzzyList) {{
                                if (f && (txt === f || first === f || first.startsWith(f) || txt.includes(f))) return true;
                            }}
                            return false;
                        }};

                        // 1. 反选所有非目标已勾选项
                        const options = Array.from(document.querySelectorAll('[role="option"], [cmdk-item]'));
                        let uncheckCount = 0;
                        for (const opt of options) {{
                            const isChecked = !!opt.querySelector('svg.text-interactive-positive') || (opt.innerText || '').includes('✓');
                            if (isChecked && !isTarget(opt)) {{
                                opt.click();
                                uncheckCount++;
                                await sleep(100);
                            }}
                        }}

                        // 2. 搜索目标模型
                        const searchInput = document.querySelector('input[placeholder*="Search"]');
                        const searchText = {_json.dumps(str(model.get('search_name') or model.get('display_name') or model.get('name') or ''))};
                        if (searchInput && searchText) {{
                            searchInput.value = searchText;
                            searchInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            await sleep(150);
                        }}

                        // 3. 点击目标模型
                        const filtered = Array.from(document.querySelectorAll('[role="option"], [cmdk-item]'));
                        const targetOpt = filtered.find(isTarget);
                        let selected = false;
                        if (targetOpt) {{
                            const alreadyChecked = !!targetOpt.querySelector('svg.text-interactive-positive') || (targetOpt.innerText || '').includes('✓');
                            if (!alreadyChecked) {{
                                targetOpt.click();
                            }}
                            selected = true;
                            await sleep(150);
                        }}

                        // 4. 关闭弹窗（若仍打开）
                        if (document.querySelector('[role="dialog"], [cmdk-root]')) {{
                            window.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true }}));
                            window.dispatchEvent(new KeyboardEvent('keyup', {{ key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true }}));
                        }}

                        return {{ success: selected, uncheckCount: uncheckCount }};
                    }})()
                    """
                    from app.core.driver import driver_for_tab
                    drv = driver_for_tab(executor.tab)
                    res_val = None
                    try:
                        eval_res = drv.run_cdp(
                            "Runtime.evaluate",
                            expression=js_select_code,
                            returnByValue=True,
                            awaitPromise=True,
                        )
                        res_val = (eval_res or {}).get("result", {}).get("value")
                    except Exception:
                        res_val = drv.run_js(js_select_code)

                    if isinstance(res_val, dict) and res_val.get("success"):
                        trigger_success = True
                        dialog_opened = False
                        logger.info(f"[SELECT_MODEL] 单模型排他选定成功: model={model['display_name']}, 清理残留={res_val.get('uncheckCount')}")
                except Exception as exc:
                    logger.debug(f"[SELECT_MODEL] 原子化选模执行异常，回退常规选择: {exc}")

                if not trigger_success:
                    def _find_option_in_candidates():
                        candidates = executor._find_visible_elements('[role="option"]')
                        if not candidates:
                            candidates = executor._find_visible_elements(
                                '[data-radix-collection-item], [cmdk-item], [role="listbox"] > *'
                            )
                        positioned = [c for c in candidates if executor._get_element_viewport_pos(c) is not None]

                        # Pass 1: 精确匹配
                        for c in positioned:
                            c_data_val = str(c.attr("data-value") or "").strip().casefold()
                            c_model_id = str(c.attr("data-model-id") or "").strip().casefold()
                            c_id = str(c.attr("id") or "").strip().casefold()
                            c_label = executor._model_element_label(c).casefold()
                            c_raw = str(getattr(c, "text", "") or getattr(c, "raw_text", "") or "").strip().casefold()
                            c_first = c_raw.splitlines()[0].strip() if c_raw else ""
                            cand_keys = {k for k in (c_data_val, c_model_id, c_id, c_label, c_raw, c_first) if k}
                            if cand_keys.intersection(exact_identifiers):
                                return c

                        # Pass 2: 前缀匹配
                        for c in positioned:
                            c_data_val = str(c.attr("data-value") or "").strip().casefold()
                            c_label = executor._model_element_label(c).casefold()
                            c_raw = str(getattr(c, "text", "") or getattr(c, "raw_text", "") or "").strip().casefold()
                            c_first = c_raw.splitlines()[0].strip() if c_raw else ""
                            for target in fuzzy_targets:
                                if target and (c_data_val == target or c_label == target or c_first == target or c_first.startswith(target) or c_label.startswith(target)):
                                    return c

                        # Pass 3: 包含匹配
                        for c in positioned:
                            c_label = executor._model_element_label(c).casefold()
                            c_raw = str(getattr(c, "text", "") or getattr(c, "raw_text", "") or "").strip().casefold()
                            c_first = c_raw.splitlines()[0].strip() if c_raw else ""
                            for target in fuzzy_targets:
                                if target and (target in c_first or target in c_label):
                                    return c
                        return None

                    deadline = time.time() + min(timeout, 2.0)
                    search_input = None
                    while time.time() < deadline and not executor._check_cancelled():
                        inputs = [
                            inp for inp in executor._find_visible_elements('input[placeholder="Search models"]')
                            if executor._get_element_viewport_pos(inp) is not None
                        ]
                        if not inputs:
                            inputs = [
                                inp for inp in executor._find_visible_elements('input[placeholder*="Search"]')
                                if executor._get_element_viewport_pos(inp) is not None
                            ]
                        search_input = inputs[-1] if inputs else executor._first_positioned_element(
                            executor._find_visible_elements('input[placeholder*="Search"]')
                        )
                        if search_input is not None:
                            break
                        time.sleep(0.05)

                    if search_input is not None:
                        search_text = str(
                            model.get("search_name")
                            or model.get("display_name")
                            or model.get("public_name")
                            or model.get("name")
                            or ""
                        ).strip()
                        executor._text_handler.fill_via_clipboard_no_click(search_input, search_text)

                    option_deadline = time.time() + timeout
                    option = None
                    while time.time() < option_deadline and not executor._check_cancelled():
                        option = _find_option_in_candidates()
                        if option is not None:
                            break
                        time.sleep(0.05)

                    if option is not None:
                        executor._stealth_click_element(
                            option,
                            target_key="arena_model_option",
                            selector='[role="option"]',
                        )
                        time.sleep(0.2)
                        dialog_opened = False
                        trigger_success = True

                    # 若弹窗未自动关闭，显式按 ESC 关闭弹窗
                    if executor._find_visible_elements('[role="dialog"], [cmdk-root]'):
                        executor._close_arena_model_dialog()
                        dialog_opened = False

            # 若通过 Trigger 点击未成功，自动降级走 Mention (@) 选模
            if not trigger_success:
                if dialog_opened:
                    executor._close_arena_model_dialog()
                    dialog_opened = False
                logger.info("[SELECT_MODEL] Trigger 选模路径未命中，启用 Mention (@) 选模保底...")
                mention_ok = _select_model_via_mention(executor, model, timeout=timeout)
                if mention_ok:
                    return
                if trigger is None:
                    raise ElementNotFoundError("Arena Direct 模型选择按钮未找到，且 Mention 选模未成功")
                raise ElementNotFoundError(f"Arena Direct 模型选项未找到: {model['display_name']}")

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

                if hasattr(executor, "_is_arena_model_active_in_fiber") and executor._is_arena_model_active_in_fiber(model):
                    logger.info(
                        "[SELECT_MODEL] Arena Direct 模型切换确认(Fiber匹配): "
                        f"{model['display_name']}"
                    )
                    if executor._find_visible_elements('[role="dialog"], [cmdk-root]'):
                        executor._close_arena_model_dialog()
                    return

                if hasattr(executor, "_get_active_arena_model_via_fiber"):
                    active_model = executor._get_active_arena_model_via_fiber()
                    if active_model:
                        active_id = str(active_model.get("id") or "").strip().lower()
                        target_id = str(model.get("arena_model_id") or "").strip().lower()
                        active_name = str(active_model.get("displayName") or active_model.get("name") or "").strip().lower()
                        target_name = str(model.get("display_name") or model.get("name") or "").strip().lower()
                        if (active_id and target_id and active_id == target_id) or (active_name and target_name and active_name == target_name):
                            logger.info(
                                "[SELECT_MODEL] Arena Direct 模型切换确认(Fiber匹配): "
                                f"{model['display_name']}"
                            )
                            if executor._find_visible_elements('[role="dialog"], [cmdk-root]'):
                                executor._close_arena_model_dialog()
                            return

                # 检查弹窗是否已关闭且 trigger 存在
                if not executor._find_visible_elements('[role="dialog"], [cmdk-root]'):
                    if selected_triggers:
                        logger.info(
                            "[SELECT_MODEL] Arena Direct 模型切换完成(弹窗已关闭): "
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
