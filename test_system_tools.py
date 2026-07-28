from pathlib import Path

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
