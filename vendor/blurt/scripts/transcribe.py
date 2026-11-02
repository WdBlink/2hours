# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "sherpa-onnx>=1.12", "truststore"]
# ///
"""Speech-to-text with timestamps. Always VAD-first: silence is dropped, speech is chunked, and every
chunk keeps its offset in the original video, so any backend (even ones without timestamps) works.

  transcribe.py setup  --backend sensevoice          download models for a local backend
  transcribe.py run    <video|audio> [--out DIR]     transcribe; writes transcript.json + transcript.txt
  transcribe.py backends                             list backends and whether they are ready

Backends
  sensevoice   local, sherpa-onnx + SenseVoice-Small int8 (~240MB). zh/en/ja/ko/yue, fast on any CPU.  [default]
  mlx-whisper  local, Apple Silicon only.   run with: uv run --with mlx-whisper transcribe.py ...
  faster-whisper local, CPU / NVIDIA CUDA.  run with: uv run --with faster-whisper transcribe.py ...
  openai       any OpenAI-compatible /audio/transcriptions API (OpenAI, Groq, SiliconFlow, ...)
               env: BLURT_ASR_API_KEY (or OPENAI_API_KEY / GROQ_API_KEY), BLURT_ASR_BASE_URL, BLURT_ASR_MODEL
  dashscope    Alibaba Qwen3-ASR (qwen3-asr-flash) via DashScope OpenAI-compatible chat API. env: DASHSCOPE_API_KEY
"""
from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

if os.name == "nt":
    import msvcrt
else:
    import fcntl

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import (MODELS_DIR, die, ffmpeg_bin, fmt_ts, global_config, save_json, update_global_config,  # noqa: E402
                     use_system_certs)

use_system_certs()

SR = 16000
SENSEVOICE_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
# Every source is pinned to the SHA-256 of the exact file it serves; a download that does not match is
# discarded and the next source is tried. Sources (checked 2026-11-02): Hugging Face LFS oids
# (api/models/<repo>/paths-info/main), the GitHub release asset digest for silero_vad.onnx, and for the
# non-LFS tokens.txt a download whose git blob id matched the repo tree (2cfc92fc...).
# The GitHub silero_vad.onnx is a different (newer) build than the Hugging Face copy, hence two hashes.
SILERO_GITHUB_SHA256 = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
SILERO_HF_SHA256 = "a35ebf52fd3ce5f1469b2a36158dba761bc47b973ea3382b3186ca15b1f5af28"
SENSEVOICE_MODEL_SHA256 = "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"
SENSEVOICE_TOKENS_SHA256 = "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc"
MODEL_FILES = {
    "silero_vad.onnx": [
        ("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx", SILERO_GITHUB_SHA256),
        ("https://huggingface.co/csukuangfj/vad/resolve/main/silero_vad.onnx", SILERO_HF_SHA256),
        ("https://hf-mirror.com/csukuangfj/vad/resolve/main/silero_vad.onnx", SILERO_HF_SHA256),
    ],
    "sensevoice/model.int8.onnx": [
        (f"https://huggingface.co/{SENSEVOICE_REPO}/resolve/main/model.int8.onnx", SENSEVOICE_MODEL_SHA256),
        (f"https://hf-mirror.com/{SENSEVOICE_REPO}/resolve/main/model.int8.onnx", SENSEVOICE_MODEL_SHA256),
    ],
    "sensevoice/tokens.txt": [
        (f"https://huggingface.co/{SENSEVOICE_REPO}/resolve/main/tokens.txt", SENSEVOICE_TOKENS_SHA256),
        (f"https://hf-mirror.com/{SENSEVOICE_REPO}/resolve/main/tokens.txt", SENSEVOICE_TOKENS_SHA256),
    ],
}
# Exact byte sizes, keyed by the pinned SHA-256 (checked 2026-11-02: Hugging Face paths-info "size", GitHub release
# asset "size", and Content-Length / Content-Range of each source). A known size tells an unfinished .part (keep it,
# resume later) from a finished but wrong one (delete it). Sources without an entry fall back to the response headers.
MODEL_SIZES = {
    SILERO_GITHUB_SHA256: 643854,
    SILERO_HF_SHA256: 1807522,
    SENSEVOICE_MODEL_SHA256: 239233841,
    SENSEVOICE_TOKENS_SHA256: 315894,
}
# How long a second process waits for another one to finish the same model (240 MB at ~70 KB/s fits).
DOWNLOAD_LOCK_TIMEOUT = 3600.0
DOWNLOAD_LOCK_POLL = 0.1


# ------------------------------------------------------------------ downloads
# Resumable, locked downloads adapted from AGIHunt/blurt b3b836ac (MIT): Range resume into .part, 416 as "already
# complete", 200 as "start over", and one lock per model. Unlike upstream, the SHA-256 check stays: the whole file is
# hashed from disk before it is installed, and the lock is taken inside download() itself.
class _Incomplete(Exception):
    """The .part is still a prefix worth keeping (fewer bytes than the known total arrived, or the request failed
    before anything was written)."""


class _BadResume(ValueError):
    """The checked file included bytes from an earlier run and was wrong: worth one fresh try from byte 0."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _marker(dest: Path) -> Path:
    return dest.with_name(dest.name + ".verified")


def _record_verified(dest: Path, sha256: str, st: os.stat_result | None = None) -> None:
    """Cache a successful check as size + mtime + hash, so later runs need only a stat. Best effort."""
    st = st or dest.stat()
    marker = _marker(dest)
    tmp = marker.with_name(f"{marker.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps({"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha256}))
        os.replace(tmp, marker)
    except OSError:
        tmp.unlink(missing_ok=True)


def _is_verified(dest: Path, accepted: dict[str, int | None], full: bool) -> bool:
    """`dest` matches one of the pinned hashes. Trusts the marker only if size and mtime still match it;
    otherwise, when `full`, hashes the file once and writes a new marker."""
    try:
        st = dest.stat()
    except OSError:
        return False
    if not any(size in (None, st.st_size) for size in accepted.values()):
        return False
    try:
        mark = json.loads(_marker(dest).read_text())
        if isinstance(mark, dict) and mark.get("size") == st.st_size and mark.get("mtime_ns") == st.st_mtime_ns \
                and accepted.get(mark.get("sha256"), -1) in (None, st.st_size):
            return True
    except (OSError, ValueError, TypeError):
        pass
    if not full:
        return False
    digest = _sha256_file(dest)
    if accepted.get(digest, -1) not in (None, st.st_size):
        return False
    _record_verified(dest, digest, st)
    return True


def _download_lock_path(rel: str) -> Path:
    return MODELS_DIR / ".locks" / (quote(rel, safe="") + ".lock")


@contextmanager
def _download_lock(rel: str):
    """One download of `rel` at a time across processes. Same polling lock as scripts/dispatch.py: flock on
    POSIX, a 1-byte msvcrt lock on Windows. Both go away with the process, so a killed holder never blocks."""
    path = _download_lock_path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as lock:
        deadline = time.monotonic() + DOWNLOAD_LOCK_TIMEOUT
        waiting = False
        while True:
            try:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                busy = (13, 36) if os.name == "nt" else (errno.EAGAIN, errno.EACCES)
                if error.errno not in busy:
                    raise
                if time.monotonic() >= deadline:
                    die(f"Timed out after {DOWNLOAD_LOCK_TIMEOUT:g}s waiting for {path}: another process is still "
                        f"downloading {rel}. Let it finish, or stop it and retry.")
                if not waiting:
                    print(f"waiting for another process downloading {rel}", file=sys.stderr, flush=True)
                    waiting = True
                time.sleep(DOWNLOAD_LOCK_POLL)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _fetch_verified(url: str, tmp: Path, expected: str, size: int | None) -> None:
    """Bring `tmp` to the complete file from `url`, resuming what is there, then hash the whole file.
    Returns only if it matches `expected`. Raises _Incomplete (keep tmp) when tmp may still be a good prefix;
    any other exception means tmp is not usable."""
    have = tmp.stat().st_size if tmp.exists() else 0
    if size is not None and have > size:
        tmp.unlink()
        have = 0
    kept = have  # bytes from an earlier run that the final hash will cover
    if not (have and have == size):
        headers = {"User-Agent": "blurt"}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30)
        except urllib.error.HTTPError as e:
            if e.code != 416 or not have:
                if have:
                    raise _Incomplete(e) from e
                raise
            r = None  # nothing past what we have: check the whole file below
        except Exception as e:  # nothing was written; a .part from an earlier run is still as good as before
            if have:
                raise _Incomplete(e) from e
            raise
        if r is not None:
            with r:
                start, total = 0, None
                status = getattr(r, "status", None) or 200
                if have and status == 206:
                    m = re.fullmatch(r"bytes (\d+)-\d+/(\d+|\*)", (r.headers.get("content-range") or "").strip())
                    if not m or int(m[1]) != have:
                        raise _BadResume(f"server resumed at the wrong offset ({r.headers.get('content-range')!r}, "
                                         f"have {have} bytes)")
                    start = have
                    total = int(m[2]) if m[2] != "*" else None
                elif status == 200 and r.headers.get("content-length"):
                    total = int(r.headers["content-length"])
                if size is not None and total is not None and total != size:
                    raise ValueError(f"server reports {total} bytes, expected {size}")
                total = size if size is not None else total
                kept = start  # 0 when the server ignored Range and sent the whole file
                done, t0 = start, time.time()
                with open(tmp, "ab" if start else "wb") as f:
                    try:
                        while chunk := r.read(1 << 20):
                            f.write(chunk)
                            done += len(chunk)
                            if total and time.time() - t0 > 2:
                                print(f"  {done >> 20}/{total >> 20} MB", file=sys.stderr, flush=True)
                                t0 = time.time()
                    except Exception as e:
                        if total is not None:
                            raise _Incomplete(f"connection lost at {done}/{total} bytes: {e}") from e
                        raise
            have = tmp.stat().st_size
            if total is not None and have < total:
                raise _Incomplete(f"incomplete: {have}/{total} bytes")
            if total is not None and have > total:
                raise (_BadResume if kept else ValueError)(f"got {have} bytes, expected {total}")
    digest = _sha256_file(tmp)  # the whole file on disk, including bytes from an earlier run
    if digest != expected:
        raise (_BadResume if kept else ValueError)(f"SHA-256 mismatch for {url}: expected {expected}, got {digest}")


def download(rel: str) -> Path:
    dest = MODELS_DIR / rel
    sources = MODEL_FILES[rel]
    accepted = {expected: MODEL_SIZES.get(expected) for _, expected in sources}
    if _is_verified(dest, accepted, full=False):  # fast path: stat + marker, no lock, no hashing
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if os.environ.get("BLURT_HF_MIRROR"):  # force the China-friendly mirror first
        sources = sorted(sources, key=lambda source: "hf-mirror" not in source[0])
    with _download_lock(rel):
        # Another process may have installed it while we waited, or an older run left an unchecked file.
        if dest.exists():
            if _is_verified(dest, accepted, full=True):
                return dest
            bad = dest.with_name(dest.name + ".bad")
            try:
                os.replace(dest, bad)
            except OSError as e:
                die(f"{dest} does not match a pinned SHA-256 and could not be moved aside: {e}")
            print(f"  {dest} does not match a pinned SHA-256; moved to {bad.name}", file=sys.stderr, flush=True)
        _marker(dest).unlink(missing_ok=True)
        last = None
        tmp = dest.with_suffix(dest.suffix + ".part")
        for url, expected in sources:
            for _ in range(2):  # a second pass only after a resumed file failed: retry this source from 0
                have = tmp.stat().st_size if tmp.exists() else 0
                try:
                    note = f" (resuming at {have} bytes)" if have else ""
                    print(f"downloading {rel} <- {url}{note}", file=sys.stderr, flush=True)
                    _fetch_verified(url, tmp, expected, MODEL_SIZES.get(expected))
                    os.replace(tmp, dest)
                    _record_verified(dest, expected)
                    return dest
                except _Incomplete as e:  # keep the .part for the next source or the next run
                    last = e
                    print(f"  failed: {e} (kept {tmp.name} to resume)", file=sys.stderr)
                    break
                except Exception as e:  # try next mirror
                    last = e
                    print(f"  failed: {e}", file=sys.stderr)
                    tmp.unlink(missing_ok=True)
                    if not isinstance(e, _BadResume):
                        break
    die(f"Could not download a verified {rel}: {last}. Set BLURT_HF_MIRROR=1 or download manually to {dest}")


# ------------------------------------------------------------------ audio + VAD
def audio_tracks(path: Path) -> int:
    info = subprocess.run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                          errors="replace").stderr
    return sum(1 for line in info.splitlines() if "Stream #" in line and "Audio:" in line)


def load_audio(path: Path, track: int = 0) -> np.ndarray:
    raw = subprocess.run([ffmpeg_bin(), "-nostdin", "-loglevel", "error", "-i", str(path), "-map", f"0:a:{track}", "-ac", "1",
                          "-af", "aresample=async=1000:first_pts=0", "-ar", str(SR), "-f", "s16le", "-"], capture_output=True).stdout
    if not raw:
        die(f"No audio track decoded from {path}")
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768


def vad_segments(samples: np.ndarray, max_speech: float = 25) -> list[tuple[float, float]]:
    import sherpa_onnx
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(download("silero_vad.onnx"))
    cfg.silero_vad.threshold = 0.45
    cfg.silero_vad.min_silence_duration = 0.3
    cfg.silero_vad.min_speech_duration = 0.25
    cfg.silero_vad.max_speech_duration = max_speech
    cfg.sample_rate = SR
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)
    win = cfg.silero_vad.window_size
    segs = []

    def drain():
        while not vad.empty():
            seg = vad.front
            segs.append((seg.start / SR, (seg.start + len(seg.samples)) / SR))
            vad.pop()

    for i in range(0, len(samples), win):
        vad.accept_waveform(samples[i:i + win])
        drain()
    vad.flush()
    drain()
    # pad a little so words at the edges are not clipped
    dur = len(samples) / SR
    return [(max(0.0, s - 0.15), min(dur, e + 0.2)) for s, e in segs]


def group_chunks(segs: list[tuple[float, float]], max_len: float, max_gap: float = 1.0) -> list[tuple[float, float]]:
    """Merge neighbouring speech segments into contiguous windows (original timeline, no remapping needed)."""
    chunks: list[list[float]] = []
    for s, e in segs:
        if chunks and s - chunks[-1][1] <= max_gap and e - chunks[-1][0] <= max_len:
            chunks[-1][1] = e
        else:
            chunks.append([s, e])
    return [(s, e) for s, e in chunks]


def wav_bytes(samples: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


# ------------------------------------------------------------------ backends
# each backend: transcribe(samples) -> list[(rel_start, rel_end, text)]
class SenseVoice:
    # SenseVoice degrades on long, mixed-language chunks (drops whole clauses); keep utterances short
    max_chunk = 10

    def __init__(self, args):
        import sherpa_onnx
        self.rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(download("sensevoice/model.int8.onnx")), tokens=str(download("sensevoice/tokens.txt")),
            num_threads=min(8, os.cpu_count() or 4), use_itn=True, language=args.language or "auto")

    def transcribe(self, x):
        st = self.rec.create_stream()
        st.accept_waveform(SR, x)
        self.rec.decode_stream(st)
        return [(0.0, len(x) / SR, st.result.text.strip())]


class MlxWhisper:
    max_chunk = 30

    def __init__(self, args):
        try:
            import mlx_whisper
        except ImportError:
            die("mlx-whisper not installed. Run with: uv run --with mlx-whisper transcribe.py ...")
        self.m, self.args = mlx_whisper, args
        self.repo = args.model or "mlx-community/whisper-large-v3-turbo"

    def transcribe(self, x):
        r = self.m.transcribe(x, path_or_hf_repo=self.repo, language=self.args.language, initial_prompt=self.args.prompt,
                              condition_on_previous_text=False, verbose=None)
        return [(s["start"], s["end"], s["text"].strip()) for s in r.get("segments", [])]


class FasterWhisper:
    max_chunk = 30

    def __init__(self, args):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            die("faster-whisper not installed. Run with: uv run --with faster-whisper transcribe.py ...")
        self.args = args
        self.model = WhisperModel(args.model or "large-v3-turbo", device="auto", compute_type="auto",
                                  download_root=str(MODELS_DIR / "faster-whisper"))

    def transcribe(self, x):
        segs, _ = self.model.transcribe(x, language=self.args.language, initial_prompt=self.args.prompt,
                                        condition_on_previous_text=False, vad_filter=False)
        return [(s.start, s.end, s.text.strip()) for s in segs]


class OpenAICompat:
    """POST {base}/audio/transcriptions. verbose_json gives segment timestamps where supported."""

    def __init__(self, args):
        self.key = (os.environ.get("BLURT_ASR_API_KEY") or os.environ.get("OPENAI_API_KEY")
                    or os.environ.get("GROQ_API_KEY"))
        if not self.key:
            die("No API key. Set BLURT_ASR_API_KEY (or OPENAI_API_KEY / GROQ_API_KEY).")
        default_base = "https://api.groq.com/openai/v1" if (os.environ.get("GROQ_API_KEY") and not os.environ.get(
            "BLURT_ASR_API_KEY")) else "https://api.openai.com/v1"
        self.base = (os.environ.get("BLURT_ASR_BASE_URL") or default_base).rstrip("/")
        self.model = args.model or os.environ.get("BLURT_ASR_MODEL") or (
            "whisper-large-v3-turbo" if "groq" in self.base else "whisper-1")
        self.verbose = not self.model.startswith("gpt-4o")  # gpt-4o-*-transcribe has no segment timestamps
        # Longer chunks = fewer requests (Groq bills >=10s per request); shorter when no timestamps come back.
        self.max_chunk = 60 if self.verbose else 20
        self.args = args

    def transcribe(self, x):
        fields = {"model": self.model, "response_format": "verbose_json" if self.verbose else "json"}
        if self.args.language:
            fields["language"] = self.args.language
        if self.args.prompt:
            fields["prompt"] = self.args.prompt
        boundary = uuid.uuid4().hex
        body = io.BytesIO()
        for k, v in fields.items():
            body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
                   f'Content-Type: audio/wav\r\n\r\n'.encode())
        body.write(wav_bytes(x))
        body.write(f"\r\n--{boundary}--\r\n".encode())
        data = _post(f"{self.base}/audio/transcriptions", body.getvalue(),
                     {"Authorization": f"Bearer {self.key}", "Content-Type": f"multipart/form-data; boundary={boundary}"})
        if data.get("segments"):
            return [(s["start"], s["end"], s["text"].strip()) for s in data["segments"]]
        return [(0.0, len(x) / SR, (data.get("text") or "").strip())]


class DashScope:
    max_chunk = 20

    def __init__(self, args):
        self.key = os.environ.get("DASHSCOPE_API_KEY") or die("Set DASHSCOPE_API_KEY")
        self.base = (os.environ.get("BLURT_ASR_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        self.model = args.model or "qwen3-asr-flash"
        self.args = args

    def transcribe(self, x):
        audio = "data:audio/wav;base64," + base64.b64encode(wav_bytes(x)).decode()
        payload = {"model": self.model, "stream": False,
                   "messages": [{"role": "system", "content": [{"text": self.args.prompt or ""}]},
                                {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": audio}}]}],
                   "asr_options": {"enable_itn": True, **({"language": self.args.language} if self.args.language else {})}}
        data = _post(f"{self.base}/chat/completions", json.dumps(payload).encode(),
                     {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        text = data["choices"][0]["message"]["content"]
        if isinstance(text, list):
            text = "".join(p.get("text", "") for p in text)
        return [(0.0, len(x) / SR, text.strip())]


def _post(url: str, body: bytes, headers: dict, retries: int = 4) -> dict:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:500]
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            die(f"ASR API error {e.code}: {msg}")
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            die(f"ASR API unreachable: {e}")


BACKENDS = {"sensevoice": SenseVoice, "mlx-whisper": MlxWhisper, "faster-whisper": FasterWhisper,
            "openai": OpenAICompat, "dashscope": DashScope}
LOCAL = {"sensevoice", "mlx-whisper", "faster-whisper"}


# ------------------------------------------------------------------ commands
def cmd_run(a):
    src = Path(a.input)
    if not src.exists():
        die(f"{src} not found")
    out = Path(a.out or src.parent)
    cfg = global_config().get("asr", {})
    name = a.backend or cfg.get("backend") or "sensevoice"
    a.model = a.model or (cfg.get("model") if name == cfg.get("backend") else None)
    a.language = a.language or cfg.get("language")
    if name not in BACKENDS:
        die(f"unknown backend {name}; choose from {list(BACKENDS)}")

    t0 = time.time()
    backend = BACKENDS[name](a)
    workers = 1 if name in LOCAL else a.concurrency
    # Blurt's native recorder writes the mic as track 0 and, with --system-audio, what the Mac played as track 1.
    # Transcribing them apart labels who spoke: the user ("mic") or the call / demo ("system").
    n_tracks = max(1, audio_tracks(src))
    duration = speech = blank = 0.0
    results = []
    for track in range(min(n_tracks, 2)):
        samples = load_audio(src, track)
        duration = max(duration, len(samples) / SR)
        segs = vad_segments(samples, max_speech=min(25, backend.max_chunk))
        chunks = group_chunks(segs, backend.max_chunk)
        label = ("mic", "system")[track] if n_tracks > 1 else None
        sp = sum(e - s for s, e in chunks)
        print(f"audio {fmt_ts(len(samples) / SR)}{f' ({label})' if label else ''}, speech {fmt_ts(sp)} "
              f"in {len(chunks)} chunks → {name}", file=sys.stderr, flush=True)

        def work(ch, samples=samples):
            s, e = ch
            return [(s + rs, s + min(re_, e - s), t) for rs, re_, t in backend.transcribe(samples[int(s * SR):int(e * SR)])]

        with ThreadPoolExecutor(workers) as pool:
            for i, ((s, e), r) in enumerate(zip(chunks, pool.map(work, chunks))):
                results.extend((s_, e_, t, label) for s_, e_, t in r)
                if track == 0 and not any(ch.isalnum() for *_, t in r for ch in t):
                    blank += e - s
                if (i + 1) % 20 == 0:
                    print(f"  {i + 1}/{len(chunks)} chunks", file=sys.stderr, flush=True)
        if track == 0:
            speech = sp

    results.sort(key=lambda r: r[0])
    segments = [{"start": round(s, 2), "end": round(e, 2), "text": t, **({"speaker": lb} if lb else {})}
                for s, e, t, lb in results if t]
    data = {"source": src.name, "backend": name, "model": getattr(backend, "model", None) if name != "sensevoice" else "sense-voice-small-int8",
            "duration": round(duration, 2), "speech_seconds": round(speech, 2),
            "elapsed_seconds": round(time.time() - t0, 1), "segments": segments}
    # SenseVoice knows only zh/en/ja/ko/yue; other languages (German…) mostly come back as "." tagged <|en|>, so
    # the language tag can't catch it. Supported speech that VAD kept practically never decodes to nothing.
    # Mic track only: system audio may be music or effects.
    if name == "sensevoice" and speech >= 5 and blank / speech > 0.3:
        data["warning"] = (f"{blank:.0f}s of {speech:.0f}s of speech decoded to nothing: the language is probably "
                           "not zh/en/ja/ko/yue, which is all SenseVoice supports. Re-run with a Whisper or cloud "
                           "backend (see reference/asr.md) and pass --language.")
        print("warning: " + data["warning"], file=sys.stderr, flush=True)
    save_json(out / "transcript.json", data)
    lines = [f"[{fmt_ts(s['start'])}-{fmt_ts(s['end'])}] {'(system) ' if s.get('speaker') == 'system' else ''}{s['text']}"
             for s in segments]
    (out / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in data.items() if k != "segments"} | {"segments": len(segments),
                                                                              "files": [str(out / "transcript.json"), str(out / "transcript.txt")]},
                     ensure_ascii=False, indent=2))


def cmd_setup(a):
    name = a.backend
    if name == "sensevoice":
        for rel in MODEL_FILES:
            download(rel)
    else:
        download("silero_vad.onnx")
        if name in ("mlx-whisper", "faster-whisper"):
            print(f"Model weights download on first run. Always invoke with: uv run --with {name} transcribe.py run ...")
    patch = {"backend": name}
    if a.model:
        patch["model"] = a.model
    if a.language:
        patch["language"] = a.language
    update_global_config({"asr": patch})
    print(json.dumps({"ok": True, "asr": global_config()["asr"]}, ensure_ascii=False))


def cmd_backends(_a):
    have = lambda rel: (MODELS_DIR / rel).exists()  # noqa: E731
    print(json.dumps({
        "configured": global_config().get("asr"),
        "sensevoice_ready": have("sensevoice/model.int8.onnx") and have("sensevoice/tokens.txt") and have("silero_vad.onnx"),
        "api_keys": {k: bool(os.environ.get(k)) for k in ("BLURT_ASR_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "DASHSCOPE_API_KEY")},
    }, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("input")
    r.add_argument("--out", help="output dir (default: next to input)")
    r.add_argument("--backend", choices=list(BACKENDS))
    r.add_argument("--model")
    r.add_argument("--language", help="e.g. zh, en; default auto-detect")
    r.add_argument("--prompt", help="glossary / context: product & module names, jargon (Whisper/API backends)")
    r.add_argument("--concurrency", type=int, default=4)
    s = sub.add_parser("setup")
    s.add_argument("--backend", choices=list(BACKENDS), default="sensevoice")
    s.add_argument("--model")
    s.add_argument("--language")
    sub.add_parser("backends")
    a = p.parse_args()
    {"run": cmd_run, "setup": cmd_setup, "backends": cmd_backends}[a.cmd](a)


if __name__ == "__main__":
    main()
