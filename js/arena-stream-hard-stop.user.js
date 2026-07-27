// ==UserScript==
// @name         Arena.ai Native Stop Repair
// @namespace    local.codex.arena-hard-stop
// @version      2.10.1
// @description  Repairs Arena.ai's lost active stream state and hard-aborts active stream fetches.
// @match        https://arena.ai/*
// @run-at       document-start
// @inject-into  page
// @noframes
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  const VERSION = '2.10.1';
  const ENABLE_STORE_CONTROLLER_REPAIR = false;
  const ENABLE_STALE_CONTROLLER_CLEANUP = true;
  const ENABLE_SYNTHETIC_CONTROLLER_REPAIR = false;
  const ENABLE_FAILED_RERUN_HEAL = false;
  const REPAIR_INTERVAL_MS = 1000;
  const LOG_PREFIX = '[Arena Hard Stop]';
  const ID_RE = /\b019[a-z0-9-]{20,}\b/ig;
  const STREAM_RE = /\/nextjs-api\/stream\/(create-evaluation|post-to-evaluation|retry-evaluation-session-message|rerun|resample|resume-webdev|resume-video-workflow|skip-direct-battle)\b/;
  const STOP_RE = /\/nextjs-api\/stream\/stop\/([^/?#]+)\/messages\/([^/?#]+)/;
  const RETRY_RE = /\/nextjs-api\/stream\/retry-evaluation-session-message\/([^/?#]+)\/messages\/([^/?#]+)/;
  const POST_RE = /\/nextjs-api\/stream\/post-to-evaluation\/([^/?#]+)/;
  const RERUN_RE = /\/nextjs-api\/stream\/rerun\/([^/?#]+)/;
  const RESAMPLE_RE = /\/nextjs-api\/stream\/resample\/([^/?#]+)/;
  const RESUME_VIDEO_RE = /\/nextjs-api\/stream\/resume-video-workflow\/([^/?#]+)/;
  const SKIP_BATTLE_RE = /\/nextjs-api\/stream\/skip-direct-battle\/([^/?#]+)/;

  const NativeFetch = window.fetch;
  const nativeFetch = NativeFetch.bind(window);
  const NativeAbortController = window.AbortController;
  const streamTextDecoder = typeof TextDecoder !== 'undefined' ? new TextDecoder() : null;
  const signalControllers = new WeakMap();
  const liveStreams = new Map();
  const recentStopResults = [];
  const recentRepairs = [];
  const recentBodyStops = [];
  const recentStreamTraces = [];
  const recentObservedStopRequests = [];
  const recentBlockedStreamRequests = [];
  const stoppedMessageGuards = new Map();
  let syntheticController = null;
  let syntheticControllerKey = '';
  let seq = 0;
  let overlayBtn = null;
  let lastStopAt = 0;
  let toastEl = null;
  let repairTimer = 0;
  let installed = false;
  let staleControllerSince = 0;

  const existingInstance = window.__arenaHardStop;
  if (existingInstance) {
    if (existingInstance.version === VERSION) {
      return;
    }
    if (typeof existingInstance.uninstall === 'function') {
      try {
        existingInstance.uninstall({ quiet: true });
      } catch (err) {
        console.warn(LOG_PREFIX, 'previous instance uninstall failed', err);
      }
    } else {
      console.warn(LOG_PREFIX, 'existing instance without uninstall detected, skipping install');
      return;
    }
  }

  function installAbortControllerTracker() {
    if (!NativeAbortController || NativeAbortController.__arenaHardStopTracked) return;

    function TrackedAbortController() {
      const controller = new NativeAbortController();
      try {
        signalControllers.set(controller.signal, controller);
      } catch (_) {}
      return controller;
    }

    try {
      TrackedAbortController.prototype = NativeAbortController.prototype;
      Object.defineProperty(TrackedAbortController, 'name', { value: 'AbortController' });
      Object.defineProperty(TrackedAbortController, '__arenaHardStopTracked', { value: true });
      window.AbortController = TrackedAbortController;
      log('AbortController tracker installed');
    } catch (err) {
      warn('AbortController tracker install failed', err);
    }
  }

  installAbortControllerTracker();

  function log(...args) {
    console.log(LOG_PREFIX, ...args);
  }

  function warn(...args) {
    console.warn(LOG_PREFIX, ...args);
  }

  function toAbsoluteUrl(input) {
    try {
      if (typeof input === 'string') return new URL(input, location.href).href;
      if (input && typeof input.url === 'string') return new URL(input.url, location.href).href;
    } catch (_) {
      // Ignore malformed non-URL request objects.
    }
    return '';
  }

  function extractArenaIds(text) {
    const out = [];
    const seen = new Set();
    const source = String(text || '');
    ID_RE.lastIndex = 0;
    let match = ID_RE.exec(source);
    while (match) {
      const id = String(match[0] || '').trim();
      if (id && !seen.has(id)) {
        seen.add(id);
        out.push(id);
      }
      match = ID_RE.exec(source);
    }
    return out;
  }

  function bodyTextForTrace(body) {
    if (body == null) return { type: '', text: '' };
    if (typeof body === 'string') return { type: 'string', text: body };
    if (body instanceof URLSearchParams) return { type: 'URLSearchParams', text: body.toString() };
    if (typeof FormData !== 'undefined' && body instanceof FormData) {
      const parts = [];
      for (const [key, value] of body.entries()) {
        parts.push(`${key}=${typeof value === 'string' ? value : `[${value && value.name ? value.name : 'file'}]`}`);
      }
      return { type: 'FormData', text: parts.join('&') };
    }
    if (typeof Blob !== 'undefined' && body instanceof Blob) return { type: 'Blob', text: '' };
    if (typeof ReadableStream !== 'undefined' && body instanceof ReadableStream) return { type: 'ReadableStream', text: '' };
    try {
      return { type: Object.prototype.toString.call(body), text: String(body) };
    } catch (_) {
      return { type: typeof body, text: '' };
    }
  }

  function makeStreamTrace(input, init, meta) {
    const url = toAbsoluteUrl(input);
    const bodyInfo = bodyTextForTrace(init && Object.prototype.hasOwnProperty.call(init, 'body') ? init.body : null);
    const bodyPreview = bodyInfo.text ? bodyInfo.text.slice(0, 1600) : '';
    let bodyFields = null;
    if (bodyInfo.text && bodyInfo.type === 'string') {
      try {
        const parsed = JSON.parse(bodyInfo.text);
        if (parsed && typeof parsed === 'object') {
          bodyFields = {
            id: parsed.id || '',
            mode: parsed.mode || '',
            userMessageId: parsed.userMessageId || '',
            modelAMessageId: parsed.modelAMessageId || '',
            modelBMessageId: parsed.modelBMessageId || '',
            messageIds: Array.isArray(parsed.messageIds) ? parsed.messageIds.slice() : [],
          };
        }
      } catch (_) {}
    }
    return {
      at: new Date().toISOString(),
      path: meta && meta.path || '',
      kind: meta && meta.kind || '',
      sessionId: meta && meta.sessionId || '',
      parentMessageIdFromUrl: meta && meta.parentMessageId || '',
      urlIds: extractArenaIds(url),
      bodyType: bodyInfo.type,
      bodyIds: extractArenaIds(bodyInfo.text),
      bodyFields,
      bodyPreview,
    };
  }

  function rememberStreamTrace(record) {
    if (!record || !record.trace) return;
    const entry = {
      streamId: record.id,
      ...record.trace,
    };
    const existingIndex = recentStreamTraces.findIndex(item => item && item.streamId === record.id);
    if (existingIndex >= 0) {
      recentStreamTraces[existingIndex] = entry;
    } else {
      recentStreamTraces.push(entry);
    }
    while (recentStreamTraces.length > 20) recentStreamTraces.shift();
  }

  function rememberChunkIds(record, chunk) {
    if (!record || !record.trace || !streamTextDecoder || !chunk) return;
    let text = '';
    try {
      text = streamTextDecoder.decode(chunk, { stream: true });
    } catch (_) {
      return;
    }
    if (!text) return;

    if (!Array.isArray(record.trace.chunkIds)) record.trace.chunkIds = [];
    if (!record.trace.chunkPreview) record.trace.chunkPreview = '';
    for (const id of extractArenaIds(text)) addUnique(record.trace.chunkIds, id);
    if (record.trace.chunkPreview.length < 1600) {
      record.trace.chunkPreview = (record.trace.chunkPreview + text).slice(0, 1600);
    }
    rememberStreamTrace(record);
  }

  function rememberObservedStopRequest(input, init, source) {
    const url = toAbsoluteUrl(input);
    if (!url) return;
    let parsed = null;
    try {
      parsed = new URL(url, location.href);
    } catch (_) {
      return;
    }
    const match = parsed.pathname.match(STOP_RE);
    if (!match) return;
    recentObservedStopRequests.push({
      at: new Date().toISOString(),
      source: source || 'fetch',
      method: inputMethod(input, init),
      path: parsed.pathname,
      sessionId: decodeURIComponent(match[1]),
      messageId: decodeURIComponent(match[2]),
    });
    while (recentObservedStopRequests.length > 20) recentObservedStopRequests.shift();
  }

  function rememberBlockedStreamRequest(blocked) {
    recentBlockedStreamRequests.push({
      at: new Date().toISOString(),
      ...blocked,
    });
    while (recentBlockedStreamRequests.length > 20) recentBlockedStreamRequests.shift();
  }

  function shouldBlockMalformedStreamRequest(input, init) {
    const url = toAbsoluteUrl(input);
    if (!url) return null;
    let parsedUrl = null;
    try {
      parsedUrl = new URL(url, location.href);
    } catch (_) {
      return null;
    }
    if (!RERUN_RE.test(parsedUrl.pathname)) return null;

    const bodyInfo = bodyTextForTrace(init && Object.prototype.hasOwnProperty.call(init, 'body') ? init.body : null);
    if (!bodyInfo.text || bodyInfo.type !== 'string') return null;
    try {
      const body = JSON.parse(bodyInfo.text);
      if (body && Array.isArray(body.messageIds) && body.messageIds.length === 0) {
        return {
          ok: false,
          reason: 'empty_rerun_messageIds',
          method: inputMethod(input, init),
          path: parsedUrl.pathname,
          bodyPreview: bodyInfo.text.slice(0, 400),
        };
      }
    } catch (_) {}
    return null;
  }

  function inputMethod(input, init) {
    return String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
  }

  function abortWith(controller, reason) {
    if (!controller || typeof controller.abort !== 'function') return;
    try {
      if (controller.signal && controller.signal.aborted) return;
      controller.abort(reason || 'Arena hard stop');
    } catch (_) {
      try {
        controller.abort();
      } catch (err) {
        warn('abort failed', err);
      }
    }
  }

  function makeAbortError() {
    try {
      return new DOMException('The operation was aborted.', 'AbortError');
    } catch (_) {
      const err = new Error('The operation was aborted.');
      err.name = 'AbortError';
      return err;
    }
  }

  function makeAbortController() {
    if (!NativeAbortController) return null;
    const controller = new NativeAbortController();
    try {
      signalControllers.set(controller.signal, controller);
    } catch (_) {}
    return controller;
  }

  function mergeSignals(originalSignal, localSignal) {
    if (!originalSignal) return localSignal || null;
    if (!localSignal) return originalSignal;

    if (window.AbortSignal && typeof window.AbortSignal.any === 'function') {
      try {
        return window.AbortSignal.any([originalSignal, localSignal]);
      } catch (_) {
        // Fall through to a tiny compatibility merger.
      }
    }

    const mergedController = makeAbortController();
    if (!mergedController) return localSignal;

    const forwardAbort = signal => {
      if (!signal || mergedController.signal.aborted) return;
      try {
        mergedController.abort('reason' in signal ? signal.reason : undefined);
      } catch (_) {
        mergedController.abort();
      }
    };

    if (originalSignal.aborted) {
      forwardAbort(originalSignal);
    } else if (typeof originalSignal.addEventListener === 'function') {
      originalSignal.addEventListener('abort', () => forwardAbort(originalSignal), { once: true });
    }

    if (localSignal.aborted) {
      forwardAbort(localSignal);
    } else if (typeof localSignal.addEventListener === 'function') {
      localSignal.addEventListener('abort', () => forwardAbort(localSignal), { once: true });
    }

    return mergedController.signal;
  }

  function parseStreamUrl(url) {
    const u = new URL(url, location.href);
    const path = u.pathname;
    const out = {
      url,
      path,
      kind: '',
      sessionId: '',
      parentMessageId: '',
      stopUrl: '',
      canCallStopApi: false,
    };

    let m = path.match(RETRY_RE);
    if (m) {
      out.kind = 'retry';
      out.sessionId = decodeURIComponent(m[1]);
      out.parentMessageId = decodeURIComponent(m[2]);
      out.stopUrl = `/nextjs-api/stream/stop/${encodeURIComponent(out.sessionId)}/messages/${encodeURIComponent(out.parentMessageId)}`;
      out.canCallStopApi = true;
      return out;
    }

    m = path.match(POST_RE) || path.match(RERUN_RE) || path.match(RESAMPLE_RE) || path.match(RESUME_VIDEO_RE) || path.match(SKIP_BATTLE_RE);
    if (m) {
      out.kind = path.split('/').pop() || 'stream';
      out.sessionId = decodeURIComponent(m[1]);
      return out;
    }

    if (path.includes('/create-evaluation')) {
      out.kind = 'create';
      out.sessionId = getSessionIdFromLocation();
    }
    return out;
  }

  function getSessionIdFromLocation() {
    const m = location.pathname.match(/\/c\/([^/?#]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function findReactFiber(el) {
    if (!el) return null;
    const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
    return key ? el[key] : null;
  }

  function looksLikeArenaStore(value) {
    if (!value || typeof value !== 'object') return false;
    if (typeof value.getState !== 'function') return false;
    const state = safeCall(() => value.getState());
    return !!(state && typeof state === 'object' && Array.isArray(state.messages) && typeof state.id === 'string');
  }

  function findArenaStoreIn(value, depth, seen) {
    if (!value || typeof value !== 'object' || depth < 0 || seen.has(value)) return null;
    seen.add(value);
    if (looksLikeArenaStore(value)) return value;

    let keys = [];
    try {
      keys = Object.keys(value);
    } catch (_) {
      return null;
    }

    for (const key of keys.slice(0, 80)) {
      if (key === '_owner' || key === 'return' || key === 'child' || key === 'sibling' || key === 'alternate') continue;
      const found = findArenaStoreIn(value[key], depth - 1, seen);
      if (found) return found;
    }
    return null;
  }

  // 极短 TTL 的 store 微缓存。
  // repairActiveStreamState 每秒一次，但一次 tick 内会通过
  // updateOverlay / 直接调用 / cleanupStaleStreamController 触发 3 次
  // findStoreFromFiber，每次都要做 4 个 querySelector + 最多 80 层 fiber 向上遍历
  // + 每层 5 个 bucket 的深度 4 递归扫描，是页面里最贵的常驻开销之一。
  // 这里只在 200ms 内复用结果（远短于 1s 的巡检周期），且复用前仍用
  // looksLikeArenaStore 完整校验，保证拿到的仍是合法 store，不改变判定语义。
  const STORE_CACHE_TTL_MS = 200;
  let cachedArenaStore = null;
  let cachedArenaStoreAt = 0;
  let cachedArenaStoreId = '';

  // 复用缓存前的三重校验：TTL 未过期 + 仍满足 looksLikeArenaStore +
  // state.id 没变（会话/路由切换会换 id，换了就重新搜索，宁可多扫一次也不拿旧 store）。
  function reusableCachedStore() {
    if (!cachedArenaStore) return null;
    if ((Date.now() - cachedArenaStoreAt) >= STORE_CACHE_TTL_MS) return null;
    if (!looksLikeArenaStore(cachedArenaStore)) return null;
    const state = safeCall(() => cachedArenaStore.getState());
    const stateId = state && typeof state.id === 'string' ? state.id : '';
    if (stateId !== cachedArenaStoreId) return null;
    const locationId = getSessionIdFromLocation();
    if (locationId && stateId && locationId !== stateId) return null;
    return cachedArenaStore;
  }

  function findStoreFromFiber() {
    const reusable = reusableCachedStore();
    if (reusable) return reusable;
    cachedArenaStore = null;
    cachedArenaStoreId = '';

    const roots = [
      document.querySelector('button[aria-label="Stop generation"]'),
      document.querySelector('button[aria-label="Send message"][type="submit"]'),
      document.querySelector('form'),
      document.querySelector('main'),
    ].filter(Boolean);

    for (const el of roots) {
      let cur = findReactFiber(el);
      for (let depth = 0; cur && depth < 80; depth += 1, cur = cur.return) {
        const buckets = [
          cur.memoizedProps,
          cur.pendingProps,
          cur.memoizedState,
          cur.dependencies,
          cur.stateNode,
        ];
        for (const bucket of buckets) {
          const found = findArenaStoreIn(bucket, 4, new WeakSet());
          if (found) {
            const foundState = safeCall(() => found.getState());
            cachedArenaStore = found;
            cachedArenaStoreAt = Date.now();
            cachedArenaStoreId = foundState && typeof foundState.id === 'string' ? foundState.id : '';
            return found;
          }
        }
      }
    }
    return null;
  }

  function safeCall(fn) {
    try {
      return fn();
    } catch (_) {
      return null;
    }
  }

  function pendingMessageInfo() {
    const store = findStoreFromFiber();
    const state = store && safeCall(() => store.getState());
    if (!state || !Array.isArray(state.messages)) return null;
    const pending = state.messages.filter(msg => msg && msg.role === 'assistant' && msg.status === 'pending');
    const parentIds = [];
    for (const msg of pending) {
      if (Array.isArray(msg.parentMessageIds)) {
        for (const id of msg.parentMessageIds) {
          if (id && !parentIds.includes(id)) parentIds.push(id);
        }
      }
    }
    return {
      store,
      state,
      sessionId: state.id || getSessionIdFromLocation(),
      parentMessageId: parentIds[0] || '',
      parentMessageIds: parentIds,
      pendingIds: pending.map(msg => msg.id).filter(Boolean),
      messageIds: state.messages.map(msg => msg && msg.id).filter(Boolean),
      pendingMessages: pending,
    };
  }

  function updateMessageLocal(info, patch) {
    if (!info || !info.state || typeof info.state.updateMessage !== 'function') return false;
    try {
      info.state.updateMessage(patch);
      return true;
    } catch (err) {
      warn('updateMessage failed', patch && patch.id, err);
      return false;
    }
  }

  function updateSessionLocal(info, patch) {
    if (!info || !info.state || typeof info.state.update !== 'function') return false;
    try {
      info.state.update(patch);
      return true;
    } catch (err) {
      warn('session update failed', err);
      return false;
    }
  }

  function applyStoppedStateLocal(info) {
    if (!info || !info.state) return null;

    const stopped = {
      assistantIds: (info.pendingIds || []).slice(),
      parentMessageId: info.parentMessageId || '',
      showStoppedUserPrompt: true,
    };

    for (const id of stopped.assistantIds) {
      updateMessageLocal(info, { id, status: 'stopped' });
    }
    if (stopped.parentMessageId) {
      updateMessageLocal(info, { id: stopped.parentMessageId, status: 'stopped' });
    }
    updateSessionLocal(info, { showStoppedUserPrompt: true });
    rememberStoppedMessageGuard(info.pendingMessages || [], stopped.parentMessageId, 'hard-stop');
    return stopped;
  }

  function applyStopFailureLocal(info, stoppedState, errorMessage) {
    if (!info || !info.state || !stoppedState) return;

    for (const id of stoppedState.assistantIds || []) {
      updateMessageLocal(info, {
        id,
        status: 'failed',
        failureReason: errorMessage || 'Failed to stop generation',
      });
    }
    if (stoppedState.parentMessageId) {
      updateMessageLocal(info, { id: stoppedState.parentMessageId, status: 'success' });
    }
    updateSessionLocal(info, { showStoppedUserPrompt: false });
  }

  // 只清理已过期（超过 30s TTL）的条目，不依赖 messagesById。
  // 原来唯一的清理入口 cleanupStoppedMessageGuards 被 healStoppedRerunFailure
  // 开头的 ENABLE_FAILED_RERUN_HEAL(=false) 挡住，永远执行不到，
  // 导致每次"硬停止"写入的条目只增不删。这里补一个无副作用的过期清扫。
  function sweepExpiredStoppedGuards() {
    if (!stoppedMessageGuards.size) return;
    const now = Date.now();
    for (const [id, guard] of stoppedMessageGuards) {
      if (!guard || guard.expiresAt <= now) stoppedMessageGuards.delete(id);
    }
  }

  function cleanupStoppedMessageGuards(messagesById) {
    const now = Date.now();
    for (const [id, guard] of stoppedMessageGuards) {
      if (!guard || guard.expiresAt <= now) {
        stoppedMessageGuards.delete(id);
        continue;
      }
      const message = messagesById && messagesById.get(id);
      if (!message && guard.role === 'assistant') stoppedMessageGuards.delete(id);
    }
  }

  function rememberStoppedMessageGuard(assistantMessages, parentMessageId, source) {
    if (!assistantMessages || !assistantMessages.length) return;
    const expiresAt = Date.now() + 30_000;
    for (const message of assistantMessages) {
      if (!message || !message.id) continue;
      stoppedMessageGuards.set(message.id, {
        role: 'assistant',
        parentMessageId: parentMessageId || '',
        source: source || 'stop',
        expiresAt,
      });
    }
    if (parentMessageId) {
      stoppedMessageGuards.set(parentMessageId, {
        role: 'parent',
        source: source || 'stop',
        expiresAt,
      });
    }
  }

  function captureStoppedGuardFromState(state) {
    if (!state || !Array.isArray(state.messages)) return;
    const messagesById = new Map(state.messages.map(message => [message.id, message]));
    const stoppedAssistants = (state.lastMessageIds || [])
      .map(id => messagesById.get(id))
      .filter(message => message && message.role === 'assistant' && message.status === 'stopped');
    if (!stoppedAssistants.length) return;
    const parentMessageId = stoppedAssistants[0] && Array.isArray(stoppedAssistants[0].parentMessageIds)
      ? stoppedAssistants[0].parentMessageIds[0] || ''
      : '';
    rememberStoppedMessageGuard(stoppedAssistants, parentMessageId, 'native-stop');
  }

  function shouldHealStoppedMessageFailure(message) {
    if (!message || message.status !== 'failed') return false;
    const reason = String(message.failureReason || '').toLowerCase();
    return reason.includes('rerun request failed')
      || reason.includes('rerun stream failed')
      || reason.includes('messages must not be empty')
      || reason.includes('failed to stop generation');
  }

  function healStoppedRerunFailure(store, state) {
    if (!ENABLE_FAILED_RERUN_HEAL) return false;
    if (!store || !state || !Array.isArray(state.messages)) return false;
    if (state.activeStreamController || state.canStopActiveStream) return false;

    captureStoppedGuardFromState(state);

    const messagesById = new Map(state.messages.map(message => [message.id, message]));
    cleanupStoppedMessageGuards(messagesById);

    const guardedAssistants = Array.from(stoppedMessageGuards.entries())
      .filter(([, guard]) => guard && guard.role === 'assistant')
      .map(([id, guard]) => ({ message: messagesById.get(id), guard }))
      .filter(entry => entry.message);

    if (!guardedAssistants.length) return false;
    if (!guardedAssistants.some(entry => shouldHealStoppedMessageFailure(entry.message))) return false;

    const assistantMessages = guardedAssistants.map(entry => entry.message);
    const parentMessageId = guardedAssistants[0].guard.parentMessageId || '';
    const info = {
      store,
      state,
      pendingIds: assistantMessages.map(message => message.id),
      pendingMessages: assistantMessages,
      parentMessageId,
    };

    for (const message of assistantMessages) {
      updateMessageLocal(info, {
        id: message.id,
        status: 'stopped',
        failureReason: null,
        traceId: void 0,
      });
    }
    if (parentMessageId) {
      updateMessageLocal(info, {
        id: parentMessageId,
        status: 'stopped',
        failureReason: null,
        traceId: void 0,
      });
    }
    updateSessionLocal(info, {
      showStoppedUserPrompt: true,
      lastMessageIds: assistantMessages.map(message => message.id),
    });
    rememberRepair({
      at: new Date().toISOString(),
      ok: true,
      kind: 'heal-stopped-rerun',
      repairedAssistantIds: assistantMessages.map(message => message.id),
    });
    return true;
  }

  function addUnique(list, value) {
    if (!value) return;
    const text = String(value).trim();
    if (!text || list.includes(text)) return;
    list.push(text);
  }

  function findLikelyMessageIds() {
    const ids = [];
    const candidates = Array.from(document.querySelectorAll('[id], [data-message-id], [data-testid], main ol *'))
      .map(el => [
        el.getAttribute('data-message-id'),
        el.id,
        el.getAttribute('data-testid'),
      ])
      .flat()
      .filter(Boolean)
      .map(String)
      .filter(v => /^019[a-z0-9-]{20,}$/i.test(v));

    for (const id of candidates) addUnique(ids, id);
    return ids;
  }

  function findLikelyParentMessageId() {
    const info = pendingMessageInfo();
    if (info && info.parentMessageId) return info.parentMessageId;
    const candidates = findLikelyMessageIds();
    return candidates[candidates.length - 1] || '';
  }

  function collectStopMessageIds(record, info) {
    return collectStopCandidates(record, info).map(candidate => candidate.id);
  }

  function collectStopCandidates(record, info) {
    const candidates = [];
    const seen = new Set();

    function addCandidate(id, source, confidence) {
      const text = String(id || '').trim();
      if (!text || seen.has(text)) return;
      seen.add(text);
      candidates.push({
        id: text,
        source: source || 'unknown',
        confidence: confidence || 'low',
      });
    }

    if (info) {
      for (const id of info.parentMessageIds || []) {
        addCandidate(id, 'store-pending-parent', 'high');
      }
      addCandidate(info.parentMessageId, 'store-pending-parent', 'high');
    }

    if (!candidates.length) {
      addCandidate(record && record.meta && record.meta.parentMessageId, 'stream-url-message-fallback', 'medium');
    }

    if (!candidates.length && record && record.trace && record.trace.bodyFields) {
      for (const id of record.trace.bodyFields.messageIds || []) {
        addCandidate(id, 'body-messageIds-fallback', 'medium');
      }
      addCandidate(record.trace.bodyFields.modelAMessageId, 'body-model-a-message-fallback', 'medium');
      addCandidate(record.trace.bodyFields.modelBMessageId, 'body-model-b-message-fallback', 'medium');
    }

    if (info && !candidates.length) {
      for (const id of info.pendingIds || []) {
        addCandidate(id, 'store-pending-assistant-fallback', 'low');
      }
    }
    return candidates;
  }

  function trackStream(input, init) {
    const url = toAbsoluteUrl(input);
    if (!url || !STREAM_RE.test(new URL(url, location.href).pathname)) return null;

    const meta = parseStreamUrl(url);
    const id = `${Date.now()}-${++seq}`;
    const record = {
      id,
      meta,
      method: inputMethod(input, init),
      startedAt: Date.now(),
      done: false,
      input,
      init,
      abort: null,
      abortBody: null,
      externalController: null,
      repairController: null,
      repairApplied: false,
      ownsAbortController: false,
      stopRequestedAt: 0,
      trace: makeStreamTrace(input, init, meta),
    };
    rememberStreamTrace(record);

    const originalSignal = (init && init.signal) || (input && input.signal);
    const localController = makeAbortController();
    const signal = mergeSignals(originalSignal, localController && localController.signal);

    if (localController) {
      record.externalController = localController;
      record.repairController = localController;
      record.ownsAbortController = true;
      record.abort = () => abortWith(localController);
      init = { ...(init || {}), signal };
    } else if (originalSignal) {
      const controller = signalControllers.get(originalSignal);
      if (controller && typeof controller.abort === 'function') {
        record.externalController = controller;
        record.repairController = controller;
        record.abort = () => abortWith(controller);
      } else {
        record.abort = () => warn('Request has an external signal but its controller was not captured.', meta.path);
      }
    }

    if (signal) {
      if (signal.aborted) {
        record.done = true;
      } else if (typeof signal.addEventListener === 'function') {
        signal.addEventListener('abort', () => {
          record.signalAbortedAt = Date.now();
          if (!record.ownsAbortController || !record.stopRequestedAt) {
            finishStream(record.id, 'signal-aborted');
            return;
          }
          setTimeout(() => {
            const current = liveStreams.get(record.id);
            if (current === record && !record.done) {
              finishStream(record.id, 'signal-aborted-timeout');
            }
          }, 4000);
        }, { once: true });
      }
    }

    liveStreams.set(id, record);
    log('stream started', meta.path, meta);
    scheduleStateRepair();

    return { record, init };
  }

  window.fetch = function arenaHardStopFetch(input, init) {
    const blocked = shouldBlockMalformedStreamRequest(input, init);
    if (blocked) {
      rememberBlockedStreamRequest(blocked);
      warn('blocked malformed stream request', blocked);
      return Promise.resolve(new Response(JSON.stringify({
        error: blocked.reason,
        message: 'Blocked malformed Arena rerun request before network send.',
      }), {
        status: 422,
        statusText: 'Blocked malformed Arena rerun',
        headers: { 'content-type': 'application/json' },
      }));
    }

    rememberObservedStopRequest(input, init, 'page-fetch');
    const tracked = trackStream(input, init);
    if (!tracked) return nativeFetch(input, init);

    const { record } = tracked;
    return nativeFetch(input, tracked.init)
      .then(response => {
        return wrapStreamResponse(record, response);
      })
      .catch(err => {
        finishStream(record.id, err && err.name === 'AbortError' ? 'aborted' : 'failed');
        throw err;
      });
  };

  function wrapStreamResponse(record, response) {
    if (!response || !response.body || typeof response.body.getReader !== 'function' || typeof ReadableStream !== 'function') {
      watchResponseBody(record, response && safeCall(() => response.clone())).catch(() => {});
      return response;
    }

    const reader = response.body.getReader();
    let controllerRef = null;
    let settled = false;

    function settle(reason) {
      if (settled) return false;
      settled = true;
      finishStream(record.id, reason);
      return true;
    }

    record.abortBody = reason => {
      if (!settle('body-hard-stopped')) return;
      rememberBodyStop({
        at: new Date().toISOString(),
        streamId: record.id,
        path: record.meta.path,
        reason: String(reason || 'hard-stop'),
      });
      try {
        reader.cancel(reason || 'Arena hard stop');
      } catch (_) {}
      if (controllerRef) {
        try {
          controllerRef.error(makeAbortError());
        } catch (_) {}
      }
    };

    const body = new ReadableStream({
      start(controller) {
        controllerRef = controller;
        (async () => {
          try {
            while (!settled) {
              const { done, value } = await reader.read();
              if (done) {
                if (settle('finished')) {
                  try {
                    controller.close();
                  } catch (_) {}
                }
                break;
              }
              record.lastChunkAt = Date.now();
              rememberChunkIds(record, value);
              controller.enqueue(value);
            }
          } catch (err) {
            if (settle(err && err.name === 'AbortError' ? 'aborted' : 'body-error')) {
              try {
                controller.error(err);
              } catch (_) {}
            }
          } finally {
            try {
              reader.releaseLock();
            } catch (_) {}
          }
        })();
      },
      cancel(reason) {
        if (settle('client-cancelled')) {
          try {
            reader.cancel(reason);
          } catch (_) {}
        }
      },
    });

    return new Response(body, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  }

  async function watchResponseBody(record, response) {
    if (!response || !response.body || typeof response.body.getReader !== 'function') {
      finishStream(record.id, 'no-body');
      return;
    }
    const reader = response.body.getReader();
    try {
      while (true) {
        const { done } = await reader.read();
        if (done) break;
        record.lastChunkAt = Date.now();
      }
      finishStream(record.id, 'finished');
    } catch (_) {
      finishStream(record.id, 'body-error');
    } finally {
      try {
        reader.releaseLock();
      } catch (_) {}
    }
  }

  function finishStream(id, reason) {
    const record = liveStreams.get(id);
    if (!record) return;
    record.done = true;
    record.finishedAt = Date.now();
    record.finishReason = reason;
    setTimeout(() => {
      liveStreams.delete(id);
      scheduleStateRepair();
    }, 300);
    cleanupRepairController(record);
    scheduleStateRepair();
  }

  function activeRecords() {
    const now = Date.now();
    for (const [id, record] of liveStreams) {
      if (record.done) continue;
      if (now - record.startedAt > 10 * 60 * 1000) {
        liveStreams.delete(id);
      }
    }
    return Array.from(liveStreams.values()).filter(record => !record.done);
  }

  function isVisibleElement(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }

  function hasNativeStopButton() {
    const btn = document.querySelector('button[aria-label="Stop generation"]:not([data-arena-hard-stop-overlay="true"])');
    return isVisibleElement(btn);
  }

  function newestActiveRecord() {
    const records = activeRecords();
    return records[records.length - 1] || null;
  }

  function rememberRepair(result) {
    recentRepairs.push(result);
    while (recentRepairs.length > 10) recentRepairs.shift();
  }

  function rememberBodyStop(result) {
    recentBodyStops.push(result);
    while (recentBodyStops.length > 10) recentBodyStops.shift();
  }

  function makeSyntheticSignal() {
    return {
      aborted: false,
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() { return false; },
    };
  }

  function getSyntheticController(info) {
    const key = info && info.sessionId ? `${info.sessionId}:${(info.pendingIds || []).join(',')}` : '';
    if (syntheticController && syntheticControllerKey === key) return syntheticController;

    const controller = makeAbortController() || { signal: makeSyntheticSignal() };
    const nativeAbort = typeof controller.abort === 'function' ? controller.abort.bind(controller) : null;
    let aborting = false;

    controller.__arenaHardStopSynthetic = true;
    controller.abort = reason => {
      if (aborting) return;
      aborting = true;
      try {
        if (nativeAbort) nativeAbort(reason || 'Arena synthetic hard stop');
        else if (controller.signal) controller.signal.aborted = true;
      } catch (_) {}
      Promise.resolve().then(() => tryStatelessStop(pendingMessageInfo())).catch(err => warn('synthetic stop failed', err)).finally(() => {
        aborting = false;
      });
    };

    syntheticController = controller;
    syntheticControllerKey = key;
    return controller;
  }

  function clearSyntheticController() {
    syntheticController = null;
    syntheticControllerKey = '';
  }

  function applyStreamController(store, state, controller, actionName) {
    if (!store || !state) return false;

    try {
      if (typeof state.setActiveStreamController === 'function') {
        state.setActiveStreamController(controller);
        return true;
      }
    } catch (err) {
      warn('setActiveStreamController failed', err);
    }

    try {
      if (typeof store.setState === 'function') {
        store.setState(
          {
            activeStreamController: controller,
            canStopActiveStream: !!controller,
          },
          false,
          actionName || 'arenaHardStop/applyStreamController'
        );
        return true;
      }
    } catch (err) {
      warn('store.setState controller repair failed', err);
    }

    return false;
  }

  function repairActiveStreamState() {
    sweepExpiredStoppedGuards();
    updateOverlay();

    const store = findStoreFromFiber();
    const state = store && safeCall(() => store.getState());
    if (!store || !state) return;
    healStoppedRerunFailure(store, state);
    cleanupStaleStreamController(store, state);
    if (!ENABLE_STORE_CONTROLLER_REPAIR) return;
    const info = pendingMessageInfo();
    const record = newestActiveRecord();
    const pendingCount = info && Array.isArray(info.pendingIds) ? info.pendingIds.length : 0;

    if (!Object.prototype.hasOwnProperty.call(state, 'activeStreamController')) {
      rememberRepair({ at: new Date().toISOString(), ok: false, reason: 'missing-activeStreamController' });
      return;
    }

    let controller = null;
    let repairKind = '';
    if (record) {
      controller = record.repairController || record.externalController || null;
      repairKind = 'tracked';
    } else if (ENABLE_SYNTHETIC_CONTROLLER_REPAIR && pendingCount > 0) {
      controller = getSyntheticController(info);
      repairKind = 'synthetic';
    } else if (state.activeStreamController && state.activeStreamController.__arenaHardStopSynthetic) {
      if (applyStreamController(store, state, null, 'arenaHardStop/clearSyntheticController')) {
        clearSyntheticController();
        rememberRepair({ at: new Date().toISOString(), ok: true, kind: 'cleanup-synthetic' });
      }
      return;
    } else {
      clearSyntheticController();
      return;
    }

    if (!controller || typeof controller.abort !== 'function') return;
    if (state.activeStreamController === controller && state.canStopActiveStream === true) return;

    const applied = applyStreamController(store, state, controller, 'arenaHardStop/repairActiveStreamController');
    if (applied) {
      if (record) record.repairApplied = true;
      rememberRepair({
        at: new Date().toISOString(),
        ok: true,
        kind: repairKind,
        streamId: record ? record.id : '',
        path: record ? record.meta.path : '',
        pendingCount,
      });
      log('repaired stream state', repairKind, record ? record.meta.path : info && info.sessionId);
    } else {
      rememberRepair({ at: new Date().toISOString(), ok: false, kind: repairKind, reason: 'apply-failed' });
    }

    updateOverlay();
  }

  function cleanupRepairController(record) {
    if (!record || !record.repairController) return;
    const store = findStoreFromFiber();
    const state = store && safeCall(() => store.getState());
    if (!store || !state) return;
    if (state.activeStreamController !== record.repairController) return;

    applyStreamController(store, state, null, 'arenaHardStop/cleanupActiveStreamController');
  }

  function cleanupStaleStreamController(store, state) {
    if (!ENABLE_STALE_CONTROLLER_CLEANUP || !store || !state) return false;
    const hasController = !!(state.activeStreamController || state.canStopActiveStream);
    const activeCount = activeRecords().length;
    const info = pendingMessageInfo();
    const pendingCount = info && Array.isArray(info.pendingIds) ? info.pendingIds.length : 0;

    if (!hasController || activeCount > 0 || pendingCount > 0) {
      staleControllerSince = 0;
      return false;
    }

    const now = Date.now();
    if (!staleControllerSince) {
      staleControllerSince = now;
      return false;
    }
    if (now - staleControllerSince < 900) return false;

    const cleared = applyStreamController(store, state, null, 'arenaHardStop/cleanupStaleStreamController');
    if (cleared) {
      rememberRepair({
        at: new Date().toISOString(),
        ok: true,
        kind: 'cleanup-stale-controller',
        staleMs: now - staleControllerSince,
      });
      staleControllerSince = 0;
    }
    return cleared;
  }

  function scheduleStateRepair() {
    if (document.readyState === 'loading') return;
    requestAnimationFrame(repairActiveStreamState);
  }

  function scheduleUiUpdate() {
    if (document.readyState === 'loading') return;
    requestAnimationFrame(updateOverlay);
  }

  function ensureOverlay() {
    if (overlayBtn) return overlayBtn;

    overlayBtn = document.createElement('button');
    overlayBtn.type = 'button';
    overlayBtn.setAttribute('aria-label', 'Hard stop Arena stream');
    overlayBtn.setAttribute('title', 'Stop generation');
    overlayBtn.setAttribute('data-arena-hard-stop-overlay', 'true');
    overlayBtn.innerHTML = '<span aria-hidden="true" style="display:block;width:18px;height:18px;border-radius:3px;background:currentColor;"></span>';
    Object.assign(overlayBtn.style, {
      position: 'fixed',
      zIndex: '2147483647',
      display: 'none',
      alignItems: 'center',
      justifyContent: 'center',
      margin: '0',
      padding: '0',
      cursor: 'pointer',
      background: '#fff',
      color: '#242424',
      border: '1px solid rgba(0,0,0,.14)',
      boxShadow: 'none',
      boxSizing: 'border-box',
    });

    const stopClick = event => {
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
      hardStopAll();
    };

    overlayBtn.addEventListener('click', stopClick, true);
    overlayBtn.addEventListener('pointerdown', event => {
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
    }, true);

    document.documentElement.appendChild(overlayBtn);
    return overlayBtn;
  }

  function hideOverlay() {
    if (!overlayBtn) return;
    overlayBtn.style.display = 'none';
    overlayBtn.setAttribute('aria-label', 'Hard stop Arena stream');
  }

  function findVisibleSendButton() {
    const selectors = [
      'button[aria-label="Send message"][type="submit"]',
      'button[aria-label="Send message"]',
      'form button[type="submit"]',
    ];
    return Array.from(document.querySelectorAll(selectors.join(','))).find(isVisibleElement) || null;
  }

  function updateOverlay() {
    const records = activeRecords();
    const info = pendingMessageInfo();
    const hasPendingAssistant = !!(info && Array.isArray(info.pendingIds) && info.pendingIds.length);

    if ((!records.length && !hasPendingAssistant) || hasNativeStopButton()) {
      hideOverlay();
      return;
    }

    const target = findVisibleSendButton();
    if (!target) {
      hideOverlay();
      return;
    }

    const btn = ensureOverlay();
    const rect = target.getBoundingClientRect();
    const style = getComputedStyle(target);
    Object.assign(btn.style, {
      display: 'inline-flex',
      left: `${rect.left}px`,
      top: `${rect.top}px`,
      width: `${rect.width}px`,
      height: `${rect.height}px`,
      borderRadius: style.borderRadius || '8px',
      background: style.backgroundColor && style.backgroundColor !== 'rgba(0, 0, 0, 0)' ? style.backgroundColor : '#fff',
      color: style.color || '#242424',
    });
    btn.disabled = false;
    btn.setAttribute('aria-label', 'Stop generation');
  }

  async function postStopUrls(stopUrls) {
    let hadSuccess = false;
    let lastError = '';
    for (const stopItem of stopUrls) {
      const stopUrl = typeof stopItem === 'string' ? stopItem : stopItem && stopItem.url;
      if (!stopUrl) continue;
      try {
        rememberObservedStopRequest(stopUrl, { method: 'POST' }, 'hard-stop');
        const res = await nativeFetch(stopUrl, { method: 'POST', credentials: 'include' });
        rememberStopResult({
          url: stopUrl,
          id: stopItem && stopItem.id || '',
          source: stopItem && stopItem.source || '',
          confidence: stopItem && stopItem.confidence || '',
          status: res.status,
          ok: res.ok,
          at: new Date().toISOString(),
        });
        log('stop api', res.status, stopUrl);
        if (res.ok) hadSuccess = true;
        else lastError = `Stop API failed: ${res.status} ${res.statusText}`;
      } catch (err) {
        rememberStopResult({
          url: stopUrl,
          id: stopItem && stopItem.id || '',
          source: stopItem && stopItem.source || '',
          confidence: stopItem && stopItem.confidence || '',
          status: 0,
          ok: false,
          error: String(err && err.message || err),
          at: new Date().toISOString(),
        });
        warn('stop api failed', stopUrl, err);
        lastError = String(err && err.message || err || 'Failed to call stop API');
      }
    }
    return {
      hadSuccess,
      error: lastError,
    };
  }

  function buildStopUrls(sessionId, record, info) {
    return collectStopCandidates(record, info).map(candidate => ({
      ...candidate,
      url: `/nextjs-api/stream/stop/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(candidate.id)}`,
    }));
  }

  function findNativeOnStop() {
    const seed = Array.from(document.querySelectorAll('button[aria-label="Stop generation"]:not([data-arena-hard-stop-overlay="true"])'))
      .find(isVisibleElement);
    if (!seed) return null;
    const fiber = findReactFiber(seed);
    for (let cur = fiber, depth = 0; cur && depth < 100; depth += 1, cur = cur.return) {
      const props = cur.memoizedProps;
      if (props && typeof props.onStop === 'function') return props.onStop;
    }
    return null;
  }

  function invokeNativeOnStop() {
    const onStop = findNativeOnStop();
    if (typeof onStop !== 'function') return false;
    try {
      onStop();
      return true;
    } catch (err) {
      warn('native onStop failed', err);
      return false;
    }
  }

  async function tryStatelessStop(info) {
    if (!info || !info.sessionId) return false;
    const stopUrls = buildStopUrls(info.sessionId, null, info);
    if (!stopUrls.length) return false;
    showToast(`Trying ${stopUrls.length} Arena stop candidate${stopUrls.length > 1 ? 's' : ''}...`);
    const stoppedState = applyStoppedStateLocal(info);
    const result = await postStopUrls(stopUrls);
    if (!result.hadSuccess) applyStopFailureLocal(info, stoppedState, result.error);
    return true;
  }

  async function hardStopAll() {
    const now = Date.now();
    if (now - lastStopAt < 500) return;
    lastStopAt = now;

    const info = pendingMessageInfo();
    const records = activeRecords();
    if (!records.length) {
      const stopped = await tryStatelessStop(info);
      if (!stopped) showToast('No active Arena stream found');
      return;
    }

    showToast(`Stopping ${records.length} Arena stream${records.length > 1 ? 's' : ''}...`);

    if (hasNativeStopButton()) {
      invokeNativeOnStop();
    }

    try {
      const controller = info && info.state && info.state.activeStreamController;
      if (controller && typeof controller.abort === 'function') controller.abort();
    } catch (err) {
      warn('state controller abort failed', err);
    }

    for (const record of records) {
      record.stopRequestedAt = Date.now();

      try {
        if (typeof record.abortBody === 'function') record.abortBody('Arena hard stop');
      } catch (err) {
        warn('body abort failed', err);
      }

      try {
        if (typeof record.abort === 'function') record.abort();
      } catch (err) {
        warn('abort failed', err);
      }

      const sessionId = record.meta.sessionId || (info && info.sessionId) || getSessionIdFromLocation();
      const stopUrls = sessionId ? buildStopUrls(sessionId, record, info) : [];
      if (stopUrls.length) {
        const stoppedState = applyStoppedStateLocal(info);
        const result = await postStopUrls(stopUrls);
        if (!result.hadSuccess) applyStopFailureLocal(info, stoppedState, result.error);
      } else {
        warn('missing stop ids; aborted fetch only', record.meta);
      }
    }

    showToast('Stop signal sent');
    scheduleUiUpdate();
  }

  function rememberStopResult(result) {
    recentStopResults.push(result);
    while (recentStopResults.length > 10) recentStopResults.shift();
  }

  function showToast(text) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      Object.assign(toastEl.style, {
        position: 'fixed',
        zIndex: '2147483647',
        right: '24px',
        bottom: '76px',
        maxWidth: '320px',
        padding: '9px 12px',
        borderRadius: '8px',
        background: 'rgba(17,17,17,.92)',
        color: '#fff',
        font: '13px/1.35 system-ui, -apple-system, Segoe UI, sans-serif',
        boxShadow: '0 8px 28px rgba(0,0,0,.22)',
        display: 'none',
      });
      document.documentElement.appendChild(toastEl);
    }
    toastEl.textContent = text;
    toastEl.style.display = 'block';
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => {
      toastEl.style.display = 'none';
    }, 2200);
  }

  function destroyUi() {
    if (overlayBtn && typeof overlayBtn.remove === 'function') {
      overlayBtn.remove();
    }
    overlayBtn = null;
    if (toastEl) {
      clearTimeout(showToast._timer);
      if (typeof toastEl.remove === 'function') {
        toastEl.remove();
      } else if (toastEl.parentNode) {
        toastEl.parentNode.removeChild(toastEl);
      }
    }
    toastEl = null;
  }

  function uninstall(options) {
    const opts = options && typeof options === 'object' ? options : {};
    try {
      if (repairTimer) {
        clearInterval(repairTimer);
        repairTimer = 0;
      }
      destroyUi();
      liveStreams.clear();
      stoppedMessageGuards.clear();
      syntheticController = null;
      syntheticControllerKey = '';
      if (typeof NativeFetch === 'function' && window.fetch !== NativeFetch) {
        window.fetch = NativeFetch;
      }
      if (NativeAbortController && window.AbortController !== NativeAbortController) {
        window.AbortController = NativeAbortController;
      }
      if (window.__arenaHardStop && window.__arenaHardStop.version === VERSION) {
        delete window.__arenaHardStop;
      }
      installed = false;
      if (!opts.quiet) {
        log('uninstalled');
      }
      return true;
    } catch (err) {
      warn('uninstall failed', err);
      return false;
    }
  }

  function boot() {
    if (installed) return;
    installed = true;
    window.__arenaHardStop = {
      version: VERSION,
      status() {
        const store = findStoreFromFiber();
        const state = store && safeCall(() => store.getState());
        const info = pendingMessageInfo();
        return {
          version: VERSION,
          active: activeRecords().map(record => ({
            id: record.id,
            ageMs: Date.now() - record.startedAt,
            method: record.method,
            path: record.meta.path,
            kind: record.meta.kind,
            sessionId: record.meta.sessionId,
            parentMessageId: record.meta.parentMessageId,
            canCallStopApi: record.meta.canCallStopApi,
            canAbort: !!record.externalController,
            canAbortBody: typeof record.abortBody === 'function',
            done: record.done,
            finishReason: record.finishReason || '',
            stopRequestedAt: record.stopRequestedAt || 0,
            stopCandidates: collectStopCandidates(record, info),
            trace: record.trace || null,
          })),
          recentStopResults: recentStopResults.slice(),
          recentRepairs: recentRepairs.slice(),
          recentBodyStops: recentBodyStops.slice(),
          recentStreamTraces: recentStreamTraces.slice(),
          recentObservedStopRequests: recentObservedStopRequests.slice(),
          recentBlockedStreamRequests: recentBlockedStreamRequests.slice(),
          stoppedGuardIds: Array.from(stoppedMessageGuards.keys()),
          idDiagnostics: info ? {
            sessionId: info.sessionId || '',
            parentMessageId: info.parentMessageId || '',
            parentMessageIds: (info.parentMessageIds || []).slice(),
            pendingIds: (info.pendingIds || []).slice(),
            pendingMessages: (info.pendingMessages || []).map(msg => ({
              id: msg && msg.id || '',
              role: msg && msg.role || '',
              status: msg && msg.status || '',
              parentMessageIds: Array.isArray(msg && msg.parentMessageIds) ? msg.parentMessageIds.slice() : [],
              model: msg && (msg.model || msg.modelId || msg.modelName || msg.modelSlug || '') || '',
            })),
          } : null,
          store: state ? {
            id: state.id || '',
            hasActiveStreamController: !!state.activeStreamController,
            canStopActiveStream: !!state.canStopActiveStream,
            hasSyntheticController: !!(state.activeStreamController && state.activeStreamController.__arenaHardStopSynthetic),
            showStoppedUserPrompt: !!state.showStoppedUserPrompt,
            pendingAssistantCount: Array.isArray(state.messages) ? state.messages.filter(msg => msg && msg.role === 'assistant' && msg.status === 'pending').length : 0,
          } : null,
          hasNativeStopButton: hasNativeStopButton(),
          hasOverlayStopButton: !!(overlayBtn && overlayBtn.getAttribute('aria-label') === 'Stop generation' && overlayBtn.style.display !== 'none'),
          stopSelectorCount: document.querySelectorAll('button[aria-label="Stop generation"]').length,
          location: location.href,
        };
      },
      stop: hardStopAll,
      uninstall,
    };
    if (repairTimer) {
      clearInterval(repairTimer);
    }
    repairTimer = setInterval(scheduleStateRepair, REPAIR_INTERVAL_MS);
    log('installed');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
