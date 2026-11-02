"""Model downloads: resume a partial file, verify the whole file, and serialize downloads of one model.

Converted from Triage's regression draft for 2hours__upstream-blurt-b3b836a (tests 1-8), plus checks for the
cached verification of an already installed file and for the per-model lock. No network access: single-process
tests replace urllib.request.urlopen; two-process tests use a local HTTP server on 127.0.0.1.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'vendor' / 'blurt' / 'scripts'


def load_transcribe():
    sys.path.insert(0, str(SCRIPTS))
    try:
        if 'transcribe' in sys.modules:
            return sys.modules['transcribe']
        if importlib.util.find_spec('numpy'):
            return importlib.import_module('transcribe')
        # download() does not use numpy; stub it only when the optional dependency is absent.
        with mock.patch.dict(sys.modules, {'numpy': types.ModuleType('numpy')}):
            return importlib.import_module('transcribe')
    finally:
        sys.path.remove(str(SCRIPTS))


T = load_transcribe()

DATA = random.Random(7).randbytes(3 << 20)          # 3 MB fake model
SHA = hashlib.sha256(DATA).hexdigest()
WRONG = bytes(b ^ 0xFF for b in DATA)               # right length, wrong content
REL = 'fake/model.int8.onnx'
MB = 1 << 20
CHILD = """
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import test_model_download as D
D.configure(Path(sys.argv[2]), [(u, D.SHA) for u in sys.argv[3:]])
p = D.T.download(D.REL)
print(json.dumps({"sha_ok_at_return": hashlib.sha256(Path(p).read_bytes()).hexdigest() == D.SHA}), flush=True)
"""
HOLD_LOCK = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import test_model_download as D
D.configure(Path(sys.argv[2]), [("http://127.0.0.1:9/a", D.SHA)])
with D.T._download_lock(D.REL):
    print("locked", flush=True)
    time.sleep(60)
"""


def configure(models_dir: Path, sources: list[tuple[str, str]], sizes: dict | None = None) -> None:
    """Point MODELS_DIR at a temporary directory and MODEL_FILES at the test data."""
    T.MODELS_DIR = models_dir
    T.MODEL_FILES = {REL: sources}
    if hasattr(T, 'MODEL_SIZES'):
        T.MODEL_SIZES = dict(sizes or {})


def child_env(home: Path) -> dict:
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), NO_PROXY='*', no_proxy='*')
    for name in ('http_proxy', 'HTTP_PROXY', 'https_proxy', 'HTTPS_PROXY'):
        env.pop(name, None)
    return env


class FakeResponse(io.BytesIO):
    """cut: give only the first `cut` bytes and end (HTTPResponse.read(n) returns short data here, no IncompleteRead)."""

    def __init__(self, body: bytes, status=200, length=None, cut=None, start=None):
        super().__init__(body if cut is None else body[:cut])
        self.status = status
        self.headers = {} if length is False else {'content-length': str(len(body) if length is None else length)}
        if status == 206:
            first = len(DATA) - len(body) if start is None else start
            self.headers['content-range'] = f'bytes {first}-{len(DATA) - 1}/{len(DATA)}'

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeServer:
    """ranges=False ignores Range (200, whole file). bad_start reports a 206 whose Content-Range starts elsewhere.
    wrong=True answers a request from 0 with WRONG: a bad mirror that has the right length."""

    def __init__(self, ranges=True, cut=None, no_length=False, bad_start=False, wrong=False):
        self.ranges, self.cut, self.no_length, self.bad_start = ranges, cut, no_length, bad_start
        self.wrong = wrong
        self.requests: list[dict] = []

    def __call__(self, req, timeout=None):
        rng = req.get_header('Range')
        start = int(rng.split('=')[1].split('-')[0]) if rng and self.ranges else 0
        if start >= len(DATA) and rng:
            self.requests.append({'range': rng, 'status': 416, 'sent': 0})
            raise urllib.error.HTTPError(req.full_url, 416, 'Range Not Satisfiable', {}, io.BytesIO())
        if start and self.bad_start:
            self.requests.append({'range': rng, 'status': 206, 'sent': len(DATA)})
            return FakeResponse(DATA, 206, start=0)
        body = (WRONG if self.wrong and not start else DATA)[start:]
        sent = len(body) if self.cut is None else min(self.cut, len(body))
        self.requests.append({'range': rng, 'status': 206 if start else 200, 'sent': sent})
        return FakeResponse(body, 206 if start else 200, False if self.no_length else None, self.cut)


class Isolated(unittest.TestCase):
    def setUp(self):
        saved = {name: getattr(T, name) for name in ('MODELS_DIR', 'MODEL_FILES', 'MODEL_SIZES') if hasattr(T, name)}
        self.addCleanup(lambda: [setattr(T, name, value) for name, value in saved.items()])
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.models = self.home / 'models'


class DownloadCase(Isolated):
    def setUp(self):
        super().setUp()
        configure(self.models, [('http://127.0.0.1:9/a', SHA), ('http://127.0.0.1:9/b', SHA)])
        self.dest = self.models / REL
        self.part = self.dest.with_suffix(self.dest.suffix + '.part')
        self.part.parent.mkdir(parents=True, exist_ok=True)

    def one_source(self, sizes=None):
        """Only source a, so a retry from 0 has to come from the same source."""
        configure(self.models, [('http://127.0.0.1:9/a', SHA)], sizes)

    def download(self, server):
        with mock.patch.object(urllib.request, 'urlopen', side_effect=server), \
                mock.patch('sys.stderr', new_callable=io.StringIO):
            return T.download(REL)

    def try_download(self, server):
        try:
            self.download(server)
        except (SystemExit, Exception):
            pass

    def assert_installed_ok(self):
        self.assertTrue(self.dest.exists(), 'not installed')
        self.assertEqual(hashlib.sha256(self.dest.read_bytes()).hexdigest(), SHA, 'installed file has the wrong SHA-256')


class SingleProcess(DownloadCase):
    # 1 .part holds the correct first 1 MB: send Range, fetch only the rest, verify the whole file
    def test_resumes_partial_with_range_and_verifies_whole_file(self):
        self.part.write_bytes(DATA[:MB])
        s = FakeServer()
        self.download(s)
        self.assert_installed_ok()
        self.assertEqual(s.requests[0]['range'], f'bytes={MB}-')
        self.assertEqual(sum(r['sent'] for r in s.requests), len(DATA) - MB, 'a resume must fetch only the rest')
        self.assertFalse(self.part.exists())

    # 2 .part is 1 MB of zeros: whole-file check fails, .part is deleted, the file is fetched again from 0
    def test_corrupt_partial_fails_checksum_is_deleted_and_restarted(self):
        self.part.write_bytes(b'\0' * MB)
        s = FakeServer()
        self.try_download(s)
        self.assertFalse(self.part.exists() and self.part.read_bytes() == b'\0' * MB, 'the bad .part is still there')
        self.assertTrue(self.dest.exists(), 'should restart from 0 and install')
        self.assert_installed_ok()
        self.assertIsNone(s.requests[-1]['range'], 'the restart must not send Range')

    # 3 connection ends early with a known total length: keep .part for the next run, which resumes
    def test_interrupted_download_keeps_partial_then_next_run_resumes(self):
        configure(self.models, [('http://127.0.0.1:9/a', SHA)])   # one source, so .part length is known
        self.try_download(FakeServer(cut=MB))
        self.assertFalse(self.dest.exists())
        self.assertTrue(self.part.exists(), '.part was deleted after an interruption; the next run starts over')
        self.assertTrue(DATA.startswith(self.part.read_bytes()))
        s = FakeServer()
        self.download(s)
        self.assert_installed_ok()
        self.assertEqual(s.requests[0]['range'], f'bytes={MB}-')

    # 4 no total length and an early end: cannot tell it is complete, fails the checksum, nothing kept
    def test_short_download_without_length_fails_checksum_and_is_deleted(self):
        self.try_download(FakeServer(cut=MB, no_length=True))
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.part.exists())

    # 5 server ignores Range (200, whole file): must not append to the old bytes
    def test_server_ignoring_range_restarts_from_zero(self):
        self.part.write_bytes(DATA[:MB])
        self.download(FakeServer(ranges=False))
        self.assert_installed_ok()

    # 6 .part is already complete (killed before the rename): 416, verify the whole file, install, no re-download
    def test_complete_partial_is_verified_and_installed_without_redownload(self):
        self.part.write_bytes(DATA)
        s = FakeServer()
        self.download(s)
        self.assert_installed_ok()
        self.assertEqual(sum(r['sent'] for r in s.requests), 0, 'a complete .part was downloaded again')

    # a 206 whose Content-Range does not start where .part ends must not be appended
    def test_partial_response_at_wrong_offset_is_not_appended(self):
        self.part.write_bytes(DATA[:MB])
        s = FakeServer(bad_start=True)
        self.download(s)
        self.assert_installed_ok()
        self.assertIsNone(s.requests[-1]['range'])

    # a source that cannot be reached leaves a good .part alone; the next source resumes it
    def test_unreachable_source_keeps_partial_for_next_source(self):
        self.part.write_bytes(DATA[:MB])
        s = FakeServer()

        def urlopen(req, timeout=None):
            if req.full_url.endswith('/a'):
                s.requests.append({'range': req.get_header('Range'), 'status': None, 'sent': 0})
                raise urllib.error.URLError('connection refused')
            return s(req, timeout)
        self.download(urlopen)
        self.assert_installed_ok()
        self.assertEqual([r['range'] for r in s.requests], [f'bytes={MB}-'] * 2)
        self.assertEqual(sum(r['sent'] for r in s.requests), len(DATA) - MB)

    # known size: a complete .part is installed with no request; a .part longer than the size is discarded
    def test_known_size_complete_partial_needs_no_request(self):
        configure(self.models, [('http://127.0.0.1:9/a', SHA)], {SHA: len(DATA)})
        self.part.write_bytes(DATA)
        s = FakeServer()
        self.download(s)
        self.assert_installed_ok()
        self.assertEqual(s.requests, [])

    def test_known_size_oversized_partial_restarts_from_zero(self):
        configure(self.models, [('http://127.0.0.1:9/a', SHA)], {SHA: len(DATA)})
        self.part.write_bytes(DATA + b'extra')
        s = FakeServer()
        self.download(s)
        self.assert_installed_ok()
        self.assertEqual([r['range'] for r in s.requests], [None])

    def test_known_size_short_download_keeps_partial(self):
        configure(self.models, [('http://127.0.0.1:9/a', SHA)], {SHA: len(DATA)})
        self.try_download(FakeServer(cut=MB, no_length=True))
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.part.read_bytes(), DATA[:MB])

    def test_server_size_different_from_pinned_size_is_rejected(self):
        configure(self.models, [('http://127.0.0.1:9/a', SHA)], {SHA: len(DATA) + 1})
        with self.assertRaises(SystemExit):
            self.download(FakeServer())
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.part.exists())


class WrongContent(DownloadCase):
    """Bytes of the right length but the wrong content must never be installed, on any path that skips a download."""

    # length unknown, .part is full length but wrong, the server answers 416: hash it, drop it, fetch from 0
    def test_416_on_full_length_wrong_partial_hashes_and_restarts(self):
        self.one_source()
        self.part.write_bytes(WRONG)
        s = FakeServer()
        self.download(s)
        self.assert_installed_ok()
        self.assertEqual([(r['range'], r['status']) for r in s.requests], [(f'bytes={len(DATA)}-', 416), (None, 200)])
        self.assertFalse(self.part.exists())

    # length known, .part is full length but wrong: no Range request, hash it, drop it, fetch from 0
    def test_known_size_full_length_wrong_partial_hashes_and_restarts(self):
        self.one_source({SHA: len(DATA)})
        self.part.write_bytes(WRONG)
        s = FakeServer()
        with mock.patch.object(T.hashlib, 'sha256', wraps=hashlib.sha256) as sha:
            self.download(s)
        self.assertGreaterEqual(sha.call_count, 2, 'both the old .part and the new download must be hashed')
        self.assert_installed_ok()
        self.assertEqual([r['range'] for r in s.requests], [None])
        self.assertFalse(self.part.exists())

    # length known, the only source sends full-length wrong bytes: fail, leave no final file and no .part
    def test_known_size_only_source_with_wrong_content_fails_clean(self):
        self.one_source({SHA: len(DATA)})
        s = FakeServer(wrong=True)
        with self.assertRaises(SystemExit):
            self.download(s)
        self.assertEqual([(r['range'], r['sent']) for r in s.requests], [(None, len(DATA))])
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.part.exists())
        self.assertFalse(T._marker(self.dest).exists())

    # one source, corrupt .part: the resume fails the hash, the same source is asked again from 0 and installs
    def test_single_source_bad_resume_retries_same_source_from_zero(self):
        self.one_source()
        self.part.write_bytes(b'\0' * MB)
        s = FakeServer()
        self.download(s)
        self.assert_installed_ok()
        self.assertEqual([r['range'] for r in s.requests], [f'bytes={MB}-', None])
        self.assertEqual(sum(r['sent'] for r in s.requests), 2 * len(DATA) - MB)
        self.assertFalse(self.part.exists())


class InstalledFile(DownloadCase):
    """The final file is no longer trusted just because it exists (old L77)."""

    def test_existing_valid_file_is_hashed_once_then_trusted_by_marker(self):
        self.dest.write_bytes(DATA)
        s = FakeServer()
        with mock.patch.object(T.hashlib, 'sha256', wraps=hashlib.sha256) as sha:
            self.download(s)
        self.assertTrue(sha.called, 'an unverified existing file must be hashed once')
        self.assertEqual(s.requests, [])
        with mock.patch.object(T.hashlib, 'sha256', wraps=hashlib.sha256) as sha:
            self.assertEqual(self.download(s), self.dest)
        self.assertFalse(sha.called, 'an unchanged verified file must not be hashed again')
        self.assertEqual(s.requests, [])

    def test_corrupt_existing_file_is_moved_aside_and_downloaded(self):
        bad = b'\0' * MB + DATA[MB:]
        self.dest.write_bytes(bad)
        s = FakeServer()
        self.download(s)
        self.assert_installed_ok()
        self.assertEqual(sum(r['sent'] for r in s.requests), len(DATA))
        self.assertEqual(self.dest.with_name(self.dest.name + '.bad').read_bytes(), bad)

    def test_changed_file_after_verification_is_checked_again(self):
        self.download(FakeServer())
        self.assert_installed_ok()
        st = self.dest.stat()
        self.dest.write_bytes(b'\0' * len(DATA))                        # same size, new content
        os.utime(self.dest, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        s = FakeServer()
        self.download(s)
        self.assert_installed_ok()
        self.assertEqual(sum(r['sent'] for r in s.requests), len(DATA))

    def test_marker_from_another_file_is_not_trusted(self):
        self.download(FakeServer())
        marker = json.loads(self.dest.with_name(self.dest.name + '.verified').read_text())
        self.assertEqual(marker['sha256'], SHA)
        self.dest.unlink()
        self.dest.write_bytes(b'\1' * len(DATA))                        # new file, maybe the same mtime
        st = self.dest.stat()
        marker.update(size=st.st_size, mtime_ns=st.st_mtime_ns, sha256='0' * 64)
        self.dest.with_name(self.dest.name + '.verified').write_text(json.dumps(marker))
        s = FakeServer()
        self.download(s)
        self.assert_installed_ok()
        self.assertEqual(sum(r['sent'] for r in s.requests), len(DATA))

    def test_existing_file_with_wrong_pinned_size_is_replaced(self):
        configure(self.models, [('http://127.0.0.1:9/a', SHA)], {SHA: len(DATA)})
        self.dest.write_bytes(DATA + b'x')
        self.download(FakeServer())
        self.assert_installed_ok()

    def test_every_pinned_source_has_a_size(self):
        T.MODEL_FILES = self._real_files
        T.MODEL_SIZES = self._real_sizes
        for rel, sources in T.MODEL_FILES.items():
            for url, expected in sources:
                self.assertIsInstance(T.MODEL_SIZES.get(expected), int, f'{rel} <- {url}')
                self.assertGreater(T.MODEL_SIZES[expected], 0)

    def setUp(self):
        self._real_files, self._real_sizes = T.MODEL_FILES, getattr(T, 'MODEL_SIZES', {})
        super().setUp()


class Lock(Isolated):
    def setUp(self):
        super().setUp()
        configure(self.models, [('http://127.0.0.1:9/a', SHA)])

    def test_lock_wait_times_out_with_clear_error(self):
        holder = subprocess.Popen([sys.executable, '-B', '-c', HOLD_LOCK, str(Path(__file__).parent), str(self.models)],
                                  env=child_env(self.home), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(holder.communicate)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), 'locked', holder.stderr.read() if holder.poll() else '')
        s = FakeServer()
        with mock.patch.object(T, 'DOWNLOAD_LOCK_TIMEOUT', 0.5), \
                mock.patch.object(urllib.request, 'urlopen', side_effect=s), \
                mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            started = time.monotonic()
            with self.assertRaises(SystemExit):
                T.download(REL)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn('Timed out', err.getvalue())
        self.assertEqual(s.requests, [], 'nothing may be fetched without the lock')
        self.assertFalse((self.models / REL).exists())

    def test_lock_name_keeps_sub_directories_apart(self):
        names = {T._download_lock_path(rel).name for rel in ('a/b.onnx', 'a_b.onnx', 'a\\b.onnx')}
        self.assertEqual(len(names), 3)
        for rel in ('sensevoice/model.int8.onnx', 'silero_vad.onnx'):
            path = T._download_lock_path(rel)
            self.assertEqual(path.parent, self.models / '.locks')
            self.assertNotIn('/', path.name)
            self.assertNotIn('\\', path.name)

    @unittest.skipUnless(os.name == 'nt', 'msvcrt is Windows-only')
    def test_windows_download_takes_and_releases_an_msvcrt_lock(self):
        import msvcrt
        with mock.patch.object(msvcrt, 'locking', wraps=msvcrt.locking) as locking, \
                mock.patch.object(urllib.request, 'urlopen', side_effect=FakeServer()), \
                mock.patch('sys.stderr', new_callable=io.StringIO):
            T.download(REL)
        modes = [call.args[1] for call in locking.call_args_list]
        self.assertIn(msvcrt.LK_NBLCK, modes)
        self.assertEqual(modes[-1], msvcrt.LK_UNLCK)

    @unittest.skipIf(os.name == 'nt', 'flock is POSIX-only')
    def test_posix_download_takes_an_flock(self):
        import fcntl
        with mock.patch.object(fcntl, 'flock', wraps=fcntl.flock) as flock, \
                mock.patch.object(urllib.request, 'urlopen', side_effect=FakeServer()), \
                mock.patch('sys.stderr', new_callable=io.StringIO):
            T.download(REL)
        self.assertIn(fcntl.LOCK_EX | fcntl.LOCK_NB, [call.args[1] for call in flock.call_args_list])


class SlowHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    log: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        rng = self.headers.get('Range')
        start = int(rng.split('=')[1].split('-')[0]) if rng else 0
        body = DATA[start:]
        self.send_response(206 if start else 200)
        if start:
            self.send_header('Content-Range', f'bytes {start}-{len(DATA) - 1}/{len(DATA)}')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        rec = {'path': self.path, 'range': rng, 'sent': 0}
        SlowHandler.log.append(rec)                  # logged on arrival, `sent` updated while sending
        try:
            while rec['sent'] < len(body):
                n = min(128 << 10, len(body) - rec['sent'])
                self.wfile.write(body[rec['sent']:rec['sent'] + n])
                self.wfile.flush()
                rec['sent'] += n
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class TwoProcesses(Isolated):
    def setUp(self):
        super().setUp()
        SlowHandler.log = []
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), SlowHandler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        base = f'http://127.0.0.1:{self.srv.server_address[1]}'
        self.urls = [f'{base}/a', f'{base}/b']
        self.part = (self.models / REL).with_suffix(Path(REL).suffix + '.part')

    def spawn(self):
        p = subprocess.Popen([sys.executable, '-B', '-c', CHILD, str(Path(__file__).parent), str(self.models), *self.urls],
                             env=child_env(self.home), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: p.poll() is None and (p.kill(), p.communicate()))
        return p

    def wait_part(self, n, timeout=15):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.part.exists() and self.part.stat().st_size >= n:
                return
            time.sleep(0.01)
        self.fail("A's .part did not start growing")

    # 7 two processes download the same model: fetched once, both return a verified file
    def test_two_processes_download_once_and_both_get_verified_file(self):
        a = self.spawn()
        self.wait_part(MB)
        b = self.spawn()
        outs = [p.communicate(timeout=120) for p in (a, b)]
        time.sleep(0.3)
        for (out, err), p in zip(outs, (a, b)):
            self.assertEqual(p.returncode, 0, err)
            self.assertTrue(json.loads(out.strip().splitlines()[-1])['sha_ok_at_return'], 'wrong file at return')
            self.assertNotIn('No such file or directory', err)
        full = [r for r in SlowHandler.log if r['sent'] == len(DATA)]
        self.assertEqual(len(full), 1, f'the model was fully downloaded {len(full)} times: {SlowHandler.log}')

    # 8 the lock holder is killed: the lock goes with it, and the waiter resumes the .part
    def test_lock_released_when_holder_killed_and_waiter_resumes(self):
        a = self.spawn()
        self.wait_part(MB)
        b = self.spawn()
        time.sleep(0.3)
        a.kill()
        a.communicate()
        out, err = b.communicate(timeout=120)
        time.sleep(0.3)
        self.assertEqual(b.returncode, 0, err)
        self.assertTrue(json.loads(out.strip().splitlines()[-1])['sha_ok_at_return'])
        self.assertTrue(any(r['range'] for r in SlowHandler.log), f"B did not resume A's .part: {SlowHandler.log}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
