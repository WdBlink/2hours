"""Windows handle-based retention deletion; no path-based delete fallback.

Ancestors cannot be renamed while pinned. OPEN_REPARSE_POINT lets us reject
junctions and symlinks without traversing them. The media handle denies writers
and renames, and FileDispositionInfo deletes that exact handle on close.
"""
from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import msvcrt
import os
from pathlib import Path
import stat

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD]
kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
kernel32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                              ctypes.c_void_p, wintypes.DWORD]
kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL

INVALID_HANDLE = ctypes.c_void_p(-1).value
READ_ATTRIBUTES = 0x80
DELETE = 0x10000
SHARE_READ = 1
SHARE_WRITE = 2
OPEN_EXISTING = 3
OPEN_ALWAYS = 4
BACKUP_SEMANTICS = 0x02000000
OPEN_REPARSE_POINT = 0x00200000
REPARSE_POINT = 0x400
DIRECTORY = 0x10


class AttributeTagInfo(ctypes.Structure):
    _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]


class BasicInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_longlong) for name in
                ("created", "accessed", "written", "changed")] + [("attributes", wintypes.DWORD)]


class UnicodeString(ctypes.Structure):
    _fields_ = [("length", wintypes.USHORT), ("maximum", wintypes.USHORT), ("buffer", wintypes.LPWSTR)]


class ObjectAttributes(ctypes.Structure):
    _fields_ = [("length", wintypes.ULONG), ("root", wintypes.HANDLE),
                ("name", ctypes.POINTER(UnicodeString)), ("attributes", wintypes.ULONG),
                ("security", ctypes.c_void_p), ("quality", ctypes.c_void_p)]


class IOStatusBlock(ctypes.Structure):
    _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]


ntdll = ctypes.WinDLL("ntdll")
ntdll.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                             ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IOStatusBlock),
                             ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
                             wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
ntdll.NtCreateFile.restype = wintypes.LONG
ntdll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG


def open_relative(path, parent, access, sharing, directory, create):
    """NtCreateFile provides Windows' equivalent of openat(dir_fd, NOFOLLOW).

RootDirectory binds each lookup to the already-open parent; OBJ_DONT_REPARSE
forbids reparsing even if another process changes directory attributes in place.
"""
    name = Path(path).name
    if not name or name in (".", "..") or any(c in name for c in "\\/:"):
        raise ValueError("Expected a single local path component")
    buffer = ctypes.create_unicode_buffer(name)
    length = len(name.encode("utf-16-le"))
    string = UnicodeString(length, length + 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), parent, ctypes.pointer(string),
                                  0x40 | 0x1000, None, None)  # CASE_INSENSITIVE | DONT_REPARSE
    status = IOStatusBlock()
    handle = wintypes.HANDLE()
    result = ntdll.NtCreateFile(ctypes.byref(handle), access | 0x100000,
                                ctypes.byref(attributes), ctypes.byref(status), None, 0x80,
                                sharing, 3 if create else 1,  # FILE_OPEN_IF / FILE_OPEN
                                OPEN_REPARSE_POINT | 0x20 | (1 if directory else 0x40),
                                None, 0)  # SYNCHRONOUS_IO_NONALERT and exact file type
    if result < 0:
        raise ctypes.WinError(ntdll.RtlNtStatusToDosError(result))
    return handle.value


class HandleStat:
    def __init__(self, fd):
        self.value = os.fstat(fd)
        basic = BasicInfo()
        if not kernel32.GetFileInformationByHandleEx(msvcrt.get_osfhandle(fd), 0,
                                                    ctypes.byref(basic), ctypes.sizeof(basic)):
            raise ctypes.WinError(ctypes.get_last_error())
        # Python's Windows st_ctime is historically creation time. Retention
        # needs NTFS ChangeTime to detect same-size writes with restored mtime.
        self.st_ctime_ns = (basic.changed - 116444736000000000) * 100

    def __getattr__(self, name):
        return getattr(self.value, name)


def checked_handle(path, *, directory, preview=False, lock=False, relative_to=None):
    access = READ_ATTRIBUTES | 0x20 if directory else READ_ATTRIBUTES if preview else 0x80000000 | DELETE
    sharing = SHARE_READ | SHARE_WRITE if directory else SHARE_READ
    if preview:
        sharing |= SHARE_WRITE | 4
    if lock:
        access = 0x80000000 | 0x40000000
        sharing = SHARE_READ | SHARE_WRITE
    if relative_to is not None:
        handle = open_relative(path, relative_to, access, sharing, directory, lock)
    else:
        filename = os.path.abspath(path)
        if not filename.startswith("\\\\"):
            filename = "\\\\?\\" + filename  # Support long local paths without a registry change.
        handle = kernel32.CreateFileW(filename, access, sharing, None, OPEN_ALWAYS if lock else OPEN_EXISTING,
                                      BACKUP_SEMANTICS | OPEN_REPARSE_POINT, None)
        if handle == INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = AttributeTagInfo()
        if not kernel32.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & REPARSE_POINT or bool(info.attributes & DIRECTORY) != directory:
            raise ValueError("Reparse point or unexpected file type; media preserved")
        return handle
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


@contextmanager
def pinned_parents(path):
    """Pin from the volume root down, so no ancestor can be redirected mid-open."""
    path = Path(os.path.abspath(path))
    if not path.drive or str(path).startswith("\\\\"):
        raise ValueError("Retention cleanup requires a local Windows volume")
    with ExitStack() as stack:
        handle = None
        for parent in reversed(path.parents):
            handle = checked_handle(parent, directory=True, relative_to=handle)
            stack.callback(kernel32.CloseHandle, handle)
        yield handle


class MediaHandle:
    def __init__(self, handle):
        self.fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)

    def stat(self):
        info = HandleStat(self.fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Hard link or non-regular media; preserved")
        return info

    def delete(self):
        # FILE_DISPOSITION_INFO contains a one-byte BOOLEAN, not a Win32 BOOL.
        disposition = ctypes.c_ubyte(1)
        handle = msvcrt.get_osfhandle(self.fd)
        if not kernel32.SetFileInformationByHandle(handle, 4, ctypes.byref(disposition), 1):
            raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def media_handle(path, *, parent):
    handle = checked_handle(path, directory=False, relative_to=parent)
    try:
        media = MediaHandle(handle)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise
    try:
        yield media
    finally:
        os.close(media.fd)


@contextmanager
def secure_lock_file(path):
    """Open a regular single-link lock without following any path redirection."""
    with pinned_parents(path) as parent:
        handle = checked_handle(path, directory=False, lock=True, relative_to=parent)
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except BaseException:
            kernel32.CloseHandle(handle)
            raise
        with os.fdopen(fd, "r+b") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Lock must be a regular file with one link")
            yield stream


def file_stat(path):
    """Use handle metadata in previews too (Win32 path-stat metadata can be cached)."""
    handle = checked_handle(path, directory=False, preview=True)
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise
    try:
        return HandleStat(fd)
    finally:
        os.close(fd)


def process_alive(pid):
    """Query only; os.kill(pid, 0) is not a portable Windows liveness probe."""
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: PID does not exist.
            return False
        raise ctypes.WinError(error)
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
