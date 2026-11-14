"""Launch the upstream Game Save Manager without touching legacy backups."""

import os
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from toolbox_paths import application_root

import win32api
import win32con
import win32gui
import win32process
import psutil


PROJECT_URL = "https://github.com/dyang886/Game-Save-Manager"
RELEASE_URL = PROJECT_URL + "/releases/latest"
REQUIRED_FILES = ("resources/app.asar", "resources.pak", "icudtl.dat", "database/database.db")


def application_directory() -> Path:
    return application_root()


def _write_profile_json(path: Path, value: Dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            active_error = sys.exception()
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_error:
                if active_error is None:
                    raise
                active_error.add_note(f"临时文件清理失败：{temporary}: {cleanup_error}")


def prepare_profile(base_dir: Path) -> Path:
    """Keep GSM's own profile and default backup paths inside the portable root."""
    root = base_dir.resolve()
    profile = root / "data" / "GameSaveManager"
    settings_path = profile / "GSM Settings" / "settings.json"
    marker = profile / "toolbox-root.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError("Game Save Manager 的配置格式无效，已保留原文件。")
    previous_roots = []
    if marker.exists():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or not isinstance(previous.get("root"), str):
            raise ValueError("Game Save Manager 的便携目录记录无效，已保留原文件。")
        pending_roots = previous.get("pending_roots", [])
        if not isinstance(pending_roots, list) or not all(isinstance(item, str) for item in pending_roots):
            raise ValueError("Game Save Manager 的待迁移目录记录无效，已保留原文件。")
        for recorded in [previous["root"], *pending_roots]:
            candidate = Path(recorded)
            if not candidate.is_absolute():
                raise ValueError("Game Save Manager 的便携目录记录必须是绝对路径，已保留原文件。")
            candidate = Path(os.path.abspath(candidate))
            if candidate not in previous_roots:
                previous_roots.append(candidate)
    for key, folder in (("backupPath", "GSM Backups"), ("exportPath", "GSM Exports")):
        value = settings.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"Game Save Manager 的 {key} 配置必须是文本。")
        if not value:
            settings[key] = str(root / "data" / folder)
        elif previous_roots and Path(value).is_absolute():
            # Normalize '..' without following a junction that moves with the bundle.
            configured_path = Path(os.path.abspath(value))
            # A previous root may contain a later one; match the closest root first.
            for previous_root in sorted(previous_roots, key=lambda item: len(item.parts), reverse=True):
                if configured_path.is_relative_to(previous_root):
                    settings[key] = str(root / configured_path.relative_to(previous_root))
                    break
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    # Record both possible locations before touching settings. If either subsequent
    # replacement fails, a later launch (even after another move) can rebase either.
    recovery_roots = list(dict.fromkeys([*previous_roots, root]))
    _write_profile_json(marker, {"root": str(recovery_roots[0]), "pending_roots": [str(item) for item in recovery_roots[1:]]})
    _write_profile_json(settings_path, settings)
    _write_profile_json(marker, {"root": str(root)})
    return profile


def find_executable(config: Dict, base_dir: Optional[Path] = None) -> Path:
    settings = config.get("game_save_manager", {})
    if not isinstance(settings, dict):
        raise ValueError("游戏备份的程序配置格式无效，请重新选择程序位置。")
    configured = settings.get("executable", "")
    if not isinstance(configured, str):
        raise ValueError("游戏备份的程序路径必须是文本，请重新选择程序位置。")
    configured = configured.strip()
    base = base_dir if base_dir is not None else application_directory()
    path = Path(configured) if configured else base / "game_save_manager" / "Game Save Manager.exe"
    if not path.is_absolute():
        path = base / path
    if not path.is_file():
        raise FileNotFoundError(f"未找到 Game Save Manager：{path}\n请通过“选择程序位置”指定程序。")
    if path.name.casefold() != "game save manager.exe":
        raise ValueError("请选择原版 Game Save Manager.exe，不能选择工具箱或 Updater.exe。")
    missing = [name for name in REQUIRED_FILES if not (path.parent / name).is_file()]
    if missing:
        raise FileNotFoundError("Game Save Manager 安装目录不完整，缺少：\n" + "\n".join(missing) + "\n请完整解压便携版，不要只复制 EXE。")
    return path.resolve()


def activate_existing_window(executable: Path, profile: Optional[Path] = None) -> bool:
    """Find only windows belonging to this exact executable, including hidden ones."""
    expected = os.path.normcase(str(executable.resolve()))
    matches = []

    def visit(hwnd, _context):
        try:
            if not win32gui.GetClassName(hwnd).startswith("Chrome_WidgetWin"):
                return
            if win32gui.GetWindow(hwnd, win32con.GW_OWNER) or not win32gui.GetWindowText(hwnd):
                return
            _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
            try:
                path = win32process.GetModuleFileNameEx(handle, 0)
            finally:
                handle.Close()
            if os.path.normcase(str(Path(path).resolve())) == expected:
                matches.append((hwnd, pid))
        except (OSError, win32gui.error):
            # A window/process may exit while it is being enumerated.
            return

    win32gui.EnumWindows(visit, None)
    for hwnd, pid in matches:
        if profile is not None:
            try:
                arguments = psutil.Process(pid).cmdline()
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as exc:
                raise ValueError("无法确认已运行的存档管理器数据目录，请先退出该实例再从工具箱打开。") from exc
            current_profile = None
            for index, argument in enumerate(arguments):
                if argument.startswith("--user-data-dir="):
                    current_profile = argument.split("=", 1)[1]
                elif argument == "--user-data-dir" and index + 1 < len(arguments):
                    current_profile = arguments[index + 1]
            if not current_profile or Path(current_profile).resolve() != profile.resolve():
                raise ValueError("存档管理器正使用其他数据目录，请先退出该实例，再从工具箱打开便携版。")
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except win32gui.error:
            continue
        try:
            win32gui.SetForegroundWindow(hwnd)
        except win32gui.error:
            try:
                win32gui.FlashWindow(hwnd, True)
            except win32gui.error:
                pass
        return True
    return False


def launch(config: Dict, base_dir: Optional[Path] = None) -> Path:
    path = find_executable(config, base_dir)
    root = base_dir if base_dir is not None else application_directory()
    if activate_existing_window(path, root / "data" / "GameSaveManager"):
        return path
    profile = prepare_profile(root)
    subprocess.Popen([str(path), f"--user-data-dir={profile}"], cwd=str(path.parent))
    return path


class GameSaveManagerTab:
    def __init__(self, parent: tk.Widget, config: Dict, save_callback: Callable[[], None]):
        self.parent = parent
        self.config = config
        self.save_callback = save_callback
        self._stopped = False
        self._launching = False
        self._cooldown_job = None
        parent.bind("<Destroy>", self._on_destroy, add="+")
        content = ttk.Frame(parent, padding=18)
        content.pack(fill=tk.BOTH, expand=True)
        ttk.Label(content, text="游戏存档管理 · Game Save Manager", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor=tk.W, pady=(0, 12))
        ttk.Label(content, text="使用原版 Game Save Manager，在独立窗口中管理游戏存档。", wraplength=600).pack(anchor=tk.W)
        ttk.Label(content, text="自动识别游戏存档 · 备份与恢复 · 版本历史 · 自定义游戏 · 导入与导出", wraplength=600).pack(anchor=tk.W, pady=(6, 18))
        actions = ttk.Frame(content)
        actions.pack(fill=tk.X)
        self.open_button = ttk.Button(actions, text="打开游戏存档管理器", command=self.open_manager)
        self.open_button.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="选择程序位置", command=self.choose_executable).pack(side=tk.LEFT)
        self.status = tk.StringVar()
        self.location = tk.StringVar()
        ttk.Label(content, textvariable=self.status, wraplength=600).pack(anchor=tk.W, pady=(18, 6))
        ttk.Label(content, textvariable=self.location, wraplength=600).pack(anchor=tk.W)
        legacy = ttk.LabelFrame(content, text="原有备份", padding=12)
        legacy.pack(fill=tk.X, pady=(24, 0))
        ttk.Label(legacy, text="原备份文件和目录配置已保留，旧的自动监控已停用。\n旧 ZIP 备份不会自动转换为 Game Save Manager 的备份格式。", wraplength=570).pack(anchor=tk.W)
        ttk.Button(legacy, text="打开旧备份目录", command=self.open_legacy_backups).pack(anchor=tk.W, pady=(10, 0))
        self.refresh_location()

    def refresh_location(self) -> None:
        try:
            path = find_executable(self.config)
        except (OSError, ValueError) as exc:
            self.status.set("尚未就绪，请选择 Game Save Manager 程序。")
            self.location.set(str(exc))
        else:
            self.status.set("已就绪")
            self.location.set(str(path))

    def open_manager(self) -> None:
        if self._stopped or self._launching:
            return
        self._launching = True
        self.open_button.configure(state=tk.DISABLED)
        try:
            launch(self.config)
        except (OSError, ValueError) as exc:
            self._finish_cooldown()
            self.status.set("启动失败")
            messagebox.showerror("无法打开 Game Save Manager", str(exc), parent=self.parent)
        else:
            self.status.set("已发送启动请求，请在 Game Save Manager 窗口中操作。")
            self._cooldown_job = self.parent.after(1500, self._finish_cooldown)

    def _finish_cooldown(self) -> None:
        self._cooldown_job = None
        self._launching = False
        if not self._stopped:
            self.open_button.configure(state=tk.NORMAL)

    def _on_destroy(self, event) -> None:
        if event.widget is self.parent:
            self.stop()

    def choose_executable(self) -> None:
        selected = filedialog.askopenfilename(parent=self.parent, title="选择 Game Save Manager.exe", filetypes=[("Windows 程序", "*.exe")])
        if not selected:
            return
        try:
            path = find_executable({"game_save_manager": {"executable": selected}})
        except (OSError, ValueError) as exc:
            messagebox.showerror("程序位置无效", str(exc), parent=self.parent)
            return
        had_settings = "game_save_manager" in self.config
        previous = self.config.get("game_save_manager")
        settings = dict(previous) if isinstance(previous, dict) else {}
        root = application_directory()
        settings["executable"] = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        self.config["game_save_manager"] = settings
        try:
            self.save_callback()
        except Exception as exc:
            if not had_settings:
                self.config.pop("game_save_manager", None)
            else:
                self.config["game_save_manager"] = previous
            messagebox.showerror("保存程序位置失败", str(exc), parent=self.parent)
        self.refresh_location()

    def open_legacy_backups(self) -> None:
        try:
            settings = self.config.get("backup", {})
            if not isinstance(settings, dict):
                raise ValueError("旧备份配置格式无效，原始配置已保留。")
            configured = settings.get("backup_dir", "")
            if not isinstance(configured, str):
                raise ValueError("旧备份目录必须是文本，原始配置已保留。")
            path = Path(configured or Path.home() / "Documents" / "SaveBackups")
            if not path.is_absolute():
                path = application_directory() / path
            if not path.is_dir():
                messagebox.showinfo("旧备份目录", f"目录不存在：{path}", parent=self.parent)
                return
            os.startfile(str(path))
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法打开旧备份目录", str(exc), parent=self.parent)

    def stop(self) -> None:
        # GSM is independent: exiting the toolbox must not interrupt a backup.
        self._stopped = True
        if self._cooldown_job is not None:
            try:
                self.parent.after_cancel(self._cooldown_job)
            except tk.TclError:
                pass
            self._cooldown_job = None
