import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("resource_guard", Path(__file__).parents[1] / "scripts/resource_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def sample(pressure=1, swap=0, compressor=0, processes=None):
    return {"time": guard.stamp(), "system": {
        "pressure": pressure, "ram_bytes": 64 * guard.GIB, "swap_used_bytes": swap,
        "swapout_bytes": 0, "compressor_bytes": compressor,
    }, "processes": processes or []}


def args(directory, command=None):
    return argparse.Namespace(output=str(directory), stage="test", interval=.02, timeout=3,
                              observe=.05, max_stage_gib=4, max_swap_growth_mib=512,
                              command=command or [])


class GuardTests(unittest.TestCase):
    def windows_process_handle(self, pid):
        api = guard._job_module._kernel()
        handle = api.OpenProcess(0x100000 | 0x1000, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return api, handle

    def assert_windows_terminated(self, api, handle):
        # A process handle is signaled only after termination. Unlike a PID
        # lookup this still identifies the same child after PID reuse and does
        # not require its process object to have disappeared from enumeration.
        self.assertEqual(api.WaitForSingleObject(handle, 0), 0)

    def test_warning_blocks_spawn_and_preserves_existing_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "started"
            command = [sys.executable, "-c", "from pathlib import Path; Path(%r).touch()" % str(marker)]
            self.assertEqual(guard.run_stage(args(root / "guard", command), lambda: sample(2)), guard.PAUSED)
            self.assertFalse(marker.exists())
            self.assertEqual(json.loads((root / "guard/summary.json").read_text())["state"], "paused_pressure")
            with self.assertRaises(FileExistsError):
                guard.run_stage(args(root / "guard"), lambda: sample())

    def test_swap_churn_and_stage_memory_trip_even_at_normal_pressure(self):
        initial = sample()
        self.assertIsNotNone(guard.pressure_reason(sample(swap=guard.GIB), initial, None, 4 * guard.GIB, guard.GIB // 2))
        swapped = sample()
        swapped["system"]["swapout_bytes"] = guard.GIB
        self.assertIsNotNone(guard.pressure_reason(swapped, initial, None, 4 * guard.GIB, guard.GIB // 2))
        owned = {"pgid": 42, "rss_bytes": guard.GIB, "footprint_bytes": 5 * guard.GIB}
        self.assertIsNotNone(guard.pressure_reason(sample(processes=[owned]), initial, 42, 4 * guard.GIB, guard.GIB))
        self.assertIsNone(guard.pressure_reason(sample(processes=[owned]), initial, 99, 4 * guard.GIB, guard.GIB))
        self.assertIsNotNone(guard.pressure_reason(sample(compressor=30 * guard.GIB), initial, None, 4 * guard.GIB, guard.GIB))

    def test_monitor_failure_stops_instead_of_running_unprotected(self):
        with tempfile.TemporaryDirectory() as tmp:
            def unavailable():
                raise OSError("sampling unavailable")
            self.assertEqual(guard.run_stage(args(Path(tmp) / "guard"), unavailable), 1)
            self.assertEqual(json.loads((Path(tmp) / "guard/summary.json").read_text())["state"], "failed_monitor")

    def test_pressure_stops_stage_descendants_without_touching_unrelated_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "child.pid"
            unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"], start_new_session=True)
            command = [sys.executable, "-c", "import subprocess,sys,time; from pathlib import Path; "
                       "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']); "
                       "Path(%r).write_text(str(p.pid)); time.sleep(20)" % str(marker)]
            child_handles = []

            def check_pressure():
                if not marker.exists():
                    return sample()
                if os.name == "nt" and not child_handles:
                    child_handles.append(self.windows_process_handle(int(marker.read_text())))
                return sample(2)

            try:
                result = guard.run_stage(args(root / "guard", command), check_pressure)
                self.assertEqual(result, guard.PAUSED)
                self.assertIsNone(unrelated.poll())
                child = int(marker.read_text())
                if os.name == "nt":
                    self.assert_windows_terminated(*child_handles[0])
                else:
                    state = subprocess.run(["ps", "-p", str(child), "-o", "state="], capture_output=True, text=True).stdout.strip()
                    self.assertTrue(not state or state.startswith("Z"), state)
            finally:
                unrelated.terminate()
                unrelated.wait()
                for api, handle in child_handles:
                    api.CloseHandle(handle)

    def test_successful_work_really_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "result.txt"
            command = [sys.executable, "-c", "from pathlib import Path; Path(%r).write_text('complete')" % str(output)]
            self.assertEqual(guard.run_stage(args(root / "guard", command), lambda: sample()), 0)
            self.assertEqual(output.read_text(), "complete")
            self.assertEqual(json.loads((root / "guard/summary.json").read_text())["state"], "completed")

    def test_deadline_stops_child_and_marks_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = args(Path(tmp) / "guard", [sys.executable, "-c", "import time; time.sleep(10)"])
            a.timeout = .05
            self.assertEqual(guard.run_stage(a, lambda: sample()), guard.PAUSED)
            self.assertEqual(json.loads((Path(tmp) / "guard/summary.json").read_text())["state"], "paused_timeout")

    def test_system_parser_requires_real_metrics(self):
        ctl = "hw.memsize: 68719476736\nvm.swapusage: total = 1000.00M  used = 25.50M  free = 974.50M\nkern.memorystatus_vm_pressure_level: 1"
        vm = 'Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages occupied by compressor: 12.\nSwapouts: 34.\n'
        data = guard.parse_system(ctl, vm)
        self.assertEqual(data["compressor_bytes"], 12 * 16384)
        self.assertEqual(data["swap_used_bytes"], 25.5 * 1024 ** 2)
        with self.assertRaises((AttributeError, KeyError, TypeError)):
            guard.parse_system("", vm)

    @unittest.skipUnless(sys.platform == "darwin", "native macOS probe")
    def test_native_probe_has_own_process_footprint(self):
        snapshot = guard.Sampler()()
        own = next(p for p in snapshot["processes"] if p["pid"] == os.getpid())
        self.assertGreater(own["footprint_bytes"], 0)
        self.assertGreaterEqual(own["lifetime_peak_bytes"], own["footprint_bytes"])
        self.assertEqual(snapshot["system"]["ram_bytes"], int(subprocess.check_output(["sysctl", "-n", "hw.memsize"])))

    @unittest.skipUnless(os.name == "nt", "native Windows probe")
    def test_windows_probe_has_real_memory_metrics(self):
        snapshot = guard.Sampler()()
        own = next(p for p in snapshot["processes"] if p["pid"] == os.getpid())
        self.assertGreater(own["rss_bytes"], 0)
        self.assertGreater(own["footprint_bytes"], 0)
        self.assertGreaterEqual(own["lifetime_peak_bytes"], own["footprint_bytes"])
        self.assertGreater(snapshot["system"]["available_bytes"], 0)
        self.assertGreater(snapshot["system"]["ram_bytes"], snapshot["system"]["available_bytes"])
        self.assertGreater(snapshot["system"]["commit_bytes"], 0)
        self.assertGreater(snapshot["system"]["commit_limit_bytes"], snapshot["system"]["commit_bytes"])
        self.assertIsNone(snapshot["system"]["swapout_bytes"])

    @unittest.skipUnless(os.name == "nt", "native Windows job isolation")
    def test_job_membership_survives_parent_exit_and_closing_kills_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "child.pid"
            command = [sys.executable, "-c", "import subprocess,sys; from pathlib import Path; "
                       "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']); "
                       "Path(%r).write_text(str(p.pid))" % str(marker)]
            with (root / "log").open("w") as log:
                process = guard.WindowsJobProcess(command, log)
                child_handle = None
                try:
                    self.assertEqual(process.wait(timeout=10), 0)
                    child = int(marker.read_text())
                    api, child_handle = self.windows_process_handle(child)
                    self.assertIn(child, process.pids())
                    sampler = guard.Sampler()
                    sampler.set_stage(process)
                    record = next(p for p in sampler()["processes"] if p["pid"] == child)
                    self.assertEqual(record["pgid"], process.pid)
                finally:
                    try:
                        process.stop()
                        if child_handle:
                            self.assert_windows_terminated(api, child_handle)
                    finally:
                        if child_handle:
                            api.CloseHandle(child_handle)

    @unittest.skipUnless(os.name == "nt", "native Windows memory gate")
    def test_native_stage_memory_limit_stops_only_the_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
            command = [sys.executable, "-c", "import time; allocation=bytearray(96*1024*1024); time.sleep(20)"]
            a = args(root / "guard", command)
            a.max_stage_gib = .07
            a.timeout = 10
            try:
                self.assertEqual(guard.run_stage(a), guard.PAUSED)
                self.assertIsNone(unrelated.poll())
                summary = json.loads((root / "guard/summary.json").read_text(encoding="utf-8"))
                self.assertIn("memory allowance", summary["reason"])
                self.assertGreater(summary["stage_peak_bytes"], 70 * 1024 ** 2)
                self.assertTrue(any(p["owned"] for p in summary["process_peaks"].values()))
            finally:
                unrelated.terminate()
                unrelated.wait()

    @unittest.skipUnless(os.name == "nt", "native Windows job assignment")
    def test_failed_job_assignment_never_starts_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "must-not-exist"
            command = [sys.executable, "-c", "from pathlib import Path; Path(%r).touch()" % str(marker)]
            api = guard._job_module._kernel()

            class DeniedAssignment:
                def __getattr__(self, name):
                    return getattr(api, name)

                def AssignProcessToJobObject(self, *_):
                    return False

            with (root / "log").open("w") as log:
                with patch.object(guard._job_module, "_kernel", return_value=DeniedAssignment()):
                    with self.assertRaises(OSError):
                        guard.WindowsJobProcess(command, log)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
