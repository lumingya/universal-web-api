// ==UserScript==
// @name         Arena.ai Image Failure Guard
// @namespace    local.codex.arena-image-failure-guard
// @version      1.0.0
// @description  Degrades failed Cloudflare image fetches to a safe placeholder so Arena's image retry path cannot crash the page.
// @match        https://arena.ai/*
// @run-at       document-start
// @inject-into  page
// @noframes
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  const VERSION = '1.0.0';
  const LOG_PREFIX = '[Arena Image Guard]';
  const FAILURE_CACHE_TTL_MS = 15_000;
  const FAILURE_CACHE_LIMIT = 128;
  const IMAGE_HOST_RE = /(?:^|\.)imagedelivery\.net$|(?:^|\.)cloudflareimages\.com$|(?:^|\.)images\.cloudflare\.com$/i;
  const IMAGE_PATH_RE = /\/cdn-cgi\/image(?:\/|$)/i;
  const IMAGE_EXTENSION_RE = /\.(?:avif|gif|jpe?g|png|webp)(?:$|[?#])/i;
  const FALLBACK_PNG = new Uint8Array([
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
    0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
    0x08, 0x06, 0x00, 0x00, 0x00, 0x1f, 0x15, 0xc4,
    0x89, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x44, 0x41,
    0x54, 0x78, 0x9c, 0x63, 0x60, 0x00, 0x00, 0x00,
    0x02, 0x00, 0x01, 0xe2, 0x21, 0xbc, 0x33, 0x00,
    0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae,
    0x42, 0x60, 0x82,
  ]);

  const existing = window.__arenaImageFailureGuard;
  if (existing) {
    if (existing.version === VERSION) return;
    if (typeof existing.uninstall === 'function') {
      try {
        existing.uninstall({ quiet: true });
      } catch (_) {}
    } else {
      return;
    }
  }

  const NativeFetch = window.fetch;
  const nativeFetch = typeof NativeFetch === 'function' ? NativeFetch.bind(window) : null;
  const nativeConsoleError = typeof console.error === 'function' ? console.error.bind(console) : null;
  const nativeConsoleWarn = typeof console.warn === 'function' ? console.warn.bind(console) : null;
  const failedImages = new Map();
  let suppressedLogs = 0;
  let installed = false;
  let guardConsoleError = null;
  let guardConsoleWarn = null;

  function toUrl(input) {
    try {
      if (typeof input === 'string' || input instanceof URL) {
        return new URL(String(input), location.href);
      }
      if (input && input.url) return new URL(String(input.url), location.href);
    } catch (_) {}
    return null;
  }

  function isCloudflareImageUrl(url) {
    if (!url) return false;
    return IMAGE_HOST_RE.test(url.hostname) || IMAGE_PATH_RE.test(url.pathname);
  }

  function isImageRequest(input, init) {
    const url = toUrl(input);
    if (!url || !isCloudflareImageUrl(url)) return false;
    const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD') return false;
    if (IMAGE_PATH_RE.test(url.pathname) || IMAGE_EXTENSION_RE.test(url.pathname)) return true;
    try {
      const headers = new Headers((init && init.headers) || (input && input.headers));
      return String(headers.get('accept') || '').toLowerCase().includes('image/');
    } catch (_) {
      return true;
    }
  }

  function cacheKey(input) {
    const url = toUrl(input);
    return url ? `${url.origin}${url.pathname}` : String(input || '');
  }

  function pruneCache(now) {
    for (const [key, expiresAt] of failedImages) {
      if (expiresAt <= now) failedImages.delete(key);
    }
    while (failedImages.size > FAILURE_CACHE_LIMIT) {
      failedImages.delete(failedImages.keys().next().value);
    }
  }

  function fallbackResponse(url, reason) {
    if (typeof Response !== 'function') {
      throw new Error('Arena image guard requires the browser Response API');
    }
    return new Response(FALLBACK_PNG.slice(), {
      status: 200,
      statusText: 'OK',
      headers: {
        'content-type': 'image/png',
        'cache-control': 'no-store',
        'x-arena-image-fallback': String(reason || 'network-error'),
        'x-arena-image-url': String(url || '').slice(0, 200),
      },
    });
  }

  function isAbortError(error) {
    return !!error && String(error.name || '').toLowerCase() === 'aborterror';
  }

  function isRetryLog(args) {
    return args.some((arg) => String(arg == null ? '' : arg).toLowerCase().includes(
      'retry failed for cloudflare image'
    ));
  }

  async function guardedFetch(input, init) {
    if (!nativeFetch || !isImageRequest(input, init)) {
      if (!nativeFetch) throw new TypeError('window.fetch is unavailable');
      return nativeFetch(input, init);
    }

    const key = cacheKey(input);
    const now = Date.now();
    pruneCache(now);
    if (failedImages.get(key) > now) {
      return fallbackResponse(key, 'cached-failure');
    }

    try {
      const response = await nativeFetch(input, init);
      if (response && response.status >= 400) {
        failedImages.set(key, Date.now() + FAILURE_CACHE_TTL_MS);
        return fallbackResponse(key, `http-${response.status}`);
      }
      return response;
    } catch (error) {
      if (isAbortError(error)) throw error;
      failedImages.set(key, Date.now() + FAILURE_CACHE_TTL_MS);
      pruneCache(Date.now());
      return fallbackResponse(key, 'network-error');
    }
  }

  function installConsoleFilter() {
    if (nativeConsoleError) {
      guardConsoleError = function arenaImageGuardError(...args) {
        if (isRetryLog(args)) {
          suppressedLogs += 1;
          return;
        }
        return nativeConsoleError(...args);
      };
      console.error = guardConsoleError;
    }
    if (nativeConsoleWarn) {
      guardConsoleWarn = function arenaImageGuardWarn(...args) {
        if (isRetryLog(args)) {
          suppressedLogs += 1;
          return;
        }
        return nativeConsoleWarn(...args);
      };
      console.warn = guardConsoleWarn;
    }
  }

  function uninstall(options) {
    const opts = options && typeof options === 'object' ? options : {};
    if (window.fetch === guardedFetch && NativeFetch) window.fetch = NativeFetch;
    if (nativeConsoleError && console.error === guardConsoleError) console.error = nativeConsoleError;
    if (nativeConsoleWarn && console.warn === guardConsoleWarn) console.warn = nativeConsoleWarn;
    failedImages.clear();
    if (window.__arenaImageFailureGuard && window.__arenaImageFailureGuard.version === VERSION) {
      delete window.__arenaImageFailureGuard;
    }
    installed = false;
    if (!opts.quiet && nativeConsoleError) nativeConsoleError(LOG_PREFIX, 'uninstalled');
    return true;
  }

  function boot() {
    if (installed) return;
    installed = true;
    window.fetch = guardedFetch;
    installConsoleFilter();
    window.__arenaImageFailureGuard = {
      version: VERSION,
      status() {
        pruneCache(Date.now());
        return {
          version: VERSION,
          cachedFailures: failedImages.size,
          suppressedLogs,
          location: location.href,
        };
      },
      uninstall,
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
