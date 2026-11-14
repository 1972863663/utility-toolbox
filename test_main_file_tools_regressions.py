"""Exercise file tools using isolated files and deferred, non-UI workers."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import utility_toolbox as toolbox
from media_organizer.models import ScanManifest


class Value:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Button:
    def __init__(self, state="normal"):
        self.state = state

    def configure(self, *, state):
        self.state = state


@pytest.fixture
def dialogs(monkeypatch):
    monkeypatch.setattr(toolbox.messagebox, "askokcancel", lambda *args, **kwargs: True)
    monkeypatch.setattr(toolbox.messagebox, "showinfo", Mock())
    monkeypatch.setattr(toolbox.messagebox, "showerror", Mock())
    monkeypatch.setattr(toolbox, "log_line", Mock())


def date_renamer(tmp_path, monkeypatch):
    app = toolbox.DateRenamerTab.__new__(toolbox.DateRenamerTab)
    app.folder = tmp_path / "files"
    app.folder.mkdir()
    app.undo_file = tmp_path / "state" / "undo.json"
    monkeypatch.setattr(toolbox, "ensure_app_dir", lambda: app.undo_file.parent.mkdir(exist_ok=True))
    app.plan = []
    app.tree = Mock()
    app.tree.get_children.return_value = []
    app.mode_var = Value("添加前缀")
    app.date_var = Value("prefix-")
    app.find_var = Value()
    app.replace_var = Value()
    app.start_var = Value("1")
    return app


@pytest.mark.parametrize("mode, original, renamed", [("转小写", "Example.txt", "example.txt"), ("转大写", "Example.txt", "EXAMPLE.txt")])
def test_case_only_rename_and_undo_do_not_add_collision_suffixes(tmp_path, monkeypatch, dialogs, mode, original, renamed):
    app = date_renamer(tmp_path, monkeypatch)
    (app.folder / original).write_bytes(b"content")
    app.mode_var.set(mode)

    app.preview()

    assert app.plan[0][1].name == renamed
    app.apply()
    assert [path.name for path in app.folder.iterdir()] == [renamed]
    app.undo_last()
    assert [path.name for path in app.folder.iterdir()] == [original]
    assert (app.folder / original).read_bytes() == b"content"


@pytest.mark.parametrize("prefix", ["../escaped-", "..\\escaped-"])
def test_rename_rejects_filename_components_that_escape_selected_folder(tmp_path, monkeypatch, dialogs, prefix):
    app = date_renamer(tmp_path, monkeypatch)
    source = app.folder / "original.txt"
    source.write_bytes(b"user content")
    app.date_var.set(prefix)

    app.preview()
    app.apply()

    assert source.read_bytes() == b"user content"
    assert not (tmp_path / "escaped-original.txt").exists()
    assert not app.undo_file.exists()


@pytest.mark.parametrize("failure", ["locked", "missing"])
def test_undo_keeps_only_failed_items_for_a_later_retry(tmp_path, monkeypatch, dialogs, failure):
    app = date_renamer(tmp_path, monkeypatch)
    old = app.folder / "original.txt"
    new = app.folder / "renamed.txt"
    done_old = app.folder / "done-original.txt"
    done_new = app.folder / "done-renamed.txt"
    if failure == "locked":
        new.write_bytes(b"retry content")
    done_new.write_bytes(b"immediate content")
    retry_item = {"old": str(old), "new": str(new)}
    app.undo_file.parent.mkdir()
    app.undo_file.write_text(json.dumps({"items": [retry_item, {"old": str(done_old), "new": str(done_new)}]}), encoding="utf-8")
    original_rename = Path.rename

    def locked_rename(path, target):
        if path == new:
            raise PermissionError("file is busy")
        return original_rename(path, target)

    with monkeypatch.context() as patch:
        if failure == "locked":
            patch.setattr(Path, "rename", locked_rename)
        app.undo_last()

    assert done_old.read_bytes() == b"immediate content"
    assert json.loads(app.undo_file.read_text(encoding="utf-8"))["items"] == [retry_item]
    if failure == "missing":
        new.write_bytes(b"retry content")
    app.undo_last()
    assert old.read_bytes() == b"retry content"
    assert not app.undo_file.exists()


def test_undo_chooses_unused_recovery_name(tmp_path, monkeypatch, dialogs):
    app = date_renamer(tmp_path, monkeypatch)
    old = app.folder / "original.txt"
    old.write_bytes(b"original occupied")
    recovered = app.folder / "original_restored.txt"
    recovered.write_bytes(b"recovery occupied")
    renamed = app.folder / "renamed.txt"
    renamed.write_bytes(b"restore me")
    app.undo_file.parent.mkdir()
    app.undo_file.write_text(json.dumps({"items": [{"old": str(old), "new": str(renamed)}]}), encoding="utf-8")

    app.undo_last()

    assert old.read_bytes() == b"original occupied"
    assert recovered.read_bytes() == b"recovery occupied"
    assert (app.folder / "original_restored_1.txt").read_bytes() == b"restore me"
    assert not renamed.exists()


def media_app(tmp_path, monkeypatch):
    app = toolbox.MediaOrganizerTab.__new__(toolbox.MediaOrganizerTab)
    app.busy = False
    app.manifest = None
    app.source_var = Value(str(tmp_path / "source"))
    app.output_var = Value(str(tmp_path / "output"))
    app.summary_var = Value("等待扫描")
    app.scan_button = Button()
    app.execute_button = Button("disabled")
    app.log_text = Mock()
    app.status_callback = Mock()
    callbacks = []
    app.parent = SimpleNamespace(after=lambda delay, callback: callbacks.append(callback))
    jobs = []

    class DeferredThread:
        def __init__(self, *, target, args=(), **kwargs):
            self.target = target
            self.args = args

        def start(self):
            jobs.append(lambda: self.target(*self.args))

    monkeypatch.setattr(toolbox.threading, "Thread", DeferredThread)
    return app, jobs, callbacks


def finish_callbacks(callbacks):
    while callbacks:
        callbacks.pop(0)()


def test_media_scan_uses_main_thread_parameter_snapshot(tmp_path, monkeypatch, dialogs):
    app, jobs, callbacks = media_app(tmp_path, monkeypatch)
    observed = []

    def scan(config):
        observed.append((config.source_dir, config.output_dir))
        return ScanManifest(config, [], [], [])

    monkeypatch.setattr(toolbox, "scan_media", scan)
    app.scan()
    app.source_var.set(str(tmp_path / "later-source"))
    app.output_var.set(str(tmp_path / "later-output"))
    jobs.pop(0)()
    finish_callbacks(callbacks)

    assert observed == [(str(tmp_path / "source"), str(tmp_path / "output"))]
    assert (tmp_path / "output" / "scan_manifest.json").exists()
    assert not (tmp_path / "later-output").exists()
    assert app.scan_button.state == "normal" and app.execute_button.state == "normal"


@pytest.mark.parametrize("first_operation", ["scan", "execute"])
def test_media_blocks_overlapping_scan_or_execution(tmp_path, monkeypatch, dialogs, first_operation):
    app, jobs, _callbacks = media_app(tmp_path, monkeypatch)
    app.manifest = object()

    getattr(app, first_operation)()
    app.scan()
    app.execute()

    assert len(jobs) == 1
    assert app.scan_button.state == "disabled" and app.execute_button.state == "disabled"


def test_media_execute_uses_the_confirmed_manifest_snapshot(tmp_path, monkeypatch, dialogs):
    app, jobs, callbacks = media_app(tmp_path, monkeypatch)
    confirmed = object()
    app.manifest = confirmed
    observed = []

    def execute(manifest, progress):
        observed.append(manifest)
        return {"copied": [], "compressed": [], "skipped": [], "failed": []}

    monkeypatch.setattr(toolbox, "execute_plan", execute)
    app.execute()
    app.manifest = object()
    jobs.pop(0)()
    finish_callbacks(callbacks)

    assert observed == [confirmed]
    assert app.scan_button.state == "normal"
    assert not app.busy


@pytest.mark.parametrize("operation", ["scan", "execute"])
def test_media_worker_failure_restores_retry_state(tmp_path, monkeypatch, dialogs, operation):
    app, jobs, callbacks = media_app(tmp_path, monkeypatch)
    app.manifest = object()
    failure = RuntimeError("controlled media failure")
    monkeypatch.setattr(toolbox, "scan_media" if operation == "scan" else "execute_plan", Mock(side_effect=failure))
    getattr(app, operation)()
    jobs.pop(0)()
    finish_callbacks(callbacks)

    assert not app.busy and app.scan_button.state == "normal"
    assert app.execute_button.state == ("disabled" if operation == "scan" else "normal")
    assert "失败" in app.summary_var.get()
    assert toolbox.messagebox.showerror.call_args.args[1] == "controlled media failure"
    getattr(app, operation)()
    assert len(jobs) == 1


def test_media_thread_start_failure_restores_controls(tmp_path, monkeypatch, dialogs):
    app, _jobs, _callbacks = media_app(tmp_path, monkeypatch)
    monkeypatch.setattr(toolbox.threading, "Thread", Mock(return_value=SimpleNamespace(start=Mock(side_effect=RuntimeError("thread unavailable")))))

    app.scan()

    assert not app.busy and app.scan_button.state == "normal"
    assert "失败" in app.summary_var.get()
    assert toolbox.messagebox.showerror.call_args.args[1] == "thread unavailable"
