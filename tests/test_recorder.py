import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "vendor/blurt/scripts"
spec = importlib.util.spec_from_file_location("twohours_record", SCRIPTS / "record.py")
record = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record)


class RecorderTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc") and shutil.which("ffprobe")
                         and shutil.which("ffmpeg"), "requires macOS Swift and ffmpeg")
    def test_native_writer_produces_decodable_finished_and_interrupted_video(self):
        source = (ROOT / "vendor/blurt/recorder/macos/BlurtRecorder.swift").read_text()
        setup = source[source.index("        let url = URL(fileURLWithPath: opts.out)"):
                       source.index("        vIn.expectsMediaDataInRealTime = true")]
        audio_setup = source[source.index("        if opts.mic {\n            let a = AVAssetWriterInput"):
                             source.index("        if sys {  // kept as its own track")]
        harness = r'''import Foundation
import AVFoundation
import CoreVideo
import CoreMedia
let opts = (out: CommandLine.arguments[2], fps: 10, mic: true)
let W = 320, H = 240
var writer: AVAssetWriter!
var vIn: AVAssetWriterInput!
var aIn: AVAssetWriterInput?
__SETUP__
vIn.expectsMediaDataInRealTime = true
writer.add(vIn)
__AUDIO_SETUP__
let asset = AVURLAsset(url: URL(fileURLWithPath: CommandLine.arguments[1]))
let reader = try AVAssetReader(asset: asset)
let video = AVAssetReaderTrackOutput(track: asset.tracks(withMediaType: .video)[0], outputSettings:
    [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange])
let audio = AVAssetReaderTrackOutput(track: asset.tracks(withMediaType: .audio)[0], outputSettings:
    [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 48000, AVNumberOfChannelsKey: 1])
reader.add(video); reader.add(audio)
precondition(reader.startReading() && writer.startWriting())
writer.startSession(atSourceTime: .zero)
var v = video.copyNextSampleBuffer(), a = audio.copyNextSampleBuffer()
while v != nil || a != nil {
    let useVideo = v != nil && (a == nil || CMTimeCompare(CMSampleBufferGetPresentationTimeStamp(v!),
                                                           CMSampleBufferGetPresentationTimeStamp(a!)) <= 0)
    let input = useVideo ? vIn! : aIn!
    while !input.isReadyForMoreMediaData { Thread.sleep(forTimeInterval: 0.002) }
    if useVideo { precondition(input.append(v!)); v = video.copyNextSampleBuffer() }
    else { precondition(input.append(a!)); a = audio.copyNextSampleBuffer() }
}
if CommandLine.arguments[3] == "interrupt" { Thread.sleep(forTimeInterval: 2); exit(0) }
vIn.markAsFinished(); aIn!.markAsFinished()
let done = DispatchSemaphore(value: 0)
writer.finishWriting { done.signal() }
precondition(done.wait(timeout: .now() + 20) == .success && writer.status == .completed)
'''.replace("__SETUP__", setup).replace("__AUDIO_SETUP__", audio_setup)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.swift").write_text(harness)
            compiled = subprocess.run([shutil.which("swiftc"), "-suppress-warnings", str(root / "main.swift"),
                                       "-o", str(root / "check")], text=True, capture_output=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            fixture = root / "source.mp4"
            subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=s=320x240:r=10:d=15",
                            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=15",
                            "-c:v", "mpeg4", "-pix_fmt", "yuv420p", "-c:a", "aac", str(fixture)],
                           check=True, timeout=30)
            for mode in ("interrupt", "finished"):
                video = root / f"{mode}.mp4"
                subprocess.run([str(root / "check"), str(fixture), str(video), mode], check=True, timeout=30)
                probe = subprocess.check_output([shutil.which("ffprobe"), "-v", "error", "-show_entries",
                                                 "format=duration", "-of", "default=nw=1:nk=1", str(video)], text=True)
                duration = float(probe.strip())
                self.assertGreaterEqual(duration, 4 if mode == "interrupt" else 14.9)
                tracks = json.loads(subprocess.check_output([shutil.which("ffprobe"), "-v", "error", "-show_entries",
                    "stream=codec_type,duration", "-of", "json", str(video)], text=True))["streams"]
                self.assertEqual({track["codec_type"] for track in tracks}, {"video", "audio"})
                self.assertTrue(all(float(track["duration"]) >= 4 for track in tracks))
                subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-i", str(video), "-f", "null", "-"],
                               check=True, timeout=30)

    def test_bundle_and_signing_use_two_hours_identity(self):
        info = plistlib.loads(record.INFO_PLIST.encode())
        self.assertEqual(info["CFBundleIdentifier"], "dev.twohours.recorder")
        self.assertEqual(info["CFBundleName"], "Two Hours")
        self.assertEqual(info["CFBundleDisplayName"], "Two Hours")
        self.assertEqual(info["CFBundleExecutable"], "twohours-recorder")
        self.assertEqual(Path(info["RecorderHome"]), Path.home() / ".2hours/recorder")
        self.assertIn("Two Hours", info["NSMicrophoneUsageDescription"])
        self.assertEqual(record.ICON, ROOT / "assets/brand/icon.png")
        with patch.object(record, "run") as run:
            record.sign_app(Path("/fixture/Two Hours.app"))
        self.assertIn('dev.twohours.recorder', run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0][:3], ["codesign", "--verify", "--strict"])

    def test_no_independent_blurt_is_required_or_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            env = dict(os.environ, HOME=str(home), BLURT_HOME=str(home / "unrelated-blurt"))
            output = subprocess.check_output([sys.executable, str(SCRIPTS / "record.py"), "identity"], env=env, text=True)
            info = json.loads(output)
            self.assertEqual(info["home"], str(home / ".2hours/recorder"))
            self.assertTrue(info["bundled"])
            self.assertFalse((home / ".blurt").exists())
            self.assertFalse((home / "unrelated-blurt").exists())
            standalone = home / "standalone/blurt/scripts"
            standalone.mkdir(parents=True)
            for name in ("_common.py", "_identity.py", "record.py"):
                shutil.copyfile(SCRIPTS / name, standalone / name)
            output = subprocess.check_output([sys.executable, str(standalone / "record.py"), "identity"], env=env, text=True)
            info = json.loads(output)
            self.assertEqual(info["name"], "Blurt")
            self.assertEqual(info["bundle_id"], "dev.blurt.recorder")
            self.assertEqual(info["home"], env["BLURT_HOME"])
            self.assertFalse(info["bundled"])

    def test_macos_never_silently_falls_back_to_host_permissions(self):
        with patch.object(record, "IS_MAC", True), patch.object(record, "native_binary", return_value=None):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                record.choose_engine(None)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                record.choose_engine("ffmpeg")
        with patch.object(record, "IS_MAC", True), patch.object(record, "pick_mic") as mic:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                record.cmd_test(SimpleNamespace())
            mic.assert_not_called()

    def test_existing_recording_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            video = session / "recording.mp4"
            video.write_bytes(b"preserve")
            with patch.object(record, "load_json", return_value=None), patch.object(record, "choose_engine") as choose:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    record.cmd_start(SimpleNamespace(session=str(session)))
                choose.assert_not_called()
            self.assertEqual(video.read_bytes(), b"preserve")

    def launch(self, session, events, returncode=0, max_minutes=120, poll=None):
        def spawn(argv):
            self.argv = argv
            log = Path(argv[argv.index("--stdout") + 1])
            log.write_text("".join(json.dumps(e) + "\n" for e in events))
            return SimpleNamespace(returncode=returncode, poll=poll or (lambda: returncode))
        with patch.object(record, "ensure_app_installed", return_value=Path("/fixture/Two Hours.app")), \
             patch.object(record.subprocess, "Popen", side_effect=spawn), \
             patch.object(record.signal, "signal"), contextlib.redirect_stdout(io.StringIO()):
            return record.run_native_app(["--out", str(session / "recording.mp4")], session,
                                         SimpleNamespace(max_minutes=max_minutes), session / ".cmd")

    def test_launchservices_passes_events_and_preserves_cancel_and_error(self):
        for terminal in ({"event": "stopped"}, {"event": "cancelled"},
                         {"event": "error", "code": "screen_permission"}):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as directory:
                session = Path(directory)
                result = self.launch(session, [{"event": "configured", "region": [1, 2, 300, 200]}, terminal])
                self.assertEqual(result["event"], terminal["event"])
                if result["event"] == "stopped":
                    self.assertEqual(result["region"], [1, 2, 300, 200])
                self.assertEqual(self.argv[:5], ["/usr/bin/open", "-n", "-W", "-a", "/fixture/Two Hours.app"])
                self.assertEqual(self.argv[-2:], ["--commands", str(session / ".cmd")])
                self.assertNotEqual(self.argv[self.argv.index("--stdout") + 1], self.argv[self.argv.index("--stderr") + 1])

    def test_launcher_exit_and_missing_final_event_are_failures(self):
        for events, code in (([], 0), ([{"event": "stopped"}], 1)):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                result = self.launch(Path(directory), events, returncode=code)
                self.assertEqual(result["event"], "error")

    def test_time_limit_forwards_stop_without_terminating_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            record.append_command(session / ".cmd", "pause")
            polls = iter([None, 0])
            with patch.object(record.time, "monotonic", side_effect=[0, 61]), patch.object(record.time, "sleep"):
                self.launch(session, [], max_minutes=1, poll=lambda: next(polls))
            self.assertEqual((session / ".cmd").read_text(), "pause\nstop\n")

    def test_native_event_logs_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            log = session / "native-events.jsonl"
            log.write_text("previous result")
            with self.assertRaises(FileExistsError):
                self.launch(session, [])
            self.assertEqual(log.read_text(), "previous result")


class CommandLogTests(unittest.TestCase):
    def test_open_writer_append_after_read_survives_and_partial_lines_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".cmd"
            commands = record.CommandLog(path)
            self.assertEqual(commands.read(), [])
            with path.open("ab", buffering=0) as writer:
                writer.write(b"pause\n")
                self.assertEqual(commands.read(), ["pause"])
                writer.write(b"st")
                self.assertEqual(commands.read(), [])
                writer.write(b"op\n")
                self.assertEqual(commands.read(), ["stop"])
                self.assertEqual(commands.read(), [])
            self.assertEqual(path.read_bytes(), b"pause\nstop\n")

    def test_native_forwarder_keeps_stop_appended_while_forwarding_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            path = session / ".cmd"
            forwarded = []
            with path.open("ab", buffering=0) as writer:
                writer.write(b"pause\n")
                def forward(command):
                    forwarded.append(command)
                    if command == "pause\n":
                        writer.write(b"stop\n")
                polls = iter([None, None, 0])
                process = SimpleNamespace(stdin=SimpleNamespace(write=forward, flush=lambda: None),
                                          stdout=io.StringIO('{"event":"stopped"}\n'),
                                          poll=lambda: next(polls), returncode=0)
                args = SimpleNamespace(fps=None, max_width=None, countdown=None, no_mic=True, mic=None,
                                       system_audio=False, max_minutes=0)
                with patch.object(record, "TWO_HOURS", False), \
                     patch.object(record, "native_binary", return_value=Path("/fixture/Blurt.app/Contents/MacOS/blurt-recorder")), \
                     patch.object(record, "lang", return_value="en"), \
                     patch.object(record.subprocess, "Popen", return_value=process), \
                     patch.object(record.signal, "signal"), patch.object(record.time, "sleep"), \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(record.run_native(session, args, {}, None, None, path)["event"], "stopped")
            self.assertEqual(forwarded, ["pause\n", "stop\n"])
            self.assertEqual(path.read_bytes(), b"pause\nstop\n")

    def test_headless_consumer_keeps_stop_appended_after_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            path = session / ".cmd"
            polls = iter([None, None, None, 0, 0])
            process = SimpleNamespace(stdin=Mock(), poll=lambda: next(polls), wait=lambda **_: 0)
            args = SimpleNamespace(no_mic=True, mic=None, screen=None, fps=None, max_width=None, max_minutes=0)
            with path.open("ab", buffering=0) as writer:
                writer.write(b"pause\n")
                def append_stop(_):
                    writer.write(b"stop\n")
                with patch.object(record.subprocess, "Popen", return_value=process), \
                     patch.object(record, "headless_video_cmd", return_value=["fixture-ffmpeg"]), \
                     patch.object(record, "chime"), patch.object(record.signal, "signal"), \
                     patch.object(record.time, "sleep", side_effect=append_stop), \
                     contextlib.redirect_stdout(io.StringIO()):
                    record.run_headless(session, args, {}, path)
            process.stdin.write.assert_called_once_with(b"q")
            self.assertEqual(path.read_bytes(), b"pause\nstop\n")

    def test_tk_consumer_keeps_stop_appended_while_handling_pause(self):
        import tk_recorder
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".cmd"
            received = []
            with path.open("ab", buffering=0) as writer:
                writer.write(b"pause\n")
                def command(value):
                    received.append(value)
                    if value == "pause":
                        writer.write(b"stop\n")
                recorder = SimpleNamespace(result=None, command_log=record.CommandLog(path), command=command,
                                           root=SimpleNamespace(after=lambda *_: None), poll_commands=lambda: None)
                tk_recorder.TkRecorder.poll_commands(recorder)
                tk_recorder.TkRecorder.poll_commands(recorder)
            self.assertEqual(received, ["pause", "stop"])
            self.assertEqual(path.read_bytes(), b"pause\nstop\n")

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc"), "requires the macOS Swift compiler")
    def test_swift_command_log_preserves_open_writer_and_partial_lines(self):
        source = (ROOT / "vendor/blurt/recorder/macos/BlurtRecorder.swift").read_text()
        command_log = source[source.index("struct CommandLog {"):source.index("// MARK: - Config")]
        harness = r'''import Foundation
__COMMAND_LOG__
let path = CommandLine.arguments[1]
var commands = CommandLog(path: path)
precondition(commands.read().isEmpty)
FileManager.default.createFile(atPath: path, contents: nil)
let writer = try FileHandle(forWritingTo: URL(fileURLWithPath: path))
try writer.write(contentsOf: Data("pause\n".utf8))
precondition(commands.read() == ["pause"])
try writer.write(contentsOf: Data("st".utf8))
precondition(commands.read().isEmpty)
try writer.write(contentsOf: Data("op\n".utf8))
precondition(commands.read() == ["stop"])
precondition(commands.read().isEmpty)
try writer.close()
let contents = try String(contentsOfFile: path, encoding: .utf8)
precondition(contents == "pause\nstop\n")
print("append-after-read and partial-line checks passed")
'''.replace("__COMMAND_LOG__", command_log)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.swift").write_text(harness)
            compiled = subprocess.run([shutil.which("swiftc"), str(root / "main.swift"), "-o", str(root / "check")],
                                      text=True, capture_output=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(root / "check"), str(root / ".cmd")],
                                    check=True, text=True, capture_output=True, timeout=10)
            self.assertIn("checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
