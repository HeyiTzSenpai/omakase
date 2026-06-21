# Omakase Plus Recommendation Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build lane-aware, franchise-aware Omakase Plus recommendations with clickable cards, separate Plan and Download actions, visible airing/sequencing labels, and local feedback learning.

**Architecture:** Keep source adapters responsible for fetching rich source metadata, move franchise/lane policy into focused pure-Python modules, keep the engine contract backwards-compatible, and let Plus routes pass lane plus per-user feedback into the existing background job flow. The dashboard remains server-rendered Jinja with small vanilla JavaScript for the existing polling loop.

**Tech Stack:** Python dataclasses, FastAPI, Jinja2, SQLite migrations, pytest, ruff, existing AniList GraphQL adapter, existing Plus SQLite connection helpers.

---

## Scope Check

The spec touches one integrated feature: recommendation intelligence. It spans adapter metadata, candidate policy, prompt assembly, Plus persistence, and dashboard rendering, but each part is required for the same user workflow: choose a lane, run recommendations, understand why a pick appeared, act on it, and send feedback. This plan keeps it as one implementation sequence with testable checkpoints after each task.

## File Structure

- Modify `src/omakase/types.py` for enriched `MediaItem`, `Recommendation`, and `OmakaseConfig` fields used across CLI, engine, and Plus.
- Create `src/omakase/franchise.py` for pure franchise policy classification and lane sorting.
- Modify `src/omakase/adapters/anilist.py` to fetch AniList relation/status fields and call the policy layer.
- Modify `src/omakase/prompt.py` to include lane, franchise, airing, sequencing, and feedback context.
- Modify `src/omakase/engine.py` to parse optional recommendation metadata and pass lane/feedback into `build_prompt`.
- Add `src/omakase/plus/migrations/003-recommendation-intelligence.sql` for `run_history.lane` and `recommendation_feedback`.
- Create `src/omakase/plus/feedback.py` for database helpers and prompt summaries.
- Modify `src/omakase/plus/routes.py` to accept lane, pass feedback into jobs, persist run lane, split plan/download, and save feedback.
- Modify `src/omakase/plus/templates/dashboard.html` for lane controls, clickable cards, chips, split actions, and feedback buttons.
- Extend `tests/test_anilist_franchise.py`, `tests/test_prompt.py`, `tests/test_engine.py`, `tests/plus/test_schema.py`, and `tests/plus/test_dashboard.py`; add `tests/test_franchise_policy.py` and `tests/plus/test_feedback.py`.

---

### Task 1: Extend Recommendation Data Contracts

**Files:**
- Modify: `src/omakase/types.py`
- Modify: `src/omakase/engine.py`
- Test: `tests/test_engine.py`
- Test: `tests/test_types.py`

- [ ] **Step 1: Write failing parser and dataclass tests**

Add these tests:

```python
from omakase.engine import _parse_recommendations
from omakase.types import MediaItem, MediaRelation


def test_parse_recommendation_optional_intelligence_fields():
    raw = """{"recommendations":[{"title":"Frieren Season 2","predicted_score":9.1,"reasoning":"because","best_match_from_history":"Frieren","anilist_id":123,"airing_status":"RELEASING","franchise_note":"Loved franchise continuation","sequence_warning":"Start with season 1","lane_reason":"new season"}]}"""
    rec = _parse_recommendations(raw)[0]
    assert rec.anilist_id == 123
    assert rec.airing_status == "RELEASING"
    assert rec.franchise_note == "Loved franchise continuation"
    assert rec.sequence_warning == "Start with season 1"
    assert rec.lane_reason == "new season"


def test_media_item_accepts_rich_relation_metadata():
    item = MediaItem(
        id=2,
        title_romaji="Example Season 2",
        season="SPRING",
        season_year=2026,
        start_date="2026-04-01",
        next_airing_episode=4,
        next_airing_at=1776200000,
        relations=[
            MediaRelation(
                relation_type="PREQUEL",
                media_id=1,
                title_romaji="Example",
                title_english="Example",
                format="TV",
                status="FINISHED",
                episodes=12,
                season="WINTER",
                season_year=2025,
            )
        ],
    )
    assert item.relations[0].relation_type == "PREQUEL"
    assert item.next_airing_episode == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_engine.py::test_parse_recommendation_optional_intelligence_fields tests/test_types.py::test_media_item_accepts_rich_relation_metadata -q
```

Expected: failures naming missing `MediaRelation`, missing `MediaItem` keyword fields, and missing `Recommendation` attributes.

- [ ] **Step 3: Add the new dataclass fields**

Update `src/omakase/types.py` with these additions:

```python
@dataclass
class MediaRelation:
    """AniList relation metadata for franchise-aware recommendation policy."""

    relation_type: str
    media_id: int
    title_romaji: str
    title_english: str | None = None
    format: str | None = None
    status: str | None = None
    episodes: int | None = None
    season: str | None = None
    season_year: int | None = None


@dataclass
class RecommendationFeedbackSignal:
    """Local Plus feedback that can influence future recommendation prompts."""

    media_id: int | None
    title: str
    feedback_type: str
```

Extend `MediaItem` with:

```python
    season: str | None = None
    season_year: int | None = None
    start_date: str | None = None
    next_airing_episode: int | None = None
    next_airing_at: int | None = None
    relations: list[MediaRelation] = field(default_factory=list)
    franchise_policy: str = "neutral"
    franchise_note: str = ""
    sequence_warning: str = ""
    loose_order: bool = False
```

Extend `Recommendation` with:

```python
    anilist_id: int | None = None
    media_id: int | None = None
    airing_status: str | None = None
    franchise_note: str | None = None
    sequence_warning: str | None = None
    lane_reason: str | None = None
```

Extend `OmakaseConfig` with:

```python
    recommendation_lane: str = "best_match"
    feedback: list[RecommendationFeedbackSignal] = field(default_factory=list)
```

- [ ] **Step 4: Parse optional recommendation fields backwards-compatibly**

In `src/omakase/engine.py`, change the `Recommendation(...)` construction inside `_parse_recommendations` to:

```python
            Recommendation(
                title=r.get("title", "Unknown"),
                predicted_score=float(r.get("predicted_score", 0)),
                reasoning=r.get("reasoning", ""),
                best_match_from_history=r.get("best_match_from_history", ""),
                anilist_id=r.get("anilist_id"),
                media_id=r.get("media_id"),
                airing_status=r.get("airing_status"),
                franchise_note=r.get("franchise_note"),
                sequence_warning=r.get("sequence_warning"),
                lane_reason=r.get("lane_reason"),
            )
```

- [ ] **Step 5: Preserve source IDs when resolving URLs**

In `_resolve_rec_urls`, after `match = lookup.get(...)`, set IDs and status:

```python
        if match:
            if source_name == "anilist":
                r.anilist_id = r.anilist_id or match.id
            r.media_id = r.media_id or match.id
            r.airing_status = r.airing_status or match.status
            r.franchise_note = r.franchise_note or match.franchise_note or None
            r.sequence_warning = r.sequence_warning or match.sequence_warning or None
```

- [ ] **Step 6: Run task tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_engine.py tests/test_types.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

Run:

```powershell
git add src/omakase/types.py src/omakase/engine.py tests/test_engine.py tests/test_types.py
git commit -m "feat: extend recommendation metadata contracts"
```

---

### Task 2: Add Pure Franchise Policy And Lane Sorting

**Files:**
- Create: `src/omakase/franchise.py`
- Test: `tests/test_franchise_policy.py`

- [ ] **Step 1: Write failing policy tests**

Create `tests/test_franchise_policy.py`:

```python
from omakase.franchise import apply_lane_policy
from omakase.types import MediaItem, MediaRelation


def hist(media_id, title, score=None, status="COMPLETED"):
    return MediaItem(id=media_id, title_romaji=title, title_english=title, score=score, status=status)


def cand(media_id, title, relation_type="SEQUEL", related_id=1, status="FINISHED", season_year=2026):
    return MediaItem(
        id=media_id,
        title_romaji=title,
        title_english=title,
        status=status,
        season_year=season_year,
        mean_score=82,
        relations=[MediaRelation(relation_type=relation_type, media_id=related_id, title_romaji="Base")],
    )


def test_loved_franchise_continuation_is_boosted():
    result = apply_lane_policy([hist(1, "Base", score=9)], [cand(2, "Base 2")], "best_match")
    assert result[0].franchise_policy == "boosted"
    assert "Loved franchise" in result[0].franchise_note


def test_low_rated_franchise_is_blocked_outside_plan_lane():
    result = apply_lane_policy([hist(1, "Base", score=4)], [cand(2, "Base 2")], "best_match")
    assert result == []


def test_paused_franchise_is_blocked_outside_plan_lane():
    result = apply_lane_policy([hist(1, "Base", status="PAUSED")], [cand(2, "Base 2")], "new_seasons")
    assert result == []


def test_plan_lane_warns_instead_of_dropping_blocked_relation():
    result = apply_lane_policy([hist(1, "Base", score=3)], [cand(2, "Base 2")], "plan_list")
    assert len(result) == 1
    assert result[0].franchise_policy == "blocked"
    assert "Low-rated" in result[0].franchise_note


def test_six_or_seven_relation_is_neutral():
    result = apply_lane_policy([hist(1, "Base", score=7)], [cand(2, "Base 2")], "best_match")
    assert result[0].franchise_policy == "neutral"


def test_new_seasons_orders_boosted_recent_before_unrelated_finished():
    boosted = cand(2, "Base 2", status="RELEASING", season_year=2026)
    unrelated = MediaItem(id=9, title_romaji="Older Gem", title_english="Older Gem", status="FINISHED", season_year=2012, mean_score=91)
    result = apply_lane_policy([hist(1, "Base", score=9)], [unrelated, boosted], "new_seasons")
    assert result[0].id == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_franchise_policy.py -q
```

Expected: failure because `omakase.franchise` does not exist.

- [ ] **Step 3: Implement policy module**

Create `src/omakase/franchise.py`:

```python
"""Franchise-aware candidate policy for Omakase recommendations."""

from __future__ import annotations

import re
from dataclasses import replace

from omakase.types import MediaItem

LOW_STATUSES = {"DROPPED", "PAUSED"}
LOOSE_RELATIONS = {"SIDE_STORY", "SPIN_OFF", "ALTERNATIVE", "SUMMARY", "COMPILATION", "OTHER"}
STRICT_RELATIONS = {"PREQUEL", "SEQUEL", "PARENT", "CONTAINS"}
_PAREN_TAIL = re.compile(r"\s*\([^)]*\)\s*$")
_SUBTITLE_SEPARATORS = (": ", " - ", " – ", " — ")
_SEASON_TAIL = re.compile(
    r"\s+(?:season\s+\d+|s\d+|part\s+\d+|\d+(?:st|nd|rd|th)\s+season|"
    r"the\s+(?:movie|final|animation)|movie|film|ova|special|specials|"
    r"i{2,}|iv|vi+|ix|x|\d+)$",
    re.IGNORECASE,
)


def _title_stem(title: str | None) -> str:
    if not title:
        return ""
    s = title.strip().lower()
    s = _PAREN_TAIL.sub("", s).strip()
    earliest = min(
        (i for i in (s.find(sep) for sep in _SUBTITLE_SEPARATORS) if i != -1),
        default=-1,
    )
    if earliest != -1:
        s = s[:earliest]
    while True:
        new = _SEASON_TAIL.sub("", s).strip()
        if new == s:
            break
        s = new
    return s


def _history_buckets(history: list[MediaItem]) -> tuple[set[int], set[int], set[int], dict[str, str]]:
    loved: set[int] = set()
    low: set[int] = set()
    neutral: set[int] = set()
    stems: dict[str, str] = {}
    for item in history:
        if item.id:
            if item.status in LOW_STATUSES or (item.score is not None and item.score <= 5):
                low.add(item.id)
            elif item.score is not None and item.score >= 8:
                loved.add(item.id)
            elif item.score is not None and 6 <= item.score <= 7:
                neutral.add(item.id)
        for title in (item.title_english, item.title_romaji):
            stem = _title_stem(title)
            if stem:
                if item.status in LOW_STATUSES or (item.score is not None and item.score <= 5):
                    stems[stem] = "blocked"
                elif item.score is not None and item.score >= 8:
                    stems[stem] = "boosted"
                elif item.score is not None and 6 <= item.score <= 7:
                    stems.setdefault(stem, "neutral")
    return loved, low, neutral, stems


def _relation_policy(candidate: MediaItem, loved: set[int], low: set[int], neutral: set[int]) -> str:
    related = {rel.media_id for rel in candidate.relations}
    if candidate.id in low or related & low:
        return "blocked"
    if candidate.id in loved or related & loved:
        return "boosted"
    if candidate.id in neutral or related & neutral:
        return "neutral"
    return "neutral"


def _stem_policy(candidate: MediaItem, stems: dict[str, str]) -> str | None:
    for title in (candidate.title_english, candidate.title_romaji):
        stem = _title_stem(title)
        if stem and stem in stems:
            return stems[stem]
    return None


def _sequence_warning(candidate: MediaItem) -> str:
    strict_prequels = [
        rel for rel in candidate.relations if rel.relation_type in STRICT_RELATIONS and rel.status != "FINISHED"
    ]
    if strict_prequels:
        title = strict_prequels[0].title_english or strict_prequels[0].title_romaji
        return f"Sequencing check: verify earlier entry {title} first."
    return ""


def classify_candidate(candidate: MediaItem, history: list[MediaItem]) -> MediaItem:
    loved, low, neutral, stems = _history_buckets(history)
    policy = _relation_policy(candidate, loved, low, neutral)
    stem_policy = _stem_policy(candidate, stems)
    if stem_policy == "blocked":
        policy = "blocked"
    elif stem_policy == "boosted" and policy != "blocked":
        policy = "boosted"

    loose = any(rel.relation_type in LOOSE_RELATIONS for rel in candidate.relations)
    note = ""
    if policy == "blocked":
        note = "Low-rated, dropped, or paused franchise relation."
    elif policy == "boosted":
        note = "Loved franchise continuation."
    return replace(
        candidate,
        franchise_policy=policy,
        franchise_note=note,
        sequence_warning="" if loose else _sequence_warning(candidate),
        loose_order=loose,
    )


def _lane_sort_key(item: MediaItem, lane: str) -> tuple:
    boosted = item.franchise_policy == "boosted"
    finished = item.status == "FINISHED"
    releasing = item.status == "RELEASING"
    year = item.season_year or 0
    score = item.mean_score or 0
    if lane == "new_seasons":
        return (not boosted, not releasing, -year, -score)
    if lane == "hidden_gems":
        return (boosted, -score, year)
    return (not boosted, not finished, releasing, -score)


def apply_lane_policy(history: list[MediaItem], candidates: list[MediaItem], lane: str) -> list[MediaItem]:
    normalized_lane = lane if lane in {"best_match", "new_seasons", "hidden_gems", "plan_list"} else "best_match"
    classified = [classify_candidate(candidate, history) for candidate in candidates]
    if normalized_lane != "plan_list":
        classified = [candidate for candidate in classified if candidate.franchise_policy != "blocked"]
    return sorted(classified, key=lambda item: _lane_sort_key(item, normalized_lane))
```

- [ ] **Step 4: Run task tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_franchise_policy.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/omakase/franchise.py tests/test_franchise_policy.py
git commit -m "feat: add franchise lane policy"
```

---

### Task 3: Enrich AniList Metadata And Apply Lanes

**Files:**
- Modify: `src/omakase/adapters/anilist.py`
- Test: `tests/test_anilist_franchise.py`

- [ ] **Step 1: Write failing adapter tests**

Add tests that instantiate `AniListAdapter`, monkeypatch `_graphql`, and assert parsed fields:

```python
from omakase.adapters.anilist import AniListAdapter


def test_anilist_candidates_parse_rich_relation_metadata(monkeypatch):
    adapter = AniListAdapter()

    def fake_graphql(query, variables):
        assert "seasonYear" in query
        assert "nextAiringEpisode" in query
        assert "relations" in query
        return {
            "data": {
                "Page": {
                    "media": [
                        {
                            "id": 2,
                            "title": {"romaji": "Base 2", "english": "Base 2"},
                            "genres": ["Drama"],
                            "tags": [],
                            "meanScore": 81,
                            "description": "desc",
                            "format": "TV",
                            "status": "RELEASING",
                            "season": "SPRING",
                            "seasonYear": 2026,
                            "startDate": {"year": 2026, "month": 4, "day": 1},
                            "episodes": 12,
                            "nextAiringEpisode": {"episode": 4, "airingAt": 1776200000},
                            "studios": {"nodes": []},
                            "relations": {
                                "edges": [
                                    {
                                        "relationType": "PREQUEL",
                                        "node": {
                                            "id": 1,
                                            "type": "ANIME",
                                            "format": "TV",
                                            "status": "FINISHED",
                                            "episodes": 12,
                                            "season": "WINTER",
                                            "seasonYear": 2025,
                                            "title": {"romaji": "Base", "english": "Base"},
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
            }
        }

    monkeypatch.setattr(adapter, "_graphql", fake_graphql)
    item = adapter._fetch_candidates([], 1)[0]
    assert item.status == "RELEASING"
    assert item.season == "SPRING"
    assert item.season_year == 2026
    assert item.start_date == "2026-04-01"
    assert item.next_airing_episode == 4
    assert item.relations[0].media_id == 1
```

Add a fetch-level lane test:

```python
def test_fetch_new_seasons_keeps_loved_continuations(monkeypatch):
    adapter = AniListAdapter()
    history = [MediaItem(id=1, title_romaji="Base", title_english="Base", score=9, status="COMPLETED")]
    candidates = [
        MediaItem(
            id=2,
            title_romaji="Base 2",
            title_english="Base 2",
            status="RELEASING",
            season_year=2026,
            relations=[MediaRelation(relation_type="PREQUEL", media_id=1, title_romaji="Base")],
        )
    ]
    monkeypatch.setattr(adapter, "_fetch_history", lambda username: history)
    monkeypatch.setattr(adapter, "_analyze_genre_affinity", lambda history: ["Drama"])
    monkeypatch.setattr(adapter, "_fetch_candidates_targeted", lambda exclude_ids, pool_size, genres: candidates)
    data = adapter.fetch("me", 10, recommendation_lane="new_seasons")
    assert data.candidates[0].franchise_policy == "boosted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_anilist_franchise.py -q
```

Expected: failures around missing parsed rich metadata and missing lane keyword behavior.

- [ ] **Step 3: Add rich fields to AniList queries**

In both candidate GraphQL query strings and the Planning query, include:

```graphql
status
season
seasonYear
startDate { year month day }
nextAiringEpisode { episode airingAt }
relations {
  edges {
    relationType
    node {
      id
      type
      format
      status
      episodes
      season
      seasonYear
      title { romaji english }
    }
  }
}
```

Add `status`, `season`, `seasonYear`, `startDate`, and `relations` to the history query too. Keep `score(format: POINT_10)` unchanged.

- [ ] **Step 4: Add parse helpers**

Add these helpers near `_candidate_is_in_franchise`:

```python
from omakase.types import MediaItem, MediaRelation, SourceData


def _date_string(value: dict | None) -> str | None:
    if not value or not value.get("year"):
        return None
    month = value.get("month") or 1
    day = value.get("day") or 1
    return f"{value['year']:04d}-{month:02d}-{day:02d}"


def _parse_relations(media: dict) -> tuple[list[int], list[MediaRelation]]:
    related_ids: list[int] = []
    relations: list[MediaRelation] = []
    for edge in media.get("relations", {}).get("edges", []) or []:
        rtype = edge.get("relationType")
        node = edge.get("node") or {}
        if not rtype or node.get("type") != "ANIME" or not node.get("id"):
            continue
        relation = MediaRelation(
            relation_type=rtype,
            media_id=node["id"],
            title_romaji=node.get("title", {}).get("romaji", ""),
            title_english=node.get("title", {}).get("english"),
            format=node.get("format"),
            status=node.get("status"),
            episodes=node.get("episodes"),
            season=node.get("season"),
            season_year=node.get("seasonYear"),
        )
        relations.append(relation)
        if rtype in FRANCHISE_RELATION_TYPES:
            related_ids.append(node["id"])
    return related_ids, relations
```

When constructing every `MediaItem`, pass:

```python
                        status=media.get("status"),
                        season=media.get("season"),
                        season_year=media.get("seasonYear"),
                        start_date=_date_string(media.get("startDate")),
                        next_airing_episode=(media.get("nextAiringEpisode") or {}).get("episode"),
                        next_airing_at=(media.get("nextAiringEpisode") or {}).get("airingAt"),
```

For candidates, use:

```python
                related_ids, relations = _parse_relations(m)
```

and pass `related_ids=related_ids, relations=relations`.

- [ ] **Step 5: Replace old hard franchise exclusion with lane policy**

At the top of `src/omakase/adapters/anilist.py`, import:

```python
from omakase.franchise import apply_lane_policy
```

In `fetch`, read lane and apply it:

```python
        lane = kwargs.get("recommendation_lane", "best_match")
        if use_planning:
            candidates = self._fetch_planning(username)
            watched_ids = {
                m.id for m in history if m.status in {"CURRENT", "COMPLETED", "DROPPED", "PAUSED"}
            }
            candidates = [c for c in candidates if c.id not in watched_ids]
            candidates = apply_lane_policy(history, candidates, "plan_list")
        else:
            preferred_genres = self._analyze_genre_affinity(history)
            if preferred_genres:
                candidates = self._fetch_candidates_targeted(exclude_ids, pool_size, preferred_genres)
            else:
                candidates = self._fetch_candidates(exclude_ids, pool_size)
            candidates = apply_lane_policy(history, candidates, lane)
```

- [ ] **Step 6: Run adapter tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_anilist_franchise.py tests/test_adapter_planning.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

Run:

```powershell
git add src/omakase/adapters/anilist.py tests/test_anilist_franchise.py
git commit -m "feat: enrich anilist franchise candidates"
```

---

### Task 4: Pass Lane, Policy, Airing, And Feedback Into The Prompt

**Files:**
- Modify: `src/omakase/prompt.py`
- Modify: `src/omakase/engine.py`
- Test: `tests/test_prompt.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write failing prompt tests**

Add:

```python
from omakase.prompt import build_prompt
from omakase.types import MediaItem, RecommendationFeedbackSignal


def test_prompt_includes_lane_policy_airing_and_feedback():
    prompt = build_prompt(
        "I like tender melancholy.",
        [MediaItem(id=1, title_romaji="Base", title_english="Base", score=9, status="COMPLETED")],
        [
            MediaItem(
                id=2,
                title_romaji="Base 2",
                title_english="Base 2",
                status="RELEASING",
                season_year=2026,
                next_airing_episode=4,
                franchise_policy="boosted",
                franchise_note="Loved franchise continuation.",
                sequence_warning="Sequencing check: verify earlier entry first.",
            )
        ],
        n_recs=1,
        lane="new_seasons",
        feedback=[RecommendationFeedbackSignal(media_id=2, title="Base 2", feedback_type="interested")],
    )
    assert "# RECOMMENDATION LANE: New Seasons" in prompt
    assert "Airing: episode 4 released/next" in prompt
    assert "Loved franchise continuation." in prompt
    assert "interested: Base 2" in prompt
    assert '"airing_status"' in prompt
    assert '"franchise_note"' in prompt
    assert '"lane_reason"' in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_prompt.py::test_prompt_includes_lane_policy_airing_and_feedback -q
```

Expected: `build_prompt` rejects `lane` or `feedback`.

- [ ] **Step 3: Extend prompt formatting**

Update function signature:

```python
from omakase.types import MediaItem, RecommendationFeedbackSignal


LANE_LABELS = {
    "best_match": "Best Match",
    "new_seasons": "New Seasons",
    "hidden_gems": "Hidden Gems",
    "plan_list": "Plan List",
}
```

Add helpers:

```python
def _format_feedback(items: list[RecommendationFeedbackSignal]) -> str:
    if not items:
        return "No local feedback yet."
    lines = []
    for item in items[-20:]:
        lines.append(f"  - {item.feedback_type}: {item.title}")
    return "\n".join(lines)


def _airing_line(item: MediaItem) -> str:
    if item.status == "RELEASING":
        if item.next_airing_episode:
            return f"Airing: episode {item.next_airing_episode} released/next"
        return "Airing"
    if item.status == "FINISHED":
        return "Finished"
    return item.status or ""
```

In `_format_candidates`, append metadata:

```python
        airing = _airing_line(m)
        if airing:
            lines.append(f"     Status: {airing}")
        if m.franchise_note:
            lines.append(f"     Franchise: {m.franchise_note}")
        if m.sequence_warning:
            lines.append(f"     Sequence: {m.sequence_warning}")
```

Change `build_prompt` signature:

```python
def build_prompt(
    taste_profile: str,
    history: list[MediaItem],
    candidates: list[MediaItem],
    n_recs: int = 10,
    lane: str = "best_match",
    feedback: list[RecommendationFeedbackSignal] | None = None,
) -> str:
```

Insert before `# USER RATING HISTORY`:

```python
# RECOMMENDATION LANE: {LANE_LABELS.get(lane, "Best Match")}
Use the selected lane to decide tie-breakers. Best Match balances profile fit and franchise continuity. New Seasons strongly considers sensible continuations from loved franchises. Hidden Gems favors less obvious older or lower-visibility picks. Plan List treats the user's planning list as the candidate pool and warns about franchise risk instead of silently dropping picks.

# LOCAL FEEDBACK
{_format_feedback(feedback or [])}
```

In strict rules, replace the blanket no-continuations rule with:

```text
- Do NOT recommend sequels, side stories, movies, OVAs, specials, or spin-offs tied to low-rated, DROPPED, or PAUSED history unless you explicitly explain why the later entry is structurally different.
- Prefer sensible next missing entries in loved franchises when they appear in the candidate pool.
- Label airing shows in the reasoning when airing status affects the pick.
```

In output format, include optional keys:

```text
  {"title": "...", "predicted_score": 0, "reasoning": "...", "best_match_from_history": "...", "anilist_id": 0, "airing_status": "...", "franchise_note": "...", "sequence_warning": "...", "lane_reason": "..."}
```

- [ ] **Step 4: Pass config fields through engine**

In `run`, pass:

```python
    prompt = build_prompt(
        taste_profile,
        data.history,
        data.candidates,
        n_recs=cfg.num_recommendations,
        lane=cfg.recommendation_lane,
        feedback=cfg.feedback,
    )
```

In adapter fetch, pass:

```python
        recommendation_lane=cfg.recommendation_lane,
```

- [ ] **Step 5: Run prompt and engine tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_prompt.py tests/test_engine.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src/omakase/prompt.py src/omakase/engine.py tests/test_prompt.py tests/test_engine.py
git commit -m "feat: prompt recommendation lanes"
```

---

### Task 5: Persist Local Recommendation Feedback

**Files:**
- Add: `src/omakase/plus/migrations/003-recommendation-intelligence.sql`
- Create: `src/omakase/plus/feedback.py`
- Test: `tests/plus/test_schema.py`
- Test: `tests/plus/test_feedback.py`

- [ ] **Step 1: Write failing schema and helper tests**

Add to `tests/plus/test_schema.py`:

```python
def test_recommendation_feedback_roundtrip(_fresh_db):
    conn = _fresh_db
    user_id = conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        ("feedback@example.com", "hash"),
    ).lastrowid
    conn.execute(
        """INSERT INTO recommendation_feedback
           (user_id, source, media_id, title, feedback_type, run_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, "anilist", 123, "Base 2", "wrong_sequel", 7),
    )
    row = conn.execute("SELECT * FROM recommendation_feedback WHERE user_id = ?", (user_id,)).fetchone()
    assert row["feedback_type"] == "wrong_sequel"
```

Create `tests/plus/test_feedback.py`:

```python
from omakase.plus.feedback import feedback_for_prompt, save_feedback
from omakase.plus.db import run_migrations
import sqlite3


def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    run_migrations(conn)
    return conn


def test_save_feedback_and_prompt_summary():
    conn = db()
    user_id = conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        ("user@example.com", "hash"),
    ).lastrowid
    save_feedback(conn, user_id, "anilist", 123, "Base 2", "interested", 4)
    signals = feedback_for_prompt(conn, user_id)
    assert signals[0].media_id == 123
    assert signals[0].feedback_type == "interested"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/plus/test_schema.py::test_recommendation_feedback_roundtrip tests/plus/test_feedback.py -q
```

Expected: missing table and missing module failures.

- [ ] **Step 3: Add migration**

Create `src/omakase/plus/migrations/003-recommendation-intelligence.sql`:

```sql
ALTER TABLE run_history ADD COLUMN lane TEXT NOT NULL DEFAULT 'best_match';

CREATE TABLE IF NOT EXISTS recommendation_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    media_id INTEGER,
    title TEXT NOT NULL,
    feedback_type TEXT NOT NULL CHECK (feedback_type IN ('interested', 'not_for_me', 'wrong_sequel', 'already_watched')),
    run_id INTEGER REFERENCES run_history(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_recommendation_feedback_user_created
ON recommendation_feedback(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_recommendation_feedback_user_media
ON recommendation_feedback(user_id, media_id);
```

- [ ] **Step 4: Add feedback helpers**

Create `src/omakase/plus/feedback.py`:

```python
"""Local recommendation feedback persistence for Omakase Plus."""

from __future__ import annotations

import sqlite3

from omakase.types import RecommendationFeedbackSignal

VALID_FEEDBACK = {"interested", "not_for_me", "wrong_sequel", "already_watched"}


def save_feedback(
    db: sqlite3.Connection,
    user_id: int,
    source: str,
    media_id: int | None,
    title: str,
    feedback_type: str,
    run_id: int | None,
) -> None:
    if feedback_type not in VALID_FEEDBACK:
        raise ValueError(f"Unsupported feedback type: {feedback_type}")
    db.execute(
        """INSERT INTO recommendation_feedback
           (user_id, source, media_id, title, feedback_type, run_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, source, media_id, title, feedback_type, run_id),
    )
    db.commit()


def feedback_for_prompt(db: sqlite3.Connection, user_id: int, limit: int = 30) -> list[RecommendationFeedbackSignal]:
    rows = db.execute(
        """SELECT media_id, title, feedback_type
           FROM recommendation_feedback
           WHERE user_id = ?
           ORDER BY id DESC
           LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    return [
        RecommendationFeedbackSignal(
            media_id=row["media_id"],
            title=row["title"],
            feedback_type=row["feedback_type"],
        )
        for row in rows
    ]
```

- [ ] **Step 5: Run schema and feedback tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/plus/test_schema.py tests/plus/test_feedback.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src/omakase/plus/migrations/003-recommendation-intelligence.sql src/omakase/plus/feedback.py tests/plus/test_schema.py tests/plus/test_feedback.py
git commit -m "feat: store recommendation feedback"
```

---

### Task 6: Wire Lane And Feedback Through Plus Run APIs

**Files:**
- Modify: `src/omakase/plus/routes.py`
- Test: `tests/plus/test_jobs.py`
- Test: `tests/plus/test_dashboard.py`
- Test: `tests/plus/test_feedback.py`

- [ ] **Step 1: Write failing route tests**

Add to `tests/plus/test_jobs.py`:

```python
def test_api_run_passes_lane_and_feedback_to_pipeline(client, monkeypatch):
    seen = {}

    def fake_run(cfg):
        seen["lane"] = cfg.recommendation_lane
        seen["feedback_types"] = [f.feedback_type for f in cfg.feedback]
        return []

    monkeypatch.setattr("omakase.plus.routes.run_pipeline", fake_run)
    monkeypatch.setattr("omakase.plus.routes.read_secret", lambda db, user_id, key: "HeyiTzSenpai" if key == "anilist_username" else "")
    response = client.post(
        "/plus/api/run",
        json={"source": "anilist", "username": "me", "mode": "fast", "count": 3, "lane": "new_seasons"},
    )
    job_id = response.json()["job_id"]
    status = client.get(f"/plus/api/run/status/{job_id}").json()
    assert status["status"] == "ok"
    assert seen["lane"] == "new_seasons"
```

Add to `tests/plus/test_feedback.py`:

```python
def test_feedback_api_saves_row(client):
    response = client.post(
        "/plus/api/feedback",
        json={
            "source": "anilist",
            "media_id": 123,
            "title": "Base 2",
            "feedback_type": "not_for_me",
            "run_id": None,
        },
    )
    assert response.json()["status"] == "ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/plus/test_jobs.py::test_api_run_passes_lane_and_feedback_to_pipeline tests/plus/test_feedback.py::test_feedback_api_saves_row -q
```

Expected: lane not read and `/plus/api/feedback` missing.

- [ ] **Step 3: Import feedback helpers**

In `src/omakase/plus/routes.py`, add:

```python
from omakase.plus.feedback import feedback_for_prompt, save_feedback
```

- [ ] **Step 4: Add lane validation helper**

Add near `_extract_anilist_id`:

```python
_LANES = {"best_match", "new_seasons", "hidden_gems", "plan_list"}


def _normalize_lane(value: str | None) -> str:
    return value if value in _LANES else "best_match"
```

- [ ] **Step 5: Pass lane and feedback into API run**

In `api_run`, read:

```python
    lane = _normalize_lane(body.get("lane"))
    if lane == "plan_list":
        use_planning = True
```

Before building `OmakaseConfig`, read:

```python
    feedback = feedback_for_prompt(db, user.id)
```

Pass:

```python
        recommendation_lane=lane,
        feedback=feedback,
```

When initializing `_jobs[job_id]`, include:

```python
            "lane": lane,
```

In `api_run_status`, read `lane = job["lane"]` and insert:

```python
        "INSERT INTO run_history (user_id, source, model, picks, lane) VALUES (?, ?, ?, ?, ?)",
        (user.id, source, model, picks_json, lane),
```

Return `"lane": lane`.

- [ ] **Step 6: Pass lane and feedback into form route**

In `dashboard_run`, add parameter:

```python
    lane: str = Form("best_match"),
```

Normalize and force planning:

```python
    lane = _normalize_lane(lane)
    if lane == "plan_list":
        use_planning = True
```

Pass `recommendation_lane=lane` and `feedback=feedback_for_prompt(db, user.id)` into `OmakaseConfig`. Insert run history with the `lane` column.

- [ ] **Step 7: Add feedback endpoint**

Add:

```python
@router.post("/api/feedback")
async def feedback_api(
    request: Request,
    db=Depends(get_db),
    user=Depends(require_user),
):
    try:
        body = await request.json()
    except Exception:
        return {"status": "error", "detail": "Invalid JSON body"}
    feedback_type = body.get("feedback_type", "")
    title = body.get("title", "")
    source = body.get("source", "anilist")
    media_id = body.get("media_id")
    run_id = body.get("run_id")
    if not title:
        return {"status": "error", "detail": "title is required"}
    try:
        save_feedback(db, user.id, source, media_id, title, feedback_type, run_id)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    return {"status": "ok"}
```

- [ ] **Step 8: Run route tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/plus/test_jobs.py tests/plus/test_dashboard.py tests/plus/test_feedback.py -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

Run:

```powershell
git add src/omakase/plus/routes.py tests/plus/test_jobs.py tests/plus/test_dashboard.py tests/plus/test_feedback.py
git commit -m "feat: wire plus lanes and feedback"
```

---

### Task 7: Update Dashboard Controls, Cards, And Actions

**Files:**
- Modify: `src/omakase/plus/templates/dashboard.html`
- Modify: `src/omakase/plus/routes.py`
- Test: `tests/plus/test_dashboard.py`

- [ ] **Step 1: Write failing dashboard tests**

Add tests:

```python
def test_dashboard_renders_lane_control(client):
    html = client.get("/plus/dashboard").text
    assert 'name="lane"' in html
    assert 'value="best_match"' in html
    assert 'value="new_seasons"' in html
    assert 'value="hidden_gems"' in html
    assert 'value="plan_list"' in html


def test_dashboard_cards_have_split_plan_and_download_actions(client):
    html = client.get("/plus/dashboard?run=1").text
    assert "Plan &amp; Download" not in html
    assert ">Plan<" in html
    assert ">Download<" in html


def test_dashboard_cards_have_feedback_buttons(client):
    html = client.get("/plus/dashboard?run=1").text
    assert 'data-feedback="interested"' in html
    assert 'data-feedback="not_for_me"' in html
    assert 'data-feedback="wrong_sequel"' in html
    assert 'data-feedback="already_watched"' in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/plus/test_dashboard.py::test_dashboard_renders_lane_control tests/plus/test_dashboard.py::test_dashboard_cards_have_split_plan_and_download_actions tests/plus/test_dashboard.py::test_dashboard_cards_have_feedback_buttons -q
```

Expected: missing lane inputs and old combined action text.

- [ ] **Step 3: Render run lane in dashboard context**

In `dashboard`, select `lane` from `run_history` for current and recent runs:

```sql
SELECT id, source, model, picks, lane, created_at
```

Set `current_run_lane = run_row["lane"] if "lane" in run_row.keys() else "best_match"` and pass `"current_run_lane": current_run_lane` to the template.

- [ ] **Step 4: Add segmented lane control**

In `dashboard.html`, before the source row inside `#run-form`, add:

```html
<div class="field">
  <label>Lane</label>
  <div class="lane-control" role="radiogroup" aria-label="Recommendation lane">
    <label><input type="radio" name="lane" value="best_match" checked>Best Match</label>
    <label><input type="radio" name="lane" value="new_seasons">New Seasons</label>
    <label><input type="radio" name="lane" value="hidden_gems">Hidden Gems</label>
    <label><input type="radio" name="lane" value="plan_list">Plan List</label>
  </div>
</div>
```

Add CSS:

```css
.lane-control{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:0.35rem}
.lane-control label{display:flex;align-items:center;justify-content:center;min-height:38px;padding:0.45rem 0.55rem;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text2);font-size:0.78rem;cursor:pointer;text-align:center}
.lane-control input{position:absolute;opacity:0;pointer-events:none}
.lane-control label:has(input:checked){border-color:var(--purple);background:rgba(124,58,237,0.16);color:var(--text)}
@media (max-width:640px){.lane-control{grid-template-columns:repeat(2,minmax(0,1fr))}}
.rec-title-link{color:var(--text);text-decoration:none}
.rec-title-link:hover{color:var(--purple-glow)}
.rec-chips{display:flex;gap:0.35rem;flex-wrap:wrap;margin:0.35rem 0 0.55rem}
.feedback-row{margin-top:0.55rem;display:flex;gap:0.35rem;flex-wrap:wrap}
```

- [ ] **Step 5: Include lane in run JavaScript**

In the body JSON, add:

```javascript
    lane: document.querySelector('input[name="lane"]:checked').value,
```

If lane is `plan_list`, set `use_planning` true before stringify:

```javascript
  const lane = document.querySelector('input[name="lane"]:checked').value;
```

Use `lane` in the JSON and `use_planning: lane === 'plan_list' || document.getElementById('run-planning').checked`.

- [ ] **Step 6: Render clickable titles and chips**

In current-run card title:

```html
{% if pick.url %}
<a class="title rec-title-link" href="{{ pick.url }}" target="_blank" rel="noopener noreferrer">{{ pick.title }}</a>
{% else %}
<div class="title">{{ pick.title }}</div>
{% endif %}
```

Under the top row:

```html
<div class="rec-chips">
  {% if pick.airing_status == 'RELEASING' %}<span class="badge badge-yellow">Airing</span>{% endif %}
  {% if pick.airing_status == 'FINISHED' %}<span class="badge badge-green">Finished</span>{% endif %}
  {% if pick.franchise_note %}<span class="badge badge-purple">{{ pick.franchise_note }}</span>{% endif %}
  {% if pick.sequence_warning %}<span class="badge badge-yellow">Sequencing check</span>{% endif %}
</div>
```

- [ ] **Step 7: Split Plan and Download actions**

Replace the combined form with:

```html
<form method="post" action="/plus/dashboard/plan" style="display:inline">
  <input type="hidden" name="anilist_id" value="{{ aid }}">
  <input type="hidden" name="title" value="{{ pick.title }}">
  <button type="submit" class="btn btn-primary btn-small">Plan</button>
</form>
<form method="post" action="/plus/dashboard/download" style="display:inline">
  <input type="hidden" name="anilist_id" value="{{ aid }}">
  <input type="hidden" name="title" value="{{ pick.title }}">
  <button type="submit" class="btn btn-secondary btn-small">Download</button>
</form>
```

Rename the form route `dashboard_plan_and_download` path from `"/dashboard/plan-and-download"` to `"/dashboard/download"` and remove the AniList planning insert block from that function. The download route should create a local planning row only when the row is missing so `download_status` has a target row; it must not call `add_to_planning`.

- [ ] **Step 8: Add feedback controls**

Inside `.rec-actions`, add:

```html
<div class="feedback-row" data-title="{{ pick.title }}" data-media-id="{{ pick.anilist_id or pick.media_id or '' }}" data-source="{{ current_run_source }}" data-run-id="{{ current_run_id }}">
  <button type="button" class="btn btn-ghost feedback-btn" data-feedback="interested">Interested</button>
  <button type="button" class="btn btn-ghost feedback-btn" data-feedback="not_for_me">Not for me</button>
  <button type="button" class="btn btn-ghost feedback-btn" data-feedback="wrong_sequel">Wrong sequel</button>
  <button type="button" class="btn btn-ghost feedback-btn" data-feedback="already_watched">Already watched</button>
</div>
```

Add JavaScript:

```javascript
document.addEventListener('click', async (event) => {
  const btn = event.target.closest('.feedback-btn');
  if (!btn) return;
  const row = btn.closest('.feedback-row');
  const mediaId = row.dataset.mediaId ? parseInt(row.dataset.mediaId, 10) : null;
  const payload = {
    source: row.dataset.source || 'anilist',
    media_id: mediaId,
    title: row.dataset.title,
    feedback_type: btn.dataset.feedback,
    run_id: row.dataset.runId ? parseInt(row.dataset.runId, 10) : null,
  };
  const resp = await fetch('/plus/api/feedback', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload),
  });
  const data = await resp.json();
  if (data.status === 'ok') {
    btn.textContent = 'Saved';
    btn.disabled = true;
  }
});
```

- [ ] **Step 9: Run dashboard tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/plus/test_dashboard.py -q
```

Expected: all dashboard tests pass.

- [ ] **Step 10: Commit**

Run:

```powershell
git add src/omakase/plus/templates/dashboard.html src/omakase/plus/routes.py tests/plus/test_dashboard.py
git commit -m "feat: update plus recommendation dashboard"
```

---

### Task 8: Full Verification, Browser Smoke, And Documentation

**Files:**
- Modify: `C:\Users\qazws\Nextcloud2\Homelab Vault\Projects\omakase\README.md`
- Modify: `C:\Users\qazws\Nextcloud2\Homelab Vault\Projects\omakase\history.md`
- Modify if present: `C:\Users\qazws\Nextcloud2\Homelab Vault\Memory\project_omakase.md`
- Modify if present: `C:\Users\qazws\Nextcloud2\Homelab Vault\MEMORY.md`
- Modify: `C:\Users\qazws\Nextcloud2\Homelab Vault\Agent-Sessions\omakase\01-plus-mvp-me-only.md`

- [ ] **Step 1: Run unit and lint verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Expected: pytest passes, ruff check clean, ruff format clean.

- [ ] **Step 2: Start local Plus preview**

Run in a background terminal:

```powershell
$env:OMAKASE_PLUS_PRIVATE='true'
$env:OMAKASE_PLUS_MASTER_KEY='dev-only-32-byte-master-key-for-local'
$env:OMAKASE_PORT='8765'
.\.venv\Scripts\python.exe -m omakase.cli web
```

Expected: Plus login is reachable at `http://127.0.0.1:8765/plus/login`.

- [ ] **Step 3: Browser smoke the dashboard**

Use the in-app Browser against `http://127.0.0.1:8765/plus/login`. Verify:

- The lane control is visible and wraps cleanly at desktop and mobile widths.
- `Plan List` sets the plan-list behavior for a run.
- A rendered card title opens the AniList URL in a new tab for AniList runs.
- `Plan` and `Download` are separate buttons.
- Clicking `Plan` does not trigger Real-Debrid download state.
- Feedback buttons save and visibly disable or show `Saved`.
- `Airing`, `Finished`, loved-franchise, and sequencing chips do not overlap card text.

- [ ] **Step 4: Safe real-target smoke**

Against `https://anime.jhinx.dev/plus`, after user login is available:

```text
Run one Fast Best Match recommendation with Count 3.
Run one New Seasons recommendation with Count 3.
Open one clickable AniList card.
Click Interested on one card.
Click Plan on one safe pick.
Do not click Download unless the user explicitly approves a Real-Debrid request for that title.
```

Expected: the live dashboard renders real picks and the safe action results are visible. If login or BYOK spend blocks this, record the block and do not call the feature shipped.

- [ ] **Step 5: Update vault docs**

Append a dated entry to `Projects/omakase/README.md` and `Projects/omakase/history.md` with:

```markdown
## 2026-06-21 — Recommendation intelligence implementation

- Built lane-aware Omakase Plus recommendation intelligence from `docs/superpowers/specs/2026-06-21-plus-recommendation-intelligence-design.md`.
- Added franchise policy for loved continuations, low-rated/dropped/paused blocks, Plan List warnings, airing labels, clickable cards, split Plan/Download, and local feedback.
- Verification: `<paste exact pytest count>`, Ruff check clean, Ruff format clean, local browser dashboard smoke at `http://127.0.0.1:8765/plus`, and `<paste live smoke result or user-gated carry>`.
- Carry: `<state any real-target verification that remains>`.
```

Update the agent session brief Outcome section with the same verification language. Keep status 🟡 if live BYOK or Real-Debrid verification remains user-gated.

- [ ] **Step 6: Commit docs**

Run:

```powershell
git add "C:\Users\qazws\Nextcloud2\Homelab Vault\Projects\omakase\README.md" "C:\Users\qazws\Nextcloud2\Homelab Vault\Projects\omakase\history.md" "C:\Users\qazws\Nextcloud2\Homelab Vault\Agent-Sessions\omakase\01-plus-mvp-me-only.md"
git commit -m "docs: record plus recommendation intelligence"
```

- [ ] **Step 7: Discord notification**

If real-target verification is complete, run:

```powershell
& C:\Users\qazws\.codex\scripts\notify-discord.ps1 `
  -Status finished `
  -Project "omakase" `
  -Session "plus-recommendation-intelligence" `
  -Summary "Omakase Plus recommendation intelligence landed with lanes, franchise policy, split actions, and feedback." `
  -Verified "Pytest + Ruff clean; local browser smoke; live anime.jhinx.dev Plus smoke completed." `
  -Next "Phase 7 public-site cutover remains a separate explicit-approval gate." `
  -RepoPath "C:\Users\qazws\Projects\omakase"
```

If live BYOK or Real-Debrid verification remains user-gated, use `-Status partial` and set `-Verified` to the exact automated and local-browser evidence gathered.

---

## Self-Review

- Spec coverage: Tasks cover candidate metadata, franchise boost/block/warn policy, next-entry/sequencing warnings, airing preference, four recommendation lanes, prompt changes, feedback storage, clickable cards, split Plan/Download, status chips, dashboard feedback controls, unit tests, browser verification, and real-target verification.
- Scope: The plan does not perform a public `omakase.jhinx.dev` cutover and does not replace the server-rendered dashboard with an SPA.
- Type consistency: `recommendation_lane` is the config field, `lane` is the HTTP/form field, and persisted run rows use `run_history.lane`.
- Migration safety: New DB changes are in `003-recommendation-intelligence.sql`; the existing duplicate `002-*` files are left untouched.
