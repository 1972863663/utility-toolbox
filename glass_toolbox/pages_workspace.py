"""Application groups and window control, with explicit operation targets."""

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QMenu, QSplitter

from .adapters import TreePort, ListPort, LogPort
from .widgets import Card, button, label, row, table, log_view


class DropCloseCard(Card):
    def __init__(self, engine):
        super().__init__("拖到这里直接关闭", "程序 / 快捷方式：关闭对应应用；文件夹：关闭目录内运行的程序；普通文件：结束占用者。")
        self.engine = engine
        self.setAcceptDrops(True)
        self.setToolTip("松手立即执行，不删除文件。请先保存目标应用中的工作。")

    def accepts(self, event):
        return (not getattr(self.engine, "_launcher_close_pending", False)
                and event.mimeData().hasUrls()
                and all(url.isLocalFile() for url in event.mimeData().urls()))

    def dragEnterEvent(self, event):
        if self.accepts(event):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self.accepts(event):
            paths = [url.toLocalFile() for url in event.mimeData().urls()]
            self.engine.close_paths(paths)
            event.acceptProposedAction()


class LauncherPage(QWidget):
    def __init__(self, backend):
        super().__init__()
        self.backend, self.engine = backend, backend.launcher
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)
        split = QSplitter(Qt.Horizontal)
        groups = Card("应用分组")
        self.groups = QListWidget()
        self.groups.setMinimumWidth(145)
        groups.box.addWidget(self.groups, 1)
        groups.box.addWidget(button("新建分组", self.engine.create_group, glyph="\ue710"))
        self.more_button = button("分组管理", self.group_menu, "ghost")
        groups.box.addWidget(self.more_button)
        split.addWidget(groups)
        self.engine.group_list = ListPort(self.groups)
        apps = Card("分组中的应用", "拖入程序、快捷方式或文件即可添加；双击列表项启动。")
        self.group_title = label("", "muted")
        apps.box.addWidget(self.group_title)
        self.table = table(["名称", "类型", "完整路径"], 2)
        self.table.setColumnWidth(0, 190)
        self.table.setColumnWidth(1, 85)
        apps.box.addWidget(self.table, 1)
        self.engine.app_tree = TreePort(self.table, backend.dispatcher)
        self.add_button = button("添加应用", self.engine.add_apps, glyph="\ue710")
        self.remove_button = button("移出分组", self.remove, "ghost")
        self.launch_button = button("启动所选", self.engine.launch_selected, "primary", "\ue768")
        self.launch_group_button = button("启动整组", self.engine.launch_current_group)
        apps.box.addLayout(row(self.add_button, self.remove_button, None, self.launch_group_button, self.launch_button))
        close_row = row(label("关闭会结束对应应用，请先保存工作。", "muted", True), None)
        self.close_selected = button("关闭所选", self.close_selection, "danger")
        self.close_group = button("关闭整组", self.close_current_group, "danger")
        close_row.addWidget(self.close_selected)
        close_row.addWidget(self.close_group)
        apps.box.addLayout(close_row)
        split.addWidget(apps)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([200, 780])
        outer.addWidget(split, 1)
        self.drop_close = DropCloseCard(self.engine)
        outer.addWidget(self.drop_close)
        self.log = log_view()
        self.log.setMaximumHeight(125)
        self.log.setPlaceholderText("启动、关闭与路径修复的结果会显示在这里。")
        self.engine.log_text = LogPort(self.log, backend.dispatcher)
        outer.addWidget(self.log)
        self.groups.currentRowChanged.connect(lambda _row: self.engine.select_group())
        self.table.doubleClicked.connect(lambda _index: self.engine.launch_selected())
        self.engine.refresh_groups()
        self.engine.refresh_apps()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync_state)
        self.timer.start(180)
        self.sync_state()

    def group_menu(self):
        menu = QMenu(self)
        menu.addAction("重命名当前分组", self.engine.rename_group)
        menu.addAction("修复当前组的失效路径", self.engine.repair_current_group)
        menu.addSeparator()
        menu.addAction("删除当前分组…", self.engine.delete_group)
        menu.exec(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))

    def remove(self):
        count = len(self.engine.app_tree.selection())
        if count and self.backend.dialogs.askyesno("移出分组", f"将 {count} 个项目从当前分组移除。磁盘上的文件不会删除。"):
            self.engine.remove_selected()

    def close_selection(self):
        paths = [self.engine.app_tree.item(token, "values")[2] for token in self.engine.app_tree.selection()]
        if paths:
            self.engine.close_paths(paths)

    def close_current_group(self):
        group = self.engine.current_group
        count = len(self.engine.groups.get(group, {}).get("apps", []))
        if count:
            self.engine.close_current_group()

    def sync_state(self):
        closing = bool(getattr(self.engine, "_launcher_close_pending", False))
        busy = bool(getattr(self.engine, "_launcher_busy", False) or closing)
        selected = bool(self.table.selectionModel().selectedRows())
        has_apps = bool(self.engine.groups.get(self.engine.current_group, {}).get("apps", []))
        self.group_title.setText(f"{self.engine.current_group or '未选择分组'} · {self.table.rowCount()} 个项目" + (" · 操作进行中…" if busy else ""))
        for control in (self.launch_button, self.remove_button):
            control.setEnabled(selected and not busy)
        self.launch_group_button.setEnabled(has_apps and not busy)
        self.close_selected.setEnabled(selected and not closing)
        self.close_group.setEnabled(has_apps and not closing)
        self.add_button.setEnabled(not busy)
        self.more_button.setEnabled(not busy)
        self.groups.setEnabled(not busy)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not (getattr(self.engine, "_launcher_busy", False) or
                                             getattr(self.engine, "_launcher_close_pending", False)):
            if all(url.isLocalFile() for url in event.mimeData().urls()):
                event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.engine.current_group or getattr(self.engine, "_launcher_busy", False) or getattr(self.engine, "_launcher_close_pending", False):
            return
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        group = self.engine.groups[self.engine.current_group]["apps"]
        existing = {str(Path(item["path"])).casefold() for item in group}
        added = 0
        for path in paths:
            if path.casefold() not in existing:
                group.append({"name": Path(path).name, "path": path, "type": self.engine.file_type(path)})
                existing.add(path.casefold())
                added += 1
        if added:
            self.engine.save_config()
            self.engine.refresh_groups()
            self.engine.refresh_apps()
            self.engine.log(f"已添加 {added} 个项目到 {self.engine.current_group}")
        event.acceptProposedAction()


class WindowPage(QWidget):
    def __init__(self, backend):
        super().__init__()
        self.backend, self.engine = backend, backend.topmost
        self.snapshot = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = Card("选择需要置顶的窗口", "先选择目标，再置顶或取消；不会影响其他窗口。")
        self.table = table(["窗口标题", "状态", "句柄"], 0)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 110)
        self.port = TreePort(self.table)
        card.box.addWidget(self.table, 1)
        self.pin = button("置顶所选", lambda: self.apply(True), "primary", "\ue718")
        self.unpin = button("取消所选置顶", lambda: self.apply(False))
        self.table.itemSelectionChanged.connect(self.sync_selection)
        card.box.addLayout(row(button("刷新列表", self.refresh, "ghost"), None, self.unpin, self.pin))
        card.box.addLayout(row(label("也可以用鼠标点选一个桌面窗口。", "muted"), None,
                               button("鼠标点选", self.engine.select_by_mouse),
                               button("取消本工具的全部置顶", self.engine.unpin_all, "ghost")))
        outer.addWidget(card)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1200)
        self.refresh()

    def refresh(self):
        windows = [(hwnd, title, self.engine.is_topmost(hwnd)) for hwnd, title in self.engine.list_windows()]
        selected = self.selected()
        if windows != self.snapshot:
            self.snapshot = windows
            self.table.setRowCount(0)
            for hwnd, title, pinned in windows:
                token = self.port.insert("", "end", values=(title, "已置顶" if pinned else "普通窗口", hwnd))
                if hwnd == selected:
                    self.port.selection_set(token)
        self.sync_selection()

    def sync_selection(self):
        valid = self.selected() is not None
        self.pin.setEnabled(valid)
        self.unpin.setEnabled(valid)

    def selected(self):
        rows = self.table.selectionModel().selectedRows()
        return int(self.table.item(rows[0].row(), 2).text()) if rows else None

    def apply(self, enabled):
        target = self.selected()
        if target is not None:
            (self.engine.pin_window if enabled else self.engine.unpin_window)(target)
            self.refresh()
