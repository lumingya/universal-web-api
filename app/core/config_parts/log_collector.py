"""
app/core/config_parts/log_collector.py - Web 前端日志收集器模块
"""
from collections import deque
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from .browser_constants import _browser_constant_bool, _browser_constant_int


class LogCollector:
    """收集日志用于前端展示"""

    def __init__(self, max_logs: int = 5000):
        self.logs: deque = deque(maxlen=max_logs)
        self.lock = threading.Lock()
        self._next_seq = 1
        self._last_clear_seq = 0

    def is_enabled(self) -> bool:
        return _browser_constant_bool("LOG_WEB_COLLECTOR_ENABLED", True)

    def _target_max_logs(self) -> int:
        return _browser_constant_int("LOG_WEB_MAX_RECORDS", 5000, min_value=0, max_value=10000)

    def _sync_limits_unlocked(self) -> None:
        target_max_logs = self._target_max_logs()
        if self.logs.maxlen == target_max_logs:
            return
        self.logs = deque(list(self.logs)[-target_max_logs:] if target_max_logs else [], maxlen=target_max_logs)

    def add(self, entry: Dict[str, Any]):
        with self.lock:
            self._sync_limits_unlocked()
            if not self.is_enabled() or self.logs.maxlen == 0:
                if self.logs:
                    self.logs.clear()
                return
            payload = dict(entry or {})
            payload["seq"] = self._next_seq
            payload.setdefault("timestamp", time.time())
            payload.setdefault("level", "INFO")
            payload.setdefault("kind", payload["level"])
            payload.setdefault("message", "")
            payload.setdefault("display_message", payload["message"])
            payload.setdefault("message_text", payload["message"])
            payload.setdefault("original_message_text", payload["message_text"])
            payload.setdefault("message_alias", "")
            payload.setdefault("logger", "")
            payload.setdefault("request_id", "SYSTEM")
            self.logs.append(payload)
            self._next_seq += 1

    def get_recent(self, since: float = 0, after_seq: int = 0) -> Tuple[List[Dict[str, Any]], int, bool]:
        with self.lock:
            self._sync_limits_unlocked()
            if not self.is_enabled() or self.logs.maxlen == 0:
                if self.logs:
                    self.logs.clear()
                return [], self._next_seq - 1, False
            cursor = max(0, int(after_seq or 0))
            cleared = bool(cursor and self._last_clear_seq and cursor <= self._last_clear_seq)
            if cursor > 0:
                latest_seq = self._next_seq - 1
                if cursor == latest_seq:
                    return [], latest_seq, cleared
                if cursor > latest_seq:
                    return list(self.logs), latest_seq, True

                oldest_seq = int(self.logs[0].get("seq", 0) or 0) if self.logs else latest_seq
                if self.logs and cursor < oldest_seq:
                    return list(self.logs), latest_seq, True

                logs = [
                    log for log in self.logs
                    if int(log.get("seq", 0) or 0) > cursor
                ]
            else:
                logs = [log for log in self.logs if float(log.get("timestamp", 0) or 0) > since]
            return logs, self._next_seq - 1, cleared

    def clear(self):
        with self.lock:
            self.logs.clear()
            self._last_clear_seq = self._next_seq - 1
            self._next_seq += 1


# 全局日志收集器实例
log_collector = LogCollector()
