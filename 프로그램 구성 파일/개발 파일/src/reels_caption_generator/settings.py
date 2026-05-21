from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


APP_NAME = "ReelsCaptionGenerator"
DEFAULT_OUTPUT_FOLDER_NAME = "생성된 캡션"
DEFAULT_TRAINING_FOLDER_NAME = "학습용 데이터"
DEFAULT_TEXT_MODEL = "gpt-5-mini"
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"


@dataclass
class AppSettings:
    api_key: str = ""
    save_api_key: bool = True
    text_model: str = DEFAULT_TEXT_MODEL
    transcription_model: str = DEFAULT_TRANSCRIPTION_MODEL
    output_dir: str = ""
    output_dir_custom: bool = False
    training_dir: str = ""
    training_dir_custom: bool = False
    save_next_to_source: bool = True
    use_browser_cookies: bool = True
    cookie_browser: str = "chrome"
    max_frames: int = 18
    frame_interval_seconds: int = 1
    include_research_reference: bool = True
    creator_note: str = "남자 약사: oyakstory. 여자 약사: jessi_yaksa. 화면 속 인물과 계정을 헷갈리지 말 것."


def app_data_dir() -> Path:
    base = os.getenv("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def app_root_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    cwd = Path.cwd()
    if cwd.name == "개발 파일" and cwd.parent.name == "프로그램 구성 파일":
        return cwd.parent.parent
    return cwd


def default_output_dir() -> Path:
    return app_root_dir() / DEFAULT_OUTPUT_FOLDER_NAME


def default_training_dir() -> Path:
    return app_root_dir() / DEFAULT_TRAINING_FOLDER_NAME


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def _is_current_default_dir(path_text: str, default_path: Path) -> bool:
    if not path_text.strip():
        return True
    try:
        path = Path(path_text).expanduser().resolve()
        resolved_default = default_path.expanduser().resolve()
    except (OSError, ValueError):
        return False
    return os.path.normcase(str(path)) == os.path.normcase(str(resolved_default))


def _coerce_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def load_settings() -> AppSettings:
    path = settings_path()
    data: dict[str, object] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}

    settings = AppSettings()
    for key, value in data.items():
        if hasattr(settings, key):
            setattr(settings, key, value)

    if not settings.output_dir:
        settings.output_dir = str(default_output_dir())
        settings.output_dir_custom = False
    elif not settings.output_dir_custom and Path(str(settings.output_dir)).name == DEFAULT_OUTPUT_FOLDER_NAME:
        settings.output_dir = str(default_output_dir())

    if not settings.training_dir:
        settings.training_dir = str(default_training_dir())
        settings.training_dir_custom = False
    elif not settings.training_dir_custom and Path(str(settings.training_dir)).name == DEFAULT_TRAINING_FOLDER_NAME:
        settings.training_dir = str(default_training_dir())

    settings.max_frames = _coerce_int(settings.max_frames, 18, 4, 32)
    settings.frame_interval_seconds = _coerce_int(settings.frame_interval_seconds, 1, 1, 10)
    settings.save_next_to_source = bool(settings.save_next_to_source)
    settings.use_browser_cookies = bool(settings.use_browser_cookies)
    settings.include_research_reference = bool(settings.include_research_reference)
    if not settings.save_api_key:
        settings.api_key = ""
    return settings


def save_settings(settings: AppSettings) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(settings)
    if not settings.save_api_key:
        payload["api_key"] = ""
    if _is_current_default_dir(str(settings.output_dir), default_output_dir()):
        payload["output_dir"] = ""
        payload["output_dir_custom"] = False
    if _is_current_default_dir(str(settings.training_dir), default_training_dir()):
        payload["training_dir"] = ""
        payload["training_dir_custom"] = False
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
