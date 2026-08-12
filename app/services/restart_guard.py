"""Cooperative scheduled restart guard for the API process.

The guard deliberately does not own the browser or tab pool.  Its only job is
to stop admitting new HTTP work, drain work that was already admitted, then
ask the launcher to replace this Python process.  The launcher keeps the
public port available while that replacement happens.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, Optional


ActivityProbe = Callable[[], int]
RestartCallback = Callable[[], Awaitable[None] | None]


class RestartGuard:
    """Coordinate a scheduled, drain-first service restart."""

    def __init__(
        self,
        *,
        enabled: bool,
        interval_seconds: float,
        drain_timeout_seconds: float,
        activity_probe: ActivityProbe,
        restart_callback: RestartCallback,
    ) -> None:
        self._enabled = bool(enabled) and interval_seconds > 0
        self._interval_seconds = max(1.0, float(interval_seconds or 0))
        self._drain_timeout_seconds = max(0.0, float(drain_timeout_seconds or 0))
        self._activity_probe = activity_probe
        self._restart_callback = restart_callback
        self._lock = asyncio.Lock()
        self._drained = asyncio.Event()
        self._draining = False
        self._active_http_requests = 0
        self._task: Optional[asyncio.Task] = None

    @property
    def is_draining(self) -> bool:
        return self._draining

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    async def start(self) -> None:
        if self._enabled and self._task is None:
            self._task = asyncio.create_task(self._run(), name="scheduled-restart-guard")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def admit_request(self) -> bool:
        """Return False after draining begins so middleware can hold the request."""
        async with self._lock:
            if self._draining:
                return False
            self._active_http_requests += 1
            return True

    async def release_request(self) -> None:
        async with self._lock:
            self._active_http_requests = max(0, self._active_http_requests - 1)
            if self._draining and self._active_http_requests == 0:
                self._drained.set()

    @asynccontextmanager
    async def track_request(self):
        admitted = await self.admit_request()
        try:
            yield admitted
        finally:
            if admitted:
                await self.release_request()

    async def wait_for_handoff(self) -> None:
        """Keep a post-drain request pending until the process is replaced.

        The launcher proxy sees the backend connection close during handoff and
        retries the original buffered request against the new process.
        """
        await asyncio.Future()

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval_seconds)
                restarted = await self._drain_and_restart()
                if restarted:
                    return
        except asyncio.CancelledError:
            raise

    async def _drain_and_restart(self) -> bool:
        async with self._lock:
            self._draining = True
            if self._active_http_requests == 0:
                self._drained.set()

        deadline = None
        if self._drain_timeout_seconds > 0:
            deadline = asyncio.get_running_loop().time() + self._drain_timeout_seconds

        while True:
            active_work = max(0, int(self._activity_probe() or 0))
            if self._drained.is_set() and active_work == 0:
                result = self._restart_callback()
                if inspect.isawaitable(result):
                    await result
                return True

            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                # A forced timeout would cut off a workflow, so resume normal
                # admission and try again after the configured interval.
                async with self._lock:
                    self._draining = False
                    self._drained.clear()
                return False

            # Once HTTP has drained, the event stays set while an independent
            # workflow may still be running.  Sleep explicitly rather than
            # waiting on the already-set event and busy-spinning the loop.
            await asyncio.sleep(0.25)
