"""Only compiled temporary fixtures are launched or terminated by this module."""

import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import utility_toolbox as t


SOURCE = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
class CloseStressFixture {
    static Process StartSleeper(string executable) {
        return Process.Start(new ProcessStartInfo(executable, "sleep") {
            UseShellExecute = false, CreateNoWindow = true
        });
    }
    static void Main(string[] args) {
        if (args.Length == 0 || args[0] == "sleep") { Thread.Sleep(60000); return; }
        string executable = args[1], pidFile = args[2];
        Process child = StartSleeper(executable);
        File.WriteAllText(pidFile, child.Id.ToString());
        if (args[0] == "spawn-exit") return;
        if (args[0] == "watchdog") {
            child.WaitForExit();
            Thread.Sleep(500);
            Process replacement = StartSleeper(executable);
            File.AppendAllText(pidFile, "\n" + replacement.Id.ToString());
            replacement.WaitForExit();
        }
    }
}
'''


@pytest.fixture(scope="module")
def compiled_fixture(tmp_path_factory):
    folder = tmp_path_factory.mktemp("close-real-fixture")
    compiler = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
    if not compiler.is_file():
        pytest.skip(".NET Framework C# compiler unavailable")
    source = folder / "CloseStressFixture.cs"
    source.write_text(SOURCE, encoding="utf-8")
    executable = folder / "CloseStressFixture.exe"
    subprocess.run([str(compiler), "/nologo", "/target:winexe", f"/out:{executable}", str(source)],
                   check=True, capture_output=True, timeout=30,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    return executable


def copy_fixture(compiled_fixture, directory, name="CloseStressFixture.exe"):
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    shutil.copy2(compiled_fixture, destination)
    return destination


def start_fixture(path, *args):
    return subprocess.Popen([str(path), *map(str, args)], creationflags=subprocess.CREATE_NO_WINDOW)


def await_pids(pid_file, minimum=1):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if pid_file.exists():
            values = [int(value) for value in pid_file.read_text(encoding="utf-8-sig").splitlines() if value.strip().isdigit()]
            if len(values) >= minimum:
                return values
        time.sleep(0.02)
    raise AssertionError(f"Controlled fixture did not report {minimum} process IDs")


def open_owned_handle(pid, executable):
    handle = t.win32api.OpenProcess(t.win32con.PROCESS_QUERY_LIMITED_INFORMATION | t.win32con.SYNCHRONIZE | t.win32con.PROCESS_TERMINATE, False, pid)
    try:
        assert t.normalize_process_path(t._process_path_from_handle(handle)) == t.normalize_process_path(str(executable))
    except BaseException:
        t.win32api.CloseHandle(handle)
        raise
    return handle


def cleanup_owned_handles(handles):
    for handle in handles:
        try:
            if not t._wait_for_process_exit(handle):
                t.win32process.TerminateProcess(handle, 0)
                assert t._wait_for_process_exit(handle, 3000)
        finally:
            t.win32api.CloseHandle(handle)


def close_through_worker(paths, kind="drop"):
    messages = []
    parent = SimpleNamespace(after=lambda _delay, callback: callback())
    if kind == "launcher":
        controller = object.__new__(t.LauncherTab)
        controller.parent = parent
        controller.log = messages.append
    else:
        controller = object.__new__(t.DropCloseController)
        controller.root = parent
        controller.status_callback = messages.append
    with mock.patch.object(t, "log_line"):
        controller._close_worker([str(path) for path in paths])
    return messages


@pytest.mark.parametrize("kind", ["drop", "launcher"])
def test_same_executable_multiple_processes_preserves_other_directory(kind, compiled_fixture, tmp_path):
    target = copy_fixture(compiled_fixture, tmp_path / "target")
    unrelated = copy_fixture(compiled_fixture, tmp_path / "unrelated")
    processes = [start_fixture(target) for _ in range(5)] + [start_fixture(unrelated)]
    try:
        messages = close_through_worker([target], kind=kind)
        assert all(process.wait(timeout=3) is not None for process in processes[:5]), messages
        assert processes[-1].poll() is None, "Different-directory same-name process was closed"
        assert not any("关闭失败" in message for message in messages), messages
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)


def test_directory_closes_nested_background_but_not_prefix_neighbor(compiled_fixture, tmp_path):
    directory = tmp_path / "suite"
    main = copy_fixture(compiled_fixture, directory)
    nested = copy_fixture(compiled_fixture, directory / "bin", "BackgroundFixture.exe")
    unrelated = copy_fixture(compiled_fixture, tmp_path / "suite-other")
    processes = [start_fixture(main), start_fixture(nested), start_fixture(unrelated)]
    try:
        messages = close_through_worker([directory])
        assert processes[0].wait(timeout=3) is not None, messages
        assert processes[1].wait(timeout=3) is not None, messages
        assert processes[2].poll() is None
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)


def test_directory_closes_background_after_launch_parent_has_exited(compiled_fixture, tmp_path):
    directory = tmp_path / "suite"
    launcher = copy_fixture(compiled_fixture, directory, "FixtureLauncher.exe")
    child = copy_fixture(compiled_fixture, directory / "bin", "BackgroundFixture.exe")
    pid_file = tmp_path / "spawned-pid.txt"
    parent = start_fixture(launcher, "spawn-exit", child, pid_file)
    handles = []
    try:
        child_pid = await_pids(pid_file)[0]
        handles.append(open_owned_handle(child_pid, child))
        assert parent.wait(timeout=3) == 0
        assert not t._wait_for_process_exit(handles[0])
        messages = close_through_worker([directory])
        assert t._wait_for_process_exit(handles[0], 3000), messages
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=3)
        cleanup_owned_handles(handles)


@pytest.mark.parametrize("kind", ["drop", "launcher"])
def test_delayed_watchdog_restart_is_reported_without_chasing(kind, compiled_fixture, tmp_path):
    target = copy_fixture(compiled_fixture, tmp_path / "target")
    watchdog = copy_fixture(compiled_fixture, tmp_path / "watchdog", "WatchdogFixture.exe")
    pid_file = tmp_path / "watchdog-pids.txt"
    guard = start_fixture(watchdog, "watchdog", target, pid_file)
    handles = []
    try:
        original_pid = await_pids(pid_file)[0]
        handles.append(open_owned_handle(original_pid, target))
        started = time.monotonic()
        messages = close_through_worker([target], kind=kind)
        worker_elapsed = time.monotonic() - started
        reported_pids = await_pids(pid_file, minimum=2)
        replacement_pid = reported_pids[-1]
        handles.append(open_owned_handle(replacement_pid, target))
        assert t._wait_for_process_exit(handles[0], 1000)
        assert not t._wait_for_process_exit(handles[1]), "Close operation must not indefinitely chase watchdog restarts"
        assert any("重新启动" in message and str(replacement_pid) in message for message in messages), {
            "kind": kind, "worker_elapsed_seconds": round(worker_elapsed, 3),
            "original_pid": original_pid, "replacement_pid": replacement_pid, "messages": messages,
        }
    finally:
        if guard.poll() is None:
            guard.kill()
        guard.wait(timeout=3)
        # The guard is stopped first so cleanup cannot trigger another restart.
        known_pids = set()
        if pid_file.exists():
            known_pids = {int(value) for value in pid_file.read_text(encoding="utf-8-sig").splitlines() if value.strip().isdigit()}
        for pid in known_pids:
            try:
                handle = open_owned_handle(pid, target)
            except (OSError, t.win32api.error):
                continue
            handles.append(handle)
        cleanup_owned_handles(handles)
