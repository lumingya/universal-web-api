import threading
import time
from dataclasses import dataclass
from typing import Any, Dict

from app.core.config import logger

from .session import TabSession


_ARENA_STORE_SNAPSHOT_JS = r"""
return (() => {
  function safe(fn, fallback) {
    try { return fn(); } catch (error) { return fallback; }
  }
  function textOf(value) {
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      return value.map(item => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object') return item.text || item.content || '';
        return '';
      }).filter(Boolean).join('\n');
    }
    return '';
  }
  function preferredModelName(value) {
    if (!value || typeof value !== 'object') return '';
    return String(value.displayName || value.publicName || value.name || value.modelName || value.slug || '').trim();
  }
  function modelIdOf(message) {
    if (!message || typeof message !== 'object') return '';
    const model = message.model;
    if (model && typeof model === 'object') {
      return String(model.id || model.modelId || '').trim();
    }
    return String(message.modelId || message.model || message.modelName || message.modelSlug || '').trim();
  }
  function getPageModelMap() {
    const html = safe(() => document.documentElement.outerHTML || '', '');
    const cache = window.__arenaDetectorModelMap;
    if (cache && cache.htmlLength === html.length && cache.map) return cache.map;

    const map = {};
    const re = /\{\\"id\\":\\"[a-f0-9-]+\\"/g;
    let match;
    while ((match = re.exec(html)) && Object.keys(map).length < 2000) {
      const start = match.index;
      let openBraces = 0;
      let end = -1;
      const limit = Math.min(html.length, start + 20000);
      for (let i = start; i < limit; i += 1) {
        if (html[i] === '{') openBraces += 1;
        else if (html[i] === '}') {
          openBraces -= 1;
          if (openBraces === 0) {
            end = i + 1;
            break;
          }
        }
      }
      if (end < 0) continue;
      const raw = html.slice(start, end).replace(/\\"/g, '"').replace(/\\\\/g, '\\');
      const item = safe(() => JSON.parse(raw), null);
      if (!item || typeof item !== 'object' || !item.id) continue;
      const name = preferredModelName(item);
      if (name) map[String(item.id).trim()] = name;
    }
    window.__arenaDetectorModelMap = { htmlLength: html.length, map };
    return map;
  }
  function modelNameOf(message) {
    if (!message || typeof message !== 'object') return '';
    const direct = preferredModelName(message.model) || preferredModelName(message);
    if (direct) return direct;
    const modelId = modelIdOf(message);
    if (!modelId) return '';
    return getPageModelMap()[modelId] || modelId;
  }
  function findReactFiber(el) {
    if (!el) return null;
    const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
    return key ? el[key] : null;
  }
  function looksLikeArenaStore(value) {
    if (!value || typeof value !== 'object') return false;
    if (typeof value.getState !== 'function') return false;
    const state = safe(() => value.getState(), null);
    return !!(state && typeof state === 'object' && Array.isArray(state.messages) && typeof state.id === 'string');
  }
  function findArenaStoreIn(value, depth, seen) {
    if (!value || typeof value !== 'object' || depth < 0 || seen.has(value)) return null;
    seen.add(value);
    if (looksLikeArenaStore(value)) return value;
    const keys = safe(() => Object.keys(value), []);
    for (const key of keys.slice(0, 100)) {
      if (['_owner', 'return', 'child', 'sibling', 'alternate'].includes(key)) continue;
      const found = findArenaStoreIn(value[key], depth - 1, seen);
      if (found) return found;
    }
    return null;
  }
  function findStoreFromFiber() {
    const roots = [
      document.querySelector('main'),
      document.querySelector('form'),
      document.body,
    ].filter(Boolean);
    for (const root of roots) {
      const fiber = findReactFiber(root);
      for (let cur = fiber, depth = 0; cur && depth < 100; depth += 1, cur = cur.return) {
        const found = findArenaStoreIn(cur.memoizedProps, 5, new WeakSet())
          || findArenaStoreIn(cur.memoizedState, 5, new WeakSet());
        if (found) return found;
      }
    }
    return null;
  }
  const store = findStoreFromFiber();
  const state = store && safe(() => store.getState(), null);
  const messages = state && Array.isArray(state.messages) ? state.messages : [];
  const byId = new Map(messages.map(message => [String(message && message.id || ''), message]));
  let assistantIds = Array.isArray(state && state.lastMessageIds)
    ? state.lastMessageIds.map(id => String(id || '')).filter(id => byId.get(id) && byId.get(id).role === 'assistant')
    : [];
  if (assistantIds.length < 2) {
    assistantIds = messages
      .filter(message => message && message.role === 'assistant')
      .slice(-2)
      .map(message => String(message.id || ''));
  }
  const a = byId.get(assistantIds[0]) || null;
  const b = byId.get(assistantIds[1]) || null;
  const parentIds = []
    .concat(Array.isArray(a && a.parentMessageIds) ? a.parentMessageIds : [])
    .concat(Array.isArray(b && b.parentMessageIds) ? b.parentMessageIds : []);
  let userMessage = null;
  for (const parentId of parentIds) {
    const candidate = byId.get(String(parentId || ''));
    if (candidate && candidate.role === 'user') {
      userMessage = candidate;
      break;
    }
  }
  if (!userMessage) {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i] && messages[i].role === 'user') {
        userMessage = messages[i];
        break;
      }
    }
  }
  return {
    url: location.href,
    conversation_id: String(state && state.id || ''),
    prompt: textOf(userMessage && userMessage.content),
    message_id_a: String(a && a.id || ''),
    message_id_b: String(b && b.id || ''),
    status_a: String(a && a.status || ''),
    status_b: String(b && b.status || ''),
    model_a: modelNameOf(a),
    model_b: modelNameOf(b),
    model_id_a: modelIdOf(a),
    model_id_b: modelIdOf(b),
    response_a: textOf(a && a.content),
    response_b: textOf(b && b.content),
  };
})()
""".strip()


@dataclass
class _GlobalNetworkWorker:
    """单个标签页的全局网络监听工作线程。"""
    session_id: str
    thread: threading.Thread
    stop_event: threading.Event


class _GlobalNetworkInterceptionManager:
    """
    全局常驻网络事件监听。

    设计要点：
    - 仅在标签页空闲时运行；
    - 标签页被任务占用时暂停，让位给工作流内监听器；
    - 命令触发逻辑仍由 CommandEngine 决定；
    - 只对 Arena 候选响应额外读取 body，用于外部判定仪事件桥接。
    """

    LISTENER_STOP_TIMEOUT_SEC = 2.0
    LISTENER_CLEAR_INTERVAL_SEC = 60.0
    LISTENER_CLEAR_EVENT_INTERVAL = 200
    ARENA_REVEAL_POLL_INTERVAL_SEC = 1.0
    ARENA_REVEAL_POLL_TIMEOUT_SEC = 600.0

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
        self._result_event_handler = self._create_result_event_handler()
        self._arena_reveal_pollers: Dict[str, threading.Thread] = {}
        self._arena_reveal_lock = threading.RLock()
        self._arena_reveal_logged_signatures = set()

    @staticmethod
    def _create_result_event_handler():
        try:
            from app.services.result_event_bridge import create_result_event_handler
            handler = create_result_event_handler()
            if handler:
                logger.info("[GlobalNet] Arena 结果事件桥接已启用（支持手动网页测试）")
            return handler
        except Exception as e:
            logger.debug(f"[GlobalNet] Arena 结果事件桥接初始化失败（忽略）: {e}")
            return None

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
                    reset_ok = cls._force_reset_listen_state(tab)
                    logger.warning(
                        f"[GlobalNet] listen.stop timed out after "
                        f"{cls.LISTENER_STOP_TIMEOUT_SEC:.1f}s; "
                        f"driver_stop_skipped=True, forced_reset={reset_ok}"
                    )
                    return reset_ok and not cls._listener_is_marked_active(listener)
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

    @staticmethod
    def _is_result_bridge_candidate(event: Dict[str, Any]) -> bool:
        url = str((event or {}).get("url") or "").strip().lower()
        return any(
            token in url
            for token in (
                "lmarena.ai",
                "arena.ai",
                "lmsys.org",
                "nextjs-api/stream",
            )
        )

    @staticmethod
    def _is_reveal_snapshot_candidate(event: Dict[str, Any]) -> bool:
        url = str((event or {}).get("url") or "").strip().lower()
        if "arena.ai" not in url and "lmarena.ai" not in url:
            return False
        return any(
            token in url
            for token in (
                "/nextjs-api/stream/",
                "/rpc/i/",
                "/api/history/",
                "/c/",
                "_rsc=",
            )
        )

    @staticmethod
    def _extract_request_post_data(response: Any) -> Any:
        req = getattr(response, "request", None)
        if req is None:
            return None
        for attr in ("postData", "post_data", "body"):
            try:
                value = getattr(req, attr, None)
            except Exception:
                value = None
            if value:
                return value
        return None

    @staticmethod
    def _read_response_body(response: Any, stop_event: threading.Event) -> tuple[Any, str]:
        if getattr(response, "_stream_enabled", False):
            deadline = time.time() + 120.0
            while time.time() < deadline and not stop_event.is_set():
                stream = getattr(response, "_stream", None)
                if isinstance(stream, dict) and stream.get("complete"):
                    break
                time.sleep(0.1)
            stream = getattr(response, "_stream", None)
            if isinstance(stream, dict):
                return stream.get("fullText") or "", "drission_stream"
            return "", "drission_stream"

        resp = getattr(response, "response", None)
        if resp is None:
            return "", "response_missing"

        try:
            body = getattr(resp, "body", "")
            return body or "", "response_body"
        except Exception as e:
            logger.debug(f"[GlobalNet] 读取候选响应体失败: {e}")
            return "", "response_body_error"

    def _dispatch_result_bridge_async(
        self,
        session: TabSession,
        response: Any,
        event: Dict[str, Any],
        stop_event: threading.Event,
    ) -> None:
        if not self._result_event_handler:
            return
        if not self._is_result_bridge_candidate(event):
            return

        thread = threading.Thread(
            target=self._dispatch_result_bridge,
            args=(session, response, dict(event or {}), stop_event),
            daemon=True,
            name=f"global-net-arena-{getattr(session, 'id', 'unknown')}",
        )
        thread.start()

    def _start_arena_reveal_poll(
        self,
        session: TabSession,
        event: Dict[str, Any],
        stop_event: threading.Event,
        reason: str,
    ) -> None:
        if not session or not self._is_reveal_snapshot_candidate(event):
            return
        session_id = str(getattr(session, "id", "") or "")
        if not session_id:
            return
        with self._arena_reveal_lock:
            current = self._arena_reveal_pollers.get(session_id)
            if current and current.is_alive():
                return
            thread = threading.Thread(
                target=self._arena_reveal_poll_loop,
                args=(session_id, stop_event, reason),
                daemon=True,
                name=f"global-net-reveal-{session_id}",
            )
            self._arena_reveal_pollers[session_id] = thread
            thread.start()

    def _arena_reveal_poll_loop(
        self,
        session_id: str,
        stop_event: threading.Event,
        reason: str,
    ) -> None:
        try:
            from app.services.result_event_bridge import emit_arena_snapshot_event
        except Exception as e:
            logger.debug(f"[GlobalNet] Arena 翻牌快照桥接不可用（忽略）: {e}")
            return

        deadline = time.time() + self.ARENA_REVEAL_POLL_TIMEOUT_SEC
        last_signature = ""
        try:
            while time.time() < deadline and not stop_event.is_set() and not self._is_shutdown():
                session = self._get_session(session_id)
                tab = getattr(session, "tab", None) if session is not None else None
                if tab is None:
                    return
                try:
                    snapshot = tab.run_js(_ARENA_STORE_SNAPSHOT_JS)
                except Exception as e:
                    logger.debug_throttled(
                        f"global_net.arena_reveal_snapshot.{session_id}",
                        f"[GlobalNet] 读取 Arena 翻牌快照失败（忽略）: {e}",
                        interval_sec=10.0,
                    )
                    time.sleep(self.ARENA_REVEAL_POLL_INTERVAL_SEC)
                    continue

                if not isinstance(snapshot, dict):
                    time.sleep(self.ARENA_REVEAL_POLL_INTERVAL_SEC)
                    continue
                snapshot["session_id"] = session_id
                model_a = str(snapshot.get("model_a") or "").strip()
                model_b = str(snapshot.get("model_b") or "").strip()
                response_a = str(snapshot.get("response_a") or "")
                response_b = str(snapshot.get("response_b") or "")
                signature = (
                    f"{snapshot.get('conversation_id')}|{snapshot.get('message_id_a')}|"
                    f"{snapshot.get('message_id_b')}|{model_a}|{model_b}"
                )
                if signature != last_signature:
                    last_signature = signature
                    log_signature = f"{session_id}|{signature}"
                    with self._arena_reveal_lock:
                        should_log = log_signature not in self._arena_reveal_logged_signatures
                        if should_log:
                            self._arena_reveal_logged_signatures.add(log_signature)
                            if len(self._arena_reveal_logged_signatures) > 500:
                                self._arena_reveal_logged_signatures.clear()
                    if should_log and (model_a or model_b):
                        logger.debug(
                            "[GlobalNet] Arena 翻牌快照更新: "
                            f"reason={reason}, model_a={model_a or '-'}, model_b={model_b or '-'}, "
                            f"a={len(response_a)}, b={len(response_b)}"
                        )

                if model_a and model_b and response_a and response_b:
                    emit_arena_snapshot_event(snapshot)
                    return

                time.sleep(self.ARENA_REVEAL_POLL_INTERVAL_SEC)
        finally:
            with self._arena_reveal_lock:
                current = self._arena_reveal_pollers.get(session_id)
                if current is threading.current_thread():
                    self._arena_reveal_pollers.pop(session_id, None)

    def _dispatch_result_bridge(
        self,
        session: TabSession,
        response: Any,
        event: Dict[str, Any],
        stop_event: threading.Event,
    ) -> None:
        try:
            raw_body, raw_body_source = self._read_response_body(response, stop_event)
            if not raw_body:
                return

            post_data = self._extract_request_post_data(response)
            self._result_event_handler(
                {
                    "event": event,
                    "raw_body": raw_body,
                    "raw_body_source": raw_body_source,
                    "request_post_data": post_data,
                    "parse_result": {"done": True},
                    "parser_id": "lmarena_global",
                    "session_id": getattr(session, "id", ""),
                }
            )
        except Exception as e:
            logger.debug(f"[GlobalNet] Arena 结果事件桥接失败（忽略）: {e}")

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
                self._dispatch_result_bridge_async(session, response, event, stop_event)
                self._start_arena_reveal_poll(session, event, stop_event, "network-event")
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
