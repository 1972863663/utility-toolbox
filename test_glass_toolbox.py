"""Qt integration coverage. All files/settings are isolated from real user data."""

import json
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QPushButton, QLabel

from glass_toolbox.window import GlassWindow
from glass_toolbox.backend import core, subtitles
from glass_toolbox.widgets import ui_font


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    app.setFont(ui_font())
    yield app


@pytest.fixture
def window(application, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "APP_DIR", tmp_path)
    monkeypatch.setattr(core, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(core, "ensure_app_dir", lambda: None)
    monkeypatch.setattr(core, "log_line", Mock())
    monkeypatch.setattr(subtitles.AppConfig, "HISTORY_FILE", tmp_path / "subtitle_history.json")
    monkeypatch.setattr(subtitles.AppConfig, "BASE_DIR", tmp_path)
    monkeypatch.setattr(core, "is_autostart_enabled", lambda: False)
    monkeypatch.setattr(core.TopmostController, "_poll_foreground", lambda _self: None)
    monkeypatch.setattr(core.TopmostController, "list_windows", lambda _self: [(1234, "测试窗口")])
    monkeypatch.setattr(core.TopmostController, "is_topmost", lambda _self, _hwnd: False)
    monkeypatch.setattr(core.SystemToolsTab, "refresh_mic_devices", lambda *_args: None)
    widget = GlassWindow(tray=False)
    errors = []
    widget.backend.dialogs.showerror = lambda *args, **kwargs: errors.append(args)
    widget.backend.dialogs.showwarning = lambda *args, **kwargs: errors.append(args)
    widget.backend.dialogs.askyesno = lambda *args, **kwargs: True
    widget.backend.dialogs.askokcancel = lambda *args, **kwargs: True
    widget.test_errors = errors
    widget.show()
    application.processEvents()
    yield widget
    wait_until(application, lambda: not widget.backend.active_jobs())
    if widget.summon_hotkey:
        widget.summon_hotkey.close()
    widget.backend.stop()
    widget._quitting = True
    widget.close()
    widget.deleteLater()
    application.processEvents()


def wait_until(app, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            for _ in range(8):
                app.processEvents()
                time.sleep(0.005)
            return
        time.sleep(0.005)
    raise AssertionError("Qt operation did not complete before timeout")


def test_navigation_keeps_inputs_and_search_recovers(window, application):
    assert len(window.routes) == 11
    page = window.pages["rename"]
    page.prefix.setText("我的前缀")
    for key in window.pages:
        window.navigate(key)
        application.processEvents()
        assert window.stack.currentWidget() is window.pages[key]._scroll
    window.search_route("不存在的工具")
    assert "没有找到" in window.status.text()
    window.search_route("重命名")
    assert window.stack.currentWidget() is page._scroll
    assert page.prefix.text() == "我的前缀"


def test_home_actions_keep_visible_text_and_keyboard_target(window, application):
    window.navigate("home")
    for width, height in ((1260, 850), (960, 650)):
        window.resize(width, height)
        application.processEvents()
        controls = [b for b in window.pages["home"].findChildren(QPushButton) if b.property("role") == "quick"]
        assert len(controls) == 6
        for control in controls:
            assert control.height() >= 102
            assert control.accessibleName()
            for text in control.findChildren(QLabel):
                assert text.width() > 0 and text.height() > 0
                if text.text():
                    assert text.fontMetrics().horizontalAdvance(text.text()) <= text.width()
    controls[0].click()
    assert window.stack.currentWidget() is window.pages["rename"]._scroll


def test_rename_real_files_preview_invalidation_apply_and_undo(window, application, tmp_path):
    page = window.pages["rename"]
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "before.txt").write_text("保留内容", encoding="utf-8")
    page.engine.folder = folder
    page.engine.mode_var.set("添加前缀")
    page.prefix.setText("new-")
    page.preview()
    wait_until(application, lambda: not page.busy)
    assert page.apply_button.isEnabled()
    assert (folder / "before.txt").exists()
    page.prefix.setText("changed-")
    assert not page.apply_button.isEnabled() and page.engine.plan == []
    page.preview()
    wait_until(application, lambda: not page.busy)
    page.apply()
    wait_until(application, lambda: not page.busy)
    assert (folder / "changed-before.txt").read_text(encoding="utf-8") == "保留内容"
    page.undo()
    wait_until(application, lambda: not page.busy)
    assert (folder / "before.txt").read_text(encoding="utf-8") == "保留内容"
    assert not window.test_errors


def test_subtitle_preview_and_apply_require_matching_inputs(window, application, tmp_path):
    page = window.pages["subtitle"]
    folder = tmp_path / "episodes"
    folder.mkdir()
    (folder / "Example.S01E01.mkv").write_bytes(b"video")
    page.engine.target_dir = folder
    page.name.setText("测试剧")
    page.preview()
    wait_until(application, lambda: not page.engine.is_running)
    page.sync_state()
    assert page.preview_signature == page.signature()
    assert (folder / "Example.S01E01.mkv").exists()
    page.name.setText("新剧名")
    assert page.preview_signature is None
    assert not page.apply_button.isEnabled()
    page.preview()
    wait_until(application, lambda: not page.engine.is_running)
    page.sync_state()
    page.apply()
    wait_until(application, lambda: not page.engine.is_running)
    assert not (folder / "Example.S01E01.mkv").exists()
    page.engine.undo()
    wait_until(application, lambda: not page.engine.is_running)
    assert (folder / "Example.S01E01.mkv").read_bytes() == b"video"
    assert not window.test_errors


def test_stopped_subtitle_preview_cannot_enable_apply(window):
    page = window.pages["subtitle"]
    page.preview_pending = True
    page._requested_signature = page.signature()
    page.log.setPlainText("任务已停止。\n完成统计：可改名 1")
    page.sync_state()
    assert page.preview_signature is None
    assert not page.apply_button.isEnabled()


def test_subtitle_waits_for_queued_summary_after_worker_finishes(window):
    page = window.pages["subtitle"]
    page.preview_pending = True
    page._requested_signature = page.signature()
    page.engine.is_running = False
    page.log.clear()
    page.sync_state()
    assert page.preview_pending and page.preview_signature is None
    page.log.appendPlainText("完成统计：可改名 1")
    page.sync_state()
    assert not page.preview_pending
    assert page.preview_signature == page.signature()


def test_worker_dispatch_returns_to_ui_and_tracks_busy(window, application):
    calls = []
    dispatcher = window.backend.dispatcher
    def worker():
        calls.append(dispatcher.call(lambda: QThread.currentThread() == application.thread()))
    window.backend.run_job(worker, lambda: calls.append("finished"))
    assert window.backend.active_jobs()
    wait_until(application, lambda: not window.backend.active_jobs())
    assert calls == [True, "finished"]


def test_config_concurrent_change_is_preserved(window, tmp_path):
    external = '{"other_instance": true}'
    core.CONFIG_FILE.write_text(external, encoding="utf-8")
    with pytest.raises(ValueError, match="其他工具箱实例"):
        window.backend.save_config()
    assert core.CONFIG_FILE.read_text(encoding="utf-8") == external


def test_window_selection_enables_actions_without_timer_delay(window):
    page = window.pages["windows"]
    assert not page.pin.isEnabled()
    page.table.selectRow(0)
    assert page.pin.isEnabled() and page.unpin.isEnabled()


def test_default_file_page_keeps_primary_action_in_view(window, application):
    window.resize(1260, 850)
    window.navigate("rename")
    application.processEvents()
    page = window.pages["rename"]
    bottom = page.apply_button.mapTo(page._scroll.viewport(), page.apply_button.rect().bottomRight())
    assert bottom.y() < page._scroll.viewport().height()


def test_quit_does_not_discard_active_file_work(window):
    window.backend._ui_jobs = 1
    assert window.quit_app() is False
    assert window.backend.dispatcher.alive
    assert not window._quitting
    window.backend._ui_jobs = 0
    assert window.test_errors[0][0] == "任务仍在进行"


def test_hotkey_settings_merge_preserves_external_tint(window):
    window.settings_path.write_text('{"tint":77,"unrelated":true}', encoding="utf-8")
    window.save_ui_settings({"summon_hotkey": "Ctrl+Alt+K"})
    saved = json.loads(window.settings_path.read_text(encoding="utf-8"))
    assert saved == {"tint": 77, "unrelated": True, "summon_hotkey": "Ctrl+Alt+K"}


def test_invalid_existing_ui_settings_are_not_overwritten(window):
    window.settings_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        window.save_ui_settings({"summon_hotkey": "Ctrl+Alt+K"})
    assert window.settings_path.read_text(encoding="utf-8") == "{broken"


def test_native_summon_restores_topmost_without_recreating_handle(window, application):
    hwnd = int(window.winId())
    window.hide()
    window.show_window()
    application.processEvents()
    assert window.isVisible()
    assert core.win32gui.GetWindowLong(hwnd, -20) & 8
    assert int(window.winId()) == hwnd


def test_toggle_alternates_hidden_and_topmost_without_losing_page(window, application):
    hwnd = int(window.winId())
    window.navigate("rename")
    window.pages["rename"].prefix.setText("保留输入")
    window.hide()
    for _ in range(3):
        window.toggle_visibility()
        application.processEvents()
        assert window.isVisible() and not window.isMinimized()
        assert core.win32gui.GetWindowLong(hwnd, -20) & 8
        window.toggle_visibility()
        application.processEvents()
        assert not window.isVisible()
    assert window.pages["rename"].prefix.text() == "保留输入"
    assert window.stack.currentWidget() is window.pages["rename"]._scroll
    assert int(window.winId()) == hwnd


def test_toggle_restores_minimized_window_instead_of_hiding_it(window, application):
    hwnd = int(window.winId())
    window.showMinimized()
    application.processEvents()
    window.toggle_visibility()
    application.processEvents()
    assert window.isVisible() and not window.isMinimized()
    assert core.win32gui.GetWindowLong(int(window.winId()), -20) & 8
    window.showMinimized()
    application.processEvents()
    window.show_window()
    application.processEvents()
    assert not window.isMinimized()
    window.set_self_topmost(False)
    assert not core.win32gui.GetWindowLong(hwnd, -20) & 8
    assert int(window.winId()) == hwnd
