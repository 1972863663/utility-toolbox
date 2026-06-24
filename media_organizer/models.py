from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts", ".3gp"}


@dataclass(slots=True)
class AppConfig:
    source_dir: str
    output_dir: str
    copy_mode: str = "preview_then_execute"
    folder_schema: str = "year_month_type"
    geocoding: str = "disabled"
    video_compression: str = "keep_original_and_copy"


@dataclass(slots=True)
class MediaItem:
    source_path: str
    media_type: str
    extension: str
    size_bytes: int
    date: str = "Unknown-Date"
    location: str = "No-Location"
    device: str = "Unknown-Device"
    gps_lat: float | None = None
    gps_lon: float | None = None
    sha256: str | None = None
    perceptual_hash: str | None = None
    target_path: str | None = None
    status: str = "planned"
    errors: list[str] = field(default_factory=list)

    @property
    def filename(self) -> str:
        return Path(self.source_path).name

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DuplicateGroup:
    kind: str
    key: str
    files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ScanManifest:
    config: AppConfig
    items: list[MediaItem]
    exact_duplicates: list[DuplicateGroup]
    similar_duplicates: list[DuplicateGroup]

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": asdict(self.config),
            "items": [item.to_dict() for item in self.items],
            "exact_duplicates": [group.to_dict() for group in self.exact_duplicates],
            "similar_duplicates": [group.to_dict() for group in self.similar_duplicates],
        }
