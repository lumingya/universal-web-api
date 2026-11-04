"""Page-first visualization regression. All sites and clicks are synthetic/local.

No preview/navigation action may enqueue an execution, click a real page button,
or silently rewrite selectors. This suite complements the existing picker/save tests.
"""
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.test_workflow_page_guide import (  # noqa: E402,F401
    bundle, center, fixture_config, page, workflow,
)

pytestmark = pytest.mark.browser
ROOT = Path(__file__).resolve().parents[1]


def install(page, steps, selectors=None):
    page.evaluate("WorkflowEditor.destroy()")
    config = {"workflow": steps, "selectors": selectors or {}}
    page.evaluate("c=>window.__WORKFLOW_EDITOR_CONFIG__=c", config)
    page.add_script_tag(content=bundle())
    return page.locator("#wfe-page-guide")


def assert_no_execution(page):
    assert page.evaluate("actualClicks") == []
    assert page.evaluate("(window.__WORKFLOW_EDITOR_PENDING_ACTIONS__||[]).filter(a=>a.type==='test_workflow').length") == 0


def test_all_steps_remain_in_rail_and_panel_toggle_stays_in_sync(page):
    guide = page.locator("#wfe-page-guide")
    before = workflow(page)
    expect(guide.locator(".rail-chip")).to_have_count(9)
    expect(guide.locator('[data-row="root.2"] .row-status')).to_contain_text("无点击位置")
    expect(guide.locator('.pin[data-pin="root.0"] .pin-name')).to_contain_text("打开当前模式")
    guide.locator('[data-command="hide"]').click()
    expect(guide.locator(".panel")).to_be_hidden()
    expect(guide.locator(".step-rail")).to_be_visible()
    guide.locator('.rail-chip[data-select="root.2"]').click()
    expect(guide.locator(".rail-context")).to_contain_text("没有点击位置")
    expect(guide.locator(".panel")).to_be_hidden()
    guide.locator('[data-command="rail-panel"]').click()
    expect(guide.locator(".panel")).to_be_visible()
    expect(page.locator("#wfe-editor-dock")).to_contain_text("收起面板")
    page.locator("#wfe-editor-dock").click()
    expect(guide.locator(".panel")).to_be_hidden()
    assert workflow(page) == before
    assert_no_execution(page)


def test_partially_visible_large_target_does_not_disappear(page):
    page.evaluate("""() => {
      const target=document.createElement('section');target.id='huge-reply';
      target.style.cssText='position:absolute;top:920px;left:100px;width:550px;height:1800px';
      document.body.append(target);
    }""")
    guide = install(page, [{"action": "STREAM_WAIT", "selector": "#huge-reply"}])
    expect(guide.locator('[data-row="root.0"]')).to_have_attribute("data-location", "visible")
    pin = guide.locator('.pin[data-pin="root.0"]')
    expect(pin).to_be_visible()
    anchor = float(pin.get_attribute("data-anchor-y"))
    assert 920 <= anchor <= 1080
    expect(guide.locator('[data-outline="root.0"]')).to_be_visible()
    assert_no_execution(page)


def test_screen_edge_badge_locates_offscreen_target_without_clicking(page):
    guide = install(page, [{"action": "CLICK", "selector": "#bottom", "label": "底部目标"}])
    expect(guide.locator('[data-row="root.0"]')).to_have_attribute("data-location", "offscreen")
    edge = guide.locator('.edge-pin[data-select="root.0"]')
    expect(edge).to_be_visible()
    expect(edge).to_contain_text("屏外")
    assert edge.get_attribute("data-pin") is None  # An edge hint is not the actual drag/drop point.
    edge.click()
    expect(guide.locator('[data-row="root.0"]')).to_have_attribute("data-location", "visible")
    expect(guide.locator('.pin[data-pin="root.0"]')).to_be_visible()
    assert_no_execution(page)


def test_nested_scroll_clipping_and_locate_use_the_actual_scroller(page):
    page.evaluate("""() => {
      const box=document.createElement('div');box.id='scrollbox';
      box.style.cssText='position:fixed;left:80px;top:150px;width:400px;height:160px;overflow:auto;background:white';
      box.innerHTML='<div style="height:550px;padding-top:260px"><button id="nested-target">内层目标</button></div>';
      document.body.append(box);
      document.querySelector('#nested-target').onclick=()=>actualClicks.push('nested');
    }""")
    guide = install(page, [{"action": "CLICK", "selector": "#nested-target"}])
    expect(guide.locator('[data-row="root.0"]')).to_have_attribute("data-location", "offscreen")
    guide.locator('.edge-pin[data-select="root.0"]').click()
    expect(guide.locator('[data-row="root.0"]')).to_have_attribute("data-location", "visible")
    assert page.locator("#scrollbox").evaluate("e=>e.scrollTop") > 0
    assert_no_execution(page)


def test_missing_hidden_dynamic_and_nonspatial_steps_have_honest_status(page):
    page.evaluate("document.querySelector('#old').hidden=true")
    steps = [
        {"action": "CLICK", "selector": "#not-yet-there"},
        {"action": "CLICK", "selector": "#old"},
        {"action": "CLICK", "selector": 'button[data-model="{desired}"]'},
        {"action": "WAIT", "value": 1.5},
        {"action": "KEY_PRESS", "value": "Ctrl+Enter"},
        {"action": "JS_EXEC", "value": "return true;"},
    ]
    guide = install(page, steps)
    for i, state in enumerate(["missing", "hidden", "dynamic", "nonspatial", "nonspatial", "nonspatial"]):
        expect(guide.locator(f'[data-row="root.{i}"]')).to_have_attribute("data-location", state)
    expect(guide.locator(".pin")).to_have_count(0)
    expect(guide.locator(".rail-chip")).to_have_count(6)
    expect(guide.locator(".location-summary")).to_contain_text("3 待定位")
    guide.locator('.rail-chip[data-select="root.0"]').click()
    expect(guide.locator(".notice")).to_contain_text("页面暂无目标")
    page.evaluate("""() => {
      const b=document.createElement('button');b.id='not-yet-there';b.textContent='后来出现';
      b.style.cssText='position:fixed;left:100px;top:100px';document.body.append(b);
      document.querySelector('#old').hidden=false;
    }""")
    expect(guide.locator('[data-row="root.0"]')).to_have_attribute("data-location", "visible")
    expect(guide.locator('[data-row="root.1"]')).to_have_attribute("data-location", "visible")
    assert workflow(page) == steps
    assert_no_execution(page)


def test_hidden_first_match_only_shows_explicit_preview_candidate(page):
    page.evaluate("""() => {
      const a=document.createElement('button');a.id='first-hidden';a.className='duplicate-match';a.hidden=true;
      document.body.prepend(a);document.querySelector('#new').classList.add('duplicate-match');
    }""")
    steps = [{"action": "CLICK", "selector": ".duplicate-match"}]
    guide = install(page, steps)
    assert page.evaluate("WorkflowPageGuide.resolve('.duplicate-match').element.id") == "first-hidden"
    expect(guide.locator('[data-row="root.0"] .row-status')).to_contain_text("首个匹配隐藏")
    expect(guide.locator(".pin.candidate")).to_contain_text("候选")
    guide.locator('[data-command="detail-view"]').click()
    expect(guide.locator(".target-status")).to_contain_text("不代表实际执行目标")
    assert workflow(page) == steps
    assert_no_execution(page)


def test_multiple_steps_at_same_target_do_not_stack_or_fall_off_edge(page):
    page.evaluate("document.querySelector('#old').style.cssText='position:fixed;left:6px;top:160px;width:100px'")
    steps = [{"action": "CLICK", "selector": "#old", "label": f"同一按钮步骤 {i+1}"} for i in range(12)]
    guide = install(page, steps)
    pins = guide.locator(".pin[data-pin]")
    expect(pins).to_have_count(12)
    boxes = [pins.nth(i).bounding_box() for i in range(12)]
    for i, a in enumerate(boxes):
        assert a["x"] >= 0 and a["y"] >= 0
        assert a["x"] + a["width"] <= 1321
        for b in boxes[i+1:]:
            width = min(a["x"]+a["width"], b["x"]+b["width"]) - max(a["x"], b["x"])
            height = min(a["y"]+a["height"], b["y"]+b["height"]) - max(a["y"], b["y"])
            assert width <= 0 or height <= 0
    assert workflow(page) == steps
    assert_no_execution(page)


def test_marker_placement_avoids_inspector_and_reveals_covered_target(page):
    page.evaluate("document.querySelector('#old').style.cssText='position:fixed;left:1060px;top:180px;width:120px'")
    guide = install(page, [{"action": "CLICK", "selector": "#old"}])
    pin = guide.locator('.pin[data-pin="root.0"]')
    expect(pin).to_be_visible()
    box = pin.bounding_box()
    p = guide.locator(".panel").bounding_box()
    assert box["x"]+box["width"] <= p["x"] or box["x"] >= p["x"]+p["width"] or box["y"] >= p["y"]+p["height"] or box["y"]+box["height"] <= p["y"]
    guide.locator('.select[data-select="root.0"]').click()
    p = guide.locator(".panel").bounding_box()
    target = page.locator("#old").bounding_box()
    assert p["x"]+p["width"] <= target["x"]
    assert_no_execution(page)


def test_branch_links_never_fabricate_a_then_to_else_execution(page):
    guide = page.locator("#wfe-page-guide")
    paths = guide.locator(".route-path").evaluate_all("es=>es.map(e=>({from:e.dataset.from,to:e.dataset.to}))")
    assert paths
    for link in paths:
        assert link["from"].rsplit(".", 1)[0] == link["to"].rsplit(".", 1)[0]
    assert not any("then" in x["from"] and "else" in x["to"] for x in paths)
    guide.locator("[data-show-links]").uncheck()
    expect(guide.locator(".route-path")).to_have_count(0)
    guide.locator("[data-show-labels]").uncheck()
    expect(guide.locator(".pin-name")).to_have_count(0)
    expect(guide.locator(".rail-chip")).to_have_count(9)
    assert_no_execution(page)


def test_transformed_body_does_not_offset_overlay_coordinates(page):
    page.evaluate("document.body.style.transform='translate(34px, 22px)'")
    guide = install(page, [{"action": "CLICK", "selector": "#old"}])
    assert guide.evaluate("e=>e.parentElement===document.documentElement")
    actual = page.locator("#old").bounding_box()
    outline = guide.locator('[data-outline="root.0"]').bounding_box()
    assert abs(outline["x"]-actual["x"]) < 1
    assert abs(outline["y"]-actual["y"]) < 1
    assert_no_execution(page)


def test_rail_and_all_interactive_markers_hide_during_real_test_and_full_hide(page):
    guide = page.locator("#wfe-page-guide")
    page.evaluate("WorkflowEditor.hide()")
    expect(guide.locator(".step-rail")).to_be_hidden()
    expect(guide.locator(".pin")).to_have_count(0)
    page.evaluate("WorkflowEditor.showPage()")
    expect(guide.locator(".step-rail")).to_be_visible()
    guide.locator('[data-command="test"]').click()
    page.on("dialog", lambda d: d.accept())
    page.locator('#wfe-tree-studio [data-cmd="test"]').click()
    expect(guide.locator(".step-rail")).to_be_hidden()
    expect(guide.locator(".pin")).to_have_count(0)
    action = page.evaluate("window.__WORKFLOW_EDITOR_PENDING_ACTIONS__.at(-1)")
    page.evaluate("id=>WorkflowEditor.handleBackendResult(id,false,'本地模拟结束',[])", action["id"])
    page.evaluate("WorkflowEditor.showPage()")
    expect(guide.locator(".step-rail")).to_be_visible()
    assert page.evaluate("actualClicks") == []


@pytest.mark.parametrize("viewport", [(390,650),(700,360),(1320,1080)])
def test_collapsed_overview_is_usable_at_small_sizes(page, viewport):
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    guide = page.locator("#wfe-page-guide")
    before = workflow(page)
    guide.locator('[data-command="hide"]').click()
    rail = guide.locator(".step-rail")
    expect(rail).to_be_visible()
    expect(guide.locator(".rail-chip")).to_have_count(9)
    box = rail.bounding_box()
    assert box["x"] >= 0 and box["x"]+box["width"] <= viewport[0]+1
    assert box["y"] >= 0 and box["y"]+box["height"] <= viewport[1]+1
    guide.locator('.rail-chip[data-select="root.6"]').click()
    expect(guide.locator(".rail-context")).to_contain_text("读取回复")
    expect(guide.locator(".panel")).to_be_hidden()
    guide.locator('[data-command="rail-edit"]').click()
    expect(guide.locator(".detail")).to_be_visible()
    assert workflow(page) == before
    assert_no_execution(page)
    if viewport == (1320,1080):
        guide.locator('[data-command="hide"]').click()
        (ROOT / "test-artifacts").mkdir(exist_ok=True)
        page.screenshot(path=str(ROOT / "test-artifacts/workflow-overview-collapsed.png"))


def test_dense_workflow_reports_folded_markers_and_prioritizes_selection(page):
    steps = [{"action": "CLICK", "selector": "#old", "label": f"重复动作 {i+1}"} for i in range(140)]
    guide = install(page, steps)
    expect(guide.locator(".rail-chip")).to_have_count(140)
    assert int(guide.locator(".step-rail").get_attribute("data-folded")) > 0
    guide.locator('.rail-chip[data-select="root.139"]').click()
    expect(guide.locator('.pin.active[data-pin="root.139"]')).to_be_visible()
    assert workflow(page) == steps
    assert_no_execution(page)
