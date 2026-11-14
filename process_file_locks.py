"""Read file owners through Windows Restart Manager; never unlock handles or delete files."""

import ctypes
from ctypes import wintypes as w


class UniqueProcess(ctypes.Structure):
    _fields_ = [("pid", w.DWORD), ("started", w.FILETIME)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [("process", UniqueProcess), ("name", w.WCHAR * 256),
                ("service", w.WCHAR * 64), ("kind", ctypes.c_int),
                ("status", w.ULONG), ("session", w.DWORD), ("restartable", w.BOOL)]


def file_owners(paths):
    """Return {pid: creation FILETIME}; identity must be checked before using a PID."""
    paths = list(dict.fromkeys(paths))
    if not paths:
        return {}
    rm = ctypes.WinDLL("rstrtmgr", use_last_error=True)
    rm.RmStartSession.argtypes = [ctypes.POINTER(w.DWORD), w.DWORD, w.LPWSTR]
    rm.RmRegisterResources.argtypes = [w.DWORD, w.UINT, ctypes.POINTER(w.LPCWSTR),
                                      w.UINT, ctypes.c_void_p, w.UINT, ctypes.c_void_p]
    rm.RmGetList.argtypes = [w.DWORD, ctypes.POINTER(w.UINT), ctypes.POINTER(w.UINT),
                           ctypes.POINTER(ProcessInfo), ctypes.POINTER(w.DWORD)]
    rm.RmEndSession.argtypes = [w.DWORD]
    for name in ("RmStartSession", "RmRegisterResources", "RmGetList", "RmEndSession"):
        getattr(rm, name).restype = w.DWORD
    session = w.DWORD()
    key = ctypes.create_unicode_buffer(33)
    error = rm.RmStartSession(ctypes.byref(session), 0, key)
    if error:
        raise ctypes.WinError(error)
    try:
        files = (w.LPCWSTR * len(paths))(*paths)
        error = rm.RmRegisterResources(session, len(paths), files, 0, None, 0, None)
        if error:
            raise ctypes.WinError(error)
        needed, count, reasons = w.UINT(), w.UINT(), w.DWORD()
        entries = None
        for _ in range(5):
            error = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), entries, ctypes.byref(reasons))
            if error == 0:
                return {entry.process.pid: (entry.process.started.dwHighDateTime << 32) | entry.process.started.dwLowDateTime
                        for entry in (entries or [])[:count.value]}
            if error != 234:
                raise ctypes.WinError(error)
            count.value = needed.value
            entries = (ProcessInfo * count.value)()
        raise RuntimeError("文件占用进程持续变化，请重试")
    finally:
        rm.RmEndSession(session)


def process_started(handle):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetProcessTimes.argtypes = [w.HANDLE, *([ctypes.POINTER(w.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = w.BOOL
    created, exited, system, user = (w.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(int(handle), ctypes.byref(created), ctypes.byref(exited),
                                  ctypes.byref(system), ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (created.dwHighDateTime << 32) | created.dwLowDateTime
