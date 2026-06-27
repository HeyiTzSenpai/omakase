"""AniList resolver for Plus direct download requests."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

API_URL = "https://graphql.anilist.co"
USER_AGENT = "Omakase/0.1 (+https://github.com/HeyiTzSenpai/omakase)"

_ANILIST_ID_RE = re.compile(r"anilist\.co/anime/(\d+)", re.I)
_ARC_HINT_RE = re.compile(r"\b(arc|cour|final|movie|ova|part|season|special)\b", re.I)
_ORDINAL_SEASON_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+season\b", re.I)
_SEASON_RE = re.compile(r"\bseason[\s._-]*(\d{1,2})\b|\bs[\s._-]*(\d{1,2})\b", re.I)


@dataclass(frozen=True)
class DirectDownloadTarget:
    anilist_id: int
    title: str
    search_titles: list[str]
    format: str = ""
    status: str = ""
    episodes: int | None = None
    season: str = ""
    season_year: int | None = None


def parse_anilist_id(value: str) -> int | None:
    text = (value or "").strip()
    match = _ANILIST_ID_RE.search(text)
    if match:
        return int(match.group(1))
    if text.isdigit():
        return int(text)
    return None


def _preferred_title(media: dict) -> str:
    title = media.get("title") or {}
    return title.get("english") or title.get("romaji") or title.get("native") or ""


def _season_number(value: str) -> int | None:
    if value.strip().isdigit():
        return int(value.strip())
    for pattern in (_SEASON_RE, _ORDINAL_SEASON_RE):
        match = pattern.search(value)
        if match:
            for group in match.groups():
                if group:
                    return int(group)
    return None


def _season_hint_alias(season_hint: str) -> str:
    hint = (season_hint or "").strip()
    if not hint:
        return ""
    number = _season_number(hint)
    if number is not None and not _ARC_HINT_RE.search(hint):
        return f"Season {number}"
    return hint


def _already_has_hint(value: str, hint: str) -> bool:
    value_lower = value.lower()
    hint_lower = hint.lower()
    if hint_lower in value_lower:
        return True
    hint_season = _season_number(hint)
    return hint_season is not None and _season_number(value) == hint_season


def _search_titles(media: dict, season_hint: str = "") -> list[str]:
    title = media.get("title") or {}
    values = [title.get("english"), title.get("romaji"), title.get("native")]

    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    if season_hint:
        hint = _season_hint_alias(season_hint)
        for value in values:
            if not value:
                continue
            if _already_has_hint(value, hint):
                continue
            season_alias = f"{value} {hint}"
            if season_alias not in result:
                result.append(season_alias)
    return result


def _target_from_media(media: dict, season_hint: str = "") -> DirectDownloadTarget:
    return DirectDownloadTarget(
        anilist_id=int(media["id"]),
        title=_preferred_title(media),
        search_titles=_search_titles(media, season_hint),
        format=media.get("format") or "",
        status=media.get("status") or "",
        episodes=media.get("episodes"),
        season=media.get("season") or "",
        season_year=media.get("seasonYear"),
    )


def _post(query: str, variables: dict) -> dict:
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            API_URL,
            json={"query": query, "variables": variables},
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        response.raise_for_status()
        return response.json()


def resolve_direct_request(query_text: str, season: str = "") -> DirectDownloadTarget:
    text = (query_text or "").strip()
    season_hint = (season or "").strip()
    if not text:
        raise ValueError("Enter an anime title or AniList URL.")

    fields = "id title { romaji english native } format status episodes season seasonYear"
    media_id = parse_anilist_id(text)
    if media_id is not None:
        data = _post(
            f"query ($id: Int) {{ Media(id: $id, type: ANIME) {{ {fields} }} }}",
            {"id": media_id},
        )
        media = (data.get("data") or {}).get("Media")
    else:
        search = f"{text} {_season_hint_alias(season_hint)}".strip() if season_hint else text
        data = _post(
            f"""
            query ($search: String!) {{
              Page(page: 1, perPage: 5) {{
                media(search: $search, type: ANIME, isAdult: false) {{
                  {fields}
                }}
              }}
            }}
            """,
            {"search": search},
        )
        media = ((data.get("data") or {}).get("Page") or {}).get("media", [None])[0]

    if not media:
        raise ValueError(f'No AniList anime found for "{text}".')
    return _target_from_media(media, season_hint)
