"""Tests for the LLM response parser."""

from __future__ import annotations

from omakase.engine import _parse_recommendations

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


def test_returns_empty_on_invalid_json():
    recs = _parse_recommendations("not json at all")
    assert recs == []


def test_handles_missing_fields_gracefully():
    raw = '{"recommendations": [{"title": "Made in Abyss"}]}'
    recs = _parse_recommendations(raw)
    assert len(recs) == 1
    assert recs[0].title == "Made in Abyss"
    assert recs[0].predicted_score == 0.0
    assert recs[0].reasoning == ""
