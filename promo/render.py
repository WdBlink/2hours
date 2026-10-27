"""Render the deterministic Canvas API used by huashu-art-motion's renderer and QA."""
from pathlib import Path
import argparse, base64, functools, http.server, json, subprocess, threading, time
from playwright.sync_api import sync_playwright
P=Path(__file__).resolve().parent
(P/'.build').mkdir(exist_ok=True)
(P/'stills').mkdir(exist_ok=True)
ap=argparse.ArgumentParser();ap.add_argument('--stills',action='store_true');ap.add_argument('--from',dest='start',type=float,default=0);ap.add_argument('--to',dest='end',type=float,default=55);ap.add_argument('--out',default='twohours-demo.mp4');ap.add_argument('--fps',type=int,default=60);a=ap.parse_args()
class Quiet(http.server.SimpleHTTPRequestHandler):
 def log_message(self,*args):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=str(P)))
threading.Thread(target=server.serve_forever,daemon=True).start()
errors=[];page_errors=[]
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch(args=['--enable-gpu-rasterization','--ignore-gpu-blocklist'])
  page=browser.new_page(viewport={'width':1920,'height':1080},device_scale_factor=1)
  page.on('pageerror',lambda e:page_errors.append(str(e)))
  page.goto(f'http://127.0.0.1:{server.server_port}/index.html?render=1');page.wait_for_function('window.__ready===true || !!window.__bootFailed')
  assert not page.evaluate('window.__bootFailed || null')
  grab="async t=>{await prepare(t);renderFrame(t);return __canvas.toDataURL('image/png').split(',')[1]}"
  if a.stills:
   times=[0,2.5,4.9,5.2,7.5,8,8.4,9,9.5,10.25,10.8,11.2,14,16.8,17.2,18.5,20,22.8,23,23.2,25.5,27,27.8,28.2,31.5,32.5,34.8,35.2,35.5,36.5,37,38.5,38.8,39.2,40.5,42,43.5,44.5,45.5,46.8,47.2,49,50.8,51.2,53,54.983]
   for t in times:(P/'stills'/f'{t:06.3f}.png').write_bytes(base64.b64decode(page.evaluate(grab,t)))
   (P/'stills/times.json').write_text(json.dumps(times))
   print(f'{len(times)} stills generated')
  else:
   out=P/a.out;raw=P/'.build/visual.mp4';audio=P/'assets/soundtrack.wav';st=time.time()
   ff=subprocess.Popen(['ffmpeg','-y','-v','error','-f','image2pipe','-framerate',str(a.fps),'-c:v','png','-i','-','-an','-c:v','libx264','-preset','medium','-crf','16','-pix_fmt','yuv420p','-g',str(a.fps*2),'-movflags','+faststart',str(raw)],stdin=subprocess.PIPE)
   try:
    for i in range(round(a.start*a.fps),round(a.end*a.fps)):
     ff.stdin.write(base64.b64decode(page.evaluate(grab,i/a.fps)))
     if i%120==0:print(f'{i}/{round(a.end*a.fps)} frames, {time.time()-st:.1f}s elapsed',flush=True)
   finally:ff.stdin.close()
   assert ff.wait()==0,'ffmpeg video encoding failed'
   subprocess.run(['ffmpeg','-y','-v','error','-i',str(raw),'-ss',str(a.start),'-t',str(a.end-a.start),'-i',str(audio),'-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-b:a','192k','-movflags','+faststart','-shortest',str(out)],check=True)
   raw.unlink()
   (P/'render-result.json').write_text(json.dumps({'file':out.name,'fps':a.fps,'frames':round((a.end-a.start)*a.fps),'duration':a.end-a.start,'page_errors':page_errors,'elapsed_seconds':round(time.time()-st,2)},indent=2))
   print(out)
  assert not page_errors,page_errors
  browser.close()
finally:server.shutdown();server.server_close()
