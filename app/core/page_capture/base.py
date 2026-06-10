"""
app/core/page_capture/base.py - shared page-side fetch capture contract.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Callable, ContextManager, Dict, Generator, Optional

from app.core.config import SSEFormatter


InteractionSlot = Callable[[str, str], ContextManager[bool]]


class PageFetchCapture:
    """Base class for page-runtime fetch capture implementations."""

    parser_id = ""
    monitor_id = "page_fetch"
    mode_name = "页面抓流"

    def __init__(
        self,
        *,
        tab,
        formatter: SSEFormatter,
        stream_config: Optional[Dict[str, Any]] = None,
        network_config: Optional[Dict[str, Any]] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        interaction_slot: Optional[InteractionSlot] = None,
    ) -> None:
        self.tab = tab
        self.formatter = formatter
        self._stream_config = stream_config or {}
        self._network_config = network_config or {}
        self._should_stop = stop_checker or (lambda: False)
        self._interaction_slot = interaction_slot
        self._sent_content_length = 0

    @classmethod
    def get_parser_id(cls) -> str:
        return cls.parser_id

    @classmethod
    def get_monitor_id(cls) -> str:
        return cls.monitor_id

    @classmethod
    def get_mode_name(cls) -> str:
        return cls.mode_name

    def prepare(self) -> None:
        raise NotImplementedError

    def monitor(self, completion_id: str) -> Generator[str, None, None]:
        raise NotImplementedError

    def get_sent_content_length(self) -> int:
        try:
            return max(0, int(self._sent_content_length or 0))
        except Exception:
            return 0

    def _check_cancelled(self) -> bool:
        return bool(self._should_stop())

    def _page_interaction_slot(self, action: str, target_key: str = ""):
        if callable(self._interaction_slot):
            return self._interaction_slot(action, target_key)
        return nullcontext(True)
