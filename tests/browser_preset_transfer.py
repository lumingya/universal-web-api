"""Real dashboard regression, API writes intercepted; requires local :8000 server.
Exercises closed/open motion, scoped JSON round-trip, draft management and mobile dialogs.
"""
import hashlib
import json
import re
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8000'
DOMAIN='aistudio.google.com'

def main():
    before=hashlib.sha256((ROOT/'config/sites.json').read_bytes()).hexdigest()
    sites=json.load(urllib.request.urlopen(BASE+'/api/config'))
    site=sites[DOMAIN]
    site['presets']={'主预设':json.loads((ROOT/'docs/examples/state-aware-workflow.json').read_text()),'其他预设':{'workflow':[],'selectors':{},'custom_marker':'untouched'}}
    site['default_preset']='主预设'
    site['transfer_test_site_field']='site-only'
    site['presets']['主预设']['custom_future_field']={'keep':['unknown',123]}
    errors=[]; writes=[]; reads=[]
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':1512,'height':1180})
        page.on('pageerror',lambda e: errors.append(str(e)))
        page.on('dialog',lambda dialog: dialog.accept())
        page.add_init_script("localStorage.setItem('uwa_onboarding_v1','1');localStorage.setItem('darkMode','false');localStorage.setItem('dashboard_nav_collapsed','false');")
        def route(r):
            req=r.request;url=req.url.split('?')[0]
            if req.method=='GET' and url.endswith('/api/config'):reads.append(url);return r.fulfill(json=sites)
            if req.method=='GET' and url.endswith('/api/config/'+DOMAIN):reads.append(url);return r.fulfill(json=site)
            if req.method=='GET' and url.endswith('/api/presets/'+DOMAIN):return r.fulfill(json={'presets':['主预设','其他预设'],'default_preset':'主预设'})
            if req.method not in ('GET','HEAD') and '/api/workflow/' not in url:writes.append((url,req.post_data_json));return r.fulfill(json={'success':True,'status':'success'})
            r.continue_()
        page.route('**/api/**',route)
        page.goto(BASE,wait_until='load')
        dismiss=page.get_by_role('button',name='暂不需要',exact=True)
        if dismiss.count():dismiss.click()
        page.get_by_role('button',name='站点配置',exact=True).click()
        page.get_by_role('button',name='编辑 Google AI Studio',exact=True).click()
        preset=page.get_by_role('combobox',name='当前预设',exact=True)
        expect(preset).to_be_enabled(timeout=20000)
        page.locator('.uwa-config-section-nav').get_by_role('button',name=re.compile('工作流')).click()
        studio=page.locator('.uwa-workflow-panel section.studio')
        workspace=studio.locator('.workspace');canvas=studio.locator('.canvas')
        expect(studio).to_be_visible()
        assert not studio.locator('h2').count()
        assert not workspace.evaluate("e=>e.classList.contains('is-inspecting')")
        closed=canvas.bounding_box();root=studio.locator('.sequence.root').bounding_box()
        assert abs((closed['x']+closed['width']/2)-(root['x']+root['width']/2))<2
        page.screenshot(path=str(ROOT/'test-artifacts/site-workflow-centered.png'),full_page=True)
        studio.locator('.node[data-path="root.2"]').click()
        expect(workspace).to_have_class(re.compile('is-inspecting'))
        workspace.evaluate("e=>Promise.all(e.getAnimations({subtree:true}).map(a=>a.finished))")
        opened=canvas.bounding_box()
        assert closed['width']-opened['width']>285
        expect(studio.locator('.inspector-close')).to_be_focused()
        page.screenshot(path=str(ROOT/'test-artifacts/site-workflow-settings.png'),full_page=True)
        studio.locator('.inspector-close').press('Escape')
        expect(studio.locator('.node[data-path="root.2"]')).to_be_focused()
        workspace.evaluate("e=>Promise.all(e.getAnimations({subtree:true}).map(a=>a.finished))")
        assert abs(canvas.bounding_box()['width']-closed['width'])<2
        assert studio.locator('.inspector-slot').get_attribute('inert') is not None
        # Download includes one complete preset, no sibling or site settings.
        menu=page.locator('.transfer-menu')
        def launch(label):
            menu.locator('summary').click();menu.get_by_role('button',name=re.compile(label)).click()
        menu.locator('summary').click()
        with page.expect_download() as download:
            menu.get_by_role('button',name=re.compile('导出当前预设')).click()
        exported=json.loads(Path(download.value.path()).read_text())
        assert list(exported['presets'])==['主预设']
        assert exported['presets']['主预设']['custom_future_field']=={'keep':['unknown',123]}
        assert 'transfer_test_site_field' not in exported
        dialog=page.get_by_role('dialog',name='导入一个预设',exact=True)
        launch('导入一个预设')
        expect(dialog.get_by_role('button',name=re.compile('选择配置 JSON'))).to_be_focused()
        def upload(data):
            page.locator('.preset-transfer-dialog input[type=file]').set_input_files({'name':'single-preset.json','mimeType':'application/json','buffer':json.dumps(data,ensure_ascii=False).encode()})
        upload(exported)
        expect(dialog.get_by_label('在本站点保存为')).to_have_value('主预设 · 导入')
        page.screenshot(path=str(ROOT/'test-artifacts/site-preset-import.png'))
        dialog.get_by_label('在本站点保存为').fill('迁入预设')
        dialog.get_by_role('button',name='导入为草稿',exact=True).click()
        expect(dialog).to_be_hidden(timeout=60000)
        expect(preset).to_have_value('迁入预设')
        assert not writes
        initial_reads=len(reads)
        preset.select_option('其他预设');preset.select_option('迁入预设')
        expect(studio.locator('.node')).to_have_count(8)
        assert len(reads)==initial_reads,'Switching preset reloaded and discarded draft'
        # Rename is direct and staged safely for a newly imported preset.
        page.get_by_role('button',name='重命名',exact=True).click()
        rename=page.get_by_role('textbox',name='新的预设名称',exact=True)
        expect(rename).to_be_focused();rename.fill('迁入 · 重命名');rename.press('Enter')
        expect(preset).to_have_value('迁入 · 重命名')
        assert not writes
        page.locator('.studio-save-button').click()
        expect(page.get_by_text('配置已保存',exact=True).first).to_be_visible()
        saved=[body['config'][DOMAIN] for url,body in writes if url.endswith('/api/config')][-1]
        assert set(saved['presets'])=={'主预设','其他预设','迁入 · 重命名'}
        assert saved['presets']['其他预设']['custom_marker']=='untouched'
        assert saved['presets']['迁入 · 重命名']['custom_future_field']==exported['presets']['主预设']['custom_future_field']
        assert saved['default_preset']=='主预设'
        # Invalid workflow is rejected before applying any change.
        launch('导入一个预设')
        upload({'presets':{'broken':{'workflow':[{'action':'SET','value':{'name':'x','value':1}},{'action':'NONEXISTENT'}]}}})
        dialog.get_by_role('button',name='导入为草稿',exact=True).click()
        expect(dialog.get_by_role('alert')).to_be_visible(timeout=60000)
        expect(preset).to_have_value('迁入 · 重命名')
        dialog.get_by_role('button',name='取消',exact=True).click()
        # Site merge skips collisions, preserving the default and shared fields.
        launch('合并站点配置')
        merged=page.get_by_role('dialog',name='合并站点配置',exact=True)
        upload({'domain':'example.org','default_preset':'新套件','advanced':{'site_transfer_marker':True},'presets':{'主预设':{'workflow':[]},'新套件':{'workflow':[],'unknown':'new'}}})
        expect(merged.get_by_text('2 个来源预设 · 1 个同名',exact=True)).to_be_visible()
        merged.get_by_role('button',name='导入为草稿',exact=True).click()
        expect(merged).to_be_hidden(timeout=60000)
        menu.locator('summary').click()
        with page.expect_download() as download:
            menu.get_by_role('button',name=re.compile('导出整个站点')).click()
        whole=json.loads(Path(download.value.path()).read_text())
        assert len(whole['presets']['主预设']['workflow'])>0
        assert whole['presets']['新套件']['unknown']=='new'
        assert whole['default_preset']=='主预设'
        assert not whole.get('advanced',{}).get('site_transfer_marker')
        assert whole['transfer_test_site_field']=='site-only'
        # Closing a slow validation must release UI and never apply its late result.
        pending=[]
        page.route('**/api/workflow/validate',lambda r: pending.append(r))
        launch('导入一个预设');upload(exported)
        dialog.get_by_label('在本站点保存为').fill('已取消，不应出现')
        dialog.get_by_role('button',name='导入为草稿',exact=True).click()
        expect(dialog.get_by_role('button',name='校验中…',exact=True)).to_be_visible()
        dialog.get_by_role('button',name='取消',exact=True).click()
        expect(dialog).to_be_hidden()
        for request in pending:
            request.abort()
        page.unroute('**/api/workflow/validate')
        assert not preset.locator('option[value="已取消，不应出现"]').count()
        # Small screen settings overlay and keyboard-safe, dark file picker.
        page.set_viewport_size({'width':390,'height':844})
        page.evaluate("document.documentElement.classList.add('dark')")
        studio.locator('.node[data-path="root.0"]').click()
        expect(studio.locator('.inspector-close')).to_be_visible()
        studio.locator('.inspector-close').click()
        launch('导入一个预设');upload(exported)
        page.screenshot(path=str(ROOT/'test-artifacts/site-preset-import-mobile-dark.png'))
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        dialog.get_by_role('button',name='取消',exact=True).click()
        assert not errors,errors
        browser.close()
    assert before==hashlib.sha256((ROOT/'config/sites.json').read_bytes()).hexdigest()
    print('PASS: centered/animated inspector, escape focus, single-preset round-trip, draft switch/rename/save, invalid-workflow rejection, non-destructive site merge, dark/mobile; no real config writes.')

if __name__=='__main__':main()
