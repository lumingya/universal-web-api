import subprocess
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "js" / "arena-stream-hard-stop.user.js"


NODE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync(process.argv[1], 'utf8');
let locationState = {
  href: 'https://arena.ai/c/A',
  pathname: '/c/A',
};
let rafCallbacks = [];
let intervalCallback = null;
const eventListeners = new Map();
const documentElements = [];

function makeElement(tagName) {
  const attrs = new Map();
  const element = {
    tagName: String(tagName || '').toUpperCase(),
    style: {},
    parentNode: null,
    innerHTML: '',
    type: '',
    disabled: false,
    setAttribute(name, value) { attrs.set(name, String(value)); },
    getAttribute(name) { return attrs.get(name) || null; },
    addEventListener() {},
    closest(selector) {
      if (this.getAttribute('aria-label') === 'Rerun stopped messages'
        && String(selector || '').includes('Rerun stopped messages')) return this;
      if (this.getAttribute('aria-label') === 'Send message'
        && String(selector || '').includes('Send message')) return this;
      return null;
    },
    remove() {
      const index = documentElements.indexOf(this);
      if (index >= 0) documentElements.splice(index, 1);
      this.parentNode = null;
    },
    appendChild(child) {
      child.parentNode = this;
      if (!documentElements.includes(child)) documentElements.push(child);
      return child;
    },
    getBoundingClientRect() {
      return { left: 10, top: 20, width: 100, height: 30 };
    },
  };
  return element;
}

const sendButton = makeElement('button');
sendButton.setAttribute('aria-label', 'Send message');
sendButton.setAttribute('type', 'submit');
sendButton.style.display = 'block';
sendButton.style.visibility = 'visible';
sendButton.style.backgroundColor = '#fff';
sendButton.style.color = '#000';
sendButton.style.borderRadius = '8px';

const retryButton = makeElement('button');
retryButton.setAttribute('aria-label', 'Rerun stopped messages');

const document = {
  readyState: 'complete',
  documentElement: makeElement('html'),
  addEventListener: addEventListener,
  removeEventListener: removeEventListener,
  createElement: makeElement,
  querySelector(selector) {
    if (selector.includes('Stop generation')) {
      if (selector.includes(':not([data-arena-hard-stop-overlay="true"])')) return null;
      return documentElements.find(el => el.getAttribute('aria-label') === 'Stop generation') || null;
    }
    if (selector.includes('Send message')) return sendButton;
    return null;
  },
  querySelectorAll(selector) {
    if (selector.includes('Stop generation')) {
      return documentElements.filter(el => el.getAttribute('aria-label') === 'Stop generation');
    }
    if (selector.includes('Send message') || selector.includes('form button')) return [sendButton];
    return [];
  },
};

document.documentElement.appendChild = child => {
  child.parentNode = document.documentElement;
  if (!documentElements.includes(child)) documentElements.push(child);
  return child;
};

function setLocation(path) {
  const url = new URL(path, 'https://arena.ai');
  locationState = {
    href: url.href,
    pathname: url.pathname,
    search: url.search,
    hash: url.hash,
    origin: url.origin,
  };
}

const fetchCalls = [];
const nativeFetch = (input, init = {}) => {
  const url = String(input && input.url ? input.url : input);
  fetchCalls.push({ url, init });
  if (url.includes('/nextjs-api/stream/stop/')) return Promise.resolve(new Response('', { status: 200 }));
  return new Promise(() => {});
};
function addEventListener(type, callback) {
  if (!eventListeners.has(type)) eventListeners.set(type, new Set());
  eventListeners.get(type).add(callback);
}
function removeEventListener(type, callback) {
  const listeners = eventListeners.get(type);
  if (listeners) listeners.delete(callback);
}
const window = {
  fetch: nativeFetch,
  Request,
  AbortController,
  AbortSignal,
  __arenaHardStop: null,
  addEventListener,
  removeEventListener,
  history: {
    pushState(_state, _title, path) { setLocation(path); },
    replaceState(_state, _title, path) { setLocation(path); },
  },
};
const storeState = {
  id: 'A',
  messages: [{
    id: 'assistant-A',
    role: 'assistant',
    status: 'pending',
    parentMessageIds: ['parent-A'],
  }],
  activeStreamController: null,
  canStopActiveStream: false,
};
const store = {
  getState() { return storeState; },
};
sendButton.__reactFiber$test = { memoizedProps: { store }, return: null };
const context = {
  window,
  document,
  location: new Proxy({}, {
    get(_target, property) { return locationState[property]; },
  }),
  console,
  URL,
  URLSearchParams,
  Request,
  AbortController,
  AbortSignal,
  DOMException,
  TextDecoder,
  ReadableStream,
  Response,
  Blob,
  FormData,
  getComputedStyle() {
    return {
      display: 'block',
      visibility: 'visible',
      backgroundColor: '#fff',
      color: '#000',
      borderRadius: '8px',
    };
  },
  requestAnimationFrame(callback) { rafCallbacks.push(callback); },
  setInterval(callback) { intervalCallback = callback; return 1; },
  clearInterval() {},
  setTimeout() { return 1; },
  clearTimeout() {},
  setImmediate,
  Promise,
  setLocation,
  flush() {
    const callbacks = rafCallbacks.splice(0);
    callbacks.forEach(callback => callback());
  },
  dispatchDocumentEvent(type, event) {
    const listeners = eventListeners.get(type);
    if (!listeners) return;
    listeners.forEach(callback => callback(event));
  },
  tick() {
    if (intervalCallback) intervalCallback();
    context.flush();
  },
};
window.window = window;
window.document = document;
window.location = context.location;
window.requestAnimationFrame = context.requestAnimationFrame;
window.setTimeout = context.setTimeout;
window.clearTimeout = context.clearTimeout;
window.setInterval = context.setInterval;
window.clearInterval = context.clearInterval;

vm.runInNewContext(source, context, { filename: 'arena-stream-hard-stop.user.js' });
context.flush();

if (documentElements.some(el => el.getAttribute('aria-label') === 'Stop generation')) {
  throw new Error('pending assistant alone must not create a hard-stop overlay');
}

sendButton.disabled = true;
context.dispatchDocumentEvent('click', { target: retryButton });
context.flush();
if (!window.__arenaHardStop.status().hasOverlayStopButton) {
  throw new Error('retrying a stopped message must restore the hard-stop overlay');
}
sendButton.disabled = false;
context.tick();
if (window.__arenaHardStop.status().hasOverlayStopButton) {
  throw new Error('retry intent must not keep the overlay after the send control is enabled');
}

window.fetch('/nextjs-api/stream/post-to-evaluation/A', { method: 'POST' });
context.flush();
if (!window.__arenaHardStop.status().hasOverlayStopButton) {
  throw new Error('expected hard-stop overlay on the originating page');
}

window.history.pushState({}, '', '/c/B');
if (window.__arenaHardStop.status().hasOverlayStopButton) {
  throw new Error('hard-stop overlay was not hidden during navigation');
}
if (window.__arenaHardStop.status().active.length !== 0) {
  throw new Error('active stream from page A remained actionable on page B');
}
context.tick();
if (window.__arenaHardStop.status().hasOverlayStopButton) {
  throw new Error('hard-stop overlay leaked from page A to page B');
}

window.history.pushState({}, '', '/');
window.__arenaHardStop.stop();
if (fetchCalls.some(call => call.url.includes('/nextjs-api/stream/stop/'))) {
  throw new Error('page B stop attempted to stop a pending message from page A');
}

async function verifyCreateRequestAndUninstall() {
  window.fetch(new Request('https://arena.ai/nextjs-api/stream/create-evaluation', {
    method: 'POST',
    body: JSON.stringify({ id: 'NEW' }),
  }));
  await new Promise(resolve => setImmediate(resolve));
  context.flush();
  window.history.pushState({}, '', '/c/NEW');
  context.tick();
  if (!window.__arenaHardStop.status().hasOverlayStopButton) {
    throw new Error('create-evaluation stream did not follow its created session');
  }

  window.history.pushState({}, '', '/c/NEW?view=alternate');
  window.__arenaHardStop.uninstall();
  context.flush();
  if (documentElements.some(el => el.getAttribute('aria-label') === 'Stop generation')) {
    throw new Error('queued navigation repair recreated the overlay after uninstall');
  }
}

verifyCreateRequestAndUninstall().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
"""


def test_active_stream_is_locked_to_originating_page():
    completed = subprocess.run(
        ["node", "-e", NODE_HARNESS, str(SCRIPT_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
