"""Tests for the prompt builder."""

from __future__ import annotations

from omakase.prompt import build_prompt
from omakase.types import MediaItem


def _media(title, score=None, status="COMPLETED", genres=None, mid=1):
    return MediaItem(
        id=mid,
        title_romaji=title,
        title_english=title,
        genres=genres or ["Drama"],
        score=score,
        status=status,
    )


def test_prompt_includes_taste_profile_text():
    prompt = build_prompt(
        taste_profile="I love morally complex protagonists.",
        history=[_media("Berserk", score=10, mid=1)],
        candidates=[_media("Vinland Saga", mid=2)],
    )
    assert "morally complex protagonists" in prompt


def test_prompt_groups_history_into_buckets():
    history = [
        _media("Loved Show", score=10, mid=1),
        _media("Liked Show", score=8, mid=2),
        _media("Mid Show", score=5, mid=3),
        _media("Bad Show", score=2, mid=4),
    ]
    prompt = build_prompt(taste_profile="x", history=history, candidates=[_media("Cand", mid=5)])
    assert "9-10 (Loved)" in prompt
    assert "7-8 (Liked)" in prompt
    assert "4-6 (Mid)" in prompt
    assert "1-3 (Bounced)" in prompt


def test_prompt_separates_dropped_shows():
    history = [
        _media("Liked", score=8, status="COMPLETED", mid=1),
        _media("Quit", score=3, status="DROPPED", mid=2),
    ]
    prompt = build_prompt(taste_profile="x", history=history, candidates=[_media("Cand", mid=3)])
    assert "DROPPED OR PAUSED" in prompt
    assert "do NOT recommend these" in prompt


def test_prompt_caps_recommendation_count_at_candidate_pool():
    candidates = [_media(f"Cand{i}", mid=100 + i) for i in range(3)]
    prompt = build_prompt(taste_profile="x", history=[], candidates=candidates)
    assert "recommend 3 anime" in prompt
