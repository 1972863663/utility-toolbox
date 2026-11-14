"""File workflows: choose -> preview -> apply, with stale-plan protection."""

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox, QSpinBox, QProgressBar

from .widgets import Card, button, label, row, field, table, log_view
from .adapters import WidgetPort, TreePort, LogPort, Value


class RenamerPage(QWidget):
    def __init__(self, backend):
        super().__init__()
        self.backend, self.engine = backend, backend.renamer
        self.busy = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)
        self.controls = Card("1  选择文件与规则", "先检查预览，再执行。原文件的撤回记录会保留。")
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setPlaceholderText("选择要批量处理的文件夹")
        self.controls.box.addLayout(row(self.path, button("选择文件夹", self.choose, glyph="\ue8b7")))
        self.mode = QComboBox()
        self.mode.addItems(["日期+序号", "前缀+序号", "查找替换", "添加前缀", "添加后缀", "转小写", "转大写"])
        self.engine.mode_var.bind(self.mode)
        self.prefix, self.find, self.replace = QLineEdit(), QLineEdit(), QLineEdit()
        self.engine.date_var.bind(self.prefix)
        self.engine.find_var.bind(self.find)
        self.engine.replace_var.bind(self.replace)
        self.start = QSpinBox()
        self.start.setRange(1, 999999)
        self.engine.start_var.bind(self.start)
        self.prefix_field = field("日期 / 前缀", self.prefix)
        self.find_field = field("查找内容", self.find)
        self.replace_field = field("替换内容 / 后缀", self.replace)
        self.start_field = field("起始序号", self.start)
        settings = QHBoxLayout()
        settings.addWidget(field("重命名规则", self.mode), 2)
        for widget in (self.prefix_field, self.find_field, self.replace_field, self.start_field):
            settings.addWidget(widget, 2 if widget is not self.start_field else 1)
        self.controls.box.addLayout(settings)
        outer.addWidget(self.controls)
        preview = Card("2  检查预览")
        self.summary = label("尚未选择文件夹。执行前会在这里列出原名和新名。", "muted", True)
        preview.box.addWidget(self.summary)
        self.table = table(["原文件名", "新文件名", "状态"], 1)
        self.table.setColumnWidth(0, 280)
        self.table.setColumnWidth(2, 145)
        preview.box.addWidget(self.table)
        self.engine.tree = TreePort(self.table, backend.dispatcher)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        preview.box.addWidget(self.progress)
        self.preview_button = button("预览改名结果", self.preview, glyph="\ue8a5")
        self.apply_button = button("执行重命名", self.apply, "primary")
        self.undo_button = button("撤回上次", self.undo, "ghost", "\ue7a7")
        preview.box.addLayout(row(self.undo_button, None, self.preview_button, self.apply_button))
        outer.addWidget(preview, 1)
        self._engine_preview = self.engine.preview
        self.engine.preview = self._engine_preview  # Existing apply/undo use the same tested planner.
        for variable in (self.engine.mode_var, self.engine.date_var, self.engine.find_var,
                         self.engine.replace_var, self.engine.start_var):
            variable.changed.connect(self.invalidate)
        self.mode.currentTextChanged.connect(self._rule_fields)
        self._rule_fields()
        self.invalidate()

    def _rule_fields(self, *_args):
        mode = self.mode.currentText()
        self.prefix_field.setVisible(mode in ("日期+序号", "前缀+序号", "添加前缀"))
        self.find_field.setVisible(mode == "查找替换")
        self.replace_field.setVisible(mode in ("查找替换", "添加后缀"))
        self.start_field.setVisible(mode in ("日期+序号", "前缀+序号"))

    def invalidate(self, *_args):
        if self.busy:
            return
        self.engine.plan.clear()
        self.apply_button.setEnabled(False)
        self.preview_button.setEnabled(self.engine.folder is not None)
        self.undo_button.setEnabled(self.engine.undo_file.exists())
        if self.engine.folder:
            self.summary.setText("规则已改变，请重新预览后再执行。")

    def choose(self):
        selected = self.backend.dialogs.askdirectory()
        if selected:
            self.engine.folder = Path(selected)
            self.engine.folder_var.set(selected)
            self.path.setText(selected)
            self.path.setToolTip(selected)
            self.preview()

    def _run(self, function, message):
        if self.busy:
            return
        self.busy = True
        self.controls.setEnabled(False)
        for control in (self.preview_button, self.apply_button, self.undo_button):
            control.setEnabled(False)
        self.summary.setText(message)
        self.progress.setRange(0, 0)
        self.progress.show()
        def finish():
            self.busy = False
            self.controls.setEnabled(True)
            self.progress.hide()
            ready = sum(status == "待改名" for _, _, status in self.engine.plan)
            self.summary.setText(f"共 {len(self.engine.plan)} 个文件 · {ready} 个待改名")
            self.preview_button.setEnabled(self.engine.folder is not None)
            self.apply_button.setEnabled(ready > 0)
            self.undo_button.setEnabled(self.engine.undo_file.exists())
        self.backend.run_job(function, finish)

    def preview(self):
        if self.engine.folder:
            self._run(self.engine.preview, "正在生成预览…")

    def apply(self):
        self._run(self.engine.apply, "正在执行重命名…")

    def undo(self):
        self._run(self.engine.undo_last, "正在恢复上一次改名…")


class SubtitlePage(QWidget):
    def __init__(self, backend):
        super().__init__()
        self.backend, self.engine = backend, backend.subtitle
        self.preview_signature = None
        self.preview_pending = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)
        self.controls = Card("1  选择剧集文件夹", "先预览匹配结果，再执行同步；更改目录或剧名后，需要重新预览。")
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setPlaceholderText("选择同时包含视频与字幕的目录")
        self.controls.box.addLayout(row(self.path, button("选择目录", self.choose, glyph="\ue8b7")))
        self.name = QLineEdit()
        self.name.setPlaceholderText("例如：三体")
        self.season = QSpinBox()
        self.season.setRange(0, 99)
        self.season.setValue(1)
        self.controls.box.addLayout(row(field("剧集名称", self.name), field("默认季数", self.season)))
        self.name_value = Value("").bind(self.name)
        self.season_value = Value("1").bind(self.season)
        d = backend.dispatcher
        self.engine.name_ent = WidgetPort(self.name, d, self.name_value)
        self.engine.season_ent = WidgetPort(self.season, d, self.season_value)
        outer.addWidget(self.controls)
        results = Card("2  匹配结果", "预览只分析名称。确认结果后，再执行文件修改。")
        self.log = log_view()
        results.box.addWidget(self.log, 1)
        self.engine.log_area = LogPort(self.log, d)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        results.box.addWidget(self.progress)
        self.engine.progress = WidgetPort(self.progress, d)
        self.preview_button = button("预览匹配", self.preview)
        self.apply_button = button("执行同步", self.apply, "primary")
        self.audit_button = button("检查字幕匹配", self.engine.run_audit, "ghost")
        self.stop_button = button("停止任务", self.engine.stop_task, "danger")
        self.undo_button = button("撤回此目录", self.engine.undo, "ghost", "\ue7a7")
        self.engine.btn_run = WidgetPort(self.preview_button, d)
        self.engine.btn_audit = WidgetPort(self.audit_button, d)
        self.engine.btn_stop = WidgetPort(self.stop_button, d)
        self.engine.btn_undo = WidgetPort(self.undo_button, d)
        results.box.addLayout(row(self.undo_button, self.audit_button, None, self.stop_button, self.preview_button, self.apply_button))
        outer.addWidget(results, 1)
        self.name.textChanged.connect(self.invalidate)
        self.season.valueChanged.connect(self.invalidate)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync_state)
        self.timer.start(150)
        self.sync_state()

    def signature(self):
        return (str(self.engine.target_dir), self.name.text(), self.season.value())

    def invalidate(self, *_args):
        self.preview_signature = None
        self.preview_pending = False
        self.apply_button.setEnabled(False)

    def choose(self):
        self.engine.select_dir()
        if self.engine.target_dir:
            self.path.setText(str(self.engine.target_dir))
            self.path.setToolTip(str(self.engine.target_dir))
            self.invalidate()

    def preview(self):
        if self.engine.is_running or self.engine._read_inputs() is None:
            return
        self.preview_signature = None
        self.engine.dry_run.set(True)
        self.engine.clear_log()
        # The worker may finish before start_task returns. Set pending first,
        # and wait for its queued summary rather than sampling is_running.
        self.preview_pending = True
        self._requested_signature = self.signature()
        self.engine.start_task()

    def apply(self):
        if self.preview_signature != self.signature() or self.engine.is_running:
            return
        if not self.backend.dialogs.askyesno("执行字幕同步", f"将按预览结果修改此目录中的文件名称：\n{self.engine.target_dir}\n\n执行后可以通过“撤回此目录”恢复。"):
            return
        self.preview_signature = None
        self.engine.dry_run.set(False)
        self.engine.start_task()

    def sync_state(self):
        running = self.engine.is_running
        if self.preview_pending and not running:
            # is_running can clear before queued log callbacks reach the UI.
            text = self.log.toPlainText()
            if any(marker in text for marker in ("任务失败：", "任务启动失败：", "任务已停止。")):
                self.preview_pending = False
            elif "完成统计：" in text:
                self.preview_pending = False
                self.preview_signature = self._requested_signature
        waiting = running or self.preview_pending
        self.controls.setEnabled(not waiting)
        valid = self.engine.target_dir is not None and bool(self.name.text().strip())
        self.preview_button.setEnabled(valid and not waiting)
        self.apply_button.setEnabled(not running and self.preview_signature == self.signature())
        self.audit_button.setEnabled(self.engine.target_dir is not None and not waiting)
        self.stop_button.setVisible(running)
        self.stop_button.setEnabled(running)
        history = self.engine.history_db.get(self.engine.target_dir.as_posix()) if self.engine.target_dir else None
        self.undo_button.setEnabled(bool(history) and not running)


class MediaPage(QWidget):
    def __init__(self, backend):
        super().__init__()
        self.backend, self.engine = backend, backend.media
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)
        self.controls = Card("1  选择源目录与输出目录", "先生成整理计划，确认统计和提示后再执行。")
        for caption, variable in (("原始照片 / 视频", self.engine.source_var), ("整理后的输出目录", self.engine.output_var)):
            entry = QLineEdit()
            variable.bind(entry)
            browse = button("浏览", lambda v=variable: self.choose(v), glyph="\ue8b7")
            self.controls.box.addWidget(label(caption, "muted"))
            self.controls.box.addLayout(row(entry, browse))
            variable.changed.connect(self.invalidate)
        outer.addWidget(self.controls)
        result = Card("2  整理预览")
        summary = label("", "muted", True)
        self.engine.summary_var.bind(summary)
        result.box.addWidget(summary)
        self.log = log_view()
        result.box.addWidget(self.log, 1)
        self.engine.log_text = LogPort(self.log, backend.dispatcher)
        self.scan_button = button("扫描并预览", self.engine.scan)
        self.execute_button = button("执行整理", self.execute, "primary")
        self.engine.scan_button = WidgetPort(self.scan_button, backend.dispatcher)
        self.engine.execute_button = WidgetPort(self.execute_button, backend.dispatcher)
        result.box.addLayout(row(None, self.scan_button, self.execute_button))
        outer.addWidget(result, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync_state)
        self.timer.start(200)
        self.sync_state()

    def choose(self, variable):
        folder = self.backend.dialogs.askdirectory()
        if folder:
            variable.set(folder)

    def invalidate(self, *_args):
        if not self.engine.busy:
            self.engine.manifest = None
            self.engine.summary_var.set("目录已改变，请重新扫描预览。")

    def execute(self):
        if self.backend.dialogs.askyesno("执行照片视频整理", "将按刚才的预览计划复制文件并生成报告。确认开始？"):
            self.engine.execute()

    def sync_state(self):
        self.controls.setEnabled(not self.engine.busy)
        ready = bool(self.engine.source_var.get().strip() and self.engine.output_var.get().strip())
        self.scan_button.setEnabled(ready and not self.engine.busy)
        self.execute_button.setEnabled(self.engine.manifest is not None and not self.engine.busy)
