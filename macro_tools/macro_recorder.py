import json
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

try:
    from pynput import keyboard, mouse
except ImportError:
    keyboard = None
    mouse = None


APP_TITLE = "Python 鼠标键盘宏录制器"
DEFAULT_HOTKEY = "<ctrl>+<shift>+r"


@dataclass
class MacroEvent:
    t: float
    type: str
    data: Dict[str, Any]


def key_to_string(key: Any) -> str:
    try:
        if hasattr(key, "char") and key.char is not None:
            return key.char
    except Exception:
        pass
    return str(key).replace("Key.", "")


def mouse_button_to_string(button: Any) -> str:
    return str(button).replace("Button.", "")


def normalize_mouse_button_name(button: Any) -> str:
    """把 pynput 的 Button.x1 / Button.x2 等对象规范成 x1 / x2。"""
    return mouse_button_to_string(button).lower().strip()


def parse_mouse_hotkey(text: str) -> Optional[str]:
    """
    解析鼠标热键。

    支持这些写法：
    - mouse:x1 / mouse:x2
    - x1 / x2
    - button.x1 / button.x2
    - mouse:back / mouse:forward
    - mouse:侧键1 / mouse:侧键2
    """
    raw = (text or "").strip().lower().replace(" ", "")
    raw = raw.replace("button.", "")
    raw = raw.strip("<>")
    if raw.startswith("mouse:"):
        raw = raw.split(":", 1)[1].strip("<>")
    aliases = {
        "x1": "x1",
        "x2": "x2",
        "back": "x1",
        "forward": "x2",
        "侧键1": "x1",
        "侧键2": "x2",
        "鼠标侧键1": "x1",
        "鼠标侧键2": "x2",
        "mouse4": "x1",
        "mouse5": "x2",
        "middle": "middle",
        "中键": "middle",
    }
    return aliases.get(raw)


class MacroRecorder:
    def __init__(self):
        self.record_mouse_moves = tk.BooleanVar(value=False)
        self.is_recording = False
        self.events: List[MacroEvent] = []
        self.start_time: Optional[float] = None
        self.keyboard_listener = None
        self.mouse_listener = None
        self.lock = threading.RLock()
        self.last_move_time = 0.0
        self.move_throttle_seconds = 0.015

    def start(self):
        if keyboard is None or mouse is None:
            raise RuntimeError("缺少 pynput 依赖，请先运行：python -m pip install pynput")
        with self.lock:
            if self.is_recording:
                return
            self.events.clear()
            self.start_time = time.perf_counter()
            self.last_move_time = 0.0
            self.is_recording = True
            self.keyboard_listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release,
            )
            self.mouse_listener = mouse.Listener(
                on_move=self._on_mouse_move,
                on_click=self._on_mouse_click,
                on_scroll=self._on_mouse_scroll,
            )
            self.keyboard_listener.start()
            self.mouse_listener.start()

    def stop(self):
        with self.lock:
            if not self.is_recording:
                return
            self.is_recording = False
            kl = self.keyboard_listener
            ml = self.mouse_listener
            self.keyboard_listener = None
            self.mouse_listener = None
        if kl is not None:
            kl.stop()
        if ml is not None:
            ml.stop()

    def clear(self):
        with self.lock:
            self.events.clear()

    def snapshot(self) -> List[MacroEvent]:
        with self.lock:
            return list(self.events)

    def _elapsed(self) -> float:
        if self.start_time is None:
            return 0.0
        return round(time.perf_counter() - self.start_time, 6)

    def _add(self, event_type: str, data: Dict[str, Any]):
        with self.lock:
            if not self.is_recording:
                return
            self.events.append(MacroEvent(t=self._elapsed(), type=event_type, data=data))

    def _on_key_press(self, key):
        self._add("key_press", {"key": key_to_string(key)})

    def _on_key_release(self, key):
        self._add("key_release", {"key": key_to_string(key)})

    def _on_mouse_move(self, x, y):
        if not self.record_mouse_moves.get():
            return
        now = time.perf_counter()
        if now - self.last_move_time < self.move_throttle_seconds:
            return
        self.last_move_time = now
        self._add("mouse_move", {"x": int(x), "y": int(y)})

    def _on_mouse_click(self, x, y, button, pressed):
        self._add("mouse_click", {
            "x": int(x),
            "y": int(y),
            "button": mouse_button_to_string(button),
            "pressed": bool(pressed),
        })

    def _on_mouse_scroll(self, x, y, dx, dy):
        self._add("mouse_scroll", {
            "x": int(x),
            "y": int(y),
            "dx": int(dx),
            "dy": int(dy),
        })

    def export_json(self, path: str, hotkey: str):
        events = self.snapshot()
        payload = {
            "format": "py_macro_recorder.v1",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "record_mouse_moves": bool(self.record_mouse_moves.get()),
            "record_hotkey": hotkey,
            "event_count": len(events),
            "events": [asdict(e) for e in events],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


class HotkeyCaptureDialog(tk.Toplevel):
    def __init__(self, parent, current_hotkey: str):
        super().__init__(parent)
        self.title("设置录制热键")
        self.resizable(False, False)
        self.result = None
        self.pressed = set()
        self.display = tk.StringVar(value=current_hotkey)
        self.mouse_capture_listener = None
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="按下键盘组合键，或点击鼠标侧键，然后点确定。",
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w")
        ttk.Label(frame, textvariable=self.display, font=("Consolas", 14, "bold"), foreground="#0f766e").pack(anchor="w", pady=(12, 16))

        btns = ttk.Frame(frame)
        btns.pack(fill="x")
        ttk.Button(btns, text="确定", command=self._ok).pack(side="right")
        ttk.Button(btns, text="取消", command=self._cancel).pack(side="right", padx=(0, 8))

        self.bind("<KeyPress>", self._on_press)
        self.bind("<KeyRelease>", self._on_release)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.focus_force()
        self._start_mouse_capture()

    def _start_mouse_capture(self):
        if mouse is None:
            return
        try:
            self.mouse_capture_listener = mouse.Listener(on_click=self._on_mouse_click)
            self.mouse_capture_listener.start()
        except Exception:
            self.mouse_capture_listener = None

    def _stop_mouse_capture(self):
        if self.mouse_capture_listener is not None:
            try:
                self.mouse_capture_listener.stop()
            except Exception:
                pass
            self.mouse_capture_listener = None

    def _on_mouse_click(self, x, y, button, pressed):
        if not pressed:
            return
        name = normalize_mouse_button_name(button)
        # 主要目标是侧键；也允许中键，避免某些鼠标驱动把侧键映射成 middle。
        if name in {"x1", "x2", "middle"}:
            self.display.set(f"mouse:{name}")

    def _normalize_tk_key(self, keysym: str) -> str:
        mapping = {
            "Control_L": "<ctrl>", "Control_R": "<ctrl>",
            "Shift_L": "<shift>", "Shift_R": "<shift>",
            "Alt_L": "<alt>", "Alt_R": "<alt>",
            "Win_L": "<cmd>", "Win_R": "<cmd>",
            "Command": "<cmd>", "Escape": "<esc>",
            "Return": "<enter>", "BackSpace": "<backspace>",
            "space": "<space>", "Tab": "<tab>",
        }
        if keysym in mapping:
            return mapping[keysym]
        if keysym.startswith("F") and keysym[1:].isdigit():
            return f"<{keysym.lower()}>"
        if len(keysym) == 1:
            return keysym.lower()
        return f"<{keysym.lower()}>"

    def _hotkey_text(self) -> str:
        order = ["<ctrl>", "<shift>", "<alt>", "<cmd>"]
        mods = [x for x in order if x in self.pressed]
        normals = sorted(x for x in self.pressed if x not in order)
        return "+".join(mods + normals) if self.pressed else ""

    def _on_press(self, event):
        k = self._normalize_tk_key(event.keysym)
        self.pressed.add(k)
        text = self._hotkey_text()
        if text:
            self.display.set(text)

    def _on_release(self, event):
        # 不立即清空，方便用户看到刚刚按下的组合键。
        pass

    def _ok(self):
        val = self.display.get().strip()
        if not val:
            messagebox.showerror("无效热键", "请至少按一个键。", parent=self)
            return
        self.result = val
        self._stop_mouse_capture()
        self.destroy()

    def _cancel(self):
        self.result = None
        self._stop_mouse_capture()
        self.destroy()


class MacroRecorderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("720x480")
        self.minsize(680, 430)
        self.recorder = MacroRecorder()
        self.hotkey_var = tk.StringVar(value=DEFAULT_HOTKEY)
        self.status_var = tk.StringVar(value="就绪")
        self.count_var = tk.StringVar(value="0 个事件")
        self.hotkey_listener = None
        self.mouse_hotkey_listener = None
        self.mouse_hotkey_button = None
        self._build_ui()
        self._install_hotkey()
        self._tick()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root)
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, font=("Microsoft YaHei UI", 18, "bold")).pack(side="left")
        self.status_label = ttk.Label(header, textvariable=self.status_var, foreground="#2563eb")
        self.status_label.pack(side="right")

        controls = ttk.LabelFrame(root, text="录制控制", padding=12)
        controls.pack(fill="x", pady=(14, 10))

        ttk.Label(controls, text="录制热键：").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.hotkey_var, width=28, font=("Consolas", 10)).grid(row=0, column=1, sticky="w", padx=(8, 8))
        ttk.Button(controls, text="捕获热键", command=self._capture_hotkey).grid(row=0, column=2, sticky="w")
        ttk.Button(controls, text="应用热键", command=self._install_hotkey).grid(row=0, column=3, sticky="w", padx=(8, 0))

        ttk.Checkbutton(
            controls,
            text="记录鼠标移动轨迹（会产生大量事件）",
            variable=self.recorder.record_mouse_moves,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(12, 0))

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", pady=(0, 10))
        self.record_btn = ttk.Button(buttons, text="开始录制", command=self._toggle_recording)
        self.record_btn.pack(side="left")
        ttk.Button(buttons, text="清空", command=self._clear).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="导出 JSON", command=self._export).pack(side="left", padx=(8, 0))
        ttk.Label(buttons, textvariable=self.count_var).pack(side="right")

        log_frame = ttk.LabelFrame(root, text="事件预览", padding=8)
        log_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(log_frame, columns=("time", "type", "data"), show="headings", height=12)
        self.tree.heading("time", text="时间")
        self.tree.heading("type", text="类型")
        self.tree.heading("data", text="数据")
        self.tree.column("time", width=90, anchor="e")
        self.tree.column("type", width=120)
        self.tree.column("data", width=430)
        yscroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")

        hint = ttk.Label(
            root,
            text="提示：默认 Ctrl+Shift+R 开始/停止录制；热键也可填 mouse:x1 / mouse:x2 绑定鼠标侧键。",
            foreground="#64748b",
        )
        hint.pack(fill="x", pady=(8, 0))

    def _install_hotkey(self):
        if keyboard is None or mouse is None:
            self.status_var.set("缺少 pynput：python -m pip install pynput")
            return
        self._stop_hotkey_listeners()
        hotkey = self.hotkey_var.get().strip() or DEFAULT_HOTKEY
        mouse_button_name = parse_mouse_hotkey(hotkey)
        if mouse_button_name:
            try:
                self.mouse_hotkey_button = mouse_button_name
                self.mouse_hotkey_listener = mouse.Listener(on_click=self._on_hotkey_mouse_click)
                self.mouse_hotkey_listener.start()
                self.status_var.set(f"鼠标热键已启用：mouse:{mouse_button_name}")
            except Exception as e:
                self.status_var.set("鼠标热键无效")
                messagebox.showerror("热键设置失败", f"无法注册鼠标热键：{hotkey}\n\n{e}")
            return
        try:
            self.hotkey_listener = keyboard.GlobalHotKeys({hotkey: self._toggle_recording_threadsafe})
            self.hotkey_listener.start()
            self.status_var.set(f"键盘热键已启用：{hotkey}")
        except Exception as e:
            self.status_var.set("热键无效")
            messagebox.showerror("热键设置失败", f"无法注册键盘热键：{hotkey}\n\n{e}")

    def _stop_hotkey_listeners(self):
        if self.hotkey_listener is not None:
            try:
                self.hotkey_listener.stop()
            except Exception:
                pass
            self.hotkey_listener = None
        if self.mouse_hotkey_listener is not None:
            try:
                self.mouse_hotkey_listener.stop()
            except Exception:
                pass
            self.mouse_hotkey_listener = None
        self.mouse_hotkey_button = None

    def _on_hotkey_mouse_click(self, x, y, button, pressed):
        if not pressed:
            return
        if normalize_mouse_button_name(button) == self.mouse_hotkey_button:
            self._toggle_recording_threadsafe()

    def _capture_hotkey(self):
        # 捕获鼠标侧键时先暂停当前全局热键，避免“设置热键”的点击本身触发开始/停止录制。
        self._stop_hotkey_listeners()
        dlg = HotkeyCaptureDialog(self, self.hotkey_var.get())
        self.wait_window(dlg)
        if dlg.result:
            self.hotkey_var.set(dlg.result)
        self._install_hotkey()

    def _toggle_recording_threadsafe(self):
        self.after(0, self._toggle_recording)

    def _toggle_recording(self):
        try:
            if self.recorder.is_recording:
                self.recorder.stop()
                self.record_btn.configure(text="开始录制")
                self.status_var.set("已停止录制")
            else:
                self.recorder.start()
                self.record_btn.configure(text="停止录制")
                self.status_var.set("录制中……")
        except Exception as e:
            messagebox.showerror("录制失败", str(e))
            self.status_var.set("录制失败")

    def _clear(self):
        if self.recorder.is_recording:
            messagebox.showinfo("正在录制", "请先停止录制再清空。")
            return
        self.recorder.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.count_var.set("0 个事件")
        self.status_var.set("已清空")

    def _export(self):
        if self.recorder.is_recording:
            messagebox.showinfo("正在录制", "请先停止录制再导出。")
            return
        if not self.recorder.snapshot():
            messagebox.showinfo("没有数据", "当前没有可导出的录制事件。")
            return
        path = filedialog.asksaveasfilename(
            title="导出宏 JSON",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            initialfile="macro_recording.json",
        )
        if not path:
            return
        try:
            self.recorder.export_json(path, self.hotkey_var.get().strip())
            self.status_var.set(f"已导出：{path}")
            messagebox.showinfo("导出成功", f"已导出到：\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _tick(self):
        events = self.recorder.snapshot()
        self.count_var.set(f"{len(events)} 个事件")
        # 只刷新尾部，避免大量移动事件导致 GUI 卡死。
        existing = len(self.tree.get_children())
        new_events = events[existing:existing + 250]
        for e in new_events:
            data = json.dumps(e.data, ensure_ascii=False)
            self.tree.insert("", "end", values=(f"{e.t:.3f}", e.type, data))
        if new_events:
            children = self.tree.get_children()
            if children:
                self.tree.see(children[-1])
        self.after(250, self._tick)

    def _on_close(self):
        try:
            self.recorder.stop()
        except Exception:
            pass
        self._stop_hotkey_listeners()
        self.destroy()


if __name__ == "__main__":
    app = MacroRecorderApp()
    app.mainloop()
