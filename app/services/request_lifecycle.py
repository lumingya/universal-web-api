"""
Helpers for request execution lifetime, worker cleanup, and tab retirement.
"""

import asyncio
import os
import queue
import threading
import time
from typing import Any, Callable, Dict, Optional

from app.core.config import BrowserConstants, _request_context, get_logger
from app.services.request_manager import RequestContext


logger = get_logger("REQUEST.LIFECYCLE")

DEFAULT_MAX_REQUEST_EXECUTE_TIME_SEC = 300.0
MIN_MAX_REQUEST_EXECUTE_TIME_SEC = 5.0
WORKER_QUEUE_FINAL_GRACE_SEC = 5.0
WORKER_QUEUE_MAX_PUT_BLOCK_SEC = 0.1
WORKER_QUEUE_CANCEL_PUT_BLOCK_SEC = 0.01
WORKER_QUEUE_WAIT_BACKOFF_FACTOR = 1.8
COMPLETED_WORKER_FAST_JOIN_SEC = 0.5
COMPLETED_WORKER_RETIRE_GRACE_SEC = 30.0


class TrackedWorkerExecutionCancelled(Exception):
    """Raised when a tracked blocking worker keeps running after cancellation."""


def _coerce_max_request_execute_time_sec(raw: Any, default: float, source: str) -> float:
    try:
        value = float(raw)
    except Exception:
        logger.debug(f"Invalid {source} MAX_REQUEST_EXECUTE_TIME_SEC={raw!r}, using default {default}s")
        return max(MIN_MAX_REQUEST_EXECUTE_TIME_SEC, float(default))

    if value <= 0:
        return 0.0
    return max(MIN_MAX_REQUEST_EXECUTE_TIME_SEC, value)


def get_max_request_execute_time_sec(default: float = DEFAULT_MAX_REQUEST_EXECUTE_TIME_SEC) -> float:
    """Return the request hard timeout; set MAX_REQUEST_EXECUTE_TIME_SEC<=0 to disable."""
    env_raw = os.getenv("MAX_REQUEST_EXECUTE_TIME_SEC")
    if env_raw is not None and str(env_raw).strip() != "":
        return _coerce_max_request_execute_time_sec(env_raw, default, "env")

    try:
        config_raw = BrowserConstants.get("MAX_REQUEST_EXECUTE_TIME_SEC")
    except Exception as e:
        logger.debug(f"Read browser config MAX_REQUEST_EXECUTE_TIME_SEC failed: {e}")
        config_raw = default

    if config_raw is None or str(config_raw).strip() == "":
        config_raw = default
    return _coerce_max_request_execute_time_sec(config_raw, default, "browser_config")


def mark_request_hard_timeout(
    ctx: RequestContext,
    started_at: float,
    max_execute_time_sec: Optional[float],
    *,
    label: str = "",
) -> bool:
    """Cancel ctx when an application-level hard timeout is reached."""
    try:
        max_seconds = (
            get_max_request_execute_time_sec()
            if max_execute_time_sec is None
            else float(max_execute_time_sec)
        )
    except Exception:
        max_seconds = DEFAULT_MAX_REQUEST_EXECUTE_TIME_SEC

    if max_seconds <= 0:
        return False
    ctx_started_at = getattr(ctx, "started_at_monotonic", None)
    if ctx_started_at is None:
        ctx_started_at = float(started_at or time.monotonic())
    elapsed = time.monotonic() - ctx_started_at
    if elapsed < max_seconds:
        return False

    if not ctx.should_stop():
        logger.warning(
            f"[{ctx.request_id}] request hard timeout reached "
            f"(elapsed={elapsed:.1f}s, limit={max_seconds:.1f}s, {label or '-'})"
        )
        ctx.request_cancel("absolute_request_timeout")
    return True


def put_worker_queue_item(
    chunk_queue: queue.Queue,
    ctx: RequestContext,
    item: Any,
    *,
    final: bool = False,
    poll_timeout: float = 0.5,
) -> bool:
    """Put worker output with bounded backpressure and cancellation awareness."""
    deadline = time.monotonic() + WORKER_QUEUE_FINAL_GRACE_SEC if final else None

    while final or not ctx.should_stop():
        try:
            chunk_queue.put(
                item,
                timeout=_coerce_worker_queue_put_timeout(
                    poll_timeout,
                    cancelled=ctx.should_stop(),
                ),
            )
            return True
        except queue.Full:
            if final:
                if ctx.should_stop() or (deadline is not None and time.monotonic() >= deadline):
                    return False
                continue
            if ctx.should_stop():
                return False

    return False


def _coerce_worker_queue_put_timeout(raw_timeout: Any, *, cancelled: bool = False) -> float:
    try:
        value = float(raw_timeout)
    except Exception:
        value = 0.5
    if cancelled:
        return WORKER_QUEUE_CANCEL_PUT_BLOCK_SEC
    return max(
        WORKER_QUEUE_CANCEL_PUT_BLOCK_SEC,
        min(value if value > 0 else 0.5, WORKER_QUEUE_MAX_PUT_BLOCK_SEC),
    )


async def wait_worker_queue_item(
    chunk_queue: queue.Queue,
    *,
    timeout: float = 0.5,
    poll_interval: float = 0.01,
    max_poll_interval: float = 0.05,
) -> Any:
    """Wait for a worker queue item without occupying the default executor."""
    try:
        timeout_value = float(timeout)
    except Exception:
        timeout_value = 0.5
    timeout_value = max(0.0, timeout_value)

    try:
        interval = float(poll_interval)
    except Exception:
        interval = 0.01
    interval = max(0.001, min(interval, 0.05))

    try:
        max_interval = float(max_poll_interval)
    except Exception:
        max_interval = 0.05
    max_interval = max(interval, min(max_interval, 0.05))

    deadline = time.monotonic() + timeout_value
    current_interval = min(interval, timeout_value) if timeout_value > 0 else interval
    while True:
        try:
            return chunk_queue.get_nowait()
        except queue.Empty:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            sleep_for = min(current_interval, remaining)
            await asyncio.sleep(sleep_for)
            current_interval = _next_worker_queue_wait_interval(
                current_interval,
                max_interval,
                remaining,
            )


def _next_worker_queue_wait_interval(current: float, max_interval: float, remaining: float) -> float:
    try:
        current_value = float(current)
    except Exception:
        current_value = 0.001
    try:
        max_value = float(max_interval)
    except Exception:
        max_value = 0.05
    try:
        remaining_value = float(remaining)
    except Exception:
        remaining_value = max_value

    max_value = max(0.001, min(max_value, 0.05))
    current_value = max(0.001, current_value)
    remaining_value = max(0.0, remaining_value)

    next_value = current_value * WORKER_QUEUE_WAIT_BACKOFF_FACTOR
    return max(0.001, min(next_value, max_value, remaining_value or max_value))


def _set_worker_future_result(
    future: "asyncio.Future[Any]",
    *,
    result: Any = None,
    error: Optional[BaseException] = None,
) -> None:
    if future.done():
        return
    if error is not None:
        future.set_exception(error)
        return
    future.set_result(result)


def _consume_worker_future_exception(future: "asyncio.Future[Any]") -> None:
    """Retrieve late worker failures after the awaiting request has been cancelled."""
    if future.cancelled():
        return
    try:
        future.exception()
    except (asyncio.CancelledError, Exception):
        pass


def _get_cancel_reason(ctx: RequestContext, fallback: str = "worker_cancelled") -> str:
    reason = str(getattr(ctx, "cancel_reason", "") or "").strip()
    return reason or fallback


def run_in_request_context(ctx: RequestContext, worker_fn: Callable[[], Any]) -> Any:
    """Run a callable with the request id attached to all logs it emits."""
    token = _request_context.set(str(getattr(ctx, "request_id", "") or "SYSTEM"))
    try:
        return worker_fn()
    finally:
        _request_context.reset(token)


async def run_tracked_blocking_call(
    worker_fn: Callable[[], Any],
    *,
    ctx: RequestContext,
    worker_state: Dict[str, Any],
    label: str,
    poll_timeout: float = 0.5,
    max_execute_time_sec: Optional[float] = None,
) -> Any:
    """Run a blocking callable on a daemon thread and keep it visible for cleanup."""
    loop = asyncio.get_running_loop()
    result_future: "asyncio.Future[Any]" = loop.create_future()
    started_at = time.monotonic()

    def worker() -> None:
        try:
            result = run_in_request_context(ctx, worker_fn)
        except Exception as exc:
            try:
                loop.call_soon_threadsafe(
                    lambda exc=exc: _set_worker_future_result(result_future, error=exc)
                )
            except RuntimeError:
                pass
        else:
            try:
                loop.call_soon_threadsafe(
                    lambda result=result: _set_worker_future_result(result_future, result=result)
                )
            except RuntimeError:
                pass

    worker_thread = threading.Thread(
        target=worker,
        daemon=True,
        name=f"tracked-worker-{label}",
    )
    worker_state["thread"] = worker_thread
    worker_state["label"] = label

    def _clear_finished_worker_state() -> None:
        if worker_state.get("thread") is worker_thread:
            worker_state["thread"] = None
            worker_state["label"] = None

    def _handle_finished_worker(future: "asyncio.Future[Any]") -> None:
        _clear_finished_worker_state()
        _consume_worker_future_exception(future)

    result_future.add_done_callback(_handle_finished_worker)
    worker_thread.start()

    try:
        while True:
            if mark_request_hard_timeout(
                ctx,
                started_at,
                max_execute_time_sec,
                label=f"worker={label}",
            ):
                raise TrackedWorkerExecutionCancelled("absolute_request_timeout")
            if ctx.should_stop():
                raise TrackedWorkerExecutionCancelled(_get_cancel_reason(ctx))
            try:
                return await asyncio.wait_for(
                    asyncio.shield(result_future),
                    timeout=max(0.05, float(poll_timeout or 0.5)),
                )
            except asyncio.TimeoutError:
                continue
    finally:
        if result_future.done():
            _clear_finished_worker_state()


def _resolve_raw_tab_id(pool: Any, session: Any) -> str:
    raw_tab_id = ""
    if pool is not None:
        try:
            persistent_index = int(getattr(session, "persistent_index", 0) or 0)
            for candidate_raw_id, candidate_index in getattr(pool, "_raw_id_to_persistent", {}).items():
                if int(candidate_index or 0) == persistent_index:
                    raw_tab_id = str(candidate_raw_id or "").strip()
                    break
        except Exception:
            raw_tab_id = ""
    if raw_tab_id:
        return raw_tab_id

    try:
        return str(getattr(getattr(session, "tab", None), "tab_id", "") or "").strip()
    except Exception:
        return ""


def retire_bound_tab_after_worker_leak(ctx: RequestContext, reason: str) -> None:
    """Mark the tab bound to ctx as unhealthy without closing its browser target."""
    tab_id = str(getattr(ctx, "tab_id", "") or "").strip()
    if not tab_id:
        return

    try:
        from app.core import get_browser

        browser = get_browser(auto_connect=False)
        pool = getattr(browser, "_tab_pool", None)
        session = getattr(pool, "_tabs", {}).get(tab_id) if pool is not None else None
        if session is None:
            return

        current_task = str(getattr(session, "current_task_id", "") or "").strip()
        bound_request_id = str(getattr(session, "_bound_request_id", "") or "").strip()
        if current_task and current_task != ctx.request_id:
            logger.warning(
                f"[{tab_id}] leaked worker found but tab has another task; skip retire "
                f"(request={ctx.request_id}, current_task={current_task})"
            )
            return
        if bound_request_id and bound_request_id != ctx.request_id:
            logger.warning(
                f"[{tab_id}] leaked worker found but bound request changed; skip retire "
                f"(request={ctx.request_id}, bound={bound_request_id})"
            )
            return

        if hasattr(session, "mark_error"):
            session.mark_error(reason)

        raw_tab_id = _resolve_raw_tab_id(pool, session)

        if pool is not None:
            try:
                monitor = getattr(pool, "_global_network_monitor", None)
                if monitor is not None and hasattr(monitor, "request_stop_for_session"):
                    monitor.request_stop_for_session(tab_id, reason=reason, detach=True)
            except Exception as e:
                logger.debug(f"[{tab_id}] stop leaked tab global monitor failed: {e}")

        if pool is not None and hasattr(pool, "_condition"):
            try:
                with pool._condition:
                    pool._condition.notify_all()
            except Exception:
                pass

        logger.warning(
            f"[{tab_id}] worker did not exit in time; marked tab ERROR "
            f"(request={ctx.request_id}, reason={reason}, raw={raw_tab_id or '-'}, "
            "browser_tab_retained=True)"
        )
    except Exception as e:
        logger.debug(f"retire leaked worker tab failed: {e}")


def cleanup_worker_thread(
    worker_thread: Optional[threading.Thread],
    ctx: RequestContext,
    *,
    cancel_reason: str = "cleanup",
    join_timeout: float = 5.0,
    retire_reason: str = "worker_cleanup_timeout",
) -> bool:
    """Cancel, join, and retire a worker thread that outlives its request."""
    if not isinstance(worker_thread, threading.Thread) or not worker_thread.is_alive():
        return False

    ctx.request_cancel(cancel_reason)
    worker_thread.join(timeout=max(0.0, float(join_timeout or 0.0)))
    if worker_thread.is_alive():
        retire_bound_tab_after_worker_leak(ctx, retire_reason)
        return True
    return False


def schedule_completed_worker_retire_check(
    worker_thread: threading.Thread,
    ctx: RequestContext,
    reason: str,
    *,
    delay_sec: float = COMPLETED_WORKER_RETIRE_GRACE_SEC,
) -> None:
    """Watch a completed request's slow worker cleanup without blocking response finalization."""
    try:
        delay = max(0.1, float(delay_sec or COMPLETED_WORKER_RETIRE_GRACE_SEC))
    except Exception:
        delay = COMPLETED_WORKER_RETIRE_GRACE_SEC

    def _wait_and_retire_if_still_alive() -> None:
        started_at = time.monotonic()
        worker_thread.join(timeout=delay)
        elapsed = time.monotonic() - started_at
        if worker_thread.is_alive():
            logger.warning(
                f"[{ctx.request_id}] completed request worker still alive after cleanup grace; "
                f"retiring bound tab (tab={ctx.tab_id or '-'}, reason={reason}, "
                f"grace={delay:.1f}s)"
            )
            retire_bound_tab_after_worker_leak(ctx, reason)
            return
        logger.debug(
            f"[{ctx.request_id}] completed request worker cleanup finished in background "
            f"(elapsed={elapsed:.2f}s, tab={ctx.tab_id or '-'})"
        )

    threading.Thread(
        target=_wait_and_retire_if_still_alive,
        daemon=True,
        name=f"completed-worker-cleanup-{ctx.request_id[:8]}",
    ).start()


async def cleanup_worker_thread_after_request(
    worker_thread: Optional[threading.Thread],
    ctx: RequestContext,
    *,
    completed: bool = False,
    cancel_reason: str = "cleanup",
    join_timeout: float = 5.0,
    retire_reason: str = "worker_cleanup_timeout",
    completed_join_timeout: float = COMPLETED_WORKER_FAST_JOIN_SEC,
    completed_retire_delay: float = COMPLETED_WORKER_RETIRE_GRACE_SEC,
) -> bool:
    """Cleanup a request worker, deferring leak retirement for already-completed requests.

    Returns True when the worker was still alive after the immediate cleanup window.
    """
    if not isinstance(worker_thread, threading.Thread) or not worker_thread.is_alive():
        return False

    if completed:
        try:
            fast_join = max(0.0, float(completed_join_timeout or 0.0))
        except Exception:
            fast_join = COMPLETED_WORKER_FAST_JOIN_SEC
        await asyncio.to_thread(worker_thread.join, timeout=fast_join)
        if worker_thread.is_alive():
            logger.warning(
                f"[{ctx.request_id}] completed request worker cleanup is still running; "
                f"defer tab retirement check (tab={ctx.tab_id or '-'}, "
                f"fast_join={fast_join:.1f}s, grace={completed_retire_delay:.1f}s)"
            )
            schedule_completed_worker_retire_check(
                worker_thread,
                ctx,
                retire_reason,
                delay_sec=completed_retire_delay,
            )
            return True
        return False

    ctx.request_cancel(cancel_reason)
    try:
        timeout = max(0.0, float(join_timeout or 0.0))
    except Exception:
        timeout = 5.0
    await asyncio.to_thread(worker_thread.join, timeout=timeout)
    if worker_thread.is_alive():
        retire_bound_tab_after_worker_leak(ctx, retire_reason)
        return True
    return False
