"""S6：本地媒体出口（/media、/download_images）的访问控制与转码资源预算。

设计取舍
--------
生成的媒体 URL 会原样写进 OpenAI 兼容响应，被第三方聊天前端直接用 ``<img src>`` /
``<audio src>`` 渲染——这类前端不会带 Bearer 头。因此默认不强制鉴权（保持兼容），
提供 ``MEDIA_REQUIRE_AUTH=true`` 显式开启：开启后接受

- 服务 API 令牌（``Authorization: Bearer`` / ``X-API-Key``，与 /v1/* 相同）；
- 控制面板令牌（``Authorization: Bearer``）；
- 直接来自本机回环、没有任何反代/隧道转发头的请求（本机控制面板直接预览）。

``MEDIA_REQUIRE_AUTH`` 的非法取值按安全开关 fail-closed（视为开启）。

转码（ffmpeg，最长 60s）：全局并发上限 ``MEDIA_TRANSCODE_MAX_CONCURRENCY``（默认 2），
等待超过 ``MEDIA_TRANSCODE_QUEUE_TIMEOUT_SEC``（默认 10s）返回 503；同一输出文件的并发
请求在同一把锁上合并（后到者等待先到者完成后直接命中缓存）；源文件超过
``MEDIA_TRANSCODE_MAX_SOURCE_MB``（默认 200）拒绝转码。
"""

from __future__ import annotations

import hmac
import os
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Mapping, Optional

from app.core.http_security import is_trusted_local_request

MEDIA_PATH_PREFIXES = ("/media/", "/download_images/")


def _env_bool_secure(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    value = env.get(name)
    if value is None or str(value).strip() == "":
        return default
    lowered = str(value).strip().lower()
    if lowered in ("true", "1", "yes", "on"):
        return True
    if lowered in ("false", "0", "no", "off"):
        return False
    return True  # fail-closed


def _env_positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float(str(env.get(name, "") or "").strip())
    except ValueError:
        return default
    return value if value > 0 and value == value else default


def media_auth_required(env: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    return _env_bool_secure(env, "MEDIA_REQUIRE_AUTH", False)


def is_media_path(path: str) -> bool:
    value = str(path or "")
    return any(value.startswith(prefix) for prefix in MEDIA_PATH_PREFIXES)


def _candidates(headers: Any, query_token: Optional[str] = None) -> list:
    out = []
    if headers is not None:
        auth = str(headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            auth = auth[7:].strip()
        if auth:
            out.append(auth)
        api_key = str(headers.get("x-api-key") or "").strip()
        if api_key:
            out.append(api_key)
    token_param = str(query_token or "").strip()
    if token_param:
        out.append(token_param)
    return out


def _token_matches(expected: str, candidates: list) -> bool:
    expected = str(expected or "").strip()
    if not expected:
        return False
    expected_bytes = expected.encode("utf-8")
    return any(hmac.compare_digest(expected_bytes, c.encode("utf-8")) for c in candidates)


def media_request_authorized(
    client_host: Optional[str],
    headers: Any,
    env: Optional[Mapping[str, str]] = None,
    query_token: Optional[str] = None,
) -> bool:
    """MEDIA_REQUIRE_AUTH 开启时的判定；未开启时恒为 True。"""
    env = os.environ if env is None else env
    if not media_auth_required(env):
        return True
    candidates = _candidates(headers, query_token=query_token)
    if candidates:
        if _token_matches(env.get("AUTH_TOKEN", ""), candidates):
            return True
        if _token_matches(env.get("DASHBOARD_AUTH_TOKEN", ""), candidates):
            return True
    return is_trusted_local_request(client_host, headers, env)


class TranscodeBusyError(RuntimeError):
    """全局转码并发已满且等待超时。"""


class TranscodeGate:
    """转码的全局并发上限 + 同键合并。"""

    def __init__(self, max_concurrency: int = 2, queue_timeout: float = 10.0):
        self.max_concurrency = max(1, int(max_concurrency))
        self.queue_timeout = max(0.0, float(queue_timeout))
        self._slots = threading.BoundedSemaphore(self.max_concurrency)
        self._keys_lock = threading.Lock()
        self._key_locks: Dict[str, list] = {}  # key -> [lock, refcount]

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "TranscodeGate":
        env = os.environ if env is None else env
        return cls(
            max_concurrency=int(_env_positive_float(env, "MEDIA_TRANSCODE_MAX_CONCURRENCY", 2)),
            queue_timeout=_env_positive_float(env, "MEDIA_TRANSCODE_QUEUE_TIMEOUT_SEC", 10.0),
        )

    @contextmanager
    def key(self, key: str) -> Iterator[None]:
        """同一输出只允许一个转码在跑；后到者等待（期间不占全局名额）。"""
        with self._keys_lock:
            entry = self._key_locks.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            if not entry[0].acquire(timeout=self.queue_timeout + 90.0):
                raise TranscodeBusyError("media_transcode_busy")
            try:
                yield
            finally:
                entry[0].release()
        finally:
            with self._keys_lock:
                entry[1] -= 1
                if entry[1] <= 0 and self._key_locks.get(key) is entry:
                    self._key_locks.pop(key, None)

    @contextmanager
    def slot(self) -> Iterator[None]:
        if not self._slots.acquire(timeout=self.queue_timeout):
            raise TranscodeBusyError("media_transcode_busy")
        try:
            yield
        finally:
            self._slots.release()


def transcode_max_source_bytes(env: Optional[Mapping[str, str]] = None) -> int:
    env = os.environ if env is None else env
    return int(_env_positive_float(env, "MEDIA_TRANSCODE_MAX_SOURCE_MB", 200) * 1024 * 1024)


__all__ = [
    "MEDIA_PATH_PREFIXES",
    "TranscodeBusyError",
    "TranscodeGate",
    "is_media_path",
    "media_auth_required",
    "media_request_authorized",
    "transcode_max_source_bytes",
]
