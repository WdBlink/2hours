"""Shared helpers for blurt scripts (stdlib only)."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")

from _identity import PROJECT_FOLDER, RECORDER_HOME

BLURT_HOME = RECORDER_HOME
MODELS_DIR = BLURT_HOME / "models"
GLOBAL_CONFIG = BLURT_HOME / "config.json"
STATE_FILE = BLURT_HOME / "recording.json"


class CommandLog:
    """Consume complete commands without unlinking a file that writers may still hold open."""
    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.pending = b""

    def read(self) -> list[str]:
        try:
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                data = stream.read()
        except FileNotFoundError:
            return []
        self.offset += len(data)
        lines = (self.pending + data).split(b"\n")
        self.pending = lines.pop()
        return [line.decode("utf-8").strip() for line in lines if line.strip()]


def append_command(path: Path, command: str) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(command + "\n")


def ensure_utf8_stdio() -> None:
    # Windows consoles default to a legacy code page; keep CJK output intact.
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


ensure_utf8_stdio()


def use_system_certs() -> None:
    """Verify TLS through the OS (truststore) when available. A fresh Windows fetches many root CAs on demand
    (e.g. the one behind huggingface.co); Python's own store lookup never triggers that and fails verification."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def find_ffmpeg(name: str = "ffmpeg") -> str | None:
    """Find an installed tool, including winget before the parent app refreshes PATH."""
    path = shutil.which(name)
    if not path and IS_WIN and name in ("ffmpeg", "ffprobe"):
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        candidates = [local / "Microsoft/WinGet/Links" / f"{name}.exe"]
        candidates += sorted((local / "Microsoft/WinGet/Packages").glob(
            f"Gyan.FFmpeg*/*/bin/{name}.exe"), reverse=True)
        path = next((str(candidate) for candidate in candidates if candidate.is_file()), None)
    return path


def ffmpeg_bin(name: str = "ffmpeg") -> str:
    path = find_ffmpeg(name)
    if not path:
        hint = "brew install ffmpeg" if IS_MAC else "winget install Gyan.FFmpeg" if IS_WIN else "sudo apt install ffmpeg"
        die(f"{name} not found on PATH. Install it first: {hint}")
    return path


def has_link_component(path: Path) -> bool:
    """Reject symlinks and Windows reparse points, including directory junctions."""
    for part in (path, *path.parents):
        try:
            value = part.lstat()
        except FileNotFoundError:
            continue
        if part.is_symlink() or getattr(value, "st_file_attributes", 0) & 0x400:
            return True
    return False


def run(cmd: list[str], check: bool = True, capture: bool = True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, check=check, capture_output=capture, text=True, encoding="utf-8", errors="replace", **kw
    )


def probe_duration(video: Path) -> float:
    out = run([ffmpeg_bin("ffprobe"), "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(video)]).stdout.strip()
    return float(out or 0)


def probe_size(video: Path) -> tuple[int, int]:
    out = run([ffmpeg_bin("ffprobe"), "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video)]).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def load_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))  # utf-8-sig: PowerShell/Notepad write a BOM
    except FileNotFoundError:
        return default


# Config safe-write below is ported from AGIHunt/blurt (MIT):
# - 21e050d4: per-process temp file and bounded os.replace retry on PermissionError (save_json)
# - 452d465a: an unreadable config reads as defaults, is backed up once (same bytes not twice, 0600) with one
#   config_unreadable line per process (read_global_config), and update_global_config refuses to write over it
# - 88db1f74: the ConfigUnreadable exception for that refusal, and save_config_if_readable
# Added here: save_json removes its temp file when it gives up; the backup is created 0600 (no chmod afterwards).
# Not ported: eb2bf801's .config.lock, 0600 config/temp files and its raw JSONDecodeError on an unreadable config;
# 452d465a's trailing-comma tolerant parser; 88db1f74's NaN/Infinity refusal.
REPLACE_ATTEMPTS = 20
REPLACE_DELAY = 0.05


def save_json(path: Path, data) -> None:
    """Atomic: write a temp file of this process's own, then os.replace. On Windows the replace fails with
    PermissionError while another process has the target open, so it is retried a bounded number of times; when it
    still fails the temp file is removed and the error is raised."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(REPLACE_DELAY)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


class ConfigUnreadable(Exception):
    """config.json can't be read, so a setting can't be saved; the file is left as it is."""

    code = "config_unreadable"

    def __init__(self, path: Path):
        super().__init__(f"{path} can't be read; nothing was saved. Fix or remove it.")
        self.path = path


_CONFIG_WARNED: set = set()


def _backup_unreadable(path: Path) -> Path | None:
    """Copy an unreadable config to config.json.bad-<time>, unless a backup with the same bytes already exists."""
    try:
        data = path.read_bytes()
        for old in sorted(path.parent.glob(path.name + ".bad-*")):
            if old.read_bytes() == data:
                return old
        stem = f"{path.name}.bad-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        for name in (stem, f"{stem}-{os.getpid()}", f"{stem}-{os.getpid()}-{uuid.uuid4().hex[:8]}"):
            bak = path.with_name(name)
            try:
                # 0600 from creation: the config may hold secrets (feishu app_secret). O_EXCL never reuses a file.
                fd = os.open(bak, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            except FileExistsError:
                continue
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
            except BaseException:
                bak.unlink(missing_ok=True)
                raise
            return bak
        return None
    except OSError:
        return None


def read_global_config() -> tuple[dict, bool]:
    """(settings, readable). A missing file is readable ({}). One that doesn't parse, is empty, or isn't a JSON
    object gives ({}, False): it is backed up once and one `config_unreadable` line goes to stderr, once per
    process, so recording carries on with the defaults."""
    path = Path(GLOBAL_CONFIG)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}, True
    except (OSError, ValueError):  # ValueError covers JSONDecodeError and UnicodeDecodeError
        cfg = None
    if isinstance(cfg, dict):
        return cfg, True
    if str(path) not in _CONFIG_WARNED:
        _CONFIG_WARNED.add(str(path))
        bak = _backup_unreadable(path)
        print(json.dumps({"event": "config_unreadable", "path": str(path), "backup": str(bak) if bak else None}),
              file=sys.stderr)
    return {}, False


def global_config() -> dict:
    return read_global_config()[0]


def update_global_config(patch: dict) -> dict:
    """Read-modify-write. An unreadable config.json is never overwritten: ConfigUnreadable is raised instead."""
    cfg, ok = read_global_config()
    if not ok:
        raise ConfigUnreadable(GLOBAL_CONFIG)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    save_json(GLOBAL_CONFIG, cfg)
    return cfg


def save_config_if_readable(patch: dict) -> bool:
    """update_global_config for convenience values (skill_dir, last_region): an unreadable config only skips the
    write. False: not saved."""
    try:
        update_global_config(patch)
        return True
    except ConfigUnreadable:
        return False


def new_session_dir(root: Path | None = None) -> Path:
    root = Path(root or Path.cwd())
    d = root / PROJECT_FOLDER / "sessions" / datetime.now().strftime("%Y%m%d-%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


def fmt_ts(sec: float) -> str:
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:04.1f}" if h else f"{int(m):02d}:{s:04.1f}"


def system_info() -> dict:
    info = {
        "os": platform.system(),
        "os_version": platform.mac_ver()[0] if IS_MAC else platform.version(),
        "arch": platform.machine().lower(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
    }
    info["apple_silicon"] = IS_MAC and info["arch"] in ("arm64", "aarch64")
    info["ram_gb"] = _ram_gb()
    info["nvidia_gpu"] = _nvidia_gpu()
    return info


def _ram_gb() -> float | None:
    try:
        if IS_MAC:
            return round(int(run(["sysctl", "-n", "hw.memsize"]).stdout) / 2**30, 1)
        if IS_WIN:
            import ctypes

            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMSTAT()
            st.dwLength = ctypes.sizeof(MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return round(st.ullTotalPhys / 2**30, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except Exception:
        return None


def _nvidia_gpu() -> str | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"]).stdout.strip()
        return out.splitlines()[0] if out else None
    except Exception:
        return None


def fix_tcl_env() -> None:
    """uv-managed Pythons can't find Tcl/Tk inside venvs; point TCL_LIBRARY/TK_LIBRARY at the base install."""
    if os.environ.get("TCL_LIBRARY"):
        return
    base = Path(sys.base_prefix)
    for init in list(base.glob("lib/tcl*/init.tcl")) + list(base.glob("tcl/tcl*/init.tcl")):
        os.environ["TCL_LIBRARY"] = str(init.parent)
        ver = init.parent.name.replace("tcl", "")
        for tkdir in (init.parent.parent / f"tk{ver}", init.parent.parent / f"tk{ver.split('.')[0]}"):
            if tkdir.exists():
                os.environ["TK_LIBRARY"] = str(tkdir)
        return


# ------------------------------------------------------------------ session output: items.json
def load_session(session: Path) -> dict | None:
    return load_json(Path(session) / "items.json")


def save_session(session: Path, data: dict) -> None:
    save_json(Path(session) / "items.json", data)


def live_items(data: dict, kind: str | None = None) -> list[dict]:
    return [i for i in data.get("items", []) if i.get("status") != "deleted" and (kind is None or i.get("kind") == kind)]
