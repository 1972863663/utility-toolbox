"""Locations shared by the source application and its packaged helpers."""

import sys
from pathlib import Path


def application_root() -> Path:
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent
    executable = Path(sys.executable).resolve()
    base = executable.parent
    if base.name.casefold() == "macro_tools" and executable.stem in {"interception_loop_macro", "macro_recorder"}:
        return base.parent
    if base.name.casefold() == "dist" and base.parent.name.casefold() == "mic_tool_workspace":
        return base.parent.parent
    return base


def data_directory(name: str) -> Path:
    return application_root() / "data" / name


def macro_recording_path() -> Path:
    return data_directory("Macros") / "macro_recording.json"
