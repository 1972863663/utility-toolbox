# Windows 实用工具箱 · 原生毛玻璃版

当前版本：**v2026.11.14.1**。主程序为 `工具箱-毛玻璃版.exe`，源码入口为 `toolbox_glass.py`。

[下载最新版](https://github.com/1972863663/utility-toolbox/releases/latest) · [当前维护说明](docs/PROJECT_HANDOFF.md) · [验证记录](docs/VALIDATION_2026-11-14.md) · [第三方许可](docs/THIRD_PARTY_GLASS.md)

## 使用

下载 Release 的 `UtilityToolbox-Glass-v2026.11.14.1.zip` 并解压后启动主程序。保留完整目录结构，宏和麦克风助手与主程序一起提供。

- Windows 原生 Acrylic 毛玻璃；鸿蒙黑体随应用内置，无需安装系统字体。
- 11 个工具入口：批量重命名、字幕同步、照片视频整理、应用启动器、窗口置顶、麦克风控制、音频输出、宏录制回放、启动与系统、游戏存档、直播入口。
- 在「外观与窗口」录入唤出快捷键，支持单键和 Ctrl / Alt / Shift 组合键。按一次唤出并置顶，再按一次隐藏到托盘。首次默认 Ctrl+Alt+Space；已有用户设置保留。
- 关闭窗口隐藏到托盘；退出使用「退出工具箱」。在「启动与系统」中开启登录后自启动，启动参数为 `--minimized-to-tray`。
- 应用启动器的“关闭所选 / 关闭整组”点击即执行；当前源码支持把程序、快捷方式、应用目录或普通文件拖到主窗口任意位置，松手会直接关闭对应程序或文件占用者。添加分组项目请用“添加应用”。不会删除拖入的文件。
- 文件操作先预览再执行，修改输入后需重新预览；界面保留页面输入与撤回记录。

毛玻璃效果取决于 Windows 版本和系统透明效果设置；应用不会自行修改系统透明设置。

## 数据与独立工具

用户设置、字幕历史、宏录制及存档资料保存在 `data/`，升级时请保留。发布包不包含个人配置、录制内容、游戏存档或备份。

- 宏助手：`macro_tools/`。
- 麦克风助手：`mic_tool_workspace/dist/一键麦克风开关.exe`。
- 游戏存档入口接入独立的 [Game Save Manager](https://github.com/dyang886/Game-Save-Manager/releases/latest)。发布包不含该第三方完整程序；可在界面中选择其程序位置。本地已有安装和备份无需移动。
- 宏回放的驱动/权限及设备名称配置依赖实际电脑环境。

## 开发与构建

使用 Windows、Python 3.12、PowerShell 7。核心依赖包括 PySide6、pywin32、psutil、Pillow、ImageHash、pillow-heif；源码中保留旧 Tk 业务引擎作为 Qt 适配层的依赖。

```powershell
python toolbox_glass.py
python -m PyInstaller --noconfirm ToolboxGlass.spec
```

构建输出 `dist/工具箱-毛玻璃版.exe`。不要删除 `utility_toolbox.py`、`toolbox_ui.py` 等依赖文件；它们不是旧版可执行程序。

无鼠标键盘输入的 Qt 验证：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest test_glass_hotkeys.py test_glass_workflows.py -q
python -m pytest test_glass_toolbox.py -q -k 'not native_summon and not toggle_alternates and not toggle_restores'
```

完整验证范围和未验证边界见验证记录。构建或运行测试不会自动启用系统自启动。
