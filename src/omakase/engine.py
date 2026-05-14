"""Orchestrator: runs the full recommendation pipeline."""

from __future__ import annotations

import json
import sys

from omakase.adapters.base import get_adapter
from omakase.llm import get_llm
from omakase.prompt import build_prompt
from omakase.types import OmakaseConfig, Recommendation, SourceData


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


def _load_taste_profile(path: str) -> str:
    """Load taste profile from a markdown file."""
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
    print(f"  [1/5] Fetching data from {cfg.source} for '{cfg.username}'...")
    adapter = get_adapter(cfg.source)
    data: SourceData = adapter.fetch(
        cfg.username, cfg.candidate_pool_size, use_planning=cfg.use_planning
    )

    if not data.history:
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

    # 2. Load taste profile
    print(f"  [2/5] Loading taste profile from {cfg.profile_path}...")
    taste_profile = _load_taste_profile(cfg.profile_path)
    print(f"        Profile: {len(taste_profile.split())} words")

    # 3. Build prompt
    print(f"  [3/5] Building prompt for {cfg.model}...")
    prompt = build_prompt(taste_profile, data.history, data.candidates)
    print(f"        Prompt: ~{len(prompt.split())} tokens")

    # 4. Send to LLM
    mode_label = "PRO" if cfg.mode == "pro" else "fast"
    print(f"  [4/5] Querying LLM ({cfg.llm_type}: {cfg.model}) [{mode_label}]...")
    llm = get_llm(cfg.llm_type, cfg.llm_url, cfg.model)
    raw = llm.generate(
        prompt,
        temperature=cfg.temperature,
        num_ctx=cfg.num_ctx,
        supports_json=cfg.supports_json_mode,
    )

    # 5. Parse
    print("  [5/5] Parsing response...")
    recs = _parse_recommendations(raw)
    print(f"        Got {len(recs)} recommendations\n")
    return recs
