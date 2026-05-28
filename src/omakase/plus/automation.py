"""Orchestrator tying AniList planning to Overseerr media requests."""

from __future__ import annotations

import os

from omakase.plus.overseerr import OverseerrClient
from omakase.plus.secrets import read_secret


def _find_best_match(results: list[dict], title: str) -> dict | None:
    """Find the best matching result from an Overseerr search.

    Preference order:
    1. Only considers results with ``mediaType == "tv"``
    2. Exact title match (case-insensitive) among TV results
    3. Highest token overlap among TV results
    4. Fallback: first TV result

    Returns ``None`` if no TV-type results exist.
    """
    tv_results = [r for r in results if r.get("mediaType") == "tv"]
    if not tv_results:
        return None

    title_lower = title.lower()
    title_tokens = set(title_lower.split())

    # Exact title match
    for r in tv_results:
        result_title = (r.get("title") or r.get("name") or "").lower()
        if result_title == title_lower:
            return r

    # Token overlap scoring
    best_score = 0.0
    best_result = tv_results[0]
    for r in tv_results:
        result_title = (r.get("title") or r.get("name") or "").lower()
        result_tokens = set(result_title.split())
        if title_tokens and result_tokens:
            overlap = len(title_tokens & result_tokens) / len(title_tokens)
            if overlap > best_score:
                best_score = overlap
                best_result = r

    return best_result


def trigger_request_after_plan(
    db,
    user_id: int,
    anilist_planning_id: int,
    title: str,
) -> str:
    """Search Overseerr for *title* and submit a request if found.

    The *anilist_planning_id* is the primary key from ``anilist_plannings``,
    **not** the AniList media ID.

    Returns one of: ``"requested"``, ``"not_found"``, ``"error"``.

    Side-effect: inserts a row into ``overseerr_requests`` with the
    appropriate status so the user can track it on the dashboard.
    """
    # Read per-user secrets, falling back to env vars
    overseerr_url = read_secret(db, user_id, "overseerr_url") or os.getenv(
        "OVERSEERR_URL", "http://overseerr.lab:5055"
    )
    overseerr_api_key = read_secret(db, user_id, "overseerr_api_key") or os.getenv(
        "OVERSEERR_API_KEY", ""
    )

    if not overseerr_api_key:
        db.execute(
            """INSERT INTO overseerr_requests
               (user_id, anilist_planning_id, status)
               VALUES (?, ?, 'error')""",
            (user_id, anilist_planning_id),
        )
        db.commit()
        return "error"

    client = OverseerrClient(overseerr_url, overseerr_api_key)

    try:
        results = client.search(title)
        match = _find_best_match(results, title)

        if match is None:
            db.execute(
                """INSERT INTO overseerr_requests
                   (user_id, anilist_planning_id, status)
                   VALUES (?, ?, 'not_found')""",
                (user_id, anilist_planning_id),
            )
            db.commit()
            return "not_found"

        # Found a match — submit the request
        media_id = match["id"]
        media_type = match.get("mediaType", "tv")
        request_result = client.request_media(media_id, media_type)
        overseerr_request_id = request_result.get("id")

        db.execute(
            """INSERT INTO overseerr_requests
               (user_id, anilist_planning_id, overseerr_request_id, status)
               VALUES (?, ?, ?, 'requested')""",
            (user_id, anilist_planning_id, overseerr_request_id),
        )
        db.commit()
        return "requested"

    except Exception:
        db.execute(
            """INSERT INTO overseerr_requests
               (user_id, anilist_planning_id, status)
               VALUES (?, ?, 'error')""",
            (user_id, anilist_planning_id),
        )
        db.commit()
        return "error"
