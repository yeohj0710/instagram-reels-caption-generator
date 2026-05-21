from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from openai import OpenAI

from reels_caption_generator.costing import (
    ResponseCostEstimate,
    cost_from_usage,
    estimate_response_cost,
    estimate_transcription_cost,
    format_usd,
)
from reels_caption_generator.settings import AppSettings
from reels_caption_generator.utils import (
    extract_plain_text,
    find_ffmpeg,
    format_timecode,
    get_media_duration,
    image_to_data_url,
    read_text_if_exists,
    run_process,
    sanitize_filename,
    strip_code_fence,
    unique_path,
)


ProgressCallback = Callable[[str, float, str], None]
CAPTION_FILE_NAME = "캡션.txt"
SCRIPT_FILE_NAME = "스크립트.txt"
SCREENSHOT_DIR_NAME = "스크린샷 추출본"
SUPPORT_DIR_NAME = "기타 파일"
SUPPORTED_URL_HOSTS = ("youtube.com", "youtu.be", "instagram.com")
AUDIO_EXTENSIONS = {
    ".aac",
    ".aif",
    ".aiff",
    ".amr",
    ".caf",
    ".flac",
    ".m4a",
    ".m4b",
    ".mp3",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}
VIDEO_EXTENSIONS = {
    ".3g2",
    ".3gp",
    ".avi",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".mts",
    ".ogv",
    ".ts",
    ".webm",
    ".wmv",
}
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
CAPTION_TEXT_MODEL_CHOICES = ["gpt-5-mini", "gpt-5-nano", "gpt-4.1-mini", "gpt-4o-mini", "직접 입력"]
TRANSCRIPTION_MODEL_CHOICES = ["gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"]
MAX_ESTIMATED_CAPTION_USD = 0.025
TARGET_ESTIMATED_CAPTION_USD = 0.018
MIN_COST_OPTIMIZED_FRAMES = 4
IMAGE_DETAIL = "high"
IMAGE_MAX_EDGE = 900
IMAGE_JPEG_QUALITY = 70


class UserFacingError(RuntimeError):
    """Error message that can be shown directly in the UI."""


@dataclass
class TrainingExample:
    title: str
    caption: str


@dataclass
class FrameSample:
    path: Path
    seconds: int


@dataclass
class CaptionResult:
    output_dir: Path
    caption_path: Path
    media_path: Path
    title: str
    transcript_path: Path | None
    screenshot_dir: Path | None


class CaptionPipeline:
    def __init__(self, settings: AppSettings, progress: ProgressCallback | None = None) -> None:
        self.settings = settings
        self.progress = progress or (lambda _message, _percent, _detail: None)
        self.ffmpeg = find_ffmpeg()
        self.estimated_transcription_cost_usd = 0.0
        api_key = settings.api_key.strip()
        if not api_key:
            raise UserFacingError("OpenAI API 키를 입력해 주세요.")
        self.client = OpenAI(api_key=api_key)

    def run(self, source: str) -> CaptionResult:
        source = source.strip().strip('"')
        if not source:
            raise UserFacingError("영상 링크를 붙여넣거나 PC 영상 파일을 선택해 주세요.")

        self.progress("준비 중", 0.03, "입력한 영상 정보를 확인합니다.")
        started = datetime.now().strftime("%y%m%d%H%M%S")
        temp_root = Path(tempfile.mkdtemp(prefix="caption_generator_"))
        try:
            if self.is_url(source):
                if not self.is_supported_url(source):
                    raise UserFacingError("YouTube 영상/Shorts 또는 Instagram 릴스/게시물 링크만 사용할 수 있습니다.")
                media_path, title, output_dir = self._prepare_url_source(source, started, temp_root)
                source_dir = output_dir
                transcript_hint = ""
            else:
                media_path, title, source_dir, output_dir = self._prepare_local_source(source, started)
                transcript_hint = read_text_if_exists(source_dir / SCRIPT_FILE_NAME)

            support_dir = output_dir / SUPPORT_DIR_NAME
            support_dir.mkdir(parents=True, exist_ok=True)

            screenshots = self._prepare_screenshots(media_path, source_dir, output_dir)
            transcript_path: Path | None = None
            transcript = transcript_hint.strip()
            if transcript:
                transcript_path = output_dir / SCRIPT_FILE_NAME
                if not transcript_path.exists():
                    transcript_path.write_text(transcript + "\n", encoding="utf-8")
                self.progress("스크립트 확인", 0.40, "기존 스크립트.txt를 사용합니다.")
            elif media_path.suffix.lower() in MEDIA_EXTENSIONS:
                transcript, transcript_path = self._transcribe_media(media_path, output_dir)

            examples = self._load_training_examples(Path(self.settings.training_dir), exclude_dir=output_dir)
            caption = self._generate_caption(title, transcript, screenshots, examples)
            caption_path = output_dir / CAPTION_FILE_NAME
            caption_path.write_text(caption.strip() + "\n", encoding="utf-8")

            self.progress("완료", 1.0, f"캡션 저장 완료: {caption_path}")
            return CaptionResult(
                output_dir=output_dir.resolve(),
                caption_path=caption_path.resolve(),
                media_path=media_path.resolve(),
                title=title,
                transcript_path=transcript_path.resolve() if transcript_path else None,
                screenshot_dir=(output_dir / SCREENSHOT_DIR_NAME).resolve()
                if (output_dir / SCREENSHOT_DIR_NAME).exists()
                else None,
            )
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    @staticmethod
    def is_url(source: str) -> bool:
        parsed = urlparse(source.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def is_supported_url(source: str) -> bool:
        host = urlparse(source.strip()).netloc.lower()
        return any(allowed in host for allowed in SUPPORTED_URL_HOSTS)

    @staticmethod
    def is_supported_local_media(source: str) -> bool:
        return Path(source).suffix.lower() in MEDIA_EXTENSIONS

    def _prepare_url_source(self, url: str, started: str, temp_root: Path) -> tuple[Path, str, Path]:
        output_root = Path(self.settings.output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        download_dir = temp_root / "download"
        download_dir.mkdir(parents=True, exist_ok=True)

        self.progress("영상 다운로드 중", 0.08, "링크에서 영상을 가져옵니다.")
        media_path, title = self._download_video(url, started, download_dir)
        output_dir = unique_path(output_root / f"{started} {sanitize_filename(title)}")
        output_dir.mkdir(parents=True, exist_ok=True)
        final_media = output_dir / f"{sanitize_filename(title)}{media_path.suffix.lower()}"
        if final_media.exists():
            final_media = unique_path(final_media)
        shutil.move(str(media_path), str(final_media))
        return final_media.resolve(), title, output_dir.resolve()

    def _prepare_local_source(self, source: str, started: str) -> tuple[Path, str, Path, Path]:
        source_path = Path(source).expanduser().resolve()
        if not source_path.exists() or not source_path.is_file():
            raise UserFacingError(f"선택한 미디어 파일을 찾을 수 없습니다.\n\n{source_path}")
        if source_path.suffix.lower() not in MEDIA_EXTENSIONS:
            raise UserFacingError("지원하는 영상 또는 오디오 파일을 선택해 주세요.")

        title = source_path.stem
        source_dir = source_path.parent.resolve()
        if self.settings.save_next_to_source:
            output_dir = source_dir
        else:
            output_root = Path(self.settings.output_dir).expanduser().resolve()
            output_root.mkdir(parents=True, exist_ok=True)
            output_dir = unique_path(output_root / f"{started} {sanitize_filename(title)}")
            output_dir.mkdir(parents=True, exist_ok=True)
            copied = output_dir / source_path.name
            if not copied.exists():
                shutil.copy2(source_path, copied)
            source_path = copied.resolve()
        return source_path.resolve(), title, source_dir, output_dir.resolve()

    def _download_video(self, url: str, started: str, output_root: Path) -> tuple[Path, str]:
        try:
            import yt_dlp
        except ImportError as exc:
            raise UserFacingError("yt-dlp 패키지가 설치되어 있지 않습니다. requirements.txt 설치가 필요합니다.") from exc

        ydl_opts: dict[str, object] = {
            "outtmpl": str(output_root / f"__download_{started}_%(title).90s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "windowsfilenames": True,
            "ffmpeg_location": str(self.ffmpeg),
            "format": "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/best",
            "merge_output_format": "mp4",
            "retries": 3,
            "fragment_retries": 3,
            "concurrent_fragment_downloads": 4,
            "progress_hooks": [self._download_progress_hook],
        }
        use_browser_cookies = self.settings.use_browser_cookies
        if use_browser_cookies:
            ydl_opts["cookiesfrombrowser"] = (self.settings.cookie_browser or "chrome",)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as exc:
            if use_browser_cookies and self._is_browser_cookie_error(exc):
                browser = self.settings.cookie_browser or "chrome"
                self.progress(
                    "쿠키 없이 재시도",
                    0.09,
                    f"{browser} 쿠키를 읽지 못해 쿠키 없이 다시 다운로드합니다.",
                )
                ydl_opts.pop("cookiesfrombrowser", None)
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                except Exception as retry_exc:
                    raise UserFacingError(
                        "영상 다운로드에 실패했습니다.\n\n"
                        "브라우저 쿠키를 읽지 못했고, 쿠키 없이 재시도해도 실패했습니다.\n"
                        f"{self._brief_error(retry_exc)}"
                    ) from retry_exc
            else:
                raise UserFacingError(f"영상 다운로드에 실패했습니다.\n\n{self._brief_error(exc)}") from exc
        if not isinstance(info, dict):
            raise UserFacingError("영상 정보를 가져오지 못했습니다.")

        title = self._best_title(info)
        downloaded = self._find_downloaded_file(output_root, started)
        if downloaded is None:
            raise UserFacingError("다운로드된 영상 파일을 찾지 못했습니다.")
        return downloaded.resolve(), title

    @staticmethod
    def _is_browser_cookie_error(exc: Exception) -> bool:
        message = str(exc).lower()
        cookie_markers = ("cookie", "cookiesfrombrowser")
        browser_markers = ("browser", "database", "could not copy", "failed to copy")
        return any(marker in message for marker in cookie_markers) and any(
            marker in message for marker in browser_markers
        )

    def _download_progress_hook(self, payload: dict[str, object]) -> None:
        status = str(payload.get("status") or "")
        if status == "downloading":
            total = _number(payload.get("total_bytes")) or _number(payload.get("total_bytes_estimate"))
            downloaded = _number(payload.get("downloaded_bytes"))
            if total and downloaded:
                ratio = max(0.0, min(1.0, downloaded / total))
                self.progress("영상 다운로드 중", 0.08 + ratio * 0.15, f"{ratio * 100:.1f}% 다운로드 중")
        elif status == "finished":
            self.progress("영상 정리 중", 0.24, "다운로드한 영상을 MP4로 정리합니다.")

    def _find_downloaded_file(self, output_root: Path, started: str) -> Path | None:
        candidates = sorted(output_root.glob(f"__download_{started}_*"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in candidates:
            if path.suffix.lower() in VIDEO_EXTENSIONS:
                return path
        return candidates[0] if candidates else None

    @staticmethod
    def _best_title(info: dict[str, object]) -> str:
        for key in ("title", "fulltitle", "alt_title", "id"):
            value = str(info.get(key) or "").strip()
            if value:
                return value
        return "영상"

    @staticmethod
    def _brief_error(error: BaseException) -> str:
        text = str(error).strip()
        text = re.sub(r"\s+", " ", text)
        return text[:500] or error.__class__.__name__

    def _prepare_screenshots(self, media_path: Path, source_dir: Path, output_dir: Path) -> list[FrameSample]:
        if media_path.suffix.lower() not in VIDEO_EXTENSIONS:
            self.progress("스크린샷 건너뜀", 0.36, "오디오 파일이라 화면 분석 없이 전사문만 사용합니다.")
            return []

        existing_dir = source_dir / SCREENSHOT_DIR_NAME
        output_screenshot_dir = output_dir / SCREENSHOT_DIR_NAME
        if existing_dir.exists() and existing_dir.is_dir():
            image_paths = self._image_paths(existing_dir)
            if image_paths:
                if existing_dir.resolve() != output_screenshot_dir.resolve():
                    output_screenshot_dir.mkdir(parents=True, exist_ok=True)
                    for image_path in image_paths:
                        target = output_screenshot_dir / image_path.name
                        if not target.exists():
                            shutil.copy2(image_path, target)
                    image_paths = self._image_paths(output_screenshot_dir)
                self.progress("스크린샷 확인", 0.36, "기존 스크린샷 추출본을 사용합니다.")
                return self._select_frame_samples(image_paths, media_path)

        output_screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.progress("스크린샷 추출 중", 0.30, "영상 길이에 맞춰 화면 분석용 프레임을 자동으로 추출합니다.")
        duration = max(1.0, get_media_duration(media_path, self.ffmpeg))
        timestamps = self._sample_timestamps(duration, self._auto_frame_count(duration))
        self.progress(
            "스크린샷 추출 중",
            0.31,
            f"영상 길이 {format_timecode(duration)} 기준으로 프레임 {len(timestamps)}장을 자동 선택했습니다.",
        )
        extracted: list[Path] = []
        for seconds in timestamps:
            image_path = output_screenshot_dir / f"{len(extracted) + 1:04d}_{format_timecode(seconds).replace(':', '-')}.jpg"
            completed = run_process(
                [
                    self.ffmpeg,
                    "-y",
                    "-ss",
                    str(seconds),
                    "-i",
                    media_path,
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    image_path,
                ]
            )
            if completed.returncode == 0 and image_path.exists():
                extracted.append(image_path)
        if not extracted:
            raise UserFacingError("영상에서 스크린샷을 추출하지 못했습니다.")
        return self._select_frame_samples(extracted, media_path)

    @staticmethod
    def _image_paths(folder: Path) -> list[Path]:
        return sorted(
            [
                path
                for path in folder.iterdir()
                if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            ],
            key=lambda path: path.name,
        )

    def _select_frame_samples(self, image_paths: list[Path], media_path: Path) -> list[FrameSample]:
        try:
            duration = max(1.0, get_media_duration(media_path, self.ffmpeg))
        except Exception:
            duration = max(1.0, float(len(image_paths)))
        max_frames = self._auto_frame_count(duration)
        if len(image_paths) <= max_frames:
            selected = image_paths
        else:
            selected = []
            denominator = max(1, max_frames - 1)
            for index in range(max_frames):
                selected.append(image_paths[round(index * (len(image_paths) - 1) / denominator)])
        samples: list[FrameSample] = []
        for fallback_seconds, path in enumerate(selected):
            samples.append(FrameSample(path=path, seconds=self._seconds_from_frame_name(path.name, fallback_seconds)))
        self.progress("스크린샷 준비 완료", 0.38, f"화면 분석용 프레임 {len(samples)}장을 준비했습니다.")
        return samples

    @staticmethod
    def _auto_frame_count(duration: float) -> int:
        duration = max(1.0, float(duration))
        return max(8, min(28, int(round(duration / 5)) + 8))

    @staticmethod
    def _sample_timestamps(duration: float, frame_count: int) -> list[float]:
        duration = max(1.0, float(duration))
        frame_count = max(1, int(frame_count))
        if frame_count == 1:
            return [0.0]

        last_second = max(0.0, duration - 0.35)
        timestamps: list[float] = []
        seen: set[int] = set()
        for index in range(frame_count):
            seconds = round(last_second * index / (frame_count - 1), 2)
            key = int(round(seconds * 100))
            if key in seen:
                continue
            timestamps.append(seconds)
            seen.add(key)
        return timestamps or [0.0]

    @staticmethod
    def _seconds_from_frame_name(name: str, fallback: int) -> int:
        match = re.search(r"(\d{2})-(\d{2})-(\d{2})", name)
        if match:
            hours, minutes, seconds = (int(part) for part in match.groups())
            return hours * 3600 + minutes * 60 + seconds
        return fallback

    def _transcribe_media(self, media_path: Path, output_dir: Path) -> tuple[str, Path | None]:
        self.progress("오디오 추출 중", 0.42, "영상 속 대사를 전사할 오디오를 준비합니다.")
        support_dir = output_dir / SUPPORT_DIR_NAME
        support_dir.mkdir(parents=True, exist_ok=True)
        audio_path = support_dir / "__transcribe.wav"
        completed = run_process(
            [
                self.ffmpeg,
                "-y",
                "-i",
                media_path,
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-codec:a",
                "pcm_s16le",
                audio_path,
            ]
        )
        if completed.returncode != 0 or not audio_path.exists():
            self.progress("전사 건너뜀", 0.50, "영상에서 오디오를 찾지 못해 화면 정보만 사용합니다.")
            return "", None

        try:
            duration = get_media_duration(audio_path, self.ffmpeg)
        except Exception:
            duration = 0.0
        if duration <= 0.5:
            self.progress("전사 건너뜀", 0.50, "전사할 음성이 거의 없어 화면 정보만 사용합니다.")
            return "", None

        transcription_cost = estimate_transcription_cost(self.settings.transcription_model, duration)
        self.estimated_transcription_cost_usd += transcription_cost
        self.progress(
            "예상 API 비용",
            0.52,
            f"전사 예상 비용: {format_usd(transcription_cost)} "
            f"({duration / 60:.1f}분, {self.settings.transcription_model})",
        )
        self.progress("전사 중", 0.54, "OpenAI 전사 모델로 대사를 읽고 있습니다.")
        prompt = (
            "한국어 릴스/쇼츠 영상입니다. 들리는 말만 전사해 주세요. "
            "화면 자막, 제품명, 논문명처럼 실제 표기가 중요한 말은 가능한 한 보존해 주세요. "
            "들리지 않는 내용은 추측하지 마세요."
        )
        try:
            with audio_path.open("rb") as audio_file:
                result = self.client.audio.transcriptions.create(
                    model=self.settings.transcription_model,
                    file=audio_file,
                    response_format="text",
                    prompt=prompt,
                    temperature=0,
                )
        except Exception as exc:
            raise UserFacingError(f"OpenAI 전사 호출에 실패했습니다.\n\n{self._brief_error(exc)}") from exc

        transcript = result if isinstance(result, str) else str(getattr(result, "text", result))
        transcript = transcript.strip()
        transcript_path = output_dir / SCRIPT_FILE_NAME
        if transcript:
            transcript_path.write_text(transcript + "\n", encoding="utf-8")
            self.progress("전사 완료", 0.62, f"스크립트 저장 완료: {transcript_path}")
            return transcript, transcript_path
        self.progress("전사 결과 없음", 0.62, "전사문이 비어 있어 화면 정보만 사용합니다.")
        return "", None

    def _load_training_examples(self, training_dir: Path, exclude_dir: Path | None = None) -> list[TrainingExample]:
        if not training_dir.exists() or not training_dir.is_dir():
            self.progress("학습용 데이터 없음", 0.66, f"학습용 데이터 폴더를 찾지 못했습니다: {training_dir}")
            return []
        examples: list[tuple[float, TrainingExample]] = []
        excluded = exclude_dir.resolve() if exclude_dir else None
        for child in training_dir.iterdir():
            if not child.is_dir():
                continue
            caption_path = child / CAPTION_FILE_NAME
            if not caption_path.exists():
                continue
            try:
                parent = caption_path.parent.resolve()
            except OSError:
                continue
            if excluded and os.path.normcase(str(parent)) == os.path.normcase(str(excluded)):
                continue
            caption = read_text_if_exists(caption_path)
            if not caption:
                continue
            examples.append((caption_path.stat().st_mtime, TrainingExample(title=caption_path.parent.name, caption=caption)))
        examples.sort(key=lambda item: item[0], reverse=True)
        selected = [example for _mtime, example in examples[:6]]
        self.progress("학습용 데이터 확인", 0.68, f"기존 캡션 예시 {len(selected)}개를 참고합니다.")
        return selected

    def _generate_caption(
        self,
        title: str,
        transcript: str,
        frames: list[FrameSample],
        examples: list[TrainingExample],
    ) -> str:
        self.progress("캡션 생성 중", 0.76, "스크린샷, 스크립트, 학습용 데이터를 종합해 캡션을 작성합니다.")
        system = self._caption_system_prompt()
        text_parts = [
            f"영상 제목/폴더명: {title}",
            f"계정/인물 메모: {self.settings.creator_note.strip() or '없음'}",
            "목표: 인스타그램 릴스 게시용 캡션.txt 본문을 완성한다.",
        ]
        if examples:
            text_parts.append("\n[기존 학습용 캡션 예시]")
            for index, example in enumerate(examples, start=1):
                text_parts.append(f"\n예시 {index}. {example.title}\n{example.caption.strip()}")
        text_parts.append("\n[이번 영상 전사문]")
        text_parts.append(transcript.strip() or "(전사문 없음. 화면 프레임을 우선 분석하세요.)")
        text_parts.append(
            "\n[화면 프레임 안내]\n"
            "아래 이미지는 영상에서 시간순으로 뽑은 프레임입니다. 화면 자막, 제품명, 논문 캡처, 인물 성별/계정 힌트를 자세히 읽으세요."
        )

        prompt_text = "\n".join(text_parts)
        frames, estimated_caption_cost = self._optimize_frames_for_cost(system, prompt_text, frames)
        total_estimated_cost = self.estimated_transcription_cost_usd + estimated_caption_cost.total_usd
        self.progress(
            "예상 API 비용",
            0.74,
            "예상 총 비용: "
            f"{format_usd(total_estimated_cost)} "
            f"(전사 {format_usd(self.estimated_transcription_cost_usd)} + "
            f"캡션 {format_usd(estimated_caption_cost.total_usd)}, "
            f"이미지 토큰 약 {estimated_caption_cost.image_tokens:,}개)",
        )

        content: list[dict[str, object]] = [{"type": "input_text", "text": prompt_text}]
        for index, frame in enumerate(frames, start=1):
            content.append({"type": "input_text", "text": f"프레임 {index} / {format_timecode(frame.seconds)}"})
            content.append(
                {
                    "type": "input_image",
                    "image_url": image_to_data_url(
                        frame.path,
                        max_edge=IMAGE_MAX_EDGE,
                        quality=IMAGE_JPEG_QUALITY,
                    ),
                    "detail": IMAGE_DETAIL,
                }
            )

        try:
            response = self.client.responses.create(
                model=self.settings.text_model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
            )
            caption = strip_code_fence(extract_plain_text(response))
        except Exception as exc:
            self.progress(
                "이미지 분석 재시도",
                0.82,
                f"이미지 포함 요청이 실패해 텍스트 정보만으로 재시도합니다: {self._brief_error(exc)}",
            )
            response = self.client.responses.create(
                model=self.settings.text_model,
                input=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": prompt_text
                        + "\n\n주의: 이미지 분석 실패로 전사문과 제목만 보고 작성합니다. 확실하지 않은 화면 정보는 단정하지 마세요.",
                    },
                ],
            )
            caption = strip_code_fence(extract_plain_text(response))

        actual_caption_cost = cost_from_usage(self.settings.text_model, getattr(response, "usage", None))
        if actual_caption_cost:
            actual_total_cost = self.estimated_transcription_cost_usd + actual_caption_cost.total_usd
            self.progress(
                "실제 API 비용",
                0.90,
                "실제 토큰 기준 비용: "
                f"{format_usd(actual_total_cost)} "
                f"(입력 {actual_caption_cost.input_tokens:,}토큰, "
                f"출력 {actual_caption_cost.output_tokens:,}토큰, 전사 예상 포함)",
            )

        caption = self._cleanup_caption(caption)
        if not caption:
            raise UserFacingError("OpenAI가 캡션 본문을 반환하지 않았습니다.")
        return caption

    def _optimize_frames_for_cost(
        self,
        system: str,
        prompt_text: str,
        frames: list[FrameSample],
    ) -> tuple[list[FrameSample], ResponseCostEstimate]:
        estimate = self._caption_cost_estimate(system, prompt_text, frames)
        if estimate.total_usd <= MAX_ESTIMATED_CAPTION_USD or len(frames) <= MIN_COST_OPTIMIZED_FRAMES:
            return frames, estimate

        original_count = len(frames)
        original_cost = estimate.total_usd
        optimized = frames
        while len(optimized) > MIN_COST_OPTIMIZED_FRAMES and estimate.total_usd > TARGET_ESTIMATED_CAPTION_USD:
            next_count = max(MIN_COST_OPTIMIZED_FRAMES, len(optimized) - 2)
            optimized = self._select_evenly(optimized, next_count)
            estimate = self._caption_cost_estimate(system, prompt_text, optimized)

        if len(optimized) != original_count:
            self.progress(
                "비용 최적화",
                0.73,
                f"예상 캡션 비용이 높아 분석 프레임을 {original_count}장 -> {len(optimized)}장으로 줄였습니다. "
                f"{format_usd(original_cost)} -> {format_usd(estimate.total_usd)}",
            )
        if estimate.total_usd > MAX_ESTIMATED_CAPTION_USD:
            self.progress(
                "비용 주의",
                0.73,
                f"프레임을 줄여도 캡션 예상 비용이 {format_usd(estimate.total_usd)}입니다. "
                "긴 전사문/학습용 예시가 많으면 비용이 늘어날 수 있습니다.",
            )
        return optimized, estimate

    def _caption_cost_estimate(
        self,
        system: str,
        prompt_text: str,
        frames: list[FrameSample],
    ) -> ResponseCostEstimate:
        return estimate_response_cost(
            self.settings.text_model,
            system + "\n\n" + prompt_text,
            [frame.path for frame in frames],
            image_detail=IMAGE_DETAIL,
        )

    @staticmethod
    def _select_evenly(items: list[FrameSample], count: int) -> list[FrameSample]:
        count = max(1, min(count, len(items)))
        if count >= len(items):
            return items
        if count == 1:
            return [items[0]]
        denominator = max(1, count - 1)
        return [items[round(index * (len(items) - 1) / denominator)] for index in range(count)]

    def _caption_system_prompt(self) -> str:
        reference_rule = (
            "영상에 논문, DOI, 학술지명, 연구 결과 캡처가 명확히 등장하면 캡션 하단에 '📚 참고 논문' 또는 '📚 참고 자료'를 짧게 포함한다. "
            "화면/전사문에서 확인된 제목, 저널, 연도, DOI만 적고 절대 지어내지 않는다. "
            if self.settings.include_research_reference
            else "논문 출처는 사용자가 요청하지 않는 한 별도로 만들지 않는다. "
        )
        return (
            "너는 한국어 약사/헬스케어 인스타그램 릴스 캡션을 쓰는 편집자다. "
            "사용자가 제공한 기존 학습용 캡션의 톤, 줄바꿈, 이모지, CTA, 해시태그 패턴을 따르되 이번 영상 내용에 맞게 새로 쓴다. "
            "대본보다 화면 자막과 제품 컷이 더 정확할 수 있으므로 이미지 프레임의 텍스트와 시각 정보를 우선한다. "
            "남자 약사와 여자 약사의 계정명을 혼동하지 않는다. 계정/인물 메모가 있으면 반드시 따른다. "
            "의약품, 건강, 피부, 눈 건강 정보는 과장하지 말고 개인 진료/처방을 대체하지 않는다는 안전 문구를 필요한 만큼 자연스럽게 넣는다. "
            "제품명, 성분명, 수치, 사용 횟수, 기간, 부작용, 예외 조건은 화면이나 전사문에서 확인된 것만 쓴다. "
            + reference_rule
            + "출력은 캡션.txt에 그대로 저장할 최종 본문만 반환한다. 제목 라벨, 설명, 마크다운 코드블록은 쓰지 않는다. "
            "권장 구조는 후킹 문장, 문제 인식, 3가지 내외 핵심 정리, 사용 팁/주의, 저장/공유/팔로우 유도, 해시태그다."
        )

    @staticmethod
    def _cleanup_caption(text: str) -> str:
        cleaned = strip_code_fence(text)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        cleaned = re.sub(r"^\s*(캡션|caption)\s*[:：]\s*", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip()


def _number(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
