"""UI-only regressions. These tests never launch tools or alter Windows settings."""

import tkinter as tk
from tkinter import ttk

import pytest

from toolbox_ui import PAGES, THEME, ToolboxShell, setup_theme
from subtitle_sync_embedded import MainApp


@pytest.fixture
def window():
    root = tk.Tk()
    root.geometry("1100x800+20+20")
    setup_theme(root, 1.0)
    yield root
    root.update_idletasks()
    # Cancel UI layout callbacks before disposing the Tcl interpreter.
    for job in root.tk.splitlist(root.tk.call("after", "info")):
        root.after_cancel(job)
    root.destroy()


def test_navigation_preserves_form_and_command(window):
    shell = ToolboxShell(window, 1, tk.StringVar(window, "就绪"))
    value = tk.StringVar(window, "用户输入")
    entry = ttk.Entry(shell.page("renamer"), textvariable=value)
    entry.pack()
    calls = []
    button = ttk.Button(shell.page("renamer"), command=lambda: calls.append(value.get()))
    button.pack()
    shell.finish()
    for key, *_ in PAGES:
        shell.nav_buttons[key].invoke()
        window.update()
        assert shell.pages[key].winfo_ismapped()
        assert sum(page.winfo_ismapped() for page in shell.pages.values()) == 1
    shell.select("renamer")
    button.invoke()
    assert entry.get() == "用户输入"
    assert calls == ["用户输入"]


def test_search_no_results_recovers_and_enter_navigates(window):
    shell = ToolboxShell(window, 1, tk.StringVar(window))
    shell.finish()
    shell.search_var.set("not-a-tool")
    window.update()
    assert not shell.visible_keys
    assert shell.empty_label.winfo_ismapped()
    shell.search_var.set("AUDIO")
    assert shell.visible_keys == ["audio"]
    shell._select_search_result()
    assert shell.active_key == "audio"
    assert shell.search_var.get() == ""
    assert len(shell.visible_keys) == 11


@pytest.mark.parametrize("scale", [1.0, 1.5])
def test_large_form_remains_accessible_by_scrolling(window, scale):
    shell = ToolboxShell(window, scale, tk.StringVar(window))
    ttk.Frame(shell.page("system"), width=1500, height=1600).pack()
    shell.finish()
    shell.select("system")
    window.update()
    page = shell.pages["system"]
    page.canvas.xview_moveto(1)
    page.canvas.yview_moveto(1)
    window.update()
    assert page.canvas.xview()[0] > 0
    assert page.canvas.yview()[0] > 0
    assert page.canvas.xview()[1] == 1
    assert page.canvas.yview()[1] == 1


def test_embedded_subtitle_focus_keeps_dark_theme(window, monkeypatch):
    monkeypatch.setattr(MainApp, "_load_history_db", lambda self: None)
    shell = ToolboxShell(window, 1, tk.StringVar(window))
    app = MainApp(shell.page("subtitle"), embedded=True)
    shell.finish()
    shell.select("subtitle")
    window.update()
    app._on_focus_in(None)
    assert app.name_ent.cget("fg") == THEME["text"]
    assert app.name_ent.cget("bg") == THEME["panel"]
    app._on_focus_out(None)
    assert app.name_ent.cget("fg") == THEME["muted"]
    assert ttk.Style(window).lookup("App.TButton", "background") == THEME["panel_alt"]
    assert ttk.Style(window).lookup("TCombobox", "fieldbackground", ["readonly"]) == THEME["panel_alt"]
