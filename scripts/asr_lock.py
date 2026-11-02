"""One 2hours transcription at a time on this machine.

Follows AsrSlot from AGIHunt/blurt e72469dc (MIT, Copyright (c) 2026 blurt contributors): a lock file that the OS
releases when its holder dies, a waiting run says it is queued on stderr, and a wait that runs out
exits 3. Differences: 2hours keeps its own lock (~/.2hours/recorder/run/asr.lock, never Blurt's),
allows one run (no max_concurrent), waits 30 minutes by default (TWOHOURS_ASR_LOCK_TIMEOUT), opens
the file on Windows the way dispatch.py does (no junctions or hard links), and lets resource_guard
hold the lock for the transcribe.py it starts, so the guard's wait is not part of the stage limit
and the child does not wait for its own parent.
"""

import errno
import importlib.util
import math
import os
import secrets
import stat
import sys
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

if os.name == "nt":
    import msvcrt

    _spec = importlib.util.spec_from_file_location("asr_lock_windows_files", Path(__file__).with_name("windows_files.py"))
    windows_files = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(windows_files)
else:
    import fcntl

TIMEOUT_ENV = "TWOHOURS_ASR_LOCK_TIMEOUT"
# Set by resource_guard for its child: "fd:<n>" (POSIX, an inherited locked descriptor)
# or "token:<hex>" (Windows, the token the guard wrote into the lock file).
HANDOFF_ENV = "TWOHOURS_ASR_LOCK"
DEFAULT_TIMEOUT = 30 * 60.0
TIMEOUT_EXIT = 3
POLL_SECONDS = 0.25
TOKEN_OFFSET = 16  # Windows locks byte 0; the holder's token sits after it.
TOKEN_BYTES = 32
BUSY = (13, 36) if os.name == "nt" else (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK)


class LockError(Exception):
    exit_code = 1


class LockTimeout(LockError):
    exit_code = TIMEOUT_EXIT


@dataclass
class Held:
    waited: float = 0.0
    env: dict = field(default_factory=dict)
    pass_fds: tuple = ()


def lock_path():
    return Path.home() / ".2hours" / "recorder" / "run" / "asr.lock"


def wait_limit():
    value = os.environ.get(TIMEOUT_ENV)
    if value is None:
        return DEFAULT_TIMEOUT
    try:
        seconds = float(value)
    except ValueError:
        raise LockError(f"{TIMEOUT_ENV} must be a number of seconds") from None
    if not (math.isfinite(seconds) and seconds > 0):
        raise LockError(f"{TIMEOUT_ENV} must be positive")
    return seconds


def is_transcription(command):
    """`... transcribe.py run ...`, the command resource_guard holds the lock for."""
    names = [Path(arg).name for arg in command]
    return any(name == "transcribe.py" and names[i + 1:i + 2] == ["run"] for i, name in enumerate(names))


def _is_link(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


@contextmanager
def _open(path):
    if os.name == "nt":
        if any(_is_link(part) for part in (path, *path.parents)):
            raise LockError("ASR lock path must not contain links or junctions")
        path.parent.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            try:
                stream = stack.enter_context(windows_files.secure_lock_file(path))
            except ValueError as error:
                raise LockError(f"unsafe ASR lock file {path}: {error}") from None
            yield stream
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as stream:
            yield stream


def _try_lock(stream):
    try:
        if os.name == "nt":
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            # flock, not upstream's lockf: the lock belongs to the open file, so a child
            # that inherits the descriptor shares it (see _inherited).
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as error:
        if error.errno in BUSY:
            return False
        raise


def _marker(kind):
    found, _, value = os.environ.get(HANDOFF_ENV, "").partition(":")
    return value if found == kind and value else None


def _inherited(path):
    """POSIX: our parent passed us the descriptor it locked. The same open file takes the lock again
    without blocking, and the descriptor must be the lock file itself."""
    if os.name == "nt" or not (_marker("fd") or "").isdigit():
        return False
    fd = int(_marker("fd"))
    try:
        if not os.path.samestat(os.fstat(fd), os.stat(path)):
            return False
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _token_matches(stream):
    """Windows: the process holding byte 0 wrote the token our parent gave us."""
    token = _marker("token")
    if os.name != "nt" or not token:
        return False
    stream.seek(TOKEN_OFFSET)
    return stream.read(TOKEN_BYTES) == token.encode("ascii", "replace")


@contextmanager
def slot(*, handoff=False, out=None):
    """Hold the 2hours ASR lock for the body. With handoff=True (resource_guard) the result carries the
    env and descriptors that let the child it starts run under this lock."""
    out = out or sys.stderr
    path = lock_path()
    if not handoff and _inherited(path):
        yield Held()
        return
    timeout = wait_limit()
    with _open(path) as stream:
        start = time.monotonic()
        queued = False
        owned = True
        while not _try_lock(stream):
            if not handoff and _token_matches(stream):
                owned = False
                break
            if not queued:
                queued = True
                print(f"asr: queued (another transcription is running; waiting up to {timeout:g}s) "
                      "有别的转写在跑，排队中", file=out, flush=True)
            if time.monotonic() - start >= timeout:
                raise LockTimeout(f"asr: waited {timeout:g}s for the transcription lock ({path}); another "
                                  "transcription is still running. Retry later. 有别的转写还没结束，请稍后重试")
            time.sleep(POLL_SECONDS)
        held = Held(waited=time.monotonic() - start)
        if queued and owned:
            print(f"asr: slot free, starting (waited {held.waited:.1f}s)", file=out, flush=True)
        if owned and handoff:
            if os.name == "nt":
                token = secrets.token_hex(TOKEN_BYTES // 2)
                stream.seek(TOKEN_OFFSET)
                stream.write(token.encode("ascii"))
                stream.flush()
                held.env = {HANDOFF_ENV: f"token:{token}"}
            else:
                held.env = {HANDOFF_ENV: f"fd:{stream.fileno()}"}
                held.pass_fds = (stream.fileno(),)
        try:
            yield held
        finally:
            if owned and os.name == "nt":
                if handoff:
                    stream.seek(TOKEN_OFFSET)
                    stream.write(b"\0" * TOKEN_BYTES)
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            # POSIX: closing our descriptor drops the lock unless a child still has the same open
            # file (a guard killed before its stage); then the child keeps it until it ends.
