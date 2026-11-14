import copy
import gc
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

import game_save_manager_launcher as gsm
import utility_toolbox as toolbox


class GameSaveManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="工具箱 GSM ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        patcher = mock.patch.object(gsm, "activate_existing_window", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def executable(self, relative="game_save_manager/Game Save Manager.exe"):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test executable")
        for required in ("resources/app.asar", "resources.pak", "icudtl.dat", "database/database.db"):
            companion = path.parent / required
            companion.parent.mkdir(parents=True, exist_ok=True)
            companion.write_bytes(b"fixture")
        return path

    def test_malformed_settings_report_validation_error(self):
        for value in (None, False, 7, [], "broken", {"executable": 5}, {"executable": []}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gsm.find_executable({"game_save_manager": value}, self.base)

    def test_wrong_program_in_distribution_is_rejected(self):
        exe = self.executable("game_save_manager/Updater.exe")
        with self.assertRaises(ValueError):
            gsm.find_executable({"game_save_manager": {"executable": str(exe)}})

    def test_missing_portable_components_are_not_reported_as_ready(self):
        exe = self.executable()
        for required in ("resources/app.asar", "resources.pak", "icudtl.dat", "database/database.db"):
            companion = exe.parent / required
            companion.unlink()
            with self.subTest(required=required), self.assertRaises(FileNotFoundError):
                gsm.find_executable({}, self.base)
            companion.write_bytes(b"fixture")

    def test_existing_window_is_reused_without_starting_another_process(self):
        exe = self.executable()
        with mock.patch.object(gsm, "activate_existing_window", return_value=True), mock.patch.object(gsm.subprocess, "Popen") as popen:
            self.assertEqual(gsm.launch({}, self.base), exe)
            popen.assert_not_called()

    def test_finds_portable_next_to_toolbox_independent_of_cwd(self):
        exe = self.executable()
        self.assertEqual(gsm.find_executable({}, self.base), exe)

    def test_frozen_base_is_executable_directory_not_extraction_directory(self):
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(
            sys, "executable", str(self.base / "工具箱.exe")
        ), mock.patch.object(sys, "_MEIPASS", "C:/unrelated-temp", create=True):
            self.assertEqual(gsm.application_directory(), self.base)

    def test_explicit_program_location_overrides_bundled_copy(self):
        self.executable()
        external = self.executable("另一个目录/Game Save Manager.exe")
        config = {"game_save_manager": {"executable": str(external)}}
        self.assertEqual(gsm.find_executable(config, self.base), external)

    def test_stale_explicit_location_does_not_silently_launch_another_copy(self):
        self.executable()
        config = {"game_save_manager": {"executable": str(self.base / "missing.exe")}}
        with self.assertRaises(FileNotFoundError):
            gsm.find_executable(config, self.base)

    def test_directory_or_non_executable_is_rejected(self):
        for path in (self.base, self.base / "readme.txt"):
            if path.suffix:
                path.write_text("not a program", encoding="utf-8")
            with self.subTest(path=path), self.assertRaises((FileNotFoundError, ValueError)):
                gsm.find_executable({"game_save_manager": {"executable": str(path)}}, self.base)

    def test_missing_distribution_reports_error_without_starting_process(self):
        with mock.patch.object(gsm.subprocess, "Popen") as popen:
            with self.assertRaises(FileNotFoundError):
                gsm.launch({}, self.base)
            popen.assert_not_called()

    def test_launch_uses_argument_list_and_program_working_directory(self):
        exe = self.executable()
        with mock.patch.object(gsm.subprocess, "Popen") as popen:
            self.assertEqual(gsm.launch({}, self.base), exe)
            popen.assert_called_once_with([str(exe), f"--user-data-dir={self.base / 'data' / 'GameSaveManager'}"], cwd=str(exe.parent))

    def test_launch_error_is_propagated_and_old_config_is_untouched(self):
        self.executable()
        config = {"backup": {"sources": ["C:/saves"], "monitor_enabled": True,
                             "backup_dir": "C:/old-backups"}}
        before = copy.deepcopy(config)
        with mock.patch.object(gsm.subprocess, "Popen", side_effect=OSError("access denied")):
            with self.assertRaises(OSError):
                gsm.launch(config, self.base)
        self.assertEqual(config, before)


class WindowActivationTests(unittest.TestCase):
    def setUp(self):
        self.exe = Path("C:/GSM/Game Save Manager.exe")
        self.handle = mock.Mock()
        self.start_patch(gsm.win32gui, "EnumWindows", side_effect=lambda visit, context: visit(123, context))
        self.start_patch(gsm.win32gui, "GetClassName", return_value="Chrome_WidgetWin_1")
        self.start_patch(gsm.win32gui, "GetWindow", return_value=0)
        self.start_patch(gsm.win32gui, "GetWindowText", return_value="游戏存档管理器")
        self.start_patch(gsm.win32process, "GetWindowThreadProcessId", return_value=(1, 99))
        self.open_process = self.start_patch(gsm.win32api, "OpenProcess", return_value=self.handle)
        self.image_name = self.start_patch(gsm.win32process, "GetModuleFileNameEx", return_value=str(self.exe))
        self.show = self.start_patch(gsm.win32gui, "ShowWindow")
        self.foreground = self.start_patch(gsm.win32gui, "SetForegroundWindow")
        self.flash = self.start_patch(gsm.win32gui, "FlashWindow")

    def start_patch(self, obj, name, **kwargs):
        patcher = mock.patch.object(obj, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def test_unrelated_window_is_not_activated(self):
        self.image_name.return_value = "C:/Other/Game Save Manager.exe"
        self.assertFalse(gsm.activate_existing_window(self.exe))
        self.show.assert_not_called()
        self.handle.Close.assert_called_once()

    def test_matching_hidden_window_is_restored_when_focus_is_restricted(self):
        self.foreground.side_effect = gsm.win32gui.error(5, "SetForegroundWindow", "denied")
        self.assertTrue(gsm.activate_existing_window(self.exe))
        self.show.assert_called_once_with(123, gsm.win32con.SW_RESTORE)
        self.flash.assert_called_once()
        self.handle.Close.assert_called_once()

    def test_disappearing_process_is_ignored(self):
        self.open_process.side_effect = gsm.win32gui.error(87, "OpenProcess", "gone")
        self.assertFalse(gsm.activate_existing_window(self.exe))
        self.show.assert_not_called()

    def test_non_portable_running_profile_is_rejected(self):
        with mock.patch.object(gsm.psutil, "Process") as process:
            process.return_value.cmdline.return_value = [str(self.exe)]
            with self.assertRaises(ValueError):
                gsm.activate_existing_window(self.exe, Path("C:/GSM/data/GameSaveManager"))
        self.show.assert_not_called()

    def test_matching_portable_profile_can_be_reused(self):
        profile = Path("C:/GSM/data/GameSaveManager")
        with mock.patch.object(gsm.psutil, "Process") as process:
            process.return_value.cmdline.return_value = [str(self.exe), f"--user-data-dir={profile}"]
            self.assertTrue(gsm.activate_existing_window(self.exe, profile))
        self.show.assert_called_once()


class GameSaveManagerTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()
        del cls.root
        gc.collect()

    def setUp(self):
        self.frame = tk.Frame(self.root)
        self.addCleanup(self._cleanup_widgets)
        self.config = {"backup": {"sources": ["C:/old-saves"],
                                  "backup_dir": "C:/old-backups", "monitor_enabled": True}}
        self.before = copy.deepcopy(self.config)
        self.save = mock.Mock()
        self.tab = gsm.GameSaveManagerTab(self.frame, self.config, self.save)
        self.root.update_idletasks()

    def _cleanup_widgets(self):
        tab = self.__dict__.pop("tab", None)
        frame = self.__dict__.pop("frame", None)
        if tab is not None:
            tab.stop()
        if frame is not None:
            frame.destroy()
        # Mock side-effect tracebacks can retain pages and their Tk variables.
        # Release them and collect cycles on the UI thread, before later workers.
        self.__dict__.pop("save", None)
        del tab, frame
        gc.collect()

    def test_opening_and_closing_tab_preserves_legacy_config(self):
        self.tab.stop()
        self.assertEqual(self.config, self.before)
        self.save.assert_not_called()

    def test_cancelling_program_picker_preserves_config(self):
        with mock.patch.object(gsm.filedialog, "askopenfilename", return_value=""):
            self.tab.choose_executable()
        self.assertEqual(self.config, self.before)
        self.save.assert_not_called()

    def make_distribution(self, folder):
        exe = Path(folder) / "Game Save Manager.exe"
        exe.write_bytes(b"test executable")
        for name in ("resources/app.asar", "resources.pak", "icudtl.dat", "database/database.db"):
            path = exe.parent / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        return exe

    def test_malformed_settings_do_not_prevent_page_opening(self):
        for value in (None, False, 8, [], "bad", {"executable": 7}):
            with self.subTest(value=value):
                frame = tk.Frame(self.root)
                try:
                    config = {"game_save_manager": value}
                    gsm.GameSaveManagerTab(frame, config, self.save)
                    self.assertEqual(config, {"game_save_manager": value})
                finally:
                    frame.destroy()

    def test_bad_legacy_settings_show_error_without_writes(self):
        for value in (None, [], 4, "broken", {"backup_dir": 7}):
            self.config["backup"] = value
            before = copy.deepcopy(self.config)
            with self.subTest(value=value), mock.patch.object(gsm.messagebox, "showerror") as error:
                self.tab.open_legacy_backups()
                error.assert_called_once()
                self.assertEqual(self.config, before)

    def test_picker_can_repair_malformed_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = self.make_distribution(folder)
            self.config["game_save_manager"] = "broken"
            with mock.patch.object(gsm.filedialog, "askopenfilename", return_value=str(exe)):
                self.tab.choose_executable()
            self.assertEqual(gsm.find_executable(self.config), exe)

    def test_failed_save_preserves_explicit_null_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = self.make_distribution(folder)
            self.config["game_save_manager"] = None
            before = copy.deepcopy(self.config)
            self.save.side_effect = OSError("disk full")
            with mock.patch.object(gsm.filedialog, "askopenfilename", return_value=str(exe)), mock.patch.object(gsm.messagebox, "showerror"):
                self.tab.choose_executable()
            self.assertEqual(self.config, before)

    def test_selected_internal_executable_survives_moving_toolbox(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "original"
            (root / "game_save_manager").mkdir(parents=True)
            exe = self.make_distribution(root / "game_save_manager")
            with mock.patch.object(gsm, "application_directory", return_value=root), mock.patch.object(gsm.filedialog, "askopenfilename", return_value=str(exe)):
                self.tab.choose_executable()
            self.assertFalse(Path(self.config["game_save_manager"]["executable"]).is_absolute())
            moved = Path(folder) / "moved"
            root.rename(moved)
            with mock.patch.object(gsm, "application_directory", return_value=moved):
                self.assertEqual(gsm.find_executable(self.config), moved / "game_save_manager/Game Save Manager.exe")

    def test_rapid_launch_clicks_are_throttled(self):
        with mock.patch.object(gsm, "launch", return_value=Path("Game Save Manager.exe")) as launch:
            for _ in range(100):
                self.tab.open_manager()
            self.assertEqual(launch.call_count, 1)
            self.tab.stop()

    def test_stopped_page_does_not_launch_a_process(self):
        self.tab.stop()
        with mock.patch.object(gsm, "launch") as launch:
            self.tab.open_manager()
            launch.assert_not_called()

    def test_malformed_startup_settings_do_not_crash_toolbox_or_overwrite_config(self):
        for value in (None, [], 4, "broken"):
            config = {"startup": value}
            frame = tk.Frame(self.root)
            try:
                with self.subTest(value=value), mock.patch.object(toolbox, "is_autostart_enabled", return_value=False):
                    toolbox.StartupTab(frame, config, self.save, lambda _message: None)
                    self.assertEqual(config, {"startup": value})
            finally:
                frame.destroy()

    def test_startup_state_refresh_is_read_only_when_config_is_damaged(self):
        self.save.side_effect = ValueError("damaged config must be preserved")
        with mock.patch.object(toolbox, "is_autostart_enabled", return_value=False):
            page = toolbox.StartupTab(self.frame, {}, self.save, lambda _message: None)
            page.refresh_state()
        self.save.assert_not_called()

    def test_explicit_startup_change_reports_save_failure_without_callback_crash(self):
        with mock.patch.object(toolbox, "is_autostart_enabled", return_value=False):
            page = toolbox.StartupTab(self.frame, {}, self.save, lambda _message: None)
        self.save.side_effect = ValueError("damaged config must be preserved")
        page.autostart_var.set(True)
        with mock.patch.object(toolbox, "set_autostart"), mock.patch.object(toolbox, "is_autostart_enabled", return_value=True), mock.patch.object(toolbox.messagebox, "showerror") as error:
            page.toggle_autostart()
        self.assertIn("damaged config", error.call_args.args[1])
        self.assertTrue(page.autostart_var.get())

    def test_saving_external_program_preserves_legacy_backup_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = self.make_distribution(folder)
            with mock.patch.object(gsm.filedialog, "askopenfilename", return_value=str(exe)):
                self.tab.choose_executable()
            self.assertEqual(self.config["backup"], self.before["backup"])
            self.assertEqual(gsm.find_executable(self.config), exe)
            self.save.assert_called_once()

    def test_save_failure_rolls_back_program_choice(self):
        self.save.side_effect = OSError("disk full")
        with tempfile.TemporaryDirectory() as folder:
            exe = self.make_distribution(folder)
            with mock.patch.object(gsm.filedialog, "askopenfilename", return_value=str(exe)), mock.patch.object(gsm.messagebox, "showerror"):
                self.tab.choose_executable()
        self.assertEqual(self.config, self.before)

    def test_launch_failure_is_reported_in_ui(self):
        with mock.patch.object(gsm, "launch", side_effect=OSError("access denied")), mock.patch.object(gsm.messagebox, "showerror") as error:
            self.tab.open_manager()
        self.assertIn("失败", self.tab.status.get())
        self.assertIn("access denied", error.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
