"""Derive the poster, small silent preview, and media diagnostics from the finished MP4."""
from pathlib import Path
import hashlib,json,math,re,subprocess
from PIL import Image,ImageDraw
P=Path(__file__).resolve().parent
video=P/'twohours-demo.mp4';qa=P/'qa';qa.mkdir(exist_ok=True)
info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','stream=index,codec_name,codec_type,width,height,r_frame_rate,avg_frame_rate,nb_frames,sample_rate,channels,duration','-show_entries','format=duration,size','-of','json',str(video)]))
v=next(x for x in info['streams'] if x['codec_type']=='video');a=next(x for x in info['streams'] if x['codec_type']=='audio')
assert (v['width'],v['height'],v['r_frame_rate'],v['nb_frames'])==(1920,1080,'60/1','3300')
assert a['sample_rate']=='48000' and a['channels']==2
(P/'media-info.json').write_text(json.dumps(info,indent=2))
subprocess.run(['ffmpeg','-y','-v','error','-ss','53','-i',str(video),'-frames:v','1','-q:v','2',str(P/'poster.jpg')],check=True)
subprocess.run(['ffmpeg','-y','-v','error','-i',str(video),'-filter_complex','[0:v]setpts=PTS/3,fps=10,scale=640:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=3','-loop','0',str(P/'twohours-preview.gif')],check=True)
for name,filters in [('blackdetect',['-vf','blackdetect=d=0.05:pix_th=0.10','-an']),('loudness',['-af','ebur128=peak=true','-vn'])]:
 with (qa/f'{name}.log').open('w') as out:subprocess.run(['ffmpeg','-hide_banner','-i',str(video),*filters,'-f','null','-'],stderr=out,stdout=subprocess.DEVNULL,check=True)
assert 'black_start' not in (qa/'blackdetect.log').read_text()
times=json.loads((P/'stills/times.json').read_text())
frames=qa/'encoded-stills';frames.mkdir(exist_ok=True)
for t in times:
 subprocess.run(['ffmpeg','-y','-v','error','-ss',str(t),'-i',str(video),'-frames:v','1','-q:v','2',str(frames/f'{t:06.3f}.jpg')],check=True)
sheet=Image.new('RGB',(1920,math.ceil(len(times)/4)*294),'#f7f7f5');draw=ImageDraw.Draw(sheet)
for i,t in enumerate(times):
 im=Image.open(frames/f'{t:06.3f}.jpg');im.thumbnail((480,270))
 x=(i%4)*480;y=(i//4)*294;sheet.paste(im,(x,y));draw.text((x+12,y+272),f'{t:.3f}s',fill='#1c2532')
sheet.save(qa/'encoded-contact-sheet.jpg',quality=90)
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
summary=(qa/'loudness.log').read_text().split('Summary:')[-1]
metric=lambda pattern:float(re.search(pattern,summary).group(1))
report=json.loads((qa/'qa.json').read_text())
provenance=json.loads((P/'assets/provenance.json').read_text())
result={
 'video_sha256':sha(video),'bytes':video.stat().st_size,'duration_seconds':float(info['format']['duration']),
 'frames':int(v['nb_frames']),'resolution':[v['width'],v['height']],'fps':60,
 'audio_codec':a['codec_name'],'audio_sample_rate':int(a['sample_rate']),'audio_channels':a['channels'],
 'integrated_loudness_lufs':metric(r'I:\s*([\d.-]+) LUFS'),
 'true_peak_dbfs':metric(r'Peak:\s*([\d.-]+) dBFS'),'loudness_range_lu':metric(r'LRA:\s*([\d.-]+) LU'),
 'black_intervals':0,'qa_segments':len(report['segments']),
 'deterministic_segments':sum(s['deterministic'] for s in report['segments']),
 'qa_transitions':len(report['transitions']),'page_errors':len(report['page_errors']),
 'canvas_framing_findings':sum(len(s['framing']) for s in report['segments']),
 'decoded_stills':len(times),'synthetic_approve_requests':provenance['requests'].count(['POST','/approve-review']),
 'artifact_sha256':{str(f.relative_to(P)):sha(f) for f in [P/'poster.jpg',P/'twohours-preview.gif',qa/'qa.json',qa/'encoded-contact-sheet.jpg',*sorted(frames.glob('*.jpg'))]}
}
(P/'validation-result.json').write_text(json.dumps(result,indent=2))
print('Poster, GIF, decoded stills, media diagnostics and SHA-256 manifest created.')
print(result['video_sha256'])
