"""Browser regression for staged workflow, typed forms and multi-case editing.
Synthetic page only; preview uses the actual Python compiler without web actions.
"""
import copy
import json
import re
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright, expect
from app.core.workflow.preview import preview_workflow
from app.core.workflow.flow_runtime import validate_workflow

ROOT=Path(__file__).resolve().parents[1]
FLOW=[
 {'action':'READONLY_HINT','value':{'title':'操作前须知','text':'第一行说明\n第二行 <script>不应执行</script>','tone':'warning','future_field':123}},
 {'action':'GROUP','label':'准备工作','value':{'variables':{'temporary_chat':False,'model_label':'Pro','retries':2,'mapping':{'a':'b'}},'steps':[
  {'action':'CAPTURE','label':'读取菜单开关','value':{'name':'menu_open','source':'attribute','attribute':'aria-expanded','selector':'#old','default':'false'}},
  {'action':'CAPTURE','label':'读取按钮是否存在','value':{'name':'available','source':'exists','selector':'#old'}},
  {'action':'SWITCH','label':'按页面情况处理','value':{'cases':[
   {'label':'菜单已打开','condition':{'left':'{menu_open}','op':'eq','right':'true'},'steps':[{'action':'CLICK','selector':'#old','custom_note':'keep'}]},
   {'label':'按钮可用','condition':{'left':'{available}','op':'eq','right':True},'steps':[{'action':'CLICK','selector':'#new'}]}],
   'default':[{'action':'READONLY_HINT','value':'没有可以操作的目标'}]}}
 ]}},
 {'action':'GROUP','label':'收尾','value':{'steps':[{'action':'WAIT','value':.1}]}}
]

@pytest.fixture
def page():
 with sync_playwright() as p:
  browser=p.chromium.launch()
  page=browser.new_page(viewport={'width':1320,'height':1040})
  page.set_content('<html><head><meta charset="utf-8"></head><body style="margin:24px;background:#f7f3ea"><button id="old">原按钮</button><button id="new">新按钮</button><div id="studio" style="max-width:1160px;margin:20px auto"></div></body></html>')
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.expose_function('backend',lambda action,data:validate_workflow(data['workflow']) if action=='validate' else preview_workflow(data))
  page.add_script_tag(content=(ROOT/'static/js/workflow-studio.js').read_text())
  page.evaluate('(workflow)=>{window.edits=[];window.openRequests=0;window.studio=WorkflowStudio.mount(document.querySelector("#studio"),{workflow,onChange:w=>edits.push(w),onOpenRealTest:()=>openRequests++,request:(action,data)=>backend(action,data)});}',FLOW)
  yield page
  assert not errors,errors
  browser.close()


def workflow(page):return page.evaluate('studio.getWorkflow()')


def test_notes_are_real_cards_and_stages_are_collapsed_without_mutation(page):
 root=page.locator('#studio');note=root.locator('.note-card').first
 expect(note.locator('.node-name')).to_have_text('操作前须知')
 expect(note.locator('.node-description')).to_have_text('第一行说明\n第二行 <script>不应执行</script>')
 assert not note.locator('script').count()
 expect(root.locator('.node[data-path="root.1.steps.0"]')).to_be_hidden()
 assert workflow(page)==FLOW and page.evaluate('edits')==[]
 root.locator('[data-cmd=toggle-stage][data-path="root.1"]').click()
 expect(root.locator('.node[data-path="root.1.steps.0"]')).to_be_visible()
 root.locator('.node[data-path="root.1.steps.0"]').click()
 expect(root.locator('.inspector')).to_be_visible()
 root.locator('[data-cmd=toggle-stage][data-path="root.1"]').click()
 expect(root.locator('.node[data-path="root.1.steps.0"]')).to_be_hidden()
 assert not root.locator('.workspace').evaluate('e=>e.classList.contains("is-inspecting")')
 assert workflow(page)==FLOW


def test_hint_fields_are_editable_without_losing_unknown_fields(page):
 root=page.locator('#studio');root.locator('.note-card .node').first.click()
 root.locator('[data-field="value.title"]').fill('修改说明')
 root.locator('[data-field="value.text"]').fill('多行\n可读正文')
 root.locator('[data-field="value.tone"]').select_option('danger')
 assert workflow(page)[0]['value']=={'title':'修改说明','text':'多行\n可读正文','tone':'danger','future_field':123}
 expect(root.locator('.note-card').first).to_have_class(re.compile(r'\btone-danger\b'))
 assert not root.locator('.inspector [data-field=target]').count()


def test_group_parameters_edit_types_and_keep_scoped_values(page):
 root=page.locator('#studio');root.get_by_role('button',name='流程参数',exact=True).click()
 expect(root.get_by_text('运行时读取',exact=True)).to_have_count(2)
 root.locator('[data-param-key=temporary_chat]').check()
 root.locator('[data-param-key=retries]').fill('4')
 root.locator('[data-param-key=model_label]').fill('Another model')
 root.locator('[data-param-key=mapping]').fill('{"a":"c","future":true}')
 variables=workflow(page)[1]['value']['variables']
 assert variables=={'temporary_chat':True,'retries':4,'model_label':'Another model','mapping':{'a':'c','future':True}}
 assert workflow(page)[1]['value']['steps']==FLOW[1]['value']['steps']
 root.locator('[data-cmd=select][data-path="root.1.steps.0"]').click()
 expect(root.locator('.node[data-path="root.1.steps.0"]')).to_be_visible()


def test_typed_simulation_and_human_branch_trace_do_not_edit_workflow(page):
 root=page.locator('#studio');root.get_by_role('button',name='检查与试跑',exact=True).click()
 root.locator('[data-sample-key=menu_open]').fill('true')
 root.locator('[data-sample-key=available]').select_option('true')
 assert page.evaluate('JSON.parse(studio.captures)')=={'menu_open':'true','available':True}
 root.locator('[data-cmd=test]').click()
 expect(root.locator('.trace')).to_contain_text('首个命中「菜单已打开」')
 expect(root.locator('.trace')).to_contain_text('未读取网页')
 result=page.evaluate('studio.trace')
 assert [t['path'] for t in result if t['status']=='planned' and t['action']=='CLICK']==['root.1.steps.2.case_0.0']
 assert workflow(page)==FLOW and page.evaluate('edits')==[]
 # Trace navigation opens the correct collapsed stage and case.
 root.locator('.trace [data-path="root.1.steps.2.case_0.0"]').click()
 expect(root.locator('.node[data-path="root.1.steps.2.case_0.0"]')).to_be_visible()


def test_sample_defaults_are_not_live_values_and_real_button_only_opens_editor(page):
 root=page.locator('#studio');root.get_by_role('button',name='检查与试跑',exact=True).click()
 root.locator('[data-cmd=sample-defaults]').click()
 assert page.evaluate('JSON.parse(studio.captures)')=={'menu_open':'false'}
 root.locator('[data-cmd=open-real-test]').click()
 assert page.evaluate('openRequests')==1
 assert page.evaluate('studio.trace')==[]
 assert workflow(page)==FLOW


def test_case_editor_add_reorder_remove_undo_and_move_to_default(page):
 root=page.locator('#studio');root.locator('[data-cmd=expand-all]').click()
 root.locator('.node[data-path="root.1.steps.2"]').click()
 root.locator('[data-cmd=case-add]').click()
 root.locator('[data-field="value.cases.2.label"]').fill('第三种情况')
 root.locator('[data-field="value.cases.2.condition.right"]').fill('special')
 root.locator('[data-cmd=case-up][data-index="2"]').click()
 assert workflow(page)[1]['value']['steps'][2]['value']['cases'][1]['label']=='第三种情况'
 root.locator('[data-cmd=case-remove][data-index="1"]').click()
 assert len(workflow(page)[1]['value']['steps'][2]['value']['cases'])==2
 root.locator('[data-cmd=undo]').click()
 assert len(workflow(page)[1]['value']['steps'][2]['value']['cases'])==3
 root.locator('.node[data-path="root.1.steps.2.case_0.0"]').click()
 root.get_by_text('整理流程结构',exact=True).click()
 root.locator('[data-destination]').select_option('root.1.steps.2.default')
 root.locator('[data-cmd=move-branch]').click()
 switch=workflow(page)[1]['value']['steps'][2]
 assert switch['value']['cases'][0]['steps']==[]
 assert switch['value']['default'][-1]['custom_note']=='keep'
 validate_workflow(workflow(page))


def test_new_multibranch_node_from_palette_can_be_saved_as_valid_workflow(page):
 root=page.locator('#studio');root.locator('.sequence.root > .add').click()
 root.locator('[data-cmd=add][data-action=SWITCH]').click()
 value=workflow(page)[-1]
 assert value['action']=='SWITCH' and len(value['value']['cases'])==2
 assert validate_workflow(workflow(page))['version']==2
 root.locator('[data-field="value.cases.0.condition.op"]').select_option('contains')
 assert workflow(page)[-1]['value']['cases'][0]['condition']['op']=='contains'


@pytest.mark.parametrize('width',[1320,390])
def test_vertical_alternatives_never_split_into_narrow_columns(page,width):
 page.set_viewport_size({'width':width,'height':980})
 root=page.locator('#studio');root.locator('[data-cmd=expand-all]').click()
 a=root.locator('.node[data-path="root.1.steps.2.case_0.0"]').bounding_box()
 b=root.locator('.node[data-path="root.1.steps.2.case_1.0"]').bounding_box()
 assert b['y']>=a['y']+a['height']
 assert a['width']>200 and abs(a['x']-b['x'])<2
 assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')


def test_switch_page_markers_use_exact_case_path_and_drag_without_other_mutation(page):
 from test_workflow_page_guide import HTML, bundle, drag, center
 page.route('**/*',lambda route:route.fulfill(content_type='text/html',body=HTML))
 page.goto('https://example.com/workflow-demo')
 config={'selectors':{},'workflow':[{'action':'SWITCH','value':{'cases':[{'label':'A','condition':{'left':1,'op':'eq','right':1},'steps':[{'action':'CLICK','selector':'#old'}]},{'label':'B','condition':{'left':2,'op':'eq','right':2},'steps':[{'action':'CLICK','selector':'#old','custom':'preserve'}]}],'default':[]}}]}
 page.evaluate('(config)=>{window.__WORKFLOW_EDITOR_CONFIG__=config;window.__WORKFLOW_EDITOR_TARGET_DOMAIN__="example.com";}',config)
 page.add_script_tag(content=bundle())
 guide=page.locator('#wfe-page-guide')
 expect(guide.locator('.pin[data-pin="root.0.case_1.0"]')).to_be_visible()
 drag(page,guide.locator('.pin[data-pin="root.0.case_1.0"]'),*center(page.locator('#new')))
 result=page.evaluate('WorkflowEditor.getSteps()')[0]
 assert result['value']['cases'][0]['steps'][0]['selector']=='#old'
 assert result['value']['cases'][1]['steps'][0]=={'action':'CLICK','selector':'#new','custom':'preserve','flow_version':2}
 assert page.evaluate('actualClicks')==[]


def test_raw_json_stays_focused_syncs_types_and_one_click_runs(page):
 root=page.locator('#studio');root.get_by_role('button',name='检查与试跑',exact=True).click()
 root.locator('[data-sample-type=menu_open]').select_option('boolean')
 root.get_by_text('高级：直接编辑样例 JSON',exact=True).click()
 raw=root.locator('[data-test=captures]')
 raw.fill('{"menu_open":"true","available":false,"unreferenced":{"keep":1}}')
 expect(raw).to_be_focused()
 expect(root.locator('[data-sample-type=menu_open]')).to_have_value('text')
 expect(root.locator('[data-sample-key=available]')).to_have_value('false')
 root.locator('[data-cmd=test]').click()
 expect(root.locator('.trace')).to_contain_text('首个命中「菜单已打开」')
 assert page.evaluate('JSON.parse(studio.captures).unreferenced')=={'keep':1}
 assert workflow(page)==FLOW


def test_invalid_json_does_not_discard_text_or_simulate_old_values(page):
 root=page.locator('#studio');root.get_by_role('button',name='检查与试跑',exact=True).click()
 root.get_by_text('高级：直接编辑样例 JSON',exact=True).click()
 raw=root.locator('[data-test=captures]');raw.fill('{"menu_open":')
 root.locator('[data-cmd=test]').click()
 assert page.evaluate('studio.trace')==[]
 expect(raw).to_have_value('{"menu_open":')
 root.get_by_role('button',name='流程画布',exact=True).click()
 expect(raw).to_be_visible()
 raw.fill('{"menu_open":"false","available":false}')
 root.locator('[data-cmd=test]').click()
 expect(root.locator('.trace')).to_contain_text('所有情况均未命中')


def test_missing_fixture_is_explicit_failure_not_default_route_success(page):
 root=page.locator('#studio');root.get_by_role('button',name='检查与试跑',exact=True).click()
 root.locator('[data-cmd=test]').click()
 expect(root.get_by_role('status')).to_contain_text('请提供页面状态样例')
 assert not any(t.get('branch')=='default' for t in page.evaluate('studio.trace'))


def test_inflight_simulation_result_is_rejected_if_sample_changes(page):
 page.evaluate('()=>{studio.options.request=(action,payload)=>new Promise(resolve=>{window.finishPreview=resolve;});}')
 root=page.locator('#studio');root.get_by_role('button',name='检查与试跑',exact=True).click()
 root.locator('[data-cmd=test]').click()
 root.locator('[data-sample-key=menu_open]').fill('new value')
 page.evaluate('finishPreview({success:true,trace:[{path:"root.1.steps.2",status:"branch",branch:"case_0"}]})')
 expect(root.get_by_role('status')).to_contain_text('样例已修改')
 assert page.evaluate('studio.trace')==[]


def test_omitted_case_steps_and_default_are_materialized_only_when_edited(page):
 minimal=[{'action':'SWITCH','value':{'cases':[{'condition':{'left':True,'op':'eq','right':True}}]}}]
 page.evaluate('(w)=>studio.setWorkflow(w)',minimal)
 root=page.locator('#studio');root.locator('[data-cmd=expand-all]').click()
 assert workflow(page)==minimal
 root.locator('[data-cmd=palette][data-path="root.0.case_0"]').click()
 root.locator('[data-cmd=add][data-action=WAIT]').click()
 value=workflow(page)[0]['value']
 assert len(value['cases'][0]['steps'])==1 and 'default' not in value
 validate_workflow(workflow(page))
 root.locator('[data-cmd=undo]').click()
 assert workflow(page)==minimal


def test_null_parameter_keeps_json_type_and_can_be_undone(page):
 original=[{'action':'GROUP','value':{'variables':{'nullable':None},'steps':[]}}]
 page.evaluate('(w)=>studio.setWorkflow(w)',original)
 root=page.locator('#studio');root.get_by_role('button',name='流程参数',exact=True).click()
 field=root.locator('textarea[data-param-key=nullable]');expect(field).to_have_value('null')
 field.fill('{"value":true}')
 assert workflow(page)[0]['value']['variables']['nullable']=={'value':True}
 root.locator('[data-cmd=undo]').click()
 assert workflow(page)==original


def test_import_export_roundtrip_preserves_all_case_fields(page,tmp_path):
 payload={'workflow':FLOW,'selectors':{'input_box':'#prompt'}}
 root=page.locator('#studio')
 root.locator('[data-import]').set_input_files({'name':'workflow.json','mimeType':'application/json','buffer':json.dumps(payload).encode()})
 expect(root.get_by_role('status')).to_contain_text('已导入为草稿')
 with page.expect_download() as download:
  root.locator('[data-cmd=export]').click()
 path=tmp_path/'roundtrip.json';download.value.save_as(path)
 result=json.loads(path.read_text())
 assert result=={'kind':'workflow','version':2,**payload}
 assert workflow(page)==FLOW


def test_page_guide_shows_note_title_and_body_not_raw_json(page):
 from test_workflow_page_guide import HTML,bundle
 page.route('**/*',lambda route:route.fulfill(content_type='text/html',body=HTML))
 page.goto('https://example.com/workflow-demo')
 page.evaluate('(config)=>{window.__WORKFLOW_EDITOR_CONFIG__=config;window.__WORKFLOW_EDITOR_TARGET_DOMAIN__="example.com";}',{'workflow':[FLOW[0]],'selectors':{}})
 page.add_script_tag(content=bundle())
 guide=page.locator('#wfe-page-guide')
 guide.locator('[data-command=detail-view]').click()
 expect(guide.locator('.detail h3')).to_have_text('1. 操作前须知')
 expect(guide.locator('.hint-content')).to_have_text('第一行说明\n第二行 <script>不应执行</script>')
 assert not guide.locator('.detail script').count()
 assert page.evaluate('WorkflowEditor.getSteps()')==[FLOW[0]]
