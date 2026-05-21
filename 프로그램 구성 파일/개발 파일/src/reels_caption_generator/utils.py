from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from typing import Iterable

from PIL import Image


INVALID_FILENAME_CHARS = r'<>:"/\|?*'


def resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base.joinpath(*parts)


def sanitize_filename(value: str, fallback: str = "media") -> str:
    cleaned = "".join("_" if ch in INVALID_FILENAME_CHARS else ch for ch in value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if len(cleaned) > 90:
        cleaned = cleaned[:90].rstrip(" .-_")
    return cleaned or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for index in range(2, 1000):
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"사용 가능한 파일명을 만들지 못했습니다: {path}")


def find_ffmpeg() -> Path:
    env_path = os.getenv("FFMPEG_BINARY")
    if env_path and Path(env_path).exists():
        return Path(env_path)
    try:
        import imageio_ffmpeg

        bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if bundled.exists():
            return bundled
    except Exception:
        pass
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    raise RuntimeError("FFmpeg을 찾을 수 없습니다. 프로그램을 다시 빌드하거나 ffmpeg를 설치해 주세요.")


def run_process(args: Iterable[str | os.PathLike[str]], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def get_media_duration(media_path: Path, ffmpeg_path: Path | None = None) -> float:
    ffmpeg = ffmpeg_path or find_ffmpeg()
    completed = run_process([ffmpeg, "-i", media_path])
    output = f"{completed.stdout}\n{completed.stderr}"
    match = re.search(r"Duration:\s*(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)", output)
    if not match:
        raise RuntimeError("영상 길이를 읽지 못했습니다.")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def format_timecode(seconds: float | int) -> str:
    total = max(0, int(round(float(seconds))))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def extract_plain_text(response: object) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)
    pieces: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                pieces.append(str(text))
    return "\n".join(pieces)


def strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:txt|text|markdown|md)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    return stripped


def image_to_data_url(path: Path, max_edge: int = 900, quality: int = 72) -> str:
    with Image.open(path) as image:
        image = image.convert("RGB")
        width, height = image.size
        largest = max(width, height)
        if largest > max_edge:
            ratio = max_edge / largest
            image = image.resize((max(1, int(width * ratio)), max(1, int(height * ratio))), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def read_text_if_exists(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        return path.read_text(encoding="cp949", errors="replace").strip()
