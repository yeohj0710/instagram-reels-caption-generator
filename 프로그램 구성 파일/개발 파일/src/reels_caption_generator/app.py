from __future__ import annotations

import os
import queue
import threading
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox
import tkinter as tk

import customtkinter as ctk

from reels_caption_generator.pipeline import (
    CAPTION_TEXT_MODEL_CHOICES,
    MEDIA_EXTENSIONS,
    TRANSCRIPTION_MODEL_CHOICES,
    CaptionPipeline,
    CaptionResult,
    UserFacingError,
)
from reels_caption_generator.settings import (
    DEFAULT_TEXT_MODEL,
    AppSettings,
    default_output_dir,
    default_training_dir,
    load_settings,
    save_settings,
)
from reels_caption_generator.utils import resource_path


PRODUCT_NAME = "릴스 캡션 생성기"
SOURCE_URL_MODE = "링크로 가져오기"
SOURCE_FILE_MODE = "PC 파일 선택"
CUSTOM_TEXT_MODEL_OPTION = "직접 입력"
MEDIA_FILE_PATTERN = " ".join(f"*{ext}" for ext in sorted(MEDIA_EXTENSIONS))


class ActivitySpinner(tk.Canvas):
    def __init__(self, master: tk.Misc, size: int = 18, color: str = "#2563eb", bg: str = "#ffffff") -> None:
        super().__init__(master, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
        self.size = size
        self.color = color
        self.angle = 90
        self.after_id: str | None = None
        self.running = False

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._tick()

    def stop(self) -> None:
        self.running = False
        if self.after_id is not None:
            try:
                self.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        self.delete("all")

    def _tick(self) -> None:
        self.delete("all")
        pad = 3
        self.create_arc(
            pad,
            pad,
            self.size - pad,
            self.size - pad,
            start=self.angle,
            extent=285,
            style="arc",
            outline=self.color,
            width=3,
        )
        self.angle = (self.angle - 18) % 360
        if self.running:
            self.after_id = self.after(33, self._tick)


class ReelsCaptionApp(ctk.CTk):
    primary_color = "#2563eb"
    primary_hover = "#1d4ed8"
    secondary_color = "#eef4ff"
    secondary_hover = "#dbeafe"
    secondary_text = "#1d4ed8"
    success_color = "#059669"
    success_hover = "#047857"
    disabled_color = "#d8dee8"
    disabled_text = "#64748b"

    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.title(PRODUCT_NAME)
        self._set_window_icon()
        self.geometry("1120x780")
        self.minsize(1000, 700)

        self.settings = load_settings()
        self.worker_thread: threading.Thread | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.latest_result: CaptionResult | None = None
        self.is_processing = False

        self.source_type = tk.StringVar(value=SOURCE_FILE_MODE)
        self.url_var = tk.StringVar()
        self.file_var = tk.StringVar()
        self.api_key_var = tk.StringVar(value=self.settings.api_key)
        self.save_api_key_var = tk.BooleanVar(value=True)
        saved_model = self.settings.text_model.strip() or DEFAULT_TEXT_MODEL
        if saved_model in CAPTION_TEXT_MODEL_CHOICES:
            self.text_model_var = tk.StringVar(value=saved_model)
            self.custom_text_model_var = tk.StringVar(value="")
        else:
            self.text_model_var = tk.StringVar(value=CUSTOM_TEXT_MODEL_OPTION)
            self.custom_text_model_var = tk.StringVar(value=saved_model)
        self.transcription_model_var = tk.StringVar(value=self.settings.transcription_model)
        self.output_dir_var = tk.StringVar(value=self.settings.output_dir or str(default_output_dir()))
        self.save_next_to_source_var = tk.BooleanVar(value=self.settings.save_next_to_source)
        self.use_cookies_var = tk.BooleanVar(value=self.settings.use_browser_cookies)
        self.cookie_browser_var = tk.StringVar(value=self.settings.cookie_browser)

        self._configure_typography()
        self._build_ui()
        self._ensure_user_folders()
        self._refresh_source_mode()
        self.after(120, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_typography(self) -> None:
        available = set(tkfont.families(self))
        for candidate in ("Pretendard", "맑은 고딕", "Malgun Gothic", "Segoe UI"):
            if candidate in available:
                self.font_family = candidate
                break
        else:
            self.font_family = "Segoe UI"
        self.option_add("*Font", f"{{{self.font_family}}} 11")
        self.font_title = ctk.CTkFont(family=self.font_family, size=31, weight="bold")
        self.font_subtitle = ctk.CTkFont(family=self.font_family, size=15)
        self.font_credit = ctk.CTkFont(family=self.font_family, size=12, weight="bold")
        self.font_card_title = ctk.CTkFont(family=self.font_family, size=19, weight="bold")
        self.font_body = ctk.CTkFont(family=self.font_family, size=14)
        self.font_label = ctk.CTkFont(family=self.font_family, size=13)
        self.font_button = ctk.CTkFont(family=self.font_family, size=14, weight="bold")
        self.font_input = ctk.CTkFont(family=self.font_family, size=14)
        self.font_log = ctk.CTkFont(family=self.font_family, size=13)

    def _set_window_icon(self) -> None:
        icon_path = resource_path("assets", "caption-generator.ico")
        if not icon_path.exists():
            return
        try:
            self.iconbitmap(str(icon_path))
        except tk.TclError:
            pass

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="#f8fafc", corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)

        ctk.CTkLabel(header, text=PRODUCT_NAME, font=self.font_title, text_color="#111827").grid(
            row=0, column=0, padx=32, pady=(22, 4), sticky="w"
        )
        credit = ctk.CTkLabel(
            header,
            text="developed by yeohj0710",
            font=self.font_credit,
            text_color="#2563eb",
            fg_color="#eaf2ff",
            corner_radius=6,
            padx=10,
            pady=3,
        )
        credit.grid(row=1, column=0, padx=32, pady=(0, 7), sticky="w")
        credit.configure(cursor="hand2")
        credit.bind("<Button-1>", lambda _event: webbrowser.open("https://github.com/yeohj0710"))
        ctk.CTkLabel(
            header,
            text="PC 영상 또는 릴스/유튜브 링크를 분석해서 기존 학습용 데이터 톤에 맞는 캡션.txt를 생성합니다.",
            font=self.font_subtitle,
            text_color="#475569",
        ).grid(row=2, column=0, padx=32, pady=(0, 22), sticky="w")
        ctk.CTkButton(
            header,
            text="사용설명서 열기",
            width=172,
            height=40,
            corner_radius=8,
            font=self.font_button,
            fg_color=self.secondary_color,
            hover_color=self.secondary_hover,
            text_color=self.secondary_text,
            command=self._open_user_guide,
        ).grid(row=0, column=1, rowspan=3, padx=(12, 32), pady=26, sticky="e")

        body = ctk.CTkFrame(self, fg_color="#edf1f6", corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(
            body,
            fg_color="#edf1f6",
            scrollbar_button_color="#94a3b8",
            scrollbar_button_hover_color="#64748b",
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(24, 12), pady=24)
        left.grid_columnconfigure(0, weight=1)

        right = ctk.CTkFrame(body, fg_color="#ffffff", corner_radius=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(12, 24), pady=24)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(4, weight=1)

        self._source_card(left).grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self._openai_card(left).grid(row=1, column=0, sticky="ew", pady=(0, 14))
        self._output_card(left).grid(row=2, column=0, sticky="ew", pady=(0, 14))
        self._status_panel(right)

    def _card(self, parent: ctk.CTkBaseClass, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color="#ffffff", corner_radius=10)
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=title, font=self.font_card_title, text_color="#111827").grid(
            row=0, column=0, padx=22, pady=(20, 12), sticky="w"
        )
        return card

    def _helper_label(self, parent: ctk.CTkBaseClass, text: str, row: int) -> None:
        ctk.CTkLabel(
            parent,
            text=text,
            font=self.font_label,
            text_color="#64748b",
            justify="left",
            anchor="w",
            wraplength=560,
        ).grid(row=row, column=0, padx=22, pady=(0, 12), sticky="ew")

    def _source_card(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        card = self._card(parent, "영상 가져오기")
        self._helper_label(card, "릴스/유튜브 링크를 붙여넣거나, PC에 저장된 영상 파일을 선택합니다.", 1)
        ctk.CTkLabel(card, text="가져올 방식", font=self.font_label, text_color="#334155").grid(
            row=2, column=0, padx=22, pady=(0, 8), sticky="w"
        )
        self.source_mode_switch = ctk.CTkSegmentedButton(
            card,
            values=[SOURCE_FILE_MODE, SOURCE_URL_MODE],
            variable=self.source_type,
            command=lambda _value: self._refresh_source_mode(),
            height=40,
            corner_radius=8,
            font=self.font_button,
            fg_color="#e2e8f0",
            selected_color="#93c5fd",
            selected_hover_color="#60a5fa",
            unselected_color="#f8fafc",
            unselected_hover_color="#edf2f7",
            text_color="#1f2937",
        )
        self.source_mode_switch.grid(row=3, column=0, padx=22, pady=(0, 16), sticky="ew")

        self.file_panel = ctk.CTkFrame(card, fg_color="#f6f8fb", corner_radius=8)
        self.file_panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.file_panel, text="내 컴퓨터 영상/오디오 파일", font=self.font_label, text_color="#334155").grid(
            row=0, column=0, padx=16, pady=(16, 7), sticky="w"
        )
        file_row = ctk.CTkFrame(self.file_panel, fg_color="transparent")
        file_row.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="ew")
        file_row.grid_columnconfigure(0, weight=1)
        self.file_entry = ctk.CTkEntry(
            file_row,
            textvariable=self.file_var,
            placeholder_text="mp4, mov, m4a, mp3 등",
            height=40,
            font=self.font_input,
            corner_radius=7,
        )
        self.file_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.file_button = ctk.CTkButton(
            file_row,
            text="파일 선택",
            width=106,
            height=40,
            corner_radius=7,
            font=self.font_button,
            fg_color=self.primary_color,
            hover_color=self.primary_hover,
            command=self._choose_media_file,
        )
        self.file_button.grid(row=0, column=1, padx=(0, 8))
        self.open_source_folder_button = ctk.CTkButton(
            file_row,
            text="열기",
            width=78,
            height=40,
            corner_radius=7,
            font=self.font_button,
            fg_color=self.secondary_color,
            hover_color=self.secondary_hover,
            text_color=self.secondary_text,
            command=self._open_source_folder,
        )
        self.open_source_folder_button.grid(row=0, column=2)

        self.url_panel = ctk.CTkFrame(card, fg_color="#f6f8fb", corner_radius=8)
        self.url_panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.url_panel, text="릴스 또는 유튜브 링크", font=self.font_label, text_color="#334155").grid(
            row=0, column=0, padx=16, pady=(16, 7), sticky="w"
        )
        self.url_entry = ctk.CTkEntry(
            self.url_panel,
            textvariable=self.url_var,
            placeholder_text="https://www.instagram.com/reel/... 또는 https://www.youtube.com/watch?v=...",
            height=40,
            font=self.font_input,
            corner_radius=7,
        )
        self.url_entry.grid(row=1, column=0, padx=16, pady=(0, 14), sticky="ew")
        cookie_row = ctk.CTkFrame(self.url_panel, fg_color="#ffffff", corner_radius=8)
        cookie_row.grid(row=2, column=0, padx=16, pady=(0, 16), sticky="ew")
        cookie_row.grid_columnconfigure(1, weight=1)
        self.use_cookies_checkbox = ctk.CTkCheckBox(
            cookie_row,
            text="브라우저 쿠키 사용",
            variable=self.use_cookies_var,
            font=self.font_body,
            checkbox_width=24,
            checkbox_height=24,
        )
        self.use_cookies_checkbox.grid(row=0, column=0, padx=14, pady=12, sticky="w")
        self.cookie_browser_combo = ctk.CTkComboBox(
            cookie_row,
            variable=self.cookie_browser_var,
            values=["chrome", "edge", "firefox", "brave", "opera", "whale"],
            width=150,
            height=36,
            font=self.font_input,
            dropdown_font=self.font_input,
            corner_radius=7,
        )
        self.cookie_browser_combo.grid(row=0, column=1, padx=(0, 14), pady=12, sticky="e")
        return card

    def _openai_card(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        card = self._card(parent, "OpenAI 설정")
        self._helper_label(card, "스크립트 전사와 화면 분석, 최종 캡션 생성에 사용합니다.", 1)
        ctk.CTkLabel(card, text="API 키", font=self.font_label, text_color="#334155").grid(
            row=2, column=0, padx=22, pady=(0, 8), sticky="w"
        )
        key_row = ctk.CTkFrame(card, fg_color="transparent")
        key_row.grid(row=3, column=0, padx=22, pady=(0, 12), sticky="ew")
        key_row.grid_columnconfigure(0, weight=1)
        self.api_entry = ctk.CTkEntry(
            key_row,
            textvariable=self.api_key_var,
            placeholder_text="sk-...",
            height=40,
            font=self.font_input,
            corner_radius=7,
        )
        self.api_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.save_key_checkbox = ctk.CTkCheckBox(
            key_row,
            text="저장",
            variable=self.save_api_key_var,
            font=self.font_body,
            checkbox_width=23,
            checkbox_height=23,
        )
        self.save_key_checkbox.grid(row=0, column=1)

        model_row = ctk.CTkFrame(card, fg_color="transparent")
        model_row.grid(row=4, column=0, padx=22, pady=(0, 12), sticky="ew")
        model_row.grid_columnconfigure(0, weight=1)
        model_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(model_row, text="캡션/이미지 모델", font=self.font_label, text_color="#334155").grid(
            row=0, column=0, padx=(0, 8), pady=(0, 8), sticky="w"
        )
        ctk.CTkLabel(model_row, text="전사 모델", font=self.font_label, text_color="#334155").grid(
            row=0, column=1, padx=(8, 0), pady=(0, 8), sticky="w"
        )
        self.text_model_combo = ctk.CTkComboBox(
            model_row,
            variable=self.text_model_var,
            values=CAPTION_TEXT_MODEL_CHOICES,
            height=38,
            font=self.font_input,
            dropdown_font=self.font_input,
            command=lambda _value: self._refresh_custom_model(),
        )
        self.text_model_combo.grid(row=1, column=0, padx=(0, 8), sticky="ew")
        self.transcription_model_combo = ctk.CTkComboBox(
            model_row,
            variable=self.transcription_model_var,
            values=TRANSCRIPTION_MODEL_CHOICES,
            height=38,
            font=self.font_input,
            dropdown_font=self.font_input,
        )
        self.transcription_model_combo.grid(row=1, column=1, padx=(8, 0), sticky="ew")

        self.custom_model_entry = ctk.CTkEntry(
            card,
            textvariable=self.custom_text_model_var,
            placeholder_text="직접 입력 모델명",
            height=38,
            font=self.font_input,
            corner_radius=7,
        )
        self.custom_model_entry.grid(row=5, column=0, padx=22, pady=(0, 14), sticky="ew")

        self._refresh_custom_model()
        return card

    def _output_card(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        card = self._card(parent, "저장 위치")
        self._helper_label(card, "PC 파일은 기본적으로 영상이 있는 폴더에 캡션.txt를 만듭니다. 링크는 생성된 캡션 폴더에 새 폴더를 만듭니다.", 1)
        self.save_next_checkbox = ctk.CTkCheckBox(
            card,
            text="PC 파일은 원본 영상 폴더에 캡션.txt 저장",
            variable=self.save_next_to_source_var,
            font=self.font_body,
            checkbox_width=24,
            checkbox_height=24,
        )
        self.save_next_checkbox.grid(row=2, column=0, padx=22, pady=(0, 12), sticky="w")
        ctk.CTkLabel(card, text="링크/별도 저장 출력 폴더", font=self.font_label, text_color="#334155").grid(
            row=3, column=0, padx=22, pady=(0, 8), sticky="w"
        )
        out_row = ctk.CTkFrame(card, fg_color="transparent")
        out_row.grid(row=4, column=0, padx=22, pady=(0, 18), sticky="ew")
        out_row.grid_columnconfigure(0, weight=1)
        self.output_entry = ctk.CTkEntry(out_row, textvariable=self.output_dir_var, height=40, font=self.font_input)
        self.output_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ctk.CTkButton(
            out_row,
            text="폴더 선택",
            width=106,
            height=40,
            corner_radius=7,
            font=self.font_button,
            fg_color=self.secondary_color,
            hover_color=self.secondary_hover,
            text_color=self.secondary_text,
            command=self._choose_output_dir,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            out_row,
            text="열기",
            width=78,
            height=40,
            corner_radius=7,
            font=self.font_button,
            fg_color=self.secondary_color,
            hover_color=self.secondary_hover,
            text_color=self.secondary_text,
            command=self._open_selected_output_dir,
        ).grid(row=0, column=2)
        return card

    def _status_panel(self, parent: ctk.CTkFrame) -> None:
        ctk.CTkLabel(parent, text="작업 상태", font=self.font_card_title, text_color="#111827").grid(
            row=0, column=0, padx=22, pady=(22, 8), sticky="w"
        )
        self.status_label = ctk.CTkLabel(parent, text="대기 중", font=self.font_body, text_color="#475569")
        self.status_label.grid(row=1, column=0, padx=22, pady=(0, 8), sticky="w")
        progress_row = ctk.CTkFrame(parent, fg_color="transparent")
        progress_row.grid(row=2, column=0, padx=22, pady=(0, 14), sticky="ew")
        progress_row.grid_columnconfigure(1, weight=1)
        self.spinner = ActivitySpinner(progress_row, bg="#ffffff")
        self.spinner.grid(row=0, column=0, padx=(0, 10))
        self.progress_bar = ctk.CTkProgressBar(progress_row, height=14, corner_radius=7)
        self.progress_bar.grid(row=0, column=1, sticky="ew")
        self.progress_bar.set(0)

        self.start_button = ctk.CTkButton(
            parent,
            text="캡션 생성 시작",
            height=46,
            corner_radius=8,
            font=self.font_button,
            fg_color=self.primary_color,
            hover_color=self.primary_hover,
            command=self._start_generation,
        )
        self.start_button.grid(row=3, column=0, padx=22, pady=(0, 16), sticky="ew")

        self.log_box = ctk.CTkTextbox(parent, height=260, font=self.font_log, corner_radius=8, fg_color="#f8fafc")
        self.log_box.grid(row=4, column=0, padx=22, pady=(0, 16), sticky="nsew")
        self.log_box.insert("1.0", "여기에 진행 로그가 표시됩니다.\n")
        self.log_box.configure(state="disabled")

        button_row = ctk.CTkFrame(parent, fg_color="transparent")
        button_row.grid(row=5, column=0, padx=22, pady=(0, 22), sticky="ew")
        button_row.grid_columnconfigure(0, weight=1)
        button_row.grid_columnconfigure(1, weight=1)
        self.open_caption_button = ctk.CTkButton(
            button_row,
            text="캡션 열기",
            height=40,
            corner_radius=8,
            font=self.font_button,
            fg_color=self.secondary_color,
            hover_color=self.secondary_hover,
            text_color=self.secondary_text,
            command=self._open_caption,
            state="disabled",
        )
        self.open_caption_button.grid(row=0, column=0, padx=(0, 8), sticky="ew")
        self.open_folder_button = ctk.CTkButton(
            button_row,
            text="결과 폴더 열기",
            height=40,
            corner_radius=8,
            font=self.font_button,
            fg_color=self.secondary_color,
            hover_color=self.secondary_hover,
            text_color=self.secondary_text,
            command=self._open_result_folder,
            state="disabled",
        )
        self.open_folder_button.grid(row=0, column=1, padx=(8, 0), sticky="ew")

    def _refresh_source_mode(self) -> None:
        if self.source_type.get() == SOURCE_URL_MODE:
            self.file_panel.grid_forget()
            self.url_panel.grid(row=4, column=0, padx=22, pady=(0, 20), sticky="ew")
        else:
            self.url_panel.grid_forget()
            self.file_panel.grid(row=4, column=0, padx=22, pady=(0, 20), sticky="ew")

    def _refresh_custom_model(self) -> None:
        if self.text_model_var.get() == CUSTOM_TEXT_MODEL_OPTION:
            self.custom_model_entry.configure(state="normal")
        else:
            self.custom_model_entry.configure(state="disabled")

    def _choose_media_file(self) -> None:
        filetypes = [("미디어 파일", MEDIA_FILE_PATTERN), ("모든 파일", "*.*")]
        path = filedialog.askopenfilename(title="영상 또는 오디오 파일 선택", filetypes=filetypes)
        if path:
            self.file_var.set(path)

    def _choose_output_dir(self) -> None:
        initial = self.output_dir_var.get().strip() or str(default_output_dir())
        path = filedialog.askdirectory(title="출력 폴더 선택", initialdir=initial if Path(initial).exists() else None)
        if path:
            self.output_dir_var.set(path)

    def _open_source_folder(self) -> None:
        path_text = self.file_var.get().strip()
        if not path_text:
            messagebox.showwarning("폴더 열기", "먼저 PC 영상 또는 오디오 파일을 선택해 주세요.")
            return

        path = Path(path_text).expanduser()
        folder = path if path.is_dir() else path.parent
        if not folder.exists():
            messagebox.showwarning("폴더 열기", f"폴더를 찾지 못했습니다.\n\n{folder}")
            return
        self._open_folder(folder, "폴더 열기 실패")

    def _open_selected_output_dir(self) -> None:
        folder = Path(self.output_dir_var.get().strip() or str(default_output_dir())).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("폴더 열기 실패", str(exc))
            return
        self._open_folder(folder, "폴더 열기 실패")

    def _open_user_guide(self) -> None:
        guide = Path.cwd() / "사용설명서.html"
        if not guide.exists():
            guide = Path.cwd().parent.parent / "사용설명서.html"
        if guide.exists():
            webbrowser.open(guide.resolve().as_uri())
        else:
            messagebox.showinfo("사용설명서", "사용설명서.html 파일을 찾지 못했습니다.")

    def _start_generation(self) -> None:
        if self.is_processing:
            return
        try:
            settings = self._collect_settings()
            source = self.url_var.get().strip() if self.source_type.get() == SOURCE_URL_MODE else self.file_var.get().strip()
            if not source:
                raise UserFacingError("영상 링크를 붙여넣거나 PC 영상 파일을 선택해 주세요.")
            save_settings(settings)
        except UserFacingError as exc:
            messagebox.showwarning("확인 필요", str(exc))
            return
        except Exception as exc:
            messagebox.showerror("설정 오류", str(exc))
            return

        self.latest_result = None
        self.open_caption_button.configure(state="disabled")
        self.open_folder_button.configure(state="disabled")
        self.is_processing = True
        self.start_button.configure(state="disabled", text="생성 중...")
        self.spinner.start()
        self.progress_bar.set(0)
        self._clear_log()
        self._append_log("작업을 시작합니다.")

        self.worker_thread = threading.Thread(target=self._run_worker, args=(settings, source), daemon=True)
        self.worker_thread.start()

    def _collect_settings(self) -> AppSettings:
        text_model = self.text_model_var.get().strip()
        if text_model == CUSTOM_TEXT_MODEL_OPTION:
            text_model = self.custom_text_model_var.get().strip()
        if not text_model:
            raise UserFacingError("캡션/이미지 모델명을 선택하거나 입력해 주세요.")
        defaults = AppSettings()
        settings = AppSettings(
            api_key=self.api_key_var.get().strip(),
            save_api_key=bool(self.save_api_key_var.get()),
            text_model=text_model,
            transcription_model=self.transcription_model_var.get().strip(),
            output_dir=self.output_dir_var.get().strip() or str(default_output_dir()),
            output_dir_custom=True,
            training_dir=str(default_training_dir()),
            training_dir_custom=False,
            save_next_to_source=bool(self.save_next_to_source_var.get()),
            use_browser_cookies=bool(self.use_cookies_var.get()),
            cookie_browser=self.cookie_browser_var.get().strip() or "chrome",
            max_frames=defaults.max_frames,
            frame_interval_seconds=defaults.frame_interval_seconds,
            include_research_reference=True,
            creator_note=defaults.creator_note,
        )
        if not settings.api_key:
            raise UserFacingError("OpenAI API 키를 입력해 주세요.")
        return settings

    def _run_worker(self, settings: AppSettings, source: str) -> None:
        try:
            pipeline = CaptionPipeline(settings, progress=lambda m, p, d: self.events.put(("progress", (m, p, d))))
            result = pipeline.run(source)
            self.events.put(("done", result))
        except UserFacingError as exc:
            self.events.put(("error", str(exc)))
        except Exception:
            self.events.put(("error", traceback.format_exc()))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    message, percent, detail = payload  # type: ignore[misc]
                    self.status_label.configure(text=str(message))
                    self.progress_bar.set(float(percent))
                    if detail:
                        self._append_log(str(detail))
                elif kind == "done":
                    self._finish_success(payload)  # type: ignore[arg-type]
                elif kind == "error":
                    self._finish_error(str(payload))
        except queue.Empty:
            pass
        self.after(120, self._drain_events)

    def _finish_success(self, result: CaptionResult) -> None:
        self.latest_result = result
        self.is_processing = False
        self.spinner.stop()
        self.status_label.configure(text="완료")
        self.progress_bar.set(1)
        self.start_button.configure(state="normal", text="캡션 생성 시작")
        self.open_caption_button.configure(state="normal")
        self.open_folder_button.configure(state="normal")
        self._append_log(f"완료: {result.caption_path}")
        messagebox.showinfo("완료", f"캡션을 저장했습니다.\n\n{result.caption_path}")

    def _finish_error(self, message: str) -> None:
        self.is_processing = False
        self.spinner.stop()
        self.status_label.configure(text="오류")
        self.start_button.configure(state="normal", text="캡션 생성 시작")
        self._append_log(message)
        messagebox.showerror("오류", message)

    def _append_log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text.rstrip() + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _open_caption(self) -> None:
        if self.latest_result and self.latest_result.caption_path.exists():
            os.startfile(self.latest_result.caption_path)  # type: ignore[attr-defined]

    def _open_result_folder(self) -> None:
        if self.latest_result and self.latest_result.output_dir.exists():
            self._open_folder(self.latest_result.output_dir, "결과 폴더 열기 실패")

    @staticmethod
    def _open_folder(path: Path, error_title: str) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                webbrowser.open(path.resolve().as_uri())
        except OSError as exc:
            messagebox.showerror(error_title, str(exc))

    def _ensure_user_folders(self) -> None:
        for folder in (Path(self.output_dir_var.get()), default_training_dir()):
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    def _on_close(self) -> None:
        try:
            save_settings(self._collect_settings())
        except Exception:
            pass
        self.destroy()
