"""Explicit Qt ports for the tested toolbox engines, without creating a Tk window."""

import threading
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt, QThread
from PySide6.QtWidgets import (
    QApplication, QComboBox, QLineEdit, QCheckBox, QSpinBox, QDoubleSpinBox,
    QTableWidgetItem, QFileDialog, QInputDialog, QMessageBox,
)


class Dispatcher(QObject):
    requested = Signal(object)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.timers = {}
        self.alive = True
        self.requested.connect(self._dispatch)

    @Slot(object)
    def _dispatch(self, payload):
        if not self.alive:
            return
        token, delay, callback = payload
        timer = QTimer(self)
        timer.setSingleShot(True)
        self.timers[token] = timer
        def run():
            self.timers.pop(token, None)
            timer.deleteLater()
            if self.alive:
                callback()
        timer.timeout.connect(run)
        timer.start(delay)

    def after(self, milliseconds, callback):
        token = uuid.uuid4().hex
        self.requested.emit((token, milliseconds, callback))
        return token

    def after_cancel(self, token):
        timer = self.timers.pop(token, None)
        if timer:
            timer.stop()
            timer.deleteLater()

    def call(self, callback):
        if QThread.currentThread() == self.thread():
            return callback()
        done, result = threading.Event(), {}
        def run():
            try:
                result["value"] = callback()
            except BaseException as exc:
                result["error"] = exc
            finally:
                done.set()
        if not self.alive:
            raise RuntimeError("窗口正在退出")
        self.after(0, run)
        while not done.wait(0.1):
            if not self.alive:
                raise RuntimeError("窗口已退出")
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def stop(self):
        self.alive = False
        for timer in self.timers.values():
            timer.stop()
            timer.deleteLater()
        self.timers.clear()

    def winfo_exists(self):
        return self.alive

    def winfo_id(self):
        return int(self.window.winId())

    def winfo_toplevel(self):
        return self

    def withdraw(self):
        self.window.hide()

    def deiconify(self):
        self.window.showNormal()

    def lift(self):
        self.window.raise_()
        self.window.activateWindow()

    def attributes(self, *_args):
        return None


class Value(QObject):
    changed = Signal(object)

    def __init__(self, value="", parent=None):
        super().__init__(parent)
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        if value != self.value:
            self.value = value
            self.changed.emit(value)

    def bind(self, widget):
        if isinstance(widget, QComboBox):
            widget.setCurrentText(str(self.value))
            widget.currentTextChanged.connect(self.set)
            self.changed.connect(lambda value: widget.setCurrentText(str(value)))
        elif isinstance(widget, QCheckBox):
            widget.setChecked(bool(self.value))
            widget.toggled.connect(self.set)
            self.changed.connect(lambda value: widget.setChecked(bool(value)))
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setValue(float(self.value) if isinstance(widget, QDoubleSpinBox) else int(self.value))
            widget.valueChanged.connect(lambda value: self.set(str(value)))
            self.changed.connect(lambda value: widget.setValue(float(value) if isinstance(widget, QDoubleSpinBox) else int(value)))
        else:
            widget.setText(str(self.value))
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.set)
            self.changed.connect(lambda value: widget.setText(str(value)))
        return self


class WidgetPort:
    def __init__(self, widget, dispatcher, value=None):
        self.widget, self.dispatcher, self.value = widget, dispatcher, value

    def get(self):
        return self.value.get() if self.value else self.widget.text()

    def configure(self, **kwargs):
        def update():
            if "state" in kwargs:
                self.widget.setEnabled(kwargs["state"] != "disabled")
            if "values" in kwargs:
                self.widget.clear()
                self.widget.addItems(list(kwargs["values"]))
            if "text" in kwargs:
                self.widget.setText(kwargs["text"])
        self.dispatcher.call(update)

    config = configure

    def delete(self, *_args):
        self.dispatcher.call(lambda: self.widget.clear())

    def insert(self, _index, text, *_args):
        def update():
            if isinstance(self.widget, (QSpinBox, QDoubleSpinBox)):
                self.widget.setValue(float(text) if isinstance(self.widget, QDoubleSpinBox) else int(text))
            else:
                self.widget.setText(str(text))
        self.dispatcher.call(update)

    def __setitem__(self, key, value):
        if key == "value":
            self.dispatcher.call(lambda: self.widget.setValue(int(value)))
        elif key == "maximum":
            self.dispatcher.call(lambda: self.widget.setMaximum(int(value)))
        else:
            raise KeyError(key)


class LogPort:
    def __init__(self, widget, dispatcher):
        self.widget, self.dispatcher = widget, dispatcher

    def insert(self, _index, text, *_args):
        self.dispatcher.call(lambda: self.widget.appendPlainText(str(text).rstrip("\n")))

    def delete(self, first, last):
        if first == "1.0" and last == "500.0":
            def trim():
                lines = self.widget.toPlainText().splitlines()
                self.widget.setPlainText("\n".join(lines[499:]))
            self.dispatcher.call(trim)
        else:
            self.dispatcher.call(self.widget.clear)

    def see(self, *_args):
        self.dispatcher.call(lambda: self.widget.verticalScrollBar().setValue(self.widget.verticalScrollBar().maximum()))

    def index(self, *_args):
        return self.dispatcher.call(lambda: f"{self.widget.document().blockCount()}.0")


class TreePort:
    def __init__(self, table, dispatcher=None):
        self.table, self.dispatcher = table, dispatcher

    def _id(self, row):
        return self.table.item(row, 0).data(Qt.UserRole)

    def _row(self, token):
        for row in range(self.table.rowCount()):
            if self._id(row) == token:
                return row
        raise KeyError(token)

    def get_children(self, *_args):
        return tuple(self._id(row) for row in range(self.table.rowCount()))

    def insert(self, _parent, _index, values=()):
        row, token = self.table.rowCount(), uuid.uuid4().hex
        self.table.insertRow(row)
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            if column == 0:
                item.setData(Qt.UserRole, token)
            self.table.setItem(row, column, item)
        return token

    def delete(self, *tokens):
        if set(tokens) == set(self.get_children()):
            self.table.setRowCount(0)
            return
        for row in sorted((self._row(token) for token in tokens), reverse=True):
            self.table.removeRow(row)

    def selection(self):
        return tuple(self._id(index.row()) for index in self.table.selectionModel().selectedRows())

    def selection_set(self, token):
        self.table.selectRow(self._row(token))

    def focus(self, token):
        self.table.setCurrentCell(self._row(token), 0)

    def index(self, token):
        return self._row(token)

    def item(self, token, option=None):
        row = self._row(token)
        values = tuple(self.table.item(row, column).text() for column in range(self.table.columnCount()))
        return values if option == "values" else {"values": values}


def _on_ui_thread(method):
    def wrapped(self, *args, **kwargs):
        if self.dispatcher:
            return self.dispatcher.call(lambda: method(self, *args, **kwargs))
        return method(self, *args, **kwargs)
    return wrapped


for _method_name in ("get_children", "insert", "delete", "selection", "selection_set", "focus", "index", "item"):
    setattr(TreePort, _method_name, _on_ui_thread(getattr(TreePort, _method_name)))


class ListPort:
    def __init__(self, widget):
        self.widget = widget

    def delete(self, *_args):
        self.widget.blockSignals(True)
        self.widget.clear()
        self.widget.blockSignals(False)

    def insert(self, _index, text):
        self.widget.addItem(text)

    def curselection(self):
        row = self.widget.currentRow()
        return (row,) if row >= 0 else ()

    def selection_set(self, row):
        self.widget.blockSignals(True)
        self.widget.setCurrentRow(row)
        self.widget.blockSignals(False)


class Dialogs:
    def __init__(self, dispatcher, notify):
        self.dispatcher, self.notify = dispatcher, notify

    def showinfo(self, title, message, **_kwargs):
        return self.dispatcher.call(lambda: self.notify(f"{title} · {message}"))

    def showwarning(self, title, message, **_kwargs):
        return self.dispatcher.call(lambda: QMessageBox.warning(self.dispatcher.window, title, str(message)))

    def showerror(self, title, message, **_kwargs):
        return self.dispatcher.call(lambda: QMessageBox.critical(self.dispatcher.window, title, str(message)))

    def askyesno(self, title, message, **_kwargs):
        def ask():
            box = QMessageBox(self.dispatcher.window)
            box.setWindowTitle(title)
            box.setText(str(message))
            box.setIcon(QMessageBox.Warning)
            confirm = box.addButton("确认继续", QMessageBox.AcceptRole)
            cancel = box.addButton("取消", QMessageBox.RejectRole)
            box.setDefaultButton(cancel)
            box.exec()
            return box.clickedButton() is confirm
        return self.dispatcher.call(ask)

    askokcancel = askyesno

    def askstring(self, title, message, initialvalue="", **_kwargs):
        def ask():
            value, accepted = QInputDialog.getText(self.dispatcher.window, title, message, text=initialvalue)
            return value if accepted else None
        return self.dispatcher.call(ask)

    def askdirectory(self, title="选择文件夹", initialdir="", **_kwargs):
        return self.dispatcher.call(lambda: QFileDialog.getExistingDirectory(self.dispatcher.window, title, initialdir))

    def askopenfilenames(self, title="选择文件", initialdir="", filetypes=None, **_kwargs):
        return self.dispatcher.call(lambda: QFileDialog.getOpenFileNames(self.dispatcher.window, title, initialdir, self._filter(filetypes))[0])

    def askopenfilename(self, title="选择文件", initialdir="", filetypes=None, **_kwargs):
        return self.dispatcher.call(lambda: QFileDialog.getOpenFileName(self.dispatcher.window, title, initialdir, self._filter(filetypes))[0])

    @staticmethod
    def _filter(filetypes):
        return ";;".join(f"{name} ({pattern})" for name, pattern in (filetypes or [("所有文件", "*")]))
