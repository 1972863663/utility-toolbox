import json
import sys
from pathlib import Path
from unittest import mock

import pytest

import toolbox_paths as paths
import game_save_manager_launcher as gsm


@pytest.mark.parametrize("relative", ["工具箱.exe", "mic_tool_workspace/dist/一键麦克风开关.exe", "macro_tools/interception_loop_macro.exe", "macro_tools/macro_recorder.exe"])
def test_all_packaged_tools_share_portable_root(tmp_path, relative):
    root = tmp_path / "移动后的工具箱"
    with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", str(root / relative)), mock.patch.object(sys, "_MEIPASS", "C:/unrelated-temp", create=True):
        assert paths.application_root() == root
        assert paths.data_directory("MicToggleHotkey") == root / "data/MicToggleHotkey"


def test_source_root_does_not_depend_on_interpreter_location(tmp_path):
    with mock.patch.object(paths, "__file__", str(tmp_path / "toolbox_paths.py")), mock.patch.object(sys, "frozen", False, create=True):
        assert paths.application_root() == tmp_path


def test_gsm_paths_rebase_when_whole_folder_moves(tmp_path):
    original = tmp_path / "original"
    profile = gsm.prepare_profile(original)
    settings_path = profile / "GSM Settings/settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["language"] = "zh_CN"
    settings["pinnedGames"] = [123]
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    moved = tmp_path / "moved"
    original.rename(moved)
    profile = gsm.prepare_profile(moved)
    settings = json.loads((profile / "GSM Settings/settings.json").read_text(encoding="utf-8"))
    assert settings["backupPath"] == str(moved / "data/GSM Backups")
    assert settings["exportPath"] == str(moved / "data/GSM Exports")
    assert settings["language"] == "zh_CN"
    assert settings["pinnedGames"] == [123]


def test_gsm_custom_external_backup_path_is_preserved(tmp_path):
    profile = gsm.prepare_profile(tmp_path)
    settings_path = profile / "GSM Settings/settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["backupPath"] = "D:/custom-game-backups"
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    gsm.prepare_profile(tmp_path)
    assert json.loads(settings_path.read_text(encoding="utf-8"))["backupPath"] == "D:/custom-game-backups"


def test_corrupt_gsm_settings_are_not_replaced(tmp_path):
    profile = gsm.prepare_profile(tmp_path)
    settings = profile / "GSM Settings/settings.json"
    settings.write_bytes(b"{damaged")
    with pytest.raises(ValueError):
        gsm.prepare_profile(tmp_path)
    assert settings.read_bytes() == b"{damaged"
