"""Production quiet-audio regression; no model imports or audio devices."""
import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'vendor/blurt/scripts/transcribe.py'


def production():
    """Compile unchanged production nodes, excluding dependency initialization."""
    names = {'SR', 'LEVEL_QUIET', 'LEVEL_PEAK', 'LEVEL_MAX_GAIN',
             'level_up', 'cmd_run', 'group_chunks'}
    nodes = []
    found = set()
    for node in ast.parse(SOURCE.read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            nodes.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)} & names
            if targets:
                nodes.append(node)
                found.update(targets)
    assert found == names, f'Production {SOURCE.name} missing required symbols: {sorted(names - found)}'
    env = {'np': np}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), env)
    return env


def tone(peak, frequency=220, dtype=np.float32):
    return (peak * np.sin(2 * np.pi * frequency * np.arange(32000) / 16000)).astype(dtype)


class LevelUpTests(unittest.TestCase):
    def setUp(self):
        self.env = production()
        self.level_up = self.env['level_up']

    def test_constants(self):
        self.assertEqual([self.env[k] for k in ('LEVEL_QUIET', 'LEVEL_PEAK', 'LEVEL_MAX_GAIN')],
                         [.08, .25, 32.0])

    def test_empty_and_zero(self):
        for x in (np.zeros(0, np.float32), np.zeros(16000, np.float32)):
            before = x.copy()
            self.assertIs(self.level_up(x), x)
            np.testing.assert_array_equal(x, before)

    def test_quiet_dtype_and_no_mutation(self):
        for dtype in (np.float32, np.float64):
            x = tone(.02, dtype=dtype)
            before = x.copy()
            y = self.level_up(x)
            self.assertIsNot(y, x)
            self.assertEqual(y.dtype, x.dtype)
            self.assertAlmostEqual(float(np.percentile(np.abs(y), 99.9)), .25, places=6)
            np.testing.assert_array_equal(x, before)

    def test_usual_loud_and_exact_threshold(self):
        for x in (tone(.1), tone(.4), np.full(100, .08, np.float64)):
            before = x.copy()
            self.assertIs(self.level_up(x), x)
            np.testing.assert_array_equal(x, before)

    def test_immediately_below_threshold(self):
        x = np.full(100, np.nextafter(.08, 0), np.float64)
        before = x.copy()
        y = self.level_up(x)
        self.assertIsNot(y, x)
        np.testing.assert_allclose(y, .25, rtol=0, atol=1e-15)
        np.testing.assert_array_equal(x, before)

    def test_gain_cap(self):
        x = tone(.0005)
        before = x.copy()
        y = self.level_up(x)
        np.testing.assert_array_equal(y, x * 32)
        self.assertAlmostEqual(float(np.abs(y).max()), .016, places=7)
        np.testing.assert_array_equal(x, before)

    def test_single_impulse_is_not_clipped(self):
        x = tone(.02)
        x[100] = .9
        before = x.copy()
        y = self.level_up(x)
        self.assertGreater(float(np.percentile(np.abs(y), 99)), .2)
        self.assertAlmostEqual(float(y.max()), 11.25, places=5)
        np.testing.assert_array_equal(x, before)

    def run_tracks(self, workers, empty=False):
        env = self.env
        # Different frequencies AND signs remain distinguishable after normalization.
        original = [tone(.02, 220), -tone(.01, 330)]
        before = [x.copy() for x in original]
        vad_inputs, calls, saved = [], [], []
        barrier = threading.Barrier(workers)

        class Backend:
            max_chunk = 25

            def __init__(self, args):
                pass

            def transcribe(self, x):
                barrier.wait(timeout=5)  # workers=2 must actually execute concurrent chunks
                calls.append((x, threading.get_ident()))
                label = 'mic' if x[1] > 0 else 'system'
                return [(.05, len(x) / env['SR'] + .1, label)]

        def vad(x, max_speech):
            self.assertEqual(max_speech, 25)
            vad_inputs.append(x)
            # First pair must be grouped; the later chunk is separated by >1s.
            return [] if empty else [(0., .2), (.3, .5), (1.75, 2.)]

        def die(message):
            self.fail(message)

        env.update(Path=Path, global_config=lambda: {}, BACKENDS={'fake': Backend}, LOCAL=set(),
                   time=time, audio_tracks=lambda src: 2, load_audio=lambda src, track: original[track],
                   vad_segments=vad, fmt_ts=lambda value: str(value), sys=sys,
                   ThreadPoolExecutor=ThreadPoolExecutor, json=json, die=die,
                   save_json=lambda path, data: saved.append(data))
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(input=str(SOURCE), out=directory, backend='fake',
                                   model=None, language=None, concurrency=workers)
            env['cmd_run'](args)
        self.assertEqual(len(vad_inputs), 2)
        self.assertEqual(len(calls), 0 if empty else 4)
        self.assertFalse(np.array_equal(vad_inputs[0], vad_inputs[1]))
        for index, heard in enumerate(vad_inputs):
            expected = original[index] * (12.5 if index == 0 else 25.)
            np.testing.assert_allclose(heard, expected, rtol=1e-6, atol=1e-8)
            self.assertAlmostEqual(float(np.percentile(np.abs(heard), 99.9)), .25, places=6)
            np.testing.assert_array_equal(original[index], before[index])
            chunks = [(x, thread) for x, thread in calls if np.shares_memory(x, heard)]
            self.assertEqual(len(chunks), 0 if empty else 2)
            if chunks:
                self.assertEqual(len({thread for _, thread in chunks}), workers)
                for start, end in ((0, 8000), (28000, 32000)):
                    matches = [x for x, _ in chunks if np.array_equal(x, heard[start:end])]
                    self.assertEqual(len(matches), 1)
                    self.assertIsNot(matches[0], heard)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['duration'], 2.)
        self.assertEqual(saved[0]['speech_seconds'], 0. if empty else .75)
        expected_segments = [] if empty else [
            {'start': start, 'end': end, 'text': label, 'speaker': label}
            for start, end in ((.05, .5), (1.8, 2.)) for label in ('mic', 'system')]
        self.assertEqual(saved[0]['segments'], expected_segments)

    def test_tracks_workers_1(self):
        self.run_tracks(1)

    def test_tracks_workers_2(self):
        self.run_tracks(2)

    def test_empty_vad_workers_1(self):
        self.run_tracks(1, empty=True)

    def test_empty_vad_workers_2(self):
        self.run_tracks(2, empty=True)


if __name__ == '__main__':
    unittest.main()
