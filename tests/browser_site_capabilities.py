"""Optional live-app capability smoke test. Requires the default Google AI Studio site.
Run against a local development server. All write APIs are intercepted; config SHA
is compared before/after. No third-party account or AI request is used.
"""
import asyncio, re, json, hashlib, urllib.request, os
from pathlib import Path
from playwright.async_api import async_playwright, expect
ROOT=Path(__file__).resolve().parents[1];DOMAIN='aistudio.google.com'
BASE_URL=os.environ.get('UWA_BASE_URL','http://127.0.0.1:8000').rstrip('/')
async def main():
 before=hashlib.sha256((ROOT/'config/sites.json').read_bytes()).hexdigest()
 site=json.load(urllib.request.urlopen(BASE_URL+'/api/config/'+DOMAIN))
 writes=[];errors=[]
 async with async_playwright() as p:
  b=await p.chromium.launch();page=await b.new_page(viewport={'width':1512,'height':1100})
  await page.add_init_script("localStorage.setItem('darkMode','false');localStorage.setItem('dashboard_nav_collapsed','false');")
  async def route_api(route):
   req=route.request;url=req.url.split('?')[0]
   if req.method in ('GET','HEAD'):
    if url.endswith('/api/config/'+DOMAIN):return await route.fulfill(json=site)
    return await route.continue_()
   data=req.post_data_json;writes.append((url,data))
   if url.endswith('/advanced-config'):
    obj=site['presets'][data['preset_name']] if data.get('preset_name') else site
    obj.setdefault('advanced',{}).update({k:v for k,v in data.items() if k!='preset_name'})
   return await route.fulfill(json={'status':'success','advanced':data})
  await page.route('**/api/**',route_api)
  page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto(BASE_URL,wait_until='load')
  await page.get_by_role('button',name='暂不需要',exact=True).click()
  await page.get_by_role('button',name='站点配置',exact=True).click()
  await page.get_by_role('button',name='编辑 Google AI Studio',exact=True).click()
  await expect(page.get_by_role('combobox',name='当前预设',exact=True)).to_be_enabled(timeout=20000)
  nav=page.locator('.uwa-config-section-nav')
  async def section(label):await nav.get_by_role('button',name=re.compile(label)).click()
  async def save():
   async with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/api/config')):
    await page.locator('.studio-save-button').click()
   await expect(page.locator('.studio-save-button')).to_be_enabled()
   return json.loads(json.dumps(writes[-1][1]))
  baseline=await save();count=len(writes)
  for label in ['输入处理','媒体提取','高级功能','响应解析']:
   await section(label)
   assert await page.locator('.site-config-content input:not([type=checkbox]):visible,.site-config-content select:visible,.site-config-content textarea:visible').count()==0,label
   details=page.locator('.site-config-content details[class*="cap-"]')
   opened=[]
   for i in range(await details.count()):
    summary=details.nth(i).locator(':scope > summary')
    if await summary.is_visible():await summary.click();opened.append(i)
   assert opened,label
   for i in reversed(opened):await details.nth(i).locator(':scope > summary').click()
  assert len(writes)==count, writes[count:]
  assert await save()==baseline,'Disclosing settings changed the full-save payload'
  print('PASS four modules start with zero parameter inputs; all disclosure levels open/close without writes or payload changes')
  # Accessibility: native summary keyboard operation, preserved DOM and editable drafts.
  await section('输入处理')
  summary=page.get_by_text('调整文件限制',exact=True).locator('xpath=ancestor::summary')
  await summary.focus();await page.keyboard.press('Enter')
  field=page.get_by_label('一次最多几个文件');await expect(field).to_be_visible()
  await field.fill('7');await field.evaluate("e=>e.dataset.retained='yes'")
  await summary.focus();await page.keyboard.press('Space');await expect(field).not_to_be_visible()
  await section('媒体提取');await section('输入处理')
  await summary.focus();await page.keyboard.press('Enter')
  await expect(field).to_have_value('7');assert await field.get_attribute('data-retained')=='yes'
  config=(await save())['config'];assert config[DOMAIN]['presets']['主预设']['file_paste']['attachments']['max_count']==7
  print('PASS keyboard disclosure, draft DOM retention across sections, and actual attachment full-save payload')
  # Cookie risk confirmation must cancel cleanly, with no save call.
  await section('高级功能');cookie=page.get_by_role('switch',name='隔离登录会话',exact=True)
  assert not await cookie.is_checked();count=len(writes)
  page.once('dialog',lambda d:asyncio.create_task(d.dismiss()))
  await cookie.click();await expect(cookie).not_to_be_checked();assert len(writes)==count
  page.once('dialog',lambda d:asyncio.create_task(d.accept()))
  async with page.expect_response(lambda r:r.request.method=='PUT' and r.url.endswith('/advanced-config')):await cookie.check()
  await expect(cookie).to_be_enabled();await expect(cookie).to_be_checked()
  assert writes[-1][1]['independent_cookies'] is True and 'preset_name' not in writes[-1][1]
  for label,key in [('等待输入框就绪','input_box_stability_wait_enabled'),('发送失败时自动恢复','send_confirmation_check_enabled')]:
   control=page.get_by_role('switch',name=label,exact=True)
   await expect(control).to_be_enabled()
   async with page.expect_response(lambda r:r.request.method=='PUT' and r.url.endswith('/advanced-config')):await control.check()
   await expect(control).to_be_enabled();await expect(control).to_be_checked()
   assert writes[-1][1][key] is True and writes[-1][1]['preset_name']=='主预设'
  print('PASS Cookie cancel/confirm and site scope; both preset advanced switches save immediately with correct scope')
  # All media switches use the original API and enum values, not localized display strings.
  await section('媒体提取')
  for label,kind in [('接收图片','image'),('接收音频','audio'),('接收视频','video')]:
   control=page.get_by_role('switch',name=label,exact=True)
   for desired in [not await control.is_checked(),await control.is_checked()]:
    async with page.expect_response(lambda r:r.request.method=='PUT' and r.url.endswith('/image-config')):await control.set_checked(desired)
    data=writes[-1][1]
    assert data['preset_name']=='主预设' and data['modalities'][kind]['enabled']==desired
    assert data['modalities'][kind]['run_policy'] in ['disabled','generic_only','on_signal','probe_if_trigger_found','always_probe']
  await section('响应解析');network=page.get_by_role('switch',name='从网络读取回复',exact=True)
  async with page.expect_response(lambda r:r.request.method=='PUT' and r.url.endswith('/stream-config')):await network.uncheck()
  assert writes[-1][1]['mode']=='dom'
  async with page.expect_response(lambda r:r.request.method=='PUT' and r.url.endswith('/stream-config')):await network.check()
  assert writes[-1][1]['mode']=='network'
  print('PASS three media switches and network/DOM switch hit original endpoints with original enum values')
  for width in [1280,1024,768,390,375]:
   await page.set_viewport_size({'width':width,'height':1000})
   for key,label in [('input','输入处理'),('media','媒体提取'),('advanced','高级功能'),('response','响应解析')]:
    mobile=page.get_by_role('combobox',name='移动端配置分类')
    if await mobile.is_visible():await mobile.select_option(key)
    else:await section(label)
    dims=await page.locator('.dashboard-scroll-region').evaluate('e=>[e.clientWidth,e.scrollWidth]')
    assert dims[1]<=dims[0]+1,(width,label,dims)
  assert not errors,errors
  await b.close()
 assert before==hashlib.sha256((ROOT/'config/sites.json').read_bytes()).hexdigest()
 print('PASS four modules at five widths; zero uncaught JS errors; user configuration SHA unchanged; ALL write APIs intercepted')
if __name__=='__main__':
 asyncio.run(main())
