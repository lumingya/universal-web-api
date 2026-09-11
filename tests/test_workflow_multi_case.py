"""Ordered multi-case control flow uses the same bounded runtime, no extra executor."""
import copy
import pytest
from app.core.workflow.flow_runtime import FlowProgram, FlowValidationError, validate_workflow
from app.core.workflow.preview import preview_workflow
from app.core.config import WorkflowCancelledError, WorkflowError


def route(cases=None, default=None):
    return {'action':'SWITCH','label':'按页面情况处理','value':{'cases':cases if cases is not None else [
        {'label':'已就绪','condition':{'left':'{state}','op':'eq','right':'ready'},'steps':[{'action':'CLICK','target':'send_btn'}]},
        {'label':'也满足但不可重复发送','condition':{'left':'{state}','op':'in','right':['ready','waiting']},'steps':[{'action':'CLICK','target':'duplicate'}]},
        {'label':'需要登录','condition':{'left':'{state}','op':'eq','right':'login'},'steps':[{'action':'READONLY_HINT','value':'请先登录'}]},
    ],'default':default if default is not None else [{'action':'WAIT','value':.1}]}}


def run(workflow, inputs=None):
    program=FlowProgram(workflow,inputs=inputs)
    leaves=[];index=0
    while index<len(program.steps):
        index=program.next_leaf(index)
        if index>=len(program.steps):break
        step,selector=program.prepare_leaf(index,{})
        leaves.append(step);list(program.events(iter([]),index));index+=1
    return leaves,program


@pytest.mark.parametrize('state,branch,target',[('ready','case_0','send_btn'),('waiting','case_1','duplicate'),('login','case_2',''),('unknown','default','')])
def test_first_matching_case_or_default_exactly_once(state,branch,target):
    workflow=[route(),{'action':'KEY_PRESS','target':'Tab'}];before=copy.deepcopy(workflow)
    leaves,program=run(workflow,{'state':state})
    assert len(leaves)==2
    assert leaves[0].get('target')==target
    assert leaves[0]['_path']==f'root.0.{branch}.0'
    assert leaves[1]['target']=='Tab'
    assert [x['branch'] for x in program.trace if x['status']=='branch']==[branch]
    assert workflow==before


def test_default_may_be_empty_and_no_case_is_fallthrough():
    leaves,program=run([route(default=[])],{'state':'unknown'})
    assert leaves==[]
    assert [t['branch'] for t in program.trace if t['status']=='branch']==['default']


@pytest.mark.parametrize('bad',[
    {'cases':[]}, {'cases':[{'steps':[]}]}, {'cases':[None]},
    {'cases':[{'condition':{'left':1,'op':'eq','right':1},'steps':[{'action':'UNKNOWN'}]}]},
    {'cases':[{'condition':{'left':1,'op':'eq','right':1},'label':3}]},
    {'cases':[{'condition':{'left':1,'op':'eq','right':1}}]*33},
    {'cases':[{'condition':{'left':1,'op':'eq','right':1}}],'default':{}},
])
def test_invalid_unreachable_cases_are_rejected_before_execution(bad):
    with pytest.raises(FlowValidationError):FlowProgram([{'action':'SWITCH','value':bad}])


def test_nested_group_scope_and_guard_can_exit_group_from_a_case():
    node=route(cases=[{'condition':{'left':'{local}','op':'eq','right':True},'steps':[
        {'action':'SET','value':{'name':'result','value':'chosen','scope':'global'}},
        {'action':'GUARD','value':{'condition':{'left':1,'op':'eq','right':1},'mode':'skip_group'}},
        {'action':'CLICK','target':'must_not_run'}]}])
    leaves,program=run([{'action':'GROUP','value':{'variables':{'local':True},'steps':[node,{'action':'CLICK','target':'also_skip'}]}},{'action':'KEY_PRESS','target':'Tab'}])
    assert [s.get('target') for s in leaves]==['Tab']
    assert program.variables.globals['result']=='chosen'
    assert program.variables.locals==[]


def test_branch_anchors_cannot_jump_across_cases():
    node=route(cases=[{'condition':{'left':1,'op':'eq','right':1},'steps':[{'action':'GUARD','value':{'condition':{'left':1,'op':'eq','right':1},'mode':'goto','anchor':'elsewhere'}}]}, {'condition':{'left':2,'op':'eq','right':2},'steps':[{'action':'LABEL','value':{'name':'elsewhere'}}]}])
    with pytest.raises(FlowValidationError):validate_workflow([node])


def test_simulation_uses_same_route_and_never_records_real_completion():
    data={'workflow':[{'action':'CAPTURE','value':{'name':'state','source':'text','selector':'#status'}},route()], 'capture_values':{'state':'ready'}}
    result=preview_workflow(data)
    assert result['success']
    assert result['planned_actions']==[{'path':'root.1.case_0.0','action':'CLICK'}]
    assert not any(t['status']=='completed' for t in result['trace'])
    assert all('condition' not in t and 'value' not in t and 'selector' not in t for t in result['trace'])


def test_cancel_before_switch_prevents_case_execution():
    program=FlowProgram([route()],inputs={'state':'ready'},stop_checker=lambda:True)
    with pytest.raises(WorkflowCancelledError):program.next_leaf(0)


def test_page_fetch_skip_is_contained_inside_case():
    from app.core.workflow.executor import WorkflowExecutor
    from unittest.mock import MagicMock
    node=route(cases=[{'condition':{'left':True,'op':'eq','right':True},'steps':[{'action':'PAGE_FETCH'},{'action':'FILL_INPUT'},{'action':'KEY_PRESS','target':'Enter'},{'action':'STREAM_WAIT'}]}],default=[{'action':'CLICK','target':'send_btn'}])
    program=FlowProgram([node]);index=program.next_leaf(0)
    executor=WorkflowExecutor(tab=MagicMock())
    last=executor._consume_request_transport_followup_steps(program.steps,index)
    assert program.steps[last+1]['action']=='STREAM_WAIT'
    assert program.steps[last+2]['_op']=='jump'


def test_control_failure_can_use_existing_try_without_replaying_actions():
    node=route(cases=[{'condition':{'left':'{missing}','op':'eq','right':1},'steps':[]}])
    leaves,_=run([{'action':'TRY','value':{'steps':[node],'attempts':1,'fallback':[{'action':'WAIT','value':.1}]}}])
    assert len(leaves)==1 and leaves[0]['action']=='WAIT'


def test_other_conditions_are_not_evaluated_after_first_match():
    node=route(cases=[{'condition':{'left':1,'op':'eq','right':1},'steps':[{'action':'CLICK','target':'send_btn'}]}, {'condition':{'left':'{missing}','op':'eq','right':2},'steps':[]}])
    leaves,_=run([node])
    assert len(leaves)==1 and leaves[0]['target']=='send_btn'


def test_case_checks_obey_transition_budget_and_cannot_be_recovered_by_try():
    from app.core.workflow.flow_runtime import FlowLimitError
    node=route(cases=[{'condition':{'left':1,'op':'eq','right':2},'steps':[]}]*32)
    program=FlowProgram([{'action':'TRY','value':{'steps':[node],'fallback':[{'action':'CLICK','target':'must_not_run'}]}}],max_transitions=4)
    with pytest.raises(FlowLimitError):program.next_leaf(0)


def test_unreachable_bad_condition_rejected_and_max_cases_valid():
    node=route(cases=[{'condition':{'left':1,'op':'eq','right':1},'steps':[]}]*32)
    assert validate_workflow([node])['version']==2
    node['value']['cases'][-1]={'condition':{'left':1,'op':'unknown','right':2},'steps':[]}
    with pytest.raises(FlowValidationError):FlowProgram([node])


def test_output_prevents_retry_or_fallback_inside_switch():
    node=route(cases=[{'condition':{'left':'{missing}','op':'eq','right':1},'steps':[]}])
    program=FlowProgram([{'action':'STREAM_OUTPUT'},{'action':'TRY','value':{'steps':[node],'fallback':[{'action':'CLICK','target':'must_not_run'}]}}])
    index=program.next_leaf(0);list(program.events(iter(['data: {"choices":[]}\n\n']),index))
    with pytest.raises(WorkflowError):program.next_leaf(index+1)


@pytest.mark.parametrize('state,clicks',[('closed',2),('open',1),('selected',0),('unexpected',0)])
def test_documented_example_can_be_imported_and_simulated(state,clicks):
    import json
    from pathlib import Path
    example=json.loads((Path(__file__).resolve().parents[1]/'docs/examples/multi-case-workflow.json').read_text())
    result=preview_workflow({**example,'capture_values':{'menu_state':state}})
    assert result['success']
    assert sum(a['action']=='CLICK' for a in result['planned_actions'])==clicks
    assert not any(a['action'] in ['FILL_INPUT','KEY_PRESS','STREAM_WAIT'] for a in result['planned_actions'])


def test_preset_save_accepts_switch_and_rejects_bad_nested_branch_atomically():
    from app.api.config_route_models import _normalize_preset_config_payload
    from fastapi import HTTPException
    payload={'selectors':{},'workflow':[route()],'custom_metadata':{'preserve':True},'stealth':False}
    assert _normalize_preset_config_payload(payload)==payload
    bad=copy.deepcopy(payload);bad['workflow'][0]['value']['default']=[{'action':'NOT_AN_ACTION'}]
    before=copy.deepcopy(bad)
    with pytest.raises(HTTPException):_normalize_preset_config_payload(bad)
    assert before==bad
