import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHODS_FILE = ROOT / "static/js/components/commands/CommandsTabMethods.js"


def _run_node(body: str) -> None:
    script = f"""
const fs = require('fs');
const vm = require('vm');
const context = {{ window: {{}} }};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const methods = context.window.CommandsTabMethods;
const assert = (condition, message) => {{ if (!condition) throw new Error(message); }};
{body}
"""
    subprocess.run(
        ["node", "-e", script, str(METHODS_FILE)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_late_result_response_cannot_overwrite_new_command() -> None:
    _run_node(
        """
(async () => {
    const pending = {};
    const state = {
        editingCommand: { id: 'command-a' }, isNew: false, showEditor: true,
        advancedUiResultsEnabled: true, commandResults: [], commandResultsLoading: false,
        commandResultsRequestSeq: 0, $emit() {},
        apiRequest(url) { return new Promise(resolve => { pending[url] = resolve; }); }
    };
    const oldRequest = methods.loadCommandResults.call(state, true);
    state.editingCommand = { id: 'command-b' };
    const newRequest = methods.loadCommandResults.call(state, true);
    pending['/api/commands/command-b/results']({ records: [{ value: 'new' }] });
    await newRequest;
    pending['/api/commands/command-a/results']({ records: [{ value: 'old' }] });
    await oldRequest;
    assert(state.commandResults.length === 1 && state.commandResults[0].value === 'new',
        'late response from the previous command replaced current results');
})().catch(error => { console.error(error); process.exit(1); });
"""
    )


def test_silent_poll_does_not_steal_loading_state() -> None:
    _run_node(
        """
(async () => {
    let resolveRequest;
    let requestCount = 0;
    const state = {
        editingCommand: { id: 'command-a' }, isNew: false, showEditor: true,
        advancedUiResultsEnabled: true, commandResults: [], commandResultsLoading: false,
        commandResultsRequestSeq: 0, $emit() {},
        apiRequest() {
            requestCount += 1;
            return new Promise(resolve => { resolveRequest = resolve; });
        }
    };
    const visibleRequest = methods.loadCommandResults.call(state, false);
    await methods.loadCommandResults.call(state, true);
    assert(requestCount === 1, 'poll overlapped a visible result request');
    resolveRequest({ records: [] });
    await visibleRequest;
    assert(state.commandResultsLoading === false, 'loading indicator remained stuck');
})().catch(error => { console.error(error); process.exit(1); });
"""
    )


def test_clear_invalidates_an_older_poll_response() -> None:
    _run_node(
        """
(async () => {
    let resolvePoll;
    const state = {
        editingCommand: { id: 'command-a' }, isNew: false, showEditor: true,
        advancedUiResultsEnabled: true, commandResults: [{ value: 'old' }],
        commandResultsLoading: false, commandResultsRequestSeq: 0, $emit() {},
        apiRequest(url, options) {
            if (options && options.method === 'DELETE') return Promise.resolve({});
            return new Promise(resolve => { resolvePoll = resolve; });
        }
    };
    context.window.confirm = () => true;
    const poll = methods.loadCommandResults.call(state, true);
    await methods.clearCommandResults.call(state);
    resolvePoll({ records: [{ value: 'resurrected' }] });
    await poll;
    assert(state.commandResults.length === 0, 'late poll resurrected cleared results');
})().catch(error => { console.error(error); process.exit(1); });
"""
    )
