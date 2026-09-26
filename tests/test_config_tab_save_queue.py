import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_TAB_FILE = ROOT / "static/js/components/ConfigTab.js"
from tests._dashboard_js import dashboard_methods_bundle  # noqa: E402

DASHBOARD_METHODS_FILE = dashboard_methods_bundle()  # R2-8：拆分后的完整源码


def test_advanced_config_saves_reach_backend_in_user_action_order() -> None:
    script = """
const fs = require('fs');
const vm = require('vm');
const context = { window: {} };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const persist = context.window.ConfigTab.methods.persistSiteAdvancedConfig;
const assert = (condition, message) => { if (!condition) throw new Error(message); };

(async () => {
    const requests = [];
    const pending = [];
    const state = {
        currentDomain: 'example.com', selectedPreset: 'main', currentConfig: { advanced: {} },
        presetConfig: { advanced: {} }, advancedConfigSaveSeq: 2, advancedConfigSaveQueue: null,
        buildAuthHeaders(value) { return value; },
        fetchJson(url, options) {
            requests.push(JSON.parse(options.body).value);
            return new Promise(resolve => pending.push(resolve));
        },
        filterSiteAdvancedFields(value) { return value; },
        filterPresetAdvancedFields(value) { return value; },
        assignCurrentConfigAdvanced(value) { this.currentConfig.advanced = value; },
        assignPresetAdvanced(value) { this.presetConfig.advanced = value; },
        $emit() {}
    };

    const first = persist.call(state, { value: 'first' }, {}, { saveSeq: 1 });
    const second = persist.call(state, { value: 'second' }, {}, { saveSeq: 2 });
    await new Promise(resolve => setImmediate(resolve));
    assert(requests.join(',') === 'first', 'second save started before first completed');
    pending.shift()({ advanced: { value: 'first' } });
    await new Promise(resolve => setImmediate(resolve));
    assert(requests.join(',') === 'first,second', 'queued save did not preserve action order');
    pending.shift()({ advanced: { value: 'second' } });
    await Promise.all([first, second]);
    assert(state.currentConfig.advanced.value === 'second', 'latest value was not retained');
})().catch(error => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", script, str(CONFIG_TAB_FILE)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_stream_config_saves_are_serialized_and_stale_failure_does_not_reload() -> None:
    script = """
const fs = require('fs');
const vm = require('vm');
const context = { window: {} };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const save = context.window.ConfigTab.methods.saveStreamConfig;
const assert = (condition, message) => { if (!condition) throw new Error(message); };

(async () => {
    const requests = [];
    const pending = [];
    const presetConfig = { stream_config: {} };
    const state = {
        currentDomain: 'example.com', selectedPreset: 'main', presetConfig,
        streamConfigSaveSeq: 0, streamConfigSaveQueue: null,
        cloneConfigSection(value) { return JSON.parse(JSON.stringify(value)); },
        buildAuthHeaders(value) { return value; },
        fetchJson(url, options) {
            requests.push(JSON.parse(options.body).value);
            return new Promise((resolve, reject) => pending.push({ resolve, reject }));
        },
        $emit() { throw new Error('stale failure triggered reload'); }
    };

    const first = save.call(state, { value: 'first' });
    const second = save.call(state, { value: 'second' });
    await new Promise(resolve => setImmediate(resolve));
    assert(requests.join(',') === 'first', 'stream saves were not serialized');
    pending.shift().reject(new Error('first failed'));
    await new Promise(resolve => setImmediate(resolve));
    assert(requests.join(',') === 'first,second', 'second stream save did not start');
    pending.shift().resolve({ ok: true });
    await Promise.all([first, second]);
    assert(presetConfig.stream_config.value === 'second', 'latest stream config was lost');
})().catch(error => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", script, str(CONFIG_TAB_FILE)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_image_config_saves_are_serialized_and_stale_failure_does_not_rollback() -> None:
    script = """
const fs = require('fs');
const vm = require('vm');
const context = {
    window: { DEFAULT_SELECTOR_DEFINITIONS: [], BROWSER_CONSTANTS_SCHEMA: {}, ENV_CONFIG_SCHEMA: {} },
    localStorage: { getItem() { return ''; }, setItem() {}, removeItem() {} },
    setTimeout, clearTimeout
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const save = context.window.DashboardMethods.updateImageConfig;
const assert = (condition, message) => { if (!condition) throw new Error(message); };

(async () => {
    const requests = [];
    const pending = [];
    const notices = [];
    const presetConfig = { image_extraction: {} };
    const state = {
        currentDomain: 'example.com', currentConfig: {},
        imageConfigSaveSeq: 0, imageConfigSaveQueue: null,
        getActivePresetConfig() { return presetConfig; },
        getActivePresetName() { return 'main'; },
        apiRequest(url, options) {
            requests.push(JSON.parse(options.body).value);
            return new Promise((resolve, reject) => pending.push({ resolve, reject }));
        },
        notify(message, type) { notices.push({ message, type }); },
        reloadConfig() { throw new Error('stale failure triggered reload'); }
    };

    const first = save.call(state, { value: 'first' });
    const second = save.call(state, { value: 'second' });
    await new Promise(resolve => setImmediate(resolve));
    assert(requests.join(',') === 'first', 'image saves were not serialized');
    pending.shift().reject(new Error('first failed'));
    await new Promise(resolve => setImmediate(resolve));
    assert(requests.join(',') === 'first,second', 'second image save did not start');
    pending.shift().resolve({ ok: true });
    await Promise.all([first, second]);
    assert(presetConfig.image_extraction.value === 'second', 'latest image config was rolled back');
    assert(notices.filter(item => item.type === 'error').length === 0, 'stale failure was surfaced');
})().catch(error => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", script, str(DASHBOARD_METHODS_FILE)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
