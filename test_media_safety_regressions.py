"""Filesystem regressions use only pytest-owned directories; no media tools run."""
from pathlib import Path
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import subtitle_sync_embedded as subtitles
from PIL import Image

from media_organizer import metadata, organizer, video
from media_organizer.models import AppConfig, MediaItem, ScanManifest


def subtitle_app(tmp_path, monkeypatch):
    app = subtitles.MainApp.__new__(subtitles.MainApp)
    app.target_dir = tmp_path
    app.history_db = {}
    app.is_running = False
    app.stop_requested = False
    app.safe_log = Mock()
    app.safe_ui = Mock()
    app.root = SimpleNamespace(after=Mock())
    for name in ("btn_undo", "btn_run", "btn_audit", "btn_stop", "progress", "dry_run"):
        setattr(app, name, Mock())
    history_dir = tmp_path / "history"
    monkeypatch.setattr(subtitles.AppConfig, "BASE_DIR", history_dir)
    monkeypatch.setattr(subtitles.AppConfig, "HISTORY_FILE", history_dir / "history.json")
    monkeypatch.setattr(subtitles.AppConfig, "TMP_HISTORY", history_dir / "history.json.tmp")
    return app


def test_select_directory_preserves_existing_permission_probe(tmp_path, monkeypatch):
    probe = tmp_path / ".permission_probe.tmp"
    probe.write_bytes(b"existing unrelated content")
    app = subtitle_app(tmp_path, monkeypatch)
    monkeypatch.setattr(subtitles.filedialog, "askdirectory", lambda: str(tmp_path))

    app.select_dir()

    assert probe.read_bytes() == b"existing unrelated content"
    assert {item.name for item in tmp_path.iterdir()} == {".permission_probe.tmp"}


def test_undo_preserves_all_existing_recovery_names(tmp_path, monkeypatch):
    app = subtitle_app(tmp_path, monkeypatch)
    original = tmp_path / "original.mkv"
    recovered = tmp_path / "original_recovered.mkv"
    another_recovered = tmp_path / "original_recovered_2.mkv"
    renamed = tmp_path / "renamed.mkv"
    original.write_bytes(b"existing original")
    recovered.write_bytes(b"first recovery")
    another_recovered.write_bytes(b"second recovery")
    renamed.write_bytes(b"media to restore")
    app.history_db = {tmp_path.as_posix(): {renamed.name: original.name}}

    app.undo()

    assert original.read_bytes() == b"existing original"
    assert recovered.read_bytes() == b"first recovery"
    assert another_recovered.read_bytes() == b"second recovery"
    assert (tmp_path / "original_recovered_3.mkv").read_bytes() == b"media to restore"
    assert not renamed.exists()
    assert not app.history_db


@pytest.mark.parametrize("stop_requested", [False, True])
def test_undo_does_not_race_an_active_batch(tmp_path, monkeypatch, stop_requested):
    app = subtitle_app(tmp_path, monkeypatch)
    renamed = tmp_path / "renamed.mkv"
    renamed.write_bytes(b"media")
    app.history_db = {tmp_path.as_posix(): {renamed.name: "original.mkv"}}
    app.is_running = True
    if stop_requested:
        app.stop_task()

    app.undo()

    assert renamed.read_bytes() == b"media"
    assert not (tmp_path / "original.mkv").exists()
    assert app.history_db[tmp_path.as_posix()] == {"renamed.mkv": "original.mkv"}


def test_batch_preserves_target_created_between_check_and_rename(tmp_path, monkeypatch):
    app = subtitle_app(tmp_path, monkeypatch)
    source = tmp_path / "raw.S01E01.mkv"
    target = tmp_path / "Show - S01E01.mkv"
    source.write_bytes(b"source media")
    app.is_running = True
    rename = subtitles.os.rename

    def target_appears(old, new):
        if Path(old) == source:
            Path(new).write_bytes(b"concurrently created media")
            raise FileExistsError("destination appeared")
        return rename(old, new)

    monkeypatch.setattr(subtitles.os, "rename", target_appears)
    app.batch_worker("Show", 1, False, tmp_path)

    assert target.read_bytes() == b"concurrently created media"
    assert source.read_bytes() == b"source media"
    assert not app.history_db[tmp_path.as_posix()]


@pytest.mark.parametrize("prior_compressed", [False, True])
def test_compression_keeps_distinct_videos_with_the_same_stem(tmp_path, monkeypatch, prior_compressed):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"
    compressed_dir = output / "Videos" / "Compressed"
    compressed_dir.mkdir(parents=True)
    if prior_compressed:
        (compressed_dir / "movie.mp4").write_bytes(b"previous compressed video")
    items = []
    for extension, content in ((".mov", b"MOV video"), (".mp4", b"MP4 video")):
        path = source / ("movie" + extension)
        path.write_bytes(content)
        items.append(MediaItem(str(path), "video", extension, len(content), target_path=str(output / "Videos" / path.name)))

    def fake_ffmpeg(command, **kwargs):
        destination = Path(command[-1])
        if destination.exists() and "-y" not in command:
            return SimpleNamespace(returncode=1, stderr="destination exists")
        input_path = Path(command[command.index("-i") + 1])
        destination.write_bytes(input_path.read_bytes())
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(video.shutil, "which", lambda name: "fake-ffmpeg")
    monkeypatch.setattr(video.subprocess, "run", fake_ffmpeg)
    manifest = ScanManifest(AppConfig(str(source), str(output)), items, [], [])

    result = organizer.execute_plan(manifest)

    expected = {b"MOV video", b"MP4 video"}
    if prior_compressed:
        expected.add(b"previous compressed video")
    assert {path.read_bytes() for path in compressed_dir.iterdir()} == expected
    assert len(result["compressed"]) == 2
    assert not result["failed"] and not result["skipped"]


def test_compress_video_rejects_existing_output_before_launch(tmp_path, monkeypatch):
    source = tmp_path / "source.mov"
    destination = tmp_path / "output.mp4"
    source.write_bytes(b"source")
    destination.write_bytes(b"previous output")
    monkeypatch.setattr(video.shutil, "which", lambda name: "fake-ffmpeg")

    def fake_ffmpeg(*args, **kwargs):
        destination.write_bytes(b"overwritten video")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(video.subprocess, "run", fake_ffmpeg)
    ok, message = video.compress_video(source, destination)

    assert not ok
    assert "exist" in message.lower()
    assert destination.read_bytes() == b"previous output"


def test_photo_sort_date_uses_original_capture_time_from_exif_ifd(tmp_path):
    photo = tmp_path / "photo.jpg"
    exif = Image.Exif()
    exif[306] = "2026:11:08 12:30:00"
    exif[34665] = {36867: "2024:05:09 09:10:11"}
    Image.new("RGB", (8, 8)).save(photo, exif=exif)

    date, _device, _latitude, _longitude, error = metadata.extract_photo_metadata(photo)

    assert date == "2024-05-09"
    assert error is None


@pytest.mark.parametrize("padding", [0, 1024 * 1024 - 8])
def test_video_gps_reads_bounded_chunks_and_handles_split_coordinates(tmp_path, monkeypatch, padding):
    source = tmp_path / "video.mov"
    payload = b"\x00" * padding + b"+22.54321+114.12345/" + b"\x00" * 32

    class BoundedReader(BytesIO):
        def read(self, size=-1):
            if not 0 < size <= 1024 * 1024:
                raise OSError("unbounded video read would exhaust memory")
            return super().read(size)

    original_open = Path.open

    def open_video(path, *args, **kwargs):
        if path == source:
            return BoundedReader(payload)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_video)

    assert metadata._gps_from_video_binary(source) == (22.54321, 114.12345)
