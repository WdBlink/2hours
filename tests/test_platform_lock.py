"""The dispatch lock must serialize independent Python processes on every OS."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'dispatch.py'
SPEC = importlib.util.spec_from_file_location('platform_dispatch', SCRIPT)
dispatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dispatch)


class PlatformLockTests(unittest.TestCase):
    def test_second_process_waits_until_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = '''import importlib.util, pathlib, sys
s = importlib.util.spec_from_file_location('d', sys.argv[1])
d = importlib.util.module_from_spec(s)
s.loader.exec_module(d)
print('ready', flush=True)
with d.locked(pathlib.Path(sys.argv[2])):
    print('acquired', flush=True)
'''
            with dispatch.locked(root):
                child = subprocess.Popen([sys.executable, '-c', code, str(SCRIPT), directory],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(child.stdout.readline().strip(), 'ready')
                    with self.assertRaises(subprocess.TimeoutExpired):
                        child.communicate(timeout=0.3)
                except BaseException:
                    child.kill()
                    child.communicate()
                    raise
            output, error = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, error)
            self.assertIn('acquired', output)

    def test_lock_wait_times_out_with_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            code = '''import importlib.util, pathlib, sys
s = importlib.util.spec_from_file_location('d', sys.argv[1])
d = importlib.util.module_from_spec(s)
s.loader.exec_module(d)
with d.locked(pathlib.Path(sys.argv[2])):
    print('held', flush=True)
    sys.stdin.read()
'''
            holder = subprocess.Popen([sys.executable, '-c', code, str(SCRIPT), directory], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), 'held')
                started = time.monotonic()
                with self.assertRaisesRegex(dispatch.DispatchError, 'Timed out after 0.3s waiting for lock'):
                    with dispatch.locked(Path(directory), timeout=0.3):
                        self.fail('lock acquired while another process holds it')
                self.assertLess(time.monotonic() - started, 5)
            finally:
                holder.communicate(input='', timeout=10)
            self.assertEqual(holder.returncode, 0)
            with dispatch.locked(Path(directory), timeout=1):
                pass

    def test_lock_timeout_setting_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in ('abc', '0', '-1'):
                with mock.patch.dict(os.environ, {'TWOHOURS_LOCK_TIMEOUT': value}):
                    with self.assertRaises(dispatch.DispatchError):
                        with dispatch.locked(Path(directory)):
                            pass
            with mock.patch.dict(os.environ, {'TWOHOURS_LOCK_TIMEOUT': '2.5'}):
                self.assertEqual(dispatch.lock_timeout(), 2.5)

    def test_unicode_atomic_write_and_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'settings.json'
            dispatch.write_json(target, {'value': '中文'})
            dispatch.write_json(target, {'value': '更新'})
            self.assertEqual(dispatch.read_json(target), {'value': '更新'})
