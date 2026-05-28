"""Regression tests for the franchise filter.

Bug discovered 2026-05-28: the candidate pool returned by AniList included
sequels and spin-offs of dropped or watched shows because AniList's
``id_not_in`` only excludes by exact id. The LLM then dutifully scored those
2/10 with reasoning citing the rule violation, polluting recommendations.

Two filters now run in `fetch()`:
- relations graph (AniList's own ``relations`` edges)
- title-stem dedup (belt for sparse relations metadata)
"""

from __future__ import annotations

from unittest.mock import patch

from omakase.adapters.anilist import (
    AniListAdapter,
    _candidate_is_in_franchise,
    _title_stem,
)
from omakase.types import MediaItem

# ── _title_stem ─────────────────────────────────────────────────────────


def test_title_stem_strips_season_marker():
    assert _title_stem("Gintama Season 2") == "gintama"
    assert _title_stem("Attack on Titan Season 3 Part 2") == "attack on titan"


def test_title_stem_strips_subtitle_after_colon():
    assert _title_stem("Gintama: THE VERY FINAL") == "gintama"
    assert _title_stem("Code Geass: Lelouch of the Re;surrection") == "code geass"


def test_title_stem_picks_earliest_separator():
    # "Chainsaw Man - The Movie: Reze Arc" has both " - " and ": ".
    # We want the earliest separator (the dash) so the stem is "chainsaw man",
    # not "chainsaw man - the movie".
    assert _title_stem("Chainsaw Man - The Movie: Reze Arc") == "chainsaw man"
    # Unicode en-dash variant (the real AniList title uses U+2013)
    assert _title_stem("Chainsaw Man – The Movie: Reze Arc") == "chainsaw man"


def test_title_stem_strips_roman_numerals():
    assert _title_stem("Macross II") == "macross"
    assert _title_stem("Fate/Zero III") == "fate/zero"


def test_title_stem_strips_movie_film_special():
    assert _title_stem("Chainsaw Man Movie") == "chainsaw man"
    assert _title_stem("One Piece Film") == "one piece"
    assert _title_stem("Bleach Special") == "bleach"


def test_title_stem_strips_parenthetical_tail():
    assert _title_stem("Naruto (TV)") == "naruto"
    assert _title_stem("Hunter x Hunter (2011)") == "hunter x hunter"


def test_title_stem_handles_none_and_empty():
    assert _title_stem(None) == ""
    assert _title_stem("") == ""
    assert _title_stem("   ") == ""


# ── _candidate_is_in_franchise ──────────────────────────────────────────


def test_candidate_blocked_by_exact_id_match():
    cand = MediaItem(id=1, title_romaji="Anything")
    assert _candidate_is_in_franchise(cand, {1, 2}, set()) is True


def test_candidate_blocked_by_relation_to_history_id():
    cand = MediaItem(id=999, title_romaji="The Very Final", related_ids=[42, 7])
    assert _candidate_is_in_franchise(cand, {42}, set()) is True


def test_candidate_blocked_by_title_stem_match():
    cand = MediaItem(id=999, title_romaji="Gintama: THE VERY FINAL")
    assert _candidate_is_in_franchise(cand, set(), {"gintama"}) is True


def test_candidate_passes_when_unrelated():
    cand = MediaItem(
        id=999,
        title_romaji="A Completely Unrelated Show",
        related_ids=[1000, 1001],
    )
    assert _candidate_is_in_franchise(cand, {42, 50}, {"naruto", "bleach"}) is False


def test_candidate_blocked_via_english_title_stem():
    cand = MediaItem(id=999, title_romaji="ロロ", title_english="Naruto: The Last Movie")
    assert _candidate_is_in_franchise(cand, set(), {"naruto"}) is True


# ── fetch() integration ─────────────────────────────────────────────────


def _hist():
    return [
        MediaItem(id=100, title_romaji="Gintama Season 2", status="DROPPED"),
        MediaItem(id=200, title_romaji="Chainsaw Man", status="COMPLETED"),
    ]


def test_fetch_drops_sequel_via_relations():
    """A sequel whose ``related_ids`` point at a history id must be excluded."""
    adapter = AniListAdapter()
    candidates_in = [
        MediaItem(id=101, title_romaji="Gintama: THE VERY FINAL", related_ids=[100]),
        MediaItem(id=999, title_romaji="Fresh Original Show", related_ids=[]),
    ]
    with (
        patch.object(adapter, "_fetch_history", return_value=_hist()),
        patch.object(adapter, "_fetch_candidates_targeted", return_value=candidates_in),
        patch.object(adapter, "_analyze_genre_affinity", return_value=["Action"]),
    ):
        data = adapter.fetch("anyuser", use_planning=False)
    cand_ids = {c.id for c in data.candidates}
    assert 101 not in cand_ids, "sequel pointed at history by relations must be dropped"
    assert 999 in cand_ids, "unrelated candidate must pass through"


def test_fetch_drops_sequel_via_title_stem_when_relations_missing():
    """If AniList's relations data is sparse, title-stem dedup still catches the sequel."""
    adapter = AniListAdapter()
    candidates_in = [
        # No related_ids set — simulates incomplete AniList metadata
        MediaItem(id=102, title_romaji="Gintama Season 3", related_ids=[]),
        MediaItem(id=201, title_romaji="Chainsaw Man – The Movie: Reze Arc", related_ids=[]),
        MediaItem(id=999, title_romaji="Mushoku Tensei", related_ids=[]),
    ]
    with (
        patch.object(adapter, "_fetch_history", return_value=_hist()),
        patch.object(adapter, "_fetch_candidates_targeted", return_value=candidates_in),
        patch.object(adapter, "_analyze_genre_affinity", return_value=["Action"]),
    ):
        data = adapter.fetch("anyuser", use_planning=False)
    cand_ids = {c.id for c in data.candidates}
    assert 102 not in cand_ids, "Gintama Season 3 must be dropped by title stem"
    assert 201 not in cand_ids, "Chainsaw Man movie must be dropped by title stem"
    assert 999 in cand_ids, "Mushoku Tensei is unrelated and must pass"


def test_fetch_planning_mode_unaffected_by_franchise_filter():
    """Planning-mode candidates are user-selected — never franchise-filter them."""
    adapter = AniListAdapter()
    planning = [
        MediaItem(id=101, title_romaji="Gintama: THE VERY FINAL"),
        MediaItem(id=999, title_romaji="Fresh Show"),
    ]
    with (
        patch.object(adapter, "_fetch_history", return_value=_hist()),
        patch.object(adapter, "_fetch_planning", return_value=planning),
    ):
        data = adapter.fetch("anyuser", use_planning=True)
    cand_ids = {c.id for c in data.candidates}
    # Planning items the user explicitly chose are kept even when same-franchise.
    assert cand_ids == {101, 999}
