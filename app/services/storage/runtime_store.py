"""R2-5：运行时数据的 SQLite 存储（请求历史、累计统计）。

以前请求历史每次保存都把整份 JSON（最多 2000 条、每条可能带几十 KB 的捕获内容）重写一遍；
现在改为 SQLite（WAL 模式）：
- ``sync_history`` 按每条记录的内容摘要只写入新增或变化的记录，删除已从内存中淘汰的记录；
- ``synchronous=NORMAL``：WAL 模式下提交不会每次都落盘同步（保持去掉 fsync 的性能意图），
  进程崩溃也不会损坏数据库，最多丢失最后一次提交；
- 旧的 ``config/request_history.json`` / ``config/app_stats.json`` 由 RequestManager 在首次加载时自动导入，
  原文件改名为 ``*.migrated-<时间>.bak``。

线程安全：单连接加可重入锁（写入频率很低，每次都是一个短事务）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER NOT NULL)",
    """CREATE TABLE IF NOT EXISTS request_history (
        request_id TEXT PRIMARY KEY,
        created_at REAL NOT NULL,
        status TEXT,
        updated_at REAL NOT NULL,
        record TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_request_history_created ON request_history(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_request_history_status ON request_history(status)",
)


def _record_key(record: Dict[str, Any], index: int) -> str:
    key = str(record.get("request_id") or "").strip()
    if key:
        return key
    digest = hashlib.sha1(json.dumps(record, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    return f"anon-{record.get('created_at', 0)}-{index}-{digest[:12]}"


def _created_at(record: Dict[str, Any]) -> float:
    try:
        return float(record.get("created_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0


class RuntimeStore:
    def __init__(self, path: str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._history_digests: Dict[str, str] = {}

    # ------------------------------------------------------------------ 连接与迁移
    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            with conn:
                for statement in _SCHEMA:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(SCHEMA_VERSION),),
                )
            self._conn = conn
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # ------------------------------------------------------------------ 统计
    def load_stats(self) -> Dict[str, int]:
        with self._lock:
            rows = self._connection().execute("SELECT key, value FROM stats").fetchall()
        return {key: int(value) for key, value in rows}

    def save_stats(self, values: Dict[str, int]) -> None:
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN")
            try:
                conn.executemany(
                    "INSERT INTO stats(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    [(str(k), int(v)) for k, v in values.items()],
                )
                conn.execute("COMMIT")
            except Exception:  # broad-except: 回滚事务后原样重新抛出
                conn.execute("ROLLBACK")
                raise

    # ------------------------------------------------------------------ 请求历史
    def history_count(self) -> int:
        with self._lock:
            return int(self._connection().execute("SELECT COUNT(*) FROM request_history").fetchone()[0])

    def load_history(self, limit: int) -> List[Dict[str, Any]]:
        """按创建时间升序返回最近 ``limit`` 条；同时记下各条摘要，供后续增量同步。"""
        if limit <= 0:
            return []
        with self._lock:
            rows = self._connection().execute(
                "SELECT request_id, record FROM request_history ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            records = []
            for request_id, text in reversed(rows):
                try:
                    record = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
                    self._history_digests[request_id] = hashlib.sha1(text.encode("utf-8")).hexdigest()
            return records

    def sync_history(self, records: Iterable[Dict[str, Any]]) -> Tuple[int, int]:
        """让数据库与给定的内存记录一致：只写入新增/变化的记录，删除不在其中的记录。返回 (写入数, 删除数)。"""
        now = time.time()
        with self._lock:
            conn = self._connection()
            current: Dict[str, Tuple[str, Dict[str, Any], str]] = {}
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    continue
                text = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str)
                current[_record_key(record, index)] = (text, record, hashlib.sha1(text.encode("utf-8")).hexdigest())
            if not self._history_digests:  # 首次同步：以数据库现有内容为基准
                for request_id, text in conn.execute("SELECT request_id, record FROM request_history"):
                    self._history_digests[request_id] = hashlib.sha1(text.encode("utf-8")).hexdigest()
            changed = [
                (key, _created_at(record), str(record.get("status") or ""), now, text)
                for key, (text, record, digest) in current.items()
                if self._history_digests.get(key) != digest
            ]
            removed = [key for key in self._history_digests if key not in current]
            if not changed and not removed:
                return 0, 0
            conn.execute("BEGIN")
            try:
                conn.executemany(
                    "INSERT INTO request_history(request_id, created_at, status, updated_at, record) VALUES(?, ?, ?, ?, ?) "
                    "ON CONFLICT(request_id) DO UPDATE SET created_at=excluded.created_at, status=excluded.status, "
                    "updated_at=excluded.updated_at, record=excluded.record",
                    changed,
                )
                for start in range(0, len(removed), 500):
                    chunk = removed[start:start + 500]
                    conn.execute(
                        f"DELETE FROM request_history WHERE request_id IN ({','.join('?' * len(chunk))})", chunk
                    )
                conn.execute("COMMIT")
            except Exception:  # broad-except: 回滚事务后原样重新抛出
                conn.execute("ROLLBACK")
                raise
            for key, _, _, _, text in changed:
                self._history_digests[key] = hashlib.sha1(text.encode("utf-8")).hexdigest()
            for key in removed:
                self._history_digests.pop(key, None)
            return len(changed), len(removed)

    def query_history(self, *, status: Optional[str] = None, since: Optional[float] = None,
                      limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """按状态/时间过滤查询（最新在前），供面板或导出使用。"""
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(float(since))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params += [max(0, int(limit)), max(0, int(offset))]
        with self._lock:
            rows = self._connection().execute(
                f"SELECT record FROM request_history {where} ORDER BY created_at DESC LIMIT ? OFFSET ?", params
            ).fetchall()
        return [json.loads(text) for (text,) in rows]


def migrate_legacy_file(path: str) -> Optional[str]:
    """把已导入的旧 JSON 文件改名为 .migrated-<时间>.bak，返回新文件名。"""
    if not os.path.exists(path):
        return None
    backup = f"{path}.migrated-{time.strftime('%Y%m%d-%H%M%S')}.bak"
    os.replace(path, backup)
    return backup
