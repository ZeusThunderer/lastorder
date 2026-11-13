"""ADR-055: what a chat line turns into before the voice reads it."""

from __future__ import annotations

import pytest

from runtime.tts.normalize import normalize


def test_roleplay_emoji_and_links_are_not_spoken():
    line = "*кивает* Иду к шахте 🙂 смотри https://example.com/map"
    assert normalize(line) == "Иду к шахте смотри ссылка"


def test_a_line_with_nothing_to_voice_is_empty():
    assert normalize("🙂🙂 ...") == ""
    assert normalize("*молчит*") == ""


def test_names_with_digits_stay_as_they_are():
    # a game name is not a number: LastOrder01 keeps its digits
    assert "LastOrder01" in normalize("привет, LastOrder01")


def test_numbers_become_russian_words():
    pytest.importorskip("num2words")
    assert normalize("сказала Мисака 00007") == "сказала Мисака семь"
    assert normalize("нашла 12 угля") == "нашла двенадцать угля"
    assert normalize("осталось 2,5 сердца") == "осталось два целых пять сердца"
