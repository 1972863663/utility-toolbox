"""Exercise queued error callbacks after the except block has finished."""
from unittest import mock
from types import SimpleNamespace

import pytest
import utility_toolbox as toolbox


class CallbackQueue:
    def __init__(self):
        self.callbacks = []

    def after(self, _delay, callback):
        self.callbacks.append(callback)


@pytest.mark.parametrize("operation", ["audio", "live", "scan", "execute"])
def test_background_failures_deliver_original_error_after_worker_exits(operation, tmp_path):
    parent = CallbackQueue()
    error = RuntimeError("controlled worker failure")
    obj = SimpleNamespace(parent=parent)
    if operation in {"scan", "execute"}:
        obj = toolbox.MediaOrganizerTab.__new__(toolbox.MediaOrganizerTab)
        obj.parent = parent
        obj.busy = True
        obj.manifest = None
        obj.scan_button = mock.Mock()
        obj.execute_button = mock.Mock()
        obj.summary_var = mock.Mock()
        obj.status_callback = mock.Mock()
    with mock.patch.object(toolbox.messagebox, "showerror") as showerror:
        if operation == "audio":
            class InlineThread:
                def __init__(self, target, **_kwargs):
                    self.target = target
                def start(self):
                    self.target()
            with mock.patch.object(toolbox.threading, "Thread", InlineThread), mock.patch.object(toolbox, "set_default_render_id", side_effect=error):
                toolbox.AudioSwitchTab.set_audio(obj, {"id": "unused", "name": "test"})
        elif operation == "live":
            with mock.patch.object(toolbox.webbrowser, "open", side_effect=error):
                toolbox.LiveShortcutTab._open_worker(obj, "test", "https://example.invalid", None)
        elif operation == "scan":
            config = toolbox.AppConfig(source_dir=str(tmp_path), output_dir=str(tmp_path))
            with mock.patch.object(toolbox, "scan_media", side_effect=error):
                toolbox.MediaOrganizerTab._scan_worker(obj, config)
        else:
            obj.manifest = object()
            with mock.patch.object(toolbox, "execute_plan", side_effect=error):
                toolbox.MediaOrganizerTab._execute_worker(obj, obj.manifest)
        assert len(parent.callbacks) == 1
        for callback in parent.callbacks:
            callback()
        assert showerror.call_args.args[1] == "controlled worker failure"


def test_quit_destroys_root_even_when_config_cannot_be_saved():
    app = toolbox.UtilityToolbox.__new__(toolbox.UtilityToolbox)
    app.root = mock.Mock()
    app.window_refresh_job = None
    app.backup = mock.Mock()
    app.drop_close = mock.Mock()
    app.topmost = mock.Mock()
    app.tray = mock.Mock()
    app.save_config = mock.Mock(side_effect=OSError("disk full"))
    with mock.patch.object(toolbox, "close_mic_toggle_processes"), mock.patch.object(toolbox.messagebox, "showerror") as showerror:
        app.quit_app()
    app.root.destroy.assert_called_once()
    assert "disk full" in showerror.call_args.args[1]


@pytest.mark.parametrize("content", [b"{broken", b"[]", b"null", b""])
def test_saving_does_not_replace_unreadable_or_non_object_existing_config(tmp_path, monkeypatch, content):
    path = tmp_path / "config.json"
    path.write_bytes(content)
    monkeypatch.setattr(toolbox, "APP_DIR", tmp_path)
    monkeypatch.setattr(toolbox, "CONFIG_FILE", path)
    with pytest.raises(ValueError):
        toolbox.save_config({"backup": {"sources": []}})
    assert path.read_bytes() == content
