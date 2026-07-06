r"""
Interception 循环宏回放器

读取：C:\Users\User\Desktop\macro_recording.json
热键：mouse:x1 开启 / 暂停
循环间隔：0.1 秒

设计：
- pynput 只负责监听 mouse:x1 热键，保证侧键识别稳定。
- Interception 负责发送鼠标/键盘事件。
- 自动跳过录制时混入的热键事件。
- 自动把第一条有效宏事件的时间归零，按下热键后立即执行，不等待录制前摇。
"""

from __future__ import annotations

import copy
import json
import sys
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Set

import interception
try:
    from pynput import mouse as pynput_mouse
except Exception:
    pynput_mouse = None


MACRO_JSON = Path(r"C:\Users\User\Desktop\macro_recording.json")
TOGGLE_HOTKEY = "mouse:x1"
LOOP_INTERVAL_SECONDS = 0.1
HOTKEY_BACKEND = "pynput"  # 推荐 pynput：侧键识别比 Interception 监听更稳定
SKIP_HOTKEY_MOUSE_EVENTS = True
ZERO_LEADING_DELAY = True
MAX_EVENT_DELAY_SECONDS = 10.0

MOUSE_BUTTON_MAP = {
    "left": "left",
    "right": "right",
    "middle": "middle",
    "x1": "mouse4",
    "x2": "mouse5",
    "mouse4": "mouse4",
    "mouse5": "mouse5",
    "button.x1": "mouse4",
    "button.x2": "mouse5",
}

KEY_MAP = {
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl", "control": "ctrl",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt",
    "cmd": "win", "cmd_l": "win", "cmd_r": "win", "win": "win",
    "enter": "enter", "return": "enter", "esc": "esc", "escape": "esc",
    "space": "space", "tab": "tab", "backspace": "backspace",
    "delete": "delete", "insert": "insert", "home": "home", "end": "end",
    "page_up": "pageup", "page_down": "pagedown", "pageup": "pageup", "pagedown": "pagedown",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "caps_lock": "capslock", "capslock": "capslock",
}

SUPPORTED_EVENT_TYPES = {"mouse_click", "mouse_move", "mouse_scroll", "key_press", "key_release"}


class RuntimeState:
    def __init__(self) -> None:
        self.enabled = threading.Event()
        self.quit = threading.Event()
        self.lock = threading.RLock()
        self.loop_count = 0
        self.pressed_keys: Set[str] = set()
        self.pressed_mouse_buttons: Set[str] = set()

    def toggle(self) -> None:
        with self.lock:
            if self.enabled.is_set():
                self.enabled.clear()
                safe_release_all(self)
                print("[PAUSE] 已暂停。再按 mouse:x1 继续。", flush=True)
            else:
                self.enabled.set()
                print("[RUN] 已开启循环回放：立即执行第一条有效事件。按 mouse:x1 暂停。", flush=True)


def normalize_button_name(button: Any) -> str:
    return str(button).strip().lower().replace("button.", "")


def normalize_mouse_hotkey(hotkey: str) -> str:
    text = str(hotkey).strip().lower().replace(" ", "")
    if text.startswith("mouse:"):
        text = text.split(":", 1)[1]
    text = text.replace("button.", "")
    aliases = {
        "x1": "x1", "x2": "x2", "mouse4": "x1", "mouse5": "x2",
        "back": "x1", "forward": "x2", "侧键1": "x1", "侧键2": "x2",
    }
    if text not in aliases:
        raise ValueError(f"当前脚本只支持 mouse:x1 / mouse:x2，收到：{hotkey!r}")
    return aliases[text]


def clean_key_name(key: Any) -> str:
    k = str(key).strip().lower().replace("key.", "")
    if len(k) == 1:
        return k
    if k.startswith("f") and k[1:].isdigit():
        return k
    return KEY_MAP.get(k, k)


def validate_and_normalize_event(raw: Dict[str, Any], hotkey_button: str) -> Dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    etype = raw.get("type")
    if etype not in SUPPORTED_EVENT_TYPES:
        return None
    data = raw.get("data")
    if not isinstance(data, dict):
        data = {}

    try:
        t = float(raw.get("t", 0.0))
    except Exception:
        t = 0.0
    if t < 0:
        t = 0.0

    if etype == "mouse_click":
        btn = normalize_button_name(data.get("button", "left"))
        if SKIP_HOTKEY_MOUSE_EVENTS and btn == hotkey_button:
            return None
        if btn not in MOUSE_BUTTON_MAP:
            print(f"[FILTER] 跳过未知鼠标按钮：{btn}", flush=True)
            return None
        return {
            "t": t,
            "type": etype,
            "data": {
                "x": int(data.get("x", 0)),
                "y": int(data.get("y", 0)),
                "button": btn,
                "pressed": bool(data.get("pressed")),
            },
        }

    if etype == "mouse_move":
        if "x" not in data or "y" not in data:
            return None
        return {"t": t, "type": etype, "data": {"x": int(data["x"]), "y": int(data["y"])}}

    if etype == "mouse_scroll":
        return {
            "t": t,
            "type": etype,
            "data": {
                "x": int(data.get("x", 0)),
                "y": int(data.get("y", 0)),
                "dx": int(data.get("dx", 0)),
                "dy": int(data.get("dy", 0)),
            },
        }

    if etype in {"key_press", "key_release"}:
        key = clean_key_name(data.get("key", ""))
        if not key:
            return None
        return {"t": t, "type": etype, "data": {"key": key}}

    return None


def load_macro(path: Path, hotkey_button: str) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    raw_events = payload.get("events", [])
    if not isinstance(raw_events, list):
        raise ValueError("JSON 格式错误：events 不是列表")

    normalized: List[Dict[str, Any]] = []
    skipped = 0
    for raw in raw_events:
        before_len = len(normalized)
        event = validate_and_normalize_event(raw, hotkey_button)
        if event is not None:
            normalized.append(event)
        elif isinstance(raw, dict) and raw.get("type") == "mouse_click":
            btn = normalize_button_name((raw.get("data") or {}).get("button", ""))
            if btn == hotkey_button:
                skipped += 1
        elif len(normalized) == before_len:
            skipped += 1

    normalized.sort(key=lambda e: float(e["t"]))
    if not normalized:
        raise ValueError("没有可回放事件。请重新录制，或检查 macro_recording.json。")

    original_first_t = float(normalized[0]["t"])
    if ZERO_LEADING_DELAY:
        for e in normalized:
            e["t"] = round(max(0.0, float(e["t"]) - original_first_t), 6)

    duration = float(normalized[-1]["t"])
    print(f"[LOAD] 文件：{path}", flush=True)
    print(
        f"[LOAD] 原始事件：{len(raw_events)}，有效回放事件：{len(normalized)}，过滤/跳过：{len(raw_events) - len(normalized)}",
        flush=True,
    )
    print(f"[FIX] 已去掉启动前摇：{original_first_t:.3f}s；第一条事件现在 t=0，按热键后立即执行。", flush=True)
    print(f"[LOAD] 宏时长：{duration:.3f}s；循环间隔：{LOOP_INTERVAL_SECONDS:.3f}s", flush=True)
    print(f"[LOAD] 首条事件：{normalized[0]}", flush=True)
    return normalized


def interruptible_sleep(seconds: float, state: RuntimeState) -> bool:
    seconds = max(0.0, min(float(seconds), MAX_EVENT_DELAY_SECONDS))
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        if state.quit.is_set() or not state.enabled.is_set():
            return False
        time.sleep(min(0.005, end - time.perf_counter()))
    return True


def safe_release_all(state: RuntimeState) -> None:
    with state.lock:
        keys = list(state.pressed_keys)
        buttons = list(state.pressed_mouse_buttons)
        state.pressed_keys.clear()
        state.pressed_mouse_buttons.clear()
    for key in keys:
        try:
            interception.key_up(key, delay=0)
        except Exception:
            pass
    for button in buttons:
        try:
            interception.mouse_up(button, delay=0)
        except Exception:
            pass


def replay_event(event: Dict[str, Any], state: RuntimeState) -> None:
    etype = event["type"]
    data = event["data"]

    if etype == "mouse_move":
        interception.move_to(int(data["x"]), int(data["y"]), allow_global_params=False)
        return

    if etype == "mouse_click":
        button = MOUSE_BUTTON_MAP[data["button"]]
        interception.move_to(int(data["x"]), int(data["y"]), allow_global_params=False)
        if data["pressed"]:
            interception.mouse_down(button, delay=0)
            with state.lock:
                state.pressed_mouse_buttons.add(button)
        else:
            interception.mouse_up(button, delay=0)
            with state.lock:
                state.pressed_mouse_buttons.discard(button)
        return

    if etype == "mouse_scroll":
        dy = int(data.get("dy", 0))
        if dy > 0:
            interception.scroll("up")
        elif dy < 0:
            interception.scroll("down")
        return

    if etype == "key_press":
        key = data["key"]
        interception.key_down(key, delay=0)
        with state.lock:
            state.pressed_keys.add(key)
        return

    if etype == "key_release":
        key = data["key"]
        interception.key_up(key, delay=0)
        with state.lock:
            state.pressed_keys.discard(key)
        return


def playback_worker(events: List[Dict[str, Any]], state: RuntimeState, hotkey_button: str) -> None:
    print(f"[READY] 待机中。按 mouse:{hotkey_button} 开始/暂停循环。Ctrl+C 退出。", flush=True)
    while not state.quit.is_set():
        state.enabled.wait(0.05)
        if not state.enabled.is_set():
            continue

        previous_t = 0.0
        for event in events:
            if state.quit.is_set() or not state.enabled.is_set():
                break
            t = float(event["t"])
            delay = max(0.0, t - previous_t)
            previous_t = t
            if delay and not interruptible_sleep(delay, state):
                break
            if state.quit.is_set() or not state.enabled.is_set():
                break
            try:
                replay_event(event, state)
            except Exception as exc:
                print(f"[EVENT ERROR] {event} -> {type(exc).__name__}: {exc}", flush=True)

        safe_release_all(state)
        if state.enabled.is_set() and not state.quit.is_set():
            state.loop_count += 1
            print(f"[LOOP] 第 {state.loop_count} 次完成，间隔 {LOOP_INTERVAL_SECONDS:.3f}s", flush=True)
            interruptible_sleep(LOOP_INTERVAL_SECONDS, state)


def pynput_hotkey_listener_worker(state: RuntimeState, hotkey_button: str) -> None:
    if pynput_mouse is None:
        print("[HOTKEY ERROR] 未安装 pynput：python -m pip install pynput", flush=True)
        return

    def on_click(x, y, button, pressed):
        if state.quit.is_set():
            return False
        if not pressed:
            return None
        name = normalize_button_name(button)
        if name == hotkey_button:
            state.toggle()
        return None

    print(f"[HOTKEY] pynput 正在监听 mouse:{hotkey_button}。", flush=True)
    with pynput_mouse.Listener(on_click=on_click) as listener:
        while not state.quit.is_set():
            time.sleep(0.05)
        listener.stop()


def self_check(events: List[Dict[str, Any]]) -> None:
    issues = []
    if events[0]["t"] != 0:
        issues.append(f"首事件不是 0：{events[0]['t']}")
    last_t = -1.0
    for i, e in enumerate(events):
        if e["t"] < last_t:
            issues.append(f"事件时间倒退：#{i}")
        last_t = e["t"]
        if e["type"] == "mouse_click" and e["data"]["button"] not in MOUSE_BUTTON_MAP:
            issues.append(f"未知鼠标按钮：#{i}")
        if e["type"] in {"key_press", "key_release"} and not e["data"].get("key"):
            issues.append(f"空按键：#{i}")
    if issues:
        print("[SELF-CHECK] 发现问题：", flush=True)
        for x in issues:
            print(f"  - {x}", flush=True)
    else:
        print("[SELF-CHECK] 通过：时间轴、事件类型、按钮/按键字段正常。", flush=True)


def main() -> int:
    macro_path = Path(sys.argv[1]) if len(sys.argv) >= 2 else MACRO_JSON
    if not macro_path.exists():
        print(f"[ERROR] 找不到宏文件：{macro_path}", flush=True)
        return 2

    hotkey_button = normalize_mouse_hotkey(TOGGLE_HOTKEY)
    events = load_macro(macro_path, hotkey_button)
    self_check(events)

    print(f"[CONFIG] 热键：{TOGGLE_HOTKEY}", flush=True)
    print(f"[CONFIG] 热键监听：{HOTKEY_BACKEND}", flush=True)
    print("[CONFIG] 回放引擎：Interception", flush=True)

    try:
        interception.auto_capture_devices(keyboard=True, mouse=True, verbose=False)
        print("[CHECK] Interception 设备初始化完成。", flush=True)
    except Exception as exc:
        print(f"[WARN] Interception 自动设备初始化失败：{type(exc).__name__}: {exc}", flush=True)
        print("[WARN] 将继续使用默认设备；如果回放无动作，需要检查 Interception 驱动/管理员权限。", flush=True)

    state = RuntimeState()
    hotkey_thread = threading.Thread(
        target=pynput_hotkey_listener_worker,
        args=(state, hotkey_button),
        daemon=True,
    )
    play_thread = threading.Thread(
        target=playback_worker,
        args=(events, state, hotkey_button),
        daemon=True,
    )
    hotkey_thread.start()
    play_thread.start()

    try:
        while True:
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("\n[EXIT] 正在退出……", flush=True)
        state.quit.set()
        state.enabled.set()
        safe_release_all(state)
        hotkey_thread.join(timeout=1.0)
        play_thread.join(timeout=1.0)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

