# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files


a = Analysis(
    ['utility_toolbox.py'],
    pathex=[],
    binaries=[],
    datas=[('icons\\utility_toolbox.ico', 'icons'), *collect_data_files('tkinterdnd2')],
    hiddenimports=[
        'keyboard',
        'pyautogui',
        'imagehash',
        'pillow_heif',
        'win32com.client',
        'tkinterdnd2',
        'tkinterdnd2.TkinterDnD',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='UtilityToolbox',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icons\\utility_toolbox.ico'],
)
