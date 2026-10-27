"""Capture only repository examples using the real renderer and intercepted demo API data."""
from pathlib import Path
import hashlib, importlib.util, json, shutil, subprocess, sys, tempfile
from playwright.sync_api import sync_playwright
P=Path(__file__).resolve().parent
ROOT=P.parent
(P/'.build').mkdir(exist_ok=True)
(P/'assets').mkdir(exist_ok=True)
spec=importlib.util.spec_from_file_location('bridge',ROOT/'scripts/review_bridge.py')
bridge=importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)
with tempfile.TemporaryDirectory(prefix='synthetic-',dir=P/'.build') as tmp:
 run=Path(tmp)
 for name in ['report.md','board.json','manifest.json']:shutil.copyfile(ROOT/'examples/review'/name,run/name)
 subprocess.run([sys.executable,str(ROOT/'scripts/run.py'),'render','--run',str(run)],check=True)
 source=bridge.inject_bridge((run/'report.html').read_text(),{'token':'synthetic-preview-only','run_id':'synthetic-demo','panels':[f'A{i}' for i in range(1,9)]},(ROOT/'assets/bridge.js').read_text())
(P/'.build/report.html').write_text(source)
shutil.copyfile(ROOT/'assets/brand/mark.svg',P/'assets/mark.svg')
tasks=[]
spoken_quote='先分析漏检题，再决定是否扩大。'
written_quote='补充验收证据，并在报告中说明未验证部分。'
for i,(title,objective,target) in enumerate([('先补失败分析','先检查 2 道漏检题，再决定是否扩大实验。','虚构检索实验'),('补齐验证证据','补充验收证据，并在报告中说明未验证部分。','虚构验证任务')],1):
 tasks.append(dict(id=f'D{i}',selected=False,target_key=f'T{i}',objective=objective,constraints='仅限虚构样例；不发送真实任务。',acceptance='可追溯到样例报告，并展示检查结论。',panel_ids=['A1' if i==1 else 'A5'],source_items=[dict(id=f'I{i}',title=title,quote=spoken_quote if i==1 else written_quote,input_method='voice' if i==1 else 'written',confidence='high',questions=[],evidence_images=[])]))
review=dict(review_id='synthetic-review',approval=None,tasks=tasks,targets=[dict(key=f'T{i}',type='thread',title=t,thread_id=f'demo-thread-{i}') for i,t in [(1,'虚构检索实验'),(2,'虚构验证任务')]])
fixtures={'/status':dict(state='idle',can_record=False,message='虚构演示 · 未启动录音与真实发送'),'/review-state':dict(panels={f'A{i}':dict(comment='',read=False,revision=0) for i in range(1,9)}),'/review-plan':None,'/plan':None,'/dispatch-preferences':dict(dispatch_mode='manual',dispatch_consent=None),'/dispatch-status':None,'/focus':{}}
layout={}; calls=[]; errors=[]
with sync_playwright() as p:
 browser=p.chromium.launch()
 page=browser.new_page(viewport={'width':1600,'height':900},device_scale_factor=2)
 page.emulate_media(reduced_motion='reduce')
 page.on('pageerror',lambda e:errors.append(str(e)))
 def route(r):
  path=r.request.url.split('twohours.demo')[-1];calls.append([r.request.method,path])
  if path=='/':r.fulfill(status=200,body=source,content_type='text/html')
  elif path=='/approve-review':r.fulfill(status=200,body=json.dumps({'review_id':'synthetic-review','plan_id':'synthetic-plan','batch_ids':['demo1','demo2']}),content_type='application/json')
  elif path in fixtures:r.fulfill(status=200,body=json.dumps(fixtures[path]),content_type='application/json')
  elif path.startswith('/favicon'):r.fulfill(status=204)
  else:r.fulfill(status=404,body='{"error":"Preview only"}',content_type='application/json')
 page.route('**/*',route);page.goto('http://twohours.demo/');page.wait_for_timeout(500)
 assert page.locator('.review-card').count()==8
 page.screenshot(path=str(P/'assets/board.png'))
 layout['board']={'w':1600,'h':900,'a1':page.locator('.review-card').first.bounding_box(),'a6':page.locator('.review-card').nth(5).bounding_box()}
 page.locator('.review-card').first.click();page.wait_for_timeout(500)
 win=page.locator('#review-window');box=win.bounding_box()
 def capture_through(window,last,path):
  # Camera crop at a complete paragraph boundary; no product CSS or DOM changes.
  outer=window.bounding_box();bottom=last.bounding_box()
  height=bottom['y']+bottom['height']-outer['y']+18
  assert height<=outer['height']-1,(path,height,outer['height'])
  page.screenshot(path=str(P/'assets'/path),clip={**outer,'height':height})
  return {'w':outer['width'],'h':height}
 layout['report']=capture_through(win,win.locator('p').filter(has_text='图示为虚构开发结果'),'report.png')
 chart=win.locator('.am-chart').first.bounding_box()
 layout['report']['chart']={k:chart[k]-(box[k] if k in ['x','y'] else 0) for k in ['x','y','width','height']}
 win.locator('.window-close').click();page.locator('.review-card').nth(5).click();page.wait_for_timeout(500)
 win.locator('.window-content').evaluate("el=>{const chart=el.querySelector('.am-flow');el.scrollTop+=chart.getBoundingClientRect().top-el.getBoundingClientRect().top-24;}")
 layout['flow']=capture_through(win,win.locator('p').filter(has_text='图示是虚构演练流程'),'flow.png')
 win.locator('.window-close').click()
 # Synthetic users preserve the spoken example as a written note, then add A5.
 # The film labels this explicit follow-up; this is not automatic transcription.
 for panel,quote in [('A1',spoken_quote),('A5',written_quote)]:
  fixtures['/review-state']['panels'][panel].update(comment=quote,revision=1)
 fixtures['/review-state']['submitted']={'items':[{'id':f'W-{panel}','quote':quote} for panel,quote in [('A1',spoken_quote),('A5',written_quote)]]}
 fixtures['/status']=dict(state='recorded',can_record=False)
 page.get_by_role('link',name='汇总意见',exact=True).click();page.wait_for_timeout(200)
 notes=page.locator('#notes-window')
 assert '已提交，等待当前会话整理。' in notes.inner_text()
 assert notes.locator('#written-notes blockquote').all_text_contents()==[spoken_quote,written_quote]
 layout['waiting']=capture_through(notes,notes.locator('#written-notes article').last,'waiting.png')
 page.locator('#notes-window .window-close').click()
 fixtures['/review-plan']=review
 page.wait_for_timeout(2700)
 page.get_by_role('link',name='集中审核 · 2',exact=True).click();page.wait_for_timeout(400)
 n=page.locator('#notes-window');box=n.bounding_box()
 n.screenshot(path=str(P/'assets/review.png'))
 assert n.locator('.approval-evidence blockquote').first.inner_text()==spoken_quote
 def rect(sel):
  r=n.locator(sel).first.bounding_box();return {k:r[k]-(box[k] if k in ['x','y'] else 0) for k in ['x','y','width','height']}
 layout['review']={'w':box['width'],'h':box['height'],'choice':rect('.approval-choice'),'target':rect('.approval-destination select'),'submit':rect('.approval-submit'),'objective':rect('.approval-understanding textarea')}
 n.locator('.approval-choice input').check();n.screenshot(path=str(P/'assets/selected-one.png'))
 n.locator('.approval-nav-item').nth(1).click();n.screenshot(path=str(P/'assets/review-second.png'));n.locator('.approval-choice input').check();n.screenshot(path=str(P/'assets/selected-two.png'));n.locator('.approval-nav-item').first.click()
 n.screenshot(path=str(P/'assets/selected.png'))
 n.locator('.approval-submit').click();page.wait_for_timeout(250)
 n.screenshot(path=str(P/'assets/confirmed.png'))
 assert n.locator('.approval-submit').inner_text()=='已完成本轮审核'
 assert not errors,errors
 browser.close()
(P/'assets/layout.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2))
files=['examples/review/report.md','examples/review/board.json','examples/review/manifest.json','assets/board.css','assets/board.js','assets/bridge.js','assets/brand/mark.svg','scripts/build_board.py']
(P/'assets/provenance.json').write_text(json.dumps({'synthetic':True,'source_sha256':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files},'network':'All browser requests intercepted; no live network requests or actual approval/dispatch.','requests':calls,'page_errors':errors},ensure_ascii=False,indent=2))
print('Captured synthetic UI into',P/'assets')
