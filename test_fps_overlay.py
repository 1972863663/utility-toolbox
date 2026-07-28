from pathlib import Path

import pytest

import utility_toolbox as toolbox


def make_runtime(root: Path, exe_data: bytes = b"overlay", present_mon_data: bytes = b"presentmon") -> Path:
    (root / "tools").mkdir(parents=True)
    (root / toolbox.FPS_OVERLAY_PROCESS_NAME).write_bytes(exe_data)
    (root / "tools" / "PresentMon-2.5.1-x64.exe").write_bytes(present_mon_data)
    return root


def test_files_have_same_content_uses_content_not_just_size(tmp_path: Path) -> None:
    left = tmp_path / "left.bin"
    right = tmp_path / "right.bin"
    left.write_bytes(b"abcd")
    right.write_bytes(b"abce")

    assert not toolbox.files_have_same_content(left, right)
    right.write_bytes(b"abcd")
    assert toolbox.files_have_same_content(left, right)


def test_install_fps_overlay_runtime_copies_required_files(tmp_path: Path) -> None:
    source = make_runtime(tmp_path / "source")
    destination = tmp_path / "installed"

    executable = toolbox.install_fps_overlay_runtime(source, destination)

    assert executable == destination / toolbox.FPS_OVERLAY_PROCESS_NAME
    assert executable.read_bytes() == b"overlay"
    assert (destination / "tools" / "PresentMon-2.5.1-x64.exe").read_bytes() == b"presentmon"


def test_install_fps_overlay_runtime_updates_changed_component(tmp_path: Path) -> None:
    source = make_runtime(tmp_path / "source", exe_data=b"new")
    destination = make_runtime(tmp_path / "installed", exe_data=b"old")

    toolbox.install_fps_overlay_runtime(source, destination)

    assert (destination / toolbox.FPS_OVERLAY_PROCESS_NAME).read_bytes() == b"new"


def test_install_fps_overlay_runtime_rejects_incomplete_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / toolbox.FPS_OVERLAY_PROCESS_NAME).write_bytes(b"overlay")

    with pytest.raises(FileNotFoundError, match="PresentMon"):
        toolbox.install_fps_overlay_runtime(source, tmp_path / "installed")


def test_running_fps_overlay_processes_filters_by_executable_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        toolbox,
        "running_process_paths",
        lambda: [
            (10, r"C:\Apps\TinyFpsOverlay.exe"),
            (11, r"C:\Apps\Other.exe"),
        ],
    )
    monkeypatch.setattr(toolbox.os, "getpid", lambda: 99)

    assert toolbox.running_fps_overlay_processes() == [
        (10, "TinyFpsOverlay.exe", r"C:\Apps\TinyFpsOverlay.exe")
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[", (0, 0xDB)),
        ("Ctrl+Alt+F10", (0x0002 | 0x0001, 0x79)),
        ("Shift+Z", (0x0004, ord("Z"))),
    ],
)
def test_parse_fps_hotkey(text: str, expected: tuple[int, int]) -> None:
    assert toolbox.parse_fps_hotkey(text) == expected
    assert toolbox.parse_fps_hotkey(toolbox.format_fps_hotkey(*expected)) == expected


def test_parse_fps_hotkey_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="不支持"):
        toolbox.parse_fps_hotkey("Ctrl+NoSuchKey")


def test_fps_color_argb_round_trip() -> None:
    assert toolbox.fps_argb_to_hex(toolbox.fps_hex_to_argb("#12ABEF")) == "#12ABEF"


def test_save_and_load_fps_overlay_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(toolbox, "FPS_OVERLAY_CONFIG_DIR", config_dir)
    monkeypatch.setattr(toolbox, "FPS_OVERLAY_CONFIG_FILE", config_dir / "config.json")
    config = toolbox.load_fps_overlay_config()
    config["TextScale"] = 145

    toolbox.save_fps_overlay_config(config)

    assert toolbox.load_fps_overlay_config()["TextScale"] == 145
