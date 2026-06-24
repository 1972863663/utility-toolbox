# Windows Utility Toolbox

A local Windows Tkinter utility toolbox packaged with PyInstaller.

## Key files

- `utility_toolbox.py` - main application
- `subtitle_sync_embedded.py` - embedded subtitle sync tool
- `media_organizer/` - media organizer module
- `UtilityToolbox.spec` - PyInstaller build configuration
- `icons/utility_toolbox.ico` - application icon
- `test_launcher_close.py` - drag-to-close/process matching tests
- `test_autostart.py` - autostart repair tests

## Build

```powershell
pyinstaller UtilityToolbox.spec --noconfirm
```

The packaged executable is generated at `dist/UtilityToolbox.exe`.
