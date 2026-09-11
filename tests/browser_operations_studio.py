"""Live-app UI smoke regression using SYNTHETIC pool and request-history fixtures.
All mutating APIs and clipboard writes are intercepted; no real tasks are cancelled.
Run with a local development server. Config JSON hashes must remain unchanged.
"""
import asyncio,json,time,re,hashlib,os
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
BASE_URL=os.environ.get('UWA_BASE_URL','http://127.0.0.1:8000').rstrip('/')
OUTPUT=Path(os.environ.get('UWA_SMOKE_OUTPUT',str(ROOT/'test-artifacts/operations')))
OUTPUT.mkdir(parents=True,exist_ok=True)
NOW=time.time()
TABS=[]
for i,(domain,model,status) in enumerate([('aistudio.google.com','Gemini · 主预设','idle'),('chatgpt.com','ChatGPT · 日常对话','busy'),('claude.ai','Claude · 写作助手','idle'),('grok.com','Grok · 搜索问答','error')],1):
 TABS.append(dict(persistent_index=i,current_domain=domain,route_domain=domain,url=f'https://{domain}/chat/session-{i}',url_route_token=f'urltoken{i}',domain_route_prefix=f'/url/{domain}',exact_url_route_prefix=f'/tab-url/urltoken{i}',id=f'tab-session-{i:03}',status=status,exposed_model_name=model,default_model_name=model,available_presets=['主预设','简洁回复'],default_preset='主预设',preset_name='主预设',effective_preset_name='主预设',is_using_default_preset=False,request_count=[128,86,52,19][i-1],busy_duration=38 if status=='busy' else 0,current_task='request-2409' if status=='busy' else '',route_groups=['writing'] if i in [1,3] else [],is_isolated_context=i==3))
POOL=dict(tabs=TABS,allocation_mode='first_idle',enabled_route_methods=['domain','route_group','fixed_tab','exact_url','exact_url_preset'],route_groups=[dict(id='writing',name='日常写作',allocation_mode='round_robin',members=[{'tab_index':1,'url':TABS[0]['url']},{'tab_index':3,'url':TABS[2]['url']}],live_member_count=2,idle_member_count=2)],excluded_urls=[],preserve_error_tabs=False,auto_remember_url_presets=False)
RECORDS=[]
for i in range(26):
 tab=TABS[i%4];status='failed' if i%7==1 else 'cancelled' if i%9==3 else 'completed'
 RECORDS.append(dict(id=f'req-{1000+i}',history_key=f'req-{1000+i}',model=tab['exposed_model_name'],target_domain=tab['current_domain'],preset_name='主预设',route_group='writing' if i%4==0 else '',tab_index=i%4+1,endpoint='/v1/chat/completions',request_type='chat',status=status,success=status=='completed',started_at=NOW-i*390,created_at=NOW-i*390,finished_at=NOW-i*390+24,duration_ms=2400+i*213,queue_ms=100,generation_ms=2300+i*213,token_estimate={'prompt':1200+i*21,'response':680+i*19},is_multimodal=i%5==0,summary='请将这些想法整理成一段简洁的摘要。',prompt='请将这些想法整理成一段简洁的摘要。',response='这是用于界面回归检查的合成响应。',has_detail=True,detail_loaded=False,error_code='response_timeout' if status=='failed' else '',error_message='等待网页响应超时，请检查标签页状态。' if status=='failed' else '',error_stack='Synthetic stack: timeout in test fixture'))
STATS=dict(memory_mb=128,disk_status='正常',total_requests=1248,total_input_tokens=482360,total_output_tokens=196840,cpu_percent=12,project_cpu=3,memory_percent=38,project_memory_percent=2,running_count=1,queued_count=2)
async def install(page,writes):
 async def route_api(route):
  req=route.request;path=urlparse(req.url).path
  if req.method not in ['GET','HEAD']:
   data=req.post_data_json;writes.append((path,data))
   if path.endswith('/config') and path.startswith('/api/tab-pool'):
    POOL.update(data)
   elif path.endswith('/preset'):
    tab=next(t for t in TABS if t['persistent_index']==int(path.split('/')[-2]));tab['preset_name']=data['preset_name'];tab['effective_preset_name']=data['preset_name']
   return await route.fulfill(json=POOL if path.endswith('/config') else {'status':'success'})
  if path=='/api/tab-pool/tabs':return await route.fulfill(json=POOL)
  if path=='/api/system/request-history':return await route.fulfill(json={'records':RECORDS,'max_records':500,'revision':'fixture-1','enabled':True})
  if path.startswith('/api/system/request-history/'):
   rec=next(r for r in RECORDS if r['id']==path.split('/')[-1]);return await route.fulfill(json={**rec,'detail_loaded':True})
  if path=='/api/system/stats':return await route.fulfill(json=STATS)
  return await route.continue_()
 await page.route('**/api/**',route_api)

async def main():
 before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'config').glob('*.json')}
 async with async_playwright() as p:
  b=await p.chromium.launch();page=await b.new_page(viewport={'width':1512,'height':1100});writes=[];errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  await page.add_init_script("localStorage.setItem('darkMode','false');Object.defineProperty(navigator,'clipboard',{value:{writeText:async t=>window.copiedText=t}})")
  await install(page,writes)
  await page.goto(BASE_URL,wait_until='load');await page.get_by_role('button',name='暂不需要',exact=True).click()
  nav=page.locator('.app-sidebar');navStyle=await nav.evaluate('e=>[getComputedStyle(e).width,getComputedStyle(e).backgroundColor,e.querySelector(".app-brand-logo").src]')
  await page.get_by_role('button',name='标签页池',exact=True).click()
  cards=page.locator('.tab-pool-card');await expect(cards).to_have_count(4)
  assert navStyle==await nav.evaluate('e=>[getComputedStyle(e).width,getComputedStyle(e).backgroundColor,e.querySelector(".app-brand-logo").src]')
  for name,n in [('空闲可用',2),('正在执行',1),('需要关注',1),('全部标签页',4)]:
   await page.locator('.ops-pool-summary').get_by_role('button',name=re.compile(name)).click();await expect(cards).to_have_count(n)
  search=page.get_by_role('searchbox',name='搜索标签页、模型或会话');await search.fill('Claude');await expect(cards).to_have_count(1)
  await search.fill('does-not-exist');await expect(page.get_by_text('没有匹配的标签页',exact=True)).to_be_visible()
  await page.get_by_role('button',name='清除筛选',exact=False).click();await expect(cards).to_have_count(4)
  await page.locator('.ops-group-chips').get_by_role('button',name=re.compile('日常写作')).click();await expect(cards).to_have_count(2)
  await page.locator('.ops-group-chips').get_by_role('button',name='全部网页',exact=True).click();await expect(cards).to_have_count(4)
  assert not writes
  print('PASS pool status, model search, empty reset and URL-matched group filtering; no API writes')
  summary=cards.first.locator('summary');await summary.focus();await page.keyboard.press('Enter')
  await expect(cards.first.locator('.tab-route-row')).to_have_count(6)
  await cards.first.get_by_title('复制固定标签页路由',exact=True).click()
  assert (await page.evaluate('window.copiedText')).endswith('/tab/1/v1/chat/completions')
  await cards.first.locator('select').evaluate("e=>e.dataset.retained='yes'")
  await summary.click();await summary.click()
  assert await cards.first.locator('select').get_attribute('data-retained')=='yes'
  await summary.click()
  await cards.first.locator('select').select_option('简洁回复')
  await expect(cards.first.locator('select')).to_be_enabled()
  assert writes[-1]==('/api/tab-pool/tabs/1/preset',{'preset_name':'简洁回复'})
  print('PASS all six route forms, clipboard value, keyboard disclosure, DOM retention and original preset API payload')
  # Model editor and destructive action remain real dialogs; cancel must not write.
  count=len(writes)
  await cards.first.locator('.model-name-control').click();await expect(page.get_by_role('dialog',name='标签页操作')).to_be_visible()
  await page.get_by_role('dialog',name='标签页操作').get_by_role('textbox').fill('temporary-draft')
  await page.keyboard.press('Escape');await expect(page.get_by_role('dialog',name='标签页操作')).to_have_count(0)
  await page.get_by_role('button',name='终止并解锁',exact=True).click()
  await page.get_by_role('dialog',name='标签页操作').get_by_role('button',name='取消',exact=True).click();assert count==len(writes)
  await page.get_by_role('button',name='路由与调度',exact=True).click()
  drawer=page.get_by_role('dialog',name='路由与调度设置')
  await expect(drawer).to_be_visible();await drawer.get_by_label('自动刷新',exact=True).uncheck()
  await drawer.get_by_label('全局分配模式',exact=True).select_option('round_robin')
  await expect(drawer.get_by_label('全局分配模式',exact=True)).to_be_enabled()
  assert writes[-1][0]=='/api/tab-pool/config' and writes[-1][1]['allocation_mode']=='round_robin'
  await drawer.get_by_role('separator').focus();await page.keyboard.press('End');await expect(drawer.get_by_role('separator')).to_have_attribute('aria-valuenow','340')
  await page.keyboard.press('Escape');await expect(drawer).not_to_be_visible()
  await page.get_by_role('button',name='标签页池设置',exact=True).click();await expect(page.locator('.tab-pool-settings-popover')).to_be_visible()
  await page.get_by_role('button',name='标签页池设置',exact=True).click()
  print('PASS model and terminate cancellation, routing drawer/keyboard resize, polling pause and allocation save')
  # Route group creation retains all the member and domain controls.
  await page.locator('.ops-group-chips').get_by_role('button',name='新建路由组',exact=True).click()
  editor=page.locator('.tab-pool-group-editor');await expect(editor).to_be_visible()
  await editor.get_by_label('组 ID',exact=True).fill('smoke-test')
  await editor.get_by_label('显示名称',exact=True).fill('回归测试组')
  await editor.locator('.member-list-item input[type=checkbox]').first.check()
  await editor.get_by_role('button',name='保存路由组',exact=True).click()
  await expect(editor.get_by_role('button',name='保存路由组',exact=True)).to_be_enabled()
  assert writes[-1][0]=='/api/tab-pool/config'
  assert any(g['id']=='smoke-test' and len(g['members'])==1 for g in writes[-1][1]['route_groups'])
  print('PASS real route group editor builds original URL-member save payload (mock write)')
  await page.get_by_role('button',name='请求监控',exact=True).click();rows=page.locator('.uwa-request-row');await expect(rows).to_have_count(20)
  await expect(page.locator('.uwa-token-kpis')).not_to_be_visible()
  await page.get_by_role('button',name='下一页',exact=True).click();await expect(rows).to_have_count(6)
  await page.get_by_role('button',name='上一页',exact=True).click()
  tabs=page.locator('.uwa-status-tabs')
  await tabs.get_by_role('button',name=re.compile('失败')).click();await expect(rows).to_have_count(4)
  await rows.first.click();detail=page.get_by_role('dialog',name='请求详情');await expect(detail).to_be_visible()
  await expect(detail.get_by_role('button',name='关闭',exact=True)).to_be_focused()
  await detail.get_by_role('button',name='查看完整错误日志',exact=True).click()
  await expect(detail.locator('pre')).to_contain_text('Synthetic stack')
  await page.screenshot(path=str(OUTPUT/'detail.png'))
  await page.keyboard.press('Escape');await expect(detail).to_have_count(0);await expect(rows.first).to_be_focused()
  await tabs.get_by_role('button',name=re.compile('取消')).click();await expect(rows).to_have_count(3)
  await page.get_by_role('searchbox',name='搜索请求').fill('not-a-record');await expect(page.get_by_text('没有匹配的请求',exact=True)).to_be_visible()
  await page.get_by_role('button',name='清除筛选',exact=True).click();await expect(rows).to_have_count(20)
  await page.get_by_label('包含多模态',exact=True).uncheck();await expect(rows).to_have_count(20)
  assert '多模态' not in '\n'.join(await rows.all_inner_texts())
  print('PASS history pagination, failed/cancelled/text/multimodal filters; lazy detail fetch, error stack, Escape and focus return')
  await page.get_by_label('包含多模态',exact=True).check()
  await page.get_by_role('searchbox',name='搜索请求').fill('Gemini')
  await page.locator('.ops-view-tabs').get_by_role('button',name='用量分析',exact=True).click()
  await expect(page.locator('.uwa-token-kpis')).to_be_visible()
  await page.get_by_role('button',name='近 30 天',exact=True).click()
  await page.locator('.ops-view-tabs').get_by_role('button',name=re.compile('请求记录')).click()
  await expect(page.get_by_role('searchbox',name='搜索请求')).to_have_value('Gemini')
  await page.get_by_role('searchbox',name='搜索请求').fill('')
  await page.get_by_text('流量趋势与站点分布',exact=True).click()
  await expect(page.locator('.uwa-monitor-overview')).to_be_visible()
  await page.get_by_role('button',name='模型',exact=True).click()
  await page.get_by_text('流量趋势与站点分布',exact=True).click()
  await page.get_by_role('button',name='系统负载',exact=True).click();await expect(page.locator('.uwa-system-load-panel')).to_be_visible()
  await page.get_by_role('button',name='系统负载',exact=True).click()
  print('PASS analysis ranges, history filter retention, request trends/ranking and system load disclosure')
  for width in [1280,1024,768,390,375]:
   await page.set_viewport_size({'width':width,'height':1000})
   for label,key in [('标签页池','pool'),('请求监控','monitor')]:
    # The sidebar remains mounted when mobile navigation is closed; use its real DOM click.
    await page.get_by_role('button',name=label,exact=True).evaluate('e=>e.click()')
    await expect(page.locator('.tab-pool-card' if key=='pool' else '.uwa-request-row').first).to_be_visible()
    dims=await page.locator('.dashboard-scroll-region').evaluate('e=>[e.clientWidth,e.scrollWidth]')
    assert dims[1]<=dims[0]+1,(width,key,dims)
    if key=='pool':
     assert (await page.locator('.tab-card-main').first.bounding_box())['width']>180,(width,'card main collapsed')
     assert (await page.locator('.tab-pool-refresh').bounding_box())['height']<=44,(width,'refresh button wrapped')
    if width==390:
     await page.locator('.dashboard-scroll-region').evaluate('e=>e.scrollTop=0')
     await page.screenshot(path=str(OUTPUT/f'mobile-{key}.png'))
  await page.set_viewport_size({'width':1512,'height':1100})
  await page.locator('.app-theme-toggle').click()
  for label,key in [('标签页池','pool'),('请求监控','monitor')]:
   await page.get_by_role('button',name=label,exact=True).click();await page.wait_for_timeout(400)
   await page.screenshot(path=str(OUTPUT/f'dark-{key}.png'))
  assert not errors,errors
  print('PASS five widths without page overflow, mobile and dark screenshots; zero uncaught JavaScript exceptions')
  # Verify fresh empty and failed reads, not just populated fixtures.
  empty=await b.new_page(viewport={'width':1280,'height':900})
  empty.on('pageerror',lambda e:errors.append(str(e)))
  await install(empty,writes)
  async def empty_pool(route): await route.fulfill(json={**POOL,'tabs':[],'route_groups':[]})
  async def empty_history(route): await route.fulfill(json={'records':[],'max_records':500,'enabled':True})
  await empty.route('**/api/tab-pool/tabs',empty_pool)
  await empty.route('**/api/system/request-history?*',empty_history)
  await empty.goto(BASE_URL,wait_until='load')
  await empty.get_by_role('button',name='暂不需要',exact=True).click()
  await empty.get_by_role('button',name='标签页池',exact=True).click()
  await expect(empty.get_by_text('等待第一个网页加入',exact=True)).to_be_visible()
  await empty.get_by_role('button',name='请求监控',exact=True).click()
  await expect(empty.get_by_text('这里记录每一次响应',exact=True)).to_be_visible()
  async def error_pool(route): await route.fulfill(json={'tabs':[],'error':'测试：浏览器未连接'})
  await empty.route('**/api/tab-pool/tabs',error_pool)
  await empty.get_by_role('button',name='标签页池',exact=True).click()
  await expect(empty.get_by_text('暂时无法读取标签页',exact=True)).to_be_visible()
  await expect(empty.locator('.ops-new-group')).to_be_disabled()
  print('PASS empty states, unavailable pool read and configuration-load write guard')
  assert not errors,errors
  await b.close()
 assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'config').glob('*.json')}
 print('PASS all user configuration JSON hashes unchanged; ALL write APIs intercepted')
if __name__=='__main__':
 asyncio.run(main())
