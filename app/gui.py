"""Tkinter desktop GUI for PDF exam formatting."""

from __future__ import annotations

import ctypes
import json
import os
import re
import threading
import traceback
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from queue import Empty, Queue
from typing import Any
from urllib.parse import unquote

import cv2
import numpy as np
import tkinter as tk
from PIL import Image, ImageGrab, ImageTk
from tkinter import filedialog, messagebox, ttk

from app.config import AppConfig, load_config
from app.io_utils import import_pymupdf, read_image, write_image
from app.layout_engine import paginate_problems, parse_row_pattern_spec
from app.models import CroppedProblem
from app.pipeline import PipelineProgress, PipelineResult, run_pipeline
from app.pdf_renderer import render_pdf
from app.runtime_paths import app_home, resource_root

APP_TITLE = "题目图片自动排版成 PDF"
EMPTY_PREVIEW_TEXT = "暂无预览。"
SUPPORTED_INPUT_LABEL = "图片 / PDF (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.pdf)"
SUPPORTED_INPUT_PATTERNS = "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.pdf"
GUI_STATE_FILE = "gui_state.json"
SUPPORTED_INPUT_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".pdf"}
WINDOWS_DROP_ENABLED = os.name == "nt"
MODE_LABEL_TO_VALUE = {"标准": "normal", "快速": "fast"}
MODE_VALUE_TO_LABEL = {value: label for label, value in MODE_LABEL_TO_VALUE.items()}
STAGE_LABELS = {
    "input": "读取输入",
    "template": "解析模板",
    "detect": "检测小题",
    "crop": "安全裁剪",
    "layout": "页面排版",
    "render": "生成 PDF",
    "debug": "导出调试结果",
    "done": "处理完成",
}
REWORK_PROFILES: tuple[tuple[str, str], ...] = (
    ("conservative_split", "保守分题"),
    ("strict_anti_merge", "强力防误并"),
    ("band_fallback", "按题带兜底"),
)
GUI_COLORS = {
    "window_bg": "#eaf2fb",
    "panel_bg": "#f7fbff",
    "surface_bg": "#ffffff",
    "accent": "#005aa0",
    "accent_dark": "#0b3f73",
    "accent_soft": "#d8e6f6",
    "border": "#b8cade",
    "text": "#17324d",
    "muted_text": "#5f7387",
    "selection": "#0b62a4",
}
LAYOUT_PATTERN_LABELS: dict[str, str] = {
    "auto": "自动",
    "vertical": "上下排版（1+1）",
    "horizontal": "左右排版（2 / 3）",
    "top1_bottom2": "上1下2（1+2）",
    "top2_bottom1": "上2下1（2+1）",
    "three_rows": "三行排版（1+1+1）",
    "custom": "自定义",
}

if WINDOWS_DROP_ENABLED:
    from ctypes import wintypes

    LONG_PTR = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
    GWL_WNDPROC = -4
    WM_DROPFILES = 0x0233
    WNDPROC = ctypes.WINFUNCTYPE(LONG_PTR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class FormatterGUI(ttk.Frame):
    """Desktop GUI wrapper around the existing formatter pipeline."""

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=16, style="App.TFrame")
        self.master = master
        self.style = ttk.Style(master)
        self.colors = GUI_COLORS
        self.project_root = app_home()
        self.state_dir = self.project_root / ".cache"
        self.state_file = self.state_dir / GUI_STATE_FILE
        self.message_queue: Queue[tuple[str, Any]] = Queue()
        self.worker: threading.Thread | None = None
        self.last_result: PipelineResult | None = None
        self.last_run_config: AppConfig | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.preview_source_image: Image.Image | None = None
        self.background_photo: ImageTk.PhotoImage | None = None
        self.empty_preview_photo: ImageTk.PhotoImage | None = None
        self.last_progress_message = ""
        self.drop_status_note = "可直接把图片或 PDF 拖到这个窗口里。" if WINDOWS_DROP_ENABLED else ""
        self._drop_wndproc: Any = None
        self._drop_old_wndproc: Any = None
        self._drop_hwnd: int | None = None
        self.current_run_reason = "标准处理"
        self.rework_attempt_index = 0

        self.input_paths: list[str] = []
        self.output_dir_var = tk.StringVar(value=str((self.project_root / "outputs").resolve()))
        self.output_name_var = tk.StringVar(value="result.pdf")
        self.mode_var = tk.StringVar(value="标准")
        self.debug_var = tk.BooleanVar(value=False)
        self.pdf_watermark_var = tk.BooleanVar(value=True)
        self.auto_start_on_paste_var = tk.BooleanVar(value=False)
        self.strict_single_question_var = tk.BooleanVar(value=False)
        self.dpi_var = tk.StringVar(value="220")
        self.per_page_var = tk.StringVar(value="4")
        self.layout_pattern_mode_var = tk.StringVar(value="auto")
        self.layout_pattern_display_var = tk.StringVar(value="自动")
        self.custom_layout_pattern_var = tk.StringVar()
        self.layout_template_var = tk.StringVar()
        self.template_lock_per_page_var = tk.BooleanVar(value=True)
        self.config_path_var = tk.StringVar()
        self.header_config_var = tk.StringVar()
        self.student_id_var = tk.StringVar()
        self.class_name_var = tk.StringVar()
        self.student_name_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")
        self.progress_label_var = tk.StringVar(value="空闲中")
        self.progress_value_var = tk.DoubleVar(value=0.0)
        self.preview_info_var = tk.StringVar(value=EMPTY_PREVIEW_TEXT)
        self.compact_mode = True
        self.compact_toggle_text_var = tk.StringVar(value="显示详细面板")
        self.advanced_visible = False
        self.advanced_toggle_text_var = tk.StringVar(value="显示高级设置")
        self.manual_page_var = tk.StringVar(value="1")
        self.manual_step_var = tk.StringVar(value="5")
        self.manual_status_var = tk.StringVar(value="手动校正未启用")
        self.manual_panel_visible = False
        self.manual_toggle_text_var = tk.StringVar(value="显示手动校正")
        self.manual_focus_view = True
        self.manual_view_toggle_text_var = tk.StringVar(value="查看整页")
        self.manual_zoom_var = tk.DoubleVar(value=170.0)
        self.manual_zoom_text_var = tk.StringVar(value="缩放 170%")
        self.manual_adjustments: dict[str, dict[str, int]] = {}
        self.manual_page_problem_id: dict[int, str] = {}
        self.manual_problem_index: dict[str, int] = {}
        self.manual_work_crops: list[CroppedProblem] = []
        self.manual_base_crops: dict[str, CroppedProblem] = {}
        self.manual_page_image_by_index: dict[int, np.ndarray] = {}
        self.manual_canvas_photo: ImageTk.PhotoImage | None = None
        self.manual_canvas_transform: dict[str, float] = {}
        self.manual_canvas_rect_id: int | None = None
        self.manual_drag_edge: str | None = None
        self.manual_tools_available = False

        # PDF "material selection" mode:
        # when enabled, user drags rectangles on a rasterized PDF page,
        # and each selection is saved as an image input automatically.
        self.material_mode_var = tk.BooleanVar(value=False)
        self.material_page_var = tk.StringVar(value="1")
        self.material_source_pdf_path: Path | None = None
        self.material_source_bgr_image: np.ndarray | None = None
        self.material_source_page_total: int = 1
        self.material_canvas_photo: ImageTk.PhotoImage | None = None
        self.material_canvas_transform: dict[str, float] = {}
        self.material_canvas_rect_id: int | None = None
        self.material_drag_start: tuple[float, float] | None = None
        self.material_selection_count: int = 0
        self.material_delete_target_var = tk.StringVar(value="删除来源PDF(仅从列表移除)")
        self.material_fullscreen_win: tk.Toplevel | None = None
        self.fullscreen_material_canvas: tk.Canvas | None = None
        self.fullscreen_material_canvas_photo: ImageTk.PhotoImage | None = None
        self.fullscreen_material_canvas_transform: dict[str, float] = {}
        self.fullscreen_material_canvas_rect_id: int | None = None
        self.fullscreen_material_drag_start: tuple[float, float] | None = None
        self.material_auto_advance_var = tk.BooleanVar(value=True)

        self.preview_fullscreen_win: tk.Toplevel | None = None
        self.preview_fullscreen_photo: ImageTk.PhotoImage | None = None

        self._configure_theme()
        self._build_ui()
        self.per_page_var.trace_add("write", self._on_per_page_value_changed)
        self.layout_pattern_mode_var.trace_add("write", self._on_layout_pattern_mode_changed)
        self.master.bind("<Control-v>", self._on_paste_shortcut, add="+")
        self.master.bind("<Control-V>", self._on_paste_shortcut, add="+")
        self._sync_strict_single_question_state()
        self._build_background_watermark()
        self._clear_preview("选择输入文件后可在这里预览。")
        self._setup_drop_target()
        self._load_state()
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_queue()

    def _build_ui(self) -> None:
        self.master.title(APP_TITLE)
        self.master.minsize(1120, 800)
        self.master.geometry("1360x900")
        self.master.configure(bg=self.colors["window_bg"])
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.grid(row=0, column=0, sticky="nsew")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.main_scroll_canvas = tk.Canvas(
            self,
            bg=self.colors["window_bg"],
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        self.main_scroll_canvas.grid(row=0, column=0, sticky="nsew")
        self.main_scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.main_scroll_canvas.yview)
        self.main_scrollbar.grid(row=0, column=1, sticky="ns")
        self.main_scroll_canvas.configure(yscrollcommand=self.main_scrollbar.set)

        self.ui_root = ttk.Frame(self.main_scroll_canvas, padding=16, style="App.TFrame")
        self.ui_root_window = self.main_scroll_canvas.create_window((0, 0), window=self.ui_root, anchor="nw")
        self.ui_root.columnconfigure(0, weight=3)
        self.ui_root.columnconfigure(1, weight=2)
        self.ui_root.bind("<Configure>", self._on_main_content_configure)
        self.main_scroll_canvas.bind("<Configure>", self._on_main_canvas_configure)
        self.main_scroll_canvas.bind_all("<MouseWheel>", self._on_main_mousewheel, add="+")

        self._build_input_section()
        self._build_output_section()
        self._build_options_section()
        self._build_preview_section()
        self._build_log_section()
        self._build_action_section()
        self._set_compact_mode(True)

    def _on_main_content_configure(self, _event: tk.Event[Any] | None = None) -> None:
        self.main_scroll_canvas.coords(self.ui_root_window, 0, 0)
        bbox = self.main_scroll_canvas.bbox("all")
        if bbox is None:
            return
        x1, y1, x2, y2 = bbox
        self.main_scroll_canvas.configure(
            scrollregion=(0, 0, max(1, int(x2 - min(0, x1))), max(1, int(y2 - min(0, y1))))
        )

    def _on_main_canvas_configure(self, event: tk.Event[Any]) -> None:
        width = max(360, int(getattr(event, "width", 360)))
        self.main_scroll_canvas.itemconfigure(self.ui_root_window, width=width)
        self._on_main_content_configure()

    def _on_main_mousewheel(self, event: tk.Event[Any]) -> str | None:
        if not self.winfo_ismapped():
            return None
        delta = int(-1 * (float(getattr(event, "delta", 0)) / 120.0))
        if delta == 0:
            return None

        target = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        if target is not None and self._is_widget_descendant(target, self.preview_scroll_canvas):
            self.preview_scroll_canvas.yview_scroll(delta, "units")
            return "break"
        if target is not None and self._is_widget_descendant(target, self.input_listbox):
            self.input_listbox.yview_scroll(delta, "units")
            return "break"
        if target is not None and hasattr(self, "log_text") and self._is_widget_descendant(target, self.log_text):
            self.log_text.yview_scroll(delta, "units")
            return "break"
        self.main_scroll_canvas.yview_scroll(delta, "units")
        return "break"

    def _is_widget_descendant(self, widget: tk.Widget, ancestor: tk.Widget) -> bool:
        current: tk.Widget | None = widget
        while current is not None:
            if current == ancestor:
                return True
            parent_name = current.winfo_parent()
            if not parent_name:
                break
            try:
                current = current.nametowidget(parent_name)
            except Exception:
                break
        return False

    def _toggle_compact_mode(self) -> None:
        self._set_compact_mode(not self.compact_mode)
        self._save_state()

    def _set_compact_mode(self, compact: bool) -> None:
        self.compact_mode = bool(compact)
        if self.compact_mode:
            self.log_frame.grid_remove()
            self.open_debug_button.grid_remove()
            self.compact_toggle_text_var.set("显示详细面板")
        else:
            self.log_frame.grid()
            self.open_debug_button.grid()
            self.compact_toggle_text_var.set("切换简洁界面")
        self.after_idle(self._on_main_content_configure)

    def _configure_theme(self) -> None:
        """Apply a Tianjin University blue themed style set to the GUI."""

        self.style.theme_use("clam")
        self.style.configure("App.TFrame", background=self.colors["window_bg"])
        self.style.configure("TFrame", background=self.colors["panel_bg"])
        self.style.configure(
            "TLabelframe",
            background=self.colors["panel_bg"],
            bordercolor=self.colors["border"],
            relief="solid",
            borderwidth=1,
        )
        self.style.configure(
            "TLabelframe.Label",
            background=self.colors["panel_bg"],
            foreground=self.colors["accent_dark"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.style.configure(
            "TLabel",
            background=self.colors["panel_bg"],
            foreground=self.colors["text"],
        )
        self.style.configure(
            "Footer.TLabel",
            background=self.colors["window_bg"],
            foreground=self.colors["accent_dark"],
        )
        self.style.configure(
            "Author.TLabel",
            background=self.colors["window_bg"],
            foreground=self.colors["muted_text"],
        )
        self.style.configure(
            "Preview.TLabel",
            background=self.colors["surface_bg"],
            foreground=self.colors["muted_text"],
            bordercolor=self.colors["border"],
            relief="solid",
            borderwidth=1,
        )
        self.style.configure(
            "TButton",
            padding=(12, 6),
            background=self.colors["accent_soft"],
            foreground=self.colors["accent_dark"],
            bordercolor=self.colors["border"],
        )
        self.style.map(
            "TButton",
            background=[
                ("active", "#c8def3"),
                ("pressed", "#b9d4ed"),
                ("disabled", "#e3edf8"),
            ],
            foreground=[("disabled", "#8ba0b6")],
        )
        self.style.configure(
            "Accent.TButton",
            padding=(14, 7),
            background=self.colors["accent"],
            foreground="#ffffff",
            bordercolor=self.colors["accent"],
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.style.map(
            "Accent.TButton",
            background=[
                ("active", "#0c67ac"),
                ("pressed", "#094d82"),
                ("disabled", "#7aa4cd"),
            ],
            foreground=[("disabled", "#eef5fd")],
        )
        self.style.configure(
            "TCheckbutton",
            background=self.colors["panel_bg"],
            foreground=self.colors["text"],
        )
        self.style.map("TCheckbutton", background=[("active", self.colors["panel_bg"])])
        self.style.configure(
            "TEntry",
            fieldbackground=self.colors["surface_bg"],
            foreground=self.colors["text"],
            bordercolor=self.colors["border"],
            insertcolor=self.colors["accent_dark"],
        )
        self.style.configure(
            "TCombobox",
            fieldbackground=self.colors["surface_bg"],
            foreground=self.colors["text"],
            bordercolor=self.colors["border"],
            arrowsize=14,
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.colors["surface_bg"])],
            selectbackground=[("readonly", self.colors["surface_bg"])],
            selectforeground=[("readonly", self.colors["text"])],
        )
        self.style.configure(
            "Horizontal.TProgressbar",
            troughcolor="#dce8f6",
            background=self.colors["accent"],
            bordercolor=self.colors["border"],
            lightcolor=self.colors["accent"],
            darkcolor=self.colors["accent"],
        )

    def _build_background_watermark(self) -> None:
        """Place a faint Tianjin University logo behind the main interface."""

        watermark = self._load_tju_watermark_image((620, 190), opacity=0.11)
        if watermark is None:
            return

        self.background_photo = ImageTk.PhotoImage(watermark)
        self.background_label = tk.Label(
            self,
            image=self.background_photo,
            bg=self.colors["window_bg"],
            bd=0,
            highlightthickness=0,
        )
        self.background_label.place(relx=0.52, rely=0.56, anchor="center")
        self.background_label.lower()

    def _load_tju_watermark_image(self, target_size: tuple[int, int], *, opacity: float) -> Image.Image | None:
        """Load and tint the official Tianjin University logo for GUI background use."""

        logo_path = (resource_root() / "assets" / "branding" / "tju_logo_source.png").resolve()
        if not logo_path.exists():
            return None

        with Image.open(logo_path) as image:
            rgba = image.convert("RGBA")
            alpha = rgba.getchannel("A").point(lambda value: max(0, min(255, int(value * opacity))))
            tinted = Image.new("RGBA", rgba.size, (120, 151, 187, 0))
            tinted.putalpha(alpha)
            preview = tinted.copy()
            preview.thumbnail(target_size)
            return preview

    def _build_input_section(self) -> None:
        frame = ttk.LabelFrame(self.ui_root, text="输入文件", padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=0)
        frame.rowconfigure(0, weight=1)

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=0, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.input_listbox = tk.Listbox(
            list_frame,
            height=10,
            selectmode=tk.EXTENDED,
            bg=self.colors["surface_bg"],
            fg=self.colors["text"],
            selectbackground=self.colors["selection"],
            selectforeground="#ffffff",
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            relief="flat",
            activestyle="none",
        )
        self.input_listbox.grid(row=0, column=0, sticky="nsew")
        self.input_listbox.bind("<<ListboxSelect>>", self._on_input_selection)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.input_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.input_listbox.configure(yscrollcommand=scrollbar.set)

        # Horizontal scrollbar so long paths are readable.
        h_scroll = ttk.Scrollbar(list_frame, orient="horizontal", command=self.input_listbox.xview)
        h_scroll.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.input_listbox.configure(xscrollcommand=h_scroll.set)
        # Horizontal scrolling support.
        # NOTE: some Tk builds don't support xscrollincrement, so keep it minimal.
        self.input_listbox.configure(exportselection=False)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=0, column=1, sticky="ns", padx=(12, 0))
        self.add_files_button = ttk.Button(button_frame, text="添加文件", command=self.add_input_files)
        self.add_files_button.grid(row=0, column=0, sticky="ew")
        self.remove_files_button = ttk.Button(button_frame, text="移除选中", command=self.remove_selected_inputs)
        self.remove_files_button.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.clear_files_button = ttk.Button(button_frame, text="清空列表", command=self.clear_inputs)
        self.clear_files_button.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.paste_files_button = ttk.Button(button_frame, text="粘贴文件", command=self.paste_input_files)
        self.paste_files_button.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.auto_start_on_paste_checkbox = ttk.Checkbutton(
            button_frame,
            text="粘贴后自动开始",
            variable=self.auto_start_on_paste_var,
            command=self._save_state,
        )
        self.auto_start_on_paste_checkbox.grid(row=4, column=0, sticky="w", pady=(8, 0))

    def _build_output_section(self) -> None:
        frame = ttk.LabelFrame(self.ui_root, text="输出设置", padding=12)
        frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="输出目录").grid(row=0, column=0, sticky="w")
        self.output_dir_entry = ttk.Entry(frame, textvariable=self.output_dir_var)
        self.output_dir_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.output_browse_button = ttk.Button(frame, text="浏览", command=self.browse_output_directory)
        self.output_browse_button.grid(row=0, column=2, sticky="ew")
        ttk.Label(frame, text="文件名").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.output_name_entry = ttk.Entry(frame, textvariable=self.output_name_var)
        self.output_name_entry.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(10, 0))
        ttk.Label(frame, text="可直接填写自定义 PDF 名称", style="Footer.TLabel").grid(
            row=1,
            column=2,
            sticky="w",
            pady=(10, 0),
        )

    def _build_options_section(self) -> None:
        frame = ttk.LabelFrame(self.ui_root, text="处理选项", padding=12)
        frame.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        for column in range(4):
            frame.columnconfigure(column, weight=1 if column in (1, 3) else 0)

        ttk.Label(frame, text="模式").grid(row=0, column=0, sticky="w")
        self.mode_combo = ttk.Combobox(frame, textvariable=self.mode_var, values=tuple(MODE_LABEL_TO_VALUE), state="readonly")
        self.mode_combo.grid(row=0, column=1, sticky="ew", padx=(8, 16))
        ttk.Label(frame, text="DPI").grid(row=0, column=2, sticky="w")
        self.dpi_entry = ttk.Entry(frame, textvariable=self.dpi_var)
        self.dpi_entry.grid(row=0, column=3, sticky="ew", padx=(8, 8))
        self.dpi_help_button = ttk.Button(frame, text="说明", command=self.show_dpi_help)
        self.dpi_help_button.grid(row=0, column=4, sticky="ew")

        ttk.Label(frame, text="每页题数").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.per_page_entry = ttk.Entry(frame, textvariable=self.per_page_var)
        self.per_page_entry.grid(row=1, column=1, sticky="ew", padx=(8, 16), pady=(10, 0))
        toggle_frame = ttk.Frame(frame)
        toggle_frame.grid(row=1, column=2, columnspan=2, sticky="w", pady=(10, 0))
        self.pdf_watermark_checkbox = ttk.Checkbutton(
            toggle_frame,
            text="PDF 添加天津大学水印",
            variable=self.pdf_watermark_var,
        )
        self.pdf_watermark_checkbox.grid(row=0, column=0, sticky="w")
        self.debug_checkbox = ttk.Checkbutton(toggle_frame, text="导出调试结果", variable=self.debug_var)
        self.debug_checkbox.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.strict_single_question_checkbox = ttk.Checkbutton(
            toggle_frame,
            text="严格单题模式（每页 1 题）",
            variable=self.strict_single_question_var,
            command=self._on_strict_single_question_toggle,
        )
        self.strict_single_question_checkbox.grid(row=2, column=0, sticky="w", pady=(6, 0))

        self.layout_choice_frame = ttk.Frame(frame)
        self.layout_choice_frame.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(10, 0))
        self.layout_choice_frame.columnconfigure(1, weight=1)
        ttk.Label(self.layout_choice_frame, text="版式选择").grid(row=0, column=0, sticky="w")
        self.layout_pattern_combo = ttk.Combobox(
            self.layout_choice_frame,
            textvariable=self.layout_pattern_display_var,
            state="readonly",
        )
        self.layout_pattern_combo.grid(row=0, column=1, sticky="ew", padx=(8, 12))
        self.layout_pattern_combo.bind("<<ComboboxSelected>>", self._on_layout_pattern_combo_selected)
        self.custom_layout_pattern_entry = ttk.Entry(
            self.layout_choice_frame,
            textvariable=self.custom_layout_pattern_var,
        )
        self.custom_layout_pattern_entry.grid(row=0, column=2, sticky="ew")
        ttk.Label(self.layout_choice_frame, text="示例：1+2、2+1、1+1+1", style="Footer.TLabel").grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="w",
            pady=(6, 0),
        )

        self.advanced_toggle_button = ttk.Button(
            frame,
            textvariable=self.advanced_toggle_text_var,
            command=self.toggle_advanced_settings,
        )
        self.advanced_toggle_button.grid(row=3, column=0, sticky="w", pady=(12, 0))

        self.advanced_frame = ttk.LabelFrame(frame, text="高级设置", padding=10)
        self.advanced_frame.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        for column in range(5):
            self.advanced_frame.columnconfigure(column, weight=1 if column in (1, 3) else 0)

        ttk.Label(self.advanced_frame, text="版式模板").grid(row=0, column=0, sticky="w")
        self.layout_template_entry = ttk.Entry(self.advanced_frame, textvariable=self.layout_template_var)
        self.layout_template_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.layout_template_browse_button = ttk.Button(self.advanced_frame, text="浏览", command=self.browse_layout_template_file)
        self.layout_template_browse_button.grid(row=0, column=2, sticky="ew")
        self.layout_template_clear_button = ttk.Button(self.advanced_frame, text="清空", command=self.clear_layout_template_path)
        self.layout_template_clear_button.grid(row=0, column=3, sticky="ew", padx=(8, 0))
        self.layout_template_help_button = ttk.Button(self.advanced_frame, text="说明", command=self.show_layout_template_help)
        self.layout_template_help_button.grid(row=0, column=4, sticky="ew", padx=(8, 0))
        self.template_lock_per_page_checkbox = ttk.Checkbutton(
            self.advanced_frame,
            text="模板锁定题数",
            variable=self.template_lock_per_page_var,
        )
        self.template_lock_per_page_checkbox.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        ttk.Label(self.advanced_frame, text="全局配置").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.config_entry = ttk.Entry(self.advanced_frame, textvariable=self.config_path_var)
        self.config_entry.grid(row=2, column=1, sticky="ew", padx=(8, 8), pady=(10, 0))
        self.config_browse_button = ttk.Button(self.advanced_frame, text="浏览", command=self.browse_config_file)
        self.config_browse_button.grid(row=2, column=2, sticky="ew", pady=(10, 0))
        self.config_clear_button = ttk.Button(self.advanced_frame, text="清空", command=self.clear_config_path)
        self.config_clear_button.grid(row=2, column=3, sticky="ew", padx=(8, 0), pady=(10, 0))
        self.config_help_button = ttk.Button(self.advanced_frame, text="说明", command=self.show_global_config_help)
        self.config_help_button.grid(row=2, column=4, sticky="ew", padx=(8, 0), pady=(10, 0))

        ttk.Label(self.advanced_frame, text="页眉配置").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.header_config_entry = ttk.Entry(self.advanced_frame, textvariable=self.header_config_var)
        self.header_config_entry.grid(row=3, column=1, sticky="ew", padx=(8, 8), pady=(10, 0))
        self.header_config_browse_button = ttk.Button(self.advanced_frame, text="浏览", command=self.browse_header_config_file)
        self.header_config_browse_button.grid(row=3, column=2, sticky="ew", pady=(10, 0))
        self.header_config_clear_button = ttk.Button(self.advanced_frame, text="清空", command=self.clear_header_config_path)
        self.header_config_clear_button.grid(row=3, column=3, sticky="ew", padx=(8, 0), pady=(10, 0))
        self.header_config_help_button = ttk.Button(self.advanced_frame, text="说明", command=self.show_header_config_help)
        self.header_config_help_button.grid(row=3, column=4, sticky="ew", padx=(8, 0), pady=(10, 0))

        identity_frame = ttk.LabelFrame(frame, text="页眉信息", padding=10)
        identity_frame.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        identity_frame.columnconfigure(1, weight=1)
        identity_frame.columnconfigure(3, weight=1)
        identity_frame.columnconfigure(5, weight=1)
        ttk.Label(identity_frame, text="学号").grid(row=0, column=0, sticky="w")
        self.student_id_entry = ttk.Entry(identity_frame, textvariable=self.student_id_var)
        self.student_id_entry.grid(row=0, column=1, sticky="ew", padx=(8, 16))
        ttk.Label(identity_frame, text="班级").grid(row=0, column=2, sticky="w")
        self.class_name_entry = ttk.Entry(identity_frame, textvariable=self.class_name_var)
        self.class_name_entry.grid(row=0, column=3, sticky="ew", padx=(8, 16))
        ttk.Label(identity_frame, text="姓名").grid(row=0, column=4, sticky="w")
        self.student_name_entry = ttk.Entry(identity_frame, textvariable=self.student_name_var)
        self.student_name_entry.grid(row=0, column=5, sticky="ew")
        self._refresh_layout_pattern_controls()
        self._set_advanced_visible(False)

    def _build_preview_section(self) -> None:
        frame = ttk.LabelFrame(self.ui_root, text="预览", padding=12)
        frame.grid(row=0, column=1, rowspan=3, sticky="nsew", padx=(12, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.preview_scroll_canvas = tk.Canvas(
            frame,
            bg=self.colors["panel_bg"],
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        self.preview_scroll_canvas.grid(row=0, column=0, sticky="nsew")
        self.preview_scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.preview_scroll_canvas.yview)
        self.preview_scrollbar.grid(row=0, column=1, sticky="ns")
        self.preview_scroll_canvas.configure(yscrollcommand=self.preview_scrollbar.set)

        self.preview_content_frame = ttk.Frame(self.preview_scroll_canvas)
        self.preview_content_window = self.preview_scroll_canvas.create_window(
            (0, 0),
            window=self.preview_content_frame,
            anchor="nw",
        )
        self.preview_content_frame.columnconfigure(0, weight=1)
        self.preview_content_frame.columnconfigure(1, weight=0)

        self.preview_label = ttk.Label(self.preview_content_frame, anchor="center", style="Preview.TLabel")
        self.preview_label.grid(row=0, column=0, sticky="nsew")
        self.preview_label.configure(text="选择输入文件后可在这里预览。")
        self.preview_info_label = ttk.Label(
            self.preview_content_frame,
            textvariable=self.preview_info_var,
            wraplength=320,
            justify="left",
        )
        self.preview_info_label.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.manual_toggle_button = ttk.Button(
            self.preview_content_frame,
            textvariable=self.manual_toggle_text_var,
            command=self._toggle_manual_panel,
        )
        self.manual_toggle_button.grid(row=2, column=0, sticky="w", pady=(8, 0))

        self.preview_fullscreen_button = ttk.Button(
            self.preview_content_frame,
            text="全屏预览",
            command=self._open_fullscreen_preview,
        )
        self.preview_fullscreen_button.grid(row=2, column=1, sticky="e", pady=(8, 0))

        self.material_toggle_button = ttk.Checkbutton(
            self.preview_content_frame,
            text="素材框选（从当前 PDF）",
            variable=self.material_mode_var,
            command=self._toggle_material_mode,
        )
        self.material_toggle_button.grid(row=3, column=0, sticky="w", pady=(8, 0))

        self.material_panel = ttk.LabelFrame(self.preview_content_frame, text="从 PDF 框选素材", padding=8)
        self.material_panel.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.material_panel.grid_remove()
        self.material_panel.columnconfigure(0, weight=0)
        self.material_panel.columnconfigure(1, weight=1)
        self.material_panel.columnconfigure(2, weight=0)
        self.material_panel.columnconfigure(3, weight=0)
        self.material_panel.rowconfigure(3, weight=1)

        ttk.Label(self.material_panel, text="页码").grid(row=0, column=0, sticky="w")
        self.material_page_spinbox = ttk.Spinbox(
            self.material_panel,
            from_=1,
            to=1,
            textvariable=self.material_page_var,
            width=7,
        )
        self.material_page_spinbox.grid(row=0, column=1, sticky="w", padx=(6, 10))
        self.material_page_spinbox.bind("<Return>", self._on_material_page_changed)
        self.material_page_spinbox.bind("<<Increment>>", self._on_material_page_changed)
        self.material_page_spinbox.bind("<<Decrement>>", self._on_material_page_changed)

        self.material_prev_button = ttk.Button(self.material_panel, text="上一页", command=lambda: self._step_material_page(-1))
        self.material_prev_button.grid(row=0, column=2, sticky="ew", padx=(8, 0))

        self.material_reload_button = ttk.Button(self.material_panel, text="载入该页", command=self._render_material_source_page)
        self.material_reload_button.grid(row=0, column=3, sticky="ew", padx=(8, 0))

        self.material_fullscreen_button = ttk.Button(
            self.material_panel,
            text="全屏框选",
            command=self._open_fullscreen_material_selector,
        )
        self.material_fullscreen_button.grid(row=1, column=3, sticky="ew", padx=(8, 0), pady=(6, 0))

        ttk.Label(self.material_panel, text="框选后删除原文件").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.material_delete_target_combo = ttk.Combobox(
            self.material_panel,
            state="readonly",
            values=["不删除", "删除来源PDF(仅从列表移除)", "删除列表第一个PDF(仅从列表移除)"],
            textvariable=self.material_delete_target_var,
        )
        self.material_delete_target_combo.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(6, 0))

        self.material_next_button = ttk.Button(self.material_panel, text="下一页", command=lambda: self._step_material_page(1))
        self.material_next_button.grid(row=0, column=4, sticky="ew", padx=(8, 0))
        self.material_panel.columnconfigure(4, weight=0)

        self.material_auto_advance_checkbox = ttk.Checkbutton(
            self.material_panel,
            text="框选后自动翻页",
            variable=self.material_auto_advance_var,
            command=self._save_state,
        )
        self.material_auto_advance_checkbox.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.material_info_var = tk.StringVar(value="未启用。上传/选中 PDF 后启用“素材框选”。")
        ttk.Label(
            self.material_panel,
            textvariable=self.material_info_var,
            style="Footer.TLabel",
            wraplength=520,
            justify="left",
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        self.material_canvas = tk.Canvas(
            self.material_panel,
            width=520,
            height=360,
            bg=self.colors["surface_bg"],
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            relief="flat",
        )
        self.material_canvas.grid(row=3, column=0, columnspan=4, sticky="nsew", pady=(8, 0))
        self.material_canvas.bind("<ButtonPress-1>", self._on_material_canvas_press)
        self.material_canvas.bind("<B1-Motion>", self._on_material_canvas_drag)
        self.material_canvas.bind("<ButtonRelease-1>", self._on_material_canvas_release)
        self.material_canvas.bind(
            "<Configure>",
            lambda _e: self._render_material_source_page() if self.material_mode_var.get() else None,
        )

        self._build_manual_correction_panel(self.preview_content_frame)
        self._set_manual_panel_visible(False)
        self._set_manual_tools_available(False)
        self.preview_content_frame.bind("<Configure>", self._on_preview_content_configure)
        self.preview_scroll_canvas.bind("<Configure>", self._on_preview_area_configure)

    def _build_manual_correction_panel(self, parent: tk.Widget) -> None:
        panel = ttk.LabelFrame(parent, text="手动校正", padding=8)
        panel.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        panel.columnconfigure(3, weight=1)
        panel.columnconfigure(6, weight=1)
        panel.rowconfigure(6, weight=1)
        self.manual_panel = panel

        ttk.Label(panel, text="页码").grid(row=0, column=0, sticky="w")
        self.manual_page_spinbox = ttk.Spinbox(
            panel,
            from_=1,
            to=1,
            textvariable=self.manual_page_var,
            width=7,
        )
        self.manual_page_spinbox.grid(row=0, column=1, sticky="w", padx=(6, 10))
        self.manual_page_spinbox.bind("<Return>", self._on_manual_page_changed)
        self.manual_page_spinbox.bind("<<Increment>>", self._on_manual_page_changed)
        self.manual_page_spinbox.bind("<<Decrement>>", self._on_manual_page_changed)
        self.manual_page_apply_button = ttk.Button(panel, text="跳转", command=self._on_manual_page_changed)
        self.manual_page_apply_button.grid(row=0, column=2, sticky="w")

        ttk.Label(panel, text="步长(px)").grid(row=0, column=3, sticky="e", padx=(10, 4))
        self.manual_step_entry = ttk.Entry(panel, textvariable=self.manual_step_var, width=6)
        self.manual_step_entry.grid(row=0, column=4, sticky="e")
        self.manual_view_toggle_button = ttk.Button(
            panel,
            textvariable=self.manual_view_toggle_text_var,
            command=self._toggle_manual_view_mode,
        )
        self.manual_view_toggle_button.grid(row=0, column=5, sticky="e", padx=(8, 0))
        self.manual_zoom_scale = ttk.Scale(
            panel,
            from_=100.0,
            to=300.0,
            orient="horizontal",
            variable=self.manual_zoom_var,
            command=self._on_manual_zoom_changed,
        )
        self.manual_zoom_scale.grid(row=0, column=6, sticky="ew", padx=(10, 6))
        self.manual_zoom_label = ttk.Label(panel, textvariable=self.manual_zoom_text_var, width=9, anchor="e")
        self.manual_zoom_label.grid(row=0, column=7, sticky="e")

        self.manual_top_up_button = ttk.Button(panel, text="上边界上移", command=lambda: self._adjust_manual_boundary("top", -1))
        self.manual_top_up_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.manual_top_down_button = ttk.Button(panel, text="上边界下移", command=lambda: self._adjust_manual_boundary("top", 1))
        self.manual_top_down_button.grid(row=1, column=2, columnspan=2, sticky="ew", padx=(8, 0), pady=(8, 0))

        self.manual_bottom_up_button = ttk.Button(panel, text="下边界上移", command=lambda: self._adjust_manual_boundary("bottom", -1))
        self.manual_bottom_up_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.manual_bottom_down_button = ttk.Button(panel, text="下边界下移", command=lambda: self._adjust_manual_boundary("bottom", 1))
        self.manual_bottom_down_button.grid(row=2, column=2, columnspan=2, sticky="ew", padx=(8, 0), pady=(6, 0))

        self.manual_reset_page_button = ttk.Button(panel, text="恢复本页自动结果", command=self._reset_manual_page)
        self.manual_reset_page_button.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.manual_preview_button = ttk.Button(panel, text="重渲染当前页预览", command=self._render_manual_selected_page_preview)
        self.manual_preview_button.grid(row=3, column=2, columnspan=2, sticky="ew", padx=(8, 0), pady=(8, 0))
        self.manual_export_button = ttk.Button(panel, text="导出校正后PDF", command=self._export_manual_corrected_pdf)
        self.manual_export_button.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 0))

        ttk.Label(panel, textvariable=self.manual_status_var, style="Footer.TLabel", wraplength=320, justify="left").grid(
            row=5,
            column=0,
            columnspan=6,
            sticky="w",
            pady=(8, 0),
        )
        self.manual_canvas = tk.Canvas(
            panel,
            width=520,
            height=360,
            bg=self.colors["surface_bg"],
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            relief="flat",
        )
        self.manual_canvas.grid(row=6, column=0, columnspan=6, sticky="nsew", pady=(8, 0))
        self.manual_canvas.bind("<ButtonPress-1>", self._on_manual_canvas_press)
        self.manual_canvas.bind("<B1-Motion>", self._on_manual_canvas_drag)
        self.manual_canvas.bind("<ButtonRelease-1>", self._on_manual_canvas_release)
        self.manual_canvas.bind("<Configure>", self._on_manual_canvas_configure)
        self.manual_controls = [
            self.manual_page_spinbox,
            self.manual_page_apply_button,
            self.manual_step_entry,
            self.manual_view_toggle_button,
            self.manual_zoom_scale,
            self.manual_top_up_button,
            self.manual_top_down_button,
            self.manual_bottom_up_button,
            self.manual_bottom_down_button,
            self.manual_reset_page_button,
            self.manual_preview_button,
            self.manual_export_button,
        ]
        self._set_manual_controls_enabled(False)

    def _toggle_manual_panel(self) -> None:
        if not self.manual_tools_available:
            return
        self._set_manual_panel_visible(not self.manual_panel_visible)

    def _set_manual_tools_available(self, available: bool) -> None:
        self.manual_tools_available = bool(available)
        if self.manual_tools_available:
            self.manual_toggle_button.grid()
        else:
            self._set_manual_panel_visible(False)
            self.manual_toggle_button.grid_remove()

    def _toggle_manual_view_mode(self) -> None:
        self.manual_focus_view = not self.manual_focus_view
        self.manual_view_toggle_text_var.set("查看整页" if self.manual_focus_view else "聚焦题目")
        self._render_manual_source_canvas()

    def _on_manual_zoom_changed(self, value: str | float) -> None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = float(self.manual_zoom_var.get())
        numeric = max(100.0, min(300.0, numeric))
        self.manual_zoom_var.set(numeric)
        self._update_manual_zoom_label()
        if self.manual_panel_visible and self.manual_page_problem_id:
            self._render_manual_source_canvas()

    def _update_manual_zoom_label(self) -> None:
        self.manual_zoom_text_var.set(f"缩放 {int(round(float(self.manual_zoom_var.get())))}%")

    def _on_manual_canvas_configure(self, _event: tk.Event[Any]) -> None:
        if self.manual_panel_visible and self.manual_page_problem_id:
            self._render_manual_source_canvas()

    def _set_manual_panel_visible(self, visible: bool) -> None:
        self.manual_panel_visible = bool(visible)
        if self.manual_panel_visible:
            self.manual_panel.grid()
            self.manual_toggle_text_var.set("隐藏手动校正")
        else:
            self.manual_panel.grid_remove()
            self.manual_toggle_text_var.set("显示手动校正")
        self.after_idle(self._update_preview_scrollregion)

    def _toggle_material_mode(self) -> None:
        enabled = bool(self.material_mode_var.get())
        if not enabled:
            self.material_panel.grid_remove()
            self._reset_material_selection_state()
            return

        # If we already have a cached source PDF, allow re-enabling
        # even when the input list selection is no longer on that PDF
        # (e.g. after "框选后删除原文件" removed it from the list).
        if self.material_source_pdf_path is not None and self.material_source_pdf_path.exists():
            self.material_selection_count = 0
            self.material_panel.grid()
            self.material_info_var.set("载入中...（请稍等）")
            self._reset_material_selection_state(keep_source_pdf=True)
            self.after_idle(self._render_material_source_page)
            return

        target = self._selected_input_path()
        if target is None or target.suffix.lower() != ".pdf":
            self.material_mode_var.set(False)
            self.material_panel.grid_remove()
            self.material_info_var.set("请先在输入列表中选中一个 PDF 文件，再启用素材框选。")
            self._reset_material_selection_state()
            return

        # When enabling, lock onto the currently selected PDF so repeated selections
        # do not break when the input listbox auto-selects newly added crops.
        self.material_source_pdf_path = target
        self.material_selection_count = 0
        self.material_panel.grid()
        self.material_info_var.set("载入中...（请稍等）")
        self._reset_material_selection_state(keep_source_pdf=True)
        self.after_idle(self._render_material_source_page)

    def _reset_material_selection_state(self, *, keep_source_pdf: bool = False) -> None:
        if not keep_source_pdf:
            self.material_source_pdf_path = None
        self.material_source_bgr_image = None
        self.material_source_page_total = 1
        self.material_canvas_photo = None
        self.material_canvas_transform = {}
        self.material_canvas_rect_id = None
        self.material_drag_start = None
        try:
            self.material_canvas.delete("all")
        except Exception:
            pass

    def _current_selected_pdf_dpi(self) -> int:
        try:
            return int(str(self.dpi_var.get()).strip() or "220")
        except (TypeError, ValueError):
            return 220

    def _on_material_page_changed(self, _event: tk.Event[Any] | None = None) -> None:
        if not self.material_mode_var.get():
            return
        self.after_idle(self._render_material_source_page)

    def _step_material_page(self, delta: int) -> None:
        if not self.material_mode_var.get():
            return
        try:
            current = int(str(self.material_page_var.get()).strip() or "1")
        except ValueError:
            current = 1
        total = max(1, int(self.material_source_page_total or 1))
        next_page = max(1, min(current + int(delta), total))
        self.material_page_var.set(str(next_page))
        self.after_idle(self._render_material_source_page)

    def _render_material_source_page(self) -> None:
        if not self.material_mode_var.get():
            return
        if self.material_source_pdf_path is None or not self.material_source_pdf_path.exists():
            self.material_info_var.set("当前素材来源 PDF 不存在或未设置。")
            return

        try:
            page_number = int(str(self.material_page_var.get()).strip() or "1")
        except ValueError:
            page_number = 1
            self.material_page_var.set("1")

        try:
            fitz = import_pymupdf()
            resolved_dpi = self._current_selected_pdf_dpi()
            scale = resolved_dpi / 72.0
            with fitz.open(str(self.material_source_pdf_path)) as document:
                total = int(getattr(document, "page_count", 1) or 1)
                page_number = max(1, min(page_number, total))
                page = document.load_page(page_number - 1)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                raster = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
                bgr = cv2.cvtColor(raster, cv2.COLOR_GRAY2BGR) if pixmap.n == 1 else cv2.cvtColor(raster[:, :, :3], cv2.COLOR_RGB2BGR)

        except Exception as exc:
            self.material_info_var.set(f"载入失败：{exc}")
            return

        self.material_source_bgr_image = bgr
        self.material_source_page_total = total
        self.material_page_var.set(str(page_number))
        self.material_page_spinbox.configure(to=max(1, total))

        # Aspect-fit rendering to canvas + bbox mapping.
        src_h, src_w = bgr.shape[:2]
        canvas_w = max(320, int(self.material_canvas.winfo_width() or 520))
        canvas_h = max(240, int(self.material_canvas.winfo_height() or 360))
        scale_fit = min(canvas_w / max(src_w, 1), canvas_h / max(src_h, 1))
        draw_w = max(1, int(round(src_w * scale_fit)))
        draw_h = max(1, int(round(src_h * scale_fit)))
        offset_x = (canvas_w - draw_w) // 2
        offset_y = (canvas_h - draw_h) // 2

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb)
        display = pil_image.resize((draw_w, draw_h), Image.Resampling.LANCZOS)
        self.material_canvas_photo = ImageTk.PhotoImage(display)

        self.material_canvas.delete("all")
        self.material_canvas.create_image(offset_x, offset_y, anchor="nw", image=self.material_canvas_photo)
        self.material_canvas_rect_id = None
        self.material_drag_start = None

        self.material_canvas_transform = {
            "scale": float(scale_fit),
            "offset_x": float(offset_x),
            "offset_y": float(offset_y),
            "source_w": float(src_w),
            "source_h": float(src_h),
            "draw_w": float(draw_w),
            "draw_h": float(draw_h),
        }

        self.material_info_var.set(
            f"已载入：{self.material_source_pdf_path.name}（第 {page_number} 页 / 共 {total} 页）。"
            f"拖拽框选区域即可加入输入列表。"
        )
        self.after_idle(self._update_preview_scrollregion)

    def _material_canvas_to_source_bbox(self, x1: float, y1: float, x2: float, y2: float) -> tuple[int, int, int, int] | None:
        if self.material_source_bgr_image is None:
            return None
        t = self.material_canvas_transform
        if not t:
            return None

        scale = max(float(t.get("scale", 1.0)), 1e-6)
        offset_x = float(t.get("offset_x", 0.0))
        offset_y = float(t.get("offset_y", 0.0))
        src_w = int(t.get("source_w", self.material_source_bgr_image.shape[1]))
        src_h = int(t.get("source_h", self.material_source_bgr_image.shape[0]))

        draw_w = float(t.get("draw_w", src_w * scale))
        draw_h = float(t.get("draw_h", src_h * scale))
        disp_x1 = min(max(x1, offset_x), offset_x + draw_w)
        disp_y1 = min(max(y1, offset_y), offset_y + draw_h)
        disp_x2 = min(max(x2, offset_x), offset_x + draw_w)
        disp_y2 = min(max(y2, offset_y), offset_y + draw_h)

        if disp_x2 <= disp_x1 or disp_y2 <= disp_y1:
            return None

        sx1 = int((disp_x1 - offset_x) / scale)
        sy1 = int((disp_y1 - offset_y) / scale)
        sx2 = int((disp_x2 - offset_x) / scale)
        sy2 = int((disp_y2 - offset_y) / scale)

        sx1 = max(0, min(src_w - 1, sx1))
        sy1 = max(0, min(src_h - 1, sy1))
        sx2 = max(1, min(src_w, sx2))
        sy2 = max(1, min(src_h, sy2))

        # Avoid tiny/empty crops.
        if sx2 - sx1 < 12 or sy2 - sy1 < 12:
            return None
        return (sx1, sy1, sx2, sy2)

    def _on_material_canvas_press(self, event: tk.Event[Any]) -> None:
        if not self.material_mode_var.get() or self.material_source_bgr_image is None:
            return
        t = self.material_canvas_transform
        if not t:
            return
        x, y = float(event.x), float(event.y)
        offset_x = float(t.get("offset_x", 0.0))
        offset_y = float(t.get("offset_y", 0.0))
        draw_w = float(t.get("draw_w", 0.0))
        draw_h = float(t.get("draw_h", 0.0))
        if not (offset_x <= x <= offset_x + draw_w and offset_y <= y <= offset_y + draw_h):
            return

        self.material_drag_start = (x, y)
        if self.material_canvas_rect_id is not None:
            try:
                self.material_canvas.delete(self.material_canvas_rect_id)
            except Exception:
                pass

        self.material_canvas_rect_id = self.material_canvas.create_rectangle(
            x, y, x, y, outline="#d52a2a", width=2, dash=(4, 3)
        )

    def _on_material_canvas_drag(self, event: tk.Event[Any]) -> None:
        if not self.material_mode_var.get() or self.material_source_bgr_image is None or self.material_drag_start is None:
            return
        if self.material_canvas_rect_id is None:
            return
        sx, sy = self.material_drag_start
        ex, ey = float(event.x), float(event.y)
        self.material_canvas.coords(self.material_canvas_rect_id, sx, sy, ex, ey)

    def _on_material_canvas_release(self, _event: tk.Event[Any]) -> None:
        if not self.material_mode_var.get() or self.material_source_bgr_image is None or self.material_drag_start is None:
            return
        if self.material_canvas_rect_id is None:
            self.material_drag_start = None
            return

        x1, y1, x2, y2 = self.material_canvas.coords(self.material_canvas_rect_id)
        nx1, ny1, nx2, ny2 = min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
        bbox = self._material_canvas_to_source_bbox(nx1, ny1, nx2, ny2)
        self.material_drag_start = None
        if bbox is None:
            return

        sx1, sy1, sx2, sy2 = bbox
        crop_bgr = self.material_source_bgr_image[sy1:sy2, sx1:sx2].copy()

        if self.material_source_pdf_path is None:
            return

        self.material_selection_count += 1
        try:
            out_dir = (self.state_dir / "selected_materials").resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
            pdf_stem = self.material_source_pdf_path.stem.strip() or "source"
            fname = f"{pdf_stem}_p{self.material_page_var.get()}_{self.material_selection_count:03d}.png"
            out_path = (out_dir / fname).resolve()
            write_image(out_path, crop_bgr)
        except Exception as exc:
            messagebox.showerror("素材框选", f"保存失败：{exc}")
            return

        # Add saved crop as a new input file.
        self._add_input_paths([str(out_path)], source="素材框选")

        self._maybe_delete_original_after_material_crop()

        # If we are not deleting the source from the list, keep it selected so
        # repeated selections can be made without re-clicking.
        if self.material_delete_target_var.get() == "不删除":
            self._restore_material_source_selection()

        # Continuous capture: optionally advance to next page automatically.
        if bool(self.material_auto_advance_var.get()):
            self._step_material_page(1)

    def _restore_material_source_selection(self) -> None:
        try:
            if self.material_source_pdf_path is None:
                return
            src_str = str(self.material_source_pdf_path)
            if src_str not in self.input_paths:
                return
            idx = self.input_paths.index(src_str)
            self.input_listbox.selection_clear(0, tk.END)
            self.input_listbox.selection_set(idx)
            self.input_listbox.activate(idx)
        except Exception:
            pass

    def _remove_input_path_from_listbox(self, target: Path) -> None:
        target_resolved = str(target.expanduser().resolve())
        if target_resolved not in self.input_paths:
            return
        idx = self.input_paths.index(target_resolved)
        del self.input_paths[idx]
        self.input_listbox.delete(idx)

    def _maybe_delete_original_after_material_crop(self) -> None:
        mode = self.material_delete_target_var.get()
        if mode == "不删除":
            return

        # Deletion here means "remove from the input list", not delete from disk.
        try:
            if mode.startswith("删除来源PDF") and self.material_source_pdf_path is not None:
                self._remove_input_path_from_listbox(self.material_source_pdf_path)
            elif mode.startswith("删除列表第一个PDF") and self.input_paths:
                self._remove_input_path_from_listbox(Path(self.input_paths[0]))
        finally:
            # Keep preview stable; just persist list changes.
            self._save_state()

    def _open_fullscreen_material_selector(self) -> None:
        if not self.material_mode_var.get():
            messagebox.showinfo("全屏框选", "请先启用“素材框选（从当前 PDF）”。")
            return

        if self.material_source_bgr_image is None:
            # Best-effort: try to render from current cached source.
            try:
                self._render_material_source_page()
            except Exception:
                pass

        if self.material_source_bgr_image is None or self.material_source_pdf_path is None:
            messagebox.showinfo("全屏框选", "未找到素材来源 PDF（或尚未渲染）。请先选中一个 PDF 并载入素材页。")
            return

        # Close previous fullscreen window if exists.
        if self.material_fullscreen_win is not None and self.material_fullscreen_win.winfo_exists():
            try:
                self.material_fullscreen_win.destroy()
            except Exception:
                pass

        win = tk.Toplevel(self.master)
        self.material_fullscreen_win = win
        win.title("全屏素材框选")
        win.configure(bg=self.colors["window_bg"])
        win.attributes("-fullscreen", True)
        win.bind("<Escape>", lambda _e: self._close_fullscreen_material_selector())

        top_bar = ttk.Frame(win)
        top_bar.pack(side="top", fill="x")
        ttk.Label(top_bar, text=f"全屏素材框选：{self.material_source_pdf_path.name}（第 {self.material_page_var.get()} 页）").pack(
            side="left", padx=10, pady=6
        )
        ttk.Button(top_bar, text="退出", command=self._close_fullscreen_material_selector).pack(side="right", padx=10, pady=6)

        canvas = tk.Canvas(win, bg=self.colors["surface_bg"], highlightthickness=1, highlightbackground=self.colors["border"])
        canvas.pack(side="top", fill="both", expand=True)
        self.fullscreen_material_canvas = canvas

        self.fullscreen_material_canvas_rect_id = None
        self.fullscreen_material_drag_start = None
        self.fullscreen_material_canvas_transform = {}

        canvas.bind("<ButtonPress-1>", self._on_fullscreen_material_canvas_press)
        canvas.bind("<B1-Motion>", self._on_fullscreen_material_canvas_drag)
        canvas.bind("<ButtonRelease-1>", self._on_fullscreen_material_canvas_release)
        canvas.bind("<Configure>", lambda _e: self._render_fullscreen_material_canvas())

        self._render_fullscreen_material_canvas()

    def _close_fullscreen_material_selector(self) -> None:
        if self.material_fullscreen_win is not None and self.material_fullscreen_win.winfo_exists():
            try:
                self.material_fullscreen_win.destroy()
            except Exception:
                pass
        self.material_fullscreen_win = None
        self.fullscreen_material_canvas = None
        self.fullscreen_material_canvas_rect_id = None
        self.fullscreen_material_drag_start = None
        self.fullscreen_material_canvas_transform = {}
        self.fullscreen_material_canvas_photo = None

    def _open_fullscreen_preview(self) -> None:
        if self.preview_source_image is None:
            messagebox.showinfo("全屏预览", "当前没有可预览的内容。")
            return

        if self.preview_fullscreen_win is not None and self.preview_fullscreen_win.winfo_exists():
            try:
                self.preview_fullscreen_win.destroy()
            except Exception:
                pass

        win = tk.Toplevel(self.master)
        self.preview_fullscreen_win = win
        win.title("全屏预览")
        win.configure(bg=self.colors["window_bg"])
        win.attributes("-fullscreen", True)
        win.bind("<Escape>", lambda _e: self._close_fullscreen_preview())

        top_bar = ttk.Frame(win)
        top_bar.pack(side="top", fill="x")

        preview_text = self.preview_info_var.get() or ""
        ttk.Label(top_bar, text=preview_text, style="Footer.TLabel").pack(side="left", padx=10, pady=6)
        ttk.Button(top_bar, text="退出", command=self._close_fullscreen_preview).pack(side="right", padx=10, pady=6)

        canvas = tk.Canvas(win, bg=self.colors["surface_bg"], highlightthickness=0)
        canvas.pack(side="top", fill="both", expand=True)

        # Render current preview_source_image to a fit-to-screen size.
        try:
            win.update_idletasks()
            cw = int(canvas.winfo_width() or 1)
            ch = int(canvas.winfo_height() or 1)
            if cw <= 2 or ch <= 2:
                cw = max(800, int(win.winfo_screenwidth() * 0.9))
                ch = max(600, int(win.winfo_screenheight() * 0.85))

            src_w, src_h = self.preview_source_image.size
            scale_fit = min(cw / max(src_w, 1), ch / max(src_h, 1))
            draw_w = max(1, int(round(src_w * scale_fit)))
            draw_h = max(1, int(round(src_h * scale_fit)))

            display = self.preview_source_image.copy()
            display = display.resize((draw_w, draw_h), Image.Resampling.LANCZOS)
            self.preview_fullscreen_photo = ImageTk.PhotoImage(display)

            canvas.delete("all")
            offset_x = (cw - draw_w) // 2
            offset_y = (ch - draw_h) // 2
            canvas.create_image(offset_x, offset_y, anchor="nw", image=self.preview_fullscreen_photo)
        except Exception as exc:
            messagebox.showerror("全屏预览", f"渲染失败：{exc}")

    def _close_fullscreen_preview(self) -> None:
        if self.preview_fullscreen_win is not None and self.preview_fullscreen_win.winfo_exists():
            try:
                self.preview_fullscreen_win.destroy()
            except Exception:
                pass
        self.preview_fullscreen_win = None
        self.preview_fullscreen_photo = None

    def _render_fullscreen_material_canvas(self) -> None:
        if self.fullscreen_material_canvas is None or self.material_source_bgr_image is None:
            return

        try:
            # Ensure we have non-zero canvas size.
            cw = int(self.fullscreen_material_canvas.winfo_width() or 1)
            ch = int(self.fullscreen_material_canvas.winfo_height() or 1)
            if cw <= 2 or ch <= 2:
                return

            src_h, src_w = self.material_source_bgr_image.shape[:2]
            scale_fit = min(cw / max(src_w, 1), ch / max(src_h, 1))
            draw_w = max(1, int(round(src_w * scale_fit)))
            draw_h = max(1, int(round(src_h * scale_fit)))
            offset_x = (cw - draw_w) // 2
            offset_y = (ch - draw_h) // 2

            rgb = cv2.cvtColor(self.material_source_bgr_image, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb)
            display = pil_image.resize((draw_w, draw_h), Image.Resampling.LANCZOS)
            self.fullscreen_material_canvas_photo = ImageTk.PhotoImage(display)

            self.fullscreen_material_canvas.delete("all")
            self.fullscreen_material_canvas.create_image(offset_x, offset_y, anchor="nw", image=self.fullscreen_material_canvas_photo)

            self.fullscreen_material_canvas_transform = {
                "scale": float(scale_fit),
                "offset_x": float(offset_x),
                "offset_y": float(offset_y),
                "source_w": float(src_w),
                "source_h": float(src_h),
                "draw_w": float(draw_w),
                "draw_h": float(draw_h),
            }
        except Exception as exc:
            # Avoid spamming popups during <Configure> events.
            try:
                if self.material_fullscreen_win is not None and self.material_fullscreen_win.winfo_exists():
                    self.material_fullscreen_win.title(f"全屏素材框选（渲染失败：{exc}）")
            except Exception:
                pass

    def _fullscreen_material_canvas_to_source_bbox(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> tuple[int, int, int, int] | None:
        if self.material_source_bgr_image is None:
            return None
        t = self.fullscreen_material_canvas_transform
        if not t:
            return None

        scale = max(float(t.get("scale", 1.0)), 1e-6)
        offset_x = float(t.get("offset_x", 0.0))
        offset_y = float(t.get("offset_y", 0.0))
        src_w = int(t.get("source_w", self.material_source_bgr_image.shape[1]))
        src_h = int(t.get("source_h", self.material_source_bgr_image.shape[0]))

        draw_w = float(t.get("draw_w", src_w * scale))
        draw_h = float(t.get("draw_h", src_h * scale))

        disp_x1 = min(max(x1, offset_x), offset_x + draw_w)
        disp_y1 = min(max(y1, offset_y), offset_y + draw_h)
        disp_x2 = min(max(x2, offset_x), offset_x + draw_w)
        disp_y2 = min(max(y2, offset_y), offset_y + draw_h)

        if disp_x2 <= disp_x1 or disp_y2 <= disp_y1:
            return None

        sx1 = int((disp_x1 - offset_x) / scale)
        sy1 = int((disp_y1 - offset_y) / scale)
        sx2 = int((disp_x2 - offset_x) / scale)
        sy2 = int((disp_y2 - offset_y) / scale)

        sx1 = max(0, min(src_w - 1, sx1))
        sy1 = max(0, min(src_h - 1, sy1))
        sx2 = max(1, min(src_w, sx2))
        sy2 = max(1, min(src_h, sy2))

        if sx2 - sx1 < 12 or sy2 - sy1 < 12:
            return None
        return (sx1, sy1, sx2, sy2)

    def _on_fullscreen_material_canvas_press(self, event: tk.Event[Any]) -> None:
        if not self.material_mode_var.get() or self.material_source_bgr_image is None:
            return
        canvas = self.fullscreen_material_canvas
        if canvas is None:
            return
        t = self.fullscreen_material_canvas_transform
        if not t:
            return

        x, y = float(event.x), float(event.y)
        offset_x = float(t.get("offset_x", 0.0))
        offset_y = float(t.get("offset_y", 0.0))
        draw_w = float(t.get("draw_w", 0.0))
        draw_h = float(t.get("draw_h", 0.0))

        if not (offset_x <= x <= offset_x + draw_w and offset_y <= y <= offset_y + draw_h):
            return

        self.fullscreen_material_drag_start = (x, y)
        if self.fullscreen_material_canvas_rect_id is not None:
            try:
                canvas.delete(self.fullscreen_material_canvas_rect_id)
            except Exception:
                pass
        self.fullscreen_material_canvas_rect_id = canvas.create_rectangle(
            x, y, x, y, outline="#d52a2a", width=2, dash=(4, 3)
        )

    def _on_fullscreen_material_canvas_drag(self, event: tk.Event[Any]) -> None:
        if not self.material_mode_var.get() or self.material_source_bgr_image is None:
            return
        canvas = self.fullscreen_material_canvas
        if canvas is None or self.fullscreen_material_drag_start is None or self.fullscreen_material_canvas_rect_id is None:
            return
        sx, sy = self.fullscreen_material_drag_start
        ex, ey = float(event.x), float(event.y)
        canvas.coords(self.fullscreen_material_canvas_rect_id, sx, sy, ex, ey)

    def _on_fullscreen_material_canvas_release(self, _event: tk.Event[Any]) -> None:
        if not self.material_mode_var.get() or self.material_source_bgr_image is None or self.fullscreen_material_drag_start is None:
            return
        canvas = self.fullscreen_material_canvas
        if canvas is None or self.fullscreen_material_canvas_rect_id is None:
            self.fullscreen_material_drag_start = None
            return

        x1, y1, x2, y2 = canvas.coords(self.fullscreen_material_canvas_rect_id)
        nx1, ny1, nx2, ny2 = min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
        bbox = self._fullscreen_material_canvas_to_source_bbox(nx1, ny1, nx2, ny2)
        self.fullscreen_material_drag_start = None
        if bbox is None:
            return

        sx1, sy1, sx2, sy2 = bbox
        crop_bgr = self.material_source_bgr_image[sy1:sy2, sx1:sx2].copy()
        if self.material_source_pdf_path is None:
            return

        self.material_selection_count += 1
        try:
            out_dir = (self.state_dir / "selected_materials").resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
            pdf_stem = self.material_source_pdf_path.stem.strip() or "source"
            fname = f"{pdf_stem}_p{self.material_page_var.get()}_{self.material_selection_count:03d}.png"
            out_path = (out_dir / fname).resolve()
            write_image(out_path, crop_bgr)
        except Exception as exc:
            messagebox.showerror("素材框选", f"保存失败：{exc}")
            return

        self._add_input_paths([str(out_path)], source="素材框选(全屏)")
        self._maybe_delete_original_after_material_crop()
        if self.material_delete_target_var.get() == "不删除":
            self._restore_material_source_selection()
        if bool(self.material_auto_advance_var.get()):
            self._step_material_page(1)

    def _on_preview_content_configure(self, _event: tk.Event[Any]) -> None:
        self._update_preview_scrollregion()

    def _on_preview_area_configure(self, event: tk.Event[Any]) -> None:
        width = max(120, int(getattr(event, "width", 120)))
        self.preview_scroll_canvas.itemconfigure(self.preview_content_window, width=width)
        wraplength = max(220, width - 20)
        self.preview_info_label.configure(wraplength=wraplength)
        self._render_preview_from_source()
        self._update_preview_scrollregion()

    def _update_preview_scrollregion(self) -> None:
        try:
            self.preview_scroll_canvas.update_idletasks()
            bbox = self.preview_scroll_canvas.bbox("all")
            if bbox is not None:
                self.preview_scroll_canvas.configure(scrollregion=bbox)
        except Exception:
            return

    def _build_log_section(self) -> None:
        frame = ttk.LabelFrame(self.ui_root, text="运行日志", padding=12)
        frame.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        self.log_frame = frame
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(frame, height=14, wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(
            state="disabled",
            bg=self.colors["surface_bg"],
            fg=self.colors["text"],
            insertbackground=self.colors["accent_dark"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.colors["border"],
        )
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _build_action_section(self) -> None:
        frame = ttk.Frame(self.ui_root, style="App.TFrame")
        frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        frame.columnconfigure(3, weight=1)
        frame.columnconfigure(6, weight=1)
        self.start_button = ttk.Button(frame, text="开始处理", command=self.start_processing, style="Accent.TButton")
        self.start_button.grid(row=0, column=0, sticky="w")
        self.rework_button = ttk.Button(frame, text="打回重生成", command=self.reject_and_regenerate)
        self.rework_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.rework_button.configure(state="disabled")
        self.compact_toggle_button = ttk.Button(
            frame,
            textvariable=self.compact_toggle_text_var,
            command=self._toggle_compact_mode,
        )
        self.compact_toggle_button.grid(row=0, column=2, sticky="w")
        self.progress_bar = ttk.Progressbar(frame, mode="determinate", maximum=100.0, variable=self.progress_value_var)
        self.progress_bar.grid(row=0, column=3, sticky="ew", padx=(12, 12))
        self.open_output_button = ttk.Button(frame, text="打开输出目录", command=self.open_output_folder)
        self.open_output_button.grid(row=0, column=4, sticky="e")
        self.open_debug_button = ttk.Button(frame, text="打开调试目录", command=self.open_debug_folder)
        self.open_debug_button.grid(row=0, column=5, sticky="e", padx=(8, 0))
        ttk.Label(
            frame,
            text="作者：北洋大学 今夕是何年",
            style="Author.TLabel",
        ).grid(row=0, column=6, sticky="e", padx=(12, 0))
        ttk.Label(self.ui_root, textvariable=self.progress_label_var, style="Footer.TLabel").grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Label(self.ui_root, textvariable=self.status_var, style="Footer.TLabel").grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def add_input_files(self) -> None:
        file_paths = filedialog.askopenfilenames(
            title="选择图片或 PDF 文件",
            filetypes=[(SUPPORTED_INPUT_LABEL, SUPPORTED_INPUT_PATTERNS), ("所有文件", "*.*")],
        )
        if file_paths:
            self._add_input_paths(file_paths, source="文件选择")

    def paste_input_files(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self._log("当前正在处理任务，暂不支持粘贴新文件。")
            return
        file_paths = self._read_input_paths_from_clipboard()
        if not file_paths:
            file_paths = self._read_input_images_from_clipboard()
        if not file_paths:
            messagebox.showinfo("粘贴文件", "剪贴板中没有可识别的文件路径/图片。")
            return
        added_count = self._add_input_paths(file_paths, source="粘贴")
        if added_count > 0 and bool(self.auto_start_on_paste_var.get()):
            self._log("已启用“粘贴后自动开始”，准备启动处理。")
            self.after(120, self.start_processing)

    def _on_paste_shortcut(self, _event: tk.Event[Any]) -> str | None:
        focus = self.master.focus_get()
        if isinstance(focus, (tk.Entry, tk.Text, tk.Spinbox, ttk.Entry, ttk.Combobox)):
            return None
        self.paste_input_files()
        return "break"

    def _read_input_paths_from_clipboard(self) -> list[str]:
        file_paths = self._read_windows_clipboard_file_list()
        if file_paths:
            return file_paths
        try:
            text = self.master.clipboard_get()
        except tk.TclError:
            text = self._read_windows_clipboard_unicode_text()
            if not text:
                return []
        return self._extract_paths_from_clipboard_text(text)

    def _read_input_images_from_clipboard(self) -> list[str]:
        # Support pasting an image (e.g. screenshot/copy from some tools).
        if ImageGrab is None:
            return []
        try:
            img = ImageGrab.grabclipboard()
        except Exception:
            return []
        if img is None:
            return []
        try:
            # PIL image -> convert to OpenCV BGR and write as PNG.
            rgb = np.array(img.convert("RGB"))
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        except Exception:
            return []
        try:
            out_dir = (self.state_dir / "pasted_clipboard_images").resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
            fname = f"clipboard_{int(time.time())}.png"
            out_path = (out_dir / fname).resolve()
            write_image(out_path, bgr)
            return [str(out_path)]
        except Exception:
            return []

    def _read_windows_clipboard_file_list(self) -> list[str]:
        if not WINDOWS_DROP_ENABLED:
            return []
        CF_HDROP = 15
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        # Some clipboard producers reject an invalid/non-null "owner" HWND.
        # Allow NULL owner and retry if needed.
        user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        shell32.DragQueryFileW.restype = wintypes.UINT

        owner_id = self.master.winfo_id() if self.master.winfo_exists() else 0
        opened = bool(user32.OpenClipboard(ctypes.c_void_p(owner_id)))
        if not opened:
            opened = bool(user32.OpenClipboard(None))
        if not opened:
            return []
        try:
            handle = user32.GetClipboardData(CF_HDROP)
            if not handle:
                return []
            count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
            results: list[str] = []
            for index in range(count):
                length = shell32.DragQueryFileW(handle, index, None, 0) + 1
                buffer = ctypes.create_unicode_buffer(length)
                shell32.DragQueryFileW(handle, index, buffer, length)
                results.append(buffer.value)
            return results
        except Exception:
            return []
        finally:
            user32.CloseClipboard()

    def _extract_paths_from_clipboard_text(self, text: str) -> list[str]:
        raw = text.replace("\x00", "\n").strip()
        if not raw:
            return []
        direct_path = self._normalize_clipboard_path(raw)
        if direct_path is not None:
            return [direct_path]

        candidates: list[str] = []
        candidates.extend(re.findall(r'"([^"]+)"', raw))
        candidates.extend(re.findall(r"'([^']+)'", raw))
        candidates.extend(re.findall(r"file:///[^\r\n\t]+", raw, flags=re.IGNORECASE))
        # UNC paths in plain text: \\server\share\file.pdf
        candidates.extend(re.findall(r"\\\\[^\r\n\t;]+", raw))
        candidates.extend(re.findall(r"[A-Za-z]:\\[^\r\n\t]+", raw))
        for token in re.split(r"[\r\n\t;]+", raw):
            value = token.strip()
            if value:
                candidates.append(value)

        deduped: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            normalized = self._normalize_clipboard_path(candidate)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _read_windows_clipboard_unicode_text(self) -> str | None:
        # Fallback for when tkinter clipboard_get fails (e.g. clipboard only has CF_HDROP).
        if not WINDOWS_DROP_ENABLED:
            return None
        CF_UNICODETEXT = 13
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE

        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        opened = bool(user32.OpenClipboard(None))
        if not opened:
            return None
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(handle)
        except Exception:
            return None
        finally:
            try:
                user32.CloseClipboard()
            except Exception:
                pass

    def _normalize_clipboard_path(self, raw_value: str) -> str | None:
        value = raw_value.strip().strip('"').strip("'").strip()
        if not value:
            return None
        if value.startswith("{") and value.endswith("}"):
            value = value[1:-1].strip()
        if value.lower().startswith("file:///"):
            value = unquote(value[8:])
            if re.match(r"^[A-Za-z]:/", value):
                value = value.replace("/", "\\")
        elif value.lower().startswith("file://"):
            # e.g. file://server/share/path or file://./path
            rest = unquote(value[7:]).lstrip("/")
            if rest and not re.match(r"^[A-Za-z]:/", rest):
                value = "\\\\" + rest.replace("/", "\\")
        path = Path(value).expanduser()
        if path.exists():
            return str(path.resolve())
        return None

    def remove_selected_inputs(self) -> None:
        selected = list(self.input_listbox.curselection())
        if not selected:
            return
        for index in reversed(selected):
            self.input_listbox.delete(index)
            del self.input_paths[index]
        self.rework_attempt_index = 0
        self._save_state()
        self._refresh_preview()
        self._log(f"已移除 {len(selected)} 个输入文件。")

    def clear_inputs(self) -> None:
        self.input_paths.clear()
        self.input_listbox.delete(0, tk.END)
        self.rework_attempt_index = 0
        self._save_state()
        self._clear_preview(EMPTY_PREVIEW_TEXT)
        self._log("已清空输入列表。")

    def browse_output_directory(self) -> None:
        initial = self.output_dir_var.get() or str((self.project_root / "outputs").resolve())
        path = filedialog.askdirectory(title="选择输出目录", initialdir=initial)
        if path:
            self.output_dir_var.set(path)
            self._save_state()

    def browse_config_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择全局配置文件",
            filetypes=[("配置文件", "*.json *.yaml *.yml"), ("所有文件", "*.*")],
        )
        if path:
            self.config_path_var.set(path)
            self._apply_identity_defaults_from_config()
            self._save_state()

    def browse_header_config_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择页眉配置文件",
            filetypes=[("配置文件", "*.json *.yaml *.yml"), ("所有文件", "*.*")],
        )
        if path:
            self.header_config_var.set(path)
            self._apply_identity_defaults_from_config()
            self._save_state()

    def browse_layout_template_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择版式模板",
            filetypes=[
                ("模板文件", "*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self.layout_template_var.set(path)
            self._save_state()

    def clear_config_path(self) -> None:
        self.config_path_var.set("")
        self._save_state()

    def clear_header_config_path(self) -> None:
        self.header_config_var.set("")
        self._save_state()

    def clear_layout_template_path(self) -> None:
        self.layout_template_var.set("")
        self._save_state()

    def show_layout_template_help(self) -> None:
        messagebox.showinfo(
            "版式模板说明",
            "版式模板用来决定输出 PDF 的版面样式。\n\n"
            "适合导入一张已经排好的参考页，支持 PDF 或图片。\n"
            "程序会尽量识别外框、页眉带和分栏位置，让输出长得更像这个示例。\n\n"
            "常见用法：\n"
            "1. 想完全照模板排版：选择模板，并保持“模板锁定题数”勾选。\n"
            "2. 想保留模板风格，但改成 6 题/页：选择模板，取消“模板锁定题数”，再填写每页题数。",
        )

    def show_dpi_help(self) -> None:
        messagebox.showinfo(
            "DPI 说明",
            "DPI 表示处理时的图像清晰度。\n\n"
            "数值越高，PDF 转图片会更清楚，题号、小字、细线条更容易保留；\n"
            "但处理速度会更慢，内存占用也会更高。\n\n"
            "常用建议：\n"
            "- 220：默认，速度和清晰度比较均衡\n"
            "- 180 到 200：想更快时用\n"
            "- 240 到 300：题图很细、截图模糊、小字较多时用",
        )

    def show_global_config_help(self) -> None:
        messagebox.showinfo(
            "全局配置说明",
            "全局配置会影响整套程序的运行规则。\n\n"
            "它通常是一个 JSON 或 YAML 文件，适合长期保存你自己的默认参数，例如：\n"
            "- 检测阈值\n"
            "- DPI\n"
            "- 布局边距\n"
            "- 水印开关\n"
            "- 模板锁定题数默认值\n\n"
            "如果你只是偶尔处理几份题图，通常可以不填这一项。",
        )

    def show_header_config_help(self) -> None:
        messagebox.showinfo(
            "页眉配置说明",
            "页眉配置只影响页眉，不会改变题目检测和主体排版。\n\n"
            "适合单独调整这些内容：\n"
            "- 页眉样式\n"
            "- 学号 / 班级 / 姓名默认值\n"
            "- 字号、间距、标题等\n\n"
            "如果你只是想快速改页眉，而不想动整套全局参数，就用这一项。",
        )

    def toggle_advanced_settings(self) -> None:
        self._set_advanced_visible(not self.advanced_visible)

    def _set_advanced_visible(self, visible: bool) -> None:
        self.advanced_visible = bool(visible)
        if self.advanced_visible:
            self.advanced_frame.grid()
            self.advanced_toggle_text_var.set("隐藏高级设置")
        else:
            self.advanced_frame.grid_remove()
            self.advanced_toggle_text_var.set("显示高级设置")

    def _on_strict_single_question_toggle(self) -> None:
        self._sync_strict_single_question_state()
        self._save_state()

    def _on_per_page_value_changed(self, *_args: Any) -> None:
        if hasattr(self, "layout_choice_frame"):
            self._refresh_layout_pattern_controls()

    def _on_layout_pattern_mode_changed(self, *_args: Any) -> None:
        if hasattr(self, "custom_layout_pattern_entry"):
            self._refresh_layout_pattern_controls()

    def _on_layout_pattern_combo_selected(self, _event: tk.Event[Any] | None = None) -> None:
        per_page = self._current_per_page_value()
        if per_page not in (2, 3):
            self.layout_pattern_mode_var.set("auto")
            self.layout_pattern_display_var.set("自动")
            return

        selected_label = self.layout_pattern_display_var.get().strip()
        options = self._layout_options_for_per_page(int(per_page))
        label_to_value = {label: value for label, value in options}
        self.layout_pattern_mode_var.set(label_to_value.get(selected_label, "auto"))

    def _sync_strict_single_question_state(self) -> None:
        if bool(self.strict_single_question_var.get()):
            self.per_page_var.set("1")
            self.per_page_entry.configure(state="disabled")
        else:
            if self.worker is None or not self.worker.is_alive():
                self.per_page_entry.configure(state="normal")
        self._refresh_layout_pattern_controls()

    def _current_per_page_value(self) -> int | None:
        raw = self.per_page_var.get().strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _layout_options_for_per_page(self, per_page: int) -> list[tuple[str, str]]:
        if per_page == 2:
            return [
                ("自动", "auto"),
                ("上下排版（1+1）", "vertical"),
                ("左右排版（2）", "horizontal"),
                ("自定义", "custom"),
            ]
        if per_page == 3:
            return [
                ("自动", "auto"),
                ("上1下2（1+2）", "top1_bottom2"),
                ("上2下1（2+1）", "top2_bottom1"),
                ("三行排版（1+1+1）", "three_rows"),
                ("单行排版（3）", "horizontal"),
                ("自定义", "custom"),
            ]
        return [("自动", "auto")]

    def _refresh_layout_pattern_controls(self) -> None:
        if not hasattr(self, "layout_choice_frame"):
            return

        per_page = self._current_per_page_value()
        strict_mode = bool(self.strict_single_question_var.get())
        show_choices = (per_page in (2, 3)) and not strict_mode

        if not show_choices:
            self.layout_choice_frame.grid_remove()
            if self.layout_pattern_mode_var.get() != "auto":
                self.layout_pattern_mode_var.set("auto")
            return

        options = self._layout_options_for_per_page(int(per_page))
        valid_values = {value for _, value in options}
        current_value = self.layout_pattern_mode_var.get().strip() or "auto"
        if current_value not in valid_values:
            current_value = "auto"
            self.layout_pattern_mode_var.set(current_value)
        self.layout_pattern_combo.configure(values=[label for label, _ in options])
        self.layout_pattern_display_var.set(next(label for label, value in options if value == current_value))
        if current_value == "custom":
            self.custom_layout_pattern_entry.grid()
        else:
            self.custom_layout_pattern_entry.grid_remove()
        self.layout_choice_frame.grid()

    def start_processing(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("任务进行中", "当前已有任务正在处理，请稍候。")
            return

        try:
            input_paths = self._get_input_paths()
            output_path = self._get_output_path()
            config = self._build_config()
        except Exception as exc:
            messagebox.showerror("输入有误", str(exc))
            return

        self.rework_attempt_index = 0
        self._start_processing_job(input_paths, output_path, config, run_reason="标准处理")

    def reject_and_regenerate(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("任务进行中", "当前已有任务正在处理，请稍候。")
            return
        if self.last_result is None:
            messagebox.showinfo("打回重生成", "请先完成一次生成，再使用打回重生。")
            return

        try:
            input_paths = self._get_input_paths()
            output_path = self._get_output_path()
            base_config = self._build_config()
        except Exception as exc:
            messagebox.showerror("输入有误", str(exc))
            return

        profile_key, profile_name = self._next_rework_profile()
        config = self._apply_rework_profile(base_config, profile_key)
        self._start_processing_job(
            input_paths,
            output_path,
            config,
            run_reason=f"打回重生成（{profile_name}）",
        )

    def _start_processing_job(
        self,
        input_paths: list[Path],
        output_path: Path,
        config: AppConfig,
        *,
        run_reason: str,
    ) -> None:
        self.current_run_reason = run_reason
        self.last_run_config = config
        self._save_state()
        self.last_result = None
        self._clear_manual_editor()
        self.last_progress_message = ""
        self._set_running(True)
        self.progress_value_var.set(0.0)
        self.progress_label_var.set("正在启动处理流程...")
        self.status_var.set("运行中")
        self._log(f"{run_reason}：开始执行处理流程。")
        self._log(f"输入文件数：{len(input_paths)}")
        self._log(f"输出 PDF：{output_path}")
        self._log(
            f"模式：{self.mode_var.get()}，DPI：{config.io.image_dpi}，每页题数：{config.layout.problems_per_page}，严格单题：{'开启' if config.detection.strict_single_question_mode else '关闭'}，PDF 水印：{'开启' if config.watermark.enabled else '关闭'}，调试导出：{'开启' if config.debug.enabled else '关闭'}"
        )
        if config.layout.template_path.strip():
            self._log(
                f"版式模板：{Path(config.layout.template_path).expanduser().resolve()}，模板锁定题数：{'开启' if config.layout.template_lock_per_page else '关闭'}"
            )

        self.worker = threading.Thread(
            target=self._run_pipeline_worker,
            args=(input_paths, output_path, config),
            daemon=True,
        )
        self.worker.start()

    def _next_rework_profile(self) -> tuple[str, str]:
        index = self.rework_attempt_index % len(REWORK_PROFILES)
        self.rework_attempt_index += 1
        return REWORK_PROFILES[index]

    def _apply_rework_profile(self, base_config: AppConfig, profile_key: str) -> AppConfig:
        config = deepcopy(base_config)
        detection = config.detection
        crop = config.crop

        if profile_key == "conservative_split":
            detection.close_kernel_width = self._scaled_odd(detection.close_kernel_width, 0.72, minimum=9)
            detection.close_kernel_height = self._scaled_odd(detection.close_kernel_height, 0.72, minimum=7)
            detection.merge_x_gap_ratio = max(0.012, detection.merge_x_gap_ratio * 0.72)
            detection.merge_y_gap_ratio = max(0.010, detection.merge_y_gap_ratio * 0.70)
            detection.merge_overlap_ratio = min(0.35, detection.merge_overlap_ratio * 1.18)
            detection.merge_peer_region_area_ratio = min(0.82, detection.merge_peer_region_area_ratio + 0.10)
            detection.allow_sparse_problem_expansion = False
            crop.expand_bottom_ratio = min(0.15, crop.expand_bottom_ratio + 0.02)
            return config

        if profile_key == "strict_anti_merge":
            detection.close_kernel_width = self._scaled_odd(detection.close_kernel_width, 0.58, minimum=7)
            detection.close_kernel_height = self._scaled_odd(detection.close_kernel_height, 0.58, minimum=5)
            detection.merge_x_gap_ratio = max(0.009, detection.merge_x_gap_ratio * 0.55)
            detection.merge_y_gap_ratio = max(0.008, detection.merge_y_gap_ratio * 0.52)
            detection.merge_overlap_ratio = min(0.40, detection.merge_overlap_ratio * 1.35)
            detection.merge_small_attachment_area_ratio = max(0.18, detection.merge_small_attachment_area_ratio * 0.75)
            detection.merge_peer_region_area_ratio = min(0.88, detection.merge_peer_region_area_ratio + 0.16)
            detection.min_region_area_ratio = max(0.0010, detection.min_region_area_ratio * 0.80)
            detection.allow_sparse_problem_expansion = False
            return config

        # band_fallback
        detection.strict_projection_fallback_enabled = True
        detection.strict_projection_row_density_threshold = max(0.0025, detection.strict_projection_row_density_threshold * 0.85)
        detection.strict_projection_gap_ratio = max(0.0090, detection.strict_projection_gap_ratio * 0.80)
        detection.strict_projection_min_band_height_ratio = max(0.012, detection.strict_projection_min_band_height_ratio * 0.80)
        detection.strict_projection_min_bands = max(2, detection.strict_projection_min_bands)
        detection.enable_pdf_text_number_detection = True
        detection.pdf_text_anchor_max_step = max(2, min(6, detection.pdf_text_anchor_max_step + 1))
        detection.pdf_text_min_questions = max(2, detection.pdf_text_min_questions)
        crop.expand_bottom_ratio = min(0.16, crop.expand_bottom_ratio + 0.03)
        return config

    @staticmethod
    def _scaled_odd(value: int, factor: float, *, minimum: int) -> int:
        scaled = max(minimum, int(round(float(value) * float(factor))))
        return scaled if scaled % 2 == 1 else scaled + 1

    def _run_pipeline_worker(self, input_paths: list[Path], output_path: Path, config: AppConfig) -> None:
        try:
            result = run_pipeline(
                input_paths=input_paths,
                output_path=output_path,
                config=config,
                progress_callback=self._queue_progress,
            )
            self.message_queue.put(("success", result))
        except Exception as exc:  # pragma: no cover - user-facing thread wrapper
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self.message_queue.put(("error", details))

    def _queue_progress(self, progress: PipelineProgress) -> None:
        self.message_queue.put(("progress", progress))

    def _poll_queue(self) -> None:
        try:
            while True:
                message_type, payload = self.message_queue.get_nowait()
                if message_type == "success":
                    self._handle_success(payload)
                elif message_type == "error":
                    self._handle_error(payload)
                elif message_type == "progress":
                    self._handle_progress(payload)
                elif message_type == "drop":
                    self._handle_dropped_files(payload)
        except Empty:
            pass
        self.after(150, self._poll_queue)

    def _handle_success(self, result: PipelineResult) -> None:
        self.last_result = result
        self._set_running(False)
        self.progress_value_var.set(100.0)
        self.progress_label_var.set("处理完成。")
        self.status_var.set("已完成")
        self._log(f"{self.current_run_reason}执行成功。")
        self._log(f"源页面数：{len(result.page_results)}")
        self._log(f"检测到的小题数：{len(result.cropped_problems)}")
        self._log(f"输出页数：{len(result.page_layouts)}")
        self._log(f"输出 PDF：{result.output_path}")
        if result.debug_dir is not None:
            self._log(f"调试目录：{result.debug_dir}")
        self._initialize_manual_editor_from_result(result)
        self._show_result_preview_if_available(result)
        self._save_state()
        messagebox.showinfo("处理完成", f"{self.current_run_reason}\nPDF 已生成：\n{result.output_path}")

    def _handle_error(self, details: str) -> None:
        self._set_running(False)
        self._clear_manual_editor()
        self.status_var.set("失败")
        self.progress_label_var.set("处理失败。")
        self._log(f"{self.current_run_reason}执行失败。")
        self._log(details)
        messagebox.showerror("处理失败", details.splitlines()[0] if details else "未知错误")

    def _handle_progress(self, progress: PipelineProgress) -> None:
        self.progress_value_var.set(progress.fraction * 100.0)
        self.progress_label_var.set(progress.message)
        stage_label = STAGE_LABELS.get(progress.stage, progress.stage)
        self.status_var.set(f"{stage_label}: {progress.completed}/{progress.total}" if progress.total else stage_label)
        if progress.message != self.last_progress_message:
            self._log(progress.message)
            self.last_progress_message = progress.message

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.start_button.configure(state=state)
        self.compact_toggle_button.configure(state=state)
        if running:
            self.rework_button.configure(state="disabled")
        else:
            self.rework_button.configure(state="normal" if self.last_result is not None else "disabled")
        self.open_output_button.configure(state="disabled" if running else "normal")
        self.open_debug_button.configure(state="disabled" if running else "normal")
        self.add_files_button.configure(state=state)
        self.remove_files_button.configure(state=state)
        self.clear_files_button.configure(state=state)
        self.paste_files_button.configure(state=state)
        self.auto_start_on_paste_checkbox.configure(state=state)
        self.output_browse_button.configure(state=state)
        self.output_dir_entry.configure(state=state)
        self.output_name_entry.configure(state=state)
        self.advanced_toggle_button.configure(state=state)
        self.manual_toggle_button.configure(state=state)
        self.mode_combo.configure(state="disabled" if running else "readonly")
        self.dpi_entry.configure(state=state)
        self.dpi_help_button.configure(state=state)
        self.per_page_entry.configure(state=state)
        self.layout_pattern_combo.configure(state="disabled" if running else "readonly")
        self.custom_layout_pattern_entry.configure(state=state)
        self.debug_checkbox.configure(state=state)
        self.pdf_watermark_checkbox.configure(state=state)
        self.strict_single_question_checkbox.configure(state=state)
        self.layout_template_entry.configure(state=state)
        self.template_lock_per_page_checkbox.configure(state=state)
        self.config_entry.configure(state=state)
        self.header_config_entry.configure(state=state)
        self.layout_template_browse_button.configure(state=state)
        self.layout_template_clear_button.configure(state=state)
        self.layout_template_help_button.configure(state=state)
        self.config_browse_button.configure(state=state)
        self.header_config_browse_button.configure(state=state)
        self.config_clear_button.configure(state=state)
        self.header_config_clear_button.configure(state=state)
        self.config_help_button.configure(state=state)
        self.header_config_help_button.configure(state=state)
        self.input_listbox.configure(state=state)
        self.student_id_entry.configure(state=state)
        self.class_name_entry.configure(state=state)
        self.student_name_entry.configure(state=state)
        if running:
            self._set_manual_controls_enabled(False)
        if not running:
            self._sync_strict_single_question_state()

    def _set_manual_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for control in getattr(self, "manual_controls", []):
            control.configure(state=state)

    def _clear_manual_editor(self) -> None:
        self.manual_adjustments.clear()
        self.manual_page_problem_id.clear()
        self.manual_problem_index.clear()
        self.manual_work_crops.clear()
        self.manual_base_crops.clear()
        self.manual_page_image_by_index.clear()
        self._set_manual_tools_available(False)
        self.manual_focus_view = True
        self.manual_view_toggle_text_var.set("查看整页")
        self.manual_zoom_var.set(170.0)
        self._update_manual_zoom_label()
        self.manual_canvas_transform = {}
        self.manual_canvas_rect_id = None
        self.manual_drag_edge = None
        self.manual_canvas.delete("all")
        placeholder_x = max(160, int(self.manual_canvas.winfo_width() or 320) // 2)
        placeholder_y = max(110, int(self.manual_canvas.winfo_height() or 220) // 2)
        self.manual_canvas.create_text(
            placeholder_x,
            placeholder_y,
            text="处理完成后可在这里拖拽校正边界",
            fill=self.colors["muted_text"],
        )
        self.manual_page_var.set("1")
        self.manual_status_var.set("手动校正未启用")
        self._set_manual_controls_enabled(False)

    def _initialize_manual_editor_from_result(self, result: PipelineResult) -> None:
        self._clear_manual_editor()
        if self.last_run_config is None:
            return
        if not result.page_layouts or not result.cropped_problems:
            return

        page_problem_map: dict[int, str] = {}
        for page_layout in result.page_layouts:
            problems = [item.problem for item in page_layout.placements if item.problem is not None]
            if len(problems) != 1:
                self.manual_status_var.set("当前排版不是“每页 1 题”，手动校正面板仅在单题页模式下启用。")
                self._set_manual_tools_available(False)
                return
            page_problem_map[page_layout.page_number] = problems[0].problem_id

        self.manual_page_problem_id = page_problem_map
        self.manual_page_var.set("1")
        self.manual_page_spinbox.configure(to=max(1, len(result.page_layouts)))

        for crop in result.cropped_problems:
            clone = self._clone_problem(crop)
            self.manual_work_crops.append(clone)
            self.manual_base_crops[crop.problem_id] = self._clone_problem(crop)
            self.manual_adjustments[crop.problem_id] = {"top": 0, "bottom": 0, "left": 0, "right": 0}
            self.manual_problem_index[crop.problem_id] = len(self.manual_work_crops) - 1

        for page_result in result.page_results:
            if page_result.page.image is not None:
                self.manual_page_image_by_index[page_result.page.page_index] = page_result.page.image

        self._set_manual_tools_available(True)
        self._set_manual_controls_enabled(True)
        self.manual_status_var.set("手动校正已启用：点击“显示手动校正”后可按页微调。")
        self._render_manual_source_canvas()
        self._render_manual_selected_page_preview()

    def _clone_problem(self, problem: CroppedProblem) -> CroppedProblem:
        image = None if problem.image is None else problem.image.copy()
        metadata = dict(problem.metadata)
        if "manual_adjustment" in metadata:
            metadata.pop("manual_adjustment", None)
        return replace(problem, image=image, metadata=metadata)

    def _on_manual_page_changed(self, _event: tk.Event[Any] | None = None) -> None:
        if not self.manual_page_problem_id:
            return
        try:
            self._get_manual_selected_page()
            self._render_manual_source_canvas()
            self._render_manual_selected_page_preview()
        except Exception as exc:
            messagebox.showerror("手动校正", str(exc))

    def _get_manual_selected_page(self) -> int:
        if not self.manual_page_problem_id:
            raise ValueError("没有可校正的页面。")
        try:
            page_number = int(self.manual_page_var.get().strip())
        except ValueError as exc:
            raise ValueError("页码必须是整数。") from exc
        page_number = max(1, min(page_number, len(self.manual_page_problem_id)))
        self.manual_page_var.set(str(page_number))
        return page_number

    def _get_manual_step(self) -> int:
        raw = self.manual_step_var.get().strip()
        if not raw:
            return 5
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError("步长必须是整数。") from exc
        return max(1, min(value, 200))

    def _adjust_manual_boundary(self, edge: str, direction: int) -> None:
        if not self.manual_page_problem_id or self.last_result is None:
            return
        try:
            page_number = self._get_manual_selected_page()
            step = self._get_manual_step()
        except ValueError as exc:
            messagebox.showerror("手动校正", str(exc))
            return

        problem_id = self.manual_page_problem_id.get(page_number)
        if not problem_id:
            return
        delta = step * int(direction)
        adjustment = self.manual_adjustments.setdefault(problem_id, {"top": 0, "bottom": 0, "left": 0, "right": 0})
        adjustment[edge] += delta
        if not self._apply_manual_adjustment(problem_id):
            adjustment[edge] -= delta
            messagebox.showinfo("手动校正", "调整过大，已经触达有效边界。")
            return
        try:
            self._render_manual_source_canvas()
            self._render_manual_selected_page_preview()
        except Exception as exc:
            messagebox.showerror("手动校正", str(exc))

    def _apply_manual_adjustment(self, problem_id: str) -> bool:
        base_problem = self.manual_base_crops.get(problem_id)
        if base_problem is None:
            return False
        page_image = self.manual_page_image_by_index.get(base_problem.page_index)
        if page_image is None:
            return False

        adjust = self.manual_adjustments.get(problem_id, {"top": 0, "bottom": 0, "left": 0, "right": 0})
        base_x1, base_y1, base_x2, base_y2 = base_problem.cropped_bbox
        y1 = max(0, base_y1 + int(adjust["top"]))
        y2 = min(page_image.shape[0], base_y2 + int(adjust["bottom"]))
        x1 = max(0, base_x1 + int(adjust["left"]))
        x2 = min(page_image.shape[1], base_x2 + int(adjust["right"]))
        if y2 - y1 < 12 or x2 - x1 < 12:
            return False

        updated_image = page_image[y1:y2, x1:x2].copy()
        index = self.manual_problem_index.get(problem_id)
        if index is None:
            return False
        target = self.manual_work_crops[index]
        target.cropped_bbox = (x1, y1, x2, y2)
        target.image = updated_image
        target.metadata = dict(target.metadata)
        target.metadata["manual_adjustment"] = dict(adjust)
        return True

    def _get_manual_current_problem(self) -> tuple[int, str, CroppedProblem, CroppedProblem, np.ndarray]:
        page_number = self._get_manual_selected_page()
        problem_id = self.manual_page_problem_id.get(page_number)
        if not problem_id:
            raise ValueError("当前页没有可校正题目。")
        index = self.manual_problem_index.get(problem_id)
        if index is None:
            raise ValueError("题目索引丢失。")
        work_problem = self.manual_work_crops[index]
        base_problem = self.manual_base_crops[problem_id]
        page_image = self.manual_page_image_by_index.get(work_problem.page_index)
        if page_image is None:
            raise ValueError("原始页面图像不可用。")
        return page_number, problem_id, work_problem, base_problem, page_image

    def _render_manual_source_canvas(self) -> None:
        if not self.manual_page_problem_id:
            return
        _, _, work_problem, _, page_image = self._get_manual_current_problem()
        source_rgb = cv2.cvtColor(page_image, cv2.COLOR_BGR2RGB)
        source_pil = Image.fromarray(source_rgb)
        page_w, page_h = source_pil.width, source_pil.height
        x1, y1, x2, y2 = work_problem.cropped_bbox

        if self.manual_focus_view:
            box_w = max(1, x2 - x1)
            box_h = max(1, y2 - y1)
            zoom_factor = max(1.0, min(3.0, float(self.manual_zoom_var.get()) / 100.0))
            margin_scale = max(0.3, 1.35 / zoom_factor)
            margin_x = max(40, int(box_w * margin_scale))
            margin_y = max(56, int(box_h * margin_scale * 1.1))
            roi_x1 = max(0, x1 - margin_x)
            roi_y1 = max(0, y1 - margin_y)
            roi_x2 = min(page_w, x2 + margin_x)
            roi_y2 = min(page_h, y2 + margin_y)

            min_roi_w = max(140, int(page_w * (0.26 / zoom_factor)))
            min_roi_h = max(120, int(page_h * (0.24 / zoom_factor)))
            if roi_x2 - roi_x1 < min_roi_w:
                cx = (roi_x1 + roi_x2) // 2
                half = min_roi_w // 2
                roi_x1 = max(0, cx - half)
                roi_x2 = min(page_w, roi_x1 + min_roi_w)
                roi_x1 = max(0, roi_x2 - min_roi_w)
            if roi_y2 - roi_y1 < min_roi_h:
                cy = (roi_y1 + roi_y2) // 2
                half = min_roi_h // 2
                roi_y1 = max(0, cy - half)
                roi_y2 = min(page_h, roi_y1 + min_roi_h)
                roi_y1 = max(0, roi_y2 - min_roi_h)
        else:
            roi_x1, roi_y1, roi_x2, roi_y2 = 0, 0, page_w, page_h

        view_w = max(1, roi_x2 - roi_x1)
        view_h = max(1, roi_y2 - roi_y1)
        view_pil = source_pil.crop((roi_x1, roi_y1, roi_x2, roi_y2))

        canvas_w = max(320, int(self.manual_canvas.winfo_width() or 520))
        canvas_h = max(240, int(self.manual_canvas.winfo_height() or 360))
        scale = min(canvas_w / max(view_w, 1), canvas_h / max(view_h, 1))
        draw_w = max(1, int(round(view_w * scale)))
        draw_h = max(1, int(round(view_h * scale)))
        offset_x = (canvas_w - draw_w) // 2
        offset_y = (canvas_h - draw_h) // 2

        display = view_pil.resize((draw_w, draw_h), Image.Resampling.BILINEAR)
        self.manual_canvas_photo = ImageTk.PhotoImage(display)
        self.manual_canvas.delete("all")
        self.manual_canvas.create_image(offset_x, offset_y, anchor="nw", image=self.manual_canvas_photo)

        rx1 = offset_x + ((x1 - roi_x1) * scale)
        ry1 = offset_y + ((y1 - roi_y1) * scale)
        rx2 = offset_x + ((x2 - roi_x1) * scale)
        ry2 = offset_y + ((y2 - roi_y1) * scale)
        self.manual_canvas_rect_id = self.manual_canvas.create_rectangle(
            rx1,
            ry1,
            rx2,
            ry2,
            outline="#d52a2a",
            width=2,
            dash=(4, 3),
        )
        self.manual_canvas.create_text(
            8,
            8,
            anchor="nw",
            text="拖拽红框边缘可调大小；按钮可精调。",
            fill=self.colors["accent_dark"],
            font=("Microsoft YaHei UI", 9),
        )
        self.manual_canvas_transform = {
            "scale": float(scale),
            "offset_x": float(offset_x),
            "offset_y": float(offset_y),
            "source_w": float(page_w),
            "source_h": float(page_h),
            "roi_x1": float(roi_x1),
            "roi_y1": float(roi_y1),
        }

    def _canvas_to_source(self, canvas_x: float, canvas_y: float) -> tuple[int, int]:
        scale = float(self.manual_canvas_transform.get("scale", 1.0))
        offset_x = float(self.manual_canvas_transform.get("offset_x", 0.0))
        offset_y = float(self.manual_canvas_transform.get("offset_y", 0.0))
        roi_x1 = float(self.manual_canvas_transform.get("roi_x1", 0.0))
        roi_y1 = float(self.manual_canvas_transform.get("roi_y1", 0.0))
        source_w = int(self.manual_canvas_transform.get("source_w", 1.0))
        source_h = int(self.manual_canvas_transform.get("source_h", 1.0))
        sx = int(round((canvas_x - offset_x) / max(scale, 1e-6) + roi_x1))
        sy = int(round((canvas_y - offset_y) / max(scale, 1e-6) + roi_y1))
        return (max(0, min(source_w - 1, sx)), max(0, min(source_h - 1, sy)))

    def _pick_drag_edge(self, canvas_x: float, canvas_y: float) -> str | None:
        if self.manual_canvas_rect_id is None:
            return None
        x1, y1, x2, y2 = self.manual_canvas.coords(self.manual_canvas_rect_id)
        edge_tol = 12.0
        inside_x = x1 - edge_tol <= canvas_x <= x2 + edge_tol
        inside_y = y1 - edge_tol <= canvas_y <= y2 + edge_tol
        if not inside_x or not inside_y:
            return None
        distances = {
            "left": abs(canvas_x - x1),
            "right": abs(canvas_x - x2),
            "top": abs(canvas_y - y1),
            "bottom": abs(canvas_y - y2),
        }
        edge = min(distances, key=distances.get)
        return edge if distances[edge] <= edge_tol else None

    def _update_adjustment_from_bbox(self, problem_id: str, bbox: tuple[int, int, int, int]) -> None:
        base = self.manual_base_crops[problem_id].cropped_bbox
        self.manual_adjustments[problem_id] = {
            "left": int(bbox[0] - base[0]),
            "top": int(bbox[1] - base[1]),
            "right": int(bbox[2] - base[2]),
            "bottom": int(bbox[3] - base[3]),
        }

    def _format_manual_status(self, page_number: int, adjustment: dict[str, int]) -> str:
        return (
            f"当前页 {page_number}：上 {adjustment['top']} px，下 {adjustment['bottom']} px，"
            f"左 {adjustment['left']} px，右 {adjustment['right']} px。"
        )

    def _on_manual_canvas_press(self, event: tk.Event[Any]) -> None:
        if not self.manual_page_problem_id:
            return
        self.manual_drag_edge = self._pick_drag_edge(float(event.x), float(event.y))

    def _on_manual_canvas_drag(self, event: tk.Event[Any]) -> None:
        if not self.manual_page_problem_id or self.manual_drag_edge is None:
            return
        try:
            _, problem_id, work_problem, _, page_image = self._get_manual_current_problem()
        except Exception:
            return

        x1, y1, x2, y2 = work_problem.cropped_bbox
        sx, sy = self._canvas_to_source(float(event.x), float(event.y))
        min_w = 12
        min_h = 12
        if self.manual_drag_edge == "top":
            y1 = max(0, min(sy, y2 - min_h))
        elif self.manual_drag_edge == "bottom":
            y2 = min(page_image.shape[0], max(sy, y1 + min_h))
        elif self.manual_drag_edge == "left":
            x1 = max(0, min(sx, x2 - min_w))
        elif self.manual_drag_edge == "right":
            x2 = min(page_image.shape[1], max(sx, x1 + min_w))
        else:
            return

        work_problem.cropped_bbox = (x1, y1, x2, y2)
        work_problem.image = page_image[y1:y2, x1:x2].copy()
        self._update_adjustment_from_bbox(problem_id, work_problem.cropped_bbox)
        self._render_manual_source_canvas()
        adjustment = self.manual_adjustments.get(problem_id, {"top": 0, "bottom": 0, "left": 0, "right": 0})
        page_number = int(self.manual_page_var.get())
        self.manual_status_var.set(self._format_manual_status(page_number, adjustment))

    def _on_manual_canvas_release(self, _event: tk.Event[Any]) -> None:
        self.manual_drag_edge = None

    def _reset_manual_page(self) -> None:
        if not self.manual_page_problem_id:
            return
        try:
            page_number = self._get_manual_selected_page()
        except ValueError as exc:
            messagebox.showerror("手动校正", str(exc))
            return
        problem_id = self.manual_page_problem_id.get(page_number)
        if not problem_id:
            return
        self.manual_adjustments[problem_id] = {"top": 0, "bottom": 0, "left": 0, "right": 0}
        self._apply_manual_adjustment(problem_id)
        try:
            self._render_manual_source_canvas()
            self._render_manual_selected_page_preview()
        except Exception as exc:
            messagebox.showerror("手动校正", str(exc))

    def _compose_manual_page_layouts(self) -> list[Any]:
        if self.last_run_config is None or self.last_result is None:
            raise ValueError("当前没有可用的运行结果。")
        return paginate_problems(
            self.manual_work_crops,
            self.last_run_config.layout,
            template_spec=self.last_result.template_spec,
        )

    def _render_manual_selected_page_preview(self) -> None:
        if not self.manual_page_problem_id or self.last_result is None or self.last_run_config is None:
            return
        page_number = self._get_manual_selected_page()
        layouts = self._compose_manual_page_layouts()
        if page_number < 1 or page_number > len(layouts):
            return

        preview_pdf = (self.project_root / ".cache" / "manual_preview_page.pdf").resolve()
        preview_pdf.parent.mkdir(parents=True, exist_ok=True)
        render_pdf(
            preview_pdf,
            [layouts[page_number - 1]],
            self.last_run_config.to_render_config(),
            self.last_run_config.header,
            self.last_run_config.fonts,
            self.last_run_config.watermark,
        )
        image, info = self._render_pdf_preview(preview_pdf, page_number=1)
        problem_id = self.manual_page_problem_id.get(page_number, "")
        adjustment = self.manual_adjustments.get(problem_id, {"top": 0, "bottom": 0, "left": 0, "right": 0})
        self._show_preview_image(image, f"{info}\n手动校正预览（目标页：{page_number}）")
        self.manual_status_var.set(self._format_manual_status(page_number, adjustment))

    def _export_manual_corrected_pdf(self) -> None:
        if not self.manual_page_problem_id or self.last_result is None or self.last_run_config is None:
            messagebox.showinfo("手动校正", "当前没有可导出的校正结果。")
            return
        try:
            output_path = self._get_output_path()
            layouts = self._compose_manual_page_layouts()
            render_pdf(
                output_path,
                layouts,
                self.last_run_config.to_render_config(),
                self.last_run_config.header,
                self.last_run_config.fonts,
                self.last_run_config.watermark,
            )
            self._show_result_preview_if_available(
                replace(
                    self.last_result,
                    output_path=output_path,
                    page_layouts=layouts,
                    cropped_problems=self.manual_work_crops,
                )
            )
            self.manual_status_var.set(f"已导出校正后 PDF：{output_path}")
            self._log(f"手动校正已导出：{output_path}")
            messagebox.showinfo("手动校正", f"已导出校正后 PDF：\n{output_path}")
        except Exception as exc:
            messagebox.showerror("手动校正", f"导出失败：{exc}")

    def open_output_folder(self) -> None:
        target = self.last_result.output_path.parent if self.last_result is not None else self._current_output_parent()
        self._open_path(target)

    def open_debug_folder(self) -> None:
        if self.last_result is None or self.last_result.debug_dir is None:
            messagebox.showinfo("调试目录", "当前还没有可用的调试输出。")
            return
        self._open_path(self.last_result.debug_dir)

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            messagebox.showerror("路径不存在", f"未找到路径：\n{path}")
            return
        os.startfile(str(path))

    def _get_input_paths(self) -> list[Path]:
        if not self.input_paths:
            raise ValueError("请至少添加一个输入图片或 PDF 文件。")
        return [Path(path).expanduser().resolve() for path in self.input_paths]

    def _get_output_path(self) -> Path:
        output_dir_raw = self.output_dir_var.get().strip()
        if not output_dir_raw:
            raise ValueError("请选择输出目录。")
        output_name_raw = self.output_name_var.get().strip()
        if not output_name_raw:
            raise ValueError("请输入输出文件名。")

        output_dir = Path(output_dir_raw).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        sanitized_name = output_name_raw.replace("/", "_").replace("\\", "_").strip()
        if not sanitized_name:
            raise ValueError("输出文件名不能为空。")
        if not sanitized_name.lower().endswith(".pdf"):
            sanitized_name = f"{sanitized_name}.pdf"
        return (output_dir / sanitized_name).resolve()

    def _build_config(self) -> AppConfig:
        dpi = self._parse_positive_int(self.dpi_var.get().strip(), field_name="DPI")
        per_page = self._parse_positive_int(self.per_page_var.get().strip(), field_name="每页题数")
        layout_template_path = self._normalized_optional_path(self.layout_template_var.get())
        config_path = self._normalized_optional_path(self.config_path_var.get())
        header_config_path = self._normalized_optional_path(self.header_config_var.get())

        identity_overrides: dict[str, str] = {}
        if self.student_id_var.get().strip():
            identity_overrides["student_id"] = self.student_id_var.get().strip()
        if self.class_name_var.get().strip():
            identity_overrides["class_name"] = self.class_name_var.get().strip()
        if self.student_name_var.get().strip():
            identity_overrides["student_name"] = self.student_name_var.get().strip()

        overrides: dict[str, Any] = {
            "performance": {"mode": self._selected_mode_value()},
            "debug": {"enabled": bool(self.debug_var.get())},
            "io": {"image_dpi": dpi, "pdf_dpi": dpi},
            "layout": {
                "problems_per_page": per_page,
                "template_lock_per_page": bool(self.template_lock_per_page_var.get()),
                "layout_pattern_mode": "auto",
                "custom_row_pattern": "",
            },
            "watermark": {"enabled": bool(self.pdf_watermark_var.get())},
        }
        if per_page in (2, 3):
            selected_pattern_mode = self.layout_pattern_mode_var.get().strip() or "auto"
            overrides["layout"]["layout_pattern_mode"] = selected_pattern_mode
            if selected_pattern_mode == "custom":
                custom_pattern = self.custom_layout_pattern_var.get().strip()
                if not custom_pattern:
                    raise ValueError("自定义版式不能为空，例如：1+2 或 2+1。")
                parse_row_pattern_spec(custom_pattern, expected_total=per_page)
                overrides["layout"]["custom_row_pattern"] = custom_pattern
        if bool(self.strict_single_question_var.get()):
            overrides["layout"]["problems_per_page"] = 1
            overrides["layout"]["layout_pattern_mode"] = "auto"
            overrides["layout"]["custom_row_pattern"] = ""
            overrides["detection"] = {
                "strict_single_question_mode": True,
                "enable_pdf_text_number_detection": True,
                "pdf_text_min_questions": 2,
                "pdf_text_anchor_max_step": 2,
                "pdf_text_bottom_trim_threshold": 242,
            }
        else:
            overrides["detection"] = {"strict_single_question_mode": False}
        if layout_template_path is not None:
            overrides["layout"]["template_path"] = layout_template_path
        if identity_overrides:
            overrides.setdefault("header", {})["identity"] = identity_overrides

        return load_config(config_path, header_config_path=header_config_path, overrides=overrides)

    def _parse_positive_int(self, raw: str, *, field_name: str) -> int:
        if not raw:
            raise ValueError(f"{field_name}不能为空。")
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{field_name}必须是整数。") from exc
        if value <= 0:
            raise ValueError(f"{field_name}必须大于 0。")
        return value

    def _selected_mode_value(self) -> str:
        return MODE_LABEL_TO_VALUE.get(self.mode_var.get(), "normal")

    def _set_mode_value(self, value: str) -> None:
        self.mode_var.set(MODE_VALUE_TO_LABEL.get(value, "标准"))

    def _normalized_optional_path(self, raw: str) -> str | None:
        value = raw.strip()
        return value or None

    def _current_output_parent(self) -> Path:
        if self.output_dir_var.get().strip():
            return Path(self.output_dir_var.get().strip()).expanduser().resolve()
        return self.project_root / "outputs"

    def _suggest_output_name(self) -> str | None:
        if not self.input_paths:
            return None
        first_name = Path(self.input_paths[0]).stem
        return f"{first_name}.pdf"

    def _on_input_selection(self, _event: tk.Event[Any] | None = None) -> None:
        self._refresh_preview()
        if self.material_mode_var.get():
            target = self._selected_input_path()
            if target is not None and target.suffix.lower() == ".pdf":
                if self.material_source_pdf_path is None or target != self.material_source_pdf_path:
                    self.material_source_pdf_path = target
                    self.material_selection_count = 0
                    self.after_idle(self._render_material_source_page)

    def _refresh_preview(self) -> None:
        target = self._selected_input_path()
        if target is None:
            self._clear_preview(EMPTY_PREVIEW_TEXT)
            return
        try:
            preview_image, info_text = self._load_preview_image(target)
        except Exception as exc:
            self._clear_preview(f"预览失败：{exc}")
            return
        self._show_preview_image(preview_image, info_text)

    def _selected_input_path(self) -> Path | None:
        selected = self.input_listbox.curselection()
        if selected:
            return Path(self.input_paths[selected[0]]).expanduser().resolve()
        if self.input_paths:
            return Path(self.input_paths[0]).expanduser().resolve()
        return None

    def _load_preview_image(self, path: Path, *, page_number: int = 1) -> tuple[Image.Image, str]:
        if path.suffix.lower() == ".pdf":
            return self._render_pdf_preview(path, page_number=page_number)

        bgr = read_image(path)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        info = f"{path.name}\n图片预览\n尺寸：{image.width} x {image.height}"
        return image, info

    def _render_pdf_preview(self, path: Path, *, page_number: int = 1) -> tuple[Image.Image, str]:
        fitz = import_pymupdf()
        with fitz.open(str(path)) as document:
            resolved_page = max(1, min(int(page_number), document.page_count))
            page = document.load_page(resolved_page - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(0.8, 0.8), alpha=False)
            raster = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
            rgb = cv2.cvtColor(raster, cv2.COLOR_GRAY2RGB) if pixmap.n == 1 else raster[:, :, :3]
            image = Image.fromarray(rgb)
            info = f"{path.name}\nPDF 预览（第 {resolved_page} 页 / 共 {document.page_count} 页）\n尺寸：{pixmap.width} x {pixmap.height}"
            return image, info

    def _show_preview_image(self, image: Image.Image, info_text: str) -> None:
        self.preview_source_image = image.copy()
        self.preview_info_var.set(info_text)
        self._render_preview_from_source()

    def _clear_preview(self, message: str) -> None:
        self.preview_source_image = None
        self.preview_photo = None
        if self.empty_preview_photo is None:
            watermark = self._load_tju_watermark_image((280, 86), opacity=0.16)
            if watermark is not None:
                self.empty_preview_photo = ImageTk.PhotoImage(watermark)
        if self.empty_preview_photo is not None:
            self.preview_label.configure(image=self.empty_preview_photo, text=message, compound="center")
        else:
            self.preview_label.configure(image="", text=message, compound="none")
        self.preview_info_var.set(message)
        self.after_idle(self._update_preview_scrollregion)

    def _render_preview_from_source(self) -> None:
        if self.preview_source_image is None:
            return
        max_w, max_h = self._compute_preview_fit_box()
        preview = self.preview_source_image.copy()
        preview.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(preview)
        self.preview_label.configure(image=self.preview_photo, text="", compound="none")
        self.after_idle(self._update_preview_scrollregion)

    def _compute_preview_fit_box(self) -> tuple[int, int]:
        viewport_w = int(self.preview_scroll_canvas.winfo_width() or 520)
        viewport_h = int(self.preview_scroll_canvas.winfo_height() or 640)
        max_w = max(360, viewport_w - 24)
        max_h = max(520, int(viewport_h * 1.1))
        return (max_w, max_h)

    def _show_result_preview_if_available(self, result: PipelineResult) -> None:
        try:
            image, info = self._render_pdf_preview(result.output_path)
            self._show_preview_image(image, f"{info}\n输出结果预览")
        except Exception:
            preview_paths = result.debug_artifacts.get("render_preview", [])
            if not preview_paths:
                return
            preview_path = preview_paths[0]
            try:
                image = Image.open(preview_path)
                self._show_preview_image(image, f"{preview_path.name}\n渲染结果预览")
            except Exception:
                return

    def _add_input_paths(self, file_paths: list[str] | tuple[str, ...], *, source: str) -> int:
        added_paths: list[str] = []
        skipped_paths: list[str] = []

        for raw_path in file_paths:
            path = Path(raw_path).expanduser().resolve()
            if not path.exists() or not path.is_file():
                skipped_paths.append(str(path))
                continue
            if path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
                skipped_paths.append(str(path))
                continue
            normalized = str(path)
            if normalized not in self.input_paths:
                self.input_paths.append(normalized)
                self.input_listbox.insert(tk.END, normalized)
                added_paths.append(normalized)

        if not self.output_name_var.get().strip() or self.output_name_var.get().strip() == "result.pdf":
            suggested = self._suggest_output_name()
            if suggested is not None:
                self.output_name_var.set(suggested)

        if added_paths:
            self.rework_attempt_index = 0
            last_index = len(self.input_paths) - 1
            self.input_listbox.selection_clear(0, tk.END)
            self.input_listbox.selection_set(last_index)
            self.input_listbox.activate(last_index)
            self._refresh_preview()
            self._save_state()
            self._log(f"已通过{source}添加 {len(added_paths)} 个输入文件。")

        if skipped_paths:
            self._log(f"已跳过 {len(skipped_paths)} 个不支持的项目。")
        return len(added_paths)

    def _setup_drop_target(self) -> None:
        if not WINDOWS_DROP_ENABLED:
            self.preview_info_var.set(EMPTY_PREVIEW_TEXT)
            return
        try:
            self.master.update_idletasks()
            self._install_windows_drop_target()
            if not self.preview_info_var.get() or self.preview_info_var.get() == EMPTY_PREVIEW_TEXT:
                self.preview_info_var.set(self.drop_status_note or EMPTY_PREVIEW_TEXT)
            self._log("已启用 Windows 拖拽导入。")
        except Exception as exc:
            self._log(f"拖拽导入不可用：{exc}")

    def _install_windows_drop_target(self) -> None:
        if not WINDOWS_DROP_ENABLED:
            return

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        user32.SetWindowLongPtrW.restype = LONG_PTR
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
        user32.CallWindowProcW.restype = LONG_PTR
        user32.CallWindowProcW.argtypes = [LONG_PTR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragFinish.argtypes = [wintypes.HANDLE]

        hwnd = self.master.winfo_id()
        self._drop_hwnd = hwnd

        def window_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_DROPFILES:
                files = self._extract_dropped_files(wparam)
                self.message_queue.put(("drop", files))
                return 0
            return user32.CallWindowProcW(self._drop_old_wndproc, hwnd, msg, wparam, lparam)

        self._drop_wndproc = WNDPROC(window_proc)
        self._drop_old_wndproc = user32.SetWindowLongPtrW(
            hwnd,
            GWL_WNDPROC,
            LONG_PTR(ctypes.cast(self._drop_wndproc, ctypes.c_void_p).value),
        )
        shell32.DragAcceptFiles(hwnd, True)

    def _extract_dropped_files(self, handle: int) -> list[str]:
        shell32 = ctypes.windll.shell32
        file_count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        files: list[str] = []
        for index in range(file_count):
            length = shell32.DragQueryFileW(handle, index, None, 0) + 1
            buffer = ctypes.create_unicode_buffer(length)
            shell32.DragQueryFileW(handle, index, buffer, length)
            files.append(buffer.value)
        shell32.DragFinish(handle)
        return files

    def _handle_dropped_files(self, file_paths: list[str]) -> None:
        if self.worker is not None and self.worker.is_alive():
            self._log("当前正在处理任务，已忽略拖入的文件。")
            return
        if file_paths:
            self._add_input_paths(file_paths, source="拖拽")

    def _apply_identity_defaults_from_config(self) -> None:
        config_path = self._normalized_optional_path(self.config_path_var.get())
        header_config_path = self._normalized_optional_path(self.header_config_var.get())
        if config_path is None and header_config_path is None:
            return
        try:
            config = load_config(config_path, header_config_path=header_config_path)
        except Exception as exc:
            self._log(f"读取配置默认页眉失败：{exc}")
            return

        if not self.student_id_var.get().strip():
            self.student_id_var.set(config.header.identity.student_id)
        if not self.class_name_var.get().strip():
            self.class_name_var.set(config.header.identity.class_name)
        if not self.student_name_var.get().strip():
            self.student_name_var.set(config.header.identity.student_name)

    def _save_state(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "input_paths": [path for path in self.input_paths if Path(path).exists()][:12],
            "output_dir": self.output_dir_var.get(),
            "output_name": self.output_name_var.get(),
            "mode": self._selected_mode_value(),
            "debug": bool(self.debug_var.get()),
            "pdf_watermark": bool(self.pdf_watermark_var.get()),
            "auto_start_on_paste": bool(self.auto_start_on_paste_var.get()),
            "strict_single_question": bool(self.strict_single_question_var.get()),
            "dpi": self.dpi_var.get(),
            "per_page": self.per_page_var.get(),
            "layout_pattern_mode": self.layout_pattern_mode_var.get(),
            "custom_layout_pattern": self.custom_layout_pattern_var.get(),
            "layout_template_path": self.layout_template_var.get(),
            "template_lock_per_page": bool(self.template_lock_per_page_var.get()),
            "config_path": self.config_path_var.get(),
            "header_config_path": self.header_config_var.get(),
            "student_id": self.student_id_var.get(),
            "class_name": self.class_name_var.get(),
            "student_name": self.student_name_var.get(),
            "manual_step": self.manual_step_var.get(),
            "manual_focus_view": bool(self.manual_focus_view),
            "manual_zoom": float(self.manual_zoom_var.get()),
            "compact_mode": bool(self.compact_mode),
            "advanced_visible": self.advanced_visible,
            "material_delete_target": self.material_delete_target_var.get(),
            "material_auto_advance": bool(self.material_auto_advance_var.get()),
            "geometry": self.master.geometry(),
        }
        self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return

        self.input_paths = [path for path in state.get("input_paths", []) if Path(path).exists()]
        self.input_listbox.delete(0, tk.END)
        for path in self.input_paths:
            self.input_listbox.insert(tk.END, path)

        legacy_output_path = str(state.get("output_path", "")).strip()
        if legacy_output_path and ("output_dir" not in state and "output_name" not in state):
            legacy_path = Path(legacy_output_path).expanduser()
            self.output_dir_var.set(str(legacy_path.parent))
            self.output_name_var.set(legacy_path.name)
        else:
            self.output_dir_var.set(state.get("output_dir", self.output_dir_var.get()))
            self.output_name_var.set(state.get("output_name", self.output_name_var.get()))
        self._set_mode_value(str(state.get("mode", self._selected_mode_value())))
        self.debug_var.set(bool(state.get("debug", self.debug_var.get())))
        self.pdf_watermark_var.set(bool(state.get("pdf_watermark", self.pdf_watermark_var.get())))
        self.auto_start_on_paste_var.set(bool(state.get("auto_start_on_paste", self.auto_start_on_paste_var.get())))
        self.strict_single_question_var.set(bool(state.get("strict_single_question", self.strict_single_question_var.get())))
        self.dpi_var.set(str(state.get("dpi", self.dpi_var.get())))
        self.per_page_var.set(str(state.get("per_page", self.per_page_var.get())))
        self.layout_pattern_mode_var.set(str(state.get("layout_pattern_mode", self.layout_pattern_mode_var.get())))
        self.custom_layout_pattern_var.set(str(state.get("custom_layout_pattern", self.custom_layout_pattern_var.get())))
        self.layout_template_var.set(state.get("layout_template_path", ""))
        self.template_lock_per_page_var.set(bool(state.get("template_lock_per_page", self.template_lock_per_page_var.get())))
        self.config_path_var.set(state.get("config_path", ""))
        self.header_config_var.set(state.get("header_config_path", ""))
        self.student_id_var.set(state.get("student_id", ""))
        self.class_name_var.set(state.get("class_name", ""))
        self.student_name_var.set(state.get("student_name", ""))
        self.manual_step_var.set(str(state.get("manual_step", self.manual_step_var.get())))
        self.manual_focus_view = bool(state.get("manual_focus_view", True))
        self.manual_view_toggle_text_var.set("查看整页" if self.manual_focus_view else "聚焦题目")
        self.manual_zoom_var.set(float(state.get("manual_zoom", self.manual_zoom_var.get())))
        self._update_manual_zoom_label()
        self._set_compact_mode(bool(state.get("compact_mode", True)))
        self._set_advanced_visible(bool(state.get("advanced_visible", False)))
        self.material_delete_target_var.set(str(state.get("material_delete_target", self.material_delete_target_var.get())))
        self.material_auto_advance_var.set(bool(state.get("material_auto_advance", self.material_auto_advance_var.get())))
        self._sync_strict_single_question_state()
        self._apply_identity_defaults_from_config()

        geometry = state.get("geometry")
        if geometry:
            try:
                normalized = self._normalize_window_geometry(str(geometry))
                self.master.geometry(normalized)
            except Exception:
                pass

        if self.input_paths:
            self.input_listbox.selection_set(0)
            self.input_listbox.activate(0)
            self._refresh_preview()

    def _on_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            should_close = messagebox.askyesno(
                "任务仍在运行",
                "当前还有任务正在处理。确定关闭窗口并停止本次任务吗？",
            )
            if not should_close:
                return
        try:
            self._save_state()
        finally:
            self._restore_drop_target()
            self.master.destroy()

    def _normalize_window_geometry(self, geometry: str) -> str:
        match = re.match(r"^\s*(\d+)x(\d+)([+-]\d+)?([+-]\d+)?\s*$", geometry)
        if not match:
            return geometry
        width = max(1120, int(match.group(1)))
        height = max(800, int(match.group(2)))
        pos_x = match.group(3) or ""
        pos_y = match.group(4) or ""
        return f"{width}x{height}{pos_x}{pos_y}"

    def _restore_drop_target(self) -> None:
        if not WINDOWS_DROP_ENABLED:
            return
        if self._drop_hwnd is None or self._drop_old_wndproc is None:
            return
        try:
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            user32.SetWindowLongPtrW.restype = LONG_PTR
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
            shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
            shell32.DragAcceptFiles(self._drop_hwnd, False)
            user32.SetWindowLongPtrW(self._drop_hwnd, GWL_WNDPROC, LONG_PTR(self._drop_old_wndproc))
        except Exception:
            return

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"{message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")


def run_gui() -> int:
    """Launch the desktop GUI."""

    root = tk.Tk()
    FormatterGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_gui())
