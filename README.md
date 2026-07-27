# Windows Utility Toolbox

A local Windows Tkinter utility toolbox packaged with PyInstaller.

## Key files

- `docs/PROJECT_HANDOFF.md` - detailed handoff document for maintainers and future agents

- `utility_toolbox.py` - main application
- `subtitle_sync_embedded.py` - embedded subtitle sync tool
- `media_organizer/` - media organizer module
- `fps_overlay/` - bundled TinyFpsOverlay runtime and PresentMon collector
- `UtilityToolbox.spec` - PyInstaller build configuration
- `icons/utility_toolbox.ico` - application icon
- `test_launcher_close.py` - drag-to-close/process matching tests
- `test_autostart.py` - autostart repair tests

## Build

```powershell
pyinstaller UtilityToolbox.spec --noconfirm
```

The packaged executable is generated at `dist/UtilityToolbox.exe`.

## FPS overlay

The `帧率监测` tab deploys the bundled `TinyFpsOverlay.exe` and PresentMon collector
to `%LOCALAPPDATA%\UtilityToolbox\fps_overlay`, then provides start, restart, stop,
status, hotkey, autostart, text color/size/weight, opacity, and config-folder
controls. The embedded overlay is launched in toolbox-managed mode, so it does not
show a separate TinyFpsOverlay tray icon. The stable deployment path keeps its
autostart entry valid even though the toolbox itself is packaged as one file.

The target system needs the .NET 8 Desktop Runtime.

## Microphone overlay helper

- `mic_toggle/一键麦克风开关.py` - click-through microphone status overlay and hotkey toggle helper
- `mic_toggle/一键麦克风开关.spec` - PyInstaller build configuration for the helper

The helper is intentionally mouse-click-through so it does not block game clicks, cannot be dragged accidentally, and does not respond to mouse clicks by default.

## Local consolidated layout

On this machine the full working folder is consolidated at `C:\Users\User\Desktop\Python\工具箱`. The runnable toolbox is `工具箱.exe`. The microphone helper workspace is stored locally under `mic_tool_workspace/` and the toolbox looks for `mic_tool_workspace/dist/一键麦克风开关.exe` first after relocation. For full handoff details, see `docs/PROJECT_HANDOFF.md`.


