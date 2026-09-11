/* STUDIO_STYLE_START */
window.WORKFLOW_STUDIO_CSS = ":host {\n  display: block;\n  color: var(--uwa-ink, #33402c);\n  font:\n    13px/1.5 system-ui,\n    -apple-system,\n    \"Segoe UI\",\n    sans-serif;\n  --ws-paper: var(--uwa-paper, #f7f3ea);\n  --ws-card: var(--uwa-paper-strong, #fffdf8);\n  --ws-ink: var(--uwa-ink, #33402c);\n  --ws-muted: var(--uwa-muted, #777b6c);\n  --ws-line: var(--uwa-line, #dddccd);\n  --ws-accent: var(--uwa-olive, #5d6b4d);\n}\n* {\n  box-sizing: border-box;\n}\nbutton,\ninput,\nselect,\ntextarea {\n  font: inherit;\n  color: inherit;\n}\nbutton {\n  cursor: pointer;\n}\nbutton:disabled {\n  opacity: 0.4;\n  cursor: not-allowed;\n}\nbutton:focus-visible,\ninput:focus-visible,\ntextarea:focus-visible,\nselect:focus-visible {\n  outline: 2px solid var(--ws-accent);\n  outline-offset: 3px;\n}\nbutton {\n  border: 1px solid var(--ws-line);\n  border-radius: 7px;\n  background: var(--ws-card);\n  padding: 6px 10px;\n}\nbutton:hover:not(:disabled) {\n  border-color: var(--ws-accent);\n}\n.studio {\n  border: 1px solid var(--ws-line);\n  border-radius: 14px;\n  background: var(--ws-paper);\n  overflow: hidden;\n}\n.head {\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n  gap: 14px;\n  padding: 20px 22px;\n  border-bottom: 1px solid var(--ws-line);\n  flex-wrap: wrap;\n}\n.eyebrow {\n  font-size: 10px;\n  letter-spacing: 2px;\n  color: var(--ws-muted);\n  font-weight: 700;\n}\n.head h2 {\n  font-size: 21px;\n  margin: 3px 0;\n  font-weight: 650;\n  letter-spacing: -0.6px;\n}\n.head p {\n  margin: 4px 0 0;\n  color: var(--ws-muted);\n  font-size: 12px;\n}\n.actions {\n  display: flex;\n  gap: 6px;\n  align-items: center;\n  flex-wrap: wrap;\n}\n.primary {\n  background: var(--ws-accent);\n  color: var(--ws-card);\n  border-color: var(--ws-accent);\n}\n.tabs {\n  display: flex;\n  gap: 7px;\n  padding: 10px 20px;\n  border-bottom: 1px solid var(--ws-line);\n  align-items: center;\n  flex-wrap: wrap;\n}\n.tabs button {\n  background: none;\n  border-color: transparent;\n}\n.tabs button.active {\n  background: var(--ws-card);\n  border-color: var(--ws-line);\n  font-weight: 650;\n}\n.counter {\n  margin-left: auto;\n  color: var(--ws-muted);\n  font-size: 11px;\n}\n.workspace {\n  display: grid;\n  grid-template-columns: minmax(0, 1fr) 290px;\n  min-height: 480px;\n}\n.canvas {\n  overflow: auto;\n  padding: 26px 24px;\n  max-height: 720px;\n  background-image: radial-gradient(var(--ws-line) 0.7px, transparent 0.7px);\n  background-size: 16px 16px;\n}\n.sequence {\n  display: flex;\n  flex-direction: column;\n  align-items: stretch;\n  position: relative;\n  min-width: 160px;\n  gap: 0;\n  max-width: 900px;\n  margin: auto;\n}\n.sequence.root {\n  min-width: 250px;\n}\n.sequence:before {\n  content: \"\";\n  position: absolute;\n  left: 50%;\n  top: 0;\n  bottom: 0;\n  width: 1px;\n  background: var(--ws-line);\n}\n.unit {\n  position: relative;\n  margin: 0 0 20px;\n  z-index: 1;\n}\n.node {\n  width: 100%;\n  display: flex;\n  align-items: center;\n  gap: 11px;\n  text-align: left;\n  background: var(--ws-card);\n  padding: 12px 13px;\n  border: 1px solid var(--ws-line);\n  border-radius: 10px;\n  box-shadow: 0 2px 3px #00000003;\n  position: relative;\n}\n.node.selected {\n  border-color: var(--ws-accent);\n  box-shadow: 0 0 0 2px color-mix(in srgb, var(--ws-accent) 15%, transparent);\n}\n.node.control {\n  border-left: 3px solid var(--ws-accent);\n}\n.node.failed {\n  border-color: #b3634d;\n}\n.node.completed,\n.node.captured,\n.node.assigned {\n  border-right: 3px solid var(--ws-accent);\n}\n.symbol {\n  flex-shrink: 0;\n  width: 29px;\n  height: 29px;\n  background: var(--ws-paper);\n  border-radius: 8px;\n  display: grid;\n  place-items: center;\n  font-size: 16px;\n  color: var(--ws-accent);\n}\n.node-info {\n  min-width: 0;\n  flex: 1;\n}\n.node-name {\n  font-size: 12px;\n  font-weight: 650;\n  display: block;\n}\n.node-description {\n  display: block;\n  font-size: 11px;\n  color: var(--ws-muted);\n  white-space: nowrap;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  max-width: 360px;\n}\n.node-code {\n  font-size: 9px;\n  font-family: monospace;\n  color: var(--ws-muted);\n  align-self: flex-start;\n}\n.branches {\n  display: grid;\n  grid-template-columns: repeat(2, minmax(160px, 1fr));\n  gap: 12px;\n  margin-top: 15px;\n  border: 1px solid var(--ws-line);\n  border-top: 0;\n  border-radius: 0 0 10px 10px;\n  padding: 0 10px 10px;\n  background: color-mix(in srgb, var(--ws-paper) 90%, transparent);\n}\n.branches.single {\n  grid-template-columns: minmax(160px, 1fr);\n}\n.branch-name {\n  display: flex;\n  align-items: center;\n  gap: 7px;\n  color: var(--ws-muted);\n  font-size: 10px;\n  letter-spacing: 0.5px;\n  margin: 0 0 12px;\n  text-align: center;\n  justify-content: center;\n}\n.branch-name span {\n  background: var(--ws-card);\n  border: 1px solid var(--ws-line);\n  padding: 2px 10px;\n  border-radius: 20px;\n}\n.branch.taken > .branch-name span {\n  background: var(--ws-accent);\n  color: var(--ws-card);\n}\n.merge {\n  font-size: 9px;\n  color: var(--ws-muted);\n  text-align: center;\n  background: var(--ws-paper);\n  position: relative;\n  margin: 5px auto 0;\n  width: 55px;\n}\n.add {\n  border-style: dashed;\n  background: var(--ws-paper);\n  font-size: 11px;\n  position: relative;\n  z-index: 1;\n  align-self: center;\n  padding: 5px 13px;\n}\n.endpoint {\n  font-size: 10px;\n  color: var(--ws-muted);\n  text-align: center;\n  border: 1px solid var(--ws-line);\n  border-radius: 20px;\n  background: var(--ws-paper);\n  padding: 4px 15px;\n  position: relative;\n  width: max-content;\n  margin: 0 auto 21px;\n  letter-spacing: 1px;\n}\n.endpoint.end {\n  margin: 22px auto 0;\n}\n.inspector {\n  border-left: 1px solid var(--ws-line);\n  background: var(--ws-card);\n  padding: 20px;\n  overflow: auto;\n  max-height: 720px;\n}\n.inspector h3 {\n  font-size: 15px;\n  margin: 5px 0 10px;\n}\n.hint {\n  color: var(--ws-muted);\n  font-size: 11px;\n  line-height: 1.8;\n  margin: 8px 0 17px;\n  overflow-wrap: anywhere;\n}\n.field {\n  display: block;\n  margin: 12px 0;\n  font-size: 11px;\n  font-weight: 550;\n}\n.field > span {\n  display: block;\n  margin-bottom: 6px;\n}\n.field input,\n.field select,\n.field textarea {\n  width: 100%;\n  background: var(--ws-paper);\n  border: 1px solid var(--ws-line);\n  border-radius: 6px;\n  padding: 8px;\n  min-height: 34px;\n  font-size: 12px;\n  font-weight: 400;\n}\n.field textarea {\n  resize: vertical;\n  min-height: 72px;\n  font-family: ui-monospace, monospace;\n}\n.field input[type=\"checkbox\"] {\n  width: auto;\n  min-height: 0;\n  vertical-align: middle;\n  margin-right: 8px;\n}\n.pair {\n  display: grid;\n  grid-template-columns: 1fr 1fr;\n  gap: 10px;\n}\n.danger {\n  color: #a55d43;\n}\n.divider {\n  border: 0;\n  border-top: 1px solid var(--ws-line);\n  margin: 20px 0;\n}\n.inspector details {\n  margin: 15px 0;\n}\n.inspector summary {\n  font-size: 11px;\n  cursor: pointer;\n  color: var(--ws-muted);\n}\n.panel {\n  padding: 24px;\n  min-height: 480px;\n}\n.panel h3 {\n  font-size: 16px;\n  margin: 0 0 6px;\n}\n.empty {\n  padding: 35px 10px;\n  text-align: center;\n  color: var(--ws-muted);\n}\n.palette {\n  padding: 20px;\n  background: var(--ws-card);\n  border-bottom: 1px solid var(--ws-line);\n}\n.palette-grid {\n  display: grid;\n  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));\n  gap: 8px;\n  margin-top: 10px;\n}\n.palette button {\n  text-align: left;\n  padding: 10px;\n}\n.palette small {\n  display: block;\n  color: var(--ws-muted);\n  font-size: 10px;\n  margin-top: 4px;\n}\n.palette h4 {\n  margin: 8px 0;\n  font-size: 11px;\n  color: var(--ws-muted);\n  letter-spacing: 1px;\n}\n.banner {\n  padding: 9px 20px;\n  font-size: 12px;\n  border-bottom: 1px solid var(--ws-line);\n  background: var(--ws-card);\n  white-space: pre-wrap;\n  overflow-wrap: anywhere;\n}\n.banner.error {\n  color: #a55d43;\n}\n.table {\n  width: 100%;\n  border-collapse: collapse;\n  font-size: 12px;\n}\n.table th,\n.table td {\n  text-align: left;\n  padding: 12px 10px;\n  border-bottom: 1px solid var(--ws-line);\n  overflow-wrap: anywhere;\n}\n.table th {\n  font-size: 10px;\n  font-weight: 500;\n  color: var(--ws-muted);\n}\ncode {\n  font:\n    11px ui-monospace,\n    monospace;\n}\n.test-grid {\n  display: grid;\n  grid-template-columns: 1fr 1fr;\n  gap: 18px;\n  max-width: 720px;\n}\n.trace {\n  margin-top: 20px;\n  max-height: 380px;\n  overflow: auto;\n}\n.trace button {\n  display: flex;\n  width: 100%;\n  text-align: left;\n  gap: 12px;\n  border: 0;\n  border-bottom: 1px solid var(--ws-line);\n  border-radius: 0;\n  padding: 9px;\n}\n.trace .status {\n  margin-left: auto;\n  color: var(--ws-accent);\n}\n.maprow {\n  display: grid;\n  grid-template-columns: 1fr 14px 1fr 28px;\n  gap: 4px;\n  align-items: center;\n  margin: 5px 0;\n}\n.maprow input {\n  width: 100%;\n  min-width: 0;\n  background: var(--ws-paper);\n  border: 1px solid var(--ws-line);\n  border-radius: 5px;\n  padding: 6px;\n  font-size: 11px;\n}\n.maprow button {\n  padding: 4px;\n}\n.footer {\n  padding: 10px 20px;\n  border-top: 1px solid var(--ws-line);\n  font-size: 10px;\n  color: var(--ws-muted);\n  display: flex;\n  justify-content: space-between;\n  gap: 10px;\n}\n.hidden {\n  display: none !important;\n}\n.row-actions {\n  display: flex;\n  gap: 5px;\n  flex-wrap: wrap;\n}\n@media (max-width: 850px) {\n  .workspace {\n    grid-template-columns: minmax(0, 1fr);\n  }\n  .inspector {\n    border-left: 0;\n    border-top: 1px solid var(--ws-line);\n    max-height: 500px;\n  }\n  .canvas {\n    max-height: 560px;\n  }\n  .head {\n    padding: 17px;\n  }\n  .test-grid {\n    grid-template-columns: 1fr;\n  }\n  .branches {\n    gap: 9px;\n  }\n  .counter {\n    display: none;\n  }\n}\n@media (prefers-color-scheme: dark) {\n  :host([data-auto-dark]) {\n    --ws-paper: #252b24;\n    --ws-card: #30372d;\n    --ws-ink: #e0e3d5;\n    --ws-muted: #adb59e;\n    --ws-line: #495241;\n    --ws-accent: #a2b288;\n    color: var(--ws-ink);\n  }\n}\n:host([data-dark]) {\n  --ws-paper: #252b24;\n  --ws-card: #30372d;\n  --ws-ink: #e0e3d5;\n  --ws-muted: #adb59e;\n  --ws-line: #495241;\n  --ws-accent: #a2b288;\n  color: var(--ws-ink);\n}\n\n/* The injected canvas is movable; dashboard instances keep normal header behavior. */\n:host([data-draggable]) .head { cursor: grab; touch-action: none; user-select: none; }\n:host([data-draggable]) .head .eyebrow::after { content: ' · 拖动标题移动'; letter-spacing: 0; }\n\n/* A quiet canvas first; settings appear only for the selected step. */\n.head { padding: 10px 18px; justify-content: flex-end; }\n.drag-handle { margin-right:auto; color:var(--ws-muted); font-size:11px; user-select:none; }\n.workspace { position:relative; grid-template-columns:minmax(0,1fr) 0px; transition:grid-template-columns .32s cubic-bezier(.22,.8,.3,1); }\n.workspace.is-inspecting { grid-template-columns:minmax(0,1fr) 290px; }\n.sequence.root { max-width:640px; }\n.inspector-slot { min-width:0; overflow:hidden; position:relative; opacity:0; transform:translateX(18px); transition:opacity .24s ease,transform .32s ease; pointer-events:none; }\n.is-inspecting .inspector-slot { opacity:1; transform:translateX(0); pointer-events:auto; }\n.inspector { position:absolute; inset:0; width:290px; height:100%; padding:16px 20px; }\n.inspector-close { display:block; margin:0 0 18px auto; font-size:11px; color:var(--ws-muted); background:transparent; }\n@media (max-width:850px) {\n  .workspace,.workspace.is-inspecting { grid-template-columns:minmax(0,1fr) 0px; }\n  .inspector-slot { position:absolute; right:0; top:0; bottom:0; width:min(310px,100%); z-index:8; transform:translateX(100%); box-shadow:-12px 0 35px #00000012; }\n  .inspector { width:100%; max-height:100%; border-top:0; border-left:1px solid var(--ws-line); }\n  .head { padding:10px 12px; }\n  .canvas { padding:24px 16px; }\n}\n@media (prefers-reduced-motion:reduce) { .workspace,.inspector-slot { transition:none; } }\n";
/* STUDIO_STYLE_END */
/* Workflow Studio — shared, dependency-free tree editor. No page actions on mount. */
(() => {
  "use strict";
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const kinds = {
    FILL_INPUT: ["填写输入", "↳", "向页面输入请求内容"],
    CLICK: ["点击元素", "↗", "使用共享选择器操作页面"],
    SELECT_MODEL: ["选择模型", "⌘", "使用现有模型目录"],
    KEY_PRESS: ["按下快捷键", "⌨", "Enter、Ctrl+Enter 等"],
    WAIT: ["等待", "◷", "为页面留出响应时间"],
    STREAM_WAIT: ["等待响应", "≈", "沿用流式读取设置"],
    STREAM_OUTPUT: ["输出响应", "≋", "沿用已有输出能力"],
    JS_EXEC: ["执行脚本", "ƒ", "保留现有脚本与参数"],
    PAGE_FETCH: ["页面请求", "⇄", "沿用页面请求配置"],
    COORD_CLICK: ["坐标点击", "⊙", "使用已有坐标参数"],
    COORD_SCROLL: ["坐标滚动", "↕", "使用已有滚动参数"],
    READONLY_HINT: ["说明卡片", "i", "给维护者留下提示"],
    SET: ["设置变量", "𝑥", "常量、映射或轻量转换"],
    CAPTURE: ["读取页面状态", "◎", "文本、属性、URL 或存在性"],
    IF: ["条件分支", "◇", "满足条件 / 否则，随后汇合"],
    GROUP: ["步骤组", "▤", "组织步骤与局部变量"],
    GUARD: ["跳过 / 跳转", "↪", "满足条件时跳组或前往锚点"],
    TRY: ["重试与兜底", "↻", "有限重试，保护有副作用的动作"],
    LABEL: ["锚点", "⚑", "供同一分支内向前跳转"],
  };
  const controls = new Set([
    "SET",
    "CAPTURE",
    "IF",
    "GROUP",
    "GUARD",
    "TRY",
    "LABEL",
  ]);
  const ops = {
    eq: "等于",
    ne: "不等于",
    contains: "包含",
    starts_with: "开头是",
    ends_with: "结尾是",
    matches: "匹配正则",
    exists: "存在",
    not_exists: "不存在",
    gt: "大于",
    gte: "大于等于",
    lt: "小于",
    lte: "小于等于",
    in: "属于列表",
  };
  const statuses = {
    planned: "计划执行",
    continued: "继续",
    started: "开始",
    completed: "完成",
    assigned: "已赋值",
    captured: "已读取",
    branch: "分支判定",
    merged: "汇合",
    entered: "进入组",
    skipped: "跳过",
    attempt: "尝试",
    retry: "重试",
    fallback: "进入兜底",
    failed: "失败",
    label: "到达锚点",
    jumped: "跳转",
  };
  const text = (v) =>
    typeof v === "object" ? JSON.stringify(v) : String(v ?? "");
  const typed = (v) => {
    try {
      return JSON.parse(v);
    } catch (_) {
      return v;
    }
  };
  function walk(nodes, fn, prefix = "root") {
    (nodes || []).forEach((n, i) => {
      const p = `${prefix}.${i}`;
      fn(n, p);
      if (controls.has(n.action))
        ["then", "else", "steps", "fallback"].forEach((b) => {
          if (Array.isArray(n.value?.[b])) walk(n.value[b], fn, `${p}.${b}`);
        });
    });
  }
  function assertTree(nodes, depth = 0, budget = { count: 0 }) {
    if (!Array.isArray(nodes) || depth > 16)
      throw Error("分支必须是数组，最多嵌套 16 层");
    for (const n of nodes) {
      if (
        !n ||
        typeof n !== "object" ||
        Array.isArray(n) ||
        typeof n.action !== "string" ||
        ++budget.count > 1000
      )
        throw Error("步骤格式无效或超过 1000 个节点");
      if (controls.has(n.action)) {
        if (!n.value || typeof n.value !== "object" || Array.isArray(n.value))
          throw Error("控制节点需要 value 对象");
        for (const key of ["then", "else", "steps", "fallback"])
          if (key in n.value) assertTree(n.value[key], depth + 1, budget);
      }
    }
  }
  function defaultNode(action) {
    const condition = { left: "{current}", op: "ne", right: "{desired}" };
    const values = {
      SET: { name: "desired", value: "", scope: "local" },
      CAPTURE: { name: "current", source: "text", timeout: 2 },
      IF: { condition, then: [], else: [] },
      GROUP: { variables: {}, steps: [] },
      GUARD: { condition, mode: "skip_group" },
      TRY: { steps: [], attempts: 2, delay: 0.3, retry_side_effects: false },
      LABEL: { name: "continue_here" },
      WAIT: 1,
      KEY_PRESS: "Enter",
      FILL_INPUT: "{prompt}",
      JS_EXEC: {},
      PAGE_FETCH: {},
      READONLY_HINT: "说明",
    };
    return {
      action,
      target: "",
      optional: false,
      flow_version: 2,
      ...(action in values ? { value: clone(values[action]) } : {}),
    };
  }
  class Studio {
    constructor(host, options = {}) {
      this.host = host;
      this.options = options;
      this.workflow = clone(options.workflow || []);
      this.selectors = clone(options.selectors || {});
      this.tab = "flow";
      this.selected = null;
      this.undoStack = [];
      this.redoStack = [];
      this.trace = [];
      this.inputs = "{}";
      this.captures = "{}";
      this.model = "";
      this.prompt = options.injected ? "这是一条工作流测试消息。" : "";
      this.notice = "";
      this.palette = null;
      this.root = host.attachShadow({ mode: "open" });
      this.root.addEventListener("click", (e) => this.click(e));
      this.root.addEventListener("input", (e) => this.input(e));
      this.root.addEventListener("change", (e) => this.change(e));
      this.root.addEventListener('keydown', e => {
        if (e.key === 'Escape' && this.selected && this.tab === 'flow' && !this.palette) {
          e.preventDefault(); e.stopPropagation(); this.closeInspector();
        }
      });
      this.root.addEventListener("focusin", () => {
        this.editKey = null;
      });
      this.observer = new MutationObserver(() => this.theme());
      this.observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["class", "data-theme"],
      });
      this.theme();
      this.render();
    }
    theme() {
      this.host.toggleAttribute(
        "data-dark",
        document.documentElement.classList.contains("dark") ||
          document.documentElement.dataset.theme === "dark",
      );
      if (this.options.injected) this.host.setAttribute("data-auto-dark", "");
    }
    destroy() {
      this.observer.disconnect();
      this.root.replaceChildren();
    }
    setWorkflow(value) {
      if (JSON.stringify(value) !== JSON.stringify(this.workflow)) {
        this.workflow = clone(value || []);
        this.selected = null;
        this.undoStack = [];
        this.redoStack = [];
        this.render();
      }
    }
    setSelectors(value) {
      this.selectors = clone(value || {});
    }
    getWorkflow() {
      return clone(this.workflow);
    }
    seq(path) {
      if (path === "root") return this.workflow;
      const parts = path.split(".");
      const branch = parts.pop();
      return this.node(parts.join(".")).value[branch];
    }
    node(path) {
      const parts = path.split(".");
      let list = this.workflow,
        node;
      for (let i = 1; i < parts.length; i += 2) {
        node = list[Number(parts[i])];
        if (!node) throw Error("步骤不存在");
        if (i + 1 < parts.length) list = node.value[parts[i + 1]];
      }
      return node;
    }
    location(path) {
      const parts = path.split(".");
      const index = Number(parts.pop());
      return {
        list: this.seq(parts.join(".")),
        index,
        parent: parts.join("."),
      };
    }
    checkpoint(key) {
      if (key && this.editKey === key) return;
      this.undoStack.push(clone(this.workflow));
      if (this.undoStack.length > 30) this.undoStack.shift();
      this.redoStack = [];
      this.editKey = key;
    }
    emit() {
      this.trace = [];
      this.notice = ''; this.error = false;
      this.root.querySelector('.banner')?.remove();
      this.options.onChange?.(this.getWorkflow());
    }
    summary(node) {
      const v = node.value || {};
      switch (node.action) {
        case "SET":
          return `${v.name || "变量"} ← ${v.value?.$map ? "映射字典 · " + Object.keys(v.value.$map).length + " 条规则" : v.value?.$transform ? "转换 · " + v.value.$transform : text(v.value)}`;
        case "CAPTURE":
          return `${v.name || "状态"} ← ${v.source || "text"} · ${v.selector || node.target || "页面"}`;
        case "IF":
        case "GUARD":
          return v.condition?.op
            ? `${text(v.condition.left)} ${ops[v.condition.op] || v.condition.op} ${text(v.condition.right)}`
            : "组合条件";
        case "TRY":
          return `最多 ${v.attempts || 1} 次 · ${v.retry_side_effects ? "允许重复副作用" : "副作用保护开启"}`;
        case "GROUP":
          return `${v.steps?.length || 0} 个步骤 · 独立局部作用域`;
        case "LABEL":
          return v.name;
        default:
          return (
            node.selector ||
            node.target ||
            text(node.value) ||
            kinds[node.action]?.[2] ||
            "保留原始配置"
          );
      }
    }
    sequence(nodes, path = "root") {
      return `<div class="sequence ${path === "root" ? "root" : ""}">${nodes
        .map((n, i) => {
          const p = `${path}.${i}`,
            k = kinds[n.action] || [n.action, "?"],
            hits = this.trace.filter((t) => t.path === p),
            status = hits.at(-1)?.status || "";
          const branches =
            n.action === "IF"
              ? [
                  ["then", "满足条件"],
                  ["else", "否则"],
                ]
              : n.action === "GROUP"
                ? [["steps", "组内步骤"]]
                : n.action === "TRY"
                  ? [
                      ["steps", "尝试执行"],
                      ...(n.value?.fallback ? [["fallback", "失败兜底"]] : []),
                    ]
                  : [];
          return `<div class="unit"><button type="button" data-cmd="select" data-path="${p}" class="node ${controls.has(n.action) ? "control" : ""} ${this.selected === p ? "selected" : ""} ${esc(status)}" aria-pressed="${this.selected === p}"><span class="symbol">${k[1]}</span><span class="node-info"><span class="node-name">${esc(n.label || k[0])}</span><span class="node-description">${esc(this.summary(n))}</span></span><span class="node-code">${esc(n.action)}</span></button>${branches.length ? `<div class="branches ${branches.length === 1 ? "single" : ""}">${branches.map(([b, title]) => `<div class="branch ${hits.some((t) => t.branch === b) ? "taken" : ""}"><div class="branch-name"><span>${title}</span></div>${this.sequence(n.value?.[b] || [], `${p}.${b}`)}</div>`).join("")}</div><div class="merge">汇合 ↓</div>` : ""}</div>`;
        })
        .join(
          "",
        )}<button class="add" data-cmd="palette" data-path="${path}" type="button">＋ 添加步骤</button></div>`;
    }
    field(label, path, value, type = "text", choices = null) {
      const attrs = `data-field="${esc(path)}" data-type="${type}"`;
      return `<label class="field"><span>${esc(label)}</span>${
        choices
          ? `<select ${attrs}>${Object.entries(choices)
              .map(
                ([v, t]) =>
                  `<option value="${esc(v)}" ${String(value) === v ? "selected" : ""}>${esc(t)}</option>`,
              )
              .join("")}</select>`
          : type === "json"
            ? `<textarea ${attrs} spellcheck="false">${esc(JSON.stringify(value ?? {}, null, 2))}</textarea>`
            : type === "checkbox"
              ? `<input ${attrs} type="checkbox" ${value ? "checked" : ""}>启用`
              : `<input ${attrs} type="${type === "number" ? "number" : "text"}" ${type === "number" ? 'step="any"' : ""} value="${esc(text(value))}">`
      }</label>`;
    }
    condition(c) {
      if (c && !c.op)
        return `<p class="hint">这是组合条件（all / any / not）。保留完整结构，可在下方编辑。</p>${this.field("组合条件", "value.condition", c, "json")}`;
      return (
        this.field(
          "左值 · 可引用 {变量}",
          "value.condition.left",
          c?.left,
          "typed",
        ) +
        this.field(
          "比较方式",
          "value.condition.op",
          c?.op || "eq",
          "text",
          ops,
        ) +
        (!["exists", "not_exists"].includes(c?.op)
          ? this.field(
              "右值 · 数字 / 布尔 / 文本",
              "value.condition.right",
              c?.right,
              "typed",
            )
          : "")
      );
    }
    inspector() {
      if (!this.selected)
        return `<div class="eyebrow">NODE INSPECTOR</div><h3>先选一个步骤</h3><p class="hint">简单流程从上向下执行。需要时再添加分支、状态读取和兜底，普通步骤不必变复杂。</p><hr class="divider"><div class="eyebrow">STATE FIRST</div><p class="hint">读取当前状态 → 比较期望状态 → 只执行必要的动作。</p><p class="hint">所有修改仍需在配置页保存；打开编辑器不会改写原流程。</p>`;
      const n = this.node(this.selected),
        v = n.value || {},
        k = kinds[n.action] || [n.action, "?"];
      let body = this.field("步骤名称（可选）", "label", n.label || "");
      if (["SET", "CAPTURE", "LABEL"].includes(n.action))
        body += this.field(
          n.action === "LABEL" ? "锚点名称" : "变量名称",
          "value.name",
          v.name,
        );
      if (["SET", "CAPTURE"].includes(n.action))
        body += this.field(
          "作用域",
          "value.scope",
          v.scope || "local",
          "text",
          { local: "当前作用域（组内为局部）", global: "本次请求 · 全局" },
        );
      if (n.action === "SET") {
        const mode =
          typeof v.value === "object" && v.value !== null
            ? v.value.$map
              ? "map"
              : v.value.$transform
                ? "transform"
                : v.value.$var
                  ? "variable"
                  : "literal"
            : "literal";
        body += `<label class="field"><span>值的来源</span><select data-expression>${Object.entries(
          {
            literal: "常量 / 占位符",
            variable: "变量引用",
            map: "映射字典",
            transform: "轻量转换",
          },
        )
          .map(
            ([a, b]) =>
              `<option value="${a}" ${a === mode ? "selected" : ""}>${b}</option>`,
          )
          .join("")}</select></label>`;
        if (mode === "map") {
          body += this.field(
            "映射输入",
            "value.value.input",
            v.value.input,
            "typed",
          );
          body += `<div class="field"><span>原始值 → 目标值</span>${Object.entries(
            v.value.$map,
          )
            .map(
              ([a, b], i) =>
                `<div class="maprow"><input aria-label="映射原始值" data-map-key="${i}" value="${esc(a)}"><span>→</span><input aria-label="映射目标值" data-map-value="${i}" value="${esc(text(b))}"><button data-cmd="map-remove" data-index="${i}" title="删除映射">×</button></div>`,
            )
            .join("")}<button data-cmd="map-add">＋ 添加映射</button></div>`;
          body += this.field(
            "未命中时的默认值",
            "value.value.default",
            v.value.default ?? "",
            "typed",
          );
        } else if (mode === "transform")
          body +=
            this.field(
              "转换",
              "value.value.$transform",
              v.value.$transform,
              "text",
              Object.fromEntries(
                [
                  "trim",
                  "lower",
                  "upper",
                  "string",
                  "number",
                  "boolean",
                  "urlencode",
                  "css_escape",
                  "json",
                ].map((x) => [x, x]),
              ),
            ) +
            this.field("转换输入", "value.value.input", v.value.input, "typed");
        else if (mode === "variable")
          body += this.field("变量路径", "value.value.$var", v.value.$var);
        else
          body += this.field(
            "变量值",
            "value.value",
            v.value,
            typeof v.value === "object" ? "json" : "typed",
          );
        body += this.field(
          "赋值策略",
          "value.mode",
          v.mode || "assign",
          "text",
          { assign: "总是赋值", default: "变量不存在时才设置" },
        );
      }
      if (n.action === "CAPTURE") {
        body += this.field(
          "读取内容",
          "value.source",
          v.source || "text",
          "text",
          {
            text: "DOM 文本",
            attribute: "元素属性",
            class: "CSS 类名",
            url: "当前 URL",
            exists: "元素是否存在",
            value: "输入框值",
          },
        );
        if (v.source === "attribute")
          body += this.field("属性名", "value.attribute", v.attribute);
        if (v.source !== "url") body += this.selectorFields(n, true);
        body += this.field(
          "等待元素（秒，0–30）",
          "value.timeout",
          v.timeout ?? 2,
          "number",
        );
      }
      if (["IF", "GUARD"].includes(n.action))
        body += this.condition(v.condition);
      if (n.action === "GUARD") {
        body += this.field(
          "命中后",
          "value.mode",
          v.mode || "skip_group",
          "text",
          { skip_group: "跳过当前组剩余步骤", goto: "前往同级后方锚点" },
        );
        if (v.mode === "goto")
          body += this.field("锚点名称", "value.anchor", v.anchor || "");
        body +=
          '<p class="hint">只允许向前跳转，不支持无限循环。顶层跳组会结束流程。</p>';
      }
      if (n.action === "GROUP")
        body += `<details><summary>组内初始变量</summary>${this.field("局部变量对象", "value.variables", v.variables || {}, "json")}</details>`;
      if (n.action === "TRY") {
        body +=
          `<div class="pair">${this.field("最多尝试（1–5）", "value.attempts", v.attempts || 1, "number")}${this.field("间隔秒（0–5）", "value.delay", v.delay ?? 0.3, "number")}</div>` +
          this.field(
            "允许重复有副作用的动作",
            "value.retry_side_effects",
            !!v.retry_side_effects,
            "checkbox",
          ) +
          '<p class="hint">默认不会重复点击、发送或上传。只有确认幂等后才开启。响应一旦输出，始终禁止恢复。</p>';
        if (!v.fallback)
          body += '<button data-cmd="fallback">＋ 添加失败兜底分支</button>';
      }
      if (!controls.has(n.action)) {
        body += this.selectorFields(n, false);
        if (
          !["CLICK", "SELECT_MODEL", "STREAM_WAIT", "STREAM_OUTPUT"].includes(
            n.action,
          ) ||
          n.value !== undefined
        )
          body += this.field(
            "动作参数 / 输入值",
            "value",
            n.value ?? "",
            typeof n.value === "object"
              ? "json"
              : n.action === "WAIT"
                ? "number"
                : "typed",
          );
        body += this.field(
          "可选步骤（沿用原执行器规则）",
          "optional",
          !!n.optional,
          "checkbox",
        );
      }
      const destinations = [["root", "主流程末尾"]];
      walk(this.workflow, (item, path) => {
        if (path === this.selected || path.startsWith(this.selected + "."))
          return;
        for (const branch of ["then", "else", "steps", "fallback"])
          if (Array.isArray(item.value?.[branch]))
            destinations.push([
              path + "." + branch,
              (item.label || kinds[item.action]?.[0] || item.action) +
                " / " +
                branch,
            ]);
      });
      body += `<details><summary>整理流程结构</summary><label class="field"><span>移动到分支末尾</span><select data-destination>${destinations.map(([path, label]) => `<option value="${esc(path)}">${esc(label)}</option>`).join("")}</select></label><button data-cmd="move-branch">移动节点</button><p class="hint">也可以保留原节点，将它包进一个新结构：</p><div class="row-actions"><button data-cmd="wrap" data-action="GROUP">步骤组</button><button data-cmd="wrap" data-action="IF">条件分支</button><button data-cmd="wrap" data-action="TRY">重试块</button></div></details>`;
      body += `<details><summary>更多原始参数 · 保留全部字段</summary>${this.field("完整步骤 JSON", "__node", n, "json")}<p class="hint">脚本、点击验证、页面请求等已有高级字段均原样保留。JS_EXEC 由原脚本加载器处理，不做变量插值。</p></details>`;
      return `<div class="eyebrow">${esc(this.selected.replace(/^root\./, "").split(".").map(p => /^\d+$/.test(p) ? Number(p) + 1 : ({then:"满足",else:"否则",steps:"组内",fallback:"兜底"}[p] || p)).join(" · "))}</div><h3>${esc(k[0])}</h3>${this.options.onLocate ? '<button data-cmd="locate-page">◎ 在网页上查看这一步</button>' : ""}<p class="hint">${esc(k[2] || "原始步骤")}</p>${body}<hr class="divider"><div class="row-actions"><button data-cmd="up" title="在同一分支内上移">↑ 上移</button><button data-cmd="down" title="在同一分支内下移">↓ 下移</button><button data-cmd="duplicate">复制</button><button class="danger" data-cmd="remove">删除</button></div>`;
    }
    selectorFields(n, capture) {
      const p = capture ? "value.selector" : "selector";
      return (
        this.field("共享选择器名称", "target", n.target || "") +
        `<p class="hint">已有：${esc(Object.keys(this.selectors).join(" · ") || "尚未配置")}</p>` +
        this.field(
          "直接选择器（可选，优先使用）",
          p,
          capture ? n.value.selector || "" : n.selector || "",
        ) +
        (this.options.onPick
          ? '<button data-cmd="pick">◎ 从网页拾取元素</button>'
          : "")
      );
    }
    variablesPanel() {
      const rows = [];
      walk(this.workflow, (n, p) => {
        if (["SET", "CAPTURE"].includes(n.action))
          rows.push(
            `<tr><td><code>${esc(n.value?.name)}</code></td><td>${n.action === "CAPTURE" ? "页面状态" : "赋值 / 转换"}</td><td>${n.value?.scope === "global" ? "本次请求全局" : p.includes(".steps.") ? "组内局部" : "当前作用域"}</td><td><button data-cmd="select" data-path="${p}">定位步骤 ↗</button></td></tr>`,
          );
      });
      return `<div class="panel"><h3>变量与输入</h3><p class="hint">每次请求独立。外部通过 workflow_variables 注入；inputs 只读，SET 可更新本次请求中的变量副本。组内 local 变量在离开组后释放。</p><table class="table"><thead><tr><th>变量名称</th><th>来源</th><th>作用域</th><th></th></tr></thead><tbody>${rows.join("") || '<tr><td colspan="4">还没有自定义变量。普通工作流无需配置。</td></tr>'}</tbody></table><h3 style="margin-top:28px">内置上下文</h3><p class="hint"><code>{model}</code> 请求模型　 <code>{prompt}</code> 请求内容<br><code>{{inputs.name}}</code> 原始入参　 <code>{{vars.name}}</code> 当前变量　 <code>{{context.model}}</code> 内置上下文<br>完整占位符保留原始类型；嵌入文本时转为字符串。不执行任意代码。</p><button data-cmd="palette" data-path="root">＋ 添加变量或状态读取</button></div>`;
    }
    testPanel() {
      const inputsField = `<label class="field"><span>外部入参 · workflow_variables（JSON）</span><textarea data-test="inputs">${esc(this.inputs)}</textarea></label>`;
      const testFields = this.options.injected
        ? `<div class="test-grid"><label class="field"><span>测试模型（有选择模型步骤时填写）</span><input data-test="model" value="${esc(this.model)}" placeholder="例如 gemini 3.1 Pro"></label><label class="field"><span>测试正文 · 填入输入框的内容</span><textarea data-test="prompt">${esc(this.prompt)}</textarea></label></div><p class="hint">页面状态会自动读取。若某个填写步骤已配置固定文本，则仍使用该步骤的文本。测试时可点击「停止测试」。</p><details><summary>高级：自定义变量入参</summary>${inputsField}</details>`
        : `<div class="test-grid"><div><label class="field"><span>测试模型 · context.model</span><input data-test="model" value="${esc(this.model)}" placeholder="例如 gemini 3.1 Pro"></label>${inputsField}</div><div><label class="field"><span>页面状态样例 · 按捕获变量名（JSON）</span><textarea data-test="captures">${esc(this.captures)}</textarea></label><p class="hint">例如 {"current": "Pro"}。仅供模拟，真实测试会从网页读取。</p></div></div>`;
      return `<div class="panel"><h3>${this.options.injected ? "在当前网页测试" : "模拟执行路径"}</h3><p class="hint">${this.options.injected ? "真实测试会执行当前流程，包括点击和发送。编辑器执行时会隐藏，完成后自动恢复。" : "模拟只计算变量与分支，不访问网页、不点击、不发送，也不验证选择器是否真实有效。页面捕获值请填入样例。"}</p>${testFields}<button class="primary" data-cmd="test" ${this.busy ? "disabled" : ""}>${this.busy ? "计算中…" : this.options.injected ? "运行真实测试" : "模拟分支路径"}</button><div class="trace" aria-live="polite">${this.trace.length ? this.trace.map((t, i) => `<button data-cmd="select" data-path="${esc(t.path)}"><code>${String(i + 1).padStart(2, "0")}</code><span>${esc(t.action || t.op || "控制")}</span><code>${esc(t.path)}</code><span class="status">${esc(statuses[t.status] || t.status)}${t.branch ? " · " + esc(t.branch) : ""}${t.attempt ? " #" + esc(t.attempt) : ""}</span></button>`).join("") : '<p class="hint">运行后在这里查看实际选择的分支、跳转、重试与兜底路径。轨迹不记录变量值和请求内容。</p>'}</div></div>`;
    }
    render() {
      const scroll = this.root.querySelector(".canvas")?.scrollTop || 0;
      const wasOpen = !!this.root.querySelector('.workspace.is-inspecting');
      const previousInspector = this.root.querySelector('.inspector')?.innerHTML || '';
      const openInspector = this.tab === 'flow' && !!this.selected;
      let count = 0;
      walk(this.workflow, () => count++);
      let panel =
        this.tab === "flow"
          ? `<div class="workspace ${wasOpen ? 'is-inspecting' : ''}"><div class="canvas"><div class="endpoint">请求开始</div>${this.sequence(this.workflow)}<div class="endpoint end">流程结束</div></div><div class="inspector-slot" ${openInspector ? '' : 'inert aria-hidden="true"'}><aside class="inspector" aria-label="步骤设置">${this.selected ? '<button type="button" class="inspector-close" data-cmd="close-inspector" aria-label="关闭步骤设置">× 关闭设置</button>' + this.inspector() : (wasOpen ? previousInspector : '')}</aside></div></div>`
          : this.tab === "variables"
            ? this.variablesPanel()
            : this.testPanel();
      this.root.innerHTML = `<style>${window.WORKFLOW_STUDIO_CSS || ""}</style><section class="studio"><header class="head">${this.options.injected ? '<span class="drag-handle">⠿ 拖动移动</span>' : ''}<div class="actions">${this.options.onPageView ? '<button data-cmd="page-view">← 页面操作</button>' : ""}<button data-cmd="undo" ${!this.undoStack.length ? "disabled" : ""} title="撤销">↶</button><button data-cmd="redo" ${!this.redoStack.length ? "disabled" : ""} title="重做">↷</button><button data-cmd="import" title="只导入动作与工作流定位器，不导入整个预设">导入工作流</button><button data-cmd="export" title="只导出当前流程；完整预设请用站点内的导入 / 导出">导出工作流</button><button data-cmd="validate">检查流程</button>${this.options.onSave ? '<button class="primary" data-cmd="save">保存配置</button>' : ""}${this.options.onClose ? '<button data-cmd="close" title="收起编辑器">×</button>' : ""}</div></header><nav class="tabs">${[
        ["flow", "流程画布"],
        ["variables", "变量与输入"],
        ["test", "测试轨迹"],
      ]
        .map(
          ([v, t]) =>
            `<button data-cmd="tab" data-tab="${v}" class="${this.tab === v ? "active" : ""}">${t}</button>`,
        )
        .join(
          "",
        )}<span class="counter">${count} 个节点 · ${this.workflow.some((n) => controls.has(n.action)) ? "结构化流程" : "线性流程"}</span></nav>${this.notice ? `<div role="status" class="banner ${this.error ? "error" : ""}">${esc(this.notice)}</div>` : ""}${
        this.palette
          ? `<div class="palette"><div class="actions"><strong>添加到 ${esc(this.palette)}</strong><button data-cmd="cancel-palette">取消</button></div>${[
              ["基础动作", false],
              ["变量与控制", true],
            ]
              .map(
                ([title, control]) =>
                  `<h4>${title}</h4><div class="palette-grid">${Object.entries(
                    kinds,
                  )
                    .filter(([a]) => controls.has(a) === control)
                    .map(
                      ([a, k]) =>
                        `<button data-cmd="add" data-action="${a}">${k[1]} ${k[0]}<small>${k[2]}</small></button>`,
                    )
                    .join("")}</div>`,
              )
              .join("")}</div>`
          : ""
      }${panel}<footer class="footer"><span>旧步骤完整保留 · 不会在打开时迁移配置</span><span>请求隔离 / 有限重试 / 输出保护</span></footer><input class="hidden" type="file" accept="application/json,.json" data-import></section>`;
      if (this.tab === "flow") {
        this.root.querySelector(".canvas").scrollTop = scroll;
        const workspace = this.root.querySelector('.workspace');
        void workspace.offsetWidth;
        workspace.classList.toggle('is-inspecting', openInspector);
      }
    }
    refreshNode() {
      const node = this.node(this.selected),
        el = this.root.querySelector(`[data-path="${this.selected}"].node`);
      if (el) {
        el.querySelector(".node-name").textContent =
          node.label || kinds[node.action]?.[0] || node.action;
        el.querySelector(".node-description").textContent = this.summary(node);
      }
      this.root.querySelector('[data-cmd="undo"]').disabled =
        !this.undoStack.length;
    }
    input(e) {
      const el = e.target;
      if (el.dataset.test) {
        this[el.dataset.test] = el.value;
        return;
      }
      if (el.dataset.field && el.tagName !== "SELECT" && el.type !== "checkbox")
        this.updateField(el);
      if (
        el.hasAttribute("data-map-key") ||
        el.hasAttribute("data-map-value")
      ) {
        const rows = [...this.root.querySelectorAll(".maprow")].map((r) => [
          r.querySelector("[data-map-key]").value,
          typed(r.querySelector("[data-map-value]").value),
        ]);
        if (new Set(rows.map((r) => r[0])).size !== rows.length) {
          el.setCustomValidity("映射的原始值不能重复");
          el.reportValidity();
          return;
        }
        el.setCustomValidity("");
        this.checkpoint("map");
        this.node(this.selected).value.value.$map = Object.fromEntries(rows);
        this.emit();
        this.refreshNode();
      }
    }
    updateField(el) {
      try {
        let val =
          el.type === "checkbox"
            ? el.checked
            : el.dataset.type === "json"
              ? JSON.parse(el.value)
              : el.dataset.type === "number"
                ? Number(el.value)
                : el.dataset.type === "typed"
                  ? typed(el.value)
                  : el.value;
        if (typeof val === "number" && !Number.isFinite(val))
          throw Error("请输入有限数字");
        if (el.dataset.field === "__node") assertTree([val]);
        this.checkpoint(el.dataset.field);
        const n = this.node(this.selected);
        if (el.dataset.field === "__node") {
          const loc = this.location(this.selected);
          loc.list[loc.index] = val;
        } else {
          const parts = el.dataset.field.split(".");
          let dest = n;
          for (const p of parts.slice(0, -1)) {
            if (!dest[p] || typeof dest[p] !== "object") dest[p] = {};
            dest = dest[p];
          }
          const key = parts.at(-1);
          if (["selector", "label"].includes(key) && val === "")
            delete dest[key];
          else dest[key] = val;
          n.flow_version = 2;
        }
        el.setCustomValidity("");
        this.emit();
        this.refreshNode();
      } catch (err) {
        el.setCustomValidity(err.message);
        el.reportValidity();
      }
    }
    async change(e) {
      const el = e.target;
      if (el.matches("[data-import]")) {
        try {
          const file = el.files[0];
          if (!file) return;
          if (file.size > 1048576) throw Error("文件不能超过 1 MiB");
          const data = JSON.parse(await file.text());
          const value = Array.isArray(data) ? data : data.workflow;
          if (!Array.isArray(value))
            throw Error("需要工作流数组或包含 workflow 的对象");
          assertTree(value);
          await this.api("validate", { workflow: value });
          this.checkpoint();
          this.workflow = clone(value);
          if (
            data.selectors &&
            typeof data.selectors === "object" &&
            !Array.isArray(data.selectors)
          ) {
            this.selectors = clone(data.selectors);
            this.options.onSelectorsChange?.(clone(this.selectors));
          }
          this.selected = null;
          this.emit();
          this.message("已导入为草稿，请确认后保存配置。");
        } catch (err) {
          this.message(err.message, true);
        }
        return;
      }
      if (el.hasAttribute("data-expression")) {
        this.checkpoint();
        this.node(this.selected).value.value = {
          literal: "",
          variable: { $var: "inputs.name" },
          map: { $map: {}, input: "{model}", default: "{model}" },
          transform: { $transform: "trim", input: "{model}" },
        }[el.value];
        this.emit();
        this.render();
        return;
      }
      if (
        el.dataset.field &&
        (el.tagName === "SELECT" || el.type === "checkbox")
      ) {
        this.updateField(el);
        this.render();
      }
    }
    message(msg, error = false) {
      this.notice = msg;
      this.error = error;
      this.render();
    }
    async api(action, payload) {
      if (this.options.request) return this.options.request(action, payload);
      const headers = { "Content-Type": "application/json" },
        token = window.getDashboardAuthToken?.();
      if (token) headers.Authorization = "Bearer " + token;
      const r = await fetch("/api/workflow/" + action, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      const data = await r.json();
      if (!r.ok)
        throw Error(
          typeof data.detail === "string"
            ? data.detail
            : JSON.stringify(data.detail || data),
        );
      return data;
    }
    showTrace(trace, message = "", success = true) {
      this.trace = Array.isArray(trace) ? clone(trace) : [];
      this.tab = "test";
      this.message(message, !success);
    }
    closeInspector() {
      const path = this.selected; this.selected = null; this.render();
      this.root.querySelector(`.node[data-path="${path}"]`)?.focus({preventScroll:true});
    }
    async click(e) {
      const b = e.target.closest("[data-cmd]");
      if (!b || b.disabled) return;
      const cmd = b.dataset.cmd;
      try {
        if (cmd === "close-inspector") {
          this.closeInspector();
          return;
        }
        if (cmd === "select") {
          this.node(b.dataset.path);
          this.selected = b.dataset.path;
          this.options.onSelect?.(this.selected);
          this.tab = "flow";
          this.palette = null;
          this.editKey = null;
          this.render();
          this.root.querySelector('.inspector-close')?.focus({preventScroll:true});
          return;
        }
        if (cmd === "tab") {
          this.tab = b.dataset.tab;
          this.palette = null;
          this.render();
          return;
        }
        if (cmd === "palette") {
          this.palette = b.dataset.path;
          this.render();
          return;
        }
        if (cmd === "cancel-palette") {
          this.palette = null;
          this.render();
          return;
        }
        if (cmd === "undo" || cmd === "redo") {
          const from = cmd === "undo" ? this.undoStack : this.redoStack,
            to = cmd === "undo" ? this.redoStack : this.undoStack;
          if (from.length) {
            to.push(clone(this.workflow));
            this.workflow = from.pop();
            this.selected = null;
            this.emit();
            this.editKey = null;
            this.render();
          }
          return;
        }
        if (cmd === "add") {
          this.checkpoint();
          const list = this.seq(this.palette);
          list.push(defaultNode(b.dataset.action));
          this.selected = `${this.palette}.${list.length - 1}`;
          this.tab = "flow";
          this.palette = null;
          this.emit();
          this.render();
          return;
        }
        if (["up", "down", "duplicate", "remove"].includes(cmd)) {
          const loc = this.location(this.selected);
          if (
            cmd === "remove" &&
            ["IF", "GROUP", "TRY"].includes(loc.list[loc.index].action) &&
            !confirm("删除这个节点及其内部所有步骤？")
          )
            return;
          const j = loc.index + (cmd === "up" ? -1 : 1);
          if (["up", "down"].includes(cmd) && (j < 0 || j >= loc.list.length))
            return;
          this.checkpoint();
          if (cmd === "remove") {
            loc.list.splice(loc.index, 1);
            this.selected = null;
          } else if (cmd === "duplicate") {
            loc.list.splice(loc.index + 1, 0, clone(loc.list[loc.index]));
            this.selected = `${loc.parent}.${loc.index + 1}`;
          } else {
            [loc.list[j], loc.list[loc.index]] = [
              loc.list[loc.index],
              loc.list[j],
            ];
            this.selected = `${loc.parent}.${j}`;
          }
          this.emit();
          this.render();
          return;
        }
        if (cmd === "move-branch") {
          const destination =
            this.root.querySelector("[data-destination]").value;
          if (destination.startsWith(this.selected + "."))
            throw Error("不能移动到节点自身内部");
          const target = this.seq(destination),
            loc = this.location(this.selected),
            node = loc.list[loc.index];
          this.checkpoint();
          loc.list.splice(loc.index, 1);
          target.push(node);
          walk(this.workflow, (n, p) => {
            if (n === node) this.selected = p;
          });
          this.emit();
          this.render();
          return;
        }
        if (cmd === "wrap") {
          this.checkpoint();
          const loc = this.location(this.selected),
            wrapper = defaultNode(b.dataset.action);
          wrapper.value[wrapper.action === "IF" ? "then" : "steps"] = [
            loc.list[loc.index],
          ];
          loc.list[loc.index] = wrapper;
          this.emit();
          this.render();
          return;
        }
        if (cmd === "fallback") {
          this.checkpoint();
          this.node(this.selected).value.fallback = [];
          this.emit();
          this.render();
          return;
        }
        if (cmd === "map-add" || cmd === "map-remove") {
          this.checkpoint();
          const v = this.node(this.selected).value.value;
          const pairs = Object.entries(v.$map);
          if (cmd === "map-add") {
            let key = "new";
            while (pairs.some((p) => p[0] === key)) key += "_";
            pairs.push([key, ""]);
          } else pairs.splice(Number(b.dataset.index), 1);
          v.$map = Object.fromEntries(pairs);
          this.emit();
          this.render();
          return;
        }
        if (cmd === "locate-page") {
          this.options.onLocate?.(this.selected);
          return;
        }
        if (cmd === "page-view") {
          this.options.onPageView?.();
          return;
        }
        if (cmd === "pick") {
          const path = this.selected;
          this.options.onPick?.((selector) => {
            this.checkpoint();
            const n = this.node(path);
            if (n.action === "CAPTURE") n.value.selector = selector;
            else n.selector = selector;
            n.flow_version = 2;
            this.emit();
            this.render();
          });
          return;
        }
        if (cmd === "import") {
          this.root.querySelector("[data-import]").click();
          return;
        }
        if (cmd === "export") {
          const blob = new Blob(
              [
                JSON.stringify(
                  { kind: "workflow", version: 2, workflow: this.workflow, selectors: this.selectors },
                  null,
                  2,
                ),
              ],
              { type: "application/json" },
            ),
            url = URL.createObjectURL(blob),
            a = document.createElement("a");
          a.href = url;
          a.download = "workflow.json";
          a.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          return;
        }
        if (cmd === "close") {
          this.options.onClose?.();
          return;
        }
        if (cmd === "save") {
          this.options.onSave?.();
          return;
        }
        if (cmd === "validate") {
          if (this.options.onValidate) {
            this.options.onValidate(this.getWorkflow());
            return;
          }
          const signature = JSON.stringify(this.workflow);
          const r = await this.api("validate", { workflow: this.workflow });
          if (signature !== JSON.stringify(this.workflow)) { this.message('流程已修改，请重新检查。'); return; }
          this.message(`结构检查通过 · ${r.nodes} 个节点。尚未验证网页元素。`);
          return;
        }
        if (cmd === "test") {
          const inputs = JSON.parse(this.inputs),
            captures = JSON.parse(this.captures);
          if (!inputs || typeof inputs !== "object" || Array.isArray(inputs))
            throw Error("入参必须是 JSON 对象");
          const payload = {
            workflow: this.getWorkflow(),
            workflow_variables: inputs,
            capture_values: captures,
            model: this.model,
            prompt: this.prompt,
          };
          if (this.options.onTest) {
            if (
              !confirm(
                "这会在当前网页真实执行流程，可能点击、填写或发送。继续？",
              )
            )
              return;
            this.options.onTest(payload);
            return;
          }
          this.busy = true;
          const signature = JSON.stringify(this.workflow);
          this.render();
          try {
            const result = await this.api("preview", payload);
            if (signature !== JSON.stringify(this.workflow))
              throw Error("流程已修改，请重新模拟。");
            this.trace = result.trace || [];
            this.notice =
              result.message || "模拟完成 · 只计算路径，未操作网页。";
            this.error = !result.success;
          } finally {
            this.busy = false;
            this.render();
          }
          return;
        }
      } catch (err) {
        this.message(err.message || String(err), true);
      }
    }
  }
  window.WorkflowStudio = {
    mount: (host, options) => new Studio(host, options),
    walk,
    defaultNode,
  };
  window.WorkflowStudioComponent = {
    name: "WorkflowStudio",
    props: { workflow: Array, selectors: Object },
    emits: ["change", "selectors-change"],
    mounted() {
      this.studio = window.WorkflowStudio.mount(this.$refs.host, {
        workflow: this.workflow,
        selectors: this.selectors,
        onChange: (w) => this.$emit("change", w),
        onSelectorsChange: (s) => this.$emit("selectors-change", s),
      });
    },
    beforeUnmount() {
      this.studio?.destroy();
    },
    watch: {
      workflow: {
        deep: true,
        handler(w) {
          this.studio?.setWorkflow(w);
        },
      },
      selectors: {
        deep: true,
        handler(s) {
          this.studio?.setSelectors(s);
        },
      },
    },
    template: '<div ref="host"></div>',
  };
})();
