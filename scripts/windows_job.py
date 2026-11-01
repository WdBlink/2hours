"""Isolate one command and its descendants in a Windows kill-on-close job.

The launcher waits on a pipe until job assignment succeeds. This closes the
spawn-before-assignment race without private process-creation or NT APIs. Target
stdin is DEVNULL, matching the resource guard's Unix behavior.
"""
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import time


class _BasicLimit(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _kernel():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    api.CreateJobObjectW.restype = wintypes.HANDLE
    api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    api.SetInformationJobObject.restype = wintypes.BOOL
    api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.c_void_p]
    api.QueryInformationJobObject.restype = wintypes.BOOL
    api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    api.AssignProcessToJobObject.restype = wintypes.BOOL
    api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateJobObject.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


class JobProcess:
    def __init__(self, command, log):
        if os.name != "nt":
            raise RuntimeError("Windows job objects require Windows")
        self.api = _kernel()
        self.job = self.api.CreateJobObjectW(None, None)
        self.process = None
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = _ExtendedLimit()
            # No breakaway permission: descendants stay in the job even after
            # their parent exits. Closing our only job handle kills them all.
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not self.api.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            launcher = ("import subprocess,sys; "
                        "gate=sys.stdin.buffer.read(1); "
                        "sys.exit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL) "
                        "if gate == b'G' else 125)")
            self.process = subprocess.Popen(
                [sys.executable, "-c", launcher, *command], stdin=subprocess.PIPE,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            if not self.api.AssignProcessToJobObject(self.job, int(self.process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.process.stdin.write(b"G")
            self.process.stdin.flush()
            self.process.stdin.close()
        except BaseException:
            if self.process is not None:
                self.process.kill()
                self.process.wait()
                try:
                    self.process.stdin.close()
                except OSError:
                    pass
            self.close()
            raise

    @property
    def pid(self):
        return self.process.pid

    @property
    def returncode(self):
        return self.process.returncode

    def poll(self):
        return self.process.poll()

    def wait(self, timeout=None):
        return self.process.wait(timeout=timeout)

    def pids(self):
        """Get current membership from the kernel, independent of parent PIDs."""
        capacity = 64
        while True:
            class ProcessList(ctypes.Structure):
                _fields_ = [("assigned", wintypes.DWORD), ("count", wintypes.DWORD),
                            ("pids", ctypes.c_size_t * capacity)]
            info = ProcessList()
            if self.api.QueryInformationJobObject(self.job, 3, ctypes.byref(info), ctypes.sizeof(info), None):
                return set(info.pids[:info.count])
            error = ctypes.get_last_error()
            if error != 234:  # ERROR_MORE_DATA
                raise ctypes.WinError(error)
            capacity = max(capacity * 2, info.assigned + 16)

    def stop(self):
        try:
            if self.job and not self.api.TerminateJobObject(self.job, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            self.process.wait(timeout=10)
            deadline = time.monotonic() + 10
            while self.pids():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Windows stage job did not empty after termination")
                time.sleep(0.02)
        finally:
            self.close()

    def close(self):
        if self.job:
            job, self.job = self.job, None
            if not self.api.CloseHandle(job):
                raise ctypes.WinError(ctypes.get_last_error())
