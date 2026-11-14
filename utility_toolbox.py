import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
import winreg
import ctypes
from ctypes import wintypes
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:
    DND_FILES = None
    TkinterDnD = None

import win32api
import win32con
import win32event
import win32gui
import win32process

from subtitle_sync_embedded import MainApp as SubtitleSyncApp
from game_save_manager_launcher import GameSaveManagerTab
from toolbox_paths import data_directory, macro_recording_path
from toolbox_ui import THEME, DarkScrolledText, ToolboxShell, setup_theme

try:
    from media_organizer.models import AppConfig, ScanManifest
    from media_organizer.organizer import execute_plan, save_manifest
    from media_organizer.scanner import scan_media
    from media_organizer.utils import write_json
except Exception:
    AppConfig = None
    ScanManifest = None
    execute_plan = None
    save_manifest = None
    scan_media = None
    write_json = None


APP_NAME = "Windows 实用工具箱"
APP_DIR = data_directory("UtilityToolbox")
CONFIG_FILE = APP_DIR / "config.json"
LOG_FILE = APP_DIR / "toolbox.log"
AUTOSTART_ARG = "--minimized-to-tray"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "UtilityToolbox"
APP_ICON_FILE = "utility_toolbox.ico"
LAUNCHER_CLOSE_EXTENSIONS = {".exe", ".bat", ".cmd", ".py", ".ps1", ".lnk"}
PROCESS_EXECUTABLE_EXTENSIONS = {".exe"}
PROCESS_SCRIPT_EXTENSIONS = {".bat", ".cmd", ".py", ".ps1"}
DIRECTORY_SCOPE_KEYWORDS = ("launcher", "runner", "启动器")
PROCESS_CLOSE_TIMEOUT_SECONDS = 10
PROCESS_EXIT_WAIT_SECONDS = 2
PROCESS_CLOSE_OBSERVATION_SECONDS = 2.0
PROCESS_CLOSE_OBSERVATION_INTERVAL_SECONDS = 0.2
SCRIPT_HOST_NAMES = {"cmd.exe", "powershell.exe", "pwsh.exe", "python.exe", "pythonw.exe", "py.exe", "wscript.exe", "cscript.exe"}
LAUNCHER_REPAIR_IGNORE_DIRS = {
    "$recycle.bin",
    "$windows.~bt",
    "$windows.~ws",
    ".git",
    ".pytest_cache",
    "__pycache__",
    "appdata",
    "application data",
    "cache",
    "cookies",
    "node_modules",
    "program files",
    "program files (x86)",
    "programdata",
    "recovery",
    "system volume information",
    "windows",
}
LAUNCHER_REPAIR_MAX_SECONDS = 25


def configure_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        from ctypes import windll

        try:
            windll.user32.SetProcessDpiAwarenessContext(-4)
        except Exception:
            windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def normalize_process_path(path: str) -> str:
    if not path:
        return ""
    path = path.strip().strip('"')
    try:
        return str(Path(path).expanduser().resolve()).casefold()
    except Exception:
        return str(Path(path).expanduser().absolute()).casefold()


def current_executable_path() -> str:
    return sys.executable


def parent_process_id(pid: Optional[int] = None) -> Optional[int]:
    if os.name != "nt":
        return None
    target_pid = os.getpid() if pid is None else pid
    snapshot = None
    try:
        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return None
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        while True:
            if int(entry.th32ProcessID) == int(target_pid):
                return int(entry.th32ParentProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    except Exception:
        return None
    finally:
        if snapshot:
            try:
                ctypes.windll.kernel32.CloseHandle(snapshot)
            except Exception:
                pass
    return None


def current_process_protected_pids() -> Set[int]:
    protected = {os.getpid()}
    parent_pid = parent_process_id()
    while parent_pid and parent_pid not in protected and len(protected) < 128:
        protected.add(parent_pid)
        parent_pid = parent_process_id(parent_pid)
    return protected


def is_current_process(pid: int, process_path: str, protected_pids: Optional[Set[int]] = None) -> bool:
    if pid in (current_process_protected_pids() if protected_pids is None else protected_pids):
        return True
    current_path = normalize_process_path(current_executable_path())
    process_normalized = normalize_process_path(process_path)
    if current_path and process_normalized == current_path:
        return True
    current_name = Path(current_executable_path()).name.casefold()
    process_name = Path(process_path).name.casefold()
    return bool(current_name and process_name == current_name)


def is_process_in_target_scope(process_path: str, target_path: str) -> bool:
    if Path(process_path).suffix.lower() not in PROCESS_EXECUTABLE_EXTENSIONS:
        return False
    if Path(target_path).suffix.lower() not in LAUNCHER_CLOSE_EXTENSIONS:
        return False
    normalized_process = normalize_process_path(process_path)
    normalized_target = normalize_process_path(target_path)
    if not normalized_process or not normalized_target:
        return False
    if normalized_process == normalized_target:
        return True
    target_stem = Path(target_path).stem.casefold()
    if not any(keyword in target_stem for keyword in DIRECTORY_SCOPE_KEYWORDS):
        return False
    try:
        target_dir = normalize_process_path(str(Path(target_path).expanduser().resolve().parent))
    except Exception:
        target_dir = normalize_process_path(str(Path(target_path).expanduser().absolute().parent))
    return normalized_process.startswith(target_dir.rstrip("\\/") + os.sep.casefold())


def resolve_shortcut_target(path: str) -> str:
    if Path(path).suffix.lower() != ".lnk" or os.name != "nt":
        return path
    import pythoncom

    initialized = False
    shell = shortcut = None
    try:
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialized = True
        except pythoncom.com_error as exc:
            # An existing MTA is already initialized and must not be uninitialized here.
            if exc.hresult != -2147417850:  # RPC_E_CHANGED_MODE
                raise
        shell = win32com_client_dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(path)
        target = getattr(shortcut, "TargetPath", "")
        if not target:
            raise RuntimeError("快捷方式没有可用目标")
        if Path(target).name.casefold() in SCRIPT_HOST_NAMES and getattr(shortcut, "Arguments", "").strip():
            raise RuntimeError("暂不支持关闭带参数的脚本宿主快捷方式，以免关闭其他终端或脚本")
        return target
    except Exception as exc:
        raise RuntimeError(f"无法解析快捷方式 {Path(path).name}：{exc}") from exc
    finally:
        shortcut = shell = None
        if initialized:
            pythoncom.CoUninitialize()


def win32com_client_dispatch(prog_id: str):
    import win32com.client

    return win32com.client.Dispatch(prog_id)


def path_looks_like_directory(path: str) -> bool:
    candidate = Path(path)
    try:
        if candidate.exists():
            return candidate.is_dir()
    except OSError:
        pass
    return candidate.suffix == ""


def resolve_process_close_target(path: str) -> str:
    target = resolve_shortcut_target(path)
    if not path_looks_like_directory(target) and Path(target).suffix.lower() in PROCESS_SCRIPT_EXTENSIONS:
        raise RuntimeError(f"暂不支持按脚本路径关闭进程：{Path(target).name}；请使用应用 EXE 或应用专用文件夹")
    return target


def resolve_process_close_targets(paths: Iterable[str]) -> Tuple[List[str], List[str]]:
    targets: List[str] = []
    errors: List[str] = []
    for path in paths:
        if not path:
            continue
        try:
            targets.append(resolve_process_close_target(str(path)))
        except Exception as exc:
            errors.append(f"{Path(str(path)).name}：{exc}")
    return targets, errors


def is_process_in_directory(process_path: str, directory_path: str) -> bool:
    if Path(process_path).suffix.lower() not in PROCESS_EXECUTABLE_EXTENSIONS:
        return False
    process = normalize_process_path(process_path)
    directory = normalize_process_path(directory_path).rstrip("\\/")
    if not process or not directory:
        return False
    return process.startswith(directory + os.sep.casefold())


def match_processes_for_drop_paths(paths: Iterable[str], processes: Iterable[Tuple[int, str]]) -> List[Tuple[int, str]]:
    processes = list(processes)
    file_targets: List[str] = []
    directory_targets: List[str] = []
    for path in paths:
        if not path:
            continue
        resolved = resolve_process_close_target(str(path))
        if path_looks_like_directory(resolved):
            directory_targets.append(resolved)
        else:
            file_targets.append(resolved)

    protected = current_process_protected_pids()
    matches = match_processes_for_paths(file_targets, processes, include_directory_scope=False, protected_pids=protected)
    if not directory_targets:
        return matches
    seen_pids = {pid for pid, _process_path in matches}
    own_path = normalize_process_path(app_executable())
    for pid, process_path in processes:
        if pid in seen_pids:
            continue
        normalized = normalize_process_path(process_path)
        if not normalized or normalized == own_path or is_current_process(pid, process_path, protected):
            continue
        if any(is_process_in_directory(process_path, directory) for directory in directory_targets):
            matches.append((pid, process_path))
            seen_pids.add(pid)
    return matches


def match_processes_for_paths(
    paths: Iterable[str], processes: Iterable[Tuple[int, str]], include_directory_scope: bool = True,
    protected_pids: Optional[Set[int]] = None,
) -> List[Tuple[int, str]]:
    targets = [path for path in paths if path]
    if not targets:
        return []
    protected = current_process_protected_pids() if protected_pids is None else protected_pids
    matches: List[Tuple[int, str]] = []
    seen_pids: Set[int] = set()
    for pid, process_path in processes:
        if pid in seen_pids:
            continue
        if normalize_process_path(process_path) == normalize_process_path(app_executable()) or is_current_process(pid, process_path, protected):
            continue
        if any(
            is_process_in_target_scope(process_path, target)
            if include_directory_scope else normalize_process_path(process_path) == normalize_process_path(target)
            for target in targets
        ):
            matches.append((pid, process_path))
            seen_pids.add(pid)
    return matches


def is_shared_process_directory(path: str) -> bool:
    directory = Path(path)
    if directory.anchor and normalize_process_path(str(directory)) == normalize_process_path(directory.anchor):
        return True
    user_directory = Path.home()
    shared = {user_directory, user_directory.parent}
    shared.update(user_directory / name for name in ("Desktop", "Documents", "Downloads"))
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData", "PUBLIC", "SystemRoot"):
        value = os.environ.get(variable)
        if value:
            shared.add(Path(value))
    windows = os.environ.get("SystemRoot")
    if windows:
        shared.update(Path(windows) / name for name in ("System32", "SysWOW64"))
    public = os.environ.get("PUBLIC")
    if public:
        shared.add(Path(public) / "Desktop")
    normalized = normalize_process_path(path)
    return any(normalized == normalize_process_path(str(candidate)) for candidate in shared)


def launcher_close_paths(apps: Iterable[Dict[str, str]]) -> List[str]:
    paths: List[str] = []
    for app in apps:
        original = app.get("path", "")
        if not original:
            continue
        try:
            target = resolve_shortcut_target(original)
        except Exception:
            # Keep this target for per-item error reporting in the close worker.
            paths.append(original)
            continue
        paths.append(target)
        if not any(keyword in Path(target).stem.casefold() for keyword in DIRECTORY_SCOPE_KEYWORDS):
            continue
        learned = app.get("close_paths", [])
        if isinstance(learned, list):
            parent = str(Path(target).parent)
            if is_shared_process_directory(parent):
                continue
            paths.extend(
                path for path in learned
                if isinstance(path, str) and is_process_in_directory(path, parent)
            )
    return paths


def discover_new_process_paths(before: Iterable[Tuple[int, str]], after: Iterable[Tuple[int, str]]) -> List[str]:
    before_pids = {pid for pid, _path in before}
    discovered: List[str] = []
    seen_paths: Set[str] = set()
    own_path = normalize_process_path(app_executable())
    for pid, process_path in after:
        normalized = normalize_process_path(process_path)
        if pid in before_pids or not normalized or normalized == own_path or is_current_process(pid, process_path) or normalized in seen_paths:
            continue
        if Path(process_path).suffix.lower() in PROCESS_EXECUTABLE_EXTENSIONS:
            discovered.append(process_path)
            seen_paths.add(normalized)
    return discovered


class ProcessSnapshot(list):
    """Compatible process list that preserves incomplete-enumeration evidence."""

    def __init__(self):
        super().__init__()
        self.unreadable: Dict[int, str] = {}


def process_snapshot_notice(processes: Iterable[Tuple[int, str]]) -> str:
    unreadable = getattr(processes, "unreadable", {})
    if unreadable:
        return f"；另有 {len(unreadable)} 个进程无法查询（权限或进程状态变化），扫描结果不完整"
    return ""


def start_process_close_worker(controller, work: Callable[[], None]) -> None:
    if getattr(controller, "_close_stopped", False):
        return
    lock = getattr(controller, "_close_lock", None)
    if lock is None:
        lock = controller._close_lock = threading.Lock()
    if not lock.acquire(blocking=False):
        return

    def run() -> None:
        try:
            if not getattr(controller, "_close_stopped", False):
                work()
        finally:
            lock.release()

    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        lock.release()
        raise


def running_process_paths() -> List[Tuple[int, str]]:
    processes = ProcessSnapshot()
    for pid in win32process.EnumProcesses():
        handle = None
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            path = _process_path_from_handle(handle)
            if path:
                processes.append((pid, path))
        except Exception as exc:
            if pid and getattr(exc, "winerror", None) != 87:
                processes.unreadable[pid] = str(exc)
        finally:
            if handle:
                try:
                    win32api.CloseHandle(handle)
                except Exception:
                    pass
    return processes


def observe_closed_process_targets(
    paths: Iterable[str], include_directories: bool = False, should_stop: Optional[Callable[[], bool]] = None
) -> Tuple[List[Tuple[int, str]], str, bool]:
    """Observe a bounded restart window without sending further close requests."""
    stopped = should_stop or (lambda: False)
    if stopped():
        return [], "", True
    file_targets: Set[str] = set()
    directory_targets: List[str] = []
    for path in paths:
        normalized = normalize_process_path(path)
        if include_directories and path_looks_like_directory(path):
            directory_targets.append(normalized.rstrip("\\/") + os.sep.casefold())
        else:
            file_targets.add(normalized)
    protected = current_process_protected_pids()
    own_path = normalize_process_path(current_executable_path())
    own_name = Path(current_executable_path()).name.casefold()
    incomplete = ProcessSnapshot()
    deadline = time.monotonic() + PROCESS_CLOSE_OBSERVATION_SECONDS
    first_snapshot = True
    while True:
        if stopped():
            return [], process_snapshot_notice(incomplete), True
        if not first_snapshot and time.monotonic() >= deadline:
            return [], process_snapshot_notice(incomplete), False
        first_snapshot = False
        snapshot = running_process_paths()
        incomplete.unreadable.update(getattr(snapshot, "unreadable", {}))
        remaining: List[Tuple[int, str]] = []
        for pid, process_path in snapshot:
            if stopped():
                return [], process_snapshot_notice(incomplete), True
            if pid in protected or Path(process_path).name.casefold() == own_name:
                continue
            normalized = normalize_process_path(process_path)
            if not normalized or normalized == own_path:
                continue
            exact_match = normalized in file_targets
            directory_match = Path(process_path).suffix.lower() in PROCESS_EXECUTABLE_EXTENSIONS and any(
                normalized.startswith(directory) for directory in directory_targets
            )
            if exact_match or directory_match:
                remaining.append((pid, process_path))
        if remaining:
            return remaining, process_snapshot_notice(incomplete), False
        budget = deadline - time.monotonic()
        if budget <= 0:
            return [], process_snapshot_notice(incomplete), False
        time.sleep(min(PROCESS_CLOSE_OBSERVATION_INTERVAL_SECONDS, budget))


def _process_path_from_handle(handle) -> str:
    """Query the image path using only PROCESS_QUERY_LIMITED_INFORMATION rights."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel32.QueryFullProcessImageNameW
    query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    query.restype = wintypes.BOOL
    buffer = ctypes.create_unicode_buffer(32768)
    length = wintypes.DWORD(len(buffer))
    if not query(int(handle), 0, buffer, ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.value


def _open_process_for_close(pid: int, expected_path: Optional[str] = None):
    try:
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION | win32con.SYNCHRONIZE, False, pid)
    except Exception as exc:
        if getattr(exc, "winerror", None) == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
            return None
        raise
    try:
        if expected_path and normalize_process_path(_process_path_from_handle(handle)) != normalize_process_path(expected_path):
            raise RuntimeError("PID 已由其他路径的进程占用，已跳过")
        return handle
    except Exception:
        win32api.CloseHandle(handle)
        raise


def _wait_for_process_exit(handle, timeout_ms: int = 0) -> bool:
    result = win32event.WaitForSingleObject(handle, max(0, timeout_ms))
    if result == win32event.WAIT_OBJECT_0:
        return True
    if result == win32event.WAIT_TIMEOUT:
        return False
    raise RuntimeError(f"无法确认进程退出状态（WaitForSingleObject={result}）")


def is_taskkill_already_closed(message: str) -> bool:
    normalized = message.casefold()
    return "没有找到进程" in normalized or "not found" in normalized


def terminate_process(pid: int) -> None:
    _closed, failures = terminate_processes_fast([pid], include_children=True)
    if failures:
        raise RuntimeError(failures[pid])


def terminate_process_only(pid: int) -> None:
    _closed, failures = terminate_processes_fast([pid], include_children=False)
    if failures:
        raise RuntimeError(failures[pid])


def terminate_processes_fast(
    pids: Iterable[int], include_children: bool = True, expected_paths: Optional[Dict[int, str]] = None
) -> Tuple[Set[int], Dict[int, str]]:
    unique_pids: List[int] = []
    seen: Set[int] = set()
    for pid in pids:
        if pid and pid not in seen:
            seen.add(pid)
            unique_pids.append(pid)
    if not unique_pids:
        return set(), {}

    closed: Set[int] = set()
    failures: Dict[int, str] = {}
    handles = {}
    command_errors: Dict[int, str] = {}

    def run_taskkill(target_pids: Iterable[int], timeout: float):
        args = ["taskkill", "/F"]
        if include_children:
            args.append("/T")
        for target_pid in target_pids:
            args.extend(["/PID", str(target_pid)])
        return subprocess.run(
            args, capture_output=True, text=True, errors="replace", timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    def output_error(completed) -> str:
        detail = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        return detail or f"taskkill failed with exit code {completed.returncode}"

    try:
        for pid in unique_pids:
            try:
                handle = _open_process_for_close(pid, (expected_paths or {}).get(pid))
                if handle is None:
                    closed.add(pid)
                    continue
                handles[pid] = handle
                if _wait_for_process_exit(handle):
                    closed.add(pid)
            except Exception as exc:
                failures[pid] = f"无法安全核对进程：{exc}"

        active = [pid for pid in handles if pid not in closed and pid not in failures]
        if active:
            command_deadline = time.monotonic() + PROCESS_CLOSE_TIMEOUT_SECONDS
            try:
                completed = run_taskkill(active, PROCESS_CLOSE_TIMEOUT_SECONDS)
            except Exception as exc:
                command_errors.update((pid, str(exc)) for pid in active)
            else:
                if completed.returncode != 0:
                    for pid in active:
                        try:
                            if _wait_for_process_exit(handles[pid]):
                                closed.add(pid)
                                continue
                            remaining = command_deadline - time.monotonic()
                            if remaining <= 0:
                                command_errors[pid] = "taskkill timeout：批量关闭重试已超时"
                                continue
                            retry = run_taskkill([pid], remaining)
                            if retry.returncode != 0:
                                command_errors[pid] = output_error(retry)
                        except Exception as exc:
                            command_errors[pid] = str(exc)

            exit_deadline = time.monotonic() + PROCESS_EXIT_WAIT_SECONDS
            for pid in active:
                if pid in closed:
                    continue
                try:
                    remaining_ms = max(0, int((exit_deadline - time.monotonic()) * 1000))
                    if _wait_for_process_exit(handles[pid], remaining_ms):
                        closed.add(pid)
                    else:
                        detail = command_errors.get(pid, "关闭请求已发送，但进程仍在运行")
                        failures[pid] = detail
                except Exception as exc:
                    failures[pid] = f"无法确认进程是否已关闭：{exc}"
    finally:
        # Retained handles identify the original process even if its PID is reused.
        for handle in handles.values():
            win32api.CloseHandle(handle)
    return closed, failures


MIC_TOGGLE_PROCESS_NAMES = {
    "一键麦克风开关.exe",
    "Ò»¼üÂó¿Ë·ç¿ª¹Ø.exe",
}
MIC_TOGGLE_CONFIG_PATH = data_directory("MicToggleHotkey") / "config.json"
MIC_OVERLAY_DEFAULT_SCALE = 0.75
MIC_OVERLAY_MIN_SCALE = 0.60
MIC_OVERLAY_MAX_SCALE = 1.20
MIC_HOTKEY_DEFAULT_ENABLED = True
MIC_HOTKEY_DEFAULT_CODES = [0x11, 0x12, 0x4D]
MIC_TARGET_DEFAULT_LABEL = "跟随 Windows 当前默认输入"
MIC_HOTKEY_KEY_NAMES: Dict[int, str] = {
    0x01: "MouseLeft",
    0x02: "MouseRight",
    0x04: "MouseMiddle",
    0x05: "MouseX1",
    0x06: "MouseX2",
    0x08: "Backspace",
    0x09: "Tab",
    0x0D: "Enter",
    0x10: "Shift",
    0x11: "Ctrl",
    0x12: "Alt",
    0x13: "Pause",
    0x14: "CapsLock",
    0x1B: "Esc",
    0x20: "Space",
    0x21: "PageUp",
    0x22: "PageDown",
    0x23: "End",
    0x24: "Home",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x2D: "Insert",
    0x2E: "Delete",
    0x5B: "Win",
    0x5C: "Win",
}
for code in range(0x30, 0x3A):
    MIC_HOTKEY_KEY_NAMES[code] = chr(code)
for code in range(0x41, 0x5B):
    MIC_HOTKEY_KEY_NAMES[code] = chr(code)
for index in range(1, 13):
    MIC_HOTKEY_KEY_NAMES[0x6F + index] = f"F{index}"
MIC_HOTKEY_SCAN_ORDER = [
    0x11,
    0x12,
    0x10,
    0x5B,
    0x5C,
    *range(0x41, 0x5B),
    *range(0x30, 0x3A),
    *range(0x70, 0x7C),
    0x01,
    0x02,
    0x04,
    0x05,
    0x06,
    0x08,
    0x09,
    0x0D,
    0x1B,
    0x20,
    0x21,
    0x22,
    0x23,
    0x24,
    0x25,
    0x26,
    0x27,
    0x28,
    0x2D,
    0x2E,
]


def normalize_mic_overlay_scale(value: object) -> float:
    try:
        scale = float(value)
    except (TypeError, ValueError):
        scale = MIC_OVERLAY_DEFAULT_SCALE
    return max(MIC_OVERLAY_MIN_SCALE, min(MIC_OVERLAY_MAX_SCALE, scale))


def read_mic_overlay_scale() -> float:
    try:
        saved = json.loads(MIC_TOGGLE_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            return normalize_mic_overlay_scale(saved.get("overlay_scale"))
    except (OSError, ValueError, TypeError):
        pass
    return MIC_OVERLAY_DEFAULT_SCALE


def save_mic_overlay_scale(scale: float) -> float:
    normalized = normalize_mic_overlay_scale(scale)
    saved: Dict = {}
    if MIC_TOGGLE_CONFIG_PATH.exists():
        try:
            loaded = json.loads(MIC_TOGGLE_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"麦克风配置文件无法读取：{exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError("麦克风配置文件格式无效")
        saved.update(loaded)
    saved["overlay_scale"] = normalized
    MIC_TOGGLE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = MIC_TOGGLE_CONFIG_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary_path, MIC_TOGGLE_CONFIG_PATH)
    return normalized


def format_mic_hotkey(codes: Iterable[int]) -> str:
    order = {code: index for index, code in enumerate(MIC_HOTKEY_SCAN_ORDER)}
    unique_codes = dict.fromkeys(codes)
    return "+".join(
        MIC_HOTKEY_KEY_NAMES[code]
        for code in sorted(unique_codes, key=lambda code: order.get(code, 999))
        if code in MIC_HOTKEY_KEY_NAMES
    )


def parse_mic_hotkey(text: str) -> List[int]:
    reverse = {name.lower(): code for code, name in MIC_HOTKEY_KEY_NAMES.items()}
    reverse.update(
        {
            "control": 0x11,
            "esc": 0x1B,
            "escape": 0x1B,
            "leftmouse": 0x01,
            "mouseleft": 0x01,
            "rightmouse": 0x02,
            "mouseright": 0x02,
            "middlemouse": 0x04,
            "mousemiddle": 0x04,
        }
    )
    parts = [part for part in text.replace(" ", "").split("+") if part]
    if not parts:
        raise ValueError("快捷键不能为空")
    codes: List[int] = []
    for part in parts:
        code = reverse.get(part.lower())
        if code is None:
            raise ValueError(f"不支持的按键：{part}")
        if code not in codes:
            codes.append(code)
    return codes


def read_mic_hotkey_settings() -> Tuple[bool, str, List[int]]:
    saved: Dict = {}
    try:
        loaded = json.loads(MIC_TOGGLE_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            saved.update(loaded)
    except (OSError, ValueError, TypeError):
        pass
    enabled = saved.get("hotkey_enabled", MIC_HOTKEY_DEFAULT_ENABLED)
    if not isinstance(enabled, bool):
        enabled = MIC_HOTKEY_DEFAULT_ENABLED
    codes = saved.get("hotkey_codes")
    if not isinstance(codes, list) or not codes or any(code not in MIC_HOTKEY_KEY_NAMES for code in codes):
        try:
            codes = parse_mic_hotkey(str(saved.get("hotkey") or "Ctrl+Alt+M"))
        except ValueError:
            codes = list(MIC_HOTKEY_DEFAULT_CODES)
    canonical = format_mic_hotkey(codes)
    return enabled, canonical, list(codes)


def save_mic_hotkey_settings(enabled: bool, hotkey_text: str) -> Tuple[bool, str, List[int]]:
    codes = parse_mic_hotkey(hotkey_text)
    canonical = format_mic_hotkey(codes)
    saved: Dict = {}
    if MIC_TOGGLE_CONFIG_PATH.exists():
        try:
            loaded = json.loads(MIC_TOGGLE_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"麦克风配置文件无法读取：{exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError("麦克风配置文件格式无效")
        saved.update(loaded)
    saved.update(
        {
            "hotkey_enabled": bool(enabled),
            "hotkey": canonical,
            "hotkey_codes": codes,
        }
    )
    MIC_TOGGLE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = MIC_TOGGLE_CONFIG_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary_path, MIC_TOGGLE_CONFIG_PATH)
    return bool(enabled), canonical, codes


def read_mic_target_settings() -> Tuple[str, str]:
    saved: Dict = {}
    try:
        loaded = json.loads(MIC_TOGGLE_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            saved.update(loaded)
    except (OSError, ValueError, TypeError):
        pass
    endpoint_id = saved.get("target_endpoint_id")
    device_name = saved.get("target_device_name")
    return (
        endpoint_id if isinstance(endpoint_id, str) else "",
        device_name if isinstance(device_name, str) and device_name.strip() else MIC_TARGET_DEFAULT_LABEL,
    )


def save_mic_target_settings(endpoint_id: str, device_name: str) -> Tuple[str, str]:
    normalized_id = endpoint_id.strip()
    normalized_name = device_name.strip() if normalized_id else MIC_TARGET_DEFAULT_LABEL
    if normalized_id and not normalized_name:
        raise ValueError("麦克风名称不能为空")
    saved: Dict = {}
    if MIC_TOGGLE_CONFIG_PATH.exists():
        try:
            loaded = json.loads(MIC_TOGGLE_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"麦克风配置文件无法读取：{exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError("麦克风配置文件格式无效")
        saved.update(loaded)
    saved.update(
        {
            "target_endpoint_id": normalized_id,
            "target_device_name": normalized_name,
        }
    )
    MIC_TOGGLE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = MIC_TOGGLE_CONFIG_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary_path, MIC_TOGGLE_CONFIG_PATH)
    return normalized_id, normalized_name


def running_mic_toggle_processes() -> List[Tuple[int, str, str]]:
    matches: List[Tuple[int, str, str]] = []
    current_pid = os.getpid()
    names = {name.lower() for name in MIC_TOGGLE_PROCESS_NAMES}
    try:
        for pid in win32process.EnumProcesses():
            if not pid or pid == current_pid:
                continue
            handle = None
            try:
                handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                path = win32process.GetModuleFileNameEx(handle, 0)
                name = Path(path).name
                if name.lower() in names:
                    matches.append((pid, name, path))
            except Exception:
                pass
            finally:
                if handle:
                    try:
                        win32api.CloseHandle(handle)
                    except Exception:
                        pass
    except Exception:
        return []
    return matches


def close_mic_toggle_processes() -> Tuple[int, Dict[int, str]]:
    processes = running_mic_toggle_processes()
    if not processes:
        return 0, {}
    pids = [pid for pid, _name, _path in processes]
    closed, failures = terminate_processes_fast(pids)
    return len(closed), failures


def current_scale(root: tk.Tk) -> float:
    try:
        return max(0.9, min(1.8, root.winfo_fpixels("1i") / 96.0))
    except tk.TclError:
        return 1.0


def scaled(value: int, scale: float) -> int:
    return max(1, round(value * scale))


def resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def powershell7_executable() -> Optional[str]:
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PowerShell" / "7" / "pwsh.exe",
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "Microsoft"
        / "WindowsApps"
        / "pwsh.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("pwsh.exe")


def app_icon_path() -> Path:
    return resource_path("icons", APP_ICON_FILE)


def bind_wrap(widget: tk.Widget, label: ttk.Label, margin: int = 28) -> None:
    widget.bind("<Configure>", lambda event: label.configure(wraplength=max(260, event.width - margin)), add="+")


def app_executable() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{Path(__file__).resolve()}"'


def autostart_command() -> str:
    return f"{app_executable()} {AUTOSTART_ARG}"


def autostart_command_matches(saved_command: str, current_command: str) -> bool:
    return os.path.normcase(saved_command.strip()) == os.path.normcase(current_command.strip())


def read_autostart_command() -> Optional[str]:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as handle:
            value, _kind = winreg.QueryValueEx(handle, RUN_VALUE_NAME)
        return str(value).strip() or None
    except FileNotFoundError:
        return None
    except OSError:
        return None


def set_autostart(enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as handle:
        if enabled:
            winreg.SetValueEx(handle, RUN_VALUE_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(handle, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass


def is_autostart_enabled() -> bool:
    saved_command = read_autostart_command()
    return saved_command is not None and autostart_command_matches(saved_command, autostart_command())


def repair_autostart_path() -> bool:
    saved_command = read_autostart_command()
    if saved_command is None or autostart_command_matches(saved_command, autostart_command()):
        return False
    try:
        arguments = win32api.CommandLineToArgv(saved_command)
        if not arguments:
            return False
        previous_executable = Path(os.path.expandvars(arguments[0]))
        if not previous_executable.is_absolute():
            return False
        previous_target = previous_executable
        if previous_executable.name.casefold() in {"python.exe", "pythonw.exe"}:
            # Accept only the source command emitted by app_executable/autostart_command.
            if len(arguments) != 3 or arguments[2] != AUTOSTART_ARG:
                return False
            previous_script = Path(arguments[1])
            if not previous_script.is_absolute() or previous_script.name.casefold() != "utility_toolbox.py":
                return False
            previous_target = previous_script
        previous_target.stat()
    except FileNotFoundError:
        pass  # Only an absent old application target is eligible for automatic relocation.
    except (OSError, ValueError):
        return False
    else:
        return False  # Another valid copy still owns this explicitly configured startup entry.
    set_autostart(True)
    return True


def ensure_app_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)


def log_line(message: str) -> None:
    ensure_app_dir()
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")


def load_config() -> Dict:
    ensure_app_dir()
    if not CONFIG_FILE.exists():
        return {}
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(config: Dict) -> None:
    ensure_app_dir()
    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise ValueError("原配置文件无法解析，已保留原文件；请备份并修复后重试。") from exc
        if not isinstance(existing, dict):
            raise ValueError("原配置文件必须是 JSON 对象，已保留原文件；请备份并修复后重试。")
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)


class DateRenamerTab:
    def __init__(self, parent: tk.Widget):
        self.parent = parent
        self.scale = current_scale(parent.winfo_toplevel())
        self.folder: Optional[Path] = None
        self.plan: List[Tuple[Path, Path, str]] = []
        self.undo_file = APP_DIR / "renamer_last_undo.json"

        top = ttk.Frame(parent, padding=scaled(12, self.scale))
        top.pack(fill=tk.X)
        for col in (2, 4, 6):
            top.columnconfigure(col, weight=1)
        ttk.Button(top, text="选择文件夹", command=self.choose_folder).grid(row=0, column=0, padx=(0, scaled(10, self.scale)), pady=scaled(4, self.scale), sticky=tk.W)
        ttk.Label(top, text="规则:").grid(row=0, column=1, padx=(0, scaled(5, self.scale)), pady=scaled(4, self.scale), sticky=tk.W)
        self.mode_var = tk.StringVar(value="日期+序号")
        self.mode_box = ttk.Combobox(
            top,
            textvariable=self.mode_var,
            values=("日期+序号", "前缀+序号", "查找替换", "添加前缀", "添加后缀", "转小写", "转大写"),
            width=12,
            state="readonly",
        )
        self.mode_box.grid(row=0, column=2, padx=(0, scaled(12, self.scale)), pady=scaled(4, self.scale), sticky=tk.EW)
        self.mode_box.bind("<<ComboboxSelected>>", lambda _event: self.preview())
        ttk.Label(top, text="日期/前缀:").grid(row=0, column=3, padx=(0, scaled(5, self.scale)), pady=scaled(4, self.scale), sticky=tk.W)
        self.date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        ttk.Entry(top, textvariable=self.date_var, width=16).grid(row=0, column=4, padx=(0, scaled(12, self.scale)), pady=scaled(4, self.scale), sticky=tk.EW)
        ttk.Label(top, text="查找:").grid(row=1, column=0, padx=(0, scaled(5, self.scale)), pady=scaled(4, self.scale), sticky=tk.W)
        self.find_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.find_var, width=12).grid(row=1, column=1, columnspan=2, padx=(0, scaled(12, self.scale)), pady=scaled(4, self.scale), sticky=tk.EW)
        ttk.Label(top, text="替换/后缀:").grid(row=1, column=3, padx=(0, scaled(5, self.scale)), pady=scaled(4, self.scale), sticky=tk.W)
        self.replace_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.replace_var, width=12).grid(row=1, column=4, padx=(0, scaled(12, self.scale)), pady=scaled(4, self.scale), sticky=tk.EW)
        ttk.Label(top, text="起始:").grid(row=1, column=5, padx=(0, scaled(5, self.scale)), pady=scaled(4, self.scale), sticky=tk.W)
        self.start_var = tk.StringVar(value="1")
        ttk.Entry(top, textvariable=self.start_var, width=5).grid(row=1, column=6, pady=scaled(4, self.scale), sticky=tk.EW)
        actions = ttk.Frame(top)
        actions.grid(row=2, column=0, columnspan=7, pady=(scaled(6, self.scale), 0), sticky=tk.W)
        ttk.Button(actions, text="预览", command=self.preview).grid(row=0, column=0, padx=(0, scaled(8, self.scale)))
        ttk.Button(actions, text="执行重命名", command=self.apply).grid(row=0, column=1, padx=(0, scaled(8, self.scale)))
        ttk.Button(actions, text="撤回上次", command=self.undo_last).grid(row=0, column=2)

        self.folder_var = tk.StringVar(value="未选择文件夹")
        ttk.Label(parent, textvariable=self.folder_var, padding=(12, 0)).pack(fill=tk.X)

        columns = ("old", "new", "status")
        self.tree = ttk.Treeview(parent, columns=columns, show="headings")
        self.tree.heading("old", text="原文件名")
        self.tree.heading("new", text="新文件名")
        self.tree.heading("status", text="状态")
        self.tree.column("old", width=scaled(340, self.scale))
        self.tree.column("new", width=scaled(340, self.scale))
        self.tree.column("status", width=scaled(220, self.scale))
        self.tree.pack(fill=tk.BOTH, expand=True, padx=scaled(12, self.scale), pady=scaled(12, self.scale))

    def choose_folder(self) -> None:
        selected = filedialog.askdirectory()
        if not selected:
            return
        self.folder = Path(selected)
        self.folder_var.set(str(self.folder))
        self.preview()

    def unique_target(self, folder: Path, stem: str, suffix: str, used: Set[str]) -> Path:
        index = 0
        while True:
            name = f"{stem}{suffix}" if index == 0 else f"{stem}_{index}{suffix}"
            target = folder / name
            key = name.lower()
            if key not in used and not target.exists():
                used.add(key)
                return target
            index += 1

    def preview(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.plan.clear()
        if not self.folder:
            return
        files = [p for p in self.folder.iterdir() if p.is_file()]
        used: Set[str] = {p.name.lower() for p in self.folder.iterdir()}
        prefix = self.date_var.get().strip() or datetime.now().strftime("%Y-%m-%d")
        find_text = self.find_var.get()
        replace_text = self.replace_var.get()
        mode = self.mode_var.get()
        try:
            start = max(1, int(self.start_var.get() or "1"))
        except ValueError:
            start = 1

        for number, source in enumerate(sorted(files, key=lambda p: p.name.lower()), start):
            if mode == "日期+序号":
                stem = f"{prefix}_{number:03d}"
            elif mode == "前缀+序号":
                stem = f"{prefix or '文件'}_{number:03d}"
            elif mode == "查找替换":
                stem = source.stem.replace(find_text, replace_text) if find_text else source.stem
            elif mode == "添加前缀":
                stem = f"{prefix}{source.stem}"
            elif mode == "添加后缀":
                stem = f"{source.stem}{replace_text or prefix}"
            elif mode == "转小写":
                stem = source.stem.lower()
            elif mode == "转大写":
                stem = source.stem.upper()
            else:
                stem = source.stem

            name = f"{stem}{source.suffix}"
            if re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " ")):
                status = "非法文件名，已跳过"
                self.plan.append((source, source, status))
                self.tree.insert("", tk.END, values=(source.name, name, status))
                continue
            if stem.lower() == source.stem.lower():
                target = source.with_name(name)
            else:
                target = self.unique_target(self.folder, stem, source.suffix, used)
            status = "无需改名" if source.name == target.name else "待改名"
            self.plan.append((source, target, status))
            self.tree.insert("", tk.END, values=(source.name, target.name, status))

    @staticmethod
    def _validate_undo_payload(payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("撤回记录必须包含文件列表")
        for item in payload["items"]:
            if not isinstance(item, dict):
                raise ValueError("撤回记录包含无效条目")
            for key in ("old", "new"):
                value = item.get(key)
                if not isinstance(value, str) or not value or "\x00" in value or not Path(value).is_absolute():
                    raise ValueError("撤回记录中的文件路径无效")
            if "identity" in item:
                identity = item["identity"]
                if not isinstance(identity, list) or len(identity) != 2 or not all(isinstance(value, int) for value in identity):
                    raise ValueError("撤回记录中的文件身份无效")
        return payload

    def _write_undo_payload(self, payload, destination=None) -> None:
        import tempfile

        destination = destination or self.undo_file
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=destination.parent,
                                             prefix=".renamer_", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                if isinstance(payload, bytes):
                    handle.write(payload.decode("utf-8"))
                else:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def apply(self) -> None:
        if not self.plan:
            messagebox.showinfo("提示", "没有可执行的重命名计划。")
            return
        if not messagebox.askokcancel("确认", "将按预览结果重命名当前文件夹中的文件，确认执行吗？"):
            return
        candidates = [(source, target) for source, target, status in self.plan if status == "待改名" and source.exists()]
        if not candidates:
            return
        previous_backup = None
        try:
            previous = self.undo_file.read_bytes() if self.undo_file.exists() else None
            if previous is not None:
                self._validate_undo_payload(json.loads(previous.decode("utf-8-sig")))
            ensure_app_dir()
            if previous is not None:
                previous_backup = self.undo_file.with_name(f".renamer_previous_{uuid.uuid4().hex}.json")
                self._write_undo_payload(previous, previous_backup)
            records = []
            for source, target in candidates:
                identity = source.stat()
                records.append({"old": str(source), "new": str(target), "identity": [identity.st_dev, identity.st_ino]})
            prepared = {"created_at": datetime.now().isoformat(), "items": records}
            self._write_undo_payload(prepared)
        except (OSError, ValueError, TypeError) as exc:
            if previous_backup is not None and previous_backup.exists():
                previous_backup.unlink()
            messagebox.showerror("重命名未执行", f"无法安全保存撤回记录，源文件未改动：{exc}")
            return
        changed = 0
        undo_items = []
        for (source, target), record in zip(candidates, records):
            try:
                source.rename(target)
                undo_items.append(record)
                changed += 1
            except OSError as exc:
                log_line(f"批量重命名失败: {source} -> {target}: {exc}")
        try:
            if not undo_items:
                if previous_backup is not None:
                    os.replace(previous_backup, self.undo_file)
                else:
                    self.undo_file.unlink()
            elif len(undo_items) != len(candidates):
                self._write_undo_payload({**prepared, "items": undo_items})
            if previous_backup is not None and previous_backup.exists():
                previous_backup.unlink()
        except OSError as exc:
            self.preview()
            recovery = f"；上一轮记录备份：{previous_backup}" if previous_backup is not None and previous_backup.exists() else ""
            messagebox.showerror("撤回记录更新失败", f"已重命名 {changed} 个文件，预记录已保留：{exc}{recovery}")
            return
        log_line(f"批量重命名完成: {changed} 个文件")
        self.preview()
        messagebox.showinfo("完成", f"已重命名 {changed} 个文件。")

    def undo_last(self) -> None:
        if not self.undo_file.exists():
            messagebox.showinfo("提示", "没有可撤回的批量重命名记录。")
            return
        try:
            with self.undo_file.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            items = self._validate_undo_payload(payload)["items"]
        except Exception as exc:
            messagebox.showerror("撤回失败", f"读取撤回记录失败：{exc}")
            return
        if not items:
            messagebox.showinfo("提示", "没有可撤回的批量重命名记录。")
            return
        if not messagebox.askokcancel("确认撤回", "将撤回上一次批量重命名，确认执行吗？"):
            return

        restored = 0
        pending = []
        for item in reversed(items):
            new_path = Path(item["new"])
            old_path = Path(item["old"])
            if "identity" in item:
                def matches_identity(path, expected_identity=item["identity"]):
                    try:
                        identity = path.stat()
                        return [identity.st_dev, identity.st_ino] == expected_identity
                    except OSError:
                        return False

                if not matches_identity(new_path):
                    if matches_identity(old_path):
                        continue  # A pre-recorded rename never ran, or was already undone.
                    pending.append(item)
                    log_line(f"撤回重命名未完成，原文件身份无法确认: {new_path}")
                    continue
            if not new_path.exists():
                pending.append(item)
                log_line(f"撤回重命名未完成，文件不存在: {new_path}")
                continue
            target = old_path
            if target.exists() and target != new_path:
                target = self.unique_target(old_path.parent, f"{old_path.stem}_restored", old_path.suffix, set())
            try:
                new_path.rename(target)
                restored += 1
            except OSError as exc:
                pending.append(item)
                log_line(f"撤回重命名失败: {new_path} -> {target}: {exc}")
        try:
            if pending:
                payload["items"] = list(reversed(pending))
                self._write_undo_payload(payload)
            else:
                self.undo_file.unlink()
        except OSError as exc:
            self.preview()
            messagebox.showerror("撤回记录保存失败", f"已恢复 {restored} 个文件，但撤回记录未能更新：{exc}")
            return
        log_line(f"撤回批量重命名完成: {restored} 个文件")
        self.preview()
        messagebox.showinfo("完成", f"已撤回 {restored} 个文件；{len(pending)} 条未完成记录已保留，可稍后重试。")


class TopmostController:
    DWMWA_CLOAKED = 14

    def __init__(self, root: tk.Tk, status_callback: Callable[[str], None]):
        self.root = root
        self.status_callback = status_callback
        self.tracked: Set[int] = set()
        self.last_hwnd: Optional[int] = None
        self.running = True
        self._poll_foreground()

    def root_hwnd(self) -> int:
        try:
            return int(self.root.winfo_id())
        except Exception:
            return 0

    def is_cloaked(self, hwnd: int) -> bool:
        try:
            cloaked = ctypes.c_int(0)
            result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                ctypes.c_void_p(hwnd),
                self.DWMWA_CLOAKED,
                ctypes.byref(cloaked),
                ctypes.sizeof(cloaked),
            )
            return result == 0 and cloaked.value != 0
        except Exception:
            return False

    def intersects_desktop(self, hwnd: int) -> bool:
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if right - left <= 120 or bottom - top <= 80:
                return False
            screen_left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
            screen_top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
            screen_right = screen_left + win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
            screen_bottom = screen_top + win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
            return left < screen_right and right > screen_left and top < screen_bottom and bottom > screen_top
        except Exception:
            return False

    def valid_target(self, hwnd: int) -> bool:
        if not hwnd or not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
            return False
        root_hwnd = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
        if root_hwnd == self.root_hwnd():
            return False
        if root_hwnd != hwnd and win32gui.GetAncestor(root_hwnd, win32con.GA_ROOT) != root_hwnd:
            return False
        if win32gui.IsIconic(root_hwnd) or self.is_cloaked(root_hwnd):
            return False
        if not win32gui.GetWindowText(root_hwnd).strip():
            return False
        try:
            _, pid = win32process.GetWindowThreadProcessId(root_hwnd)
            if pid == os.getpid():
                return False
        except Exception:
            pass
        class_name = win32gui.GetClassName(root_hwnd)
        if class_name in {"Progman", "WorkerW", "Shell_TrayWnd", "Windows.UI.Core.CoreWindow"}:
            return False
        try:
            ex_style = win32gui.GetWindowLong(root_hwnd, win32con.GWL_EXSTYLE)
            if ex_style & win32con.WS_EX_TOOLWINDOW and not ex_style & win32con.WS_EX_APPWINDOW:
                return False
            owner = win32gui.GetWindow(root_hwnd, win32con.GW_OWNER)
            if owner and not ex_style & win32con.WS_EX_APPWINDOW:
                return False
        except Exception:
            return False
        return self.intersects_desktop(root_hwnd)

    def _poll_foreground(self) -> None:
        if not self.running:
            return
        try:
            hwnd = win32gui.GetForegroundWindow()
            if self.valid_target(hwnd):
                self.last_hwnd = hwnd
        except Exception:
            pass
        self.root.after(500, self._poll_foreground)

    def window_title(self, hwnd: Optional[int]) -> str:
        if not hwnd:
            return "无可用窗口"
        try:
            title = win32gui.GetWindowText(hwnd).strip()
            return title or "无标题窗口"
        except Exception:
            return "未知窗口"

    def foreground_window(self) -> Optional[int]:
        try:
            hwnd = win32gui.GetForegroundWindow()
            return hwnd if self.valid_target(hwnd) else None
        except Exception:
            return None

    def window_from_point(self, x: int, y: int) -> Optional[int]:
        try:
            hwnd = win32gui.WindowFromPoint((x, y))
            hwnd = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
            return hwnd if self.valid_target(hwnd) else None
        except Exception:
            return None

    def is_topmost(self, hwnd: int) -> bool:
        try:
            return bool(win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST)
        except Exception:
            return False

    def list_windows(self) -> List[Tuple[int, str]]:
        windows: List[Tuple[int, str]] = []

        def collect(hwnd: int, _extra) -> bool:
            if self.valid_target(hwnd):
                title = win32gui.GetWindowText(hwnd).strip()
                if title:
                    windows.append((hwnd, title))
            return True

        win32gui.EnumWindows(collect, None)
        windows.sort(key=lambda item: item[1].lower())
        return windows

    def pin_window(self, hwnd: int) -> None:
        if not self.valid_target(hwnd):
            self.status_callback("选择的窗口不可置顶或已经关闭。")
            return
        if self.set_topmost(hwnd, True):
            self.status_callback(f"已置顶：{self.window_title(hwnd)}")
            log_line(f"置顶指定窗口: {self.window_title(hwnd)}")
        else:
            self.status_callback(f"置顶失败：{self.window_title(hwnd)}。如果它是管理员窗口，请用管理员身份运行工具箱。")
            log_line(f"置顶失败: {self.window_title(hwnd)}")

    def unpin_window(self, hwnd: int) -> None:
        if not hwnd or not win32gui.IsWindow(hwnd):
            self.status_callback("选择的窗口已经关闭。")
            return
        if self.set_topmost(hwnd, False):
            self.status_callback(f"已取消置顶：{self.window_title(hwnd)}")
            log_line(f"取消置顶指定窗口: {self.window_title(hwnd)}")
        else:
            self.status_callback(f"取消置顶失败：{self.window_title(hwnd)}")
            log_line(f"取消置顶失败: {self.window_title(hwnd)}")

    def toggle_window(self, hwnd: int) -> None:
        if not self.valid_target(hwnd):
            self.status_callback("选择的窗口不可置顶或已经关闭。")
            return
        if self.is_topmost(hwnd):
            self.unpin_window(hwnd)
        else:
            self.pin_window(hwnd)

    def toggle_foreground(self) -> None:
        hwnd = self.foreground_window()
        if not hwnd and self.last_hwnd and self.valid_target(self.last_hwnd):
            hwnd = self.last_hwnd
        if not hwnd:
            self.status_callback("没有找到当前最上层可置顶窗口。")
            return
        self.last_hwnd = hwnd
        self.toggle_window(hwnd)

    def select_by_mouse(self) -> None:
        self.status_callback("请在 10 秒内用鼠标左键点击窗口，将立即切换置顶。")

        def worker() -> None:
            deadline = time.time() + 10
            was_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
            while time.time() < deadline and self.running:
                is_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
                if is_down and not was_down:
                    x, y = win32gui.GetCursorPos()
                    hwnd = self.window_from_point(x, y)
                    if hwnd:
                        self.last_hwnd = hwnd
                        self.root.after(0, lambda h=hwnd: self.toggle_window(h))
                    else:
                        self.root.after(0, lambda: self.status_callback("鼠标点击的位置没有可选择窗口。"))
                    return
                was_down = is_down
                time.sleep(0.03)
            self.root.after(0, lambda: self.status_callback("鼠标选择已超时。"))

        threading.Thread(target=worker, daemon=True).start()

    def set_topmost(self, hwnd: int, enabled: bool) -> bool:
        insert_after = win32con.HWND_TOPMOST if enabled else win32con.HWND_NOTOPMOST
        try:
            win32gui.SetWindowPos(
                hwnd,
                insert_after,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            )
            time.sleep(0.05)
            success = self.is_topmost(hwnd) == enabled
            if success:
                if enabled:
                    self.tracked.add(hwnd)
                else:
                    self.tracked.discard(hwnd)
            return success
        except Exception as exc:
            log_line(f"SetWindowPos 失败: {self.window_title(hwnd)} -> {exc}")
            return False

    def pin_current(self) -> None:
        hwnd = self.last_hwnd
        if not hwnd or not self.valid_target(hwnd):
            self.status_callback("没有找到可置顶的当前窗口。")
            return
        self.pin_window(hwnd)

    def unpin_current(self) -> None:
        hwnd = self.last_hwnd
        if not hwnd or not win32gui.IsWindow(hwnd):
            self.status_callback("没有找到可取消置顶的当前窗口。")
            return
        self.unpin_window(hwnd)

    def unpin_all(self) -> None:
        failures = 0
        for hwnd in list(self.tracked):
            try:
                if not win32gui.IsWindow(hwnd):
                    self.tracked.discard(hwnd)
                elif not self.set_topmost(hwnd, False):
                    failures += 1
            except Exception:
                failures += 1
        if failures:
            self.status_callback(f"取消置顶失败：{failures} 个窗口，已保留记录供重试。")
        else:
            self.status_callback("已取消所有由本工具设置的置顶窗口。")

    def stop(self) -> None:
        self.running = False
        self.unpin_all()


class DropCloseController:
    def __init__(self, root: tk.Tk, status_callback: Callable[[str], None]):
        self.root = root
        self.status_callback = status_callback
        self._close_lock = threading.Lock()
        self._close_stopped = False
        if os.name == "nt":
            self.enable()

    def enable(self) -> None:
        if DND_FILES is None or not hasattr(self.root, "drop_target_register"):
            log_line("DropCloseController disabled: tkinterdnd2 unavailable")
            self.status_callback("拖入关闭不可用：缺少 tkinterdnd2")
            return
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._on_drop)
            log_line("DropCloseController enabled via tkinterdnd2")
            self.status_callback("已启用拖入文件夹/程序强制关闭")
        except Exception as exc:
            log_line(f"启用拖放关闭失败: {exc}")

    def _on_drop(self, event) -> str:
        try:
            paths = [str(path) for path in self.root.tk.splitlist(event.data)]
            log_line(f"DropCloseController received drop via tkinterdnd2: {paths}")
            if paths:
                self.close_paths(paths)
        except Exception as exc:
            log_line(f"DropCloseController drop event failed: {exc}")
        return "break"

    def close_paths(self, paths: List[str]) -> None:
        if getattr(self, "_close_stopped", False):
            return
        selected = list(paths)
        coordinator = getattr(self, "_close_coordinator", None)
        if coordinator is not None:
            coordinator(lambda: self._close_worker(selected))
        else:
            start_process_close_worker(self, lambda: self._close_worker(selected))

    def _close_worker(self, paths: List[str]) -> None:
        if getattr(self, "_close_stopped", False):
            return
        log_line(f"DropCloseController close worker start: {paths}")
        targets, input_errors = resolve_process_close_targets(paths)
        input_error_summary = f"；目标失败 {len(input_errors)} 项；{input_errors[0]}" if input_errors else ""
        for error in input_errors:
            log_line(f"拖入关闭目标失败：{error}")
        try:
            processes = running_process_paths()
            snapshot_notice = process_snapshot_notice(processes)
            log_line(f"DropCloseController process snapshot count: {len(processes)}")
            matches = match_processes_for_drop_paths(targets, processes)
        except Exception as exc:
            message = f"拖入关闭失败：{exc}{input_error_summary}"
            log_line(message)
            self.root.after(0, lambda m=message: self.status_callback(m))
            return
        log_line(f"DropCloseController matched processes: {matches}")
        names = ", ".join(Path(path).name for path in paths[:3])
        if len(paths) > 3:
            names += f" 等 {len(paths)} 项"
        if not matches:
            message = f"拖入关闭：未找到可查询的相关进程（{names}）{input_error_summary}{snapshot_notice}"
            log_line(message)
            self.root.after(0, lambda m=message: self.status_callback(m))
            return
        pid_to_path = dict(matches)
        if getattr(self, "_close_stopped", False):
            return
        try:
            closed, failures = terminate_processes_fast(pid_to_path, include_children=False, expected_paths=pid_to_path)
        except Exception as exc:
            message = f"拖入关闭失败：{exc}{input_error_summary}"
            log_line(message)
            self.root.after(0, lambda m=message: self.status_callback(m))
            return
        if getattr(self, "_close_stopped", False):
            return
        for pid in sorted(closed):
            log_line(f"拖入强制关闭已验证: pid={pid} path={pid_to_path[pid]}")
        for pid, error in failures.items():
            log_line(f"拖入强制关闭失败: pid={pid} path={pid_to_path[pid]} error={error}")
        if failures:
            first_error = next(iter(failures.values()))
            message = f"拖入关闭完成：已关闭 {len(closed)} 个，失败 {len(failures)} 个；{first_error}"
        else:
            message = f"拖入关闭完成：已确认关闭 {len(closed)} 个进程"
        message += input_error_summary
        try:
            remaining, final_notice, cancelled = observe_closed_process_targets(
                targets, include_directories=True, should_stop=lambda: getattr(self, "_close_stopped", False)
            )
            if cancelled:
                return
            if remaining:
                details = "、".join(f"{Path(path).name}（PID {pid}）" for pid, path in remaining)
                message = f"拖入关闭：观察期内仍有相关进程运行：{details}；{message}"
                if any(pid in closed or pid not in pid_to_path for pid, _path in remaining):
                    message += "；可能被重新启动"
            else:
                message += f"；{PROCESS_CLOSE_OBSERVATION_SECONDS:g} 秒观察期内未发现可查询的残留进程"
            message += final_notice
        except Exception as exc:
            message += f"；关闭后复查失败：{exc}"
        log_line(message)
        self.root.after(0, lambda m=message: self.status_callback(m))

    def stop(self) -> None:
        self._close_stopped = True


class TrayIcon:
    WM_TRAY = win32con.WM_USER + 20
    TASKBAR_CREATED = win32gui.RegisterWindowMessage("TaskbarCreated")
    ID_OPEN = 1001
    ID_PIN = 1002
    ID_UNPIN = 1003
    ID_UNPIN_ALL = 1004
    ID_EXIT = 1005
    ID_HOTKEY_TOGGLE = 2001
    HOTKEY_CANDIDATES = [
        ("Ctrl+Alt+T", win32con.MOD_CONTROL | win32con.MOD_ALT | win32con.MOD_NOREPEAT, ord("T")),
        ("Ctrl+Alt+Y", win32con.MOD_CONTROL | win32con.MOD_ALT | win32con.MOD_NOREPEAT, ord("Y")),
        ("Ctrl+Shift+T", win32con.MOD_CONTROL | win32con.MOD_SHIFT | win32con.MOD_NOREPEAT, ord("T")),
        ("Ctrl+Alt+F12", win32con.MOD_CONTROL | win32con.MOD_ALT | win32con.MOD_NOREPEAT, win32con.VK_F12),
    ]

    def __init__(self, root: tk.Tk, topmost: TopmostController, exit_callback: Optional[Callable[[], None]] = None):
        self.root = root
        self.topmost = topmost
        self.exit_callback = exit_callback
        self.hwnd = None
        self.icon_handle = None
        self.hotkey_registered = False
        self.hotkey_label = ""
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        message_map = {
            self.WM_TRAY: self._on_tray,
            self.TASKBAR_CREATED: self._on_taskbar_created,
            win32con.WM_COMMAND: self._on_command,
            win32con.WM_HOTKEY: self._on_hotkey,
            win32con.WM_DESTROY: self._on_destroy,
        }
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = "UtilityToolboxTray"
        wc.lpfnWndProc = message_map
        try:
            win32gui.RegisterClass(wc)
        except win32gui.error:
            pass
        self.hwnd = win32gui.CreateWindow(
            wc.lpszClassName,
            APP_NAME,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            wc.hInstance,
            None,
        )
        try:
            self.icon_handle = win32gui.LoadImage(
                wc.hInstance,
                str(app_icon_path()),
                win32con.IMAGE_ICON,
                0,
                0,
                win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
            )
        except Exception:
            self.icon_handle = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
        try:
            self._add_icon()
        except Exception as exc:
            log_line(f"首次注册托盘图标失败，等待任务栏恢复事件: {exc}")
        for label, modifiers, vk in self.HOTKEY_CANDIDATES:
            try:
                win32gui.RegisterHotKey(self.hwnd, self.ID_HOTKEY_TOGGLE, modifiers, vk)
                self.hotkey_registered = True
                self.hotkey_label = label
                log_line(f"置顶热键已注册: {label}")
                self.root.after(0, lambda text=label: self.topmost.status_callback(f"置顶热键：{text}"))
                break
            except Exception as exc:
                log_line(f"注册热键 {label} 失败: {exc}")
        if not self.hotkey_registered:
            self.root.after(0, lambda: self.topmost.status_callback("置顶热键注册失败：快捷键已被占用，请用鼠标点选。"))
        win32gui.PumpMessages()

    def _add_icon(self) -> None:
        if not self.hwnd or not self.icon_handle:
            raise RuntimeError("托盘窗口或图标尚未初始化")
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        nid = (self.hwnd, 0, flags, self.WM_TRAY, self.icon_handle, APP_NAME)
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)

    def _on_taskbar_created(self, hwnd, msg, wparam, lparam):
        try:
            self._add_icon()
            log_line("检测到 Windows 任务栏重启，已恢复托盘图标")
        except Exception as exc:
            log_line(f"Windows 任务栏重启后恢复托盘图标失败: {exc}")
        return True

    def _on_tray(self, hwnd, msg, wparam, lparam):
        if lparam in {win32con.WM_LBUTTONUP, win32con.WM_LBUTTONDBLCLK}:
            self.root.after(0, self.open_root)
        elif lparam in {win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.WM_CONTEXTMENU}:
            self.root.after(0, self.show_tk_menu)
        return True

    def open_root(self) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def show_tk_menu(self) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="打开工具箱", command=self.open_root)
        menu.add_separator()
        menu.add_command(label="置顶当前窗口", command=self.topmost.pin_current)
        menu.add_command(label="取消当前窗口置顶", command=self.topmost.unpin_current)
        menu.add_command(label="取消所有本工具置顶窗口", command=self.topmost.unpin_all)
        menu.add_separator()
        menu.add_command(label="退出", command=self.exit_app)
        x, y = win32gui.GetCursorPos()
        try:
            menu.tk_popup(x, y)
        finally:
            try:
                menu.grab_release()
            finally:
                menu.destroy()

    def _show_menu(self) -> None:
        menu = win32gui.CreatePopupMenu()
        try:
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_OPEN, "打开工具箱")
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, None)
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_PIN, "置顶当前窗口")
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_UNPIN, "取消当前窗口置顶")
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_UNPIN_ALL, "取消所有本工具置顶窗口")
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, None)
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_EXIT, "退出")
            pos = win32gui.GetCursorPos()
            win32gui.SetForegroundWindow(self.hwnd)
            win32gui.TrackPopupMenu(
                menu,
                win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON,
                pos[0],
                pos[1],
                0,
                self.hwnd,
                None,
            )
            win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)
        finally:
            try:
                win32gui.DestroyMenu(menu)
            except Exception:
                pass

    def _on_command(self, hwnd, msg, wparam, lparam):
        command = win32api.LOWORD(wparam)
        if command == self.ID_OPEN:
            self.root.after(0, self.open_root)
        elif command == self.ID_PIN:
            self.root.after(0, self.topmost.pin_current)
        elif command == self.ID_UNPIN:
            self.root.after(0, self.topmost.unpin_current)
        elif command == self.ID_UNPIN_ALL:
            self.root.after(0, self.topmost.unpin_all)
        elif command == self.ID_EXIT:
            self.root.after(0, self.exit_app)
        return True

    def _on_hotkey(self, hwnd, msg, wparam, lparam):
        if wparam == self.ID_HOTKEY_TOGGLE:
            self.root.after(0, self.topmost.toggle_foreground)
        return True

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        if self.hotkey_registered:
            try:
                win32gui.UnregisterHotKey(self.hwnd, self.ID_HOTKEY_TOGGLE)
            except Exception:
                pass
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self.hwnd, 0))
        except Exception:
            pass
        win32gui.PostQuitMessage(0)
        return True

    def stop(self) -> None:
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_DESTROY, 0, 0)

    def exit_app(self) -> None:
        close_mic_toggle_processes()
        if self.exit_callback:
            self.exit_callback()
        else:
            self.root.destroy()


AUDIO_DEVICES = [
    {"name": "HyperX Cloud Alpha Wireless", "id": "{0.0.0.00000000}.{300AB429-8538-4C15-B885-0A28C416A317}"},
    {"name": "Realtek Speakers", "id": "{0.0.0.00000000}.{729281F5-3901-4B99-9799-F2A9891E705F}"},
]
CLSCTX_ALL = 23
E_RENDER = 0
E_CAPTURE = 1
DEVICE_STATE_ACTIVE = 0x00000001
ROLE_CONSOLE = 0
ROLE_MULTIMEDIA = 1
ROLE_COMMUNICATIONS = 2


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> "GUID":
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


ole32 = ctypes.OleDLL("ole32")
ole32.CoInitialize.argtypes = [ctypes.c_void_p]
ole32.CoInitialize.restype = ctypes.c_long
ole32.CoUninitialize.argtypes = []
ole32.CoUninitialize.restype = None
ole32.CoCreateInstance.argtypes = [
    ctypes.POINTER(GUID),
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(GUID),
    ctypes.POINTER(ctypes.c_void_p),
]
ole32.CoCreateInstance.restype = ctypes.c_long
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
ole32.CoTaskMemFree.restype = None


def check_hr(hr: int, context: str) -> None:
    if hr < 0:
        raise OSError(f"{context} failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")


def com_method(ptr: ctypes.c_void_p, index: int, restype, *argtypes):
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


def release(ptr: ctypes.c_void_p) -> None:
    if ptr:
        com_method(ptr, 2, ctypes.c_ulong)(ptr)


def create_com_object(clsid: str, iid: str) -> ctypes.c_void_p:
    clsid_guid = GUID.from_string(clsid)
    iid_guid = GUID.from_string(iid)
    result = ctypes.c_void_p()
    check_hr(
        ole32.CoCreateInstance(ctypes.byref(clsid_guid), None, CLSCTX_ALL, ctypes.byref(iid_guid), ctypes.byref(result)),
        "CoCreateInstance",
    )
    return result


CAPTURE_DEVICE_REGISTRY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
ENDPOINT_NAME_PROPERTY = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"


def endpoint_registry_id(endpoint_id: str) -> str:
    match = re.search(r"(\{[0-9A-Fa-f-]{36}\})$", endpoint_id)
    return match.group(1) if match else endpoint_id


def capture_device_name(endpoint_id: str) -> str:
    key_path = f"{CAPTURE_DEVICE_REGISTRY}\\{endpoint_registry_id(endpoint_id)}\\Properties"
    access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, access) as key:
            value, _value_type = winreg.QueryValueEx(key, ENDPOINT_NAME_PROPERTY)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except OSError:
        pass
    return "未命名麦克风"


def enumerate_active_capture_devices() -> List[Dict[str, str]]:
    ole32.CoInitialize(None)
    enumerator = ctypes.c_void_p()
    collection = ctypes.c_void_p()
    try:
        enumerator = create_com_object(
            "BCDE0395-E52F-467C-8E3D-C4579291692E",
            "A95664D2-9614-4F35-A746-DE8DB63617E6",
        )
        enum_endpoints = com_method(
            enumerator,
            3,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_void_p),
        )
        check_hr(
            enum_endpoints(enumerator, E_CAPTURE, DEVICE_STATE_ACTIVE, ctypes.byref(collection)),
            "EnumAudioEndpoints",
        )
        count = ctypes.c_uint()
        get_count = com_method(collection, 3, ctypes.c_long, ctypes.POINTER(ctypes.c_uint))
        check_hr(get_count(collection, ctypes.byref(count)), "IMMDeviceCollection.GetCount")
        item = com_method(collection, 4, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        devices: List[Dict[str, str]] = []
        for index in range(count.value):
            device = ctypes.c_void_p()
            raw_id = ctypes.c_wchar_p()
            try:
                check_hr(item(collection, index, ctypes.byref(device)), "IMMDeviceCollection.Item")
                get_id = com_method(device, 5, ctypes.c_long, ctypes.POINTER(ctypes.c_wchar_p))
                check_hr(get_id(device, ctypes.byref(raw_id)), "IMMDevice.GetId")
                endpoint_id = raw_id.value or ""
                devices.append({"id": endpoint_id, "name": capture_device_name(endpoint_id)})
            finally:
                if raw_id:
                    ole32.CoTaskMemFree(raw_id)
                release(device)
        return devices
    finally:
        release(collection)
        release(enumerator)
        ole32.CoUninitialize()


def get_default_render_id() -> str:
    enumerator = create_com_object("BCDE0395-E52F-467C-8E3D-C4579291692E", "A95664D2-9614-4F35-A746-DE8DB63617E6")
    device = ctypes.c_void_p()
    try:
        get_default = com_method(
            enumerator,
            4,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        )
        check_hr(get_default(enumerator, E_RENDER, ROLE_MULTIMEDIA, ctypes.byref(device)), "GetDefaultAudioEndpoint")
        get_id = com_method(device, 5, ctypes.c_long, ctypes.POINTER(ctypes.c_wchar_p))
        raw_id = ctypes.c_wchar_p()
        check_hr(get_id(device, ctypes.byref(raw_id)), "IMMDevice.GetId")
        try:
            return raw_id.value or ""
        finally:
            ole32.CoTaskMemFree(raw_id)
    finally:
        release(device)
        release(enumerator)


def set_default_render_id(device_id: str) -> None:
    policy = create_com_object("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9", "F8679F50-850A-41CF-9C72-430F290290C8")
    try:
        set_default = com_method(policy, 13, ctypes.c_long, ctypes.c_wchar_p, ctypes.c_int)
        for role in (ROLE_CONSOLE, ROLE_MULTIMEDIA, ROLE_COMMUNICATIONS):
            check_hr(set_default(policy, device_id, role), "SetDefaultEndpoint")
    finally:
        release(policy)


def choose_audio_target(current_id: str) -> Dict[str, str]:
    first, second = AUDIO_DEVICES
    return second if current_id.casefold() == first["id"].casefold() else first


class AudioSwitchTab:
    def __init__(self, parent: tk.Widget, status_callback: Callable[[str], None]):
        self.parent = parent
        self.status_callback = status_callback
        self.scale = current_scale(parent.winfo_toplevel())
        frame = ttk.Frame(parent, padding=scaled(18, self.scale))
        frame.pack(fill=tk.BOTH, expand=True)
        title = ttk.Label(frame, text="选择默认音频输出", font=("Microsoft YaHei UI", scaled(18, self.scale), "bold"))
        title.pack(anchor=tk.W, fill=tk.X)
        bind_wrap(frame, title)
        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, pady=(scaled(16, self.scale), 0))
        ttk.Button(buttons, text="耳机音频", command=lambda: self.set_audio(AUDIO_DEVICES[0])).pack(
            side=tk.LEFT,
            padx=(0, scaled(10, self.scale)),
            ipadx=scaled(24, self.scale),
            ipady=scaled(10, self.scale),
        )
        ttk.Button(buttons, text="音响音频", command=lambda: self.set_audio(AUDIO_DEVICES[1])).pack(
            side=tk.LEFT,
            ipadx=scaled(24, self.scale),
            ipady=scaled(10, self.scale),
        )

    def set_audio(self, device: Dict[str, str]) -> None:
        def worker() -> None:
            try:
                ole32.CoInitialize(None)
                set_default_render_id(device["id"])
                self.parent.after(0, lambda: self._audio_done(device["name"]))
            except Exception as exc:
                self.parent.after(0, lambda error=str(exc): messagebox.showerror("切换失败", error))
            finally:
                ole32.CoUninitialize()

        threading.Thread(target=worker, daemon=True).start()

    def _audio_done(self, name: str) -> None:
        self.status_callback(f"已切换音频输出：{name}")


class LiveShortcutTab:
    SHORTCUTS = [
        ("DANK1NG 虎牙直播", "https://www.huya.com/10188", None),
        ("陈泽 抖音直播 615189692839", "https://www.douyin.com/follow/live/615189692839", "chenze"),
        ("陈泽陈泽 抖音直播 539169864202", "https://www.douyin.com/follow/live/539169864202", "chenze"),
    ]

    def __init__(self, parent: tk.Widget, status_callback: Callable[[str], None]):
        self.parent = parent
        self.status_callback = status_callback
        self.scale = current_scale(parent.winfo_toplevel())
        frame = ttk.Frame(parent, padding=scaled(18, self.scale))
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="直播快捷入口", font=("Microsoft YaHei UI", scaled(18, self.scale), "bold")).pack(anchor=tk.W)
        ttk.Label(frame, text="DANK1NG 直接打开虎牙；陈泽入口会按原小程序逻辑打开页面后发送按键并点击固定坐标。").pack(anchor=tk.W, fill=tk.X, pady=(scaled(6, self.scale), scaled(14, self.scale)))
        for title, url, mode in self.SHORTCUTS:
            row = ttk.Frame(frame)
            row.pack(fill=tk.X, pady=scaled(4, self.scale))
            ttk.Label(row, text=title, width=34).pack(side=tk.LEFT)
            ttk.Button(row, text="打开", command=lambda u=url, m=mode, t=title: self.open_shortcut(t, u, m)).pack(side=tk.LEFT)

    def open_shortcut(self, title: str, url: str, mode: Optional[str]) -> None:
        threading.Thread(target=self._open_worker, args=(title, url, mode), daemon=True).start()

    def _open_worker(self, title: str, url: str, mode: Optional[str]) -> None:
        try:
            if not webbrowser.open(url):
                raise RuntimeError("浏览器未接受打开请求")
            if mode == "chenze":
                import keyboard
                import pyautogui

                time.sleep(4)
                keyboard.press_and_release("h")
                time.sleep(0.1)
                keyboard.press_and_release("b")
                time.sleep(0.5)
                for x, y in [(2535, 22), (2366, 1328), (2462, 1248)]:
                    pyautogui.click(x, y)
                    time.sleep(0.5)
            self.parent.after(0, lambda: self.status_callback(f"已打开：{title}"))
        except Exception as exc:
            self.parent.after(0, lambda error=str(exc): messagebox.showerror("打开失败", error))


class SystemToolsTab:
    MIC_TOGGLE_NAMES = {
        "一键麦克风开关.exe",
        "Ò»¼üÂó¿Ë·ç¿ª¹Ø.exe",
    }

    def __init__(self, parent: tk.Widget, status_callback: Callable[[str], None]):
        self.parent = parent
        self.status_callback = status_callback
        self.scale = current_scale(parent.winfo_toplevel())
        frame = ttk.Frame(parent, padding=scaled(18, self.scale))
        frame.pack(fill=tk.BOTH, expand=True)
        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X, pady=(0, scaled(6, self.scale)))
        for column, (title, command) in enumerate((
            ("管理员运行 PowerShell 7", self.open_admin_powershell7),
            ("启动麦克风悬浮窗", self.open_mic_toggle),
            ("关闭麦克风悬浮窗", self.close_mic_toggle),
        )):
            actions.columnconfigure(column, weight=1, uniform="system_actions")
            ttk.Button(actions, text=title, command=command).grid(
                row=0, column=column, sticky=tk.EW, padx=(0, scaled(8, self.scale)))

        target_box = ttk.LabelFrame(frame, text="选择要静音的麦克风", padding=scaled(12, self.scale))
        target_box.pack(anchor=tk.W, fill=tk.X, pady=(scaled(14, self.scale), 0))
        target_row = ttk.Frame(target_box)
        target_row.pack(anchor=tk.W, fill=tk.X)
        self.mic_target_var = tk.StringVar(value=MIC_TARGET_DEFAULT_LABEL)
        self.mic_target_choices: Dict[str, Dict[str, str]] = {}
        self.mic_target_combo = ttk.Combobox(
            target_row,
            textvariable=self.mic_target_var,
            state="readonly",
            width=42,
        )
        self.mic_target_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(target_row, text="刷新列表", command=self.refresh_mic_devices).pack(
            side=tk.LEFT,
            padx=(scaled(8, self.scale), 0),
        )
        ttk.Button(target_row, text="应用目标", command=self.apply_mic_target).pack(
            side=tk.LEFT,
            padx=(scaled(8, self.scale), 0),
        )
        ttk.Label(
            target_box,
            text="选择具体设备后只控制它；选择“跟随 Windows 当前默认输入”则继续自动跟随系统默认麦克风。",
            foreground=THEME["muted"],
        ).pack(anchor=tk.W, pady=(scaled(8, self.scale), 0))
        self.refresh_mic_devices(show_status=False)

        size_box = ttk.LabelFrame(frame, text="麦克风悬浮窗大小", padding=scaled(12, self.scale))
        size_box.pack(anchor=tk.W, fill=tk.X, pady=(scaled(14, self.scale), 0))
        size_row = ttk.Frame(size_box)
        size_row.pack(anchor=tk.W)
        self.mic_overlay_scale_var = tk.StringVar(value=f"{round(read_mic_overlay_scale() * 100)}")
        ttk.Label(size_row, text="缩放比例").pack(side=tk.LEFT)
        self.mic_overlay_scale_input = ttk.Spinbox(
            size_row,
            from_=round(MIC_OVERLAY_MIN_SCALE * 100),
            to=round(MIC_OVERLAY_MAX_SCALE * 100),
            increment=5,
            width=6,
            textvariable=self.mic_overlay_scale_var,
            justify=tk.CENTER,
        )
        self.mic_overlay_scale_input.pack(side=tk.LEFT, padx=(scaled(10, self.scale), scaled(4, self.scale)))
        ttk.Label(size_row, text="%").pack(side=tk.LEFT)
        ttk.Button(size_row, text="应用大小", command=self.apply_mic_overlay_scale).pack(
            side=tk.LEFT,
            padx=(scaled(12, self.scale), 0),
        )
        ttk.Label(
            size_box,
            text="可输入 60–120；图标、字体和间距会一起等比例变化。",
            foreground=THEME["muted"],
        ).pack(anchor=tk.W, pady=(scaled(8, self.scale), 0))

        hotkey_enabled, hotkey_text, _hotkey_codes = read_mic_hotkey_settings()
        hotkey_box = ttk.LabelFrame(frame, text="麦克风开启/静音快捷键", padding=scaled(12, self.scale))
        hotkey_box.pack(anchor=tk.W, fill=tk.X, pady=(scaled(12, self.scale), 0))
        hotkey_row = ttk.Frame(hotkey_box)
        hotkey_row.pack(anchor=tk.W, fill=tk.X)
        self.mic_hotkey_enabled_var = tk.BooleanVar(value=hotkey_enabled)
        self.mic_hotkey_var = tk.StringVar(value=hotkey_text)
        ttk.Checkbutton(
            hotkey_row,
            text="启用快捷键",
            variable=self.mic_hotkey_enabled_var,
        ).pack(side=tk.LEFT)
        self.mic_hotkey_input = ttk.Entry(hotkey_row, textvariable=self.mic_hotkey_var, width=22)
        self.mic_hotkey_input.pack(side=tk.LEFT, padx=(scaled(12, self.scale), scaled(8, self.scale)))
        ttk.Button(hotkey_row, text="录制按键", command=self.record_mic_hotkey).pack(side=tk.LEFT)
        ttk.Button(hotkey_row, text="应用快捷键", command=self.apply_mic_hotkey).pack(
            side=tk.LEFT,
            padx=(scaled(8, self.scale), 0),
        )
        ttk.Label(
            hotkey_box,
            text="支持组合键、F1–F12、MouseX1/MouseX2；关闭后悬浮窗仍显示状态，但按键不会切换麦克风。",
            foreground=THEME["muted"],
        ).pack(anchor=tk.W, pady=(scaled(8, self.scale), 0))

    def open_admin_powershell7(self) -> None:
        try:
            executable = powershell7_executable()
            if not executable:
                raise FileNotFoundError("找不到 pwsh.exe，请先安装 PowerShell 7")
            result = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                executable,
                None,
                str(Path.home()),
                1,
            )
            if result <= 32:
                raise OSError(f"ShellExecuteW failed: {result}")
            self.status_callback("已请求管理员权限启动 PowerShell 7")
        except Exception as exc:
            messagebox.showerror("启动失败", f"无法以管理员身份启动 PowerShell 7：{exc}")

    def apply_mic_overlay_scale(self) -> None:
        try:
            percent = float(self.mic_overlay_scale_var.get().strip())
            minimum = MIC_OVERLAY_MIN_SCALE * 100
            maximum = MIC_OVERLAY_MAX_SCALE * 100
            if not minimum <= percent <= maximum:
                raise ValueError(f"请输入 {minimum:.0f}–{maximum:.0f} 之间的数值")
            saved_scale = save_mic_overlay_scale(percent / 100.0)
            saved_percent = round(saved_scale * 100)
            self.mic_overlay_scale_var.set(str(saved_percent))
            if self._running_mic_processes():
                self.status_callback(f"麦克风悬浮窗已调整为 {saved_percent}%（约 1 秒内生效）")
            else:
                self.status_callback(f"麦克风悬浮窗大小已保存为 {saved_percent}%（启动后生效）")
        except Exception as exc:
            messagebox.showerror("设置失败", f"无法保存麦克风悬浮窗大小：{exc}")

    def refresh_mic_devices(self, show_status: bool = True) -> None:
        try:
            selected_id, selected_name = read_mic_target_settings()
            devices = enumerate_active_capture_devices()
            choices: Dict[str, Dict[str, str]] = {
                MIC_TARGET_DEFAULT_LABEL: {"id": "", "name": MIC_TARGET_DEFAULT_LABEL}
            }
            selected_label = MIC_TARGET_DEFAULT_LABEL
            for device in devices:
                label = device["name"]
                if label in choices:
                    short_id = endpoint_registry_id(device["id"]).strip("{}")[:8]
                    label = f"{label} · {short_id}"
                choices[label] = device
                if selected_id and device["id"].casefold() == selected_id.casefold():
                    selected_label = label
            if selected_id and selected_label == MIC_TARGET_DEFAULT_LABEL:
                selected_label = f"{selected_name}（当前未连接）"
                choices[selected_label] = {"id": selected_id, "name": selected_name}
            self.mic_target_choices = choices
            self.mic_target_combo.configure(values=list(choices))
            self.mic_target_var.set(selected_label)
            if show_status:
                self.status_callback(f"已刷新麦克风列表，共 {len(devices)} 个可用设备")
        except Exception as exc:
            messagebox.showerror("刷新失败", f"无法读取麦克风设备：{exc}")

    def apply_mic_target(self) -> None:
        try:
            label = self.mic_target_var.get()
            target = self.mic_target_choices.get(label)
            if target is None:
                raise ValueError("请先从列表中选择麦克风")
            endpoint_id, device_name = save_mic_target_settings(target["id"], target["name"])
            suffix = "（约 1 秒内生效）" if self._running_mic_processes() else "（启动后生效）"
            if endpoint_id:
                self.status_callback(f"快捷键目标已设置为：{device_name}{suffix}")
            else:
                self.status_callback(f"快捷键目标已设置为：{MIC_TARGET_DEFAULT_LABEL}{suffix}")
        except Exception as exc:
            messagebox.showerror("设置失败", f"无法保存麦克风目标：{exc}")

    def apply_mic_hotkey(self) -> None:
        try:
            enabled, canonical, _codes = save_mic_hotkey_settings(
                self.mic_hotkey_enabled_var.get(),
                self.mic_hotkey_var.get(),
            )
            self.mic_hotkey_var.set(canonical)
            state = "已启用" if enabled else "已关闭"
            suffix = "（约 1 秒内生效）" if self._running_mic_processes() else "（启动后生效）"
            self.status_callback(f"麦克风快捷键 {canonical} {state}{suffix}")
        except Exception as exc:
            messagebox.showerror("设置失败", f"无法保存麦克风快捷键：{exc}")

    def record_mic_hotkey(self) -> None:
        if getattr(self, "_mic_hotkey_record_dialog", None):
            try:
                self._mic_hotkey_record_dialog.lift()
                return
            except tk.TclError:
                self._mic_hotkey_record_dialog = None

        dialog = tk.Toplevel(self.parent)
        self._mic_hotkey_record_dialog = dialog
        dialog.title("录制麦克风快捷键")
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)
        dialog.transient(self.parent.winfo_toplevel())
        content = ttk.Frame(dialog, padding=scaled(18, self.scale))
        content.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            content,
            text="先松开当前按键，再按下新的快捷键组合。",
            font=("Microsoft YaHei UI", scaled(13, self.scale), "bold"),
        ).pack()
        preview = ttk.Label(content, text="请松开鼠标和键盘…", foreground=THEME["accent"])
        preview.pack(pady=(scaled(12, self.scale), 0))

        armed = False
        stable_codes: List[int] = []
        stable_count = 0

        def close_dialog() -> None:
            self._mic_hotkey_record_dialog = None
            dialog.destroy()

        def tick() -> None:
            nonlocal armed, stable_codes, stable_count
            if not dialog.winfo_exists():
                return
            codes = [code for code in MIC_HOTKEY_SCAN_ORDER if win32api.GetAsyncKeyState(code) & 0x8000]
            if not armed:
                if not codes:
                    armed = True
                    preview.configure(text="等待输入…")
                dialog.after(35, tick)
                return
            if codes:
                canonical = format_mic_hotkey(codes)
                preview.configure(text=canonical)
                if codes == stable_codes:
                    stable_count += 1
                else:
                    stable_codes = codes
                    stable_count = 0
                if stable_count >= 8:
                    self.mic_hotkey_var.set(canonical)
                    close_dialog()
                    return
            else:
                stable_codes = []
                stable_count = 0
            dialog.after(35, tick)

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        tick()

    def open_mic_toggle(self) -> None:
        if self._running_mic_processes():
            self.status_callback("麦克风悬浮窗已在运行")
            return
        if getattr(sys, "frozen", False):
            app_dir = Path(sys.executable).resolve().parent
        else:
            app_dir = Path(__file__).resolve().parent
        executable = next((path for path in self._mic_toggle_candidates(app_dir) if path.exists()), None)
        if executable is None:
            messagebox.showerror("启动失败", "找不到一键麦克风开关.exe，请先生成麦克风悬浮窗工具。")
            return
        try:
            os.startfile(executable)
            self.status_callback("已启动麦克风悬浮窗")
        except Exception as exc:
            messagebox.showerror("启动失败", f"无法启动麦克风悬浮窗：{exc}")

    def close_mic_toggle(self) -> None:
        closed, failures = close_mic_toggle_processes()
        if closed <= 0 and not failures:
            self.status_callback("麦克风悬浮窗未在运行")
            return
        if failures:
            pid_args = " ".join(f"/pid {pid}" for pid in failures)
            try:
                result = ctypes.windll.shell32.ShellExecuteW(
                    None,
                    "runas",
                    "taskkill.exe",
                    f"/f {pid_args}",
                    None,
                    0,
                )
                if result <= 32:
                    raise OSError(f"ShellExecuteW failed: {result}")
                self.status_callback(f"已请求管理员权限关闭麦克风悬浮窗（{len(failures)} 个进程）")
                return
            except Exception as exc:
                messagebox.showerror(
                    "关闭失败",
                    "无法关闭麦克风悬浮窗。它可能正在以管理员权限运行，请用管理员身份运行工具箱后再点关闭。\n\n"
                    + "\n".join([f"{pid}：{error}" for pid, error in failures.items()] + [str(exc)]),
                )
                return

        self.status_callback(f"已关闭麦克风悬浮窗（{closed} 个进程）")

    def _mic_toggle_candidates(self, app_dir: Path) -> List[Path]:
        return [
            app_dir / "一键麦克风开关.exe",
            app_dir / "mic_tool_workspace" / "dist" / "一键麦克风开关.exe",
            app_dir / "mic_tool_workspace" / "dist" / "Ò»¼üÂó¿Ë·ç¿ª¹Ø.exe",
            Path.home() / "Documents" / "工具箱" / "mic_tool_workspace" / "dist" / "一键麦克风开关.exe",
            Path.home() / "Documents" / "工具箱" / "mic_tool_workspace" / "dist" / "Ò»¼üÂó¿Ë·ç¿ª¹Ø.exe",
        ]

    def _running_mic_processes(self) -> List[Tuple[int, str, str]]:
        known_paths: Set[str] = set()
        if getattr(sys, "frozen", False):
            app_dir = Path(sys.executable).resolve().parent
        else:
            app_dir = Path(__file__).resolve().parent
        for candidate in self._mic_toggle_candidates(app_dir):
            try:
                known_paths.add(str(candidate.resolve()).lower())
            except Exception:
                known_paths.add(str(candidate).lower())

        matches: List[Tuple[int, str, str]] = []
        current_pid = os.getpid()
        try:
            for pid in win32process.EnumProcesses():
                if not pid or pid == current_pid:
                    continue
                handle = None
                try:
                    handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                    path = win32process.GetModuleFileNameEx(handle, 0)
                    name = Path(path).name
                    normalized_path = str(Path(path).resolve()).lower()
                    if name.lower() in {item.lower() for item in self.MIC_TOGGLE_NAMES} or normalized_path in known_paths:
                        matches.append((pid, name, path))
                except Exception:
                    pass
                finally:
                    if handle:
                        try:
                            win32api.CloseHandle(handle)
                        except Exception:
                            pass
        except Exception:
            return []
        return matches

    def _running_processes(self) -> List[str]:
        try:
            import win32process

            names = []
            for pid in win32process.EnumProcesses():
                try:
                    handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                    path = win32process.GetModuleFileNameEx(handle, 0)
                    names.append(Path(path).name)
                    win32api.CloseHandle(handle)
                except Exception:
                    pass
            return names
        except Exception:
            return []


class StartupTab:
    def __init__(self, parent: tk.Widget, config: Dict, save_callback: Callable[[], None], status_callback: Callable[[str], None]):
        self.parent = parent
        self.config = config
        self.save_callback = save_callback
        self.status_callback = status_callback
        self.scale = current_scale(parent.winfo_toplevel())
        stored_startup = self.config.get("startup")
        startup_cfg = stored_startup if isinstance(stored_startup, dict) else {}
        self.startup_cfg = startup_cfg
        if "autostart" not in startup_cfg:
            startup_cfg["autostart"] = is_autostart_enabled()

        frame = ttk.Frame(parent, padding=scaled(18, self.scale))
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="自启动设置", font=("Microsoft YaHei UI", scaled(18, self.scale), "bold")).pack(anchor=tk.W)

        self.autostart_var = tk.BooleanVar(value=bool(startup_cfg.get("autostart", False)))
        ttk.Checkbutton(
            frame,
            text="开机启动工具箱（启动后最小化到托盘）",
            variable=self.autostart_var,
            command=self.toggle_autostart,
        ).pack(anchor=tk.W, pady=(scaled(16, self.scale), scaled(8, self.scale)))

        self.state_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.state_var).pack(anchor=tk.W, pady=(scaled(8, self.scale), 0))
        ttk.Button(frame, text="刷新当前状态", command=self.refresh_state).pack(anchor=tk.W, pady=(scaled(16, self.scale), 0), ipadx=scaled(16, self.scale), ipady=scaled(6, self.scale))
        self.refresh_state()

    def toggle_autostart(self) -> None:
        enabled = bool(self.autostart_var.get())
        try:
            set_autostart(enabled)
        except OSError as exc:
            self.autostart_var.set(not enabled)
            messagebox.showerror("启动项失败", str(exc))
            return
        self.startup_cfg["autostart"] = enabled
        self.config["startup"] = self.startup_cfg
        try:
            self.save_callback()
        except (OSError, ValueError) as exc:
            self.refresh_state()
            messagebox.showerror("保存配置失败", f"自启动状态已更新，但本地配置未能保存：{exc}", parent=self.parent)
            return
        self.refresh_state()
        self.status_callback("开机自启已开启" if enabled else "开机自启已关闭")

    def refresh_state(self) -> None:
        enabled = is_autostart_enabled()
        self.autostart_var.set(enabled)
        self.startup_cfg["autostart"] = enabled
        self.state_var.set("当前状态：已开启开机自启" if enabled else "当前状态：未开启开机自启")


class LauncherTab:
    def __init__(self, parent: tk.Widget, status_callback: Callable[[str], None]):
        self.parent = parent
        self.status_callback = status_callback
        self._close_lock = threading.Lock()
        self._close_stopped = False
        self.config_file = APP_DIR / "launcher.json"
        self.groups: Dict[str, Dict[str, List[Dict[str, str]]]] = {}
        self.current_group: Optional[str] = None
        self.scale = current_scale(parent.winfo_toplevel())
        self._build()
        self.load_config()
        if not self.groups:
            self.groups = {"默认组": {"apps": []}}
            self.current_group = "默认组"
        self.refresh_groups()
        self.refresh_apps()

    def _build(self) -> None:
        root = ttk.Frame(self.parent, padding=scaled(12, self.scale))
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        side = ttk.Frame(root, width=scaled(190, self.scale))
        side.grid(row=0, column=0, sticky=tk.NS, padx=(0, scaled(12, self.scale)))
        side.grid_propagate(False)
        ttk.Button(side, text="新建分组", command=self.create_group).pack(fill=tk.X, pady=2)
        ttk.Button(side, text="重命名分组", command=self.rename_group).pack(fill=tk.X, pady=2)
        ttk.Button(side, text="删除分组", command=self.delete_group).pack(fill=tk.X, pady=2)
        self.group_list = tk.Listbox(side, height=18, activestyle="none")
        self.group_list.pack(fill=tk.BOTH, expand=True, pady=(scaled(10, self.scale), 0))
        self.group_list.bind("<<ListboxSelect>>", lambda _event: self.select_group())

        main = ttk.Frame(root)
        main.grid(row=0, column=1, sticky=tk.NSEW)
        main.rowconfigure(1, weight=1)
        actions = ttk.Frame(main)
        actions.grid(row=0, column=0, sticky=tk.EW, pady=(0, scaled(8, self.scale)))
        for index, (title, command) in enumerate((
            ("添加应用/文件", self.add_apps), ("删除选中", self.remove_selected),
            ("启动选中", self.launch_selected), ("启动当前组", self.launch_current_group),
            ("关闭当前组", self.close_current_group), ("修复失效路径", self.repair_current_group),
        )):
            actions.columnconfigure(index % 3, weight=1, uniform="launcher_actions")
            ttk.Button(actions, text=title, command=command).grid(
                row=index // 3, column=index % 3, sticky=tk.EW,
                padx=(0, scaled(8, self.scale)), pady=scaled(3, self.scale))

        columns = ("name", "type", "path")
        self.app_tree = ttk.Treeview(main, columns=columns, show="headings", selectmode="extended")
        for column, text, width in [("name", "名称", 160), ("type", "类型", 90), ("path", "路径", 420)]:
            self.app_tree.heading(column, text=text)
            self.app_tree.column(column, width=scaled(width, self.scale))
        self.app_tree.grid(row=1, column=0, sticky=tk.NSEW)
        self.app_tree.bind("<Double-1>", lambda _event: self.launch_selected())
        self.log_text = DarkScrolledText(main, height=6)
        self.log_text.grid(row=2, column=0, sticky=tk.EW, pady=(scaled(8, self.scale), 0))

    def file_type(self, path: str) -> str:
        ext = Path(path).suffix.lower()
        if ext in {".exe", ".bat", ".cmd", ".py", ".ps1", ".lnk"}:
            return "可执行/快捷方式"
        if ext in {".url", ".webloc"}:
            return "网址"
        if ext in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}:
            return "图片"
        if ext in {".mp4", ".avi", ".mov", ".mkv"}:
            return "视频"
        return "文件"

    def refresh_groups(self) -> None:
        self.group_list.delete(0, tk.END)
        for name, payload in self.groups.items():
            self.group_list.insert(tk.END, f"{name} ({len(payload.get('apps', []))})")
        names = list(self.groups)
        if self.current_group in names:
            self.group_list.selection_set(names.index(self.current_group))

    def refresh_apps(self) -> None:
        self.app_tree.delete(*self.app_tree.get_children())
        if not self.current_group:
            return
        for app in self.groups.get(self.current_group, {}).get("apps", []):
            self.app_tree.insert("", tk.END, values=(app["name"], app["type"], app["path"]))

    def select_group(self) -> None:
        selection = self.group_list.curselection()
        if not selection:
            return
        self.current_group = list(self.groups)[selection[0]]
        self.refresh_apps()

    def create_group(self) -> None:
        name = simpledialog.askstring("新建分组", "分组名称：", parent=self.parent)
        if not name:
            return
        name = name.strip()
        if not name or name in self.groups:
            return
        self.groups[name] = {"apps": []}
        self.current_group = name
        self.save_config()
        self.refresh_groups()
        self.refresh_apps()

    def rename_group(self) -> None:
        if not self.current_group:
            return
        name = simpledialog.askstring("重命名分组", "新的分组名称：", initialvalue=self.current_group, parent=self.parent)
        name = name.strip() if name else ""
        if not name or name in self.groups:
            return
        self.groups[name] = self.groups.pop(self.current_group)
        self.current_group = name
        self.save_config()
        self.refresh_groups()

    def delete_group(self) -> None:
        if not self.current_group or not messagebox.askyesno("确认删除", f"删除分组 {self.current_group}？"):
            return
        self.groups.pop(self.current_group, None)
        self.current_group = next(iter(self.groups), None)
        self.save_config()
        self.refresh_groups()
        self.refresh_apps()

    def add_apps(self) -> None:
        if not self.current_group:
            self.create_group()
        if not self.current_group:
            return
        paths = filedialog.askopenfilenames(title="选择应用或文件", filetypes=[("所有文件", "*.*")])
        for path in paths:
            self.groups[self.current_group]["apps"].append({"name": Path(path).name, "path": path, "type": self.file_type(path)})
        if paths:
            self.save_config()
            self.refresh_groups()
            self.refresh_apps()
            self.log(f"已添加 {len(paths)} 个项目")

    def remove_selected(self) -> None:
        if not self.current_group:
            return
        indices = {self.app_tree.index(item) for item in self.app_tree.selection()}
        self.groups[self.current_group]["apps"] = [
            app for index, app in enumerate(self.groups[self.current_group]["apps"]) if index not in indices
        ]
        self.save_config()
        self.refresh_groups()
        self.refresh_apps()

    def launch_current_group(self) -> None:
        if self.current_group:
            self.launch_apps(self.groups[self.current_group]["apps"])

    def launch_selected(self) -> None:
        selected_paths = {self.app_tree.item(item, "values")[2] for item in self.app_tree.selection()}
        if self.current_group:
            selected_apps = [app for app in self.groups[self.current_group]["apps"] if app.get("path") in selected_paths]
            self.launch_apps(selected_apps)

    def launch_apps(self, apps: List[Dict[str, str]]) -> None:
        self._start_launcher_operation(self._launch_worker, apps)

    def _start_launcher_operation(self, operation, apps) -> None:
        if getattr(self, "_close_stopped", False):
            return
        if getattr(self, "_launcher_close_pending", False):
            self.log("关闭请求正在等待或执行，请完成后再启动或修复应用。")
            return
        if getattr(self, "_launcher_busy", False):
            return
        cancel = self._launcher_cancel = threading.Event()
        idle = self._launcher_idle = threading.Event()
        self._launcher_busy = True
        selected_apps = list(apps)

        def worker():
            try:
                if not cancel.is_set() and not getattr(self, "_close_stopped", False):
                    operation(selected_apps)
            except Exception as exc:
                if not getattr(self, "_close_stopped", False):
                    try:
                        self.parent.after(0, lambda error=str(exc): self.log(f"应用操作失败：{error}"))
                    except (RuntimeError, tk.TclError):
                        pass
            finally:
                self._launcher_busy = False
                idle.set()

        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception as exc:
            self._launcher_busy = False
            idle.set()
            self.log(f"应用操作启动失败：{exc}")

    def _launcher_cancelled(self) -> bool:
        cancel = getattr(self, "_launcher_cancel", None)
        return getattr(self, "_close_stopped", False) or (cancel is not None and cancel.is_set())

    def _queue_launcher_close(self, work) -> None:
        if getattr(self, "_close_stopped", False):
            return
        if getattr(self, "_launcher_close_pending", False):
            self.log("关闭请求已在等待或执行，请稍候。")
            return
        close_lock = getattr(self, "_close_lock", None)
        if close_lock is None:
            close_lock = self._close_lock = threading.Lock()
        if not close_lock.acquire(blocking=False):
            self.log("关闭请求已在执行，请稍候。")
            return
        self._launcher_close_pending = True
        cancel = getattr(self, "_launcher_cancel", None)
        if cancel is not None:
            cancel.set()
        idle = getattr(self, "_launcher_idle", None)
        if idle is not None and not idle.is_set():
            self.log("已取消后续启动/修复项，正在等待当前操作返回后关闭应用。")

        def close_after_current_operation():
            try:
                while idle is not None and not idle.is_set():
                    if getattr(self, "_close_stopped", False):
                        return
                    idle.wait(0.05)
                if not getattr(self, "_close_stopped", False):
                    work()
            except Exception as exc:
                if not getattr(self, "_close_stopped", False):
                    try:
                        self.parent.after(0, lambda error=str(exc): self.log(f"关闭失败：{error}"))
                    except (RuntimeError, tk.TclError):
                        pass
            finally:
                close_lock.release()
                self._launcher_close_pending = False

        try:
            threading.Thread(target=close_after_current_operation, daemon=True).start()
        except Exception as exc:
            close_lock.release()
            self._launcher_close_pending = False
            self.log(f"关闭请求启动失败：{exc}")

    def repair_current_group(self) -> None:
        if not self.current_group:
            return
        apps = self.groups.get(self.current_group, {}).get("apps", [])
        self._start_launcher_operation(self._repair_worker, apps)

    def close_current_group(self) -> None:
        if self.current_group:
            apps = list(self.groups[self.current_group]["apps"])
            self._queue_launcher_close(lambda: self._close_worker(launcher_close_paths(apps)))

    def close_paths(self, paths: List[str]) -> None:
        selected = list(paths)
        self._queue_launcher_close(lambda: self._close_worker(selected))

    def stop(self) -> None:
        self._close_stopped = True
        cancel = getattr(self, "_launcher_cancel", None)
        if cancel is not None:
            cancel.set()

    def _launch_worker(self, apps: List[Dict[str, str]]) -> None:
        repaired_any = False
        for app in apps:
            if self._launcher_cancelled():
                break
            path = app.get("path", "")
            try:
                repaired_path = self._ensure_launch_path(app, max_seconds=5)
                if repaired_path and repaired_path != path:
                    path = repaired_path
                    repaired_any = True
                if self._launcher_cancelled():
                    break
                if Path(path).suffix.lower() == ".url":
                    raw = Path(path).read_bytes()
                    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
                    try:
                        content = raw.decode(encoding)
                    except UnicodeDecodeError:
                        content = raw.decode("mbcs")
                    urls = [line.split("=", 1)[1].strip() for line in content.splitlines() if line.strip().casefold().startswith("url=")]
                    if not urls or not urls[0]:
                        raise ValueError("网址快捷方式缺少有效 URL")
                    if not webbrowser.open(urls[0]):
                        raise RuntimeError("浏览器未接受打开请求")
                else:
                    os.startfile(path)
                self.parent.after(0, lambda p=path: self.log(f"已启动：{Path(p).name}"))
            except Exception as exc:
                self.parent.after(0, lambda p=path, e=exc: self.log(f"启动失败：{Path(p).name} - {e}"))

        if repaired_any and not getattr(self, "_close_stopped", False):
            self.parent.after(0, self.save_config)
            self.parent.after(0, self.refresh_apps)

    def _repair_worker(self, apps: List[Dict[str, str]]) -> None:
        repaired = 0
        missing = 0
        for app in apps:
            if self._launcher_cancelled():
                break
            old_path = app.get("path", "")
            if old_path and Path(old_path).exists():
                continue
            found = self._find_relocated_path(app, max_seconds=LAUNCHER_REPAIR_MAX_SECONDS)
            if self._launcher_cancelled():
                break
            if found:
                app["path"] = found
                app["type"] = self.file_type(found)
                repaired += 1
                self.parent.after(0, lambda n=app.get("name", Path(found).name), p=found: self.log(f"已修复路径：{n} -> {p}"))
            else:
                missing += 1
                self.parent.after(0, lambda n=app.get("name", Path(old_path).name): self.log(f"未找到新位置：{n}"))
        if getattr(self, "_close_stopped", False):
            return
        if repaired:
            self.parent.after(0, self.save_config)
            self.parent.after(0, self.refresh_apps)
        outcome = "已停止" if self._launcher_cancelled() else "完成"
        self.parent.after(0, lambda: self.log(f"路径修复{outcome}：修复 {repaired} 个，未找到 {missing} 个"))

    def _ensure_launch_path(self, app: Dict[str, str], max_seconds: int = 5) -> str:
        path = app.get("path", "")
        if path and Path(path).exists():
            return path
        found = self._find_relocated_path(app, max_seconds=max_seconds)
        if not found or self._launcher_cancelled():
            return path
        old_path = path
        app["path"] = found
        app["type"] = self.file_type(found)
        self.parent.after(0, lambda n=app.get("name", Path(found).name), old=old_path, new=found: self.log(f"启动前已自动修复路径：{n} -> {new}"))
        return found

    def _find_relocated_path(self, app: Dict[str, str], max_seconds: int = LAUNCHER_REPAIR_MAX_SECONDS) -> Optional[str]:
        old_path = app.get("path", "")
        target_name = (app.get("name") or Path(old_path).name).lower()
        if not target_name:
            return None

        direct_candidates: List[Path] = []
        if old_path:
            direct_candidates.append(Path(old_path))
            if Path(old_path).suffix.lower() == ".lnk":
                try:
                    direct_candidates.append(Path(resolve_shortcut_target(old_path)))
                except Exception:
                    pass
        close_paths = app.get("close_paths", [])
        if isinstance(close_paths, list):
            direct_candidates.extend(Path(str(path)) for path in close_paths if path)

        for candidate in direct_candidates:
            try:
                if candidate.exists() and candidate.name.lower() == target_name:
                    return str(candidate)
            except Exception:
                pass

        search_roots = self._launcher_search_roots(old_path)
        deadline = time.monotonic() + max_seconds
        matches: List[Path] = []
        for root in search_roots:
            if time.monotonic() > deadline:
                break
            matches.extend(self._find_by_name(root, target_name, deadline, limit=8))
            if matches:
                break

        if not matches:
            return None
        return str(self._choose_best_repair_match(matches, old_path))

    def _launcher_search_roots(self, old_path: str) -> List[Path]:
        roots: List[Path] = []

        def add(path: Path) -> None:
            try:
                resolved = path.resolve()
            except Exception:
                resolved = path
            if not resolved.exists() or not resolved.is_dir():
                return
            normalized = str(resolved).lower()
            if normalized not in seen:
                seen.add(normalized)
                roots.append(resolved)

        seen: Set[str] = set()
        if old_path:
            old = Path(old_path)
            for parent in list(old.parents)[:3]:
                add(parent)
        for path in [
            Path.home() / "Desktop",
            Path.home() / "Documents",
            Path.home() / "Downloads",
            Path.home() / "Desktop" / "Python",
            Path(__file__).resolve().parent,
        ]:
            add(path)
        if os.name == "nt":
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for index in range(26):
                if bitmask & (1 << index):
                    add(Path(f"{chr(65 + index)}:\\"))
        return roots

    def _find_by_name(self, root: Path, target_name: str, deadline: float, limit: int = 8) -> List[Path]:
        matches: List[Path] = []
        try:
            walker = os.walk(root)
            for current, dirs, files in walker:
                if time.monotonic() > deadline or len(matches) >= limit:
                    break
                dirs[:] = [
                    name for name in dirs
                    if name.lower() not in LAUNCHER_REPAIR_IGNORE_DIRS
                    and not name.startswith(".")
                ]
                for filename in files:
                    if filename.lower() == target_name:
                        candidate = Path(current) / filename
                        try:
                            if candidate.exists():
                                matches.append(candidate)
                                if len(matches) >= limit:
                                    break
                        except Exception:
                            pass
        except Exception:
            return matches
        return matches

    def _choose_best_repair_match(self, matches: List[Path], old_path: str) -> Path:
        old = Path(old_path) if old_path else None

        def score(path: Path) -> Tuple[int, int, float, str]:
            same_drive = 0
            common_parts = 0
            if old:
                try:
                    same_drive = 0 if path.drive.lower() == old.drive.lower() else 1
                    left = [part.lower() for part in path.parts]
                    right = [part.lower() for part in old.parts]
                    common_parts = -sum(1 for a, b in zip(left, right) if a == b)
                except Exception:
                    pass
            try:
                mtime = -path.stat().st_mtime
            except Exception:
                mtime = 0
            return (same_drive, common_parts, mtime, str(path).lower())

        return sorted(matches, key=score)[0]

    def _close_worker(self, paths: List[str]) -> None:
        if getattr(self, "_close_stopped", False):
            return
        resolved, input_errors = resolve_process_close_targets(paths)
        input_error_summary = f"；目标失败 {len(input_errors)} 项；{input_errors[0]}" if input_errors else ""
        for error in input_errors:
            self.parent.after(0, lambda e=error: self.log(f"关闭目标失败：{e}"))
        try:
            targets = [path for path in resolved if Path(path).suffix.lower() in PROCESS_EXECUTABLE_EXTENSIONS]
            if not targets:
                message = f"当前组没有可按进程关闭的应用{input_error_summary}"
                self.parent.after(0, lambda m=message: self.log(m))
                return
            processes = running_process_paths()
            snapshot_notice = process_snapshot_notice(processes)
            matches = match_processes_for_paths(targets, processes, include_directory_scope=False)
        except Exception as exc:
            message = f"关闭失败：{exc}{input_error_summary}"
            self.parent.after(0, lambda m=message: self.log(m))
            return
        if not matches:
            message = f"当前组没有找到可查询的运行中应用{input_error_summary}{snapshot_notice}"
            self.parent.after(0, lambda m=message: self.log(m))
            return
        pid_to_path = {pid: process_path for pid, process_path in matches}
        if getattr(self, "_close_stopped", False):
            return
        try:
            closed, failures = terminate_processes_fast(pid_to_path, include_children=False, expected_paths=pid_to_path)
        except Exception as exc:
            message = f"关闭失败：{exc}{input_error_summary}"
            self.parent.after(0, lambda m=message: self.log(m))
            return
        if getattr(self, "_close_stopped", False):
            return
        for pid in sorted(closed):
            process_path = pid_to_path.get(pid, str(pid))
            self.parent.after(0, lambda p=process_path, i=pid: self.log(f"已确认进程退出：{Path(p).name}（PID {i}）"))
        for pid, error in failures.items():
            process_path = pid_to_path.get(pid, str(pid))
            self.parent.after(0, lambda p=process_path, e=error: self.log(f"关闭失败：{Path(p).name} - {e}"))
        residual_message = ""
        try:
            remaining, final_notice, cancelled = observe_closed_process_targets(
                targets, should_stop=lambda: getattr(self, "_close_stopped", False)
            )
            if cancelled:
                return
            if remaining:
                details = "、".join(f"{Path(path).name}（PID {pid}）" for pid, path in remaining)
                message = f"关闭后观察期内仍有相关进程运行：{details}"
                if any(pid in closed or pid not in pid_to_path for pid, _path in remaining):
                    message += "；可能被重新启动"
                message += final_notice
                residual_message = message
                self.parent.after(0, lambda m=message: self.log(m))
            else:
                residual_message = f"{PROCESS_CLOSE_OBSERVATION_SECONDS:g} 秒观察期内未发现可查询的残留进程{final_notice}"
                self.parent.after(0, lambda m=residual_message: self.log(m))
        except Exception as exc:
            message = f"关闭后复查失败：{exc}"
            residual_message = message
            self.parent.after(0, lambda m=message: self.log(m))
        if input_errors:
            message = f"关闭完成：已确认退出 {len(closed)} 个，进程失败 {len(failures)} 个{input_error_summary}"
            if residual_message:
                message += f"；{residual_message}"
            self.parent.after(0, lambda m=message: self.log(m))

    def log(self, message: str) -> None:
        self.log_text.insert(tk.END, f"[{datetime.now():%H:%M:%S}] {message}\n")
        self.log_text.see(tk.END)
        self.status_callback(message)

    @staticmethod
    def _validate_config(payload: object) -> Dict:
        if not isinstance(payload, dict) or not isinstance(payload.get("groups", {}), dict):
            raise ValueError("启动器配置必须包含合法的分组对象")
        for name, group in payload.get("groups", {}).items():
            if not isinstance(name, str) or not name.strip() or not isinstance(group, dict):
                raise ValueError("启动器分组名称或内容无效")
            if not isinstance(group.get("apps", []), list):
                raise ValueError(f"分组 {name} 的应用列表无效")
            for app in group.get("apps", []):
                if not isinstance(app, dict) or not isinstance(app.get("path"), str) or not app["path"].strip():
                    raise ValueError(f"分组 {name} 含有无效的应用路径")
        return payload

    def save_config(self) -> bool:
        temporary = self.config_file.with_name(f"{self.config_file.name}.{uuid.uuid4().hex}.tmp")
        try:
            current = self.config_file.read_bytes() if self.config_file.exists() else None
            if getattr(self, "_config_read_error", None):
                raise ValueError("原启动器配置无法读取，已保留原文件；请修复后重新打开工具箱。")
            if hasattr(self, "_loaded_config_bytes") and current != self._loaded_config_bytes:
                raise ValueError("启动器配置已被其他程序修改，请重新打开工具箱后再保存。")
            existing = self._validate_config(json.loads(current.decode("utf-8-sig"))) if current is not None else {}
            payload = {**existing, "groups": self.groups, "current_group": self.current_group}
            self._validate_config(payload)
            encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(encoded)
            os.replace(temporary, self.config_file)
            self._loaded_config_bytes = encoded
            return True
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("保存启动器配置失败", str(exc), parent=self.parent)
            return False
        finally:
            if temporary.exists():
                temporary.unlink()

    def load_config(self) -> None:
        self._config_read_error = None
        self._loaded_config_bytes = None
        try:
            if not self.config_file.exists():
                return
            self._loaded_config_bytes = self.config_file.read_bytes()
            payload = self._validate_config(json.loads(self._loaded_config_bytes.decode("utf-8-sig")))
            self.groups = payload.get("groups", {})
            for group in self.groups.values():
                group.setdefault("apps", [])
                for app in group["apps"]:
                    if not isinstance(app.get("name"), str) or not app["name"]:
                        app["name"] = Path(app["path"]).name
                    if not isinstance(app.get("type"), str):
                        app["type"] = self.file_type(app["path"])
            current = payload.get("current_group")
            self.current_group = current if isinstance(current, str) and current in self.groups else next(iter(self.groups), None)
        except (OSError, ValueError, TypeError) as exc:
            self._config_read_error = str(exc)
            self.groups = {}
            self.current_group = None
            messagebox.showerror("读取启动器配置失败", f"{exc}\n原配置已保留，本次不会覆盖。", parent=self.parent)


class MediaOrganizerTab:
    def __init__(self, parent: tk.Widget, status_callback: Callable[[str], None]):
        self.parent = parent
        self.status_callback = status_callback
        self.scale = current_scale(parent.winfo_toplevel())
        self.manifest = None
        self.busy = False
        self._build()

    def _build(self) -> None:
        frame = ttk.Frame(self.parent, padding=scaled(14, self.scale))
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="源目录").grid(row=0, column=0, sticky=tk.W, pady=scaled(4, self.scale))
        self.source_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.source_var).grid(row=0, column=1, sticky=tk.EW, padx=scaled(8, self.scale), pady=scaled(4, self.scale))
        ttk.Button(frame, text="选择", command=lambda: self.pick_dir(self.source_var)).grid(row=0, column=2)
        ttk.Label(frame, text="输出目录").grid(row=1, column=0, sticky=tk.W, pady=scaled(4, self.scale))
        self.output_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.output_var).grid(row=1, column=1, sticky=tk.EW, padx=scaled(8, self.scale), pady=scaled(4, self.scale))
        ttk.Button(frame, text="选择", command=lambda: self.pick_dir(self.output_var)).grid(row=1, column=2)
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=scaled(8, self.scale))
        self.scan_button = ttk.Button(buttons, text="扫描预览", command=self.scan)
        self.scan_button.pack(side=tk.LEFT, padx=(0, scaled(8, self.scale)))
        self.execute_button = ttk.Button(buttons, text="确认执行", command=self.execute, state=tk.DISABLED)
        self.execute_button.pack(side=tk.LEFT)
        self.summary_var = tk.StringVar(value="等待扫描")
        ttk.Label(frame, textvariable=self.summary_var).grid(row=3, column=0, columnspan=3, sticky=tk.EW)
        self.log_text = DarkScrolledText(frame, height=20)
        self.log_text.grid(row=4, column=0, columnspan=3, sticky=tk.NSEW, pady=(scaled(8, self.scale), 0))
        frame.rowconfigure(4, weight=1)

    def pick_dir(self, variable: tk.StringVar) -> None:
        selected = filedialog.askdirectory()
        if selected:
            variable.set(selected)

    def scan(self) -> None:
        if self.busy:
            return
        if not all([AppConfig, scan_media, save_manifest, write_json]):
            messagebox.showerror("缺少依赖", "照片视频管家模块不可用。")
            return
        source_dir, output_dir = self.source_var.get(), self.output_var.get()
        if not source_dir or not output_dir:
            messagebox.showwarning("缺少目录", "请先选择源目录和输出目录。")
            return
        config = AppConfig(source_dir=source_dir, output_dir=output_dir)
        self.manifest = None
        self._set_busy(True)
        self.summary_var.set("正在扫描...")
        try:
            threading.Thread(target=self._scan_worker, args=(config,), daemon=True).start()
        except Exception as exc:
            self._task_failed("扫描失败", str(exc))

    def _scan_worker(self, config) -> None:
        try:
            output = Path(config.output_dir)
            manifest = scan_media(config)
            output.mkdir(parents=True, exist_ok=True)
            write_json(output / "config.json", manifest.to_dict()["config"])
            save_manifest(manifest, output)
            self.parent.after(0, lambda: self._scan_done(manifest))
        except Exception as exc:
            self.parent.after(0, lambda error=str(exc): self._task_failed("扫描失败", error))

    def _scan_done(self, manifest) -> None:
        self.manifest = manifest
        unknown = sum(1 for item in manifest.items if item.date == "Unknown-Date")
        located = sum(1 for item in manifest.items if item.gps_lat is not None and item.gps_lon is not None)
        duplicate_groups = len(manifest.exact_duplicates) + len(manifest.similar_duplicates)
        video_size = sum(item.size_bytes for item in manifest.items if item.media_type == "video") / 1024 / 1024
        self.summary_var.set(f"预览完成：文件 {len(manifest.items)}，未知日期 {unknown}，有地点 {located}，重复组 {duplicate_groups}，视频 {video_size:.1f} MB")
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, f"scan_manifest.json 已生成到 {manifest.config.output_dir}\n")
        warnings = Counter(error for item in manifest.items for error in item.errors)
        for error, count in warnings.most_common():
            self.log_text.insert(tk.END, f"{error}：{count} 个\n")
        self._set_busy(False)
        self.status_callback("照片视频扫描完成")

    def execute(self) -> None:
        if self.busy or not self.manifest or not execute_plan:
            return
        manifest = self.manifest
        self._set_busy(True)
        self.summary_var.set("正在执行复制和报告生成...")
        try:
            threading.Thread(target=self._execute_worker, args=(manifest,), daemon=True).start()
        except Exception as exc:
            self._task_failed("执行失败", str(exc))

    def _execute_worker(self, manifest) -> None:
        try:
            result = execute_plan(manifest, lambda message: self.parent.after(0, lambda m=message: self.log_text.insert(tk.END, m + "\n")))
            self.parent.after(0, lambda: self._execute_done(result))
        except Exception as exc:
            self.parent.after(0, lambda error=str(exc): self._task_failed("执行失败", error))

    def _execute_done(self, result: Dict) -> None:
        self.manifest = None
        self._set_busy(False)
        self.summary_var.set(f"执行完成：复制 {len(result['copied'])}，压缩成功 {len(result['compressed'])}，跳过/失败 {len(result['skipped'])}，复制失败 {len(result['failed'])}")
        self.log_text.insert(tk.END, f"地点 CSV：{result.get('locations_report_csv')}\n")
        self.status_callback("照片视频整理完成")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.scan_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.execute_button.configure(state=tk.NORMAL if not busy and self.manifest is not None else tk.DISABLED)

    def _task_failed(self, title: str, error: str) -> None:
        self._set_busy(False)
        self.summary_var.set(f"{title}：{error}")
        self.status_callback(title)
        messagebox.showerror(title, error)



class MacroToolsTab:
    def __init__(self, parent: ttk.Frame, status_callback: Callable[[str], None]):
        self.parent = parent
        self.status_callback = status_callback
        self.macro_json_var = tk.StringVar(value=str(macro_recording_path()))
        self.loop_hotkey_var = tk.StringVar(value="mouse:x1")
        self.loop_interval_var = tk.StringVar(value="0.1")
        self._build()

    def _build(self) -> None:
        outer = ttk.Frame(self.parent, padding=18)
        outer.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(outer, text="\u9f20\u6807\u952e\u76d8\u5b8f\u5de5\u5177", font=("Microsoft YaHei UI", 20, "bold"))
        title.pack(anchor=tk.W, pady=(0, 8))
        desc = ttk.Label(
            outer,
            text="\u5f55\u5236\u9f20\u6807\u548c\u952e\u76d8\u64cd\u4f5c\uff0c\u5bfc\u51fa JSON\uff1b\u4f7f\u7528 Interception \u5faa\u73af\u56de\u653e\u3002\u542f\u52a8/\u6682\u505c\u70ed\u952e\u548c\u5faa\u73af\u95f4\u9694\u90fd\u53ef\u81ea\u5b9a\u4e49\u3002",
            wraplength=980,
        )
        desc.pack(anchor=tk.W, fill=tk.X, pady=(0, 16))

        path_box = ttk.LabelFrame(outer, text="\u5b8f\u6587\u4ef6", padding=12)
        path_box.pack(fill=tk.X, pady=(0, 14))
        path_box.columnconfigure(0, weight=1)
        ttk.Entry(path_box, textvariable=self.macro_json_var).grid(row=0, column=0, sticky=tk.EW, padx=(0, 8))
        ttk.Button(path_box, text="\u9009\u62e9 JSON", command=self.choose_macro_json).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(path_box, text="\u6253\u5f00\u6240\u5728\u76ee\u5f55", command=self.open_macro_json_folder).grid(row=0, column=2)

        settings_box = ttk.LabelFrame(outer, text="\u56de\u653e\u8bbe\u7f6e", padding=12)
        settings_box.pack(fill=tk.X, pady=(0, 14))
        settings_box.columnconfigure(1, weight=1)
        settings_box.columnconfigure(3, weight=1)
        ttk.Label(settings_box, text="\u542f\u52a8/\u6682\u505c\u70ed\u952e\uff1a").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        ttk.Entry(settings_box, textvariable=self.loop_hotkey_var, width=24).grid(row=0, column=1, sticky=tk.EW, padx=(0, 18))
        ttk.Label(settings_box, text="\u5faa\u73af\u95f4\u9694\uff08\u79d2\uff09\uff1a").grid(row=0, column=2, sticky=tk.W, padx=(0, 8))
        ttk.Entry(settings_box, textvariable=self.loop_interval_var, width=12).grid(row=0, column=3, sticky=tk.EW)
        ttk.Label(settings_box, text="\u793a\u4f8b\uff1amouse:x1\u3001mouse:x2\u3001<ctrl>+<shift>+r\uff1b\u95f4\u9694\u53ef\u586b 0\u30010.1\u30011.5", foreground=THEME["muted"]).grid(row=1, column=0, columnspan=4, sticky=tk.W, pady=(8, 0))

        actions = ttk.LabelFrame(outer, text="\u64cd\u4f5c", padding=12)
        actions.pack(fill=tk.X, pady=(0, 14))
        for i in range(3):
            actions.columnconfigure(i, weight=1, uniform="macro_actions")
        ttk.Button(actions, text="\u542f\u52a8\u5b8f\u5f55\u5236\u5668", command=self.launch_recorder).grid(row=0, column=0, sticky=tk.EW, padx=4, pady=4)
        ttk.Button(actions, text="\u542f\u52a8\u5faa\u73af\u56de\u653e", command=self.launch_loop_player).grid(row=0, column=1, sticky=tk.EW, padx=4, pady=4)
        ttk.Button(actions, text="\u5b89\u88c5/\u68c0\u67e5\u4f9d\u8d56", command=self.install_requirements).grid(row=0, column=2, sticky=tk.EW, padx=4, pady=4)
        ttk.Button(actions, text="\u6253\u5f00\u5b8f\u5de5\u5177\u76ee\u5f55", command=self.open_macro_tools_folder).grid(row=1, column=0, sticky=tk.EW, padx=4, pady=4)
        ttk.Button(actions, text="打开默认宏文件", command=self.open_default_macro_json).grid(row=1, column=1, sticky=tk.EW, padx=4, pady=4)
        ttk.Button(actions, text="\u590d\u5236\u4e00\u952e\u542f\u52a8 BAT \u5230\u684c\u9762", command=self.create_desktop_loop_bat).grid(row=1, column=2, sticky=tk.EW, padx=4, pady=4)

        info = ttk.LabelFrame(outer, text="\u5f53\u524d\u89c4\u5219", padding=12)
        info.pack(fill=tk.BOTH, expand=True)
        text = (
            "1. \u5f55\u5236\u5668\u9ed8\u8ba4 Ctrl+Shift+R \u5f00\u59cb/\u505c\u6b62\uff0c\u4e5f\u652f\u6301\u6355\u83b7 mouse:x1 / mouse:x2\u3002\n"
            "2. \u5faa\u73af\u56de\u653e\u5668\u8bfb\u53d6\u4e0a\u65b9 JSON\uff0c\u542f\u52a8/\u6682\u505c\u70ed\u952e\u548c\u5faa\u73af\u95f4\u9694\u53ef\u5728\u754c\u9762\u4e2d\u4fee\u6539\u3002\n"
            "3. \u5faa\u73af\u811a\u672c\u5df2\u53bb\u6389\u5f55\u5236\u5f00\u59cb\u540e\u7684\u524d\u6447\u7b49\u5f85\uff1a\u7b2c\u4e00\u6761\u6709\u6548\u52a8\u4f5c\u4f1a\u7acb\u5373\u6267\u884c\u3002\n"
            "4. \u5982\u679c Interception \u56de\u653e\u65e0\u52a8\u4f5c\uff0c\u901a\u5e38\u662f\u9a71\u52a8\u3001\u6743\u9650\u6216\u8bbe\u5907\u8bc6\u522b\u95ee\u9898\uff1b\u70ed\u952e\u76d1\u542c\u5df2\u4f7f\u7528 pynput\uff0c\u56de\u653e\u4ecd\u4f7f\u7528 Interception\u3002"
        )
        label = ttk.Label(info, text=text, justify=tk.LEFT, wraplength=980)
        label.pack(anchor=tk.W, fill=tk.X)

    def macro_tools_dir(self) -> Path:
        candidates = []
        try:
            candidates.append(Path(sys.executable).resolve().parent / "macro_tools")
        except Exception:
            pass
        try:
            candidates.append(resource_path("macro_tools"))
        except Exception:
            pass
        try:
            candidates.append(Path(__file__).resolve().parent / "macro_tools")
        except Exception:
            pass
        candidates.append(Path.home() / "Documents" / "macro_loop")
        for path in candidates:
            if path.exists():
                return path
        return candidates[0]

    def _python_executable(self, prefer_windowed: bool = False) -> Optional[str]:
        names = ["pythonw.exe", "python.exe"] if prefer_windowed else ["python.exe", "pythonw.exe"]
        current = Path(sys.executable)
        if current.name.lower() in {"python.exe", "pythonw.exe"}:
            return str(current)
        for name in names:
            candidate = current.with_name(name)
            if candidate.exists():
                return str(candidate)
        for name in names:
            found = shutil.which(name) or shutil.which(name.replace(".exe", ""))
            if found:
                return found
        py_launcher = shutil.which("py.exe") or shutil.which("py")
        if py_launcher:
            return py_launcher
        return None

    def _launch_python_script(self, script: Path, args: Optional[List[str]] = None, new_console: bool = False, windowed: bool = False) -> None:
        if not script.exists():
            raise FileNotFoundError(f"找不到脚本：{script}")
        python_exe = self._python_executable(prefer_windowed=windowed)
        if not python_exe:
            raise RuntimeError("找不到 Python，也没有可用的内置 exe。")
        cmd = [python_exe, str(script)] + list(args or [])
        flags = 0
        if os.name == "nt" and new_console:
            flags |= subprocess.CREATE_NEW_CONSOLE
        subprocess.Popen(cmd, cwd=str(script.parent), creationflags=flags)

    def _launch_exe_or_script(self, exe_name: str, script_name: str, args: Optional[List[str]] = None, new_console: bool = False, windowed: bool = False) -> None:
        folder = self.macro_tools_dir()
        exe = folder / exe_name
        if exe.exists():
            flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" and new_console else 0
            subprocess.Popen([str(exe)] + list(args or []), cwd=str(folder), creationflags=flags)
            return
        self._launch_python_script(folder / script_name, args=args, new_console=new_console, windowed=windowed)

    def choose_macro_json(self) -> None:
        path = filedialog.askopenfilename(
            title="\u9009\u62e9\u5b8f JSON",
            filetypes=[("JSON \u6587\u4ef6", "*.json"), ("\u6240\u6709\u6587\u4ef6", "*.*")],
            initialdir=str(Path.home() / "Desktop"),
        )
        if path:
            self.macro_json_var.set(path)

    def launch_recorder(self) -> None:
        try:
            self._launch_exe_or_script("macro_recorder.exe", "macro_recorder.py", windowed=True)
            self.status_callback("\u5df2\u542f\u52a8\u5b8f\u5f55\u5236\u5668")
        except Exception as exc:
            messagebox.showerror("\u542f\u52a8\u5931\u8d25", f"\u65e0\u6cd5\u542f\u52a8\u5b8f\u5f55\u5236\u5668\uff1a{exc}")

    def loop_settings(self) -> Tuple[str, float]:
        hotkey = self.loop_hotkey_var.get().strip() or "mouse:x1"
        try:
            interval = float((self.loop_interval_var.get().strip() or "0.1"))
        except ValueError:
            raise ValueError("\u5faa\u73af\u95f4\u9694\u5fc5\u987b\u662f\u6570\u5b57")
        if not math.isfinite(interval):
            raise ValueError("循环间隔必须是有限数字")
        if interval < 0:
            raise ValueError("\u5faa\u73af\u95f4\u9694\u4e0d\u80fd\u5c0f\u4e8e 0")
        if interval > 3600:
            raise ValueError("\u5faa\u73af\u95f4\u9694\u6700\u5927\u4e0d\u80fd\u8d85\u8fc7 3600 \u79d2")
        return hotkey, interval

    def launch_loop_player(self) -> None:
        macro_json = self.macro_json_var.get().strip() or str(macro_recording_path())
        if not Path(macro_json).exists():
            messagebox.showerror("\u5b8f\u6587\u4ef6\u4e0d\u5b58\u5728", f"\u627e\u4e0d\u5230\u5b8f JSON\uff1a\n{macro_json}")
            return
        try:
            hotkey, interval = self.loop_settings()
            self._launch_exe_or_script(
                "interception_loop_macro.exe",
                "interception_loop_macro.py",
                args=[macro_json, "--hotkey", hotkey, "--interval", str(interval)],
                new_console=True,
            )
            self.status_callback(f"\u5df2\u542f\u52a8 Interception \u5faa\u73af\u56de\u653e\uff1b\u70ed\u952e {hotkey}\uff0c\u95f4\u9694 {interval:g}s")
        except Exception as exc:
            messagebox.showerror("\u542f\u52a8\u5931\u8d25", f"\u65e0\u6cd5\u542f\u52a8\u5faa\u73af\u56de\u653e\uff1a{exc}")

    def install_requirements(self) -> None:
        folder = self.macro_tools_dir()
        req = folder / "requirements.txt"
        if not req.exists():
            messagebox.showerror("\u4f9d\u8d56\u6587\u4ef6\u4e0d\u5b58\u5728", f"\u627e\u4e0d\u5230\uff1a\n{req}")
            return
        python_exe = self._python_executable()
        if not python_exe:
            messagebox.showerror("\u627e\u4e0d\u5230 Python", "\u672a\u627e\u5230 python.exe\u3002\u5185\u7f6e exe \u53ef\u76f4\u63a5\u4f7f\u7528\uff1b\u5982\u9700\u5b89\u88c5\u4f9d\u8d56\uff0c\u8bf7\u5148\u5b89\u88c5 Python \u5e76\u52a0\u5165 PATH\u3002")
            return
        cmd = [python_exe, "-m", "pip", "install", "-r", str(req)]
        flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        subprocess.Popen(cmd, cwd=str(folder), creationflags=flags)
        self.status_callback("\u5df2\u6253\u5f00\u4f9d\u8d56\u5b89\u88c5\u7a97\u53e3")

    def open_macro_tools_folder(self) -> None:
        folder = self.macro_tools_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def open_macro_json_folder(self) -> None:
        path = Path(self.macro_json_var.get().strip())
        folder = path.parent if path.parent.exists() else Path.home() / "Desktop"
        os.startfile(str(folder))

    def open_default_macro_json(self) -> None:
        path = macro_recording_path()
        if path.exists():
            os.startfile(str(path))
        else:
            messagebox.showinfo("\u6587\u4ef6\u4e0d\u5b58\u5728", f"默认宏文件不存在：\n{path}")

    def create_desktop_loop_bat(self) -> None:
        folder = self.macro_tools_dir()
        script = folder / "interception_loop_macro.py"
        exe = folder / "interception_loop_macro.exe"
        macro_json = self.macro_json_var.get().strip() or str(macro_recording_path())
        hotkey, interval = self.loop_settings()
        desktop = Path.home() / "Desktop"
        bat = desktop / "run_interception_macro.bat"
        extra = f' --hotkey "{hotkey}" --interval "{interval:g}"'
        command = f'"{exe}" "{macro_json}"{extra}' if exe.exists() else f'python "{script}" "{macro_json}"{extra}'
        content = (
            "@echo off\n"
            "chcp 65001 >nul\n"
            f"cd /d \"{folder}\"\n"
            "echo Starting Interception loop macro...\n"
            f"{command}\n"
            "pause\n"
        )
        bat.write_text(content, encoding="utf-8", newline="\r\n")
        self.status_callback(f"\u5df2\u5199\u5165 {bat}")
        messagebox.showinfo("\u5df2\u521b\u5efa", f"\u5df2\u521b\u5efa\u684c\u9762\u542f\u52a8\u5668\uff1a\n{bat}")

class UtilityToolbox:
    def __init__(self, root: tk.Tk, start_minimized: bool = False):
        self.root = root
        self.config = load_config()
        self.scale = current_scale(root)
        self.root.title(APP_NAME)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        icon_path = app_icon_path()
        if icon_path.exists():
            try:
                self.root.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass
        self.root.configure(bg=THEME["bg"])
        self.root.minsize(min(scaled(980, self.scale), root.winfo_screenwidth() - 40),
                          min(scaled(720, self.scale), root.winfo_screenheight() - 80))
        self.center_window()

        self.status_var = tk.StringVar(value="就绪")
        self.window_rows: Dict[str, int] = {}
        self.window_snapshot: Tuple[Tuple[int, str], ...] = tuple()
        self.window_refresh_job: Optional[str] = None
        self._setup_style()

        self.shell = ToolboxShell(root, self.scale, self.status_var)
        renamer_frame = self.shell.page("renamer")
        topmost_frame = self.shell.page("topmost")
        topmost_frame.configure(padding=scaled(18, self.scale))
        backup_frame = self.shell.page("backup")
        subtitle_frame = self.shell.page("subtitle")
        audio_frame = self.shell.page("audio")
        live_frame = self.shell.page("live")
        system_frame = self.shell.page("system")
        startup_frame = self.shell.page("startup")
        launcher_frame = self.shell.page("launcher")
        media_frame = self.shell.page("media")
        macro_frame = self.shell.page("macro")

        self.renamer = DateRenamerTab(renamer_frame)
        self.topmost = TopmostController(root, self.set_status)
        self.drop_close = DropCloseController(root, self.set_status)
        self.tray = TrayIcon(root, self.topmost, self.quit_app)
        self._build_topmost_tab(topmost_frame)
        self.backup = GameSaveManagerTab(backup_frame, self.config, self.save_config)
        self.subtitle = SubtitleSyncApp(subtitle_frame, embedded=True)
        self.audio = AudioSwitchTab(audio_frame, self.set_status)
        self.live_shortcuts = LiveShortcutTab(live_frame, self.set_status)
        self.system_tools = SystemToolsTab(system_frame, self.set_status)
        self.startup = StartupTab(startup_frame, self.config, self.save_config, self.set_status)
        self.launcher = LauncherTab(launcher_frame, self.set_status)
        self.drop_close._close_coordinator = self.launcher._queue_launcher_close
        self.media = MediaOrganizerTab(media_frame, self.set_status)
        self.macro_tools = MacroToolsTab(macro_frame, self.set_status)

        self.shell.finish()
        self.subtitle._on_focus_out(None)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        if start_minimized:
            self.root.after(0, self.root.withdraw)

    def _setup_style(self) -> None:
        setup_theme(self.root, self.scale)

    def center_window(self, width: int = 1280, height: int = 860) -> None:
        self.root.update_idletasks()
        width = scaled(width, self.scale)
        height = scaled(height, self.scale)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(width, screen_width - 40)
        height = min(height, screen_height - 80)
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build_topmost_tab(self, parent: ttk.Frame) -> None:
        hint = ttk.Label(parent, text="鼠标点选会立即切换置顶；热键会切换当前最上层窗口。默认 Ctrl+Alt+T，被占用时自动换备用键。", font=("Microsoft YaHei UI", scaled(18, self.scale)))
        hint.pack(anchor=tk.W, fill=tk.X)
        bind_wrap(parent, hint)
        controls = ttk.Frame(parent)
        controls.pack(fill=tk.X, pady=(12, 8))
        for col in range(3):
            controls.columnconfigure(col, weight=1, uniform="topmost_actions")
        actions = [
            ("鼠标点选并切换置顶", self.topmost.select_by_mouse),
            ("置顶选中窗口", self.pin_selected_window),
            ("取消选中窗口置顶", self.unpin_selected_window),
            ("取消所有本工具置顶窗口", self.topmost.unpin_all),
        ]
        for index, (text, command) in enumerate(actions):
            ttk.Button(controls, text=text, command=command).grid(
                row=index // 3,
                column=index % 3,
                padx=scaled(4, self.scale),
                pady=scaled(4, self.scale),
                sticky=tk.EW,
            )

        columns = ("hwnd", "title")
        self.window_tree = ttk.Treeview(parent, columns=columns, show="headings", height=18)
        self.window_tree.heading("hwnd", text="窗口句柄")
        self.window_tree.heading("title", text="窗口标题")
        self.window_tree.column("hwnd", width=scaled(120, self.scale), anchor=tk.CENTER)
        self.window_tree.column("title", width=scaled(640, self.scale))
        self.window_tree.pack(fill=tk.BOTH, expand=True, pady=(4, 12))

        ttk.Label(parent, textvariable=self.status_var).pack(anchor=tk.W)
        self.refresh_window_list()
        self.schedule_window_refresh()

    def refresh_window_list(self, auto: bool = False) -> None:
        windows = tuple(self.topmost.list_windows())
        if auto and windows == self.window_snapshot:
            return
        selected_hwnd = None
        selection = self.window_tree.selection()
        if selection:
            selected_hwnd = self.window_rows.get(selection[0])
        self.window_snapshot = windows
        self.window_tree.delete(*self.window_tree.get_children())
        self.window_rows.clear()
        next_selection = None
        for hwnd, title in windows:
            item_id = self.window_tree.insert("", tk.END, values=(hwnd, title))
            self.window_rows[item_id] = hwnd
            if hwnd == selected_hwnd:
                next_selection = item_id
        if next_selection:
            self.window_tree.selection_set(next_selection)
            self.window_tree.focus(next_selection)
        if not auto:
            self.set_status(f"已刷新桌面窗口：{len(self.window_rows)} 个")

    def schedule_window_refresh(self) -> None:
        if not self.root.winfo_exists():
            return
        self.refresh_window_list(auto=True)
        self.window_refresh_job = self.root.after(1000, self.schedule_window_refresh)

    def selected_hwnd(self) -> Optional[int]:
        selection = self.window_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先在列表里选择一个窗口。")
            return None
        return self.window_rows.get(selection[0])

    def pin_selected_window(self) -> None:
        hwnd = self.selected_hwnd()
        if hwnd:
            self.topmost.pin_window(hwnd)

    def unpin_selected_window(self) -> None:
        hwnd = self.selected_hwnd()
        if hwnd:
            self.topmost.unpin_window(hwnd)

    def set_status(self, message: str) -> None:
        self.status_var.set(message)

    def save_config(self) -> None:
        save_config(self.config)

    def hide_to_tray(self) -> None:
        close_mic_toggle_processes()
        self.root.withdraw()
        self.set_status("已隐藏到托盘，双击托盘图标可重新打开")

    def quit_app(self) -> None:
        try:
            close_mic_toggle_processes()
            if self.window_refresh_job:
                self.root.after_cancel(self.window_refresh_job)
            self.backup.stop()
            self.drop_close.stop()
            launcher = getattr(self, "launcher", None)
            if launcher is not None:
                launcher.stop()
            self.topmost.stop()
            self.tray.stop()
        finally:
            try:
                self.save_config()
            except (OSError, ValueError) as exc:
                messagebox.showerror("保存配置失败", f"工具箱将退出，原配置未被覆盖：{exc}", parent=self.root)
            finally:
                self.root.destroy()


def main() -> int:
    ensure_app_dir()
    try:
        if repair_autostart_path():
            log_line(f"已更新开机自启路径: {autostart_command()}")
    except OSError as exc:
        log_line(f"更新开机自启路径失败: {exc}")
    configure_dpi_awareness()
    start_minimized = AUTOSTART_ARG in sys.argv[1:]
    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    if start_minimized:
        root.withdraw()
    UtilityToolbox(root, start_minimized=start_minimized)
    try:
        root.mainloop()
    finally:
        close_mic_toggle_processes()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
