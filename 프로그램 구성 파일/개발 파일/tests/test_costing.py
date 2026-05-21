from __future__ import annotations

from PIL import Image

from reels_caption_generator.costing import (
    estimate_image_tokens,
    estimate_response_cost,
    estimate_transcription_cost,
    format_krw,
)


def test_estimate_transcription_cost_uses_minutes() -> None:
    assert estimate_transcription_cost("gpt-4o-mini-transcribe", 120) == 0.006


def test_estimate_image_tokens_for_gpt5_high_detail(tmp_path) -> None:
    image_path = tmp_path / "frame.jpg"
    Image.new("RGB", (1080, 1920), "white").save(image_path)

    tokens = estimate_image_tokens(image_path, "gpt-5-mini", detail="high")

    assert tokens == 910


def test_response_cost_includes_image_tokens(tmp_path) -> None:
    image_path = tmp_path / "frame.jpg"
    Image.new("RGB", (512, 512), "white").save(image_path)

    estimate = estimate_response_cost("gpt-5-mini", "짧은 프롬프트", [image_path])

    assert estimate.image_tokens > 0
    assert estimate.total_usd > 0


def test_format_krw_keeps_small_cost_visible() -> None:
    assert format_krw(0.00034) == "1원 미만"
    assert format_krw(0.025) == "약 38원"
