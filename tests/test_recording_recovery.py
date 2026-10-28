import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("recovery_record", ROOT / "vendor/blurt/scripts/record.py")
record = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(record)


class RecordingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = self.root / "session with spaces"
        self.session.mkdir()
        self.video = self.session / "recording.mp4"
        self.video.write_bytes(b"original fragmented video")
        state_patch = patch.object(record, "STATE_FILE", self.root / "recording.json")
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def commands(self, argv, **kwargs):
        if argv[0] == "/bin/ps":
            return SimpleNamespace(stdout="", returncode=0)
        if "-count_frames" in argv:
            return SimpleNamespace(stdout=json.dumps({"format": {"duration": "1.25"}, "streams": [
                {"codec_type": "video", "width": 320, "height": 240, "nb_read_frames": "2"}]}))
        Path(argv[-1]).write_bytes(b"recovered video")
        return SimpleNamespace(returncode=0, stderr="")

    def recover(self):
        with patch.object(record, "run", side_effect=self.commands), patch.object(record, "ffmpeg_bin", side_effect=lambda name="ffmpeg": name):
            return record.recover_session(self.session)

    def test_recovery_preserves_original_and_reports_missing_audio(self):
        result = self.recover()
        self.assertEqual(self.video.read_bytes(), b"recovered video")
        self.assertEqual((self.session / "recording.original.mp4").read_bytes(), b"original fragmented video")
        self.assertTrue(result["recovered"])
        self.assertTrue(result["incomplete"])
        self.assertEqual(result["mic_audio"], "missing")
        self.assertIn("不能作为用户口述", result["warning"])
        self.assertFalse(list(self.session.glob(".recording-recovery-*")))
        with self.assertRaisesRegex(ValueError, "backup already exists"):
            self.recover()

    def test_failed_remux_or_probe_preserves_source_and_cleans_temporary(self):
        for failure in (SimpleNamespace(returncode=1, stderr="bad input"), ValueError("bad probe")):
            with self.subTest(failure=failure), patch.object(record, "IS_MAC", False), \
                 patch.object(record, "ffmpeg_bin", return_value="ffmpeg"), \
                 patch.object(record, "run", side_effect=failure if isinstance(failure, Exception) else None,
                              return_value=failure):
                with self.assertRaises(ValueError):
                    record.recover_session(self.session)
            self.assertEqual(self.video.read_bytes(), b"original fragmented video")
            self.assertFalse((self.session / "recording.original.mp4").exists())
            self.assertFalse(list(self.session.glob(".recording-recovery-*")))

    def test_system_audio_does_not_claim_microphone_narration(self):
        (self.session / "native-events.jsonl").write_text(json.dumps(
            {"event": "stopped", "audio_buffers": 0, "system_buffers": 12}) + "\n")
        def audio_probe(argv, **kwargs):
            result = self.commands(argv, **kwargs)
            if "-count_frames" in argv:
                payload = json.loads(result.stdout)
                payload["streams"].append({"codec_type": "audio"})
                result.stdout = json.dumps(payload)
            return result
        with patch.object(record, "run", side_effect=audio_probe), patch.object(record, "ffmpeg_bin", return_value="ffmpeg"):
            result = record.recover_session(self.session)
        self.assertEqual(result["mic_audio"], "missing")
        self.assertEqual(result["system_audio"], "captured")
        self.assertIn("未录到麦克风", result["warning"])
        self.assertIn("保留系统音频", result["warning"])
        (self.session / "recording.original.mp4").unlink()
        (self.session / "native-events.jsonl").unlink()
        with patch.object(record, "run", side_effect=audio_probe), patch.object(record, "ffmpeg_bin", return_value="ffmpeg"):
            result = record.recover_session(self.session)
        self.assertEqual(result["mic_audio"], "unverified")
        self.assertEqual(result["system_audio"], "unverified")
        self.assertIn("尚未核验", result["warning"])

    def test_invalid_video_and_source_change_never_replace_original(self):
        def invalid_probe(argv, **kwargs):
            result = self.commands(argv, **kwargs)
            if "-count_frames" in argv:
                result.stdout = json.dumps({"format": {"duration": "2"}, "streams": [{"codec_type": "audio"}]})
            return result
        with patch.object(record, "run", side_effect=invalid_probe), patch.object(record, "ffmpeg_bin", return_value="ffmpeg"):
            with self.assertRaisesRegex(ValueError, "no decodable video"):
                record.recover_session(self.session)
        def changed_source(argv, **kwargs):
            result = self.commands(argv, **kwargs)
            if "-count_frames" in argv:
                self.video.write_bytes(b"newer data")
            return result
        with patch.object(record, "run", side_effect=changed_source), patch.object(record, "ffmpeg_bin", return_value="ffmpeg"):
            with self.assertRaisesRegex(ValueError, "changed during recovery"):
                record.recover_session(self.session)
        self.assertEqual(self.video.read_bytes(), b"newer data")
        self.assertFalse((self.session / "recording.original.mp4").exists())

    def test_promotion_failure_keeps_both_original_names(self):
        with patch.object(record.os, "replace", side_effect=OSError("promotion failed")):
            with self.assertRaises(OSError):
                self.recover()
        self.assertEqual(self.video.read_bytes(), b"original fragmented video")
        self.assertEqual((self.session / "recording.original.mp4").read_bytes(), self.video.read_bytes())
        self.assertFalse(list(self.session.glob(".recording-recovery-*")))

    def test_complete_metadata_and_discard_are_not_recovered(self):
        for name, content in (("meta.json", json.dumps({"video": "recording.mp4", "duration": 3})),
                              ("meta.json", json.dumps({"recovered": True})),
                              (".cmd", "pause\ndiscard\n"),
                              ("events.jsonl", '{"t":1.5,"type":"discard"}\n'),
                              ("native-events.jsonl", '{"event":"cancelled"}\n')):
            with self.subTest(name=name, content=content):
                path = self.session / name
                path.write_text(content)
                with self.assertRaises(ValueError), patch.object(record, "run") as run:
                    record.recover_session(self.session)
                run.assert_not_called()
                path.unlink()
                self.assertEqual(self.video.read_bytes(), b"original fragmented video")

    def test_partial_events_and_unrelated_fields_do_not_claim_discard(self):
        (self.session / "events.jsonl").write_text(
            '{"t":1,"type":"marker","event":"discard"}\n{"t":2,"type":"discard"')
        self.assertTrue(self.recover()["recovered"])

    def test_active_wrapper_or_orphaned_native_prevents_recovery(self):
        record.STATE_FILE.write_text(json.dumps({"pid": os.getpid(), "session": str(self.session)}))
        with self.assertRaisesRegex(ValueError, "still recording"):
            self.recover()
        record.STATE_FILE.unlink()
        process = f"123 /Applications/Two Hours.app/Contents/MacOS/twohours-recorder --out {self.video} --events {self.session}/events.jsonl\n"
        with patch.object(record, "IS_MAC", True), patch.object(record, "run", return_value=SimpleNamespace(stdout=process)):
            with self.assertRaisesRegex(ValueError, "native recorder is still writing"):
                record.recover_session(self.session)

    def test_standalone_open_file_is_checked_without_blocking_idle_app(self):
        process = "123 /Applications/Two Hours.app/Contents/MacOS/twohours-recorder\n"
        for code in (0, 2, 1):
            calls = []
            def commands(argv, **kwargs):
                if argv[0] == "/bin/ps":
                    return SimpleNamespace(stdout=process)
                if argv[0] == "/usr/sbin/lsof":
                    calls.append((argv, kwargs))
                    return SimpleNamespace(returncode=code)
                return self.commands(argv, **kwargs)
            with self.subTest(code=code), patch.object(record, "IS_MAC", True), \
                 patch.object(record, "run", side_effect=commands), patch.object(record, "ffmpeg_bin", return_value="ffmpeg"):
                if code == 1:
                    self.assertTrue(record.recover_session(self.session)["recovered"])
                else:
                    with self.assertRaises(ValueError):
                        record.recover_session(self.session)
                    self.assertEqual(self.video.read_bytes(), b"original fragmented video")
            self.assertEqual(calls[0][0][1:], ["-a", "-p", "123", "-Fn", "--", str(self.video.resolve())])
            self.assertEqual(calls[0][1]["timeout"], 5)

    def test_symlinked_source_is_rejected(self):
        other = self.root / "external.mp4"
        self.video.rename(other)
        self.video.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "non-regular"):
            self.recover()
        self.assertEqual(other.read_bytes(), b"original fragmented video")

    def test_failed_native_start_recovers_through_done_protocol(self):
        self.video.unlink()
        def failed_native(*args):
            self.video.write_bytes(b"original fragmented video")
            return {"event": "error", "code": "launch_1", "message": "crashed"}
        args = SimpleNamespace(session=str(self.session), engine=None, region=None, last_region=False)
        output = io.StringIO()
        with patch.object(record, "run_native", side_effect=failed_native), \
             patch.object(record, "global_config", return_value={}), patch.object(record, "choose_engine", return_value="native"), \
             patch.object(record, "run", side_effect=self.commands), patch.object(record, "ffmpeg_bin", return_value="ffmpeg"), \
             patch.object(record, "probe_duration", return_value=1.25), contextlib.redirect_stdout(output):
            record.cmd_start(args)
        meta = json.loads((self.session / "meta.json").read_text())
        self.assertTrue(meta["recovered"])
        self.assertTrue(meta["incomplete"])
        self.assertIn(f"DONE video={self.video.resolve()} duration=", output.getvalue())
        self.assertFalse(record.STATE_FILE.exists())

    def test_cancelled_native_never_attempts_recovery(self):
        self.video.unlink()
        args = SimpleNamespace(session=str(self.session), engine=None, region=None, last_region=False)
        with patch.object(record, "run_native", return_value={"event": "cancelled"}), \
             patch.object(record, "global_config", return_value={}), patch.object(record, "choose_engine", return_value="native"), \
             patch.object(record, "recover_session") as recover, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                record.cmd_start(args)
        self.assertEqual(result.exception.code, 2)
        recover.assert_not_called()

    def test_incomplete_stop_and_missing_microphone_are_saved_and_reported(self):
        output = io.StringIO()
        with patch.object(record, "probe_duration", return_value=1), contextlib.redirect_stdout(output):
            record.finish_session(self.session, {"event": "stopped", "incomplete": True,
                                                 "mic_enabled": True, "audio_buffers": 0, "system_buffers": 4}, {})
        meta = json.loads((self.session / "meta.json").read_text())
        self.assertIn("结尾可能丢失", meta["warning"])
        self.assertIn("未录到麦克风", meta["warning"])
        self.assertIn("recording_warning", output.getvalue())
        self.assertIn("DONE video=", output.getvalue())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires ffmpeg")
    def test_real_short_video_is_remuxed_and_decodable(self):
        subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=s=64x64:r=5:d=0.4", "-c:v", "libx264", "-movflags", "empty_moov+frag_keyframe",
                        str(self.video)], check=True, timeout=10)
        original = self.video.read_bytes()
        with patch.object(record, "IS_MAC", False), contextlib.redirect_stdout(io.StringIO()):
            record.cmd_recover(SimpleNamespace(session=str(self.session)))
        self.assertEqual((self.session / "recording.original.mp4").read_bytes(), original)
        self.assertTrue(json.loads((self.session / "meta.json").read_text())["recovered"])
        subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-i", str(self.video),
                        "-f", "null", "-"], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
