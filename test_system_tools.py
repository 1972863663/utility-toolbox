from pathlib import Path
import json

import utility_toolbox as toolbox


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
