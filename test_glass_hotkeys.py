from unittest.mock import Mock

import pytest

from glass_toolbox.hotkeys import SummonHotkey, parse_hotkey


class FakeAPI:
    def __init__(self):
        self.bindings = {}
        self.fail_key = None

    def register(self, hwnd, identifier, modifiers, key):
        if key == self.fail_key or (modifiers, key) in self.bindings.values():
            raise ValueError("快捷键已被占用")
        self.bindings[identifier] = (modifiers, key)

    def unregister(self, hwnd, identifier):
        del self.bindings[identifier]


@pytest.mark.parametrize("text,mods,key", [("Ctrl+Alt+Space", 0x4003, 32),
                                           ("Ctrl+Shift+K", 0x4006, 75),
                                           ("Alt+F9", 0x4001, 120),
                                           ("F8", 0x4000, 119), ("A", 0x4000, 65),
                                           ("7", 0x4000, 55), ("Space", 0x4000, 32),
                                           ("Tab", 0x4000, 9), ("Shift+A", 0x4004, 65)])
def test_parse_combination(text, mods, key):
    assert parse_hotkey(text) == (text, mods, key)


@pytest.mark.parametrize("text", ["Ctrl", "Shift", "Alt", "F12", "Ctrl+F12", "Meta+K", "Ctrl+K, Ctrl+C", "nonsense"])
def test_rejects_incomplete_reserved_or_multiple_keys(text):
    with pytest.raises(ValueError):
        parse_hotkey(text)


def test_registration_conflict_keeps_previous_key_and_file():
    api, persist = FakeAPI(), Mock()
    hotkey = SummonHotkey(1, api)
    hotkey.change("Ctrl+Alt+Space")
    old = api.bindings.copy()
    api.fail_key = ord("K")
    with pytest.raises(ValueError):
        hotkey.change("Ctrl+Alt+K", persist)
    assert api.bindings == old
    assert hotkey.text == "Ctrl+Alt+Space"
    persist.assert_not_called()


def test_save_failure_releases_candidate_and_preserves_old_key():
    api = FakeAPI()
    hotkey = SummonHotkey(1, api)
    hotkey.change("Ctrl+Alt+Space")
    old = api.bindings.copy()
    with pytest.raises(OSError):
        hotkey.change("Ctrl+Alt+K", Mock(side_effect=OSError("disk full")))
    assert api.bindings == old
    assert hotkey.text == "Ctrl+Alt+Space"


def test_replace_disable_and_close_release_registrations():
    api, persist = FakeAPI(), Mock()
    hotkey = SummonHotkey(1, api)
    hotkey.change("Ctrl+Alt+Space")
    old_id = hotkey.identifier
    hotkey.change("Ctrl+Alt+K", persist)
    assert hotkey.identifier != old_id and old_id not in api.bindings
    persist.assert_called_once_with("Ctrl+Alt+K")
    hotkey.change("")
    assert not api.bindings and hotkey.identifier is None
    hotkey.change("Ctrl+Alt+Space")
    hotkey.close()
    assert not api.bindings


def test_same_key_does_not_double_register():
    api = FakeAPI()
    hotkey = SummonHotkey(1, api)
    hotkey.change("Ctrl+Alt+Space")
    identifier = hotkey.identifier
    hotkey.change("Ctrl+Alt+Space")
    assert list(api.bindings) == [identifier]


def test_single_key_and_combination_can_replace_each_other():
    api, persist = FakeAPI(), Mock()
    hotkey = SummonHotkey(1, api)
    for text in ("F8", "Ctrl+Alt+Space", "A"):
        hotkey.change(text, persist)
        assert hotkey.text == text
        assert len(api.bindings) == 1
        assert api.bindings[hotkey.identifier] == parse_hotkey(text)[1:]
