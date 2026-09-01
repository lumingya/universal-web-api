import json
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMANDS_PATH = PROJECT_ROOT / "config" / "commands.json"


NODE_DIRECT_PROBE_HARNESS = r"""
const fs = require('fs');

const commandsPath = process.argv[1];
const commandId = process.argv[2];
const scenario = process.argv[3];
const payload = JSON.parse(fs.readFileSync(commandsPath, 'utf8'));
const command = payload.commands.find((item) => item.id === commandId);
if (!command) throw new Error(`missing command: ${commandId}`);
const probe = command.trigger.probe_js;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

class ElementNode {
  constructor(tagName, { text = '', className = '', role = '', rect = {}, src = '' } = {}) {
    this.tagName = String(tagName || 'div').toUpperCase();
    this.text = text;
    this.className = className;
    this.children = [];
    this.parentElement = null;
    this.role = role;
    this.src = src;
    this.currentSrc = src;
    this.naturalWidth = src ? 1024 : 0;
    this.naturalHeight = src ? 1024 : 0;
    this.style = { display: 'block', visibility: 'visible', opacity: '1' };
    this.rect = Object.assign({ left: 100, top: 0, width: 800, height: src ? 320 : 88 }, rect);
  }
  get isConnected() { return true; }
  get offsetWidth() { return this.rect.width; }
  get offsetHeight() { return this.rect.height; }
  get classList() {
    const parts = new Set(String(this.className || '').split(/\s+/).filter(Boolean));
    return { contains: (value) => parts.has(value) };
  }
  append(...nodes) {
    for (const node of nodes) {
      node.parentElement = this;
      this.children.push(node);
    }
    return this;
  }
  contains(target) {
    return target === this || this.children.some((child) => child.contains(target));
  }
  get innerText() {
    return [this.text, ...this.children.map((child) => child.innerText)].filter(Boolean).join(' ');
  }
  get textContent() { return this.innerText; }
  getBoundingClientRect() {
    return Object.assign({}, this.rect, {
      x: this.rect.left,
      y: this.rect.top,
      right: this.rect.left + this.rect.width,
      bottom: this.rect.top + this.rect.height,
    });
  }
  getAttribute(name) {
    if (name === 'role') return this.role || null;
    if (name === 'aria-haspopup' && this.role === 'combobox') return 'listbox';
    return null;
  }
  hasAttribute(name) { return this.getAttribute(name) != null; }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const all = descendants(this);
    if (selector === 'img') return all.filter((node) => node.tagName === 'IMG');
    if (selector === '*') return all;
    if (selector.includes('data-role="user"')) return all.filter((node) => node.isUserData);
    return [];
  }
  closest() { return null; }
}

function descendants(root) {
  return (root.children || []).flatMap((child) => [child, ...descendants(child)]);
}

function imageNode(src) {
  return new ElementNode('img', { src, rect: { left: 120, top: 80, width: 320, height: 320 } });
}

function turnNode({ text = '', user = false, image = '', top = 0 } = {}) {
  const node = new ElementNode('div', {
    text,
    className: user ? 'mx-auto max-w-[800px] flex w-full justify-end' : 'mx-auto max-w-[800px] w-full',
    rect: { left: 100, top, width: 800, height: image ? 420 : 88 },
  });
  if (user) node.isUserData = true;
  if (image) node.append(imageNode(image));
  return node;
}

function makeEnvironment({ pathname = '/c/direct-conversation', mode = 'Direct', latestText = '', latestImage = '', history = [] } = {}) {
  const body = new ElementNode('body', { rect: { left: 0, top: 0, width: 1280, height: 720 } });
  const modeButton = new ElementNode('button', { text: mode, role: 'combobox', rect: { left: 250, top: 12, width: 100, height: 30 } });
  const main = new ElementNode('main', { rect: { left: 240, top: 0, width: 1040, height: 650 } });
  const ol = new ElementNode('ol', {
    className: 'mt-8 flex w-full max-w-screen-xl grow flex-col-reverse justify-end gap-4 overscroll-none pb-2 duration-500',
    rect: { left: 256, top: -1200, width: 1008, height: 1800 },
  });
  const spacer = new ElementNode('div', { className: 'h-0', rect: { left: 256, top: 540, width: 1008, height: 0 } });
  ol.append(spacer, turnNode({ text: latestText, image: latestImage, top: 100 }), turnNode({ text: 'user: latest prompt', user: true }));
  history.forEach((item, index) => ol.append(turnNode({ text: item.text, image: item.image || '', top: item.top == null ? -200 - index * 100 : item.top })));
  main.append(modeButton, ol);
  body.append(main);

  const systemNodes = [];
  const document = {
    body,
    documentElement: body,
    scrollingElement: body,
    querySelector(selector) {
      if (selector === 'main ol.flex-col-reverse' || selector === 'ol.flex-col-reverse' || selector === 'main ol' || selector === 'ol') return ol;
      return this.querySelectorAll(selector)[0] || null;
    },
    querySelectorAll(selector) {
      if (selector.includes('button') || selector.includes('[role="combobox"]') || selector.includes('[aria-haspopup="listbox"]')) return [modeButton];
      if (selector.includes('[role="alert"]') || selector.includes('[data-sonner-toast]') || selector.includes('.toast')) return systemNodes;
      return [];
    },
  };
  const window = {
    __codexWorkflowContext: { runtime_id: 'runtime-1', parser_id: '', target_side: '' },
    getComputedStyle: (node) => node.style,
  };
  const location = { href: `https://arena.ai${pathname}`, pathname };
  return { body, document, location, ol, systemNodes, window };
}

function runProbe(env) {
  const fn = new Function('window', 'document', 'location', 'NodeFilter', 'Element', 'getComputedStyle', probe);
  return fn(env.window, env.document, env.location, { SHOW_TEXT: 4 }, ElementNode, env.window.getComputedStyle);
}

function replaceLatest(env, text, image = '') {
  env.ol.children[1] = turnNode({ text, image, top: 100 });
  env.ol.children[1].parentElement = env.ol;
}

if (scenario === 'direct-history-layout') {
  const env = makeEnvironment({
    pathname: '/c/01a05284-9f15-7533-a98d-6d362154c64e',
    latestText: 'luna-lisa-alpha Edit',
    latestImage: 'https://messages.example.test/session/latest-success.png?signature=1',
    history: [{ text: 'Max Something went wrong with this response, please try again.', top: 210 }],
  });
  const first = runProbe(env);
  assert(first.hit === false && first.mode === 'direct', `expected direct baseline false, got ${JSON.stringify(first)}`);
  env.ol.children[3].rect.top = 750;
  const second = runProbe(env);
  assert(second.hit === false && second.mode === 'direct', `layout shift should not hit, got ${JSON.stringify(second)}`);
} else if (scenario === 'direct-new-latest-500') {
  const env = makeEnvironment({ latestText: 'luna-lisa-alpha Edit', latestImage: 'https://messages.example.test/session/latest-success.png' });
  runProbe(env);
  replaceLatest(env, 'Max Something went wrong with this response, please try again.');
  const second = runProbe(env);
  assert(second.hit === true && second.mode === 'direct' && second.summary.includes('Arena 500 error'), `expected fresh 500 hit, got ${JSON.stringify(second)}`);
} else if (scenario === 'direct-422-history-and-latest') {
  const env = makeEnvironment({
    pathname: '/c/01a05284-9f15-7533-a98d-6d362154c64e',
    latestText: 'mosa-f Edit',
    latestImage: 'https://messages.example.test/session/latest-success.png',
    history: [{ text: 'Max This content violates our Terms of Use.', top: 210 }],
  });
  const first = runProbe(env);
  assert(first.hit === false && first.mode === 'direct', `history 422 should baseline false, got ${JSON.stringify(first)}`);
  env.ol.children[3].rect.top = 750;
  const shifted = runProbe(env);
  assert(shifted.hit === false && shifted.mode === 'direct', `history 422 layout shift should not hit, got ${JSON.stringify(shifted)}`);
  replaceLatest(env, 'Max This content violates our Terms of Use.');
  const second = runProbe(env);
  assert(second.hit === true && second.mode === 'direct' && second.summary.includes('prompt rejection'), `expected fresh 422 hit, got ${JSON.stringify(second)}`);
} else if (scenario === 'direct-system-card') {
  const env = makeEnvironment({ latestText: 'luna-lisa-alpha Edit', latestImage: 'https://messages.example.test/session/latest-success.png' });
  runProbe(env);
  env.systemNodes.push(new ElementNode('div', { text: 'Response failed', role: 'alert', rect: { left: 900, top: 50, width: 300, height: 80 } }));
  const second = runProbe(env);
  assert(second.hit === true && second.mode === 'direct', `expected fresh system card hit, got ${JSON.stringify(second)}`);
} else {
  throw new Error(`unknown scenario: ${scenario}`);
}
"""


def run_direct_probe_scenario(command_id: str, scenario: str) -> None:
    result = subprocess.run(
        ["node", "-e", NODE_DIRECT_PROBE_HARNESS, str(COMMANDS_PATH), command_id, scenario],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


class ArenaCommandPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = json.loads((PROJECT_ROOT / "config" / "commands.json").read_text(encoding="utf-8"))
        cls.commands = {item.get("id"): item for item in payload.get("commands", [])}

    def test_main_command_persists_each_completed_round(self):
        script = self.commands["cmd_arena_auto_battle"]["script"]
        self.assertIn("record_arena_rule_candidates(\n                reply_info,", script)
        self.assertIn("source=f\"page-final-visible-text/", script)

    def test_arena_commands_persist_current_page_before_manual_stop(self):
        script = self.commands["cmd_arena_auto_battle"]["script"]
        self.assertIn("source='manual-stop-final-snapshot'", script)
        self.assertLess(
            script.index("source='manual-stop-final-snapshot'"),
            script.index("returning collected URLs"),
        )

    def test_context_model_lab_is_disabled_by_default(self):
        self.assertFalse(self.commands["cmd_arena_context_model_lab"]["enabled"])

    def test_deepseek_refresh_does_not_observe_arena_pages(self):
        trigger = self.commands["cmd_72ae0f7d"]["trigger"]
        self.assertEqual(trigger["scope"], "domain")
        self.assertEqual(trigger["domain"], "chat.deepseek.com")

    def test_arena_runtime_cpu_guard_version_matches_command_probe(self):
        command = self.commands["cmd_arena_stop_fix_runtime"]
        source = (PROJECT_ROOT / "js" / "arena-stream-hard-stop.user.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("window.__arenaHardStop.version !== '2.12.1'", command["trigger"]["probe_js"])
        self.assertIn("const VERSION = '2.12.1';", source)
        self.assertIn("const REPAIR_INTERVAL_MS = 1000;", source)
        self.assertEqual(command["trigger"]["periodic_interval_sec"], 30)
        self.assertEqual(command["trigger"]["interrupt_policy"], "resume")
        self.assertEqual(command["trigger"]["fire_mode"], "level")
        self.assertTrue(command["log_enabled"])
        self.assertEqual(command["log_level"], "INFO")
        self.assertTrue(command["actions"][0]["bootstrap_on_session_ready"])
        self.assertFalse(command["trigger"].get("abort_on_match", False))

    def test_arena_clear_error_command_clicks_clear_then_aborts_workflow(self):
        command = self.commands["cmd_19f7ae6f"]
        trigger = command["trigger"]

        self.assertTrue(command["enabled"])
        self.assertEqual(trigger["value"], "Clear")
        self.assertEqual(trigger["scope"], "domain")
        self.assertEqual(trigger["domain"], "arena.ai")
        self.assertFalse(trigger["check_while_busy_workflow"])
        self.assertFalse(trigger["allow_during_workflow"])
        self.assertEqual(trigger["interrupt_policy"], "abort")
        self.assertIn("text === 'clear'", trigger["probe_js"])
        self.assertEqual(
            [action["type"] for action in command["actions"]],
            ["run_js", "abort_task"],
        )
        self.assertIn("target.click()", command["actions"][0]["code"])
        self.assertEqual(
            command["actions"][1]["reason"],
            "arena_clear_error_detected",
        )

    def test_arena_clear_error_command_is_enabled_in_local_overrides(self):
        payload = json.loads(
            (PROJECT_ROOT / "config" / "commands.local.json").read_text(encoding="utf-8")
        )
        local_commands = {item.get("id"): item for item in payload.get("commands", [])}

        self.assertTrue(local_commands["cmd_19f7ae6f"]["enabled"])

    def test_arena_prompt_rejection_command_aborts_workflow_with_http_422_message(self):
        command = self.commands["cmd_arena_prompt_rejected_422"]
        trigger = command["trigger"]

        self.assertTrue(command["enabled"])
        self.assertEqual(trigger["type"], "page_check")
        self.assertEqual(trigger["scope"], "domain")
        self.assertEqual(trigger["domain"], "arena.ai")
        self.assertTrue(trigger["check_while_busy_workflow"])
        self.assertTrue(trigger["allow_during_workflow"])
        self.assertEqual(trigger["interrupt_policy"], "abort")
        self.assertIn("this content violates our terms of use", trigger["probe_js"].lower())
        self.assertIn("HTTP 422", trigger["interrupt_message"])
        self.assertEqual(
            [action["type"] for action in command["actions"]],
            ["abort_task"],
        )
        self.assertEqual(
            command["actions"][0]["reason"],
            "arena_prompt_rejected",
        )

    def test_arena_error_probes_have_direct_only_stable_state_wrappers(self):
        response_probe = self.commands["cmd_arena_response_error_500"]["trigger"]["probe_js"]
        rejection_probe = self.commands["cmd_arena_prompt_rejected_422"]["trigger"]["probe_js"]

        self.assertIn("const runLegacy = () =>", response_probe)
        self.assertIn("if (!isDirectMode) return runLegacy();", response_probe)
        self.assertIn("if (!isDirectMode) return runLegacy();", rejection_probe)
        self.assertIn("directModeText === 'direct'", response_probe)
        self.assertIn("directModeText === 'direct'", rejection_probe)

        self.assertIn("__codexArenaDirectProbe_500", response_probe)
        self.assertIn("__codexArenaDirectProbe_422", rejection_probe)
        self.assertNotIn("__codexArenaDirectProbe_422", response_probe)
        self.assertNotIn("__codexArenaDirectProbe_500", rejection_probe)

        self.assertIn("contentTop", response_probe)
        self.assertIn("failedSides", response_probe)
        self.assertIn("this content violates our terms of use", rejection_probe.lower())
        self.assertIn("knownSignatures", response_probe)
        self.assertIn("knownSignatures", rejection_probe)

    def test_direct_500_probe_ignores_historical_error_after_layout_shift(self):
        run_direct_probe_scenario("cmd_arena_response_error_500", "direct-history-layout")

    def test_direct_500_probe_hits_new_latest_assistant_error(self):
        run_direct_probe_scenario("cmd_arena_response_error_500", "direct-new-latest-500")

    def test_direct_422_probe_ignores_history_but_hits_latest_assistant_rejection(self):
        run_direct_probe_scenario("cmd_arena_prompt_rejected_422", "direct-422-history-and-latest")

    def test_direct_500_probe_hits_new_system_error_card(self):
        run_direct_probe_scenario("cmd_arena_response_error_500", "direct-system-card")

    def test_arena_response_error_command_is_enabled_in_local_overrides(self):
        payload = json.loads(
            (PROJECT_ROOT / "config" / "commands.local.json").read_text(encoding="utf-8")
        )
        local_commands = {item.get("id"): item for item in payload.get("commands", [])}

        self.assertTrue(local_commands["cmd_arena_response_error_500"]["enabled"])
        self.assertTrue(local_commands["cmd_arena_prompt_rejected_422"]["enabled"])

    def test_standalone_arena_ip_rotation_command_is_enabled_in_local_overrides(self):
        command = self.commands["cmd_arena_rotate_ip_standalone"]
        self.assertEqual(command["name"], "ARENA手动切换IP（复用自动盲测IP池）")
        self.assertIn(
            "app.services.arena_proxy_rotation import rotate_arena_proxy",
            command["script"],
        )

        payload = json.loads(
            (PROJECT_ROOT / "config" / "commands.local.json").read_text(encoding="utf-8")
        )
        local_commands = {item.get("id"): item for item in payload.get("commands", [])}

        self.assertTrue(local_commands["cmd_arena_rotate_ip_standalone"]["enabled"])


if __name__ == "__main__":
    unittest.main()
