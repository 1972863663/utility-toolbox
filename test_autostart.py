import unittest
from unittest import mock

import utility_toolbox


class AutostartTests(unittest.TestCase):
    def test_moved_executable_makes_saved_command_stale(self):
        saved = r'"C:\Users\User\Desktop\工具箱.exe" --minimized-to-tray'
        current = r'"C:\Users\User\Desktop\系统网络工具\工具箱.exe" --minimized-to-tray'

        self.assertFalse(utility_toolbox.autostart_command_matches(saved, current))

    def test_command_comparison_is_case_insensitive_on_windows(self):
        saved = r'"C:\USERS\USER\DESKTOP\系统网络工具\工具箱.exe" --minimized-to-tray'
        current = r'"C:\Users\User\Desktop\系统网络工具\工具箱.exe" --minimized-to-tray'

        self.assertTrue(utility_toolbox.autostart_command_matches(saved, current))

    @mock.patch("utility_toolbox.set_autostart")
    @mock.patch("utility_toolbox.read_autostart_command")
    def test_repairs_stale_existing_autostart_command(self, read_command, set_autostart):
        read_command.return_value = r'"C:\Users\User\Desktop\工具箱.exe" --minimized-to-tray'

        repaired = utility_toolbox.repair_autostart_path()

        self.assertTrue(repaired)
        set_autostart.assert_called_once_with(True)

    @mock.patch("utility_toolbox.set_autostart")
    @mock.patch("utility_toolbox.read_autostart_command")
    def test_does_not_enable_autostart_when_no_value_exists(self, read_command, set_autostart):
        read_command.return_value = None

        repaired = utility_toolbox.repair_autostart_path()

        self.assertFalse(repaired)
        set_autostart.assert_not_called()


if __name__ == "__main__":
    unittest.main()
