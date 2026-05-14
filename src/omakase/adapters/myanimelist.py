"""MyAnimeList adapter — fetches anime watch history and candidate pool.

Uses the MAL API v2 (requires MAL_CLIENT_ID env var) for user data,
and Jikan v4 REST API for the candidate pool.

To get a MAL Client ID:
  1. Go to https://myanimelist.net/apiconfig
  2. Register a new "Web" application (any name/URL works)
  3. Copy the Client ID
  4. Set it:  $env:MAL_CLIENT_ID="your_client_id"
"""
from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from omakase.adapters.base import SourceAdapter, register
from omakase.types import MediaItem, SourceData

USER_AGENT = "Omakase/0.1 (homelab; +https://github.com/HeyiTzSenpai/omakase)"

MAL_API = "https://api.myanimelist.net/v2"
JIKAN_API = "https://api.jikan.moe/v4"


def _get_mal_client_id() -> str:
    cid = os.environ.get("MAL_CLIENT_ID", "")
    if not cid:
        raise ValueError(
            "MAL_CLIENT_ID env var is not set. "
            "Get one at https://myanimelist.net/apiconfig, then:\n"
            "  $env:MAL_CLIENT_ID='your_client_id'"
        )
    return cid


def _mal_headers() -> dict[str, str]:
    return {
        "X-MAL-Client-ID": _get_mal_client_id(),
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


def _jikan_fetch(endpoint: str) -> dict:
    req = Request(f"{JIKAN_API}{endpoint}", headers={"User-Agent": USER_AGENT})
    with urlopen(req) as resp:
        return json.loads(resp.read().decode())


def _mal_fetch(endpoint: str) -> dict:
    req = Request(f"{MAL_API}{endpoint}", headers=_mal_headers())
    with urlopen(req) as resp:
        return json.loads(resp.read().decode())


def _parse_media(m: dict, source: str = "jikan") -> MediaItem:
    """Parse an anime dict from either Jikan or MAL API into a MediaItem."""
    if source == "mal":
        mid = m.get("id", 0)
        title = m.get("title", "")
        title_en = m.get("alternative_titles", {}).get("en", "") or title
        genres = [g.get("name", "") for g in m.get("genres", []) if g.get("name")]
        studios_list = m.get("studios", [])
        studio = studios_list[0].get("name", "") if studios_list else None
        desc = m.get("synopsis", "") or ""
        fmt = m.get("media_type", "")
        episodes = m.get("num_episodes")
        mean_score = m.get("mean")
    else:
        mid = m.get("mal_id", 0)
        title = m.get("title", "")
        title_en = m.get("title_english") or title
        genres = [g.get("name", "") for g in m.get("genres", []) if g.get("name")]
        studios_list = m.get("studios", [])
        studio = studios_list[0].get("name", "") if studios_list else None
        desc = m.get("synopsis", "") or ""
        fmt = m.get("type", "")
        episodes = m.get("episodes")
        mean_score = m.get("score")

    if desc and len(desc) > 200:
        desc = desc[:200] + "..."
    return MediaItem(
        id=mid,
        title_romaji=title,
        title_english=title_en,
        genres=genres,
        tags=[],
        format=fmt,
        episodes=episodes,
        studio=studio,
        description=desc,
        mean_score=float(mean_score) if mean_score else None,
    )


@register("myanimelist")
class MALAdapter(SourceAdapter):
    name = "myanimelist"

    def _fetch_history(self, username: str) -> list[MediaItem]:
        """Fetch all user anime list entries from MAL API v2."""
        statuses = ["watching", "completed", "on_hold", "dropped", "plan_to_watch"]
        items: list[MediaItem] = []
        seen_ids: set[int] = set()
        # Request available fields; some may be omitted if not authorized
        fields = "genres,studios,synopsis,media_type,num_episodes,mean"

        for status in statuses:
            offset = 0
            limit = 100
            while True:
                try:
                    data = _mal_fetch(
                        f"/users/{username}/animelist?"
                        f"fields={fields}&limit={limit}&offset={offset}"
                        f"&status={status}"
                    )
                except HTTPError as e:
                    if e.code == 404:
                        return items  # user not found or no list
                    raise

                entries = data.get("data", [])
                if not entries:
                    break

                for entry in entries:
                    node = entry.get("node", {})
                    mid = node.get("id")
                    if not mid or mid in seen_ids:
                        continue
                    seen_ids.add(mid)

                    list_status = entry.get("list_status", {})
                    user_score = list_status.get("score")
                    user_status = {
                        "watching": "CURRENT",
                        "completed": "COMPLETED",
                        "on_hold": "PAUSED",
                        "dropped": "DROPPED",
                        "plan_to_watch": "PLANNING",
                    }.get(status, status.upper())

                    mi = _parse_media(node, source="mal")
                    mi.score = float(user_score) if user_score else None
                    mi.status = user_status
                    items.append(mi)

                paging = data.get("paging", {})
                if not paging.get("next"):
                    break
                offset += limit
                time.sleep(0.35)

        return items

    def _fetch_candidates_jikan(self, exclude_ids: list[int], pool_size: int) -> list[MediaItem]:
        """Fetch top anime from Jikan, excluding watched IDs."""
        items: list[MediaItem] = []
        page = 1
        while len(items) < pool_size:
            try:
                data = _jikan_fetch(f"/top/anime?page={page}&limit=25")
            except HTTPError:
                break
            media_list = data.get("data", [])
            if not media_list:
                break
            for entry in media_list:
                mid = entry.get("mal_id")
                if mid and mid in exclude_ids:
                    continue
                mi = _parse_media(entry, source="jikan")
                items.append(mi)
            page += 1
            time.sleep(0.5)
        return items[:pool_size]

    def _fetch_planning(self, username: str) -> list[MediaItem]:
        """Fetch user's plan_to_watch list from MAL API."""
        items: list[MediaItem] = []
        offset = 0
        limit = 100
        fields = "genres,studios,synopsis,media_type,num_episodes,mean"
        while True:
            try:
                data = _mal_fetch(
                    f"/users/{username}/animelist?"
                    f"fields={fields}&limit={limit}&offset={offset}"
                    f"&status=plan_to_watch"
                )
            except HTTPError as e:
                if e.code == 404:
                    return items
                raise
            entries = data.get("data", [])
            if not entries:
                break
            for entry in entries:
                node = entry.get("node", {})
                mi = _parse_media(node, source="mal")
                items.append(mi)
            paging = data.get("paging", {})
            if not paging.get("next"):
                break
            offset += limit
            time.sleep(0.35)
        return items

    def fetch(self, username: str, pool_size: int = 100, **kwargs) -> SourceData:
        history = self._fetch_history(username)
        exclude_ids = [m.id for m in history if m.id]

        use_planning = kwargs.get("use_planning", False)
        if use_planning:
            candidates = self._fetch_planning(username)
            # In planning mode the candidates ARE the planning list, so they
            # legitimately overlap with history. Only drop actively watched ones.
            watched_ids = {m.id for m in history if m.status in {"CURRENT", "COMPLETED", "DROPPED", "PAUSED"}}
            candidates = [c for c in candidates if c.id not in watched_ids]
        else:
            candidates = self._fetch_candidates_jikan(exclude_ids, pool_size)
            seen = set(exclude_ids)
            candidates = [c for c in candidates if c.id not in seen]
        return SourceData(
            username=username,
            history=history,
            candidates=candidates,
            source_name="myanimelist",
        )
