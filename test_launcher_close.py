import unittest

import utility_toolbox


class LauncherCloseTests(unittest.TestCase):
    def test_normalizes_existing_paths_case_insensitively(self):
        path = utility_toolbox.normalize_process_path(__file__.upper())

        self.assertEqual(path, str(utility_toolbox.Path(__file__).resolve()).lower())

    def test_matches_exact_process_paths_only(self):
        app_path = r"C:\Tools\ExampleApp\ExampleApp.exe"
        processes = [
            (101, app_path),
            (202, app_path + ".bak"),
            (303, ""),
        ]

        matches = utility_toolbox.match_processes_for_paths([app_path], processes)

        self.assertEqual(matches, [(101, app_path)])

    def test_matches_processes_running_from_launcher_directory(self):
        launcher = r"C:\OOPZ\oopz\oopz-runner.exe"
        processes = [
            (101, r"C:\OOPZ\oopz\oopz.exe"),
            (202, r"C:\OOPZ\oopz\resources\helper.exe"),
            (303, r"C:\Other\oopz.exe"),
        ]

        matches = utility_toolbox.match_processes_for_paths([launcher], processes)

        self.assertEqual(matches, [(101, processes[0][1]), (202, processes[1][1])])

    def test_does_not_match_unrelated_processes_next_to_regular_exe(self):
        target = r"C:\Users\User\Desktop\左Shift锁定器.exe"
        processes = [
            (101, r"C:\Users\User\Desktop\左Shift锁定器.exe"),
            (202, r"C:\Users\User\Desktop\工具箱.exe"),
        ]

        matches = utility_toolbox.match_processes_for_paths([target], processes)

        self.assertEqual(matches, [(101, processes[0][1])])

    def test_treats_taskkill_not_found_as_already_closed(self):
        self.assertTrue(utility_toolbox.is_taskkill_already_closed('错误: 没有找到进程 "1234"。'))
        self.assertTrue(utility_toolbox.is_taskkill_already_closed('ERROR: The process "1234" not found.'))
        self.assertFalse(utility_toolbox.is_taskkill_already_closed("Access is denied."))

    def test_discovers_new_process_paths_after_launch(self):
        before = [
            (101, r"C:\Windows\explorer.exe"),
            (102, r"C:\Tools\launcher.exe"),
        ]
        after = before + [
            (201, r"D:\Games\ActualGame\game.exe"),
            (202, r"D:\Games\ActualGame\helper.exe"),
        ]

        discovered = utility_toolbox.discover_new_process_paths(before, after)

        self.assertEqual(discovered, [r"D:\Games\ActualGame\game.exe", r"D:\Games\ActualGame\helper.exe"])

    def test_drop_folder_matches_processes_inside_folder_tree(self):
        folder = r"C:\Tools\Suite"
        processes = [
            (101, r"C:\Tools\Suite\App.exe"),
            (202, r"C:\Tools\Suite\bin\Helper.exe"),
            (303, r"C:\Tools\SuiteBackup\App.exe"),
            (404, r"C:\Windows\explorer.exe"),
        ]

        matches = utility_toolbox.match_processes_for_drop_paths([folder], processes)

        self.assertEqual(matches, [(101, processes[0][1]), (202, processes[1][1])])

    def test_drop_exe_reuses_exact_process_matching(self):
        target = r"C:\Tools\Editor\editor.exe"
        processes = [
            (101, target),
            (202, r"C:\Tools\Editor\helper.exe"),
        ]

        matches = utility_toolbox.match_processes_for_drop_paths([target], processes)

        self.assertEqual(matches, [(101, target)])

    def test_drop_folder_never_matches_current_process_pid(self):
        folder = r"C:\Users\User\Desktop\系统网络工具"
        own_path = r"C:\Users\User\Desktop\系统网络工具\工具箱.exe"
        processes = [
            (999, own_path),
            (101, r"C:\Users\User\Desktop\系统网络工具\helper.exe"),
        ]

        original_getpid = utility_toolbox.os.getpid
        utility_toolbox.os.getpid = lambda: 999
        try:
            matches = utility_toolbox.match_processes_for_drop_paths([folder], processes)
        finally:
            utility_toolbox.os.getpid = original_getpid

        self.assertEqual(matches, [(101, processes[1][1])])

    def test_drop_folder_never_matches_current_process_parent_pid(self):
        folder = r"C:\Users\User\Desktop\系统网络工具"
        own_path = r"C:\Users\User\Desktop\系统网络工具\工具箱.exe"
        processes = [
            (998, own_path),
            (101, r"C:\Users\User\Desktop\系统网络工具\helper.exe"),
        ]

        original_protected = utility_toolbox.current_process_protected_pids
        utility_toolbox.current_process_protected_pids = lambda: {999, 998}
        try:
            matches = utility_toolbox.match_processes_for_drop_paths([folder], processes)
        finally:
            utility_toolbox.current_process_protected_pids = original_protected

        self.assertEqual(matches, [(101, processes[1][1])])

    def test_drop_folder_never_matches_current_frozen_executable_path(self):
        folder = r"C:\Users\User\Desktop\系统网络工具"
        own_path = r"C:\Users\User\Desktop\系统网络工具\工具箱.exe"
        processes = [
            (101, own_path),
            (202, r"C:\Users\User\Desktop\系统网络工具\helper.exe"),
        ]

        original_frozen = getattr(utility_toolbox.sys, "frozen", None)
        original_executable = utility_toolbox.sys.executable
        utility_toolbox.sys.frozen = True
        utility_toolbox.sys.executable = own_path
        try:
            matches = utility_toolbox.match_processes_for_drop_paths([folder], processes)
        finally:
            if original_frozen is None:
                delattr(utility_toolbox.sys, "frozen")
            else:
                utility_toolbox.sys.frozen = original_frozen
            utility_toolbox.sys.executable = original_executable

        self.assertEqual(matches, [(202, processes[1][1])])

    def test_drop_folder_never_matches_current_executable_basename(self):
        folder = r"C:\Users\User\Downloads\超级变色龙"
        own_name_elsewhere = r"C:\Users\User\Downloads\超级变色龙\工具箱.exe"
        processes = [
            (101, own_name_elsewhere),
            (202, r"C:\Users\User\Downloads\超级变色龙\game.exe"),
        ]

        original_executable = utility_toolbox.sys.executable
        utility_toolbox.sys.executable = r"C:\Users\User\Desktop\系统网络工具\工具箱.exe"
        try:
            matches = utility_toolbox.match_processes_for_drop_paths([folder], processes)
        finally:
            utility_toolbox.sys.executable = original_executable

        self.assertEqual(matches, [(202, processes[1][1])])


if __name__ == "__main__":
    unittest.main()
