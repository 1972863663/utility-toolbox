from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest
import utility_toolbox as t


def launcher():
    app=object.__new__(t.LauncherTab)
    app.parent=SimpleNamespace(after=lambda _delay, callback:callback())
    app.log=Mock()
    app.save_config=Mock()
    app.refresh_apps=Mock()
    return app


def test_failed_path_repair_does_not_abort_rest_of_launch_group(tmp_path, monkeypatch):
    app=launcher()
    good=tmp_path/'good.exe'
    good.write_bytes(b'fixture')
    def ensure(item, max_seconds):
        if item['path']=='bad':
            raise OSError('controlled path failure')
        return item['path']
    app._ensure_launch_path=ensure
    started=[]
    monkeypatch.setattr(t.os,'startfile',started.append)
    app._launch_worker([{'path':'bad'},{'path':str(good)}])
    assert started==[str(good)]
    assert any('controlled path failure' in call.args[0] for call in app.log.call_args_list)


@pytest.mark.parametrize('content', [b'[InternetShortcut]\nIconFile=foo.ico', b'[InternetShortcut]\nURL='])
def test_invalid_internet_shortcut_does_not_claim_started(content,tmp_path,monkeypatch):
    app=launcher()
    path=tmp_path/'invalid.url'
    path.write_bytes(content)
    opened=Mock(return_value=True)
    monkeypatch.setattr(t.webbrowser,'open',opened)
    app._launch_worker([{'path':str(path)}])
    assert not opened.called
    assert any('失败' in call.args[0] for call in app.log.call_args_list)
    assert not any('已启动' in call.args[0] for call in app.log.call_args_list)


def test_utf16_internet_shortcut_opens_correct_url(tmp_path,monkeypatch):
    app=launcher()
    path=tmp_path/'unicode.url'
    path.write_text('[InternetShortcut]\nURL=https://example.invalid/test',encoding='utf-16')
    opened=[]
    monkeypatch.setattr(t.webbrowser,'open',lambda url:opened.append(url) or True)
    app._launch_worker([{'path':str(path)}])
    assert opened==['https://example.invalid/test']


def test_browser_refusal_is_not_success(tmp_path,monkeypatch):
    app=launcher()
    path=tmp_path/'test.url'
    path.write_text('[InternetShortcut]\nURL=https://example.invalid/',encoding='utf-8')
    monkeypatch.setattr(t.webbrowser,'open',lambda _url:False)
    app._launch_worker([{'path':str(path)}])
    assert not any('已启动' in call.args[0] for call in app.log.call_args_list)
    assert any('失败' in call.args[0] for call in app.log.call_args_list)


def test_rapid_launch_clicks_only_schedule_one_batch(monkeypatch):
    app=launcher()
    queued=[]
    class Thread:
        def __init__(self,target,args=(),**_):
            self.target,self.args=target,args
        def start(self):
            queued.append(self)
    monkeypatch.setattr(t.threading,'Thread',Thread)
    for _ in range(1000):
        app.launch_apps([{'path':'example.exe'}])
    assert len(queued)==1


def test_launch_batch_is_snapshotted_at_click_time(monkeypatch):
    app=launcher()
    pending=[]
    received=[]
    app._launch_worker=lambda entries:received.extend(entry['path'] for entry in entries)
    class Thread:
        def __init__(self,target,**_):
            self.target=target
        def start(self):
            pending.append(self.target)
    monkeypatch.setattr(t.threading,'Thread',Thread)
    apps=[{'path':'chosen.exe'}]
    app.launch_apps(apps)
    apps.append({'path':'added-later.exe'})
    pending[0]()
    assert received==['chosen.exe']


def test_live_shortcut_does_not_inject_keys_when_browser_rejects(monkeypatch):
    app=SimpleNamespace(parent=SimpleNamespace(after=lambda _delay,callback:callback()),status_callback=Mock())
    monkeypatch.setattr(t.webbrowser,'open',lambda _url:False)
    monkeypatch.setattr(t.time,'sleep',lambda _seconds:None)
    # A failed URL must stop before imports/input; substitute modules to avoid real input.
    import sys
    keyboard=SimpleNamespace(press_and_release=Mock())
    mouse=SimpleNamespace(click=Mock())
    monkeypatch.setitem(sys.modules,'keyboard',keyboard)
    monkeypatch.setitem(sys.modules,'pyautogui',mouse)
    monkeypatch.setattr(t.messagebox,'showerror',Mock())
    t.LiveShortcutTab._open_worker(app,'fixture','https://example.invalid','chenze')
    assert not keyboard.press_and_release.called
    assert not mouse.click.called
    assert not app.status_callback.called


@pytest.mark.parametrize("stop_while_waiting", [False, True])
@pytest.mark.parametrize("close_kind", ["launcher", "drop"])
def test_close_cancels_unstarted_apps_and_waits_without_blocking_ui(monkeypatch, stop_while_waiting, close_kind):
    app = launcher()
    real_thread = threading.Thread
    queued, started, snapshots = [], [], []
    entered, release = threading.Event(), threading.Event()
    first, second = r"C:\Fixture\first.exe", r"C:\Fixture\second.exe"
    app._ensure_launch_path = lambda item, max_seconds: item["path"]
    app._close_worker = lambda paths: snapshots.append(list(started))
    close_paths = app.close_paths
    if close_kind == "drop":
        drop = object.__new__(t.DropCloseController)
        drop._close_coordinator = app._queue_launcher_close
        drop._close_worker = app._close_worker
        close_paths = drop.close_paths

    class DeferredThread:
        def __init__(self, target, args=(), **kwargs):
            self.job = lambda: target(*args)
        def start(self):
            queued.append(self.job)

    def startfile(path):
        started.append(path)
        if path == first:
            entered.set()
            assert release.wait(3), "test must release the simulated launch"

    monkeypatch.setattr(t.threading, "Thread", DeferredThread)
    monkeypatch.setattr(t.os, "startfile", startfile)
    app.launch_apps([{"path": first}, {"path": second}])
    launch = real_thread(target=queued.pop(0), daemon=True)
    launch.start()
    close = None
    try:
        assert entered.wait(2)
        close_paths([first, second])
        close = real_thread(target=queued.pop(0), daemon=True)
        close.start()
        # Close must wait in the background while the UI remains able to reject new work.
        close.join(0.05)
        assert snapshots == []
        app.launch_apps([{"path": second}])
        assert not queued
        assert any("关闭" in call.args[0] for call in app.log.call_args_list)
        if stop_while_waiting:
            app.stop()
            close.join(1)
            assert not close.is_alive()
            assert snapshots == []
    finally:
        release.set()
        launch.join(2)
        if close is not None:
            close.join(2)
    assert not launch.is_alive() and not close.is_alive()
    assert started == [first]
    assert snapshots == ([] if stop_while_waiting else [[first]])


def test_repair_queued_before_stop_does_not_search_or_change_paths(monkeypatch):
    app = launcher()
    item = {"path": r"C:\Missing\original.exe"}
    app.current_group = "group"
    app.groups = {"group": {"apps": [item]}}
    app._find_relocated_path = Mock(return_value=r"C:\New\original.exe")
    queued = []
    monkeypatch.setattr(t.threading, "Thread", lambda target, **kwargs: SimpleNamespace(start=lambda: queued.append(target)))
    app.repair_current_group()
    app.stop()
    queued.pop()()
    app._find_relocated_path.assert_not_called()
    assert item["path"] == r"C:\Missing\original.exe"
    app.save_config.assert_not_called()


@pytest.mark.parametrize("operation", ["launch", "repair"])
def test_close_during_path_search_does_not_commit_the_cancelled_repair(monkeypatch, operation):
    app = launcher()
    item = {"path": r"C:\Missing\original.exe"}
    app.current_group = "group"
    app.groups = {"group": {"apps": [item]}}
    app._close_worker = Mock()
    queued = []
    monkeypatch.setattr(t.threading, "Thread", lambda target, **kwargs: SimpleNamespace(start=lambda: queued.append(target)))
    monkeypatch.setattr(t.os, "startfile", Mock())

    def find(*args, **kwargs):
        app.close_paths([item["path"]])
        return r"C:\New\original.exe"

    app._find_relocated_path = find
    if operation == "launch":
        app.launch_apps([item])
    else:
        app.repair_current_group()
    queued.pop(0)()
    assert item["path"] == r"C:\Missing\original.exe"
    queued.pop(0)()
    app._close_worker.assert_called_once()
    app.save_config.assert_not_called()
    t.os.startfile.assert_not_called()


def test_close_thread_start_failure_releases_pending_state(monkeypatch):
    app = launcher()
    app._launch_worker = Mock()
    monkeypatch.setattr(t.threading, "Thread", lambda **kwargs: SimpleNamespace(start=Mock(side_effect=RuntimeError("thread unavailable"))))
    app.close_paths([r"C:\Fixture\first.exe"])
    queued = []
    monkeypatch.setattr(t.threading, "Thread", lambda target, **kwargs: SimpleNamespace(start=lambda: queued.append(target)))
    app.launch_apps([{"path": r"C:\Fixture\second.exe"}])
    assert len(queued) == 1
    assert any("thread unavailable" in call.args[0] for call in app.log.call_args_list)


def test_launch_thread_start_failure_does_not_leave_close_waiting(monkeypatch):
    app = launcher()
    app._close_worker = Mock()
    monkeypatch.setattr(t.threading, "Thread", lambda **kwargs: SimpleNamespace(start=Mock(side_effect=RuntimeError("thread unavailable"))))
    app.launch_apps([{"path": r"C:\Fixture\first.exe"}])
    queued = []
    monkeypatch.setattr(t.threading, "Thread", lambda target, **kwargs: SimpleNamespace(start=lambda: queued.append(target)))
    app.close_paths([r"C:\Fixture\first.exe"])
    queued.pop()()
    app._close_worker.assert_called_once()


def test_stopped_drop_does_not_delegate_or_cancel_a_launcher_operation():
    drop = object.__new__(t.DropCloseController)
    drop._close_coordinator = Mock()
    drop.stop()
    drop.close_paths([r"C:\Fixture\first.exe"])
    drop._close_coordinator.assert_not_called()
