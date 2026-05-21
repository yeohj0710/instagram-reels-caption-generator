from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


# Snapshot from official OpenAI pricing pages checked on 2026-05-21.
TEXT_MODEL_PRICES_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4o-mini": (0.15, 0.60),
}

TRANSCRIPTION_USD_PER_MINUTE: dict[str, float] = {
    "gpt-4o-mini-transcribe": 0.003,
    "gpt-4o-transcribe": 0.006,
    "whisper-1": 0.006,
}

IMAGE_TOKEN_TABLE: dict[str, tuple[int, int]] = {
    "gpt-5": (70, 140),
    "gpt-5-mini": (70, 140),
    "gpt-5-nano": (70, 140),
    "gpt-4.1-mini": (85, 170),
    "gpt-4o-mini": (2833, 5667),
}

DEFAULT_TEXT_PRICE_PER_1M = (0.25, 2.00)
DEFAULT_IMAGE_TOKEN_TABLE = (85, 170)
ESTIMATED_CAPTION_OUTPUT_TOKENS = 900
DISPLAY_KRW_PER_USD = 1500


@dataclass(frozen=True)
class ResponseCostEstimate:
    input_tokens: int
    output_tokens: int
    image_tokens: int
    input_usd: float
    output_usd: float

    @property
    def total_usd(self) -> float:
        return self.input_usd + self.output_usd


def format_usd(value: float) -> str:
    if value <= 0:
        return "$0.0000"
    if value < 0.01:
        return f"${value:.4f}"
    return f"${value:.3f}"


def format_krw(value_usd: float) -> str:
    if value_usd <= 0:
        return "0원"
    krw = value_usd * DISPLAY_KRW_PER_USD
    if krw < 1:
        return "1원 미만"
    if krw < 10:
        value = f"{krw:.1f}".rstrip("0").rstrip(".")
        return f"약 {value}원"
    return f"약 {round(krw):,}원"


def estimate_text_tokens(text: str) -> int:
    # Korean captions and prompts are dense; this intentionally leans slightly conservative.
    return max(1, math.ceil(len(text) / 1.8))


def estimate_transcription_cost(model: str, seconds: float) -> float:
    minutes = max(0.0, float(seconds)) / 60
    rate = TRANSCRIPTION_USD_PER_MINUTE.get(model, 0.006)
    return minutes * rate


def text_model_price(model: str) -> tuple[float, float]:
    return TEXT_MODEL_PRICES_PER_1M.get(model, DEFAULT_TEXT_PRICE_PER_1M)


def estimate_response_cost(
    model: str,
    prompt_text: str,
    image_paths: list[Path],
    *,
    image_detail: str = "high",
    output_tokens: int = ESTIMATED_CAPTION_OUTPUT_TOKENS,
) -> ResponseCostEstimate:
    text_tokens = estimate_text_tokens(prompt_text)
    image_tokens = sum(estimate_image_tokens(path, model, detail=image_detail) for path in image_paths)
    input_tokens = text_tokens + image_tokens
    input_price, output_price = text_model_price(model)
    return ResponseCostEstimate(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        image_tokens=image_tokens,
        input_usd=(input_tokens / 1_000_000) * input_price,
        output_usd=(output_tokens / 1_000_000) * output_price,
    )


def estimate_image_tokens(path: Path, model: str, *, detail: str = "high") -> int:
    base_tokens, tile_tokens = image_token_table(model)
    if detail == "low":
        return base_tokens

    try:
        with Image.open(path) as image:
            width, height = image.size
    except OSError:
        return base_tokens + (tile_tokens * 4)

    width = max(1.0, float(width))
    height = max(1.0, float(height))

    largest = max(width, height)
    if largest > 2048:
        ratio = 2048 / largest
        width *= ratio
        height *= ratio

    shortest = min(width, height)
    if shortest > 0:
        ratio = 768 / shortest
        width *= ratio
        height *= ratio

    tiles = math.ceil(width / 512) * math.ceil(height / 512)
    return base_tokens + (tile_tokens * max(1, tiles))


def image_token_table(model: str) -> tuple[int, int]:
    if model in IMAGE_TOKEN_TABLE:
        return IMAGE_TOKEN_TABLE[model]
    if model.startswith("gpt-5"):
        return IMAGE_TOKEN_TABLE["gpt-5"]
    if model.startswith("gpt-4.1"):
        return IMAGE_TOKEN_TABLE["gpt-4.1-mini"]
    if model.startswith("gpt-4o-mini"):
        return IMAGE_TOKEN_TABLE["gpt-4o-mini"]
    return DEFAULT_IMAGE_TOKEN_TABLE


def cost_from_usage(model: str, usage: object | None) -> ResponseCostEstimate | None:
    if usage is None:
        return None

    input_tokens = _usage_int(usage, "input_tokens", "prompt_tokens")
    output_tokens = _usage_int(usage, "output_tokens", "completion_tokens")
    if input_tokens is None or output_tokens is None:
        return None

    input_price, output_price = text_model_price(model)
    return ResponseCostEstimate(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        image_tokens=0,
        input_usd=(input_tokens / 1_000_000) * input_price,
        output_usd=(output_tokens / 1_000_000) * output_price,
    )


def _usage_int(usage: object, *names: str) -> int | None:
    for name in names:
        value = getattr(usage, name, None)
        if value is None and isinstance(usage, dict):
            value = usage.get(name)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None
