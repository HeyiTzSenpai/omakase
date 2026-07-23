"""Orchestrator: runs the full recommendation pipeline."""

from __future__ import annotations

import json
import sys
from urllib.parse import quote_plus

from omakase.adapters.base import get_adapter
from omakase.llm import get_llm
from omakase.prompt import build_prompt
from omakase.types import MediaItem, OmakaseConfig, Recommendation, SourceData


class EmptyHistoryError(Exception):
    """The source adapter returned no history for this username.

    Raised when the username is wrong / the list is empty / the list is private —
    cases the visitor can fix by changing input, not retrying.
    """


def _parse_recommendations(raw: str) -> list[Recommendation]:
    """Parse LLM JSON output into Recommendation objects. Graceful on failure."""
    cleaned = raw.strip()

    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        end = cleaned.find("\n")
        if end != -1:
            cleaned = cleaned[end + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].rstrip()

    # Try to find a JSON object in the response
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        if start != -1:
            cleaned = cleaned[start:]
    if not cleaned.endswith("}"):
        end = cleaned.rfind("}")
        if end != -1:
            cleaned = cleaned[: end + 1]

    try:
        data = json.loads(cleaned)
        recs = data.get("recommendations", [])
        return [
            Recommendation(
                title=r.get("title", "Unknown"),
                predicted_score=float(r.get("predicted_score", 0)),
                reasoning=r.get("reasoning", ""),
                best_match_from_history=r.get("best_match_from_history", ""),
            )
            for r in recs
        ]
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        print(f"[!] Failed to parse LLM output: {e}", file=sys.stderr)
        print(f"Raw output: {raw[:500]}", file=sys.stderr)
        return []


def _resolve_rec_urls(
    recs: list[Recommendation],
    candidates: list[MediaItem],
    source_name: str,
) -> None:
    """Set each Recommendation's `url` + `source` so the UI can link out.

    Strategy: match the LLM-returned title against the candidate pool
    (case-insensitive, against both English and romaji titles). On hit,
    build a permalink with the candidate's source-native ID. On miss,
    fall back to a source-specific search URL — still useful, never broken.
    """
    if not recs:
        return

    # Build a lookup keyed on normalized titles. Includes both english
    # and romaji titles to maximize hit rate — LLMs return either form.
    lookup: dict[str, MediaItem] = {}
    for c in candidates:
        for t in (c.title_english, c.title_romaji):
            if t:
                lookup.setdefault(t.strip().lower(), c)

    def permalink(item: MediaItem) -> str | None:
        if source_name == "anilist":
            return f"https://anilist.co/anime/{item.id}/"
        if source_name == "myanimelist":
            return f"https://myanimelist.net/anime/{item.id}/"
        return None

    def search_url(title: str) -> str:
        q = quote_plus(title.strip())
        if source_name == "myanimelist":
            return f"https://myanimelist.net/anime.php?q={q}&cat=anime"
        # Default to AniList search — covers the "anilist" name and any
        # future sources that don't ship a dedicated search URL.
        return f"https://anilist.co/search/anime?search={q}"

    for r in recs:
        r.source = source_name
        match = lookup.get(r.title.strip().lower())
        link = permalink(match) if match else None
        r.url = link or search_url(r.title)


def _load_taste_profile(path: str) -> str:
    """Load taste profile from a markdown file.

    An empty `path` means "no profile" — caller has opted into broader,
    score-only recommendations. Returns an empty string in that case so
    the prompt builder takes its no-profile branch.
    """
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"[!] Taste profile not found at: {path}", file=sys.stderr)
        print("   Create one or use --profile to point to your profile file.", file=sys.stderr)
        sys.exit(1)


def run(cfg: OmakaseConfig) -> list[Recommendation]:
    """Run the full recommendation pipeline and return recommendations."""
    # 1. Fetch data from source
    src_desc = (
        f"uploaded export ({len(cfg.export_data)} bytes)"
        if cfg.export_data
        else f"'{cfg.username}'"
    )
    print(f"  [1/5] Fetching data from {cfg.source} for {src_desc}...")
    adapter = get_adapter(cfg.source)
    data: SourceData = adapter.fetch(
        cfg.username,
        cfg.candidate_pool_size,
        use_planning=cfg.use_planning,
        export_data=cfg.export_data,
        mal_client_id=cfg.mal_client_id,
    )

    if not data.history:
        if cfg.export_data:
            raise EmptyHistoryError(
                "The uploaded export had no scored anime entries. "
                "Make sure you exported the Anime list (not Manga) and that it "
                "contains rated titles."
            )
        raise EmptyHistoryError(
            f"No anime history found for '{cfg.username}' on {cfg.source}. "
            "Double-check the username (it's case-sensitive on some sources) "
            "and make sure the list is public."
        )
    if not data.candidates:
        if cfg.use_planning:
            raise EmptyHistoryError(
                f"Your {cfg.source} Plan-to-Watch list is empty. "
                "Add some titles to it, or uncheck 'Recommend from my Plan to Watch'."
            )
        raise EmptyHistoryError(
            "Couldn't find candidate anime to recommend from. "
            "This usually means the source is down — try again in a moment."
        )

    loved = sum(1 for m in data.history if m.score and m.score >= 9)
    liked = sum(1 for m in data.history if m.score and 7 <= m.score < 9)
    print(f"        History: {len(data.history)} entries ({loved} loved, {liked} liked)")
    source_label = "plan-to-watch" if cfg.use_planning else "popular"
    print(f"        Candidates: {len(data.candidates)} ({source_label})")

    # 2. Load taste profile (may be intentionally empty for broader recs)
    if cfg.taste_profile is not None:
        print("  [2/5] Using request-local taste profile...")
        taste_profile = cfg.taste_profile.strip()
    elif cfg.profile_path:
        print(f"  [2/5] Loading taste profile from {cfg.profile_path}...")
        taste_profile = _load_taste_profile(cfg.profile_path)
    else:
        print("  [2/5] Skipping taste profile (broader recs from scores alone)...")
        taste_profile = ""
    if taste_profile:
        print(f"        Profile: {len(taste_profile.split())} words")
    else:
        print("        Profile: (none — inferring taste from scoring history)")

    # 3. Build prompt
    print(f"  [3/5] Building prompt for {cfg.model}...")
    prompt = build_prompt(taste_profile, data.history, data.candidates)
    print(f"        Prompt: ~{len(prompt.split())} tokens")

    # 4. Send to LLM
    mode_label = "PRO" if cfg.mode == "pro" else "fast"
    print(f"  [4/5] Querying LLM ({cfg.llm_type}: {cfg.model}) [{mode_label}]...")
    llm = get_llm(cfg.llm_type, cfg.llm_url, cfg.model, api_key=cfg.api_key)
    raw = llm.generate(
        prompt,
        temperature=cfg.temperature,
        num_ctx=cfg.num_ctx,
        supports_json=cfg.supports_json_mode,
    )

    # 5. Parse
    print("  [5/5] Parsing response...")
    recs = _parse_recommendations(raw)
    _resolve_rec_urls(recs, data.candidates, data.source_name)
    print(f"        Got {len(recs)} recommendations\n")
    return recs
