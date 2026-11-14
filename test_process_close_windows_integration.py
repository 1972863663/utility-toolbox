"""Real Windows processes; only temporary executables spawned by these tests are closed."""
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pythoncom
import win32com.client

import utility_toolbox as t


@pytest.fixture(scope='module')
def sleeper_exe(tmp_path_factory):
    folder = tmp_path_factory.mktemp('close-integration')
    compiler = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')
    if not compiler.is_file():
        pytest.skip('Windows .NET Framework C# compiler unavailable')
    source = folder / 'Sleeper.cs'
    source.write_text('class Program { static void Main() { System.Threading.Thread.Sleep(60000); } }', encoding='utf-8')
    executable = folder / 'ToolboxCloseFixture.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', f'/out:{executable}', str(source)],
                   check=True, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    return executable


@pytest.mark.parametrize('mode', ['executable', 'shortcut', 'folder', 'group'])
def test_close_only_requested_temporary_processes(mode, sleeper_exe, tmp_path, monkeypatch):
    target_dir = tmp_path / '应用 with spaces'
    outside_dir = tmp_path / '应用 with spaces-other'
    target_dir.mkdir()
    outside_dir.mkdir()
    target = target_dir / 'ToolboxCloseFixture.exe'
    outside = outside_dir / target.name
    shutil.copy2(sleeper_exe, target)
    shutil.copy2(sleeper_exe, outside)
    paths = [target, outside]
    if mode == 'folder':
        helper = target_dir / 'bin' / 'FixtureHelper.exe'
        helper.parent.mkdir()
        shutil.copy2(sleeper_exe, helper)
        paths.append(helper)
    processes = [subprocess.Popen([str(path)], creationflags=subprocess.CREATE_NO_WINDOW) for path in paths]
    callbacks, messages = [], []
    parent = SimpleNamespace(after=lambda _delay, callback: callbacks.append(callback))
    monkeypatch.setattr(t, 'log_line', lambda _message: None)
    try:
        deadline = time.monotonic() + 5
        while True:
            observed = {pid for pid, _path in t.running_process_paths()}
            if all(process.pid in observed for process in processes):
                break
            assert time.monotonic() < deadline, 'test process did not become queryable'
            time.sleep(0.02)
        selected = str(target_dir if mode == 'folder' else target)
        if mode == 'shortcut':
            shortcut_path = tmp_path / '测试程序.lnk'
            pythoncom.CoInitialize()
            try:
                shortcut = win32com.client.Dispatch('WScript.Shell').CreateShortcut(str(shortcut_path))
                shortcut.TargetPath = str(target)
                shortcut.Save()
                shortcut = None
            finally:
                pythoncom.CoUninitialize()
            selected = str(shortcut_path)
        if mode == 'group':
            controller = object.__new__(t.LauncherTab)
            controller.parent = parent
            controller.log = messages.append
        else:
            controller = object.__new__(t.DropCloseController)
            controller.root = parent
            controller.status_callback = messages.append
        worker = threading.Thread(target=controller._close_worker, args=([selected],))
        worker.start()
        worker.join(20)
        assert not worker.is_alive(), 'close worker did not finish within its bound'
        for callback in callbacks:
            callback()
        assert processes[0].wait(timeout=2) is not None
        assert processes[1].poll() is None, 'unrelated same-name process was closed'
        if mode == 'folder':
            assert processes[2].wait(timeout=2) is not None
        assert messages and not any('失败' in message for message in messages), messages
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
