from pathlib import Path
import importlib.util
import json

import utility_toolbox as toolbox


def load_mic_toggle_module():
    source_path = Path(__file__).parent / "mic_toggle" / "一键麦克风开关.py"
    spec = importlib.util.spec_from_file_location("mic_toggle_helper", source_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_powershell7_executable_prefers_standard_install(
    tmp_path: Path,
    monkeypatch,
) -> None:
    program_files = tmp_path / "Program Files"
    executable = program_files / "PowerShell" / "7" / "pwsh.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"pwsh")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    assert toolbox.powershell7_executable() == str(executable)


def test_mic_overlay_scale_is_clamped() -> None:
    assert toolbox.normalize_mic_overlay_scale("0.4") == toolbox.MIC_OVERLAY_MIN_SCALE
    assert toolbox.normalize_mic_overlay_scale("2") == toolbox.MIC_OVERLAY_MAX_SCALE
    assert toolbox.normalize_mic_overlay_scale("0.85") == 0.85
    assert toolbox.normalize_mic_overlay_scale("invalid") == toolbox.MIC_OVERLAY_DEFAULT_SCALE


def test_save_mic_overlay_scale_preserves_hotkey(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "MicToggleHotkey" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"hotkey": "MouseX2", "hotkey_codes": [6]}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(toolbox, "MIC_TOGGLE_CONFIG_PATH", config_path)

    saved_scale = toolbox.save_mic_overlay_scale(0.8)
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert saved_scale == 0.8
    assert saved == {"hotkey": "MouseX2", "hotkey_codes": [6], "overlay_scale": 0.8}
    assert toolbox.read_mic_overlay_scale() == 0.8


def test_parse_and_format_mic_hotkey() -> None:
    assert toolbox.parse_mic_hotkey("Ctrl + Alt + M") == [0x11, 0x12, 0x4D]
    assert toolbox.parse_mic_hotkey("MouseX2") == [0x06]
    assert toolbox.format_mic_hotkey([0x12, 0x11, 0x4D]) == "Ctrl+Alt+M"


def test_parse_mic_hotkey_rejects_unknown_key() -> None:
    try:
        toolbox.parse_mic_hotkey("Ctrl+NotAKey")
    except ValueError as exc:
        assert "NotAKey" in str(exc)
    else:
        raise AssertionError("unknown key should be rejected")


def test_save_mic_hotkey_settings_preserves_overlay_scale(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "MicToggleHotkey" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"overlay_scale": 0.75}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(toolbox, "MIC_TOGGLE_CONFIG_PATH", config_path)

    enabled, hotkey, codes = toolbox.save_mic_hotkey_settings(False, "MouseX1")
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert (enabled, hotkey, codes) == (False, "MouseX1", [0x05])
    assert saved == {
        "overlay_scale": 0.75,
        "hotkey_enabled": False,
        "hotkey": "MouseX1",
        "hotkey_codes": [0x05],
    }
    assert toolbox.read_mic_hotkey_settings() == (False, "MouseX1", [0x05])


def test_mic_helper_loads_disabled_hotkey(tmp_path: Path, monkeypatch) -> None:
    helper = load_mic_toggle_module()
    config_path = tmp_path / "MicToggleHotkey" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "hotkey_enabled": False,
                "hotkey": "MouseX1",
                "hotkey_codes": [0x05],
                "overlay_scale": 0.75,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(helper, "CONFIG_DIR", config_path.parent)
    monkeypatch.setattr(helper, "CONFIG_PATH", config_path)

    loaded = helper.load_config()

    assert loaded["hotkey_enabled"] is False
    assert loaded["hotkey"] == "MouseX1"
    assert loaded["hotkey_codes"] == [0x05]


def test_mic_helper_disabled_hotkey_does_not_toggle(monkeypatch) -> None:
    helper = load_mic_toggle_module()
    app = object.__new__(helper.MicToggleApp)
    app.config = {"hotkey_enabled": False, "hotkey_codes": [0x05]}
    app.hotkey_was_down = False
    app.recording = False
    scheduled = []
    toggles = []
    app.root = type("FakeRoot", (), {"after": lambda _self, delay, callback: scheduled.append((delay, callback))})()
    app.on_toggle = lambda: toggles.append(True)
    monkeypatch.setattr(helper, "key_down", lambda _code: True)

    app.poll_hotkey()

    assert toggles == []
    assert app.hotkey_was_down is False
    assert scheduled and scheduled[0][0] == 35
