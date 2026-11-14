"""Regression tests use only isolated processes and files."""
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import utility_toolbox as t
from test_process_close_windows_integration import sleeper_exe
from test_glass_toolbox import application, window, wait_until
from test_glass_workflows import send_close_drop


def controller():
    app = object.__new__(t.LauncherTab)
    app.parent = SimpleNamespace(after=lambda delay, callback: callback())
    app.messages = []
    app.log = app.messages.append
    return app


def test_selected_launcher_uses_same_targets_as_group():
    app = controller()
    entry = {"path": r"C:\Demo\app-launcher.exe", "close_paths": [r"C:\Demo\app.exe"]}
    app.current_group = "test"
    app.groups = {"test": {"apps": [entry]}}
    app._queue_launcher_close = lambda work: work()
    app._close_worker = Mock()
    app.close_paths([entry["path"]])
    app._close_worker.assert_called_once_with(t.launcher_close_paths([entry]))


def test_failure_is_not_overwritten_by_empty_final_snapshot(monkeypatch):
    app = controller()
    path = r"C:\Fixture\fixture.exe"
    monkeypatch.setattr(t, "running_process_paths", lambda: [(90901, path)])
    monkeypatch.setattr(t, "terminate_processes_fast", lambda *a, **k: (set(), {90901: "access denied"}))
    monkeypatch.setattr(t, "observe_closed_process_targets", lambda *a, **k: ([], "", False))
    app._close_worker([path])
    assert "access denied" in app.messages[-1]
    assert "失败 1" in app.messages[-1]


@pytest.mark.parametrize("mode", ["folder", "taskkill_unavailable"])
def test_real_close_handles_folder_and_command_failure(mode, sleeper_exe, tmp_path, monkeypatch):
    folder = tmp_path / "应用"
    folder.mkdir()
    target = folder / "fixture.exe"
    shutil.copy2(sleeper_exe, target)
    process = subprocess.Popen([str(target)], creationflags=subprocess.CREATE_NO_WINDOW)
    app = controller()
    monkeypatch.setattr(t, "log_line", lambda message: None)
    if mode == "taskkill_unavailable":
        monkeypatch.setattr(t.subprocess, "run", Mock(side_effect=FileNotFoundError("taskkill unavailable")))
    try:
        app._close_worker([str(folder if mode == "folder" else target)])
        assert process.poll() is not None, app.messages
        assert not any("失败" in m for m in app.messages), app.messages
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.parametrize("delivery", ["worker", "drop"])
def test_locked_file_closes_only_its_holder(tmp_path, monkeypatch, window, application, delivery):
    import time
    compiler = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
    source = tmp_path / "Holder.cs"
    source.write_text('class P { static void Main(string[] a) { using(var f = System.IO.File.Open(a[0], System.IO.FileMode.Open, System.IO.FileAccess.Read, System.IO.FileShare.None)) { System.IO.File.WriteAllText(a[1], "ready"); System.Threading.Thread.Sleep(60000); } } }')
    exe = tmp_path / "Holder.exe"
    subprocess.run([str(compiler), "/nologo", "/target:winexe", f"/out:{exe}", str(source)], check=True, capture_output=True)
    files = [tmp_path / name for name in ("目标 文档.txt", "other.txt")]
    processes = []
    monkeypatch.setattr(t, "log_line", lambda message: None)
    try:
        for index, file in enumerate(files):
            file.write_text("preserve content")
            ready = tmp_path / f"ready{index}"
            processes.append(subprocess.Popen([str(exe), str(file), str(ready)], creationflags=subprocess.CREATE_NO_WINDOW))
            deadline = time.monotonic() + 5
            while not ready.exists():
                assert time.monotonic() < deadline
                time.sleep(.02)
        app = controller()
        if delivery == "drop":
            page = window.pages["launcher"]
            send_close_drop(page.drop_close, [files[0]])
            wait_until(application, lambda: not getattr(page.engine, "_launcher_close_pending", False), timeout=20)
            app.messages = [page.log.toPlainText()]
        else:
            app._close_worker([str(files[0])])
        assert processes[0].poll() is not None, app.messages
        assert not any("失败" in m for m in app.messages), app.messages
        assert processes[1].poll() is None
        assert files[0].read_text() == "preserve content"
    finally:
        for process in processes:
            if process.poll() is None: process.kill()
            process.wait(timeout=5)
