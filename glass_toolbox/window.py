"""Acrylic desktop shell. All tools stay mounted when navigating."""

import ctypes
import json
import os
from ctypes import wintypes

from PySide6.QtCore import Qt, QTimer, QRectF, QPoint, QSaveFile, QIODevice
from PySide6.QtGui import QColor, QPainter, QPen, QLinearGradient, QShortcut, QKeySequence, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout,
    QStackedWidget, QScrollArea, QLineEdit, QCompleter, QSystemTrayIcon,
    QMenu, QDialog, QSlider, QSizeGrip, QKeySequenceEdit,
)

from .backend import Backend, core
from .native import apply_acrylic
from .hotkeys import SummonHotkey, DEFAULT_SUMMON_HOTKEY
from .widgets import STYLE, Card, button, label, row, icon, ui_font, quick_action
from .pages_files import RenamerPage, SubtitlePage, MediaPage
from .pages_workspace import LauncherPage, WindowPage
from .pages_tools import MicPage, AudioPage, MacroPage, StartupPage, BackupPage, LivePage


CATEGORIES = [
    ("home", "工作台", "\ue80f", "常用工具，随手可达", []),
    ("files", "文件处理", "\ue8b7", "先预览结果，再放心执行", [
        ("rename", "批量重命名", RenamerPage), ("subtitle", "字幕同步", SubtitlePage), ("media", "照片视频整理", MediaPage)]),
    ("workspace", "应用与窗口", "\ue737", "管理应用分组，让窗口各就其位", [
        ("launcher", "应用启动器", LauncherPage), ("windows", "窗口置顶", WindowPage)]),
    ("audio", "音频控制", "\ue767", "输出设备与麦克风，集中控制", [
        ("mic", "麦克风控制", MicPage), ("output", "音频输出", AudioPage)]),
    ("automation", "自动化", "\ue945", "把重复操作交给工具", [
        ("macro", "宏录制与回放", MacroPage), ("startup", "启动与系统", StartupPage)]),
    ("extras", "更多工具", "\ue713", "存档与直播，快速打开", [
        ("backup", "游戏存档", BackupPage), ("live", "直播入口", LivePage)]),
]


class TitleBar(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.owner = window
        box = QHBoxLayout(self)
        box.setContentsMargins(22, 8, 10, 8)
        box.addWidget(label("TOOLBOX   /   工具箱", "eyebrow"))
        box.addStretch()
        self.pin = button("", lambda: window.set_self_topmost(self.pin.isChecked()), "ghost", "\ue718")
        self.pin.setCheckable(True)
        self.pin.setFixedSize(40, 34)
        self.pin.setToolTip("置顶工具箱")
        self.pin.setAccessibleName("置顶工具箱")
        box.addWidget(self.pin)
        for glyph, tip, callback in (("\ue921", "最小化", window.showMinimized),
                                     ("\ue922", "最大化 / 还原", self.toggle_maximized),
                                     ("\ue8bb", "关闭到托盘", window.close)):
            control = button("", callback, "ghost", glyph)
            control.setFixedSize(40, 34)
            control.setToolTip(tip)
            control.setAccessibleName(tip)
            if glyph == "\ue8bb":
                control.setObjectName("close")
            box.addWidget(control)

    def toggle_maximized(self):
        self.owner.showNormal() if self.owner.isMaximized() else self.owner.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.owner.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximized()


class GlassWindow(QWidget):
    def __init__(self, *, tray=True):
        super().__init__()
        self.setWindowTitle("工具箱 · 原生毛玻璃")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(960, 650)
        self.settings_path = core.APP_DIR / "glass_ui.json"
        self.settings = self.read_settings()
        self.tint = self.settings.get("tint", 42)
        self.acrylic = False
        self._quitting = False
        self._hotkey_registered = False
        self.summon_hotkey = None
        self.summon_error = ""
        self.hotkey_text = "置顶快捷键未注册"
        self.tray = None
        self.setFont(ui_font())
        self.setStyleSheet(STYLE)
        self.setWindowIcon(icon("\ue80f", "#9bbfff", 32))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        self.titlebar = TitleBar(self)
        outer.addWidget(self.titlebar)
        body = QHBoxLayout()
        body.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(202)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(16, 20, 16, 18)
        nav.setSpacing(7)
        nav.addWidget(label("你的工具箱", "section"))
        nav.addWidget(label("UTILITY WORKSPACE", "brand"))
        nav.addSpacing(23)
        self.nav_buttons = {}
        for key, title, glyph, _description, _pages in CATEGORIES:
            control = button(title, lambda k=key: self.navigate_category(k), "nav", glyph)
            control.setCheckable(True)
            self.nav_buttons[key] = control
            nav.addWidget(control)
        nav.addStretch()
        self.material_label = label("原生毛玻璃", "muted")
        nav.addWidget(self.material_label)
        nav.addWidget(button("外观与窗口", self.appearance, "ghost", "\ue790"))
        nav.addWidget(button("退出工具箱", self.quit_app, "ghost", "\ue7e8"))
        body.addWidget(sidebar)
        content = QWidget()
        content_box = QVBoxLayout(content)
        content_box.setContentsMargins(28, 22, 28, 14)
        content_box.setSpacing(17)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(6)
        self.title = label("工作台", "title")
        self.subtitle = label("常用工具，随手可达", "subtitle")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        heading.addLayout(titles, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索工具    Ctrl K")
        self.search.setAccessibleName("搜索工具")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(245)
        heading.addWidget(self.search, 0, Qt.AlignTop)
        content_box.addLayout(heading)
        self.tabs = QWidget()
        self.tabs_box = QHBoxLayout(self.tabs)
        self.tabs_box.setContentsMargins(0, 0, 0, 0)
        self.tabs_box.setSpacing(5)
        content_box.addWidget(self.tabs)
        self.stack = QStackedWidget()
        content_box.addWidget(self.stack, 1)
        self.status = label("就绪", "muted")
        self.status.setWordWrap(True)
        self.status.setMaximumHeight(48)
        content_box.addLayout(row(self.status, None, QSizeGrip(self)))
        body.addWidget(content, 1)
        outer.addLayout(body, 1)
        self.backend = Backend(self, self.notify)
        self.pages, self.routes, self.category_last = {}, {}, {}
        self._tab_buttons = {}
        home = self.build_home()
        self.add_page("home", home)
        for category, _title, _glyph, _description, pages in CATEGORIES:
            for key, title, page_class in pages:
                self.routes[key] = (category, title)
                self.add_page(key, page_class(self.backend))
        self.completer = QCompleter([title for _category, title in self.routes.values()], self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.completer.popup().setFont(ui_font())
        self.completer.popup().setStyleSheet("QAbstractItemView { background:#1b293b; color:#e4edf9; border:1px solid #4c6080; padding:5px; } QAbstractItemView::item { padding:9px 10px; } QAbstractItemView::item:selected { background:#344d70; color:#ffffff; }")
        self.completer.activated.connect(self.search_route)
        self.search.setCompleter(self.completer)
        self.search.returnPressed.connect(lambda: self.search_route(self.search.text()))
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.focus_search)
        QShortcut(QKeySequence("Escape"), self.search, activated=self.search.clear)
        if tray:
            self.setup_tray()
            self.register_hotkey()
            self.summon_hotkey = SummonHotkey(self.winId())
            try:
                saved = self.settings.get("summon_hotkey", DEFAULT_SUMMON_HOTKEY)
                if not isinstance(saved, str):
                    raise ValueError("已保存的唤出快捷键格式无效，请在“外观与窗口”中重新设置。")
                self.summon_hotkey.change(saved)
            except (ValueError, OSError) as exc:
                self.summon_error = str(exc)
                self.notify("唤出快捷键未启用：" + str(exc))
        self.navigate_category("home")
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1260, available.width() - 40), min(850, available.height() - 40))
        self.move(available.center() - self.rect().center())

    def read_settings(self):
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return {}
            if type(value.get("tint")) is not int or not 20 <= value["tint"] <= 150:
                value.pop("tint", None)
            return value
        except (OSError, ValueError):
            return {}

    def add_page(self, key, page):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        scroll.viewport().setAutoFillBackground(False)
        page.setAutoFillBackground(False)
        self.pages[key] = page
        self.stack.addWidget(scroll)
        page._scroll = scroll

    def build_home(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(18)
        hero = Card()
        hero.setObjectName("hero")
        hero.box.setContentsMargins(28, 26, 28, 26)
        hero.box.addWidget(label("让操作更轻一点。", "title"))
        hero.box.addWidget(label("文件整理、应用管理、设备控制。\n从这里开始，少一点寻找，多一点专注。", "subtitle", True))
        hero.box.addSpacing(10)
        hero.box.addLayout(row(button("打开应用启动器", lambda: self.navigate("launcher"), "primary", "\ue768"),
                               button("批量处理文件", lambda: self.navigate("rename")), None))
        box.addWidget(hero)
        box.addWidget(label("快捷打开", "section"))
        grid = QGridLayout()
        grid.setSpacing(14)
        for index, (key, title, description, glyph) in enumerate([
            ("rename", "批量重命名", "预览、改名与撤回", "\ue8ac"),
            ("windows", "窗口置顶", "把需要的窗口留在前面", "\ue718"),
            ("mic", "麦克风控制", "设备、热键与悬浮窗", "\ue720"),
            ("subtitle", "字幕同步", "剧集和字幕，整齐对齐", "\ue8f2"),
            ("macro", "宏录制与回放", "重复操作，一次设定", "\ue945"),
            ("backup", "游戏存档", "备份、恢复与版本管理", "\ue74e"),
        ]):
            control = quick_action(title, description, lambda k=key: self.navigate(k), glyph)
            grid.addWidget(control, index // 3, index % 3)
        box.addLayout(grid)
        hint = Card("操作提示")
        hint.box.addWidget(label("Ctrl K 搜索工具  ·  Tab 切换焦点  ·  双击标题栏最大化\n关闭窗口后可从托盘再次打开；退出请使用左下角“退出工具箱”。", "muted", True))
        box.addWidget(hint)
        box.addStretch()
        return page

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def search_route(self, text):
        matches = [key for key, (_category, title) in self.routes.items() if text.strip() and text.casefold() in title.casefold()]
        exact = [key for key in matches if self.routes[key][1] == text]
        if exact or len(matches) == 1:
            self.navigate((exact or matches)[0])
            self.search.clear()
        elif not matches:
            self.notify("没有找到工具，请换一个关键词。")
        else:
            self.completer.complete()

    def navigate_category(self, category):
        pages = next(item[4] for item in CATEGORIES if item[0] == category)
        self.navigate(self.category_last.get(category, pages[0][0] if pages else "home"))

    def navigate(self, key):
        category = self.routes.get(key, ("home", ""))[0]
        self.category_last[category] = key
        for cat, title, _glyph, description, pages in CATEGORIES:
            self.nav_buttons[cat].setChecked(cat == category)
            if cat != category:
                continue
            self.title.setText(title)
            self.subtitle.setText(description)
            while self.tabs_box.count():
                item = self.tabs_box.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._tab_buttons = {}
            for page_key, page_title, _cls in pages:
                tab = button(page_title, lambda k=page_key: self.navigate(k), "tab")
                tab.setCheckable(True)
                tab.setChecked(page_key == key)
                self.tabs_box.addWidget(tab)
                self._tab_buttons[page_key] = tab
            self.tabs_box.addStretch()
            self.tabs.setVisible(bool(pages))
        self.stack.setCurrentWidget(self.pages[key]._scroll)

    def notify(self, message):
        self.status.setText(str(message))
        self.status.setToolTip(str(message))

    def appearance(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("外观与窗口")
        dialog.resize(540, 440)
        box = QVBoxLayout(dialog)
        box.setContentsMargins(24, 24, 24, 24)
        box.setSpacing(18)
        box.addWidget(label("界面字体：HarmonyOS Sans SC · 鸿蒙黑体", "muted", True))
        box.addWidget(label("玻璃深浅", "section"))
        box.addWidget(label("向左更通透，向右提高背景遮盖。文字始终清晰。", "muted", True))
        slider = QSlider(Qt.Horizontal)
        slider.setRange(20, 150)
        slider.setValue(self.tint)
        slider.setAccessibleName("玻璃遮盖强度")
        slider.valueChanged.connect(self.set_tint)
        box.addWidget(slider)
        box.addWidget(label("唤出工具箱", "section"))
        box.addWidget(label("点击输入框录入单键或组合键，再点击应用。按一次唤出并置顶，再按一次隐藏到托盘。", "muted", True))
        editor = QKeySequenceEdit()
        editor.setMaximumSequenceLength(1)
        editor.setFinishingKeyCombinations([])
        editor.setAccessibleName("唤出工具箱快捷键")
        saved = self.settings.get("summon_hotkey", DEFAULT_SUMMON_HOTKEY)
        editor.setKeySequence(QKeySequence(saved if isinstance(saved, str) else ""))
        state = label(self.summon_status(), "muted", True)
        def apply():
            try:
                self.apply_summon_hotkey(editor.keySequence().toString(QKeySequence.PortableText))
            except (ValueError, OSError) as exc:
                state.setText(str(exc) + "\n" + self.summon_status())
            else:
                state.setText(self.summon_status())
        box.addLayout(row(editor, button("清除", editor.clear, "ghost"), button("应用快捷键", apply, "primary")))
        box.addWidget(state)
        box.addWidget(label("支持 F8、字母、数字等单键及 Ctrl / Alt / Shift 组合键。单独字母或空格会占用对应输入，建议使用功能键。清除后应用可停用。", "muted", True))
        box.addWidget(label(self.hotkey_text + "：切换前台窗口置顶", "muted", True))
        box.addLayout(row(button("恢复默认", lambda: slider.setValue(42), "ghost"), None,
                          button("完成", dialog.accept, "primary")))
        dialog.exec()
        try:
            self.save_ui_settings({"tint": self.tint})
        except (ValueError, OSError) as exc:
            self.notify("外观已应用，但设置无法保存：" + str(exc))

    def save_ui_settings(self, updates):
        # Merge with the latest file so changing a hotkey does not erase tint
        # or another instance's unrelated settings. Invalid files stay intact.
        try:
            current = json.loads(self.settings_path.read_text(encoding="utf-8")) if self.settings_path.exists() else {}
        except (ValueError, OSError) as exc:
            raise ValueError("原设置文件无法读取，已保留：" + str(exc)) from exc
        if not isinstance(current, dict):
            raise ValueError("原设置文件格式无效，已保留。")
        current.update(updates)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        file = QSaveFile(str(self.settings_path))
        if not file.open(QIODevice.WriteOnly):
            raise OSError(file.errorString())
        data = json.dumps(current, ensure_ascii=False, indent=2).encode("utf-8")
        if file.write(data) != len(data):
            file.cancelWriting()
            raise OSError("设置未完整写入，原文件已保留。")
        if not file.commit():
            raise OSError(file.errorString())
        self.settings = current

    def summon_status(self):
        if self.summon_error:
            return "唤出快捷键未启用：" + self.summon_error
        text = self.summon_hotkey.text if self.summon_hotkey else ""
        return f"当前生效：{text}" if text else "唤出快捷键已停用。"

    def apply_summon_hotkey(self, text):
        if self.summon_hotkey is None:
            self.summon_hotkey = SummonHotkey(self.winId())
        canonical = self.summon_hotkey.change(text, lambda value: self.save_ui_settings({"summon_hotkey": value}))
        self.summon_error = ""
        self.notify(f"唤出快捷键已保存：{canonical}" if canonical else "唤出快捷键已停用。")
        return canonical

    def set_tint(self, value):
        self.tint = value
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.enable_glass)

    def enable_glass(self):
        self.acrylic = apply_acrylic(self.winId())
        self.material_label.setText("原生毛玻璃" if self.acrylic else "兼容背景")
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        opacity = self.tint if self.acrylic else 245
        gradient.setColorAt(0, QColor(26, 37, 57, opacity + 20 if opacity < 230 else opacity))
        gradient.setColorAt(1, QColor(15, 23, 37, opacity))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(215, 232, 255, 55), 1))
        painter.drawRoundedRect(rect, 12 if not self.isMaximized() else 0, 12 if not self.isMaximized() else 0)

    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("工具箱 · 原生毛玻璃")
        self.tray_menu = QMenu()
        self.tray_menu.addAction("打开工具箱", self.show_window)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("置顶当前窗口", self.backend.topmost.pin_current)
        self.tray_menu.addAction("取消当前窗口置顶", self.backend.topmost.unpin_current)
        self.tray_menu.addAction("取消本工具的全部置顶", self.backend.topmost.unpin_all)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("退出工具箱", self.quit_app)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def register_hotkey(self):
        for text, modifiers, key in core.TrayIcon.HOTKEY_CANDIDATES:
            if ctypes.windll.user32.RegisterHotKey(wintypes.HWND(int(self.winId())), 2001, modifiers, key):
                self._hotkey_registered = True
                self.hotkey_text = text
                return

    def nativeEvent(self, event_type, message):
        if os.name == "nt":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312:
                # Recording one of our active combinations must not trigger
                # a summon or pin an unrelated foreground window.
                recorded = self.hotkey_text if msg.wParam == 2001 else ""
                summon = getattr(self, "summon_hotkey", None)
                if summon is not None and msg.wParam == summon.identifier:
                    recorded = summon.text
                focus = QApplication.focusWidget()
                while recorded and focus is not None:
                    if isinstance(focus, QKeySequenceEdit):
                        focus.setKeySequence(QKeySequence(recorded))
                        return True, 0
                    focus = focus.parentWidget()
            if msg.message == 0x0312 and msg.wParam == 2001 and hasattr(self, "backend"):
                QTimer.singleShot(0, self.backend.topmost.toggle_foreground)
                return True, 0
            if msg.message == 0x0312 and getattr(self, "summon_hotkey", None) is not None and msg.wParam == self.summon_hotkey.identifier:
                QTimer.singleShot(0, self.toggle_visibility)
                return True, 0
            if msg.message == 0x0084 and not self.isMaximized():
                x = ctypes.c_short(msg.lParam & 0xffff).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xffff).value
                pos = self.mapFromGlobal(QPoint(x, y))
                left, right = pos.x() < 7, pos.x() >= self.width() - 7
                top, bottom = pos.y() < 7, pos.y() >= self.height() - 7
                hit = (13 if left else 14 if right else 12) if top else ((16 if left else 17 if right else 15) if bottom else (10 if left else 11 if right else 0))
                if hit:
                    return True, hit
        return super().nativeEvent(event_type, message)

    def toggle_visibility(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_window()

    def show_window(self):
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.show()
        self.set_self_topmost(True)
        self.raise_()
        self.activateWindow()
        modal = QApplication.activeModalWidget()
        if modal and modal is not self:
            modal.raise_()
            modal.activateWindow()

    def set_self_topmost(self, enabled):
        try:
            hwnd = int(self.winId())
            # Do not change Qt window flags: that can recreate the HWND and
            # invalidate both native hotkey registrations and Acrylic.
            core.win32gui.SetWindowPos(hwnd, -1 if enabled else -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            actual = bool(core.win32gui.GetWindowLong(hwnd, -20) & 0x00000008)
            self.titlebar.pin.setChecked(actual)
            self.titlebar.pin.setToolTip("取消工具箱置顶" if actual else "置顶工具箱")
            self.titlebar.pin.setAccessibleName("取消工具箱置顶" if actual else "置顶工具箱")
        except Exception as exc:
            self.notify("工具箱置顶未完成：" + str(exc))

    def closeEvent(self, event):
        if self._quitting:
            event.accept()
        elif self.tray and self.tray.isVisible():
            event.ignore()
            self.hide()
        else:
            event.ignore()
            self.quit_app()

    def quit_app(self):
        if self.backend.active_jobs():
            self.backend.dialogs.showwarning("任务仍在进行", "请等待当前操作完成；字幕任务也可以先点击“停止任务”。窗口可以最小化，任务会继续运行。")
            return False
        self._quitting = True
        if self._hotkey_registered:
            ctypes.windll.user32.UnregisterHotKey(wintypes.HWND(int(self.winId())), 2001)
        if self.summon_hotkey:
            self.summon_hotkey.close()
        if self.tray:
            self.tray.hide()
        self.backend.stop()
        self.close()
        QApplication.instance().quit()
        return True
