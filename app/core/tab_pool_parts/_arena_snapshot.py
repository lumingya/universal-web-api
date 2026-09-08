"""
app/core/tab_pool_parts/_arena_snapshot.py

无外部依赖的纯 JavaScript 常量定义模块。
供 network, arena_tab_listener 以及命令引擎沙箱安全引用。
"""

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

__all__ = ["_ARENA_STORE_SNAPSHOT_JS"]
