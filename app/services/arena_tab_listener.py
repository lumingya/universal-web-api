"""
app/services/arena_tab_listener.py - Arena 翻牌探测与结果桥接扩展监听器

从通用全局网络拦截层解耦的独立扩展模块。
通过 register_response_listener 挂载到 _GlobalNetworkInterceptionManager，
实现对 Arena 候选响应的事件桥接与前端 React Fiber store 的翻牌轮询。
"""

import threading
import time
from typing import Any, Callable, Dict, Optional, Set

from app.core.config import logger
from app.core.page_lifecycle import is_page_refresh_error
from app.core.tab_pool_parts.session import TabSession


def is_explicit_arena_direct_url(url: Any) -> bool:
    """Check if URL explicitly points to Arena direct chat page."""
    try:
        raw = str(url or "").strip()
        if not raw:
            return False
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(raw)
        path = (parsed.path or "").rstrip("/").lower()
        if path in {"/direct", "/text/direct", "/image/direct", "/code/direct", "/search/direct"}:
            return True
        if path.startswith(("/direct/", "/text/direct/", "/image/direct/", "/code/direct/", "/search/direct/")):
            return True
        if path.endswith("/direct"):
            return True
        query = parse_qs(parsed.query)
        if query.get("mode") == ["direct"]:
            return True
        return False
    except Exception:
        return False


_ARENA_STORE_SNAPSHOT_JS = r"""
return (() => {
  function safe(fn, fallback) {
    try { return fn(); } catch (error) { return fallback; }
  }
  function isExplicitDirectUrl(url) {
    try {
      const u = new URL(url || location.href);
      const p = String(u.pathname || '').toLowerCase();
      if (
        p === '/direct' ||
        p.startsWith('/direct/') ||
        p === '/text/direct' ||
        p.startsWith('/text/direct/') ||
        p === '/image/direct' ||
        p.startsWith('/image/direct/') ||
        p === '/code/direct' ||
        p.startsWith('/code/direct/') ||
        p === '/search/direct' ||
        p.startsWith('/search/direct/') ||
        p.endsWith('/direct') ||
        u.searchParams.get('mode') === 'direct'
      ) return true;
      return false;
    } catch (e) {
      const s = String(url || location.href || '').toLowerCase();
      return s.includes('/direct') || s.includes('mode=direct');
    }
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
  const stateMode = String((state && (state.evaluationMode || state.mode || state.sessionMode || state.type)) || '').toLowerCase();
  const isDirect = stateMode.includes('direct') || stateMode.includes('single') || isExplicitDirectUrl(location.href);

  if (isDirect) {
    return {
      is_direct: true,
      mode: 'direct',
      url: location.href,
      conversation_id: String(state && state.id || ''),
      message_id_a: '',
      message_id_b: '',
      status_a: '',
      status_b: '',
      model_a: '',
      model_b: '',
      model_id_a: '',
      model_id_b: '',
      response_a: '',
      response_b: '',
    };
  }

  const messages = state && Array.isArray(state.messages) ? state.messages : [];
  const byId = new Map(messages.map(message => [String(message && message.id || ''), message]));
  let assistantIds = Array.isArray(state && state.lastMessageIds)
    ? state.lastMessageIds.map(id => String(id || '')).filter(id => byId.get(id) && byId.get(id).role === 'assistant')
    : [];

  // 严格校验双侧对战：必须有成对的 2 个 assistant 消息
  if (assistantIds.length < 2) {
    const lastAssistants = messages.filter(m => m && m.role === 'assistant');
    if (lastAssistants.length >= 2) {
      const cand1 = lastAssistants[lastAssistants.length - 1];
      const cand2 = lastAssistants[lastAssistants.length - 2];
      const p1 = String((Array.isArray(cand1.parentMessageIds) ? cand1.parentMessageIds[0] : cand1.parentId) || '');
      const p2 = String((Array.isArray(cand2.parentMessageIds) ? cand2.parentMessageIds[0] : cand2.parentId) || '');
      const s1 = String(cand1.side || cand1.modelSide || '').toLowerCase();
      const s2 = String(cand2.side || cand2.modelSide || '').toLowerCase();
      const m1 = modelIdOf(cand1);
      const m2 = modelIdOf(cand2);
      const isBattlePair = (p1 && p2 && p1 === p2) && (
        (s1 && s2 && s1 !== s2) ||
        (m1 && m2 && m1 !== m2)
      );
      if (isBattlePair) {
        assistantIds = [String(cand2.id || ''), String(cand1.id || '')];
      }
    }
  }

  if (assistantIds.length < 2) {
    return {
      is_direct: false,
      mode: stateMode || 'arena',
      url: location.href,
      conversation_id: String(state && state.id || ''),
      message_id_a: '',
      message_id_b: '',
      status_a: '',
      status_b: '',
      model_a: '',
      model_b: '',
      model_id_a: '',
      model_id_b: '',
      response_a: '',
      response_b: '',
    };
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
    is_direct: false,
    mode: stateMode || 'arena',
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


class ArenaTabListener:
    """Arena 翻牌探测与结果桥接监听器。"""

    ARENA_REVEAL_POLL_INTERVAL_SEC = 3.0
    ARENA_REVEAL_POLL_TIMEOUT_SEC = 120.0
    RESULT_BRIDGE_MAX_ACTIVE_PER_SESSION = 2

    def __init__(
        self,
        get_session_fn: Optional[Callable[[str], Optional[TabSession]]] = None,
        is_shutdown_fn: Optional[Callable[[], bool]] = None,
    ):
        self._get_session = get_session_fn
        self._is_shutdown = is_shutdown_fn or (lambda: False)
        self._result_event_handler = self._create_result_event_handler()
        self._result_bridge_lock = threading.RLock()
        self._result_bridge_active_by_session: Dict[str, int] = {}
        self._arena_reveal_pollers: Dict[str, threading.Thread] = {}
        self._arena_reveal_lock = threading.RLock()
        self._arena_reveal_logged_signatures: Set[str] = set()

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
    def is_arena_candidate(event: Dict[str, Any]) -> bool:
        """快速判断事件是否可能属于 Arena。"""
        url = str((event or {}).get("url") or "").strip().lower()
        if not url:
            return False
        return any(host in url for host in ("lmarena.ai", "arena.ai", "lmsys.org"))

    @classmethod
    def _is_result_bridge_candidate(cls, event: Dict[str, Any]) -> bool:
        if not cls.is_arena_candidate(event):
            return False
        url = str((event or {}).get("url") or "").strip().lower()
        return any(
            token in url
            for token in (
                "/nextjs-api/stream/",
                "nextjs-api/stream",
                "create-evaluation",
                "post-to-evaluation",
                "stream/create",
                "stream/post",
            )
        )

    @classmethod
    def _is_reveal_snapshot_candidate(cls, event: Dict[str, Any]) -> bool:
        if not cls.is_arena_candidate(event):
            return False
        url = str((event or {}).get("url") or "").strip().lower()
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

    def _claim_result_bridge_slot(self, session_id: str) -> bool:
        key = str(session_id or "unknown")
        with self._result_bridge_lock:
            active = int(self._result_bridge_active_by_session.get(key, 0) or 0)
            if active >= self.RESULT_BRIDGE_MAX_ACTIVE_PER_SESSION:
                logger.debug_throttled(
                    f"global_net.result_bridge_busy.{key}",
                    f"[GlobalNet] Arena 结果桥接忙，跳过候选响应: {key}, active={active}",
                    interval_sec=10.0,
                )
                return False
            self._result_bridge_active_by_session[key] = active + 1
            return True

    def _release_result_bridge_slot(self, session_id: str) -> None:
        key = str(session_id or "unknown")
        with self._result_bridge_lock:
            active = int(self._result_bridge_active_by_session.get(key, 0) or 0)
            if active <= 1:
                self._result_bridge_active_by_session.pop(key, None)
            else:
                self._result_bridge_active_by_session[key] = active - 1

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

        session_id = str(getattr(session, "id", "") or "unknown")
        if not self._claim_result_bridge_slot(session_id):
            return

        try:
            thread = threading.Thread(
                target=self._dispatch_result_bridge,
                args=(session, response, dict(event or {}), stop_event),
                daemon=True,
                name=f"global-net-arena-{session_id}",
            )
            thread.start()
        except Exception as e:
            self._release_result_bridge_slot(session_id)
            logger.debug(f"[GlobalNet] Arena 结果桥接线程启动失败（忽略）: {e}")

    def _dispatch_result_bridge(
        self,
        session: TabSession,
        response: Any,
        event: Dict[str, Any],
        stop_event: threading.Event,
    ) -> None:
        session_id = str(getattr(session, "id", "") or "unknown")
        try:
            from app.core.tab_pool_parts.network import _GlobalNetworkInterceptionManager
            raw_body, raw_body_source = _GlobalNetworkInterceptionManager.read_response_body(response, stop_event)
            if not raw_body:
                return

            post_data = _GlobalNetworkInterceptionManager.extract_request_post_data(response)
            self._result_event_handler(
                {
                    "event": event,
                    "raw_body": raw_body,
                    "raw_body_source": raw_body_source,
                    "request_post_data": post_data,
                    "parse_result": {"done": True},
                    "parser_id": "lmarena_global",
                    "session_id": getattr(session, "id", ""),
                    "session": session,
                }
            )
        except Exception as e:
            logger.debug(f"[GlobalNet] Arena 结果事件桥接失败（忽略）: {e}")
        finally:
            self._release_result_bridge_slot(session_id)

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

        try:
            from app.core.workflow.arena_direct_guard import is_arena_direct_preset
            tab = getattr(session, "tab", None)
            current_url = getattr(tab, "url", "") if tab else ""
            if current_url and is_explicit_arena_direct_url(current_url):
                return
            preset_name = str(getattr(session, "preset_name", "") or getattr(session, "preset", "") or "")
            if preset_name and is_arena_direct_preset("arena.ai", preset_name):
                return
        except Exception:
            pass

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
                session = self._get_session(session_id) if callable(self._get_session) else None
                tab = getattr(session, "tab", None) if session is not None else None
                if tab is None:
                    return
                try:
                    snapshot = tab.run_js(_ARENA_STORE_SNAPSHOT_JS)
                except Exception as e:
                    if not is_page_refresh_error(e):
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

                is_direct = bool(
                    snapshot.get("is_direct")
                    or str(snapshot.get("mode") or "").strip().lower() == "direct"
                    or is_explicit_arena_direct_url(snapshot.get("url"))
                )
                if is_direct:
                    return

                snapshot["session_id"] = session_id
                model_a = str(snapshot.get("model_a") or "").strip()
                model_b = str(snapshot.get("model_b") or "").strip()
                response_a = str(snapshot.get("response_a") or "")
                response_b = str(snapshot.get("response_b") or "")
                message_id_a = str(snapshot.get("message_id_a") or "").strip()
                message_id_b = str(snapshot.get("message_id_b") or "").strip()

                if not (model_a and model_b and message_id_a and message_id_b and message_id_a != message_id_b):
                    time.sleep(self.ARENA_REVEAL_POLL_INTERVAL_SEC)
                    continue

                signature = (
                    f"{snapshot.get('conversation_id')}|{message_id_a}|"
                    f"{message_id_b}|{model_a}|{model_b}"
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
                    if should_log:
                        logger.debug(
                            "[GlobalNet] Arena 翻牌快照更新: "
                            f"reason={reason}, model_a={model_a}, model_b={model_b}, "
                            f"a={len(response_a)}, b={len(response_b)}"
                        )

                if response_a and response_b:
                    emit_arena_snapshot_event(snapshot)
                    return

                time.sleep(self.ARENA_REVEAL_POLL_INTERVAL_SEC)
        finally:
            with self._arena_reveal_lock:
                current = self._arena_reveal_pollers.get(session_id)
                if current is threading.current_thread():
                    self._arena_reveal_pollers.pop(session_id, None)

    def __call__(
        self,
        session: TabSession,
        response: Any,
        event: Dict[str, Any],
        stop_event: threading.Event,
    ) -> None:
        """作为全局 response listener 的回调入口。"""
        self._dispatch_result_bridge_async(session, response, event, stop_event)
        self._start_arena_reveal_poll(session, event, stop_event, "network-event")


def register_arena_tab_listener(manager: Any) -> Optional[Callable[[], None]]:
    """向全局网络管理器挂载 Arena 翻牌与结果监听器。"""
    if manager is None or not hasattr(manager, "register_response_listener"):
        return None

    get_session_fn = getattr(manager, "_get_session", None)
    is_shutdown_fn = getattr(manager, "_is_shutdown", None)
    listener = ArenaTabListener(get_session_fn=get_session_fn, is_shutdown_fn=is_shutdown_fn)

    return manager.register_response_listener(
        ArenaTabListener.is_arena_candidate,
        listener,
    )