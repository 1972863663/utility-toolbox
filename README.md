# Windows Utility Toolbox

A local Windows Tkinter utility toolbox packaged with PyInstaller.

## Key files

- `docs/PROJECT_HANDOFF.md` - detailed handoff document for maintainers and future agents

- `utility_toolbox.py` - main application
- `subtitle_sync_embedded.py` - embedded subtitle sync tool
- `media_organizer/` - media organizer module
- `UtilityToolbox.spec` - PyInstaller build configuration
- `icons/utility_toolbox.ico` - application icon
- `test_launcher_close.py` - drag-to-close/process matching tests
- `test_autostart.py` - autostart repair tests
- `test_system_tools.py` - PowerShell and microphone overlay configuration tests

## Build

```powershell
pyinstaller UtilityToolbox.spec --noconfirm
```

The packaged executable is generated at `dist/UtilityToolbox.exe`.

Closing the main window hides the toolbox to the Windows notification area instead of exiting.
If Windows Explorer or the taskbar restarts, the toolbox automatically restores its tray icon.

## Microphone overlay helper

- `mic_toggle/一键麦克风开关.py` - click-through microphone status overlay and hotkey toggle helper
- `mic_toggle/一键麦克风开关.spec` - PyInstaller build configuration for the helper

The helper is intentionally mouse-click-through so it does not block game clicks, cannot be dragged accidentally, and does not respond to mouse clicks by default.

The mute hotkey can either follow the current Windows primary default capture endpoint or lock onto a specific active microphone selected in `系统工具 → 选择要静音的麦克风`. The overlay shows the controlled device's name and reloads its saved settings while running. The same page can enable or disable the global hotkey, type or record a custom keyboard/mouse combination, and choose a proportional overlay scale from 60% to 120%; the default target follows Windows, the default hotkey is `Ctrl+Alt+M`, and the default scale is 75%.

Build the helper into the local workspace with:

```powershell
python -m PyInstaller mic_toggle\一键麦克风开关.spec --noconfirm --clean --distpath mic_tool_workspace\dist --workpath mic_tool_workspace\build\mic-toggle
```

## Local consolidated layout

On this machine the full working folder is consolidated at `C:\Users\User\Desktop\Python\工具箱`. The runnable toolbox is `工具箱.exe`. The microphone helper workspace is stored locally under `mic_tool_workspace/` and the toolbox looks for `mic_tool_workspace/dist/一键麦克风开关.exe` first after relocation. For full handoff details, see `docs/PROJECT_HANDOFF.md`.


