"""One 2hours transcription at a time on this machine (after upstream Blurt e72469dc's AsrSlot, MIT).

LockTests use small stand-in children and run everywhere. TranscribeTests run the real vendored
transcribe.py with tests/fixtures/fake_sherpa (no models, burns CPU, logs load/decode events) and
need numpy and ffmpeg.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LOCK_MODULE = ROOT / "scripts/asr_lock.py"
TRANSCRIBE = ROOT / "vendor/blurt/scripts/transcribe.py"
FAKE = Path(__file__).resolve().parent / "fixtures/fake_sherpa"
SHORT, LONG = "300", "100000"  # FAKE_ITERS: a few seconds, or far longer than any test (killed)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


asr_lock = load("test_asr_lock_module", LOCK_MODULE)
guard = load("test_asr_lock_guard", ROOT / "scripts/resource_guard.py")

# Holds the lock until stdin closes; says "held" once it has it.
HOLDER = """import importlib.util, sys
spec = importlib.util.spec_from_file_location("lock", sys.argv[1])
lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock)
try:
    with lock.slot():
        print("held", flush=True)
        sys.stdin.read()
except lock.LockError as error:
    print(error, file=sys.stderr, flush=True)
    sys.exit(error.exit_code)
"""
# Stands in for `transcribe.py run`: takes the lock the way the vendored script does, then leaves a marker.
FAKE_TRANSCRIBE = """import importlib.util, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("lock", sys.argv[2])
lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock)
try:
    with lock.slot():
        Path(sys.argv[3]).write_text(str(time.monotonic()))
except lock.LockError as error:
    print(error, file=sys.stderr, flush=True)
    sys.exit(error.exit_code)
"""


def home_env(home, **extra):
    env = {key: value for key, value in os.environ.items() if key not in (asr_lock.HANDOFF_ENV, asr_lock.TIMEOUT_ENV)}
    env.update(HOME=str(home), USERPROFILE=str(home), **extra)
    return env


def sample():
    return {"time": guard.stamp(), "system": {"pressure": 1, "ram_bytes": 64 * guard.GIB, "swap_used_bytes": 0,
                                              "swapout_bytes": 0, "compressor_bytes": 0}, "processes": []}


def guard_args(directory, command, timeout):
    return argparse.Namespace(output=str(directory), stage="transcribe", interval=.02, timeout=timeout,
                              observe=.05, max_stage_gib=4, max_swap_growth_mib=512, command=command)


class LockTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        env = patch.dict(os.environ, home_env(self.home), clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.children = []
        self.addCleanup(self.stop_children)
        self.fake = self.root / "transcribe.py"
        self.fake.write_text(FAKE_TRANSCRIBE, encoding="utf-8")
        self.marker = self.root / "ran"

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait()
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream:
                    stream.close()

    def holder(self, **env):
        child = subprocess.Popen([sys.executable, "-c", HOLDER, str(LOCK_MODULE)], env=home_env(self.home, **env),
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(child)
        return child

    def held(self, child, limit=30):
        # Bounded read: a holder stuck waiting for the lock (TIMEOUT_ENV is stripped, so 30 minutes)
        # fails here instead of hanging the run.
        line = []
        reader = threading.Thread(target=lambda: line.append(child.stdout.readline()), daemon=True)
        reader.start()
        reader.join(limit)
        if reader.is_alive():
            child.kill()
            self.fail(f"lock holder did not report 'held' within {limit}s")
        self.assertEqual(line[0].strip(), "held", child.stderr.read() if child.poll() is not None else "")

    def guarded(self, timeout, limit=60):
        command = [sys.executable, str(self.fake), "run", str(LOCK_MODULE), str(self.marker)]
        # run_stage waits for the lock in this process; bound it so a guard that ignores the lock
        # wait limit fails here instead of waiting 30 minutes.
        result = {}
        runner = threading.Thread(target=lambda: result.update(
            code=guard.run_stage(guard_args(self.root / "guard", command, timeout), sample)), daemon=True)
        runner.start()
        runner.join(limit)
        if runner.is_alive():
            self.fail(f"resource_guard still running after {limit}s")
        code = result["code"]
        summary = json.loads((self.root / "guard/summary.json").read_text(encoding="utf-8"))
        log = (self.root / "guard/command.log").read_text(encoding="utf-8") if (self.root / "guard/command.log").exists() else ""
        return code, summary, log

    def test_lock_file_is_2hours_own(self):
        self.assertEqual(asr_lock.lock_path(), self.home / ".2hours/recorder/run/asr.lock")
        self.assertNotIn(".blurt", str(asr_lock.lock_path()))

    def test_wait_limit_default_and_env_override(self):
        self.assertEqual(asr_lock.wait_limit(), 30 * 60)
        with patch.dict(os.environ, {asr_lock.TIMEOUT_ENV: "2.5"}):
            self.assertEqual(asr_lock.wait_limit(), 2.5)
        for bad in ("soon", "0", "-1", "nan", "inf"):
            with self.subTest(bad=bad), patch.dict(os.environ, {asr_lock.TIMEOUT_ENV: bad}):
                with self.assertRaises(asr_lock.LockError):
                    asr_lock.wait_limit()

    def test_second_holder_times_out_with_exit_3_after_env_limit(self):
        first = self.holder()
        self.held(first)
        began = time.monotonic()
        second = self.holder(**{asr_lock.TIMEOUT_ENV: "0.5"})
        _, err = second.communicate(timeout=20)
        self.assertEqual(second.returncode, 3, err)
        self.assertLess(time.monotonic() - began, 10)
        self.assertIn("有别的转写在跑，排队中", err)
        self.assertIn("稍后重试", err)
        self.assertIsNone(first.poll())

    def test_lock_wait_is_not_counted_in_guard_stage_limit(self):
        first = self.holder()
        self.held(first)
        release = threading.Timer(1.5, first.stdin.close)
        release.start()
        self.addCleanup(release.cancel)
        began = time.monotonic()
        code, summary, log = self.guarded(timeout=1)
        self.assertEqual(code, 0, (summary, log))
        self.assertEqual(summary["state"], "completed")
        self.assertGreaterEqual(time.monotonic() - began, 1.4)
        self.assertGreaterEqual(summary["asr_lock_wait_seconds"], 1.0)
        self.assertTrue(self.marker.exists())

    def test_guard_lock_timeout_exits_3_without_starting_stage(self):
        first = self.holder()
        self.held(first)
        with patch.dict(os.environ, {asr_lock.TIMEOUT_ENV: "0.5"}):
            code, summary, _ = self.guarded(timeout=30)
        self.assertEqual(code, 3)
        self.assertEqual(summary["state"], "lock_timeout")
        self.assertIn("稍后重试", summary["reason"])
        self.assertNotIn("pid", summary)
        self.assertFalse(self.marker.exists())

    def test_guard_child_runs_under_guard_lock_without_waiting(self):
        with patch.dict(os.environ, {asr_lock.TIMEOUT_ENV: "20"}):
            code, summary, log = self.guarded(timeout=3)
        self.assertEqual(code, 0, (summary, log))
        self.assertEqual(summary["state"], "completed")
        self.assertNotIn("queued", log)
        self.assertTrue(self.marker.exists())
        # Released afterwards: a new run gets it at once.
        after = self.holder(**{asr_lock.TIMEOUT_ENV: "0.5"})
        self.held(after)

    def test_handoff_marker_without_the_lock_is_not_trusted(self):
        first = self.holder()
        self.held(first)
        forged = "token:" + "0" * asr_lock.TOKEN_BYTES if os.name == "nt" else "fd:0"
        second = self.holder(**{asr_lock.TIMEOUT_ENV: "0.5", asr_lock.HANDOFF_ENV: forged})
        _, err = second.communicate(timeout=20)
        self.assertEqual(second.returncode, 3, err)

    @unittest.skipIf(os.name == "nt", "POSIX descriptor handoff")
    def test_handoff_descriptor_of_lock_file_without_its_lock_is_not_trusted(self):
        first = self.holder()
        self.held(first)
        # The right file, but a separate open of it: it does not carry first's lock.
        with asr_lock.lock_path().open("rb") as stray:
            fd = stray.fileno()
            second = subprocess.Popen([sys.executable, "-c", HOLDER, str(LOCK_MODULE)], pass_fds=(fd,),
                                      env=home_env(self.home, **{asr_lock.TIMEOUT_ENV: "0.5",
                                                                 asr_lock.HANDOFF_ENV: f"fd:{fd}"}),
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.children.append(second)
            out, err = second.communicate(timeout=20)
        self.assertNotIn("held", out)
        self.assertEqual(second.returncode, 3, err)
        self.assertIsNone(first.poll())

    @unittest.skipIf(os.name == "nt", "POSIX descriptor handoff")
    def test_child_keeps_lock_if_guard_side_closes_first(self):
        with asr_lock.slot(handoff=True) as held:
            child = subprocess.Popen([sys.executable, "-c", HOLDER, str(LOCK_MODULE)],
                                     env=home_env(self.home, **held.env), pass_fds=held.pass_fds,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.children.append(child)
            self.held(child)
        waiter = self.holder(**{asr_lock.TIMEOUT_ENV: "0.5"})
        _, err = waiter.communicate(timeout=20)
        self.assertEqual(waiter.returncode, 3, err)
        child.stdin.close()
        child.wait(timeout=20)
        self.held(self.holder())

    @unittest.skipUnless(os.name == "nt", "Windows junctions and hard links")
    def test_windows_lock_refuses_junction_and_hardlink(self):
        import _winapi
        external = self.root / "external"
        external.mkdir()
        recorder = self.home / ".2hours/recorder"
        recorder.mkdir(parents=True)
        _winapi.CreateJunction(str(external), str(recorder / "run"))
        with self.assertRaises(asr_lock.LockError):
            with asr_lock.slot():
                self.fail("junction lock unexpectedly acquired")
        self.assertFalse(list(external.iterdir()))
        os.rmdir(recorder / "run")
        (recorder / "run").mkdir()
        target = external / "data.bin"
        target.write_bytes(b"")
        os.link(target, recorder / "run/asr.lock")
        with self.assertRaises(asr_lock.LockError):
            with asr_lock.slot(handoff=True):
                self.fail("hardlink lock unexpectedly acquired")
        self.assertEqual(target.read_bytes(), b"")


def transcription_ready():
    return shutil.which("ffmpeg") and importlib.util.find_spec("numpy") is not None


class Box:
    def __init__(self, root):
        self.root = root
        self.home = root / "home"
        self.data = self.home / ".2hours/recorder"
        self.log = root / "fake.jsonl"
        # Pinned size + SHA-256 marker, so the files also pass the verified-download check from upstream
        # b3b836ac (MODEL_SIZES); the fake backend never reads the bytes.
        pinned = {
            "silero_vad.onnx": ("9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6", 643854),
            "sensevoice/model.int8.onnx": ("c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51", 239233841),
            "sensevoice/tokens.txt": ("f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc", 315894),
        }
        for rel, (sha, size) in pinned.items():
            path = self.data / "models" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"x")
                f.truncate(size)
            st = path.stat()
            path.with_name(path.name + ".verified").write_text(
                json.dumps({"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha}))
        self.wav = root / "in.wav"
        subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=24", "-ar", "16000", "-ac", "1", str(self.wav)], check=True)

    def env(self, iters, **extra):
        return home_env(self.home, PYTHONPATH=str(FAKE), PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1",
                        OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", FAKE_SHERPA_LOG=str(self.log), FAKE_ITERS=iters,
                        **extra)

    def command(self, name):
        return [sys.executable, "-B", str(TRANSCRIBE), "run", str(self.wav), "--out", str(self.root / name),
                "--backend", "sensevoice"]

    def start(self, name, iters, **extra):
        err = open(self.root / f"{name}.stderr", "w", encoding="utf-8")
        process = subprocess.Popen(self.command(name), env=self.env(iters, **extra), stdout=subprocess.DEVNULL,
                                   stderr=err, start_new_session=os.name != "nt")
        err.close()
        process.name, process.err = name, self.root / f"{name}.stderr"
        return process

    def events(self, process, kind):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]
        return [row["t"] for row in rows if row["pid"] == process.pid and row["event"] == kind]

    def wait_decoding(self, process, limit=30):
        end = time.time() + limit
        while time.time() < end:
            if self.events(process, "decode_start"):
                return
            if process.poll() is not None:
                raise AssertionError(f"{process.name} exited {process.returncode} before decoding: "
                                     f"{process.err.read_text(encoding='utf-8')}")
            time.sleep(0.05)
        raise AssertionError(f"{process.name} never started decoding")


def kill_tree(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def finish(process, limit=60):
    try:
        return process.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        raise AssertionError(f"{process.name} still running after {limit}s; stderr: "
                             f"{process.err.read_text(encoding='utf-8')[-400:]}")


@unittest.skipUnless(transcription_ready(), "needs ffmpeg and numpy")
class TranscribeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.box = Box(Path(temp.name))
        self.procs = []
        self.addCleanup(self.kill_all)

    def kill_all(self):
        for process in self.procs:
            if process.poll() is None:
                kill_tree(process)

    def start(self, *args, **kwargs):
        process = self.box.start(*args, **kwargs)
        self.procs.append(process)
        return process

    def test_single_run_still_works(self):
        process = self.start("solo", SHORT)
        self.assertEqual(finish(process), 0, process.err.read_text(encoding="utf-8"))
        self.assertTrue((self.box.root / "solo/transcript.txt").exists())
        self.assertNotIn("queued", process.err.read_text(encoding="utf-8"))

    def test_second_run_waits_for_first(self):
        first = self.start("a", SHORT)
        self.box.wait_decoding(first)
        second = self.start("b", SHORT)
        self.assertEqual(finish(first), 0, first.err.read_text(encoding="utf-8"))
        self.assertEqual(finish(second), 0, second.err.read_text(encoding="utf-8"))
        first_end = max(self.box.events(first, "decode_end"))
        second_start = min(self.box.events(second, "decode_start"))
        self.assertGreaterEqual(second_start, first_end, f"decode overlap {first_end - second_start:.2f}s")

    def test_waiting_run_says_it_is_queued(self):
        first = self.start("a", SHORT)
        self.box.wait_decoding(first)
        second = self.start("b", SHORT)
        finish(first), finish(second)
        err = second.err.read_text(encoding="utf-8")
        self.assertIn("queued", err)
        self.assertIn("有别的转写在跑，排队中", err)
        self.assertIn("slot free", err)

    def test_lock_file_lives_in_own_data_dir(self):
        process = self.start("solo", SHORT)
        self.assertEqual(finish(process), 0, process.err.read_text(encoding="utf-8"))
        self.assertTrue((self.box.data / "run/asr.lock").exists())

    @unittest.skipIf(os.name == "nt", "SIGKILL of a process group")
    def test_waiter_proceeds_after_holder_killed(self):
        holder = self.start("holder", LONG)
        self.box.wait_decoding(holder)
        waiter = self.start("waiter", SHORT)
        time.sleep(3)
        os.killpg(holder.pid, signal.SIGKILL)
        holder.wait()
        killed = time.time()
        self.assertEqual(finish(waiter), 0, waiter.err.read_text(encoding="utf-8"))
        first = min(self.box.events(waiter, "decode_start"))
        self.assertGreaterEqual(first, killed - 0.05, f"waiter decoded {killed - first:.2f}s before holder died")
        self.assertLess(first - killed, 5)

    def test_wait_timeout_exits_3(self):
        holder = self.start("holder", LONG)
        self.box.wait_decoding(holder)
        waiter = self.start("waiter", SHORT, TWOHOURS_ASR_LOCK_TIMEOUT="2")
        code = finish(waiter, limit=20)
        self.assertIsNone(holder.poll(), "holder finished; the test did not exercise a timeout")
        self.assertEqual(code, 3, waiter.err.read_text(encoding="utf-8"))
        self.assertRegex(waiter.err.read_text(encoding="utf-8"), r"(?i)slot|busy|lock")
        self.assertEqual(self.box.events(waiter, "decode_start"), [])

    def test_guarded_transcription_does_not_wait_for_its_guard(self):
        with patch.dict(os.environ, self.box.env(SHORT, TWOHOURS_ASR_LOCK_TIMEOUT="60"), clear=True):
            code = guard.run_stage(guard_args(self.box.root / "guard", self.box.command("guarded"), 60), sample)
        log = (self.box.root / "guard/command.log").read_text(encoding="utf-8")
        self.assertEqual(code, 0, log)
        self.assertNotIn("queued", log)
        self.assertTrue((self.box.root / "guarded/transcript.txt").exists())

    def test_direct_run_waits_for_guarded_run(self):
        result = {}
        with patch.dict(os.environ, self.box.env(LONG), clear=True):
            stage = threading.Thread(target=lambda: result.update(code=guard.run_stage(
                guard_args(self.box.root / "guard", self.box.command("guarded"), 8), sample)))
            stage.start()
            self.addCleanup(stage.join)
            end = time.time() + 30
            while time.time() < end and not [line for line in (self.box.log.read_text().splitlines()
                                                                if self.box.log.exists() else []) if "decode_start" in line]:
                time.sleep(0.05)
            direct = self.start("direct", SHORT, TWOHOURS_ASR_LOCK_TIMEOUT="2")
            code = finish(direct, limit=20)
            stage.join()
        self.assertEqual(code, 3, direct.err.read_text(encoding="utf-8"))
        self.assertEqual(self.box.events(direct, "decode_start"), [])
        self.assertEqual(result["code"], guard.PAUSED)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_windows_job_object_stop_releases_lock(self):
        with open(self.box.root / "holder.log", "w", encoding="utf-8") as log:
            job = guard.WindowsJobProcess(self.box.command("holder"), log, env=self.box.env(LONG))
            try:
                time.sleep(5)
                waiter = self.start("waiter", SHORT)
                time.sleep(2)
            finally:
                job.stop()
        self.assertEqual(finish(waiter), 0, waiter.err.read_text(encoding="utf-8"))
        self.assertIn("queued", waiter.err.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
