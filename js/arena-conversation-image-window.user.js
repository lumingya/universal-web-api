// ==UserScript==
// @name         Arena.ai Conversation Image Window
// @namespace    local.codex.arena-conversation-image-window
// @version      1.0.0
// @description  Keeps only the four Arena conversation images nearest the viewport bottom loaded at a time.
// @match        https://arena.ai/*
// @run-at       document-start
// @inject-into  page
// @noframes
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  const VERSION = '1.0.0';
  const MAX_IMAGES = 4;
  const PLACEHOLDER_SRC = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==';
  const PLACEHOLDER_ATTR = 'data-uwa-image-window-placeholder';
  const ORIGINAL_SRC_ATTR = 'data-uwa-image-window-original-src';
  const MEDIA_HOST_SUFFIX = '.r2.cloudflarestorage.com';
  const INSTANCE_KEY = '__arenaConversationImageWindow';

  const existing = window[INSTANCE_KEY];
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

  const imagePrototype = window.HTMLImageElement && window.HTMLImageElement.prototype;
  if (!imagePrototype) return;

  const nativeSrcDescriptor = Object.getOwnPropertyDescriptor(imagePrototype, 'src');
  const nativeSrcsetDescriptor = Object.getOwnPropertyDescriptor(imagePrototype, 'srcset');
  const nativeSetAttribute = Element.prototype.setAttribute;
  const nativeRemoveAttribute = Element.prototype.removeAttribute;
  const originalSetAttribute = imagePrototype.setAttribute;

  if (!nativeSrcDescriptor || typeof nativeSrcDescriptor.set !== 'function') return;

  const records = new WeakMap();
  const trackedImages = new Set();
  const allowedImages = new Set();
  const bypassImages = new WeakSet();
  let observer = null;
  let scheduled = false;
  let installed = false;
  let maxImages = MAX_IMAGES;
  let guardedSrcDescriptor = null;
  let guardedSrcsetDescriptor = null;
  let guardedSetAttribute = null;

  function isConversationMediaUrl(value) {
    if (!value || value === PLACEHOLDER_SRC) return false;
    try {
      const url = new URL(String(value), location.href);
      return url.protocol === 'https:' && url.hostname.endsWith(MEDIA_HOST_SUFFIX);
    } catch (_) {
      return false;
    }
  }

  function srcsetHasConversationMedia(value) {
    return String(value || '').split(',').some(part => isConversationMediaUrl(part.trim().split(/\s+/)[0]));
  }

  function nativeSet(image, name, value) {
    if (image.getAttribute(name) === String(value)) return;
    bypassImages.add(image);
    try {
      nativeSetAttribute.call(image, name, value);
    } finally {
      bypassImages.delete(image);
    }
  }

  function nativeRemove(image, name) {
    if (!image.hasAttribute(name)) return;
    bypassImages.add(image);
    try {
      nativeRemoveAttribute.call(image, name);
    } finally {
      bypassImages.delete(image);
    }
  }

  function recordFor(image) {
    let record = records.get(image);
    if (!record) {
      record = {
        src: '',
        srcset: null,
        sizes: null,
        originalLoading: null,
        loadingCaptured: false,
      };
      records.set(image, record);
      trackedImages.add(image);
    }
    return record;
  }

  function captureSource(image, source, srcset) {
    const record = recordFor(image);
    if (isConversationMediaUrl(source)) record.src = String(source);
    if (srcsetHasConversationMedia(srcset)) record.srcset = String(srcset);
    if (record.sizes === null) record.sizes = image.getAttribute('sizes');
    if (!record.loadingCaptured) {
      record.originalLoading = image.getAttribute('loading');
      record.loadingCaptured = true;
    }
    return record;
  }

  function defer(image, source, srcset) {
    const record = captureSource(image, source, srcset);
    if (!record.src && !record.srcset) return false;

    allowedImages.delete(image);
    nativeSet(image, 'loading', 'lazy');
    if (record.srcset) nativeRemove(image, 'srcset');
    nativeSet(image, PLACEHOLDER_ATTR, 'true');
    if (record.src) nativeSet(image, ORIGINAL_SRC_ATTR, record.src);
    nativeSet(image, 'src', PLACEHOLDER_SRC);
    return true;
  }

  function restore(image, record) {
    if (!record || (!record.src && !record.srcset)) return;
    nativeRemove(image, PLACEHOLDER_ATTR);
    nativeRemove(image, ORIGINAL_SRC_ATTR);
    nativeSet(image, 'loading', 'eager');
    if (record.srcset) {
      nativeSet(image, 'srcset', record.srcset);
      if (record.sizes !== null) nativeSet(image, 'sizes', record.sizes);
    }
    if (record.src) {
      nativeSet(image, 'src', record.src);
    } else {
      nativeRemove(image, 'src');
    }
  }

  function block(image, record) {
    if (!record || (!record.src && !record.srcset)) return;
    nativeSet(image, 'loading', 'lazy');
    if (record.srcset) nativeRemove(image, 'srcset');
    nativeSet(image, PLACEHOLDER_ATTR, 'true');
    if (record.src) nativeSet(image, ORIGINAL_SRC_ATTR, record.src);
    nativeSet(image, 'src', PLACEHOLDER_SRC);
  }

  function unmanage(image) {
    if (!image) return;
    const record = records.get(image);
    if (record) {
      restore(image, record);
      records.delete(image);
      trackedImages.delete(image);
      allowedImages.delete(image);
      nativeRemove(image, PLACEHOLDER_ATTR);
      nativeRemove(image, ORIGINAL_SRC_ATTR);
    }
  }

  function nearestViewportBottomDistance(image) {
    const rect = image.getBoundingClientRect();
    const bottom = window.innerHeight || document.documentElement.clientHeight || 0;
    if (rect.top <= bottom && rect.bottom >= bottom) return 0;
    if (rect.top > bottom) return rect.top - bottom;
    return bottom - rect.bottom;
  }

  function collectConversationImages() {
    const candidates = [];
    for (const image of document.images) {
      let record = records.get(image);
      if (!record) {
        const src = image.getAttribute('src') || image.currentSrc || '';
        const srcset = image.getAttribute('srcset') || '';
        if (!isConversationMediaUrl(src) && !srcsetHasConversationMedia(srcset)) continue;
        defer(image, src, srcset);
        record = records.get(image);
      }
      if (record && (record.src || record.srcset)) {
        candidates.push({ image, record, distance: nearestViewportBottomDistance(image) });
      }
    }
    return candidates;
  }

  function refresh() {
    scheduled = false;
    if (!installed) return;

    const candidates = collectConversationImages();
    candidates.sort((left, right) => left.distance - right.distance);
    const nextAllowed = new Set(candidates.slice(0, maxImages).map(item => item.image));

    for (const image of allowedImages) {
      if (!nextAllowed.has(image)) block(image, records.get(image));
    }
    allowedImages.clear();

    for (const item of candidates) {
      if (!nextAllowed.has(item.image)) {
        block(item.image, item.record);
        continue;
      }
      restore(item.image, item.record);
      allowedImages.add(item.image);
    }
  }

  function scheduleRefresh() {
    if (scheduled || !installed) return;
    scheduled = true;
    requestAnimationFrame(refresh);
  }

  function onSourceWrite(image, name, value) {
    if (bypassImages.has(image)) {
      nativeSetAttribute.call(image, name, value);
      return;
    }

    const record = records.get(image);
    const isMedia = name === 'src' ? isConversationMediaUrl(value) : srcsetHasConversationMedia(value);
    if (!isMedia && !record) {
      nativeSetAttribute.call(image, name, value);
      return;
    }

    const source = name === 'src' ? value : record && record.src;
    const srcset = name === 'srcset' ? value : record && record.srcset;
    defer(image, source, srcset);
    scheduleRefresh();
  }

  function installImagePropertyGuards() {
    guardedSrcDescriptor = {
      configurable: true,
      enumerable: nativeSrcDescriptor.enumerable,
      get: nativeSrcDescriptor.get,
      set(value) {
        if (bypassImages.has(this)) return nativeSrcDescriptor.set.call(this, value);
        if (!isConversationMediaUrl(value) && !records.has(this)) {
          return nativeSrcDescriptor.set.call(this, value);
        }
        defer(this, value, records.get(this) && records.get(this).srcset);
        scheduleRefresh();
      },
    };
    Object.defineProperty(imagePrototype, 'src', guardedSrcDescriptor);

    if (nativeSrcsetDescriptor && typeof nativeSrcsetDescriptor.set === 'function') {
      guardedSrcsetDescriptor = {
        configurable: true,
        enumerable: nativeSrcsetDescriptor.enumerable,
        get: nativeSrcsetDescriptor.get,
        set(value) {
          if (bypassImages.has(this)) return nativeSrcsetDescriptor.set.call(this, value);
          if (!srcsetHasConversationMedia(value) && !records.has(this)) {
            return nativeSrcsetDescriptor.set.call(this, value);
          }
          defer(this, records.get(this) && records.get(this).src, value);
          scheduleRefresh();
        },
      };
      Object.defineProperty(imagePrototype, 'srcset', guardedSrcsetDescriptor);
    }

    guardedSetAttribute = function arenaConversationImageWindowSetAttribute(name, value) {
      const normalized = String(name || '').toLowerCase();
      if (normalized === 'src' || normalized === 'srcset') {
        return onSourceWrite(this, normalized, value);
      }
      return originalSetAttribute.call(this, name, value);
    };
    imagePrototype.setAttribute = guardedSetAttribute;
  }

  function installObserver() {
    observer = new MutationObserver(mutations => {
      for (const mutation of mutations) {
        if (mutation.type === 'childList' && mutation.addedNodes.length) {
          scheduleRefresh();
          return;
        }
        if (mutation.type === 'attributes' && mutation.target instanceof HTMLImageElement) {
          scheduleRefresh();
          return;
        }
      }
    });
    observer.observe(document, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['src', 'srcset'],
    });
  }

  function status() {
    return {
      version: VERSION,
      maxImages,
      trackedImages: Array.from(trackedImages).filter(image => image.isConnected).length,
      loadedImages: Array.from(allowedImages).filter(image => image.isConnected).length,
      location: location.href,
    };
  }

  function configure(options) {
    if (options && Number.isInteger(options.maxImages) && options.maxImages > 0) {
      maxImages = options.maxImages;
      scheduleRefresh();
    }
    return status();
  }

  function uninstall(options) {
    const quiet = !!(options && options.quiet);
    installed = false;
    if (observer) observer.disconnect();
    window.removeEventListener('scroll', scheduleRefresh, true);
    window.removeEventListener('resize', scheduleRefresh);
    document.removeEventListener('load', scheduleRefresh, true);

    const currentSrcDescriptor = Object.getOwnPropertyDescriptor(imagePrototype, 'src');
    if (currentSrcDescriptor && currentSrcDescriptor.set === guardedSrcDescriptor.set) {
      Object.defineProperty(imagePrototype, 'src', nativeSrcDescriptor);
    }
    const currentSrcsetDescriptor = Object.getOwnPropertyDescriptor(imagePrototype, 'srcset');
    if (nativeSrcsetDescriptor && currentSrcsetDescriptor && guardedSrcsetDescriptor
      && currentSrcsetDescriptor.set === guardedSrcsetDescriptor.set) {
      Object.defineProperty(imagePrototype, 'srcset', nativeSrcsetDescriptor);
    }
    if (imagePrototype.setAttribute === guardedSetAttribute) imagePrototype.setAttribute = originalSetAttribute;

    for (const image of trackedImages) {
      const record = records.get(image);
      if (!image.isConnected || !record) continue;
      restore(image, record);
      if (record.originalLoading === null) nativeRemove(image, 'loading');
      else nativeSet(image, 'loading', record.originalLoading);
    }
    allowedImages.clear();
    trackedImages.clear();
    if (window[INSTANCE_KEY] && window[INSTANCE_KEY].version === VERSION) delete window[INSTANCE_KEY];
    if (!quiet) console.info('[Arena Conversation Image Window] uninstalled');
    return true;
  }

  function boot() {
    if (installed) return;
    installed = true;
    installImagePropertyGuards();
    installObserver();
    window.addEventListener('scroll', scheduleRefresh, true);
    window.addEventListener('resize', scheduleRefresh);
    document.addEventListener('load', scheduleRefresh, true);
    window[INSTANCE_KEY] = { version: VERSION, status, configure, uninstall, unmanage };
    scheduleRefresh();
  }

  boot();
})();
