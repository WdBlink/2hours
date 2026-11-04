"""write_json retries os.replace only on PermissionError, as Blurt v1.0.12 write_text_atomic does on Windows."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_dispatch as fixture  # noqa: E402

dispatch = fixture.dispatch


class ReplaceSpy:
    def __init__(self, fail_first=0, error=PermissionError):
        self.real = os.replace
        self.fail_first = fail_first
        self.error = error
        self.calls = []

    def __call__(self, source, target):
        if len(self.calls) < self.fail_first:
            self.calls.append(self.error.__name__)
            raise self.error(13, "simulated", str(target))
        try:
            self.real(source, target)
        except PermissionError:
            self.calls.append("PermissionError")
            raise
        self.calls.append("ok")


class WriteJsonRetryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.path = self.dir / "dispatch-state.json"
        dispatch.write_json(self.path, {"old": True})
        self.before = self.path.read_bytes()

    def leftovers(self):
        return sorted(p.name for p in self.dir.iterdir() if p.name != self.path.name)

    def test_retries_permission_error_then_replaces(self):
        spy = ReplaceSpy(fail_first=3)
        with patch.object(dispatch.os, "replace", spy), patch.object(dispatch.time, "sleep") as sleep:
            dispatch.write_json(self.path, {"new": True})
        self.assertEqual(spy.calls, ["PermissionError"] * 3 + ["ok"])
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual(dispatch.read_json(self.path), {"new": True})
        self.assertEqual(self.leftovers(), [])

    def test_gives_up_after_bounded_retries_and_removes_temp_file(self):
        spy = ReplaceSpy(fail_first=10 ** 6)
        with patch.object(dispatch.os, "replace", spy), patch.object(dispatch.time, "sleep"):
            with self.assertRaises(PermissionError):
                dispatch.write_json(self.path, {"new": True})
        self.assertEqual(len(spy.calls), dispatch.REPLACE_ATTEMPTS)
        self.assertEqual(dispatch.REPLACE_ATTEMPTS, 20)
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(self.leftovers(), [])

    def test_other_errors_are_not_retried(self):
        spy = ReplaceSpy(fail_first=1, error=IsADirectoryError)
        with patch.object(dispatch.os, "replace", spy), patch.object(dispatch.time, "sleep") as sleep:
            with self.assertRaises(IsADirectoryError):
                dispatch.write_json(self.path, {"new": True})
        self.assertEqual(spy.calls, ["IsADirectoryError"])
        self.assertEqual(sleep.call_count, 0)
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(self.leftovers(), [])

    @unittest.skipUnless(os.name == "nt", "Windows sharing rules")
    def test_windows_reader_holding_target_is_retried(self):
        spy = ReplaceSpy()
        reader = open(self.path, "rb")
        timer = threading.Timer(0.3, reader.close)
        timer.start()
        self.addCleanup(timer.cancel)
        self.addCleanup(reader.close)
        with patch.object(dispatch.os, "replace", spy):
            dispatch.write_json(self.path, {"new": True})
        retries = spy.calls.count("PermissionError")
        print(f"\nwindows write_json retry: {len(spy.calls)} os.replace calls, {retries} PermissionError before success",
              file=sys.stderr, flush=True)
        self.assertGreater(retries, 0)
        self.assertEqual(spy.calls[-1], "ok")
        self.assertEqual(dispatch.read_json(self.path), {"new": True})
        self.assertEqual(self.leftovers(), [])

    @unittest.skipUnless(os.name == "nt", "Windows sharing rules")
    def test_windows_reader_holding_target_too_long_keeps_old_file(self):
        spy = ReplaceSpy()
        with open(self.path, "rb"):
            with patch.object(dispatch.os, "replace", spy):
                with self.assertRaises(PermissionError):
                    dispatch.write_json(self.path, {"new": True})
        print(f"\nwindows write_json gave up after {len(spy.calls)} os.replace calls", file=sys.stderr, flush=True)
        self.assertEqual(spy.calls, ["PermissionError"] * dispatch.REPLACE_ATTEMPTS)
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()
