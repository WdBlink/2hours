"""Native Windows deletion invariants, including junction and handle races."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("windows_settings", Path(__file__).parents[1] / "scripts/settings.py")
settings = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(settings)


@unittest.skipUnless(os.name == "nt", "Windows handle API")
class WindowsRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = settings.RetentionSettings(self.root / "home")
        self.run = self.store.runs / "example"
        self.session = self.run / "blurt"
        self.session.mkdir(parents=True)
        (self.run / "manifest.json").write_text(json.dumps({"run_id": "example", "panels": []}))
        self.video = self.session / "recording.mp4"
        self.video.write_bytes(b"reviewed recording")
        os.utime(self.video, (1000, 1000))
        self.processed()
        self.preview = self.store.save({"retention": "after_review", "revision": 0})

    def processed(self, reviewed=True):
        (self.session / "items.json").write_text(json.dumps({"items": [], "reviewed": reviewed}))

    def cleanup(self):
        return self.store.cleanup({"preview_id": self.preview["preview_id"]})

    def junction(self, link, target):
        result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_cleanup_removes_only_pinned_regular_media(self):
        keep = self.session / "transcript.json"
        keep.write_text("keep")
        result = self.cleanup()
        self.assertEqual(result["deleted"], ["example/blurt/recording.mp4"])
        self.assertEqual(result["failed"], [])
        self.assertFalse(self.video.exists())
        self.assertTrue(keep.exists())

    def test_junction_run_is_excluded_even_without_symlink_privilege(self):
        outside = self.root / "outside"
        (outside / "blurt").mkdir(parents=True)
        external = outside / "blurt/recording.mp4"
        external.write_bytes(b"external")
        self.junction(self.store.runs / "linked", outside)
        preview = self.store.preview()
        self.assertTrue(preview["warnings"])
        self.assertFalse(settings.safe(self.store.runs / "linked/blurt"))
        self.store.cleanup({"preview_id": preview["preview_id"]})
        self.assertEqual(external.read_bytes(), b"external")

    def test_junction_substitution_after_preview_is_rejected(self):
        moved = self.root / "original"
        self.session.rename(moved)
        self.junction(self.session, moved)
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.cleanup()
        self.assertEqual((moved / "recording.mp4").read_bytes(), b"reviewed recording")

    def test_hardlink_media_is_never_eligible(self):
        external = self.root / "external.wav"
        external.write_bytes(b"keep")
        os.link(external, self.session / "audio.wav")
        preview = self.store.preview()
        self.assertFalse(next(r for r in preview["files"] if r["path"].endswith("audio.wav"))["eligible"])
        self.store.cleanup({"preview_id": preview["preview_id"]})
        self.assertEqual(external.read_bytes(), b"keep")
        self.assertTrue((self.session / "audio.wav").exists())

    def test_junction_inserted_during_parent_open_cannot_redirect_delete(self):
        original_open = settings.windows_files.checked_handle
        moved = self.root / "moved"
        attacked = []

        def substitute(path, **kwargs):
            if kwargs.get("directory") and Path(path) == self.session and not attacked:
                self.session.rename(moved)
                self.junction(self.session, moved)
                attacked.append(True)
            return original_open(path, **kwargs)

        with patch.object(settings.windows_files, "checked_handle", side_effect=substitute):
            result = self.cleanup()
        self.assertTrue(attacked)
        self.assertEqual(result["deleted"], [])
        self.assertTrue(result["failed"])
        self.assertEqual((moved / "recording.mp4").read_bytes(), b"reviewed recording")

    def test_changed_media_before_exclusive_open_is_preserved(self):
        original_open = settings.windows_files.checked_handle
        old = self.session / "old.mp4"
        attacked = []

        def substitute(path, **kwargs):
            if Path(path) == self.video and not kwargs.get("preview") and not attacked:
                self.video.rename(old)
                self.video.write_bytes(b"unreviewed replacement")
                attacked.append(True)
            return original_open(path, **kwargs)

        with patch.object(settings.windows_files, "checked_handle", side_effect=substitute):
            result = self.cleanup()
        self.assertTrue(attacked)
        self.assertEqual(result["deleted"], [])
        self.assertIn("文件已变化", result["failed"][0]["error"])
        self.assertEqual(old.read_bytes(), b"reviewed recording")
        self.assertEqual(self.video.read_bytes(), b"unreviewed replacement")

    def test_handle_excludes_parent_rename_file_replacement_and_writes(self):
        original = self.store.check_cleanup_protection
        attempts = []

        def attack(run, info):
            for operation in (lambda: self.session.rename(self.root / "moved"),
                              lambda: self.video.rename(self.session / "old.mp4"),
                              lambda: self.video.write_bytes(b"unreviewed")):
                with self.assertRaises(OSError):
                    operation()
                attempts.append(True)
            return original(run, info)

        with patch.object(self.store, "check_cleanup_protection", side_effect=attack):
            result = self.cleanup()
        self.assertEqual(len(attempts), 6)
        self.assertEqual(result["deleted"], ["example/blurt/recording.mp4"])
        self.assertEqual(result["failed"], [])

    def test_revoke_review_between_checks_preserves_media(self):
        original = self.store.check_cleanup_protection
        calls = []

        def revoke(run, info):
            original(run, info)
            if not calls:
                self.processed(False)
            calls.append(True)

        with patch.object(self.store, "check_cleanup_protection", side_effect=revoke):
            result = self.cleanup()
        self.assertEqual(result["deleted"], [])
        self.assertIn("尚未完成审核", result["failed"][0]["error"])
        self.assertEqual(self.video.read_bytes(), b"reviewed recording")

    def test_reader_without_delete_sharing_preserves_media(self):
        with self.video.open("rb"):
            result = self.cleanup()
        self.assertEqual(result["deleted"], [])
        self.assertTrue(result["failed"])
        self.assertTrue(self.video.exists())

    def test_same_size_write_with_restored_mtime_invalidates_preview(self):
        before = self.video.stat()
        self.video.write_bytes(b"unreviewed content")  # Same length as the original.
        os.utime(self.video, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.cleanup()
        self.assertEqual(self.video.read_bytes(), b"unreviewed content")

    def test_process_probe_is_read_only(self):
        child = subprocess.Popen([os.sys.executable, "-c", "import time; time.sleep(20)"])
        try:
            self.assertTrue(settings.process_alive(child.pid))
            self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=5)
        self.assertFalse(settings.process_alive(child.pid))


if __name__ == "__main__":
    unittest.main()
