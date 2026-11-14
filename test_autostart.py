import unittest
from unittest import mock
import pytest

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


@pytest.mark.parametrize("interpreter_name", ["python.exe", "pythonw.exe"])
@pytest.mark.parametrize("old_script_exists", [False, True])
def test_source_autostart_ownership_follows_the_script(tmp_path, interpreter_name, old_script_exists):
    interpreter = tmp_path / interpreter_name
    interpreter.write_bytes(b"inert interpreter fixture")
    old_script = tmp_path / "old" / "utility_toolbox.py"
    new_script = tmp_path / "new" / "utility_toolbox.py"
    new_script.parent.mkdir()
    new_script.write_text("# inert new source", encoding="utf-8")
    if old_script_exists:
        old_script.parent.mkdir()
        old_script.write_text("# other valid copy", encoding="utf-8")
    saved = f'"{interpreter}" "{old_script}" --minimized-to-tray'
    current = f'"{interpreter}" "{new_script}" --minimized-to-tray'
    with mock.patch.object(utility_toolbox, "read_autostart_command", return_value=saved), mock.patch.object(utility_toolbox, "autostart_command", return_value=current), mock.patch.object(utility_toolbox, "set_autostart") as write:
        assert utility_toolbox.repair_autostart_path() is (not old_script_exists)
    if old_script_exists:
        write.assert_not_called()
    else:
        write.assert_called_once_with(True)


@pytest.mark.parametrize("tail", ['-m utility_toolbox --minimized-to-tray', '-c "print(1)"', 'utility_toolbox.py --minimized-to-tray', '"C:\\Missing\\other.py" --minimized-to-tray', '"C:\\Missing\\utility_toolbox.py" --different-flag', '"C:\\Missing\\utility_toolbox.py" --minimized-to-tray --extra'])
def test_unrecognized_python_autostart_arguments_are_not_rewritten(tmp_path, tail):
    missing_interpreter = tmp_path / "python.exe"
    saved = f'"{missing_interpreter}" {tail}'
    with mock.patch.object(utility_toolbox, "read_autostart_command", return_value=saved), mock.patch.object(utility_toolbox, "autostart_command", return_value='"C:\\Current\\工具箱.exe" --minimized-to-tray'), mock.patch.object(utility_toolbox, "set_autostart") as write:
        assert not utility_toolbox.repair_autostart_path()
    write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
