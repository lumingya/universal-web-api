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
        COORD_CLICK: { color: 'rgba(249, 115, 22, 0.18)', border: '#F97316', name: 'Coord Click' },
        CLICK: { color: 'rgba(59, 130, 246, 0.15)', border: '#3B82F6', name: '点击' },
        INPUT: { color: 'rgba(16, 185, 129, 0.15)', border: '#10B981', name: '输入' },
        READ: { color: 'rgba(139, 92, 246, 0.15)', border: '#8B5CF6', name: '读取' }
    };

    // 🔧 后端 API 地址（从注入时传入，或使用默认值）
    const BALL_SIZE = 32;
    const BALL_RADIUS = BALL_SIZE / 2;
    const API_BASE = window.__WORKFLOW_EDITOR_API_BASE__ || 'http://127.0.0.1:9099';

    const state = {
        steps: [],
        siteConfig: null,
        presetName: null,
        isPickingElement: false,
        pickingCallback: null,
        isVisible: true
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
      
      .wfe-hidden { display: none !important; }
    `;
    document.head.appendChild(style);
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
                text: '',
                selector: '',
                targetKey: '',
                optional: false,
                ...opts.config
            };

            this.element = null;
            this.isDragging = false;
            this.offset = { x: 0, y: 0 };
            this.isWarning = false;       // 警告状态
            this.warningMessage = '';     // 警告信息

            this.render();
            this.bind();

            // 不在构造函数中自动定位，由 addBall 统一处理
        }
    
      render() {
          const tc = TYPES[this.type];
          const selectorHint = this.config.selector ? ` → ${this.config.selector.slice(0, 30)}` : '';
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
        this.isDragging = true;
        this.element.classList.add('dragging');
        this.offset = { x: e.clientX - this.x, y: e.clientY - this.y };
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
    }
    
    move(x, y) {
      this.x = Math.max(0, Math.min(window.innerWidth - BALL_SIZE, x));
      this.y = Math.max(0, Math.min(window.innerHeight - BALL_SIZE, y));
      this.element.style.left = this.x + 'px';
      this.element.style.top = this.y + 'px';
    }
    
      updateSeq(n) {
          this.seq = n;
          this.element.textContent = String(n);
          const selectorHint = this.config.selector ? ` → ${this.config.selector.slice(0, 30)}` : '';
          this.element.title = `#${n} ${TYPES[this.type].name}${selectorHint}`;
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
            // 更新 title 显示警告信息
            const tc = TYPES[this.type];
            this.element.title = `⚠️ #${this.seq} ${tc.name} - ${message}`;
        }

        clearWarning() {
            this.isWarning = false;
            this.warningMessage = '';
            this.element?.classList.remove('warning');
            this.updateSeq(this.seq); // 恢复正常 title
        }

    destroy() {
      this.element?.remove();
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
      } else if (this.type === 'INPUT') {
        data.x = Math.round(this.x + BALL_RADIUS);
        data.y = Math.round(this.y + BALL_RADIUS);
        data.text = this.config.text;
      } else if (this.type === 'READ') {
        data.selector = this.config.selector;
      }
      
      return data;
    }
  }
  
  // ========== 全局拖拽 ==========
  document.addEventListener('mousemove', (e) => {
    const ball = state.steps.find(b => b.isDragging);
    if (ball) ball.move(e.clientX - ball.offset.x, e.clientY - ball.offset.y);
  }, true);
  
  document.addEventListener('mouseup', () => {
    state.steps.forEach(b => {
      if (b.isDragging) {
        b.isDragging = false;
        b.element.classList.remove('dragging');
      }
    });
  }, true);
  
  // ========== 右键菜单 ==========
  let currentMenu = null;
  
  function showMenu(ball, x, y) {
    hideMenu();
    
    const tc = TYPES[ball.type];
    const menu = el('div', { className: 'wfe-menu' });
    
    menu.appendChild(el('div', { className: 'wfe-menu-header' }, [
      el('div', { className: 'wfe-menu-title' }, [`步骤 #${ball.seq}：${tc.name}`]),
      el('div', { className: 'wfe-menu-subtitle' }, [
        ball.type === 'CLICK' ? '在此坐标模拟鼠标点击' :
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
    if (ball.type !== 'COORD_CLICK') {
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

    if (ball.type === 'CLICK' || ball.type === 'COORD_CLICK') {
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
        let pendingDelay = 0; // 累积前面 WAIT 步骤的延迟

        workflow.forEach((step, idx) => {
            const action = step.action;

            // 处理 WAIT 步骤：累积延迟给下一个动作
            if (action === 'WAIT') {
                const waitValue = parseFloat(step.value) || 0;
                pendingDelay += waitValue * 1000; // 转为毫秒
                return;
            }

            // 跳过 KEY_PRESS 等其他步骤
            if (!['CLICK', 'COORD_CLICK', 'FILL_INPUT', 'STREAM_WAIT'].includes(action)) {
                console.log(`[WorkflowEditor] 跳过步骤类型: ${action}`);
                return;
            }

            const targetKey = step.target;
            const selector = state.siteConfig.selectors[targetKey];

            let type, stepConfig = {};

            if (action === 'CLICK') {
                type = 'CLICK';
                stepConfig = {
                    delay_ms: pendingDelay,
                    random_radius: 10,
                    selector: selector,
                    targetKey: targetKey,
                    optional: !!step.optional
                };
            } else if (action === 'COORD_CLICK') {
                type = 'COORD_CLICK';
                stepConfig = {
                    delay_ms: pendingDelay,
                    x: Number(step.value?.x ?? 100),
                    y: Number(step.value?.y ?? (window.innerHeight / 2)),
                    random_radius: Number(step.value?.random_radius ?? 10),
                    targetKey: targetKey || '',
                    optional: !!step.optional
                };
            } else if (action === 'FILL_INPUT') {
                type = 'INPUT';
                stepConfig = {
                    delay_ms: pendingDelay,
                    text: step.value || '',
                    selector: selector,
                    targetKey: targetKey,
                    optional: !!step.optional
                };
            } else if (action === 'STREAM_WAIT') {
                type = 'READ';
                stepConfig = {
                    delay_ms: pendingDelay,
                    selector: selector || '',
                    targetKey: targetKey,
                    optional: !!step.optional
                };
            }

            addBall(type, stepConfig);
            pendingDelay = 0; // 重置延迟
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
  
  function createToolbar() {
    if (toolbar) return;
    
      toolbar = el('div', { className: 'wfe-toolbar', id: 'wfe-toolbar' }, [
          el('button', { className: 'wfe-btn', 'data-action': 'add-coord-click' }, ['+ Coord']),
          el('button', { className: 'wfe-btn', 'data-action': 'add-click' }, ['+ 点击']),
          el('button', { className: 'wfe-btn', 'data-action': 'add-input' }, ['+ 输入']),
          el('button', { className: 'wfe-btn', 'data-action': 'add-read' }, ['+ 读取']),
          el('button', { className: 'wfe-btn primary', 'data-action': 'save' }, ['💾 保存']),
          el('button', { className: 'wfe-btn danger', 'data-action': 'clear' }, ['清空']),
          el('button', { className: 'wfe-btn', 'data-action': 'close' }, ['✖'])
      ]);
    
    document.body.appendChild(toolbar);
    
    toolbar.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;
      
        switch (btn.dataset.action) {
            case 'add-coord-click': addBall('COORD_CLICK'); break;
            case 'add-click': addBall('CLICK'); break;
            case 'add-input': addBall('INPUT'); break;
            case 'add-read': addBall('READ'); break;
            case 'save': doSave(); break;
            case 'clear': if (confirm('确定清空所有步骤？')) clearAll(); break;
            case 'close': hideEditor(); break;
        }
    });
  }
  
    async function doSave() {
        if (!state.siteConfig) {
            state.siteConfig = { selectors: {}, workflow: [] };
        }

        const steps = state.steps;
        const selectors = { ...(state.siteConfig.selectors || {}) };

        // 构建新的 workflow 数组
        const newWorkflow = [];

        steps.forEach((ball, idx) => {
            const delayMs = ball.config.delay_ms || 0;
            const targetKey = ball.type === 'CLICK'
                ? normalizeKey(ball.config.targetKey || '')
                : ['INPUT', 'READ'].includes(ball.type)
                    ? ensureBallTargetKey(ball, selectors)
                    : '';

            // 如果有延迟，插入 WAIT 步骤
            if (delayMs > 0) {
                newWorkflow.push({
                    action: 'WAIT',
                    target: '',
                    optional: false,
                    value: delayMs / 1000 // 转为秒
                });
            }

            // 插入实际动作步骤
            if (ball.type === 'CLICK') {
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
            } else if (ball.type === 'INPUT') {
                newWorkflow.push({
                    action: 'FILL_INPUT',
                    target: targetKey || 'input_box',
                    optional: !!ball.config.optional,
                    value: ball.config.text || null
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

        // 获取当前域名
        const domain = window.location.hostname;
        const presetName = window.__WORKFLOW_EDITOR_PRESET_NAME__ || state.presetName || '主预设';

        console.log('[WorkflowEditor] 保存配置:', { domain, presetName, workflow: newWorkflow, selectors });

        try {
            const response = await fetch(`${API_BASE}/api/sites/${domain}/workflow`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    workflow: newWorkflow,
                    selectors,
                    preset_name: presetName
                })
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

            // 检测 CSP 或网络错误，提供降级方案
            if (e.message?.includes('Failed to fetch') || e.message?.includes('Content Security Policy')) {
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
                        `⚠️ 由于该网站安全策略限制，无法直接保存。\n\n` +
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
    state.steps.forEach(b => b.element?.classList.remove('wfe-hidden'));
  }
  
  function hideEditor() {
    state.isVisible = false;
    toolbar?.classList.add('wfe-hidden');
    state.steps.forEach(b => b.element?.classList.add('wfe-hidden'));
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
    addInput: () => addBall('INPUT'),
    addRead: () => addBall('READ'),
    clear: clearAll,
    export: exportConfig,
    show: showEditor,
    hide: hideEditor,
    getSteps: () => state.steps.map(b => b.toJSON()),
    reload: () => {
      state.presetName = window.__WORKFLOW_EDITOR_PRESET_NAME__ || state.presetName || null;
      loadFromConfig(window.__WORKFLOW_EDITOR_CONFIG__ || state.siteConfig);
    }
  };
  
})();
