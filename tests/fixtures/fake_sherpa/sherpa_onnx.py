"""Linux 上 sherpa_onnx 的替身，只用来测并发，不做识别。

- VAD：把整段音频按 FAKE_SEG 秒（默认 8）切成连续的"语音段"，不看内容。
- OfflineRecognizer.from_sense_voice(num_threads=N, ...)：只记下 N，不读模型文件。
- decode_stream：起 N 个线程，每个线程做固定次数的 numpy 矩阵乘（FAKE_ITERS，默认 40 次 256x256），
  用来模拟 onnxruntime 的 N 个计算线程。numpy 做矩阵乘时释放 GIL，所以 N 个线程能同时占满 N 个核。
  需要 OPENBLAS_NUM_THREADS=1 等，让每个线程只用一个核。
- 每个事件（load / decode_start / decode_end）以一行 JSON 追加到 FAKE_SHERPA_LOG，带 pid 和 time.time()。
"""
import json
import os
import threading
import time

import numpy as np

_LOG = os.environ.get("FAKE_SHERPA_LOG")
_SEG = float(os.environ.get("FAKE_SEG", "8"))
_ITERS = int(os.environ.get("FAKE_ITERS", "40"))
_lock = threading.Lock()


def _log(event, **kw):
    if not _LOG:
        return
    line = json.dumps({"pid": os.getpid(), "t": time.time(), "event": event, **kw})
    with _lock, open(_LOG, "a") as f:
        f.write(line + "\n")


class _Silero:
    model = ""
    threshold = min_silence_duration = min_speech_duration = max_speech_duration = 0
    window_size = 512


class VadModelConfig:
    def __init__(self):
        self.silero_vad = _Silero()
        self.sample_rate = 16000
        self.num_threads = None


class _Seg:
    def __init__(self, start, samples):
        self.start, self.samples = start, samples


class VoiceActivityDetector:
    def __init__(self, cfg, buffer_size_in_seconds=60):
        self.sr = cfg.sample_rate
        self.step = int(min(_SEG, cfg.silero_vad.max_speech_duration or _SEG) * self.sr)
        self.total = 0
        self.queue = []
        self.emitted = 0
        _log("vad_init", num_threads=getattr(cfg, "num_threads", None))

    def accept_waveform(self, x):
        self.total += len(x)
        while self.total - self.emitted >= self.step:
            self.queue.append(_Seg(self.emitted, np.zeros(self.step, dtype=np.float32)))
            self.emitted += self.step

    def flush(self):
        if self.total > self.emitted:
            self.queue.append(_Seg(self.emitted, np.zeros(self.total - self.emitted, dtype=np.float32)))
            self.emitted = self.total

    def empty(self):
        return not self.queue

    @property
    def front(self):
        return self.queue[0]

    def pop(self):
        self.queue.pop(0)


class _Result:
    text = "测试"


class _Stream:
    result = _Result()

    def accept_waveform(self, sr, x):
        self.n = len(x)


def _burn(iters):
    a = np.random.default_rng(0).random((256, 256))
    for _ in range(iters):
        a = np.tanh(a @ a / 256.0)


class OfflineRecognizer:
    def __init__(self, num_threads):
        self.num_threads = num_threads

    @classmethod
    def from_sense_voice(cls, model, tokens, num_threads=1, **kw):
        _log("load", num_threads=num_threads, model=os.path.basename(model))
        return cls(num_threads)

    def create_stream(self):
        return _Stream()

    def decode_stream(self, st):
        _log("decode_start")
        ts = [threading.Thread(target=_burn, args=(_ITERS,)) for _ in range(self.num_threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        _log("decode_end")
