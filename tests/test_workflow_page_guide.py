"""Real Chromium regression on a synthetic webpage, no AI account or external network.
Covers physical drag/pick, configuration round-trip, and real target clicks using the DSL.
"""
import copy
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
HTML = '''<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:#f5f3ec;color:#35402f;font:15px system-ui}header{height:86px;border-bottom:1px solid #dedfce;padding:26px 58px;background:#fffdf8}header b{font-size:23px}header span{float:right;color:#87917b}main{margin:35px 0 0 65px;width:660px}h1{font-size:32px;margin:5px 0 12px}p{color:#7d8572;line-height:1.8}section{background:#fffdf8;border:1px solid #dbddce;padding:26px;border-radius:14px;margin:22px 0}button{font:inherit;cursor:pointer;background:#e9eedf;border:1px solid #cbd3be;border-radius:8px;padding:13px 26px;color:#48543c}textarea{width:100%;height:126px;border:1px solid #d7dbcc;border-radius:9px;margin:12px 0;padding:15px;font:inherit;background:#fcfbf7}#new{margin-left:65px}#send{float:right;background:#5d6b4d;color:white}#reply{min-height:85px;color:#858c79}#lower{margin-top:600px;height:220px}#board{position:absolute;left:740px;top:290px;width:75px;height:180px;background:#e5eada;border:1px dashed #91a57b}.same{padding:7px 12px;font-size:12px}
</style></head><body><header><b>Leaf / 工作台</b><span>本地合成测试页面 · 不连接 AI 服务</span></header><main id="demo"><p>CONVERSATION / 01</p><h1>把步骤放回真实页面</h1><p>先检查目标，再发送请求。页面上的数字标记与右侧步骤一一对应。</p><section><p>选择工作模式</p><button id="old">当前模式 · Flash</button><button id="new">目标模式 · Pro</button><p><button class="same">重复按钮 A</button> <button class="same">重复按钮 B</button></p></section><section><label>消息正文<textarea id="prompt" placeholder="在这里输入消息"></textarea></label><button id="send">发送消息 ↗</button><div style="clear:both"></div></section><section id="reply">回复将显示在这里</section><div id="lower">页面下方的目标<button id="bottom">底部按钮</button></div></main><div id="board">坐标区</div><script>window.actualClicks=[];document.querySelector('#demo').addEventListener('click',e=>{if(e.target.tagName==='BUTTON')actualClicks.push(e.target.id||e.target.textContent)});</script></body></html>'''


def fixture_config():
    return {'selectors':{'shared':'css:#old','input':'css:#prompt','reply':'css:#reply'},'workflow':[
        {'action':'CLICK','target':'shared','optional':False,'label':'打开当前模式','execution':{'retry':{'max_attempts':2}},'custom_note':'preserve'},
        {'action':'FILL_INPUT','target':'input','value':None,'label':'填写消息'},
        {'action':'WAIT','value':.5,'label':'等待页面响应'},
        {'action':'IF','label':'按需切换模式','value':{'condition':{'left':True,'op':'eq','right':True},'then':[{'action':'CLICK','target':'shared','label':'仅在需要时选择模式'}],'else':[{'action':'READONLY_HINT','value':'保持当前模式'}]}},
        {'action':'COORD_CLICK','value':{'x':770,'y':320,'random_radius':0},'label':'固定位置点击'},
        {'action':'COORD_SCROLL','value':{'start_x':770,'start_y':380,'end_x':770,'end_y':440},'label':'滚动区域'},
        {'action':'STREAM_WAIT','target':'reply','label':'读取回复'},
    ]}


def bundle():
    return '\n'.join((ROOT/'static/js'/name).read_text() for name in ['workflow-studio.js','workflow-page-guide.js','workflow-editor-inject.js'])


@pytest.fixture
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width':1320,'height':1080})
        page.route('**/*',lambda route:route.fulfill(content_type='text/html',body=HTML))
        page.goto('https://example.com/workflow-demo')
        page.evaluate('(config)=>{window.__WORKFLOW_EDITOR_CONFIG__=config;window.__WORKFLOW_EDITOR_TARGET_DOMAIN__="example.com";window.__WORKFLOW_EDITOR_PRESET_NAME__="demo";}',fixture_config())
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_script_tag(content=bundle())
        yield page
        assert not errors, errors
        browser.close()


def drag(page, handle, x, y):
    r = handle.bounding_box()
    assert r
    page.mouse.move(r['x']+r['width']/2,r['y']+r['height']/2)
    page.mouse.down()
    page.mouse.move(x,y,steps=12)
    page.mouse.up()


def center(locator):
    r=locator.bounding_box()
    return (r['x']+r['width']/2,r['y']+r['height']/2)


def workflow(page):
    return page.evaluate('window.WorkflowEditor.getSteps()')


def test_page_first_numbers_and_no_implicit_execution(page):
    guide=page.locator('#wfe-page-guide')
    expect(guide.locator('.panel')).to_be_visible()
    expect(page.locator('#wfe-tree-studio')).to_be_hidden()
    expect(guide.locator('.row')).to_have_count(9)
    expect(guide.locator('.pin[data-pin="root.0"]')).to_be_visible()
    assert workflow(page)==fixture_config()['workflow']
    (ROOT/'test-artifacts').mkdir(exist_ok=True)
    page.screenshot(path=str(ROOT/'test-artifacts/workflow-page-guide.png'))
    guide.locator('[data-select="root.2"].select').click()
    expect(guide.locator('.detail')).to_contain_text('没有点击位置')
    guide.locator('[data-command=next]').click()
    expect(guide.locator('.detail')).to_contain_text('两侧不会同时执行')
    assert page.evaluate('actualClicks')==[]


def test_dock_panel_and_canvas_are_draggable_and_clamped(page):
    guide=page.locator('#wfe-page-guide');panel=guide.locator('.panel');dock=page.locator('#wfe-editor-dock')
    old=panel.bounding_box();drag(page,panel.locator('h2'),old['x']-40,old['y']+45)
    new=panel.bounding_box();assert new['x']<old['x']-20 and new['y']!=old['y']
    old=dock.bounding_box();drag(page,dock,185,960);new=dock.bounding_box()
    assert abs(old['x']-new['x'])>100
    expect(panel).to_be_visible() # Drag release must NOT trigger click-toggle.
    dock.click();expect(panel).to_be_hidden();dock.click();expect(panel).to_be_visible()
    guide.locator('[data-command=advanced]').click();canvas=page.locator('#wfe-tree-studio');expect(canvas).to_be_visible()
    old=canvas.bounding_box();drag(page,canvas.locator('.drag-handle'),120,30);new=canvas.bounding_box();assert new['x']!=old['x'] or new['y']!=old['y']
    canvas.locator('[data-cmd=page-view]').click();expect(panel).to_be_visible()
    page.set_viewport_size({'width':390,'height':844})
    page.wait_for_timeout(100)
    r=panel.bounding_box();assert r['x']>=0 and r['x']+r['width']<=391
    r=dock.bounding_box();assert r['x']>=0 and r['x']+r['width']<=391


def test_nested_dom_drag_save_ack_undo_and_reload(page):
    guide=page.locator('#wfe-page-guide');original=fixture_config();x,y=center(page.locator('#new'))
    drag(page,guide.locator('.pin[data-pin="root.3.then.0"]'),x,y)
    w=workflow(page);assert w[3]['value']['then'][0]['selector']=='#new'
    assert 'selector' not in w[0] and w[0]==original['workflow'][0]
    assert w[3]['value']['else']==original['workflow'][3]['value']['else']
    assert page.evaluate('actualClicks')==[]
    guide.locator('[data-command=save]').click()
    action=page.evaluate('window.__WORKFLOW_EDITOR_PENDING_ACTIONS__.at(-1)')
    assert action['payload']['workflow']==w
    assert action['payload']['selectors']==original['selectors']
    guide.locator('[data-command=advanced]').click();canvas=page.locator('#wfe-tree-studio')
    canvas.locator('[data-cmd=undo]').click();assert workflow(page)==original['workflow']
    canvas.locator('[data-cmd=redo]').click();assert workflow(page)==w
    # An older successful save cannot undo a new edit.
    page.evaluate('window.WorkflowEditor.showPage()')
    drag(page,guide.locator('.pin[data-pin="root.0"]'),*center(page.locator('#send')))
    newer=workflow(page)
    page.evaluate('(id)=>WorkflowEditor.handleBackendResult(id,true,"保存成功")',action['id'])
    assert workflow(page)==newer
    page.evaluate('WorkflowEditor.destroy()')
    page.evaluate('(config)=>window.__WORKFLOW_EDITOR_CONFIG__=config',action['payload'])
    page.add_script_tag(content=bundle())
    assert workflow(page)==w
    expect(page.locator('#wfe-page-guide .pin[data-pin="root.3.then.0"]')).to_be_visible()


def test_picker_esc_unique_selector_and_no_page_click(page):
    guide=page.locator('#wfe-page-guide');before=workflow(page)
    guide.locator('[data-command=detail-view]').click();guide.locator('[data-command=pick]').click();expect(guide.locator('.pick-surface')).to_be_visible()
    page.keyboard.press('Escape');expect(guide.locator('.pick-surface')).to_have_count(0);assert workflow(page)==before
    guide.locator('[data-command=detail-view]').click();guide.locator('[data-command=pick]').click();page.mouse.click(*center(page.locator('.same').nth(1)))
    selector=workflow(page)[0]['selector']
    assert page.locator(selector).count()==1 and page.locator(selector).inner_text()=='重复按钮 B'
    assert page.evaluate('actualClicks')==[]
    expect(guide.locator('.panel')).to_be_visible()


def test_drag_escape_and_blank_drop_do_not_mutate(page):
    guide=page.locator('#wfe-page-guide');before=workflow(page);pin=guide.locator('.pin[data-pin="root.0"]')
    page.mouse.move(*center(pin));page.mouse.down();page.mouse.move(*center(page.locator('#new')),steps=8);page.keyboard.press('Escape');page.mouse.up()
    assert workflow(page)==before
    drag(page,guide.locator('.pin[data-pin="root.0"]'),20,1040)
    assert workflow(page)==before
    expect(guide.locator('.notice')).to_contain_text('没有选中')
    assert page.evaluate('actualClicks')==[]


def test_coordinate_and_scroll_endpoint_drag_are_not_just_visual(page):
    guide=page.locator('#wfe-page-guide')
    drag(page,guide.locator('.pin[data-pin="root.4"]'),745,335)
    assert workflow(page)[4]['value']=={'x':745,'y':335,'random_radius':0}
    drag(page,guide.locator('.pin[data-pin="root.5"][data-end=end]'),755,495)
    assert workflow(page)[5]['value']=={'start_x':770,'start_y':380,'end_x':755,'end_y':495}
    assert page.evaluate('actualClicks')==[]


def test_scroll_relayout_and_missing_dynamic_target_explanations(page):
    guide=page.locator('#wfe-page-guide');pin=guide.locator('.pin[data-pin="root.0"]');old=pin.bounding_box()['y']
    page.evaluate('window.scrollBy(0,100)');page.wait_for_timeout(150)
    assert abs(pin.bounding_box()['y']-(old-100))<2
    page.evaluate('document.getElementById("old").remove()');page.wait_for_timeout(700)
    expect(pin).to_have_count(0)
    guide.locator('[data-select="root.0"].select').click()
    expect(guide.locator('.detail')).to_contain_text('页面暂无目标')
    assert page.evaluate('WorkflowPageGuide.resolve("css:button[data-model=\\\"{desired}\\\"]").reason').startswith('运行时')


def test_basic_fields_and_advanced_page_selection_stay_in_sync(page):
    guide=page.locator('#wfe-page-guide')
    guide.locator('[data-select="root.2"].select').click();guide.locator('[data-command=detail-view]').click();guide.locator('[data-edit=wait]').fill('1.2')
    assert workflow(page)[2]['value']==1.2
    guide.locator('[data-command=list-view]').click();guide.locator('[data-select="root.1"].select').click();guide.locator('[data-command=detail-view]').click();guide.locator('[data-edit=input]').fill('测试正文')
    assert workflow(page)[1]['value']=='测试正文'
    guide.locator('[data-command=detail-view]').click();guide.locator('[data-command=edit]').click();canvas=page.locator('#wfe-tree-studio')
    expect(canvas.locator('[data-field=value]').first).to_have_value('测试正文')
    canvas.locator('[data-cmd=locate-page]').click();expect(guide.locator('.panel')).to_be_visible()
    expect(guide.locator('[data-edit=input]')).to_have_value('测试正文')


def test_real_test_feedback_is_passive_and_restores_after_failure(page):
    guide=page.locator('#wfe-page-guide')
    guide.locator('[data-command=test]').click();canvas=page.locator('#wfe-tree-studio')
    page.on('dialog',lambda d:d.accept())
    canvas.locator('[data-cmd=test]').click()
    expect(guide.locator('.panel')).to_be_hidden();expect(guide.locator('.pin')).to_have_count(0)
    action=page.evaluate('window.__WORKFLOW_EDITOR_PENDING_ACTIONS__.at(-1)')
    assert action['type']=='test_workflow' and action['payload']['visual_feedback'] is True
    detail={'path':'root.0','selector':'#new'}
    page.evaluate('([id,phase])=>WorkflowEditor.handleBackendStatus(id,phase,"执行点击")',[action['id'],'step:'+json.dumps(detail)])
    expect(guide.locator('.caption')).to_contain_text('正在执行')
    r=guide.locator('.halo').bounding_box();actual=page.locator('#new').bounding_box();assert abs(r['x']-actual['x'])<1
    assert page.evaluate('document.elementFromPoint(...(()=>{const r=document.querySelector("#new").getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})()).id')=='new'
    page.evaluate('(id)=>WorkflowEditor.handleBackendResult(id,false,"模拟失败回推",[{path:"root.0",status:"failed",action:"CLICK"}])',action['id'])
    expect(canvas).to_be_visible()
    canvas.locator('[data-cmd=page-view]').click();expect(guide.locator('.panel')).to_be_visible()
    expect(guide.locator('.notice')).to_contain_text('模拟失败回推')
    assert page.evaluate('actualClicks')==[]


def test_saved_selector_is_used_for_actual_page_click_by_flow_program(page):
    from app.core.workflow.flow_runtime import FlowProgram
    guide=page.locator('#wfe-page-guide')
    drag(page,guide.locator('.pin[data-pin="root.0"]'),*center(page.locator('#new')))
    step=workflow(page)[0]
    page.evaluate('WorkflowEditor.hide()')
    program=FlowProgram([step])
    i=program.next_leaf(0);resolved,selector=program.prepare_leaf(i,fixture_config()['selectors'])
    def perform():
        page.locator(selector.removeprefix('css:')).click()
        if False:yield ''
    list(program.events(perform(),i))
    assert page.evaluate('actualClicks')==['new']
    assert program.trace[-1]['status']=='completed'


def test_destroy_removes_every_overlay_and_listener_reinject_once(page):
    page.locator('#wfe-page-guide [data-command=detail-view]').click();page.locator('#wfe-page-guide [data-command=pick]').click()
    page.evaluate('WorkflowEditor.destroy()')
    expect(page.locator('#wfe-page-guide,#wfe-editor-dock,#wfe-tree-studio')).to_have_count(0)
    page.mouse.click(*center(page.locator('#new')))
    assert page.evaluate('actualClicks')==['new']
    page.add_script_tag(content=bundle())
    expect(page.locator('#wfe-page-guide')).to_have_count(1)
    expect(page.locator('#wfe-editor-dock')).to_have_count(1)
    page.evaluate('window.scrollBy(0,20)');page.wait_for_timeout(700)
    expect(page.locator('#wfe-page-guide .pin[data-pin="root.0"]')).to_have_count(1)


def test_picker_from_canvas_returns_to_canvas_without_edit_on_escape(page):
    guide=page.locator('#wfe-page-guide');before=workflow(page)
    guide.locator('[data-command=detail-view]').click();guide.locator('[data-command=edit]').click();canvas=page.locator('#wfe-tree-studio')
    canvas.locator('[data-cmd=pick]').click()
    expect(canvas).to_be_hidden();expect(guide.locator('.pick-surface')).to_be_visible()
    page.keyboard.press('Escape')
    expect(canvas).to_be_visible();assert workflow(page)==before
    canvas.locator('[data-cmd=pick]').click();page.mouse.click(*center(page.locator('#new')))
    expect(canvas).to_be_visible();assert workflow(page)[0]['selector']=='#new'
    assert page.evaluate('actualClicks')==[]


def test_ambiguous_direct_failure_is_not_replayed_through_bridge(page):
    posts=[]
    def fail(route):
        posts.append(route.request.url)
        route.abort('failed')
    page.route('**/api/workflow-editor/test',fail)
    page.evaluate('window.__WORKFLOW_EDITOR_API_BASE__=location.origin')
    page.locator('#wfe-page-guide [data-command=test]').click()
    page.on('dialog',lambda d:d.accept())
    page.locator('#wfe-tree-studio [data-cmd=test]').click()
    expect(page.locator('#wfe-test-status')).to_contain_text('未重复执行')
    assert len(posts)==1
    assert page.evaluate('(window.__WORKFLOW_EDITOR_PENDING_ACTIONS__||[]).filter(a=>a.type==="test_workflow").length')==0


def test_unique_selector_escapes_attributes_and_refuses_frames(page):
    result=page.evaluate('''() => {
      const b=document.createElement('button');b.setAttribute('data-testid','test"[odd]');document.body.append(b);
      const selector=WorkflowPageGuide.uniqueSelector(b);
      const frame=document.createElement('iframe');document.body.append(frame);let rejected=false;
      try {WorkflowPageGuide.uniqueSelector(frame);} catch (_) {rejected=true;}
      return {same:document.querySelector(selector)===b,rejected};
    }''')
    assert result=={'same':True,'rejected':True}


def test_stop_button_requests_cancellation_without_restoring_interactive_overlay_early(page):
    page.locator('#wfe-page-guide [data-command=test]').click()
    page.on('dialog',lambda d:d.accept())
    page.locator('#wfe-tree-studio [data-cmd=test]').click()
    page.locator('#wfe-cancel-test').click()
    assert page.evaluate('window.__WORKFLOW_EDITOR_TEST_CANCELLED__') is True
    expect(page.locator('#wfe-page-guide .panel')).to_be_hidden()
    expect(page.locator('#wfe-page-guide .pin')).to_have_count(0)
    expect(page.locator('#wfe-cancel-test')).to_be_disabled()
    action=page.evaluate('window.__WORKFLOW_EDITOR_PENDING_ACTIONS__.at(-1)')
    page.evaluate('(id)=>WorkflowEditor.handleBackendResult(id,false,"测试已停止",[])',action['id'])
    expect(page.locator('#wfe-cancel-test')).to_have_count(0)
    expect(page.locator('#wfe-tree-studio')).to_be_visible()


def test_touch_drag_moves_dock_without_toggling(page):
    dock=page.locator('#wfe-editor-dock');start=center(dock);old=dock.bounding_box()
    cdp=page.context.new_cdp_session(page)
    cdp.send('Emulation.setTouchEmulationEnabled',{'enabled':True,'maxTouchPoints':1})
    cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':start[0],'y':start[1]}]})
    for i in range(1,7):
        cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':start[0]-i*45,'y':start[1]-i*18}]})
    cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
    assert dock.bounding_box()['x']<old['x']-100
    expect(page.locator('#wfe-page-guide .panel')).to_be_visible()


def test_collapse_keeps_observable_and_draggable_markers_with_separate_visibility(page):
    guide=page.locator('#wfe-page-guide');panel=guide.locator('.panel');dock=page.locator('#wfe-editor-dock')
    guide.locator('[data-command=hide]').click()
    expect(panel).to_be_hidden()
    expect(guide.locator('.pin[data-pin="root.0"]')).to_be_visible()
    expect(dock).to_contain_text('标记保留')
    guide.locator('.pin[data-pin="root.0"]').click()
    expect(panel).to_be_hidden()
    drag(page,guide.locator('.pin[data-pin="root.0"]'),*center(page.locator('#new')))
    assert workflow(page)[0]['selector']=='#new'
    expect(panel).to_be_hidden()
    assert page.evaluate('actualClicks')==[]
    page.evaluate('window.scrollBy(0,50)');page.wait_for_timeout(100)
    expect(guide.locator('.pin[data-pin="root.0"]')).to_be_visible()
    dock.click();expect(panel).to_be_visible()
    guide.locator('[data-show-markers]').uncheck()
    expect(guide.locator('.pin')).to_have_count(0)
    dock.click();dock.click()
    expect(guide.locator('[data-show-markers]')).not_to_be_checked()
    expect(guide.locator('.pin')).to_have_count(0)
    guide.locator('[data-show-markers]').check()
    expect(guide.locator('.pin[data-pin="root.0"]')).to_be_visible()
    page.evaluate('WorkflowEditor.hide()') # Legacy API still means hide everything.
    expect(panel).to_be_hidden();expect(guide.locator('.pin')).to_have_count(0)


@pytest.mark.parametrize('viewport',[(1320,1080),(808,650),(390,650),(700,360)])
def test_guide_single_scroll_no_clipped_footer_or_overlapping_explanation(page,viewport):
    page.set_viewport_size({'width':viewport[0],'height':viewport[1]})
    guide=page.locator('#wfe-page-guide');panel=guide.locator('.panel')
    expect(guide.locator('.list')).to_be_visible();expect(guide.locator('.detail')).to_be_hidden()
    def check():
        geometry=panel.evaluate("""p=>{const root=p.getRootNode(),box=p.getBoundingClientRect(),body=root.querySelector('.guide-body').getBoundingClientRect(),pager=root.querySelector('.pager').getBoundingClientRect(),footer=root.querySelector('.bottom').getBoundingClientRect();return {bottom:box.bottom,top:box.top,bodyBottom:body.bottom,pagerTop:pager.top,footerBottom:footer.bottom,extra:p.scrollHeight-p.clientHeight}}""")
        assert geometry['top']>=0 and geometry['bottom']<=viewport[1]+1
        assert geometry['bodyBottom']<=geometry['pagerTop']+1
        assert geometry['footerBottom']<=geometry['bottom']+1
        assert geometry['extra']<=1
        expect(guide.locator('[data-command=save]')).to_be_in_viewport()
        expect(guide.locator('[data-command=advanced]')).to_be_in_viewport()
    check()
    guide.locator('[data-command=detail-view]').click()
    expect(guide.locator('.detail')).to_be_visible();expect(guide.locator('.list')).to_be_hidden()
    guide.locator('.detail details').first.locator('summary').click();check()
    guide.locator('[data-command=list-view]').click();check()
    if viewport==(390,650):
        page.evaluate("document.documentElement.classList.add('dark')")
        page.wait_for_timeout(700)
        page.screenshot(path=str(ROOT/'test-artifacts/page-guide-compact-dark.png'))


def test_invalid_setting_returns_to_settings_before_save(page):
    guide=page.locator('#wfe-page-guide')
    guide.locator('[data-select="root.2"].select').click()
    guide.locator('[data-command=detail-view]').click()
    guide.locator('[data-edit=wait]').fill('-1')
    guide.locator('[data-command=list-view]').click()
    guide.locator('[data-command=save]').click()
    expect(guide.locator('.detail')).to_be_visible()
    assert guide.locator('[data-edit=wait]').evaluate('e=>!e.checkValidity()')
    assert page.evaluate('(window.__WORKFLOW_EDITOR_PENDING_ACTIONS__||[]).length')==0
    assert workflow(page)[2]['value']==.5
