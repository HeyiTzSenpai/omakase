"""Tests for the LLM response parser."""

from __future__ import annotations

import pytest

from omakase.engine import LLMOutputParseError, _parse_recommendations, _resolve_rec_urls
from omakase.types import MediaItem, Recommendation

SAMPLE_JSON = """
{"recommendations": [
  {"title": "Vinland Saga", "predicted_score": 9, "reasoning": "Morally complex protagonist, dense world-building.", "best_match_from_history": "Berserk"},
  {"title": "Mushishi", "predicted_score": 8, "reasoning": "Quiet, atmospheric pacing.", "best_match_from_history": "Natsume's Book of Friends"}
]}
"""


def test_parses_clean_json():
    recs = _parse_recommendations(SAMPLE_JSON)
    assert len(recs) == 2
    assert recs[0].title == "Vinland Saga"
    assert recs[0].predicted_score == 9.0
    assert "Berserk" in recs[0].best_match_from_history


def test_strips_markdown_code_fences():
    wrapped = "```json\n" + SAMPLE_JSON.strip() + "\n```"
    recs = _parse_recommendations(wrapped)
    assert len(recs) == 2


def test_strips_leading_and_trailing_prose():
    noisy = "Here are your picks:\n" + SAMPLE_JSON.strip() + "\nHope you enjoy!"
    recs = _parse_recommendations(noisy)
    assert len(recs) == 2


def test_raises_on_invalid_json():
    """An unparseable response surfaces as an explicit error.

    Previously this returned `[]` silently, so a max-tokens truncation in
    the LLM call landed as a "0 picks" run in the dashboard with no
    indication of what failed. Now the caller sees a clear error.
    """
    with pytest.raises(LLMOutputParseError):
        _parse_recommendations("not json at all")


def test_raises_on_truncated_json():
    """Mid-string truncation (the real-world max-tokens case) raises."""
    truncated = (
        '{"recommendations": [{"title": "Vinland Saga", "predicted_score": 9, '
        '"reasoning": "Morally complex protagonist who'  # cut mid-string
    )
    with pytest.raises(LLMOutputParseError):
        _parse_recommendations(truncated)


def test_handles_missing_fields_gracefully():
    raw = '{"recommendations": [{"title": "Made in Abyss"}]}'
    recs = _parse_recommendations(raw)
    assert len(recs) == 1
    assert recs[0].title == "Made in Abyss"
    assert recs[0].predicted_score == 0.0
    assert recs[0].reasoning == ""


def _rec(title: str) -> Recommendation:
    return Recommendation(
        title=title,
        predicted_score=8.0,
        reasoning="",
        best_match_from_history="",
    )


def test_resolve_urls_anilist_permalink_on_title_match():
    candidates = [
        MediaItem(id=21, title_romaji="One Piece", title_english="One Piece"),
        MediaItem(id=99, title_romaji="Vinland Saga", title_english="Vinland Saga"),
    ]
    recs = [_rec("Vinland Saga")]
    _resolve_rec_urls(recs, candidates, "anilist")
    assert recs[0].source == "anilist"
    assert recs[0].url == "https://anilist.co/anime/99/"


def test_resolve_urls_mal_permalink_on_title_match():
    candidates = [
        MediaItem(
            id=5114,
            title_romaji="Hagane no Renkinjutsushi",
            title_english="Fullmetal Alchemist: Brotherhood",
        ),
    ]
    recs = [_rec("Fullmetal Alchemist: Brotherhood")]
    _resolve_rec_urls(recs, candidates, "myanimelist")
    assert recs[0].source == "myanimelist"
    assert recs[0].url == "https://myanimelist.net/anime/5114/"


def test_resolve_urls_falls_back_to_search_when_no_match():
    candidates = [MediaItem(id=1, title_romaji="Cowboy Bebop", title_english="Cowboy Bebop")]
    recs = [_rec("Some Obscure Title")]
    _resolve_rec_urls(recs, candidates, "anilist")
    assert recs[0].url is not None
    assert "anilist.co/search/anime" in recs[0].url
    assert "Some+Obscure+Title" in recs[0].url


def test_resolve_urls_match_is_case_insensitive_and_uses_romaji_too():
    candidates = [
        MediaItem(id=42, title_romaji="Shingeki no Kyojin", title_english="Attack on Titan"),
    ]
    recs_en = [_rec("attack on titan")]
    recs_ro = [_rec("Shingeki No Kyojin")]
    _resolve_rec_urls(recs_en, candidates, "anilist")
    _resolve_rec_urls(recs_ro, candidates, "anilist")
    assert recs_en[0].url == "https://anilist.co/anime/42/"
    assert recs_ro[0].url == "https://anilist.co/anime/42/"
