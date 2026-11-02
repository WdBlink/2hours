r"""Windows recorder fixes ported from upstream blurt 7dea4480/f3f93b0f: Matroska clusters flushed every second, and
segment concat without the \\?\ extended-length prefix (which ffmpeg's concat demuxer mishandles)."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'vendor/blurt/scripts'
sys.path.insert(0, str(SCRIPTS))
import _common
import tk_recorder
SPEC = importlib.util.spec_from_file_location('windows_record_fixes', SCRIPTS / 'record.py')
record = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(record)

EXT = '\\\\?\\'  # \\?\
FLAGS = ['-cluster_time_limit', '1000', '-flush_packets', '1']
X264 = ['-c:v', 'libx264']


def recorder(session):
    return tk_recorder.TkRecorder(session, None, 10, 320, 'en', 0, None, [0, 0, 320, 240], session / '.cmd',
                                  lambda _: None)


class PlainPathTests(unittest.TestCase):
    def test_strips_extended_prefix_from_drive_paths(self):
        self.assertEqual(tk_recorder._plain(EXT + 'C:\\Users\\u\\run\\blurt\\parts.txt'),
                         'C:\\Users\\u\\run\\blurt\\parts.txt')
        self.assertEqual(tk_recorder._plain(Path(EXT + 'D:\\x.mp4')), 'D:\\x.mp4')

    def test_extended_unc_becomes_plain_unc(self):
        self.assertEqual(tk_recorder._plain(EXT + 'UNC\\srv\\share\\run\\parts.txt'), '\\\\srv\\share\\run\\parts.txt')
        self.assertEqual(tk_recorder._plain(EXT + 'unc\\srv\\share\\x'), '\\\\srv\\share\\x')

    def test_paths_without_prefix_are_unchanged(self):
        for path in ('C:\\a\\b.mp4', '\\\\srv\\share\\x', '/tmp/a b/c.mp4', 'part00.mp4', '\\\\.\\C:\\x', ''):
            self.assertEqual(tk_recorder._plain(path), path)


class MemoryWindowsPath(PureWindowsPath):
    r"""A Windows path whose files live in a dict, so the \\?\ and UNC sessions below never touch a real disk.
    Keys ignore case and the \\?\ / \\?\UNC\ prefix, as the Windows file APIs do."""
    files = None

    @staticmethod
    def key(text):
        text = str(text)
        if text[:8].upper() == '\\\\?\\UNC\\':
            text = '\\\\' + text[8:]
        elif text.startswith(EXT):
            text = text[4:]
        return text.lower()

    def exists(self):
        return self.key(self) in self.files

    def is_file(self):
        return self.exists()

    def stat(self):
        return SimpleNamespace(st_size=len(self.files[self.key(self)]))

    def write_bytes(self, data):
        self.files[self.key(self)] = bytes(data)

    def write_text(self, text, encoding=None):
        self.write_bytes(text.encode(encoding or 'utf-8'))

    def read_text(self, encoding=None):
        return self.files[self.key(self)].decode(encoding or 'utf-8')

    def unlink(self, missing_ok=False):
        if self.files.pop(self.key(self), None) is None and not missing_ok:
            raise FileNotFoundError(str(self))


class TkConcatPathTests(unittest.TestCase):
    r"""finalize() on a \\?\ session, with Windows path rules on every OS and every file kept in memory: nothing
    is created on disk (CI cannot reach \\srv\share, and \\?\C:\s would be a real C:\s). The real concat on
    Windows is in WindowsExtendedPathMediaTests."""

    def finalize_in(self, name):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        cwd = os.getcwd()
        os.chdir(directory.name)  # anything that slipped past the fakes would land here and fail the last check
        self.addCleanup(os.chdir, cwd)
        files = {}
        session = type('Session', (MemoryWindowsPath,), {'files': files})(name)
        video, log = session / 'seg00.mkv', session / 'seg00.log'
        video.write_bytes(b'v')
        log.write_text('')
        rec = recorder(session)
        rec.segments = [{'video': video, 'log': log, 'wall_start': 0, 'audio_from': 0, 'audio_to': 0}]
        calls, lists = [], []

        def fake_run(cmd, check=False):
            calls.append(cmd)
            if 'concat' in cmd:  # read the list the way ffmpeg would: from the path it was given
                listed = type(session)(cmd[cmd.index('-i') + 1])
                if not listed.exists():
                    return SimpleNamespace(returncode=1, stderr=f'{listed}: No such file or directory', stdout='')
                lists.append(listed.read_text())
            type(session)(cmd[-1]).write_bytes(b'x')
            return SimpleNamespace(returncode=0, stderr='', stdout='')

        @contextlib.contextmanager
        def fake_open(path, mode='r', encoding=None):
            self.assertEqual(mode, 'w')
            buffer = io.StringIO()
            yield buffer
            type(session)(path).write_text(buffer.getvalue())

        real_path = Path
        with patch.object(tk_recorder, 'ffmpeg_bin', return_value='ffmpeg'), patch.object(tk_recorder, 'run', fake_run), \
                patch.object(tk_recorder, 'open', fake_open, create=True), \
                patch.object(tk_recorder, 'Path', lambda p: p if isinstance(p, PureWindowsPath) else real_path(p)), \
                patch.dict(sys.modules, {'record': SimpleNamespace(first_frame_wall=lambda _: None)}):
            result = rec.finalize()
        concat = [c for c in calls if 'concat' in c]
        self.assertEqual(len(concat), 1, calls)
        self.assertEqual(lists, ["file 'part00.mp4'\n"])
        self.assertEqual(os.listdir(directory.name), [])
        self.assertEqual(sorted(files), [MemoryWindowsPath.key(session / n) for n in ('events.jsonl', 'recording.mp4')])
        return result, concat[0], session

    def test_concat_list_and_output_drop_extended_prefix(self):
        result, cmd, session = self.finalize_in(EXT + 'C:\\s')
        self.assertEqual(result['event'], 'stopped', result)
        self.assertEqual(cmd[cmd.index('-i') + 1], 'C:\\s\\parts.txt')
        self.assertEqual(cmd[-1], 'C:\\s\\recording.mp4')
        self.assertEqual(result['file'], EXT + 'C:\\s\\recording.mp4')

    def test_concat_list_and_output_use_plain_unc(self):
        result, cmd, _ = self.finalize_in(EXT + 'UNC\\srv\\share\\s')
        self.assertEqual(result['event'], 'stopped', result)
        self.assertEqual(cmd[cmd.index('-i') + 1], '\\\\srv\\share\\s\\parts.txt')
        self.assertEqual(cmd[-1], '\\\\srv\\share\\s\\recording.mp4')


class MatroskaClusterTests(unittest.TestCase):
    """A grabber killed mid-take must leave a file that decodes up to its last second."""

    def assert_flags_before_output(self, cmd, out):
        self.assertEqual(cmd[-1], str(out), cmd)
        self.assertEqual(cmd[-5:-1], FLAGS, cmd)

    def test_ffmpeg_engine_command(self):
        out = Path(tempfile.gettempdir()) / 'video.mkv'
        with patch.dict(os.environ, {'BLURT_FAKE_SCREEN': '1'}), \
                patch.object(record, 'pick_encoder', return_value=X264):
            self.assert_flags_before_output(record.headless_video_cmd(out, None, 30, 1920), out)

    def test_ffmpeg_engine_windows_gdigrab_command(self):
        out = Path(tempfile.gettempdir()) / 'video.mkv'
        env = {k: v for k, v in os.environ.items() if k != 'BLURT_FAKE_SCREEN'}
        no_dda = SimpleNamespace(stdout='', returncode=1)
        with patch.dict(os.environ, env, clear=True), patch.object(record, 'IS_MAC', False), \
                patch.object(record, 'IS_LINUX', False), patch.object(record, 'ffmpeg_bin', return_value='ffmpeg'), \
                patch.object(record, 'run', return_value=no_dda), patch.object(record, 'pick_encoder', return_value=X264):
            cmd = record.headless_video_cmd(out, None, 30, 1920)
        self.assertIn('gdigrab', cmd)
        self.assert_flags_before_output(cmd, out)

    def tk_cmd(self, fake_screen, windows=False):
        rec = recorder(Path('.'))
        rec.region = [0, 0, 320, 240]
        out = Path(tempfile.gettempdir()) / 'seg00.mkv'
        env = {k: v for k, v in os.environ.items() if k != 'BLURT_FAKE_SCREEN'}
        if fake_screen:
            env['BLURT_FAKE_SCREEN'] = '1'
        with patch.dict(os.environ, env, clear=True), patch.object(tk_recorder, 'IS_WIN', windows), \
                patch.object(tk_recorder, 'ffmpeg_bin', return_value='ffmpeg'), \
                patch.object(record, 'pick_encoder', return_value=X264), patch.dict(sys.modules, {'record': record}):
            return rec.video_cmd(out), out

    def test_tk_segment_command(self):
        cmd, out = self.tk_cmd(fake_screen=True)
        self.assert_flags_before_output(cmd, out)

    def test_tk_segment_windows_gdigrab_command(self):
        cmd, out = self.tk_cmd(fake_screen=False, windows=True)
        self.assertIn('gdigrab', cmd)
        self.assert_flags_before_output(cmd, out)


@unittest.skipUnless(os.name == 'nt' and _common.find_ffmpeg(), 'Windows extended-length path with ffmpeg')
class WindowsExtendedPathMediaTests(unittest.TestCase):
    def test_generated_segments_join_under_extended_length_root(self):
        with tempfile.TemporaryDirectory(prefix='twohours-') as directory:
            plain = Path(directory).resolve() / 'long path'
            plain.mkdir()
            root = Path(EXT + str(plain))
            recorder_ = recorder(root)
            recorder_.elapsed_before = 2
            for index in range(2):
                video = plain / f'seg{index:02d}.mkv'
                subprocess.run([_common.ffmpeg_bin(), '-v', 'error', '-f', 'lavfi', '-i',
                                f'color=c={"red" if index == 0 else "blue"}:s=320x240:r=10:d=1',
                                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True, timeout=20)
                log = plain / f'seg{index:02d}.log'
                log.write_text(f'Duration: N/A, start: {1000 + index * 4}.000000', encoding='utf-8')
                recorder_.segments.append({'video': root / video.name, 'log': root / log.name,
                                           'wall_start': 1000 + index * 4, 'audio_from': 0, 'audio_to': 0})
            result = recorder_.finalize()
            self.assertEqual(result['event'], 'stopped', result)
            probe = subprocess.check_output([_common.ffmpeg_bin('ffprobe'), '-v', 'error', '-show_streams',
                                             '-show_format', '-of', 'json', str(plain / 'recording.mp4')], text=True)
            data = json.loads(probe)
            self.assertEqual({stream['codec_type'] for stream in data['streams']}, {'video'})
            self.assertAlmostEqual(float(data['format']['duration']), 2, delta=0.2)
            self.assertFalse(list(plain.glob('seg*.mkv')))


if __name__ == '__main__':
    unittest.main()
