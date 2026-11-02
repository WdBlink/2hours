#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["psutil>=7.0; sys_platform == 'win32'"]
# ///
"""Run one foreground media stage with a macOS/Windows memory gate and telemetry."""
import argparse
import ctypes
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import datetime

GIB = 1024 ** 3
PAUSED = 75

if os.name == "nt":
    _spec = importlib.util.spec_from_file_location("resource_windows_job", Path(__file__).with_name("windows_job.py"))
    _job_module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_job_module)
    WindowsJobProcess = _job_module.JobProcess
_spec = importlib.util.spec_from_file_location("resource_asr_lock", Path(__file__).with_name("asr_lock.py"))
asr_lock = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(asr_lock)


class RUsageInfoV4(ctypes.Structure):
    # ABI from the macOS SDK's sys/resource.h, RUSAGE_INFO_V4.
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "user_time system_time pkg_idle_wkups interrupt_wkups pageins wired_size "
            "resident_size phys_footprint proc_start_abstime proc_exit_abstime "
            "child_user_time child_system_time child_pkg_idle_wkups child_interrupt_wkups "
            "child_pageins child_elapsed_abstime diskio_bytesread diskio_byteswritten "
            "cpu_time_qos_default cpu_time_qos_maintenance cpu_time_qos_background "
            "cpu_time_qos_utility cpu_time_qos_legacy cpu_time_qos_user_initiated "
            "cpu_time_qos_user_interactive billed_system_time serviced_system_time "
            "logical_writes lifetime_max_phys_footprint instructions cycles billed_energy "
            "serviced_energy interval_max_phys_footprint runnable_time"
        ).split()
    ]


class WindowsPerformanceInfo(ctypes.Structure):
    # https://learn.microsoft.com/windows/win32/api/psapi/ns-psapi-performance_information
    _fields_ = [("cb", ctypes.c_uint32)] + [
        (name, ctypes.c_size_t) for name in (
            "CommitTotal CommitLimit CommitPeak PhysicalTotal PhysicalAvailable "
            "SystemCache KernelTotal KernelPaged KernelNonpaged PageSize").split()
    ] + [(name, ctypes.c_uint32) for name in ("HandleCount", "ProcessCount", "ThreadCount")]


def stamp():
    return datetime.now().astimezone().isoformat()


def command_output(argv):
    return subprocess.check_output(argv, text=True, stderr=subprocess.PIPE, timeout=3)


def parse_system(sysctl, vmstat):
    values = dict(line.split(": ", 1) for line in sysctl.splitlines() if ": " in line)
    page_size = int(re.search(r"page size of (\d+) bytes", vmstat)[1])
    pages = {name.strip('"'): int(value) for name, value in
             re.findall(r'^(.+?):\s+(\d+)\.', vmstat, re.M)}
    used = re.search(r"used = ([\d.]+)M", values["vm.swapusage"])
    if not used:
        raise ValueError("cannot read swap usage")
    return {
        "pressure": int(values["kern.memorystatus_vm_pressure_level"]),
        "ram_bytes": int(values["hw.memsize"]),
        "swap_used_bytes": int(float(used[1]) * 1024 ** 2),
        "compressor_bytes": pages["Pages occupied by compressor"] * page_size,
        "swapout_bytes": pages["Swapouts"] * page_size,
    }


class Sampler:
    def __init__(self):
        self.stage = None
        if sys.platform == "win32":
            import psutil
            self.psutil = psutil
            self.performance_api = ctypes.WinDLL("psapi", use_last_error=True)
            self.performance_api.GetPerformanceInfo.argtypes = [ctypes.POINTER(WindowsPerformanceInfo), ctypes.c_uint32]
            self.performance_api.GetPerformanceInfo.restype = ctypes.c_int
            return
        if sys.platform != "darwin":
            raise RuntimeError("memory protection requires macOS or Windows; refusing unguarded execution")
        self.lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self.lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        self.lib.proc_pid_rusage.restype = ctypes.c_int

    def __call__(self):
        if sys.platform == "win32":
            return self.windows_sample()
        system = parse_system(command_output([
            "sysctl", "hw.memsize", "vm.swapusage", "kern.memorystatus_vm_pressure_level"
        ]), command_output(["vm_stat"]))
        output = command_output(["ps", "-axo", "pid=,ppid=,pgid=,rss=,lstart=,comm="])
        processes = []
        for line in output.splitlines():
            fields = line.split(None, 9)
            if len(fields) != 10:
                continue
            pid, ppid, pgid, rss = map(int, fields[:4])
            usage = RUsageInfoV4()
            ok = self.lib.proc_pid_rusage(pid, 4, ctypes.byref(usage)) == 0
            birth = str(usage.proc_start_abstime) if ok else " ".join(fields[4:9])
            processes.append({
                "key": f"{pid}:{birth}", "pid": pid, "ppid": ppid, "pgid": pgid,
                "name": Path(fields[9]).name, "rss_bytes": rss * 1024,
                "footprint_bytes": usage.phys_footprint if ok else None,
                "lifetime_peak_bytes": usage.lifetime_max_phys_footprint if ok else None,
            })
        if not processes:
            raise RuntimeError("process sampling returned no records")
        return {"time": stamp(), "system": system, "processes": processes}

    def set_stage(self, process):
        self.stage = process

    def windows_sample(self):
        psutil = self.psutil
        memory, swap = psutil.virtual_memory(), psutil.swap_memory()
        performance = WindowsPerformanceInfo()
        performance.cb = ctypes.sizeof(performance)
        if not self.performance_api.GetPerformanceInfo(ctypes.byref(performance), performance.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        if not performance.CommitLimit:
            raise RuntimeError("Windows returned an invalid system commit limit")
        commit_percent = performance.CommitTotal * 100 / performance.CommitLimit
        # Windows psutil exposes pagefile usage but not compressed-memory size
        # or cumulative page-out traffic. Unsupported counters remain null.
        system = {
            "pressure": 2 if memory.percent >= 90 or commit_percent >= 90 else 1,
            "ram_bytes": memory.total, "available_bytes": memory.available,
            "memory_percent": memory.percent, "commit_percent": commit_percent,
            "commit_bytes": performance.CommitTotal * performance.PageSize,
            "commit_limit_bytes": performance.CommitLimit * performance.PageSize,
            "swap_used_bytes": swap.used,
            "compressor_bytes": None, "swapout_bytes": None,
        }
        members = self.stage.pids() if self.stage else set()
        processes = []
        for process in psutil.process_iter():
            try:
                with process.oneshot():
                    info = process.memory_info()
                    birth = process.create_time()
                    processes.append({
                        "key": f"{process.pid}:{birth}", "pid": process.pid,
                        "ppid": process.ppid(),
                        "pgid": self.stage.pid if process.pid in members else 0,
                        "name": process.name(), "rss_bytes": info.rss,
                        "footprint_bytes": info.private,
                        "lifetime_peak_bytes": max(info.peak_wset, info.peak_pagefile),
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if not processes:
            raise RuntimeError("Windows process sampling returned no records")
        if self.stage:
            # A target may exit during enumeration. A live owned PID that
            # cannot be read must instead stop the stage, never run unguarded.
            live_members = self.stage.pids()
            missing = (members & live_members) - {p["pid"] for p in processes}
            if missing:
                raise RuntimeError(f"cannot sample live stage processes: {sorted(missing)}")
            for process in processes:
                process["pgid"] = self.stage.pid if process["pid"] in live_members else 0
        return {"time": stamp(), "system": system, "processes": processes}


def pressure_reason(sample, initial, pgid, max_stage_bytes, max_swap_growth_bytes):
    system = sample["system"]
    if system["pressure"] != 1:
        return f"system memory pressure={system['pressure']} (normal=1)"
    if system["compressor_bytes"] is not None and system["compressor_bytes"] > system["ram_bytes"] * 0.40:
        return "compressor exceeds 40% of physical memory"
    if system["swap_used_bytes"] - initial["system"]["swap_used_bytes"] > max_swap_growth_bytes:
        return "swap grew beyond the stage allowance"
    if (system["swapout_bytes"] is not None and initial["system"]["swapout_bytes"] is not None
            and system["swapout_bytes"] - initial["system"]["swapout_bytes"] > max_swap_growth_bytes):
        return "swap-out traffic exceeded the stage allowance"
    owned = [p for p in sample["processes"] if pgid and p["pgid"] == pgid]
    total = sum(max(p["rss_bytes"], p["footprint_bytes"] or 0) for p in owned)
    if total > max_stage_bytes:
        return "stage process group exceeded its memory allowance"
    return None


class Journal:
    def __init__(self, directory, stage):
        self.directory = directory
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.stream = (directory / "samples.jsonl").open("x", encoding="utf-8")
        self.summary = {"stage": stage, "started_at": stamp(), "state": "preflight",
                        "samples": 0, "process_peaks": {}, "stage_peak_bytes": 0,
                        "notes": ["Peaks are sampled unless explicitly marked lifetime.",
                                  "Lifetime peaks for background processes may predate this stage.",
                                  "Group totals may double-count shared memory."]}

    def sample(self, sample, pgid):
        owned = [p for p in sample["processes"] if pgid and p["pgid"] == pgid]
        total = sum(max(p["rss_bytes"], p["footprint_bytes"] or 0) for p in owned)
        self.summary["stage_peak_bytes"] = max(self.summary["stage_peak_bytes"], total)
        self.summary["samples"] += 1
        self.summary["last_system"] = sample["system"]
        for process in sample["processes"]:
            key = process["key"]
            peak = self.summary["process_peaks"].setdefault(key, {
                **process, "first_seen": sample["time"], "owned": False,
                "peak_rss_bytes": 0, "peak_footprint_bytes": 0,
            })
            peak["last_seen"] = sample["time"]
            peak["owned"] |= bool(pgid and process["pgid"] == pgid)
            peak["peak_rss_bytes"] = max(peak["peak_rss_bytes"], process["rss_bytes"])
            peak["peak_footprint_bytes"] = max(peak["peak_footprint_bytes"], process["footprint_bytes"] or 0)
            peak["lifetime_peak_bytes"] = max(peak["lifetime_peak_bytes"] or 0,
                                              process["lifetime_peak_bytes"] or 0)
        # Keep the timeline small; the summary retains peaks for every observed process.
        largest = sorted(sample["processes"], key=lambda p: max(p["rss_bytes"], p["footprint_bytes"] or 0), reverse=True)[:25]
        selected = {p["key"]: p for p in largest + owned}
        self.stream.write(json.dumps({**sample, "processes": list(selected.values()),
                                      "stage_bytes": total}, ensure_ascii=False) + "\n")
        self.stream.flush()
        self.save()

    def save(self):
        target = self.directory / "summary.json"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)

    def close(self):
        self.summary["ended_at"] = stamp()
        self.save()
        self.stream.close()


def stop_group(process):
    """Release only this stage's resources; suspended processes retain their allocations."""
    if os.name == "nt":
        process.stop()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    end = time.monotonic() + 2
    while time.monotonic() < end:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def run_stage(args, sampler=None):
    journal = Journal(Path(args.output), args.stage)
    process = None
    exit_code = 1
    locks = ExitStack()
    try:
        held = None
        if args.command and asr_lock.is_transcription(args.command):
            # Wait for the machine-wide ASR lock before the stage clock starts: queueing behind
            # another transcription is not part of --timeout. The child gets the lock handed over.
            try:
                held = locks.enter_context(asr_lock.slot(handoff=True))
            except asr_lock.LockTimeout as error:
                journal.summary.update(state="lock_timeout", reason=str(error))
                print(error, file=sys.stderr, flush=True)
                return asr_lock.TIMEOUT_EXIT
            journal.summary["asr_lock_wait_seconds"] = round(held.waited, 3)
        env = {**os.environ, **held.env} if held else None
        sampler = sampler or Sampler()
        initial = sampler()
        journal.summary["initial_system"] = initial["system"]
        journal.summary["limits"] = {"normal_pressure": 1, "max_compressor_fraction": .4,
                                     "max_stage_gib": args.max_stage_gib,
                                     "max_swap_growth_mib": args.max_swap_growth_mib,
                                     "interval_seconds": args.interval}
        for index in range(3):
            sample = initial if index == 0 else sampler()
            journal.sample(sample, None)
            reason = pressure_reason(sample, initial, None, args.max_stage_gib * GIB,
                                     args.max_swap_growth_mib * 1024 ** 2)
            if reason:
                journal.summary.update(state="paused_pressure", reason=reason)
                return PAUSED
            if index < 2:
                time.sleep(args.interval)
        start = time.monotonic()
        with (journal.directory / "command.log").open("x", encoding="utf-8") as log:
            if args.command:
                if os.name == "nt":
                    process = WindowsJobProcess(args.command, log, env=env)
                else:
                    process = subprocess.Popen(["/usr/bin/nice", "-n", "10", *args.command],
                                               stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                               start_new_session=True, env=env,
                                               pass_fds=held.pass_fds if held else ())
                if hasattr(sampler, "set_stage"):
                    sampler.set_stage(process)
                journal.summary["pid"] = process.pid
            journal.summary["state"] = "running" if process else "observing"
            journal.save()
            while True:
                sample = sampler()
                journal.sample(sample, process.pid if process else None)
                reason = pressure_reason(sample, initial, process.pid if process else None,
                                         args.max_stage_gib * GIB, args.max_swap_growth_mib * 1024 ** 2)
                if reason:
                    journal.summary.update(state="paused_pressure", reason=reason)
                    exit_code = PAUSED
                    break
                elapsed = time.monotonic() - start
                if process and process.poll() is not None:
                    exit_code = process.returncode
                    journal.summary.update(state="completed" if exit_code == 0 else "failed",
                                           returncode=exit_code)
                    break
                if not process and elapsed >= args.observe:
                    journal.summary["state"] = "completed"
                    exit_code = 0
                    break
                if elapsed >= args.timeout:
                    journal.summary.update(state="paused_timeout", reason="stage deadline exceeded")
                    exit_code = PAUSED
                    break
                time.sleep(args.interval)
    except KeyboardInterrupt:
        journal.summary.update(state="paused_interrupt", reason="interrupted by operator")
        exit_code = PAUSED
    except Exception as error:
        journal.summary.update(state="failed_monitor", reason=f"{type(error).__name__}: {error}")
        exit_code = 1
    finally:
        try:
            if process:
                stop_group(process)
        finally:
            locks.close()
            journal.close()
            print(json.dumps({"stage": args.stage, "state": journal.summary["state"],
                              "reason": journal.summary.get("reason"),
                              "summary": str(journal.directory / "summary.json")}, ensure_ascii=False), flush=True)
    return exit_code


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def interrupted(_signum, _frame):
    raise KeyboardInterrupt()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new private directory; existing directories are never overwritten")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--interval", type=positive, default=1)
    parser.add_argument("--timeout", type=positive, default=300)
    parser.add_argument("--observe", type=positive, default=10, help="seconds to observe when no command is supplied")
    parser.add_argument("--max-stage-gib", type=positive, default=4)
    parser.add_argument("--max-swap-growth-mib", type=positive, default=512)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command.pop(0)
    if not 0.2 <= args.interval <= 2:
        parser.error("--interval must be between 0.2 and 2 seconds")
    os.umask(0o077)
    signal.signal(signal.SIGTERM, interrupted)
    return run_stage(args)


if __name__ == "__main__":
    sys.exit(main())
