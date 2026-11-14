"""Connect the Qt UI to existing, regression-tested file/system engines."""

import json
import threading
import sys
from pathlib import Path
from datetime import datetime

import utility_toolbox as core
import subtitle_sync_embedded as subtitles
import game_save_manager_launcher as saves

from .adapters import Dispatcher, Dialogs, Value


def engine(cls, **attributes):
    instance = object.__new__(cls)
    instance.__dict__.update(attributes)
    return instance


class Backend:
    def __init__(self, window, notify):
        self.dispatcher = Dispatcher(window)
        self._ui_jobs = 0
        self.notify = lambda message: self.dispatcher.after(0, lambda: notify(message))
        self.dialogs = Dialogs(self.dispatcher, notify)
        self._dialog_originals = []
        self._dialog_originals.append((core, "app_executable", core.app_executable))
        entry = Path(__file__).resolve().parent.parent / "toolbox_glass.py"
        core.app_executable = lambda: (f'"{sys.executable}"' if getattr(sys, "frozen", False)
                                       else f'"{sys.executable}" "{entry}"')
        for module in (core, subtitles, saves):
            for name in ("messagebox", "filedialog", "simpledialog"):
                if hasattr(module, name):
                    self._dialog_originals.append((module, name, getattr(module, name)))
                    setattr(module, name, self.dialogs)
        self.config = core.load_config()
        self._loaded_config = core.CONFIG_FILE.read_bytes() if core.CONFIG_FILE.exists() else None
        self.renamer = engine(core.DateRenamerTab, parent=self.dispatcher, scale=1, folder=None, plan=[],
                              undo_file=core.APP_DIR / "renamer_last_undo.json",
                              folder_var=Value("尚未选择文件夹"), mode_var=Value("日期+序号"),
                              date_var=Value(datetime.now().strftime("%Y-%m-%d")), find_var=Value(""),
                              replace_var=Value(""), start_var=Value("1"))
        self.launcher = engine(core.LauncherTab, parent=self.dispatcher, scale=1, status_callback=self.notify,
                               _close_lock=threading.Lock(), _close_stopped=False,
                               config_file=core.APP_DIR / "launcher.json", groups={}, current_group=None)
        self.launcher.load_config()
        if not self.launcher.groups:
            self.launcher.groups = {"默认组": {"apps": []}}
            self.launcher.current_group = "默认组"
        self.subtitle = engine(subtitles.MainApp, root=self.dispatcher, embedded=True,
                               history_db={}, _history_read_error=None, _history_loaded_bytes=None,
                               is_running=False, stop_requested=False, target_dir=None, dry_run=Value(True))
        self.subtitle._load_history_db()
        self.media = engine(core.MediaOrganizerTab, parent=self.dispatcher, status_callback=self.notify,
                            scale=1, manifest=None, busy=False, source_var=Value(""), output_var=Value(""),
                            summary_var=Value("选择源目录和输出目录，然后扫描预览。"))
        enabled, hotkey, _codes = core.read_mic_hotkey_settings()
        self.system = engine(core.SystemToolsTab, parent=self.dispatcher, status_callback=self.notify, scale=1,
                             mic_target_choices={}, mic_target_var=Value(core.MIC_TARGET_DEFAULT_LABEL),
                             mic_overlay_scale_var=Value(str(round(core.read_mic_overlay_scale() * 100))),
                             mic_hotkey_enabled_var=Value(enabled), mic_hotkey_var=Value(hotkey))
        self.audio = engine(core.AudioSwitchTab, parent=self.dispatcher, status_callback=self.notify, scale=1)
        self.live = engine(core.LiveShortcutTab, parent=self.dispatcher, status_callback=self.notify, scale=1)
        self.macro = engine(core.MacroToolsTab, parent=self.dispatcher, status_callback=self.notify,
                            macro_json_var=Value(str(core.macro_recording_path())),
                            loop_hotkey_var=Value("mouse:x1"), loop_interval_var=Value("0.1"))
        stored_startup = self.config.get("startup")
        self.startup = engine(core.StartupTab, parent=self.dispatcher, config=self.config,
                              save_callback=self.save_config, status_callback=self.notify,
                              startup_cfg=dict(stored_startup) if isinstance(stored_startup, dict) else {},
                              autostart_var=Value(False), state_var=Value(""))
        self.startup.refresh_state()
        self.backup = engine(saves.GameSaveManagerTab, parent=self.dispatcher, config=self.config,
                             save_callback=self.save_config, _stopped=False, _launching=False,
                             _cooldown_job=None, status=Value(""), location=Value(""))
        self.topmost = core.TopmostController(self.dispatcher, self.notify)

    def save_config(self):
        current = core.CONFIG_FILE.read_bytes() if core.CONFIG_FILE.exists() else None
        if current != self._loaded_config:
            raise ValueError("配置已被其他工具箱实例修改，请重新打开本窗口后再保存。")
        core.save_config(self.config)
        self._loaded_config = core.CONFIG_FILE.read_bytes()

    def active_jobs(self):
        return bool(self._ui_jobs or self.subtitle.is_running or self.media.busy or
                    getattr(self.launcher, "_launcher_busy", False) or
                    getattr(self.launcher, "_launcher_close_pending", False))

    def run_job(self, function, finished):
        self._ui_jobs += 1
        def worker():
            error = None
            try:
                function()
            except Exception as exc:
                error = exc
            def complete():
                self._ui_jobs -= 1
                finished()
                if error is not None:
                    self.dialogs.showerror("操作未完成", str(error))
            self.dispatcher.after(0, complete)
        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            self._ui_jobs -= 1
            finished()
            raise

    def stop(self):
        self.launcher.stop()
        self.backup.stop()
        self.topmost.stop()
        self.dispatcher.stop()
        for module, name, original in self._dialog_originals:
            setattr(module, name, original)
