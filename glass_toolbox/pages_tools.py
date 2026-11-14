"""Device, automation and companion tools, with explicit settings actions."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QDialog, QKeySequenceEdit, QMenu,
)

from .backend import core
from .adapters import WidgetPort
from .widgets import Card, Switch, button, label, row, field


def layout(page):
    box = QVBoxLayout(page)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(16)
    return box


class MicPage(QWidget):
    def __init__(self, backend):
        super().__init__()
        self.backend, self.engine = backend, backend.system
        e = self.engine
        outer = layout(self)
        launch = Card("麦克风悬浮开关", "用悬浮窗查看静音状态，用快捷键切换麦克风。")
        launch.box.addLayout(row(button("启动悬浮窗", e.open_mic_toggle, "primary", "\ue720"),
                                 button("关闭悬浮窗", e.close_mic_toggle, "ghost"), None))
        outer.addWidget(launch)
        device = Card("设备与显示")
        self.devices = QComboBox()
        e.mic_target_var.bind(self.devices)
        e.mic_target_combo = WidgetPort(self.devices, backend.dispatcher)
        device.box.addWidget(label("快捷键控制的麦克风", "muted"))
        device.box.addLayout(row(self.devices, button("刷新", e.refresh_mic_devices, "ghost"), button("应用设备", e.apply_mic_target)))
        size = QSpinBox()
        size.setRange(60, 120)
        size.setSuffix(" %")
        e.mic_overlay_scale_var.bind(size)
        device.box.addLayout(row(field("悬浮窗大小", size), None, button("应用大小", e.apply_mic_overlay_scale)))
        outer.addWidget(device)
        keys = Card("快捷键", "支持组合键、F1–F12、MouseX1 / MouseX2。修改后点击应用。")
        enabled = Switch("启用麦克风快捷键")
        e.mic_hotkey_enabled_var.bind(enabled)
        self.hotkey = QLineEdit()
        e.mic_hotkey_var.bind(self.hotkey)
        keys.box.addWidget(enabled)
        keys.box.addLayout(row(self.hotkey, button("录制按键", self.record), button("应用快捷键", e.apply_mic_hotkey)))
        outer.addWidget(keys)
        outer.addStretch()
        e.refresh_mic_devices(False)

    def record(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("录制麦克风快捷键")
        dialog.resize(460, 180)
        box = QVBoxLayout(dialog)
        box.addWidget(label("按下需要的组合键，或选择鼠标侧键。", "subtitle", True))
        editor = QKeySequenceEdit(dialog)
        editor.setMaximumSequenceLength(1)
        box.addWidget(editor)
        def accept(text):
            self.engine.mic_hotkey_var.set(text)
            dialog.accept()
        box.addLayout(row(button("MouseX1", lambda: accept("MouseX1")),
                          button("MouseX2", lambda: accept("MouseX2")), None,
                          button("使用按键", lambda: accept(editor.keySequence().toString(QKeySequence.PortableText)), "primary")))
        editor.setFocus()
        dialog.exec()


class AudioPage(QWidget):
    def __init__(self, backend):
        super().__init__()
        outer = layout(self)
        card = Card("切换音频输出", "选择已配置的设备，切换 Windows 默认输出。")
        for device in core.AUDIO_DEVICES:
            card.box.addLayout(row(label(device["name"], "section"), None,
                                     button("切换到此设备", lambda d=device: backend.audio.set_audio(d), glyph="\ue767")))
        outer.addWidget(card)
        outer.addWidget(label("设备未连接时会显示原因，原有输出不会被替换为其他设备。", "muted", True))
        outer.addStretch()


class MacroPage(QWidget):
    def __init__(self, backend):
        super().__init__()
        self.backend, self.engine = backend, backend.macro
        e = self.engine
        outer = layout(self)
        record = Card("录制一个宏", "在独立录制器中记录操作，保存为 JSON 后在下方回放。")
        record.box.addLayout(row(button("打开宏录制器", e.launch_recorder, glyph="\ue7c8"), None))
        outer.addWidget(record)
        play = Card("循环回放", "检查文件与触发键后启动，回放器会在独立窗口中运行。")
        path = QLineEdit()
        e.macro_json_var.bind(path)
        play.box.addWidget(label("宏文件", "muted"))
        play.box.addLayout(row(path, button("选择 JSON", e.choose_macro_json)))
        hotkey = QLineEdit()
        e.loop_hotkey_var.bind(hotkey)
        interval = QDoubleSpinBox()
        interval.setRange(0, 3600)
        interval.setDecimals(2)
        interval.setSingleStep(0.1)
        interval.setSuffix(" 秒")
        e.loop_interval_var.bind(interval)
        play.box.addLayout(row(field("开始 / 停止触发键", hotkey), field("循环间隔", interval)))
        self.more = button("更多操作", self.menu, "ghost")
        play.box.addLayout(row(self.more, None, button("启动循环回放", e.launch_loop_player, "primary", "\ue768")))
        outer.addWidget(play)
        outer.addStretch()

    def menu(self):
        e = self.engine
        menu = QMenu(self)
        menu.addAction("打开当前宏所在目录", e.open_macro_json_folder)
        menu.addAction("打开默认宏文件", e.open_default_macro_json)
        menu.addAction("打开宏工具目录", e.open_macro_tools_folder)
        menu.addSeparator()
        menu.addAction("创建桌面回放启动器…", self.desktop_launcher)
        menu.addAction("安装脚本依赖…", self.install)
        menu.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def desktop_launcher(self):
        target = Path.home() / "Desktop" / "run_interception_macro.bat"
        if self.backend.dialogs.askyesno("创建回放启动器", f"将写入此文件{'（覆盖已有文件）' if target.exists() else ''}：\n{target}"):
            self.engine.create_desktop_loop_bat()

    def install(self):
        if self.backend.dialogs.askyesno("安装脚本依赖", "将打开命令窗口，向本机 Python 安装宏脚本所需依赖。使用内置 EXE 通常无需安装。确认继续？"):
            self.engine.install_requirements()


class StartupPage(QWidget):
    def __init__(self, backend):
        super().__init__()
        outer = layout(self)
        e = backend.startup
        card = Card("登录 Windows 后启动", "开启后工具箱会在登录时启动，并最小化到托盘。")
        switch = Switch("开机启动工具箱")
        e.autostart_var.bind(switch)
        switch.clicked.connect(lambda _checked: e.toggle_autostart())
        card.box.addWidget(switch)
        status = label("", "muted")
        e.state_var.bind(status)
        card.box.addWidget(status)
        card.box.addLayout(row(button("刷新当前状态", e.refresh_state, "ghost"), None))
        outer.addWidget(card)
        shell = Card("管理员终端", "打开 PowerShell 7，Windows 会请求管理员权限。")
        shell.box.addLayout(row(button("打开管理员 PowerShell 7", backend.system.open_admin_powershell7), None))
        outer.addWidget(shell)
        outer.addStretch()


class BackupPage(QWidget):
    def __init__(self, backend):
        super().__init__()
        e = backend.backup
        outer = layout(self)
        card = Card("游戏存档管理", "在 Game Save Manager 中备份、恢复和管理版本历史。")
        status, location = label("", "section"), label("", "muted", True)
        e.status.bind(status)
        e.location.bind(location)
        card.box.addWidget(status)
        card.box.addWidget(location)
        launch = button("打开存档管理器", e.open_manager, "primary", "\ue74e")
        e.open_button = WidgetPort(launch, backend.dispatcher)
        card.box.addLayout(row(launch, button("选择程序位置", e.choose_executable, "ghost"), None))
        outer.addWidget(card)
        legacy = Card("以前的备份", "原有备份和目录配置已保留。旧 ZIP 备份与 Game Save Manager 的备份格式分别管理。")
        legacy.box.addLayout(row(button("打开旧备份目录", e.open_legacy_backups), None))
        outer.addWidget(legacy)
        outer.addStretch()
        e.refresh_location()


class LivePage(QWidget):
    def __init__(self, backend):
        super().__init__()
        outer = layout(self)
        card = Card("直播快捷入口", "在默认浏览器中打开直播间。")
        for title, url, _mode in core.LiveShortcutTab.SHORTCUTS:
            card.box.addLayout(row(label(title, "section"), None,
                                     button("打开直播", lambda t=title, u=url: backend.live.open_shortcut(t, u, None), glyph="\ue8a7")))
        outer.addWidget(card)
        outer.addStretch()
