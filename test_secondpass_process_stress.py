"""Second-pass close/UI fault injection: never terminates real user processes."""

from types import SimpleNamespace
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

import utility_toolbox as t


@pytest.fixture(autouse=True)
def immediate_mock_observation(monkeypatch):
    monkeypatch.setattr(t, "PROCESS_CLOSE_OBSERVATION_SECONDS", 0, raising=False)


def drop_controller():
    messages = []
    controller = object.__new__(t.DropCloseController)
    controller.root = SimpleNamespace(after=lambda _delay, callback: callback())
    controller.status_callback = messages.append
    return controller, messages


def launcher_controller():
    messages = []
    controller = object.__new__(t.LauncherTab)
    controller.parent = SimpleNamespace(after=lambda _delay, callback: callback())
    controller.log = messages.append
    return controller, messages


@pytest.mark.parametrize("kind", ["drop", "launcher"])
def test_unqueryable_process_snapshot_is_not_reported_as_complete(kind):
    controller, messages = drop_controller() if kind == "drop" else launcher_controller()
    with mock.patch.object(t, "log_line"), mock.patch.object(t.win32process, "EnumProcesses", return_value=[101]), mock.patch.object(
        t.win32api, "OpenProcess", side_effect=PermissionError("Access is denied")
    ), mock.patch.object(t, "terminate_processes_fast") as terminate:
        controller._close_worker([r"C:\Demo\editor.exe"])
    terminate.assert_not_called()
    assert any(word in messages[-1] for word in ["无法检查", "无法查询", "不完整", "权限"]), messages


def test_old_learned_neighbor_in_shared_folder_is_not_implicitly_trusted():
    app = {"path": r"C:\Users\User\Downloads\game-launcher.exe",
           "close_paths": [r"C:\Users\User\Downloads\unrelated-editor.exe"]}
    targets = t.launcher_close_paths([app])
    assert app["close_paths"][0] not in targets, targets


@pytest.mark.parametrize("kind", ["drop", "launcher"])
def test_burst_of_1000_close_requests_starts_only_one_inflight_worker(kind):
    controller, _messages = drop_controller() if kind == "drop" else launcher_controller()
    with mock.patch.object(t.threading, "Thread") as thread:
        for _ in range(1000):
            controller.close_paths([r"C:\Demo\editor.exe"])
    assert thread.call_count == 1, thread.call_count


def test_grandparent_process_is_protected_during_folder_matching():
    parents = {None: 998, 999: 998, 998: 997, 997: 0}
    processes = [(999, r"C:\Suite\toolbox.exe"), (998, r"C:\Suite\loader.exe"),
                 (997, r"C:\Suite\desktop-host.exe"), (101, r"C:\Suite\app.exe")]
    with mock.patch.object(t.os, "getpid", return_value=999), mock.patch.object(
        t, "parent_process_id", side_effect=lambda pid=None: parents.get(pid)
    ), mock.patch.object(t, "current_executable_path", return_value=r"C:\Suite\toolbox.exe"), mock.patch.object(
        t, "app_executable", return_value=r"C:\Suite\toolbox.exe"
    ):
        matches = t.match_processes_for_drop_paths([r"C:\Suite"], processes)
    assert matches == [(101, r"C:\Suite\app.exe")], matches


def test_automatic_window_refresh_preserves_last_action_result():
    app = object.__new__(t.UtilityToolbox)
    app.topmost = SimpleNamespace(list_windows=lambda: [(301, "New window")])
    app.window_snapshot = ()
    app.window_rows = {}
    app.window_tree = mock.Mock()
    app.window_tree.selection.return_value = []
    app.window_tree.get_children.return_value = []
    app.window_tree.insert.return_value = "row1"
    app.set_status = mock.Mock()
    app.refresh_window_list(auto=True)
    app.set_status.assert_not_called()


def test_tray_popup_resources_are_released_after_each_display():
    tray = object.__new__(t.TrayIcon)
    tray.root = object()
    tray.topmost = SimpleNamespace(pin_current=mock.Mock(), unpin_current=mock.Mock(), unpin_all=mock.Mock())
    with mock.patch.object(t.tk, "Menu") as menu, mock.patch.object(t.win32gui, "GetCursorPos", return_value=(0, 0)):
        for _ in range(1000):
            tray.show_tk_menu()
    assert menu.return_value.destroy.call_count == 1000


def test_large_batch_closes_each_unique_pid_handle_once_without_process_trees():
    pids = list(range(10000, 12000))
    counts = {}

    def exited(handle, _timeout_ms=0):
        counts[handle] = counts.get(handle, 0) + 1
        return counts[handle] > 1

    with mock.patch.object(t, "_open_process_for_close", side_effect=lambda pid, _expected=None: pid), mock.patch.object(
        t, "_wait_for_process_exit", side_effect=exited
    ), mock.patch.object(t.win32api, "CloseHandle") as release, mock.patch.object(
        t.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")
    ) as run:
        closed, errors = t.terminate_processes_fast(pids + pids, include_children=False)
    assert closed == set(pids)
    assert not errors
    assert release.call_count == len(pids)
    assert run.call_count == 1
    assert "/T" not in run.call_args.args[0]


def test_repeated_mixed_targets_keep_both_failure_and_restart_in_final_status():
    path = r"C:\Demo\editor.exe"
    for _ in range(100):
        controller, messages = launcher_controller()
        with mock.patch.object(t, "running_process_paths", side_effect=[[(101, path)], [(202, path)]]), mock.patch.object(
            t, "is_current_process", return_value=False
        ), mock.patch.object(t, "terminate_processes_fast", return_value=({101}, {})) as terminate:
            controller._close_worker([r"C:\Demo\helper.py", path])
        assert "目标失败 1" in messages[-1]
        assert "重新启动" in messages[-1]
        assert "202" in messages[-1]
        terminate.assert_called_once()


def test_500_taskbar_restart_notifications_only_readd_icon():
    tray = object.__new__(t.TrayIcon)
    tray.hwnd = 123
    tray.icon_handle = 456
    with mock.patch.object(t, "log_line"), mock.patch.object(t.win32gui, "Shell_NotifyIcon") as notify, mock.patch.object(
        t.win32gui, "CreateWindow"
    ) as create:
        for _ in range(500):
            tray._on_taskbar_created(123, tray.TASKBAR_CREATED, 0, 0)
    assert notify.call_count == 500
    assert all(call.args[0] == t.win32gui.NIM_ADD for call in notify.call_args_list)
    create.assert_not_called()


def test_500_readonly_autostart_refreshes_do_not_rewrite_registry():
    saved = r'"C:\Tools\toolbox.exe" --minimized-to-tray'
    with mock.patch.object(t, "read_autostart_command", return_value=saved), mock.patch.object(t, "autostart_command", return_value=saved), mock.patch.object(
        t, "set_autostart"
    ) as write:
        for _ in range(500):
            assert t.is_autostart_enabled()
    write.assert_not_called()


def test_launching_valid_second_copy_does_not_take_over_existing_autostart(tmp_path):
    original = tmp_path / "installed toolbox.exe"
    original.write_bytes(b"inert executable fixture; never executed")
    saved = f'"{original}" --minimized-to-tray'
    current = f'"{tmp_path / "another toolbox.exe"}" --minimized-to-tray'
    with mock.patch.object(t, "read_autostart_command", return_value=saved), mock.patch.object(t, "autostart_command", return_value=current), mock.patch.object(
        t, "set_autostart"
    ) as write:
        repaired = t.repair_autostart_path()
    assert not repaired
    write.assert_not_called()


@pytest.mark.parametrize("kind", ["drop", "launcher"])
def test_stop_rejects_future_close_requests(kind):
    controller, _messages = drop_controller() if kind == "drop" else launcher_controller()
    controller.stop()
    with mock.patch.object(t.threading, "Thread") as thread:
        controller.close_paths([r"C:\Demo\editor.exe"])
    thread.assert_not_called()


@pytest.mark.parametrize("kind", ["drop", "launcher"])
def test_worker_completion_allows_next_close_request(kind):
    controller, _messages = drop_controller() if kind == "drop" else launcher_controller()
    calls = []

    class InlineThread:
        def __init__(self, target, args=(), **_kwargs):
            self.target, self.args = target, args
        def start(self):
            self.target(*self.args)

    controller._close_worker = lambda paths: calls.append(paths)
    with mock.patch.object(t.threading, "Thread", InlineThread):
        for _ in range(50):
            controller.close_paths([r"C:\Demo\editor.exe"])
    assert len(calls) == 50


@pytest.mark.parametrize("kind", ["drop", "launcher"])
def test_1000_parallel_requests_share_one_blocked_worker(kind):
    controller, _messages = drop_controller() if kind == "drop" else launcher_controller()
    controller._close_lock = threading.Lock()
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    starts = []

    def blocked_worker(_paths):
        starts.append(1)
        entered.set()
        try:
            assert release.wait(5)
        finally:
            completed.set()

    controller._close_worker = blocked_worker
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(lambda _index: controller.close_paths([r"C:\Demo\editor.exe"]), range(1000)))
        assert entered.wait(2)
        assert len(starts) == 1
    finally:
        release.set()
        assert completed.wait(5)


@pytest.mark.parametrize("kind", ["drop", "launcher"])
def test_stop_during_enumeration_prevents_later_termination(kind):
    controller, _messages = drop_controller() if kind == "drop" else launcher_controller()
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    path = r"C:\Demo\editor.exe"

    def snapshot():
        entered.set()
        assert release.wait(5)
        return [(101, path)]

    original = controller._close_worker
    def worker(paths):
        try:
            original(paths)
        finally:
            completed.set()
    controller._close_worker = worker
    with mock.patch.object(t, "log_line"), mock.patch.object(t, "running_process_paths", side_effect=snapshot), mock.patch.object(
        t, "is_current_process", return_value=False
    ), mock.patch.object(t, "terminate_processes_fast") as terminate:
        try:
            controller.close_paths([path])
            assert entered.wait(2)
            controller.stop()
        finally:
            release.set()
            assert completed.wait(5)
        terminate.assert_not_called()


def test_process_protection_snapshot_is_not_recreated_per_candidate():
    processes = [(index, fr"C:\Apps\app{index}.exe") for index in range(1000, 1400)]
    with mock.patch.object(t, "parent_process_id", return_value=1) as parent, mock.patch.object(t, "app_executable", return_value=r"C:\Toolbox\toolbox.exe"):
        t.match_processes_for_drop_paths([r"C:\Apps\app1000.exe"], processes)
    assert parent.call_count <= 2, parent.call_count


def test_observer_reports_later_matching_process_without_terminating_it(monkeypatch):
    monkeypatch.setattr(t, "PROCESS_CLOSE_OBSERVATION_SECONDS", 2)
    path = r"C:\Demo\editor.exe"
    with mock.patch.object(t, "running_process_paths", side_effect=[[], [(202, path)]]), mock.patch.object(
        t, "current_process_protected_pids", return_value=set()
    ) as protected, mock.patch.object(t.time, "sleep"), mock.patch.object(t, "terminate_processes_fast") as terminate:
        remaining, _notice, cancelled = t.observe_closed_process_targets([path])
    assert remaining == [(202, path)]
    assert not cancelled
    protected.assert_called_once()
    terminate.assert_not_called()


def test_observer_cancel_does_not_query_or_wait():
    with mock.patch.object(t, "running_process_paths") as snapshot, mock.patch.object(t.time, "sleep") as sleep:
        remaining, _notice, cancelled = t.observe_closed_process_targets([r"C:\Demo\editor.exe"], should_stop=lambda: True)
    assert not remaining and cancelled
    snapshot.assert_not_called()
    sleep.assert_not_called()


def test_observer_does_not_begin_another_snapshot_after_deadline(monkeypatch):
    monkeypatch.setattr(t, "PROCESS_CLOSE_OBSERVATION_SECONDS", 2)
    with mock.patch.object(t, "running_process_paths", return_value=[]) as snapshot, mock.patch.object(
        t, "current_process_protected_pids", return_value=set()
    ), mock.patch.object(t.time, "monotonic", side_effect=[0, 0.1, 2.0]), mock.patch.object(t.time, "sleep"):
        remaining, _notice, cancelled = t.observe_closed_process_targets([r"C:\Demo\editor.exe"])
    assert not remaining and not cancelled
    snapshot.assert_called_once()
