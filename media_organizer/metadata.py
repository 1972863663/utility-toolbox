from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


def register_heif() -> None:
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except Exception:
        pass


def extract_photo_metadata(path: Path) -> tuple[str, str, float | None, float | None, str | None]:
    try:
        register_heif()
        from PIL import Image, ExifTags
    except Exception as exc:
        return _date_from_filename(path) or "Unknown-Date", "Unknown-Device", None, None, f"Image metadata unavailable: {exc}"

    try:
        with Image.open(path) as image:
            exif = image.getexif()
            if not exif:
                return _date_from_filename(path) or "Unknown-Date", "Unknown-Device", None, None, None
            tags = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
            date_raw = tags.get("DateTimeOriginal") or tags.get("DateTime") or tags.get("DateTimeDigitized")
            date = _parse_media_date(date_raw) or _date_from_filename(path) or "Unknown-Date"
            make = str(tags.get("Make") or "").strip()
            model = str(tags.get("Model") or "").strip()
            device = " ".join(part for part in [make, model] if part) or "Unknown-Device"
            lat, lon = _gps_from_exif(exif, ExifTags)
            return date, device, lat, lon, None
    except Exception as exc:
        return _date_from_filename(path) or "Unknown-Date", "Unknown-Device", None, None, f"EXIF read failed: {exc}"


def extract_video_metadata(path: Path) -> tuple[str, str, float | None, float | None, str | None]:
    fallback_lat, fallback_lon = _gps_from_video_binary(path)
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return (
            _date_from_filename(path) or "Unknown-Date",
            "Unknown-Device",
            fallback_lat,
            fallback_lon,
            "ffprobe not found; video capture time/location unavailable",
        )

    command = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        if completed.returncode != 0:
            return (
                _date_from_filename(path) or "Unknown-Date",
                "Unknown-Device",
                fallback_lat,
                fallback_lon,
                completed.stderr[-800:] or "ffprobe failed",
            )
        data = json.loads(completed.stdout or "{}")
        date = _date_from_video_tags(data) or _date_from_filename(path) or "Unknown-Date"
        device = _device_from_video_tags(data) or "Unknown-Device"
        lat, lon = _gps_from_video_tags(data)
        lat = lat if lat is not None else fallback_lat
        lon = lon if lon is not None else fallback_lon
        return date, device, lat, lon, None
    except Exception as exc:
        return _date_from_filename(path) or "Unknown-Date", "Unknown-Device", None, None, f"Video metadata read failed: {exc}"


def _parse_media_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _date_from_video_tags(data: dict[str, Any]) -> str | None:
    candidates: list[Any] = []
    candidates.extend(_date_tag_values(data.get("format", {}).get("tags", {})))
    for stream in data.get("streams", []):
        candidates.extend(_date_tag_values(stream.get("tags", {})))
    for value in candidates:
        parsed = _parse_media_date(value)
        if parsed:
            return parsed
    return None


def _date_tag_values(tags: dict[str, Any]) -> list[Any]:
    values = []
    for key, value in tags.items():
        lowered = str(key).lower()
        if lowered in {"creation_time", "date", "com.apple.quicktime.creationdate"} or "date" in lowered:
            values.append(value)
    return values


def _device_from_video_tags(data: dict[str, Any]) -> str | None:
    tags = dict(data.get("format", {}).get("tags", {}))
    for stream in data.get("streams", []):
        tags.update(stream.get("tags", {}))
    make = tags.get("com.apple.quicktime.make") or tags.get("make")
    model = tags.get("com.apple.quicktime.model") or tags.get("model")
    device = " ".join(str(part).strip() for part in [make, model] if part)
    return device or None


def _gps_from_video_tags(data: dict[str, Any]) -> tuple[float | None, float | None]:
    tags = dict(data.get("format", {}).get("tags", {}))
    for stream in data.get("streams", []):
        tags.update(stream.get("tags", {}))
    for key, value in tags.items():
        lowered = str(key).lower()
        if "location" in lowered or "gps" in lowered:
            parsed = _parse_iso6709(str(value))
            if parsed:
                return parsed
    return None, None


def _parse_iso6709(value: str) -> tuple[float, float] | None:
    match = re.search(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", value.strip())
    if not match:
        return None
    try:
        return float(match.group(1)), float(match.group(2))
    except ValueError:
        return None


def _gps_from_video_binary(path: Path) -> tuple[float | None, float | None]:
    try:
        data = path.read_bytes()
    except Exception:
        return None, None
    text = data.decode("latin-1", errors="ignore")
    match = re.search(r"([+-]\d{2,3}\.\d{3,})([+-]\d{2,3}\.\d{3,})(?:[+-]\d+(?:\.\d+)?)?/", text)
    if not match:
        return None, None
    parsed = _parse_iso6709(match.group(0))
    if not parsed:
        return None, None
    lat, lon = parsed
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None, None


def _date_from_filename(path: Path) -> str | None:
    text = path.stem
    patterns = [
        ("ymd", r"(20\d{2}|19\d{2})[-_]?([01]\d)[-_]?([0-3]\d)"),
        ("dmy", r"([0-3]\d)[-_]([01]\d)[-_](20\d{2}|19\d{2})"),
        ("mdy", r"([01]\d)[-_]([0-3]\d)[-_](20\d{2}|19\d{2})"),
    ]
    for kind, pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        groups = match.groups()
        if kind == "ymd":
            year, month, day = groups
        elif kind == "dmy":
            day, month, year = groups
        else:
            month, day, year = groups
        try:
            return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _gps_from_exif(exif: Any, exif_tags: Any) -> tuple[float | None, float | None]:
    gps_tag = next((key for key, name in exif_tags.TAGS.items() if name == "GPSInfo"), None)
    if gps_tag is None:
        return None, None
    gps = exif.get_ifd(gps_tag)
    if not gps:
        return None, None
    gps_tags = {exif_tags.GPSTAGS.get(k, k): v for k, v in gps.items()}
    lat = _coord_to_float(gps_tags.get("GPSLatitude"), gps_tags.get("GPSLatitudeRef"))
    lon = _coord_to_float(gps_tags.get("GPSLongitude"), gps_tags.get("GPSLongitudeRef"))
    return lat, lon


def _coord_to_float(value: Any, ref: Any) -> float | None:
    if not value or not ref:
        return None
    parts = [float(part) for part in value]
    decimal = parts[0] + parts[1] / 60 + parts[2] / 3600
    if str(ref).upper() in {"S", "W"}:
        decimal *= -1
    return decimal
