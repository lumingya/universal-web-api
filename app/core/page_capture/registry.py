"""
app/core/page_capture/registry.py - registry for provider-specific page captures.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Type

from app.core.config import SSEFormatter, logger

from .base import InteractionSlot, PageFetchCapture


_CAPTURE_CLASSES: Dict[str, Type[PageFetchCapture]] = {}


def register_page_fetch_capture(capture_class: Type[PageFetchCapture]) -> None:
    parser_id = str(capture_class.get_parser_id() or "").strip().lower()
    if not parser_id:
        raise ValueError("page fetch capture parser_id is required")
    _CAPTURE_CLASSES[parser_id] = capture_class
    logger.info(f"已注册页面抓流实现: {parser_id} -> {capture_class.__name__}")


def create_page_fetch_capture(
    *,
    tab,
    formatter: SSEFormatter,
    stream_config: Optional[Dict[str, Any]] = None,
    stop_checker: Optional[Callable[[], bool]] = None,
    interaction_slot: Optional[InteractionSlot] = None,
) -> Optional[PageFetchCapture]:
    stream_config = stream_config or {}
    if stream_config.get("mode", "dom") != "network":
        return None

    network_config = stream_config.get("network", {}) or {}
    parser_id = str(network_config.get("parser", "") or "").strip().lower()
    if not parser_id:
        return None

    capture_class = _CAPTURE_CLASSES.get(parser_id)
    if capture_class is None:
        return None

    return capture_class(
        tab=tab,
        formatter=formatter,
        stream_config=stream_config,
        network_config=network_config,
        stop_checker=stop_checker,
        interaction_slot=interaction_slot,
    )
