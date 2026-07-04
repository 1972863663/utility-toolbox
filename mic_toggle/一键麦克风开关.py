from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox
import tkinter as tk


APP_NAME = "一键麦克风开关"
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "MicToggleHotkey"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_CONFIG = {"hotkey": "Ctrl+Alt+M", "hotkey_codes": [0x11, 0x12, 0x4D]}
MUTEX_NAME = "Global\\MicToggleHotkeySingleInstance"

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080

CLSCTX_ALL = 23
E_CAPTURE = 1
ROLE_COMMUNICATIONS = 2

KEY_NAMES: dict[int, str] = {
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
    KEY_NAMES[code] = chr(code)
for code in range(0x41, 0x5B):
    KEY_NAMES[code] = chr(code)
for index in range(1, 13):
    KEY_NAMES[0x6F + index] = f"F{index}"

KEY_SCAN_ORDER = [
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


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def quote_arg(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"' if any(ch in value for ch in ' \t"') else value


def relaunch_as_admin() -> None:
    if is_admin() or "--no-admin" in sys.argv:
        return
    if getattr(sys, "frozen", False):
        executable = sys.executable
        params = " ".join(quote_arg(arg) for arg in sys.argv[1:])
    else:
        executable = sys.executable
        params = " ".join([quote_arg(str(Path(__file__).resolve())), *[quote_arg(arg) for arg in sys.argv[1:]]])
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
    if result <= 32:
        messagebox.showerror(APP_NAME, "需要管理员权限才能监听全局热键并切换麦克风。")
    raise SystemExit(0)


def ensure_single_instance() -> None:
    handle = ctypes.windll.kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if not handle:
        return
    if ctypes.windll.kernel32.GetLastError() == 183:
        raise SystemExit(0)


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.exists():
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                config.update(saved)
    except Exception:
        pass
    codes = config.get("hotkey_codes")
    if not isinstance(codes, list) or not all(isinstance(item, int) for item in codes):
        codes = parse_hotkey_text(str(config.get("hotkey") or DEFAULT_CONFIG["hotkey"]))
    config["hotkey_codes"] = codes or list(DEFAULT_CONFIG["hotkey_codes"])
    config["hotkey"] = format_hotkey(config["hotkey_codes"])
    return config


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def key_down(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def set_window_click_through(hwnd: int) -> None:
    """Make a Windows Tk top-level window ignore mouse input.

    WS_EX_TRANSPARENT makes hit-testing fall through to windows below it, so the
    overlay does not block games or other full-screen applications. WS_EX_LAYERED
    keeps alpha transparency working, and WS_EX_TOOLWINDOW keeps the tiny overlay
    out of Alt-Tab/taskbar style surfaces.
    """

    if os.name != "nt" or not hwnd:
        return
    user32 = ctypes.windll.user32
    try:
        get_style = user32.GetWindowLongPtrW
        set_style = user32.SetWindowLongPtrW
    except AttributeError:
        get_style = user32.GetWindowLongW
        set_style = user32.SetWindowLongW
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = ctypes.c_void_p
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    set_style.restype = ctypes.c_void_p
    style = int(get_style(wintypes.HWND(hwnd), GWL_EXSTYLE) or 0)
    style |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW
    set_style(wintypes.HWND(hwnd), GWL_EXSTYLE, ctypes.c_void_p(style))


def current_pressed_codes() -> list[int]:
    return [code for code in KEY_SCAN_ORDER if key_down(code)]


def format_hotkey(codes: list[int]) -> str:
    if not codes:
        return ""
    order = {code: index for index, code in enumerate(KEY_SCAN_ORDER)}
    return "+".join(KEY_NAMES.get(code, f"VK{code}") for code in sorted(dict.fromkeys(codes), key=lambda code: order.get(code, 999)))


def parse_hotkey_text(text: str) -> list[int]:
    reverse = {name.lower(): code for code, name in KEY_NAMES.items()}
    aliases = {
        "control": 0x11,
        "ctrl": 0x11,
        "alt": 0x12,
        "shift": 0x10,
        "esc": 0x1B,
        "escape": 0x1B,
        "leftmouse": 0x01,
        "mouseleft": 0x01,
        "rightmouse": 0x02,
        "mouseright": 0x02,
        "middlemouse": 0x04,
        "mousemiddle": 0x04,
    }
    reverse.update(aliases)
    codes = []
    for part in text.replace(" ", "").split("+"):
        code = reverse.get(part.lower())
        if code is not None and code not in codes:
            codes.append(code)
    return codes


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


def get_default_capture_endpoint_volume() -> ctypes.c_void_p:
    enumerator = create_com_object("BCDE0395-E52F-467C-8E3D-C4579291692E", "A95664D2-9614-4F35-A746-DE8DB63617E6")
    device = ctypes.c_void_p()
    endpoint = ctypes.c_void_p()
    try:
        get_default = com_method(enumerator, 4, ctypes.c_long, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
        check_hr(get_default(enumerator, E_CAPTURE, ROLE_COMMUNICATIONS, ctypes.byref(device)), "GetDefaultAudioEndpoint")

        activate = com_method(
            device,
            3,
            ctypes.c_long,
            ctypes.POINTER(GUID),
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        endpoint_iid = GUID.from_string("5CDF2C82-841E-4546-9722-0CF74078229A")
        check_hr(activate(device, ctypes.byref(endpoint_iid), CLSCTX_ALL, None, ctypes.byref(endpoint)), "IMMDevice.Activate")
        return endpoint
    finally:
        release(device)
        release(enumerator)


def get_mic_muted() -> bool:
    ole32.CoInitialize(None)
    endpoint = ctypes.c_void_p()
    try:
        endpoint = get_default_capture_endpoint_volume()
        get_mute = com_method(endpoint, 15, ctypes.c_long, ctypes.POINTER(wintypes.BOOL))
        muted = wintypes.BOOL()
        check_hr(get_mute(endpoint, ctypes.byref(muted)), "IAudioEndpointVolume.GetMute")
        return bool(muted.value)
    finally:
        release(endpoint)
        ole32.CoUninitialize()


def set_mic_muted(muted: bool) -> None:
    ole32.CoInitialize(None)
    endpoint = ctypes.c_void_p()
    try:
        endpoint = get_default_capture_endpoint_volume()
        set_mute = com_method(endpoint, 14, ctypes.c_long, wintypes.BOOL, ctypes.POINTER(GUID))
        event_context = GUID.from_string("F6C9199E-2178-4D63-99AB-5F0600D9219D")
        check_hr(set_mute(endpoint, wintypes.BOOL(1 if muted else 0), ctypes.byref(event_context)), "IAudioEndpointVolume.SetMute")
    finally:
        release(endpoint)
        ole32.CoUninitialize()


def toggle_mic() -> bool:
    muted = not get_mic_muted()
    set_mic_muted(muted)
    return muted


class StatusOverlay:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.muted: bool | None = None

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.82)
        root.resizable(False, False)

        self.label = tk.Label(
            root,
            text="检测中",
            fg="#ffffff",
            font=("Microsoft YaHei UI", 16, "bold"),
            padx=18,
            pady=10,
        )
        self.label.pack()
        self.place_top_right()
        self.enable_click_through()

    def place_top_right(self) -> None:
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = self.root.winfo_screenwidth() - width - 24
        y = 24
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.enable_click_through()

    def enable_click_through(self) -> None:
        self.root.update_idletasks()
        set_window_click_through(int(self.root.winfo_id()))

    def set_state(self, muted: bool) -> None:
        if self.muted == muted:
            return
        self.muted = muted
        text = "已闭麦" if muted else "已开麦"
        bg = "#b91c1c" if muted else "#047857"
        self.root.configure(bg=bg)
        self.label.configure(text=text, bg=bg)
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.enable_click_through()

    def set_error(self) -> None:
        self.root.configure(bg="#374151")
        self.label.configure(text="麦克风异常", bg="#374151")
        self.enable_click_through()


class MicToggleApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = load_config()
        self.overlay = StatusOverlay(root)
        self.busy = threading.Lock()
        self.hotkey_was_down = False
        self.recording = False
        self.root.title(APP_NAME)
        self._build_menu()
        self.poll_state()
        self.poll_hotkey()

    def _build_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="立即切换麦克风", command=self.on_toggle)
        self.menu.add_command(label=f"设置热键（当前：{self.config['hotkey']}）", command=self.record_hotkey)
        self.menu.add_command(label="放回右上角", command=self.overlay.place_top_right)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.quit)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    def show_menu(self, event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def refresh_menu(self) -> None:
        self.menu.entryconfigure(1, label=f"设置热键（当前：{self.config['hotkey']}）")

    def record_hotkey(self) -> None:
        if self.recording:
            return
        self.recording = True
        dialog = tk.Toplevel(self.root)
        dialog.title(APP_NAME)
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)
        dialog.configure(bg="#111827")
        label = tk.Label(
            dialog,
            text="请按下新的热键组合\n支持键盘键和鼠标键",
            bg="#111827",
            fg="#ffffff",
            font=("Microsoft YaHei UI", 12, "bold"),
            padx=24,
            pady=18,
        )
        label.pack()
        preview = tk.Label(dialog, text="等待输入...", bg="#111827", fg="#93c5fd", font=("Microsoft YaHei UI", 11), padx=24, pady=8)
        preview.pack()
        dialog.update_idletasks()
        x = self.root.winfo_x() - dialog.winfo_width() - 12
        y = max(24, self.root.winfo_y())
        dialog.geometry(f"+{x}+{y}")

        stable_codes: list[int] = []
        stable_count = 0

        def finish(codes: list[int]) -> None:
            self.config["hotkey_codes"] = codes
            self.config["hotkey"] = format_hotkey(codes)
            save_config(self.config)
            self.refresh_menu()
            self.hotkey_was_down = True
            self.recording = False
            dialog.destroy()

        def tick() -> None:
            nonlocal stable_codes, stable_count
            if not dialog.winfo_exists():
                self.recording = False
                return
            codes = current_pressed_codes()
            if codes:
                preview.configure(text=format_hotkey(codes))
                if codes == stable_codes:
                    stable_count += 1
                else:
                    stable_codes = codes
                    stable_count = 0
                if stable_count >= 8:
                    finish(codes)
                    return
            dialog.after(35, tick)

        dialog.protocol("WM_DELETE_WINDOW", lambda: (setattr(self, "recording", False), dialog.destroy()))
        tick()

    def on_toggle(self) -> None:
        if not self.busy.acquire(blocking=False):
            return
        threading.Thread(target=self._toggle_worker, daemon=True).start()

    def _toggle_worker(self) -> None:
        try:
            muted = toggle_mic()
            self.root.after(0, lambda: self.overlay.set_state(muted))
        except Exception as exc:
            self.root.after(0, lambda: messagebox.showerror(APP_NAME, f"切换麦克风失败：{exc}"))
        finally:
            self.busy.release()

    def poll_state(self) -> None:
        threading.Thread(target=self._poll_worker, daemon=True).start()
        self.root.after(1000, self.poll_state)

    def _poll_worker(self) -> None:
        try:
            muted = get_mic_muted()
            self.root.after(0, lambda: self.overlay.set_state(muted))
        except Exception:
            self.root.after(0, self.overlay.set_error)

    def poll_hotkey(self) -> None:
        codes = self.config.get("hotkey_codes") or []
        active = bool(codes) and all(key_down(code) for code in codes)
        if active and not self.hotkey_was_down and not self.recording:
            self.on_toggle()
        self.hotkey_was_down = active
        self.root.after(35, self.poll_hotkey)

    def quit(self) -> None:
        self.root.destroy()


def main() -> int:
    ensure_single_instance()
    relaunch_as_admin()
    root = tk.Tk()
    MicToggleApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
