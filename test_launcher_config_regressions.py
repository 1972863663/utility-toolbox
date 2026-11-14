import json
from types import SimpleNamespace
from unittest import mock

import pytest
import utility_toolbox as t


def launcher(tmp_path):
    app = object.__new__(t.LauncherTab)
    app.parent = mock.Mock()
    app.config_file = tmp_path / 'launcher.json'
    app.groups = {'默认组': {'apps': []}}
    app.current_group = '默认组'
    app.refresh_groups = mock.Mock()
    app.refresh_apps = mock.Mock()
    app.status_callback = mock.Mock()
    return app


@pytest.mark.parametrize('raw', ['{broken', 'null', '[]', '{"groups":{"bad":null}}', '{"groups":{"bad":{"apps":[null]}}}'])
def test_invalid_launcher_config_is_preserved_on_save(tmp_path, raw):
    app = launcher(tmp_path)
    app.config_file.write_text(raw, encoding='utf-8')
    with mock.patch.object(t.messagebox, 'showerror'):
        app.load_config()
        app.save_config()
    assert app.config_file.read_text(encoding='utf-8') == raw


def test_launcher_write_failure_keeps_original_bytes(tmp_path, monkeypatch):
    app = launcher(tmp_path)
    original = '{"groups":{"默认组":{"apps":[]}}, "extra":42}'
    app.config_file.write_text(original, encoding='utf-8')
    app.load_config()
    app.groups['new'] = {'apps': []}
    monkeypatch.setattr(t.os, 'replace', mock.Mock(side_effect=OSError('disk failure')))
    with mock.patch.object(t.messagebox, 'showerror'):
        app.save_config()
    assert app.config_file.read_text(encoding='utf-8') == original


def test_launcher_preserves_unknown_metadata_and_current_group_fallback(tmp_path):
    app = launcher(tmp_path)
    app.config_file.write_text(json.dumps({'groups': {'actual': {'apps': []}}, 'current_group':'missing', 'extra': 42}), encoding='utf-8')
    app.load_config()
    assert app.current_group == 'actual'
    app.save_config()
    assert json.loads(app.config_file.read_text(encoding='utf-8'))['extra'] == 42


def test_launcher_does_not_overwrite_external_config_change(tmp_path):
    app = launcher(tmp_path)
    app.config_file.write_text('{"groups": {}}', encoding='utf-8')
    app.load_config()
    changed = '{"groups":{"external":{"apps":[]}}}'
    app.config_file.write_text(changed, encoding='utf-8')
    with mock.patch.object(t.messagebox, 'showerror'):
        app.save_config()
    assert app.config_file.read_text(encoding='utf-8') == changed


@pytest.mark.parametrize('name', ['  other  ', '   '])
def test_rename_group_strips_before_collision_check(tmp_path, name):
    app = launcher(tmp_path)
    app.groups['other'] = {'apps': [{'name': 'keep', 'path': r'C:\keep.exe', 'type':'app'}]}
    before = json.loads(json.dumps(app.groups))
    with mock.patch.object(t.simpledialog, 'askstring', return_value=name):
        app.rename_group()
    assert app.groups == before
    assert app.current_group == '默认组'


def test_remove_selected_removes_only_selected_row_not_same_name(tmp_path):
    app = launcher(tmp_path)
    app.groups['默认组']['apps'] = [
        {'name':'app.exe','path':r'C:\one\app.exe','type':'app'},
        {'name':'app.exe','path':r'C:\two\app.exe','type':'app'},
    ]
    app.app_tree = SimpleNamespace(selection=lambda:('row1',), index=lambda _row:0,
        item=lambda _row, _option:('app.exe','app',r'C:\one\app.exe'))
    app.remove_selected()
    assert [a['path'] for a in app.groups['默认组']['apps']] == [r'C:\two\app.exe']


def test_missing_macro_program_does_not_report_started(tmp_path):
    tab = object.__new__(t.MacroToolsTab)
    tab.macro_tools_dir = lambda: tmp_path
    tab.status_callback = mock.Mock()
    with mock.patch.object(t.messagebox, 'showerror') as error:
        tab.launch_recorder()
    assert error.called
    assert not tab.status_callback.called


def test_generated_macro_bat_preserves_chinese_paths(tmp_path, monkeypatch):
    desktop = tmp_path / 'Desktop'
    desktop.mkdir()
    tab = object.__new__(t.MacroToolsTab)
    folder = tmp_path / '工具箱' / 'macro_tools'
    folder.mkdir(parents=True)
    (folder / 'interception_loop_macro.exe').write_bytes(b'test')
    tab.macro_tools_dir = lambda: folder
    tab.macro_json_var = SimpleNamespace(get=lambda:str(tmp_path/'录制.json'))
    tab.loop_settings = lambda: ('mouse:x1', 0.1)
    tab.status_callback = mock.Mock()
    monkeypatch.setattr(t.Path, 'home', lambda: tmp_path)
    with mock.patch.object(t.messagebox, 'showinfo'):
        tab.create_desktop_loop_bat()
    content = (desktop/'run_interception_macro.bat').read_text(encoding='utf-8')
    assert str(folder / 'interception_loop_macro.exe') in content
    assert str(tmp_path/'录制.json') in content
