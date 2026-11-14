"""Undo history must survive a temporarily unavailable renamed file."""

import json
from unittest import mock

import subtitle_sync_embedded as subtitles


def test_missing_subtitle_undo_item_remains_persisted_and_can_be_retried(tmp_path, monkeypatch):
    history = tmp_path / "state" / "history.json"
    folder = tmp_path / "media"
    folder.mkdir()
    monkeypatch.setattr(subtitles.AppConfig, "BASE_DIR", history.parent)
    monkeypatch.setattr(subtitles.AppConfig, "HISTORY_FILE", history)
    monkeypatch.setattr(subtitles.AppConfig, "TMP_HISTORY", history.with_suffix(".tmp"))
    app = subtitles.MainApp.__new__(subtitles.MainApp)
    app.is_running = False
    app.target_dir = folder
    app.btn_undo = mock.Mock()
    app.safe_ui = mock.Mock()
    app.safe_log = mock.Mock()
    app.history_db = {folder.as_posix(): {"available.srt": "original-available.srt", "missing.srt": "original-missing.srt"}}
    (folder / "available.srt").write_bytes(b"available subtitle")

    app.undo()

    assert (folder / "original-available.srt").read_bytes() == b"available subtitle"
    expected = {folder.as_posix(): {"missing.srt": "original-missing.srt"}}
    assert app.history_db == expected
    assert json.loads(history.read_text(encoding="utf-8")) == expected
    # Reload from disk before retrying, so an in-memory-only fix is insufficient.
    app.history_db = {}
    app._load_history_db()
    (folder / "missing.srt").write_bytes(b"returned subtitle")
    app.undo()

    assert (folder / "original-missing.srt").read_bytes() == b"returned subtitle"
    assert not (folder / "missing.srt").exists()
    assert app.history_db == {}
    assert json.loads(history.read_text(encoding="utf-8")) == {}
