from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def compress_video(source: Path, destination: Path) -> tuple[bool, str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg not found in PATH"

    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "24",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(destination),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=None, check=False)
        if completed.returncode == 0:
            return True, "compressed"
        return False, completed.stderr[-1200:] or "ffmpeg failed"
    except Exception as exc:
        return False, str(exc)
