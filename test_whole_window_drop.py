import json
import shutil
import subprocess
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QLineEdit
from test_glass_toolbox import application, window, wait_until
from test_glass_workflows import send_close_drop
from test_process_close_windows_integration import sleeper_exe


def surface(window, name):
    if name == "window": return window
    if name == "titlebar": return window.titlebar
    if name == "sidebar": return window.nav_buttons["files"]
    if name == "search": return window.search
    if name == "table": return window.pages["launcher"].table.viewport()
    if name == "disabled_button": return window.pages["launcher"].launch_button
    if name == "dynamic":
        control = QLineEdit(window)
        control.ensurePolished()
        return control
    return window.pages[name]._scroll.viewport()


@pytest.mark.parametrize("name", ["window", "titlebar", "sidebar", "search", "table", "disabled_button", "dynamic",
                                  "home", "rename", "subtitle", "media", "launcher", "windows", "mic", "output", "macro", "startup", "backup", "live"])
def test_file_drop_anywhere_closes_once_without_editing_inputs(window, tmp_path, monkeypatch, name):
    close = Mock()
    monkeypatch.setattr(window.backend.launcher, "close_paths", close)
    before = json.dumps(window.backend.launcher.groups)
    window.search.setText("preserve")
    target = surface(window, name)
    send_close_drop(target, [tmp_path / "程序.exe"])
    close.assert_called_once()
    assert close.call_args.args[0] == [(tmp_path / "程序.exe").as_posix()]
    assert window.search.text() == "preserve"
    assert json.dumps(window.backend.launcher.groups) == before


@pytest.mark.parametrize("name", ["window", "sidebar", "search", "table", "rename"])
def test_whole_window_drop_really_ends_only_temporary_target(window, application, tmp_path, sleeper_exe, name):
    first, other = tmp_path / "target", tmp_path / "target-other"
    first.mkdir(); other.mkdir()
    paths = [folder / sleeper_exe.name for folder in (first, other)]
    for path in paths: shutil.copy2(sleeper_exe, path)
    processes = [subprocess.Popen([str(path)], creationflags=subprocess.CREATE_NO_WINDOW) for path in paths]
    try:
        send_close_drop(surface(window, name), [paths[0]])
        wait_until(application, lambda: not getattr(window.backend.launcher, "_launcher_close_pending", False), timeout=20)
        assert processes[0].poll() is not None, window.pages["launcher"].log.toPlainText()
        assert processes[1].poll() is None
        assert all(path.is_file() for path in paths)
    finally:
        for process in processes:
            if process.poll() is None: process.kill()
            process.wait(timeout=5)
