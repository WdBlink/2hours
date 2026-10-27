import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("settings", Path(__file__).parents[1] / "scripts/settings.py")
settings = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(settings)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / ".2hours"
        self.store = settings.RetentionSettings(self.home)
        self.run = self.home / "runs/example"
        self.session = self.run / "blurt"
        self.session.mkdir(parents=True)
        (self.run / "manifest.json").write_text(json.dumps({"run_id": "example", "panels": []}))
        self.video = self.session / "recording.mp4"
        self.video.write_bytes(b"recording")
        os.utime(self.video, (1000, 1000))

    def tearDown(self):
        self.temp.cleanup()

    def processed(self, reviewed=True):
        (self.session / "items.json").write_text(json.dumps({"items": [], "reviewed": reviewed}))

    def policy(self, value):
        return self.store.save({"retention": value, "revision": self.store.read()["revision"]})

    def test_default_and_policy_changes_never_delete_media(self):
        self.processed()
        preview = self.store.preview()
        self.assertEqual(preview["settings"]["retention"], "keep")
        self.assertFalse(preview["files"][0]["eligible"])
        self.assertIsNone(preview["files"][0]["expires_at"])
        self.assertFalse(self.store.path.exists())
        for value in settings.POLICIES:
            self.policy(value)
            self.assertTrue(self.video.exists())
            self.assertEqual(settings.RetentionSettings(self.home).read()["retention"], value)

    def test_dispatch_default_opt_in_revoke_and_retention_preservation(self):
        before = self.store.read()
        self.assertEqual(before["dispatch_mode"], "manual")
        self.assertIsNone(before["dispatch_consent"])
        self.assertFalse(self.store.path.exists())
        self.policy("1_week")
        result = self.store.save({"dispatch_mode": "automatic", "revision": 1})["settings"]
        self.assertEqual(result["retention"], "1_week")
        first = result["dispatch_consent"]
        self.assertTrue(first["id"])
        self.assertIn("enabled_at", first)
        self.policy("2_days")
        self.assertEqual(self.store.read()["dispatch_consent"], first)
        self.store.save({"dispatch_mode": "manual", "revision": 3})
        self.assertIsNone(self.store.read()["dispatch_consent"])
        result = self.store.save({"dispatch_mode": "automatic", "revision": 4})["settings"]
        self.assertNotEqual(result["dispatch_consent"]["id"], first["id"])
        self.assertTrue(self.video.exists())

    def test_old_settings_read_as_manual_without_rewriting_them(self):
        self.store.path.write_text('{"retention":"2_days","revision":7}')
        original = self.store.path.read_bytes()
        self.assertEqual(self.store.read()["dispatch_mode"], "manual")
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_unprocessed_unreviewed_and_read_card_stay_protected(self):
        self.policy("after_review")
        self.assertIn("尚未处理", self.store.preview()["files"][0]["reason"])
        self.processed(False)
        (self.run / "review-state.json").write_text(json.dumps({"panels": {"A1": {"read": True}}}))
        preview = self.store.preview()
        self.assertIn("尚未完成审核", preview["files"][0]["reason"])
        self.assertEqual(self.store.cleanup({"preview_id": preview["preview_id"]})["deleted"], [])
        self.assertTrue(self.video.exists())

    def test_expiry_is_explicit_and_future_media_stays(self):
        self.processed()
        preview = self.policy("1_day")
        self.assertEqual(preview["files"][0]["expires_at"], "1970-01-02T00:16:40+00:00")
        os.utime(self.video, (4102444800, 4102444800))
        self.assertFalse(self.store.preview()["files"][0]["eligible"])

    def test_new_recording_cannot_reuse_old_reviewed_results(self):
        self.processed()
        preview = self.policy("after_review")
        self.assertTrue(preview["files"][0]["eligible"])
        self.video.write_bytes(b"new unprocessed recording")
        self.assertIn("需重新处理", self.store.preview()["files"][0]["reason"])

    def test_cleanup_only_removes_previewed_media_in_owned_runs(self):
        self.processed()
        transcript = self.session / "transcript.json"
        transcript.write_text("keep")
        frame = self.session / "frames/evidence.png"
        frame.parent.mkdir()
        frame.write_bytes(b"frame")
        external = self.root / "other-project.mp4"
        external.write_bytes(b"external")
        preview = self.policy("after_review")
        self.assertEqual(preview["eligible_bytes"], len(b"recording"))
        result = self.store.cleanup({"preview_id": preview["preview_id"]})
        self.assertEqual(result["deleted"], ["example/blurt/recording.mp4"])
        self.assertEqual(result["failed"], [])
        self.assertFalse(self.video.exists())
        for path in (transcript, frame, external, self.session / "items.json", self.run / "manifest.json"):
            self.assertTrue(path.exists())

    def test_stale_preview_rejects_file_settings_and_review_changes(self):
        self.processed()
        preview = self.policy("after_review")
        self.video.write_bytes(b"new recording")
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.store.cleanup({"preview_id": preview["preview_id"]})
        preview = self.store.preview()
        self.policy("keep")
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.store.cleanup({"preview_id": preview["preview_id"]})
        preview = self.policy("after_review")
        self.processed(False)
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.store.cleanup({"preview_id": preview["preview_id"]})
        self.assertTrue(self.video.exists())

    def test_symlinks_and_hardlinks_do_not_expand_cleanup_scope(self):
        self.processed()
        outside = self.root / "outside"
        outside.mkdir()
        external = outside / "recording.mp4"
        external.write_bytes(b"external")
        (self.home / "runs/linked-run").symlink_to(outside, target_is_directory=True)
        (self.session / "audio.wav").symlink_to(external)
        os.link(external, self.session / "video.mkv")
        preview = self.policy("after_review")
        self.assertEqual(len([row for row in preview["files"] if row["eligible"]]), 1)
        self.assertTrue(preview["warnings"])
        self.store.cleanup({"preview_id": preview["preview_id"]})
        self.assertEqual(external.read_bytes(), b"external")
        self.assertTrue((self.session / "audio.wav").is_symlink())

    def test_replacing_parent_with_symlink_after_preview_is_rejected(self):
        self.processed()
        preview = self.policy("after_review")
        moved = self.root / "moved"
        self.session.rename(moved)
        self.session.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.store.cleanup({"preview_id": preview["preview_id"]})
        self.assertTrue((moved / "recording.mp4").exists())

    def test_review_change_after_cleanup_preview_is_rechecked_under_run_lock(self):
        self.processed()
        preview = self.policy("after_review")
        original_preview = self.store.preview
        original_lock = settings.dispatch.locked
        held = []

        def review_changes_after_preview():
            result = original_preview()
            self.processed(False)
            return result

        @contextmanager
        def track_lock(directory):
            with original_lock(directory):
                held.append(directory)
                try:
                    yield
                finally:
                    held.remove(directory)

        original_check = self.store.check_cleanup_protection

        def check_while_locked(run, info):
            self.assertIn(self.home, held)
            self.assertIn(self.run, held)
            return original_check(run, info)

        with patch.object(self.store, "preview", side_effect=review_changes_after_preview), \
                patch.object(settings.dispatch, "locked", side_effect=track_lock), \
                patch.object(self.store, "check_cleanup_protection", side_effect=check_while_locked):
            result = self.store.cleanup({"preview_id": preview["preview_id"]})
        self.assertEqual(result["deleted"], [])
        self.assertIn("尚未完成审核", result["failed"][0]["error"])
        self.assertEqual(self.video.read_bytes(), b"recording")

    def test_replacement_between_stat_and_move_is_preserved_with_recovery_path(self):
        self.processed()
        preview = self.policy("after_review")
        original_rename = settings.os.rename
        old_video = self.session / "original.mp4"

        def replace_then_move(source, destination, **kwargs):
            original_rename(self.video, old_video)
            self.video.write_bytes(b"new unreviewed recording")
            return original_rename(source, destination, **kwargs)

        with patch.object(settings.os, "rename", side_effect=replace_then_move):
            result = self.store.cleanup({"preview_id": preview["preview_id"]})
        self.assertEqual(result["deleted"], [])
        self.assertEqual(old_video.read_bytes(), b"recording")
        recovered = Path(result["failed"][0]["recovery_path"])
        self.assertEqual(recovered.read_bytes(), b"new unreviewed recording")
        self.assertIn(".retention-recovery-", str(recovered))
        self.assertFalse(self.video.exists())

    def test_review_change_during_move_preserves_media(self):
        self.processed()
        preview = self.policy("after_review")
        original_rename = settings.os.rename

        def move_then_revoke(source, destination, **kwargs):
            result = original_rename(source, destination, **kwargs)
            self.processed(False)
            return result

        with patch.object(settings.os, "rename", side_effect=move_then_revoke):
            result = self.store.cleanup({"preview_id": preview["preview_id"]})
        self.assertEqual(result["deleted"], [])
        self.assertIn("尚未完成审核", result["failed"][0]["error"])
        self.assertEqual(Path(result["failed"][0]["recovery_path"]).read_bytes(), b"recording")

    def test_recording_start_after_cleanup_preview_preserves_media(self):
        self.processed()
        preview = self.policy("after_review")
        original_preview = self.store.preview

        def start_after_preview():
            result = original_preview()
            state = self.home / "recorder/recording.json"
            state.parent.mkdir(exist_ok=True)
            state.write_text(json.dumps({"pid": os.getpid(), "session": str(self.session)}))
            return result

        with patch.object(self.store, "preview", side_effect=start_after_preview):
            result = self.store.cleanup({"preview_id": preview["preview_id"]})
        self.assertEqual(result["deleted"], [])
        self.assertIn("录制进行中", result["failed"][0]["error"])
        self.assertEqual(self.video.read_bytes(), b"recording")

    def test_new_original_path_after_move_is_not_deleted(self):
        self.processed()
        preview = self.policy("after_review")
        original_rename = settings.os.rename

        def move_then_record(source, destination, **kwargs):
            result = original_rename(source, destination, **kwargs)
            self.video.write_bytes(b"new recording")
            return result

        with patch.object(settings.os, "rename", side_effect=move_then_record):
            result = self.store.cleanup({"preview_id": preview["preview_id"]})
        self.assertEqual(result["deleted"], ["example/blurt/recording.mp4"])
        self.assertEqual(self.video.read_bytes(), b"new recording")
        self.assertFalse(list(self.run.glob(".retention-recovery-*")))

    def test_active_or_unknown_recorder_state_protects_media(self):
        self.processed()
        self.policy("after_review")
        state = self.home / "recorder/recording.json"
        state.parent.mkdir()
        state.write_text(json.dumps({"pid": 123, "session": str(self.session)}))
        with patch.object(settings.os, "kill", return_value=None):
            self.assertFalse(self.store.preview()["files"][0]["eligible"])
        state.write_text("[]")
        self.assertFalse(self.store.preview()["files"][0]["eligible"])

    def test_invalid_settings_and_paths_never_write_or_delete_elsewhere(self):
        for body in ({"retention": [], "revision": 0}, {"retention": "all", "revision": 0},
                     {"retention": "keep", "revision": True}, {"retention": "keep", "revision": 0, "path": str(self.root)}):
            with self.assertRaises(ValueError):
                self.store.save(body)
        self.store.path.symlink_to(self.root / "outside-settings.json")
        with self.assertRaises(ValueError):
            self.store.save({"retention": "1_day", "revision": 0})
        self.assertFalse((self.root / "outside-settings.json").exists())
        self.assertTrue(self.video.exists())


if __name__ == "__main__":
    unittest.main()
