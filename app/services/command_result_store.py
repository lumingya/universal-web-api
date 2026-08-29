"""
app/services/command_result_store.py - 通用命令执行结果持久化存储层 (CRUD)

职责：
- 提供命令结果记录的底层 JSON 文件读写与增删改查；
- 维护原子写入与最大记录数上限控制；
- 存储层保持纯粹 CRUD，不耦合特定厂商或业务规则。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_FILE = _PROJECT_ROOT / "config" / "command_results.local.json"
_LOCK = threading.RLock()
_MAX_RECORDS = 5000


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f"{path.stem}_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


def _load_results() -> Dict[str, Any]:
    if not RESULTS_FILE.exists():
        return {"version": 1, "records": []}
    try:
        with RESULTS_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "records": []}
    records = payload.get("records") if isinstance(payload, dict) else []
    return {"version": 1, "records": records if isinstance(records, list) else []}


def get_all_records() -> List[Dict[str, Any]]:
    """获取所有记录副本。"""
    with _LOCK:
        return list(_load_results()["records"])


def get_record(record_id: str) -> Optional[Dict[str, Any]]:
    """根据 ID 获取单条记录。"""
    rid = str(record_id or "").strip()
    if not rid:
        return None
    with _LOCK:
        for item in _load_results()["records"]:
            if isinstance(item, dict) and str(item.get("id") or "") == rid:
                return dict(item)
    return None


def list_command_results(command_id: str, limit: int = 500) -> List[Dict[str, Any]]:
    """根据 command_id 检索结果列表，按创建时间倒序。"""
    command_id = str(command_id or "").strip()
    with _LOCK:
        records = _load_results()["records"]
    filtered = [item for item in records if isinstance(item, dict) and item.get("command_id") == command_id]
    filtered.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    return filtered[: max(1, min(int(limit or 500), 2000))]


def add_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """新增单条命令执行结果。"""
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    rec = dict(record)
    if not rec.get("id"):
        rec["id"] = uuid.uuid4().hex
    with _LOCK:
        payload = _load_results()
        records = payload["records"]
        records.append(rec)
        payload["records"] = records[-_MAX_RECORDS:]
        _atomic_write_json(RESULTS_FILE, payload)
    return rec


def add_records(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """批量新增命令执行结果。"""
    items: List[Dict[str, Any]] = []
    for raw in records:
        if isinstance(raw, dict):
            item = dict(raw)
            if not item.get("id"):
                item["id"] = uuid.uuid4().hex
            items.append(item)
    if not items:
        return []
    with _LOCK:
        payload = _load_results()
        existing = payload["records"]
        existing.extend(items)
        payload["records"] = existing[-_MAX_RECORDS:]
        _atomic_write_json(RESULTS_FILE, payload)
    return items


def add_records_unique(
    records: Iterable[Dict[str, Any]],
    unique_fields: Iterable[str],
) -> List[Dict[str, Any]]:
    """Add records while atomically suppressing duplicates by business key."""
    fields = tuple(str(field) for field in unique_fields if str(field))
    if not fields:
        return add_records(records)

    items: List[Dict[str, Any]] = []
    for raw in records:
        if isinstance(raw, dict):
            item = dict(raw)
            if not item.get("id"):
                item["id"] = uuid.uuid4().hex
            items.append(item)
    if not items:
        return []

    def key(item: Dict[str, Any]) -> tuple[Any, ...]:
        return tuple(str(item.get(field) or "") for field in fields)

    with _LOCK:
        payload = _load_results()
        existing = payload["records"]
        seen = {key(item) for item in existing if isinstance(item, dict)}
        accepted = []
        for item in items:
            item_key = key(item)
            if item_key in seen:
                continue
            seen.add(item_key)
            accepted.append(item)
        if accepted:
            existing.extend(accepted)
            payload["records"] = existing[-_MAX_RECORDS:]
            _atomic_write_json(RESULTS_FILE, payload)
        return accepted


def delete_record(record_id: str) -> bool:
    """根据 ID 删除单条记录。"""
    rid = str(record_id or "").strip()
    if not rid:
        return False
    with _LOCK:
        payload = _load_results()
        before = len(payload["records"])
        payload["records"] = [
            item for item in payload["records"]
            if not (isinstance(item, dict) and str(item.get("id") or "") == rid)
        ]
        removed = before != len(payload["records"])
        if removed:
            _atomic_write_json(RESULTS_FILE, payload)
        return removed


def clear_command_results(command_id: str, rule_id: str = "") -> int:
    """清空指定 command_id（及可选 rule_id）的结果。"""
    command_id = str(command_id or "").strip()
    rule_id = str(rule_id or "").strip()
    with _LOCK:
        payload = _load_results()
        before = len(payload["records"])
        payload["records"] = [
            item
            for item in payload["records"]
            if item.get("command_id") != command_id
            or (rule_id and str(item.get("rule_id") or "") != rule_id)
        ]
        removed = before - len(payload["records"])
        if removed:
            _atomic_write_json(RESULTS_FILE, payload)
        return removed


# 业务转发兼容层（保持既有测试与导入不中断）
from app.services.arena_rule_service import (  # noqa: E402
    _detector_accepts,
    _matches_rule,
    record_arena_rule_candidates,
)

__all__ = [
    "RESULTS_FILE",
    "_atomic_write_json",
    "_detector_accepts",
    "_load_results",
    "_matches_rule",
    "add_record",
    "add_records",
    "add_records_unique",
    "clear_command_results",
    "delete_record",
    "get_all_records",
    "get_record",
    "list_command_results",
    "record_arena_rule_candidates",
    "requests",
]
