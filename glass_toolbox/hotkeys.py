"""Global summon hotkey with conflict-safe replacement and rollback."""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

DEFAULT_SUMMON_HOTKEY = "Ctrl+Alt+Space"


def parse_hotkey(text):
    sequence = QKeySequence(text, QKeySequence.PortableText)
    if sequence.count() != 1 or sequence.isEmpty():
        raise ValueError("请录入一个单键或组合键，例如 F8 或 Ctrl+Alt+Space。")
    chord = sequence[0]
    modifiers = chord.keyboardModifiers()
    allowed = Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier
    if modifiers.value & ~allowed.value:
        raise ValueError("支持单键及 Ctrl / Alt / Shift 组合键，不支持 Windows 键。")
    key = chord.key().value
    if ord('0') <= key <= ord('9') or ord('A') <= key <= ord('Z'):
        vk = key
    elif Qt.Key_F1.value <= key <= Qt.Key_F24.value and key != Qt.Key_F12.value:
        vk = 0x70 + key - Qt.Key_F1.value
    else:
        vk = {
            Qt.Key_Space.value: 0x20, Qt.Key_Tab.value: 0x09,
            Qt.Key_Return.value: 0x0D, Qt.Key_Enter.value: 0x0D,
            Qt.Key_Backspace.value: 0x08, Qt.Key_Delete.value: 0x2E,
            Qt.Key_Insert.value: 0x2D, Qt.Key_Home.value: 0x24,
            Qt.Key_End.value: 0x23, Qt.Key_PageUp.value: 0x21,
            Qt.Key_PageDown.value: 0x22, Qt.Key_Left.value: 0x25,
            Qt.Key_Up.value: 0x26, Qt.Key_Right.value: 0x27,
            Qt.Key_Down.value: 0x28, Qt.Key_Escape.value: 0x1B,
        }.get(key)
    if vk is None:
        raise ValueError("请使用字母、数字、空格、方向键或功能键；F12 为系统保留键。")
    native_modifiers = 0x4000  # MOD_NOREPEAT
    for flag, value in ((Qt.ControlModifier, 2), (Qt.AltModifier, 1), (Qt.ShiftModifier, 4)):
        if modifiers & flag:
            native_modifiers |= value
    return sequence.toString(QKeySequence.PortableText), native_modifiers, vk


class WindowsHotkeys:
    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL

    def register(self, hwnd, identifier, modifiers, key):
        if not self.user32.RegisterHotKey(hwnd, identifier, modifiers, key):
            error = ctypes.get_last_error()
            raise ValueError(f"快捷键已被占用或无法注册（Windows 错误 {error}）。请换一个组合键。")

    def unregister(self, hwnd, identifier):
        if not self.user32.UnregisterHotKey(hwnd, identifier):
            raise OSError(ctypes.get_last_error(), "无法释放快捷键")


class SummonHotkey:
    def __init__(self, hwnd, api=None):
        self.hwnd = int(hwnd)
        self.api = api if api is not None else WindowsHotkeys()
        self.identifier = None
        self.text = ""

    def change(self, text, persist=lambda _value: None):
        parsed = parse_hotkey(text) if text.strip() else None
        canonical = parsed[0] if parsed else ""
        if canonical == self.text:
            persist(canonical)
            return canonical
        candidate = (2003 if self.identifier == 2002 else 2002) if parsed else None
        if parsed:
            self.api.register(self.hwnd, candidate, parsed[1], parsed[2])
        try:
            # Keep the old registration until the replacement is both registered
            # and saved. A disk error or conflict cannot strand the user.
            persist(canonical)
        except Exception:
            if candidate is not None:
                self.api.unregister(self.hwnd, candidate)
            raise
        if self.identifier is not None:
            try:
                self.api.unregister(self.hwnd, self.identifier)
            except OSError:
                if candidate is not None:
                    self.api.unregister(self.hwnd, candidate)
                persist(self.text)
                raise
        self.identifier, self.text = candidate, canonical
        return canonical

    def close(self):
        if self.identifier is not None:
            self.api.unregister(self.hwnd, self.identifier)
            self.identifier = None
            self.text = ""
