"""Second-pass file stress checks: fixtures and mocked failures only."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import subtitle_sync_embedded as subtitles
import utility_toolbox as toolbox
from media_organizer import organizer
from media_organizer.models import AppConfig, MediaItem, ScanManifest
from media_organizer.scanner import scan_media
from PIL import Image


def subtitle_app(tmp_path, monkeypatch):
    app = subtitles.MainApp.__new__(subtitles.MainApp)
    app.target_dir = tmp_path / "media"
    app.target_dir.mkdir()
    app.history_db = {}
    app.is_running = False
    app.stop_requested = False
    app.safe_log = Mock()
    app.safe_ui = Mock()
    app.clear_log = Mock()
    app.root = SimpleNamespace(after=Mock())
    app.season_ent = SimpleNamespace(get=lambda: "1")
    app._read_inputs = lambda: ("Show", 1, False, app.target_dir)
    for name in ("btn_undo", "btn_run", "btn_audit", "btn_stop", "progress", "dry_run"):
        setattr(app, name, Mock())
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    monkeypatch.setattr(subtitles.AppConfig, "BASE_DIR", history_dir)
    monkeypatch.setattr(subtitles.AppConfig, "HISTORY_FILE", history_dir / "history.json")
    monkeypatch.setattr(subtitles.AppConfig, "TMP_HISTORY", history_dir / "history.json.tmp")
    return app


def test_subtitle_repeated_rename_cycle_retains_original_filename(tmp_path, monkeypatch):
    app = subtitle_app(tmp_path, monkeypatch)
    original = app.target_dir / "raw.S01E01.mkv"
    original.write_bytes(b"original video")
    for show in ("Alpha", "Beta", "Alpha"):
        app.is_running = True
        app.batch_worker(show, 1, False, app.target_dir)

    app.undo()

    assert original.read_bytes() == b"original video"
    assert sorted(path.name for path in app.target_dir.iterdir()) == [original.name]
    assert not app.history_db


@pytest.mark.parametrize("repeat_show", [None, "Alpha"])
def test_subtitle_replacement_file_does_not_inherit_another_files_rename_history(tmp_path, monkeypatch, repeat_show):
    app = subtitle_app(tmp_path, monkeypatch)
    original = app.target_dir / "raw.S01E01.mkv"
    original.write_bytes(b"original video")
    app.is_running = True
    app.batch_worker("Alpha", 1, False, app.target_dir)
    alpha = app.target_dir / "Alpha - S01E01.mkv"
    original_record = dict(app.history_db[app.target_dir.as_posix()][alpha.name])
    parked = tmp_path / "parked-original.mkv"
    alpha.rename(parked)
    alpha.write_bytes(b"unrelated replacement")

    app.is_running = True
    app.batch_worker("Beta", 1, False, app.target_dir)
    if repeat_show:
        records = dict(app.history_db[app.target_dir.as_posix()])
        app.is_running = True
        app.batch_worker(repeat_show, 1, False, app.target_dir)
        assert app.history_db[app.target_dir.as_posix()] == records
        assert (app.target_dir / "Beta - S01E01.mkv").read_bytes() == b"unrelated replacement"
    app.undo()

    assert alpha.read_bytes() == b"unrelated replacement"
    assert parked.read_bytes() == b"original video"
    assert not original.exists()
    assert app.history_db[app.target_dir.as_posix()] == {alpha.name: original_record}


def test_subtitle_legacy_history_is_retained_when_chain_identity_is_unknown(tmp_path, monkeypatch):
    app = subtitle_app(tmp_path, monkeypatch)
    source = app.target_dir / "Alpha - S01E01.mkv"
    source.write_bytes(b"unknown identity")
    history = {app.target_dir.as_posix(): {source.name: "raw.S01E01.mkv"}}
    app.history_db = history
    app.is_running = True

    app.batch_worker("Beta", 1, False, app.target_dir)

    assert source.read_bytes() == b"unknown identity"
    assert json.loads(subtitles.AppConfig.HISTORY_FILE.read_text(encoding="utf-8")) == history
    assert any("身份" in call.args[0] for call in app.safe_log.call_args_list)


def test_subtitle_failed_rename_and_failed_journal_rollback_never_undo_foreign_file(tmp_path, monkeypatch):
    app = subtitle_app(tmp_path, monkeypatch)
    source = app.target_dir / "raw.S01E01.mkv"
    source.write_bytes(b"source video")
    destination = app.target_dir / "Show - S01E01.mkv"
    save = app._save_history_db
    calls = []

    def fail_rollback():
        calls.append(True)
        return False if len(calls) == 3 else save()

    rename = Path.rename

    def raced_rename(path, target):
        if path == source:
            Path(target).write_bytes(b"foreign file")
            raise FileExistsError("destination appeared")
        return rename(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(app, "_save_history_db", fail_rollback)
        patch.setattr(Path, "rename", raced_rename)
        app.is_running = True
        app.batch_worker("Show", 1, False, app.target_dir)
    app._load_history_db()

    app.undo()

    assert source.read_bytes() == b"source video"
    assert destination.read_bytes() == b"foreign file"
    assert sorted(path.name for path in app.target_dir.iterdir()) == sorted([source.name, destination.name])


def test_subtitle_ten_rename_undo_cycles_and_repeated_cancellation(tmp_path, monkeypatch):
    app = subtitle_app(tmp_path, monkeypatch)
    source = app.target_dir / "raw.S01E01.mkv"
    source.write_bytes(b"stable video")
    for _cycle in range(10):
        app.is_running = True
        app.stop_requested = False
        app.batch_worker("Show", 1, False, app.target_dir)
        app.undo()
        assert source.read_bytes() == b"stable video"
    for _attempt in range(100):
        app.is_running = True
        app.stop_task()
        app.batch_worker("Show", 1, False, app.target_dir)
        assert not app.is_running
        assert source.read_bytes() == b"stable video"


def test_subtitle_cancel_during_rename_blocks_one_hundred_reentry_attempts(tmp_path, monkeypatch):
    app = subtitle_app(tmp_path, monkeypatch)
    first = app.target_dir / "raw.S01E01.mkv"
    second = app.target_dir / "raw.S01E02.mkv"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    renamed = threading.Event()
    release = threading.Event()
    rename = Path.rename

    def pause_first_rename(path, target):
        result = rename(path, target)
        if path == first:
            renamed.set()
            if not release.wait(5):
                raise RuntimeError("test did not release the rename")
        return result

    monkeypatch.setattr(Path, "rename", pause_first_rename)
    app.is_running = True
    worker = threading.Thread(target=app.batch_worker, args=("Show", 1, False, app.target_dir))
    worker.start()
    try:
        assert renamed.wait(5)
        app._read_inputs = Mock(side_effect=AssertionError("a second job must not read inputs"))
        for _attempt in range(100):
            app.stop_task()
            app.start_task()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and not app.is_running
    assert second.read_bytes() == b"second"
    assert (app.target_dir / "Show - S01E01.mkv").read_bytes() == b"first"
    app.undo()
    assert first.read_bytes() == b"first"


@pytest.mark.parametrize("kind", ["broken_json", "wrong_mapping"])
def test_subtitle_invalid_history_is_preserved_without_renaming_files(tmp_path, monkeypatch, kind):
    app = subtitle_app(tmp_path, monkeypatch)
    source = app.target_dir / "raw.S01E01.mkv"
    source.write_bytes(b"video")
    raw = b'{"incomplete":' if kind == "broken_json" else json.dumps({app.target_dir.as_posix(): ["wrong type"]}).encode()
    subtitles.AppConfig.HISTORY_FILE.write_bytes(raw)
    app._load_history_db()
    app.is_running = True

    app.batch_worker("Show", 1, False, app.target_dir)

    assert subtitles.AppConfig.HISTORY_FILE.read_bytes() == raw
    assert source.read_bytes() == b"video"


@pytest.mark.parametrize("with_file", [False, True])
def test_stopped_subtitle_audit_never_reports_pass(tmp_path, monkeypatch, with_file):
    app = subtitle_app(tmp_path, monkeypatch)
    if with_file:
        (app.target_dir / "unmatched.S01E01.mkv").write_bytes(b"video")
    app.is_running = True
    app.stop_task()

    app.audit_worker(app.target_dir, 1)

    assert not any("校对通过" in call.args[0] for call in app.safe_log.call_args_list)
    assert not app.is_running


@pytest.mark.parametrize("operation", ["start_task", "run_audit"])
def test_subtitle_thread_start_failure_does_not_lock_the_page(tmp_path, monkeypatch, operation):
    app = subtitle_app(tmp_path, monkeypatch)
    monkeypatch.setattr(subtitles.threading, "Thread", Mock(return_value=SimpleNamespace(start=Mock(side_effect=RuntimeError("no worker available")))))

    getattr(app, operation)()

    assert not app.is_running
    assert any("no worker available" in call.args[0] for call in app.safe_log.call_args_list)


def date_app(tmp_path, monkeypatch):
    app = toolbox.DateRenamerTab.__new__(toolbox.DateRenamerTab)
    app.folder = tmp_path / "media"
    app.folder.mkdir()
    state = tmp_path / "history"
    state.mkdir()
    app.undo_file = state / "undo.json"
    app.preview = Mock()
    monkeypatch.setattr(toolbox, "ensure_app_dir", lambda: None)
    monkeypatch.setattr(toolbox, "log_line", Mock())
    monkeypatch.setattr(toolbox.messagebox, "askokcancel", lambda *args, **kwargs: True)
    monkeypatch.setattr(toolbox.messagebox, "showinfo", Mock())
    monkeypatch.setattr(toolbox.messagebox, "showerror", Mock())
    return app


def test_date_undo_validates_all_records_before_mutating_files(tmp_path, monkeypatch):
    app = date_app(tmp_path, monkeypatch)
    renamed = app.folder / "renamed.txt"
    renamed.write_bytes(b"content")
    original = app.folder / "original.txt"
    raw = json.dumps({"items": [{"broken": True}, {"old": str(original), "new": str(renamed)}]}).encode()
    app.undo_file.write_bytes(raw)

    app.undo_last()

    assert renamed.read_bytes() == b"content"
    assert not original.exists()
    assert app.undo_file.read_bytes() == raw
    assert toolbox.messagebox.showerror.called


def test_date_journal_write_failure_preserves_previous_undo_and_current_files(tmp_path, monkeypatch):
    app = date_app(tmp_path, monkeypatch)
    original = app.folder / "original.txt"
    renamed = app.folder / "renamed.txt"
    original.write_bytes(b"content")
    old_journal = b'{"created_at":"previous","items":[]}'
    app.undo_file.write_bytes(old_journal)
    app.plan = [(original, renamed, "待改名")]

    def fail_during_write(payload, stream, **kwargs):
        stream.write("{")
        raise OSError("disk full during journal write")

    with monkeypatch.context() as patch:
        patch.setattr(toolbox.json, "dump", fail_during_write)
        try:
            app.apply()
        except OSError:
            pass  # Assert the filesystem invariant even if failure propagates.

    assert app.undo_file.read_bytes() == old_journal
    assert original.read_bytes() == b"content"
    assert not renamed.exists()


def test_date_zero_success_keeps_previous_journal_exactly(tmp_path, monkeypatch):
    app = date_app(tmp_path, monkeypatch)
    source = app.folder / "original.txt"
    destination = app.folder / "new.txt"
    source.write_bytes(b"source")
    destination.write_bytes(b"preexisting target")
    previous = b'{"items": [], "unknown": 42}\n'
    app.undo_file.write_bytes(previous)
    app.plan = [(source, destination, "待改名")]

    app.apply()

    assert source.read_bytes() == b"source"
    assert destination.read_bytes() == b"preexisting target"
    assert app.undo_file.read_bytes() == previous


def test_date_ten_rename_undo_cycles_preserve_content_and_names(tmp_path, monkeypatch):
    app = date_app(tmp_path, monkeypatch)
    source = app.folder / "original.txt"
    destination = app.folder / "renamed.txt"
    source.write_bytes(b"stable content")
    for _cycle in range(10):
        app.plan = [(source, destination, "待改名")]
        app.apply()
        assert destination.read_bytes() == b"stable content"
        app.undo_last()
        assert source.read_bytes() == b"stable content"
        assert not destination.exists()


def test_date_partial_batch_journal_failure_never_restores_unrelated_target(tmp_path, monkeypatch):
    app = date_app(tmp_path, monkeypatch)
    first = app.folder / "first.txt"
    first_new = app.folder / "first-new.txt"
    second = app.folder / "second.txt"
    second_new = app.folder / "second-new.txt"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    second_new.write_bytes(b"foreign")
    app.plan = [(first, first_new, "待改名"), (second, second_new, "待改名")]
    write = app._write_undo_payload

    def fail_final_update(payload, destination=None):
        if isinstance(payload, dict) and len(payload["items"]) == 1:
            raise OSError("disk full after partial batch")
        return write(payload, destination)

    with monkeypatch.context() as patch:
        patch.setattr(app, "_write_undo_payload", fail_final_update)
        app.apply()

    app.undo_last()

    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert second_new.read_bytes() == b"foreign"
    assert not (app.folder / "second_restored.txt").exists()


def test_subtitle_history_disk_failure_leaves_sources_and_previous_journal_unchanged(tmp_path, monkeypatch):
    app = subtitle_app(tmp_path, monkeypatch)
    source = app.target_dir / "raw.S01E01.mkv"
    source.write_bytes(b"video")
    previous = b'{}\n'
    subtitles.AppConfig.HISTORY_FILE.write_bytes(previous)
    app._load_history_db()

    def disk_full(payload, stream, **kwargs):
        stream.write("{")
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(subtitles.json, "dump", disk_full)
        app.is_running = True
        app.batch_worker("Show", 1, False, app.target_dir)

    assert source.read_bytes() == b"video"
    assert subtitles.AppConfig.HISTORY_FILE.read_bytes() == previous
    assert not list(subtitles.AppConfig.BASE_DIR.glob("*.tmp"))


def media_manifest(tmp_path, count=1):
    source = tmp_path / "source"
    output = tmp_path / "output"
    items = []
    for index in range(count):
        folder = source / str(index)
        folder.mkdir(parents=True)
        path = folder / "photo.jpg"
        payload = f"media-{index}".encode()
        path.write_bytes(payload)
        items.append(MediaItem(str(path), "photo", ".jpg", len(payload), target_path=str(output / "Photos" / path.name)))
    return ScanManifest(AppConfig(str(source), str(output)), items, [], [])


def test_media_copy_preserves_concurrently_created_destination(tmp_path):
    manifest = media_manifest(tmp_path)
    destination = Path(manifest.items[0].target_path)

    def target_appears(message):
        if "Copying" in message:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"concurrently created file")

    organizer.execute_plan(manifest, target_appears)

    assert destination.read_bytes() == b"concurrently created file"
    assert Path(manifest.items[0].source_path).read_bytes() == b"media-0"


def test_media_copy_failure_removes_its_incomplete_output(tmp_path, monkeypatch):
    manifest = media_manifest(tmp_path)
    destination = Path(manifest.items[0].target_path)

    def incomplete_copy(source, target):
        target.write(b"incomplete")
        raise OSError("disk full")

    monkeypatch.setattr(organizer.shutil, "copyfileobj", incomplete_copy)

    result = organizer.execute_plan(manifest)

    assert len(result["failed"]) == 1
    assert not destination.exists()
    assert Path(manifest.items[0].source_path).read_bytes() == b"media-0"


def test_media_failed_metadata_copy_keeps_external_replacement(tmp_path, monkeypatch):
    manifest = media_manifest(tmp_path)
    destination = Path(manifest.items[0].target_path)

    def replace_then_fail(source, target):
        Path(target).rename(tmp_path / "moved-by-external-program.jpg")
        Path(target).write_bytes(b"external replacement")
        raise OSError("metadata copy failed")

    monkeypatch.setattr(organizer.shutil, "copystat", replace_then_fail)

    result = organizer.execute_plan(manifest)

    assert len(result["failed"]) == 1
    assert destination.read_bytes() == b"external replacement"


def test_media_many_same_named_inputs_preserve_every_payload(tmp_path):
    manifest = media_manifest(tmp_path, count=128)

    result = organizer.execute_plan(manifest)

    outputs = list((Path(manifest.config.output_dir) / "Photos").iterdir())
    assert len(outputs) == 128
    assert {path.read_bytes() for path in outputs} == {f"media-{index}".encode() for index in range(128)}
    assert len(result["copied"]) == 128
    assert not result["failed"]


def test_media_repeated_scan_and_copy_excludes_nested_output_tree(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for index in range(32):
        Image.new("RGB", (8, 8), (index, 50, 100)).save(source / f"photo-{index}.png")
    config = AppConfig(str(source), str(source / "organized"))

    first = scan_media(config)
    result = organizer.execute_plan(first)
    second = scan_media(config)

    assert len(result["copied"]) == 32 and not result["failed"]
    assert len(second.items) == 32
    assert all(Path(item.source_path).parent == source for item in second.items)
