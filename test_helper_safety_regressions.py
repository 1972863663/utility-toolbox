"""Helper recovery checks; all input, audio and listener boundaries are fakes."""

import importlib.util
import sys
import tkinter as tk
import types
from pathlib import Path
from unittest import mock

import pytest

from test_macro_and_media_regressions import load_loop_module
from test_system_tools import load_mic_toggle_module


def load_recorder_module():
    name = "safety_macro_recorder"
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "macro_tools/macro_recorder.py")
    module = importlib.util.module_from_spec(spec)
    pynput = types.ModuleType("pynput")
    pynput.keyboard = types.SimpleNamespace()
    pynput.mouse = types.SimpleNamespace()
    with mock.patch.dict(sys.modules, {name: module, "pynput": pynput}):
        spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("failure", ["keyboard_construct", "mouse_construct", "keyboard_start", "mouse_start"])
def test_failed_recording_start_preserves_events_and_stops_partial_listeners(failure):
    module = load_recorder_module()
    keyboard = mock.Mock()
    mouse = mock.Mock()
    module.keyboard.Listener = mock.Mock(return_value=keyboard)
    module.mouse.Listener = mock.Mock(return_value=mouse)
    boundary = {
        "keyboard_construct": module.keyboard.Listener,
        "mouse_construct": module.mouse.Listener,
        "keyboard_start": keyboard.start,
        "mouse_start": mouse.start,
    }[failure]
    boundary.side_effect = RuntimeError("listener unavailable")
    with mock.patch.object(module.tk, "BooleanVar", return_value=mock.Mock()):
        recorder = module.MacroRecorder()
    previous = module.MacroEvent(0.1, "key_press", {"key": "a"})
    recorder.events.append(previous)
    with pytest.raises(RuntimeError, match="listener unavailable"):
        recorder.start()
    assert not recorder.is_recording
    assert recorder.snapshot() == [previous]
    if keyboard.start.called:
        keyboard.stop.assert_called_once()
    if mouse.start.called:
        mouse.stop.assert_called_once()
    boundary.side_effect = None
    recorder.start()
    assert recorder.is_recording
    assert recorder.snapshot() == []
    recorder.stop()


def test_second_recording_replaces_previous_rows():
    module = load_recorder_module()
    root = tk.Tk()
    root.withdraw()
    try:
        tree = module.ttk.Treeview(root, columns=("t", "type", "data"))
        tree.insert("", "end", values=("old", "old", "old"))
        tree.insert("", "end", values=("old", "old", "old"))
        events = []
        recorder = types.SimpleNamespace(is_recording=False, start=lambda: events.clear(), snapshot=lambda: events)
        app = types.SimpleNamespace(recorder=recorder, tree=tree, record_btn=mock.Mock(), status_var=tk.StringVar(root), count_var=tk.StringVar(root), after=mock.Mock(), _tick=mock.Mock())
        module.MacroRecorderApp._toggle_recording(app)
        events.append(module.MacroEvent(0.0, "key_press", {"key": "new"}))
        module.MacroRecorderApp._tick(app)
        rows = tree.get_children()
        assert len(rows) == 1
        assert "new" in tree.item(rows[0], "values")[2]
    finally:
        root.destroy()


@pytest.mark.parametrize("kind,release,collection", [("a", "key_up", "pressed_keys"), ("left", "mouse_up", "pressed_mouse_buttons")])
def test_failed_input_release_remains_tracked_until_retry_succeeds(kind, release, collection):
    module = load_loop_module()
    state = module.RuntimeState()
    getattr(state, collection).add(kind)
    physical_down = {kind}
    attempts = []

    def release_input(value, delay):
        attempts.append(value)
        if len(attempts) == 1:
            raise OSError("transient driver failure")
        physical_down.remove(value)

    setattr(module.interception, release, release_input)
    module.safe_release_all(state)
    assert getattr(state, collection) == {kind}
    module.safe_release_all(state)
    assert not physical_down
    assert not getattr(state, collection)


@pytest.mark.parametrize("kind", ["keyboard", "mouse"])
def test_hotkey_listener_failure_requests_process_exit(kind):
    module = load_loop_module()
    state = module.RuntimeState()
    if kind == "keyboard":
        module.pynput_keyboard = types.SimpleNamespace(GlobalHotKeys=mock.Mock(side_effect=ValueError("invalid hotkey")))
        worker = module.keyboard_hotkey_listener_worker
        hotkey = "not-a-key"
    else:
        module.pynput_mouse = None
        worker = module.pynput_hotkey_listener_worker
        hotkey = "x1"
    worker(state, hotkey)
    assert state.quit.is_set()


def test_player_main_exits_nonzero_when_hotkey_cannot_start(tmp_path):
    module = load_loop_module()
    path = tmp_path / "macro.json"
    path.write_text('{"events":[{"t":0,"type":"key_press","data":{"key":"a"}}]}', encoding="utf-8")
    module.pynput_keyboard = types.SimpleNamespace(GlobalHotKeys=mock.Mock(side_effect=ValueError("invalid hotkey")))
    module.interception.auto_capture_devices = mock.Mock()

    def inline_thread(*, target, args=(), daemon=False):
        return types.SimpleNamespace(start=lambda: target(*args), join=lambda timeout: None)

    with mock.patch.object(module, "configure_console_encoding"), mock.patch.object(module, "playback_worker"), mock.patch.object(module.threading, "Thread", side_effect=inline_thread), mock.patch.object(module.time, "sleep", side_effect=AssertionError("main should exit without waiting")), mock.patch.object(sys, "argv", ["player", str(path), "--hotkey", "not-a-key"]):
        assert module.main() != 0


@pytest.fixture
def mic_app():
    module = load_mic_toggle_module()
    callbacks = []
    launches = []
    root = mock.Mock()
    root.after.side_effect = lambda delay, callback: callbacks.append((delay, callback))
    config = dict(module.DEFAULT_CONFIG)
    config["target_endpoint_id"] = "first-device"
    with mock.patch.object(module, "load_config", side_effect=lambda: dict(config)), mock.patch.object(module, "StatusOverlay"), mock.patch.object(module.MicToggleApp, "_build_menu"), mock.patch.object(module.MicToggleApp, "poll_state"), mock.patch.object(module.MicToggleApp, "poll_hotkey"):
        app = module.MicToggleApp(root)
    app.refresh_menu = mock.Mock()

    def start_thread(*, target, args=(), daemon=False):
        return types.SimpleNamespace(start=lambda: launches.append(lambda: target(*args)))

    def flush_ui():
        pending = callbacks[:]
        callbacks.clear()
        for delay, callback in pending:
            if delay == 0:
                callback()

    with mock.patch.object(module, "load_config", side_effect=lambda: dict(config)), mock.patch.object(module.threading, "Thread", side_effect=start_thread):
        yield module, app, config, launches, flush_ui


def test_mic_poll_does_not_accumulate_unfinished_queries(mic_app):
    module, app, config, launches, flush_ui = mic_app
    for _ in range(10):
        app.poll_state()
    assert len(launches) == 1
    with mock.patch.object(module, "get_mic_status", side_effect=OSError("device offline")):
        launches.pop(0)()
    flush_ui()
    app.poll_state()
    assert len(launches) == 1


def test_mic_old_device_result_cannot_replace_current_device(mic_app):
    module, app, config, launches, flush_ui = mic_app
    app.poll_state()
    config["target_endpoint_id"] = "second-device"
    app.poll_state()
    with mock.patch.object(module, "get_mic_status", return_value=(False, "Old microphone", "first-device")):
        launches[0]()
    flush_ui()
    app.overlay.set_state.assert_not_called()


def test_mic_old_poll_cannot_undo_displayed_toggle(mic_app):
    module, app, config, launches, flush_ui = mic_app
    app.poll_state()
    app.on_toggle()
    with mock.patch.object(module, "toggle_mic_status", return_value=(True, "Microphone", "first-device")), mock.patch.object(module, "get_mic_status", return_value=(False, "Microphone", "first-device")):
        launches[1]()
        flush_ui()
        launches[0]()
        flush_ui()
    assert app.overlay.set_state.call_args.args == (True, "Microphone")


def test_mic_result_after_quit_does_not_update_destroyed_window(mic_app):
    module, app, config, launches, flush_ui = mic_app
    app.poll_state()
    app.quit()
    with mock.patch.object(module, "get_mic_status", return_value=(False, "Microphone", "first-device")):
        launches[0]()
    flush_ui()
    app.overlay.set_state.assert_not_called()
