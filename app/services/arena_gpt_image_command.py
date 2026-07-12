"""Dedicated Arena blind-test runner for GPT Image 2 C2PA detection."""

from __future__ import annotations

import base64
import os
import random
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests


GPT_IMAGE_MARKERS = (
    b"gpt-image",
    b"OpenAI Media Service API",
    b"OpenAI OpCo, LLC",
)


def inspect_gpt_image2_png(payload: bytes) -> dict[str, Any]:
    """Return strong GPT Image 2 evidence found in a PNG C2PA caBX chunk."""
    data = bytes(payload or b"")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return {"matched": False, "reason": "not_png", "markers": []}

    offset = 8
    cabx_chunks: list[bytes] = []
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            return {"matched": False, "reason": "invalid_png", "markers": []}
        if chunk_type == b"caBX":
            cabx_chunks.append(data[offset + 8 : offset + 8 + length])
        offset = chunk_end
        if chunk_type == b"IEND":
            break

    if not cabx_chunks:
        return {"matched": False, "reason": "c2pa_missing", "markers": []}

    manifest = b"".join(cabx_chunks)
    marker_hits = [marker.decode("ascii") for marker in GPT_IMAGE_MARKERS if marker in manifest]
    # CBOR text values use one-byte length prefixes here: i=9 for gpt-image,
    # c=3 for 2.0. Requiring the adjacent fields prevents a stray version
    # elsewhere in the signed manifest from producing a false positive.
    has_model = b"dnameigpt-imagegversionc2.0" in manifest
    has_openai_claim = b"OpenAI Media Service API" in manifest
    matched = has_model and has_openai_claim
    return {
        "matched": matched,
        "reason": "gpt_image_2_c2pa" if matched else "c2pa_not_gpt_image_2",
        "markers": marker_hits,
        "caBX_bytes": len(manifest),
    }


def _value(values: dict[str, Any], key: str, default: Any = "") -> Any:
    value = values.get(key, default)
    return default if value is None else value


def _sleep(ctx: dict[str, Any], seconds: float, step: float = 0.2) -> None:
    deadline = time.time() + max(0.0, float(seconds))
    while time.time() < deadline:
        ctx["raise_if_cancelled"]()
        ctx["raise_if_command_loop_cancelled"]()
        ctx["reset_timeout"]()
        time.sleep(min(step, max(0.0, deadline - time.time())))


def _current_url(tab: Any) -> str:
    try:
        return str(tab.run_js("return location.href") or "").strip()
    except Exception:
        return str(getattr(tab, "url", "") or "").strip()


def _same_page_url(current_url: str, target_url: str) -> bool:
    try:
        current = urlsplit(str(current_url or "").strip())
        target = urlsplit(str(target_url or "").strip())
        return (
            current.scheme.lower() == target.scheme.lower()
            and current.netloc.lower() == target.netloc.lower()
            and current.path.rstrip("/") == target.path.rstrip("/")
        )
    except Exception:
        return False


def _new_image_chat_ready(tab: Any, redirect_url: str) -> bool:
    if not _same_page_url(_current_url(tab), redirect_url):
        return False
    input_box = _find(tab, "css:textarea[name='message']", 1.0)
    if not input_box:
        return False
    try:
        return int(input_box.run_js("return String(this.value || '').length")) == 0
    except Exception:
        return False


def _visible_image_sources(tab: Any) -> list[str]:
    script = """
        return Array.from(document.images)
            .filter((img) => {
                const r = img.getBoundingClientRect();
                const s = getComputedStyle(img);
                return img.isConnected && img.complete && img.naturalWidth >= 256
                    && img.naturalHeight >= 256 && r.width >= 120 && r.height >= 120
                    && r.bottom > 0 && r.right > 0 && r.top < innerHeight
                    && s.display !== 'none' && s.visibility !== 'hidden';
            })
            .map((img) => img.currentSrc || img.src)
            .filter(Boolean);
    """
    try:
        return list(dict.fromkeys(str(item) for item in (tab.run_js(script) or []) if item))
    except Exception:
        return []


def _read_image_bytes(tab: Any, url: str, timeout: float = 20.0) -> bytes:
    if url.startswith("data:"):
        try:
            return base64.b64decode(url.split(",", 1)[1])
        except Exception:
            return b""

    if url.startswith(("http://", "https://")):
        try:
            response = requests.get(
                url,
                headers={"Referer": _current_url(tab), "User-Agent": "Mozilla/5.0"},
                timeout=timeout,
            )
            response.raise_for_status()
            return response.content
        except Exception:
            pass

    try:
        result = tab.run_js(
            """
            return fetch(arguments[0], { credentials: 'include' })
                .then((response) => response.arrayBuffer())
                .then((buffer) => {
                    const bytes = new Uint8Array(buffer);
                    let binary = '';
                    const step = 0x8000;
                    for (let i = 0; i < bytes.length; i += step) {
                        binary += String.fromCharCode(...bytes.subarray(i, i + step));
                    }
                    return btoa(binary);
                });
            """,
            url,
        )
        return base64.b64decode(str(result or ""))
    except Exception:
        return b""


def _find(tab: Any, selector: str, timeout: float = 1.0) -> Any:
    try:
        return tab.ele(selector, timeout=timeout)
    except Exception:
        return None


def _upload_reference_image(ctx: dict[str, Any], path: str) -> bool:
    tab = ctx["tab"]
    logger = ctx["logger"]
    image_path = Path(os.path.expandvars(os.path.expanduser(path))).resolve()
    if not image_path.is_file():
        raise RuntimeError(f"reference_image_missing:{image_path}")

    inputs = []
    try:
        inputs = list(tab.eles('css:input[type="file"]', timeout=1.5) or [])
    except Exception:
        pass
    if not inputs:
        for selector in (
            "xpath://button[contains(., 'Upload') or contains(., '上传')]",
            "css:button[aria-label*='upload' i]",
            "css:button[aria-label*='image' i]",
        ):
            button = _find(tab, selector, 0.5)
            if button:
                try:
                    button.click()
                    _sleep(ctx, 0.5)
                except Exception:
                    pass
                try:
                    inputs = list(tab.eles('css:input[type="file"]', timeout=1.0) or [])
                except Exception:
                    inputs = []
                if inputs:
                    break

    for file_input in inputs:
        try:
            file_input.input(str(image_path))
            file_input.run_js(
                "this.dispatchEvent(new Event('input',{bubbles:true}));"
                "this.dispatchEvent(new Event('change',{bubbles:true}));"
            )
            logger.info(f"[GPT-IMAGE-2] reference image uploaded: {image_path.name}")
            _sleep(ctx, 2.0)
            return True
        except Exception as error:
            logger.debug(f"[GPT-IMAGE-2] file input upload failed: {error}")
    return False


def _refresh_page(ctx: dict[str, Any]) -> None:
    tab = ctx["tab"]
    try:
        tab.refresh()
    except Exception:
        tab.run_js("location.reload()")
    _sleep(ctx, 2.0)


def _is_generating(tab: Any) -> bool:
    try:
        result = tab.run_js(
            """
            return (() => {
                const visible = (element) => {
                    if (!(element instanceof HTMLElement) || !element.isConnected) return false;
                    const rect = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    return rect.width >= 8 && rect.height >= 8
                        && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const hasStopButton = Array.from(document.querySelectorAll(
                    'button[aria-label="Stop generation"], button[aria-label*="Stop" i], button[data-testid*="stop" i]'
                )).some(visible);
                if (hasStopButton) return true;
                return Array.from(document.querySelectorAll('main *, [role="main"] *'))
                    .filter(visible)
                    .some((element) => /^generating\\s+image(?:\\.\\.\\.)?$/i.test(
                        String(element.textContent || '').trim()
                    ));
            })();
            """
        )
        return result is True
    except Exception:
        return False


def _is_generation_stopped(tab: Any) -> bool:
    try:
        result = tab.run_js(
                """
                return (() => {
                    const visible = (element) => {
                        if (!(element instanceof HTMLElement) || !element.isConnected) return false;
                        const rect = element.getBoundingClientRect();
                        const style = getComputedStyle(element);
                        return rect.width >= 8 && rect.height >= 8
                            && rect.bottom > 0 && rect.right > 0
                            && rect.top < innerHeight && rect.left < innerWidth
                            && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    return Array.from(document.querySelectorAll('main *, [role="main"] *'))
                        .filter(visible)
                        .some((element) => /^generation\\s+stopped$/i.test(
                            String(element.textContent || '').trim()
                        ));
                })();
                """
        )
        return result is True
    except Exception:
        return False


def _stop_generation(ctx: dict[str, Any]) -> bool:
    tab = ctx["tab"]
    logger = ctx["logger"]
    for selector in (
        'css:button[aria-label="Stop generation"]',
        "css:button[aria-label*='Stop' i]",
        "css:button[data-testid*='stop' i]",
    ):
        button = _find(tab, selector, 0.8)
        if not button:
            continue
        try:
            button.click()
            logger.warning("[GPT-IMAGE-2] generation still active after recovery minute; clicked stop")
            _sleep(ctx, 2.0)
            return True
        except Exception as error:
            logger.warning(f"[GPT-IMAGE-2] stop generation click failed: {error}")
    logger.error("[GPT-IMAGE-2] generation is active but stop button was not found")
    return False


def _stop_generation_and_confirm(ctx: dict[str, Any]) -> bool:
    if not _stop_generation(ctx):
        return False
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if _is_generation_stopped(ctx["tab"]) or not _is_generating(ctx["tab"]):
            return True
        _sleep(ctx, 0.25, 0.05)
    return False


def _vote_a_better(ctx: dict[str, Any]) -> bool:
    tab = ctx["tab"]
    logger = ctx["logger"]
    script = r"""
        return (() => {
            const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
            const visible = (element) => {
                if (!(element instanceof HTMLElement) || !element.isConnected) return false;
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return rect.width >= 8 && rect.height >= 8 && rect.bottom > 0 && rect.right > 0
                    && rect.top < innerHeight && rect.left < innerWidth
                    && style.display !== 'none' && style.visibility !== 'hidden'
                    && style.pointerEvents !== 'none' && Number(style.opacity || '1') > 0.2;
            };
            const matches = (text) => {
                const value = norm(text);
                return value.includes('a 更好') || value.includes('a is better')
                    || value === 'a better' || value.includes('← a');
            };
            const elements = Array.from(document.querySelectorAll('button, [role="button"]'));
            const button = elements.find((element) => {
                const text = element.innerText || element.textContent || element.getAttribute('aria-label');
                return visible(element) && !element.disabled
                    && element.getAttribute('aria-disabled') !== 'true' && matches(text);
            });
            if (!button) return false;
            button.scrollIntoView({block: 'center', inline: 'center'});
            button.click();
            return true;
        })();
    """
    try:
        clicked = bool(tab.run_js(script))
    except Exception as error:
        logger.warning(f"[GPT-IMAGE-2] failed to click A better: {error}")
        return False
    if clicked:
        logger.info("[GPT-IMAGE-2] non-target result; selected A better")
        _sleep(ctx, 1.0)
    else:
        logger.warning("[GPT-IMAGE-2] non-target result; A better button was not found")
    return clicked


def _start_new_image_chat(ctx: dict[str, Any], redirect_url: str) -> bool:
    tab = ctx["tab"]
    logger = ctx["logger"]
    selectors = (
        "css:a[href='/image']",
        "xpath://a[contains(., 'New Chat') or contains(., 'New chat')]",
        "xpath://button[contains(., 'New Chat') or contains(., 'New chat')]",
    )
    if _new_image_chat_ready(tab, redirect_url):
        logger.stream(f"[GPT-IMAGE-2] new image chat ready: url={_current_url(tab)}")
        return True

    for selector in selectors:
        element = _find(tab, selector, 0.8)
        if not element:
            continue
        try:
            element.click()
            _sleep(ctx, 1.2)
            if _new_image_chat_ready(tab, redirect_url):
                logger.stream(f"[GPT-IMAGE-2] new image chat confirmed: url={_current_url(tab)}")
                return True
        except Exception:
            continue
    try:
        logger.stream(f"[GPT-IMAGE-2] New Chat not confirmed; navigating to {redirect_url}")
        tab.get(redirect_url)
        _sleep(ctx, 2.0)
        return _new_image_chat_ready(tab, redirect_url)
    except Exception:
        return False


def _send_prompt(
    ctx: dict[str, Any], prompt: str, *, require_clear_confirmation: bool = False
) -> bool:
    tab = ctx["tab"]
    logger = ctx["logger"]
    input_box = _find(tab, "css:textarea[name='message']", 5.0)
    if not input_box:
        return False
    if require_clear_confirmation:
        try:
            replaced = input_box.run_js(
                """
                const value = String(arguments[0] || '');
                const setter = Object.getOwnPropertyDescriptor(
                    HTMLTextAreaElement.prototype, 'value'
                ).set;
                this.focus();
                setter.call(this, value);
                this.dispatchEvent(new InputEvent('input', {
                    bubbles: true,
                    data: value,
                    inputType: 'insertFromPaste'
                }));
                this.dispatchEvent(new Event('change', {bubbles: true}));
                this.setSelectionRange(value.length, value.length);
                return String(this.value || '');
                """,
                prompt,
            )
        except Exception as error:
            logger.error(f"[GPT-IMAGE-2] failed to replace composer text: {error}")
            return False
        if str(replaced or "") != prompt:
            logger.error("[GPT-IMAGE-2] composer replacement did not match the prompt")
            return False

        stable_checks = 0
        deadline = time.time() + 1.5
        while time.time() < deadline:
            current_input = _find(tab, "css:textarea[name='message']", 0.3)
            try:
                current_value = str(
                    current_input.run_js("return String(this.value || '')") if current_input else ""
                )
            except Exception:
                current_value = ""
            if current_value == prompt:
                stable_checks += 1
                if stable_checks >= 3:
                    break
            else:
                stable_checks = 0
            _sleep(ctx, 0.15, 0.05)
        if stable_checks < 3:
            logger.error("[GPT-IMAGE-2] controlled composer restored or changed the prompt")
            return False

        def composer_is_empty() -> bool:
            current_input = _find(tab, "css:textarea[name='message']", 0.3)
            if not current_input:
                return False
            try:
                return int(current_input.run_js("return String(this.value || '').length")) == 0
            except Exception:
                return False

        def wait_until_sent(timeout: float = 3.0) -> bool:
            deadline = time.time() + timeout
            while time.time() < deadline:
                if composer_is_empty():
                    return True
                _sleep(ctx, 0.15, 0.05)
            return False

        button = _find(
            tab,
            "css:button[aria-label='Send message'][type='submit']:not(:disabled), "
            "button[type='submit']:not([aria-label='Stop generation']):not(:disabled)",
            1.0,
        )
        if button:
            try:
                button.click()
            except Exception as error:
                logger.warning(f"[GPT-IMAGE-2] send button click failed: {error}")
        else:
            logger.warning("[GPT-IMAGE-2] send button not found")
        if wait_until_sent():
            logger.stream("[GPT-IMAGE-2] send confirmed after button click")
            return True

        for enter_attempt in range(2):
            current_input = _find(tab, "css:textarea[name='message']", 0.5)
            if not current_input:
                logger.warning("[GPT-IMAGE-2] composer missing before Enter fallback")
            else:
                try:
                    current_input.input("\n")
                except Exception as error:
                    logger.warning(
                        f"[GPT-IMAGE-2] Enter fallback failed: "
                        f"attempt={enter_attempt + 1}/2 error={error}"
                    )
            if wait_until_sent():
                logger.stream(
                    f"[GPT-IMAGE-2] send confirmed after Enter: attempt={enter_attempt + 1}/2"
                )
                return True
            logger.warning(
                f"[GPT-IMAGE-2] composer still contains text after Enter: "
                f"attempt={enter_attempt + 1}/2"
            )
        return False

    try:
        input_box.clear()
    except Exception:
        pass
    input_box.input(prompt)
    _sleep(ctx, 0.3)
    before_url = _current_url(tab)

    def submission_confirmed(timeout: float = 2.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            current_url = _current_url(tab)
            if current_url and current_url != before_url:
                return True
            try:
                if int(input_box.run_js("return String(this.value || '').length")) == 0:
                    return True
            except Exception:
                pass
            for selector in (
                "css:button[aria-label*='Stop' i]",
                "css:button[data-testid*='stop' i]",
                "css:[aria-busy='true']",
            ):
                if _find(tab, selector, 0.15):
                    return True
            _sleep(ctx, 0.15, 0.05)
        return False

    for attempt in range(2):
        try:
            input_box.input("\n")
        except Exception as error:
            if isinstance(error, RuntimeError) and "cancelled" in str(error).lower():
                raise
            logger.warning(
                f"[GPT-IMAGE-2] Enter submit failed: attempt={attempt + 1}/2 error={error}"
            )
        if submission_confirmed(2.5):
            logger.stream(f"[GPT-IMAGE-2] Enter submit confirmed: attempt={attempt + 1}/2")
            return True
        if attempt == 0:
            logger.stream("[GPT-IMAGE-2] Enter submit not confirmed; retrying Enter")
            _sleep(ctx, 0.5)

    button = _find(tab, "css:button[type='submit']", 1.0)
    if button:
        try:
            logger.stream("[GPT-IMAGE-2] Enter submit not confirmed; trying send button")
            button.click()
            if submission_confirmed(2.5):
                logger.stream("[GPT-IMAGE-2] send button submit confirmed")
                return True
        except Exception as error:
            logger.warning(f"[GPT-IMAGE-2] send button fallback failed: {error}")
    return False


def _wait_for_gpt_image2(
    ctx: dict[str, Any], baseline: set[str], timeout: float
) -> tuple[list[dict[str, Any]], list[str]]:
    tab = ctx["tab"]
    logger = ctx["logger"]
    deadline = time.time() + timeout
    inspected: set[str] = set()
    matches: list[dict[str, Any]] = []
    candidates: list[str] = []
    last_new_image_at = 0.0

    while time.time() < deadline:
        _sleep(ctx, 0.5)
        sources = [url for url in _visible_image_sources(tab) if url not in baseline]
        if sources:
            last_new_image_at = last_new_image_at or time.time()
        for side_index, url in enumerate(sources):
            if url in inspected:
                continue
            payload = _read_image_bytes(tab, url)
            if not payload:
                continue
            inspected.add(url)
            candidates.append(url)
            evidence = inspect_gpt_image2_png(payload)
            logger.info(
                f"[GPT-IMAGE-2][C2PA] side={'A' if side_index == 0 else 'B'} "
                f"matched={evidence['matched']} reason={evidence['reason']}"
            )
            if evidence["matched"]:
                matches.append({"url": url, "side": "A" if side_index == 0 else "B", **evidence})
        if (
            last_new_image_at
            and time.time() - last_new_image_at >= 4.0
            and len(inspected) >= len(sources)
            and (_is_generation_stopped(tab) or not _is_generating(tab))
        ):
            break
    return matches, candidates


def _recover_timed_out_generation(
    ctx: dict[str, Any], initial_matches: list[dict[str, Any]], initial_candidates: list[str]
) -> tuple[list[dict[str, Any]], list[str], bool]:
    tab = ctx["tab"]
    logger = ctx["logger"]
    matches = list(initial_matches)
    candidates = list(initial_candidates)

    _refresh_page(ctx)
    if _is_generating(tab):
        logger.warning("[GPT-IMAGE-2] generation still active after refresh; stopping immediately")
        if not _stop_generation_and_confirm(ctx):
            raise _StopGenerationFailure(
                "generation_still_active_after_stop; command loop terminated"
            )
        logger.info("[GPT-IMAGE-2] timed-out generation stopped; continuing next round")
        return matches, candidates, True
    else:
        logger.info("[GPT-IMAGE-2] generation completed after refresh; inspecting current images")
        recovered_matches, recovered_candidates = _wait_for_gpt_image2(ctx, set(), 5.0)
        matches.extend(recovered_matches)
        candidates.extend(recovered_candidates)

    unique_matches = list({item.get("url", ""): item for item in matches if item.get("url")}.values())
    unique_candidates = list(dict.fromkeys(url for url in candidates if url))
    return unique_matches, unique_candidates, False


class _StopGenerationFailure(RuntimeError):
    pass


def run(ctx: dict[str, Any], *, single_mode: bool = False) -> str:
    tab = ctx["tab"]
    logger = ctx["logger"]
    session = ctx.get("session")
    current_command_id = str(getattr(session, "_current_command_id", "") or "").strip()
    current_command_name = str(getattr(session, "current_command_name", "") or "").strip()
    single_mode = bool(
        single_mode
        or current_command_id == "cmd_15d91034"
        or current_command_name == "ARENA自动盲测不翻牌 -gpt image 2 - 单"
    )
    ui = ctx.get("command_ui") if isinstance(ctx.get("command_ui"), dict) else {}
    values = ui.get("values") if isinstance(ui.get("values"), dict) else ui
    values = values if isinstance(values, dict) else {}

    total_runs = max(1, int(_value(values, "total_runs", 20)))
    prompt = str(_value(values, "prompt_text", "Create an image.")).strip()
    random_chars = str(_value(values, "random_insert_chars", "abcdefghijklmnopqrstuvwxyz"))
    reference_image = str(_value(values, "reference_image", "")).strip()
    generation_mode = "image-to-image" if reference_image else "text-to-image"
    timeout = 150.0 if single_mode else min(
        100.0,
        max(1.0, float(_value(values, "wait_reply_timeout_sec", 100))),
    )
    redirect_url = str(_value(values, "redirect_url", "https://arena.ai/image")).strip()
    hit_urls: list[str] = []
    consecutive_send_failures = 0

    logger.stream(
        f"[GPT-IMAGE-2] starting {total_runs} rounds; "
        f"mode={generation_mode}; timeout={timeout:g}s"
    )
    for index in range(total_runs):
        status = "completed"
        ctx["begin_command_loop"](index + 1, total_runs, "Arena GPT Image 2")
        try:
            if not single_mode:
                logger.stream(
                    f"[GPT-IMAGE-2] round {index + 1}/{total_runs}: creating a new image chat"
                )
                if not _start_new_image_chat(ctx, redirect_url):
                    raise RuntimeError("new_image_chat_failed")
            if reference_image and not _upload_reference_image(ctx, reference_image):
                raise RuntimeError("reference_image_upload_failed")

            baseline = set(_visible_image_sources(tab))
            run_prompt = prompt
            if random_chars:
                position = random.randint(0, len(run_prompt))
                run_prompt = run_prompt[:position] + random.choice(random_chars) + run_prompt[position:]
            logger.stream(f"[GPT-IMAGE-2] round {index + 1}/{total_runs}: sending prompt")
            if not _send_prompt(
                ctx,
                run_prompt,
                require_clear_confirmation=single_mode,
            ):
                if not single_mode:
                    raise RuntimeError("send_failed")
                consecutive_send_failures += 1
                status = "failed"
                logger.error(
                    f"[GPT-IMAGE-2] send failed {consecutive_send_failures} consecutive rounds; "
                    "recovering the stuck page before the next round"
                )
                _refresh_page(ctx)
                if _is_generating(tab):
                    logger.warning(
                        "[GPT-IMAGE-2] stale generation remained after failed send; stopping it"
                    )
                    if not _stop_generation_and_confirm(ctx):
                        raise _StopGenerationFailure(
                            "generation_still_active_after_stop; command loop terminated"
                        )
                continue
            consecutive_send_failures = 0

            matches, candidates = _wait_for_gpt_image2(ctx, baseline, timeout)
            if single_mode and not matches and _is_generation_stopped(tab):
                logger.info(
                    f"[GPT-IMAGE-2] round {index + 1}/{total_runs} generation stopped "
                    "without a target match; continuing next round without voting"
                )
                continue
            if single_mode and (_is_generating(tab) or not candidates):
                logger.warning(
                    f"[GPT-IMAGE-2] round {index + 1}/{total_runs} reached {timeout:g}s "
                    "without a completed image response; refreshing within the same round"
                )
                matches, candidates, interrupted = _recover_timed_out_generation(
                    ctx, matches, candidates
                )
                if interrupted:
                    continue
            page_url = _current_url(tab)
            if matches:
                info = {
                    "url": page_url,
                    "response_sides": [
                        f"gpt-image 2.0 OpenAI Media Service API mode:{generation_mode}"
                        if item["side"] == side else "other image model"
                        for side in ("A", "B")
                        for item in matches[:1]
                    ],
                }
                record_result = ctx["record_arena_rule_candidates"](
                    info,
                    run_prompt,
                    source="image-c2pa/gpt-image-2",
                )
                if page_url and page_url not in hit_urls:
                    hit_urls.append(page_url)
                logger.info(
                    f"[GPT-IMAGE-2][HIT] sides={','.join(item['side'] for item in matches)} "
                    f"url={page_url} drawer_matches={record_result.get('matched', 0)}"
                )
                if single_mode:
                    break
            elif single_mode:
                logger.info(
                    f"[GPT-IMAGE-2] no GPT Image 2 C2PA match; images={len(candidates)} url={page_url}"
                )
                if candidates:
                    _vote_a_better(ctx)
            else:
                logger.info(
                    f"[GPT-IMAGE-2] no GPT Image 2 C2PA match; images={len(candidates)} url={page_url}"
                )
        except _StopGenerationFailure:
            status = "failed"
            raise
        except RuntimeError as error:
            if str(error) in {"python_script_cancelled", "python_script_loop_cancelled"}:
                status = "cancelled"
                if str(error) == "python_script_cancelled":
                    break
            else:
                status = "failed"
                logger.error(f"[GPT-IMAGE-2] round {index + 1} failed: {error}")
                if not single_mode:
                    try:
                        tab.get(redirect_url)
                        _sleep(ctx, 2.0)
                    except Exception:
                        pass
        except Exception as error:
            status = "failed"
            logger.error(f"[GPT-IMAGE-2] round {index + 1} failed: {error}")
        finally:
            ctx["end_command_loop"](status)

    if hit_urls:
        return "GPT Image 2 C2PA hit URLs:\n" + "\n".join(
            f"{index}. {url}" for index, url in enumerate(hit_urls, 1)
        )
    return "GPT Image 2 C2PA hits: 0"


__all__ = ["GPT_IMAGE_MARKERS", "inspect_gpt_image2_png", "run"]
