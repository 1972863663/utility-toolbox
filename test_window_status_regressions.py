from unittest import mock
import pytest
import utility_toolbox as t


@pytest.mark.parametrize('operation', ['pin_current', 'unpin_current'])
def test_current_window_failure_does_not_report_success(operation):
    controller = object.__new__(t.TopmostController)
    controller.last_hwnd = 321
    controller.valid_target = lambda _hwnd: True
    controller.set_topmost = lambda _hwnd, _enabled: False
    controller.window_title = lambda _hwnd: 'fixture'
    messages = []
    controller.status_callback = messages.append
    with mock.patch.object(t.win32gui, 'IsWindow', return_value=True), mock.patch.object(t, 'log_line'):
        getattr(controller, operation)()
    assert messages and '失败' in messages[-1]


def test_unpin_all_keeps_failed_windows_for_retry():
    controller = object.__new__(t.TopmostController)
    controller.tracked = {321}
    controller.set_topmost = lambda _hwnd, _enabled: False
    messages = []
    controller.status_callback = messages.append
    with mock.patch.object(t.win32gui, 'IsWindow', return_value=True):
        controller.unpin_all()
    assert controller.tracked == {321}
    assert '失败' in messages[-1]
