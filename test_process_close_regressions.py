"""Process-close regressions; termination commands and process handles are mocked."""

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import pythoncom
import pytest

import utility_toolbox as toolbox


@pytest.fixture(autouse=True)
def immediate_mock_observation(monkeypatch):
    monkeypatch.setattr(toolbox, "PROCESS_CLOSE_OBSERVATION_SECONDS", 0, raising=False)


class ShortcutCloseTests(unittest.TestCase):
    def test_existing_mta_is_preserved_after_shortcut_resolution(self):
        results = []
        shortcut = SimpleNamespace(TargetPath=r"C:\Demo\editor.exe", Arguments="")
        shell = SimpleNamespace(CreateShortcut=lambda _path: shortcut)

        def resolve_in_mta():
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            try:
                results.append(toolbox.resolve_shortcut_target(r"C:\Demo\editor.lnk"))
                # This still requires the caller's original COM initialization.
                original("WScript.Shell")
                results.append("caller COM preserved")
            finally:
                pythoncom.CoUninitialize()

        original = toolbox.win32com_client_dispatch
        with mock.patch.object(toolbox, "win32com_client_dispatch", return_value=shell):
            worker = threading.Thread(target=resolve_in_mta)
            worker.start()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [r"C:\Demo\editor.exe", "caller COM preserved"])

    def test_shortcut_resolution_initializes_com_on_worker_thread(self):
        result = []

        def dispatch(_prog_id):
            # The real COM call verifies thread initialization, without saving a file.
            toolbox.win32com_client_dispatch_original("WScript.Shell")
            return SimpleNamespace(CreateShortcut=lambda _path: SimpleNamespace(
                TargetPath=r"C:\Demo\editor.exe", Arguments=""))

        original = toolbox.win32com_client_dispatch
        with mock.patch.object(toolbox, "win32com_client_dispatch_original", original, create=True), mock.patch.object(
            toolbox, "win32com_client_dispatch", side_effect=dispatch
        ):
            worker = threading.Thread(target=lambda: result.append(
                toolbox.resolve_shortcut_target(r"C:\Demo\editor.lnk")))
            worker.start()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [r"C:\Demo\editor.exe"])

    def test_shortcut_rejects_script_host_with_arguments(self):
        shortcut = SimpleNamespace(TargetPath=r"C:\Windows\System32\cmd.exe", Arguments=r'/c "C:\Demo\job.cmd"')
        shell = SimpleNamespace(CreateShortcut=lambda _path: shortcut)
        with mock.patch.object(toolbox, "win32com_client_dispatch", return_value=shell):
            with self.assertRaisesRegex(RuntimeError, "脚本|参数"):
                toolbox.resolve_shortcut_target(r"C:\Demo\job.lnk")

    def test_shortcut_error_is_not_silently_treated_as_target(self):
        with mock.patch.object(toolbox, "win32com_client_dispatch", side_effect=RuntimeError("COM unavailable")):
            with self.assertRaisesRegex(RuntimeError, "COM unavailable"):
                toolbox.resolve_shortcut_target(r"C:\Demo\editor.lnk")


class VerifiedTerminationTests(unittest.TestCase):
    def setUp(self):
        self.handles = {101: object(), 202: object()}
        self.patches = [
            mock.patch.object(toolbox, "_open_process_for_close", side_effect=lambda pid, expected_path=None: self.handles[pid], create=True),
            mock.patch.object(toolbox, "_wait_for_process_exit", return_value=False, create=True),
            mock.patch.object(toolbox.win32api, "CloseHandle"),
            mock.patch.object(toolbox, "PROCESS_EXIT_WAIT_SECONDS", 0, create=True),
        ]
        self.open_process, self.wait, self.close_handle, _ = [patch.start() for patch in self.patches]
        for patch in reversed(self.patches):
            self.addCleanup(patch.stop)

    def test_success_output_does_not_prove_process_exited(self):
        completed = SimpleNamespace(returncode=0, stdout="SUCCESS", stderr="")
        with mock.patch.object(toolbox.subprocess, "run", return_value=completed):
            closed, failures = toolbox.terminate_processes_fast([101], include_children=False)
        self.assertEqual(closed, set())
        self.assertIn(101, failures)
        self.assertIn("运行", failures[101])

    def test_mixed_batch_not_found_does_not_hide_access_denied(self):
        batch = SimpleNamespace(returncode=1, stdout='', stderr='The process "101" not found.\nPID 202: Access is denied.')
        denied = SimpleNamespace(returncode=1, stdout='', stderr='Access is denied.')
        checks = {101: 0, 202: 0}

        def exited(handle, timeout_ms=0):
            pid = next(pid for pid, value in self.handles.items() if value is handle)
            checks[pid] += 1
            return pid == 101 and checks[pid] > 1

        self.wait.side_effect = exited
        with mock.patch.object(toolbox.subprocess, "run", side_effect=[batch, denied]) as run:
            closed, failures = toolbox.terminate_processes_fast([101, 202], include_children=False)
        self.assertEqual(closed, {101})
        self.assertIn(202, failures)
        self.assertIn("Access is denied", failures[202])
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all("/T" not in call.args[0] for call in run.call_args_list))

    def test_timeout_is_reported_per_pid(self):
        error = toolbox.subprocess.TimeoutExpired("taskkill", 10)
        with mock.patch.object(toolbox.subprocess, "run", side_effect=error) as run:
            closed, failures = toolbox.terminate_processes_fast([101], include_children=False)
        self.assertEqual(closed, set())
        self.assertIn(101, failures)
        self.assertRegex(failures[101].casefold(), "timed out|timeout")
        self.assertIsNotNone(run.call_args.kwargs.get("timeout"))

    def test_expected_path_is_checked_before_termination(self):
        self.open_process.side_effect = RuntimeError("PID 已由其他进程占用")
        with mock.patch.object(toolbox.subprocess, "run") as run:
            closed, failures = toolbox.terminate_processes_fast(
                [101], include_children=False, expected_paths={101: r"C:\Demo\editor.exe"})
        self.assertEqual(closed, set())
        self.assertIn(101, failures)
        run.assert_not_called()

    def test_retained_handle_is_closed_after_verification(self):
        self.wait.side_effect = [False, True]
        completed = SimpleNamespace(returncode=0, stdout="SUCCESS", stderr="")
        with mock.patch.object(toolbox.subprocess, "run", return_value=completed):
            closed, failures = toolbox.terminate_processes_fast([101], include_children=False)
        self.assertEqual((closed, failures), ({101}, {}))
        self.close_handle.assert_called_once_with(self.handles[101])

    def test_disappeared_process_is_not_sent_to_taskkill(self):
        self.open_process.return_value = None
        self.open_process.side_effect = None
        with mock.patch.object(toolbox.subprocess, "run") as run:
            result = toolbox.terminate_processes_fast([101], include_children=False)
        self.assertEqual(result, ({101}, {}))
        run.assert_not_called()

    def test_cannot_query_process_is_failure_not_success(self):
        self.open_process.side_effect = PermissionError("Access is denied")
        with mock.patch.object(toolbox.subprocess, "run") as run:
            closed, failures = toolbox.terminate_processes_fast([101], include_children=False)
        self.assertFalse(closed)
        self.assertIn("Access is denied", failures[101])
        run.assert_not_called()

    def test_wait_error_does_not_report_closed_or_leak_handle(self):
        self.wait.side_effect = [False, RuntimeError("invalid wait state")]
        completed = SimpleNamespace(returncode=0, stdout="SUCCESS", stderr="")
        with mock.patch.object(toolbox.subprocess, "run", return_value=completed):
            closed, failures = toolbox.terminate_processes_fast([101], include_children=False)
        self.assertFalse(closed)
        self.assertIn("invalid wait state", failures[101])
        self.close_handle.assert_called_once_with(self.handles[101])

    def test_missing_taskkill_is_reported_and_releases_handle(self):
        with mock.patch.object(toolbox.subprocess, "run", side_effect=FileNotFoundError("taskkill unavailable")):
            closed, failures = toolbox.terminate_processes_fast([101], include_children=False)
        self.assertFalse(closed)
        self.assertIn("taskkill unavailable", failures[101])
        self.close_handle.assert_called_once_with(self.handles[101])


class ProcessEnumerationTests(unittest.TestCase):
    def test_drop_launcher_exe_does_not_expand_to_neighboring_apps(self):
        target = r"C:\Downloads\game-launcher.exe"
        processes = [(101, target), (202, r"C:\Downloads\unrelated.exe"), (303, r"C:\Downloads\Other\helper.exe")]
        with mock.patch.object(toolbox, "is_current_process", return_value=False):
            result = toolbox.match_processes_for_drop_paths([target], processes)
        self.assertEqual(result, [(101, target)])

    def test_bad_shortcut_does_not_discard_other_launcher_paths(self):
        bad = r"C:\Demo\broken.lnk"
        good = r"C:\Demo\editor.exe"
        def resolve(path):
            if path == bad:
                raise RuntimeError("broken shortcut")
            return path
        with mock.patch.object(toolbox, "resolve_shortcut_target", side_effect=resolve):
            paths = toolbox.launcher_close_paths([{"path": bad}, {"path": good}])
        self.assertIn(good, paths)
        self.assertIn(bad, paths)  # Worker retains the failed target for visible reporting.

    def test_changed_executable_path_rejects_pid_and_releases_handle(self):
        handle = object()
        with mock.patch.object(toolbox.win32api, "OpenProcess", return_value=handle), mock.patch.object(
            toolbox, "_process_path_from_handle", return_value=r"C:\Other\other.exe"
        ), mock.patch.object(toolbox.win32api, "CloseHandle") as close:
            with self.assertRaisesRegex(RuntimeError, "其他路径"):
                toolbox._open_process_for_close(101, r"C:\Demo\editor.exe")
        close.assert_called_once_with(handle)

    def test_uses_limited_rights_image_path_query(self):
        handle = object()
        with mock.patch.object(toolbox.win32process, "EnumProcesses", return_value=[101]), mock.patch.object(
            toolbox.win32api, "OpenProcess", return_value=handle
        ), mock.patch.object(toolbox, "_process_path_from_handle", return_value=r"C:\Demo\editor.exe", create=True) as query, mock.patch.object(
            toolbox.win32process, "GetModuleFileNameEx", side_effect=RuntimeError("insufficient module rights")
        ), mock.patch.object(toolbox.win32api, "CloseHandle"):
            result = toolbox.running_process_paths()
        self.assertEqual(result, [(101, r"C:\Demo\editor.exe")])
        query.assert_called_once_with(handle)

    def test_drop_folder_accepts_process_snapshot_iterator(self):
        processes = iter([(101, r"C:\Demo\editor.exe"), (202, r"C:\DemoBackup\editor.exe")])
        with mock.patch.object(toolbox, "is_current_process", return_value=False), mock.patch.object(
            toolbox, "app_executable", return_value=r"C:\Toolbox\toolbox.exe"
        ):
            result = toolbox.match_processes_for_drop_paths([r"C:\Demo"], processes)
        self.assertEqual(result, [(101, r"C:\Demo\editor.exe")])


class CloseWorkerTests(unittest.TestCase):
    def test_launcher_continues_valid_exe_and_retains_target_failure_status(self):
        tab = toolbox.LauncherTab.__new__(toolbox.LauncherTab)
        tab.parent = SimpleNamespace(after=lambda _delay, callback: callback())
        messages = []
        tab.log = messages.append
        path = r"C:\Demo\editor.exe"
        with mock.patch.object(toolbox, "running_process_paths", side_effect=[[(101, path)], []]), mock.patch.object(
            toolbox, "is_current_process", return_value=False
        ), mock.patch.object(toolbox, "terminate_processes_fast", return_value=({101}, {})) as terminate:
            tab._close_worker([path, r"C:\Demo\helper.py"])
        terminate.assert_called_once()
        self.assertEqual(dict(terminate.call_args.args[0]), {101: path})
        self.assertIn("失败", messages[-1])
        self.assertIn("helper.py", messages[-1])
        self.assertTrue(any("101" in message for message in messages))

    def test_drop_continues_valid_exe_and_retains_target_failure_status(self):
        drop = toolbox.DropCloseController.__new__(toolbox.DropCloseController)
        drop.root = SimpleNamespace(after=lambda _delay, callback: callback())
        messages = []
        drop.status_callback = messages.append
        path = r"C:\Demo\editor.exe"
        with mock.patch.object(toolbox, "log_line"), mock.patch.object(toolbox, "running_process_paths", side_effect=[[(101, path)], []]), mock.patch.object(
            toolbox, "is_current_process", return_value=False
        ), mock.patch.object(toolbox, "terminate_processes_fast", return_value=({101}, {})) as terminate:
            drop._close_worker([r"C:\Demo\helper.py", path])
        terminate.assert_called_once()
        self.assertEqual(dict(terminate.call_args.args[0]), {101: path})
        self.assertIn("失败", messages[-1])
        self.assertIn("helper.py", messages[-1])
        self.assertIn("关闭 1", messages[-1])

    def test_launcher_reports_process_restarted_after_confirmed_exit(self):
        tab = toolbox.LauncherTab.__new__(toolbox.LauncherTab)
        tab.parent = SimpleNamespace(after=lambda _delay, callback: callback())
        messages = []
        tab.log = messages.append
        path = r"C:\Demo\editor.exe"
        with mock.patch.object(toolbox, "running_process_paths", side_effect=[[(101, path)], [(202, path)]]), mock.patch.object(
            toolbox, "is_current_process", return_value=False
        ), mock.patch.object(toolbox, "terminate_processes_fast", return_value=({101}, {})) as terminate:
            tab._close_worker([path])
        self.assertIn("仍有", messages[-1])
        self.assertIn("重新启动", messages[-1])
        self.assertIn("202", messages[-1])
        terminate.assert_called_once()

    def test_drop_reports_process_restarted_after_confirmed_exit(self):
        drop = toolbox.DropCloseController.__new__(toolbox.DropCloseController)
        drop.root = SimpleNamespace(after=lambda _delay, callback: callback())
        messages = []
        drop.status_callback = messages.append
        path = r"C:\Demo\editor.exe"
        with mock.patch.object(toolbox, "log_line"), mock.patch.object(
            toolbox, "running_process_paths", side_effect=[[(101, path)], [(202, path)]]
        ), mock.patch.object(toolbox, "is_current_process", return_value=False), mock.patch.object(
            toolbox, "terminate_processes_fast", return_value=({101}, {})
        ) as terminate:
            drop._close_worker([r"C:\Demo"])
        self.assertIn("仍有", messages[-1])
        self.assertIn("重新启动", messages[-1])
        self.assertIn("202", messages[-1])
        terminate.assert_called_once()

    def test_launcher_script_path_has_explicit_unsupported_message(self):
        tab = toolbox.LauncherTab.__new__(toolbox.LauncherTab)
        tab.parent = SimpleNamespace(after=lambda _delay, callback: callback())
        messages = []
        tab.log = messages.append
        with mock.patch.object(toolbox, "running_process_paths", return_value=[]), mock.patch.object(toolbox, "terminate_processes_fast") as terminate:
            tab._close_worker([r"C:\Demo\job.py"])
        self.assertIn("暂不支持", messages[-1])
        terminate.assert_not_called()

    def test_drop_script_path_has_explicit_unsupported_message(self):
        drop = toolbox.DropCloseController.__new__(toolbox.DropCloseController)
        drop.root = SimpleNamespace(after=lambda _delay, callback: callback())
        messages = []
        drop.status_callback = messages.append
        with mock.patch.object(toolbox, "log_line"), mock.patch.object(toolbox, "running_process_paths", return_value=[]), mock.patch.object(
            toolbox, "terminate_processes_fast"
        ) as terminate:
            drop._close_worker([r"C:\Demo\job.py"])
        self.assertIn("暂不支持", messages[-1])
        terminate.assert_not_called()

    def test_launcher_reports_enumeration_error(self):
        tab = toolbox.LauncherTab.__new__(toolbox.LauncherTab)
        tab.parent = SimpleNamespace(after=lambda _delay, callback: callback())
        messages = []
        tab.log = messages.append
        with mock.patch.object(toolbox, "running_process_paths", side_effect=RuntimeError("snapshot unavailable")):
            tab._close_worker([r"C:\Demo\editor.exe"])
        self.assertTrue(any("snapshot unavailable" in message for message in messages))

    def test_drop_reports_enumeration_error(self):
        drop = toolbox.DropCloseController.__new__(toolbox.DropCloseController)
        drop.root = SimpleNamespace(after=lambda _delay, callback: callback())
        messages = []
        drop.status_callback = messages.append
        with mock.patch.object(toolbox, "log_line"), mock.patch.object(toolbox, "running_process_paths", side_effect=RuntimeError("snapshot unavailable")):
            drop._close_worker([r"C:\Demo\editor.exe"])
        self.assertTrue(any("snapshot unavailable" in message for message in messages))

    def test_drop_does_not_terminate_unmatched_descendants(self):
        drop = toolbox.DropCloseController.__new__(toolbox.DropCloseController)
        drop.root = SimpleNamespace(after=lambda _delay, callback: callback())
        drop.status_callback = mock.Mock()
        targets = [(101, r"C:\Demo\editor.exe")]
        with mock.patch.object(toolbox, "log_line"), mock.patch.object(toolbox, "running_process_paths", return_value=targets), mock.patch.object(
            toolbox, "match_processes_for_drop_paths", return_value=targets
        ), mock.patch.object(toolbox, "is_current_process", return_value=False), mock.patch.object(
            toolbox, "terminate_processes_fast", return_value=({101}, {})
        ) as terminate, mock.patch.object(toolbox, "terminate_process"):
            drop._close_worker([r"C:\Demo\editor.exe"])
        self.assertEqual(terminate.call_args.kwargs["include_children"], False)
        self.assertEqual(terminate.call_args.kwargs["expected_paths"], dict(targets))


if __name__ == "__main__":
    unittest.main()
