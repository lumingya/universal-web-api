// R2-8：前端单元测试（node --test），不需要浏览器。由 tests/test_js_unit.py 在 pytest 中调用。
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const JS = path.join(ROOT, 'static', 'js');

function loadIndexScripts(names) {
  const store = new Map();
  const warnings = [];
  const ctx = {
    console: { ...console, warn: (...args) => warnings.push(args.join(' ')) },
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  for (const name of names) vm.runInContext(fs.readFileSync(path.join(JS, name), 'utf8'), ctx, { filename: name });
  return { window: ctx, warnings, store };
}

function dashboardScriptOrder() {
  // 以 index.html 的实际加载顺序为准：组装器之前的 dashboard 相关脚本
  const html = fs.readFileSync(path.join(ROOT, 'static', 'index.html'), 'utf8');
  const srcs = [...html.matchAll(/<script (?:type="module" )?src="\/static\/js\/([^"?]+)\?[^"]*"><\/script>/g)].map((m) => m[1]);
  const end = srcs.indexOf('dashboard-methods.js');
  assert.ok(end > 0, 'index.html 应加载 dashboard-methods.js');
  return ['dashboard-schema.js', ...srcs.slice(0, end + 1).filter((s) => s.startsWith('dashboard/')), 'dashboard-methods.js'];
}

test('index.html 的加载顺序能组装出全部方法且没有重名', () => {
  const { window, warnings } = loadIndexScripts(dashboardScriptOrder());
  const methods = window.DashboardMethods;
  assert.ok(Object.keys(methods).length >= 150);
  assert.deepEqual(warnings, []);
  for (const [name, value] of Object.entries(methods)) assert.equal(typeof value, 'function', name);
  for (const name of ['siteDisplayName', 'siteStudioInitial']) assert.ok(name in methods, name);
});

test('共享辅助函数：导入大小限制、站点显示名、令牌存取', () => {
  const { window, store } = loadIndexScripts(['dashboard-schema.js', 'dashboard/shared.js']);
  const shared = window.DashboardShared;
  assert.equal(shared.importFileSizeError({ size: 10 }, 100, '配置文件'), '');
  assert.match(shared.importFileSizeError({ size: 9 * 1024 * 1024 }, 8 * 1024 * 1024, '配置文件'), /配置文件过大/);
  assert.equal(shared.formatGitCompareErrorText(new Error('  boom ')), 'boom');
  assert.equal(shared.formatGitCompareErrorText(''), '读取官方配置失败');
  assert.equal(typeof shared.siteDisplayName('chat.deepseek.com'), 'string');
  assert.ok(shared.siteDisplayName('chat.deepseek.com').length > 0);
  shared.setStoredDashboardToken('secret-token');
  assert.equal(shared.getStoredDashboardToken(), 'secret-token');
  assert.ok([...store.values()].includes('secret-token'));
});

test('维护面板：状态文案与样式映射', () => {
  const ctx = { console };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(JS, 'components', 'panels', 'AdapterPanel.js'), 'utf8'), ctx);
  const methods = ctx.window.AdapterPanel.methods;
  assert.equal(methods.statusLabel('update_available'), '可安全更新');
  assert.equal(methods.statusLabel('unknown_status'), 'unknown_status');
  assert.equal(methods.statusClass('healthy'), 'adapter-badge is-good');
  assert.equal(methods.statusClass('conflict'), 'adapter-badge is-warn');
  assert.equal(methods.statusClass('broken'), 'adapter-badge is-bad');
});

test('除 Vue 全局构建外，index.html 的脚本都以 ES 模块加载', () => {
  const html = fs.readFileSync(path.join(ROOT, 'static', 'index.html'), 'utf8');
  const classic = [...html.matchAll(/<script src="([^"]+)"><\/script>/g)].map((m) => m[1]);
  assert.deepEqual(classic.map((s) => s.split('?')[0]), ['/static/vendor/vue.global.prod.js']);
});

