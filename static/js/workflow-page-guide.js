/* Page-first workflow editor. The tree remains authoritative; annotations are disposable views. */
(() => {
  "use strict";
  const esc = (v) =>
    String(v ?? "").replace(
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
  const names = {
    CLICK: "点击元素",
    FILL_INPUT: "填写输入",
    SELECT_MODEL: "选择模型",
    STREAM_WAIT: "等待并读取回复",
    STREAM_OUTPUT: "输出回复",
    COORD_CLICK: "点击指定坐标",
    COORD_SCROLL: "从起点滚动到终点",
    KEY_PRESS: "按下快捷键",
    WAIT: "等待",
    JS_EXEC: "执行脚本",
    PAGE_FETCH: "发送页面请求",
    READONLY_HINT: "流程说明",
    SET: "设置变量",
    CAPTURE: "读取页面状态",
    IF: "判断条件",
    SWITCH: "按情况处理",
    GROUP: "步骤组",
    GUARD: "跳过 / 跳转",
    TRY: "重试与兜底",
    LABEL: "跳转锚点",
  };
  const nodeTitle = n => n.action === "READONLY_HINT" ? window.WorkflowStudio.hintData(n).title : (n.label || names[n.action] || n.action);
  const targeted = new Set([
    "CLICK",
    "FILL_INPUT",
    "SELECT_MODEL",
    "STREAM_WAIT",
    "STREAM_OUTPUT",
    "CAPTURE",
  ]);
  const coords = new Set(["COORD_CLICK", "COORD_SCROLL"]);
  const branches = {
    then: "满足条件",
    else: "否则",
    steps: "组内",
    fallback: "失败兜底",
    default: "其他情况",
  };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(Math.max(lo, hi), v));
  const inRect = (x, y, r) =>
    x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;

  function uniqueSelector(element) {
    if (
      !(element instanceof Element) ||
      [document.body, document.documentElement].includes(element)
    )
      throw Error("请选取具体按钮、输入框或内容区域，而不是整张网页。");
    if (["iframe", "frame"].includes(element.localName))
      throw Error(
        "不能把内嵌网页当作内部按钮。请在对应页面中打开编辑器，或在高级设置中配置定位。",
      );
    if (element.getRootNode() !== document)
      throw Error(
        "此元素位于 Shadow DOM 内，请在高级编排中配置执行器支持的定位方式。",
      );
    const unique = (s) => {
      try {
        return (
          document.querySelectorAll(s).length === 1 &&
          document.querySelector(s) === element
        );
      } catch {
        return false;
      }
    };
    if (element.id) {
      const s = "#" + CSS.escape(element.id);
      if (unique(s)) return s;
    }
    for (const attr of [
      "data-testid",
      "data-test",
      "data-id",
      "aria-label",
      "name",
    ]) {
      const value = element.getAttribute(attr);
      if (value) {
        const s = `${element.localName}[${attr}="${CSS.escape(value)}"]`;
        if (unique(s)) return s;
      }
    }
    const path = [];
    let current = element;
    while (current && current !== document.documentElement) {
      let part = current.localName;
      const same = [...current.parentElement.children].filter(
        (e) => e.localName === current.localName,
      );
      if (same.length > 1) part += `:nth-of-type(${same.indexOf(current) + 1})`;
      path.unshift(part);
      const s = path.join(" > ");
      if (unique(s)) return s;
      current = current.parentElement;
    }
    throw Error("无法生成唯一定位器，配置未改动。");
  }

  function resolve(selector, prepared = false) {
    if (typeof selector !== "string" || !selector.trim())
      return { reason: "尚未绑定目标" };
    if (!prepared && /\{\{?\s*[A-Za-z_]\w*(?:\.\w+)*\s*\}\}?/.test(selector))
      return { reason: "运行时变量定位；需执行到此步骤后确认" };
    try {
      let nodes;
      const raw = selector.trim();
      if (raw.startsWith("xpath:") || raw.startsWith("//")) {
        const snapshot = document.evaluate(
          raw.replace(/^xpath:/, ""),
          document,
          null,
          XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,
          null,
        );
        nodes = Array.from({ length: snapshot.snapshotLength }, (_, i) =>
          snapshot.snapshotItem(i),
        ).filter((n) => n instanceof Element);
      } else {
        let css = raw.replace(/^css:/, "");
        if (/^tag:[\w-]+$/.test(css)) css = css.slice(4);
        else if (/^@[\w-]+=/.test(css)) {
          const i = css.indexOf("=");
          css = `[${css.slice(1, i)}="${CSS.escape(css.slice(i + 1))}"]`;
        }
        nodes = [...document.querySelectorAll(css)];
      }
      const element = nodes[0];
      if (!element)
        return { reason: "页面暂无目标；可能尚未展开菜单或进入分支" };
      return { element, count: nodes.length };
    } catch {
      return { reason: "定位语法无法在页面预览；请重新选取或用真实测试确认" };
    }
  }

  // Delegate to stable hosts: a Studio re-render must not remove the drag handle.
  function draggable(target, accepts) {
    let drag = null,
      suppress = false,
      timer = null;
    const place = (x, y) => {
      const r = target.getBoundingClientRect();
      target.style.left = clamp(x, 8, innerWidth - r.width - 8) + "px";
      target.style.top =
        clamp(y, 8, innerHeight - Math.min(r.height, innerHeight - 16) - 8) +
        "px";
      target.style.right = "auto";
      target.style.bottom = "auto";
    };
    const resize = () => {
      if (target.style.left && target.style.display !== "none") {
        const r = target.getBoundingClientRect();
        place(r.left, r.top);
      }
    };
    const end = (e) => {
      if (!drag) return;
      if (e?.type === "pointercancel" || e?.key === "Escape")
        place(drag.left, drag.top);
      else if (drag.moved) {
        suppress = true;
        clearTimeout(timer);
        timer = setTimeout(() => (suppress = false), 500);
      }
      try {
        target.releasePointerCapture(drag.id);
      } catch {}
      drag = null;
      window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", end, true);
      window.removeEventListener("pointercancel", end, true);
      window.removeEventListener("keydown", key, true);
    };
    const key = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        end(e);
      }
    };
    const move = (e) => {
      if (!drag || e.pointerId !== drag.id) return;
      const dx = e.clientX - drag.x,
        dy = e.clientY - drag.y;
      if (Math.hypot(dx, dy) > 5) drag.moved = true;
      if (drag.moved) {
        e.preventDefault();
        e.stopPropagation();
        place(drag.left + dx, drag.top + dy);
      }
    };
    const start = (e) => {
      if (e.button !== 0 || !accepts(e)) return;
      const r = target.getBoundingClientRect();
      drag = {
        id: e.pointerId,
        x: e.clientX,
        y: e.clientY,
        left: r.left,
        top: r.top,
        moved: false,
      };
      target.setPointerCapture(e.pointerId);
      e.preventDefault();
      e.stopPropagation();
      window.addEventListener("pointermove", move, true);
      window.addEventListener("pointerup", end, true);
      window.addEventListener("pointercancel", end, true);
      window.addEventListener("keydown", key, true);
    };
    const click = (e) => {
      if (suppress) {
        e.preventDefault();
        e.stopImmediatePropagation();
        suppress = false;
      }
    };
    target.addEventListener("pointerdown", start);
    target.addEventListener("click", click, true);
    window.addEventListener("resize", resize);
    return () => {
      end();
      clearTimeout(timer);
      target.removeEventListener("pointerdown", start);
      target.removeEventListener("click", click, true);
      window.removeEventListener("resize", resize);
    };
  }

  const style = `:host{all:initial;position:fixed;inset:0;z-index:2147483644;pointer-events:none;color:#33402c;font:13px/1.5 system-ui,-apple-system,sans-serif;--paper:#f7f3ea;--card:#fffdf8;--line:#dcdccc;--muted:#747a69;--ink:#33402c;--olive:#5d6b4d}*{box-sizing:border-box}button,input,textarea,select{font:inherit;color:inherit}button{cursor:pointer;background:var(--card);border:1px solid var(--line);border-radius:7px;padding:7px 10px}button:hover{border-color:var(--olive)}button:disabled{opacity:.4;cursor:default}input[type=checkbox]{accent-color:var(--olive)}button:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--olive);outline-offset:2px}.panel{pointer-events:auto;position:fixed;right:20px;top:64px;width:350px;max-width:calc(100vw - 24px);max-height:calc(100vh - 90px);display:flex;flex-direction:column;background:var(--paper);border:1px solid var(--line);border-radius:15px;box-shadow:0 14px 54px #0002;overflow:hidden;color:var(--ink)}header{padding:15px 17px 12px;cursor:grab;touch-action:none;user-select:none;border-bottom:1px solid var(--line)}.headrow,.tools,.pager{display:flex;align-items:center;gap:6px;justify-content:space-between}.eyebrow{font-size:9px;letter-spacing:1.5px;color:var(--muted);font-weight:700}h2{margin:3px 0;font-size:19px;letter-spacing:-.5px}h3{font-size:14px;margin:0 0 7px}.muted{color:var(--muted);font-size:11px}.intro{margin:0;padding:10px 17px;background:var(--card);font-size:11px;color:var(--muted);border-bottom:1px solid var(--line);line-height:1.8}.tools{padding:9px 13px;font-size:11px;flex-wrap:wrap}.tools label{display:flex;align-items:center;gap:5px}.list{overflow:auto;min-height:90px;max-height:30vh;padding:0 10px 9px;flex:1}.row{display:flex;align-items:center;gap:8px;margin:5px 0;padding:8px;border:1px solid transparent;border-radius:9px;background:var(--card)}.row.active{border-color:var(--olive);box-shadow:inset 3px 0 var(--olive)}.row .select{border:0;background:none;flex:1;text-align:left;padding:0;min-width:0}.row strong{font-size:12px;display:block}.row small{display:block;font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:245px}.number{display:grid;place-items:center;flex-shrink:0;width:28px;height:28px;padding:0;border-radius:50%;background:var(--paper);color:var(--olive);font-size:11px;font-weight:700}.draggable{cursor:grab;touch-action:none;user-select:none}.detail{border-top:1px solid var(--line);padding:13px 17px;background:var(--card);max-height:30vh;overflow:auto;flex-shrink:0}.explain{margin:5px 0 10px;font-size:11px;line-height:1.8;color:var(--muted);overflow-wrap:anywhere}.detail label{display:block;font-size:11px;margin:9px 0}.detail input,.detail textarea,.detail select{display:block;width:100%;padding:7px;border:1px solid var(--line);background:var(--paper);border-radius:6px;margin-top:5px}.detail textarea{resize:vertical;min-height:54px}.detail input[type=checkbox]{display:inline;width:auto}.detail details{font-size:10px;color:var(--muted);margin:10px 0}.detail code{display:block;white-space:pre-wrap;overflow-wrap:anywhere;padding:6px 0}.pager{padding:10px 14px;border-top:1px solid var(--line);font-size:11px}.bottom{padding:10px 14px;border-top:1px solid var(--line);display:flex;gap:7px;align-items:center;flex-wrap:wrap}.primary{background:var(--olive);color:var(--card);border-color:var(--olive)}.notice{padding:8px 14px;border-top:1px solid var(--line);font-size:11px;max-height:75px;overflow:auto;color:var(--olive);background:var(--card)}.notice.error{color:#a45039}.pin{pointer-events:auto;position:fixed;transform:translate(-50%,-50%);width:33px;height:33px;padding:0;border-radius:50%;border:2px solid var(--card);background:var(--olive);color:var(--card);box-shadow:0 0 0 1px var(--olive),0 2px 7px #0003;display:grid;place-items:center;font-size:12px;font-weight:700;cursor:grab;touch-action:none;user-select:none}.pin.active{box-shadow:0 0 0 4px #8da97555,0 3px 9px #0003}.halo{position:fixed;border:2px solid var(--olive);background:#95ae7618;border-radius:5px;pointer-events:none}.halo.error{border-color:#b96048;background:#b9604815}.caption{position:fixed;max-width:330px;background:var(--ink);color:var(--card);border-radius:6px;padding:6px 10px;font-size:11px;pointer-events:none}.leader{position:fixed;inset:0;width:100%;height:100%;pointer-events:none;overflow:visible}.leader line{stroke:var(--olive);stroke-width:1.5;stroke-dasharray:4 4}.cross{position:fixed;width:12px;height:12px;transform:translate(-50%,-50%);pointer-events:none;color:var(--olive)}.cross:before,.cross:after{content:'';position:absolute;background:currentColor}.cross:before{height:1px;width:12px;top:6px}.cross:after{width:1px;height:12px;left:6px}.pick-surface{position:fixed;inset:0;pointer-events:auto;cursor:crosshair;touch-action:none}.pick-tip{position:fixed;top:16px;left:50%;transform:translateX(-50%);background:var(--ink);color:var(--card);padding:11px 17px;border-radius:10px;font-size:12px;pointer-events:auto;box-shadow:0 4px 20px #0002;max-width:90vw}.pick-tip button{margin-left:10px} .drag-dot{position:fixed;pointer-events:none;width:35px;height:35px;transform:translate(-50%,-50%);border-radius:50%;background:var(--olive);color:white;display:grid;place-items:center;font-weight:700}.hidden{display:none!important}.invalid{color:#a45039}@media(max-width:500px){.panel{right:12px;top:12px;width:320px;max-height:calc(100vh - 75px)}.list{max-height:24vh}.detail{max-height:26vh}}:host([data-dark]){--paper:#252b24;--card:#30372d;--line:#495241;--muted:#aab39c;--ink:#e0e3d5;--olive:#a2b288}.caption,.pick-tip{color:var(--card)}`;


  const layoutStyle = `
    :host{color-scheme:light}:host([data-dark]){color-scheme:dark}
    [hidden]{display:none!important}
    .panel{top:20px;width:340px;max-height:calc(100vh - 90px);max-height:min(660px,calc(100dvh - 90px))}
    header{padding:13px 15px;flex-shrink:0}h2{font-size:16px;margin:0 0 2px;letter-spacing:0}
    header .muted{font-size:10px}header button{font-size:11px;padding:6px 9px;white-space:nowrap}
    .tools{padding:9px 14px;flex-shrink:0;gap:8px;border-bottom:1px solid var(--line)}
    .tools label{font-size:11px}.tools input{margin:0}.tools label:last-child{color:var(--muted)}
    .view-tabs{display:flex;gap:4px;padding:7px 12px;flex-shrink:0;background:var(--paper)}
    .view-tabs button{flex:1;font-size:12px;padding:6px;border-color:transparent;background:transparent;color:var(--muted)}
    .view-tabs [aria-pressed=true]{background:var(--card);border-color:var(--line);color:var(--ink);font-weight:600}
    .guide-body{min-height:0;overflow:auto;overscroll-behavior:contain;flex:1 1 auto;scrollbar-width:thin;scrollbar-color:var(--line) transparent;border-top:1px solid var(--line)}
    .list{overflow:visible;min-height:0;max-height:none;padding:5px 10px 10px}.row{margin:5px 0;padding:9px 8px}.row strong{font-size:12px;line-height:1.5;overflow-wrap:anywhere}.row small{max-width:245px}
    .detail{border-top:0;padding:15px;max-height:none;overflow:visible;flex-shrink:1;min-height:0}
    .detail h3{font-size:14px}.detail .explain{margin-bottom:12px}.detail > button{font-size:11px;margin:0 3px 7px 0}
    .pager{flex-shrink:0;padding:8px 12px;background:var(--paper);font-size:10px}.pager button{padding:6px 9px;font-size:11px}
    .bottom{flex-shrink:0;padding:10px 12px;display:grid;grid-template-columns:1fr 1fr;gap:6px;background:var(--paper)}
    .bottom button{font-size:12px;padding:7px 5px;min-width:0}.bottom .secondary{border-color:transparent;background:transparent;font-size:11px;color:var(--muted);padding:3px 5px}
    .notice{flex-shrink:0;max-height:60px;scrollbar-width:thin}
    .caption{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:min(280px,calc(100vw - 16px))}
    @media(max-width:500px){.panel{right:12px;top:12px;width:320px;max-height:calc(100dvh - 76px)}.list,.detail{max-height:none}}
    @media(max-height:480px){.panel{top:10px;max-height:calc(100dvh - 20px)}header{padding:8px 12px}.tools{padding:6px 12px}.view-tabs{padding:4px 12px}.bottom{padding:6px 12px;gap:3px}.pager{padding:5px 12px}}
  `;

  class Guide {
    constructor(options) {
      this.options = options;
      this.studio = options.studio;
      this.visible = true;
      this.annotationsActive = true;
      this.markersEnabled = true;
      this.view = "list";
      this.listScroll = 0;
      this.testing = false;
      this.selected = null;
      this.all = true;
      this.trace = [];
      this.message = "";
      this.saved = JSON.stringify({
        workflow: this.studio.getWorkflow(),
        selectors: this.studio.selectors,
      });
      this.destroyed = false;
      this.host = document.createElement("div");
      this.host.id = "wfe-page-guide";
      this.root = this.host.attachShadow({ mode: "open" });
      this.root.innerHTML = `<style>${style}${layoutStyle}</style><div class="annotations"></div><section class="panel" aria-label="页面操作可视化"><header><div class="headrow"><div><h2>页面步骤</h2><span class="muted">拖动标题移动 · 只查看，不执行</span></div><button data-command="hide" title="仅收起面板，保留页面上的步骤标记" aria-label="收起面板，保留标记">收起 ↘</button></div></header><div class="tools"></div><nav class="view-tabs" aria-label="页面步骤视图"><button data-command="list-view" aria-pressed="true">步骤列表</button><button data-command="detail-view" aria-pressed="false">步骤设置</button></nav><div class="guide-body"><div class="list"></div><div class="detail" hidden></div></div><div class="pager"></div><div class="notice" role="status" hidden></div><div class="bottom"><button data-command="save" class="primary">保存配置</button><button data-command="test">真实测试…</button><button data-command="trace" class="secondary">执行轨迹</button><button data-command="advanced" class="secondary">分支与变量 ↗</button></div></section><div class="picking"></div>`;
      document.body.append(this.host);
      this.panel = this.root.querySelector(".panel");
      this.root.addEventListener("click", (e) => this.click(e));
      this.root.addEventListener("change", (e) => {
        if (!e.target.dataset.edit) this.change(e);
      });
      this.root.addEventListener("input", (e) => {
        if (e.target.dataset.edit) this.change(e);
      });
      this.root.addEventListener("pointerdown", (e) => this.startPinDrag(e));
      this.unDrag = draggable(
        this.panel,
        (e) =>
          e.composedPath().some((n) => n.tagName === "HEADER") &&
          !e
            .composedPath()
            .some((n) =>
              ["BUTTON", "INPUT", "SELECT", "TEXTAREA"].includes(n.tagName),
            ),
      );
      this.resetClick = () => {
        this.suppressClick = false;
      };
      window.addEventListener("pointerdown", this.resetClick, true);
      this.blockClick = (e) => {
        if (this.suppressClick) {
          e.preventDefault();
          e.stopImmediatePropagation();
          this.suppressClick = false;
        }
      };
      window.addEventListener("click", this.blockClick, true);
      this.layout = () => {
        if (!this.raf)
          this.raf = requestAnimationFrame(() => {
            this.raf = null;
            if (!this.destroyed && !this.drag) this.paint();
          });
      };
      window.addEventListener("scroll", this.layout, true);
      window.addEventListener("resize", this.layout);
      this.key = (e) => {
        if (e.key === "Escape" && (this.drag || this.picking)) {
          e.preventDefault();
          e.stopImmediatePropagation();
          this.cancelDrag();
          this.endPick();
        }
      };
      window.addEventListener("keydown", this.key, true);
      this.observer = new MutationObserver((records) => {
        if (
          records.some(
            (r) => !this.host.contains(r.target) && r.target !== this.host,
          )
        )
          this.layout();
      });
      this.observer.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["style", "class", "hidden"],
      });
      this.timer = setInterval(() => {
        this.host.toggleAttribute(
          "data-dark",
          document.documentElement.classList.contains("dark"),
        );
        this.layout();
      }, 600);
      this.refresh();
    }
    rows() {
      const rows = [];
      window.WorkflowStudio.walk(this.studio.workflow, (node, path) =>
        rows.push({ node, path, number: rows.length + 1 }),
      );
      return rows;
    }
    current() {
      return this.rows().find((r) => r.path === this.selected);
    }
    canTarget(n) {
      return (
        targeted.has(n.action) &&
        !(n.action === "CAPTURE" && n.value?.source === "url")
      );
    }
    selector(n) {
      return n.action === "CAPTURE"
        ? n.value?.selector || this.studio.selectors[n.target] || ""
        : n.selector || this.studio.selectors[n.target] || "";
    }
    location(n) {
      if (coords.has(n.action)) return { coordinate: true };
      return this.canTarget(n)
        ? resolve(this.selector(n))
        : { reason: "此步骤没有固定页面目标" };
    }
    crumb(path) {
      return path
        .split(".")
        .slice(1, -1)
        .filter((p) => branches[p] || /^case_\d+$/.test(p))
        .map((p) => branches[p] || "情况 " + (Number(p.slice(5))+1))
        .join(" / ");
    }
    describe(n) {
      if (n.action === "CLICK" && n.target === "send_btn")
        return "发送消息并等待发送确认。重新绑定时请选择网页上的发送按钮；真实测试会实际发送。";
      const map = {
        CLICK:
          "点击标记指向的网页元素。拖到另一个元素可重新绑定；不会连带修改使用相同共享定位器的其他步骤。",
        FILL_INPUT: "在标记的输入区域填写内容。留空时使用本次请求的正文。",
        SELECT_MODEL:
          "在标记位置选择请求指定的模型，具体模型名在真实测试中填写。",
        STREAM_WAIT: "监听并读取标记区域的回复；并不是点击这个区域。",
        STREAM_OUTPUT: "提取并输出标记区域的回复。",
        COORD_CLICK:
          "点击固定视口坐标。拖动标记精确修改点击位置；页面滚动或尺寸变化后需重新确认。",
        COORD_SCROLL: "从「起」坐标滚动到「终」坐标；可分别拖动两端。",
        CAPTURE: "读取标记元素的状态并存入变量；不会点击元素。",
        WAIT: `等待 ${n.value ?? 0.5} 秒，然后继续。此步骤没有点击位置。`,
        KEY_PRESS: `按下 ${n.value || "Enter"}。按键作用于执行时的焦点，不是一个固定位置。`,
        JS_EXEC:
          "执行自定义脚本，影响取决于脚本内容。这里不会假装它有一个固定点击位置；请在编排检查器中查看脚本。",
        PAGE_FETCH: "通过页面发出网络请求，没有固定点击位置。",
        IF: "根据条件选择一侧分支。列表是设计顺序，两侧不会同时执行；真实路径以测试轨迹为准。",
        SWITCH: "从上到下检查情况，只执行第一个符合的分支；都不符合则执行其他情况。页面序号是设计顺序，不代表所有分支都会执行。",
        GROUP: "按顺序执行组内步骤，并建立局部变量作用域。",
        TRY: "尝试执行组内步骤，按配置重试或走失败兜底；两条路径不是连续执行。",
        SET: "赋值、映射或转换变量；不操作网页。",
        GUARD: "满足条件时跳过步骤组或前往锚点。",
        LABEL: "标记一个可跳转的位置；不操作网页。",
        READONLY_HINT: "给维护者看的说明，不是点击动作。",
      };
      return map[n.action] || "此动作的细节请在编排检查器中查看。";
    }
    refresh() {
      const body = this.root.querySelector(".guide-body");
      const scroll = body.scrollTop;
      const rows = this.rows();
      if (!rows.some((r) => r.path === this.selected))
        this.selected = rows[0]?.path || null;
      this.root.querySelector(".tools").innerHTML =
        `<label title="数字对应步骤；拖动数字到新目标可修改定位。收起面板后仍保留标记。"><input type="checkbox" data-show-markers ${this.markersEnabled ? "checked" : ""}>页面标记</label><label title="关闭后只显示当前步骤的位置"><input type="checkbox" data-show-all ${this.all ? "checked" : ""} ${this.markersEnabled ? "" : "disabled"}>显示全部位置</label>`;
      this.root.querySelector(".list").innerHTML =
        rows
          .map(
            (r) =>
              `<div class="row ${r.path === this.selected ? "active" : ""}" data-row="${r.path}"><button class="number ${this.canTarget(r.node) || coords.has(r.node.action) ? "draggable" : ""}" ${this.canTarget(r.node) || coords.has(r.node.action) ? `data-pin="${r.path}" data-end="start"` : ""} data-select="${r.path}" aria-label="第 ${r.number} 步${this.canTarget(r.node) || coords.has(r.node.action) ? "，拖到网页目标" : ""}">${r.number}</button><button class="select" data-select="${r.path}"><strong>${esc(nodeTitle(r.node))}</strong><small>${esc(this.crumb(r.path) || r.node.action)}${this.trace.some((t) => t.path === r.path) ? " · " + esc(this.trace.filter((t) => t.path === r.path).at(-1).status) : ""}</small></button></div>`,
          )
          .join("") ||
        '<p class="explain">还没有步骤。点击「分支与变量」添加动作，或导入现有工作流。</p>';
      const row = this.current();
      if (!this.editing) this.renderDetail(row);
      const i = rows.findIndex((r) => r.path === this.selected);
      this.root.querySelector(".pager").innerHTML =
        `<button data-command="previous" ${i <= 0 ? "disabled" : ""}>← 上一步</button><span>${i < 0 ? 0 : i + 1} / ${rows.length} · 设计序号</span><button data-command="next" ${i < 0 || i >= rows.length - 1 ? "disabled" : ""}>下一步 →</button>`;
      body.scrollTop = scroll;
      this.updateDirty();
      this.paint();
    }
    targetMessage(loc) {
      if(!loc.element) return loc.reason;
      const rect=loc.element.getBoundingClientRect();
      if(!rect.width || !rect.height || getComputedStyle(loc.element).visibility==='hidden') return '目标存在但当前不可见，请先展开菜单或进入相应页面状态。';
      return loc.count>1 ? `匹配 ${loc.count} 个元素，预览首个。建议重新选取唯一目标。` : '已找到目标；点击「定位目标」滚动到它。';
    }
    renderDetail(row) {
      const el = this.root.querySelector(".detail");
      if (!row) {
        el.innerHTML = "";
        return;
      }
      const n = row.node,
        loc = this.location(n);
      let fields = "";
      if (n.action === "WAIT")
        fields =
          '<label>等待秒数<input data-edit="wait" type="number" min="0" max="600" step="0.1" value="' +
          esc(n.value ?? 0.5) +
          '"></label>';
      if (n.action === "KEY_PRESS")
        fields =
          '<label>快捷键<input data-edit="key" value="' +
          esc(n.value || "Enter") +
          '" placeholder="Enter 或 Ctrl+Enter"></label>';
      if (n.action === "FILL_INPUT")
        fields =
          '<label>输入内容（留空使用请求正文）<textarea data-edit="input">' +
          esc(n.value ?? "") +
          "</textarea></label>";
      if (n.action === "READONLY_HINT")
        fields =
          '<p class="explain hint-content" style="white-space:pre-wrap">' +
          esc(window.WorkflowStudio.hintData(n).text) +
          "</p>";
      el.innerHTML = `<h3>${row.number}. ${esc(nodeTitle(n))}</h3><p class="explain">${esc(this.describe(n))}</p>${this.canTarget(n) ? `<p class="explain target-status">${esc(this.targetMessage(loc))}</p><button data-command="locate">◎ 定位目标</button> <button data-command="pick">重新选取元素</button><details><summary>技术细节 · 当前定位器</summary><code>${esc(this.selector(n) || "尚未绑定")}</code></details>` : ""}${coords.has(n.action) ? '<button data-command="pick">在页面指定' + (n.action === "COORD_SCROLL" ? "起点" : "坐标") + "</button>" : ""}${fields}<button data-command="edit">编辑这一步的更多设置 ↗</button>`;
    }
    updateDirty() {
      const dirty =
        this.saved !==
        JSON.stringify({
          workflow: this.studio.getWorkflow(),
          selectors: this.studio.selectors,
        });
      this.root.querySelector("[data-command=save]").textContent = dirty
        ? "保存配置 · 有修改"
        : "保存配置";
    }
    savedAs(workflow, selectors) {
      this.saved = JSON.stringify({ workflow, selectors });
      this.updateDirty();
    }
    notify(message, error = false) {
      this.message = message;
      const el = this.root.querySelector(".notice");
      el.hidden = !message;
      el.textContent = message;
      el.classList.toggle("error", error);
    }
    select(path, scroll = false) {
      if (!this.rows().some((r) => r.path === path)) return;
      this.selected = path;
      this.refresh();
      if (scroll) {
        const loc = this.location(this.current().node);
        loc.element?.scrollIntoView({
          block: "center",
          inline: "nearest",
          behavior: "instant",
        });
        this.layout();
      }
      if (this.visible && this.view === "list") this.root.querySelector(`[data-row="${path}"]`)?.scrollIntoView({block:"nearest",inline:"nearest"});
      this.options.onSelect?.(path);
    }
    setView(view) {
      const body = this.root.querySelector(".guide-body");
      if (this.view === "list") this.listScroll = body.scrollTop;
      this.view = view;
      this.root.querySelector(".list").hidden = view !== "list";
      this.root.querySelector(".detail").hidden = view !== "detail";
      this.root.querySelector("[data-command=list-view]").setAttribute("aria-pressed", String(view === "list"));
      this.root.querySelector("[data-command=detail-view]").setAttribute("aria-pressed", String(view === "detail"));
      body.scrollTop = view === "list" ? this.listScroll : 0;
    }
    show(value, {keepMarkers = false} = {}) {
      this.visible = value;
      this.annotationsActive = value || keepMarkers;
      this.panel.style.display = value && !this.testing ? "" : "none";
      if (!value) {
        this.endPick();
        this.cancelDrag();
      }
      this.paint();
    }
    setTesting(value) {
      this.testing = value;
      this.panel.style.display = this.visible && !value ? "" : "none";
      if (value) {
        this.endPick();
        this.cancelDrag();
      } else {
        this.testPath = null;
        this.testDetail = null;
      }
      this.paint();
    }
    progress(path, detail = null) {
      if (!this.rows().some((r) => r.path === path)) return;
      this.testPath = path;
      this.testDetail = detail;
      this.paint();
    }
    result(trace, message, success) {
      this.trace = Array.isArray(trace) ? trace : [];
      this.notify(message, !success);
      const fail = this.trace.find((t) => t.status === "failed");
      if (fail && this.rows().some((r) => r.path === fail.path))
        this.selected = fail.path;
      this.refresh();
    }
    points(row) {
      const n = row.node;
      if (coords.has(n.action)) {
        const v =
          (this.testing && row.path === this.testPath
            ? this.testDetail?.coordinates
            : null) ||
          n.value ||
          {};
        const pairs =
          n.action === "COORD_SCROLL"
            ? [
                ["start", v.start_x, v.start_y],
                ["end", v.end_x, v.end_y],
              ]
            : [["start", v.x, v.y]];
        return pairs
          .filter(
            ([, x, y]) =>
              typeof x === "number" &&
              Number.isFinite(x) &&
              typeof y === "number" &&
              Number.isFinite(y),
          )
          .map(([end, x, y]) => ({ x, y, end, row, coordinate: true }));
      }
      const loc =
        this.testing && row.path === this.testPath && this.testDetail?.selector
          ? resolve(this.testDetail.selector, true)
          : this.location(n);
      if (!loc.element) return [];
      const r = loc.element.getBoundingClientRect();
      if (
        !r.width ||
        !r.height ||
        getComputedStyle(loc.element).visibility === "hidden"
      )
        return [];
      return [
        {
          x: r.left + r.width / 2,
          y: r.top + r.height / 2,
          end: "start",
          row,
          rect: r,
          element: loc.element,
        },
      ];
    }
    paint() {
      if (this.drag || this.picking || this.destroyed) return;
      const layer = this.root.querySelector(".annotations");
      if ((!this.annotationsActive || !this.markersEnabled) && !this.testing) {
        layer.replaceChildren();
        this.lastPaint = null;
        return;
      }
      const rows = this.rows(),
        active = this.testing ? this.testPath : this.selected;
      const status=this.root.querySelector('.target-status');
      const current=rows.find(r=>r.path===this.selected);
      if(status && current && !this.testing) status.textContent=this.targetMessage(this.location(current.node));
      let marks = "",
        lines = "",
        highlight = "";
      const occupied = new Map();
      for (const row of rows) {
        if (
          this.testing ? row.path !== active : !this.all && row.path !== active
        )
          continue;
        const pts = this.points(row);
        if (pts.length === 2)
          lines += `<line x1="${pts[0].x}" y1="${pts[0].y}" x2="${pts[1].x}" y2="${pts[1].y}"/>`;
        for (const p of pts) {
          if (p.x < 0 || p.y < 0 || p.x > innerWidth || p.y > innerHeight)
            continue;
          const key = Math.round(p.x / 8) + ":" + Math.round(p.y / 8),
            overlap = occupied.get(key) || 0;
          occupied.set(key, overlap + 1);
          const x = clamp(
              p.x + (p.coordinate ? 0 : overlap * 39),
              20,
              innerWidth - 20,
            ),
            y = clamp(p.y, 20, innerHeight - 20);
          if (x !== p.x || y !== p.y)
            lines += `<line x1="${p.x}" y1="${p.y}" x2="${x}" y2="${y}"/>`;
          if (!this.testing)
            marks += `<span class="cross" style="left:${p.x}px;top:${p.y}px"></span><button class="pin ${row.path === active ? "active" : ""}" data-pin="${row.path}" data-end="${p.end}" data-select="${row.path}" style="left:${x}px;top:${y}px" title="${row.number}. ${esc(row.node.label || names[row.node.action])} · 拖动修改${p.coordinate ? "坐标" : "元素"}" aria-label="页面标记 ${row.number}${row.node.action === "COORD_SCROLL" ? " " + p.end : ""}">${row.number}${row.node.action === "COORD_SCROLL" ? (p.end === "end" ? "终" : "起") : ""}</button>`;
          if (row.path === active) {
            const r = p.rect || {
              left: p.x - 18,
              top: p.y - 18,
              width: 36,
              height: 36,
            };
            const failed = this.trace.some(
              (t) => t.path === row.path && t.status === "failed",
            );
            highlight += `<div class="halo ${failed ? "error" : ""}" style="left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px"></div>`;
            if (p.end === "start")
              highlight += `<div class="caption" style="left:${clamp(r.left, 8, innerWidth - 335)}px;top:${clamp(r.top - 33, 8, innerHeight - 40)}px">${this.testing ? "正在执行" : "查看位置"} · ${row.number} ${esc(row.node.label || names[row.node.action])}</div>`;
          }
        }
      }
      const markup = `<svg class="leader">${lines}</svg>${highlight}${marks}`;
      if (markup !== this.lastPaint) {
        layer.innerHTML = markup;
        this.lastPaint = markup;
      }
    }
    overEditor(x, y) {
      if (
        this.panel.style.display !== "none" &&
        inRect(x, y, this.panel.getBoundingClientRect())
      )
        return true;
      return (this.options.editorElements?.() || []).some(
        (e) =>
          e &&
          getComputedStyle(e).display !== "none" &&
          inRect(x, y, e.getBoundingClientRect()),
      );
    }
    normalizeTarget(node, element) {
      if (!element) return element;
      if (node.action === "FILL_INPUT") {
        const input =
          element.closest(
            'textarea,input,[contenteditable]:not([contenteditable="false"])',
          ) || element.closest("label")?.control;
        if (!input)
          throw Error("请选中输入框或可编辑区域，而不是它旁边的文字。");
        return input;
      }
      if (["CLICK", "SELECT_MODEL"].includes(node.action))
        return (
          element.closest(
            'button,a,[role="button"],[role="menuitem"],[role="option"],[role="tab"],[role="combobox"],summary',
          ) || element
        );
      return element;
    }
    hit(x, y) {
      if (
        this.panel.style.display !== "none" &&
        inRect(x, y, this.panel.getBoundingClientRect())
      )
        return null;
      for (const e of this.options.editorElements?.() || [])
        if (
          e &&
          getComputedStyle(e).display !== "none" &&
          inRect(x, y, e.getBoundingClientRect())
        )
          return null;
      this.host.style.display = "none";
      let element = document.elementFromPoint(x, y);
      this.host.style.display = "";
      if (
        !element ||
        element === document.body ||
        element === document.documentElement ||
        element.closest('[id^="wfe-"],.wfe-test-status,.wfe-toast')
      )
        return null;
      return element;
    }
    commit(path, point, element, end = "start") {
      if (this.testing) throw Error("真实测试期间不能修改流程");
      const node = this.studio.node(path);
      let selector;
      if (this.overEditor(point.x, point.y))
        throw Error("请拖到网页区域，不是编辑器面板上。");
      if (!coords.has(node.action)) {
        selector = uniqueSelector(this.normalizeTarget(node, element));
      }
      this.studio.checkpoint();
      if (coords.has(node.action)) {
        const value = { ...(node.value || {}) };
        if (node.action === "COORD_CLICK") {
          value.x = Math.round(point.x);
          value.y = Math.round(point.y);
        } else {
          value[end === "end" ? "end_x" : "start_x"] = Math.round(point.x);
          value[end === "end" ? "end_y" : "start_y"] = Math.round(point.y);
        }
        node.value = value;
      } else if (node.action === "CAPTURE")
        node.value = { ...node.value, selector };
      else node.selector = selector;
      node.flow_version = 2;
      this.selected = path;
      this.studio.selected = path;
      this.studio.emit();
      this.studio.render();
      this.trace = [];
      this.refresh();
      this.notify(
        coords.has(node.action)
          ? "坐标已更新为草稿，请保存配置。"
          : "目标已重新绑定，只修改这一步。请保存配置。",
      );
    }
    startPinDrag(e) {
      const pin = e.target.closest("[data-pin]");
      if (!pin || e.button !== 0 || this.testing) return;
      e.preventDefault();
      e.stopPropagation();
      this.endPick();
      const path = pin.dataset.pin;
      this.lastPaint = null;
      this.drag = {
        path,
        end: pin.dataset.end || "start",
        id: e.pointerId,
        x: e.clientX,
        y: e.clientY,
        moved: false,
      };
      pin.setPointerCapture(e.pointerId);
      this.dragEl = pin;
      this.moveDrag = (ev) => {
        if (!this.drag || ev.pointerId !== this.drag.id) return;
        const d = this.drag;
        if (Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 5) d.moved = true;
        if (!d.moved) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        const target = this.hit(ev.clientX, ev.clientY);
        this.dragTarget = target;
        const r = target?.getBoundingClientRect();
        this.root.querySelector(".annotations").innerHTML =
          `${r ? `<div class="halo" style="left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px"></div>` : ""}<div class="drag-dot" style="left:${ev.clientX}px;top:${ev.clientY}px">↗</div>`;
      };
      this.upDrag = (ev) => {
        if (!this.drag || ev.pointerId !== this.drag.id) return;
        const d = this.drag;
        const node = this.studio.node(d.path);
        const element = d.moved ? this.hit(ev.clientX, ev.clientY) : null;
        this.cancelDrag();
        ev.preventDefault();
        ev.stopImmediatePropagation();
        this.suppressClick = true;
        clearTimeout(this.clickTimer);
        this.clickTimer = setTimeout(() => (this.suppressClick = false), 400);
        if (d.moved) {
          try {
            if (!coords.has(node.action) && !element)
              throw Error(
                "没有选中网页元素，原配置未修改。也可点击「重新选取元素」。",
              );
            if (
              ev.clientX < 0 ||
              ev.clientX > innerWidth ||
              ev.clientY < 0 ||
              ev.clientY > innerHeight
            )
              throw Error("请放到视口内");
            this.commit(
              d.path,
              { x: ev.clientX, y: ev.clientY },
              element,
              d.end,
            );
          } catch (err) {
            this.notify(err.message, true);
          }
        } else this.select(d.path);
      };
      this.cancelPointer = () => this.cancelDrag();
      window.addEventListener("pointermove", this.moveDrag, true);
      window.addEventListener("pointerup", this.upDrag, true);
      window.addEventListener("pointercancel", this.cancelPointer, true);
    }
    cancelDrag() {
      if (!this.drag) return;
      const id = this.drag.id;
      this.drag = null;
      this.lastPaint = null;
      try {
        this.dragEl?.releasePointerCapture(id);
      } catch {}
      window.removeEventListener("pointermove", this.moveDrag, true);
      window.removeEventListener("pointerup", this.upDrag, true);
      window.removeEventListener("pointercancel", this.cancelPointer, true);
      this.paint();
    }
    startPick(path = this.selected, callback = null) {
      if (this.testing) return;
      this.endPick();
      this.picking = { path, callback };
      this.lastPaint = null;
      this.panel.style.display = "none";
      this.root.querySelector(".annotations").replaceChildren();
      const layer = this.root.querySelector(".picking");
      layer.innerHTML =
        '<div class="pick-surface"></div><div class="pick-tip">点击网页上的目标，不会触发页面操作 · Esc 取消 <button data-command="cancel-pick">取消</button></div>';
      this.pickMove = (e) => {
        if (!this.picking) return;
        const element = this.hit(e.clientX, e.clientY);
        const r = element?.getBoundingClientRect();
        this.root.querySelector(".annotations").innerHTML = r
          ? `<div class="halo" style="left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px"></div>`
          : "";
      };
      layer
        .querySelector(".pick-surface")
        .addEventListener("pointermove", this.pickMove);
      for (const type of ["pointerdown", "pointerup", "mousedown", "mouseup"])
        layer.querySelector(".pick-surface").addEventListener(type, (e) => {
          e.preventDefault();
          e.stopPropagation();
        });
      layer.querySelector(".pick-surface").addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const pick = this.picking,
          element = this.hit(e.clientX, e.clientY);
        try {
          if (pick.callback) {
            pick.callback(
              uniqueSelector(
                this.normalizeTarget(this.studio.node(pick.path), element),
              ),
            );
          } else
            this.commit(pick.path, { x: e.clientX, y: e.clientY }, element);
          this.endPick();
        } catch (err) {
          this.notify(err.message, true);
          layer.querySelector(".pick-tip").firstChild.textContent =
            err.message + " · Esc 取消 ";
        }
      });
    }
    endPick() {
      if (!this.picking) return;
      this.picking = null;
      this.lastPaint = null;
      this.root.querySelector(".picking").replaceChildren();
      this.panel.style.display = this.visible && !this.testing ? "" : "none";
      this.paint();
      this.options.onPickEnd?.();
    }
    change(e) {
      if (this.testing) return;
      const el = e.target;
      if (el.hasAttribute("data-show-markers")) {
        this.markersEnabled = el.checked;
        this.refresh();
        this.root.querySelector("[data-show-markers]")?.focus({preventScroll:true});
        this.options.onMarkersChange?.(this.markersEnabled);
        return;
      }
      if (el.hasAttribute("data-show-all")) {
        this.all = el.checked;
        this.paint();
        return;
      }
      if (el.dataset.edit) {
        try {
          const row = this.current();
          if (!row) return;
          let value = el.value;
          if (el.dataset.edit === "wait") {
            if (value === "") throw Error("请输入等待时间");
            value = Number(value);
            if (!Number.isFinite(value) || value < 0 || value > 600)
              throw Error("等待时间需为 0–600 秒");
          }
          if (el.dataset.edit === "input" && !value) value = null;
          this.studio.checkpoint();
          this.studio.node(row.path).value = value;
          this.editing = true;
          try {
            this.studio.emit();
            this.studio.render();
          } finally {
            this.editing = false;
          }
          el.setCustomValidity("");
          this.updateDirty();
          this.notify("参数已更新为草稿，请保存配置。");
        } catch (err) {
          el.setCustomValidity(err.message);
          el.reportValidity();
        }
      }
    }
    click(e) {
      if (this.suppressClick) {
        e.preventDefault();
        e.stopPropagation();
        this.suppressClick = false;
        return;
      }
      const select = e.target.closest("[data-select]");
      if (select) {
        const fromList = !!select.closest(".row");
        this.select(select.dataset.select, true);
        if (fromList) this.root.querySelector(`[data-row="${this.selected}"] .select`)?.focus({preventScroll:true});
        return;
      }
      const b = e.target.closest("[data-command]");
      if (!b || b.disabled) return;
      const cmd = b.dataset.command;
      if (["save", "test", "advanced", "edit"].includes(cmd)) {
        const invalid = this.root.querySelector(":invalid");
        if (invalid) {
          this.setView("detail");
          invalid.reportValidity();
          return;
        }
      }
      const rows = this.rows(),
        i = rows.findIndex((r) => r.path === this.selected);
      if (cmd === "list-view") this.setView("list");
      if (cmd === "detail-view") this.setView("detail");
      if (cmd === "hide") { this.show(false, {keepMarkers:true}); this.options.onHide?.(); }
      if (cmd === "advanced") this.options.onAdvanced?.();
      if (cmd === "edit") this.options.onAdvanced?.(this.selected);
      if (cmd === "test") this.options.onTest?.();
      if (cmd === "trace") this.options.onTrace?.();
      if (cmd === "save") this.options.onSave?.();
      if (cmd === "previous" || cmd === "next") {
        const row = rows[i + (cmd === "previous" ? -1 : 1)];
        if (row) this.select(row.path, true);
      }
      if (cmd === "locate") {
        const loc = this.current() && this.location(this.current().node);
        if (loc?.element) this.select(this.selected, true);
        else this.notify(loc?.reason || "请选择步骤", true);
      }
      if (cmd === "pick") this.startPick();
      if (cmd === "cancel-pick") this.endPick();
    }
    destroy() {
      this.destroyed = true;
      this.cancelDrag();
      this.endPick();
      this.unDrag();
      this.observer.disconnect();
      clearInterval(this.timer);
      clearTimeout(this.clickTimer);
      cancelAnimationFrame(this.raf);
      window.removeEventListener("scroll", this.layout, true);
      window.removeEventListener("resize", this.layout);
      window.removeEventListener("keydown", this.key, true);
      window.removeEventListener("click", this.blockClick, true);
      window.removeEventListener("pointerdown", this.resetClick, true);
      this.host.remove();
    }
  }
  window.WorkflowPageGuide = {
    mount: (options) => new Guide(options),
    draggable,
    uniqueSelector,
    resolve,
  };
})();
