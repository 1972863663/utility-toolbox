"""Tkinter presentation layer inspired by Neverlose Reloaded's desktop layout.

No system operations or configuration persistence belong in this module.
"""

import ctypes
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import scrolledtext, ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk


THEME = {
    "bg": "#161a22", "sidebar": "#242a34", "panel": "#10131b",
    "panel_alt": "#1c212c", "border": "#2c3240", "text": "#edf0f7",
    "muted": "#939baa", "accent": "#527cff", "accent_hover": "#6b8fff",
    "success": "#64c6a4", "danger": "#f38292", "selection": "#24375b",
}

# Stable keys are independent of translated labels and navigation ordering.
PAGES = (
    ("renamer", "文件与媒体", "批量重命名", "预览文件名称，按规则批量修改，并保留撤回入口。", "\ue8a5", "rename 文件 日期"),
    ("subtitle", "文件与媒体", "视频字幕同步", "整理剧集命名，让视频与外挂字幕一一对应。", "\ue8f2", "subtitle 字幕 视频"),
    ("media", "文件与媒体", "照片视频管家", "先扫描预览，再按整理计划处理照片和视频。", "\ue91b", "photo media 图片"),
    ("launcher", "效率工具", "应用启动器", "用分组管理常用程序，集中启动、关闭与修复路径。", "\ue71d", "app launcher 启动 分组"),
    ("topmost", "效率工具", "窗口置顶", "将需要关注的窗口保持在前，随时取消置顶。", "\ue718", "window pin 窗口"),
    ("macro", "效率工具", "宏工具", "录制操作、配置循环，管理已有宏文件。", "\ue768", "macro 录制 循环"),
    ("backup", "效率工具", "游戏备份", "打开游戏存档管理器，并保留原有备份入口。", "\ue74e", "game save backup 存档"),
    ("audio", "系统与快捷", "音频输出", "快速切换常用的耳机和音响输出。", "\ue767", "audio 声音 耳机"),
    ("system", "系统与快捷", "系统工具", "管理麦克风悬浮窗、快捷键与 PowerShell 入口。", "\ue713", "system mic 麦克风 powershell"),
    ("startup", "系统与快捷", "自启动", "查看并设置工具箱的登录启动行为。", "\ue7e8", "startup 开机 登录"),
    ("live", "系统与快捷", "直播快捷", "打开已配置的直播入口。", "\ue714", "live 虎牙 抖音"),
)


class DarkScrolledText(scrolledtext.ScrolledText):
    """Keep ScrolledText's public API with a themed, non-native scrollbar."""

    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        original = self.vbar
        self.vbar = ttk.Scrollbar(self.frame, orient="vertical", command=self.yview)
        self.vbar.pack(side="right", fill="y", before=original)
        self.configure(yscrollcommand=self.vbar.set)
        original.destroy()


def setup_theme(root, scale):
    """Use pixel-sized fonts so Windows DPI is applied exactly once."""
    p = lambda value: max(1, round(value * scale))
    font = ("Microsoft YaHei UI", -p(13))
    style = ttk.Style(root)
    style.theme_use("clam")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        tkfont.nametofont(name).configure(family=font[0], size=font[1])
    root.configure(bg=THEME["bg"])
    if sys.platform == "win32":
        root.after_idle(lambda: _dark_titlebar(root))
    root.option_add("*TCombobox*Listbox.background", THEME["panel_alt"])
    root.option_add("*TCombobox*Listbox.foreground", THEME["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", THEME["selection"])
    root.option_add("*TCombobox*Listbox.selectForeground", THEME["text"])
    style.configure(".", font=font, background=THEME["panel"], foreground=THEME["text"],
                    bordercolor=THEME["border"], darkcolor=THEME["border"], lightcolor=THEME["border"],
                    troughcolor=THEME["bg"], focuscolor=THEME["accent"])
    for name in ("TFrame", "App.TFrame", "Panel.TFrame", "TLabel", "App.TLabel"):
        style.configure(name, background=THEME["panel"], foreground=THEME["text"], font=font)
    style.configure("Title.TLabel", foreground=THEME["text"], font=(font[0], -p(19), "bold"))
    for name in ("TButton", "App.TButton"):
        style.configure(name, background=THEME["panel_alt"], foreground=THEME["text"],
                        borderwidth=1, relief="flat", padding=(p(13), p(8)), focusthickness=1)
        style.map(name, background=[("disabled", THEME["panel_alt"]), ("pressed", THEME["selection"]), ("active", "#2b3344")],
                  foreground=[("disabled", "#717988")], bordercolor=[("focus", THEME["accent"])])
    for name, color, hover in (("Accent.TButton", THEME["accent"], THEME["accent_hover"]),
                               ("Danger.TButton", "#67333f", "#834252")):
        style.configure(name, background=color, foreground="#ffffff", padding=(p(13), p(8)), relief="flat")
        style.map(name, background=[("disabled", THEME["panel_alt"]), ("active", hover)],
                  foreground=[("disabled", "#717988")])
    for name in ("TCheckbutton", "App.TCheckbutton", "TRadiobutton"):
        style.configure(name, background=THEME["panel"], foreground=THEME["text"], padding=(0, p(4)),
                        indicatorbackground=THEME["panel_alt"], indicatorforeground="#ffffff")
        style.map(name, background=[("active", THEME["panel"])],
                  foreground=[("disabled", "#717988")],
                  indicatorbackground=[("selected", THEME["accent"])])
    _setup_switches(root, style, scale)
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground=THEME["panel_alt"], foreground=THEME["text"],
                        insertcolor=THEME["text"], arrowcolor=THEME["muted"], padding=p(6),
                        selectbackground=THEME["selection"], selectforeground=THEME["text"])
        style.map(name, fieldbackground=[("readonly", THEME["panel_alt"]), ("disabled", THEME["bg"])],
                  foreground=[("disabled", "#717988"), ("readonly", THEME["text"])],
                  bordercolor=[("focus", THEME["accent"])], selectbackground=[("!disabled", THEME["selection"])])
    style.configure("Treeview", background=THEME["panel"], fieldbackground=THEME["panel"],
                    foreground=THEME["text"], rowheight=p(36), borderwidth=0)
    style.configure("Treeview.Heading", background=THEME["panel_alt"], foreground=THEME["muted"],
                    font=(font[0], -p(12)), relief="flat", padding=(p(10), p(9)))
    style.map("Treeview", background=[("selected", THEME["selection"])], foreground=[("selected", "#ffffff")])
    style.map("Treeview.Heading", background=[("active", "#2b3344")])
    for name in ("TLabelframe", "App.TLabelframe"):
        style.configure(name, background=THEME["panel"], bordercolor=THEME["border"], borderwidth=1, relief="solid")
        style.configure(name + ".Label", background=THEME["panel"], foreground=THEME["muted"],
                        font=(font[0], -p(12), "bold"))
    for name in ("Horizontal.TProgressbar", "App.Horizontal.TProgressbar"):
        style.configure(name, background=THEME["accent"], troughcolor=THEME["panel_alt"], borderwidth=0, thickness=p(4))
    for name in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(name, background="#343c4c", arrowcolor=THEME["muted"], borderwidth=0,
                        troughcolor=THEME["panel"], arrowsize=p(12))
        style.map(name, background=[("active", "#56647b")])


def _setup_switches(root, style, scale):
    images = []
    for selected, disabled in ((False, False), (True, False), (False, True), (True, True)):
        width, height = round(34 * scale), round(20 * scale)
        bitmap = Image.new("RGBA", (width * 3, height * 3))
        draw = ImageDraw.Draw(bitmap)
        fill = ("#34466e" if disabled else THEME["accent"]) if selected else THEME["border"]
        draw.rounded_rectangle((0, 0, width * 3 - 1, height * 3 - 1), radius=height * 1.5, fill=fill)
        left = (width - height + 3) if selected else 3
        draw.ellipse((left * 3, 9, (left + height - 6) * 3, (height - 3) * 3),
                     fill="#7e8798" if disabled else "#f5f7ff")
        # Transparent trailing space keeps the toggle clear of its label.
        padded = Image.new("RGBA", (width + round(9 * scale), height))
        padded.paste(bitmap.resize((width, height), Image.Resampling.LANCZOS), (0, 0))
        images.append(ImageTk.PhotoImage(padded, master=root))
    root._toolbox_switch_images = images
    if "Toolbox.Switch.indicator" not in style.element_names():
        style.element_create("Toolbox.Switch.indicator", "image", images[0],
                             ("disabled", "selected", images[3]), ("disabled", images[2]), ("selected", images[1]))
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
        ("Toolbox.Switch.indicator", {"side": "left", "sticky": ""}),
        ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
            ("Checkbutton.label", {"sticky": "nswe"})]})]})])


def _dark_titlebar(root):
    """Request a dark caption for this window only (older Windows may ignore it)."""
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        enabled = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
    except (AttributeError, OSError):
        pass  # The native caption remains functional without this cosmetic option.


def style_page(widget, scale):
    """Adapt legacy classic Tk widgets and explicit fonts inside a tool page."""
    p = lambda value: max(1, round(value * scale))
    if not isinstance(widget, ttk.Widget):
        options = widget.keys()
        settings = {}
        if "background" in options:
            settings["background"] = THEME["panel"]
        if "foreground" in options:
            settings["foreground"] = THEME["text"]
        for option, value in (("insertbackground", THEME["text"]), ("selectbackground", THEME["selection"]),
                              ("selectforeground", THEME["text"]), ("highlightbackground", THEME["border"]),
                              ("highlightcolor", THEME["accent"]), ("activebackground", THEME["panel_alt"])):
            if option in options:
                settings[option] = value
        if isinstance(widget, (tk.Text, tk.Listbox, tk.Entry)):
            settings.update(relief="flat", borderwidth=0, highlightthickness=1)
        widget.configure(**settings)
    if "font" in widget.keys() and widget.cget("font"):
        existing = tkfont.Font(font=widget.cget("font"))
        family = existing.actual("family")
        weight = existing.actual("weight")
        size = 15 if weight == "bold" else 13
        widget.configure(font=(family, -p(size), weight))
    for child in widget.winfo_children():
        style_page(child, scale)


class ScrollPage(ttk.Frame):
    """Keep legacy tool forms reachable when their requested size exceeds the view."""

    def __init__(self, parent, scale):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=THEME["panel"], highlightthickness=0, borderwidth=0)
        self.body = ttk.Frame(self.canvas)
        self.item = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.vertical = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vertical.set, xscrollcommand=self.horizontal.set,
                              yscrollincrement=max(1, round(24 * scale)))
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vertical.grid(row=0, column=1, sticky="ns")
        self.horizontal.grid(row=1, column=0, sticky="ew")
        self.canvas.bind("<Configure>", self._resize)
        self.body.bind("<Configure>", self._resize)
        self.bind("<Map>", self._resize)
        self.body.bind("<Destroy>", self._cancel_layout, add="+")
        self._layout_job = None

    def _cancel_layout(self, event):
        if event.widget is self.body and self._layout_job:
            self.after_cancel(self._layout_job)
            self._layout_job = None

    def _resize(self, _event=None):
        if self._layout_job is None:
            self._layout_job = self.after_idle(self._layout)

    def _layout(self):
        self._layout_job = None
        full_width, full_height = self.winfo_width(), self.winfo_height()
        requested_width, requested_height = self.body.winfo_reqwidth(), self.body.winfo_reqheight()
        vertical = requested_height > full_height
        horizontal = requested_width > full_width - (self.vertical.winfo_reqwidth() if vertical else 0)
        vertical = requested_height > full_height - (self.horizontal.winfo_reqheight() if horizontal else 0)
        horizontal = requested_width > full_width - (self.vertical.winfo_reqwidth() if vertical else 0)
        self.vertical.grid() if vertical else self.vertical.grid_remove()
        self.horizontal.grid() if horizontal else self.horizontal.grid_remove()
        width = max(requested_width, full_width - (self.vertical.winfo_reqwidth() if vertical else 0))
        height = max(requested_height, full_height - (self.horizontal.winfo_reqheight() if horizontal else 0))
        self.canvas.itemconfigure(self.item, width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))


class ToolboxShell:
    def __init__(self, root, scale, status_var):
        self.root, self.scale = root, scale
        self.p = lambda value: max(1, round(value * scale))
        self.active_key = None
        self.pages, self.nav_buttons, self.group_labels = {}, {}, {}
        self.nav_images = {}
        self.visible_keys = []
        self.frame = tk.Frame(root, bg=THEME["bg"])
        self.frame.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(self.frame, bg=THEME["sidebar"], width=self.p(216))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        brand = tk.Frame(self.sidebar, bg=THEME["sidebar"])
        brand.pack(fill="x", padx=self.p(22), pady=(self.p(23), self.p(20)))
        self._label(brand, "TOOLBOX", 20, "bold", bg="sidebar").pack(anchor="w")
        self._label(brand, "你的桌面工作台", 11, color="muted", bg="sidebar").pack(anchor="w", pady=(self.p(4), 0))
        tk.Frame(self.sidebar, height=1, bg=THEME["border"]).pack(fill="x", padx=self.p(16))
        footer = tk.Frame(self.sidebar, bg=THEME["sidebar"])
        footer.pack(side="bottom", fill="x", padx=self.p(20), pady=self.p(18))
        self._label(footer, "●  本地工具箱", 12, color="success", bg="sidebar").pack(anchor="w")
        self._label(footer, "11 个工具 · 本地运行", 10, color="muted", bg="sidebar").pack(anchor="w", pady=(self.p(5), 0))
        self.nav_canvas = tk.Canvas(self.sidebar, bg=THEME["sidebar"], highlightthickness=0,
                                    yscrollincrement=self.p(22))
        self.nav_canvas.pack(fill="both", expand=True, padx=self.p(12), pady=self.p(12))
        self.nav = tk.Frame(self.nav_canvas, bg=THEME["sidebar"])
        self.nav_item = self.nav_canvas.create_window(0, 0, window=self.nav, anchor="nw")
        self.nav_canvas.bind("<Configure>", lambda e: self.nav_canvas.itemconfigure(self.nav_item, width=e.width))
        self.nav.bind("<Configure>", lambda _e: self.nav_canvas.configure(scrollregion=self.nav_canvas.bbox("all")))
        for key, group, title, _desc, icon, _keywords in PAGES:
            if group not in self.group_labels:
                self.group_labels[group] = self._label(self.nav, group, 10, color="muted", bg="sidebar")
            button = tk.Button(self.nav, text="  " + title, anchor="w", relief="flat", borderwidth=0,
                               bg=THEME["sidebar"], fg=THEME["muted"], activebackground=THEME["selection"],
                               activeforeground=THEME["text"], font=("Microsoft YaHei UI", -self.p(13)),
                               padx=self.p(14), pady=self.p(8), cursor="hand2", takefocus=True,
                               highlightthickness=1, highlightbackground=THEME["sidebar"], highlightcolor=THEME["accent"],
                               command=lambda k=key: self.select(k))
            icons = self._navigation_icons(icon)
            if icons:
                self.nav_images[key] = icons
                button.configure(image=icons[0], compound="left")
            button.bind("<Enter>", lambda _e, k=key: self._hover(k, True))
            button.bind("<Leave>", lambda _e, k=key: self._hover(k, False))
            button.bind("<Up>", lambda _e, k=key: self._move_focus(k, -1))
            button.bind("<Down>", lambda _e, k=key: self._move_focus(k, 1))
            button.bind("<Return>", lambda _e, k=key: self.select(k))
            self.nav_buttons[key] = button
        self.empty_label = self._label(self.nav, "没有匹配的工具\n换个关键词试试", 12, color="muted", bg="sidebar")

        self.main = tk.Frame(self.frame, bg=THEME["bg"])
        self.main.pack(side="left", fill="both", expand=True)
        toolbar = tk.Frame(self.main, bg=THEME["bg"])
        toolbar.pack(fill="x", padx=self.p(24), pady=(self.p(20), self.p(16)))
        self.breadcrumb = self._label(toolbar, "工作台  /  文件与媒体", 11, color="muted")
        self.breadcrumb.pack(side="left")
        search_box = tk.Frame(toolbar, bg=THEME["panel_alt"], highlightthickness=1, highlightbackground=THEME["border"])
        search_box.pack(side="right")
        self._label(search_box, "搜索工具", 11, color="muted", bg="panel_alt").pack(side="left", padx=(self.p(10), self.p(6)))
        self.search_var = tk.StringVar(root)
        self.search_entry = tk.Entry(search_box, textvariable=self.search_var, width=15, relief="flat", borderwidth=0,
                                     bg=THEME["panel_alt"], fg=THEME["text"], insertbackground=THEME["text"],
                                     selectbackground=THEME["selection"], font=("Microsoft YaHei UI", -self.p(12)))
        self.search_entry.pack(side="left", ipady=self.p(8))
        self._label(search_box, "Ctrl K", 10, color="muted", bg="panel_alt").pack(side="left", padx=self.p(10))
        self.search_var.trace_add("write", self._filter)
        self.search_entry.bind("<Escape>", lambda _e: self.search_var.set(""))
        self.search_entry.bind("<Return>", self._select_search_result)
        root.bind("<Control-k>", self._focus_search, add="+")
        root.bind("<MouseWheel>", self._wheel, add="+")
        tk.Frame(self.main, height=1, bg=THEME["border"]).pack(fill="x", padx=self.p(24))
        heading = tk.Frame(self.main, bg=THEME["bg"])
        heading.pack(fill="x", padx=self.p(26), pady=(self.p(22), self.p(18)))
        self.page_number = self._label(heading, "01 / 11", 11, color="muted")
        self.page_number.pack(side="right", anchor="n", pady=self.p(5))
        self.title_label = self._label(heading, "", 24, "bold")
        self.title_label.pack(anchor="w")
        self.description = self._label(heading, "", 12, color="muted")
        self.description.pack(anchor="w", pady=(self.p(8), 0))
        heading.bind("<Configure>", lambda e: self.description.configure(wraplength=max(100, e.width - self.p(80))))
        status = tk.Frame(self.main, bg=THEME["bg"])
        status.pack(side="bottom", fill="x", padx=self.p(24), pady=self.p(13))
        self._label(status, "●", 10, color="accent").pack(side="left", padx=(0, self.p(8)))
        self.status = self._label(status, "", 11, color="muted")
        self.status.configure(textvariable=status_var, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.host = tk.Frame(self.main, bg=THEME["panel"], highlightthickness=1, highlightbackground=THEME["border"])
        self.host.pack(fill="both", expand=True, padx=self.p(24))
        for key, *_ in PAGES:
            self.pages[key] = ScrollPage(self.host, scale)
        self._filter()

    def _label(self, parent, text, size, weight="normal", color="text", bg="bg"):
        return tk.Label(parent, text=text, bg=THEME[bg], fg=THEME[color], borderwidth=0,
                        font=("Microsoft YaHei UI", -self.p(size), weight), justify="left")

    def _navigation_icons(self, glyph):
        font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segmdl2.ttf"
        if not font_path.is_file():
            return None  # Navigation labels remain complete on systems without this font.
        size = self.p(17)
        font = ImageFont.truetype(str(font_path), size * 3)
        icons = []
        for color in (THEME["muted"], THEME["accent_hover"]):
            bitmap = Image.new("RGBA", (size * 3, size * 3))
            draw = ImageDraw.Draw(bitmap)
            bounds = draw.textbbox((0, 0), glyph, font=font)
            draw.text(((size * 3 - bounds[2] + bounds[0]) / 2 - bounds[0],
                       (size * 3 - bounds[3] + bounds[1]) / 2 - bounds[1]), glyph, font=font, fill=color)
            icons.append(ImageTk.PhotoImage(bitmap.resize((size, size), Image.Resampling.LANCZOS), master=self.root))
        return icons

    def page(self, key):
        return self.pages[key].body

    def finish(self):
        for page in self.pages.values():
            style_page(page.body, self.scale)
        self._emphasize_actions()
        self.select("renamer")

    def _emphasize_actions(self):
        primary = {"预览", "扫描预览", "启动选中", "打开游戏存档管理器", "启动麦克风悬浮窗"}
        def visit(widget):
            if isinstance(widget, ttk.Button) and widget.cget("text") in primary:
                widget.configure(style="Accent.TButton")
            for child in widget.winfo_children():
                visit(child)
        for page in self.pages.values():
            visit(page.body)

    def select(self, key):
        if key not in self.pages:
            raise KeyError(key)
        if self.active_key:
            self.pages[self.active_key].pack_forget()
        self.active_key = key
        self.pages[key].pack(fill="both", expand=True, padx=self.p(8), pady=self.p(8))
        for index, (page_key, group, title, description, _icon, _keywords) in enumerate(PAGES, 1):
            selected = page_key == key
            self.nav_buttons[page_key].configure(bg=THEME["selection"] if selected else THEME["sidebar"],
                                                  fg=THEME["text"] if selected else THEME["muted"],
                                                  text="  " + title,
                                                  highlightbackground=THEME["selection"] if selected else THEME["sidebar"])
            if page_key in self.nav_images:
                self.nav_buttons[page_key].configure(image=self.nav_images[page_key][int(selected)])
            if selected:
                self.breadcrumb.configure(text="工作台  /  " + group)
                self.title_label.configure(text=title)
                self.description.configure(text=description)
                self.page_number.configure(text=f"{index:02d} / {len(PAGES):02d}")

    def _hover(self, key, entered):
        if key != self.active_key:
            self.nav_buttons[key].configure(bg=THEME["panel_alt"] if entered else THEME["sidebar"])

    def _filter(self, *_args):
        query = self.search_var.get().strip().casefold() if hasattr(self, "search_var") else ""
        for widget in self.nav.winfo_children():
            widget.pack_forget()
        self.visible_keys = []
        groups = set()
        for key, group, title, description, _icon, keywords in PAGES:
            if query and query not in f"{title} {group} {description} {keywords}".casefold():
                continue
            if group not in groups:
                self.group_labels[group].pack(anchor="w", padx=self.p(14), pady=(self.p(14), self.p(6)))
                groups.add(group)
            self.nav_buttons[key].pack(fill="x", pady=self.p(2))
            self.visible_keys.append(key)
        if not self.visible_keys:
            self.empty_label.pack(anchor="w", padx=self.p(12), pady=self.p(20))
        self.nav_canvas.yview_moveto(0)

    def _select_search_result(self, _event=None):
        if self.visible_keys:
            key = self.visible_keys[0]
            self.select(key)
            self.search_var.set("")
            self.nav_buttons[key].focus_set()
        return "break"

    def _focus_search(self, _event=None):
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        return "break"

    def _move_focus(self, key, offset):
        if key in self.visible_keys:
            index = (self.visible_keys.index(key) + offset) % len(self.visible_keys)
            button = self.nav_buttons[self.visible_keys[index]]
            button.focus_set()
            height = max(1, self.nav.winfo_height())
            top, bottom = self.nav_canvas.yview()
            if button.winfo_y() < top * height:
                self.nav_canvas.yview_moveto(button.winfo_y() / height)
            elif button.winfo_y() + button.winfo_height() > bottom * height:
                self.nav_canvas.yview_moveto((button.winfo_y() + button.winfo_height() - self.nav_canvas.winfo_height()) / height)
        return "break"

    def _wheel(self, event):
        widget = event.widget
        while widget is not None and widget is not self.nav_canvas:
            widget = getattr(widget, "master", None)
        if widget is self.nav_canvas:
            if self.nav.winfo_height() > self.nav_canvas.winfo_height():
                self.nav_canvas.yview_scroll(-int(event.delta / 120), "units")
            return "break"
        if not self.active_key or isinstance(event.widget, (tk.Text, tk.Listbox, ttk.Treeview, ttk.Combobox, ttk.Spinbox)):
            return
        page = self.pages[self.active_key]
        widget = event.widget
        while widget is not None and widget is not page:
            widget = getattr(widget, "master", None)
        if widget is page:
            page.canvas.yview_scroll(-int(event.delta / 120), "units")
            return "break"
