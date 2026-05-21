from __future__ import annotations

from pathlib import Path

from reels_caption_generator.pipeline import CaptionPipeline
from reels_caption_generator.utils import sanitize_filename, strip_code_fence


def test_strip_code_fence() -> None:
    assert strip_code_fence("```text\nhello\n```") == "hello"


def test_sanitize_filename_removes_windows_reserved_chars() -> None:
    assert sanitize_filename('a/b:c*?"<>|') == "a_b_c______"


def test_seconds_from_frame_name() -> None:
    assert CaptionPipeline._seconds_from_frame_name("0015_00-00-14.jpg", 3) == 14
    assert CaptionPipeline._seconds_from_frame_name("plain.jpg", 3) == 3


def test_auto_frame_count_scales_with_duration() -> None:
    assert CaptionPipeline._auto_frame_count(10) == 10
    assert CaptionPipeline._auto_frame_count(60) == 20
    assert CaptionPipeline._auto_frame_count(999) == 28


def test_sample_timestamps_cover_video_range() -> None:
    timestamps = CaptionPipeline._sample_timestamps(30, 6)

    assert len(timestamps) == 6
    assert timestamps[0] == 0
    assert 29 <= timestamps[-1] <= 30


def test_cleanup_caption_removes_label() -> None:
    assert CaptionPipeline._cleanup_caption("캡션: 저장하세요\n\n#태그") == "저장하세요\n\n#태그"


def test_browser_cookie_error_detection() -> None:
    assert CaptionPipeline._is_browser_cookie_error(
        RuntimeError("ERROR: Could not copy Chrome cookie database")
    )
    assert not CaptionPipeline._is_browser_cookie_error(RuntimeError("HTTP Error 404: Not Found"))


def test_load_training_examples_reads_child_caption_files(tmp_path: Path) -> None:
    example_dir = tmp_path / "예시"
    example_dir.mkdir()
    (example_dir / "캡션.txt").write_text("본문", encoding="utf-8")
    pipeline = object.__new__(CaptionPipeline)
    pipeline.progress = lambda _message, _percent, _detail: None

    examples = CaptionPipeline._load_training_examples(pipeline, tmp_path)

    assert len(examples) == 1
    assert examples[0].title == "예시"
    assert examples[0].caption == "본문"
