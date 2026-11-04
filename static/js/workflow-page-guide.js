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
  const nodeTitle = (n) =>
    n.action === "READONLY_HINT"
      ? window.WorkflowStudio.hintData(n).title
      : n.label || names[n.action] || n.action;
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
      return { element, count: nodes.length, elements: nodes };
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

  // The overview lives outside the inspector: collapsing settings must not hide the route.
  const overviewStyle = `
    :host{--click:#356fa1;--input:#8262a6;--read:#427865;--coord:#a56a2c;--warn:#a05a2c}
    :host([data-dark]){--click:#99c6ee;--input:#c9b2ed;--read:#9bc9b3;--coord:#edc080;--warn:#e4b880}
    .panel{width:322px;max-height:min(580px,calc(100dvh - 175px))}
    .tools{gap:8px 12px;justify-content:flex-start;padding:9px 13px}.tools label{font-size:10px}.tools label:last-child{color:var(--ink)}
    .location-summary{display:flex;align-items:center;gap:5px 8px;flex-wrap:wrap;flex-shrink:0;padding:8px 13px;font-size:10px;color:var(--muted);border-bottom:1px solid var(--line)}
    .location-summary b{font-weight:650;color:var(--ink)}.location-summary .needs-attention{color:var(--warn)}
    .location-summary button{margin-left:auto;padding:2px 6px;font-size:10px;border:0;background:none}
    .row{position:relative;gap:8px;padding:9px 7px}.row .select{min-width:0}.row strong{font-size:11px}.row small{max-width:220px;font-size:9px}
    .row-status{display:block;font-size:10px;line-height:1.6;margin-top:3px;color:var(--read);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .row[data-location=offscreen] .row-status{color:var(--click)}
    .row[data-location=hidden] .row-status,.row[data-location=missing] .row-status,.row[data-location=unbound] .row-status,.row[data-location=dynamic] .row-status,.row[data-location=unsupported] .row-status,.row[data-location=incomplete] .row-status{color:var(--warn)}
    .row[data-location=nonspatial] .row-status{color:var(--muted)}
    .row-locate{flex-shrink:0;width:27px;height:28px;padding:0;font-size:15px;background:transparent;border-color:transparent;color:var(--muted)}
    .row-locate:hover{color:var(--ink);background:var(--paper)}
    .pin{width:var(--pin-width,34px);height:34px;padding:3px;display:flex;justify-content:flex-start;gap:6px;border:1px solid var(--tone,var(--olive));border-radius:20px;background:var(--card);color:var(--ink);box-shadow:0 2px 9px #0003;overflow:hidden;font-weight:500;isolation:isolate}
    .pin .pin-number{display:grid;place-items:center;flex:none;min-width:26px;height:26px;padding:0 4px;border-radius:50%;background:var(--tone,var(--olive));color:var(--card);font:700 11px/1 system-ui,sans-serif}
    .pin .pin-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;line-height:1.4;pointer-events:none;padding-right:6px}
    .pin.active{box-shadow:0 0 0 3px var(--card),0 0 0 5px var(--tone,var(--olive)),0 4px 12px #0003}
    .pin.candidate{border-style:dashed}.pin.edge-pin{border-style:dashed;cursor:pointer;background:var(--paper)}
    .pin.overflow-pin{border-style:dotted}.pin:focus-visible{outline:3px solid var(--ink);outline-offset:5px}
    .target-outline{position:fixed;pointer-events:none;border:1px dashed var(--tone,var(--olive));border-radius:5px;opacity:.65}
    .target-outline.active{border-width:2px;border-style:solid;opacity:1;background:color-mix(in srgb,var(--tone,var(--olive)) 9%,transparent)}
    .leader line{stroke:var(--olive);stroke-width:1.4;stroke-dasharray:3 3;opacity:.65}
    .leader .route-path{stroke:var(--olive);stroke-width:1.7;stroke-dasharray:5 5;fill:none;opacity:.65}
    .leader .route-path.active{stroke-width:2.5;opacity:.95}
    .caption{z-index:1;max-width:min(250px,calc(100vw - 16px))}
    .step-rail{pointer-events:auto;position:fixed;left:16px;bottom:70px;width:min(860px,calc(100vw - 32px));max-width:calc(100vw - 32px);z-index:2;background:var(--paper);color:var(--ink);border:1px solid var(--line);border-radius:12px;box-shadow:0 5px 25px #0002;overflow:hidden}
    .rail-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 11px 2px;font-size:10px;color:var(--muted);line-height:1.4;min-height:24px}
    .rail-head strong{color:var(--ink);font-size:11px}.rail-head button{padding:0 5px;background:none;border:0;font-size:10px}
    .rail-track{display:flex;align-items:center;gap:5px;overflow-x:auto;overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:var(--line) transparent;padding:6px 10px 8px;scroll-padding-inline:12px;min-height:44px}
    .rail-chip{display:flex;align-items:center;gap:6px;flex-shrink:0;border:1px solid var(--line);border-radius:7px;background:var(--card);padding:5px 8px;color:var(--ink);font-size:11px;max-width:180px;min-height:29px}
    .rail-chip b{font:700 10px/1 system-ui,sans-serif;display:grid;place-items:center;min-width:18px;height:18px;border-radius:50%;background:var(--tone,var(--olive));color:var(--card)}
    .rail-chip span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.rail-chip.active{border-color:var(--olive);box-shadow:inset 0 -2px var(--olive)}
    .rail-chip[data-location=nonspatial]{border-style:dashed;color:var(--muted)}
    .rail-chip[data-location=missing],.rail-chip[data-location=hidden],.rail-chip[data-location=unbound],.rail-chip[data-location=dynamic],.rail-chip[data-location=unsupported],.rail-chip[data-location=incomplete]{border-style:dashed;border-color:var(--warn)}
    .rail-chip[data-location=offscreen]::after{content:'↗';color:var(--click);font-size:12px}.rail-separator{flex:none;color:var(--muted);font-size:11px}
    .rail-current{display:flex;align-items:center;gap:8px;min-height:31px;padding:5px 10px;border-top:1px solid var(--line);background:var(--card);font-size:10px}
    .rail-current .rail-context{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted)}
    .rail-current button{font-size:10px;white-space:nowrap;padding:2px 6px;background:var(--paper)}
    .rail-current .rail-problem{color:var(--warn)}
    @media(max-width:850px){.step-rail{left:8px;max-width:calc(100vw - 16px);width:calc(100vw - 16px)}.panel{max-height:calc(100dvh - 188px)}.rail-head{padding-top:5px}.rail-chip{font-size:10px}.rail-current{gap:4px}.rail-head .rail-hint{display:none}.rail-current button{padding:2px 4px}}
    @media(max-height:480px){.panel{max-height:calc(100dvh - 20px)}:host([data-panel-open]) .step-rail{display:none}.location-summary{padding:4px 12px}.tools{padding:5px 12px;gap:6px 10px}.row{padding:6px}.step-rail{bottom:64px}}
  `;

  const typeTone = (action) =>
    ["CLICK", "SELECT_MODEL"].includes(action)
      ? "var(--click)"
      : action === "FILL_INPUT"
        ? "var(--input)"
        : ["STREAM_WAIT", "STREAM_OUTPUT", "CAPTURE"].includes(action)
          ? "var(--read)"
          : coords.has(action)
            ? "var(--coord)"
            : "var(--olive)";
  const controlBoundary = new Set(["IF", "SWITCH", "TRY", "GROUP", "GUARD"]);
  const shortTitle = (node) => {
    if (node.action === "WAIT") return `等待 ${node.value ?? 0.5} 秒`;
    if (node.action === "KEY_PRESS") return `按键 ${node.value || "Enter"}`;
    return nodeTitle(node);
  };
  const overlapArea = (a, b, padding = 0) =>
    Math.max(
      0,
      Math.min(a.right, b.right + padding) - Math.max(a.left, b.left - padding),
    ) *
    Math.max(
      0,
      Math.min(a.bottom, b.bottom + padding) - Math.max(a.top, b.top - padding),
    );
  const plainRect = (r) => ({
    left: r.left,
    top: r.top,
    right: r.right,
    bottom: r.bottom,
    width: r.width,
    height: r.height,
  });
  function rendered(element) {
    if (!element?.isConnected || !element.getClientRects().length) return false;
    const r = element.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    if (element.checkVisibility) {
      try {
        return element.checkVisibility({
          checkOpacity: true,
          checkVisibilityCSS: true,
          opacityProperty: true,
          visibilityProperty: true,
        });
      } catch (_) {}
    }
    for (let e = element; e; e = e.parentElement) {
      const s = getComputedStyle(e);
      if (
        s.visibility === "hidden" ||
        s.visibility === "collapse" ||
        s.display === "none" ||
        Number(s.opacity) === 0
      )
        return false;
    }
    return true;
  }
  function clippedRect(element) {
    const rect = plainRect(element.getBoundingClientRect());
    const clip = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
    // A visible fragment counts as visible. Nested scroll containers clip independently.
    for (
      let parent = element.parentElement;
      parent;
      parent = parent.parentElement
    ) {
      const s = getComputedStyle(parent),
        r = parent.getBoundingClientRect();
      if (/auto|scroll|hidden|clip/.test(s.overflowX)) {
        clip.left = Math.max(clip.left, r.left);
        clip.right = Math.min(clip.right, r.right);
      }
      if (/auto|scroll|hidden|clip/.test(s.overflowY)) {
        clip.top = Math.max(clip.top, r.top);
        clip.bottom = Math.min(clip.bottom, r.bottom);
      }
    }
    const left = Math.max(rect.left, clip.left),
      top = Math.max(rect.top, clip.top),
      right = Math.min(rect.right, clip.right),
      bottom = Math.min(rect.bottom, clip.bottom);
    return {
      rect,
      clip,
      visible:
        right > left && bottom > top
          ? {
              left,
              top,
              right,
              bottom,
              width: right - left,
              height: bottom - top,
            }
          : null,
    };
  }


  const railEditStyle = `
    .step-rail{max-height:calc(100dvh - 28px);display:flex;flex-direction:column}
    .rail-head{flex-shrink:0;padding:6px 9px;gap:8px;min-height:35px;border-bottom:1px solid var(--line)}
    .rail-grip{display:flex;align-items:center;gap:7px;cursor:grab;touch-action:none;user-select:none;min-width:0;flex:1;outline-offset:2px;border-radius:4px}
    .rail-grip:active{cursor:grabbing}.rail-grip strong{white-space:nowrap;font-size:11px}.grip-dots{font-size:19px;line-height:1;color:var(--muted)}
    .rail-grip small{color:var(--muted);font-size:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.rail-save-state{color:var(--muted);font-size:9px;white-space:nowrap}
    .rail-head .rail-head-button{font-size:10px;padding:3px 5px;flex-shrink:0}.rail-head .rail-hide{font-size:15px}
    .rail-tools{display:flex;align-items:center;gap:5px;padding:6px 9px;flex-shrink:0;flex-wrap:wrap;background:var(--card)}
    .rail-tools button{padding:4px 7px;font-size:10px;min-height:28px;white-space:nowrap}.rail-tools .new-spatial{cursor:grab;touch-action:none}.new-spatial:active{cursor:grabbing}
    .rail-tools .rail-save{margin-left:auto;background:var(--olive);color:var(--card);border-color:var(--olive)}
    .rail-tools .rail-history{padding:4px 7px;font-size:13px}.rail-tools .insertion-label{font-size:9px;color:var(--muted);flex:1;min-width:90px}
    .rail-track{flex:0 0 auto;min-height:43px;padding-bottom:6px}.rail-chip b[data-pin]{cursor:grab;touch-action:none}.rail-chip b[data-pin]:hover{outline:2px solid var(--olive);outline-offset:2px}
    .rail-current{flex-shrink:0;flex-wrap:wrap;min-height:34px}.rail-current .rail-context{min-width:150px}.rail-current .rail-delete{color:var(--warn)}
    .rail-note{padding:6px 10px;border-top:1px solid var(--line);font-size:10px;color:var(--read);background:var(--card);max-height:52px;overflow:auto;flex-shrink:0}.rail-note.error{color:var(--warn)}
    .rail-editor{min-height:0;overflow:auto;overscroll-behavior:contain;max-height:min(260px,40dvh);padding:9px 12px;background:var(--card);border-top:1px solid var(--line);scrollbar-width:thin}
    .rail-editor h3{font-size:12px;margin:0 0 8px}.rail-fields{display:flex;flex-wrap:wrap;gap:9px}.rail-fields label{display:flex;flex-direction:column;gap:4px;min-width:120px;flex:1;font-size:10px;color:var(--muted)}
    .rail-fields label.wide{flex-basis:100%}.rail-fields input,.rail-fields textarea{padding:6px 8px;border:1px solid var(--line);border-radius:5px;background:var(--paper);font:12px/1.5 system-ui,sans-serif;color:var(--ink);width:100%;min-width:0}
    .rail-fields textarea{min-height:62px;resize:vertical}.rail-fields textarea.code{font:11px/1.6 ui-monospace,monospace;min-height:100px}.rail-fields input:invalid,.rail-fields textarea:invalid{border-color:var(--warn)}
    .rail-editor .binding-text{font-size:10px;overflow-wrap:anywhere;color:var(--muted);margin:8px 0 3px}.rail-editor .binding-text code{font-size:10px;user-select:text}
    .rail-order{display:flex;gap:5px;align-items:center;margin-top:9px}.rail-order button{font-size:10px;padding:3px 7px}.rail-order span{font-size:9px;color:var(--muted);margin-left:auto}
    .step-rail[data-collapsed]{width:max-content!important;max-width:calc(100vw - 16px)}
    .step-rail[data-collapsed] .rail-tools,.step-rail[data-collapsed] .rail-track,.step-rail[data-collapsed] .rail-current,.step-rail[data-collapsed] .rail-editor,.step-rail[data-collapsed] .rail-note{display:none!important}
    .step-rail[data-collapsed] .rail-head{border-bottom:0}.step-rail[data-collapsed] .rail-grip small,.step-rail[data-collapsed] .rail-hint{display:none}
    .rail-palette{pointer-events:auto;position:fixed;width:330px;max-width:calc(100vw - 20px);max-height:calc(100dvh - 20px);overflow:auto;overscroll-behavior:contain;background:var(--paper);color:var(--ink);border:1px solid var(--line);border-radius:11px;padding:11px;box-shadow:0 8px 32px #0004;z-index:5;scrollbar-width:thin}
    .palette-head{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12px;font-weight:650}.palette-head button{padding:1px 6px;border:0;background:none;font-size:18px}
    .palette-tip{font-size:10px;color:var(--muted);line-height:1.7;margin:5px 0 9px}.palette-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}
    .palette-action{display:flex;flex-direction:column;align-items:flex-start;gap:3px;text-align:left;padding:9px;border-radius:7px;background:var(--card);min-width:0}.palette-action strong{font-size:11px}.palette-action small{font-size:9px;color:var(--muted);line-height:1.4}.palette-action[data-spatial]{cursor:grab;touch-action:none}
    .add-drag-tip{position:fixed;left:50%;top:12px;transform:translateX(-50%);max-width:90vw;padding:9px 14px;border-radius:9px;background:var(--ink);color:var(--card);font-size:12px;pointer-events:none;z-index:6}
    .add-ghost{min-width:38px;padding:0 8px;width:auto;white-space:nowrap;border-radius:20px;font-size:11px}
    @media(max-width:850px){.rail-grip small,.rail-head .rail-hint{display:none}.rail-tools .insertion-label{display:none}.rail-current .rail-context{flex-basis:100%;min-width:0}.rail-tools{gap:4px}.rail-tools button{padding:4px 6px}.rail-fields label{min-width:90px}.rail-save-state{font-size:8px}.rail-head{gap:4px}.panel{max-height:calc(100dvh - 230px)}}
    @media(max-height:480px){.panel{max-height:calc(100dvh - 20px)}.rail-editor{max-height:30dvh}.step-rail:not([data-collapsed]){max-height:calc(100dvh - 16px)}}
  `;
  const addActions = [
    ['CLICK','元素点击','拖到按钮或任意元素'],
    ['COORD_CLICK','坐标点击','拖到任意视口位置'],
    ['FILL_INPUT','填写输入','拖到输入框再编辑内容'],
    ['COORD_SCROLL','坐标滚动','依次指定起点与终点'],
    ['WAIT','等待','直接添加并设置秒数'],
    ['KEY_PRESS','快捷键','直接添加并设置按键'],
    ['SELECT_MODEL','选择模型','拖到模型选择器'],
    ['STREAM_WAIT','读取回复','拖到回复区域'],
    ['JS_EXEC','脚本','编辑脚本，不执行'],
    ['READONLY_HINT','说明','添加文字说明'],
  ];
  const copy = value => JSON.parse(JSON.stringify(value));

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
      this.showLabels = true;
      this.showLinks = true;
      this.lastRail = null;
      this.railHidden=false;
      this.railCollapsed=false;
      this.quickEditorOpen=false;
      this.addMenuOpen=false;
      this.pendingAdd=null;
      this.addDrag=null;
      this.saveStatus='';
      this.lastEditorSignature=null;
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
      this.root.innerHTML = `<style>${style}${layoutStyle}${overviewStyle}${railEditStyle}</style><div class="annotations"></div><section class="panel" aria-label="页面操作可视化"><header><div class="headrow"><div><h2>页面步骤</h2><span class="muted">拖动标题移动 · 编辑不执行</span></div><button data-command="hide" title="仅收起面板，保留页面上的步骤标记" aria-label="收起面板，保留标记">收起 ↘</button></div></header><div class="tools"></div><div class="location-summary" aria-label="目标定位概况"></div><nav class="view-tabs" aria-label="页面步骤视图"><button data-command="list-view" aria-pressed="true">步骤列表</button><button data-command="detail-view" aria-pressed="false">步骤设置</button></nav><div class="guide-body"><div class="list"></div><div class="detail" hidden></div></div><div class="pager"></div><div class="notice" role="status" hidden></div><div class="bottom"><button data-command="save" class="primary">保存配置</button><button data-command="test">真实测试…</button><button data-command="trace" class="secondary">执行轨迹</button><button data-command="advanced" class="secondary">分支与变量 ↗</button></div></section><aside class="step-rail" aria-label="页面步骤可视化编排"><div class="rail-head"><div class="rail-grip" tabindex="0" role="button" aria-label="拖动移动工具条；方向键移动，Home复位，Enter收起" title="拖动移动工具条；双击复位"><span class="grip-dots" aria-hidden="true">⠿</span><strong>可视化编排</strong><small>编辑不执行 · 拖入网页添加</small></div><span class="rail-save-state"></span><span class="rail-hint"></span><button class="rail-head-button" data-command="rail-panel">步骤面板</button><button class="rail-head-button" data-command="rail-collapse" aria-expanded="true" title="收起工具条，保留页面标记">收起 ▾</button><button class="rail-head-button rail-hide" data-command="rail-hide" aria-label="隐藏工具条" title="隐藏此栏；可从步骤面板勾选「工具条」恢复">×</button></div><div class="rail-tools"><button class="new-spatial" data-new-action="CLICK" title="拖到网页元素添加点击；也可点选后拾取">＋元素点击</button><button class="new-spatial" data-new-action="COORD_CLICK" title="拖到任意位置添加坐标点击；也可点选后拾取">＋坐标点击</button><button data-command="rail-add" aria-expanded="false">＋添加 ▾</button><button class="rail-history" data-command="rail-undo" title="撤销编辑" aria-label="撤销编辑">↶</button><button class="rail-history" data-command="rail-redo" title="重做编辑" aria-label="重做编辑">↷</button><span class="insertion-label"></span><button class="rail-save" data-command="rail-save">保存到本地</button></div><div class="rail-track" role="navigation" aria-label="完整设计顺序"></div><div class="rail-current"><span class="rail-context"></span><button data-command="rail-locate">定位</button><button data-command="rail-pick">重新选取</button><button data-command="rail-quickedit" aria-expanded="false">编辑</button><button class="rail-delete" data-command="rail-delete">删除</button><button data-command="rail-edit" title="打开步骤面板中的更多设置">更多</button></div><div class="rail-editor" hidden></div><div class="rail-note" role="status" aria-live="polite" hidden></div></aside><section class="rail-palette" aria-label="添加步骤" hidden></section><div class="picking"></div>`;
      // Do not inherit transformed/filtered body containing blocks or clipped body stacking contexts.
      for (const [key, value] of Object.entries({
        position: "fixed",
        inset: "0",
        zIndex: "2147483644",
        pointerEvents: "none",
      }))
        this.host.style[key] = value;
      document.documentElement.append(this.host);
      this.panel = this.root.querySelector(".panel");
      this.rail=this.root.querySelector('.step-rail');
      this.unRailDrag=draggable(this.rail,e=>!this.testing&&!this.addDrag&&e.composedPath().some(n=>n.classList?.contains('rail-grip')));
      this.rail.addEventListener('dblclick',e=>{if(e.target.closest('.rail-grip'))this.resetRailPosition();});
      this.root.addEventListener('keydown',e=>this.railKey(e));
      this.root.addEventListener('focusin',e=>{if(e.target.dataset?.railField)this.studio.editKey=null;});
      this.root.addEventListener("click", (e) => {
        e.stopPropagation();
        this.click(e);
      });
      this.root.addEventListener("change", (e) => {
        if(e.target.dataset.railField)this.quickFieldChange(e);
        else if (!e.target.dataset.edit) this.change(e);
      });
      this.root.addEventListener("input", (e) => {
        if(e.target.dataset.railField)this.quickFieldChange(e);
        else if (e.target.dataset.edit) this.change(e);
      });
      this.root.addEventListener("pointerdown",e=>{
        if(e.target.closest('[data-new-action]'))this.startAddDrag(e);
        else this.startPinDrag(e);
      });
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
            if (!this.destroyed && !this.drag && !this.addDrag) this.paint();
          });
      };
      window.addEventListener("scroll", this.layout, true);
      window.addEventListener("resize", this.layout);
      this.panel.addEventListener("pointermove", this.layout);
      this.rail.addEventListener('pointermove',this.layout);
      this.key = (e) => {
        if (e.key === "Escape" && (this.drag || this.picking || this.addDrag || this.addMenuOpen)) {
          e.preventDefault();
          e.stopImmediatePropagation();
          const adding=!!this.addDrag||!!this.pendingAdd;
          this.cancelAddDrag();this.closePalette();
          this.cancelDrag();
          this.endPick();
          if(adding){this.suppressNextClick();this.notify('已取消添加，原流程未修改。');}
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
        attributeFilter: [
          "style",
          "class",
          "hidden",
          "id",
          "disabled",
          "open",
          "aria-hidden",
          "aria-expanded",
          "aria-disabled",
          "data-testid",
          "data-state",
        ],
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
      if (!this.canTarget(n)) return { reason: "此步骤没有固定页面目标" };
      const result = resolve(this.selector(n));
      // Do not change resolver/executor semantics: a visible alternate is a labelled preview only.
      if (!this.testing && result.element && !rendered(result.element)) {
        const candidate = result.elements?.find(rendered);
        if (candidate)
          return { ...result, element: candidate, previewOnly: true };
      }
      return result;
    }
    crumb(path) {
      return path
        .split(".")
        .slice(1, -1)
        .filter((p) => branches[p] || /^case_\d+$/.test(p))
        .map((p) => branches[p] || "情况 " + (Number(p.slice(5)) + 1))
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
        SWITCH:
          "从上到下检查情况，只执行第一个符合的分支；都不符合则执行其他情况。页面序号是设计顺序，不代表所有分支都会执行。",
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
        `<label title="拖动数字到新目标可修改定位，查看和切换步骤不会执行动作"><input type="checkbox" data-show-markers ${this.markersEnabled ? "checked" : ""}>页面标记</label><label title="关闭后只显示当前步骤的位置"><input type="checkbox" data-show-all ${this.all ? "checked" : ""} ${this.markersEnabled ? "" : "disabled"}>显示全部位置</label><label><input type="checkbox" data-show-labels ${this.showLabels ? "checked" : ""}>动作名称</label><label title="仅连接同一顺序内的已定位动作，不把不同分支串成执行路径"><input type="checkbox" data-show-links ${this.showLinks ? "checked" : ""}>顺序连线</label><label title="显示或恢复可拖动的底部编排工具条"><input type="checkbox" data-show-rail ${this.railHidden?'':'checked'}>工具条</label>`;
      this.root.querySelector(".list").innerHTML =
        rows
          .map(
            (r) =>
              `<div class="row ${r.path === this.selected ? "active" : ""}" data-row="${r.path}"><button class="number ${this.canTarget(r.node) || coords.has(r.node.action) ? "draggable" : ""}" ${this.canTarget(r.node) || coords.has(r.node.action) ? `data-pin="${r.path}" data-end="start"` : ""} data-select="${r.path}" aria-label="第 ${r.number} 步${this.canTarget(r.node) || coords.has(r.node.action) ? "，拖到网页目标" : ""}">${r.number}</button><button class="select" data-select="${r.path}"><strong>${esc(nodeTitle(r.node))}</strong><small>${esc(this.crumb(r.path) || r.node.action)}${this.trace.some((t) => t.path === r.path) ? " · " + esc(this.trace.filter((t) => t.path === r.path).at(-1).status) : ""}</small><span class="row-status"></span></button><button class="row-locate" data-command="locate-step" data-path="${r.path}" title="查看这一步的位置；不会执行" aria-label="定位第 ${r.number} 步">◎</button></div>`,
          )
          .join("") ||
        '<p class="explain">还没有步骤。可从底部「＋添加」拖入动作，或导入现有工作流。</p>';
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
      if (!loc.element) return loc.reason || "此步骤没有固定页面目标";
      if (!rendered(loc.element))
        return "目标存在但当前不可见，请先手动展开菜单或进入相应页面状态。";
      if (loc.previewOnly)
        return `匹配 ${loc.count} 个元素，首个匹配当前隐藏；虚线标记仅预览可见候选，不代表实际执行目标，原定位器未修改。建议重新选取唯一目标。`;
      if (!clippedRect(loc.element).visible)
        return "目标在当前视口或滚动区域之外；点击编号或「定位目标」滚动到它，不会触发点击。";
      return loc.count > 1
        ? `匹配 ${loc.count} 个元素，预览首个。建议重新选取唯一目标。`
        : "已找到目标；编号及连线只用于查看，不会点击。";
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
    updateRailChrome() {
      const rail=this.rail;
      rail.toggleAttribute('data-collapsed',this.railCollapsed);
      rail.hidden=this.railHidden||!this.annotationsActive||this.testing||!!this.picking;
      const collapse=rail.querySelector('[data-command=rail-collapse]');
      collapse.textContent=this.railCollapsed?'展开 ▴':'收起 ▾';
      collapse.setAttribute('aria-expanded',String(!this.railCollapsed));
      const row=this.current();
      for(const command of ['rail-delete','rail-quickedit','rail-edit'])rail.querySelector(`[data-command=${command}]`).disabled=!row;
      rail.querySelector('[data-command=rail-undo]').disabled=!this.studio.undoStack.length;
      rail.querySelector('[data-command=rail-redo]').disabled=!this.studio.redoStack.length;
      rail.querySelector('[data-command=rail-quickedit]').setAttribute('aria-expanded',String(this.quickEditorOpen));
      rail.querySelector('.insertion-label').textContent=row?`在第 ${row.number} 步后添加${this.crumb(row.path)?' · 当前分支':''}`:'添加第一个步骤';
      if(!this.editing)this.renderRailEditor(row);
      this.clampRail();
      if(this.addMenuOpen)this.positionPalette();
    }
    clampRail() {
      if(this.destroyed||this.rail.hidden||!this.rail.style.top)return;
      const r=this.rail.getBoundingClientRect();
      if(!r.width||!r.height)return;
      this.rail.style.left=clamp(r.left,8,innerWidth-r.width-8)+'px';
      this.rail.style.top=clamp(r.top,8,innerHeight-r.height-8)+'px';
    }
    resetRailPosition() {
      for(const p of ['left','top','right','bottom','width'])this.rail.style[p]='';
      this.paint();
    }
    railKey(e) {
      if(!e.target.closest?.('.rail-grip')||this.testing)return;
      if(e.key==='Enter'||e.key===' '){e.preventDefault();this.railCollapsed=!this.railCollapsed;this.updateRailChrome();this.paint();return;}
      if(e.key==='Home'){e.preventDefault();this.resetRailPosition();return;}
      const moves={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]};
      if(!moves[e.key])return;
      e.preventDefault();e.stopPropagation();
      const r=this.rail.getBoundingClientRect(),delta=e.shiftKey?30:10,[dx,dy]=moves[e.key];
      this.rail.style.left=r.left+dx*delta+'px';this.rail.style.top=r.top+dy*delta+'px';
      this.rail.style.bottom='auto';this.rail.style.right='auto';this.clampRail();this.paint();
    }
    closePalette() {
      this.addMenuOpen=false;this.root.querySelector('.rail-palette').hidden=true;
      this.rail.querySelector('[data-command=rail-add]').setAttribute('aria-expanded','false');
    }
    openPalette() {
      if(this.testing||!this.validateEdits())return;
      if(this.addMenuOpen){this.closePalette();return;}
      this.addMenuOpen=true;
      const menu=this.root.querySelector('.rail-palette');menu.hidden=false;
      menu.innerHTML=`<div class="palette-head"><span>添加步骤</span><button data-command="rail-close-palette" aria-label="关闭添加菜单">×</button></div><p class="palette-tip">拖动动作到网页，或点击动作后再选位置。<br>插入当前选中步骤之后；Esc 取消，不触发页面操作。</p><div class="palette-grid">${addActions.map(([action,title,hint])=>`<button class="palette-action" data-new-action="${action}" ${this.needsPlacement(action)?'data-spatial':''}><strong>${esc(title)}</strong><small>${esc(hint)}</small></button>`).join('')}</div>`;
      this.rail.querySelector('[data-command=rail-add]').setAttribute('aria-expanded','true');
      this.positionPalette();
    }
    positionPalette() {
      const menu=this.root.querySelector('.rail-palette');if(menu.hidden)return;
      const button=this.rail.querySelector('[data-command=rail-add]').getBoundingClientRect(),r=menu.getBoundingClientRect();
      menu.style.left=clamp(button.left,10,innerWidth-r.width-10)+'px';
      menu.style.top=clamp(button.top-r.height-8>=10?button.top-r.height-8:button.bottom+8,10,innerHeight-r.height-10)+'px';
    }
    needsPlacement(action) {return targeted.has(action)||coords.has(action);}
    makeNode(action) {
      if(!addActions.some(a=>a[0]===action))throw Error('不支持添加此动作');
      const n=window.WorkflowStudio.defaultNode(action);
      if(action==='WAIT')n.value=.5;
      if(action==='FILL_INPUT')n.value=null;
      if(action==='JS_EXEC')n.value='return document.title;';
      return n;
    }
    mutationDone(path,message) {
      this.selected=path;this.studio.selected=path;this.studio.editKey=null;
      this.trace=[];this.lastEditorSignature=null;
      this.studio.emit();this.studio.render();this.refresh();
      if(path)this.rail.querySelector(`.rail-chip[data-select="${path}"]`)?.scrollIntoView({block:'nearest',inline:'nearest'});
      if(message)this.notify(message);
    }
    insertNode(node,anchor) {
      if(this.testing)throw Error('真实测试期间不能编辑');
      const rows=this.rows();if(rows.length>=1000)throw Error('步骤最多 1000 个');
      let list=this.studio.workflow,parent='root',index=list.length;
      if(anchor) {
        const row=rows.find(r=>r.node===anchor);
        if(!row)throw Error('流程已变化，请重新选择插入位置');
        const loc=this.studio.location(row.path);list=loc.list;parent=loc.parent;index=loc.index+1;
      }
      this.studio.checkpoint();list.splice(index,0,node);
      this.quickEditorOpen=true;this.railCollapsed=false;this.railHidden=false;
      if(this.visible){this.show(false,{keepMarkers:true});this.options.onHide?.();}
      this.mutationDone(`${parent}.${index}`,'步骤已添加到草稿；可继续编辑，点击「保存到本地」后生效。');
    }
    removeSelected() {
      if(this.testing||!this.selected||!this.validateEdits())return;
      const row=this.current();if(!row)return;
      let count=0;window.WorkflowStudio.walk([row.node],()=>count++);
      if(count>1&&!confirm(`删除第 ${row.number} 步及其内部 ${count-1} 个步骤？\n这是草稿修改，可以撤销，保存前不会写入本地。`))return;
      this.closePalette();const loc=this.studio.location(row.path);
      this.studio.checkpoint();loc.list.splice(loc.index,1);
      let next=loc.list.length?`${loc.parent}.${Math.min(loc.index,loc.list.length-1)}`:loc.parent==='root'?null:loc.parent.split('.').slice(0,-1).join('.');
      this.mutationDone(next,'步骤已从草稿删除；可撤销，保存后才会写入本地。');
    }
    moveSelected(offset) {
      if(this.testing||!this.selected||!this.validateEdits())return;
      const loc=this.studio.location(this.selected),index=loc.index+offset;
      if(index<0||index>=loc.list.length)return;
      this.studio.checkpoint();[loc.list[index],loc.list[loc.index]]=[loc.list[loc.index],loc.list[index]];
      this.mutationDone(`${loc.parent}.${index}`,'步骤顺序已调整，只移动当前分支内的步骤。');
    }
    railHistory(redo=false) {
      if(this.testing)return;
      const from=redo?this.studio.redoStack:this.studio.undoStack,to=redo?this.studio.undoStack:this.studio.redoStack;
      if(!from.length)return;
      this.closePalette();this.cancelAddDrag();this.endPick();
      to.push(copy(this.studio.workflow));this.studio.workflow=from.pop();
      this.root.querySelector('.rail-editor').replaceChildren();
      this.mutationDone(null,redo?'已重做草稿修改。':'已撤销草稿修改。');
    }
    renderRailEditor(row) {
      const editor=this.rail.querySelector('.rail-editor');
      editor.hidden=!this.quickEditorOpen||!row;
      if(!row){editor.replaceChildren();this.lastEditorSignature=null;return;}
      if(!this.quickEditorOpen)return;
      const signature=row.path+'\n'+JSON.stringify(row.node);
      if(this.lastEditorSignature===signature)return;
      this.lastEditorSignature=signature;editor.dataset.path=row.path;
      const n=row.node;
      const input=(field,label,value,attrs='',wide=false)=>`<label class="${wide?'wide':''}">${esc(label)}<input data-rail-field="${field}" value="${esc(value??'')}" ${attrs}></label>`;
      const textarea=(field,label,value,code=false)=>`<label class="wide">${esc(label)}<textarea data-rail-field="${field}" class="${code?'code':''}">${esc(value??'')}</textarea></label>`;
      let fields=input('label','步骤名称（可选）',n.action==='READONLY_HINT'?window.WorkflowStudio.hintData(n).title:n.label,'maxlength="120"');
      if(n.action==='WAIT')fields+=input('wait','等待秒数',n.value??.5,'type="number" min="0" max="600" step="0.1" required');
      if(n.action==='KEY_PRESS')fields+=input('key','快捷键',n.value||'Enter','placeholder="Enter / Ctrl+Enter" required');
      if(n.action==='FILL_INPUT')fields+=textarea('input','输入内容（留空使用请求正文）',n.value);
      if(n.action==='JS_EXEC'&&typeof n.value==='string')fields+=textarea('script','脚本代码（这里只编辑，不运行）',n.value,true);
      if(n.action==='READONLY_HINT'){const hint=window.WorkflowStudio.hintData(n);fields+=textarea('hint','说明正文',hint.text);}
      if(coords.has(n.action))for(const [key,label] of (n.action==='COORD_CLICK'?[['x','X 坐标'],['y','Y 坐标']]:[['start_x','起点 X'],['start_y','起点 Y'],['end_x','终点 X'],['end_y','终点 Y']]))fields+=input('coord-'+key,label,n.value?.[key],'type="number" min="0" step="1" required');
      const loc=this.studio.location(row.path);
      editor.innerHTML=`<h3>${row.number}. ${esc(names[n.action]||n.action)} · 直接编辑</h3><div class="rail-fields">${fields}</div>${this.canTarget(n)?`<p class="binding-text">当前绑定：<code>${esc(this.selector(n)||'尚未绑定')}</code> · 拖动编号可更换目标</p>`:''}<div class="rail-order"><button data-command="rail-up" ${loc.index===0?'disabled':''}>↑ 上移</button><button data-command="rail-down" ${loc.index===loc.list.length-1?'disabled':''}>↓ 下移</button><button data-command="rail-edit">更多设置 ↗</button><span>保存前仅修改草稿</span></div>`;
    }
    quickFieldChange(e) {
      if(this.testing)return;
      const el=e.target,field=el.dataset.railField,row=this.current();
      if(!field||!row||el.closest('.rail-editor')?.dataset.path!==row.path)return;
      try {
        let value=el.value;
        if(field==='wait'||field.startsWith('coord-')) {
          if(!value.trim())throw Error('请输入数值');value=Number(value);
          if(!Number.isFinite(value)||value<0||(field==='wait'&&value>600)||(field.startsWith('coord-')&&!Number.isInteger(value)))throw Error(field==='wait'?'等待时间需为 0–600 秒':'坐标必须是非负整数');
        }
        if(field==='key'&&!value.trim())throw Error('请输入快捷键');
        const next=copy(row.node);
        if(field==='label'){if(next.action==='READONLY_HINT')next.value={...window.WorkflowStudio.hintData(next),...(typeof next.value==='object'?next.value:{}),title:value||'流程说明'};else if(value)next.label=value;else delete next.label;}
        else if(field==='input')next.value=value||null;
        else if(['wait','key','script'].includes(field))next.value=value;
        else if(field==='hint')next.value={...window.WorkflowStudio.hintData(next),...(typeof next.value==='object'?next.value:{}),text:value};
        else if(field.startsWith('coord-'))next.value={...(next.value||{}),[field.slice(6)]:value};
        else return;
        el.setCustomValidity('');
        if(JSON.stringify(next)===JSON.stringify(row.node))return;
        this.studio.checkpoint(`rail:${row.path}:${field}`);
        const loc=this.studio.location(row.path);loc.list[loc.index]=next;
        this.editing=true;
        try{this.studio.emit();this.studio.render();}finally{this.editing=false;}
        this.lastEditorSignature=row.path+'\n'+JSON.stringify(next);
        this.renderDetail(this.current());
        this.updateDirty();this.notify('参数已更新为草稿，请保存到本地。');
      }catch(err){el.setCustomValidity(err.message);this.notify(err.message,true);}
    }
    validateEdits() {
      const invalid=this.root.querySelector(':invalid');if(!invalid)return true;
      if(invalid.closest('.rail-editor')) {
        this.railHidden=false;this.railCollapsed=false;this.quickEditorOpen=true;
        this.rail.querySelector('.rail-editor').hidden=false;this.updateRailChrome();
      }else{this.options.onPage?.();this.show(true);this.setView('detail');}
      invalid.focus({preventScroll:true});invalid.reportValidity();return false;
    }
    setSaveStatus(status,message='') {
      this.saveStatus=status;clearTimeout(this.saveTimer);
      if(status==='pending'||status==='waiting')this.saveTimer=setTimeout(()=>{
        if(this.destroyed)return;this.saveStatus='unconfirmed';this.updateDirty();this.notify('尚未收到本地保存确认。草稿仍保留，请检查控制台后再决定是否重试。',true);
      },20000);
      this.updateDirty();if(message)this.notify(message,status==='error');
    }
    suppressNextClick() {
      this.suppressClick=true;clearTimeout(this.clickTimer);
      this.clickTimer=setTimeout(()=>this.suppressClick=false,450);
    }
    beginAddition(action) {
      if(this.testing||!this.validateEdits())return;
      try {
        const node=this.makeNode(action),anchor=this.current()?.node||null;
        this.closePalette();
        if(!this.needsPlacement(action)){this.insertNode(node,anchor);return;}
        this.endPick();this.pendingAdd={node,anchor,start:null};this.startAddPicker();
      }catch(err){this.notify(err.message,true);}
    }
    bindNewNode(node,point,element) {
      if(point.x<0||point.y<0||point.x>innerWidth||point.y>innerHeight)throw Error('请放到当前视口内');
      if(this.overEditor(point.x,point.y))throw Error('请放到网页，而不是编辑器工具条上');
      if(coords.has(node.action))return;
      node.selector=uniqueSelector(this.normalizeTarget(node,element));
    }
    completeNewTarget(pending,point,element) {
      const rows=this.rows();
      if(rows.length>=1000)throw Error('步骤最多 1000 个');
      if(pending.anchor&&!rows.some(r=>r.node===pending.anchor))throw Error('流程已经改变，请取消后重新添加');
      this.bindNewNode(pending.node,point,element);
      if(pending.node.action==='COORD_SCROLL'&&!pending.start) {
        pending.start={x:Math.round(point.x),y:Math.round(point.y)};this.pendingAdd=pending;this.startAddPicker();return;
      }
      if(pending.node.action==='COORD_SCROLL')pending.node.value={start_x:pending.start.x,start_y:pending.start.y,end_x:Math.round(point.x),end_y:Math.round(point.y)};
      if(pending.node.action==='COORD_CLICK')pending.node.value={x:Math.round(point.x),y:Math.round(point.y),random_radius:0};
      this.pendingAdd=null;this.endPick();this.insertNode(pending.node,pending.anchor);
    }
    startAddDrag(e) {
      const source=e.target.closest('[data-new-action]');
      if(!source||!this.needsPlacement(source.dataset.newAction)||e.button!==0||this.testing)return;
      if(!this.validateEdits()){e.preventDefault();return;}
      e.preventDefault();e.stopPropagation();this.endPick();
      const action=source.dataset.newAction;
      this.addDrag={id:e.pointerId,source,action,node:this.makeNode(action),anchor:this.current()?.node||null,x:e.clientX,y:e.clientY,moved:false};
      source.setPointerCapture(e.pointerId);this.lastPaint=null;
      this.addMove=ev=>{
        const d=this.addDrag;if(!d||ev.pointerId!==d.id)return;
        if(Math.hypot(ev.clientX-d.x,ev.clientY-d.y)>5)d.moved=true;
        if(!d.moved)return;ev.preventDefault();ev.stopImmediatePropagation();
        let element=this.hit(ev.clientX,ev.clientY),r=element?.getBoundingClientRect();
        if(element&&!coords.has(d.action)){try{element=this.normalizeTarget(d.node,element);r=element?.getBoundingClientRect();}catch(_){r=null;}}
        this.root.querySelector('.annotations').innerHTML=`${r&&!coords.has(d.action)?`<div class="halo" style="left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px"></div>`:''}<div class="drag-dot add-ghost" style="left:${ev.clientX}px;top:${ev.clientY}px">＋ ${esc(names[d.action])}</div><div class="add-drag-tip">松开添加${coords.has(d.action)?'视口坐标':'元素绑定'} · 不触发真实点击 · Esc 取消</div>`;
      };
      this.addUp=ev=>{
        const d=this.addDrag;if(!d||ev.pointerId!==d.id)return;
        const element=d.moved?this.hit(ev.clientX,ev.clientY):null;
        ev.preventDefault();ev.stopImmediatePropagation();this.cancelAddDrag();this.suppressNextClick();
        if(!d.moved){this.beginAddition(d.action);return;}
        this.closePalette();
        try{this.completeNewTarget({node:d.node,anchor:d.anchor,start:null},{x:ev.clientX,y:ev.clientY},element);}
        catch(err){this.notify(err.message+'；没有新增步骤。',true);}
      };
      this.addCancel=()=>{this.cancelAddDrag();this.suppressNextClick();};
      window.addEventListener('pointermove',this.addMove,true);window.addEventListener('pointerup',this.addUp,true);window.addEventListener('pointercancel',this.addCancel,true);
    }
    cancelAddDrag() {
      const drag=this.addDrag;if(!drag)return;
      this.addDrag=null;
      try{drag.source.releasePointerCapture(drag.id);}catch(_){}
      window.removeEventListener('pointermove',this.addMove,true);window.removeEventListener('pointerup',this.addUp,true);window.removeEventListener('pointercancel',this.addCancel,true);
      this.lastPaint=null;this.paint();
    }
    startAddPicker() {
      const pending=this.pendingAdd;if(!pending||this.testing)return;
      this.picking={addition:true};this.panel.style.display='none';this.rail.hidden=true;this.closePalette();
      this.root.querySelector('.annotations').replaceChildren();this.lastPaint=null;
      const layer=this.root.querySelector('.picking');
      const wording=pending.node.action==='COORD_SCROLL'?(pending.start?'点击滚动终点':'点击滚动起点'):pending.node.action==='COORD_CLICK'?'点击要记录的视口坐标':'点击要绑定的网页元素';
      layer.innerHTML=`<div class="pick-surface"></div><div class="pick-tip">${wording} · 仅添加步骤，不执行 · Esc 取消 <button data-command="cancel-pick">取消</button></div>`;
      const surface=layer.querySelector('.pick-surface');
      surface.addEventListener('pointermove',e=>{
        if(!this.pendingAdd)return;let element=this.hit(e.clientX,e.clientY),r=element?.getBoundingClientRect();
        if(element&&!coords.has(pending.node.action)){try{r=this.normalizeTarget(pending.node,element)?.getBoundingClientRect();}catch(_){r=null;}}
        this.root.querySelector('.annotations').innerHTML=coords.has(pending.node.action)?`<div class="cross" style="left:${e.clientX}px;top:${e.clientY}px"></div>`:r?`<div class="halo" style="left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px"></div>`:'';
      });
      for(const type of ['pointerdown','pointerup','mousedown','mouseup'])surface.addEventListener(type,e=>{e.preventDefault();e.stopPropagation();});
      surface.addEventListener('click',e=>{
        e.preventDefault();e.stopPropagation();
        try{this.completeNewTarget(pending,{x:e.clientX,y:e.clientY},this.hit(e.clientX,e.clientY));}
        catch(err){this.notify(err.message,true);const tip=layer.querySelector('.pick-tip');if(tip)tip.firstChild.textContent=err.message+' · Esc 取消 ';}
      });
    }
    handleRailCommand(cmd) {
      if(!cmd.startsWith('rail-'))return false;
      if(this.testing)return true;
      if(cmd==='rail-add'){this.openPalette();return true;}
      if(cmd==='rail-close-palette'){this.closePalette();return true;}
      if(cmd==='rail-collapse'){this.closePalette();this.railCollapsed=!this.railCollapsed;this.updateRailChrome();this.paint();return true;}
      if(cmd==='rail-hide'){this.closePalette();this.railHidden=true;this.refresh();this.notify('工具条已隐藏；从步骤面板勾选「工具条」可恢复。');return true;}
      if(cmd==='rail-quickedit'){if(!this.current())return true;this.quickEditorOpen=!this.quickEditorOpen;this.railCollapsed=false;if(this.quickEditorOpen&&this.visible){this.show(false,{keepMarkers:true});this.options.onHide?.();}this.updateRailChrome();this.paint();return true;}
      if(cmd==='rail-delete'){this.removeSelected();return true;}
      if(cmd==='rail-up'||cmd==='rail-down'){this.moveSelected(cmd==='rail-up'?-1:1);return true;}
      if(cmd==='rail-undo'||cmd==='rail-redo'){this.railHistory(cmd==='rail-redo');return true;}
      if(cmd==='rail-save'){if(this.validateEdits())this.options.onSave?.();return true;}
      return false;
    }

    updateDirty() {
      const dirty =
        this.saved !==
        JSON.stringify({
          workflow: this.studio.getWorkflow(),
          selectors: this.studio.selectors,
        });
      this.dirty=dirty;
      this.root.querySelector("[data-command=save]").textContent = dirty
        ? "保存配置 · 有修改"
        : "保存配置";
      const pending=['pending','waiting'].includes(this.saveStatus);
      const save=this.rail?.querySelector('[data-command=rail-save]');
      if(save){save.disabled=pending;save.textContent=pending?'等待本地确认…':dirty?'保存到本地 · 有修改':'保存到本地';}
      const badge=this.rail?.querySelector('.rail-save-state');
      if(badge)badge.textContent=pending?'提交中':this.saveStatus==='error'?'保存失败':this.saveStatus==='unconfirmed'?'尚未确认':dirty?'● 未保存':this.saveStatus==='saved'?'已保存':'已同步';
    }
    savedAs(workflow, selectors) {
      this.saved = JSON.stringify({ workflow, selectors });
      this.saveStatus='saved';clearTimeout(this.saveTimer);
      this.updateDirty();
    }
    notify(message, error = false) {
      this.message = message;
      const el = this.root.querySelector(".notice");
      el.hidden = !message;
      el.textContent = message;
      el.classList.toggle("error", error);
      const note=this.rail?.querySelector('.rail-note');
      if(note){note.hidden=!message;note.textContent=message;note.classList.toggle('error',error);}
    }
    select(path, scroll = false) {
      if (!this.rows().some((r) => r.path === path)) return;
      if(path!==this.selected&&!this.validateEdits())return;
      this.selected = path;
      this.refresh();
      if (scroll) {
        const current = this.current(),
          loc = this.location(current.node);
        if (loc.element && rendered(loc.element)) {
          loc.element.scrollIntoView({
            block: "center",
            inline: "nearest",
            behavior: "instant",
          });
          const r = clippedRect(loc.element).visible,
            p = this.panel.getBoundingClientRect();
          if (this.visible && r && overlapArea(p, r) > 0) {
            const positions = [
                [12, 20],
                [innerWidth - p.width - 12, 20],
              ],
              oldOverlap = overlapArea(p, r);
            for (const [x, y] of positions) {
              const candidate = {
                left: x,
                top: y,
                right: x + p.width,
                bottom: y + p.height,
              };
              if (x >= 8 && overlapArea(candidate, r) < oldOverlap * 0.25) {
                this.panel.style.left = x + "px";
                this.panel.style.top = y + "px";
                this.panel.style.right = "auto";
                this.panel.style.bottom = "auto";
                break;
              }
            }
          }
        }
        const info = this.inspect(current);
        if (!["visible", "offscreen", "nonspatial"].includes(info.kind))
          this.notify(info.reason, true);
        else this.notify("");
        this.paint();
        this.layout();
      }
      this.root
        .querySelector(`.rail-chip[data-select="${path}"]`)
        ?.scrollIntoView({ block: "nearest", inline: "nearest" });
      if (this.visible && this.view === "list")
        this.root
          .querySelector(`[data-row="${path}"]`)
          ?.scrollIntoView({ block: "nearest", inline: "nearest" });
      this.options.onSelect?.(path);
    }
    setView(view) {
      const body = this.root.querySelector(".guide-body");
      if (this.view === "list") this.listScroll = body.scrollTop;
      this.view = view;
      this.root.querySelector(".list").hidden = view !== "list";
      this.root.querySelector(".detail").hidden = view !== "detail";
      this.root
        .querySelector("[data-command=list-view]")
        .setAttribute("aria-pressed", String(view === "list"));
      this.root
        .querySelector("[data-command=detail-view]")
        .setAttribute("aria-pressed", String(view === "detail"));
      body.scrollTop = view === "list" ? this.listScroll : 0;
    }
    show(value, { keepMarkers = false } = {}) {
      this.visible = value;
      this.annotationsActive = value || keepMarkers;
      this.panel.style.display = value && !this.testing ? "" : "none";
      if (!value) {
        this.closePalette();this.cancelAddDrag();
        this.endPick();
        this.cancelDrag();
      }
      this.paint();
    }
    setTesting(value) {
      this.testing = value;
      this.panel.style.display = this.visible && !value ? "" : "none";
      if (value) {
        this.closePalette();this.cancelAddDrag();
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
    inspect(row) {
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
        const points = pairs
          .filter(([, x, y]) => Number.isFinite(x) && Number.isFinite(y))
          .map(([end, x, y]) => ({
            x,
            y,
            end,
            row,
            coordinate: true,
            offscreen: x < 0 || y < 0 || x > innerWidth || y > innerHeight,
          }));
        if (points.length !== pairs.length)
          return {
            kind: "incomplete",
            label: "坐标未完整配置",
            reason: "请在页面指定有效的坐标；不会将缺失坐标假装成 (0,0)。",
            points,
          };
        const outside = points.some((p) => p.offscreen);
        return {
          kind: outside ? "offscreen" : "visible",
          label: outside ? "坐标超出当前视口" : "固定视口坐标",
          reason: outside
            ? "请恢复记录时的窗口尺寸，或重新指定坐标；滚动网页不会改变视口坐标。"
            : "坐标以当前视口左上角为原点。",
          points,
        };
      }
      if (!this.canTarget(n))
        return {
          kind: "nonspatial",
          label:
            n.action === "WAIT"
              ? `等待 ${n.value ?? 0.5} 秒 · 无点击位置`
              : "无固定页面位置",
          reason: this.describe(n),
          points: [],
        };
      const loc =
        this.testing && row.path === this.testPath && this.testDetail?.selector
          ? resolve(this.testDetail.selector, true)
          : this.location(n);
      if (!loc.element) {
        const reason = loc.reason || "尚未绑定目标";
        const kind = reason.startsWith("运行时")
          ? "dynamic"
          : reason.startsWith("尚未")
            ? "unbound"
            : reason.startsWith("定位语法")
              ? "unsupported"
              : "missing";
        const labels = {
          dynamic: "运行时目标",
          unbound: "尚未绑定",
          unsupported: "定位语法不支持预览",
          missing: "当前页面未找到",
        };
        return { kind, label: labels[kind], reason, points: [], loc };
      }
      if (!rendered(loc.element))
        return {
          kind: "hidden",
          label: "目标当前隐藏",
          reason:
            "目标存在但不可见。请先手动展开菜单或切换页面状态；查看器不会代你点击。",
          points: [],
          loc,
        };
      const { rect, clip, visible } = clippedRect(loc.element);
      const x = visible
        ? visible.left + visible.width / 2
        : rect.left + rect.width / 2;
      const y = visible
        ? visible.top + visible.height / 2
        : rect.top + rect.height / 2;
      const kind = visible ? "visible" : "offscreen";
      const label = !visible
        ? "目标在屏外 / 滚动区外"
        : loc.previewOnly
          ? "可见候选 · 首个匹配隐藏"
          : loc.count > 1
            ? `已定位 · ${loc.count} 个匹配`
            : "已定位";
      return {
        kind,
        label,
        reason: this.targetMessage(loc),
        loc,
        points: [
          {
            x,
            y,
            end: "start",
            row,
            rect,
            clip,
            visibleRect: visible,
            element: loc.element,
            offscreen: !visible,
            previewOnly: !!loc.previewOnly,
          },
        ],
      };
    }
    points(row) {
      return this.inspect(row).points;
    }
    refreshOverview(rows, locations) {
      const counts = { visible: 0, offscreen: 0, nonspatial: 0, problem: 0 };
      for (const row of rows) {
        const loc = locations.get(row.path);
        counts[loc.kind in counts ? loc.kind : "problem"]++;
        const el = this.root.querySelector(`[data-row="${row.path}"]`);
        if (el) {
          el.dataset.location = loc.kind;
          const status = el.querySelector(".row-status");
          if (status && status.textContent !== loc.label)
            status.textContent = loc.label;
          if (status) status.title = loc.reason;
        }
      }
      const summary = `<span><b>${rows.length}</b> 步</span><span><b>${counts.visible}</b> 可见</span><span>${counts.offscreen} 屏外</span><span class="${counts.problem ? "needs-attention" : ""}">${counts.problem} 待定位</span><span>${counts.nonspatial} 无固定位置</span><button data-command="refresh-locations" title="重新检测目标，不执行任何步骤">↻ 刷新</button>`;
      const summaryEl = this.root.querySelector(".location-summary");
      if (summaryEl.innerHTML !== summary) summaryEl.innerHTML = summary;
      const rail = this.root.querySelector(".step-rail");
      rail.hidden = this.railHidden || !this.annotationsActive || this.testing || !!this.picking;
      this.host.toggleAttribute(
        "data-panel-open",
        this.visible && !this.testing,
      );
      // Keep the full step order readable without leaving a giant settings panel over the page.
      const track = rail.querySelector(".rail-track");
      const markup = rows
        .map((row, i) => {
          const loc = locations.get(row.path),
            branch = this.crumb(row.path);
          return `${i ? `<span class="rail-separator" aria-hidden="true">${rows[i - 1].path.split(".").slice(0, -1).join(".") === row.path.split(".").slice(0, -1).join(".") ? "→" : "↳"}</span>` : ""}<button class="rail-chip ${row.path === this.selected ? "active" : ""}" data-select="${row.path}" data-location="${loc.kind}" style="--tone:${typeTone(row.node.action)}" aria-label="第 ${row.number} 步 ${esc(shortTitle(row.node))}，${esc(loc.label)}${branch ? "，" + esc(branch) : ""}" title="${esc(branch ? branch + " · " : "")}${esc(loc.reason)}"><b ${this.canTarget(row.node)||coords.has(row.node.action)?`data-pin="${row.path}" data-end="start" title="拖动编号到网页可重新绑定"`:''}>${row.number}</b><span>${esc(shortTitle(row.node))}</span></button>`;
        })
        .join("");
      if (markup !== this.lastRail) {
        const scroll = track.scrollLeft,
          focused =
            this.root.activeElement?.closest(".rail-chip")?.dataset.select;
        track.innerHTML =
          markup || '<span class="muted">还没有步骤；拖动「＋元素点击」到网页开始。</span>';
        track.scrollLeft = scroll;
        this.lastRail = markup;
        if (focused)
          track
            .querySelector(`[data-select="${focused}"]`)
            ?.focus({ preventScroll: true });
      }
      const current = rows.find((r) => r.path === this.selected),
        loc = current && locations.get(current.path);
      rail.querySelector("[data-command=rail-panel]").textContent = this.visible
        ? "收起面板 ↘"
        : "展开面板 ↗";
      const description = current
        ? `${current.number}. ${shortTitle(current.node)} · ${loc.kind === "nonspatial" ? this.describe(current.node) : loc.label}`
        : "点击编号，只查看位置，不执行";
      const context = rail.querySelector(".rail-context");
      if (context.textContent !== description)
        context.textContent = description;
      context.title = current ? loc.reason : description;
      context.classList.toggle(
        "rail-problem",
        !!loc && !["visible", "offscreen", "nonspatial"].includes(loc.kind),
      );
      rail.querySelector("[data-command=rail-pick]").hidden =
        !current ||
        !(this.canTarget(current.node) || coords.has(current.node.action));
      rail.querySelector("[data-command=rail-locate]").disabled =
        !current || !loc.points.length;
      const hint = rail.querySelector(".rail-hint");
      hint.textContent = this.markersEnabled
        ? `${counts.visible} 可见 · ${counts.offscreen + counts.problem} 待查看`
        : "页面标记已隐藏";
      if (!rail.style.top && this.visible && innerWidth > 850) {
        const r = this.panel.getBoundingClientRect(),
          railRect = rail.getBoundingClientRect();
        const available = r.left - 28;
        rail.style.width =
          r.bottom > railRect.top - 10 && available > 310
            ? Math.min(860, available - 16) + "px"
            : "";
      } else if(!rail.style.top)rail.style.width = "";
      this.updateRailChrome();
      return { counts, rail };
    }
    markerWidth(text) {
      if (!this.showLabels) return 34;
      const measured = Array.from(text).reduce(
        (total, ch) => total + (/[\u2e80-\uffff]/.test(ch) ? 11 : 6.2),
        0,
      );
      return Math.min(
        204,
        Math.max(100, Math.ceil(measured) + 58),
        innerWidth - 24,
      );
    }
    placeMarker(point, width, placed, obstacles) {
      const height = 34,
        margin = 8,
        half = width / 2;
      const r = point.visibleRect;
      const candidates = [];
      if (point.offscreen) {
        const c = point.clip || {
          left: 0,
          top: 0,
          right: innerWidth,
          bottom: innerHeight,
        };
        candidates.push([
          clamp(point.x, c.left + half + margin, c.right - half - margin),
          clamp(point.y, c.top + 25, c.bottom - 25),
        ]);
      } else if (r) {
        candidates.push(
          [r.left + half, r.top - 23],
          [r.left + half, r.bottom + 23],
          [r.right + half + 9, point.y],
          [r.left - half - 9, point.y],
        );
      } else candidates.push([point.x, point.y]);
      const origin = candidates[0];
      // Search outward rather than clamping every duplicate badge to the same edge pixel.
      for (let ring = 1; ring <= 12; ring++) {
        for (const [dx, dy] of [
          [0, -ring],
          [0, ring],
          [-ring, 0],
          [ring, 0],
          [-ring, -1],
          [ring, 1],
          [-1, -ring],
          [1, ring],
        ])
          candidates.push([
            origin[0] + dx * (width + 8),
            origin[1] + dy * (height + 8),
          ]);
      }
      let best = null,
        score = Infinity;
      for (const [cx, cy] of candidates) {
        const x = clamp(cx, half + margin, innerWidth - half - margin),
          y = clamp(cy, 25, innerHeight - 25);
        const box = {
          left: x - half,
          right: x + half,
          top: y - height / 2,
          bottom: y + height / 2,
        };
        const collision = placed.reduce(
          (v, b) => v + overlapArea(box, b, 5),
          0,
        );
        const blocked = obstacles.reduce(
          (v, b) => v + overlapArea(box, b, 5),
          0,
        );
        const onTarget = point.visibleRect
          ? overlapArea(box, point.visibleRect, 3)
          : 0;
        const scoreNow =
          collision * 100000 +
          blocked * 1000000 +
          onTarget * 100 +
          Math.hypot(x - point.x, y - point.y);
        if (scoreNow < score) {
          best = { x, y, box, overflow: collision > 0 || blocked > 0 };
          score = scoreNow;
        }
        if (!collision && !blocked && !onTarget) break;
      }
      placed.push(best.box);
      return best;
    }
    paint() {
      if (this.drag || this.addDrag || this.picking || this.destroyed) return;
      const rows = this.rows(),
        locations = new Map(rows.map((row) => [row.path, this.inspect(row)]));
      const { rail } = this.refreshOverview(rows, locations);
      const layer = this.root.querySelector(".annotations");
      const active = this.testing ? this.testPath : this.selected;
      const status = this.root.querySelector(".target-status"),
        current = rows.find((r) => r.path === this.selected);
      if (status && current && !this.testing)
        status.textContent = locations.get(current.path).reason;
      if ((!this.annotationsActive || !this.markersEnabled) && !this.testing) {
        layer.replaceChildren();
        this.lastPaint = null;
        return;
      }
      const obstacles = [
        this.panel,
        rail,
        ...(this.options.editorElements?.() || []),
      ]
        .filter((e) => e && !e.hidden && getComputedStyle(e).display !== "none")
        .map((e) => e.getBoundingClientRect())
        .filter((r) => r.width && r.height);
      const placed = [],
        anchors = new Map(),
        draw = [];
      let marks = "",
        leaders = "",
        highlights = "",
        routes = "";
      let folded = 0,
        placementAttempts = 0;
      for (const row of rows) {
        if (
          this.testing ? row.path !== active : !this.all && row.path !== active
        )
          continue;
        const loc = locations.get(row.path),
          pts = loc.points;
        if (pts.length === 2 && !pts.some((p) => p.offscreen))
          leaders += `<line class="scroll-vector" x1="${pts[0].x}" y1="${pts[0].y}" x2="${pts[1].x}" y2="${pts[1].y}" marker-end="url(#wfe-guide-arrow)"/>`;
        for (const point of pts) draw.push({ row, loc, point });
      }
      // The selected step gets a slot first, even near the 1000-node DSL limit.
      draw.sort(
        (a, b) => Number(b.row.path === active) - Number(a.row.path === active),
      );
      for (const { row, loc, point: p } of draw) {
        if (
          !this.testing &&
          (++placementAttempts > 120 || placed.length >= 96)
        ) {
          folded++;
          continue;
        }
        const chosen = row.path === active,
          tone = typeTone(row.node.action),
          label = shortTitle(row.node);
        if (this.testing) {
          if (p.offscreen) continue;
          const r = p.rect || {
            left: p.x - 18,
            top: p.y - 18,
            width: 36,
            height: 36,
          };
          highlights += `<div class="halo" style="left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px"></div><div class="caption" style="left:${clamp(r.left, 8, innerWidth - 260)}px;top:${clamp(r.top - 33, 8, innerHeight - 40)}px">正在执行 · ${row.number} ${esc(label)}</div>`;
          continue;
        }
        const edge = p.offscreen,
          clip = p.clip || {
            left: 0,
            top: 0,
            right: innerWidth,
            bottom: innerHeight,
          };
        const direction =
          p.y < clip.top
            ? "↑"
            : p.y > clip.bottom
              ? "↓"
              : p.x < clip.left
                ? "←"
                : p.x > clip.right
                  ? "→"
                  : "↗";
        const suffix = edge
          ? p.coordinate
            ? " · 视口外"
            : " · 屏外"
          : p.previewOnly
            ? " · 候选"
            : "";
        const titleText = label + suffix;
        let width = this.markerWidth(titleText),
          spot = this.placeMarker(p, width, placed, obstacles);
        if (spot.overflow && width > 34) {
          placed.pop();
          width = 34;
          spot = this.placeMarker(p, width, placed, obstacles);
        }
        if (spot.overflow) {
          placed.pop();
          folded++;
          continue;
        } // Honest overflow count; every step remains accessible in the rail.
        const endText =
          row.node.action === "COORD_SCROLL"
            ? p.end === "end"
              ? "终"
              : "起"
            : "";
        const dragAttr = edge
          ? ""
          : `data-pin="${row.path}" data-end="${p.end}"`;
        const title = `${row.number}. ${label}${suffix} · ${edge ? "点击定位，绝不执行" : p.previewOnly ? "仅预览可见候选；原定位器未修改" : "点击查看；拖动可重新绑定" + (p.coordinate ? "坐标" : "元素")}${loc.loc?.count > 1 ? " · 共 " + loc.loc.count + " 个匹配" : ""}`;
        marks += `<button class="pin ${edge ? "edge-pin " : ""}${p.previewOnly ? "candidate " : ""}${chosen ? "active" : ""}" ${dragAttr} data-select="${row.path}" data-marker="${row.path}" data-anchor-x="${p.x}" data-anchor-y="${p.y}" style="left:${spot.x}px;top:${spot.y}px;--pin-width:${width}px;--tone:${tone}" title="${esc(title)}" aria-label="页面标记 ${row.number}${endText ? " " + p.end : ""} ${esc(label)}${esc(suffix)}"><b class="pin-number">${edge ? direction : ""}${row.number}${endText}</b>${this.showLabels && width > 34 ? `<span class="pin-name">${esc(titleText)}</span>` : ""}</button>`;
        if (!edge) {
          leaders += `<line class="target-leader" data-path="${row.path}" x1="${p.x}" y1="${p.y}" x2="${spot.x}" y2="${spot.y}"/>`;
          marks += `<span class="cross" style="left:${p.x}px;top:${p.y}px;color:${tone}"></span>`;
          if (p.end === "start")
            anchors.set(row.path, { x: spot.x, y: spot.y, box: spot.box });
          if (p.visibleRect) {
            const r = p.visibleRect;
            highlights += `<div class="target-outline ${chosen ? "active" : ""}" data-outline="${row.path}" style="left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;--tone:${tone}"></div>`;
          } else if (chosen)
            highlights += `<div class="halo" style="left:${p.x - 18}px;top:${p.y - 18}px;width:36px;height:36px"></div>`;
        }
      }
      if (this.showLinks && !this.testing && this.all) {
        const lastByBranch = new Map();
        for (const row of rows) {
          const branch = row.path.split(".").slice(0, -1).join(".");
          if (controlBoundary.has(row.node.action)) {
            lastByBranch.delete(branch);
            continue;
          }
          const to = anchors.get(row.path),
            last = lastByBranch.get(branch);
          if (to && last) {
            const from = last.anchor,
              dx = to.x - from.x,
              dy = to.y - from.y,
              len = Math.hypot(dx, dy);
            if (len > 45) {
              const startGap =
                Math.abs(dx) > Math.abs(dy)
                  ? (from.box.right - from.box.left) / 2 + 5
                  : 23;
              const endGap =
                Math.abs(dx) > Math.abs(dy)
                  ? (to.box.right - to.box.left) / 2 + 7
                  : 24;
              const x1 = from.x + (dx / len) * startGap,
                y1 = from.y + (dy / len) * startGap,
                x2 = to.x - (dx / len) * endGap,
                y2 = to.y - (dy / len) * endGap;
              if (len > startGap + endGap + 4)
                routes += `<path class="route-path ${row.path === active || last.path === active ? "active" : ""}" data-from="${last.path}" data-to="${row.path}" d="M${x1},${y1} L${x2},${y2}" marker-end="url(#wfe-guide-arrow)"><title>设计顺序 ${last.number} → ${row.number}，不是自动执行路径</title></path>`;
            }
          }
          if (to)
            lastByBranch.set(branch, {
              anchor: to,
              path: row.path,
              number: row.number,
            });
          else if (locations.get(row.path).kind !== "nonspatial")
            lastByBranch.delete(branch);
        }
      }
      if (folded) {
        const hint = rail.querySelector(".rail-hint");
        hint.textContent += ` · ${folded} 个标记收进导航`;
        hint.title = "页面空间不足；收起面板或关闭动作名称可展开更多编号。";
        const context = rail.querySelector(".rail-context");
        context.textContent += ` · ${folded} 个编号在导航中查看`;
      }
      rail.dataset.folded = String(folded);
      const markup = `<svg class="leader" aria-hidden="true"><defs><marker id="wfe-guide-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0L7 3.5L0 7Z" fill="var(--olive)"/></marker></defs>${routes}${leaders}</svg>${highlights}${marks}`;
      if (markup !== this.lastPaint) {
        const focus = this.root.activeElement?.closest(".pin");
        const key = focus
          ? { path: focus.dataset.select, end: focus.dataset.end }
          : null;
        layer.innerHTML = markup;
        this.lastPaint = markup;
        if (key)
          layer
            .querySelector(
              `[data-select="${key.path}"]${key.end ? `[data-end="${key.end}"]` : ""}`,
            )
            ?.focus({ preventScroll: true });
      }
    }
    overEditor(x, y) {
      return [
        this.panel,
        this.root.querySelector(".step-rail"),
        this.root.querySelector('.rail-palette'),
        ...(this.options.editorElements?.() || []),
      ].some((e) => {
        if (!e || e.hidden || getComputedStyle(e).display === "none")
          return false;
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && inRect(x, y, r);
      });
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
      if (this.overEditor(x, y)) return null;
      const display = this.host.style.display;
      this.host.style.display = "none";
      let element;
      try {
        element = document.elementFromPoint(x, y);
      } finally {
        this.host.style.display = display;
      }
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
        navigate:!!pin.closest('.rail-chip')||!!pin.closest('.row'),
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
        } else this.select(d.path, d.navigate);
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
      this.closePalette();this.cancelAddDrag();
      this.endPick();
      this.picking = { path, callback };
      this.root.querySelector(".step-rail").hidden = true;
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
      if(this.picking.addition)this.pendingAdd=null;
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
      if(el.hasAttribute('data-show-rail')){this.railHidden=!el.checked;this.refresh();return;}
      if (el.hasAttribute("data-show-markers")) {
        this.markersEnabled = el.checked;
        this.refresh();
        this.root
          .querySelector("[data-show-markers]")
          ?.focus({ preventScroll: true });
        this.options.onMarkersChange?.(this.markersEnabled);
        return;
      }
      if (el.hasAttribute("data-show-all")) {
        this.all = el.checked;
        this.paint();
        return;
      }
      if (
        el.hasAttribute("data-show-labels") ||
        el.hasAttribute("data-show-links")
      ) {
        if (el.hasAttribute("data-show-labels")) this.showLabels = el.checked;
        else this.showLinks = el.checked;
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
      const add=e.target.closest('[data-new-action]');
      if(add){this.beginAddition(add.dataset.newAction);return;}
      const select = e.target.closest("[data-select]");
      if (select) {
        const fromList = !!select.closest(".row");
        this.select(select.dataset.select, true);
        if (fromList)
          this.root
            .querySelector(`[data-row="${this.selected}"] .select`)
            ?.focus({ preventScroll: true });
        return;
      }
      const b = e.target.closest("[data-command]");
      if (!b || b.disabled) return;
      const cmd = b.dataset.command;
      if(this.handleRailCommand(cmd))return;
      if (cmd === "locate-step") {
        this.select(b.dataset.path, true);
        return;
      }
      if (cmd === "refresh-locations") {
        this.lastPaint = null;
        this.paint();
        return;
      }
      if (cmd === "rail-panel") {
        if (this.visible) {
          this.show(false, { keepMarkers: true });
          this.options.onHide?.();
        } else {
          this.options.onPage?.();
          this.show(true);
        }
        return;
      }
      if (cmd === "rail-edit") {
        if(!this.validateEdits())return;
        this.quickEditorOpen=false;this.renderDetail(this.current());
        this.options.onPage?.();
        this.show(true);
        this.setView("detail");
        return;
      }
      if (cmd === "rail-pick") {
        this.startPick();
        return;
      }
      if (cmd === "rail-locate") {
        if (this.selected) this.select(this.selected, true);
        return;
      }
      if (["save", "test", "advanced", "edit"].includes(cmd)) {
        if(!this.validateEdits())return;
      }
      const rows = this.rows(),
        i = rows.findIndex((r) => r.path === this.selected);
      if (cmd === "list-view") this.setView("list");
      if (cmd === "detail-view") this.setView("detail");
      if (cmd === "hide") {
        this.show(false, { keepMarkers: true });
        this.options.onHide?.();
      }
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
      this.cancelAddDrag();this.closePalette();this.unRailDrag();clearTimeout(this.saveTimer);
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
