"""
Helpers for request execution lifetime, worker cleanup, and tab retirement.
"""

import asyncio
import os
import queue
import threading
import time
from typing import Any, Callable, Dict, Optional

from app.core.config import get_logger
from app.services.request_manager import RequestContext


logger = get_logger("REQUEST.LIFECYCLE")

DEFAULT_MAX_REQUEST_EXECUTE_TIME_SEC = 120.0
MIN_MAX_REQUEST_EXECUTE_TIME_SEC = 5.0
WORKER_QUEUE_FINAL_GRACE_SEC = 5.0


class TrackedWorkerExecutionCancelled(Exception):
    """Raised when a tracked blocking worker keeps running after cancellation."""


def get_max_request_execute_time_sec(default: float = DEFAULT_MAX_REQUEST_EXECUTE_TIME_SEC) -> float:
    """Return the request hard timeout; set MAX_REQUEST_EXECUTE_TIME_SEC<=0 to disable."""
    raw = os.getenv("MAX_REQUEST_EXECUTE_TIME_SEC")
    if raw is None or str(raw).strip() == "":
        return max(MIN_MAX_REQUEST_EXECUTE_TIME_SEC, float(default))

    try:
        value = float(raw)
    except Exception:
        logger.debug(f"Invalid MAX_REQUEST_EXECUTE_TIME_SEC={raw!r}, using default {default}s")
        return max(MIN_MAX_REQUEST_EXECUTE_TIME_SEC, float(default))

    if value <= 0:
        return 0.0
    return max(MIN_MAX_REQUEST_EXECUTE_TIME_SEC, value)


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
    elapsed = time.monotonic() - float(started_at or time.monotonic())
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
            chunk_queue.put(item, timeout=max(0.05, float(poll_timeout or 0.5)))
            return True
        except queue.Full:
            if final:
                if ctx.should_stop() or (deadline is not None and time.monotonic() >= deadline):
                    return False
                continue
            if ctx.should_stop():
                return False

    return False


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


def _get_cancel_reason(ctx: RequestContext, fallback: str = "worker_cancelled") -> str:
    reason = str(getattr(ctx, "cancel_reason", "") or "").strip()
    return reason or fallback


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
            result = worker_fn()
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
        if result_future.done() and worker_state.get("thread") is worker_thread:
            worker_state["thread"] = None
            worker_state["label"] = None


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


def _submit_raw_tab_close(pool: Any, tab_id: str, raw_tab_id: str, context_id: str, reason: str) -> bool:
    if pool is None or not raw_tab_id or not hasattr(pool, "_close_raw_tab"):
        return False

    def _close_and_dispose() -> None:
        closed_raw_tab = False
        try:
            closed_raw_tab = bool(pool._close_raw_tab(raw_tab_id))
        except Exception as e:
            logger.debug(f"[{tab_id}] close leaked raw tab failed: raw={raw_tab_id}, err={e}")

        if closed_raw_tab and context_id and hasattr(pool, "_dispose_browser_context"):
            try:
                pool._dispose_browser_context(context_id)
            except Exception as e:
                logger.debug(f"[{tab_id}] dispose leaked browser context failed: {e}")

        try:
            condition = getattr(pool, "_condition", None)
            if condition is not None:
                with condition:
                    condition.notify_all()
        except Exception:
            pass

        logger.warning(
            f"[{tab_id}] leaked worker raw tab close finished "
            f"(reason={reason}, raw={raw_tab_id}, closed={closed_raw_tab})"
        )

    executor = getattr(pool, "_maintenance_executor", None)
    if executor is not None:
        try:
            executor.submit(_close_and_dispose)
            return True
        except RuntimeError as e:
            logger.debug(f"[{tab_id}] maintenance submit failed for leaked raw tab close: {e}")

    try:
        threading.Thread(
            target=_close_and_dispose,
            daemon=True,
            name=f"tab-retire-close-{tab_id}",
        ).start()
        return True
    except Exception as e:
        logger.debug(f"[{tab_id}] failed to spawn leaked raw tab close thread: {e}")
        return False


def retire_bound_tab_after_worker_leak(ctx: RequestContext, reason: str) -> None:
    """Mark the tab bound to ctx as unhealthy and close its raw target off-thread."""
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
        context_id = str(getattr(session, "browser_context_id", "") or "").strip()

        if pool is not None:
            try:
                monitor = getattr(pool, "_global_network_monitor", None)
                if monitor is not None and hasattr(monitor, "request_stop_for_session"):
                    monitor.request_stop_for_session(tab_id, reason=reason, detach=True)
            except Exception as e:
                logger.debug(f"[{tab_id}] stop leaked tab global monitor failed: {e}")

        close_submitted = _submit_raw_tab_close(pool, tab_id, raw_tab_id, context_id, reason)

        if pool is not None and hasattr(pool, "_condition"):
            try:
                with pool._condition:
                    pool._condition.notify_all()
            except Exception:
                pass

        logger.warning(
            f"[{tab_id}] worker did not exit in time; marked tab ERROR "
            f"(request={ctx.request_id}, reason={reason}, raw={raw_tab_id or '-'}, "
            f"close_submitted={close_submitted})"
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
