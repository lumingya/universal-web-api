import threading
import time
from dataclasses import dataclass
from typing import Any, Dict

from app.core.config import logger

from .session import TabSession


@dataclass
class _GlobalNetworkWorker:
    """单个标签页的全局网络监听工作线程。"""
    session_id: str
    thread: threading.Thread
    stop_event: threading.Event


class _GlobalNetworkInterceptionManager:
    """
    全局常驻网络事件监听（仅负责把事件上报给 CommandEngine）。

    设计要点：
    - 仅在标签页空闲时运行；
    - 标签页被任务占用时暂停，让位给工作流内监听器；
    - 事件命中逻辑仍由 CommandEngine 决定。
    """

    LISTENER_STOP_TIMEOUT_SEC = 2.0
    LISTENER_CLEAR_INTERVAL_SEC = 60.0
    LISTENER_CLEAR_EVENT_INTERVAL = 200

    def __init__(
        self,
        get_session_fn,
        is_shutdown_fn,
        listen_pattern: str = "http",
        wait_timeout: float = 0.5,
        retry_delay: float = 1.0,
    ):
        self._get_session = get_session_fn
        self._is_shutdown = is_shutdown_fn
        self._listen_pattern = str(listen_pattern or "http").strip() or "http"
        self._wait_timeout = max(0.1, float(wait_timeout or 0.5))
        self._retry_delay = max(0.2, float(retry_delay or 1.0))
        self._workers: Dict[str, _GlobalNetworkWorker] = {}
        self._lock = threading.RLock()
        self._stop_join_timeout = max(2.0, self._wait_timeout + self._retry_delay + 0.2)

    @staticmethod
    def _extract_event(response: Any) -> Dict[str, Any]:
        req = getattr(response, "request", None)
        resp = getattr(response, "response", None)

        url = (
            getattr(req, "url", None)
            or getattr(resp, "url", None)
            or getattr(response, "url", None)
            or ""
        )
        method = (
            getattr(req, "method", None)
            or getattr(response, "method", None)
            or ""
        )
        status = (
            getattr(resp, "status", None)
            or getattr(resp, "status_code", None)
            or getattr(response, "status", None)
            or 0
        )

        try:
            status = int(status)
        except Exception:
            status = 0

        return {
            "url": str(url or ""),
            "method": str(method or "").upper(),
            "status": status,
            "timestamp": time.time(),
        }

    @staticmethod
    def _is_expected_stop_error(error: Any) -> bool:
        text = str(error or "").strip().lower()
        if not text:
            return False
        expected_markers = (
            "监听未启动或已停止",
            "target closed",
            "invalid session",
            "no such window",
            "not connected",
            "connection refused",
            "disconnected",
        )
        if "nonetype" in text and "is_running" in text:
            return True
        return any(marker in text for marker in expected_markers)

    @staticmethod
    def _force_reset_listen_state(tab: Any) -> bool:
        listener = getattr(tab, "listen", None)
        if listener is None:
            return True

        ok = True
        for attr, value in (
            ("listening", False),
            ("_network_enabled", False),
            ("_driver", None),
        ):
            try:
                if hasattr(listener, attr):
                    setattr(listener, attr, value)
            except Exception:
                ok = False

        try:
            clear = getattr(listener, "clear", None)
            if callable(clear):
                clear()
        except Exception:
            ok = False

        return ok

    @staticmethod
    def _force_stop_listener_driver(listener: Any) -> bool:
        driver = getattr(listener, "_driver", None)
        if driver is None:
            return False

        stopped = False
        for method_name in ("stop", "close", "disconnect"):
            method = getattr(driver, method_name, None)
            if not callable(method):
                continue
            try:
                method()
                stopped = True
                break
            except Exception:
                pass
        return stopped

    @staticmethod
    def _listener_is_marked_active(listener: Any) -> bool:
        try:
            return bool(getattr(listener, "listening", False))
        except Exception:
            return False

    @classmethod
    def _safe_stop_listen(cls, tab: Any) -> bool:
        listener = getattr(tab, "listen", None)
        if listener is None:
            return True

        try:
            if cls._listener_is_marked_active(listener):
                stop_result: Dict[str, Any] = {"error": None}

                def _stop_listener():
                    try:
                        listener.stop()
                    except Exception as e:
                        stop_result["error"] = e

                stop_thread = threading.Thread(
                    target=_stop_listener,
                    daemon=True,
                    name="global-net-listen-stop",
                )
                stop_thread.start()
                stop_thread.join(timeout=cls.LISTENER_STOP_TIMEOUT_SEC)
                if stop_thread.is_alive():
                    driver_stopped = cls._force_stop_listener_driver(listener)
                    reset_ok = cls._force_reset_listen_state(tab)
                    logger.warning(
                        f"[GlobalNet] listen.stop timed out after "
                        f"{cls.LISTENER_STOP_TIMEOUT_SEC:.1f}s; "
                        f"driver_stopped={driver_stopped}, forced_reset={reset_ok}"
                    )
                    return False
                if stop_result["error"] is not None:
                    raise stop_result["error"]
        except Exception as e:
            reset_ok = cls._force_reset_listen_state(tab)
            log = logger.debug if cls._is_expected_stop_error(e) else logger.warning
            log(f"[GlobalNet] listen.stop failed; forced_reset={reset_ok}: {e}")
            return reset_ok and not cls._listener_is_marked_active(listener)

        try:
            if cls._listener_is_marked_active(listener):
                reset_ok = cls._force_reset_listen_state(tab)
                if cls._listener_is_marked_active(listener):
                    logger.warning(
                        f"[GlobalNet] listen.stop returned but listener is still active "
                        f"(forced_reset={reset_ok})"
                    )
                    return False
                logger.debug("[GlobalNet] listener state reset after stop")
        except Exception as e:
            reset_ok = cls._force_reset_listen_state(tab)
            log = logger.debug if cls._is_expected_stop_error(e) else logger.warning
            log(f"[GlobalNet] listen state check failed; forced_reset={reset_ok}: {e}")
            return reset_ok

        try:
            clear = getattr(listener, "clear", None)
            if callable(clear):
                clear()
        except Exception:
            pass

        return True

    @staticmethod
    def _safe_clear_listener(tab: Any, session_id: str, reason: str) -> bool:
        listener = getattr(tab, "listen", None)
        if listener is None:
            return True

        try:
            clear = getattr(listener, "clear", None)
            if callable(clear):
                clear()
                return True
        except Exception as e:
            logger.debug(f"[GlobalNet] 清理监听残留失败: {session_id}, reason={reason}, err={e}")
            return False

        return False

    def _should_cleanup_worker_listener(
        self,
        session_id: str,
        stop_event: threading.Event,
        tab: Any = None,
    ) -> bool:
        with self._lock:
            current = self._workers.get(session_id)
            if current is None or current.stop_event is stop_event:
                return True
        if tab is None:
            return False
        try:
            session = self._get_session(session_id)
            return session is None or getattr(session, "tab", None) is not tab
        except Exception:
            return False

    def _dispatch_event(self, session: TabSession, event: Dict[str, Any]):
        try:
            from app.services.command_engine import command_engine
            command_engine.handle_network_event(session, event)
        except Exception as e:
            logger.debug(f"[GlobalNet] 事件上报失败（忽略）: {e}")

    def _forget_worker_if_current(self, worker: _GlobalNetworkWorker) -> None:
        with self._lock:
            current = self._workers.get(worker.session_id)
            if current is worker:
                self._workers.pop(worker.session_id, None)

    def _join_stopping_worker(self, worker: _GlobalNetworkWorker, reason: str) -> bool:
        if not worker.thread.is_alive():
            self._forget_worker_if_current(worker)
            return True
        if worker.thread is threading.current_thread():
            return False

        worker.thread.join(timeout=self._stop_join_timeout)
        if worker.thread.is_alive():
            logger.warning(
                f"[GlobalNet] previous worker still stopping: {worker.session_id} "
                f"(reason={reason or '-'})"
            )
            return False

        self._forget_worker_if_current(worker)
        return True

    def start_for_session(self, session: TabSession) -> bool:
        if not session:
            return False

        while True:
            with self._lock:
                existing = self._workers.get(session.id)
                if existing is None:
                    break
                if not existing.thread.is_alive():
                    self._workers.pop(session.id, None)
                    break
                if not existing.stop_event.is_set():
                    return True

            if not self._join_stopping_worker(existing, "start"):
                return False

        with self._lock:
            existing = self._workers.get(session.id)
            if existing and existing.thread.is_alive():
                return True
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._worker_loop,
                args=(session.id, stop_event),
                daemon=True,
                name=f"global-net-{session.id}",
            )
            self._workers[session.id] = _GlobalNetworkWorker(
                session_id=session.id,
                thread=thread,
                stop_event=stop_event,
            )
            thread.start()
            logger.debug(f"[GlobalNet] 启动监听: {session.id} pattern={self._listen_pattern!r}")
            return True

    def stop_for_session(self, session_id: str, reason: str = "", join: bool = False) -> bool:
        if not session_id:
            return True

        worker = None
        with self._lock:
            worker = self._workers.pop(session_id, None)
        if not worker:
            return True

        worker.stop_event.set()

        session = self._get_session(session_id)
        stop_ok = True
        if session is not None:
            stop_ok = self._safe_stop_listen(session.tab)

        should_join = bool(join or not stop_ok)
        if should_join and worker.thread.is_alive() and worker.thread is not threading.current_thread():
            worker.thread.join(timeout=self._stop_join_timeout)
            if worker.thread.is_alive():
                logger.warning(
                    f"[GlobalNet] worker did not stop promptly: {session_id} "
                    f"(reason={reason or '-'}, stop_listen_ok={stop_ok}, "
                    f"requested_join={join})"
                )
                return False

        if reason:
            logger.debug(f"[GlobalNet] 停止监听: {session_id} ({reason})")
        else:
            logger.debug(f"[GlobalNet] 停止监听: {session_id}")
        return True

    def request_stop_for_session(
        self,
        session_id: str,
        reason: str = "",
        *,
        detach: bool = False,
    ) -> bool:
        if not session_id:
            return True

        with self._lock:
            worker = self._workers.get(session_id)
        if not worker:
            return True

        worker.stop_event.set()
        if reason:
            logger.debug(f"[GlobalNet] 请求停止监听: {session_id} ({reason})")
        else:
            logger.debug(f"[GlobalNet] 请求停止监听: {session_id}")
        return True

    def shutdown(self):
        with self._lock:
            session_ids = list(self._workers.keys())
        for session_id in session_ids:
            self.stop_for_session(session_id, reason="shutdown", join=True)
        logger.info("[GlobalNet] 全局网络监听已关闭")

    def _worker_loop(self, session_id: str, stop_event: threading.Event):
        tab = None
        listening = False
        last_listener_clear_at = time.monotonic()
        events_since_listener_clear = 0

        try:
            while not stop_event.is_set():
                if self._is_shutdown():
                    break

                session = self._get_session(session_id)
                if session is None:
                    break

                tab = session.tab

                if not listening:
                    try:
                        # 复用连接，降低对 CDP session 的额外占用
                        tab.listen._reuse_driver = True
                        tab.listen.start(self._listen_pattern)
                        listening = True
                        last_listener_clear_at = time.monotonic()
                        events_since_listener_clear = 0
                    except Exception as e:
                        logger.debug(f"[GlobalNet] 启动监听失败: {session_id}, err={e}")
                        stop_event.wait(self._retry_delay)
                        continue

                now = time.monotonic()
                if listening and now - last_listener_clear_at >= self.LISTENER_CLEAR_INTERVAL_SEC:
                    if self._safe_clear_listener(tab, session_id, "interval"):
                        last_listener_clear_at = now
                        events_since_listener_clear = 0

                try:
                    response = tab.listen.wait(timeout=self._wait_timeout)
                except Exception as e:
                    if stop_event.is_set() or self._is_shutdown():
                        break
                    err_text = str(e)
                    if "NoneType" in err_text and "is_running" in err_text:
                        logger.debug(f"[GlobalNet] 监听状态失效，准备重启: {session_id}")
                    else:
                        logger.debug(f"[GlobalNet] wait 异常: {session_id}, err={e}")
                    listening = False
                    self._safe_stop_listen(tab)
                    stop_event.wait(self._retry_delay)
                    continue

                if response is None or response is False:
                    continue

                if stop_event.is_set() or self._is_shutdown():
                    break

                event = self._extract_event(response)
                self._dispatch_event(session, event)
                events_since_listener_clear += 1
                if events_since_listener_clear >= self.LISTENER_CLEAR_EVENT_INTERVAL:
                    if self._safe_clear_listener(tab, session_id, "event_budget"):
                        last_listener_clear_at = time.monotonic()
                        events_since_listener_clear = 0

        finally:
            if tab is not None and self._should_cleanup_worker_listener(session_id, stop_event, tab):
                self._safe_stop_listen(tab)
            with self._lock:
                current = self._workers.get(session_id)
                if current is not None and current.stop_event is stop_event:
                    self._workers.pop(session_id, None)
