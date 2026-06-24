from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from .models import ScanManifest
from .reports import generate_duplicate_report, generate_locations_report
from .utils import unique_path, write_json
from .video import compress_video

ProgressCallback = Callable[[str], None]


def save_manifest(manifest: ScanManifest, output_dir: Path) -> Path:
    path = output_dir / "scan_manifest.json"
    write_json(path, manifest.to_dict())
    return path


def execute_plan(manifest: ScanManifest, progress: ProgressCallback | None = None) -> dict:
    output = Path(manifest.config.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    log = {"copied": [], "compressed": [], "skipped": [], "failed": []}

    for index, item in enumerate(manifest.items, start=1):
        source = Path(item.source_path)
        target = unique_path(Path(item.target_path or ""))
        try:
            _emit(progress, f"[{index}/{len(manifest.items)}] Copying {source.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            item.target_path = str(target)
            item.status = "copied"
            log["copied"].append({"source": str(source), "target": str(target)})

            if item.media_type == "video":
                compressed = target.parent / "Compressed" / f"{target.stem}.mp4"
                _emit(progress, f"Compressing {source.name}")
                ok, message = compress_video(target, compressed)
                record = {"source": str(target), "target": str(compressed), "message": message}
                if ok:
                    log["compressed"].append(record)
                else:
                    log["skipped"].append(record)
        except Exception as exc:
            item.status = "failed"
            item.errors.append(str(exc))
            log["failed"].append({"source": str(source), "message": str(exc)})

    _emit(progress, "Writing reports")
    save_manifest(manifest, output)
    log["duplicates_report"] = str(generate_duplicate_report(manifest, output))
    locations_csv, locations_html = generate_locations_report(manifest, output)
    log["locations_report_csv"] = str(locations_csv)
    log["locations_report_html"] = str(locations_html)
    write_json(output / "run_log.json", log)
    _emit(progress, "Done")
    return log


def _emit(progress: ProgressCallback | None, message: str) -> None:
    if progress:
        progress(message)
