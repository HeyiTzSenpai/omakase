"""Build the LLM prompt from anime history + candidate pool + taste profile."""

from __future__ import annotations

from omakase.types import MediaItem

DROPPED_STATUSES = {"DROPPED", "PAUSED"}


def _format_history(items: list[MediaItem]) -> str:
    """Group scored history by bucket; filter out dropped/paused items."""
    active = [m for m in items if m.status not in DROPPED_STATUSES]
    dropped = [m for m in items if m.status in DROPPED_STATUSES]

    buckets = {"9-10 (Loved)": [], "7-8 (Liked)": [], "4-6 (Mid)": [], "1-3 (Bounced)": [], "Unscored": []}
    for m in active:
        s = m.score
        if s is None:
            key = "Unscored"
        elif s >= 9:
            key = "9-10 (Loved)"
        elif s >= 7:
            key = "7-8 (Liked)"
        elif s >= 4:
            key = "4-6 (Mid)"
        else:
            key = "1-3 (Bounced)"
        buckets[key].append(m)

    lines = []
    for bucket_name in ["9-10 (Loved)", "7-8 (Liked)", "4-6 (Mid)", "1-3 (Bounced)", "Unscored"]:
        group = buckets[bucket_name]
        if not group:
            continue
        lines.append(f"\n## {bucket_name} ({len(group)} titles)")
        for m in group:
            title = m.title_english or m.title_romaji
            genres = ", ".join(m.genres[:3]) if m.genres else ""
            tag_str = ", ".join(m.tags[:3]) if m.tags else ""
            extras = []
            if m.studio:
                extras.append(m.studio)
            if m.format and m.format != "TV":
                extras.append(m.format)
            extra_str = f" ({'; '.join(extras)})" if extras else ""
            lines.append(f"  - {title}: {m.score or '?'}/10 — {genres}{extra_str}")
            if tag_str:
                lines.append(f"    Tags: {tag_str}")

    if dropped:
        lines.append(f"\n## DROPPED OR PAUSED ({len(dropped)} titles — do NOT recommend these)")
        for m in dropped:
            title = m.title_english or m.title_romaji
            genres = ", ".join(m.genres[:3]) if m.genres else ""
            reason = f", {m.status}"
            lines.append(f"  - {title} — {genres}{reason}")

    return "\n".join(lines)


def _format_candidates(items: list[MediaItem]) -> str:
    """Format candidate pool for the prompt, one per line."""
    lines = []
    for i, m in enumerate(items, 1):
        title = m.title_english or m.title_romaji
        genres = ", ".join(m.genres[:3]) if m.genres else ""
        tag_str = ", ".join(m.tags[:3]) if m.tags else ""
        desc = m.description or ""
        lines.append(f"  {i}. {title}")
        if genres:
            lines.append(f"     Genres: {genres}")
        if tag_str:
            lines.append(f"     Tags: {tag_str}")
        if desc:
            lines.append(f"     About: {desc[:150]}")
    return "\n".join(lines)


def build_prompt(
    taste_profile: str,
    history: list[MediaItem],
    candidates: list[MediaItem],
) -> str:
    """Assemble the full LLM prompt."""
    n_recs = min(len(candidates), 10)

    prompt = f"""You are a recommendation engine for one user. Your task is to deeply understand their personal taste — both from what they say about themselves and from how they've scored anime — then recommend {n_recs} anime they'll love.

# THE USER'S OWN TASTE PROFILE
{taste_profile}

Read the profile above carefully. This is the user describing what they love and hate in their own words. Treat this as the primary signal about their taste.

# USER RATING HISTORY ({len(history)} titles)
Every anime this user has scored or interacted with. The scores are their honest ratings.
{_format_history(history)}

# CANDIDATE POOL ({len(candidates)} anime the user has NOT seen)
{_format_candidates(candidates)}

# HOW TO RECOMMEND

1. Start by analyzing what makes this user tick — combine their written taste profile with their actual scoring patterns. Which genres, studios, formats, and story traits consistently earn 8+? Which ones get low scores?
2. Identify what their highly-rated entries have in common that their low-rated entries lack.
3. From the candidate pool, pick the {n_recs} best matches. Be critical — a 7 is a solid recommendation, not everything needs to be a 10.

For each recommendation provide:
- title (use English if available, else romaji)
- predicted_score (1-10 — be honest, don't inflate)
- reasoning (2-3 sentences tying it to SPECIFIC anime they've scored AND to their written taste profile)
- best_match_from_history (one title from their history this most resembles)

# STRICT RULES
- Do NOT recommend any anime the user has already seen or that appears in their history
- Do NOT recommend continuations, sequels, second seasons, or spin-offs of anything in the user's history
- Do NOT recommend anything in the "DROPPED OR PAUSED" section
- Do NOT recommend anime that share an exact title match with anything in the user's history (watch for sequel series)
- Only recommend from the candidate pool — do not invent titles
- Do not output anything outside the JSON object

# OUTPUT FORMAT (strict JSON only — no prose, no markdown, no code fences)
{{"recommendations": [
  {{"title": "...", "predicted_score": 0, "reasoning": "...", "best_match_from_history": "..."}}
]}}"""
    return prompt
