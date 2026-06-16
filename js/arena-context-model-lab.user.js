// ==UserScript==
// @name         Arena.ai Context/Model Lab
// @namespace    local.codex.arena-context-model-lab
// @version      0.9.0
// @description  Records Arena.ai model/context changing triggers and provides opt-in experimental guards.
// @match        https://arena.ai/*
// @run-at       document-start
// @inject-into  page
// @noframes
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  const VERSION = '0.9.0';
  const LOG_PREFIX = '[Arena Context Lab]';
  const ID_RE = /\b019[a-z0-9-]{20,}\b/ig;
  const STREAM_RE = /\/nextjs-api\/stream\/(create-evaluation|post-to-evaluation|retry-evaluation-session-message|rerun|resample|resume-webdev|resume-video-workflow|skip-direct-battle)\b/;
  const API_RE = /\/nextjs-api\//;
  const STOP_RE = /\/nextjs-api\/stream\/stop\/([^/?#]+)\/messages\/([^/?#]+)/;
  const RETRY_RE = /\/nextjs-api\/stream\/retry-evaluation-session-message\/([^/?#]+)\/messages\/([^/?#]+)/;
  const RERUN_RE = /\/nextjs-api\/stream\/rerun\/([^/?#]+)/;
  const CREATE_RE = /\/nextjs-api\/stream\/create-evaluation\b/;
  const POST_RE = /\/nextjs-api\/stream\/post-to-evaluation\/([^/?#]+)/;
  const RESPONSE_PREVIEW_LIMIT = 1600;
  const STORE_SNAPSHOT_DELAYS = [250, 1000, 3000];
  const VOTE_API_BLOCK_WINDOW_MS = 4000;
  const MODEL_POLL_INTERVAL_MS = 100;
  const MODEL_POLL_DURATION_MS = 5000;
  const STATIC_PATH_RE = /(?:^\/_next\/static\/|^\/favicon\.ico$|\.(?:css|js|map|png|jpe?g|gif|webp|svg|ico|woff2?|ttf|otf)(?:$|[?#]))/i;

  const existing = window.__arenaContextLab;
  if (existing) {
    if (existing.version === VERSION) return;
    if (typeof existing.uninstall === 'function') {
      try {
        existing.uninstall({ quiet: true });
      } catch (err) {
        console.warn(LOG_PREFIX, 'previous uninstall failed', err);
      }
    } else {
      console.warn(LOG_PREFIX, 'existing instance without uninstall detected, skipping install');
      return;
    }
  }

  const NativeFetch = window.fetch;
  const nativeFetch = NativeFetch.bind(window);
  const NativeXMLHttpRequest = window.XMLHttpRequest;
  const nativeXhrOpen = NativeXMLHttpRequest && NativeXMLHttpRequest.prototype.open;
  const nativeXhrSend = NativeXMLHttpRequest && NativeXMLHttpRequest.prototype.send;
  const nativeSendBeacon = navigator.sendBeacon ? navigator.sendBeacon.bind(navigator) : null;
  const events = [];
  const config = {
    logToConsole: false,
    blockVoteButtons: false,
    blockUploads: false,
    blockRerun: false,
    blockRetry: false,
    blockCreate: false,
    blockPost: false,
    blockEmptyRerunMessageIds: false,
    blockNextjsApiAfterVoteClick: false,
    blockSameOriginNetworkAfterVoteClick: false,
    captureNextjsApi: true,
    captureVoteWindowNetwork: true,
    captureXhr: true,
    captureBeacon: true,
    captureResponsePreview: true,
    captureDelayedSnapshots: true,
    captureModelChanges: true,
    captureStoreSetState: true,
    rewriteNextPostToCreateEvaluation: false,
  };
  let installed = false;
  let eventSeq = 0;
  let fetchActive = true;
  let xhrActive = true;
  let beaconActive = true;
  let labFetchWrapper = null;
  let labBeaconWrapper = null;
  let voteApiBlockUntil = 0;
  let lastVoteClickSeq = 0;
  let forceCleanNextSend = false;
  let lastClientContextBackup = null;
  const patchedStores = [];
  let domPollTimer = null;

  function safeCall(fn, fallback) {
    try {
      return fn();
    } catch (_) {
      return fallback;
    }
  }

  function pushEvent(type, payload) {
    const entry = {
      seq: ++eventSeq,
      at: new Date().toISOString(),
      type,
      ...(payload || {}),
    };
    events.push(entry);
    while (events.length > 400) events.shift();
    if (config.logToConsole) console.log(LOG_PREFIX, entry);
    return entry;
  }

  function toAbsoluteUrl(input) {
    try {
      if (typeof input === 'string') return new URL(input, location.href).href;
      if (input && typeof input.url === 'string') return new URL(input.url, location.href).href;
    } catch (_) {}
    return '';
  }

  function inputMethod(input, init) {
    return String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
  }

  function sameOriginPath(url) {
    return safeCall(() => {
      const parsed = new URL(url, location.href);
      if (parsed.origin !== location.origin) return '';
      return parsed.pathname || '';
    }, '');
  }

  function isVoteWindowActive() {
    return Date.now() <= voteApiBlockUntil;
  }

  function shouldRecordNetworkPath(path) {
    if (!path || STATIC_PATH_RE.test(path)) return false;
    if (API_RE.test(path)) return true;
    return config.captureVoteWindowNetwork && isVoteWindowActive();
  }

  function extractArenaIds(text) {
    const out = [];
    const seen = new Set();
    ID_RE.lastIndex = 0;
    let match = ID_RE.exec(String(text || ''));
    while (match) {
      const id = String(match[0] || '').trim();
      if (id && !seen.has(id)) {
        seen.add(id);
        out.push(id);
      }
      match = ID_RE.exec(String(text || ''));
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
    return { type: Object.prototype.toString.call(body), text: safeCall(() => String(body), '') };
  }

  function parseBodyFields(bodyInfo) {
    if (!bodyInfo || bodyInfo.type !== 'string' || !bodyInfo.text) return null;
    return safeCall(() => {
      const parsed = JSON.parse(bodyInfo.text);
      if (!parsed || typeof parsed !== 'object') return null;
      return {
        id: parsed.id || '',
        mode: parsed.mode || '',
        modality: parsed.modality || '',
        userMessageId: parsed.userMessageId || '',
        modelAMessageId: parsed.modelAMessageId || '',
        modelBMessageId: parsed.modelBMessageId || '',
        messageIds: Array.isArray(parsed.messageIds) ? parsed.messageIds.slice() : [],
        userContent: parsed.userMessage && typeof parsed.userMessage.content === 'string'
          ? parsed.userMessage.content.slice(0, 300)
          : '',
        attachmentCount: parsed.userMessage && Array.isArray(parsed.userMessage.experimental_attachments)
          ? parsed.userMessage.experimental_attachments.length
          : 0,
      };
    }, null);
  }

  function safeJsonParse(text) {
    return safeCall(() => JSON.parse(String(text || '')), null);
  }

  function uuidish() {
    if (crypto && typeof crypto.randomUUID === 'function') {
      return `019e${crypto.randomUUID().replace(/-/g, '').slice(0, 23)}`;
    }
    const rand = Array.from({ length: 23 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
    return `019e${rand}`;
  }

  function makeInitWithBody(init, body) {
    const next = { ...(init || {}) };
    next.method = 'POST';
    next.body = JSON.stringify(body);
    const headers = new Headers((init && init.headers) || {});
    if (!headers.has('content-type')) headers.set('content-type', 'application/json');
    next.headers = headers;
    return next;
  }

  function requestBodyFrom(input, init) {
    if (init && Object.prototype.hasOwnProperty.call(init, 'body')) return init.body;
    return null;
  }

  function traceRequest(input, init, url, path, method) {
    const bodyInfo = bodyTextForTrace(requestBodyFrom(input, init));
    return {
      method,
      path,
      urlIds: extractArenaIds(url),
      bodyIds: extractArenaIds(bodyInfo.text),
      bodyType: bodyInfo.type,
      bodyFields: parseBodyFields(bodyInfo),
      bodyPreview: bodyInfo.text ? bodyInfo.text.slice(0, 1200) : '',
      store: storeSnapshot(),
    };
  }

  function traceSimpleBody(body) {
    const bodyInfo = bodyTextForTrace(body);
    return {
      bodyIds: extractArenaIds(bodyInfo.text),
      bodyType: bodyInfo.type,
      bodyFields: parseBodyFields(bodyInfo),
      bodyPreview: bodyInfo.text ? bodyInfo.text.slice(0, 1200) : '',
    };
  }

  function canPreviewResponse(response) {
    if (!response || !config.captureResponsePreview) return false;
    const contentType = String(response.headers && response.headers.get && response.headers.get('content-type') || '');
    if (/text\/event-stream|application\/octet-stream/i.test(contentType)) return false;
    return /json|text|javascript|x-ndjson|problem/i.test(contentType);
  }

  function responsePreview(response, meta) {
    const base = {
      requestSeq: meta && meta.requestSeq || 0,
      requestType: meta && meta.requestType || '',
      method: meta && meta.method || '',
      path: meta && meta.path || '',
      status: response ? response.status : 0,
      ok: !!(response && response.ok),
      contentType: response && response.headers && response.headers.get
        ? String(response.headers.get('content-type') || '')
        : '',
      store: storeSnapshot(),
    };
    if (!canPreviewResponse(response)) {
      pushEvent('api-response', base);
      return;
    }
    response.clone().text().then(text => {
      const trimmed = String(text || '').slice(0, RESPONSE_PREVIEW_LIMIT);
      pushEvent('api-response', {
        ...base,
        responseIds: extractArenaIds(trimmed),
        responsePreview: trimmed,
      });
    }).catch(err => {
      pushEvent('api-response', {
        ...base,
        responsePreviewError: String(err && err.message ? err.message : err),
      });
    });
  }

  function classifyStreamPath(path) {
    if (CREATE_RE.test(path)) return 'create';
    if (POST_RE.test(path)) return 'post';
    if (RERUN_RE.test(path)) return 'rerun';
    if (RETRY_RE.test(path)) return 'retry';
    return 'stream';
  }

  function shouldBlockStream(kind, bodyFields) {
    if (kind === 'create' && config.blockCreate) return 'blockCreate';
    if (kind === 'post' && config.blockPost) return 'blockPost';
    if (kind === 'rerun' && config.blockRerun) return 'blockRerun';
    if (kind === 'retry' && config.blockRetry) return 'blockRetry';
    if (
      kind === 'rerun'
      && config.blockEmptyRerunMessageIds
      && bodyFields
      && Array.isArray(bodyFields.messageIds)
      && bodyFields.messageIds.length === 0
    ) {
      return 'emptyRerunMessageIds';
    }
    return '';
  }

  function blockedResponse(reason) {
    return Promise.resolve(new Response(JSON.stringify({
      error: reason,
      message: `Arena Context Lab blocked ${reason}`,
    }), {
      status: 451,
      statusText: `Arena Context Lab blocked ${reason}`,
      headers: { 'content-type': 'application/json' },
    }));
  }

  function shouldBlockApiAfterVote(path) {
    if (!config.blockNextjsApiAfterVoteClick) return '';
    if (!path || !API_RE.test(path)) return '';
    if (STREAM_RE.test(path) || STOP_RE.test(path)) return '';
    if (!isVoteWindowActive()) return '';
    return 'nextjsApiAfterVoteClick';
  }

  function shouldBlockSameOriginAfterVote(path) {
    if (!config.blockSameOriginNetworkAfterVoteClick) return '';
    if (!path || STATIC_PATH_RE.test(path)) return '';
    if (STREAM_RE.test(path) || STOP_RE.test(path)) return '';
    if (!isVoteWindowActive()) return '';
    return 'sameOriginNetworkAfterVoteClick';
  }

  function rewritePostToCreateIfNeeded(input, init, url, path) {
    const shouldRewrite = forceCleanNextSend || config.rewriteNextPostToCreateEvaluation;
    if (!shouldRewrite || !POST_RE.test(path)) return null;
    const bodyInfo = bodyTextForTrace(requestBodyFrom(input, init));
    const body = safeJsonParse(bodyInfo.text);
    if (!body || typeof body !== 'object' || !body.userMessage) return null;

    const nextBody = {
      ...body,
      id: uuidish(),
      mode: body.mode || 'battle',
      userMessageId: uuidish(),
      modelAMessageId: uuidish(),
      modelBMessageId: uuidish(),
    };
    delete nextBody.parentMessageIds;

    forceCleanNextSend = false;
    pushEvent('context-reset-rewrite', {
      fromPath: path,
      toPath: '/nextjs-api/stream/create-evaluation',
      oldSessionId: body.id || '',
      newSessionId: nextBody.id || '',
      bodyFields: parseBodyFields({ type: 'string', text: JSON.stringify(nextBody) }),
      store: storeSnapshot(),
    });
    return {
      input: new URL('/nextjs-api/stream/create-evaluation', location.href).href,
      init: makeInitWithBody(init, nextBody),
    };
  }

  function scheduleStoreSnapshots(source, referenceSeq) {
    if (!config.captureDelayedSnapshots) return;
    for (const delayMs of STORE_SNAPSHOT_DELAYS) {
      setTimeout(() => {
        pushEvent('store-snapshot', {
          source,
          referenceSeq: referenceSeq || 0,
          delayMs,
          store: storeSnapshot(),
        });
      }, delayMs);
    }
  }

  function messageModelSignature(store) {
    const tail = store && Array.isArray(store.tailMessages) ? store.tailMessages : [];
    return tail.map(msg => [
      msg && msg.id || '',
      msg && msg.status || '',
      msg && msg.model || '',
    ].join(':')).join('|');
  }

  function compactModelState(store) {
    const tail = store && Array.isArray(store.tailMessages) ? store.tailMessages : [];
    return {
      last: store && Array.isArray(store.lastMessageIds) ? store.lastMessageIds.slice() : [],
      count: store && store.messageCount || 0,
      messages: tail.map(msg => ({
        id: msg && msg.id || '',
        role: msg && msg.role || '',
        status: msg && msg.status || '',
        model: msg && msg.model || '',
        parents: Array.isArray(msg && msg.parentMessageIds) ? msg.parentMessageIds.slice() : [],
      })),
    };
  }

  function startModelChangePoll(source, referenceSeq) {
    if (!config.captureModelChanges) return;
    let lastSignature = messageModelSignature(storeSnapshot());
    const deadline = Date.now() + MODEL_POLL_DURATION_MS;
    const timer = setInterval(() => {
      const store = storeSnapshot();
      const nextSignature = messageModelSignature(store);
      if (nextSignature !== lastSignature) {
        lastSignature = nextSignature;
        pushEvent('model-state-change', {
          source,
          referenceSeq: referenceSeq || 0,
          store: compactModelState(store),
        });
      }
      if (Date.now() >= deadline) clearInterval(timer);
    }, MODEL_POLL_INTERVAL_MS);
  }

  function domTextSignature() {
    const main = safeCall(() => document.querySelector('main'), null) || document.body;
    const text = main && typeof main.innerText === 'string' ? main.innerText : '';
    return String(text)
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 2000);
  }

  function startDomPoll(source, referenceSeq) {
    if (!config.captureModelChanges) return;
    if (domPollTimer) return;
    let lastText = domTextSignature();
    const deadline = Date.now() + MODEL_POLL_DURATION_MS;
    domPollTimer = setInterval(() => {
      const nextText = domTextSignature();
      if (nextText !== lastText) {
        lastText = nextText;
        pushEvent('dom-text-change', {
          source,
          referenceSeq: referenceSeq || 0,
          textPreview: nextText,
        });
      }
      if (Date.now() >= deadline) {
        clearInterval(domPollTimer);
        domPollTimer = null;
      }
    }, MODEL_POLL_INTERVAL_MS);
  }

  function findReactFiber(el) {
    if (!el) return null;
    const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
    return key ? el[key] : null;
  }

  function looksLikeArenaStore(value) {
    if (!value || typeof value !== 'object') return false;
    if (typeof value.getState !== 'function') return false;
    const state = safeCall(() => value.getState(), null);
    return !!(state && typeof state === 'object' && Array.isArray(state.messages) && typeof state.id === 'string');
  }

  function findArenaStoreIn(value, depth, seen) {
    if (!value || typeof value !== 'object' || depth < 0 || seen.has(value)) return null;
    seen.add(value);
    if (looksLikeArenaStore(value)) return value;
    const keys = safeCall(() => Object.keys(value), []);
    for (const key of keys.slice(0, 80)) {
      if (key === '_owner' || key === 'return' || key === 'child' || key === 'sibling' || key === 'alternate') continue;
      const found = findArenaStoreIn(value[key], depth - 1, seen);
      if (found) return found;
    }
    return null;
  }

  function findStoreFromFiber() {
    const roots = [
      document.querySelector('button[aria-label="Stop generation"]'),
      document.querySelector('button[aria-label="Send message"][type="submit"]'),
      document.querySelector('form'),
      document.querySelector('main'),
      document.body,
    ].filter(Boolean);
    for (const root of roots) {
      const fiber = findReactFiber(root);
      for (let cur = fiber, depth = 0; cur && depth < 80; depth += 1, cur = cur.return) {
        const found = findArenaStoreIn(cur.memoizedProps, 4, new WeakSet())
          || findArenaStoreIn(cur.memoizedState, 4, new WeakSet());
        if (found) return found;
      }
    }
    return null;
  }

  function storeSnapshot() {
    const store = findStoreFromFiber();
    const state = store && safeCall(() => store.getState(), null);
    if (!state) return null;
    const messages = Array.isArray(state.messages) ? state.messages : [];
    return {
      id: state.id || '',
      mode: state.mode || '',
      modality: state.modality || '',
      showStoppedUserPrompt: !!state.showStoppedUserPrompt,
      hasActiveStreamController: !!state.activeStreamController,
      canStopActiveStream: !!state.canStopActiveStream,
      lastMessageIds: Array.isArray(state.lastMessageIds) ? state.lastMessageIds.slice() : [],
      messageCount: messages.length,
      tailMessages: messages.slice(-8).map(msg => ({
        id: msg && msg.id || '',
        role: msg && msg.role || '',
        status: msg && msg.status || '',
        parentMessageIds: Array.isArray(msg && msg.parentMessageIds) ? msg.parentMessageIds.slice() : [],
        model: msg && (msg.model || msg.modelId || msg.modelName || msg.modelSlug || '') || '',
        contentPreview: typeof (msg && msg.content) === 'string' ? msg.content.slice(0, 120) : '',
      })),
    };
  }

  function clickLabel(target) {
    const el = target && target.closest
      ? target.closest('button, [role="button"], label, input, [data-testid], [aria-label]')
      : target;
    if (!el) return '';
    return [
      el.getAttribute && el.getAttribute('aria-label'),
      el.getAttribute && el.getAttribute('title'),
      el.innerText,
      el.value,
      el.getAttribute && el.getAttribute('data-testid'),
    ].filter(Boolean).join(' | ').trim().slice(0, 240);
  }

  function isVoteLabel(text) {
    return /A\s*更好|B\s*更好|都好|都不好|A\s*better|B\s*better|both|neither/i.test(String(text || ''));
  }

  function onClickCapture(event) {
    const label = clickLabel(event.target);
    if (!label) return;
    const vote = isVoteLabel(label);
    if (!vote && !/重试|重新|rerun|retry|regenerate|upload|image|图片|文件|file/i.test(label)) return;
    const entry = pushEvent(vote ? 'vote-click' : 'ui-click', {
      label,
      blocked: vote && config.blockVoteButtons,
      softBlockApiAfterVoteClick: vote && config.blockNextjsApiAfterVoteClick,
      store: storeSnapshot(),
    });
    if (vote) {
      lastVoteClickSeq = entry.seq;
      if (config.blockNextjsApiAfterVoteClick) {
        voteApiBlockUntil = Date.now() + VOTE_API_BLOCK_WINDOW_MS;
      }
    }
    scheduleStoreSnapshots(vote ? 'vote-click' : 'ui-click', entry.seq);
    if (vote) {
      installStoreHooks();
      startModelChangePoll('vote-click', entry.seq);
      startDomPoll('vote-click', entry.seq);
    }
    if (entry.blocked) {
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
    }
  }

  function onChangeCapture(event) {
    const target = event.target;
    if (!target || target.tagName !== 'INPUT' || target.type !== 'file') return;
    const files = Array.from(target.files || []);
    const blocked = config.blockUploads;
    const entry = pushEvent('file-input-change', {
      blocked,
      files: files.map(file => ({
        name: file.name,
        type: file.type,
        size: file.size,
      })),
      store: storeSnapshot(),
    });
    scheduleStoreSnapshots('file-input-change', entry.seq);
    if (blocked) {
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
      try {
        target.value = '';
      } catch (_) {}
    }
  }

  function installXhrHooks() {
    if (!NativeXMLHttpRequest || !nativeXhrOpen || !nativeXhrSend || !config.captureXhr) return;
    const proto = NativeXMLHttpRequest.prototype;
    if (proto.__arenaContextLabPatched) return;
    const open = nativeXhrOpen;
    const send = nativeXhrSend;
    proto.open = function arenaContextLabXhrOpen(method, url) {
      try {
        this.__arenaContextLabMethod = String(method || 'GET').toUpperCase();
        this.__arenaContextLabUrl = toAbsoluteUrl(url);
      } catch (_) {}
      return open.apply(this, arguments);
    };
    proto.send = function arenaContextLabXhrSend(body) {
      if (!xhrActive) return send.apply(this, arguments);
      const url = this.__arenaContextLabUrl || '';
      const method = this.__arenaContextLabMethod || 'GET';
      const path = sameOriginPath(url);
      if (shouldRecordNetworkPath(path)) {
        const blockReason = shouldBlockSameOriginAfterVote(path);
        const entry = pushEvent('xhr-request', {
          blocked: !!blockReason,
          blockReason,
          voteReferenceSeq: blockReason ? lastVoteClickSeq : 0,
          method,
          path,
          urlIds: extractArenaIds(url),
          ...traceSimpleBody(body),
          store: storeSnapshot(),
        });
        scheduleStoreSnapshots('xhr-request', entry.seq);
        if (blockReason) {
          try { this.abort && this.abort(); } catch (_) {}
          return;
        }
      }
      return send.apply(this, arguments);
    };
    Object.defineProperty(proto, '__arenaContextLabPatched', {
      value: true,
      configurable: true,
    });
  }

  function installBeaconHook() {
    if (!nativeSendBeacon || !config.captureBeacon) return;
    if (navigator.__arenaContextLabBeaconPatched) return;
    labBeaconWrapper = function arenaContextLabSendBeacon(url, data) {
      if (!beaconActive) return nativeSendBeacon(url, data);
      const href = toAbsoluteUrl(url);
      const path = sameOriginPath(href);
      if (shouldRecordNetworkPath(path)) {
        const blockReason = shouldBlockSameOriginAfterVote(path);
        const entry = pushEvent('beacon-request', {
          blocked: !!blockReason,
          blockReason,
          voteReferenceSeq: blockReason ? lastVoteClickSeq : 0,
          urlIds: extractArenaIds(href),
          method: 'BEACON',
          path,
          ...traceSimpleBody(data),
          store: storeSnapshot(),
        });
        scheduleStoreSnapshots('beacon-request', entry.seq);
        if (blockReason) return false;
      }
      return nativeSendBeacon(url, data);
    };
    navigator.sendBeacon = labBeaconWrapper;
    Object.defineProperty(navigator, '__arenaContextLabBeaconPatched', {
      value: true,
      configurable: true,
    });
  }

  function installStoreHooks() {
    if (!config.captureStoreSetState) return;
    const store = safeCall(() => findStoreFromFiber(), null);
    if (!store || patchedStores.includes(store)) return;
    const originalSetState = typeof store.setState === 'function' ? store.setState.bind(store) : null;
    if (!originalSetState) return;
    try {
      store.setState = function arenaContextLabSetState(partial, replace, action) {
        const before = compactModelState(safeCall(() => store.getState(), null));
        const result = originalSetState(partial, replace, action);
        const after = compactModelState(safeCall(() => store.getState(), null));
        pushEvent('store-set-state', {
          action: typeof action === 'string' ? action : '',
          before,
          after,
        });
        return result;
      };
      patchedStores.push(store);
      pushEvent('store-hook-installed', {
        store: compactModelState(safeCall(() => store.getState(), null)),
      });
    } catch (err) {
      pushEvent('store-hook-error', {
        error: String(err && err.message ? err.message : err),
      });
    }
  }

  labFetchWrapper = function arenaContextLabFetch(input, init) {
    if (!fetchActive) return nativeFetch(input, init);
    const url = toAbsoluteUrl(input);
    const method = inputMethod(input, init);
    const path = safeCall(() => new URL(url, location.href).pathname, '');
    const rewrite = rewritePostToCreateIfNeeded(input, init, url, path);
    if (rewrite) {
      return labFetchWrapper(rewrite.input, rewrite.init);
    }
    let responseMeta = null;
    if (path && STREAM_RE.test(path)) {
      const trace = traceRequest(input, init, url, path, method);
      const bodyFields = trace.bodyFields;
      const kind = classifyStreamPath(path);
      const blockReason = shouldBlockStream(kind, bodyFields);
      const entry = pushEvent('stream-request', {
        kind,
        blocked: !!blockReason,
        blockReason,
        ...trace,
      });
      scheduleStoreSnapshots(`stream-${kind}`, entry.seq);
      if (blockReason) return blockedResponse(blockReason);
      responseMeta = {
        requestSeq: entry.seq,
        requestType: 'stream-request',
        method,
        path,
      };
    } else if (path && STOP_RE.test(path)) {
      const match = path.match(STOP_RE);
      const entry = pushEvent('stop-request', {
        method,
        path,
        sessionId: match ? decodeURIComponent(match[1]) : '',
        messageId: match ? decodeURIComponent(match[2]) : '',
        store: storeSnapshot(),
      });
      scheduleStoreSnapshots('stop-request', entry.seq);
      responseMeta = {
        requestSeq: entry.seq,
        requestType: 'stop-request',
        method,
        path,
      };
    } else if (path && API_RE.test(path) && config.captureNextjsApi) {
      const blockReason = shouldBlockApiAfterVote(path);
      const entry = pushEvent('api-request', {
        blocked: !!blockReason,
        blockReason,
        voteReferenceSeq: blockReason ? lastVoteClickSeq : 0,
        ...traceRequest(input, init, url, path, method),
      });
      scheduleStoreSnapshots('api-request', entry.seq);
      if (blockReason) return blockedResponse(blockReason);
      responseMeta = {
        requestSeq: entry.seq,
        requestType: 'api-request',
        method,
        path,
      };
    }
    const promise = nativeFetch(input, init);
    if (responseMeta && responseMeta.requestType !== 'stream-request') {
      return promise.then(response => {
        responsePreview(response, responseMeta);
        return response;
      }, err => {
        pushEvent('api-error', {
          ...responseMeta,
          error: String(err && err.message ? err.message : err),
          store: storeSnapshot(),
        });
        throw err;
      });
    }
    return promise;
  };
  window.fetch = labFetchWrapper;
  installXhrHooks();
  installBeaconHook();

  function configure(nextConfig) {
    if (!nextConfig || typeof nextConfig !== 'object') return { ...config };
    for (const [key, value] of Object.entries(nextConfig)) {
      if (Object.prototype.hasOwnProperty.call(config, key)) config[key] = !!value;
    }
    pushEvent('configure', { config: { ...config } });
    return { ...config };
  }

  function compactMessage(msg) {
    if (!msg || typeof msg !== 'object') return null;
    return {
      id: msg.id || '',
      role: msg.role || '',
      status: msg.status || '',
      parents: Array.isArray(msg.parentMessageIds) ? msg.parentMessageIds.slice() : [],
      model: msg.model || '',
      text: msg.contentPreview || '',
    };
  }

  function compactStore(store) {
    if (!store) return null;
    return {
      id: store.id || '',
      mode: store.mode || '',
      modality: store.modality || '',
      stoppedPrompt: !!store.showStoppedUserPrompt,
      controller: !!store.hasActiveStreamController,
      canStop: !!store.canStopActiveStream,
      last: Array.isArray(store.lastMessageIds) ? store.lastMessageIds.slice() : [],
      count: store.messageCount || 0,
      tail: Array.isArray(store.tailMessages) ? store.tailMessages.map(compactMessage).filter(Boolean) : [],
    };
  }

  function compactEvent(event) {
    const out = {
      seq: event.seq || 0,
      at: event.at || '',
      type: event.type || '',
    };
    for (const key of [
      'kind',
      'method',
      'path',
      'label',
      'blocked',
      'blockReason',
      'softBlockApiAfterVoteClick',
      'voteReferenceSeq',
      'previousLastIds',
      'keptIds',
      'keptCount',
      'beforeCount',
      'reason',
      'status',
      'ok',
      'contentType',
      'delayMs',
      'source',
      'referenceSeq',
      'requestSeq',
      'requestType',
      'error',
      'responsePreviewError',
      'action',
      'textPreview',
    ]) {
      if (Object.prototype.hasOwnProperty.call(event, key)) out[key] = event[key];
    }
    if (event.files) out.files = event.files;
    if (event.bodyFields) out.bodyFields = event.bodyFields;
    if (event.urlIds) out.urlIds = event.urlIds;
    if (event.bodyIds) out.bodyIds = event.bodyIds;
    if (event.responseIds) out.responseIds = event.responseIds;
    if (event.responsePreview) out.responsePreview = event.responsePreview;
    if (event.bodyPreview) out.bodyPreview = event.bodyPreview;
    if (event.store) out.store = compactStore(event.store);
    if (event.before) out.before = event.before;
    if (event.after) out.after = event.after;
    return out;
  }

  function timeline(limit) {
    const n = Number.isFinite(Number(limit)) ? Math.max(1, Number(limit)) : 80;
    return events.slice(-n).map(compactEvent);
  }

  function clear() {
    events.length = 0;
    return true;
  }

  function cleanNextSend() {
    forceCleanNextSend = true;
    pushEvent('context-reset-armed', {
      mode: 'rewrite-next-post-to-create-evaluation',
      store: storeSnapshot(),
    });
    return true;
  }

  function resetCurrentStoreClientOnly() {
    const store = findStoreFromFiber();
    const state = store && safeCall(() => store.getState(), null);
    if (!store || !state || typeof store.setState !== 'function' || !Array.isArray(state.messages)) {
      return false;
    }
    const next = {
      ...state,
      messages: [],
      lastMessageIds: [],
      activeStreamController: null,
      canStopActiveStream: false,
      showStoppedUserPrompt: false,
    };
    store.setState(next, true);
    pushEvent('context-reset-client-only', {
      store: storeSnapshot(),
    });
    return true;
  }

  function cleanNextSendSameModels() {
    const store = findStoreFromFiber();
    const state = store && safeCall(() => store.getState(), null);
    if (!store || !state || typeof store.setState !== 'function' || !Array.isArray(state.messages)) {
      pushEvent('context-reset-same-models-arm-failed', {
        reason: 'store-unavailable',
        store: storeSnapshot(),
      });
      return false;
    }
    const lastIds = Array.isArray(state.lastMessageIds) ? state.lastMessageIds.slice() : [];
    lastClientContextBackup = {
      messages: state.messages,
      lastMessageIds: lastIds,
    };
    store.setState({
      ...state,
      messages: [],
      lastMessageIds: [],
      showStoppedUserPrompt: false,
    }, true);
    pushEvent('context-reset-same-models-armed', {
      previousLastIds: lastIds,
      keptIds: [],
      keptCount: 0,
      beforeCount: state.messages.length,
      store: storeSnapshot(),
    });
    return true;
  }

  function restoreClientContext() {
    const store = findStoreFromFiber();
    const state = store && safeCall(() => store.getState(), null);
    if (!store || !state || typeof store.setState !== 'function' || !lastClientContextBackup) return false;
    store.setState({
      ...state,
      messages: lastClientContextBackup.messages,
      lastMessageIds: lastClientContextBackup.lastMessageIds,
    }, true);
    pushEvent('context-reset-same-models-restored', {
      store: storeSnapshot(),
    });
    lastClientContextBackup = null;
    return true;
  }

  function status() {
    return {
      version: VERSION,
      config: { ...config },
      eventCount: events.length,
      events: events.slice(),
      recentEvents: events.slice(-40),
      timeline: timeline(80),
      store: storeSnapshot(),
      location: location.href,
    };
  }

  function uninstall(options) {
    const opts = options && typeof options === 'object' ? options : {};
    document.removeEventListener('click', onClickCapture, true);
    document.removeEventListener('change', onChangeCapture, true);
    fetchActive = false;
    xhrActive = false;
    beaconActive = false;
    if (window.fetch === labFetchWrapper) window.fetch = NativeFetch;
    if (NativeXMLHttpRequest && nativeXhrOpen && nativeXhrSend) {
      try {
        NativeXMLHttpRequest.prototype.open = nativeXhrOpen;
        NativeXMLHttpRequest.prototype.send = nativeXhrSend;
        delete NativeXMLHttpRequest.prototype.__arenaContextLabPatched;
      } catch (_) {}
    }
    if (nativeSendBeacon && navigator.sendBeacon === labBeaconWrapper) {
      try {
        navigator.sendBeacon = nativeSendBeacon;
        delete navigator.__arenaContextLabBeaconPatched;
      } catch (_) {}
    }
    if (window.__arenaContextLab && window.__arenaContextLab.version === VERSION) {
      delete window.__arenaContextLab;
    }
    installed = false;
    if (!opts.quiet) console.log(LOG_PREFIX, 'uninstalled');
    return true;
  }

  function boot() {
    if (installed) return;
    installed = true;
    document.addEventListener('click', onClickCapture, true);
    document.addEventListener('change', onChangeCapture, true);
    window.__arenaContextLab = {
      version: VERSION,
      configure,
      clear,
      cleanNextSend,
      cleanNextSendSameModels,
      restoreClientContext,
      resetCurrentStoreClientOnly,
      timeline,
      status,
      uninstall,
    };
    pushEvent('installed', { version: VERSION });
    setTimeout(installStoreHooks, 1000);
    setTimeout(installStoreHooks, 3000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
