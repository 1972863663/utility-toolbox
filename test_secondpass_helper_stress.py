"""Second-pass fault injection, without real hooks, input, drivers or audio writes."""

import json
import types
from pathlib import Path
from unittest import mock

import pytest

import game_save_manager_launcher as gsm
from test_helper_safety_regressions import load_recorder_module
from test_macro_and_media_regressions import load_loop_module
from test_system_tools import load_mic_toggle_module


def recorder_without_hooks():
    module = load_recorder_module()
    with mock.patch.object(module.tk, "BooleanVar", return_value=mock.Mock(get=lambda: False)):
        recorder = module.MacroRecorder()
    return module, recorder


def test_recorder_stop_cleans_other_hook_when_first_hook_stop_fails():
    _module, recorder = recorder_without_hooks()
    keyboard, mouse = mock.Mock(), mock.Mock()
    keyboard.stop.side_effect = [OSError("hook cleanup failed"), None]
    recorder.keyboard_listener = keyboard
    recorder.mouse_listener = mouse
    recorder.is_recording = True

    with pytest.raises(OSError, match="hook cleanup failed"):
        recorder.stop()

    mouse.stop.assert_called_once()
    assert not recorder.is_recording
    recorder.stop()
    assert keyboard.stop.call_count == 2


def test_recorder_export_failure_preserves_previous_recording(tmp_path):
    module, recorder = recorder_without_hooks()
    path = tmp_path / "macro.json"
    original = b'{"events":[{"old":"valuable recording"}]}'
    path.write_bytes(original)

    def interrupted_dump(payload, stream, **kwargs):
        stream.write('{"partial":')
        raise OSError("disk full")

    with mock.patch.object(module.json, "dump", side_effect=interrupted_dump):
        with pytest.raises(OSError, match="disk full"):
            recorder.export_json(str(path), "<ctrl>+r")
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_old_recorder_hook_callback_is_ignored_after_restart():
    module, recorder = recorder_without_hooks()
    keyboards = []

    def keyboard_listener(**callbacks):
        listener = types.SimpleNamespace(start=lambda: None, stop=lambda: None, **callbacks)
        keyboards.append(listener)
        return listener

    module.keyboard.Listener = keyboard_listener
    module.mouse.Listener = lambda **callbacks: types.SimpleNamespace(start=lambda: None, stop=lambda: None, **callbacks)
    recorder.start()
    recorder.stop()
    recorder.start()
    keyboards[0].on_press(types.SimpleNamespace(char="old"))
    keyboards[1].on_press(types.SimpleNamespace(char="new"))

    assert [event.data["key"] for event in recorder.snapshot()] == ["new"]


def test_recorder_mouse_callback_does_not_hold_state_lock_while_reading_tk():
    module, recorder = recorder_without_hooks()
    recorder.is_recording = True

    def read_variable():
        assert not recorder.lock._is_owned(), "Tk may wait for the UI thread which also needs this lock"
        return True

    recorder.record_mouse_moves.get = read_variable
    callback = recorder._session_callback(recorder._generation, recorder._on_mouse_move)
    callback(1, 2)
    assert [event.type for event in recorder.snapshot()] == ["mouse_move"]


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), -float("inf")])
def test_macro_rejects_nonfinite_event_timestamps(tmp_path, timestamp):
    module = load_loop_module()
    path = tmp_path / "macro.json"
    path.write_text(json.dumps({"events": [{"t": timestamp, "type": "key_press", "data": {"key": "a"}}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_macro(path, "x1", 0.1)


def test_macro_string_false_must_not_turn_a_release_into_a_press(tmp_path):
    module = load_loop_module()
    path = tmp_path / "macro.json"
    path.write_text(json.dumps({"events": [{"t": 0, "type": "mouse_click", "data": {"button": "left", "pressed": "false", "x": 1, "y": 2}}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_macro(path, "x1", 0.1)


@pytest.mark.parametrize("payload", [None, [], {"events": [{"t": "broken", "type": "key_press", "data": {"key": "a"}}]}, {"events": [{"t": 0, "type": "mouse_move", "data": {"x": "bad", "y": 1}}]}])
def test_macro_malformed_payload_is_a_validation_error(tmp_path, payload):
    module = load_loop_module()
    path = tmp_path / "macro.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_macro(path, "x1", 0.1)


def test_listener_backend_exit_is_reported_without_hanging_main():
    module = load_loop_module()
    state = module.RuntimeState()
    listener = types.SimpleNamespace(start=lambda: None, stop=lambda: None, join=lambda: None, is_alive=lambda: False, running=False)
    module.pynput_keyboard = types.SimpleNamespace(GlobalHotKeys=lambda mapping: listener)
    # Bound the current broken loop without real sleeping or a real listener.
    with mock.patch.object(module.time, "sleep", side_effect=lambda seconds: state.quit.set()):
        module.keyboard_hotkey_listener_worker(state, "<ctrl>+r")
    assert state.error is not None


def mic_without_ui():
    module = load_mic_toggle_module()
    with mock.patch.object(module, "load_config", return_value=dict(module.DEFAULT_CONFIG)), mock.patch.object(module, "StatusOverlay"), mock.patch.object(module.MicToggleApp, "_build_menu"), mock.patch.object(module.MicToggleApp, "poll_state"), mock.patch.object(module.MicToggleApp, "poll_hotkey"):
        app = module.MicToggleApp(mock.Mock())
    return module, app


def test_queued_mic_toggle_does_not_touch_audio_after_quit():
    module, app = mic_without_ui()
    queued = []

    def thread(*, target, args=(), **kwargs):
        return types.SimpleNamespace(start=lambda: queued.append(lambda: target(*args)))

    with mock.patch.object(module.threading, "Thread", side_effect=thread), mock.patch.object(module, "toggle_mic_status", return_value=(True, "fake device", "fake-id")) as audio:
        app.on_toggle()
        app.quit()
        queued.pop()()
    audio.assert_not_called()
    assert not app.busy.locked()


def test_mic_rapid_clicks_start_only_one_audio_operation():
    module, app = mic_without_ui()
    queued = []

    def thread(*, target, args=(), **kwargs):
        return types.SimpleNamespace(start=lambda: queued.append(lambda: target(*args)))

    with mock.patch.object(module.threading, "Thread", side_effect=thread):
        for _ in range(10000):
            app.on_toggle()
    assert len(queued) == 1


@pytest.mark.parametrize("codes", [[True], [-1], [256], [2 ** 100]])
def test_mic_invalid_virtual_key_codes_are_not_accepted(tmp_path, codes):
    module = load_mic_toggle_module()
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey_codes": codes}), encoding="utf-8")
    with mock.patch.object(module, "CONFIG_PATH", path):
        loaded = module.load_config()
    assert all(type(code) is int and 0 < code < 256 for code in loaded["hotkey_codes"])


def test_mic_invalid_hotkey_token_does_not_enable_partial_shortcut(tmp_path):
    module = load_mic_toggle_module()
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey_codes": None, "hotkey": "Ctrl+NotAKey"}), encoding="utf-8")
    with mock.patch.object(module, "CONFIG_PATH", path):
        loaded = module.load_config()
    assert loaded["hotkey_codes"] != [17]


@pytest.mark.parametrize("failure_stage", ["first-marker", "settings", "final-marker"])
def test_gsm_failed_marker_update_does_not_break_next_portable_move(tmp_path, failure_stage):
    original, moved, moved_again = (tmp_path / name for name in ("original", "moved", "moved-again"))
    gsm.prepare_profile(original)
    original.rename(moved)
    replace = gsm.os.replace

    marker_writes = 0

    def fail_marker(source, destination):
        nonlocal marker_writes
        if destination.name == "toolbox-root.json":
            marker_writes += 1
            if (failure_stage == "first-marker" and marker_writes == 1) or (failure_stage == "final-marker" and marker_writes == 2):
                raise OSError("profile temporarily locked")
        elif failure_stage == "settings" and destination.name == "settings.json":
            raise OSError("profile temporarily locked")
        replace(source, destination)

    with mock.patch.object(gsm.os, "replace", side_effect=fail_marker):
        with pytest.raises(OSError, match="profile temporarily locked"):
            gsm.prepare_profile(moved)
    moved.rename(moved_again)
    profile = gsm.prepare_profile(moved_again)
    settings = json.loads((profile / "GSM Settings/settings.json").read_text(encoding="utf-8"))
    assert settings["backupPath"] == str(moved_again / "data/GSM Backups")
    assert settings["exportPath"] == str(moved_again / "data/GSM Exports")


def test_gsm_explicit_external_path_with_dotdot_is_not_rebased(tmp_path):
    original = tmp_path / "original"
    moved = tmp_path / "moved"
    profile = gsm.prepare_profile(original)
    settings_path = profile / "GSM Settings/settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    external = str(original / ".." / "external-backups")
    settings["backupPath"] = external
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    original.rename(moved)
    profile = gsm.prepare_profile(moved)
    saved = json.loads((profile / "GSM Settings/settings.json").read_text(encoding="utf-8"))
    assert saved["backupPath"] == external


def test_gsm_internal_junction_path_keeps_its_portable_location(tmp_path):
    original, moved = tmp_path / "original", tmp_path / "moved"
    profile = gsm.prepare_profile(original)
    settings_path = profile / "GSM Settings/settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    link_path = original / "data" / "linked-backups"
    settings["backupPath"] = str(link_path)
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    original.rename(moved)
    resolve = Path.resolve

    def resolve_junction(path, *args, **kwargs):
        if path == link_path:
            return tmp_path / "external-junction-target"
        return resolve(path, *args, **kwargs)

    # Model an NTFS junction without creating one or requiring elevated rights.
    with mock.patch.object(Path, "resolve", resolve_junction):
        profile = gsm.prepare_profile(moved)
    saved = json.loads((profile / "GSM Settings/settings.json").read_text(encoding="utf-8"))
    assert saved["backupPath"] == str(moved / "data" / "linked-backups")
