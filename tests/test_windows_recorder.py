"""Windows recorder coverage using generated video/audio, never screen or microphone capture."""
import array
import importlib.util
import json
import math
import os
import queue
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'vendor/blurt/scripts'
sys.path.insert(0, str(SCRIPTS))
import _common
import tk_recorder
SPEC = importlib.util.spec_from_file_location('windows_record', SCRIPTS / 'record.py')
record = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(record)


class RecorderPlatformTests(unittest.TestCase):
    def test_native_engine_is_rejected_on_windows(self):
        with patch.object(record, 'IS_MAC', False):
            with self.assertRaises(SystemExit):
                record.choose_engine('native')

    def test_missing_tk_is_an_actionable_error(self):
        with patch.object(record, 'IS_MAC', False), patch.object(record, 'tk_available', return_value=False):
            with self.assertRaises(SystemExit):
                record.choose_engine('tk')

    def test_microphone_pause_drops_samples_and_stop_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = SimpleNamespace(start=lambda: None, stop=lambda: None, close=lambda: None)
            with patch.dict(sys.modules, {'sounddevice': SimpleNamespace(RawInputStream=lambda **_: fake)}):
                audio = record.MicRecorder({'index': 0, 'samplerate': 48000}, Path(directory) / 'audio.wav')
            audio.start()
            clock = SimpleNamespace(currentTime=10, inputBufferAdcTime=9.99)
            status = SimpleNamespace(input_overflow=False)
            audio._cb(b'\0\0' * 480, 480, clock, status)
            audio.gate = lambda: False
            audio._cb(b'\xff\xff' * 480, 480, clock, status)
            audio.gate = lambda: True
            audio._cb(b'\1\0' * 480, 480, clock, status)
            audio.stop()
            audio.stop()
            self.assertEqual(audio.frames, 960)
            with wave.open(str(audio.path)) as wav:
                self.assertEqual(wav.getnframes(), 960)
                self.assertEqual(wav.readframes(960), b'\0\0' * 480 + b'\1\0' * 480)

    def test_tk_mux_failure_preserves_original_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / 'seg00.mkv'
            video.write_bytes(b'original video')
            log = root / 'seg00.log'
            log.write_text('')
            recorder = tk_recorder.TkRecorder(root, None, 10, 320, 'en', 0, None, [0, 0, 320, 240], root / '.cmd', lambda _: None)
            recorder.segments = [{'video': video, 'log': log, 'wall_start': 0, 'audio_from': 0, 'audio_to': 0}]
            with patch.object(tk_recorder, 'ffmpeg_bin', return_value='ffmpeg'), \
                 patch.object(tk_recorder, 'run', return_value=SimpleNamespace(returncode=1, stderr='encoder error')):
                result = recorder.finalize()
            self.assertEqual(result['event'], 'error')
            self.assertEqual(result['code'], 'segment_mux_failed')
            self.assertEqual(video.read_bytes(), b'original video')

    def test_missing_or_empty_resume_segment_cannot_claim_success(self):
        for empty in (False, True):
            with self.subTest(empty=empty), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                first = root / 'seg00.mkv'
                first.write_bytes(b'valid first segment')
                second = root / 'seg01.mkv'
                if empty:
                    second.touch()
                recorder = tk_recorder.TkRecorder(root, None, 10, 320, 'en', 0, None,
                                                  [0, 0, 320, 240], root / '.cmd', lambda _: None)
                for index, video in enumerate((first, second)):
                    log = root / f'seg{index:02d}.log'
                    log.write_text('capture log', encoding='utf-8')
                    recorder.segments.append({'video': video, 'log': log, 'wall_start': 0,
                                              'audio_from': 0, 'audio_to': 0})
                with patch.object(tk_recorder, 'ffmpeg_bin', return_value='ffmpeg'), \
                     patch.object(tk_recorder, 'run', return_value=SimpleNamespace(returncode=0, stderr='')) as command:
                    result = recorder.finalize()
                self.assertEqual(result['event'], 'error')
                self.assertEqual(result['code'], 'missing_segment')
                self.assertEqual(first.read_bytes(), b'valid first segment')
                self.assertTrue((root / 'seg00.log').is_file())
                self.assertTrue((root / 'seg01.log').is_file())
                self.assertFalse((root / 'recording.mp4').exists())
                self.assertEqual(command.call_count, 1)  # No final concatenation of an incomplete take.

    @unittest.skipUnless(os.name == 'nt', 'Windows process handle query')
    def test_process_liveness_does_not_kill_target(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])
        try:
            self.assertTrue(record._alive(child.pid))
            self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=5)
        self.assertFalse(record._alive(child.pid))


@unittest.skipUnless(_common.find_ffmpeg(), 'requires ffmpeg')
class SyntheticMediaTests(unittest.TestCase):
    def test_generated_segments_and_paused_audio_join_to_valid_mp4_in_unicode_path(self):
        with tempfile.TemporaryDirectory(prefix='twohours-') as directory:
            root = Path(directory) / '中文 folder'
            root.mkdir()
            recorder = tk_recorder.TkRecorder(root, None, 10, 320, 'en', 0, None, [0, 0, 320, 240], root / '.cmd', lambda _: None)
            rate = 48000
            samples = array.array('h', (int(math.sin(2 * math.pi * 440 * n / rate) * 4000) for n in range(rate * 2)))
            with wave.open(str(root / 'audio.wav'), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(rate)
                output.writeframes(samples.tobytes())
            recorder.audio = SimpleNamespace(sr=rate, first_wall=1000, frames=rate * 2, overflows=0)
            recorder.elapsed_before = 2
            for index in range(2):
                video = root / f'seg{index:02d}.mkv'
                subprocess.run([_common.ffmpeg_bin(), '-v', 'error', '-f', 'lavfi', '-i',
                                f'color=c={"red" if index == 0 else "blue"}:s=320x240:r=10:d=1',
                                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True, timeout=20)
                log = root / f'seg{index:02d}.log'
                log.write_text(f'Duration: N/A, start: {1000 + index * 4}.000000', encoding='utf-8')
                recorder.segments.append({'video': video, 'log': log, 'wall_start': 1000 + index * 4,
                                          'audio_from': index * rate, 'audio_to': (index + 1) * rate})
            result = recorder.finalize()
            self.assertEqual(result['event'], 'stopped', result)
            probe = subprocess.check_output([_common.ffmpeg_bin('ffprobe'), '-v', 'error', '-show_streams',
                                             '-show_format', '-of', 'json', str(root / 'recording.mp4')], text=True)
            data = json.loads(probe)
            self.assertEqual({stream['codec_type'] for stream in data['streams']}, {'video', 'audio'})
            self.assertAlmostEqual(float(data['format']['duration']), 2, delta=0.2)
            self.assertFalse(list(root.glob('seg*.mkv')))

    @unittest.skipUnless(os.name == 'nt', 'Windows Tk synthetic capture')
    def test_tk_auto_stops_synthetic_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / 'synthetic'
            env = dict(os.environ, PYTHONUTF8='1', BLURT_FAKE_SCREEN='1', HOME=directory, USERPROFILE=directory)
            child = subprocess.run([sys.executable, str(SCRIPTS / 'record.py'), 'start', '--session', str(session),
                                    '--engine', 'tk', '--region', '50,50,320,240', '--no-mic', '--countdown', '0',
                                    '--fps', '10', '--max-minutes', '0.025'],
                                   env=env, capture_output=True, encoding='utf-8', timeout=35)
            self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
            self.assertTrue((session / 'recording.mp4').is_file())
            self.assertIn('DONE video=', child.stdout)

    @unittest.skipUnless(os.name == 'nt', 'Windows Tk synthetic capture')
    def test_tk_external_pause_resume_marker_and_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / 'controls'
            env = dict(os.environ, PYTHONUTF8='1', BLURT_FAKE_SCREEN='1', HOME=directory, USERPROFILE=directory)
            child = subprocess.Popen([sys.executable, str(SCRIPTS / 'record.py'), 'start', '--session', str(session),
                                      '--engine', 'tk', '--region', '50,50,320,240', '--no-mic', '--countdown', '0',
                                      '--fps', '10', '--max-minutes', '0.3'],
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
            lines = queue.Queue()
            output = []
            def read():
                for line in child.stdout:
                    output.append(line)
                    lines.put(line)
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            def wait_event(event):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    line = lines.get(timeout=max(0.1, deadline - time.monotonic()))
                    if line.startswith('{') and json.loads(line).get('event') == event:
                        return
                self.fail('missing event ' + event)
            try:
                wait_event('recording')
                time.sleep(0.5)
                _common.append_command(session / '.cmd', 'pause')
                wait_event('paused')
                _common.append_command(session / '.cmd', 'resume')
                wait_event('resumed')
                time.sleep(0.5)
                _common.append_command(session / '.cmd', 'marker')
                wait_event('marker')
                _common.append_command(session / '.cmd', 'stop')
                child.wait(timeout=20)
                reader.join(timeout=5)
                error = child.stderr.read()
                self.assertEqual(child.returncode, 0, ''.join(output) + error)
                events = [json.loads(line)['type'] for line in (session / 'events.jsonl').read_text().splitlines()]
                self.assertEqual(events, ['pause', 'resume', 'marker', 'stop'])
                self.assertTrue((session / 'recording.mp4').is_file())
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                child.stdout.close()
                child.stderr.close()


if __name__ == '__main__':
    unittest.main()
