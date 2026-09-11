"""Shipped Gemini/DeepSeek workflows: state branches, scopes, compatibility and transport IR."""
import copy
import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from app.core.workflow.flow_runtime import FlowProgram, validate_workflow
from app.core.workflow.executor import WorkflowExecutor

ROOT=Path(__file__).resolve().parents[1]
BEFORE=json.loads((ROOT/'docs/migrations/gemini-deepseek-v2.before.json').read_text())
AFTER=json.loads((ROOT/'config/sites.json').read_text())
GEMINI='gemini.google.com'
DEEPSEEK='chat.deepseek.com'


def walk(nodes):
    for node in nodes:
        yield node
        if node['action'] in {'IF','GROUP','TRY'}:
            for key in ('then','else','steps','fallback'):
                yield from walk(node.get('value',{}).get(key,[]))


def plan(preset, captures=None, transport_sent=False):
    captures=captures or {}
    def capture(spec,target):
        return captures.get(spec['name'],spec.get('default',False))
    flow=FlowProgram(preset['workflow'],context={'prompt':'test prompt'},capture=capture)
    executor=WorkflowExecutor(tab=MagicMock())
    result=[];index=0
    while index<len(flow.steps):
        index=flow.next_leaf(index)
        if index>=len(flow.steps):break
        step,selector=flow.prepare_leaf(index,preset['selectors'])
        result.append((step,selector))
        list(flow.events(iter([]),index))
        if step['action']=='PAGE_FETCH' and transport_sent:
            index=executor._consume_request_transport_followup_steps(flow.steps,index)
        index+=1
    return result,flow


def test_only_requested_sites_and_workflows_change_and_upgrade_is_repeatable():
    for domain in (GEMINI,):
        assert AFTER[domain]['default_preset']==BEFORE[domain]['default_preset']
        assert set(AFTER[domain]['presets'])==set(BEFORE[domain]['presets'])
        for name,old in BEFORE[domain]['presets'].items():
            new=AFTER[domain]['presets'][name]
            assert {k:v for k,v in old.items() if k!='workflow'}=={k:v for k,v in new.items() if k!='workflow'}
            assert validate_workflow(new['workflow'])['version']==2
            assert sum(n['action']=='GROUP' for n in new['workflow'])==3
            assert not any(n['action']=='WAIT' for n in walk(new['workflow']))
    assert 'www.google.com' not in AFTER
    spec=importlib.util.spec_from_file_location('upgrade',ROOT/'scripts/upgrade_gemini_deepseek.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.upgrade(AFTER)==AFTER
    # Existing repository configuration outside the requested sites stays byte-value equivalent.
    shipped=json.loads(subprocess.check_output(['git','show','HEAD:config/sites.json'],cwd=ROOT))
    for domain,site in AFTER.items():
        if domain not in {GEMINI,DEEPSEEK}:assert site==shipped[domain]


@pytest.mark.parametrize('name',list(BEFORE[GEMINI]['presets']))
def test_gemini_matching_model_skips_both_menu_clicks_and_preserves_send(name):
    preset=AFTER[GEMINI]['presets'][name]
    model=next(n for n in preset['workflow'] if n.get('label')=='只在需要时切换模型')['value']['variables']['model_label']
    steps,flow=plan(preset,{'current_model':model,'temporary_pressed':'true'})
    targets=[step.get('target') for step,_ in steps]
    assert targets.count('new_chat_btn')==1
    assert 'ctrl+shift+o' not in targets
    assert '点击模型选择' not in targets and '选择模型' not in targets
    assert '临时对话按钮' not in targets
    assert targets.count('send_btn')==1
    assert [s['action'] for s,_ in steps][-3:]==['FILL_INPUT','CLICK','STREAM_WAIT']
    old_send=next(s for s in BEFORE[GEMINI]['presets'][name]['workflow'] if s.get('target')=='send_btn')
    new_send=next(s for s,_ in steps if s.get('target')=='send_btn')
    assert new_send.get('execution')==old_send.get('execution')
    assert flow.variables.locals==[]


@pytest.mark.parametrize('name',list(BEFORE[GEMINI]['presets']))
@pytest.mark.parametrize('expanded',['true','false'])
def test_gemini_mismatch_selects_once_and_only_opens_closed_menu(name,expanded):
    steps,_=plan(AFTER[GEMINI]['presets'][name],{'current_model':'unrelated model','menu_expanded':expanded,'temporary_pressed':'false'})
    targets=[s.get('target') for s,_ in steps]
    assert targets.count('点击模型选择')==(0 if expanded=='true' else 1)
    assert targets.count('选择模型')==1
    assert targets.count('临时对话按钮')==(0 if name=='3.7 非隐私对话' else 1)
    assert next(s for s,_ in steps if s.get('target')=='选择模型')['optional'] is False
    if '临时对话按钮' in targets:
        assert next(s for s,_ in steps if s.get('target')=='临时对话按钮')['optional'] is False


def test_deepseek_single_main_preset_keeps_normal_settings_and_only_four_actions():
    source=json.loads((ROOT/'docs/migrations/deepseek-single-preset.before.json').read_text())[DEEPSEEK]['presets']['专家模式']
    site=AFTER[DEEPSEEK]
    assert list(site['presets'])==['主预设']
    assert site['default_preset']=='主预设'
    main=site['presets']['主预设']
    expected=copy.deepcopy(source)
    expected['selectors'].pop('专家',None)
    expected['selectors'].pop('快速',None)
    assert {k:v for k,v in main.items() if k!='workflow'}=={k:v for k,v in expected.items() if k!='workflow'}
    assert [n['label'] for n in main['workflow']]==['准备新对话','发送并读取回复']
    assert validate_workflow(main['workflow'])['nodes']==6
    steps,flow=plan(main)
    assert [n['action'] for n,_ in steps]==['CLICK','FILL_INPUT','KEY_PRESS','STREAM_WAIT']
    assert [n.get('target') for n,_ in steps]==['new_chat_btn','input_box','Enter','result_container']
    assert not any(n['action'] in {'SELECT_MODEL','CAPTURE','IF','PAGE_FETCH'} for n in walk(main['workflow']))
    assert main.get('stream_config',{}).get('request_transport',{}).get('mode')!='page_fetch'


@pytest.fixture
def ui_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page()
        page.set_default_timeout(1000)
        yield page
        browser.close()


def run_on_page(page,preset):
    from types import SimpleNamespace
    from app.core.workflow.flow_runtime import capture_page_state
    from playwright.sync_api import expect
    class Element:
        def __init__(self,locator):self.locator=locator
        @property
        def text(self):return self.locator.inner_text()
        def attr(self,name):return self.locator.get_attribute(name)
    def find(selector,timeout=2):
        locator=page.locator(selector.removeprefix('css:')).first
        return Element(locator) if locator.count() else None
    executor=SimpleNamespace(_check_cancelled=lambda:False,_selectors=preset['selectors'],finder=SimpleNamespace(find=find))
    flow=FlowProgram(preset['workflow'],context={'prompt':'synthetic test'},capture=lambda spec,target:capture_page_state(executor,spec,target,flow.variables))
    index=0
    while index<len(flow.steps):
        index=flow.next_leaf(index)
        if index>=len(flow.steps):break
        step,selector=flow.prepare_leaf(index,preset['selectors'])
        def perform():
            if step['action']=='CLICK':
                if not page.locator(selector).count() and step.get('optional'):return
                page.locator(selector).first.click()
                for condition in (step.get('execution') or {}).get('verification',{}).get('conditions',[]):
                    probe=page.locator(preset['selectors'].get(condition['target'],condition['target'])).first
                    if condition['state']=='visible':expect(probe).to_be_visible()
                    if condition['state'] in {'hidden','absent'}:expect(probe).to_be_hidden()
            elif step['action']=='FILL_INPUT':page.locator(selector).fill('synthetic test')
            elif step['action']=='KEY_PRESS':page.keyboard.press(step['target'])
            if False:yield ''
        list(flow.events(perform(),index));index+=1
    return flow


@pytest.mark.parametrize('matching,expanded',[(True,False),(False,False),(False,True)])
def test_gemini_existing_xpath_targets_and_actual_dom_state_branch(ui_page,matching,expanded):
    preset=copy.deepcopy(AFTER[GEMINI]['presets']['3.7flash'])
    # Keep original send-confirmation untouched in shipped JSON; this DOM fixture
    # does not emulate the provider's disappearing send button or SSE transport.
    for step in walk(preset['workflow']):
        if step.get('target')=='send_btn':step.pop('execution',None)
    ui_page.set_content('''<button id="new"><span>发起新对话</span></button>
    <button id="temp" aria-label="临时对话" aria-pressed="false">临时对话</button>
    <button id="model" data-test-id="bard-mode-menu-button" aria-expanded="false">Other</button>
    <div id="menu" hidden><gem-menu-item id="choice">3.7 Flash</gem-menu-item></div>
    <rich-textarea><div class="ql-editor" contenteditable="true" role="textbox"></div></rich-textarea>
    <button id="send" aria-label="发送">发送</button><div class="markdown-main-panel"></div>
    <script>window.clicks=[];document.addEventListener('click',e=>clicks.push(e.target.id||e.target.parentElement.id));
    model.onclick=()=>{menu.hidden=false;model.setAttribute('aria-expanded','true')};
    choice.onclick=()=>{model.textContent='3.7 Flash';menu.hidden=true;model.setAttribute('aria-expanded','false')};
    temp.onclick=()=>temp.setAttribute('aria-pressed','true');</script>''')
    ui_page.evaluate('([same,open])=>{model.textContent=same?"3.7 Flash":"Other";menu.hidden=!open;model.setAttribute("aria-expanded",String(open))}',[matching,expanded])
    run_on_page(ui_page,preset)
    clicks=ui_page.evaluate('clicks')
    assert clicks.count('new')==1 and clicks.count('temp')==1 and clicks.count('send')==1
    assert clicks.count('model')==(0 if matching or expanded else 1)
    assert clicks.count('choice')==(0 if matching else 1)


@pytest.mark.parametrize('selected',[True,False])
def test_deepseek_main_never_changes_page_model_and_sends_once(ui_page,selected):
    preset=AFTER[DEEPSEEK]['presets']['主预设']
    ui_page.set_content('''<div class="_5a8ac7a" id="new">新对话</div>
    <div data-model-type="expert" id="expert" aria-selected="false">专家</div>
    <textarea></textarea><div class="ds-markdown"></div><script>
    window.clicks=[];window.sent=0;document.addEventListener('click',e=>clicks.push(e.target.id));
    document.querySelector('textarea').onkeydown=e=>{if(e.key==='Enter')sent++};
    expert.onclick=()=>expert.setAttribute('aria-selected','true');</script>''')
    ui_page.locator('#expert').evaluate('(e,selected)=>e.setAttribute("aria-selected",String(selected))',selected)
    run_on_page(ui_page,preset)
    assert ui_page.evaluate('clicks.filter(x=>x==="expert").length')==0
    assert ui_page.locator('#expert').get_attribute('aria-selected')==str(selected).lower()
    assert ui_page.evaluate('sent')==1
