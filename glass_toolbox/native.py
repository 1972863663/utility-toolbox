"""Per-window Acrylic, based on the MIT PythonBlurBehind implementation.

Reference: https://github.com/Peticali/PythonBlurBehind
The original license is retained in docs/THIRD_PARTY_GLASS.md.
This module never changes the user's Windows settings.
"""

import ctypes
from ctypes import wintypes
import sys


class AccentPolicy(ctypes.Structure):
    _fields_ = [("state", ctypes.c_uint), ("flags", ctypes.c_uint),
                ("color", ctypes.c_uint), ("animation", ctypes.c_uint)]


class CompositionData(ctypes.Structure):
    _fields_ = [("attribute", ctypes.c_int), ("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]


def apply_acrylic(hwnd, tint=18):
    if sys.platform != "win32":
        return False
    try:
        hwnd = wintypes.HWND(int(hwnd))
        policy = AccentPolicy(4, 2, (max(1, min(255, tint)) << 24) | 0x211810, 0)
        data = CompositionData(19, ctypes.addressof(policy), ctypes.sizeof(policy))
        apply = ctypes.windll.user32.SetWindowCompositionAttribute
        apply.argtypes = [wintypes.HWND, ctypes.POINTER(CompositionData)]
        apply.restype = wintypes.BOOL
        success = bool(apply(hwnd, ctypes.byref(data)))
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        dwm.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        for attribute, value in ((20, 1), (33, 2)):
            value = ctypes.c_int(value)
            dwm(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
        return success
    except (AttributeError, OSError):
        return False
