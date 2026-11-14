"""Headless UI-to-engine checks; no input injection or real system changes.

Run with QT_QPA_PLATFORM=offscreen. File operations use pytest temp directories.
"""

import ctypes
import json
import threading
from ctypes import wintypes
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image
from PySide6.QtGui import QFontInfo, QKeySequence, QFontDatabase
from PySide6.QtWidgets import QApplication, QPushButton, QSpinBox, QCheckBox, QKeySequenceEdit

from glass_toolbox.backend import core
from glass_toolbox.hotkeys import SummonHotkey
from glass_toolbox.widgets import ui_font
from test_process_close_windows_integration import sleeper_exe
from test_glass_hotkeys import FakeAPI
from test_glass_toolbox import application, window, wait_until


def click(page, text):
    buttons = [b for b in page.findChildren(QPushButton) if b.text() == text]
    assert len(buttons) == 1
    assert buttons[0].isEnabled()
    buttons[0].click()  # Qt signal only; does not move or click the OS pointer.


def test_font_resolves_to_bundled_regular_and_medium(application):
    font = ui_font()
    assert QFontInfo(font).family() == "HarmonyOS Sans SC"
    assert {"Regular", "Medium"} <= set(QFontDatabase.styles(font.family()))


@pytest.mark.parametrize("mode,expected", [
    ("日期+序号", "2026-11-13_007.txt"), ("前缀+序号", "2026-11-13_007.txt"),
    ("查找替换", "Demo.txt"), ("添加前缀", "2026-11-13Example.txt"),
    ("添加后缀", "ExampleDemo.txt"), ("转小写", "example.txt"), ("转大写", "EXAMPLE.txt"),
])
def test_all_rename_modes_through_controls(window, application, tmp_path, mode, expected):
    page = window.pages["rename"]
    folder = tmp_path / "source"
    folder.mkdir()
    source = folder / "Example.txt"
    source.write_bytes(b"original content")
    page.engine.folder = folder
    page.mode.setCurrentText(mode)
    page.prefix.setText("2026-11-13")
    page.find.setText("Example")
    page.replace.setText("Demo")
    page.start.setValue(7)
    page.invalidate()
    page.preview_button.click()
    wait_until(application, lambda: not page.busy)
    assert page.table.item(0, 1).text() == expected
    assert source.read_bytes() == b"original content"
    page.apply_button.click()
    wait_until(application, lambda: not page.busy)
    assert (folder / expected).read_bytes() == b"original content"
    page.undo_button.click()
    wait_until(application, lambda: not page.busy)
    assert [p.name for p in folder.iterdir()] == ["Example.txt"]
    assert not window.test_errors


def test_media_scan_execute_and_input_invalidation(window, application, tmp_path):
    page = window.pages["media"]
    source, output = tmp_path / "photos", tmp_path / "organized"
    source.mkdir()
    photo = source / "sample.png"
    Image.new("RGB", (16, 16), "red").save(photo)
    original = photo.read_bytes()
    page.engine.source_var.set(str(source))
    page.engine.output_var.set(str(output))
    page.sync_state()
    page.scan_button.click()
    wait_until(application, lambda: not page.engine.busy)
    page.sync_state()
    assert len(page.engine.manifest.items) == 1
    page.execute_button.click()
    wait_until(application, lambda: not page.engine.busy)
    assert len(list(output.rglob("*.png"))) == 1
    assert next(output.rglob("*.png")).read_bytes() == original == photo.read_bytes()
    assert (output / "scan_manifest.json").exists()
    page.engine.scan()
    wait_until(application, lambda: not page.engine.busy)
    page.engine.output_var.set(str(tmp_path / "new-output"))
    page.sync_state()
    assert page.engine.manifest is None and not page.execute_button.isEnabled()
    assert not window.test_errors


def test_launcher_group_add_select_launch_remove_roundtrip(window, application, tmp_path, monkeypatch):
    page, dialogs = window.pages["launcher"], window.backend.dialogs
    monkeypatch.setattr(dialogs, "askstring", lambda *a, **kw: "测试分组")
    click(page, "新建分组")
    paths = [tmp_path / "first.txt", tmp_path / "second.txt"]
    for path in paths:
        path.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(dialogs, "askopenfilenames", lambda *a, **kw: list(map(str, paths)))
    click(page, "添加应用")
    assert page.table.rowCount() == 2
    launch = Mock()
    monkeypatch.setattr(page.engine, "launch_apps", launch)
    page.table.selectRow(1)
    page.sync_state()
    click(page, "启动所选")
    assert [v["path"] for v in launch.call_args.args[0]] == [str(paths[1])]
    click(page, "移出分组")
    assert page.table.rowCount() == 1
    saved = json.loads(page.engine.config_file.read_text(encoding="utf-8"))
    assert saved["groups"]["测试分组"]["apps"][0]["path"] == str(paths[0])
    assert all(p.read_text(encoding="utf-8") == "keep" for p in paths)
    assert not window.test_errors


@pytest.mark.parametrize("kind", ["selection", "group"])
def test_launcher_close_dispatches_without_confirmation(window, monkeypatch, kind):
    page = window.pages["launcher"]
    page.engine.groups[page.engine.current_group]["apps"] = [{"name": "fixture", "type": "程序", "path": "C:/fake.exe"}]
    page.engine.refresh_apps()
    page.table.selectRow(0)
    page.sync_state()
    close = Mock()
    monkeypatch.setattr(page.engine, "close_paths", close)
    group_close = Mock()
    monkeypatch.setattr(page.engine, "close_current_group", group_close)
    confirm = Mock(return_value=False)
    monkeypatch.setattr(window.backend.dialogs, "askyesno", confirm)
    (page.close_selected if kind == "selection" else page.close_group).click()
    confirm.assert_not_called()
    if kind == "selection":
        close.assert_called_once_with(["C:/fake.exe"])
    else:
        group_close.assert_called_once_with()


def test_mic_settings_write_only_isolated_config(window, tmp_path, monkeypatch):
    page = window.pages["mic"]
    config = tmp_path / "mic.json"
    config.write_text('{"custom":"keep","target_endpoint_id":"fixture"}', encoding="utf-8")
    monkeypatch.setattr(core, "MIC_TOGGLE_CONFIG_PATH", config)
    monkeypatch.setattr(core.SystemToolsTab, "_running_mic_processes", lambda self: [])
    page.hotkey.setText("Ctrl+Alt+M")
    click(page, "应用快捷键")
    size = page.findChild(QSpinBox)
    size.setValue(85)
    click(page, "应用大小")
    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved["hotkey"] == "Ctrl+Alt+M" and saved["overlay_scale"] == .85
    assert saved["custom"] == "keep" and saved["target_endpoint_id"] == "fixture"
    page.hotkey.setText("InvalidKey")
    before = config.read_bytes()
    click(page, "应用快捷键")
    assert config.read_bytes() == before
    assert len(window.test_errors) == 1 and window.test_errors[0][0] == "设置失败"


def test_startup_permission_failure_restores_switch(window, monkeypatch):
    switch = window.pages["startup"].findChild(QCheckBox)
    monkeypatch.setattr(core, "set_autostart", Mock(side_effect=OSError("denied")))
    switch.click()
    assert not switch.isChecked()
    assert window.test_errors == [("启动项失败", "denied")]


def test_audio_buttons_dispatch_correct_device_and_surface_failure(window, application, monkeypatch):
    chosen = []
    monkeypatch.setattr(core, "set_default_render_id", lambda value: chosen.append(value))
    buttons = window.pages["output"].findChildren(QPushButton)
    for button, device in zip(buttons, core.AUDIO_DEVICES, strict=True):
        button.click()
        wait_until(application, lambda: device["id"] in chosen and device["name"] in window.status.text())
    assert chosen == [d["id"] for d in core.AUDIO_DEVICES]
    monkeypatch.setattr(core, "set_default_render_id", Mock(side_effect=OSError("device absent")))
    buttons[0].click()
    wait_until(application, lambda: bool(window.test_errors))
    assert window.test_errors[-1] == ("切换失败", "device absent")


def test_live_buttons_open_correct_urls_without_automation(window, monkeypatch):
    opened = Mock()
    monkeypatch.setattr(window.backend.live, "open_shortcut", opened)
    for button in window.pages["live"].findChildren(QPushButton):
        button.click()
    assert [call.args for call in opened.call_args_list] == [(title, url, None) for title, url, mode in core.LiveShortcutTab.SHORTCUTS]


def test_worker_failure_is_reported_and_busy_clears(window, application):
    finished = []
    def fail():
        raise OSError("controlled disk failure")
    window.backend.run_job(fail, lambda: finished.append(True))
    wait_until(application, lambda: not window.backend.active_jobs())
    assert finished == [True]
    assert window.test_errors == [("操作未完成", "controlled disk failure")]


@pytest.mark.parametrize("key", ["Ins", "F8", "Ctrl+Alt+Space"])
def test_hotkey_event_toggles_without_system_input(window, application, monkeypatch, key):
    assert application.platformName() == "offscreen", "Run this test headlessly"
    window.summon_hotkey = SummonHotkey(1, FakeAPI())
    window.apply_summon_hotkey(key)
    pin = Mock()
    monkeypatch.setattr(window, "set_self_topmost", pin)
    monkeypatch.setattr(QApplication, "focusWidget", lambda: None)
    window.hide()
    message = wintypes.MSG()
    message.message, message.wParam = 0x0312, window.summon_hotkey.identifier
    for expected in [True, False] * 10:
        assert window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(message)) == (True, 0)
        wait_until(application, lambda: window.isVisible() == expected)
    assert pin.call_count == 10 and all(c.args == (True,) for c in pin.call_args_list)
    assert json.loads(window.settings_path.read_text())["summon_hotkey"] == key


def test_recording_active_hotkey_does_not_hide_window(window, monkeypatch):
    window.summon_hotkey = SummonHotkey(1, FakeAPI())
    window.apply_summon_hotkey("Ins")
    editor = QKeySequenceEdit(window)
    monkeypatch.setattr(QApplication, "focusWidget", lambda: editor)
    message = wintypes.MSG()
    message.message, message.wParam = 0x0312, window.summon_hotkey.identifier
    assert window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(message)) == (True, 0)
    assert editor.keySequence().toString(QKeySequence.PortableText) == "Ins"
    assert window.isVisible()


@pytest.mark.parametrize("kind", ["selection", "group", "drop-exe", "drop-shortcut", "drop-folder", "drop-multiple", "drop-native-fallback"])
def test_close_button_ends_real_process_without_touching_same_name_elsewhere(
        window, application, tmp_path, sleeper_exe, kind, monkeypatch):
    import shutil
    import subprocess
    page = window.pages["launcher"]
    first, other = tmp_path / "target", tmp_path / "target-other"
    first.mkdir(); other.mkdir()
    paths = [folder / sleeper_exe.name for folder in (first, other)]
    for path in paths:
        shutil.copy2(sleeper_exe, path)
    processes = [subprocess.Popen([str(p)], creationflags=subprocess.CREATE_NO_WINDOW) for p in paths]
    try:
        page.engine.groups[page.engine.current_group]["apps"] = [
            {"name": paths[0].name, "type": "程序", "path": str(paths[0])}]
        page.engine.refresh_apps()
        page.table.selectRow(0)
        page.sync_state()
        if kind.startswith("drop-"):
            dropped = paths[0]
            if kind == "drop-shortcut":
                import pythoncom
                from win32com.client import Dispatch
                dropped = tmp_path / "程序快捷方式.lnk"
                pythoncom.CoInitialize()
                try:
                    shortcut = Dispatch("WScript.Shell").CreateShortcut(str(dropped))
                    shortcut.TargetPath = str(paths[0])
                    shortcut.Save()
                    shortcut = None
                finally:
                    pythoncom.CoUninitialize()
            elif kind == "drop-folder":
                dropped = first
                helper = first / "bin" / "helper.exe"
                helper.parent.mkdir()
                shutil.copy2(sleeper_exe, helper)
                processes.append(subprocess.Popen([str(helper)], creationflags=subprocess.CREATE_NO_WINDOW))
            elif kind == "drop-native-fallback":
                monkeypatch.setattr(core.subprocess, "run", Mock(side_effect=FileNotFoundError("taskkill unavailable")))
            dropped_paths = [dropped]
            if kind == "drop-multiple":
                third = first / "third.exe"
                shutil.copy2(sleeper_exe, third)
                processes.append(subprocess.Popen([str(third)], creationflags=subprocess.CREATE_NO_WINDOW))
                dropped_paths.append(third)
            send_close_drop(page.drop_close, dropped_paths)
        else:
            (page.close_selected if kind == "selection" else page.close_group).click()
        wait_until(application, lambda: not getattr(page.engine, "_launcher_close_pending", False), timeout=20)
        assert processes[0].poll() is not None, page.log.toPlainText()
        assert processes[1].poll() is None
        if kind in ("drop-multiple", "drop-folder"):
            assert processes[2].poll() is not None, page.log.toPlainText()
        assert "失败" not in page.log.toPlainText(), page.log.toPlainText()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def test_drop_close_dispatches_paths_directly_and_never_adds_to_group(window, tmp_path, monkeypatch):
    from PySide6.QtCore import QMimeData, QUrl, QPointF
    from PySide6.QtGui import QDropEvent
    from PySide6.QtCore import Qt
    page = window.pages["launcher"]
    before = list(page.engine.groups[page.engine.current_group]["apps"])
    close = Mock()
    monkeypatch.setattr(page.engine, "close_paths", close)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])
    event = QDropEvent(QPointF(1, 1), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    page.drop_close.dropEvent(event)
    close.assert_called_once()
    assert [Path(value) for value in close.call_args.args[0]] == [tmp_path]
    assert event.isAccepted()
    assert page.engine.groups[page.engine.current_group]["apps"] == before


def test_close_remains_available_to_cancel_remaining_launches(window):
    page = window.pages["launcher"]
    page.engine.groups[page.engine.current_group]["apps"] = [{"name": "fixture", "type": "程序", "path": "C:/fake.exe"}]
    page.engine.refresh_apps()
    page.table.selectRow(0)
    page.engine._launcher_busy = True
    try:
        page.sync_state()
        assert page.close_selected.isEnabled() and page.close_group.isEnabled()
        assert not page.launch_button.isEnabled()
    finally:
        page.engine._launcher_busy = False


def send_close_drop(target, paths):
    from PySide6.QtCore import QMimeData, QUrl, QPoint, QPointF, Qt
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    enter = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, enter)
    assert enter.isAccepted(), "drag enter was rejected"
    drop = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, drop)
    assert drop.isAccepted(), "drop was rejected"


@pytest.mark.parametrize("case", ["remote", "pending"])
def test_drop_close_rejects_remote_paths_and_duplicate_pending_drop(window, monkeypatch, case):
    from PySide6.QtCore import QMimeData, QUrl, QPoint, Qt
    from PySide6.QtGui import QDragEnterEvent
    page = window.pages["launcher"]
    before = list(page.engine.groups[page.engine.current_group]["apps"])
    close = Mock()
    monkeypatch.setattr(page.engine, "close_paths", close)
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.invalid/file.exe") if case == "remote" else QUrl.fromLocalFile("C:/fixture.exe")])
    page.engine._launcher_close_pending = case == "pending"
    try:
        enter = QDragEnterEvent(QPoint(10,10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(page.drop_close, enter)
        assert not enter.isAccepted()
        close.assert_not_called()
        assert page.engine.groups[page.engine.current_group]["apps"] == before
    finally:
        page.engine._launcher_close_pending = False
