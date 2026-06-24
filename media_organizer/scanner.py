from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .metadata import extract_photo_metadata, extract_video_metadata, register_heif
from .models import PHOTO_EXTENSIONS, VIDEO_EXTENSIONS, AppConfig, DuplicateGroup, MediaItem, ScanManifest
from .utils import safe_segment, sha256_file


def scan_media(config: AppConfig) -> ScanManifest:
    source = Path(config.source_dir)
    output = Path(config.output_dir)
    items: list[MediaItem] = []

    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext not in PHOTO_EXTENSIONS and ext not in VIDEO_EXTENSIONS:
            continue

        media_type = "photo" if ext in PHOTO_EXTENSIONS else "video"
        item = MediaItem(
            source_path=str(path),
            media_type=media_type,
            extension=ext,
            size_bytes=path.stat().st_size,
            sha256=sha256_file(path),
        )

        if media_type == "photo":
            date, device, lat, lon, error = extract_photo_metadata(path)
            item.date = safe_segment(date, "Unknown-Date")
            item.device = safe_segment(device, "Unknown-Device")
            item.gps_lat = lat
            item.gps_lon = lon
            item.location = "No-Location"
            item.perceptual_hash = perceptual_hash(path)
            if error:
                item.errors.append(error)
        else:
            date, device, lat, lon, error = extract_video_metadata(path)
            item.date = safe_segment(date, "Unknown-Date")
            item.device = safe_segment(device, "Unknown-Device")
            item.gps_lat = lat
            item.gps_lon = lon
            item.location = "No-Location"
            if error:
                item.errors.append(error)

        item.target_path = str(target_path_for(output, item))
        items.append(item)

    exact = _exact_duplicates(items)
    similar = _similar_duplicates(items)
    return ScanManifest(config=config, items=items, exact_duplicates=exact, similar_duplicates=similar)


def target_path_for(output_dir: Path, item: MediaItem) -> Path:
    kind_dir = "Photos" if item.media_type == "photo" else "Videos"
    year_dir, month_dir = date_parts(item.date)
    return (
        output_dir
        / year_dir
        / month_dir
        / kind_dir
        / Path(item.source_path).name
    )


def date_parts(date: str) -> tuple[str, str]:
    if len(date) >= 7 and date[4] == "-" and date[:4].isdigit() and date[5:7].isdigit():
        year = date[:4]
        month = date[:7]
        return safe_segment(year, "Unknown-Year"), safe_segment(month, "Unknown-Month")
    return "Unknown-Year", "Unknown-Month"


def perceptual_hash(path: Path) -> str | None:
    try:
        register_heif()
        from PIL import Image
        import imagehash

        with Image.open(path) as image:
            return str(imagehash.phash(image))
    except Exception:
        return None


def _exact_duplicates(items: list[MediaItem]) -> list[DuplicateGroup]:
    by_hash: dict[str, list[str]] = defaultdict(list)
    for item in items:
        if item.sha256:
            by_hash[item.sha256].append(item.source_path)
    return [DuplicateGroup("exact", key, files) for key, files in by_hash.items() if len(files) > 1]


def _similar_duplicates(items: list[MediaItem], max_distance: int = 6) -> list[DuplicateGroup]:
    photos = [item for item in items if item.perceptual_hash]
    used: set[str] = set()
    groups: list[DuplicateGroup] = []
    for item in photos:
        if item.source_path in used:
            continue
        group = [item.source_path]
        for other in photos:
            if other.source_path == item.source_path or other.source_path in used:
                continue
            if _hex_hamming(item.perceptual_hash or "", other.perceptual_hash or "") <= max_distance:
                group.append(other.source_path)
        if len(group) > 1:
            used.update(group)
            groups.append(DuplicateGroup("similar", item.perceptual_hash or "", group))
    return groups


def _hex_hamming(left: str, right: str) -> int:
    if len(left) != len(right):
        return 999
    return sum(bin(int(a, 16) ^ int(b, 16)).count("1") for a, b in zip(left, right))
