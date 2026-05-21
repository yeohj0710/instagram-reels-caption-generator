from __future__ import annotations

from reels_caption_generator.settings import AppSettings, DEFAULT_TEXT_MODEL


def test_default_settings_are_shareable() -> None:
    settings = AppSettings()

    assert settings.text_model == DEFAULT_TEXT_MODEL
    assert settings.save_next_to_source is True
    assert "oyakstory" in settings.creator_note
    assert "jessi" in settings.creator_note
