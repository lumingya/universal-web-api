"""Dashboard smoke test for upgraded shipped configuration; requires local :8000.
API writes are intercepted; no AI login or provider traffic.
"""
import re,hashlib
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
root=Path(__file__).resolve().parents[1];before=hashlib.sha256((root/'config/sites.json').read_bytes()).hexdigest();errors=[];writes=[]
with sync_playwright() as p:
 b=p.chromium.launch();page=b.new_page(viewport={'width':1512,'height':1120})
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.add_init_script("localStorage.setItem('uwa_onboarding_v1','1');localStorage.setItem('darkMode','false');localStorage.setItem('dashboard_nav_collapsed','false');")
 def guard(route):
  if route.request.method not in ('GET','HEAD'):
   writes.append(route.request.url);return route.fulfill(json={'success':True})
  route.continue_()
 page.route('**/api/**',guard)
 page.route('**/health',lambda route:route.fulfill(json={'status':'degraded','browser_connected':False}))
 page.goto('http://127.0.0.1:8000',wait_until='load')
 dismiss=page.get_by_role('button',name='暂不需要',exact=True)
 if dismiss.count():dismiss.click()
 page.get_by_role('button',name='站点配置',exact=True).click()
 expect(page.locator('.site-discovery-note')).to_be_visible()
 assert page.get_by_role('button',name='编辑 Google',exact=True).count()==0
 page.locator('.site-card-open').filter(has=page.get_by_text('gemini.google.com',exact=True)).click()
 preset=page.get_by_role('combobox',name='当前预设',exact=True);expect(preset).to_be_enabled(timeout=20000)
 expect(preset).to_have_value('3.7flash')
 expect(preset.locator('option')).to_have_count(4)
 page.locator('.uwa-config-section-nav').get_by_role('button',name=re.compile('工作流')).click()
 studio=page.locator('.uwa-workflow-panel section.studio');expect(studio).to_be_visible()
 expect(studio.locator('.node-name').get_by_text('只在需要时切换模型',exact=True)).to_be_visible()
 assert studio.locator('.node-code[title=GROUP]').count()==3
 studio.locator('[data-cmd=expand-all]').click()
 studio.locator('.node-name').get_by_text('已匹配则保留，否则切换模型',exact=True).click()
 expect(studio.locator('[data-field="value.condition.op"]')).to_have_value('matches')
 page.wait_for_timeout(350) # Finish the inspector slide before recording the view.
 page.screenshot(path=str(root/'test-artifacts/gemini-upgraded-workflow.png'),full_page=True)
 assert not errors,errors
 assert not writes,writes
 b.close()
assert before==hashlib.sha256((root/'config/sites.json').read_bytes()).hexdigest()
print('PASS live dashboard: Google absent, admission note visible, actual Gemini grouped workflow loaded and editable; no page errors or config writes')
