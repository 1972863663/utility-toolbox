from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .models import AppConfig, ScanManifest
from .organizer import execute_plan, save_manifest
from .scanner import scan_media
from .utils import write_json


class ScanWorker(QThread):
    finished_scan = Signal(object)
    failed = Signal(str)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config

    def run(self) -> None:
        try:
            output = Path(self.config.output_dir)
            manifest = scan_media(self.config)
            output.mkdir(parents=True, exist_ok=True)
            write_json(output / "config.json", manifest.to_dict()["config"])
            save_manifest(manifest, output)
            self.finished_scan.emit(manifest)
        except Exception as exc:
            self.failed.emit(str(exc))


class ExecuteWorker(QThread):
    progress = Signal(str)
    finished_run = Signal(object)
    failed = Signal(str)

    def __init__(self, manifest: ScanManifest) -> None:
        super().__init__()
        self.manifest = manifest

    def run(self) -> None:
        try:
            result = execute_plan(self.manifest, self.progress.emit)
            self.finished_run.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("本地照片视频管家")
        self.resize(980, 700)
        self.manifest: ScanManifest | None = None
        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)

        title = QLabel("本地照片视频管家")
        title.setObjectName("Title")
        subtitle = QLabel("完全本地读取拍摄时间和 GPS 经纬度，按年份 / 月份整理，预览确认后再复制处理。")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        paths = QFrame()
        paths.setObjectName("Panel")
        paths_layout = QGridLayout(paths)
        paths_layout.setContentsMargins(18, 16, 18, 16)
        paths_layout.setHorizontalSpacing(12)
        paths_layout.setVerticalSpacing(12)

        self.source = QLineEdit()
        self.output = QLineEdit()
        self.source.setPlaceholderText("选择网盘下载后的照片/视频目录")
        self.output.setPlaceholderText("选择整理后的输出目录")
        self._add_path_row(paths_layout, 0, "源目录", self.source)
        self._add_path_row(paths_layout, 1, "输出目录", self.output)
        layout.addWidget(paths)

        actions = QHBoxLayout()
        self.scan_button = QPushButton("扫描预览")
        self.scan_button.setObjectName("PrimaryButton")
        self.execute_button = QPushButton("确认执行")
        self.execute_button.setObjectName("SecondaryButton")
        self.execute_button.setEnabled(False)
        actions.addWidget(self.scan_button)
        actions.addWidget(self.execute_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        self.summary = QLabel("等待扫描")
        self.summary.setObjectName("Summary")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        stats = QHBoxLayout()
        self.file_card = self._stat_card("文件", "0")
        self.unknown_card = self._stat_card("未知日期", "0")
        self.duplicate_card = self._stat_card("重复组", "0")
        self.location_card = self._stat_card("有地点", "0")
        self.video_card = self._stat_card("视频大小", "0 MB")
        for card in [self.file_card, self.unknown_card, self.location_card, self.duplicate_card, self.video_card]:
            stats.addWidget(card)
        layout.addLayout(stats)

        log_title = QLabel("处理日志")
        log_title.setObjectName("SectionTitle")
        layout.addWidget(log_title)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("扫描和执行结果会显示在这里。")
        layout.addWidget(self.log, 1)

        self.setCentralWidget(root)
        self.scan_button.clicked.connect(self.scan)
        self.execute_button.clicked.connect(self.execute)

    def _add_path_row(self, layout: QGridLayout, row: int, label: str, line_edit: QLineEdit) -> None:
        label_widget = QLabel(label)
        label_widget.setObjectName("FieldLabel")
        button = QPushButton("选择")
        button.clicked.connect(lambda: self._pick_directory(line_edit))
        layout.addWidget(label_widget, row, 0)
        layout.addWidget(line_edit, row, 1)
        layout.addWidget(button, row, 2)

    def _stat_card(self, label: str, value: str) -> QFrame:
        card = QFrame()
        card.setObjectName("StatCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        value_label = QLabel(value)
        value_label.setObjectName("StatValue")
        name_label = QLabel(label)
        name_label.setObjectName("StatLabel")
        layout.addWidget(value_label)
        layout.addWidget(name_label)
        card.value_label = value_label  # type: ignore[attr-defined]
        return card

    def _pick_directory(self, line_edit: QLineEdit) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择目录")
        if directory:
            line_edit.setText(directory)

    def scan(self) -> None:
        if not self.source.text() or not self.output.text():
            QMessageBox.warning(self, "缺少目录", "请先选择源目录和输出目录。")
            return
        config = AppConfig(source_dir=self.source.text(), output_dir=self.output.text())
        self._busy(True, "正在读取照片和视频的拍摄时间...")
        self.log.clear()
        self.worker = ScanWorker(config)
        self.worker.finished_scan.connect(self._scan_done)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def execute(self) -> None:
        if not self.manifest:
            return
        self._busy(True, "正在执行复制、重复报告和视频压缩...")
        self.execute_worker = ExecuteWorker(self.manifest)
        self.execute_worker.progress.connect(self.log.appendPlainText)
        self.execute_worker.finished_run.connect(self._execute_done)
        self.execute_worker.failed.connect(self._failed)
        self.execute_worker.start()

    def _scan_done(self, manifest: ScanManifest) -> None:
        self.manifest = manifest
        unknown_dates = sum(1 for item in manifest.items if item.date == "Unknown-Date")
        located = sum(1 for item in manifest.items if item.gps_lat is not None and item.gps_lon is not None)
        duplicate_groups = len(manifest.exact_duplicates) + len(manifest.similar_duplicates)
        video_size = sum(item.size_bytes for item in manifest.items if item.media_type == "video")
        metadata_warnings = sum(1 for item in manifest.items if item.errors)

        self.summary.setText(
            f"预览完成：将按年份和月份整理。"
            f"元数据警告 {metadata_warnings} 个；确认执行前不会改动源目录。"
        )
        self._set_card(self.file_card, str(len(manifest.items)))
        self._set_card(self.unknown_card, str(unknown_dates))
        self._set_card(self.location_card, str(located))
        self._set_card(self.duplicate_card, str(duplicate_groups))
        self._set_card(self.video_card, f"{video_size / 1024 / 1024:.1f} MB")

        self.log.appendPlainText(f"scan_manifest.json 已生成到 {manifest.config.output_dir}")
        self.log.appendPlainText("目录结构：输出目录 / 年份 / 年份-月份 / Photos 或 Videos")
        self.log.appendPlainText("地点功能：只读取文件内嵌 GPS 经纬度，不联网反查城市名。")
        warning_counter: Counter[str] = Counter()
        warning_examples: dict[str, str] = {}
        for item in manifest.items:
            for error in item.errors:
                warning_counter[error] += 1
                warning_examples.setdefault(error, Path(item.source_path).name)
        for error, count in warning_counter.most_common():
            example = warning_examples[error]
            self.log.appendPlainText(f"{error}；共 {count} 个文件，例如 {example}")
        self.execute_button.setEnabled(True)
        self._busy(False)

    def _execute_done(self, result: dict) -> None:
        self.summary.setText(
            f"执行完成：复制 {len(result['copied'])}；"
            f"压缩成功 {len(result['compressed'])}；"
            f"压缩跳过/失败 {len(result['skipped'])}；"
            f"复制失败 {len(result['failed'])}。地点报告已生成。"
        )
        self.log.appendPlainText(f"地点 CSV：{result.get('locations_report_csv')}")
        self.log.appendPlainText(f"地点 HTML：{result.get('locations_report_html')}")
        self._busy(False)

    def _failed(self, message: str) -> None:
        QMessageBox.critical(self, "任务失败", message)
        self.log.appendPlainText(message)
        self._busy(False)

    def _busy(self, busy: bool, message: str | None = None) -> None:
        self.scan_button.setEnabled(not busy)
        if self.manifest:
            self.execute_button.setEnabled(not busy)
        self.progress.setRange(0, 0 if busy else 1)
        self.progress.setValue(0 if busy else 1)
        if message:
            self.summary.setText(message)

    def _set_card(self, card: QFrame, value: str) -> None:
        card.value_label.setText(value)  # type: ignore[attr-defined]

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f5f7fb;
                color: #20242a;
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QLabel#Title {
                font-size: 24px;
                font-weight: 700;
                color: #17202a;
            }
            QLabel#Subtitle {
                color: #667085;
                padding-bottom: 2px;
            }
            QLabel#SectionTitle {
                font-size: 15px;
                font-weight: 700;
                color: #344054;
            }
            QLabel#FieldLabel {
                color: #475467;
                font-weight: 600;
            }
            QFrame#Panel, QFrame#StatCard {
                background: #ffffff;
                border: 1px solid #d8dee8;
                border-radius: 8px;
            }
            QLineEdit {
                min-height: 32px;
                padding: 4px 10px;
                border: 1px solid #cfd6e2;
                border-radius: 6px;
                background: #ffffff;
            }
            QLineEdit:focus {
                border-color: #2f6fed;
            }
            QPushButton {
                min-height: 32px;
                padding: 4px 16px;
                border-radius: 6px;
                border: 1px solid #cfd6e2;
                background: #ffffff;
                color: #1f2937;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #eef4ff;
                border-color: #9db8ff;
            }
            QPushButton#PrimaryButton {
                background: #2563eb;
                color: #ffffff;
                border-color: #2563eb;
            }
            QPushButton#PrimaryButton:hover {
                background: #1d4ed8;
            }
            QPushButton#SecondaryButton {
                background: #0f766e;
                color: #ffffff;
                border-color: #0f766e;
            }
            QPushButton:disabled {
                background: #eef1f5;
                color: #98a2b3;
                border-color: #d8dee8;
            }
            QProgressBar {
                height: 8px;
                border: 0;
                border-radius: 4px;
                background: #e4e9f2;
            }
            QProgressBar::chunk {
                border-radius: 4px;
                background: #2563eb;
            }
            QLabel#Summary {
                padding: 12px 14px;
                background: #eef6ff;
                border: 1px solid #c7ddff;
                border-radius: 8px;
                color: #18426b;
            }
            QLabel#StatValue {
                font-size: 22px;
                font-weight: 700;
                color: #111827;
            }
            QLabel#StatLabel {
                color: #667085;
            }
            QPlainTextEdit {
                border: 1px solid #d8dee8;
                border-radius: 8px;
                background: #ffffff;
                padding: 10px;
                font-family: Consolas, "Microsoft YaHei UI", monospace;
                font-size: 13px;
            }
            """
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
