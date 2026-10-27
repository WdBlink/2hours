"""Original deterministic ambient score and light UI cues. No samples or voice recordings."""
from pathlib import Path
import json, wave
import numpy as np
P=Path(__file__).resolve().parent
(P/'assets').mkdir(exist_ok=True)
SR=48000;DURATION=55;N=SR*DURATION
track=np.zeros((N,2),dtype=np.float64)
def tone(at,dur,midi,amp=.05,pan=0,pluck=False):
 n=int(dur*SR);t=np.arange(n)/SR;f=440*2**((midi-69)/12)
 env=(1-np.exp(-t/(.012 if pluck else .45)))*np.exp(-t/(1.1 if pluck else dur*.65))
 env*=np.minimum(1,np.maximum(0,(dur-t)/(.3 if pluck else 1.0)))
 v=(np.sin(2*np.pi*f*t)+.22*np.sin(2*np.pi*f*2*t)+.055*np.sin(2*np.pi*f*3*t))*env*amp
 start=int(at*SR);end=min(start+n,N);v=v[:end-start]
 track[start:end,0]+=v*np.sqrt((1-pan)/2);track[start:end,1]+=v*np.sqrt((1+pan)/2)
chords=[[50,57,61,64],[47,54,57,62],[43,54,59,62],[45,52,57,59]]
for i,at in enumerate(np.arange(0,54,6)):
 for j,note in enumerate(chords[i%4]):tone(float(at),7,note,.042,(-.55+j*.36))
 melody=[74,76,78,81,78,76,74,69]
 for j in range(4):tone(float(at)+.5+j*1.2,2.3,melody[(i+j)%len(melody)],.038,(-.3 if j%2 else .3),True)
# Duck the score under the scripted voice-caption section.
t=np.arange(N)/SR;duck=1-.40*np.clip((t-28)/1.0,0,1)*np.clip((35-t)/1.0,0,1);track*=duck[:,None]
cues=[(1.2,'type'),(2.2,'type'),(3.4,'enter'),(5.5,'paper'),(6.7,'paper'),(15.9,'click'),(17.2,'page'),(23.1,'page'),(29.2,'ink'),(38.9,'page'),(43.0,'click'),(44.0,'click'),(46.0,'confirm'),(48.1,'receipt'),(49.0,'receipt')]
rng=np.random.default_rng(20261025)
for at,kind in cues:
 if kind in ['confirm','receipt']:
  tone(at,.9,81,.065,0,True);tone(at+.12,1.0,86,.045,.15,True)
 elif kind in ['paper','page','ink']:
  dur=.45 if kind=='ink' else .16;n=int(dur*SR);tt=np.arange(n)/SR
  noise=rng.normal(size=n);noise=np.convolve(noise,np.ones(9)/9,mode='same');v=noise*np.sin(np.pi*tt/dur)**2*(.026 if kind=='ink' else .035)
  st=int(at*SR);track[st:st+n]+=v[:,None]
 else:
  dur=.08;n=int(dur*SR);tt=np.arange(n)/SR
  v=np.sin(2*np.pi*(1200 if kind=='type' else 850)*tt)*np.exp(-tt/0.014)*.05
  st=int(at*SR);track[st:st+n]+=v[:,None]
track*=np.clip(t/.5,0,1)[:,None]*np.clip((DURATION-t)/1.65,0,1)[:,None]
peak=np.max(np.abs(track));track*=.60/max(peak,1e-9)
pcm=(track*32767).astype('<i2')
with wave.open(str(P/'assets/soundtrack.wav'),'wb') as w:w.setnchannels(2);w.setsampwidth(2);w.setframerate(SR);w.writeframes(pcm.tobytes())
(P/'assets/audio-cues.json').write_text(json.dumps({'duration':DURATION,'sample_rate':SR,'channels':2,'peak_dbfs':round(20*np.log10(np.max(np.abs(track))),2),'rms_dbfs':round(20*np.log10(np.sqrt(np.mean(track**2))),2),'score':'Original additive synthesis; four chords and a pentatonic melody; no samples.','seed':20261025,'cues':[{'time':at,'sound':kind} for at,kind in cues]},ensure_ascii=False,indent=2))
print('Original audio generated: 55s / 48kHz stereo')
