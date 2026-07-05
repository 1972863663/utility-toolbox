import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
import zipfile
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
import win32gui
import win32process

from subtitle_sync_embedded import MainApp as SubtitleSyncApp

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
APP_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "UtilityToolbox"
CONFIG_FILE = APP_DIR / "config.json"
LOG_FILE = APP_DIR / "toolbox.log"
DEFAULT_BACKUP_DIR = Path.home() / "Documents" / "SaveBackups"
AUTOSTART_ARG = "--minimized-to-tray"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "UtilityToolbox"
APP_ICON_FILE = "utility_toolbox.ico"
LAUNCHER_CLOSE_EXTENSIONS = {".exe", ".bat", ".cmd", ".py", ".ps1", ".lnk"}
PROCESS_EXECUTABLE_EXTENSIONS = {".exe"}
DIRECTORY_SCOPE_KEYWORDS = ("launcher", "runner", "start", "auto", "启动器", "启动")
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

THEME = {
    "bg": "#f5f7fb",
    "panel": "#ffffff",
    "panel_alt": "#eef3f8",
    "border": "#d7e0ea",
    "text": "#1f2937",
    "muted": "#64748b",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "success": "#0f766e",
    "danger": "#dc2626",
    "selection": "#dbeafe",
}


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
    if parent_pid:
        protected.add(parent_pid)
    return protected


def is_current_process(pid: int, process_path: str) -> bool:
    if pid in current_process_protected_pids():
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
    try:
        shell = win32com_client_dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(path)
        target = getattr(shortcut, "TargetPath", "")
        return target or path
    except Exception:
        return path


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


def is_process_in_directory(process_path: str, directory_path: str) -> bool:
    if Path(process_path).suffix.lower() not in PROCESS_EXECUTABLE_EXTENSIONS:
        return False
    process = normalize_process_path(process_path)
    directory = normalize_process_path(directory_path).rstrip("\\/")
    if not process or not directory:
        return False
    return process.startswith(directory + os.sep.casefold())


def match_processes_for_drop_paths(paths: Iterable[str], processes: Iterable[Tuple[int, str]]) -> List[Tuple[int, str]]:
    file_targets: List[str] = []
    directory_targets: List[str] = []
    for path in paths:
        if not path:
            continue
        resolved = resolve_shortcut_target(str(path))
        if path_looks_like_directory(resolved):
            directory_targets.append(resolved)
        else:
            file_targets.append(resolved)

    matches = match_processes_for_paths(file_targets, processes)
    seen_pids = {pid for pid, _process_path in matches}
    own_path = normalize_process_path(app_executable())
    for pid, process_path in processes:
        if pid in seen_pids:
            continue
        normalized = normalize_process_path(process_path)
        if not normalized or normalized == own_path or is_current_process(pid, process_path):
            continue
        if any(is_process_in_directory(process_path, directory) for directory in directory_targets):
            matches.append((pid, process_path))
            seen_pids.add(pid)
    return matches


def match_processes_for_paths(paths: Iterable[str], processes: Iterable[Tuple[int, str]]) -> List[Tuple[int, str]]:
    targets = [path for path in paths if path]
    matches: List[Tuple[int, str]] = []
    seen_pids: Set[int] = set()
    for pid, process_path in processes:
        if pid in seen_pids:
            continue
        if normalize_process_path(process_path) == normalize_process_path(app_executable()) or is_current_process(pid, process_path):
            continue
        if any(is_process_in_target_scope(process_path, target) for target in targets):
            matches.append((pid, process_path))
            seen_pids.add(pid)
    return matches


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


def running_process_paths() -> List[Tuple[int, str]]:
    processes: List[Tuple[int, str]] = []
    for pid in win32process.EnumProcesses():
        handle = None
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            path = win32process.GetModuleFileNameEx(handle, 0)
            if path:
                processes.append((pid, path))
        except Exception:
            pass
        finally:
            if handle:
                try:
                    win32api.CloseHandle(handle)
                except Exception:
                    pass
    return processes


def is_taskkill_already_closed(message: str) -> bool:
    normalized = message.casefold()
    return "没有找到进程" in normalized or "not found" in normalized


def terminate_process(pid: int) -> None:
    completed = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        if is_taskkill_already_closed(detail):
            return
        raise RuntimeError(detail or f"taskkill failed with exit code {completed.returncode}")


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

            if stem == source.stem:
                target = source
            else:
                target = self.unique_target(self.folder, stem, source.suffix, used)
            status = "无需改名" if source.name == target.name else "待改名"
            self.plan.append((source, target, status))
            self.tree.insert("", tk.END, values=(source.name, target.name, status))

    def apply(self) -> None:
        if not self.plan:
            messagebox.showinfo("提示", "没有可执行的重命名计划。")
            return
        if not messagebox.askokcancel("确认", "将按预览结果重命名当前文件夹中的文件，确认执行吗？"):
            return
        changed = 0
        undo_items = []
        for source, target, status in self.plan:
            if status == "无需改名" or not source.exists():
                continue
            try:
                source.rename(target)
                undo_items.append({"old": str(source), "new": str(target)})
                changed += 1
            except OSError as exc:
                log_line(f"批量重命名失败: {source} -> {target}: {exc}")
        if undo_items:
            ensure_app_dir()
            with self.undo_file.open("w", encoding="utf-8") as handle:
                json.dump({"created_at": datetime.now().isoformat(), "items": undo_items}, handle, ensure_ascii=False, indent=2)
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
            items = payload.get("items", [])
        except Exception as exc:
            messagebox.showerror("撤回失败", f"读取撤回记录失败：{exc}")
            return
        if not items:
            messagebox.showinfo("提示", "没有可撤回的批量重命名记录。")
            return
        if not messagebox.askokcancel("确认撤回", "将撤回上一次批量重命名，确认执行吗？"):
            return

        restored = 0
        for item in reversed(items):
            new_path = Path(item["new"])
            old_path = Path(item["old"])
            if not new_path.exists():
                continue
            target = old_path
            if target.exists():
                target = old_path.with_name(f"{old_path.stem}_restored{old_path.suffix}")
            try:
                new_path.rename(target)
                restored += 1
            except OSError as exc:
                log_line(f"撤回重命名失败: {new_path} -> {target}: {exc}")
        try:
            self.undo_file.unlink()
        except OSError:
            pass
        log_line(f"撤回批量重命名完成: {restored} 个文件")
        self.preview()
        messagebox.showinfo("完成", f"已撤回 {restored} 个文件。")


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
        self.set_topmost(hwnd, True)
        self.status_callback(f"已置顶：{self.window_title(hwnd)}")
        log_line(f"置顶窗口: {self.window_title(hwnd)}")

    def unpin_current(self) -> None:
        hwnd = self.last_hwnd
        if not hwnd or not win32gui.IsWindow(hwnd):
            self.status_callback("没有找到可取消置顶的当前窗口。")
            return
        self.set_topmost(hwnd, False)
        self.status_callback(f"已取消置顶：{self.window_title(hwnd)}")
        log_line(f"取消置顶窗口: {self.window_title(hwnd)}")

    def unpin_all(self) -> None:
        for hwnd in list(self.tracked):
            try:
                if win32gui.IsWindow(hwnd):
                    self.set_topmost(hwnd, False)
            except Exception:
                pass
        self.tracked.clear()
        self.status_callback("已取消所有由本工具设置的置顶窗口。")

    def stop(self) -> None:
        self.running = False
        self.unpin_all()


class DropCloseController:
    def __init__(self, root: tk.Tk, status_callback: Callable[[str], None]):
        self.root = root
        self.status_callback = status_callback
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
        threading.Thread(target=self._close_worker, args=(paths,), daemon=True).start()

    def _close_worker(self, paths: List[str]) -> None:
        log_line(f"DropCloseController close worker start: {paths}")
        processes = running_process_paths()
        log_line(f"DropCloseController process snapshot count: {len(processes)}")
        matches = match_processes_for_drop_paths(paths, processes)
        safe_matches = []
        for pid, process_path in matches:
            if is_current_process(pid, process_path):
                log_line(f"DropCloseController skipped protected process: pid={pid} path={process_path}")
            else:
                safe_matches.append((pid, process_path))
        matches = safe_matches
        log_line(f"DropCloseController matched processes: {matches}")
        names = ", ".join(Path(path).name for path in paths[:3])
        if len(paths) > 3:
            names += f" 等 {len(paths)} 项"
        if not matches:
            message = f"拖入关闭：未找到正在运行的相关进程（{names}）"
            log_line(message)
            self.root.after(0, lambda m=message: self.status_callback(m))
            return
        closed = 0
        failures: List[str] = []
        for pid, process_path in matches:
            try:
                log_line(f"DropCloseController taskkill start: pid={pid} path={process_path}")
                terminate_process(pid)
                closed += 1
                log_line(f"拖入强制关闭: pid={pid} path={process_path}")
            except Exception as exc:
                failures.append(f"{Path(process_path).name}: {exc}")
                log_line(f"拖入强制关闭失败: pid={pid} path={process_path} error={exc}")
        if failures:
            message = f"拖入关闭完成：已关闭 {closed} 个，失败 {len(failures)} 个"
        else:
            message = f"拖入关闭完成：已强制关闭 {closed} 个进程"
        self.root.after(0, lambda m=message: self.status_callback(m))

    def stop(self) -> None:
        return


class TrayIcon:
    WM_TRAY = win32con.WM_USER + 20
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
        self.hotkey_registered = False
        self.hotkey_label = ""
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        message_map = {
            self.WM_TRAY: self._on_tray,
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
            icon = win32gui.LoadImage(
                wc.hInstance,
                str(app_icon_path()),
                win32con.IMAGE_ICON,
                0,
                0,
                win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
            )
        except Exception:
            icon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        nid = (self.hwnd, 0, flags, self.WM_TRAY, icon, APP_NAME)
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
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
            menu.grab_release()

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
        if self.exit_callback:
            self.exit_callback()
        else:
            self.root.destroy()


class BackupMonitorTab:
    def __init__(self, parent: tk.Widget, config: Dict, save_callback: Callable[[], None]):
        self.parent = parent
        self.scale = current_scale(parent.winfo_toplevel())
        self.config = config
        self.save_callback = save_callback
        backup_cfg = self.config.setdefault("backup", {})
        backup_cfg.setdefault("sources", [])
        backup_cfg.setdefault("backup_dir", str(DEFAULT_BACKUP_DIR))
        backup_cfg.setdefault("monitor_enabled", False)

        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.pending: Dict[str, float] = {}
        self.signatures: Dict[str, Tuple[int, int]] = {}
        self.ui_queue: "queue.Queue[str]" = queue.Queue()

        controls = ttk.Frame(parent, padding=scaled(12, self.scale))
        controls.pack(fill=tk.X)
        for col in range(3):
            controls.columnconfigure(col, weight=1, uniform="backup_actions")
        ttk.Button(controls, text="添加存档目录", command=self.add_source).grid(row=0, column=0, padx=scaled(4, self.scale), pady=scaled(4, self.scale), sticky=tk.EW)
        ttk.Button(controls, text="移除选中", command=self.remove_selected).grid(row=0, column=1, padx=scaled(4, self.scale), pady=scaled(4, self.scale), sticky=tk.EW)
        ttk.Button(controls, text="选择备份目录", command=self.choose_backup_dir).grid(row=0, column=2, padx=scaled(4, self.scale), pady=scaled(4, self.scale), sticky=tk.EW)
        ttk.Button(controls, text="立即备份", command=self.backup_now).grid(row=1, column=0, padx=scaled(4, self.scale), pady=scaled(4, self.scale), sticky=tk.EW)

        self.monitor_var = tk.BooleanVar(value=backup_cfg.get("monitor_enabled", False))
        ttk.Checkbutton(controls, text="启动监控", variable=self.monitor_var, command=self.toggle_monitor).grid(row=1, column=1, padx=scaled(4, self.scale), pady=scaled(4, self.scale), sticky=tk.W)
        self.backup_dir_var = tk.StringVar(value=backup_cfg["backup_dir"])
        ttk.Label(parent, textvariable=self.backup_dir_var, padding=(12, 0)).pack(fill=tk.X)

        self.listbox = tk.Listbox(parent, height=8)
        self.listbox.configure(
            bg=THEME["panel"],
            fg=THEME["text"],
            selectbackground=THEME["selection"],
            selectforeground=THEME["text"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
            relief="flat",
        )
        self.listbox.pack(fill=tk.X, padx=scaled(12, self.scale), pady=scaled(8, self.scale))
        for source in backup_cfg["sources"]:
            self.listbox.insert(tk.END, source)

        self.log = scrolledtext.ScrolledText(parent, height=12)
        self.log.configure(
            bg=THEME["panel"],
            fg=THEME["text"],
            insertbackground=THEME["text"],
            selectbackground=THEME["selection"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=THEME["border"],
        )
        self.log.pack(fill=tk.BOTH, expand=True, padx=scaled(12, self.scale), pady=(0, scaled(12, self.scale)))
        parent.after(300, self._drain_queue)
        if self.monitor_var.get():
            self.start_monitor()

    def add_log(self, text: str) -> None:
        self.ui_queue.put(text)
        log_line(text)

    def _drain_queue(self) -> None:
        while True:
            try:
                msg = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            self.log.insert(tk.END, f"{msg}\n")
            self.log.see(tk.END)
        self.parent.after(300, self._drain_queue)

    def add_source(self) -> None:
        selected = filedialog.askdirectory(title="选择要监控的存档目录")
        if not selected:
            return
        sources = self.config["backup"]["sources"]
        if selected not in sources:
            sources.append(selected)
            self.listbox.insert(tk.END, selected)
            self.save_callback()

    def remove_selected(self) -> None:
        selection = list(self.listbox.curselection())
        if not selection:
            return
        sources = self.config["backup"]["sources"]
        for index in reversed(selection):
            value = self.listbox.get(index)
            self.listbox.delete(index)
            if value in sources:
                sources.remove(value)
        self.save_callback()

    def choose_backup_dir(self) -> None:
        selected = filedialog.askdirectory(title="选择备份保存目录")
        if not selected:
            return
        self.config["backup"]["backup_dir"] = selected
        self.backup_dir_var.set(selected)
        self.save_callback()

    def toggle_monitor(self) -> None:
        enabled = self.monitor_var.get()
        self.config["backup"]["monitor_enabled"] = enabled
        self.save_callback()
        if enabled:
            self.start_monitor()
        else:
            self.stop_monitor()

    def signature(self, folder: Path) -> Tuple[int, int]:
        latest = 0
        total_size = 0
        for root, _, files in os.walk(folder):
            for name in files:
                path = Path(root) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                latest = max(latest, stat.st_mtime_ns)
                total_size += stat.st_size
        return latest, total_size

    def start_monitor(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._monitor_loop, daemon=True)
        self.worker.start()
        self.add_log("存档监控已启动。")

    def stop_monitor(self) -> None:
        self.stop_event.set()
        self.add_log("存档监控已停止。")

    def _monitor_loop(self) -> None:
        while not self.stop_event.is_set():
            now = time.time()
            for source_text in list(self.config["backup"]["sources"]):
                source = Path(source_text)
                if not source.exists():
                    continue
                sig = self.signature(source)
                old = self.signatures.get(source_text)
                if old is None:
                    self.signatures[source_text] = sig
                elif old != sig:
                    self.signatures[source_text] = sig
                    self.pending[source_text] = now + 60
                    self.add_log(f"检测到变化，稍后备份：{source}")

            for source_text, ready_at in list(self.pending.items()):
                if now >= ready_at:
                    self.pending.pop(source_text, None)
                    self.create_backup(Path(source_text))

            self.stop_event.wait(30)

    def backup_now(self) -> None:
        sources = self.config["backup"]["sources"]
        if not sources:
            self.add_log("尚未添加存档目录，无法备份。")
            return
        for source_text in self.config["backup"]["sources"]:
            self.create_backup(Path(source_text))

    def unique_backup_path(self, backup_root: Path, safe_name: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        candidate = backup_root / f"{safe_name}_{stamp}.zip"
        index = 1
        while candidate.exists():
            candidate = backup_root / f"{safe_name}_{stamp}_{index}.zip"
            index += 1
        return candidate

    def create_backup(self, source: Path) -> None:
        if not source.exists():
            self.add_log(f"目录不存在，跳过：{source}")
            return
        backup_root = Path(self.config["backup"]["backup_dir"])
        backup_root.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in source.name) or "Save"
        zip_path = self.unique_backup_path(backup_root, safe_name)
        try:
            backup_root_resolved = backup_root.resolve()
        except OSError:
            backup_root_resolved = backup_root
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for root, _, files in os.walk(source):
                    root_path = Path(root)
                    try:
                        root_resolved = root_path.resolve()
                        if root_resolved == backup_root_resolved or root_resolved.is_relative_to(backup_root_resolved):
                            continue
                    except OSError:
                        pass
                    for name in files:
                        path = Path(root) / name
                        try:
                            if path.resolve() == zip_path.resolve():
                                continue
                            archive.write(path, path.relative_to(source.parent))
                        except OSError:
                            self.add_log(f"文件被占用，跳过：{path}")
            self.prune_old_backups(backup_root, safe_name)
            self.add_log(f"备份完成：{zip_path}")
        except Exception as exc:
            self.add_log(f"备份失败：{source} -> {exc}")

    def prune_old_backups(self, backup_root: Path, safe_name: str) -> None:
        backups = sorted(backup_root.glob(f"{safe_name}_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[30:]:
            try:
                old.unlink()
            except OSError:
                pass

    def stop(self) -> None:
        self.stop_event.set()


AUDIO_DEVICES = [
    {"name": "HyperX Cloud Alpha Wireless", "id": "{0.0.0.00000000}.{300AB429-8538-4C15-B885-0A28C416A317}"},
    {"name": "Realtek Speakers", "id": "{0.0.0.00000000}.{729281F5-3901-4B99-9799-F2A9891E705F}"},
]
CLSCTX_ALL = 23
E_RENDER = 0
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
                self.parent.after(0, lambda: messagebox.showerror("切换失败", str(exc)))
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
            webbrowser.open(url)
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
            self.parent.after(0, lambda: messagebox.showerror("打开失败", str(exc)))


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
        ttk.Label(frame, text="系统工具", font=("Microsoft YaHei UI", scaled(18, self.scale), "bold")).pack(anchor=tk.W)
        ttk.Button(
            frame,
            text="管理员运行 Windows PowerShell",
            command=self.open_admin_powershell,
        ).pack(anchor=tk.W, pady=(scaled(16, self.scale), 0), ipadx=scaled(20, self.scale), ipady=scaled(8, self.scale))
        ttk.Button(
            frame,
            text="启动麦克风悬浮窗",
            command=self.open_mic_toggle,
        ).pack(anchor=tk.W, pady=(scaled(10, self.scale), 0), ipadx=scaled(20, self.scale), ipady=scaled(8, self.scale))
        ttk.Button(
            frame,
            text="关闭麦克风悬浮窗",
            command=self.close_mic_toggle,
        ).pack(anchor=tk.W, pady=(scaled(10, self.scale), 0), ipadx=scaled(20, self.scale), ipady=scaled(8, self.scale))

    def open_admin_powershell(self) -> None:
        try:
            result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", None, r"C:\Windows\System32", 1)
            if result <= 32:
                raise OSError(f"ShellExecuteW failed: {result}")
            self.status_callback("已请求管理员权限启动 Windows PowerShell")
        except Exception as exc:
            messagebox.showerror("启动失败", f"无法以管理员身份启动 Windows PowerShell：{exc}")

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
        processes = self._running_mic_processes()
        if not processes:
            self.status_callback("麦克风悬浮窗未在运行")
            return

        closed = 0
        elevated_pids: List[int] = []
        errors: List[str] = []
        for pid, name, path in processes:
            try:
                handle = win32api.OpenProcess(
                    win32con.PROCESS_TERMINATE | win32con.PROCESS_QUERY_LIMITED_INFORMATION,
                    False,
                    pid,
                )
                try:
                    win32api.TerminateProcess(handle, 0)
                    closed += 1
                finally:
                    win32api.CloseHandle(handle)
            except Exception as exc:
                elevated_pids.append(pid)
                errors.append(f"{name}({pid})：{exc}")

        if elevated_pids:
            pid_args = " ".join(f"/pid {pid}" for pid in elevated_pids)
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
                self.status_callback(f"已请求管理员权限关闭麦克风悬浮窗（{len(elevated_pids)} 个进程）")
                return
            except Exception as exc:
                messagebox.showerror(
                    "关闭失败",
                    "无法关闭麦克风悬浮窗。它可能正在以管理员权限运行，请用管理员身份运行工具箱后再点关闭。\n\n"
                    + "\n".join(errors + [str(exc)]),
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
        startup_cfg = self.config.setdefault("startup", {})
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
        self.config.setdefault("startup", {})["autostart"] = enabled
        self.save_callback()
        self.refresh_state()
        self.status_callback("开机自启已开启" if enabled else "开机自启已关闭")

    def refresh_state(self) -> None:
        enabled = is_autostart_enabled()
        self.autostart_var.set(enabled)
        self.config.setdefault("startup", {})["autostart"] = enabled
        self.save_callback()
        self.state_var.set("当前状态：已开启开机自启" if enabled else "当前状态：未开启开机自启")


class LauncherTab:
    def __init__(self, parent: tk.Widget, status_callback: Callable[[str], None]):
        self.parent = parent
        self.status_callback = status_callback
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
        side = ttk.Frame(root, width=scaled(230, self.scale))
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
        ttk.Button(actions, text="添加应用/文件", command=self.add_apps).pack(side=tk.LEFT, padx=(0, scaled(8, self.scale)))
        ttk.Button(actions, text="删除选中", command=self.remove_selected).pack(side=tk.LEFT, padx=(0, scaled(8, self.scale)))
        ttk.Button(actions, text="启动选中", command=self.launch_selected).pack(side=tk.LEFT, padx=(0, scaled(8, self.scale)))
        ttk.Button(actions, text="启动当前组", command=self.launch_current_group).pack(side=tk.LEFT, padx=(0, scaled(8, self.scale)))
        ttk.Button(actions, text="关闭当前组", command=self.close_current_group).pack(side=tk.LEFT, padx=(0, scaled(8, self.scale)))
        ttk.Button(actions, text="修复失效路径", command=self.repair_current_group).pack(side=tk.LEFT, padx=(0, scaled(8, self.scale)))

        columns = ("name", "type", "path")
        self.app_tree = ttk.Treeview(main, columns=columns, show="headings", selectmode="extended")
        for column, text, width in [("name", "名称", 220), ("type", "类型", 120), ("path", "路径", 560)]:
            self.app_tree.heading(column, text=text)
            self.app_tree.column(column, width=scaled(width, self.scale))
        self.app_tree.grid(row=1, column=0, sticky=tk.NSEW)
        self.app_tree.bind("<Double-1>", lambda _event: self.launch_selected())
        self.log_text = scrolledtext.ScrolledText(main, height=6)
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
        if not name or name in self.groups:
            return
        self.groups[name.strip()] = self.groups.pop(self.current_group)
        self.current_group = name.strip()
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
        names = {self.app_tree.item(item, "values")[0] for item in self.app_tree.selection()}
        self.groups[self.current_group]["apps"] = [app for app in self.groups[self.current_group]["apps"] if app["name"] not in names]
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
        threading.Thread(target=self._launch_worker, args=(apps,), daemon=True).start()

    def repair_current_group(self) -> None:
        if not self.current_group:
            return
        apps = self.groups.get(self.current_group, {}).get("apps", [])
        threading.Thread(target=self._repair_worker, args=(apps,), daemon=True).start()

    def close_current_group(self) -> None:
        if self.current_group:
            paths: List[str] = []
            for app in self.groups[self.current_group]["apps"]:
                if app.get("path"):
                    paths.append(app["path"])
                learned_paths = app.get("close_paths", [])
                if isinstance(learned_paths, list):
                    paths.extend(str(path) for path in learned_paths if path)
            self.close_paths(paths)

    def close_paths(self, paths: List[str]) -> None:
        threading.Thread(target=self._close_worker, args=(paths,), daemon=True).start()

    def _launch_worker(self, apps: List[Dict[str, str]]) -> None:
        learned_any = False
        repaired_any = False
        for app in apps:
            path = app.get("path", "")
            repaired_path = self._ensure_launch_path(app)
            if repaired_path and repaired_path != path:
                path = repaired_path
                repaired_any = True
            before = running_process_paths()
            try:
                if Path(path).suffix.lower() == ".url":
                    for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
                        if line.startswith("URL="):
                            webbrowser.open(line[4:].strip())
                            break
                else:
                    os.startfile(path)
                time.sleep(2)
                learned = discover_new_process_paths(before, running_process_paths())
                if learned:
                    existing = app.setdefault("close_paths", [])
                    if isinstance(existing, list):
                        seen = {normalize_process_path(item) for item in existing}
                        for process_path in learned:
                            if normalize_process_path(process_path) not in seen:
                                existing.append(process_path)
                                seen.add(normalize_process_path(process_path))
                                learned_any = True
                self.parent.after(0, lambda p=path: self.log(f"已启动：{Path(p).name}"))
            except Exception as exc:
                self.parent.after(0, lambda p=path, e=exc: self.log(f"启动失败：{Path(p).name} - {e}"))
        if learned_any or repaired_any:
            self.parent.after(0, self.save_config)
            self.parent.after(0, self.refresh_apps)

    def _repair_worker(self, apps: List[Dict[str, str]]) -> None:
        repaired = 0
        missing = 0
        for app in apps:
            old_path = app.get("path", "")
            if old_path and Path(old_path).exists():
                continue
            found = self._find_relocated_path(app)
            if found:
                app["path"] = found
                app["type"] = self.file_type(found)
                repaired += 1
                self.parent.after(0, lambda n=app.get("name", Path(found).name), p=found: self.log(f"已修复路径：{n} -> {p}"))
            else:
                missing += 1
                self.parent.after(0, lambda n=app.get("name", Path(old_path).name): self.log(f"未找到新位置：{n}"))
        if repaired:
            self.parent.after(0, self.save_config)
            self.parent.after(0, self.refresh_apps)
        self.parent.after(0, lambda: self.log(f"路径修复完成：修复 {repaired} 个，未找到 {missing} 个"))

    def _ensure_launch_path(self, app: Dict[str, str]) -> str:
        path = app.get("path", "")
        if path and Path(path).exists():
            return path
        found = self._find_relocated_path(app)
        if not found:
            return path
        old_path = path
        app["path"] = found
        app["type"] = self.file_type(found)
        self.parent.after(0, lambda n=app.get("name", Path(found).name), old=old_path, new=found: self.log(f"启动前已自动修复路径：{n} -> {new}"))
        return found

    def _find_relocated_path(self, app: Dict[str, str]) -> Optional[str]:
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
        deadline = time.monotonic() + LAUNCHER_REPAIR_MAX_SECONDS
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
        targets = [path for path in paths if Path(path).suffix.lower() in LAUNCHER_CLOSE_EXTENSIONS]
        if not targets:
            self.parent.after(0, lambda: self.log("当前组没有可按进程关闭的应用"))
            return
        matches = match_processes_for_paths(targets, running_process_paths())
        if not matches:
            self.parent.after(0, lambda: self.log("当前组没有找到正在运行的应用"))
            return
        for pid, process_path in matches:
            try:
                terminate_process(pid)
                self.parent.after(0, lambda p=process_path: self.log(f"已关闭：{Path(p).name}"))
            except Exception as exc:
                self.parent.after(0, lambda p=process_path, e=exc: self.log(f"关闭失败：{Path(p).name} - {e}"))

    def log(self, message: str) -> None:
        self.log_text.insert(tk.END, f"[{datetime.now():%H:%M:%S}] {message}\n")
        self.log_text.see(tk.END)
        self.status_callback(message)

    def save_config(self) -> None:
        ensure_app_dir()
        payload = {"groups": self.groups, "current_group": self.current_group}
        self.config_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_config(self) -> None:
        if not self.config_file.exists():
            return
        try:
            payload = json.loads(self.config_file.read_text(encoding="utf-8"))
            self.groups = payload.get("groups", {}) if isinstance(payload.get("groups"), dict) else {}
            self.current_group = payload.get("current_group")
        except Exception:
            self.groups = {}


class MediaOrganizerTab:
    def __init__(self, parent: tk.Widget, status_callback: Callable[[str], None]):
        self.parent = parent
        self.status_callback = status_callback
        self.scale = current_scale(parent.winfo_toplevel())
        self.manifest = None
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
        ttk.Button(buttons, text="扫描预览", command=self.scan).pack(side=tk.LEFT, padx=(0, scaled(8, self.scale)))
        self.execute_button = ttk.Button(buttons, text="确认执行", command=self.execute, state=tk.DISABLED)
        self.execute_button.pack(side=tk.LEFT)
        self.summary_var = tk.StringVar(value="等待扫描")
        ttk.Label(frame, textvariable=self.summary_var).grid(row=3, column=0, columnspan=3, sticky=tk.EW)
        self.log_text = scrolledtext.ScrolledText(frame, height=20)
        self.log_text.grid(row=4, column=0, columnspan=3, sticky=tk.NSEW, pady=(scaled(8, self.scale), 0))
        frame.rowconfigure(4, weight=1)

    def pick_dir(self, variable: tk.StringVar) -> None:
        selected = filedialog.askdirectory()
        if selected:
            variable.set(selected)

    def scan(self) -> None:
        if not all([AppConfig, scan_media, save_manifest, write_json]):
            messagebox.showerror("缺少依赖", "照片视频管家模块不可用。")
            return
        if not self.source_var.get() or not self.output_var.get():
            messagebox.showwarning("缺少目录", "请先选择源目录和输出目录。")
            return
        self.execute_button.configure(state=tk.DISABLED)
        self.summary_var.set("正在扫描...")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        try:
            output = Path(self.output_var.get())
            manifest = scan_media(AppConfig(source_dir=self.source_var.get(), output_dir=self.output_var.get()))
            output.mkdir(parents=True, exist_ok=True)
            write_json(output / "config.json", manifest.to_dict()["config"])
            save_manifest(manifest, output)
            self.parent.after(0, lambda: self._scan_done(manifest))
        except Exception as exc:
            self.parent.after(0, lambda: messagebox.showerror("扫描失败", str(exc)))

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
        self.execute_button.configure(state=tk.NORMAL)
        self.status_callback("照片视频扫描完成")

    def execute(self) -> None:
        if not self.manifest or not execute_plan:
            return
        self.execute_button.configure(state=tk.DISABLED)
        self.summary_var.set("正在执行复制和报告生成...")
        threading.Thread(target=self._execute_worker, daemon=True).start()

    def _execute_worker(self) -> None:
        try:
            result = execute_plan(self.manifest, lambda message: self.parent.after(0, lambda m=message: self.log_text.insert(tk.END, m + "\n")))
            self.parent.after(0, lambda: self._execute_done(result))
        except Exception as exc:
            self.parent.after(0, lambda: messagebox.showerror("执行失败", str(exc)))

    def _execute_done(self, result: Dict) -> None:
        self.summary_var.set(f"执行完成：复制 {len(result['copied'])}，压缩成功 {len(result['compressed'])}，跳过/失败 {len(result['skipped'])}，复制失败 {len(result['failed'])}")
        self.log_text.insert(tk.END, f"地点 CSV：{result.get('locations_report_csv')}\n")
        self.status_callback("照片视频整理完成")


class UtilityToolbox:
    def __init__(self, root: tk.Tk, start_minimized: bool = False):
        self.root = root
        self.config = load_config()
        self.scale = current_scale(root)
        self.root.title(APP_NAME)
        icon_path = app_icon_path()
        if icon_path.exists():
            try:
                self.root.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass
        self.root.configure(bg=THEME["bg"])
        self.root.minsize(scaled(700, self.scale), scaled(540, self.scale))
        self.center_window()

        self.status_var = tk.StringVar(value="就绪")
        self.window_rows: Dict[str, int] = {}
        self.window_snapshot: Tuple[Tuple[int, str], ...] = tuple()
        self.window_refresh_job: Optional[str] = None
        self._setup_style()

        notebook = ttk.Notebook(root)
        notebook.pack(fill=tk.BOTH, expand=True)

        renamer_frame = ttk.Frame(notebook)
        topmost_frame = ttk.Frame(notebook, padding=scaled(18, self.scale))
        backup_frame = ttk.Frame(notebook)
        subtitle_frame = tk.Frame(notebook, bg=THEME["bg"])
        audio_frame = ttk.Frame(notebook)
        live_frame = ttk.Frame(notebook)
        system_frame = ttk.Frame(notebook)
        startup_frame = ttk.Frame(notebook)
        launcher_frame = ttk.Frame(notebook)
        media_frame = ttk.Frame(notebook)

        notebook.add(renamer_frame, text="批量重命名")
        notebook.add(topmost_frame, text="窗口置顶")
        notebook.add(backup_frame, text="存档备份")
        notebook.add(subtitle_frame, text="视频字幕同步")
        notebook.add(audio_frame, text="音频输出")
        notebook.add(live_frame, text="直播快捷")
        notebook.add(system_frame, text="系统工具")
        notebook.add(startup_frame, text="自启动")
        notebook.add(launcher_frame, text="应用启动器")
        notebook.add(media_frame, text="照片视频管家")

        self.renamer = DateRenamerTab(renamer_frame)
        self.topmost = TopmostController(root, self.set_status)
        self.drop_close = DropCloseController(root, self.set_status)
        self.tray = TrayIcon(root, self.topmost, self.quit_app)
        self._build_topmost_tab(topmost_frame)
        self.backup = BackupMonitorTab(backup_frame, self.config, self.save_config)
        self.subtitle = SubtitleSyncApp(subtitle_frame, embedded=True)
        self.audio = AudioSwitchTab(audio_frame, self.set_status)
        self.live_shortcuts = LiveShortcutTab(live_frame, self.set_status)
        self.system_tools = SystemToolsTab(system_frame, self.set_status)
        self.startup = StartupTab(startup_frame, self.config, self.save_config, self.set_status)
        self.launcher = LauncherTab(launcher_frame, self.set_status)
        self.media = MediaOrganizerTab(media_frame, self.set_status)

        status = ttk.Label(root, textvariable=self.status_var, anchor=tk.W, padding=(8, 4))
        status.pack(fill=tk.X)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        if start_minimized:
            self.root.after(0, self.root.withdraw)

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.tk.call("tk", "scaling", 1.0 + (self.scale - 1.0) * 0.75)
        base_size = scaled(14, self.scale)
        title_size = scaled(18, self.scale)
        for font_name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(font_name).configure(family="Microsoft YaHei UI", size=base_size)
            except tk.TclError:
                pass
        try:
            tkfont.nametofont("TkHeadingFont").configure(weight="bold")
        except tk.TclError:
            pass

        padding = (scaled(14, self.scale), scaled(9, self.scale))
        style.configure(".", font=("Microsoft YaHei UI", base_size), background=THEME["bg"], foreground=THEME["text"])
        style.configure("TFrame", background=THEME["bg"])
        style.configure("TLabel", background=THEME["bg"], foreground=THEME["text"])
        style.configure("TButton", background=THEME["panel"], foreground=THEME["text"], bordercolor=THEME["border"], focusthickness=1, focuscolor=THEME["accent"], padding=padding)
        style.map("TButton", background=[("active", THEME["panel_alt"]), ("pressed", THEME["selection"])], foreground=[("disabled", THEME["muted"])])
        style.configure("TCheckbutton", background=THEME["bg"], foreground=THEME["text"])
        style.map("TCheckbutton", background=[("active", THEME["bg"])], foreground=[("active", THEME["text"])])
        style.configure("TEntry", fieldbackground=THEME["panel"], foreground=THEME["text"], bordercolor=THEME["border"], insertcolor=THEME["text"], padding=scaled(4, self.scale))
        style.configure("TCombobox", fieldbackground=THEME["panel"], foreground=THEME["text"], bordercolor=THEME["border"], arrowcolor=THEME["accent"], padding=scaled(4, self.scale))
        style.configure("Treeview", background=THEME["panel"], fieldbackground=THEME["panel"], foreground=THEME["text"], bordercolor=THEME["border"], rowheight=scaled(40, self.scale))
        style.configure("Treeview.Heading", background=THEME["panel_alt"], foreground=THEME["text"], relief="flat", font=("Microsoft YaHei UI", base_size, "bold"), padding=(scaled(10, self.scale), scaled(8, self.scale)))
        style.map("Treeview", background=[("selected", THEME["selection"])], foreground=[("selected", THEME["text"])])
        style.configure("TNotebook", background=THEME["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=THEME["panel_alt"], foreground=THEME["muted"], padding=(scaled(16, self.scale), scaled(8, self.scale)), font=("Microsoft YaHei UI", base_size))
        style.map("TNotebook.Tab", background=[("selected", THEME["panel"])], foreground=[("selected", THEME["accent"])])
        style.configure("TLabelframe", background=THEME["bg"], foreground=THEME["text"], bordercolor=THEME["border"])
        style.configure("TLabelframe.Label", background=THEME["bg"], foreground=THEME["muted"], font=("Microsoft YaHei UI", title_size, "bold"))

    def center_window(self, width: int = 1100, height: int = 820) -> None:
        self.root.update_idletasks()
        width = scaled(width, self.scale)
        height = scaled(height, self.scale)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
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
        self.window_tree.column("hwnd", width=scaled(150, self.scale), anchor=tk.CENTER)
        self.window_tree.column("title", width=scaled(820, self.scale))
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
        prefix = "实时检测" if auto else "已刷新"
        self.set_status(f"{prefix}桌面窗口：{len(self.window_rows)} 个")

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
        self.root.withdraw()
        self.set_status("已隐藏到托盘，双击托盘图标可重新打开")

    def quit_app(self) -> None:
        try:
            if self.window_refresh_job:
                self.root.after_cancel(self.window_refresh_job)
            self.backup.stop()
            self.drop_close.stop()
            self.topmost.stop()
            self.tray.stop()
        finally:
            self.save_config()
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
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
