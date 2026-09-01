from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import threading
import time
import uuid
import winreg
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox
import tkinter as tk


APP_NAME = "一键麦克风开关"
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "MicToggleHotkey"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_OVERLAY_SCALE = 0.75
MIN_OVERLAY_SCALE = 0.60
MAX_OVERLAY_SCALE = 1.20
DEFAULT_CONFIG = {
    "hotkey_enabled": True,
    "hotkey": "Ctrl+Alt+M",
    "hotkey_codes": [0x11, 0x12, 0x4D],
    "overlay_scale": DEFAULT_OVERLAY_SCALE,
    "target_endpoint_id": "",
    "target_device_name": "跟随 Windows 当前默认输入",
}
MUTEX_NAME = "Global\\MicToggleHotkeySingleInstance"

GWLP_WNDPROC = -4
GWL_EXSTYLE = -20
GA_ROOT = 2
WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
LWA_ALPHA = 0x00000002
_CLICK_THROUGH_WNDPROCS: dict[int, tuple[object, int]] = {}

CLSCTX_ALL = 23
E_CAPTURE = 1
ROLE_CONSOLE = 0
ROLE_COMMUNICATIONS = 2
DEFAULT_CAPTURE_ROLE = ROLE_CONSOLE
CAPTURE_DEVICE_REGISTRY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
ENDPOINT_NAME_PROPERTY = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"
FALLBACK_DEVICE_NAME = "当前默认麦克风"

OVERLAY_PANEL = "#101827"
OVERLAY_BORDER = "#334155"
OVERLAY_TEXT = "#F8FAFC"
OVERLAY_MUTED_TEXT = "#94A3B8"
OVERLAY_MUTED_ACCENT = "#FB7185"
OVERLAY_MUTED_ICON_BG = "#3B1722"
OVERLAY_ACTIVE_ACCENT = "#34D399"
OVERLAY_ACTIVE_ICON_BG = "#0C332A"
OVERLAY_ERROR_ACCENT = "#FBBF24"
OVERLAY_ERROR_ICON_BG = "#3A2A0A"

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
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
ole32.CoTaskMemFree.restype = None


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
    if not isinstance(config.get("hotkey_enabled"), bool):
        config["hotkey_enabled"] = DEFAULT_CONFIG["hotkey_enabled"]
    if not isinstance(config.get("target_endpoint_id"), str):
        config["target_endpoint_id"] = ""
    if not isinstance(config.get("target_device_name"), str) or not config["target_device_name"].strip():
        config["target_device_name"] = DEFAULT_CONFIG["target_device_name"]
    config["overlay_scale"] = normalize_overlay_scale(config.get("overlay_scale"))
    return config


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = CONFIG_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary_path, CONFIG_PATH)


def normalize_overlay_scale(value: object) -> float:
    try:
        scale = float(value)
    except (TypeError, ValueError):
        scale = DEFAULT_OVERLAY_SCALE
    return max(MIN_OVERLAY_SCALE, min(MAX_OVERLAY_SCALE, scale))


def key_down(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def set_window_click_through(hwnd: int) -> None:
    """Make a Windows Tk top-level window ignore mouse input.

    WS_EX_TRANSPARENT only becomes a reliable real mouse click-through style for
    this Tk overlay when paired with WS_EX_LAYERED. Keep the layered alpha at
    255 so the overlay remains visually solid instead of bleeding the desktop or
    game through the label.
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
    user32.CallWindowProcW.argtypes = [
        ctypes.c_void_p,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.CallWindowProcW.restype = ctypes.c_longlong
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    kernel32 = ctypes.windll.kernel32
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.GetCurrentProcessId.argtypes = []
    kernel32.GetCurrentProcessId.restype = wintypes.DWORD
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetLayeredWindowAttributes.argtypes = [
        wintypes.HWND,
        wintypes.COLORREF,
        ctypes.c_ubyte,
        wintypes.DWORD,
    ]
    user32.SetLayeredWindowAttributes.restype = wintypes.BOOL

    # Tk can expose a child/wrapper HWND through winfo_id(). Apply the style to
    # every plausible HWND in the parent chain plus the GA_ROOT top-level window.
    targets: list[int] = []
    current = int(hwnd)
    while current and current not in targets:
        targets.append(current)
        current = int(user32.GetParent(wintypes.HWND(current)) or 0)
    root = int(user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT) or 0)
    if root and root not in targets:
        targets.append(root)

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    def window_rect(target: int) -> tuple[int, int, int, int]:
        rect = RECT()
        user32.GetWindowRect(wintypes.HWND(target), ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom

    base_left, base_top, base_right, base_bottom = window_rect(int(hwnd))
    base_area = max(1, (base_right - base_left) * (base_bottom - base_top))

    def is_overlay_sized(target: int) -> bool:
        left, top, right, bottom = window_rect(target)
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            return False
        area = width * height
        # Keep this scoped to the tiny overlay and its same-sized Tk child/wrapper
        # HWNDs. Do not touch unrelated large Tk internals owned by the process.
        same_position = abs(left - base_left) <= 8 and abs(top - base_top) <= 8
        similar_size = area <= base_area * 4
        return same_position and similar_size

    # Tk may also create child HWNDs inside the visible top-level. If any child
    # remains hit-testable, clicks still get eaten by this process even when the
    # top-level has WS_EX_TRANSPARENT. Mark overlay-sized HWNDs owned by this
    # process, then force WM_NCHITTEST to return HTTRANSPARENT for each.
    current_pid = int(kernel32.GetCurrentProcessId())
    enum_windows_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect_owned_window(candidate: int) -> None:
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(wintypes.HWND(candidate), ctypes.byref(owner_pid))
        if owner_pid.value == current_pid and is_overlay_sized(candidate) and candidate not in targets:
            targets.append(candidate)

    def enum_child(hwnd_child, _lparam):
        collect_owned_window(int(hwnd_child))
        return True

    def enum_top(hwnd_top, _lparam):
        collect_owned_window(int(hwnd_top))
        user32.EnumChildWindows(wintypes.HWND(hwnd_top), enum_windows_proc(enum_child), 0)
        return True

    user32.EnumWindows(enum_windows_proc(enum_top), 0)

    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    SWP_FRAMECHANGED = 0x0020
    flags = SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED

    wndproc_type = ctypes.WINFUNCTYPE(
        ctypes.c_longlong,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    def subclass_for_hit_test(target: int) -> None:
        if target in _CLICK_THROUGH_WNDPROCS:
            return
        old_proc_holder: dict[str, int] = {"old": 0}

        def wndproc(hwnd_arg, msg, wparam, lparam):
            if msg == WM_NCHITTEST:
                return HTTRANSPARENT
            return user32.CallWindowProcW(
                ctypes.c_void_p(old_proc_holder["old"]),
                hwnd_arg,
                msg,
                wparam,
                lparam,
            )

        callback = wndproc_type(wndproc)
        old_proc = int(set_style(wintypes.HWND(target), GWLP_WNDPROC, ctypes.cast(callback, ctypes.c_void_p)) or 0)
        old_proc_holder["old"] = old_proc
        _CLICK_THROUGH_WNDPROCS[target] = (callback, old_proc)

    for target in targets:
        style = int(get_style(wintypes.HWND(target), GWL_EXSTYLE) or 0)
        style |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW
        set_style(wintypes.HWND(target), GWL_EXSTYLE, ctypes.c_void_p(style))
        user32.SetLayeredWindowAttributes(wintypes.HWND(target), 0, 255, LWA_ALPHA)
        subclass_for_hit_test(target)
        user32.SetWindowPos(wintypes.HWND(target), None, 0, 0, 0, 0, flags)


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
    return FALLBACK_DEVICE_NAME


def get_capture_endpoint_volume(
    role: int = DEFAULT_CAPTURE_ROLE,
    endpoint_id: str = "",
) -> tuple[ctypes.c_void_p, str]:
    enumerator = create_com_object("BCDE0395-E52F-467C-8E3D-C4579291692E", "A95664D2-9614-4F35-A746-DE8DB63617E6")
    device = ctypes.c_void_p()
    endpoint = ctypes.c_void_p()
    requested_endpoint_id = endpoint_id
    resolved_endpoint_id = ""
    try:
        if requested_endpoint_id:
            get_device = com_method(
                enumerator,
                5,
                ctypes.c_long,
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_void_p),
            )
            check_hr(
                get_device(enumerator, requested_endpoint_id, ctypes.byref(device)),
                "IMMDeviceEnumerator.GetDevice",
            )
        else:
            get_default = com_method(
                enumerator,
                4,
                ctypes.c_long,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_void_p),
            )
            check_hr(get_default(enumerator, E_CAPTURE, role, ctypes.byref(device)), "GetDefaultAudioEndpoint")

        raw_id = ctypes.c_wchar_p()
        try:
            get_id = com_method(device, 5, ctypes.c_long, ctypes.POINTER(ctypes.c_wchar_p))
            check_hr(get_id(device, ctypes.byref(raw_id)), "IMMDevice.GetId")
            resolved_endpoint_id = raw_id.value or ""
        finally:
            if raw_id:
                ole32.CoTaskMemFree(raw_id)

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
        return endpoint, resolved_endpoint_id
    finally:
        release(device)
        release(enumerator)


def endpoint_is_muted(endpoint: ctypes.c_void_p) -> bool:
    get_mute = com_method(endpoint, 15, ctypes.c_long, ctypes.POINTER(wintypes.BOOL))
    muted = wintypes.BOOL()
    check_hr(get_mute(endpoint, ctypes.byref(muted)), "IAudioEndpointVolume.GetMute")
    return bool(muted.value)


def set_endpoint_muted(endpoint: ctypes.c_void_p, muted: bool) -> None:
    set_mute = com_method(endpoint, 14, ctypes.c_long, wintypes.BOOL, ctypes.POINTER(GUID))
    event_context = GUID.from_string("F6C9199E-2178-4D63-99AB-5F0600D9219D")
    check_hr(set_mute(endpoint, wintypes.BOOL(1 if muted else 0), ctypes.byref(event_context)), "IAudioEndpointVolume.SetMute")


def get_mic_status(role: int = DEFAULT_CAPTURE_ROLE, endpoint_id: str = "") -> tuple[bool, str, str]:
    ole32.CoInitialize(None)
    endpoint = ctypes.c_void_p()
    try:
        endpoint, resolved_id = get_capture_endpoint_volume(role, endpoint_id)
        return endpoint_is_muted(endpoint), capture_device_name(resolved_id), resolved_id
    finally:
        release(endpoint)
        ole32.CoUninitialize()


def get_mic_muted(role: int = DEFAULT_CAPTURE_ROLE) -> bool:
    return get_mic_status(role)[0]


def set_mic_muted(muted: bool, role: int = DEFAULT_CAPTURE_ROLE, endpoint_id: str = "") -> None:
    ole32.CoInitialize(None)
    endpoint = ctypes.c_void_p()
    try:
        endpoint, _endpoint_id = get_capture_endpoint_volume(role, endpoint_id)
        set_endpoint_muted(endpoint, muted)
    finally:
        release(endpoint)
        ole32.CoUninitialize()


def toggle_mic_status(role: int = DEFAULT_CAPTURE_ROLE, endpoint_id: str = "") -> tuple[bool, str, str]:
    ole32.CoInitialize(None)
    endpoint = ctypes.c_void_p()
    try:
        endpoint, resolved_id = get_capture_endpoint_volume(role, endpoint_id)
        muted = not endpoint_is_muted(endpoint)
        set_endpoint_muted(endpoint, muted)
        return muted, capture_device_name(resolved_id), resolved_id
    finally:
        release(endpoint)
        ole32.CoUninitialize()


def toggle_mic(role: int = DEFAULT_CAPTURE_ROLE, endpoint_id: str = "") -> bool:
    return toggle_mic_status(role, endpoint_id)[0]


class StatusOverlay:
    def __init__(self, root: tk.Tk, scale: float = DEFAULT_OVERLAY_SCALE):
        self.root = root
        self.state_signature: tuple[bool, str] | None = None
        self.scale = normalize_overlay_scale(scale)
        self.current_accent = OVERLAY_ERROR_ACCENT
        self.current_icon_bg = OVERLAY_ERROR_ICON_BG
        self.current_muted = False

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        # Keep the overlay visually solid. Mouse pass-through is handled by
        # WS_EX_TRANSPARENT, so alpha transparency is unnecessary and can make
        # desktop/game content bleed through the label.
        root.attributes("-alpha", 1.0)
        root.resizable(False, False)

        root.configure(bg=OVERLAY_BORDER)
        self.card = tk.Frame(
            root,
            bg=OVERLAY_PANEL,
            highlightbackground=OVERLAY_BORDER,
            highlightthickness=0,
            bd=0,
        )
        self.card.pack()

        self.accent = tk.Frame(self.card, bg=OVERLAY_ERROR_ACCENT)
        self.accent.pack(side=tk.LEFT, fill=tk.Y)
        self.accent.pack_propagate(False)

        self.icon = tk.Canvas(
            self.card,
            bg=OVERLAY_PANEL,
            highlightthickness=0,
            bd=0,
        )
        self.icon.pack(side=tk.LEFT)

        self.text_area = tk.Frame(self.card, bg=OVERLAY_PANEL)
        self.text_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.status_label = tk.Label(
            self.text_area,
            text="正在检测麦克风",
            bg=OVERLAY_PANEL,
            fg=OVERLAY_TEXT,
            anchor=tk.W,
        )
        self.status_label.pack(anchor=tk.W)
        self.device_label = tk.Label(
            self.text_area,
            text="读取当前默认设备…",
            bg=OVERLAY_PANEL,
            fg=OVERLAY_MUTED_TEXT,
            anchor=tk.W,
        )
        self.device_label.pack(anchor=tk.W)
        self.set_scale(self.scale, force=True)
        self.enable_click_through()
        self.reinforce_click_through()

    def scaled(self, value: float) -> int:
        return max(1, round(value * self.scale))

    def set_scale(self, scale: float, force: bool = False) -> None:
        normalized = normalize_overlay_scale(scale)
        if not force and abs(normalized - self.scale) < 0.001:
            return
        self.scale = normalized
        self.card.configure(highlightthickness=max(1, self.scaled(1)))
        self.accent.configure(width=self.scaled(4), height=self.scaled(68))
        self.icon.configure(width=self.scaled(42), height=self.scaled(42))
        self.icon.pack_configure(
            padx=(self.scaled(12), self.scaled(10)),
            pady=self.scaled(13),
        )
        self.text_area.pack_configure(
            padx=(0, self.scaled(16)),
            pady=(self.scaled(11), self.scaled(10)),
        )
        self.status_label.configure(
            font=("Microsoft YaHei UI", max(7, self.scaled(13)), "bold"),
        )
        self.device_label.configure(
            font=("Microsoft YaHei UI", max(6, self.scaled(9))),
        )
        self.device_label.pack_configure(pady=(self.scaled(3), 0))
        self.draw_icon(self.current_accent, self.current_icon_bg, self.current_muted)
        self.place_top_right()
        self.reinforce_click_through()

    def draw_icon(self, accent: str, icon_bg: str, muted: bool) -> None:
        s = self.scaled
        self.icon.delete("all")
        self.icon.create_oval(s(1), s(1), s(41), s(41), fill=icon_bg, outline=icon_bg)
        self.icon.create_line(s(21), s(10), s(21), s(23), fill=accent, width=s(6), capstyle=tk.ROUND)
        self.icon.create_arc(s(12), s(14), s(30), s(31), start=180, extent=180, style=tk.ARC, outline=accent, width=s(2))
        self.icon.create_line(s(21), s(30), s(21), s(34), fill=accent, width=s(2))
        self.icon.create_line(s(16), s(34), s(26), s(34), fill=accent, width=s(2), capstyle=tk.ROUND)
        if muted:
            self.icon.create_line(s(10), s(32), s(32), s(10), fill=accent, width=s(3), capstyle=tk.ROUND)

    def place_top_right(self) -> None:
        # Clear the previous explicit geometry before measuring so a newly
        # selected device name can grow the card instead of being clipped.
        self.root.geometry("")
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        margin = self.scaled(24)
        x = self.root.winfo_screenwidth() - width - margin
        y = margin
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.enable_click_through()

    def enable_click_through(self) -> None:
        self.root.update_idletasks()
        set_window_click_through(int(self.root.winfo_id()))

    def reinforce_click_through(self) -> None:
        # Tk may rewrite extended window styles shortly after alpha/topmost
        # changes. Reapply a few times so the final real HWND remains
        # mouse-transparent.
        for delay_ms in (50, 150, 350, 750, 1500):
            self.root.after(delay_ms, self.enable_click_through)

    def set_state(self, muted: bool, device_name: str = FALLBACK_DEVICE_NAME) -> None:
        signature = (muted, device_name)
        if self.state_signature == signature:
            return
        self.state_signature = signature
        status_text = "麦克风已静音" if muted else "麦克风已开启"
        accent = OVERLAY_MUTED_ACCENT if muted else OVERLAY_ACTIVE_ACCENT
        icon_bg = OVERLAY_MUTED_ICON_BG if muted else OVERLAY_ACTIVE_ICON_BG
        self.current_accent = accent
        self.current_icon_bg = icon_bg
        self.current_muted = muted
        self.accent.configure(bg=accent)
        self.status_label.configure(text=status_text)
        self.device_label.configure(text=f"默认设备  ·  {device_name}")
        self.draw_icon(accent, icon_bg, muted)
        self.place_top_right()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.enable_click_through()

    def set_error(self) -> None:
        self.state_signature = None
        self.current_accent = OVERLAY_ERROR_ACCENT
        self.current_icon_bg = OVERLAY_ERROR_ICON_BG
        self.current_muted = False
        self.accent.configure(bg=OVERLAY_ERROR_ACCENT)
        self.status_label.configure(text="麦克风连接异常")
        self.device_label.configure(text="请检查当前默认输入设备")
        self.draw_icon(OVERLAY_ERROR_ACCENT, OVERLAY_ERROR_ICON_BG, muted=False)
        self.place_top_right()
        self.enable_click_through()


class MicToggleApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = load_config()
        self.overlay = StatusOverlay(root, self.config["overlay_scale"])
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
        self.menu.add_command(label=self.hotkey_menu_label(), command=self.record_hotkey)
        self.menu.add_command(label="放回右上角", command=self.overlay.place_top_right)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.quit)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    def show_menu(self, event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def refresh_menu(self) -> None:
        self.menu.entryconfigure(1, label=self.hotkey_menu_label())

    def hotkey_menu_label(self) -> str:
        state = "已启用" if self.config.get("hotkey_enabled", True) else "已关闭"
        return f"设置热键（{state}：{self.config['hotkey']}）"

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
            self.config["hotkey_enabled"] = True
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
            muted, device_name, _endpoint_id = toggle_mic_status(
                endpoint_id=self.config.get("target_endpoint_id", "")
            )
            self.root.after(0, lambda: self.overlay.set_state(muted, device_name))
        except Exception as exc:
            self.root.after(0, lambda: messagebox.showerror(APP_NAME, f"切换麦克风失败：{exc}"))
        finally:
            self.busy.release()

    def poll_state(self) -> None:
        latest_config = load_config()
        if latest_config != self.config:
            self.config = latest_config
            self.overlay.set_scale(self.config["overlay_scale"])
            self.refresh_menu()
        threading.Thread(target=self._poll_worker, daemon=True).start()
        self.root.after(1000, self.poll_state)

    def _poll_worker(self) -> None:
        try:
            muted, device_name, _endpoint_id = get_mic_status(
                endpoint_id=self.config.get("target_endpoint_id", "")
            )
            self.root.after(0, lambda: self.overlay.set_state(muted, device_name))
        except Exception:
            self.root.after(0, self.overlay.set_error)

    def poll_hotkey(self) -> None:
        codes = self.config.get("hotkey_codes") or []
        enabled = self.config.get("hotkey_enabled", True)
        active = bool(enabled and codes) and all(key_down(code) for code in codes)
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
