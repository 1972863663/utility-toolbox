import json
import os
import re
import shutil
import sys
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Final, List, Optional, Set, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk


class AppConfig:
    APP_NAME: Final[str] = "视频字幕同步器"
    VERSION: Final[str] = "22.0"

    VIDEO_EXTS: Final[Set[str]] = {".mp4", ".mkv", ".avi", ".ts", ".m2ts", ".m4v", ".mov", ".wmv", ".flv"}
    SUB_EXTS: Final[Set[str]] = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup", ".txt"}
    MEDIA_EXTS: Final[Set[str]] = VIDEO_EXTS | SUB_EXTS

    BASE_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "UtilityToolbox"
    HISTORY_FILE = BASE_DIR / "subtitle_sync_history.json"
    TMP_HISTORY = BASE_DIR / "subtitle_sync_history.json.tmp"

    ILLEGAL_MAP_WIN = {
        "?": "？",
        ":": "：",
        "*": "＊",
        '"': "＂",
        "<": "《",
        ">": "》",
        "|": "｜",
        "/": "／",
        "\\": "＼",
    }
    ILLEGAL_MAP_POSIX = {"/": "／"}

    OFFSET_OP_ED: Final[int] = 100
    OFFSET_PROMO: Final[int] = 200
    MAX_SEASON: Final[int] = 99
    PLACEHOLDER_TEXT: Final[str] = "输入剧名..."
    MAX_LOG_LINES: Final[int] = 3000

    OP_ED_KEYWORDS: Final[List[str]] = ["ncop", "nced", "op", "ed", "opening", "ending", "creditless"]
    PROMO_KEYWORDS: Final[List[str]] = ["cm", "pv", "trailer", "preview", "teaser", "promo", "番宣", "予告"]
    SPECIAL_KEYWORDS: Final[List[str]] = [
        "sp",
        "special",
        "ova",
        "oav",
        "oad",
        "ona",
        "extra",
        "bonus",
        "recap",
        "ova特典",
        "番外",
        "特典",
        "总集篇",
        "總集篇",
    ]
    EXTRA_KEYWORDS: Final[List[str]] = OP_ED_KEYWORDS + PROMO_KEYWORDS + SPECIAL_KEYWORDS

    RE_SPLIT_NUM = re.compile(r"(\d+)")
    RE_NOISE = re.compile(
        r"\b(?:480p|576p|720p|1080p|1440p|2160p|4320p|4k|8k|"
        r"bdrip|bdremux|bluray|blu[-_. ]?ray|web[-_. ]?dl|webrip|hdtv|dvdrip|"
        r"xvid|divx|avc|hevc|x265|x264|h\.?264|h\.?265|hi10p|10bit|8bit|"
        r"aac|ac3|eac3|flac|flacx\d|opus|dts|truehd|atmos|ddp\d?\.?\d?|"
        r"hdr10\+?|hdr|dv|dolby[-_. ]?vision|sdr|remux|proper|repack|vostfr|multi|"
        r"part\s*\d+|pt\s*\d+|cd\s*\d+|disc\s*\d+|disk\s*\d+|vol\.?\s*\d+|"
        r"gb|big5|chs|cht|jpn|japanese|eng|english|sc|tc|简体|繁体|简日|繁日|"
        r"简英|繁英|内封|外挂|字幕|字幕组)\b|"
        r"\d{3,5}x\d{3,5}|\d+bpp|\[[0-9a-f]{8,}\]|\([0-9a-f]{8,}\)",
        re.IGNORECASE,
    )
    RE_DATE = re.compile(
        r"(?:19|20)\d{2}[-_. ](?:1[0-2]|0?[1-9])[-_. ](?:3[01]|[12]\d|0?[1-9])|"
        r"(?:3[01]|[12]\d|0?[1-9])[-_. ](?:1[0-2]|0?[1-9])[-_. ](?:19|20)\d{2}"
    )
    RE_SEASON = re.compile(
        r"(?:^|[\s._\-\[])(?:s|season)\s*0*(\d{1,2})(?=e\d|$|[\s._\-\]])|第\s*0*(\d{1,2})\s*季",
        re.IGNORECASE,
    )
    RE_VERSION = re.compile(r"(?:^|[\s._\-\[\(])v(\d{1,2})(?:$|[\s._\-\]\)])", re.IGNORECASE)
    RE_YEAR = re.compile(r"(?:19|20)\d{2}")
    RE_STANDARD_EPISODES = [
        re.compile(
            r"(?:^|[\s._\-\[])(?:s|season)\s*0*(?P<season>\d{1,2})\s*[._\-\s]?"
            r"(?:e|ep)\s*0*(?P<episode>\d{1,4})\s*(?:e|ep)\s*0*(?P<end>\d{1,4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:^|[\s._\-\[])(?:s|season)\s*0*(?P<season>\d{1,2})\s*[._\-\s]?"
            r"(?:e|ep)?\s*0*(?P<episode>\d{1,4})"
            r"(?:\s*(?:-|~|–|—|to|至|到)\s*(?:s\d{1,2}\s*)?(?:e|ep)?\s*0*(?P<end>\d{1,4}))?",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:^|[\s._\-\[])(?P<season>\d{1,2})x0*(?P<episode>\d{1,4})"
            r"(?:\s*(?:x|-|~|–|—|to|至|到)\s*0*(?P<end>\d{1,4}))?",
            re.IGNORECASE,
        ),
        re.compile(
            r"第\s*0*(?P<season>\d{1,2})\s*季\s*(?:第)?\s*0*(?P<episode>\d{1,4})\s*(?:话|話|集|回)"
            r"(?:\s*(?:-|~|–|—|至|到)\s*0*(?P<end>\d{1,4})\s*(?:话|話|集|回)?)?",
            re.IGNORECASE,
        ),
    ]
    RE_ABSOLUTE_EPISODES = [
        re.compile(
            r"(?:#|＃)\s*0*(?P<episode>\d{1,4})(?:v(?P<version>\d{1,2}))?"
            r"(?:\s*(?:-|~|–|—|to|至|到)\s*0*(?P<end>\d{1,4}))?",
            re.IGNORECASE,
        ),
        re.compile(
            r"\[(?P<episode>\d{1,4})(?:\s*(?:-|~|–|—|至|到)\s*(?P<end>\d{1,4}))?(?:v(?P<version>\d{1,2})|end)?\]",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:^|[\s._\-\[])(?:e|ep|episode|第|第\s*)\s*0*(?P<episode>\d{1,4})(?:\s*(?:话|話|集|回))?"
            r"(?:\s*(?:-|~|–|—|to|至|到)\s*(?:e|ep|第)?\s*0*(?P<end>\d{1,4})(?:\s*(?:话|話|集|回))?)?",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:^|[\s._\-])0*(?P<episode>\d{1,4})(?:v(?P<version>\d{1,2}))?"
            r"(?:\s*(?:-|~|–|—|to|至|到)\s*0*(?P<end>\d{1,4}))?(?:$|[\s._\-\]\)])",
            re.IGNORECASE,
        ),
    ]

    @staticmethod
    @lru_cache(maxsize=128)
    def word_re(keyword: str) -> re.Pattern:
        return re.compile(rf"(?<![a-z]){re.escape(keyword)}(?![a-z])", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedFile:
    season: int
    episode: Optional[int]
    episode_end: Optional[int]
    version: Optional[int]
    season_out_of_bounds: bool
    is_extra: bool
    rule: str


@dataclass(frozen=True)
class RenameItem:
    source: Path
    target: Optional[Path]
    parsed: ParsedFile
    status: str
    message: str


class RenameLogic:
    @staticmethod
    def sanitize(name: str) -> Tuple[str, bool]:
        char_map = AppConfig.ILLEGAL_MAP_WIN if os.name == "nt" else AppConfig.ILLEGAL_MAP_POSIX
        clean_name = "".join(char_map.get(c, c) for c in name.strip() if ord(c) >= 32)
        return clean_name, clean_name != name

    @staticmethod
    def natural_key(path: Path) -> List[object]:
        return [int(part) if part.isdigit() else part.lower() for part in AppConfig.RE_SPLIT_NUM.split(path.name)]

    @staticmethod
    def language_tag(name: str) -> str:
        lower_name = name.lower()
        if any(token in lower_name for token in ["简", "chs", "sc", "gb", "zh-hans"]):
            return "[sc]"
        if any(token in lower_name for token in ["繁", "cht", "tc", "big5", "zh-hant"]):
            return "[tc]"
        if any(token in lower_name for token in ["jpn", "jp", "日文"]):
            return "[jp]"
        if any(token in lower_name for token in ["eng", "en", "英文"]):
            return "[en]"
        return ""

    @staticmethod
    def _is_word_present(text: str, keywords: List[str]) -> bool:
        return any(AppConfig.word_re(keyword).search(text) for keyword in keywords)

    @staticmethod
    def _chinese_number_to_int(text: str) -> Optional[int]:
        digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if not text or any(ch not in digits and ch not in "十百" for ch in text):
            return None

        total = 0
        section = 0
        current = 0
        for ch in text:
            if ch in digits:
                current = digits[ch]
            elif ch == "十":
                section += (current or 1) * 10
                current = 0
            elif ch == "百":
                section += (current or 1) * 100
                current = 0
        total = section + current
        return total if total > 0 else None

    @staticmethod
    def _normalize_text(text: str) -> str:
        fullwidth = str.maketrans("０１２３４５６７８９", "0123456789")
        normalized = text.translate(fullwidth)

        def replace_chinese_number(match: re.Match) -> str:
            value = RenameLogic._chinese_number_to_int(match.group(1))
            return f"第{value}{match.group(2)}" if value is not None else match.group(0)

        return re.sub(r"第([零〇一二两三四五六七八九十百]+)(季|话|話|集|回)", replace_chinese_number, normalized)

    @staticmethod
    def _clean_stem(stem: str) -> str:
        cleaned = AppConfig.RE_NOISE.sub(" ", stem.lower())
        cleaned = AppConfig.RE_DATE.sub(" ", cleaned)
        cleaned = re.sub(r"[\[\]\(\)【】「」『』]", " ", cleaned)
        cleaned = re.sub(r"[._]+", " ", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def _safe_episode(value: Optional[str]) -> Optional[int]:
        if not value:
            return None
        episode = int(value)
        if episode <= 0:
            return None
        if AppConfig.RE_YEAR.fullmatch(value) and episode >= 1900:
            return None
        return episode

    @staticmethod
    def _version_from_match(match: Optional[re.Match], stem_lower: str) -> Optional[int]:
        if match:
            value = match.groupdict().get("version")
            if value:
                return int(value)
        version_match = AppConfig.RE_VERSION.search(stem_lower)
        return int(version_match.group(1)) if version_match else None

    @staticmethod
    def _special_offset(stem_lower: str) -> Tuple[bool, int, str]:
        if RenameLogic._is_word_present(stem_lower, AppConfig.OP_ED_KEYWORDS):
            return True, AppConfig.OFFSET_OP_ED, "op_ed"
        if RenameLogic._is_word_present(stem_lower, AppConfig.PROMO_KEYWORDS):
            return True, AppConfig.OFFSET_PROMO, "promo"
        if RenameLogic._is_word_present(stem_lower, AppConfig.SPECIAL_KEYWORDS):
            return True, 0, "special"
        return False, 0, "normal"

    @staticmethod
    def parse_file(filename: str, fallback_season: int) -> ParsedFile:
        stem = RenameLogic._normalize_text(Path(filename).stem)
        stem_lower = stem.lower()
        clean_name = RenameLogic._clean_stem(stem)
        is_extra, offset, special_rule = RenameLogic._special_offset(stem_lower)

        season_out_of_bounds = False
        season_match = AppConfig.RE_SEASON.search(stem_lower)
        if is_extra:
            season = 0
        elif season_match:
            season_value = int(next(group for group in season_match.groups() if group))
            if 0 <= season_value <= AppConfig.MAX_SEASON:
                season = season_value
            else:
                season = fallback_season
                season_out_of_bounds = True
        else:
            season = fallback_season

        episode = None
        episode_end = None
        version = None
        rule = special_rule

        for pattern in AppConfig.RE_STANDARD_EPISODES:
            match = pattern.search(clean_name)
            if match:
                parsed_season = int(match.group("season"))
                if not is_extra and 0 <= parsed_season <= AppConfig.MAX_SEASON:
                    season = parsed_season
                elif not is_extra:
                    season_out_of_bounds = True
                episode = RenameLogic._safe_episode(match.group("episode"))
                episode_end = RenameLogic._safe_episode(match.groupdict().get("end"))
                version = RenameLogic._version_from_match(match, stem_lower)
                rule = "season_episode"
                break

        if episode is None:
            text_without_season = AppConfig.RE_SEASON.sub(" ", clean_name)
            for pattern in AppConfig.RE_ABSOLUTE_EPISODES:
                match = pattern.search(text_without_season)
                if match:
                    episode = RenameLogic._safe_episode(match.group("episode"))
                    episode_end = RenameLogic._safe_episode(match.groupdict().get("end"))
                    version = RenameLogic._version_from_match(match, stem_lower)
                    rule = special_rule if is_extra else "absolute_episode"
                    break

        if episode is None:
            numbers = [
                n
                for n in re.findall(r"\d+", AppConfig.RE_SEASON.sub(" ", clean_name))
                if len(n) <= 4 and not AppConfig.RE_YEAR.fullmatch(n)
            ]
            if numbers:
                episode = RenameLogic._safe_episode(numbers[0])
                version = RenameLogic._version_from_match(None, stem_lower)
                rule = special_rule if is_extra else "numeric_fallback"
            elif is_extra:
                episode = 1
                version = RenameLogic._version_from_match(None, stem_lower)

        if episode is not None:
            episode += offset
        if episode_end is not None:
            episode_end += offset
            if episode_end <= episode:
                episode_end = None

        return ParsedFile(season, episode, episode_end, version, season_out_of_bounds, is_extra, rule)

    @staticmethod
    def make_target_name(show_name: str, source: Path, parsed: ParsedFile) -> str:
        tag = RenameLogic.language_tag(source.name) if source.suffix.lower() in AppConfig.SUB_EXTS else ""
        episode_label = f"E{parsed.episode:02d}"
        if parsed.episode_end is not None:
            episode_label = f"{episode_label}-E{parsed.episode_end:02d}"
        stem = f"{show_name} - S{parsed.season:02d}{episode_label}"
        if parsed.version and parsed.version > 1:
            stem = f"{stem} v{parsed.version}"
        if tag:
            stem = f"{stem} {tag}"
        return f"{stem}{source.suffix.lower()}"

    @staticmethod
    def build_plan(files: List[Path], show_name: str, fallback_season: int) -> List[RenameItem]:
        target_count: Dict[str, int] = {}
        raw_items: List[RenameItem] = []

        for source in sorted(files, key=RenameLogic.natural_key):
            parsed = RenameLogic.parse_file(source.name, fallback_season)
            if parsed.episode is None:
                raw_items.append(RenameItem(source, None, parsed, "skip", "未识别到集数"))
                continue

            target = source.with_name(RenameLogic.make_target_name(show_name, source, parsed))
            target_count[target.name.lower()] = target_count.get(target.name.lower(), 0) + 1
            raw_items.append(RenameItem(source, target, parsed, "ready", ""))

        planned: List[RenameItem] = []
        for item in raw_items:
            if item.target is None:
                planned.append(item)
                continue

            if item.parsed.season_out_of_bounds:
                message = f"季数超出范围，已回退到 S{item.parsed.season:02d}"
            else:
                message = ""

            target_key = item.target.name.lower()
            if target_count[target_key] > 1:
                planned.append(RenameItem(item.source, item.target, item.parsed, "conflict", "多个文件会生成同一个目标名"))
            elif item.source.name.lower() == item.target.name.lower():
                planned.append(RenameItem(item.source, item.target, item.parsed, "same", "名称已符合规则"))
            elif item.target.exists():
                planned.append(RenameItem(item.source, item.target, item.parsed, "conflict", "目标文件已存在"))
            else:
                planned.append(RenameItem(item.source, item.target, item.parsed, "ready", message))

        return planned


class MainApp:
    def __init__(self, root: tk.Tk, embedded: bool = False):
        self.root = root
        self.embedded = embedded
        self.scale = self._current_scale()
        self.history_db: Dict[str, Dict[str, str]] = {}
        self.is_running = False
        self.target_dir: Optional[Path] = None

        self.setup_ui()
        self._load_history_db()
        if not self.embedded and hasattr(self.root, "protocol"):
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
            self.root.after(10, self._check_visibility_and_center)

    def _current_scale(self) -> float:
        try:
            return max(0.9, min(1.8, self.root.winfo_fpixels("1i") / 96.0))
        except tk.TclError:
            return 1.0

    def _scale(self, value: int) -> int:
        return max(1, round(value * self.scale))

    def _check_visibility_and_center(self):
        if self.root.winfo_viewable():
            self._center_window()
        else:
            self.root.after(50, self._check_visibility_and_center)

    def _center_window(self):
        if self.embedded:
            return
        self.root.update_idletasks()
        width, height = 1050, 880
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.root.geometry(f"{width}x{height}+{(screen_width - width) // 2}+{(screen_height - height) // 2}")

    def on_closing(self):
        if self.is_running:
            if not messagebox.askokcancel(
                "任务仍在运行",
                "改名任务还没有结束。\n此时退出会停止后续文件处理，已完成的文件不会自动回滚。\n\n确认退出吗？",
            ):
                return
            self.is_running = False
        if not self.embedded:
            self.root.destroy()

    def setup_ui(self):
        self._setup_theme()

        container = ttk.Frame(self.root, padding=self._scale(25), style="App.TFrame")
        container.pack(fill=tk.BOTH, expand=True)

        head = ttk.Frame(container, style="App.TFrame")
        head.pack(fill=tk.X, pady=(0, 15))
        ttk.Label(
            head,
            text=AppConfig.APP_NAME,
            font=("Microsoft YaHei UI", self._scale(30), "bold"),
            style="Title.TLabel",
        ).pack(side=tk.LEFT)

        cfg = ttk.LabelFrame(container, text=" 任务控制 ", padding=self._scale(15), style="App.TLabelframe")
        cfg.pack(fill=tk.X, pady=10)

        row = ttk.Frame(cfg, style="Panel.TFrame")
        row.pack(fill=tk.X, pady=5)
        row.columnconfigure(1, weight=1)
        ttk.Label(row, text="项目剧名:", style="App.TLabel").grid(row=0, column=0, padx=5, pady=4, sticky=tk.W)

        placeholder_color = "#64748b"
        self.name_ent = tk.Entry(
            row,
            bg="#ffffff",
            fg=placeholder_color,
            insertbackground="#1f2937",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#d7e0ea",
            highlightcolor="#2563eb",
            font=("Microsoft YaHei UI", self._scale(14)),
        )
        self.name_ent.grid(row=0, column=1, padx=5, pady=4, ipady=self._scale(5), sticky=tk.EW)
        self.name_ent.insert(0, AppConfig.PLACEHOLDER_TEXT)
        self.name_ent.bind("<FocusIn>", self._on_focus_in)
        self.name_ent.bind("<FocusOut>", self._on_focus_out)

        ttk.Label(row, text="默认季数:", style="App.TLabel").grid(row=0, column=2, padx=5, pady=4, sticky=tk.W)
        validate_season = self.root.register(lambda value: value.isdigit() or value == "")
        self.season_ent = tk.Entry(
            row,
            width=8,
            justify="center",
            validate="key",
            validatecommand=(validate_season, "%P"),
            bg="#ffffff",
            fg="#1f2937",
            insertbackground="#1f2937",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#d7e0ea",
            highlightcolor="#2563eb",
            font=("Microsoft YaHei UI", self._scale(14)),
        )
        self.season_ent.grid(row=0, column=3, padx=5, pady=4, ipady=self._scale(5), sticky=tk.EW)
        self.season_ent.insert(0, "1")

        self.dry_run = tk.BooleanVar(value=True)
        self.cb_test = ttk.Checkbutton(
            cfg,
            text="测试模式：只预览，不改动文件",
            variable=self.dry_run,
            style="App.TCheckbutton",
        )
        self.cb_test.pack(anchor=tk.W, pady=10)

        self.progress = ttk.Progressbar(container, mode="determinate", style="App.Horizontal.TProgressbar")
        self.progress.pack(fill=tk.X, pady=10)

        btn_frame = ttk.Frame(container, style="App.TFrame")
        btn_frame.pack(fill=tk.X, pady=10)
        for col in range(3):
            btn_frame.columnconfigure(col, weight=1, uniform="subtitle_actions")

        self.btn_dir = ttk.Button(btn_frame, text="选择目录", command=self.select_dir, style="App.TButton")
        self.btn_dir.grid(row=0, column=0, padx=5, pady=4, sticky=tk.EW)
        self.btn_run = ttk.Button(btn_frame, text="同步对齐", command=self.start_task, style="Accent.TButton")
        self.btn_run.grid(row=0, column=1, padx=5, pady=4, sticky=tk.EW)
        self.btn_audit = ttk.Button(btn_frame, text="自动校对", command=self.run_audit, style="App.TButton")
        self.btn_audit.grid(row=0, column=2, padx=5, pady=4, sticky=tk.EW)
        self.btn_stop = ttk.Button(btn_frame, text="停止", command=self.stop_task, style="Danger.TButton", state=tk.DISABLED)
        self.btn_stop.grid(row=1, column=0, padx=5, pady=4, sticky=tk.EW)
        self.btn_clear = ttk.Button(btn_frame, text="清空日志", command=self.clear_log, style="App.TButton")
        self.btn_clear.grid(row=1, column=1, padx=5, pady=4, sticky=tk.EW)
        self.btn_undo = ttk.Button(
            btn_frame,
            text="撤销该目录",
            command=self.undo,
            style="Danger.TButton",
            state=tk.DISABLED,
        )
        self.btn_undo.grid(row=1, column=2, padx=5, pady=4, sticky=tk.EW)

        self.log_area = scrolledtext.ScrolledText(
            container,
            height=20,
            font=("Consolas", self._scale(14)),
            bg="#ffffff",
            fg="#1f2937",
            insertbackground="#1f2937",
            selectbackground="#dbeafe",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#d7e0ea",
        )
        self.log_area.pack(fill=tk.BOTH, expand=True, pady=10)
        self._init_log_styles()

    def _setup_theme(self):
        self.root.configure(bg="#f5f7fb")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        button_padding = (self._scale(14), self._scale(9))
        style.configure("App.TFrame", background="#f5f7fb")
        style.configure("Panel.TFrame", background="#f5f7fb")
        style.configure("App.TLabel", background="#f5f7fb", foreground="#1f2937", font=("Microsoft YaHei UI", self._scale(14)))
        style.configure("Title.TLabel", background="#f5f7fb", foreground="#2563eb")
        style.configure("App.TLabelframe", background="#f5f7fb", foreground="#1f2937", bordercolor="#d7e0ea")
        style.configure("App.TLabelframe.Label", background="#f5f7fb", foreground="#64748b", font=("Microsoft YaHei UI", self._scale(16), "bold"))
        style.configure("App.TCheckbutton", background="#f5f7fb", foreground="#1f2937", font=("Microsoft YaHei UI", self._scale(14)))
        style.map("App.TCheckbutton", background=[("active", "#f5f7fb")], foreground=[("active", "#1f2937")])
        style.configure("App.TButton", background="#ffffff", foreground="#1f2937", bordercolor="#d7e0ea", borderwidth=1, padding=button_padding, font=("Microsoft YaHei UI", self._scale(14)))
        style.map("App.TButton", background=[("active", "#eef3f8"), ("disabled", "#eef3f8")], foreground=[("disabled", "#94a3b8")])
        style.configure("Accent.TButton", background="#2563eb", foreground="#ffffff", bordercolor="#1d4ed8", borderwidth=1, padding=button_padding, font=("Microsoft YaHei UI", self._scale(14), "bold"))
        style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("disabled", "#94a3b8")])
        style.configure("Danger.TButton", background="#dc2626", foreground="#ffffff", bordercolor="#b91c1c", borderwidth=1, padding=button_padding, font=("Microsoft YaHei UI", self._scale(14)))
        style.map("Danger.TButton", background=[("active", "#b91c1c"), ("disabled", "#94a3b8")])
        style.configure("App.Horizontal.TProgressbar", troughcolor="#eef3f8", background="#0f766e", bordercolor="#d7e0ea")

    def _init_log_styles(self):
        styles = {
            "vid": "#2563eb",
            "sub": "#0f766e",
            "success": "#0f766e",
            "err": "#dc2626",
            "info": "#7c3aed",
            "warn": "#b45309",
            "audit": "#be185d",
        }
        for tag, color in styles.items():
            self.log_area.tag_config(tag, foreground=color)

    def _on_focus_in(self, _event):
        if self.name_ent.get() == AppConfig.PLACEHOLDER_TEXT:
            self.name_ent.delete(0, tk.END)
            self.name_ent.config(fg="#1f2937")

    def _on_focus_out(self, _event):
        raw = self.name_ent.get().strip()
        if raw:
            return
        placeholder_color = "#64748b"
        self.name_ent.config(fg=placeholder_color)
        self.name_ent.insert(0, AppConfig.PLACEHOLDER_TEXT)

    def safe_log(self, msg: str, tag: Optional[str] = None):
        def task():
            self.log_area.insert(tk.END, str(msg) + "\n", tag)
            if int(self.log_area.index("end-1c").split(".")[0]) > AppConfig.MAX_LOG_LINES:
                self.log_area.delete("1.0", "500.0")
            self.log_area.see(tk.END)

        self.root.after(0, task)

    def safe_ui(self, widget: tk.Widget, **kwargs):
        def task():
            value = kwargs.pop("val", None)
            maximum = kwargs.pop("maximum", None)
            text_value = kwargs.pop("text_value", None)
            if value is not None:
                widget["value"] = value
            if maximum is not None:
                widget["maximum"] = maximum
            if text_value is not None and hasattr(widget, "delete"):
                widget.delete(0, tk.END)
                widget.insert(0, text_value)
            if kwargs:
                widget.configure(**kwargs)

        self.root.after(0, task)

    def clear_log(self):
        self.log_area.delete("1.0", tk.END)

    def select_dir(self):
        directory = filedialog.askdirectory()
        if not directory:
            return

        path = Path(directory)
        probe = path / ".permission_probe.tmp"
        try:
            probe.touch()
            probe.unlink()
        except OSError:
            messagebox.showerror("系统拦截", f"文件夹拒绝访问或受写保护：{directory}\n请检查权限或更换目录。")
            return

        self.target_dir = path
        folder_key = path.as_posix()
        self.safe_log(f"工作目录：{directory}", "info")
        undo_state = tk.NORMAL if folder_key in self.history_db and self.history_db[folder_key] else tk.DISABLED
        self.safe_ui(self.btn_undo, state=undo_state)

    def stop_task(self):
        self.is_running = False
        self.safe_log("正在停止任务，当前文件处理完成后会退出队列。", "warn")

    def _read_inputs(self) -> Optional[Tuple[str, int, bool, Path]]:
        if not self.target_dir:
            messagebox.showwarning("缺少目录", "请先选择要处理的目录。")
            return None

        raw_name = self.name_ent.get().strip()
        if not raw_name or raw_name == AppConfig.PLACEHOLDER_TEXT:
            messagebox.showwarning("非法输入", "项目剧名不能为空。")
            return None

        show_name, changed = RenameLogic.sanitize(raw_name)
        if not show_name:
            messagebox.showwarning("非法输入", "项目剧名不能只包含非法字符。")
            return None
        if changed:
            self.safe_log(f"剧名包含保留字符，已自动转换为：{show_name}", "warn")

        try:
            fallback_season = int(self.season_ent.get() or "1")
            if not (0 <= fallback_season <= AppConfig.MAX_SEASON):
                raise ValueError
        except ValueError:
            fallback_season = 1
            self.safe_log("默认季数无效，已重置为 1。", "warn")
            self.safe_ui(self.season_ent, text_value="1")

        return show_name, fallback_season, self.dry_run.get(), self.target_dir

    def start_task(self):
        if self.is_running:
            return
        inputs = self._read_inputs()
        if inputs is None:
            return

        show_name, fallback_season, is_dry_run, current_dir = inputs
        self.is_running = True
        self.safe_ui(self.btn_run, state=tk.DISABLED)
        self.safe_ui(self.btn_audit, state=tk.DISABLED)
        self.safe_ui(self.btn_stop, state=tk.NORMAL)
        threading.Thread(
            target=self.batch_worker,
            args=(show_name, fallback_season, is_dry_run, current_dir),
            daemon=True,
        ).start()

    def batch_worker(self, show_name: str, fallback_season: int, is_dry_run: bool, current_dir: Path):
        folder_key = current_dir.as_posix()
        try:
            files = [item for item in current_dir.iterdir() if item.is_file() and item.suffix.lower() in AppConfig.MEDIA_EXTS]
            plan = RenameLogic.build_plan(files, show_name, fallback_season)
            self.safe_ui(self.progress, val=0, maximum=max(len(plan), 1))

            mode = "预览" if is_dry_run else "执行"
            self.safe_log(f"[{mode}] 共发现 {len(files)} 个可处理文件。", "info")

            summary = {"ready": 0, "same": 0, "skip": 0, "conflict": 0, "done": 0, "failed": 0}
            if folder_key not in self.history_db:
                self.history_db[folder_key] = {}

            for index, item in enumerate(plan, 1):
                if not self.is_running:
                    self.safe_log("任务已停止。", "warn")
                    break

                self.safe_ui(self.progress, val=index)
                summary[item.status] = summary.get(item.status, 0) + 1

                if item.status == "skip":
                    self.safe_log(f"跳过：{item.source.name}，{item.message}", "warn")
                    continue
                if item.status == "same":
                    self.safe_log(f"已符合规则：{item.source.name}", "info")
                    continue
                if item.status == "conflict":
                    self.safe_log(f"冲突：{item.source.name} -> {item.target.name}，{item.message}", "err")
                    continue

                if item.message:
                    self.safe_log(f"提醒：{item.source.name}，{item.message}", "warn")

                log_tag = "vid" if item.source.suffix.lower() in AppConfig.VIDEO_EXTS else "sub"
                if is_dry_run:
                    self.safe_log(f"计划：{item.source.name}\n  -> {item.target.name}", log_tag)
                    continue

                try:
                    if item.target.exists():
                        raise FileExistsError(f"目标文件已存在：{item.target.name}")
                    shutil.move(str(item.source), str(item.target))
                    self.history_db[folder_key][item.target.name] = item.source.name
                    summary["done"] += 1
                    self.safe_log(f"完成：{item.target.name}", "success")
                except PermissionError:
                    summary["failed"] += 1
                    self.safe_log(f"失败：文件被占用，请关闭播放器或下载器 -> {item.source.name}", "err")
                except OSError as exc:
                    summary["failed"] += 1
                    self.safe_log(f"失败：{item.source.name} -> {exc}", "err")

            if not is_dry_run:
                self._save_history_db()

            self.safe_log(
                f"完成统计：可改名 {summary['ready']}，已执行 {summary['done']}，"
                f"已符合 {summary['same']}，跳过 {summary['skip']}，冲突 {summary['conflict']}，失败 {summary['failed']}",
                "success" if summary["failed"] == 0 and summary["conflict"] == 0 else "warn",
            )
        finally:
            self.is_running = False
            self.safe_ui(self.btn_run, state=tk.NORMAL)
            self.safe_ui(self.btn_audit, state=tk.NORMAL)
            self.safe_ui(self.btn_stop, state=tk.DISABLED)
            self.root.after(0, lambda: self.dry_run.set(True))
            AppConfig.word_re.cache_clear()
            if folder_key in self.history_db and self.history_db[folder_key]:
                self.safe_ui(self.btn_undo, state=tk.NORMAL)

    def run_audit(self):
        if self.is_running:
            return
        if not self.target_dir:
            messagebox.showwarning("缺少目录", "请先选择要校对的目录。")
            return

        try:
            fallback_season = int(self.season_ent.get() or "1")
        except ValueError:
            fallback_season = 1

        self.clear_log()
        self.safe_log("开始校对视频与字幕映射。", "audit")
        self.is_running = True
        self.safe_ui(self.btn_run, state=tk.DISABLED)
        self.safe_ui(self.btn_audit, state=tk.DISABLED)
        self.safe_ui(self.btn_stop, state=tk.NORMAL)
        threading.Thread(target=self.audit_worker, args=(self.target_dir, fallback_season), daemon=True).start()

    def audit_worker(self, current_dir: Path, fallback_season: int):
        try:
            files = [item for item in current_dir.iterdir() if item.is_file() and item.suffix.lower() in AppConfig.MEDIA_EXTS]
            self.safe_ui(self.progress, val=0, maximum=max(len(files), 1))
            results: Dict[Tuple[int, int], Dict[str, List[Path]]] = {}

            for index, file_path in enumerate(sorted(files, key=RenameLogic.natural_key), 1):
                if not self.is_running:
                    self.safe_log("校对已停止。", "warn")
                    break
                parsed = RenameLogic.parse_file(file_path.name, fallback_season)
                if parsed.episode is not None:
                    key = (parsed.season, parsed.episode)
                    results.setdefault(key, {"v": [], "s": []})
                    bucket = "v" if file_path.suffix.lower() in AppConfig.VIDEO_EXTS else "s"
                    results[key][bucket].append(file_path)
                self.safe_ui(self.progress, val=index)

            issues: List[Tuple[str, str]] = []
            for key in sorted(results.keys()):
                group = results[key]
                season_ep = f"S{key[0]:02d}E{key[1]:02d}"
                if group["v"] and not group["s"]:
                    for video in group["v"]:
                        issues.append((f"视频缺字幕：{video.name} ({season_ep})", "warn"))
                if group["s"] and not group["v"]:
                    for sub in group["s"]:
                        issues.append((f"字幕缺视频：{sub.name} ({season_ep})", "warn"))
                if len(group["v"]) > 1:
                    issues.append((f"同一集存在多个视频：{season_ep}", "err"))
                if len(group["s"]) > 1:
                    issues.append((f"同一集存在多个字幕：{season_ep}", "warn"))

            if not issues:
                self.safe_log("校对通过：视频与字幕已能按集数匹配。", "success")
            else:
                for message, tag in issues:
                    self.safe_log(message, tag)
                self.safe_log(f"校对完成：发现 {len(issues)} 个问题。", "warn")
        finally:
            self.is_running = False
            self.safe_ui(self.btn_run, state=tk.NORMAL)
            self.safe_ui(self.btn_audit, state=tk.NORMAL)
            self.safe_ui(self.btn_stop, state=tk.DISABLED)
            self.safe_ui(self.progress, val=0, maximum=100)
            AppConfig.word_re.cache_clear()

    def _save_history_db(self):
        try:
            AppConfig.BASE_DIR.mkdir(parents=True, exist_ok=True)
            with open(AppConfig.TMP_HISTORY, "w", encoding="utf-8") as file:
                json.dump(self.history_db, file, ensure_ascii=False, indent=2)
            os.replace(AppConfig.TMP_HISTORY, AppConfig.HISTORY_FILE)
        except Exception as exc:
            self.safe_log(f"历史记录保存失败：{exc}", "err")

    def _load_history_db(self):
        if not AppConfig.HISTORY_FILE.exists():
            return
        try:
            with open(AppConfig.HISTORY_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict):
                self.history_db = data
        except Exception:
            self.history_db = {}

    def undo(self):
        if not self.target_dir:
            return

        folder_key = self.target_dir.as_posix()
        mapping = self.history_db.get(folder_key)
        if not mapping:
            messagebox.showinfo("信息", "当前目录没有可撤销的历史记录。")
            return

        self.safe_ui(self.btn_undo, state=tk.DISABLED)
        count = 0
        undone_keys: List[str] = []

        for new_name, old_name in reversed(list(mapping.items())):
            new_path = self.target_dir / new_name
            old_path = self.target_dir / old_name
            if not new_path.exists():
                undone_keys.append(new_name)
                continue
            if old_path.exists():
                old_path = old_path.with_name(f"{old_path.stem}_recovered{old_path.suffix}")

            try:
                shutil.move(str(new_path), str(old_path))
                undone_keys.append(new_name)
                count += 1
            except PermissionError:
                self.safe_log(f"撤销失败：文件被占用 -> {new_name}", "err")
            except OSError as exc:
                self.safe_log(f"撤销失败：{new_name} -> {exc}", "err")

        for key in undone_keys:
            mapping.pop(key, None)
        if not mapping:
            self.history_db.pop(folder_key, None)

        self._save_history_db()
        self.safe_log(f"撤销完成：恢复 {count} 个文件。", "success")
        if self.history_db.get(folder_key):
            self.safe_ui(self.btn_undo, state=tk.NORMAL)


if __name__ == "__main__":
    if os.name == "nt":
        try:
            from ctypes import windll

            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    app_root = tk.Tk()
    app_root.title(AppConfig.APP_NAME)
    MainApp(app_root)
    app_root.mainloop()
