"""
app/services/stats_recorder.py - 请求用量统计与历史记录器
"""

import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional, List

from app.core.config import get_logger

logger = get_logger("STATS")

DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "stats.db"
RETENTION_DAYS = 7


class StatsRecorder:
    """请求统计记录器（单例，线程安全）"""

    _instance: Optional['StatsRecorder'] = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._local = threading.local()
        self._init_lock = threading.Lock()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._start_cleanup_thread()
        self._initialized = True

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(DB_PATH))
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def _init_db(self):
        with self._init_lock:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS request_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    domain TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    message_length INTEGER DEFAULT 0,
                    response_length INTEGER DEFAULT 0,
                    duration_ms INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'unknown',
                    error_message TEXT DEFAULT '',
                    preset_name TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON request_history(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_domain
                ON request_history(domain)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status
                ON request_history(status)
            """)
            conn.commit()
            conn.close()

    def _start_cleanup_thread(self):
        def _cleanup_loop():
            while True:
                time.sleep(3600)
                try:
                    self._cleanup_old_data()
                except Exception as e:
                    logger.debug(f"[STATS] 清理线程异常: {e}")
        t = threading.Thread(target=_cleanup_loop, daemon=True, name="stats-cleanup")
        t.start()

    def _cleanup_old_data(self):
        cutoff = time.time() - RETENTION_DAYS * 86400
        try:
            conn = self._get_conn()
            conn.execute("DELETE FROM request_history WHERE timestamp < ?", (cutoff,))
            conn.commit()
        except Exception as e:
            logger.debug(f"[STATS] 清理旧数据失败: {e}")

    def record_request(
        self,
        request_id: str,
        domain: str = "",
        model: str = "",
        message_length: int = 0,
        response_length: int = 0,
        duration_ms: int = 0,
        status: str = "unknown",
        error_message: str = "",
        preset_name: str = "",
    ):
        try:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO request_history
                   (request_id, timestamp, domain, model, message_length,
                    response_length, duration_ms, status, error_message, preset_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_id, time.time(),
                    domain or "", model or "",
                    message_length or 0, response_length or 0,
                    duration_ms or 0, status or "unknown",
                    error_message or "", preset_name or "",
                ),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"[STATS] 记录请求失败: {e}")

    def get_summary(self) -> Dict[str, Any]:
        conn = self._get_conn()
        now = time.time()
        today_start = now - (now % 86400)
        week_start = today_start - 6 * 86400

        result = {
            "total_requests": 0,
            "today_requests": 0,
            "week_requests": 0,
            "total_characters": 0,
            "today_characters": 0,
            "success_rate": 0.0,
            "avg_duration_ms": 0,
            "top_domains": [],
        }

        try:
            row = conn.execute(
                "SELECT COUNT(*) as total, "
                "COALESCE(SUM(message_length + response_length), 0) as chars, "
                "COALESCE(AVG(CASE WHEN status='success' THEN 1.0 ELSE 0.0 END), 0) * 100 as success_pct, "
                "COALESCE(AVG(duration_ms), 0) as avg_dur "
                "FROM request_history"
            ).fetchone()
            if row:
                result["total_requests"] = row["total"]
                result["total_characters"] = row["chars"]
                result["success_rate"] = round(row["success_pct"], 1)
                result["avg_duration_ms"] = round(row["avg_dur"], 0)

            row = conn.execute(
                "SELECT COUNT(*) as cnt, COALESCE(SUM(message_length + response_length), 0) as chars "
                "FROM request_history WHERE timestamp >= ?",
                (today_start,),
            ).fetchone()
            if row:
                result["today_requests"] = row["cnt"]
                result["today_characters"] = row["chars"]

            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM request_history WHERE timestamp >= ?",
                (week_start,),
            ).fetchone()
            if row:
                result["week_requests"] = row["cnt"]

            rows = conn.execute(
                "SELECT domain, COUNT(*) as cnt, "
                "COALESCE(SUM(message_length + response_length), 0) as chars "
                "FROM request_history WHERE domain != '' "
                "GROUP BY domain ORDER BY cnt DESC LIMIT 10"
            ).fetchall()
            for row in rows:
                result["top_domains"].append({
                    "domain": row["domain"],
                    "count": row["cnt"],
                    "characters": row["chars"],
                })
        except Exception as e:
            logger.warning(f"[STATS] 获取统计摘要失败: {e}")

        return result

    def get_daily_stats(self, days: int = 7) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        now = time.time()
        start = now - days * 86400
        result = []
        try:
            rows = conn.execute(
                """SELECT
                    CAST((CAST(timestamp AS INTEGER) -
                          CAST(timestamp AS INTEGER) % 86400) AS INTEGER) as day_ts,
                    COUNT(*) as cnt,
                    COALESCE(SUM(message_length + response_length), 0) as chars,
                    COALESCE(AVG(CASE WHEN status='success' THEN 1.0 ELSE 0.0 END), 0) * 100 as success_pct
                   FROM request_history
                   WHERE timestamp >= ?
                   GROUP BY day_ts
                   ORDER BY day_ts ASC""",
                (start,),
            ).fetchall()
            for row in rows:
                result.append({
                    "timestamp": row["day_ts"],
                    "count": row["cnt"],
                    "characters": row["chars"],
                    "success_rate": round(row["success_pct"], 1),
                })
        except Exception as e:
            logger.warning(f"[STATS] 获取每日统计失败: {e}")
        return result

    def get_history(
        self,
        page: int = 1,
        page_size: int = 50,
        domain: str = "",
        status: str = "",
    ) -> Dict[str, Any]:
        conn = self._get_conn()
        conditions = []
        params = []
        if domain:
            conditions.append("domain = ?")
            params.append(domain)
        if status:
            conditions.append("status = ?")
            params.append(status)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        total = 0
        try:
            row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM request_history {where}", params
            ).fetchone()
            total = row["cnt"] if row else 0
        except Exception:
            pass

        offset = (page - 1) * page_size
        items = []
        try:
            rows = conn.execute(
                f"SELECT * FROM request_history {where} "
                "ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()
            for row in rows:
                items.append({
                    "id": row["id"],
                    "request_id": row["request_id"],
                    "timestamp": row["timestamp"],
                    "domain": row["domain"],
                    "model": row["model"],
                    "message_length": row["message_length"],
                    "response_length": row["response_length"],
                    "duration_ms": row["duration_ms"],
                    "status": row["status"],
                    "error_message": row["error_message"],
                    "preset_name": row["preset_name"],
                })
        except Exception as e:
            logger.warning(f"[STATS] 获取历史记录失败: {e}")

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        }

    def clear_history(self, before_days: int = 7):
        cutoff = time.time() - before_days * 86400
        try:
            conn = self._get_conn()
            conn.execute("DELETE FROM request_history WHERE timestamp < ?", (cutoff,))
            conn.commit()
        except Exception as e:
            logger.warning(f"[STATS] 清理历史记录失败: {e}")


stats_recorder = StatsRecorder()
