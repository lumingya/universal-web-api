"""Playwright regression: dashboard + injected studio. API writes are intercepted.
Run: python tests/browser_workflow_studio.py (local dev server on port 8000).
No login, external AI request, or configuration mutation is performed.
"""
import asyncio
import hashlib
import json
import re
import urllib.request
from pathlib import Path
from playwright.async_api import async_playwright, expect
ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8000'
DOMAIN = 'aistudio.google.com'

async def main():
    before = hashlib.sha256((ROOT/'config/sites.json').read_bytes()).hexdigest()
    sample = json.loads((ROOT/'docs/examples/state-aware-workflow.json').read_text())
    site = json.load(urllib.request.urlopen(BASE+'/api/config/'+DOMAIN))
    site['presets']['主预设'].update(sample)
    all_sites = json.load(urllib.request.urlopen(BASE+'/api/config'))
    all_sites[DOMAIN] = site
    writes, errors = [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width':1512,'height':1500})
        page.on('pageerror', lambda e: errors.append(str(e)))
        await page.add_init_script("localStorage.setItem('uwa_onboarding_v1','1');localStorage.setItem('darkMode','false');localStorage.setItem('dashboard_nav_collapsed','false');")
        async def routes(route):
            req=route.request
            if req.method=='GET' and req.url.split('?')[0].endswith('/api/config'): return await route.fulfill(json=all_sites)
            if req.url.split('?')[0].endswith('/api/config/'+DOMAIN): return await route.fulfill(json=site)
            if req.method not in ('GET','HEAD') and '/api/workflow/' not in req.url:
                writes.append((req.url, req.post_data_json))
                return await route.fulfill(json={'success':True,'status':'success'})
            await route.continue_()
        await page.route('**/api/**', routes)
        await page.route('**/health',lambda route:route.fulfill(json={'status':'degraded','browser_connected':False}))
        await page.goto(BASE,wait_until='load')
        dismiss=page.get_by_role('button',name='暂不需要',exact=True)
        if await dismiss.count(): await dismiss.click()
        await page.get_by_role('button',name='站点配置',exact=True).click()
        await page.get_by_role('button',name='编辑 Google AI Studio',exact=True).click()
        await expect(page.get_by_role('combobox',name='当前预设',exact=True)).to_be_enabled(timeout=20000)
        await page.locator('.uwa-config-section-nav').get_by_role('button',name=re.compile('工作流')).click()
        studio=page.locator('.uwa-workflow-panel').locator('section.studio')
        await expect(studio).to_be_visible()
        assert not writes,'Opening structured editor performed a write'
        await expect(studio.locator('.node')).to_have_count(8)
        await studio.locator('.node[data-path="root.2"]').click()
        await expect(studio.get_by_text('左值 · 可引用 {变量}',exact=True)).to_be_visible()
        await studio.get_by_role('button',name='检查流程',exact=True).click()
        await expect(studio.get_by_role('status')).to_contain_text('结构检查通过',timeout=30000)
        await studio.get_by_role('button',name='检查与试跑',exact=True).click()
        await studio.locator('[data-test=model]').fill('gemini 3.1 Pro')
        await studio.locator('[data-sample-key=current]').fill('Pro')
        await studio.get_by_role('button',name='模拟分支路径',exact=True).click()
        await expect(studio.locator('.trace')).to_contain_text('条件不成立',timeout=30000)
        assert await studio.locator('.trace').get_by_text('计划执行',exact=True).count()==3
        await studio.get_by_role('button',name='流程画布',exact=True).click()
        await page.screenshot(path=str(ROOT/'test-artifacts/workflow-studio-light.png'),full_page=True)
        # Edits are reflected in the parent save payload, including nested nodes.
        await studio.locator('.node[data-path="root.2.then.0"]').click()
        await studio.locator('[data-field="label"]').fill('仅在必要时打开菜单')
        await page.locator('.studio-save-button').click()
        saved=[payload for url,payload in writes if url.endswith('/api/config')][-1]
        preset=saved['config'][DOMAIN]['presets']['主预设']
        assert preset['workflow'][2]['value']['then'][0]['label']=='仅在必要时打开菜单'
        assert preset['workflow'][2]['value']['else']==[]
        await page.evaluate("document.documentElement.classList.add('dark')")
        await page.screenshot(path=str(ROOT/'test-artifacts/workflow-studio-dark.png'),full_page=True)
        await page.set_viewport_size({'width':390,'height':844})
        await page.screenshot(path=str(ROOT/'test-artifacts/workflow-studio-mobile.png'),full_page=True)
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
        print('PASS dashboard: shared tree, nested save, simulated branch, light/dark/mobile')
        # A synthetic remote page, with no outbound network; exercise the real injected bundle.
        remote = await browser.new_page(viewport={'width':1440,'height':1080})
        remote.on('pageerror',lambda e: errors.append(str(e)))
        await remote.route('**/*', lambda route: route.fulfill(content_type='text/html',body='<html><body><button data-current-mode>Pro</button><textarea></textarea></body></html>'))
        await remote.goto('https://example.com/editor-test')
        await remote.evaluate('(config)=>{window.__WORKFLOW_EDITOR_CONFIG__=config;window.__WORKFLOW_EDITOR_TARGET_DOMAIN__="example.com";window.__WORKFLOW_EDITOR_PRESET_NAME__="主预设";}', sample)
        await remote.add_script_tag(content=(ROOT/'static/js/workflow-studio.js').read_text()+'\n'+(ROOT/'static/js/workflow-page-guide.js').read_text()+'\n'+(ROOT/'static/js/workflow-editor-inject.js').read_text())
        await expect(remote.locator('#wfe-page-guide .panel')).to_be_visible()
        await remote.locator('#wfe-page-guide [data-command=advanced]').click()
        injected=remote.locator('#wfe-tree-studio')
        await expect(injected).to_be_visible()
        assert await remote.evaluate('window.WorkflowEditor.getSteps()')==sample['workflow']
        await injected.locator('.node[data-path="root.2.then.0"]').click()
        await injected.get_by_text('整理流程结构',exact=True).click()
        await injected.locator('[data-cmd=wrap][data-action=GROUP]').click()
        assert (await remote.evaluate('window.WorkflowEditor.getSteps()'))[2]['value']['then'][0]['action']=='GROUP'
        await injected.locator('[data-cmd=undo]').click()
        assert await remote.evaluate('window.WorkflowEditor.getSteps()')==sample['workflow']
        await injected.get_by_role('button',name='保存配置',exact=True).click()
        actions=await remote.evaluate('window.__WORKFLOW_EDITOR_PENDING_ACTIONS__')
        assert actions[-1]['payload']['workflow']==sample['workflow']
        # Backend bridge result paints failure trace, without erasing the tree.
        await remote.evaluate('window.WorkflowEditor.handleBackendResult("test",false,"模拟回推失败轨迹",[{path:"root.2.then.0",action:"CLICK",status:"failed"}])')
        await expect(injected.locator('.trace')).to_contain_text('失败')
        assert await remote.evaluate('window.WorkflowEditor.getSteps()')==sample['workflow']
        await injected.get_by_role('button',name='流程画布',exact=True).click()
        await injected.locator('.node[data-path="root.2"]').click()
        await remote.screenshot(path=str(ROOT/'test-artifacts/workflow-studio-injected.png'))
        await remote.evaluate('window.WorkflowEditor.destroy()')
        assert await remote.locator('#wfe-tree-studio').count()==0
        assert not errors,errors
        await browser.close()
    assert before==hashlib.sha256((ROOT/'config/sites.json').read_bytes()).hexdigest(),'Config changed'
    print('PASS injected editor: lossless round-trip, save bridge, failure trace, destroy; no config changes; no page errors')

if __name__=='__main__':asyncio.run(main())
