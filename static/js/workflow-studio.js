/* STUDIO_STYLE_START */
window.WORKFLOW_STUDIO_CSS = ":host {\n  display: block;\n  color: var(--uwa-ink, #33402c);\n  font:\n    13px/1.5 system-ui,\n    -apple-system,\n    \"Segoe UI\",\n    sans-serif;\n  --ws-paper: var(--uwa-paper, #f7f3ea);\n  --ws-card: var(--uwa-paper-strong, #fffdf8);\n  --ws-ink: var(--uwa-ink, #33402c);\n  --ws-muted: var(--uwa-muted, #777b6c);\n  --ws-line: var(--uwa-line, #dddccd);\n  --ws-accent: var(--uwa-olive, #5d6b4d);\n}\n* {\n  box-sizing: border-box;\n}\nbutton,\ninput,\nselect,\ntextarea {\n  font: inherit;\n  color: inherit;\n}\nbutton {\n  cursor: pointer;\n}\nbutton:disabled {\n  opacity: 0.4;\n  cursor: not-allowed;\n}\nbutton:focus-visible,\ninput:focus-visible,\ntextarea:focus-visible,\nselect:focus-visible {\n  outline: 2px solid var(--ws-accent);\n  outline-offset: 3px;\n}\nbutton {\n  border: 1px solid var(--ws-line);\n  border-radius: 7px;\n  background: var(--ws-card);\n  padding: 6px 10px;\n}\nbutton:hover:not(:disabled) {\n  border-color: var(--ws-accent);\n}\n.studio {\n  border: 1px solid var(--ws-line);\n  border-radius: 14px;\n  background: var(--ws-paper);\n  overflow: hidden;\n}\n.head {\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n  gap: 14px;\n  padding: 20px 22px;\n  border-bottom: 1px solid var(--ws-line);\n  flex-wrap: wrap;\n}\n.eyebrow {\n  font-size: 10px;\n  letter-spacing: 2px;\n  color: var(--ws-muted);\n  font-weight: 700;\n}\n.head h2 {\n  font-size: 21px;\n  margin: 3px 0;\n  font-weight: 650;\n  letter-spacing: -0.6px;\n}\n.head p {\n  margin: 4px 0 0;\n  color: var(--ws-muted);\n  font-size: 12px;\n}\n.actions {\n  display: flex;\n  gap: 6px;\n  align-items: center;\n  flex-wrap: wrap;\n}\n.primary {\n  background: var(--ws-accent);\n  color: var(--ws-card);\n  border-color: var(--ws-accent);\n}\n.tabs {\n  display: flex;\n  gap: 7px;\n  padding: 10px 20px;\n  border-bottom: 1px solid var(--ws-line);\n  align-items: center;\n  flex-wrap: wrap;\n}\n.tabs button {\n  background: none;\n  border-color: transparent;\n}\n.tabs button.active {\n  background: var(--ws-card);\n  border-color: var(--ws-line);\n  font-weight: 650;\n}\n.counter {\n  margin-left: auto;\n  color: var(--ws-muted);\n  font-size: 11px;\n}\n.workspace {\n  display: grid;\n  grid-template-columns: minmax(0, 1fr) 290px;\n  min-height: 480px;\n}\n.canvas {\n  overflow: auto;\n  padding: 26px 24px;\n  max-height: 720px;\n  background-image: radial-gradient(var(--ws-line) 0.7px, transparent 0.7px);\n  background-size: 16px 16px;\n}\n.sequence {\n  display: flex;\n  flex-direction: column;\n  align-items: stretch;\n  position: relative;\n  min-width: 160px;\n  gap: 0;\n  max-width: 900px;\n  margin: auto;\n}\n.sequence.root {\n  min-width: 250px;\n}\n.sequence:before {\n  content: \"\";\n  position: absolute;\n  left: 50%;\n  top: 0;\n  bottom: 0;\n  width: 1px;\n  background: var(--ws-line);\n}\n.unit {\n  position: relative;\n  margin: 0 0 20px;\n  z-index: 1;\n}\n.node {\n  width: 100%;\n  display: flex;\n  align-items: center;\n  gap: 11px;\n  text-align: left;\n  background: var(--ws-card);\n  padding: 12px 13px;\n  border: 1px solid var(--ws-line);\n  border-radius: 10px;\n  box-shadow: 0 2px 3px #00000003;\n  position: relative;\n}\n.node.selected {\n  border-color: var(--ws-accent);\n  box-shadow: 0 0 0 2px color-mix(in srgb, var(--ws-accent) 15%, transparent);\n}\n.node.control {\n  border-left: 3px solid var(--ws-accent);\n}\n.node.failed {\n  border-color: #b3634d;\n}\n.node.completed,\n.node.captured,\n.node.assigned {\n  border-right: 3px solid var(--ws-accent);\n}\n.symbol {\n  flex-shrink: 0;\n  width: 29px;\n  height: 29px;\n  background: var(--ws-paper);\n  border-radius: 8px;\n  display: grid;\n  place-items: center;\n  font-size: 16px;\n  color: var(--ws-accent);\n}\n.node-info {\n  min-width: 0;\n  flex: 1;\n}\n.node-name {\n  font-size: 12px;\n  font-weight: 650;\n  display: block;\n}\n.node-description {\n  display: block;\n  font-size: 11px;\n  color: var(--ws-muted);\n  white-space: nowrap;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  max-width: 360px;\n}\n.node-code {\n  font-size: 9px;\n  font-family: monospace;\n  color: var(--ws-muted);\n  align-self: flex-start;\n}\n.branches {\n  display: grid;\n  grid-template-columns: repeat(2, minmax(160px, 1fr));\n  gap: 12px;\n  margin-top: 15px;\n  border: 1px solid var(--ws-line);\n  border-top: 0;\n  border-radius: 0 0 10px 10px;\n  padding: 0 10px 10px;\n  background: color-mix(in srgb, var(--ws-paper) 90%, transparent);\n}\n.branches.single {\n  grid-template-columns: minmax(160px, 1fr);\n}\n.branch-name {\n  display: flex;\n  align-items: center;\n  gap: 7px;\n  color: var(--ws-muted);\n  font-size: 10px;\n  letter-spacing: 0.5px;\n  margin: 0 0 12px;\n  text-align: center;\n  justify-content: center;\n}\n.branch-name span {\n  background: var(--ws-card);\n  border: 1px solid var(--ws-line);\n  padding: 2px 10px;\n  border-radius: 20px;\n}\n.branch.taken > .branch-name span {\n  background: var(--ws-accent);\n  color: var(--ws-card);\n}\n.merge {\n  font-size: 9px;\n  color: var(--ws-muted);\n  text-align: center;\n  background: var(--ws-paper);\n  position: relative;\n  margin: 5px auto 0;\n  width: 55px;\n}\n.add {\n  border-style: dashed;\n  background: var(--ws-paper);\n  font-size: 11px;\n  position: relative;\n  z-index: 1;\n  align-self: center;\n  padding: 5px 13px;\n}\n.endpoint {\n  font-size: 10px;\n  color: var(--ws-muted);\n  text-align: center;\n  border: 1px solid var(--ws-line);\n  border-radius: 20px;\n  background: var(--ws-paper);\n  padding: 4px 15px;\n  position: relative;\n  width: max-content;\n  margin: 0 auto 21px;\n  letter-spacing: 1px;\n}\n.endpoint.end {\n  margin: 22px auto 0;\n}\n.inspector {\n  border-left: 1px solid var(--ws-line);\n  background: var(--ws-card);\n  padding: 20px;\n  overflow: auto;\n  max-height: 720px;\n}\n.inspector h3 {\n  font-size: 15px;\n  margin: 5px 0 10px;\n}\n.hint {\n  color: var(--ws-muted);\n  font-size: 11px;\n  line-height: 1.8;\n  margin: 8px 0 17px;\n  overflow-wrap: anywhere;\n}\n.field {\n  display: block;\n  margin: 12px 0;\n  font-size: 11px;\n  font-weight: 550;\n}\n.field > span {\n  display: block;\n  margin-bottom: 6px;\n}\n.field input,\n.field select,\n.field textarea {\n  width: 100%;\n  background: var(--ws-paper);\n  border: 1px solid var(--ws-line);\n  border-radius: 6px;\n  padding: 8px;\n  min-height: 34px;\n  font-size: 12px;\n  font-weight: 400;\n}\n.field textarea {\n  resize: vertical;\n  min-height: 72px;\n  font-family: ui-monospace, monospace;\n}\n.field input[type=\"checkbox\"] {\n  width: auto;\n  min-height: 0;\n  vertical-align: middle;\n  margin-right: 8px;\n}\n.pair {\n  display: grid;\n  grid-template-columns: 1fr 1fr;\n  gap: 10px;\n}\n.danger {\n  color: #a55d43;\n}\n.divider {\n  border: 0;\n  border-top: 1px solid var(--ws-line);\n  margin: 20px 0;\n}\n.inspector details {\n  margin: 15px 0;\n}\n.inspector summary {\n  font-size: 11px;\n  cursor: pointer;\n  color: var(--ws-muted);\n}\n.panel {\n  padding: 24px;\n  min-height: 480px;\n}\n.panel h3 {\n  font-size: 16px;\n  margin: 0 0 6px;\n}\n.empty {\n  padding: 35px 10px;\n  text-align: center;\n  color: var(--ws-muted);\n}\n.palette {\n  padding: 20px;\n  background: var(--ws-card);\n  border-bottom: 1px solid var(--ws-line);\n}\n.palette-grid {\n  display: grid;\n  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));\n  gap: 8px;\n  margin-top: 10px;\n}\n.palette button {\n  text-align: left;\n  padding: 10px;\n}\n.palette small {\n  display: block;\n  color: var(--ws-muted);\n  font-size: 10px;\n  margin-top: 4px;\n}\n.palette h4 {\n  margin: 8px 0;\n  font-size: 11px;\n  color: var(--ws-muted);\n  letter-spacing: 1px;\n}\n.banner {\n  padding: 9px 20px;\n  font-size: 12px;\n  border-bottom: 1px solid var(--ws-line);\n  background: var(--ws-card);\n  white-space: pre-wrap;\n  overflow-wrap: anywhere;\n}\n.banner.error {\n  color: #a55d43;\n}\n.table {\n  width: 100%;\n  border-collapse: collapse;\n  font-size: 12px;\n}\n.table th,\n.table td {\n  text-align: left;\n  padding: 12px 10px;\n  border-bottom: 1px solid var(--ws-line);\n  overflow-wrap: anywhere;\n}\n.table th {\n  font-size: 10px;\n  font-weight: 500;\n  color: var(--ws-muted);\n}\ncode {\n  font:\n    11px ui-monospace,\n    monospace;\n}\n.test-grid {\n  display: grid;\n  grid-template-columns: 1fr 1fr;\n  gap: 18px;\n  max-width: 720px;\n}\n.trace {\n  margin-top: 20px;\n  max-height: 380px;\n  overflow: auto;\n}\n.trace button {\n  display: flex;\n  width: 100%;\n  text-align: left;\n  gap: 12px;\n  border: 0;\n  border-bottom: 1px solid var(--ws-line);\n  border-radius: 0;\n  padding: 9px;\n}\n.trace .status {\n  margin-left: auto;\n  color: var(--ws-accent);\n}\n.maprow {\n  display: grid;\n  grid-template-columns: 1fr 14px 1fr 28px;\n  gap: 4px;\n  align-items: center;\n  margin: 5px 0;\n}\n.maprow input {\n  width: 100%;\n  min-width: 0;\n  background: var(--ws-paper);\n  border: 1px solid var(--ws-line);\n  border-radius: 5px;\n  padding: 6px;\n  font-size: 11px;\n}\n.maprow button {\n  padding: 4px;\n}\n.footer {\n  padding: 10px 20px;\n  border-top: 1px solid var(--ws-line);\n  font-size: 10px;\n  color: var(--ws-muted);\n  display: flex;\n  justify-content: space-between;\n  gap: 10px;\n}\n.hidden {\n  display: none !important;\n}\n.row-actions {\n  display: flex;\n  gap: 5px;\n  flex-wrap: wrap;\n}\n@media (max-width: 850px) {\n  .workspace {\n    grid-template-columns: minmax(0, 1fr);\n  }\n  .inspector {\n    border-left: 0;\n    border-top: 1px solid var(--ws-line);\n    max-height: 500px;\n  }\n  .canvas {\n    max-height: 560px;\n  }\n  .head {\n    padding: 17px;\n  }\n  .test-grid {\n    grid-template-columns: 1fr;\n  }\n  .branches {\n    gap: 9px;\n  }\n  .counter {\n    display: none;\n  }\n}\n@media (prefers-color-scheme: dark) {\n  :host([data-auto-dark]) {\n    --ws-paper: #252b24;\n    --ws-card: #30372d;\n    --ws-ink: #e0e3d5;\n    --ws-muted: #adb59e;\n    --ws-line: #495241;\n    --ws-accent: #a2b288;\n    color: var(--ws-ink);\n  }\n}\n:host([data-dark]) {\n  --ws-paper: #252b24;\n  --ws-card: #30372d;\n  --ws-ink: #e0e3d5;\n  --ws-muted: #adb59e;\n  --ws-line: #495241;\n  --ws-accent: #a2b288;\n  color: var(--ws-ink);\n}\n\n/* The injected canvas is movable; dashboard instances keep normal header behavior. */\n:host([data-draggable]) .head { cursor: grab; touch-action: none; user-select: none; }\n:host([data-draggable]) .head .eyebrow::after { content: ' · 拖动标题移动'; letter-spacing: 0; }\n\n/* A quiet canvas first; settings appear only for the selected step. */\n.head { padding: 10px 18px; justify-content: flex-end; }\n.drag-handle { margin-right:auto; color:var(--ws-muted); font-size:11px; user-select:none; }\n.workspace { position:relative; grid-template-columns:minmax(0,1fr) 0px; transition:grid-template-columns .32s cubic-bezier(.22,.8,.3,1); }\n.workspace.is-inspecting { grid-template-columns:minmax(0,1fr) 290px; }\n.sequence.root { max-width:640px; }\n.inspector-slot { min-width:0; overflow:hidden; position:relative; opacity:0; transform:translateX(18px); transition:opacity .24s ease,transform .32s ease; pointer-events:none; }\n.is-inspecting .inspector-slot { opacity:1; transform:translateX(0); pointer-events:auto; }\n.inspector { position:absolute; inset:0; width:290px; height:100%; padding:16px 20px; }\n.inspector-close { display:block; margin:0 0 18px auto; font-size:11px; color:var(--ws-muted); background:transparent; }\n@media (max-width:850px) {\n  .workspace,.workspace.is-inspecting { grid-template-columns:minmax(0,1fr) 0px; }\n  .inspector-slot { position:absolute; right:0; top:0; bottom:0; width:min(310px,100%); z-index:8; transform:translateX(100%); box-shadow:-12px 0 35px #00000012; }\n  .inspector { width:100%; max-height:100%; border-top:0; border-left:1px solid var(--ws-line); }\n  .head { padding:10px 12px; }\n  .canvas { padding:24px 16px; }\n}\n@media (prefers-reduced-motion:reduce) { .workspace,.inspector-slot { transition:none; } }\n\n/* Reading-first workflow: collapsible stages, vertically ordered alternatives. */\n[hidden] { display:none !important; }\n:host { color-scheme:light; }\n:host([data-dark]) { color-scheme:dark; }\n.canvas,.inspector,.panel { scrollbar-width:thin; scrollbar-color:var(--ws-line) transparent; }\n.sequence,.sequence.root { min-width:0; }\n.sequence.root { max-width:720px; }\n.unit { margin-bottom:18px; }\n.node-row { position:relative; }\n.stage .node-row > .node { padding-right:90px; }\n.node { min-height:64px; }\n.node-info { min-width:0; }\n.node-name { line-height:1.65; overflow-wrap:anywhere; }\n.node-description { max-width:none; line-height:1.65; font-size:11px; }\n.node-code { white-space:nowrap; font:10px system-ui; color:var(--ws-muted); }\n.stage .node-code { display:none; }\n.stage-toggle { position:absolute; right:12px; top:50%; transform:translateY(-50%); border-color:transparent; background:var(--ws-paper); color:var(--ws-accent); font-size:11px; padding:6px 9px; }\n.stage-toggle span { margin-left:5px; }\n.flow-view-tools { display:flex; align-items:center; justify-content:space-between; gap:10px; font-size:10px; color:var(--ws-muted); margin:-10px 0 24px; }\n.flow-view-tools > div { display:flex; gap:5px; }\n.flow-view-tools button { font-size:10px; padding:4px 8px; background:var(--ws-paper); }\n.branches,.branches.single { display:flex; flex-direction:column; gap:12px; padding:12px 0 0; margin:0; border:0; border-radius:0; background:transparent; }\n.branch { min-width:0; border-left:2px solid var(--ws-line); padding:0 0 0 12px; }\n.branches .branches .branch { padding-left:8px; }\n.branch-name { justify-content:flex-start; align-items:center; gap:8px; text-align:left; margin:0 0 10px; flex-wrap:wrap; letter-spacing:0; }\n.branch-name span { font-size:10px; padding:3px 10px; }\n.branch-name small { font-size:10px; overflow-wrap:anywhere; }\n.branch.taken { border-left-color:var(--ws-accent); }\n.empty-route { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:4px 0 10px; font-size:11px; color:var(--ws-muted); }\n.empty-route .add { white-space:nowrap; padding:3px 7px; font-size:10px; border-color:transparent; }\n.add { border:0; font-size:10px; opacity:.8; padding:4px 10px; }\n.add:hover,.add:focus-visible { opacity:1; background:var(--ws-card); }\n.merge { width:max-content; max-width:100%; font-size:10px; margin:12px auto 0; }\n.decision-note { color:var(--ws-muted); font-size:11px; margin:10px 0 0; }\n.note-card .node { align-items:flex-start; border-left:3px solid var(--ws-accent); }\n.note-card .node-description { white-space:pre-wrap; overflow:visible; text-overflow:clip; margin-top:5px; }\n.note-card .symbol { font-size:15px; }\n.note-card.tone-warning .node { border-left-color:#b19048; }\n.note-card.tone-danger .node { border-left-color:#ba755d; }\n.note-card.tone-success .node { border-left-color:#79915b; }\n.case-editor { margin:14px 0; padding:12px 10px; border:1px solid var(--ws-line); border-radius:9px; background:var(--ws-paper); }\n.case-heading { display:flex; align-items:center; justify-content:space-between; gap:4px; font-size:11px; }\n.case-heading button { padding:3px 5px; font-size:10px; }\n.panel { max-height:800px; overflow:auto; }\n.panel-intro { margin-bottom:22px; }\n.panel-intro h3 { font-size:20px; margin:6px 0 8px; }\n.panel-kicker { color:var(--ws-muted); font-size:10px; letter-spacing:1px; }\n.param-section { margin:20px 0; padding-top:18px; border-top:1px solid var(--ws-line); }\n.param-section h4,.run-option h4,.trace-section h4 { font-size:14px; margin:0 0 6px; }\n.section-heading { display:flex; align-items:flex-start; gap:12px; margin-bottom:12px; }\n.section-heading > div { flex:1; }\n.section-heading p { color:var(--ws-muted); font-size:11px; margin:4px 0; }\n.section-heading > button { font-size:10px; }\n.section-number { color:var(--ws-accent); background:var(--ws-card); border:1px solid var(--ws-line); width:28px; height:28px; display:grid; place-items:center; border-radius:8px; font-size:10px; flex-shrink:0; }\n.param-card { display:flex; align-items:center; gap:14px; padding:14px; background:var(--ws-card); border:1px solid var(--ws-line); border-radius:9px; margin:8px 0; }\n.param-label { flex:1; min-width:0; }\n.param-label strong,.param-label code,.param-label small { display:block; }\n.param-label strong { font-size:12px; }\n.param-label code { font-size:10px; color:var(--ws-muted); margin:3px 0; overflow-wrap:anywhere; }\n.param-label small { font-size:10px; color:var(--ws-muted); overflow-wrap:anywhere; }\n.param-card > button { font-size:10px; flex-shrink:0; }\n.param-value { width:40%; min-width:100px; }\n.param-value input:not([type=checkbox]),.param-value textarea,.sample-field input,.sample-field textarea,.sample-field > select,.sample-add input { display:block; width:100%; min-width:0; background:var(--ws-paper); border:1px solid var(--ws-line); border-radius:7px; padding:8px 10px; font:12px system-ui; color:var(--ws-ink); }\n.param-value textarea { min-height:75px; resize:vertical; }\n.param-boolean { display:flex; align-items:center; gap:8px; font-size:12px; }\ninput[type=checkbox] { accent-color:var(--ws-accent); }\n.value-badge { background:var(--ws-paper); border:1px solid var(--ws-line); border-radius:20px; padding:4px 9px; font-size:10px; color:var(--ws-muted); white-space:nowrap; }\n.context-cards,.run-options { display:grid; grid-template-columns:1fr 1fr; gap:12px; }\n.context-cards > div,.run-option { border:1px solid var(--ws-line); padding:16px; border-radius:10px; background:var(--ws-card); }\n.context-cards strong,.context-cards code { display:block; }\n.context-cards p,.run-option p { font-size:11px; line-height:1.8; color:var(--ws-muted); margin:8px 0 0; }\n.context-cards code { margin-top:6px; color:var(--ws-muted); }\n.technical { margin:15px 0; border-top:1px solid var(--ws-line); padding-top:12px; }\n.technical summary { font-size:11px; color:var(--ws-muted); cursor:pointer; }\n.technical pre { white-space:pre-wrap; overflow-wrap:anywhere; font-size:10px; max-height:260px; overflow:auto; }\n.section-empty { color:var(--ws-muted); font-size:11px; padding:12px 0; }\n.run-option.chosen { border-color:var(--ws-accent); }\n.run-option h4 { margin-top:12px; }\n.run-option button { margin-top:12px; font-size:11px; }\n.sample-field { padding:12px 14px; border:1px solid var(--ws-line); background:var(--ws-card); border-radius:9px; margin:8px 0; }\n.sample-label { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px; }\n.sample-label strong { font-size:12px; }\n.sample-label code { font-size:10px; color:var(--ws-muted); }\n.sample-label > select { margin-left:auto; font-size:10px; border:0; background:var(--ws-paper); padding:3px; border-radius:4px; }\n.sample-add { display:flex; gap:8px; margin:12px 0; }\n.sample-add button { white-space:nowrap; font-size:11px; }\n.sample-warning { color:#b0774b; font-size:11px; }\n.run-actions { display:flex; align-items:center; gap:14px; margin:22px 0; }\n.run-actions > span { font-size:11px; color:var(--ws-muted); }\n.trace-section { border-top:1px solid var(--ws-line); padding-top:20px; }\n.trace { margin-top:10px; }\n.trace button { align-items:flex-start; border:1px solid var(--ws-line); border-radius:8px; margin:6px 0; padding:12px; }\n.trace-index { width:23px; font-size:10px; color:var(--ws-muted); }\n.trace-copy { min-width:0; flex:1; }\n.trace-copy strong { display:block; font-size:12px; }\n.trace-copy small { display:block; font-size:11px; line-height:1.7; color:var(--ws-muted); margin-top:4px; }\n.trace .status { font-size:10px; }\n@media(max-width:850px) {\n .node-code { display:none; }\n .param-card { flex-wrap:wrap; gap:10px; }\n .param-value { flex-basis:100%; order:3; }\n .context-cards,.run-options,.test-grid { grid-template-columns:1fr; }\n .panel { padding:18px 14px; }\n .flow-view-tools { flex-wrap:wrap; }\n .branch { padding-left:8px; }\n .empty-route { align-items:flex-start; }\n}\n";
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
    IF: ["条件判断", "◇", "条件成立或不成立，只走一条路径"],
    SWITCH: ["按情况处理", "⑂", "从上到下匹配，执行第一个符合的情况；都不符合则走默认路径"],
    GROUP: ["步骤组", "▤", "组织步骤与局部变量"],
    GUARD: ["跳过 / 跳转", "↪", "满足条件时跳组或前往锚点"],
    TRY: ["重试与兜底", "↻", "有限重试，保护有副作用的动作"],
    LABEL: ["锚点", "⚑", "供同一分支内向前跳转"],
  };
  const controls = new Set([
    "SET",
    "CAPTURE",
    "IF",
    "SWITCH",
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
  function branchEntries(n) {
    if(n.action === 'SWITCH') return [...(n.value?.cases || []).map((c,i)=>({key:'case_'+i,title:c.label || '情况 '+(i+1),steps:c.steps || [],condition:c.condition})),{key:'default',title:'其他情况',steps:n.value?.default || []}];
    if(n.action==='IF')return [{key:'then',title:'条件成立',steps:n.value?.then || []},{key:'else',title:'条件不成立',steps:n.value?.else || []}];
    const names={then:'条件成立',else:'条件不成立',steps:n.action==='TRY'?'尝试执行':'阶段内步骤',fallback:'执行失败时'};
    return ['IF','GROUP','TRY'].includes(n.action) ? ['then','else','steps','fallback'].filter(k=>Array.isArray(n.value?.[k])).map(k=>({key:k,title:names[k],steps:n.value[k]})) : [];
  }
  function branchList(n,key) {
    if(n.action==='SWITCH' && /^case_\d+$/.test(key)) return n.value.cases[Number(key.slice(5))]?.steps;
    return n.value?.[key];
  }
  const friendlyNames={temporary_chat:'使用临时对话',temporary_pressed:'临时对话是否开启',current_model:'当前模型',model_label:'目标模型',desired_pattern:'目标模型规则',menu_expanded:'模型菜单是否展开',mode_target:'目标模式',selected_selector:'选中状态定位'};
  const friendly = name => friendlyNames[name] || name;
  const hintData = n => typeof n.value==='object' && n.value && !Array.isArray(n.value) ? {title:n.label || n.value.title || '流程说明',text:String(n.value.text ?? ''),tone:['info','success','warning','danger'].includes(n.value.tone)?n.value.tone:'info'} : {title:n.label || '流程说明',text:text(n.value),tone:'info'};
  const parseObject = value => {const v=JSON.parse(value);if(!v||typeof v!=='object'||Array.isArray(v)) throw Error('需要 JSON 对象');return v;};
  const valueType=v=>typeof v==='boolean'?'boolean':typeof v==='number'?'number':(v===null||typeof v==='object')?'json':'text';
  function walk(nodes, fn, prefix = "root") {
    (nodes || []).forEach((n, i) => {
      const p = `${prefix}.${i}`;
      fn(n, p);
      branchEntries(n).forEach(b=>walk(b.steps,fn,`${p}.${b.key}`));
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
        if(n.action==='SWITCH') {
          if(!Array.isArray(n.value.cases)||!n.value.cases.length||n.value.cases.length>32) throw Error('多分支需要 1–32 个情况');
          n.value.cases.forEach(c=>{if(!c||typeof c!=='object'||!c.condition)throw Error('情况缺少条件');assertTree(c.steps || [],depth+1,budget);});
          assertTree(n.value.default || [],depth+1,budget);
        } else for (const key of ["then", "else", "steps", "fallback"])
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
      SWITCH: {cases:[{label:'情况 1',condition:{left:'{current}',op:'eq',right:'情况 A'},steps:[]},{label:'情况 2',condition:{left:'{current}',op:'eq',right:'情况 B'},steps:[]}],default:[]},
      GROUP: { variables: {}, steps: [] },
      GUARD: { condition, mode: "skip_group" },
      TRY: { steps: [], attempts: 2, delay: 0.3, retry_side_effects: false },
      LABEL: { name: "continue_here" },
      WAIT: 1,
      KEY_PRESS: "Enter",
      FILL_INPUT: "{prompt}",
      JS_EXEC: {},
      PAGE_FETCH: {},
      READONLY_HINT: {title:"流程说明",text:"在这里填写操作说明。",tone:"info"},
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
      this.expanded = new Set();
      this.traceMode = options.injected ? "real" : "simulation";
      this.captureTypes = {};
      this.inputTypes = {};
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
        this.expanded.clear();
        this.trace = [];
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
    seq(path, create = false) {
      if (path === "root") return this.workflow;
      const parts = path.split(".");
      const branch = parts.pop();
      const n=this.node(parts.join('.'));
      let list=branchList(n,branch);
      if(create && !Array.isArray(list)) {
        list=[];
        if(n.action==='SWITCH' && /^case_\d+$/.test(branch))n.value.cases[Number(branch.slice(5))].steps=list;
        else n.value[branch]=list;
      }
      return list;
    }
    node(path) {
      const parts = path.split(".");
      let list = this.workflow,
        node;
      for (let i = 1; i < parts.length; i += 2) {
        node = list[Number(parts[i])];
        if (!node) throw Error("步骤不存在");
        if (i + 1 < parts.length) list = branchList(node,parts[i + 1]);
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
      for(const [cmd,stack] of [['undo',this.undoStack],['redo',this.redoStack]]) {
        const button=this.root.querySelector(`[data-cmd="${cmd}"]`);if(button)button.disabled=!stack.length;
      }
      this.options.onChange?.(this.getWorkflow());
    }
    summary(node) {
      const v = node.value || {};
      switch (node.action) {
        case "READONLY_HINT": return hintData(node).text;
        case "SWITCH": return `${v.cases?.length || 0} 种情况 · 首个命中后继续后续流程`;
        case "SET":
          return `${v.name || "变量"} ← ${v.value?.$map ? "映射字典 · " + Object.keys(v.value.$map).length + " 条规则" : v.value?.$transform ? "转换 · " + v.value.$transform : text(v.value)}`;
        case "CAPTURE":
          return `从网页读取${({text:'文字',attribute:'属性',class:'样式类名',url:'网址',exists:'是否存在',value:'输入值'})[v.source || 'text']} → ${friendly(v.name || '状态')}`;
        case "IF":
        case "GUARD":
          return this.describeCondition(v.condition);
        case "TRY":
          return `最多 ${v.attempts || 1} 次 · ${v.retry_side_effects ? "允许重复副作用" : "副作用保护开启"}`;
        case "GROUP":
          return `${v.steps?.length || 0} 个直属步骤${Object.keys(v.variables || {}).length ? " · " + Object.keys(v.variables).length + " 项预设参数" : ""}`;
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
    describeCondition(c) {
      const show=v=>typeof v==='string'?v.replace(/\{\{?\s*(?:vars\.)?([\w]+)\s*\}?\}/g,(_,k)=>'「'+friendly(k)+'」'):text(v);
      if(!c) return '尚未设置条件';
      if(c.all) return c.all.map(x=>this.describeCondition(x)).join('，并且 ');
      if(c.any) return c.any.map(x=>this.describeCondition(x)).join('，或者 ');
      if(c.not) return '不满足：'+this.describeCondition(c.not);
      return `${show(c.left)} ${ops[c.op] || c.op} ${['exists','not_exists'].includes(c.op)?'':show(c.right)}`;
    }
    reveal(path) {walk(this.workflow,(n,p)=>{if(path.startsWith(p+'.'))this.expanded.add(p);});}
    sequence(nodes, path = "root") {
      return `<div class="sequence ${path==='root'?'root':''}">${nodes.map((n,i)=>{
        const p=path+'.'+i,k=kinds[n.action] || [n.action,'?'],hits=this.trace.filter(t=>t.path===p),status=hits.at(-1)?.status || '';
        const entries=branchEntries(n),foldable=['GROUP','SWITCH','TRY'].includes(n.action),open=!foldable||this.expanded.has(p);
        const hint=n.action==='READONLY_HINT'?hintData(n):null;
        const title=hint?hint.title:(n.label || k[0]);
        return `<div class="unit ${hint?'note-card tone-'+hint.tone:''} ${foldable?'stage':''}"><div class="node-row"><button type="button" data-cmd="select" data-path="${p}" class="node ${controls.has(n.action)?'control':''} ${this.selected===p?'selected':''} ${esc(status)}" aria-pressed="${this.selected===p}"><span class="symbol">${hint?({info:'i',success:'✓',warning:'!',danger:'!'})[hint.tone]:k[1]}</span><span class="node-info"><span class="node-name">${esc(title)}</span><span class="node-description">${esc(this.summary(n))}</span></span><span class="node-code" title="${esc(n.action)}">${esc(hint?'说明':k[0])}</span></button>${foldable?`<button class="stage-toggle" data-cmd="toggle-stage" data-path="${p}" aria-expanded="${open}" aria-label="${open?'收起':'展开'} ${esc(title)}">${open?'收起':'展开'} <span>${open?'⌃':'⌄'}</span></button>`:''}</div>${entries.length?`<div class="stage-content" ${open?'':'hidden'}>${n.action==='SWITCH'?'<p class="decision-note">从上到下检查，只执行第一条符合的情况。</p>':''}<div class="branches single ${n.action==='IF'||n.action==='SWITCH'?'decisions':''}">${entries.map((b,j)=>`<section class="branch ${hits.some(t=>t.branch===b.key)?'taken':''}"><div class="branch-name"><span>${esc(n.action==='SWITCH' && b.key!=='default' ? String(j+1).padStart(2,'0')+' · '+b.title : b.title)}</span>${n.action==='SWITCH'&&b.condition?`<small>${esc(this.describeCondition(b.condition))}</small>`:''}</div>${b.steps.length?this.sequence(b.steps,p+'.'+b.key):`<div class="empty-route"><span>${b.key==='default'?'其余情况不做额外操作，继续后续流程':'不做额外操作，继续后续流程'}</span><button class="add" data-cmd="palette" data-path="${p+'.'+b.key}" title="为这条路径添加步骤">＋ 添加</button></div>`}</section>`).join('')}</div><div class="merge">继续后续流程 ↓</div></div>`:''}</div>`;
      }).join('')}<button class="add" data-cmd="palette" data-path="${path}" type="button">＋ 添加步骤</button></div>`;
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
          : ["json","multiline"].includes(type)
            ? `<textarea ${attrs} spellcheck="false">${esc(type==="json"?JSON.stringify(value ?? {}, null, 2):String(value ?? ""))}</textarea>`
            : type === "checkbox"
              ? `<input ${attrs} type="checkbox" ${value ? "checked" : ""}>启用`
              : `<input ${attrs} type="${type === "number" ? "number" : "text"}" ${type === "number" ? 'step="any"' : ""} value="${esc(text(value))}">`
      }</label>`;
    }
    condition(c, base = "value.condition") {
      if (c && !c.op)
        return `<p class="hint">这是组合条件（all / any / not）。保留完整结构，可在下方编辑。</p>${this.field("组合条件", base, c, "json")}`;
      return (
        this.field(
          "左值 · 可引用 {变量}",
          base+".left",
          c?.left,
          "typed",
        ) +
        this.field(
          "比较方式",
          base+".op",
          c?.op || "eq",
          "text",
          ops,
        ) +
        (!["exists", "not_exists"].includes(c?.op)
          ? this.field(
              "右值 · 数字 / 布尔 / 文本",
              base+".right",
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
      if(n.action==='SWITCH') {
        body += '<p class="hint">从上到下匹配，只执行第一个符合的情况；全部不符合则执行默认路径。可在画布为每条路径添加步骤。</p>';
        body += (v.cases || []).map((c,i)=>`<section class="case-editor"><div class="case-heading"><strong>情况 ${i+1}</strong><div><button data-cmd="case-up" data-index="${i}" ${i===0?'disabled':''} title="提高优先级">↑</button><button data-cmd="case-down" data-index="${i}" ${i===v.cases.length-1?'disabled':''} title="降低优先级">↓</button><button class="danger" data-cmd="case-remove" data-index="${i}" ${v.cases.length<=1?'disabled':''}>删除</button></div></div>${this.field('情况名称','value.cases.'+i+'.label',c.label || '')}${this.condition(c.condition,'value.cases.'+i+'.condition')}</section>`).join('');
        body += `<button data-cmd="case-add" ${(v.cases || []).length>=32?'disabled':''}>＋ 添加一种情况</button><p class="hint">默认路径始终保留在最后，不需要写条件。</p>`;
      }
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
        body += `<p class="hint">本阶段的参数可在「流程参数」中逐项编辑；它们只在本阶段内生效。</p><button data-cmd="tab" data-tab="variables">编辑阶段参数 →</button><details><summary>高级：完整参数对象</summary>${this.field("局部变量对象", "value.variables", v.variables || {}, "json")}</details>`;
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
      if(n.action==='READONLY_HINT') {
        if(n.value && typeof n.value==='object' && !Array.isArray(n.value)) {
          body += this.field('说明标题','value.title',v.title || '')+this.field('说明正文','value.text',v.text || '', 'multiline')+this.field('提示类型','value.tone',v.tone || 'info','text',{info:'普通说明',success:'完成提示',warning:'注意事项',danger:'风险提醒'});
        } else body += this.field('说明正文','value',n.value || '', 'multiline');
      }
      if (!controls.has(n.action) && n.action!=='READONLY_HINT') {
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
        branchEntries(item).forEach(b=>destinations.push([path+'.'+b.key,(item.label || kinds[item.action]?.[0] || item.action)+' / '+b.title]));
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
    definitions() {
      const params=[],states=[],assignments=[];
      walk(this.workflow,(n,path)=>{
        if(n.action==='GROUP') for(const [key,value] of Object.entries(n.value?.variables || {})) params.push({key,value,path,group:n.label || '未命名阶段'});
        if(n.action==='CAPTURE') states.push({key:n.value?.name,path,node:n});
        if(n.action==='SET') assignments.push({key:n.value?.name,path,node:n});
      });
      return {params,states,assignments};
    }
    inputReferences() {
      const refs=new Set();
      const scan=v=>{if(typeof v==='string'){for(const m of v.matchAll(/(?:\{\{?\s*|^)(?:inputs\.)([A-Za-z_][\w]*)/g))refs.add(m[1]);}else if(v&&typeof v==='object')Object.values(v).forEach(scan);};
      walk(this.workflow,n=>{if(n.action!=='JS_EXEC')scan(n.value);scan(n.selector);scan(n.target);});
      return [...refs];
    }
    paramInput(d) {
      const type=valueType(d.value),attrs=`data-param-node="${esc(d.path)}" data-param-key="${esc(d.key)}" data-value-type="${type}" aria-label="${esc(d.group+' / '+friendly(d.key))}"`;
      if(type==='boolean')return `<label class="param-boolean"><input type="checkbox" ${attrs} ${d.value?'checked':''}>启用</label>`;
      if(type==='json')return `<textarea ${attrs} spellcheck="false">${esc(JSON.stringify(d.value,null,2))}</textarea>`;
      return `<input type="${type==='number'?'number':'text'}" ${type==='number'?'step="any"':''} ${attrs} value="${esc(d.value ?? '')}">`;
    }
    variablesPanel() {
      const {params,states,assignments}=this.definitions();
      const rowLink=(path,label='定位步骤')=>`<button data-cmd="select" data-path="${esc(path)}">${label} ↗</button>`;
      return `<div class="panel params-panel"><div class="panel-intro"><span class="panel-kicker">看懂数据从哪里来</span><h3>流程参数</h3><p class="hint">预设参数是已设定的内容；页面状态要运行时才能读取。这里不会展示上一次请求的状态值。</p></div>
      <section class="param-section"><div class="section-heading"><span class="section-number">01</span><div><h4>预设里已经设好的参数</h4><p>可以直接修改。每项只在所属阶段内生效，修改后请保存配置。</p></div></div>${params.length?params.map(d=>`<div class="param-card"><div class="param-label"><strong>${esc(friendly(d.key))}</strong><code>${esc(d.key)}</code><small>${esc(d.group)} · 阶段内参数</small></div><div class="param-value">${this.paramInput(d)}</div>${rowLink(d.path,'查看阶段')}</div>`).join(''):'<p class="section-empty">此流程没有阶段初始参数，无需额外填写。</p>'}</section>
      <section class="param-section"><div class="section-heading"><span class="section-number">02</span><div><h4>从页面读取的状态</h4><p>这些不是预设值。真实运行时从网页读取，模拟时由你提供样例。</p></div></div>${states.map(d=>`<div class="param-card"><div class="param-label"><strong>${esc(d.node.label || friendly(d.key))}</strong><code>${esc(d.key)}</code><small>${esc(this.summary(d.node))}</small></div><span class="value-badge">运行时读取</span>${rowLink(d.path,'编辑读取方式')}</div>`).join('')||'<p class="section-empty">此流程无需读取页面状态。</p>'}</section>
      ${assignments.length?`<section class="param-section"><div class="section-heading"><span class="section-number">＋</span><div><h4>运行中计算与赋值</h4><p>会按执行顺序计算，不是当前已获得的结果。</p></div></div>${assignments.map(d=>`<div class="param-card"><div class="param-label"><strong>${esc(d.node.label || friendly(d.key))}</strong><code>${esc(d.key)}</code><small>${esc(this.summary(d.node))}</small></div><span class="value-badge">${d.node.value?.scope==='global'?'本次请求内':'当前作用域'}</span>${rowLink(d.path,'编辑计算')}</div>`).join('')}</section>`:''}
      <section class="param-section"><div class="section-heading"><span class="section-number">03</span><div><h4>随请求传入的内容</h4><p>由调用方传入，不会改写预设。试填样例请前往「检查与试跑」。</p></div></div><div class="context-cards"><div><strong>请求正文</strong><code>{prompt}</code><p>供填写步骤使用；固定文本步骤仍使用自己的文本。</p></div><div><strong>请求模型名称</strong><code>{model}</code><p>只有引用它的步骤才会使用，不会强制改变预设指定的模型。</p></div></div><p class="hint">${this.inputReferences().length?'显式引用的自定义入参：'+this.inputReferences().map(esc).join('、'):'未发现显式引用的自定义入参。普通流程无需填写 JSON。'}</p><details class="technical"><summary>高级：变量引用与作用域</summary><p class="hint">inputs 只读；vars 是本次请求中的变量副本。组内变量在离开阶段后释放，全局变量也不会跨请求保留。完整占位符保留类型，嵌入文本才转为字符串。</p><code>{{inputs.name}} / {{vars.name}} / {{context.model}}</code></details></section>
      <button data-cmd="palette" data-path="root">＋ 添加参数计算或状态读取</button></div>`;
    }
    sampleField(key,value,type,scope,description) {
      const attr=`data-sample-scope="${scope}" data-sample-key="${esc(key)}" data-value-type="${type}" aria-label="${scope==='captures'?'状态样例':'请求参数'} ${esc(key)}"`;
      const input=type==='boolean'?`<select ${attr}><option value="" ${value===undefined?'selected':''}>请选择样例</option><option value="true" ${value===true?'selected':''}>是 / true</option><option value="false" ${value===false?'selected':''}>否 / false</option></select>`:type==='json'?`<textarea ${attr}>${value===undefined?'':esc(JSON.stringify(value,null,2))}</textarea>`:`<input ${attr} type="${type==='number'?'number':'text'}" ${type==='number'?'step="any"':''} value="${value===undefined?'':esc(value)}" placeholder="填写模拟值，不会操作网页">`;
      return `<div class="sample-field"><div class="sample-label"><strong>${esc(description || friendly(key))}</strong><code>${esc(key)}</code><select data-sample-type="${esc(key)}" data-sample-scope="${scope}" aria-label="${esc(key)} 的样例类型">${Object.entries({text:'文本',boolean:'布尔值',number:'数值',json:'JSON'}).map(([k,v])=>`<option value="${k}" ${k===type?'selected':''}>${v}</option>`).join('')}</select></div>${input}</div>`;
    }
    sampleFields(scope) {
      let values;
      try{values=parseObject(this[scope]);}catch(_){return '<p class="sample-warning">高级 JSON 尚未填写完整，请先在下方修正；表单不会覆盖它。</p>';}
      const definitions=this.definitions().states;
      const keys=scope==='captures'?[...new Set(definitions.map(d=>d.key))]:[...new Set([...this.inputReferences(),...Object.keys(values)])];
      return keys.map(key=>{
        const d=definitions.find(d=>d.key===key),stored=Object.hasOwn(values,key)?values[key]:undefined;
        const types=scope==='captures'?this.captureTypes:this.inputTypes;
        const type=(Object.hasOwn(types,key)?types[key]:null) || (stored!==undefined?valueType(stored):(scope==='captures'&&d?.node.value?.source==='exists'?'boolean':'text'));
        return this.sampleField(key,stored,type,scope,scope==='captures'?d?.node.label:undefined);
      }).join('') || `<p class="section-empty">${scope==='captures'?'此流程不需要页面状态样例。':'当前没有自定义入参，通常无需添加。'}</p>`;
    }
    traceText(t) {
      let n;try{n=this.node(t.path);}catch(_){return statuses[t.status] || t.status;}
      if(t.status==='branch') {
        const b=branchEntries(n).find(b=>b.key===t.branch);
        if(n.action==='SWITCH')return `${t.branch==='default'?'所有情况均未命中，走':'首个命中'}「${b?.title || t.branch}」；其他情况不执行`;
        return `${t.branch==='then'?'条件成立':'条件不成立'}，${b?.steps.length?'进入对应路径':'不做额外操作，继续后续流程'}`;
      }
      if(this.traceMode==='simulation' && t.status==='captured')return '已代入你填写的状态样例（未读取网页）';
      if(t.status==='planned')return '计划执行 · 未操作网页';
      return (statuses[t.status] || t.status)+(t.attempt?' · 第 '+t.attempt+' 次':'');
    }
    testPanel() {
      const real=!!this.options.injected;
      let invalid=false;try{parseObject(this.captures);parseObject(this.inputs);}catch(_){invalid=true;}
      const realCard=real?'<p>当前已连接网页。确认后会点击、填写或发送，测试中可停止。</p>':`<p>需要在受控浏览器打开目标站点，再进入页面操作面板。此按钮只打开编辑器，不自动执行。</p><button data-cmd="open-real-test" ${this.options.onOpenRealTest?'':'disabled'}>打开网页操作面板 ↗</button>`;
      return `<div class="panel test-panel"><div class="panel-intro"><span class="panel-kicker">先确认路径，再操作网页</span><h3>检查与试跑</h3><p class="hint">模拟结果不等于网页执行成功。真实试跑可能发送消息，请确认目标网页和测试正文。</p></div><div class="run-options"><section class="run-option ${real?'':'chosen'}"><span class="value-badge">不点击 · 不发送</span><h4>模拟流程</h4><p>根据你提供的状态样例，解释将走哪条路径。${real?'请在控制台使用模拟功能。':'不检查页面元素是否有效，也不验证重试和兜底效果。'}</p></section><section class="run-option ${real?'chosen':''}"><span class="value-badge">会操作真实网页</span><h4>真实试跑</h4>${realCard}</section></div>
      <section class="param-section"><h4>${real?'本次试跑的内容':'本次模拟的输入'}</h4><div class="test-grid"><label class="field"><span>请求模型名称（按需填写）</span><input data-test="model" value="${esc(this.model)}" placeholder="只供引用请求模型的步骤使用"></label><label class="field"><span>${real?'测试正文':'模拟正文（可选）'}</span><textarea data-test="prompt">${esc(this.prompt)}</textarea></label></div><p class="hint">这里不会改写预设指定的目标模型；填写步骤若配置固定文本，仍使用自己的文本。</p></section>
      ${real?'':`<section class="param-section"><div class="section-heading"><div><h4>假设网页当前是什么状态？</h4><p>这是模拟样例，不是真实读取结果。同名状态会共用一个样例。</p></div><button data-cmd="sample-defaults">使用已配置的默认值</button></div><div data-sample-fields="captures">${this.sampleFields('captures')}</div><p class="hint">属性值通常是文本，例如文本 true 与布尔值 true 不同；可在每项右侧选择类型。</p></section>`}
      <details class="technical"><summary>自定义请求参数（按需填写）</summary><div data-sample-fields="inputs">${this.sampleFields('inputs')}</div><div class="sample-add"><input data-new-input placeholder="新增参数名，如 language" aria-label="新增请求参数名称"><button data-cmd="sample-add">添加参数</button></div></details>
      <details class="technical" ${invalid?'open':''}><summary>高级：直接编辑样例 JSON</summary><label class="field"><span>自定义请求参数 · workflow_variables</span><textarea data-test="inputs">${esc(this.inputs)}</textarea></label>${real?'':`<label class="field"><span>页面状态样例 · 仅用于模拟</span><textarea data-test="captures">${esc(this.captures)}</textarea></label>`}<p class="hint">与表单是同一份数据；不会删除未被当前流程引用的自定义键。</p></details>
      <div class="run-actions"><button class="primary" data-cmd="test" ${this.busy?'disabled':''}>${this.busy?'计算中…':real?'运行真实测试':'模拟分支路径'}</button><span>${real?'执行前会再次确认':'只计算路径，不访问网页'}</span></div>
      <section class="trace-section"><h4>${this.traceMode==='simulation'?'模拟路径说明':'真实执行轨迹'}</h4><div class="trace" aria-live="polite">${this.trace.length?this.trace.map((t,i)=>{let n;try{n=this.node(t.path);}catch(_){}return `<button data-cmd="select" data-path="${esc(t.path)}"><span class="trace-index">${i+1}</span><span class="trace-copy"><strong>${esc(n?.label || kinds[n?.action]?.[0] || '流程控制')}</strong><small>${esc(this.traceText(t))}</small></span><span class="status">${esc(statuses[t.status] || t.status)}</span></button>`;}).join(''):'<p class="section-empty">运行后会逐步解释命中的情况、跳过的路径及后续动作。不会记录请求正文或变量值。</p>'}</div>${this.trace.length?`<details class="technical"><summary>技术轨迹（路径与状态）</summary><pre>${esc(JSON.stringify(this.trace,null,2))}</pre></details>`:''}</section></div>`;
    }
    editPanelValue(el) {
      try {
        if(el.dataset.paramNode){
          const n=this.node(el.dataset.paramNode),type=el.dataset.valueType;
          const value=this.readTypedInput(el,type);
          this.checkpoint('param:'+el.dataset.paramNode+':'+el.dataset.paramKey);
          Object.defineProperty(n.value.variables,el.dataset.paramKey,{value,writable:true,enumerable:true,configurable:true});
          this.emit();el.setCustomValidity('');return true;
        }
        if(el.dataset.sampleKey){
          const scope=el.dataset.sampleScope,values=parseObject(this[scope]);
          if(el.tagName==='SELECT' && el.value==='')delete values[el.dataset.sampleKey];
          else Object.defineProperty(values,el.dataset.sampleKey,{value:this.readTypedInput(el,el.dataset.valueType),writable:true,enumerable:true,configurable:true});
          this[scope]=JSON.stringify(values,null,2);el.setCustomValidity('');
          const raw=this.root.querySelector(`[data-test="${scope}"]`);if(raw)raw.value=this[scope];
          return true;
        }
      }catch(e){el.setCustomValidity(e.message);el.reportValidity();return true;}
      return false;
    }
    syncSamples(el) {
      const scope=el.dataset.test;
      try {
        parseObject(this[scope]);el.setCustomValidity('');
        if(scope==='captures')this.captureTypes={};else this.inputTypes={};
      }catch(e){el.setCustomValidity(e.message);}
      const fields=this.root.querySelector(`[data-sample-fields="${scope}"]`);
      if(fields)fields.innerHTML=this.sampleFields(scope);
    }
    readTypedInput(el,type) {
      if(type==='boolean')return el.type==='checkbox'?el.checked:el.value==='true';
      if(type==='number'){const n=Number(el.value);if(!el.value.trim()||!Number.isFinite(n))throw Error('请输入有效数字');return n;}
      if(type==='json')return JSON.parse(el.value);
      return el.value;
    }
    render() {
      if(this.selected && this.tab==='flow') this.reveal(this.selected);
      const opened = new Set([...this.root.querySelectorAll("details[open]")].map(d=>d.querySelector("summary")?.textContent));
      const scroll = this.root.querySelector(".canvas")?.scrollTop || 0;
      const wasOpen = !!this.root.querySelector('.workspace.is-inspecting');
      const previousInspector = this.root.querySelector('.inspector')?.innerHTML || '';
      const openInspector = this.tab === 'flow' && !!this.selected;
      let count = 0;
      walk(this.workflow, () => count++);
      let panel =
        this.tab === "flow"
          ? `<div class="workspace ${wasOpen ? 'is-inspecting' : ''}"><div class="canvas"><div class="flow-view-tools"><span>先看阶段，再展开步骤</span><div><button data-cmd="expand-all">展开全部</button><button data-cmd="collapse-all">收起阶段</button></div></div><div class="endpoint">请求开始</div>${this.sequence(this.workflow)}<div class="endpoint end">流程结束</div></div><div class="inspector-slot" ${openInspector ? '' : 'inert aria-hidden="true"'}><aside class="inspector" aria-label="步骤设置">${this.selected ? '<button type="button" class="inspector-close" data-cmd="close-inspector" aria-label="关闭步骤设置">× 关闭设置</button>' + this.inspector() : (wasOpen ? previousInspector : '')}</aside></div></div>`
          : this.tab === "variables"
            ? this.variablesPanel()
            : this.testPanel();
      this.root.innerHTML = `<style>${window.WORKFLOW_STUDIO_CSS || ""}</style><section class="studio"><header class="head">${this.options.injected ? '<span class="drag-handle">⠿ 拖动移动</span>' : ''}<div class="actions">${this.options.onPageView ? '<button data-cmd="page-view">← 页面操作</button>' : ""}<button data-cmd="undo" ${!this.undoStack.length ? "disabled" : ""} title="撤销">↶</button><button data-cmd="redo" ${!this.redoStack.length ? "disabled" : ""} title="重做">↷</button><button data-cmd="import" title="只导入动作与工作流定位器，不导入整个预设">导入工作流</button><button data-cmd="export" title="只导出当前流程；完整预设请用站点内的导入 / 导出">导出工作流</button><button data-cmd="validate">检查流程</button>${this.options.onSave ? '<button class="primary" data-cmd="save">保存配置</button>' : ""}${this.options.onClose ? '<button data-cmd="close" title="收起编辑器">×</button>' : ""}</div></header><nav class="tabs">${[
        ["flow", "流程画布"],
        ["variables", "流程参数"],
        ["test", "检查与试跑"],
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
      }${panel}<footer class="footer"><span>查看不会执行动作 · 修改后请保存配置</span><span>保留原始字段与执行顺序</span></footer><input class="hidden" type="file" accept="application/json,.json" data-import></section>`;
      this.root.querySelectorAll("details").forEach(d=>{if(opened.has(d.querySelector("summary")?.textContent))d.open=true;});
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
          node.action==='READONLY_HINT'?hintData(node).title:(node.label || kinds[node.action]?.[0] || node.action);
        el.querySelector(".node-description").textContent = this.summary(node);
        if(node.action==='READONLY_HINT') el.closest('.unit').className='unit note-card tone-'+hintData(node).tone;
      }
      this.root.querySelector('[data-cmd="undo"]').disabled =
        !this.undoStack.length;
    }
    input(e) {
      const el = e.target;
      if(el.type!=="checkbox" && el.tagName!=="SELECT" && this.editPanelValue(el))return;
      if (el.dataset.test) {
        this[el.dataset.test] = el.value;
        if(["inputs","captures"].includes(el.dataset.test))this.syncSamples(el);
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
      if((el.type==='checkbox'||el.tagName==='SELECT') && this.editPanelValue(el))return;
      if(el.dataset.sampleType){
        try {
          const scope=el.dataset.sampleScope,key=el.dataset.sampleType,values=parseObject(this[scope]),old=values[key];
          (scope==='captures'?this.captureTypes:this.inputTypes)[key]=el.value;
          let next;
          if(old!==undefined){
            if(el.value==='text')next=typeof old==='object'?JSON.stringify(old):String(old);
            if(el.value==='boolean' && [true,false,'true','false'].includes(old))next=old===true||old==='true';
            if(el.value==='number' && String(old).trim() && Number.isFinite(Number(old)))next=Number(old);
            if(el.value==='json'){try{next=typeof old==='string'?JSON.parse(old):old;}catch(_){}}
          }
          if(next===undefined)delete values[key];else Object.defineProperty(values,key,{value:next,writable:true,enumerable:true,configurable:true});
          this[scope]=JSON.stringify(values,null,2);this.render();
        }catch(e){this.message(e.message,true);}return;
      }
      if(el.dataset.test){
        this[el.dataset.test]=el.value;
        if(['inputs','captures'].includes(el.dataset.test))this.syncSamples(el);
        return;
      }
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
      this.traceMode = this.options.injected ? "real" : "simulation";
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
        const invalid=this.root.querySelector(':invalid');
        if(invalid && !['undo','redo'].includes(cmd)){invalid.reportValidity();return;}
        if(cmd==='expand-all' || cmd==='collapse-all') {
          if(cmd==='expand-all')walk(this.workflow,(n,p)=>this.expanded.add(p));
          else {this.expanded.clear();this.selected=null;}
          this.render();return;
        }
        if(cmd==='toggle-stage') {
          const path=b.dataset.path;
          if(this.expanded.has(path)){this.expanded.delete(path);if(this.selected?.startsWith(path+'.'))this.selected=null;}
          else this.expanded.add(path);
          this.render();this.root.querySelector(`[data-cmd="toggle-stage"][data-path="${path}"]`)?.focus({preventScroll:true});return;
        }
        if(cmd.startsWith('case-')) {
          const node=this.node(this.selected),cases=node.value.cases,i=Number(b.dataset.index);
          if(cmd==='case-add' && cases.length>=32)return;
          if(cmd==='case-remove' && (cases.length<=1 || ((cases[i].steps || []).length && !confirm('删除这个情况及其内部步骤？'))))return;
          if(cmd==='case-up'&&i<=0 || cmd==='case-down'&&i>=cases.length-1)return;
          this.checkpoint();
          if(cmd==='case-add')cases.push({label:'情况 '+(cases.length+1),condition:{left:'{current}',op:'eq',right:''},steps:[]});
          if(cmd==='case-remove')cases.splice(i,1);
          if(cmd==='case-up'||cmd==='case-down'){const j=i+(cmd==='case-up'?-1:1);[cases[i],cases[j]]=[cases[j],cases[i]];}
          this.expanded.add(this.selected);this.emit();this.render();return;
        }
        if(cmd==='sample-defaults') {
          const values=parseObject(this.captures);
          this.definitions().states.forEach(d=>{if(!Object.hasOwn(values,d.key)&&Object.hasOwn(d.node.value,'default'))Object.defineProperty(values,d.key,{value:clone(d.node.value.default),enumerable:true,writable:true,configurable:true});});
          this.captures=JSON.stringify(values,null,2);this.render();return;
        }
        if(cmd==='sample-add') {
          const key=this.root.querySelector('[data-new-input]').value.trim(),values=parseObject(this.inputs);
          if(!/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(key)||key.startsWith('__'))throw Error('参数名请使用字母、数字或下划线，不能以数字或双下划线开头');
          if(Object.keys(values).length>=128)throw Error('最多 128 个请求参数');
          if(!Object.hasOwn(values,key))Object.defineProperty(values,key,{value:'',writable:true,enumerable:true,configurable:true});
          this.inputs=JSON.stringify(values,null,2);this.render();return;
        }
        if(cmd==='open-real-test'){this.options.onOpenRealTest?.();return;}
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
          const list = this.seq(this.palette, true);
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
            ["IF", "SWITCH", "GROUP", "TRY"].includes(loc.list[loc.index].action) &&
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
          this.checkpoint();
          const target = this.seq(destination, true),
            loc = this.location(this.selected),
            node = loc.list[loc.index];
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
          this.trace = [];
          this.traceMode = "simulation";
          const signature = JSON.stringify([this.workflow,this.inputs,this.captures,this.model,this.prompt]);
          this.render();
          try {
            const result = await this.api("preview", payload);
            if (signature !== JSON.stringify([this.workflow,this.inputs,this.captures,this.model,this.prompt]))
              throw Error("流程或样例已修改，请重新模拟。");
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
    branchEntries,
    hintData,
  };
  window.WorkflowStudioComponent = {
    name: "WorkflowStudio",
    props: { workflow: Array, selectors: Object },
    emits: ["change", "selectors-change", "open-real-test"],
    mounted() {
      this.studio = window.WorkflowStudio.mount(this.$refs.host, {
        workflow: this.workflow,
        selectors: this.selectors,
        onChange: (w) => this.$emit("change", w),
        onSelectorsChange: (s) => this.$emit("selectors-change", s),
        onOpenRealTest: () => this.$emit("open-real-test"),
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
