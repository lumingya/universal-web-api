"""Direct manipulation on the page rail. Synthetic pages; no user browser or site writes."""
import json
from pathlib import Path

import pytest
pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect  # noqa: E402
from tests.test_workflow_page_guide import page, drag, center, workflow, bundle, fixture_config  # noqa: E402,F401
from tests.test_workflow_page_overview import install, assert_no_execution  # noqa: E402

pytestmark = pytest.mark.browser


def palette(guide, action):
    guide.locator('[data-command=rail-add]').click()
    return guide.locator(f'.rail-palette [data-new-action="{action}"]')


def test_rail_can_move_collapse_hide_restore_without_changing_workflow(page):
    guide=page.locator('#wfe-page-guide');rail=guide.locator('.step-rail');before=workflow(page)
    old=rail.bounding_box();drag(page,rail.locator('.rail-grip'),620,230);new=rail.bounding_box()
    assert abs(old['y']-new['y'])>100 and new['x']>=0
    expect(rail.locator('.rail-track')).to_be_visible()
    rail.locator('[data-command=rail-collapse]').click()
    expect(rail.locator('.rail-track')).to_be_hidden()
    expect(rail.locator('.rail-head')).to_be_visible()
    drag(page,rail.locator('.rail-grip'),260,130)
    expect(rail.locator('.rail-track')).to_be_hidden()
    rail.locator('[data-command=rail-hide]').click();expect(rail).to_be_hidden()
    expect(guide.locator('[data-show-rail]')).not_to_be_checked()
    guide.locator('[data-show-rail]').check();expect(rail).to_be_visible()
    rail.locator('[data-command=rail-collapse]').click();expect(rail.locator('.rail-track')).to_be_visible()
    assert workflow(page)==before
    assert_no_execution(page)


def test_drag_new_element_creates_one_step_with_unique_binding_and_one_undo(page):
    guide=page.locator('#wfe-page-guide');before=workflow(page)
    drag(page,guide.locator('.rail-tools [data-new-action=CLICK]'),*center(page.locator('#new')))
    after=workflow(page)
    assert len(after)==len(before)+1
    assert after[1]['action']=='CLICK' and after[1]['selector']=='#new' and after[1]['flow_version']==2
    assert after[0]==before[0] and after[2:]==before[1:]
    expect(guide.locator('.rail-editor')).to_be_visible()
    expect(guide.locator('.pin[data-pin="root.1"]')).to_be_visible()
    guide.locator('[data-command=rail-undo]').click();assert workflow(page)==before
    guide.locator('[data-command=rail-redo]').click();assert workflow(page)==after
    assert_no_execution(page)


def test_drag_new_coordinate_does_not_require_a_dom_element(page):
    guide=page.locator('#wfe-page-guide');before=workflow(page)
    drag(page,guide.locator('.rail-tools [data-new-action=COORD_CLICK]'),900,750)
    n=workflow(page)[1]
    assert n['action']=='COORD_CLICK' and n['value']=={'x':900,'y':750,'random_radius':0}
    assert workflow(page)[2:]==before[1:]
    guide.locator('[data-rail-field=coord-x]').fill('810')
    assert workflow(page)[1]['value']['x']==810
    assert_no_execution(page)


def test_drag_blank_dom_target_and_escape_do_not_add_partial_steps(page):
    guide=page.locator('#wfe-page-guide');before=workflow(page);handle=guide.locator('.rail-tools [data-new-action=CLICK]')
    drag(page,handle,900,750)
    assert workflow(page)==before
    expect(guide.locator('.rail-note')).to_contain_text('没有新增步骤')
    page.mouse.move(*center(handle));page.mouse.down();page.mouse.move(*center(page.locator('#new')),steps=8)
    page.keyboard.press('Escape');page.mouse.up()
    assert workflow(page)==before
    expect(guide.locator('.add-ghost')).to_have_count(0)
    assert_no_execution(page)


def test_click_palette_then_pick_adds_without_activating_target(page):
    guide=page.locator('#wfe-page-guide');before=workflow(page)
    guide.locator('.rail-tools [data-new-action=CLICK]').click()
    expect(guide.locator('.pick-surface')).to_be_visible()
    assert workflow(page)==before
    page.mouse.click(*center(page.locator('.same').nth(1)))
    added=workflow(page)[1]
    assert page.locator(added['selector']).count()==1
    assert page.locator(added['selector']).inner_text()=='重复按钮 B'
    assert_no_execution(page)


def test_scroll_creation_is_transactional_until_both_endpoints_exist(page):
    guide=page.locator('#wfe-page-guide');before=workflow(page)
    palette(guide,'COORD_SCROLL').click();page.mouse.click(770,330)
    expect(guide.locator('.pick-tip')).to_contain_text('终点')
    assert workflow(page)==before
    page.keyboard.press('Escape');assert workflow(page)==before
    palette(guide,'COORD_SCROLL').click();page.mouse.click(770,330);page.mouse.click(780,450)
    assert workflow(page)[1]['value']=={'start_x':770,'start_y':330,'end_x':780,'end_y':450}
    assert_no_execution(page)


def test_add_wait_validate_edit_and_save_receipt(page):
    guide=page.locator('#wfe-page-guide')
    palette(guide,'WAIT').click()
    expect(guide.locator('[data-rail-field=wait]')).to_have_value('0.5')
    guide.locator('[data-rail-field=wait]').fill('-1')
    guide.locator('[data-command=rail-save]').click()
    assert page.evaluate('(window.__WORKFLOW_EDITOR_PENDING_ACTIONS__||[]).length')==0
    assert workflow(page)[1]['value']==.5
    guide.locator('[data-rail-field=wait]').fill('2.5')
    guide.locator('[data-rail-field=label]').fill('等页面准备好')
    guide.locator('[data-command=rail-save]').click()
    action=page.evaluate('window.__WORKFLOW_EDITOR_PENDING_ACTIONS__.at(-1)')
    assert action['type']=='save_workflow'
    assert action['payload']['workflow']==workflow(page)
    assert action['payload']['selectors']==fixture_config()['selectors']
    assert action['payload']['preset_name']=='demo'
    expect(guide.locator('[data-command=rail-save]')).to_be_disabled()
    expect(guide.locator('.rail-save-state')).to_contain_text('提交中')
    page.evaluate('(id)=>WorkflowEditor.handleBackendResult(id,true,"保存成功")',action['id'])
    expect(guide.locator('[data-command=rail-save]')).to_be_enabled()
    expect(guide.locator('.rail-save-state')).to_have_text('已保存')
    assert_no_execution(page)


def test_save_failure_keeps_draft_and_allows_explicit_retry(page):
    guide=page.locator('#wfe-page-guide');palette(guide,'WAIT').click();before=workflow(page)
    guide.locator('[data-command=rail-save]').click()
    action=page.evaluate('window.__WORKFLOW_EDITOR_PENDING_ACTIONS__.at(-1)')
    page.evaluate('(id)=>WorkflowEditor.handleBackendResult(id,false,"模拟磁盘写入失败")',action['id'])
    expect(guide.locator('.rail-save-state')).to_contain_text('保存失败')
    expect(guide.locator('.rail-note')).to_contain_text('模拟磁盘写入失败')
    expect(guide.locator('[data-command=rail-save]')).to_be_enabled()
    assert workflow(page)==before
    assert_no_execution(page)


def test_edit_input_and_reorder_changes_draft_not_webpage(page):
    guide=page.locator('#wfe-page-guide');before=workflow(page)
    guide.locator('.rail-chip[data-select="root.1"] span').click()
    guide.locator('[data-command=rail-quickedit]').click()
    guide.locator('[data-rail-field=input]').fill('只修改流程里的正文')
    guide.locator('[data-rail-field=label]').fill('填写测试正文')
    assert workflow(page)[1]['value']=='只修改流程里的正文'
    assert page.locator('#prompt').input_value()==''
    guide.locator('[data-command=rail-down]').click()
    assert workflow(page)[2]['action']=='FILL_INPUT'
    guide.locator('[data-command=rail-up]').click()
    assert workflow(page)[1]['action']=='FILL_INPUT'
    assert workflow(page)[0]==before[0] and workflow(page)[3:]==before[3:]
    assert_no_execution(page)


def test_nested_add_delete_preserves_other_branch_and_unknown_fields(page):
    guide=page.locator('#wfe-page-guide');before=workflow(page)
    guide.locator('.rail-chip[data-select="root.3.then.0"] span').click()
    palette(guide,'WAIT').click()
    after=workflow(page)
    assert len(after[3]['value']['then'])==2
    assert after[3]['value']['then'][1]['action']=='WAIT'
    assert after[3]['value']['else']==before[3]['value']['else']
    assert after[0]==before[0]
    guide.locator('[data-command=rail-delete]').click()
    assert workflow(page)==before
    guide.locator('[data-command=rail-undo]').click();assert workflow(page)==after
    assert_no_execution(page)


def test_deleting_group_requires_confirmation_and_is_undoable(page):
    guide=page.locator('#wfe-page-guide');before=workflow(page)
    guide.locator('.rail-chip[data-select="root.3"] span').click()
    page.once('dialog',lambda d:d.dismiss());guide.locator('[data-command=rail-delete]').click()
    assert workflow(page)==before
    page.once('dialog',lambda d:d.accept());guide.locator('[data-command=rail-delete]').click()
    assert workflow(page)==before[:3]+before[4:]
    guide.locator('[data-command=rail-undo]').click();assert workflow(page)==before
    assert_no_execution(page)


def test_drag_existing_number_retargets_only_that_step(page):
    guide=page.locator('#wfe-page-guide');before=workflow(page)
    drag(page,guide.locator('.rail-chip[data-select="root.0"] b[data-pin]'),*center(page.locator('#new')))
    after=workflow(page)
    assert len(after)==len(before) and after[0]['selector']=='#new'
    assert after[3]['value']['then']==before[3]['value']['then']
    assert page.evaluate('window.__WORKFLOW_EDITOR_CONFIG__.selectors.shared')=='css:#old'
    assert_no_execution(page)


def test_empty_workflow_can_add_delete_and_undo(page):
    guide=install(page,[])
    palette(guide,'WAIT').click();assert workflow(page)[0]['action']=='WAIT'
    guide.locator('[data-command=rail-delete]').click();assert workflow(page)==[]
    expect(guide.locator('[data-command=rail-delete]')).to_be_disabled()
    guide.locator('[data-command=rail-undo]').click();assert workflow(page)[0]['action']=='WAIT'
    assert_no_execution(page)


def test_save_to_local_fixture_file_and_reload_preserves_added_binding(page,tmp_path):
    guide=page.locator('#wfe-page-guide');saved=tmp_path/'demo-workflow.json'
    def save_api(route):
        payload=route.request.post_data_json
        saved.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        route.fulfill(status=200,content_type='application/json',body='{"success":true}')
    page.route('**/api/sites/example.com/workflow',save_api)
    page.evaluate('window.__WORKFLOW_EDITOR_API_BASE__=location.origin')
    page.on('dialog',lambda d:d.accept())
    drag(page,guide.locator('.rail-tools [data-new-action=CLICK]'),*center(page.locator('#new')))
    expected=workflow(page);guide.locator('[data-command=rail-save]').click()
    expect(guide.locator('.rail-save-state')).to_have_text('已保存')
    stored=json.loads(saved.read_text(encoding='utf-8'))
    assert stored['workflow']==expected and stored['preset_name']=='demo'
    page.evaluate('WorkflowEditor.destroy()')
    page.evaluate('(c)=>window.__WORKFLOW_EDITOR_CONFIG__=c',stored)
    page.add_script_tag(content=bundle())
    assert workflow(page)==expected
    expect(page.locator('#wfe-page-guide .pin[data-pin="root.1"]')).to_be_visible()
    assert_no_execution(page)


@pytest.mark.parametrize('size',[(390,650),(700,360)])
def test_moved_and_collapsed_rail_stays_in_viewport_after_resize(page,size):
    guide=page.locator('#wfe-page-guide');rail=guide.locator('.step-rail')
    guide.locator('[data-command=hide]').click()
    drag(page,rail.locator('.rail-grip'),1100,400)
    page.set_viewport_size({'width':size[0],'height':size[1]});page.wait_for_timeout(150)
    box=rail.bounding_box()
    assert box['x']>=0 and box['x']+box['width']<=size[0]+1
    assert box['y']>=0 and box['y']+box['height']<=size[1]+1
    rail.locator('[data-command=rail-collapse]').click()
    expect(rail.locator('.rail-head')).to_be_visible()
    assert_no_execution(page)
