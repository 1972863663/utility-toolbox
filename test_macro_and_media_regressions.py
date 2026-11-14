import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image

import utility_toolbox as toolbox
from media_organizer.models import AppConfig
from media_organizer.scanner import scan_media
from test_system_tools import load_mic_toggle_module


def load_loop_module():
    spec = importlib.util.spec_from_file_location("isolated_macro_loop", Path(__file__).parent / "macro_tools" / "interception_loop_macro.py")
    module = importlib.util.module_from_spec(spec)
    # No driver or input hook is loaded; physical input is never emitted.
    pynput = types.ModuleType("pynput")
    pynput.mouse = types.SimpleNamespace()
    pynput.keyboard = types.SimpleNamespace()
    with mock.patch.dict(sys.modules, {"interception": types.ModuleType("interception"), "pynput": pynput}):
        spec.loader.exec_module(module)
    return module


def test_macro_worker_accepts_interval_passed_by_main_without_input():
    module = load_loop_module()
    state = module.RuntimeState()
    state.enabled.set()
    delays = []
    def sleep(seconds, runtime, **kwargs):
        delays.append(seconds)
        runtime.quit.set()
        return False
    with mock.patch.object(module, "safe_release_all"), mock.patch.object(module, "interruptible_sleep", side_effect=sleep):
        module.playback_worker([], state, "mouse:x1", 0.25)
    assert state.loop_count == 1
    assert delays == [0.25]


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_non_finite_macro_interval_is_rejected_by_ui_and_player(value):
    module = load_loop_module()
    obj = types.SimpleNamespace(loop_hotkey_var=types.SimpleNamespace(get=lambda: "mouse:x1"), loop_interval_var=types.SimpleNamespace(get=lambda: value))
    with pytest.raises(ValueError):
        module.parse_loop_interval(value)
    with pytest.raises(ValueError):
        toolbox.MacroToolsTab.loop_settings(obj)


def test_configured_loop_wait_longer_than_ten_seconds_is_honored_without_real_wait():
    module = load_loop_module()
    state = module.RuntimeState()
    state.enabled.set()
    # Time jumps past 10 s but stays below 30 s. At least one sleep must occur.
    with mock.patch.object(module.time, "perf_counter", side_effect=[0.0, 11.0, 11.0, 31.0]), mock.patch.object(module.time, "sleep") as sleep:
        module.interruptible_sleep(30.0, state, max_seconds=3600)
    sleep.assert_called_once()


def test_macro_sleep_does_not_pass_negative_time_to_system_sleep():
    module = load_loop_module()
    state = module.RuntimeState()
    state.enabled.set()
    with mock.patch.object(module.time, "perf_counter", side_effect=[0.0, 0.0999, 0.1001]), mock.patch.object(module.time, "sleep") as sleep:
        assert module.interruptible_sleep(0.1, state)
    sleep.assert_not_called()


def test_media_scan_does_not_reingest_its_output_subtree(tmp_path):
    source = tmp_path / "source"
    output = source / "organized"
    output.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(source / "photo.png")
    Image.new("RGB", (8, 8), "blue").save(output / "previous-copy.png")
    manifest = scan_media(AppConfig(str(source), str(output)))
    assert [Path(item.source_path).name for item in manifest.items] == ["photo.png"]


@pytest.mark.parametrize("same_directory", [True, False])
def test_media_scan_rejects_same_output_or_missing_source(tmp_path, same_directory):
    source = tmp_path / "source"
    if same_directory:
        source.mkdir()
    output = source if same_directory else tmp_path / "output"
    with pytest.raises((ValueError, FileNotFoundError)):
        scan_media(AppConfig(str(source), str(output)))


def test_mic_toggle_error_survives_after_worker_returns_without_touching_audio():
    module = load_mic_toggle_module()
    callbacks = []
    obj = types.SimpleNamespace(config={}, root=types.SimpleNamespace(after=lambda delay, callback: callbacks.append(callback)), busy=mock.Mock())
    cls = next(cls for cls in vars(module).values() if isinstance(cls, type) and "_toggle_worker" in cls.__dict__)
    with mock.patch.object(module, "toggle_mic_status", side_effect=RuntimeError("test microphone failure")), mock.patch.object(module.messagebox, "showerror") as showerror:
        cls._toggle_worker(obj)
        callbacks[0]()
    assert "test microphone failure" in showerror.call_args.args[1]
    obj.busy.release.assert_called_once()
