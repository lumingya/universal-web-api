"""媒体路由的访问控制与转码资源预算（修复 S6）。

`GET /media/{filename}` 与 `/download_images` 静态目录原本完全无认证，
而带 `?format=` 的请求会触发一次最长 60 秒的 ffmpeg 转码，
既没有全局并发上限，也没有同键去重 —— 同一个文件被并发请求 N 次，
就会同时拉起 N 个 ffmpeg 进程。任何能连上端口的人都可以把 CPU 打满。

这里提供两件东西：

1. `media_access_allowed()` —— 可选的媒体鉴权（默认关闭以保持兼容，
   因为 `<img src>` / `<audio src>` 这类标签没法带 Authorization 头）。
2. `TranscodeGuard` —— 全局并发信号量 + 同键去重锁 + 源文件体积上限。
"""

from __future__ import annotations

import hmac
import os
import threading
from contextlib import contextmanager
from typing import Any, Dict, Optional

from app.core.config_parts.env_config import AppConfig, parse_bool_literal


# ---------------------------------------------------------------- 配置读取


def _env_flag(name: str, default: bool) -> bool:
    parsed = parse_bool_literal(os.environ.get(name))
    return default if parsed is None else parsed


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        return default


def media_access_requires_auth() -> bool:
    """是否对媒体路由强制鉴权。

    默认 **false**：媒体 URL 会直接出现在 OpenAI 兼容响应里，被浏览器的
    `<img>` / `<audio>` 标签加载，而这些标签无法携带 Authorization 头。
    把默认值改成 true 会静默打断所有现有前端，所以做成显式开关，
    并在 `.env.example` 里写清楚风险。
    """
    return _env_flag("MEDIA_ACCESS_REQUIRE_AUTH", False)


def media_access_allowed(
    *,
    authorization: Optional[str] = None,
    x_api_key: Optional[str] = None,
    query_token: Optional[str] = None,
) -> bool:
    """校验媒体访问凭据。未开启鉴权时恒为 True。

    除了标准请求头，还接受 `?token=` 查询参数 —— 这是让 `<img src>`
    这类无法设置请求头的场景在开启鉴权后仍能工作的唯一途径。
    """
    if not media_access_requires_auth():
        return True

    expected = str(AppConfig.get_auth_token() or "").strip()
    if not expected:
        # 要求鉴权却没有令牌属配置错误，fail-closed
        return False

    raw_auth = str(authorization or "").strip()
    if raw_auth.lower().startswith("bearer "):
        raw_auth = raw_auth[7:].strip()

    expected_bytes = expected.encode("utf-8")
    for candidate in (raw_auth, str(x_api_key or "").strip(), str(query_token or "").strip()):
        if candidate and hmac.compare_digest(expected_bytes, candidate.encode("utf-8")):
            return True
    return False


# ---------------------------------------------------------------- 转码预算


class TranscodeBusyError(RuntimeError):
    """转码槽位已满，调用方应返回 503。"""


class TranscodeTooLargeError(RuntimeError):
    """源文件超过允许转码的体积上限，调用方应返回 413。"""


class TranscodeGuard:
    """全局并发预算 + 同键去重。

    同键去重很关键：没有它的时候，同一个文件被并发请求 N 次就会拉起
    N 个 ffmpeg 做**完全相同**的工作。加锁之后后到的请求会等第一个做完，
    然后直接命中磁盘缓存。
    """

    def __init__(
        self,
        max_concurrency: Optional[int] = None,
        acquire_timeout: Optional[float] = None,
        max_source_bytes: Optional[int] = None,
    ):
        self._max_concurrency = (
            int(max_concurrency)
            if max_concurrency is not None
            else _env_int("MEDIA_TRANSCODE_MAX_CONCURRENCY", 2)
        )
        self._acquire_timeout = (
            float(acquire_timeout)
            if acquire_timeout is not None
            else float(_env_int("MEDIA_TRANSCODE_QUEUE_TIMEOUT_SEC", 30))
        )
        self._max_source_bytes = (
            int(max_source_bytes)
            if max_source_bytes is not None
            else _env_int("MEDIA_TRANSCODE_MAX_SOURCE_MB", 256) * 1024 * 1024
        )
        self._semaphore = threading.BoundedSemaphore(self._max_concurrency)
        self._keys_lock = threading.Lock()
        self._key_locks: Dict[str, Any] = {}

    # -- 只读属性，便于测试与日志 -------------------------------------
    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    @property
    def max_source_bytes(self) -> int:
        return self._max_source_bytes

    def check_source_size(self, size_bytes: Any) -> None:
        try:
            size = int(size_bytes)
        except (TypeError, ValueError):
            return
        if size > self._max_source_bytes:
            raise TranscodeTooLargeError(
                f"media_source_too_large:{size}>{self._max_source_bytes}"
            )

    @contextmanager
    def _key_lock(self, key: str):
        with self._keys_lock:
            entry = self._key_locks.get(key)
            if entry is None:
                entry = {"lock": threading.Lock(), "waiters": 0}
                self._key_locks[key] = entry
            entry["waiters"] += 1

        entry["lock"].acquire()
        try:
            yield
        finally:
            entry["lock"].release()
            with self._keys_lock:
                entry["waiters"] -= 1
                if entry["waiters"] <= 0:
                    self._key_locks.pop(key, None)

    @contextmanager
    def slot(self, key: str):
        """占用一个转码槽位；同一个 key 串行执行。

        先抢 key 锁再抢信号量：这样同一文件的并发请求只有一个会去竞争
        全局槽位，不会把整个预算被同一个文件的重复请求吃光。
        """
        with self._key_lock(str(key or "")):
            if not self._semaphore.acquire(timeout=self._acquire_timeout):
                raise TranscodeBusyError("media_transcode_busy")
            try:
                yield
            finally:
                self._semaphore.release()


#: 进程级单例
transcode_guard = TranscodeGuard()


__all__ = [
    "TranscodeBusyError",
    "TranscodeGuard",
    "TranscodeTooLargeError",
    "media_access_allowed",
    "media_access_requires_auth",
    "transcode_guard",
]
