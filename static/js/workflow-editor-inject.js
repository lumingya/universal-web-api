/**
 * 可视化工作流编辑器 v2.0 - 简洁版
 * 特性：自动加载配置 + 元素定位 + 极简 UI
 */
(function() {
  'use strict';
  
  if (window.__WORKFLOW_EDITOR_INJECTED__) {
    console.log('[WorkflowEditor] 已存在，重新显示');
    window.WorkflowEditor?.show?.();
    return;
  }
  window.__WORKFLOW_EDITOR_INJECTED__ = true;
  
    // ========== 配置 ==========
    const TYPES = {
        COORD_CLICK: { color: 'rgba(249, 115, 22, 0.18)', border: '#F97316', name: '坐标点击' },
        COORD_SCROLL: { color: 'rgba(14, 165, 233, 0.18)', border: '#0EA5E9', name: '模拟滑动' },
        CLICK: { color: 'rgba(59, 130, 246, 0.15)', border: '#3B82F6', name: '点击' },
        INPUT: { color: 'rgba(16, 185, 129, 0.15)', border: '#10B981', name: '输入' },
        READ: { color: 'rgba(139, 92, 246, 0.15)', border: '#8B5CF6', name: '读取' },
        WAIT: { color: 'rgba(245, 158, 11, 0.18)', border: '#F59E0B', name: '等待' },
        KEY: { color: 'rgba(236, 72, 153, 0.18)', border: '#EC4899', name: '按键' },
        SCRIPT: { color: 'rgba(99, 102, 241, 0.18)', border: '#6366F1', name: '脚本' }
    };
    const VISUAL_ACTION_DEFS = [
        { ballType: 'COORD_CLICK', workflowAction: 'COORD_CLICK', toolbarAction: 'add-coord-click', toolbarLabel: '+ 坐标点击', menuLabel: '坐标点击' },
        { ballType: 'COORD_SCROLL', workflowAction: 'COORD_SCROLL', toolbarAction: 'add-coord-scroll', toolbarLabel: '+ 滑动', menuLabel: '滑动' },
        { ballType: 'CLICK', workflowAction: 'CLICK', toolbarAction: 'add-click', toolbarLabel: '+ 点击', menuLabel: '点击' },
        { ballType: 'INPUT', workflowAction: 'FILL_INPUT', toolbarAction: 'add-input', toolbarLabel: '+ 输入', menuLabel: '输入' },
        { ballType: 'READ', workflowAction: 'STREAM_WAIT', toolbarAction: 'add-read', toolbarLabel: '+ 读取', menuLabel: '读取' },
        { ballType: 'WAIT', workflowAction: 'WAIT', toolbarAction: 'add-wait', toolbarLabel: '+ 等待', menuLabel: '等待' },
        { ballType: 'KEY', workflowAction: 'KEY_PRESS', toolbarAction: 'add-key', toolbarLabel: '+ 按键', menuLabel: '按键' },
        { ballType: 'SCRIPT', workflowAction: 'JS_EXEC', toolbarAction: 'add-script', toolbarLabel: '+ 脚本', menuLabel: '脚本' }
    ];
    const VISUAL_ACTION_BY_TOOLBAR_ACTION = Object.fromEntries(
        VISUAL_ACTION_DEFS.map(def => [def.toolbarAction, def])
    );
    const SUPPORTED_VISUAL_WORKFLOW_ACTIONS = new Set(
        VISUAL_ACTION_DEFS.map(def => def.workflowAction)
    );

    // 🔧 后端 API 地址（从注入时传入，或使用默认值）
    const BALL_SIZE = 32;
    const BALL_RADIUS = BALL_SIZE / 2;
    const TEST_DIRECT_FETCH_TIMEOUT_MS = 1200;
    const TEST_ACTIVITY_TIMEOUT_MS = 60000;
    const getApiBase = () => window.__WORKFLOW_EDITOR_API_BASE__ || 'http://127.0.0.1:9099';
    const getCurrentTabId = () => window.__WORKFLOW_EDITOR_TAB_ID__ || '';

    const state = {
        steps: [],
        siteConfig: null,
        presetName: null,
        isPickingElement: false,
        pickingCallback: null,
        isVisible: true,
        pendingBackendActions: new Map(),
        cleanupHandlers: [],
        testExecution: {
          pendingCount: 0,
          shouldRestoreVisible: false,
          statusText: '',
          actionId: '',
          lastActivityAt: 0,
          watchdogTimer: null
        }
    };
  
  // ========== 样式 ==========
  function injectStyles() {
    if (document.getElementById('wfe-styles')) return;
    const style = document.createElement('style');
    style.id = 'wfe-styles';
    style.textContent = `
      .wfe-ball {
        position: fixed;
        width: 32px;
        height: 32px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: grab;
        z-index: 2147483640;
        border: 2px solid;
        transition: all 0.2s;
        font-family: system-ui, -apple-system, sans-serif;
        font-size: 13px;
        font-weight: 700;
      }
      .wfe-ball:hover { transform: scale(1.15); box-shadow: 0 0 12px rgba(0,0,0,0.2); }
      .wfe-ball.dragging { cursor: grabbing; transform: scale(1.2); }
      .wfe-ball.read-type { cursor: pointer; }
      .wfe-ball.warning {
        border-color: #dc2626 !important;
        background: rgba(220, 38, 38, 0.15) !important;
        animation: wfe-pulse 1.5s ease-in-out infinite;
      }
      .wfe-ball.scroll-end {
        border-radius: 8px;
      }
      .wfe-scroll-link {
        position: fixed;
        height: 4px;
        border-radius: 999px;
        transform-origin: 0 50%;
        pointer-events: none;
        z-index: 2147483639;
        box-shadow: 0 0 10px rgba(14, 165, 233, 0.45), 0 0 18px rgba(14, 165, 233, 0.22);
        background: linear-gradient(90deg, rgba(14, 165, 233, 0.92), rgba(56, 189, 248, 0.6));
      }
      .wfe-scroll-link.warning {
        background: linear-gradient(90deg, rgba(220, 38, 38, 0.92), rgba(248, 113, 113, 0.6));
        box-shadow: 0 0 10px rgba(220, 38, 38, 0.4), 0 0 18px rgba(220, 38, 38, 0.18);
      }
      .wfe-ball.warning::after {
        content: '⚠';
        position: absolute;
        top: -8px;
        right: -8px;
        font-size: 12px;
        background: #dc2626;
        color: white;
        border-radius: 50%;
        width: 16px;
        height: 16px;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      @keyframes wfe-pulse {
        0%, 100% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.4); }
        50% { box-shadow: 0 0 0 6px rgba(220, 38, 38, 0); }
      }

      .wfe-menu {
        position: fixed;
        background: white;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        box-shadow: 0 10px 40px rgba(0,0,0,0.15);
        z-index: 2147483645;
        min-width: 280px;
        font-family: system-ui, sans-serif;
        font-size: 13px;
        animation: wfe-fade 0.15s;
      }
      @keyframes wfe-fade { from { opacity: 0; transform: translateY(-4px); } }
      
      .wfe-menu-header {
        padding: 12px 14px;
        border-bottom: 1px solid #f3f4f6;
        background: #f9fafb;
      }
      .wfe-menu-title { font-weight: 600; font-size: 13px; color: #111827; }
      .wfe-menu-subtitle { font-size: 11px; color: #6b7280; margin-top: 2px; }
      
      .wfe-menu-body { padding: 6px 0; }
      .wfe-menu-item {
        padding: 8px 14px;
        display: flex;
        align-items: center;
        gap: 10px;
        transition: background 0.1s;
      }
      .wfe-menu-item:hover:not(.disabled) { background: #f9fafb; }
      .wfe-menu-item.disabled { opacity: 0.5; }
      .wfe-menu-item.clickable { cursor: pointer; }
      
      .wfe-menu-label { flex: 1; font-size: 12px; color: #374151; }
      .wfe-menu-input {
        border: 1px solid #d1d5db;
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 12px;
        width: 70px;
        text-align: center;
      }
      .wfe-menu-input:focus { outline: none; border-color: #3b82f6; }
      .wfe-menu-input.wide { width: 140px; text-align: left; }
      .wfe-menu-textarea {
        width: 100%;
        min-height: 108px;
        border: 1px solid #d1d5db;
        border-radius: 8px;
        padding: 8px 10px;
        font-size: 12px;
        line-height: 1.55;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        resize: vertical;
      }
      .wfe-menu-textarea:focus { outline: none; border-color: #3b82f6; }
      
      .wfe-divider { height: 1px; background: #f3f4f6; margin: 4px 0; }
      .wfe-menu-item.danger { color: #dc2626; }
      .wfe-menu-item.danger:hover { background: #fef2f2; }
      
      .wfe-toolbar {
        position: fixed;
        bottom: 20px;
        right: 20px;
        background: white;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 8px;
        z-index: 2147483638;
        display: flex;
        gap: 6px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        font-family: system-ui, sans-serif;
        align-items: center;
        user-select: none;
      }
      .wfe-toolbar-handle {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 34px;
        height: 34px;
        border-radius: 6px;
        color: #6b7280;
        cursor: grab;
      }
      .wfe-toolbar-handle:hover { background: #f3f4f6; color: #374151; }
      .wfe-toolbar-handle:active { cursor: grabbing; }
      .wfe-toolbar-handle-dots {
        width: 14px;
        height: 14px;
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 2px;
      }
      .wfe-toolbar-handle-dots span {
        width: 4px;
        height: 4px;
        border-radius: 999px;
        background: currentColor;
      }
      .wfe-toolbar-action-wrap {
        position: relative;
      }
      .wfe-toolbar-meta {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        margin-right: 2px;
      }
      .wfe-toolbar-badge {
        display: inline-flex;
        align-items: center;
        max-width: 220px;
        min-height: 28px;
        padding: 0 10px;
        border-radius: 999px;
        border: 1px solid #dbeafe;
        background: linear-gradient(135deg, #eff6ff, #f8fafc);
        color: #1d4ed8;
        font-size: 11px;
        font-weight: 600;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .wfe-toolbar-menu {
        position: absolute;
        left: 0;
        bottom: calc(100% + 8px);
        min-width: 168px;
        padding: 6px;
        background: white;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
        box-shadow: 0 12px 30px rgba(0,0,0,0.16);
        display: flex;
        flex-direction: column;
        gap: 4px;
        z-index: 2147483646;
        animation: wfe-fade 0.15s;
      }
      .wfe-toolbar-menu-item {
        border: 0;
        background: transparent;
        color: #374151;
        text-align: left;
        font-size: 12px;
        line-height: 1.4;
        padding: 8px 10px;
        border-radius: 8px;
        cursor: pointer;
        transition: background 0.12s, color 0.12s;
      }
      .wfe-toolbar-menu-item:hover {
        background: #f3f4f6;
        color: #111827;
      }
      
      .wfe-btn {
        padding: 6px 10px;
        border: 1px solid #e5e7eb;
        border-radius: 6px;
        background: white;
        cursor: pointer;
        font-size: 11px;
        font-weight: 500;
        transition: all 0.15s;
        color: #374151;
      }
      .wfe-btn:hover { background: #f9fafb; border-color: #d1d5db; transform: translateY(-1px); }
      .wfe-btn:active { transform: translateY(0); }
      .wfe-btn.primary { background: #3b82f6; color: white; border-color: #3b82f6; }
      .wfe-btn.primary:hover { background: #2563eb; }
      .wfe-btn.danger { color: #dc2626; }
      .wfe-btn.danger:hover { background: #fef2f2; }
      
      .wfe-pick-overlay {
        position: fixed;
        inset: 0;
        z-index: 2147483642;
        cursor: crosshair;
        background: rgba(0,0,0,0.02);
      }
      .wfe-highlight {
        outline: 2px solid #8b5cf6 !important;
        outline-offset: 2px !important;
        background: rgba(139,92,246,0.1) !important;
      }
      .wfe-pick-tip {
        position: fixed;
        top: 16px;
        left: 50%;
        transform: translateX(-50%);
        background: #8b5cf6;
        color: white;
        padding: 10px 20px;
        border-radius: 6px;
        font-size: 13px;
        font-weight: 500;
        z-index: 2147483646;
        box-shadow: 0 4px 16px rgba(139,92,246,0.3);
      }
      .wfe-toast {
        position: fixed;
        left: 50%;
        bottom: 90px;
        transform: translateX(-50%);
        max-width: min(78vw, 560px);
        padding: 10px 14px;
        border-radius: 10px;
        font-size: 12px;
        line-height: 1.5;
        color: white;
        background: rgba(17, 24, 39, 0.94);
        box-shadow: 0 10px 28px rgba(0,0,0,0.22);
        z-index: 2147483647;
        font-family: system-ui, sans-serif;
        animation: wfe-fade 0.15s;
      }
      .wfe-toast.success { background: rgba(5, 150, 105, 0.95); }
      .wfe-toast.error { background: rgba(220, 38, 38, 0.95); }
      .wfe-test-ring {
        position: fixed;
        width: 22px;
        height: 22px;
        margin-left: -11px;
        margin-top: -11px;
        border-radius: 999px;
        border: 2px solid rgba(56, 189, 248, 0.95);
        background: rgba(125, 211, 252, 0.18);
        box-shadow: 0 0 18px rgba(56, 189, 248, 0.45);
        z-index: 2147483646;
        pointer-events: none;
        animation: wfe-ring-pop 0.45s ease-out forwards;
      }
      .wfe-test-ring.error {
        border-color: rgba(248, 113, 113, 0.95);
        background: rgba(254, 202, 202, 0.18);
        box-shadow: 0 0 18px rgba(248, 113, 113, 0.45);
      }
      .wfe-test-status {
        position: fixed;
        right: 20px;
        bottom: 72px;
        display: inline-flex;
        align-items: center;
        gap: 10px;
        max-width: min(70vw, 420px);
        padding: 10px 14px;
        border-radius: 12px;
        border: 1px solid rgba(251, 191, 36, 0.35);
        background: rgba(17, 24, 39, 0.92);
        color: #f9fafb;
        box-shadow: 0 14px 36px rgba(0,0,0,0.28);
        z-index: 2147483646;
        font-family: system-ui, sans-serif;
        font-size: 12px;
        line-height: 1.45;
        pointer-events: none;
        animation: wfe-fade 0.15s;
      }
      .wfe-test-status-dot {
        width: 10px;
        height: 10px;
        border-radius: 999px;
        flex: 0 0 auto;
        background: #f59e0b;
        box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.38);
        animation: wfe-status-pulse 1.4s ease-in-out infinite;
      }
      .wfe-test-status-text {
        min-width: 0;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      @keyframes wfe-status-pulse {
        0%, 100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.38); }
        50% { box-shadow: 0 0 0 8px rgba(245, 158, 11, 0); }
      }
      @keyframes wfe-ring-pop {
        0% { transform: scale(0.55); opacity: 0.95; }
        100% { transform: scale(2.1); opacity: 0; }
      }
      
      .wfe-hidden { display: none !important; }
    `;
    document.head.appendChild(style);
  }

  function registerCleanup(fn) {
    if (typeof fn === 'function') {
      state.cleanupHandlers.push(fn);
    }
  }
  
  // ========== DOM 工具 ==========
  function el(tag, props = {}, children = []) {
    const element = document.createElement(tag);
    Object.entries(props).forEach(([k, v]) => {
      if (k === 'className') element.className = v;
      else if (k === 'style') Object.assign(element.style, v);
      else if (k.startsWith('data-')) element.setAttribute(k, v);
      else element[k] = v;
    });
    children.forEach(c => element.appendChild(typeof c === 'string' ? document.createTextNode(c) : c));
    return element;
  }
  
  function findElement(selector) {
    if (!selector) return null;
    try {
      const elements = document.querySelectorAll(selector);
      return elements.length > 0 ? elements[elements.length - 1] : null;
    } catch {
      return null;
    }
  }
  
  function getElementCenter(element) {
    if (!element) return { x: window.innerWidth / 2, y: window.innerHeight / 2 };
    const rect = element.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2
    };
  }
  
  function generateSelector(element) {
    if (!element || element === document.body) return 'body';
    
    if (element.id && !element.id.startsWith('wfe-')) {
      const sel = '#' + CSS.escape(element.id);
      if (document.querySelectorAll(sel).length === 1) return sel;
    }
    
    const testId = element.getAttribute('data-testid');
    if (testId) {
      const sel = `[data-testid="${testId}"]`;
      if (document.querySelectorAll(sel).length === 1) return sel;
    }
    
    if (element.className && typeof element.className === 'string') {
      const classes = element.className.split(' ')
        .filter(c => c && !c.startsWith('wfe-'))
        .slice(0, 2);
      if (classes.length > 0) {
        const sel = element.tagName.toLowerCase() + '.' + classes.join('.');
        if (document.querySelectorAll(sel).length === 1) return sel;
      }
    }
    
    return element.tagName.toLowerCase();
  }

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function showToast(message, type = 'info', duration = 2200) {
    const toast = el('div', { className: `wfe-toast ${type}` }, [String(message || '')]);
    document.body.appendChild(toast);
    window.setTimeout(() => toast.remove(), duration);
  }

  function shouldPreferBridgeMode() {
    try {
      const apiUrl = new URL(getApiBase(), window.location.href);
      return apiUrl.origin !== window.location.origin;
    } catch (_) {
      return false;
    }
  }

  function stopTestWatchdog() {
    if (state.testExecution.watchdogTimer) {
      window.clearInterval(state.testExecution.watchdogTimer);
      state.testExecution.watchdogTimer = null;
    }
  }

  function touchTestActivity() {
    state.testExecution.lastActivityAt = Date.now();
  }

  function startTestWatchdog() {
    touchTestActivity();
    stopTestWatchdog();
    state.testExecution.watchdogTimer = window.setInterval(() => {
      if (!state.testExecution.pendingCount) {
        stopTestWatchdog();
        return;
      }
      const inactiveFor = Date.now() - Number(state.testExecution.lastActivityAt || 0);
      if (inactiveFor < TEST_ACTIVITY_TIMEOUT_MS) {
        return;
      }
      console.debug('[WorkflowEditor] test watchdog timeout', {
        actionId: state.testExecution.actionId,
        inactiveFor
      });
      const staleActionId = String(state.testExecution.actionId || '');
      if (staleActionId) {
        state.pendingBackendActions.delete(staleActionId);
      }
      stopTestWatchdog();
      state.testExecution.actionId = '';
      showToast('测试状态长时间没有更新，已自动恢复编辑器。', 'error', 3600);
      resumeEditorAfterTest();
    }, 1000);
  }

  function showPointRing(x, y, isError = false) {
    const ring = el('div', {
      className: `wfe-test-ring${isError ? ' error' : ''}`,
      style: { left: `${x}px`, top: `${y}px` }
    });
    document.body.appendChild(ring);
    window.setTimeout(() => ring.remove(), 520);
  }

  function flashElement(element) {
    if (!element) return;
    element.classList.add('wfe-highlight');
    window.setTimeout(() => element.classList.remove('wfe-highlight'), 900);
  }

  function dispatchMouseSequence(target, x, y) {
    if (!target) return;
    const eventInit = {
      bubbles: true,
      cancelable: true,
      clientX: x,
      clientY: y,
      view: window
    };
    target.dispatchEvent(new MouseEvent('mousemove', eventInit));
    target.dispatchEvent(new MouseEvent('mousedown', eventInit));
    target.dispatchEvent(new MouseEvent('mouseup', eventInit));
    target.dispatchEvent(new MouseEvent('click', eventInit));
  }

  function resolveClickableTarget(element) {
    if (!element) return null;
    return element.closest(
      'button, a, summary, label, option, [role="button"], [role="menuitem"], [role="tab"], [role="option"], [aria-haspopup], input[type="button"], input[type="submit"], input[type="checkbox"], input[type="radio"], [tabindex]'
    ) || element;
  }

  function activateElement(target, x, y) {
    if (!target) return false;
    const clickable = resolveClickableTarget(target);
    clickable.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
    clickable.focus?.({ preventScroll: true });

    try {
      if (typeof clickable.click === 'function') {
        clickable.click();
        return true;
      }
    } catch (_) {}

    try {
      if (typeof PointerEvent === 'function') {
        const pointerInit = {
          bubbles: true,
          cancelable: true,
          clientX: x,
          clientY: y,
          pointerId: 1,
          pointerType: 'mouse',
          isPrimary: true,
          view: window
        };
        clickable.dispatchEvent(new PointerEvent('pointerdown', pointerInit));
        clickable.dispatchEvent(new PointerEvent('pointerup', pointerInit));
      }
    } catch (_) {}

    dispatchMouseSequence(clickable, x, y);
    return true;
  }

  function setElementTextValue(element, text) {
    const nextText = String(text || '');
    if (!element) return false;

    if ('value' in element) {
      element.focus?.();
      element.value = nextText;
      element.dispatchEvent(new Event('input', { bubbles: true }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }

    if (element.isContentEditable) {
      element.focus?.();
      element.textContent = nextText;
      element.dispatchEvent(new InputEvent('input', { bubbles: true, data: nextText, inputType: 'insertText' }));
      return true;
    }

    return false;
  }

  function enqueueBackendAction(type, payload, options = {}) {
    const queue = Array.isArray(window.__WORKFLOW_EDITOR_PENDING_ACTIONS__)
      ? window.__WORKFLOW_EDITOR_PENDING_ACTIONS__
      : [];
      const trackTestStatus = options.trackTestStatus !== false && String(type || '') === 'test_workflow';
      const action = {
        id: `wfe_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        type: String(type || ''),
        payload: payload || {},
        created_at: Date.now()
      };
      queue.push(action);
      window.__WORKFLOW_EDITOR_PENDING_ACTIONS__ = queue;
      state.pendingBackendActions.set(action.id, action);
      console.debug('[WorkflowEditor] queued backend action', {
        id: action.id,
        type: action.type,
        queueLength: queue.length,
        payload,
        trackTestStatus
      });
      if (trackTestStatus) {
        state.testExecution.actionId = action.id;
        updateTestingStatus(`已提交测试请求，等待本地控制台接管 · ${getToolbarPresetText()}`);
      }
      return action;
  }

  function buildWorkflowPayload(stepSubset = null) {
    if (!state.siteConfig) {
      state.siteConfig = { selectors: {}, workflow: [] };
    }

    const steps = Array.isArray(stepSubset) ? stepSubset : state.steps;
    const selectors = { ...(state.siteConfig.selectors || {}) };
    const newWorkflow = [];

    steps.forEach((ball) => {
      const delayMs = ball.config.delay_ms || 0;
      const targetKey = ball.type === 'CLICK'
        ? normalizeKey(ball.config.targetKey || '')
        : ['INPUT', 'READ'].includes(ball.type)
          ? ensureBallTargetKey(ball, selectors)
          : '';

      if (delayMs > 0) {
        newWorkflow.push({
          action: 'WAIT',
          target: '',
          optional: false,
          value: delayMs / 1000
        });
      }

      if (ball.type === 'WAIT') {
        newWorkflow.push({
          action: 'WAIT',
          target: '',
          optional: !!ball.config.optional,
          value: Number(ball.config.wait_seconds || 0)
        });
      } else if (ball.type === 'CLICK') {
        if (ball.config.selector && targetKey) {
          selectors[targetKey] = ball.config.selector;
        }
        newWorkflow.push({
          action: 'CLICK',
          target: targetKey || '',
          optional: !!ball.config.optional,
          value: null
        });
      } else if (ball.type === 'COORD_CLICK') {
        newWorkflow.push({
          action: 'COORD_CLICK',
          target: '',
          optional: !!ball.config.optional,
          value: {
            x: Math.round(ball.x + BALL_RADIUS),
            y: Math.round(ball.y + BALL_RADIUS),
            random_radius: Number(ball.config.random_radius || 0)
          }
        });
      } else if (ball.type === 'COORD_SCROLL') {
        newWorkflow.push({
          action: 'COORD_SCROLL',
          target: '',
          optional: !!ball.config.optional,
          value: {
            start_x: Math.round(ball.x + BALL_RADIUS),
            start_y: Math.round(ball.y + BALL_RADIUS),
            end_x: Number(ball.config.endX || 0),
            end_y: Number(ball.config.endY || 0)
          }
        });
      } else if (ball.type === 'INPUT') {
        newWorkflow.push({
          action: 'FILL_INPUT',
          target: targetKey || 'input_box',
          optional: !!ball.config.optional,
          value: ball.config.text || null
        });
      } else if (ball.type === 'KEY') {
        newWorkflow.push({
          action: 'KEY_PRESS',
          target: String(ball.config.key || '').trim() || 'Enter',
          optional: !!ball.config.optional,
          value: null
        });
      } else if (ball.type === 'SCRIPT') {
        newWorkflow.push({
          action: 'JS_EXEC',
          target: '',
          optional: !!ball.config.optional,
          value: String(ball.config.script || '').trim() || 'return document.title;'
        });
      } else if (ball.type === 'READ') {
        newWorkflow.push({
          action: 'STREAM_WAIT',
          target: targetKey || 'result_container',
          optional: !!ball.config.optional,
          value: null
        });
      }
    });

    return { workflow: newWorkflow, selectors };
  }

  function getWorkflowPromptFromPayload(payload) {
    const steps = Array.isArray(payload?.workflow) ? payload.workflow : [];
    for (let i = steps.length - 1; i >= 0; i -= 1) {
      const step = steps[i];
      if (step?.action === 'FILL_INPUT' && step.value != null) {
        return String(step.value);
      }
    }
    return '';
  }

  function getCurrentPresetName() {
    return window.__WORKFLOW_EDITOR_PRESET_NAME__ || state.presetName || '主预设';
  }

  function getToolbarPresetText() {
    return `预设：${getCurrentPresetName()}`;
  }

  function getTestingStatusText(explicitText = '') {
    return explicitText || state.testExecution.statusText || `测试中 · ${getToolbarPresetText()}`;
  }

  function refreshToolbarMeta() {
    const toolbarText = getToolbarPresetText();
    if (toolbarPresetBadge) {
      toolbarPresetBadge.textContent = toolbarText;
      toolbarPresetBadge.title = getCurrentPresetName();
    }
    if (testingStatusText) {
      const testingText = getTestingStatusText();
      testingStatusText.textContent = testingText;
      testingStatusText.title = testingText;
    }
  }

  function updateTestingStatus(text = '', toastType = '', toastDuration = 0) {
    state.testExecution.statusText = getTestingStatusText(text);
    touchTestActivity();
    if (!testingStatus) {
      showTestingStatus(state.testExecution.statusText);
    } else {
      refreshToolbarMeta();
    }
    if (toastType) {
      showToast(state.testExecution.statusText, toastType, toastDuration || 1800);
    }
  }

  function showTestingStatus(text = '') {
    state.testExecution.statusText = getTestingStatusText(text);
    touchTestActivity();
    if (!testingStatus) {
      testingStatusText = el('div', { className: 'wfe-test-status-text' }, [state.testExecution.statusText]);
      testingStatus = el('div', { className: 'wfe-test-status', id: 'wfe-test-status' }, [
        el('div', { className: 'wfe-test-status-dot' }),
        testingStatusText
      ]);
      document.body.appendChild(testingStatus);
    } else {
      testingStatus.classList.remove('wfe-hidden');
    }
    refreshToolbarMeta();
  }

  function hideTestingStatus() {
    state.testExecution.statusText = '';
    state.testExecution.actionId = '';
    stopTestWatchdog();
    testingStatus?.remove();
    testingStatus = null;
    testingStatusText = null;
  }

  function suspendEditorForTest(statusText = '') {
    state.testExecution.statusText = getTestingStatusText(statusText);
    state.testExecution.actionId = '';
    if (state.testExecution.pendingCount === 0) {
      state.testExecution.shouldRestoreVisible = !!state.isVisible;
      if (state.isVisible) {
        hideEditor();
      }
    }
    state.testExecution.pendingCount += 1;
    showTestingStatus(state.testExecution.statusText);
    startTestWatchdog();
    console.debug('[WorkflowEditor] suspendEditorForTest', {
      pendingCount: state.testExecution.pendingCount,
      shouldRestoreVisible: state.testExecution.shouldRestoreVisible,
      statusText: state.testExecution.statusText
    });
  }

  function resumeEditorAfterTest() {
    if (state.testExecution.pendingCount > 0) {
      state.testExecution.pendingCount -= 1;
    }

    const shouldRestore =
      state.testExecution.pendingCount === 0
      && state.testExecution.shouldRestoreVisible;

    if (state.testExecution.pendingCount === 0) {
      hideTestingStatus();
    }

    if (shouldRestore) {
      state.testExecution.shouldRestoreVisible = false;
      showEditor();
    }

    console.debug('[WorkflowEditor] resumeEditorAfterTest', {
      pendingCount: state.testExecution.pendingCount,
      restored: shouldRestore
    });
  }

  async function runWorkflowTest(stepSubset = null) {
    const steps = Array.isArray(stepSubset) ? stepSubset : state.steps;
    if (!steps.length) {
      showToast('当前没有可测试的步骤', 'error');
      return;
    }

    const payload = buildWorkflowPayload(steps);
    const domain = window.location.hostname;
    const presetName = getCurrentPresetName();
    const siteConfig = state.siteConfig || {};
    const statusText = Array.isArray(stepSubset)
      ? `测试单步 · ${getToolbarPresetText()}`
      : `测试整体 · ${getToolbarPresetText()}`;

    closeToolbarActionMenu();
    hideMenu();
    suspendEditorForTest(statusText);
    showToast(
      Array.isArray(stepSubset)
        ? `开始测试步骤，共 ${payload.workflow.length} 个动作`
        : `开始整体测试，共 ${payload.workflow.length} 个动作`,
      'info',
      1400
    );

    const requestPayload = {
      domain,
      tab_id: getCurrentTabId(),
      preset_name: presetName,
      prompt: getWorkflowPromptFromPayload(payload),
      workflow: payload.workflow,
      selectors: payload.selectors,
      stealth: !!siteConfig.stealth,
      stream_config: siteConfig.stream_config || {},
      image_extraction: siteConfig.image_extraction || {},
      file_paste: siteConfig.file_paste || {}
    };

    console.debug('[WorkflowEditor] runWorkflowTest', {
      stepCount: payload.workflow.length,
      stepSubset: Array.isArray(stepSubset),
      requestPayload
    });

    if (shouldPreferBridgeMode()) {
      const action = enqueueBackendAction('test_workflow', requestPayload);
      console.debug('[WorkflowEditor] using bridge mode directly', { actionId: action.id });
      showToast('测试请求已提交，本地控制台将直接执行。', 'info', 2200);
      return action;
    }

    try {
      const controller = typeof AbortController === 'function' ? new AbortController() : null;
      const timeoutId = controller
        ? window.setTimeout(() => controller.abort(), TEST_DIRECT_FETCH_TIMEOUT_MS)
        : null;
      const response = await fetch(`${getApiBase()}/api/workflow-editor/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestPayload),
        signal: controller?.signal
      });
      if (timeoutId) {
        window.clearTimeout(timeoutId);
      }

      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.success) {
        throw new Error(result.detail || result.message || `HTTP ${response.status}`);
      }

      console.debug('[WorkflowEditor] direct test success', result);
      showToast(result.message || '测试完成', 'success', 3200);
      resumeEditorAfterTest();
    } catch (error) {
      const message = String(error?.message || error || '');
      const shouldUseBridge =
        /Failed to fetch/i.test(message)
        || /Content Security Policy/i.test(message)
        || /NetworkError/i.test(message);
      const isDirectTimeout = /direct_test_timeout/i.test(message) || /AbortError/i.test(message);

      if (!shouldUseBridge && !isDirectTimeout) {
        console.debug('[WorkflowEditor] direct test failed without bridge fallback', error);
        resumeEditorAfterTest();
        throw error;
      }

      const action = enqueueBackendAction('test_workflow', requestPayload);
      console.debug('[WorkflowEditor] switched to bridge mode', { actionId: action.id, message, isDirectTimeout });
      showToast('测试请求已提交，等待本地控制台执行。', 'info', 2200);
      return action;
    }
  }

  async function testBall(ball) {
    await runWorkflowTest([ball]);
  }

  async function testAllSteps() {
    await runWorkflowTest();
  }
  
  function normalizeKey(value) {
    return String(value || '')
      .trim()
      .replace(/[^\w\u4e00-\u9fa5-]+/g, '_')
      .replace(/^_+|_+$/g, '');
  }

  function findSelectorKeyByValue(selectors, selector) {
    if (!selector) return '';
    for (const [key, value] of Object.entries(selectors || {})) {
      if (value === selector) return key;
    }
    return '';
  }

  function generateTargetKey(type, selectors, preferred) {
    const used = selectors || {};
    const normalizedPreferred = normalizeKey(preferred);
    if (normalizedPreferred) {
      return normalizedPreferred;
    }

    const base =
      type === 'INPUT' ? 'input_box' :
      type === 'READ' ? 'result_container' :
      'click_target';

    if (!used[base]) {
      return base;
    }

    let index = 1;
    while (used[`${base}_${index}`]) {
      index += 1;
    }
    return `${base}_${index}`;
  }

  function ensureBallTargetKey(ball, selectors) {
    if (!ball.config.selector) {
      return ball.config.targetKey || '';
    }

    const existingKey = findSelectorKeyByValue(selectors, ball.config.selector);
    if (existingKey) {
      ball.config.targetKey = existingKey;
      return existingKey;
    }

    const resolvedKey = generateTargetKey(ball.type, selectors, ball.config.targetKey);
    ball.config.targetKey = resolvedKey;
    selectors[resolvedKey] = ball.config.selector;
    return resolvedKey;
  }
  
  // ========== 小球类 ==========
    class Ball {
        constructor(opts) {
            this.id = 'b' + Date.now() + Math.random().toString(36).slice(2, 7);
            this.type = opts.type;
            this.seq = opts.seq;
            this.x = opts.x ?? 100;
            this.y = opts.y ?? 100;
            this.config = {
                delay_ms: opts.seq === 1 ? 0 : 1000,
                random_radius: 10,
                endX: '',
                endY: '',
                text: '',
                wait_seconds: 1,
                key: 'Enter',
                script: 'return document.title;',
                selector: '',
                targetKey: '',
                optional: false,
                ...opts.config
            };
            if (this.type === 'WAIT' && opts.config?.delay_ms == null) {
                this.config.delay_ms = 0;
            }

            this.element = null;
            this.endElement = null;
            this.connectionElement = null;
            this.dragAnchor = null;
            this.offset = { x: 0, y: 0 };
            this.isWarning = false;       // 警告状态
            this.warningMessage = '';     // 警告信息

            this.render();
            this.bind();
            this.updateSeq(this.seq);

            // 不在构造函数中自动定位，由 addBall 统一处理
        }
    
      render() {
          const tc = TYPES[this.type];
          const selectorHint = this.config.selector ? ` → ${this.config.selector.slice(0, 30)}` : '';
          if (this.type === 'COORD_SCROLL') {
              this.connectionElement = el('div', {
                  className: 'wfe-scroll-link',
                  'data-ball-id': this.id
              });
              this.element = el('div', {
                  className: 'wfe-ball scroll-start',
                  style: {
                      background: tc.color,
                      borderColor: tc.border,
                      color: tc.border
                  },
                  'data-ball-id': this.id,
                  title: `#${this.seq} ${tc.name}${selectorHint}`
              });
              this.endElement = el('div', {
                  className: 'wfe-ball scroll-end',
                  style: {
                      background: 'rgba(255,255,255,0.92)',
                      borderColor: tc.border,
                      color: tc.border
                  },
                  'data-ball-id': this.id,
                  title: `#${this.seq} ${tc.name}${selectorHint}`
              });

              document.body.append(this.connectionElement, this.element, this.endElement);
              this.move(this.x, this.y);
              this.moveEnd((Number(this.config.endX) || (this.x + BALL_RADIUS)) - BALL_RADIUS, (Number(this.config.endY) || (this.y + BALL_RADIUS)) - BALL_RADIUS);
              return;
          }

          this.element = el('div', {
              className: 'wfe-ball' + (this.type === 'READ' ? ' read-type' : ''),
              style: {
                  background: tc.color,
                  borderColor: tc.border,
                  color: tc.border,
                  left: this.x + 'px',
                  top: this.y + 'px'
              },
              'data-ball-id': this.id,
              title: `#${this.seq} ${tc.name}${selectorHint}`
          }, [String(this.seq)]);

          document.body.appendChild(this.element);
      }
    
    bind() {
      this.element.addEventListener('mousedown', (e) => {
        if (e.button !== 0 || this.type === 'READ') return;
        this.startDrag('start', e);
        e.preventDefault();
        e.stopPropagation();
      });
      
      this.element.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        e.stopPropagation();
        showMenu(this, e.clientX, e.clientY);
      });
      
      if (this.type === 'READ') {
        this.element.addEventListener('click', () => {
          startPicker(this);
        });
      }

      if (this.endElement) {
        this.endElement.addEventListener('mousedown', (e) => {
          if (e.button !== 0) return;
          this.startDrag('end', e);
          e.preventDefault();
          e.stopPropagation();
        });
        this.endElement.addEventListener('contextmenu', (e) => {
          e.preventDefault();
          e.stopPropagation();
          showMenu(this, e.clientX, e.clientY);
        });
      }
    }

    startDrag(anchor, e) {
      this.dragAnchor = anchor;
      const left = anchor === 'end' ? this.getEndLeft() : this.x;
      const top = anchor === 'end' ? this.getEndTop() : this.y;
      const activeElement = anchor === 'end' ? this.endElement : this.element;
      activeElement?.classList.add('dragging');
      this.offset = { x: e.clientX - left, y: e.clientY - top };
    }

    stopDrag() {
      this.dragAnchor = null;
      this.element?.classList.remove('dragging');
      this.endElement?.classList.remove('dragging');
    }

    getEndLeft() {
      return Number(this.config.endX || 0) - BALL_RADIUS;
    }

    getEndTop() {
      return Number(this.config.endY || 0) - BALL_RADIUS;
    }

    getStartCenter() {
      return { x: this.x + BALL_RADIUS, y: this.y + BALL_RADIUS };
    }

    getEndCenter() {
      return {
        x: Number(this.config.endX || 0),
        y: Number(this.config.endY || 0)
      };
    }

    syncScrollVisuals() {
      if (this.type !== 'COORD_SCROLL' || !this.connectionElement) return;
      const start = this.getStartCenter();
      const end = this.getEndCenter();
      const dx = end.x - start.x;
      const dy = end.y - start.y;
      const distance = Math.max(4, Math.sqrt(dx * dx + dy * dy));
      const angle = Math.atan2(dy, dx) * 180 / Math.PI;

      this.connectionElement.style.left = `${start.x}px`;
      this.connectionElement.style.top = `${start.y - 2}px`;
      this.connectionElement.style.width = `${distance}px`;
      this.connectionElement.style.transform = `rotate(${angle}deg)`;
    }
    
    move(x, y) {
      this.x = Math.max(0, Math.min(window.innerWidth - BALL_SIZE, x));
      this.y = Math.max(0, Math.min(window.innerHeight - BALL_SIZE, y));
      this.element.style.left = this.x + 'px';
      this.element.style.top = this.y + 'px';
      this.syncScrollVisuals();
    }

    moveEnd(x, y) {
      const nextX = Math.max(0, Math.min(window.innerWidth - BALL_SIZE, x));
      const nextY = Math.max(0, Math.min(window.innerHeight - BALL_SIZE, y));
      this.config.endX = nextX + BALL_RADIUS;
      this.config.endY = nextY + BALL_RADIUS;
      if (this.endElement) {
        this.endElement.style.left = `${nextX}px`;
        this.endElement.style.top = `${nextY}px`;
      }
      this.syncScrollVisuals();
    }
    
      updateSeq(n) {
          this.seq = n;
          if (this.type === 'COORD_SCROLL') {
              this.element.textContent = `S${n}`;
              this.endElement.textContent = `E${n}`;
          } else if (this.type === 'WAIT') {
              this.element.textContent = `W${n}`;
          } else if (this.type === 'KEY') {
              this.element.textContent = `K${n}`;
          } else if (this.type === 'SCRIPT') {
              this.element.textContent = `J${n}`;
          } else {
              this.element.textContent = String(n);
          }
          const selectorHint = this.config.selector ? ` → ${this.config.selector.slice(0, 30)}` : '';
          if (this.type === 'COORD_SCROLL') {
              this.element.title = `S${n} ${TYPES[this.type].name} 起点${selectorHint}`;
              this.endElement.title = `E${n} ${TYPES[this.type].name} 终点${selectorHint}`;
          } else {
              this.element.title = `#${n} ${TYPES[this.type].name}${selectorHint}`;
          }
          if (n === 1) this.config.delay_ms = 0;
      }
    
    locateToElement() {
      const target = findElement(this.config.selector);
      if (target) {
        const pos = getElementCenter(target);
        this.move(pos.x - BALL_RADIUS, pos.y - BALL_RADIUS);
      }
    }

        setWarning(message) {
            this.isWarning = true;
            this.warningMessage = message;
            this.element?.classList.add('warning');
            this.endElement?.classList.add('warning');
            this.connectionElement?.classList.add('warning');
            // 更新 title 显示警告信息
            const tc = TYPES[this.type];
            if (this.type === 'COORD_SCROLL') {
                this.element.title = `⚠️ S${this.seq} ${tc.name} 起点 - ${message}`;
                this.endElement.title = `⚠️ E${this.seq} ${tc.name} 终点 - ${message}`;
            } else {
                this.element.title = `⚠️ #${this.seq} ${tc.name} - ${message}`;
            }
        }

        clearWarning() {
            this.isWarning = false;
            this.warningMessage = '';
            this.element?.classList.remove('warning');
            this.endElement?.classList.remove('warning');
            this.connectionElement?.classList.remove('warning');
            this.updateSeq(this.seq); // 恢复正常 title
        }

    setHidden(hidden) {
      const method = hidden ? 'add' : 'remove';
      this.connectionElement?.classList[method]('wfe-hidden');
      this.element?.classList[method]('wfe-hidden');
      this.endElement?.classList[method]('wfe-hidden');
    }

    destroy() {
      this.connectionElement?.remove();
      this.element?.remove();
      this.endElement?.remove();
    }
    
    toJSON() {
      const data = {
        seq: this.seq,
        type: this.type.toLowerCase(),
        delay_ms: this.config.delay_ms
      };
      
      if (this.type === 'CLICK' || this.type === 'COORD_CLICK') {
        data.x = Math.round(this.x + BALL_RADIUS);
        data.y = Math.round(this.y + BALL_RADIUS);
        data.random_radius = this.config.random_radius;
      } else if (this.type === 'COORD_SCROLL') {
        data.start_x = Math.round(this.x + BALL_RADIUS);
        data.start_y = Math.round(this.y + BALL_RADIUS);
        data.end_x = Number(this.config.endX || 0);
        data.end_y = Number(this.config.endY || 0);
      } else if (this.type === 'INPUT') {
        data.x = Math.round(this.x + BALL_RADIUS);
        data.y = Math.round(this.y + BALL_RADIUS);
        data.text = this.config.text;
      } else if (this.type === 'WAIT') {
        data.wait_seconds = Number(this.config.wait_seconds || 0);
      } else if (this.type === 'KEY') {
        data.key = this.config.key;
      } else if (this.type === 'SCRIPT') {
        data.script = this.config.script;
      } else if (this.type === 'READ') {
        data.selector = this.config.selector;
      }
      
      return data;
    }
  }
  
  // ========== 全局拖拽 ==========
  document.addEventListener('mousemove', (e) => {
    const ball = state.steps.find(b => b.dragAnchor);
    if (!ball) return;
    if (ball.dragAnchor === 'end') {
      ball.moveEnd(e.clientX - ball.offset.x, e.clientY - ball.offset.y);
    } else {
      ball.move(e.clientX - ball.offset.x, e.clientY - ball.offset.y);
    }
  }, true);
  
  document.addEventListener('mouseup', () => {
    state.steps.forEach(b => {
      if (b.dragAnchor) {
        b.stopDrag();
      }
    });
  }, true);
  
  // ========== 右键菜单 ==========
  let currentMenu = null;
  
  function showMenu(ball, x, y) {
    hideMenu();
    
    const tc = TYPES[ball.type];
    const menu = el('div', { className: 'wfe-menu' });
    if (ball.type === 'SCRIPT') {
      menu.style.minWidth = '360px';
    }
    
    menu.appendChild(el('div', { className: 'wfe-menu-header' }, [
      el('div', { className: 'wfe-menu-title' }, [`步骤 #${ball.seq}：${tc.name}`]),
      el('div', { className: 'wfe-menu-subtitle' }, [
        ball.type === 'CLICK' ? '在此坐标模拟鼠标点击' :
        ball.type === 'COORD_SCROLL' ? '从起点滚轮滑动到终点坐标' :
        ball.type === 'INPUT' ? '在此位置输入文本内容' :
        '提取特定元素的文本'
      ])
    ]));
    
    const body = el('div', { className: 'wfe-menu-body' });
    
    // 延迟
    if (ball.seq === 1) {
      body.appendChild(el('div', { className: 'wfe-menu-item disabled' }, [
        el('span', { className: 'wfe-menu-label' }, ['⚡ 起始步骤 (无延迟)'])
      ]));
    } else {
      const delayInput = el('input', {
        type: 'number',
        className: 'wfe-menu-input',
        value: ball.config.delay_ms,
        min: 0,
        step: 100
      });
      delayInput.addEventListener('change', () => ball.config.delay_ms = parseInt(delayInput.value) || 0);
      delayInput.addEventListener('click', e => e.stopPropagation());
      
      body.appendChild(el('div', { className: 'wfe-menu-item' }, [
        el('span', { className: 'wfe-menu-label' }, ['⏱️ 距上一步间隔 (ms)']),
        delayInput
      ]));
    }
    
    body.appendChild(el('div', { className: 'wfe-divider' }));
    
    // 类型特定
    if (['CLICK', 'INPUT', 'READ'].includes(ball.type)) {
      const keyInput = el('input', {
        type: 'text',
        className: 'wfe-menu-input wide',
        value: ball.config.targetKey || '',
        placeholder: 'selector_key'
      });
      keyInput.addEventListener('input', () => {
        const normalized = normalizeKey(keyInput.value);
        ball.config.targetKey = normalized;
        keyInput.value = normalized;
      });
      keyInput.addEventListener('click', e => e.stopPropagation());
      body.appendChild(el('div', { className: 'wfe-menu-item' }, [
        el('span', { className: 'wfe-menu-label' }, ['Key']),
        keyInput
      ]));
    }

    const optionalInput = el('input', {
      type: 'checkbox',
      checked: !ball.config.optional,
      title: '勾选后找不到元素会报错；不勾选则跳过该步骤'
    });
    optionalInput.addEventListener('change', () => ball.config.optional = !optionalInput.checked);
    optionalInput.addEventListener('click', e => e.stopPropagation());
    body.appendChild(el('div', { className: 'wfe-menu-item' }, [
      el('span', { className: 'wfe-menu-label' }, ['必需步骤']),
      optionalInput
    ]));

    body.appendChild(el('div', { className: 'wfe-divider' }));

    if (ball.type === 'WAIT') {
      const waitInput = el('input', {
        type: 'number',
        className: 'wfe-menu-input',
        value: Number(ball.config.wait_seconds || 0),
        min: 0,
        step: 0.1
      });
      waitInput.addEventListener('change', () => {
        ball.config.wait_seconds = parseFloat(waitInput.value) || 0;
      });
      waitInput.addEventListener('click', e => e.stopPropagation());
      body.appendChild(el('div', { className: 'wfe-menu-item' }, [
        el('span', { className: 'wfe-menu-label' }, ['⏳ 等待时长 (秒)']),
        waitInput
      ]));
    } else if (ball.type === 'CLICK' || ball.type === 'COORD_CLICK') {
      const radiusInput = el('input', {
        type: 'number',
        className: 'wfe-menu-input',
        value: ball.config.random_radius,
        min: 0,
        max: 50
      });
      radiusInput.addEventListener('change', () => ball.config.random_radius = parseInt(radiusInput.value) || 0);
      radiusInput.addEventListener('click', e => e.stopPropagation());
      
      body.appendChild(el('div', { className: 'wfe-menu-item' }, [
        el('span', { className: 'wfe-menu-label' }, ['🎯 随机范围 (px)']),
        radiusInput
      ]));
      body.appendChild(el('div', { className: 'wfe-menu-item disabled' }, [
        el('span', { className: 'wfe-menu-label' }, [`📍 坐标: (${Math.round(ball.x + BALL_RADIUS)}, ${Math.round(ball.y + BALL_RADIUS)})`])
      ]));
      if (ball.type === 'CLICK') {
        body.appendChild(el('div', { className: 'wfe-menu-item disabled' }, [
          el('span', { className: 'wfe-menu-label' }, [`Selector: ${ball.config.selector || '(unset)'}`])
        ]));
        const clickPickBtn = el('div', { className: 'wfe-menu-item clickable' }, [
          el('span', { className: 'wfe-menu-label', style: { color: '#8b5cf6' } }, ['Pick element'])
        ]);
        clickPickBtn.addEventListener('click', () => {
          hideMenu();
          startPicker(ball);
        });
        body.appendChild(clickPickBtn);
      }
    } else if (ball.type === 'COORD_SCROLL') {
      const endXInput = el('input', {
        type: 'number',
        className: 'wfe-menu-input',
        value: ball.config.endX ?? '',
        placeholder: 'end x'
      });
      endXInput.addEventListener('change', () => ball.config.endX = parseInt(endXInput.value) || 0);
      endXInput.addEventListener('click', e => e.stopPropagation());

      const endYInput = el('input', {
        type: 'number',
        className: 'wfe-menu-input',
        value: ball.config.endY ?? '',
        placeholder: 'end y'
      });
      endYInput.addEventListener('change', () => ball.config.endY = parseInt(endYInput.value) || 0);
      endYInput.addEventListener('click', e => e.stopPropagation());

      body.appendChild(el('div', { className: 'wfe-menu-item' }, [
        el('span', { className: 'wfe-menu-label' }, ['终点 X']),
        endXInput
      ]));
      body.appendChild(el('div', { className: 'wfe-menu-item' }, [
        el('span', { className: 'wfe-menu-label' }, ['终点 Y']),
        endYInput
      ]));
      body.appendChild(el('div', { className: 'wfe-menu-item disabled' }, [
        el('span', { className: 'wfe-menu-label' }, [`起点: (${Math.round(ball.x + BALL_RADIUS)}, ${Math.round(ball.y + BALL_RADIUS)})`])
      ]));
      body.appendChild(el('div', { className: 'wfe-menu-item disabled' }, [
        el('span', { className: 'wfe-menu-label' }, [`终点: (${Number(ball.config.endX || 0)}, ${Number(ball.config.endY || 0)})`])
      ]));
    } else if (ball.type === 'INPUT') {
      const textInput = el('input', {
        type: 'text',
        className: 'wfe-menu-input wide',
        value: ball.config.text,
        placeholder: '输入内容...'
      });
      textInput.addEventListener('input', () => ball.config.text = textInput.value);
      textInput.addEventListener('click', e => e.stopPropagation());
      
      body.appendChild(el('div', { className: 'wfe-menu-item' }, [
        el('span', { className: 'wfe-menu-label' }, ['✏️ 输入文本']),
        textInput
      ]));
      body.appendChild(el('div', { className: 'wfe-menu-item disabled' }, [
        el('span', { className: 'wfe-menu-label' }, [`📍 坐标: (${Math.round(ball.x + BALL_RADIUS)}, ${Math.round(ball.y + BALL_RADIUS)})`])
      ]));
      body.appendChild(el('div', { className: 'wfe-menu-item disabled' }, [
        el('span', { className: 'wfe-menu-label' }, [`Selector: ${ball.config.selector || '(unset)'}`])
      ]));
      const inputPickBtn = el('div', { className: 'wfe-menu-item clickable' }, [
        el('span', { className: 'wfe-menu-label', style: { color: '#8b5cf6' } }, ['Pick element'])
      ]);
      inputPickBtn.addEventListener('click', () => {
        hideMenu();
        startPicker(ball);
      });
      body.appendChild(inputPickBtn);
    } else if (ball.type === 'KEY') {
      const keyValueInput = el('input', {
        type: 'text',
        className: 'wfe-menu-input wide',
        value: ball.config.key || '',
        placeholder: 'Enter / Ctrl+Enter / Escape'
      });
      keyValueInput.addEventListener('input', () => {
        ball.config.key = String(keyValueInput.value || '').trim();
      });
      keyValueInput.addEventListener('click', e => e.stopPropagation());

      body.appendChild(el('div', { className: 'wfe-menu-item' }, [
        el('span', { className: 'wfe-menu-label' }, ['⌨️ 按键 / 组合键']),
        keyValueInput
      ]));
      body.appendChild(el('div', { className: 'wfe-menu-item disabled' }, [
        el('span', { className: 'wfe-menu-label' }, ['例如：Enter、Ctrl+Enter、Shift+Enter、Escape'])
      ]));
    } else if (ball.type === 'SCRIPT') {
      const scriptInput = el('textarea', {
        className: 'wfe-menu-textarea',
        value: ball.config.script || '',
        placeholder: 'return document.title;'
      });
      scriptInput.addEventListener('input', () => {
        ball.config.script = scriptInput.value;
      });
      scriptInput.addEventListener('click', e => e.stopPropagation());

      body.appendChild(el('div', { className: 'wfe-menu-item disabled' }, [
        el('span', { className: 'wfe-menu-label' }, ['🧠 JavaScript 脚本步骤'])
      ]));
      body.appendChild(el('div', { className: 'wfe-menu-item' }, [
        el('span', { className: 'wfe-menu-label' }, ['代码'])
      ]));
      body.appendChild(scriptInput);
    } else if (ball.type === 'READ') {
      body.appendChild(el('div', { className: 'wfe-menu-item disabled' }, [
        el('span', { className: 'wfe-menu-label' }, [`🔍 ${ball.config.selector || '(未设置)'}`])
      ]));
      
      const pickBtn = el('div', { className: 'wfe-menu-item clickable' }, [
        el('span', { className: 'wfe-menu-label', style: { color: '#8b5cf6' } }, ['🖱️ 重新拾取元素'])
      ]);
      pickBtn.addEventListener('click', () => {
        hideMenu();
        startPicker(ball);
      });
      body.appendChild(pickBtn);
    }
    
    body.appendChild(el('div', { className: 'wfe-divider' }));

    const testBtn = el('div', { className: 'wfe-menu-item clickable' }, [
      el('span', { className: 'wfe-menu-label', style: { color: '#2563eb' } }, ['▶ 测试此步骤'])
    ]);
    testBtn.addEventListener('click', async () => {
      hideMenu();
      try {
        await testBall(ball);
      } catch (error) {
        console.error('[WorkflowEditor] 单步测试失败:', error);
        showToast(`测试失败: ${error.message || error}`, 'error', 3200);
      }
    });
    body.appendChild(testBtn);

    body.appendChild(el('div', { className: 'wfe-divider' }));
    
    const delBtn = el('div', { className: 'wfe-menu-item clickable danger' }, [
      el('span', { className: 'wfe-menu-label' }, ['❌ 删除此步骤'])
    ]);
    delBtn.addEventListener('click', () => {
      removeBall(ball);
      hideMenu();
    });
    body.appendChild(delBtn);
    
    menu.appendChild(body);
    document.body.appendChild(menu);
    
    const rect = menu.getBoundingClientRect();
    menu.style.left = Math.min(x, window.innerWidth - rect.width - 10) + 'px';
    menu.style.top = Math.min(y, window.innerHeight - rect.height - 10) + 'px';
    
    currentMenu = menu;
  }
  
  function hideMenu() {
    currentMenu?.remove();
    currentMenu = null;
  }
  
  document.addEventListener('click', (e) => {
    if (currentMenu && !currentMenu.contains(e.target) && !e.target.closest('.wfe-ball')) {
      hideMenu();
    }
  }, true);
  
  // ========== 元素拾取 ==========
  let pickOverlay, pickTip, highlighted;
  
  function startPicker(ball) {
    state.isPickingElement = true;
    state.pickingCallback = (selector) => {
      ball.config.selector = selector;
      ball.clearWarning();
      const selectors = state.siteConfig?.selectors || {};
      if (!ball.config.targetKey) {
        ball.config.targetKey = findSelectorKeyByValue(selectors, selector) || generateTargetKey(ball.type, selectors);
      }
      ball.locateToElement();
    };
    
    pickOverlay = el('div', { className: 'wfe-pick-overlay' });
    pickTip = el('div', { className: 'wfe-pick-tip' }, ['🎯 点击元素选择 | ESC 取消']);
    
    document.body.append(pickOverlay, pickTip);
    
    pickOverlay.addEventListener('mousemove', onPickMove);
    pickOverlay.addEventListener('click', onPickClick);
    document.addEventListener('keydown', onPickKey);
  }
  
  function onPickMove(e) {
    pickOverlay.style.pointerEvents = 'none';
    const target = document.elementFromPoint(e.clientX, e.clientY);
    pickOverlay.style.pointerEvents = 'auto';
    
    highlighted?.classList.remove('wfe-highlight');
    
    if (target && target !== document.body && !target.className?.includes?.('wfe-')) {
      target.classList.add('wfe-highlight');
      highlighted = target;
    }
  }
  
  function onPickClick(e) {
    pickOverlay.style.pointerEvents = 'none';
    const target = document.elementFromPoint(e.clientX, e.clientY);
    pickOverlay.style.pointerEvents = 'auto';
    
    if (target && highlighted && state.pickingCallback) {
      state.pickingCallback(generateSelector(target));
    }
    endPicker();
  }
  
  function onPickKey(e) {
    if (e.key === 'Escape') endPicker();
  }
  
  function endPicker() {
    state.isPickingElement = false;
    state.pickingCallback = null;
    highlighted?.classList.remove('wfe-highlight');
    highlighted = null;
    pickOverlay?.remove();
    pickTip?.remove();
    document.removeEventListener('keydown', onPickKey);
  }
  
    // ========== 小球管理 ==========
    function addBall(type, config = {}) {
        const seq = state.steps.length + 1;

        // 默认位置：错开排列
        let x = Number.isFinite(config.x) ? config.x - BALL_RADIUS : 100 + (seq - 1) * 40;
        let y = Number.isFinite(config.y) ? config.y - BALL_RADIUS : window.innerHeight / 2;
        let elementNotFound = false;

        if (config.selector) {
            const target = findElement(config.selector);
            if (target) {
                const pos = getElementCenter(target);
                if (pos) {
                    x = pos.x - BALL_RADIUS;
                    y = pos.y - BALL_RADIUS;
                }
            } else {
                // 元素未找到，标记警告状态
                elementNotFound = true;
                console.warn(`[WorkflowEditor] ⚠️ 未找到元素: ${config.selector}`);
            }
        }

        if (type === 'COORD_SCROLL') {
            if (!Number.isFinite(config.endX)) config.endX = x + BALL_RADIUS;
            if (!Number.isFinite(config.endY)) config.endY = y + BALL_RADIUS + 280;
        }

        const ball = new Ball({
            type,
            seq,
            x,
            y,
            config
        });

        state.steps.push(ball);

        // 如果元素未找到，设置警告状态
        if (elementNotFound) {
            ball.setWarning(`元素不存在: ${config.selector}`);
        }

        // 仅在新建步骤时自动拾取；坐标点击直接使用保存的坐标
        if (!config.selector && !Number.isFinite(config.x) && ['CLICK', 'INPUT', 'READ'].includes(type)) {
            setTimeout(() => startPicker(ball), 100);
        }

        return ball;
    }
  function removeBall(ball) {
    const idx = state.steps.indexOf(ball);
    if (idx > -1) {
      ball.destroy();
      state.steps.splice(idx, 1);
      state.steps.forEach((b, i) => b.updateSeq(i + 1));
    }
  }
  
  function clearAll() {
    state.steps.forEach(b => b.destroy());
    state.steps = [];
  }
  
  function exportConfig() {
    return state.steps.map(b => b.toJSON());
  }
  
    // ========== 🔧 加载现有配置（读取实际延迟）==========
    function loadFromConfig(config) {
        clearAll();
        state.siteConfig = {
            ...(config || {}),
            selectors: { ...((config && config.selectors) || {}) },
            workflow: Array.isArray(config?.workflow) ? config.workflow : []
        };

        const workflow = state.siteConfig.workflow;

        workflow.forEach((step, idx) => {
            const action = step.action;
            if (!SUPPORTED_VISUAL_WORKFLOW_ACTIONS.has(action)) {
                console.log(`[WorkflowEditor] 跳过步骤类型: ${action}`);
                return;
            }

            const targetKey = step.target;
            const selector = state.siteConfig.selectors[targetKey];

            let type, stepConfig = {};

            if (action === 'WAIT') {
                type = 'WAIT';
                stepConfig = {
                    delay_ms: 0,
                    wait_seconds: Number(step.value || 0),
                    optional: !!step.optional
                };
            } else if (action === 'CLICK') {
                type = 'CLICK';
                stepConfig = {
                    delay_ms: 0,
                    random_radius: 10,
                    selector: selector,
                    targetKey: targetKey,
                    optional: !!step.optional
                };
            } else if (action === 'COORD_CLICK') {
                type = 'COORD_CLICK';
                stepConfig = {
                    delay_ms: 0,
                    x: Number(step.value?.x ?? 100),
                    y: Number(step.value?.y ?? (window.innerHeight / 2)),
                    random_radius: Number(step.value?.random_radius ?? 10),
                    targetKey: targetKey || '',
                    optional: !!step.optional
                };
            } else if (action === 'COORD_SCROLL') {
                type = 'COORD_SCROLL';
                stepConfig = {
                    delay_ms: 0,
                    x: Number(step.value?.start_x ?? 100),
                    y: Number(step.value?.start_y ?? (window.innerHeight / 2)),
                    endX: Number(step.value?.end_x ?? 100),
                    endY: Number(step.value?.end_y ?? (window.innerHeight / 2 + 300)),
                    optional: !!step.optional
                };
            } else if (action === 'FILL_INPUT') {
                type = 'INPUT';
                stepConfig = {
                    delay_ms: 0,
                    text: step.value || '',
                    selector: selector,
                    targetKey: targetKey,
                    optional: !!step.optional
                };
            } else if (action === 'KEY_PRESS') {
                type = 'KEY';
                stepConfig = {
                    delay_ms: 0,
                    key: String(step.target || '').trim() || 'Enter',
                    optional: !!step.optional
                };
            } else if (action === 'JS_EXEC') {
                type = 'SCRIPT';
                stepConfig = {
                    delay_ms: 0,
                    script: String(step.value || '').trim() || 'return document.title;',
                    optional: !!step.optional
                };
            } else if (action === 'STREAM_WAIT') {
                type = 'READ';
                stepConfig = {
                    delay_ms: 0,
                    selector: selector || '',
                    targetKey: targetKey,
                    optional: !!step.optional
                };
            }

            addBall(type, stepConfig);
        });

        console.log(`[WorkflowEditor] ✅ 已加载 ${state.steps.length} 个步骤`);

        // 汇总显示未找到的元素
        const warningBalls = state.steps.filter(b => b.isWarning);
        if (warningBalls.length > 0) {
            const missingSelectors = warningBalls
                .map(b => `• ${b.config.targetKey || '未知'}: ${b.config.selector}`)
                .join('\n');

            setTimeout(() => {
                alert(
                    `⚠️ 以下 ${warningBalls.length} 个选择器对应的元素当前不存在：\n\n` +
                    `${missingSelectors}\n\n` +
                    `可能原因：\n` +
                    `1. 元素需要特定操作后才会出现（如输入框有内容时）\n` +
                    `2. 页面尚未完全加载\n` +
                    `3. 选择器已失效需要更新\n\n` +
                    `标记为红色的小球表示元素未找到。`
                );
            }, 300);
        }
    }
    
  // ========== 工具栏 ==========
  let toolbar;
  let toolbarActionMenu;
  let toolbarActionToggle;
  let toolbarPresetBadge;
  let toolbarDragState = null;
  let testingStatus;
  let testingStatusText;

  function clampToolbarPosition(left, top) {
    if (!toolbar) return { left, top };
    const maxLeft = Math.max(0, window.innerWidth - toolbar.offsetWidth - 8);
    const maxTop = Math.max(0, window.innerHeight - toolbar.offsetHeight - 8);
    return {
      left: Math.max(8, Math.min(left, maxLeft)),
      top: Math.max(8, Math.min(top, maxTop))
    };
  }

  function setToolbarPosition(left, top) {
    if (!toolbar) return;
    const next = clampToolbarPosition(left, top);
    toolbar.style.left = `${next.left}px`;
    toolbar.style.top = `${next.top}px`;
    toolbar.style.right = 'auto';
    toolbar.style.bottom = 'auto';
  }

  function closeToolbarActionMenu() {
    toolbarActionMenu?.classList.add('wfe-hidden');
    toolbarActionToggle?.setAttribute('aria-expanded', 'false');
  }

  function openToolbarActionMenu() {
    toolbarActionMenu?.classList.remove('wfe-hidden');
    toolbarActionToggle?.setAttribute('aria-expanded', 'true');
  }

  function toggleToolbarActionMenu() {
    if (!toolbarActionMenu) return;
    const isHidden = toolbarActionMenu.classList.contains('wfe-hidden');
    if (isHidden) openToolbarActionMenu();
    else closeToolbarActionMenu();
  }

  function onToolbarDragMove(e) {
    if (!toolbarDragState) return;
    setToolbarPosition(
      toolbarDragState.startLeft + (e.clientX - toolbarDragState.startX),
      toolbarDragState.startTop + (e.clientY - toolbarDragState.startY)
    );
  }

  function onToolbarDragEnd() {
    toolbarDragState = null;
    document.removeEventListener('mousemove', onToolbarDragMove, true);
    document.removeEventListener('mouseup', onToolbarDragEnd, true);
  }

  function startToolbarDrag(e) {
    if (!toolbar || e.button !== 0) return;
    closeToolbarActionMenu();
    toolbarDragState = {
      startX: e.clientX,
      startY: e.clientY,
      startLeft: toolbar.offsetLeft,
      startTop: toolbar.offsetTop
    };
    document.addEventListener('mousemove', onToolbarDragMove, true);
    document.addEventListener('mouseup', onToolbarDragEnd, true);
    e.preventDefault();
    e.stopPropagation();
  }
  
  function createToolbar() {
    if (toolbar) return;
      const handleDots = el('div', { className: 'wfe-toolbar-handle-dots' }, [
          el('span'), el('span'), el('span'), el('span')
      ]);
      const handle = el('div', {
          className: 'wfe-toolbar-handle',
          title: '拖动工具栏'
      }, [handleDots]);

      toolbarActionToggle = el('button', {
          className: 'wfe-btn',
          'data-action': 'toggle-action-menu',
          title: '添加动作',
          ariaExpanded: 'false'
      }, ['+ 动作 ▴']);

      toolbarActionMenu = el(
          'div',
          { className: 'wfe-toolbar-menu wfe-hidden' },
          VISUAL_ACTION_DEFS.map(def =>
              el('button', {
                  className: 'wfe-toolbar-menu-item',
                  'data-action': def.toolbarAction,
                  type: 'button'
              }, [def.menuLabel || def.toolbarLabel.replace(/^\+\s*/, '')])
          )
      );

      const actionWrap = el('div', { className: 'wfe-toolbar-action-wrap' }, [
          toolbarActionToggle,
          toolbarActionMenu
      ]);

      toolbarPresetBadge = el('div', {
          className: 'wfe-toolbar-badge',
          title: getCurrentPresetName()
      }, [getToolbarPresetText()]);

      const toolbarMeta = el('div', { className: 'wfe-toolbar-meta' }, [
          toolbarPresetBadge
      ]);

      toolbar = el('div', { className: 'wfe-toolbar', id: 'wfe-toolbar' }, [
          handle,
          toolbarMeta,
          actionWrap,
          el('button', { className: 'wfe-btn', 'data-action': 'test-all' }, ['🧪 测试']),
          el('button', { className: 'wfe-btn primary', 'data-action': 'save' }, ['💾 保存']),
          el('button', { className: 'wfe-btn danger', 'data-action': 'clear' }, ['清空']),
          el('button', { className: 'wfe-btn', 'data-action': 'close' }, ['✖'])
      ]);
    
    document.body.appendChild(toolbar);
    const rect = toolbar.getBoundingClientRect();
    setToolbarPosition(rect.left, rect.top);
    refreshToolbarMeta();

    handle.addEventListener('mousedown', startToolbarDrag);
    toolbar.addEventListener('mousedown', (e) => {
      if (e.target !== toolbar) return;
      startToolbarDrag(e);
    });
    
    toolbar.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;

        if (btn.dataset.action === 'toggle-action-menu') {
            toggleToolbarActionMenu();
            return;
        }

        const actionDef = VISUAL_ACTION_BY_TOOLBAR_ACTION[btn.dataset.action];
        if (actionDef) {
            closeToolbarActionMenu();
            addBall(actionDef.ballType);
            return;
        }

        switch (btn.dataset.action) {
            case 'test-all': testAllSteps(); break;
            case 'save': doSave(); break;
            case 'clear': if (confirm('确定清空所有步骤？')) clearAll(); break;
            case 'close': hideEditor(); break;
        }
    });
  }

  const handleOutsideToolbarClick = (e) => {
    if (!toolbar) return;
    if (!toolbar.contains(e.target)) {
      closeToolbarActionMenu();
    }
  };
  document.addEventListener('click', handleOutsideToolbarClick, true);
  registerCleanup(() => document.removeEventListener('click', handleOutsideToolbarClick, true));

  const handleToolbarResize = () => {
    if (!toolbar) return;
    setToolbarPosition(toolbar.offsetLeft, toolbar.offsetTop);
  };
  window.addEventListener('resize', handleToolbarResize);
  registerCleanup(() => window.removeEventListener('resize', handleToolbarResize));
  
    async function doSave() {
        if (!state.siteConfig) {
            state.siteConfig = { selectors: {}, workflow: [] };
        }

        const steps = state.steps;
        const { workflow: newWorkflow, selectors } = buildWorkflowPayload();

        // 获取当前域名
        const domain = window.location.hostname;
        const presetName = getCurrentPresetName();

        console.log('[WorkflowEditor] 保存配置:', { domain, presetName, workflow: newWorkflow, selectors });

        const savePayload = {
            domain,
            workflow: newWorkflow,
            selectors,
            preset_name: presetName
        };

        if (shouldPreferBridgeMode()) {
            enqueueBackendAction('save_workflow', savePayload, { trackTestStatus: false });
            showToast(`保存请求已提交，等待本地控制台写入配置。`, 'info', 2200);
            return;
        }

        try {
            const response = await fetch(`${getApiBase()}/api/sites/${domain}/workflow`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(savePayload)
            });

            if (response.ok) {
                const result = await response.json();
                state.siteConfig = {
                    ...state.siteConfig,
                    selectors,
                    workflow: newWorkflow
                };
                alert(`✅ 保存成功！\n\n已更新 ${steps.length} 个步骤到 ${domain} / ${presetName}`);
                console.log('[WorkflowEditor] 保存结果:', result);
            } else {
                const error = await response.json();
                alert(`❌ 保存失败: ${error.message || error.detail || '未知错误'}`);
            }
        } catch (e) {
            console.error('[WorkflowEditor] 保存异常:', e);

            const message = String(e?.message || e || '');
            if (
                message.includes('Failed to fetch')
                || message.includes('Content Security Policy')
                || message.includes('NetworkError')
            ) {
                enqueueBackendAction('save_workflow', savePayload, { trackTestStatus: false });
                showToast(`保存请求已提交，等待本地控制台写入配置。`, 'info', 2200);
                return;
            }

            // 检测 CSP 或网络错误，提供降级方案
            if (e.message?.includes('Failed to fetch') || e.message?.includes('Content Security Policy')) {
                const isMixedContentBlocked =
                    window.location.protocol === 'https:'
                    && /^http:\/\//i.test(getApiBase());
                const exportData = {
                    ...(state.siteConfig || {}),
                    selectors,
                    workflow: newWorkflow
                };
                const jsonStr = JSON.stringify(exportData, null, 2);

                // 尝试复制到剪贴板
                try {
                    await navigator.clipboard.writeText(jsonStr);
                    alert(
                        `⚠️ 当前页面无法直接把配置写回本地控制台。\n\n` +
                        (isMixedContentBlocked
                            ? `原因：当前网站是 HTTPS 页面，而本地控制台 API 是 HTTP 地址，浏览器会拦截这类跨源本地请求。\n\n`
                            : `原因：浏览器拦截了当前网站对本地 API 的跨源访问（常见于 CSP / CORS / 私网访问限制）。\n\n`) +
                        `当前预设配置已复制到剪贴板。\n\n` +
                        `请返回控制面板的「查看 JSON」，直接粘贴并保存当前预设。`
                    );
                    console.log('[WorkflowEditor] 配置已复制到剪贴板:', exportData);
                } catch (clipboardError) {
                    // 剪贴板也失败，显示 JSON 让用户手动复制
                    console.error('[WorkflowEditor] 剪贴板写入失败:', clipboardError);
                    prompt(
                        '⚠️ 无法自动保存或复制。请手动复制以下配置：',
                        jsonStr
                    );
                }
            } else {
                alert(`❌ 保存失败: ${e.message}`);
            }
        }
    }
  
  function showEditor() {
    state.isVisible = true;
    toolbar?.classList.remove('wfe-hidden');
    state.steps.forEach(b => b.setHidden(false));
    refreshToolbarMeta();
  }
  
  function hideEditor() {
    state.isVisible = false;
    toolbar?.classList.add('wfe-hidden');
    state.steps.forEach(b => b.setHidden(true));
    hideMenu();
    endPicker();
  }
  
  // ========== 初始化 ==========
    function init() {
        console.log('[WorkflowEditor] 🚀 初始化中...');
        injectStyles();
        createToolbar();

        const config = window.__WORKFLOW_EDITOR_CONFIG__;
        const targetDomain = window.__WORKFLOW_EDITOR_TARGET_DOMAIN__;
        state.presetName = window.__WORKFLOW_EDITOR_PRESET_NAME__ || null;
        const currentDomain = window.location.hostname;
        refreshToolbarMeta();

        // 域名校验
        if (targetDomain && targetDomain !== currentDomain) {
            alert(
                `❌ 域名不匹配！\n\n` +
                `配置目标: ${targetDomain}\n` +
                `当前页面: ${currentDomain}\n\n` +
                `请导航到正确的网站后重试。`
            );
            console.error(`[WorkflowEditor] 域名不匹配: 期望 ${targetDomain}, 实际 ${currentDomain}`);
            hideEditor();
            return;
        }

        // 自动加载配置
        if (config) {
            state.siteConfig = config;
            loadFromConfig(state.siteConfig);
        } else {
            console.log('[WorkflowEditor] 未提供配置，进入空白编辑模式');
            alert(
                `⚠️ 未找到当前站点 (${currentDomain}) 的配置。\n\n` +
                `你可以手动添加步骤，但保存功能可能不可用。`
            );
        }

        console.log('[WorkflowEditor] ✅ 编辑器已就绪');
    }
  
  init();
  
  window.WorkflowEditor = {
    addClick: () => addBall('CLICK'),
    addCoordClick: () => addBall('COORD_CLICK'),
    addCoordScroll: () => addBall('COORD_SCROLL'),
    addInput: () => addBall('INPUT'),
    addRead: () => addBall('READ'),
    addWait: () => addBall('WAIT'),
    addKey: () => addBall('KEY'),
    addScript: () => addBall('SCRIPT'),
    clear: clearAll,
    export: exportConfig,
    show: showEditor,
    hide: hideEditor,
    getSteps: () => state.steps.map(b => b.toJSON()),
    handleBackendStatus: (actionId, phase, message) => {
      const normalizedActionId = String(actionId || '');
      if (normalizedActionId) {
        state.testExecution.actionId = normalizedActionId;
      }
      const text = String(message || '').trim() || getTestingStatusText();
      console.debug('[WorkflowEditor] backend status', { actionId: normalizedActionId, phase, message: text });
      updateTestingStatus(text, phase === 'running' ? 'info' : '', phase === 'running' ? 1800 : 0);
    },
    handleBackendResult: (actionId, success, message) => {
      const action = actionId ? state.pendingBackendActions.get(String(actionId)) : null;
      if (actionId) {
        state.pendingBackendActions.delete(String(actionId));
      }
      if (action?.type === 'save_workflow' && success) {
        state.siteConfig = {
          ...(state.siteConfig || {}),
          selectors: { ...((action.payload && action.payload.selectors) || {}) },
          workflow: Array.isArray(action?.payload?.workflow) ? action.payload.workflow : []
        };
      } else {
        touchTestActivity();
        resumeEditorAfterTest();
      }
      console.debug('[WorkflowEditor] backend result', {
        actionId,
        actionType: action?.type || null,
        success,
        message
      });
      showToast(
        message || (action?.type === 'save_workflow'
          ? (success ? '保存完成' : '保存失败')
          : (success ? '测试完成' : '测试失败')),
        success ? 'success' : 'error',
        3200
      );
    },
    destroy: () => {
      hideMenu();
      endPicker();
      state.steps.slice().forEach(ball => removeBall(ball));
      state.steps = [];
      state.pendingBackendActions.clear();
      toolbar?.remove();
      toolbar = null;
      toolbarActionMenu = null;
      toolbarActionToggle = null;
      toolbarPresetBadge = null;
      hideTestingStatus();
      document.getElementById('wfe-styles')?.remove();
      document.querySelectorAll('.wfe-toast, .wfe-pick-tip, .wfe-test-ring, .wfe-highlight').forEach(node => node.remove());
      const cleanupHandlers = state.cleanupHandlers.slice();
      state.cleanupHandlers = [];
      cleanupHandlers.forEach(fn => {
        try { fn(); } catch (_) {}
      });
      delete window.WorkflowEditor;
      delete window.__WORKFLOW_EDITOR_PENDING_ACTIONS__;
      window.__WORKFLOW_EDITOR_INJECTED__ = false;
    },
    reload: () => {
      state.presetName = window.__WORKFLOW_EDITOR_PRESET_NAME__ || state.presetName || null;
      refreshToolbarMeta();
      loadFromConfig(window.__WORKFLOW_EDITOR_CONFIG__ || state.siteConfig);
    }
  };
  
})();
