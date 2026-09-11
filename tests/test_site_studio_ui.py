"""Site Studio regressions. Pure component tests; never writes user configuration."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_selector_filters_preserve_editing_and_configuration():
    script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const ctx = {window: {}};
vm.runInNewContext(fs.readFileSync('static/js/components/panels/SelectorPanel.js', 'utf8'), ctx);
const c = ctx.window.SelectorPanel;
const state = {...c.data(), selectors: {input_box: 'textarea', send_btn: '', result_container: '.reply', custom: ''}};
for (const [k, fn] of Object.entries(c.methods)) state[k] = fn.bind(state);
for (const [k, fn] of Object.entries(c.computed)) Object.defineProperty(state, k, {get: () => fn.call(state)});
const original = JSON.stringify(state.selectors);
assert.equal(state.count, 4);
assert.equal(state.selectorEntries.length, 4);
state.fieldFilter = 'core'; assert.equal(state.selectorEntries.length, 3);
state.fieldFilter = 'empty'; assert.equal(state.selectorEntries.length, 2);
state.fieldQuery = '发送'; assert.equal(state.selectorEntries[0][0], 'send_btn');
state.fieldFilter = 'all'; state.fieldQuery = '  INPUT_BOX  ';
assert.equal(state.selectorEntries[0][0], 'input_box');
state.fieldQuery = '.reply'; assert.equal(state.selectorEntries[0][0], 'result_container');
state.fieldQuery = 'no-such-field'; assert.equal(state.selectorEntries.length, 0);
assert.equal(JSON.stringify(state.selectors), original, 'filtering modified config');
state.fieldQuery = ''; state.fieldFilter = 'empty'; state.editingSelectorKey = 'send_btn';
state.selectors.send_btn = 'b';
assert(state.selectorEntries.some(([k]) => k === 'send_btn'), 'field disappears after first keystroke');
state.selectors.send_btn = 'button[type=submit]'; state.editingSelectorKey = '';
assert(!state.selectorEntries.some(([k]) => k === 'send_btn'));
state.fieldFilter = 'all'; state.fieldQuery = '.reply'; state.editingSelectorKey = 'result_container';
state.selectors.result_container = '.new-reply';
assert(state.selectorEntries.some(([k]) => k === 'result_container'), 'editing value search removed active field');
c.watch.selectors.call(state);
assert.equal(state.fieldFilter, 'all'); assert.equal(state.fieldQuery, '');
"""
    subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)


def test_studio_search_and_save_feedback_are_truthful():
    script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const ctx = {window: {}};
vm.runInNewContext(fs.readFileSync('static/js/dashboard-methods.js', 'utf8'), ctx);
vm.runInNewContext(fs.readFileSync('static/js/dashboard-state.js', 'utf8'), ctx);
const methods = ctx.window.DashboardMethods;
const state = {sites: {'www.doubao.com': {}, 'chat.deepseek.com': {}}, searchQuery: ' 豆包 ', siteDisplayName: methods.siteDisplayName};
const filtered = () => ctx.window.DashboardState.computed.filteredSites.call(state);
assert.equal(filtered().length, 1); assert.equal(filtered()[0], 'www.doubao.com');
state.searchQuery = 'DEEPSEEK'; assert.equal(filtered()[0], 'chat.deepseek.com');
state.searchQuery = 'no match'; assert.equal(filtered().length, 0);
state.searchQuery = ' '; assert.equal(filtered().length, 2);
assert.equal(methods.siteStudioInitial.call(state, 'chat.qwen.ai'), 'Q');
const summary = methods.siteLibrarySummary.call({sites: {'example.com': {default_preset: 'B', presets: {
  A: {selectors: {}}, B: {selectors: {input_box: 'textarea', send_btn: ' ', result_container: '.reply'}}
}}}}, 'example.com');
assert.equal(summary.presets, 2); assert.equal(summary.presetName, 'B'); assert.equal(summary.coreFilled, 2);
assert.equal(summary.selectors, 3);
const legacy = methods.siteLibrarySummary.call({sites: {'old.example': {selectors: {input_box: 'input'}}}}, 'old.example');
assert.equal(legacy.presets, 1); assert.equal(legacy.coreFilled, 1);
(async () => {
  let flushed = false, notice = '';
  const target = {sites: state.sites, isSaving: false, studioLastSavedAt: '',
    validateConfig: () => true, flushConfigTabDrafts: () => {flushed = true},
    apiRequest: async (url, options) => {assert(flushed); assert.equal(url, '/api/config'); assert.equal(options.method, 'POST')},
    notify: (text) => {notice = text}};
  await methods.saveConfig.call(target);
  assert(target.studioLastSavedAt); assert.equal(target.isSaving, false); assert.equal(notice, '配置已保存');
  const saved = target.studioLastSavedAt;
  target.apiRequest = async () => {throw new Error('offline')};
  await methods.saveConfig.call(target);
  assert.equal(target.studioLastSavedAt, saved, 'failure incorrectly updates last successful save');
  assert(notice.includes('保存失败')); assert.equal(target.isSaving, false);
})().catch(e => {console.error(e); process.exit(1)});
"""
    subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)


def test_capability_switch_names_and_native_disclosures():
    """The new controls expose unique readable names; settings start closed."""
    from html.parser import HTMLParser

    class Controls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.switches = []
            self.disclosures = 0

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "input" and values.get("role") == "switch":
                names = [v for k, v in attrs if k == "aria-label"]
                assert len(names) == 1 and names[0], "switches need one unambiguous name"
                self.switches.append(names[0])
            if tag == "details" and "cap-" in values.get("class", ""):
                assert "open" not in values and ":open" not in values
                self.disclosures += 1

    expected = {
        "panels/FilePastePanel.js": {"发送附件", "长文本处理"},
        "panels/ImageConfigPanel.js": {"接收图片", "接收音频", "接收视频"},
        "panels/StreamConfigPanel.js": {"从网络读取回复"},
        "ConfigTab.js": {"隔离登录会话", "等待输入框就绪", "发送失败时自动恢复"},
    }
    for filename, names in expected.items():
        parser = Controls()
        source = (ROOT / "static/js/components" / filename).read_text(encoding="utf-8")
        parser.feed(source.split("template: `", 1)[1])
        assert set(parser.switches) == names
        assert len(parser.switches) == len(names)
        assert parser.disclosures >= 2
