"""Config file safety: per-process temp files, Windows replace retries, unreadable config backed up once.

Ported behaviour from AGIHunt/blurt (MIT) eb2bf801 and 88db1f74; cases follow devdesk 2hours__upstream-blurt-eb2bf80.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "vendor/blurt/scripts"
sys.path.insert(0, str(SCRIPTS))
import _common  # noqa: E402
SPEC = importlib.util.spec_from_file_location("config_safety_record", SCRIPTS / "record.py")
record = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(record)

BROKEN = b'{"record": {"last_region": [10, 20,'   # a config cut off mid-write


class Sentinel(Exception):
    pass


class ConfigSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cfg = self.home / "config.json"
        self.patches = [patch.object(_common, "GLOBAL_CONFIG", self.cfg),
                        patch.object(record, "STATE_FILE", self.home / "recording.json")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def backups(self):
        return sorted(p for p in self.home.iterdir() if p.name.startswith("config.json.bad"))

    # 1. Unreadable config: reads give defaults, the original is backed up once
    def test_corrupt_config_reads_as_defaults_and_is_backed_up_once(self):
        self.cfg.write_bytes(BROKEN)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(_common.global_config(), {})
            self.assertEqual(_common.global_config(), {})
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].read_bytes(), BROKEN)
        lines = [line for line in err.getvalue().splitlines() if "config_unreadable" in line]
        self.assertEqual(len(lines), 1, err.getvalue())
        self.assertEqual(self.cfg.read_bytes(), BROKEN)

    # 2. A top-level value that is not an object counts as unreadable
    def test_non_object_json_reads_as_defaults(self):
        self.cfg.write_text("[1]\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(_common.global_config(), {})

    # 3. record.py start gets past reading the config and reaches engine selection
    def test_start_gets_past_config_with_corrupt_file(self):
        self.cfg.write_bytes(BROKEN)
        a = SimpleNamespace(session=str(self.home / "session"), inbox=False, engine=None, region=None,
                            last_region=False, system_audio=False)
        with patch.object(record, "choose_engine", side_effect=Sentinel), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(Sentinel):
                record.cmd_start(a)

    # 4. Writing a setting must not replace an unreadable config with a partial one
    def test_update_does_not_overwrite_unreadable_config(self):
        self.cfg.write_bytes(BROKEN)
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                _common.update_global_config({"record": {"last_region": [0, 0, 100, 100]}})
            except Exception as e:  # a clear refusal is allowed, a raw JSONDecodeError is not
                self.assertNotIsInstance(e, json.JSONDecodeError)
        backups = self.backups()
        self.assertTrue(self.cfg.read_bytes() == BROKEN or (backups and backups[0].read_bytes() == BROKEN),
                        "the unreadable config was neither kept nor backed up")

    # 4b. Convenience writes (skill_dir, last_region) skip quietly and leave the file as it is
    def test_convenience_write_skips_unreadable_config(self):
        self.cfg.write_bytes(BROKEN)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(_common.save_config_if_readable({"record": {"last_region": [0, 0, 1, 1]}}))
        self.assertEqual(self.cfg.read_bytes(), BROKEN)
        self.cfg.write_text('{"lang": "zh"}', encoding="utf-8")
        self.assertTrue(_common.save_config_if_readable({"record": {"last_region": [0, 0, 1, 1]}}))
        self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8")),
                         {"lang": "zh", "record": {"last_region": [0, 0, 1, 1]}})

    # 1b. The backup of a private (0600) config is private too: it may hold secrets such as feishu app_secret
    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_backup_of_private_config_is_0600(self):
        self.cfg.write_bytes(b'{"feishu": {"app_secret": "s3cret"},')
        self.cfg.chmod(0o600)
        old_umask = os.umask(0o022)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(_common.global_config(), {})
        finally:
            os.umask(old_umask)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cfg.stat().st_mode & 0o777, 0o600)

    # 4c. A finished recording is not failed by an unreadable config: last_region / skill_dir writes are skipped
    def test_finish_session_with_unreadable_config(self):
        self.cfg.write_bytes(BROKEN)
        session = self.home / "session"
        session.mkdir()
        (session / "recording.mp4").write_bytes(b"\0" * 16)
        result = {"event": "stopped", "file": "recording.mp4", "region": [1, 2, 300, 200]}
        stdout = io.StringIO()
        with patch.object(record, "probe_duration", return_value=12.5), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            record.finish_session(session, result, {"started_at": "2026-11-02T21:00:00+08:00", "engine": "tk"})
            record.remember_skill_dir()
        meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["video"], "recording.mp4")
        self.assertEqual(meta["duration"], 12.5)
        self.assertEqual(meta["region"], [1, 2, 300, 200])
        self.assertIn("DONE video=", stdout.getvalue())
        self.assertEqual(self.cfg.read_bytes(), BROKEN)

    # 5. Per-process temp file: another writer's config.json.tmp is left alone
    def test_save_does_not_touch_another_writers_tmp(self):
        foreign = self.cfg.with_suffix(".json.tmp")
        foreign.write_text("another process is still writing", encoding="utf-8")
        _common.save_json(self.cfg, {"a": 1})
        self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8")), {"a": 1})
        self.assertTrue(foreign.exists())
        self.assertEqual(foreign.read_text(encoding="utf-8"), "another process is still writing")

    # 6. Windows contention, mocked so it runs everywhere: PermissionError a few times, then success
    def test_save_retries_permission_error_then_succeeds(self):
        real = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise PermissionError(13, "The process cannot access the file")
            return real(src, dst)
        with patch.object(_common.os, "replace", side_effect=flaky), patch("time.sleep"):
            _common.save_json(self.cfg, {"b": 2})
        self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8")), {"b": 2})
        self.assertGreaterEqual(calls["n"], 4)

    # 7. Retries are bounded, the error surfaces, and this process's temp file is removed
    def test_save_gives_up_after_bounded_retries(self):
        with patch.object(_common.os, "replace", side_effect=PermissionError(13, "held")) as rep, patch("time.sleep"):
            with self.assertRaises(PermissionError):
                _common.save_json(self.cfg, {"c": 3})
        self.assertLessEqual(rep.call_count, 50)
        self.assertEqual([p.name for p in self.home.iterdir() if p.name.endswith(".tmp")], [],
                         "the temp file must be removed when giving up")

    # 8. Real Windows contention: a reader holds config.json (Python open has no FILE_SHARE_DELETE) for 0.3 s
    @unittest.skipUnless(os.name == "nt", "needs Windows file-sharing semantics; runs on the windows-latest CI")
    def test_windows_save_while_reader_holds_file(self):
        import threading
        import time
        _common.save_json(self.cfg, {"old": 1})
        fh = open(self.cfg, "rb")
        timer = threading.Timer(0.3, fh.close)
        timer.start()
        try:
            t0 = time.monotonic()
            _common.save_json(self.cfg, {"new": 1})
            self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8")), {"new": 1})
            self.assertLess(time.monotonic() - t0, 5)
        finally:
            timer.join()
            fh.close()


class ConcurrentSaveTests(unittest.TestCase):
    # 9. Two processes saving at once: no writer errors and the file still parses
    #    (lost updates are out of scope; preventing them needs a cross-process lock)
    def test_two_processes_saving_leave_parseable_config(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import _common, json\n"
                "from pathlib import Path\n"
                "p = Path(sys.argv[2]); errs = 0\n"
                "for j in range(50):\n"
                "    try: _common.save_json(p, {'w': sys.argv[3], 'j': j, 'pad': 'x' * (j % 7) * 10})\n"
                "    except Exception as e: errs += 1; print(type(e).__name__, e, file=sys.stderr)\n"
                "sys.exit(1 if errs else 0)\n")
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.json"
            ps = [subprocess.Popen([sys.executable, "-B", "-c", code, str(SCRIPTS), str(cfg), str(i)],
                                   stderr=subprocess.PIPE, text=True) for i in range(2)]
            errs = [p.communicate()[1] for p in ps]
            self.assertEqual([p.returncode for p in ps], [0, 0], errs)
            json.loads(cfg.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
