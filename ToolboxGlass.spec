# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import copy_metadata

a = Analysis(
    ['toolbox_glass.py'],
    pathex=[], binaries=[],
    datas=[('icons/utility_toolbox.ico', 'icons'),
           ('glass_toolbox/fonts', 'glass_toolbox/fonts'),
           ('docs/THIRD_PARTY_GLASS.md', 'licenses'),
           *copy_metadata('PySide6'), *copy_metadata('PySide6_Essentials'),
           *copy_metadata('shiboken6')],
    hiddenimports=['keyboard', 'pyautogui', 'imagehash', 'pillow_heif', 'win32com.client'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'tkinterdnd2'],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='工具箱-毛玻璃版', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    icon=['icons/utility_toolbox.ico'],
)
