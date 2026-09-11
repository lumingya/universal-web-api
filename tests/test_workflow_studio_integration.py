"""Shared engine / editor adapters. All browser actions are deterministic mocks."""
import copy
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app.core.config import WorkflowError
from app.core.workflow.flow_runtime import FlowProgram, has_control_flow
from app.core.workflow.preview import preview_workflow
from app.api import config_workflow_support as support

ROOT = Path(__file__).resolve().parents[1]


def example():
    return json.loads((ROOT / 'docs/examples/state-aware-workflow.json').read_text())


@pytest.mark.parametrize('current,clicks,branch', [('Pro', 1, 'else'), ('Flash', 3, 'then')])
def test_preview_is_no_action_and_uses_real_conditions(current, clicks, branch):
    payload = example()
    baseline = copy.deepcopy(payload)
    result = preview_workflow({**payload, 'model': 'gemini 3.1 Pro', 'capture_values': {'current': current}})
    assert result['success'] and result['mode'] == 'simulation'
    assert sum(s['action'] == 'CLICK' for s in result['planned_actions']) == clicks
    assert any(t.get('branch') == branch for t in result['trace'])
    assert not any(t['status'] == 'completed' for t in result['trace'])
    assert payload == baseline
    assert 'gemini 3.1 Pro' not in json.dumps(result['trace'])


def test_missing_capture_is_not_simulated_success():
    result = preview_workflow({**example(), 'model': 'm'})
    assert not result['success'] and 'current' in result['message']


def test_plain_leaf_is_opt_in_and_inline_selector_is_supported():
    assert not has_control_flow([{'action': 'WAIT', 'value': 0}])
    assert has_control_flow([{'action': 'WAIT', 'value': 0, 'flow_version': 2}])
    p = FlowProgram([{'action': 'CLICK', 'selector': 'css:[data-mode="{mode}"]'}], inputs={'mode': 'Pro'})
    assert p.prepare_leaf(p.next_leaf(0), {})[1] == 'css:[data-mode="Pro"]'


def test_output_before_try_prevents_late_recovery():
    p = FlowProgram([{'action': 'STREAM_OUTPUT'}, {'action': 'TRY', 'value': {
        'attempts': 2, 'steps': [{'action': 'WAIT', 'value': 0}], 'fallback': []}}])
    i = p.next_leaf(0)
    list(p.events(iter(['data: {"choices":[]}\n\n']), i))
    i = p.next_leaf(i + 1)
    assert p.recover(WorkflowError('oops'), i) is None


def test_requests_preserve_inputs_and_reject_invalid_objects():
    from app.api.chat import ChatRequest, ResponsesRequest, _responses_request_to_chat_request
    from app.api.tab_routes import ChatRequest as TabChatRequest
    for cls, kwargs in [(ChatRequest, {'messages': []}), (TabChatRequest, {'messages': []}), (ResponsesRequest, {'input': 'hi'})]:
        body = cls(**kwargs, workflow_variables={'mode': 'Pro', 'enabled': False})
        assert body.workflow_variables == {'mode': 'Pro', 'enabled': False}
        for bad in ([], {'__class__': 'x'}, {'value': 'x' * 66000}):
            with pytest.raises(ValidationError):
                cls(**kwargs, workflow_variables=bad)
    body = ResponsesRequest(input='hello', workflow_variables={'mode': 'Pro'})
    assert _responses_request_to_chat_request(body, stream=True).workflow_variables == {'mode': 'Pro'}


@pytest.mark.parametrize('current,fail,expected', [('Pro', False, ['FILL_INPUT','CLICK','STREAM_WAIT']), ('Flash', False, ['CLICK','CLICK','FILL_INPUT','CLICK','STREAM_WAIT']), ('Flash', True, ['CLICK'])])
def test_editor_executes_only_selected_leaves_and_keeps_failure_trace(monkeypatch, current, fail, expected):
    calls = []
    instances = []
    class Executor:
        def __init__(self, **kwargs):
            self.cleaned = False
            instances.append(self)
        def workflow_execution_scope(self): return nullcontext()
        def _check_cancelled(self): return False
        def execute_step(self, **kwargs):
            calls.append(kwargs)
            if fail: raise WorkflowError('mock click failed')
            if False: yield ""
        def cleanup_after_workflow(self): self.cleaned = True
    tab = SimpleNamespace(url='https://example.com/chat', tab_id='tab-1', html='')
    session = SimpleNamespace(tab=tab, id='session-1')
    browser = MagicMock()
    browser.tab_pool.acquire_by_raw_tab_id.return_value = session
    monkeypatch.setattr('app.core.workflow.executor.WorkflowExecutor', Executor)
    monkeypatch.setattr(support, 'capture_page_state', lambda *_: current)
    monkeypatch.setattr(support, 'config_engine', MagicMock())
    monkeypatch.setattr(support, '_wake_workflow_editor_test_tab', lambda *_: None)
    result = support._execute_workflow_editor_test_payload(browser, {**example(), 'domain':'example.com', 'tab_id':'tab-1', 'model':'gemini 3.1 Pro'})
    assert result['success'] is (not fail)
    assert [c['action'] for c in calls] == expected
    assert result['trace'] and instances[0].cleaned
    browser.tab_pool.release.assert_called_once()
    if fail: assert any(t['status'] == 'failed' for t in result['trace'])
    else: assert all(c['action'] != '__FLOW_INTERNAL' for c in calls)


def test_invalid_inputs_fail_before_any_browser_acquisition():
    browser = MagicMock()
    with pytest.raises(HTTPException) as e:
        support._execute_workflow_editor_test_payload(browser, {**example(), 'domain':'example.com', 'workflow_variables':[]})
    assert e.value.status_code == 400
    browser.get_browser_handle.assert_not_called()


def test_injector_bundles_shared_editor_and_failure_trace_bridge():
    from app.core.workflow_editor import WorkflowEditorInjector
    script = WorkflowEditorInjector._load_script()
    assert script.index('window.WorkflowStudio =') < script.index('function mountTreeStudio()')
    assert 'window.WORKFLOW_STUDIO_CSS' in script
    tab = MagicMock()
    trace = [{'path':'root.0', 'status':'failed'}]
    support._notify_workflow_editor_action_result(tab, 'id', False, 'failed', trace)
    assert tab.run_js.call_args.args[-1] == trace


def test_request_history_summary_excludes_trace():
    from app.services.request_manager import RequestManager
    record = {'workflow_trace': [{'path':'root.0','status':'completed'}], 'prompt':'hello'}
    assert 'workflow_trace' not in RequestManager._to_history_list_record(record)
    assert record['workflow_trace']


def test_full_preset_save_validates_nested_branches():
    from app.api.config_route_models import _normalize_preset_config_payload
    with pytest.raises(HTTPException):
        _normalize_preset_config_payload({'workflow': [{'action':'IF','value':{'condition':{'left':1,'op':'eq','right':1},'then':[{'action':'NO_SUCH_ACTION'}]}}]})


def test_page_fetch_followup_never_scans_into_a_branch():
    from app.core.workflow.executor import WorkflowExecutor
    executor = WorkflowExecutor(tab=MagicMock())
    flow = FlowProgram([{'action':'PAGE_FETCH'}, {'action':'FILL_INPUT'}, {'action':'IF','value':{'condition':{'left':True,'op':'eq','right':True},'then':[{'action':'CLICK','target':'send_btn'}]}}])
    assert executor._consume_request_transport_followup_steps(flow.steps, 0) == 1


def test_structured_workflow_cannot_be_replayed_by_legacy_outer_retry():
    from app.core.browser.workflow import BrowserWorkflowMixin
    session = SimpleNamespace(id='session', _workflow_structured_active=True)
    calls = []
    class Browser(BrowserWorkflowMixin):
        _should_stop_checker = staticmethod(lambda: False)
        _is_stream_terminal_error_chunk = staticmethod(lambda c: True)
        _is_retriable_stream_terminal_error_chunk = staticmethod(lambda c: True)
        _build_stream_terminal_alert_message = staticmethod(lambda *a, **k: 'test terminal error')
        _emit_stream_terminal_alert_event = staticmethod(lambda *a, **k: None)
        def _execute_workflow_stream_once(self, *a, **k):
            calls.append(k)
            yield 'terminal-error'
    result = list(Browser()._execute_workflow_stream(session, [], _skip_chunk_planning=True, workflow_variables={'mode':'Pro'}))
    assert result == ['terminal-error'] and len(calls) == 1
    assert calls[0]['workflow_variables'] == {'mode':'Pro'}


def test_history_appends_multiple_runs_with_privacy_and_size_bound():
    from app.services.request_manager import RequestManager, RequestContext
    ctx = RequestContext(request_id='r')
    manager = object.__new__(RequestManager)
    manager.get_request = lambda request_id: ctx
    assert manager.append_workflow_trace('r', [{'path':'root.0','status':'completed','prompt':'SECRET'}])
    assert manager.append_workflow_trace('r', [{'path':'root.1','status':'failed'}])
    assert [e['run'] for e in ctx.monitor['workflow_trace']] == [1,2]
    assert 'SECRET' not in json.dumps(ctx.monitor)
    manager.append_workflow_trace('r', [{'path':'root.2','status':'completed'}] * 700)
    assert len(ctx.monitor['workflow_trace']) == 500


def test_embedded_css_matches_canonical_source():
    source = (ROOT / 'static/js/workflow-studio.js').read_text()
    assignment = source.split('/* STUDIO_STYLE_START */', 1)[1].split('/* STUDIO_STYLE_END */', 1)[0]
    css = json.loads(assignment.split('=', 1)[1].strip().removesuffix(';'))
    assert css == (ROOT / 'static/css/workflow-studio.css').read_text()


def test_visual_feedback_reports_resolved_target_and_honors_page_cancel(monkeypatch):
    from app.api import config_workflow_support as support
    import time
    phases=[];calls=[];instances=[]
    class Executor:
        def __init__(self, **kwargs): self.stop=kwargs['should_stop_checker'];self.cleaned=False;instances.append(self)
        def workflow_execution_scope(self): return nullcontext()
        def _check_cancelled(self): return self.stop()
        def execute_step(self, **kwargs):
            if not self.stop(): calls.append(kwargs)
            if False: yield ''
        def cleanup_after_workflow(self): self.cleaned=True
    tab=SimpleNamespace(url='https://example.com/test',tab_id='tab',html='',run_js=MagicMock(return_value=False))
    browser=MagicMock();session=SimpleNamespace(tab=tab,id='s');browser.tab_pool.acquire_by_raw_tab_id.return_value=session
    monkeypatch.setattr('app.core.workflow.executor.WorkflowExecutor',Executor)
    monkeypatch.setattr(support,'config_engine',MagicMock())
    monkeypatch.setattr(support,'_wake_workflow_editor_test_tab',lambda *_:None)
    payload={'domain':'example.com','tab_id':'tab','visual_feedback':True,'workflow_variables':{'name':'new'},'workflow':[{'action':'CLICK','selector':'css:#{name}'}]}
    result=support._execute_workflow_editor_test_payload(browser,payload,progress_callback=lambda p,m:phases.append(p))
    assert result['success']
    detail=json.loads(next(p for p in phases if p.startswith('step:'))[5:])
    assert detail['path']=='root.0' and detail['selector']=='css:#new'
    assert calls[0]['selector']=='css:#new'
    assert 'css:#new' not in json.dumps(result['trace'])
    calls.clear();tab.run_js.return_value=True
    result=support._execute_workflow_editor_test_payload(browser,payload)
    assert not result['success'] and result['cancelled'] and not calls
    assert all(e.cleaned for e in instances)
    assert browser.tab_pool.release.call_count==2
