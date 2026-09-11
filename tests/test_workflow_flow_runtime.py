import copy
import json
import pytest

from app.core.workflow.flow_runtime import (FlowProgram, Variables, validate_workflow, validate_inputs,
    FlowValidationError, FlowVariableError, FlowLimitError, capture_page_state)
from app.core.config import WorkflowCancelledError, WorkflowError


def node(action, value=None, **fields):
    return {'action':action, 'target':'', 'optional':False, 'value':value, **fields}


def drive(program, callback=lambda step, context: iter(())):
    i = 0
    executed = []
    while i < len(program.steps):
        i = program.next_leaf(i)
        if i == len(program.steps): break
        try:
            step, selector = program.prepare_leaf(i, {})
            list(program.events(callback(step, program.leaf_context(step)), i))
            executed.append(step)
            i += 1
        except Exception as exc:
            resumed = program.recover(exc, i)
            if resumed is None: raise
            i = resumed
    return executed


def test_read_map_compare_skip_and_branch_merge():
    flow = [node('SET', {'name':'desired','value':{'$map':{'gemini 3.1 Pro':'Pro'},'input':{'$var':'context.model'}}}),
            node('CAPTURE', {'name':'current','source':'text','selector':'.model'}),
            node('IF', {'condition':{'left':{'$var':'vars.current'},'op':'ne','right':'{desired}'},
                'then':[node('CLICK', target='menu'), node('CLICK', selector='button[data-name="{desired}"]')], 'else':[]}),
            node('FILL_INPUT'),node('STREAM_WAIT')]
    original=copy.deepcopy(flow)
    result=drive(FlowProgram(flow,context={'model':'gemini 3.1 Pro'},capture=lambda *_:'Pro'))
    assert [x['action'] for x in result]==['FILL_INPUT','STREAM_WAIT']
    p=FlowProgram(flow,context={'model':'gemini 3.1 Pro'},capture=lambda *_:'Flash')
    assert [x['action'] for x in drive(p)]==['CLICK','CLICK','FILL_INPUT','STREAM_WAIT']
    assert flow==original
    assert any(x.get('branch')=='then' for x in p.trace)
    assert 'gemini 3.1 Pro' not in json.dumps(p.trace)


def test_scope_inputs_defaults_and_typed_interpolation():
    v=Variables({'model':'API model'}, {'model':'User value','zero':0,'flag':False})
    assert v.resolve('{model}')=='User value'
    assert v.resolve('{{context.model}}')=='API model'
    assert v.resolve('{{inputs.zero}}')==0
    assert v.resolve('{flag}') is False
    assert v.resolve({'$var':'missing','default':2})==2
    with pytest.raises(FlowVariableError): v.resolve('{missing}')
    v.set('model','Default',mode='default');assert v.resolve('{model}')=='User value'
    flow=[node('SET',{'name':'x','value':1}),node('GROUP',{'variables':{'x':2},'steps':[
        node('SET',{'name':'y','value':3,'scope':'global'}),node('WAIT','{x}')]}),node('WAIT','{x}')]
    p=FlowProgram(flow); result=drive(p)
    assert [s['value'] for s in result]==[2,1]
    assert p.variables.globals['y']==3 and not p.variables.locals
    assert FlowProgram([]).variables.globals=={}


@pytest.mark.parametrize('op,a,b,result', [('eq',0,False,False),('eq','Pro','Pro',True),('ne',1,'1',True),
    ('contains','Flash Lite','Lite',True),('starts_with','Pro 3','Pro',True),('ends_with','Pro 3','3',True),
    ('in','a',['a','b'],True),('matches','Flash Lite','^Flash',True),('gte',2,1,True)])
def test_comparisons(op,a,b,result):
    assert Variables().condition({'left':a,'op':op,'right':b})==result


def test_exists_is_not_truthiness_and_conditions_short_circuit():
    v=Variables(inputs={'zero':0,'no':False,'none':None})
    assert v.condition({'left':{'$var':'zero'},'op':'exists'})
    assert v.condition({'left':{'$var':'no'},'op':'exists'})
    assert v.condition({'left':{'$var':'missing'},'op':'not_exists'})
    assert not v.condition({'left':{'$var':'none'},'op':'exists'})
    assert not v.condition({'all':[{'left':1,'op':'eq','right':2},{'left':'{missing}','op':'eq','right':1}]})
    with pytest.raises(FlowVariableError):v.condition({'left':'9','op':'gt','right':2})


def test_guard_forward_anchor_and_group_scope_exit():
    flow=[node('GROUP',{'variables':{'inside':True},'steps':[
        node('GUARD',{'condition':{'left':1,'op':'eq','right':1}}),node('CLICK')]}),
        node('GUARD',{'mode':'goto','anchor':'done','condition':{'left':True,'op':'eq','right':True}}),
        node('CLICK'),node('LABEL',{'name':'done'}),node('WAIT',1)]
    p=FlowProgram(flow);assert [s['action'] for s in drive(p)]==['WAIT'];assert p.variables.locals==[]
    for invalid in [
        [node('GUARD',{'mode':'goto','anchor':'missing','condition':{'left':1,'op':'eq','right':1}})],
        [node('LABEL',{'name':'x'}),node('GUARD',{'mode':'goto','anchor':'x','condition':{'left':1,'op':'eq','right':1}})],
    ]:
        with pytest.raises(FlowValidationError):validate_workflow(invalid)


def test_bounded_retry_fallback_and_rollback():
    flow=[node('TRY',{'attempts':3,'delay':0,'steps':[node('SET',{'name':'dirty','value':1}),node('WAIT',1)],
                      'fallback':[node('SET',{'name':'caught','value':'{error.type}'})]})]
    p=FlowProgram(flow);calls=[]
    def failing(step,context):calls.append(step);raise WorkflowError('temporary')
    drive(p,failing)
    assert len(calls)==3 and p.variables.globals['caught']=='WorkflowError'
    assert len([e for e in p.trace if e['status']=='retry'])==2
    assert p.variables.error=={}


def test_no_replay_after_side_effect_or_output_and_cancellation_never_caught():
    for leaf in [node('CLICK'),node('STREAM_WAIT',retry_safe=True)]:
        p=FlowProgram([node('TRY',{'attempts':3,'delay':0,'steps':[leaf]})]);calls=[]
        def fail(step,context):
            calls.append(step)
            if step['action']=='STREAM_WAIT':yield 'data: {"choices":[]}\n\n'
            raise WorkflowError('failure')
        with pytest.raises(WorkflowError):drive(p,fail)
        assert len(calls)==1
    p=FlowProgram([node('TRY',{'steps':[node('WAIT')],'fallback':[node('CLICK')]})],stop_checker=lambda:True)
    with pytest.raises(WorkflowCancelledError):drive(p)


def test_recovered_sse_error_is_not_emitted():
    p=FlowProgram([node('TRY',{'steps':[node('WAIT')],'fallback':[]})])
    i=p.next_leaf(0)
    def fail(*_):yield 'data: {"error":{"message":"bad"}}\n\n'
    drive(p,fail)
    assert any(t['status']=='fallback' for t in p.trace)


def test_limits_and_expression_safety():
    for value in [{'__proto__':1},[],{'x':'a'*70000}]:
        with pytest.raises(FlowValidationError):validate_inputs(value)
    for expr in ['{vars.__class__}',{'$transform':'eval','input':'print(1)'}]:
        with pytest.raises(FlowVariableError):Variables().resolve(expr)
    with pytest.raises(FlowVariableError):Variables().condition({'left':'x','op':'matches','right':'['})
    with pytest.raises(FlowValidationError):validate_workflow([node('TRY',{'attempts':6})])
    p=FlowProgram([node('SET',{'name':'x','value':1})]*4,max_transitions=2)
    with pytest.raises(FlowLimitError):drive(p)
