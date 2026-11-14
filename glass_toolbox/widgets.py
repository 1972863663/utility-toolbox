"""Reusable, keyboard-accessible controls for the glass toolbox."""

from html import escape
from functools import lru_cache
from pathlib import Path
import warnings

from PySide6.QtCore import Qt, QSize, Signal, QRectF, Property, QPropertyAnimation
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QIcon, QPixmap, QFontDatabase
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QTableWidget,
    QHeaderView, QAbstractItemView, QPlainTextEdit, QLineEdit, QCheckBox,
)


@lru_cache(maxsize=1)
def ui_font_family():
    """Load bundled, unmodified HarmonyOS fonts for this application only."""
    for weight in ("Regular", "Medium"):
        path = Path(__file__).resolve().parent / "fonts" / f"HarmonyOS_Sans_SC_{weight}.ttf"
        if QFontDatabase.addApplicationFont(str(path)) == -1:
            warnings.warn(f"Unable to load UI font: {path}", RuntimeWarning)
    available = set(QFontDatabase.families())
    return next((name for name in ("HarmonyOS Sans SC", "Microsoft YaHei UI", "Noto Sans SC") if name in available), "Segoe UI")


def ui_font():
    font = QFont(ui_font_family())
    font.setPixelSize(14)
    font.setWeight(QFont.Normal)
    font.setStyleStrategy(QFont.PreferAntialias)
    return font


STYLE = """
QWidget { font-size:14px; font-weight:400; color:#eef3ff; background:transparent; }
QFrame#sidebar { background:rgba(16,24,38,85); border-right:1px solid rgba(255,255,255,18); }
QFrame#card { background:rgba(23,32,47,116); border:1px solid rgba(230,240,255,30); border-radius:16px; }
QFrame#hero { background:rgba(54,79,111,80); border:1px solid rgba(180,211,255,45); border-radius:18px; }
QLabel#title { font-size:26px; font-weight:500; color:#f5f8ff; }
QLabel#subtitle { color:#c2cddd; font-size:14px; }
QLabel#section { color:#f1f5ff; font-size:16px; font-weight:500; }
QLabel#muted { color:#b0bfd2; font-size:13px; }
QLabel#eyebrow { color:#a4bfdf; font-size:12px; font-weight:500; }
QLabel#brand { color:#b0bfd2; font-size:11px; }
QLabel#actionTitle { color:#edf3ff; font-size:15px; font-weight:500; }
QLabel#actionDescription { color:#b0bfd2; font-size:13px; }
QPushButton[role='quick'] { text-align:left; padding:0; border-radius:13px; min-height:102px; }
QPushButton { min-height:20px; padding:10px 16px; background:rgba(255,255,255,12); border:1px solid rgba(255,255,255,20); border-radius:9px; color:#dce7f7; font-weight:500; }
QPushButton:hover { background:rgba(170,204,255,27); border-color:rgba(180,213,255,55); }
QPushButton:pressed { background:rgba(130,180,255,45); }
QPushButton:focus { border:1px solid #9ebfff; }
QPushButton:disabled { color:#748299; background:rgba(255,255,255,5); border-color:rgba(255,255,255,9); }
QPushButton[role='primary'] { background:#89b4ff; color:#10213a; border-color:#a6c6ff; font-weight:500; }
QPushButton[role='primary']:hover { background:#a2c5ff; }
QPushButton[role='primary']:disabled { color:#7f94af; background:rgba(85,117,161,55); border-color:rgba(140,175,220,30); }
QPushButton[role='danger'] { background:rgba(210,91,112,14); color:#f3a4b4; border-color:rgba(227,129,146,35); }
QPushButton[role='danger']:hover { background:rgba(210,91,112,40); }
QPushButton[role='ghost'] { background:transparent; border-color:transparent; }
QPushButton[role='nav'] { text-align:left; padding:12px 16px; border-color:transparent; background:transparent; color:#aabbd4; }
QPushButton[role='nav']:checked { background:rgba(128,176,255,32); border:1px solid rgba(161,198,255,48); color:#eef5ff; }
QPushButton[role='nav']:hover { background:rgba(170,204,255,18); color:#f5f8ff; }
QPushButton[role='tab'] { padding:7px 14px; background:transparent; border-color:transparent; color:#aebfd8; }
QPushButton[role='tab']:checked { background:rgba(168,205,255,22); border-color:rgba(190,217,255,42); color:#f5f8ff; }
QPushButton#close:hover { background:#b44c61; color:white; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QKeySequenceEdit { min-height:22px; padding:9px 12px; background:rgba(10,18,31,105); border:1px solid rgba(191,214,244,32); border-radius:9px; selection-background-color:#4778bd; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color:#89b4ff; background:rgba(16,29,49,125); }
QLineEdit:disabled, QComboBox:disabled { color:#75849a; }
QComboBox::drop-down { border:0; width:28px; }
QComboBox QAbstractItemView { background:#202c3e; border:1px solid #435673; selection-background-color:#3a557a; padding:5px; }
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width:20px; border:0; }
QTableWidget { background:transparent; border:0; selection-background-color:rgba(114,170,255,40); alternate-background-color:rgba(255,255,255,4); outline:0; }
QTableWidget::item { padding:7px 10px; border-bottom:1px solid rgba(255,255,255,12); }
QTableWidget::item:selected { background:rgba(114,170,255,40); color:#ffffff; }
QHeaderView::section { background:rgba(255,255,255,7); color:#aabbd2; border:0; border-bottom:1px solid rgba(255,255,255,20); padding:11px 10px; text-align:left; }
QTableCornerButton::section { background:transparent; border:0; }
QListWidget { background:rgba(12,21,35,45); border:0; border-radius:10px; outline:0; }
QListWidget::item { padding:12px; margin:2px; border-radius:8px; }
QListWidget::item:selected { background:rgba(130,180,255,32); color:#edf5ff; }
QPlainTextEdit { background:rgba(8,16,28,70); border:1px solid rgba(255,255,255,16); border-radius:10px; padding:12px; color:#c3d1e5; font-size:13px; }
QScrollArea { border:0; background:transparent; }
QScrollBar:vertical { background:transparent; width:8px; margin:2px; }
QScrollBar::handle:vertical { background:rgba(182,204,235,70); min-height:35px; border-radius:3px; }
QScrollBar:horizontal { background:transparent; height:8px; margin:2px; }
QScrollBar::handle:horizontal { background:rgba(182,204,235,70); min-width:35px; border-radius:3px; }
QScrollBar::add-line, QScrollBar::sub-line { width:0; height:0; }
QScrollBar::add-page, QScrollBar::sub-page { background:transparent; }
QProgressBar { height:5px; max-height:5px; border:0; border-radius:2px; background:rgba(255,255,255,12); }
QProgressBar::chunk { border-radius:2px; background:#86b3ff; }
QMenu, QDialog, QMessageBox { background:#1b293b; border:1px solid #435674; }
QMenu::item { padding:10px 24px; }
QMenu::item:selected { background:#344d70; }
QToolTip { background:#26374d; color:#edf5ff; border:1px solid #536e92; padding:7px; }
QSlider::groove:horizontal { height:4px; background:rgba(255,255,255,35); border-radius:2px; }
QSlider::handle:horizontal { background:#9dc2ff; width:14px; margin:-5px 0; border-radius:7px; }
"""


def icon(glyph, color="#bfd2ef", size=20):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QColor(color))
    font = QFont("Segoe MDL2 Assets")
    font.setPixelSize(size * 2 - 3)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, glyph)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class ParagraphLabel(QLabel):
    """Readable paragraph leading, escaping paths and other dynamic text."""
    def setText(self, text):
        content = escape(str(text)).replace("\n", "<br>")
        super().setText(f'<p style="line-height:145%; margin:0">{content}</p>')


def label(text, kind=None, wrap=False):
    widget = ParagraphLabel() if wrap else QLabel()
    widget.setTextFormat(Qt.RichText if wrap else Qt.PlainText)
    widget.setText(text)
    if kind:
        widget.setObjectName(kind)
    widget.setWordWrap(wrap)
    widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return widget


def quick_action(title, description, callback, glyph):
    control = button("", callback, "quick")
    control.setAccessibleName(title)
    control.setAccessibleDescription(description)
    control.setMinimumHeight(104)
    box = QHBoxLayout(control)
    box.setContentsMargins(18, 18, 18, 18)
    box.setSpacing(13)
    symbol = QLabel()
    symbol.setPixmap(icon(glyph, size=22).pixmap(22, 22))
    box.addWidget(symbol)
    text = QVBoxLayout()
    text.setSpacing(7)
    text.addWidget(label(title, "actionTitle"))
    text.addWidget(label(description, "actionDescription"))
    box.addLayout(text, 1)
    for child in control.findChildren(QLabel):
        child.setTextInteractionFlags(Qt.NoTextInteraction)
        child.setAttribute(Qt.WA_TransparentForMouseEvents)
    return control


def button(text, callback=None, role=None, glyph=None):
    widget = QPushButton(text)
    widget.setCursor(Qt.PointingHandCursor)
    if role:
        widget.setProperty("role", role)
    if glyph:
        widget.setIcon(icon(glyph))
        widget.setIconSize(QSize(17, 17))
    if callback:
        widget.clicked.connect(lambda _checked=False: callback())
    return widget


class Card(QFrame):
    def __init__(self, title="", description="", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(22, 18, 22, 18)
        self.box.setSpacing(12)
        if title:
            self.box.addWidget(label(title, "section"))
        if description:
            self.box.addWidget(label(description, "muted", True))


def row(*widgets):
    layout = QHBoxLayout()
    layout.setSpacing(10)
    for widget in widgets:
        if widget is None:
            layout.addStretch()
        else:
            layout.addWidget(widget)
    return layout


def field(caption, widget, hint=None):
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(7)
    layout.addWidget(label(caption, "muted"))
    layout.addWidget(widget)
    if hint:
        layout.addWidget(label(hint, "muted", True))
    return container


def table(headers, stretch=0):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectRows)
    widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
    widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
    widget.setShowGrid(False)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().hide()
    widget.verticalHeader().setDefaultSectionSize(43)
    widget.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    widget.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
    widget.setMinimumHeight(130)
    return widget


def log_view():
    widget = QPlainTextEdit()
    widget.setReadOnly(True)
    widget.setMaximumBlockCount(3000)
    widget.setMinimumHeight(100)
    return widget


class Switch(QCheckBox):
    """Native checkbox semantics with an animated glass-style switch indicator."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._position = 0.0
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(34)
        self.animation = QPropertyAnimation(self, b"position", self)
        self.animation.setDuration(140)
        self.toggled.connect(self._animate)

    def _get_position(self):
        return self._position

    def _set_position(self, value):
        self._position = value
        self.update()

    position = Property(float, _get_position, _set_position)

    def _animate(self, checked):
        self.animation.stop()
        self.animation.setStartValue(self._position)
        self.animation.setEndValue(1.0 if checked else 0.0)
        self.animation.start()

    def sizeHint(self):
        return QSize(56 + self.fontMetrics().horizontalAdvance(self.text()), 36)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(1.0 if self.isEnabled() else 0.45)
        y = (self.height() - 22) / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#86b3ff") if self.isChecked() else QColor(220, 233, 255, 45))
        painter.drawRoundedRect(QRectF(1, y, 39, 22), 11, 11)
        painter.setBrush(QColor("#f3f8ff"))
        painter.drawEllipse(QRectF(4 + 17 * self._position, y + 3, 16, 16))
        painter.setPen(QColor("#dce8f9"))
        painter.drawText(self.rect().adjusted(52, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        if self.hasFocus():
            painter.setPen(QPen(QColor("#a9ceff"), 1, Qt.DotLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 5, 5)
