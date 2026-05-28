"""Overseerr REST API client for triggering media requests."""

from __future__ import annotations

import httpx


class OverseerrClient:
    """Client for the Overseerr API (v1)."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "X-Api-Key": api_key,
            "Accept": "application/json",
        }

    def search(self, title: str) -> list[dict]:
        """Search Overseerr for media matching *title*.

        Returns a list of result dicts, each containing at least ``id``,
        ``mediaType``, ``title``/``name``, and ``tmdbId``.
        """
        resp = httpx.get(
            f"{self.base_url}/api/v1/search",
            headers=self.headers,
            params={"query": title},
        )
        resp.raise_for_status()
        return resp.json().get("results", [])

    def search_by_tmdb_id(self, tmdb_id: int) -> dict | None:
        """Search Overseerr for media by TMDB ID.

        Uses the TMDB ID as the search query and filters results by
        ``tmdbId``.  Returns the first match or ``None``.
        """
        resp = httpx.get(
            f"{self.base_url}/api/v1/search",
            headers=self.headers,
            params={"query": str(tmdb_id)},
        )
        resp.raise_for_status()
        for result in resp.json().get("results", []):
            if result.get("tmdbId") == tmdb_id:
                return result
        return None

    def request_media(self, media_id: int, media_type: str = "tv", seasons: str = "all") -> dict:
        """Submit a media request to Overseerr.

        Args:
            media_id: The TMDB (or internal Overseerr) media ID.
            media_type: Usually ``"tv"`` for anime.
            seasons: ``"all"`` to request all seasons, or a list of season numbers.

        Returns the response dict from the Overseerr API.
        """
        resp = httpx.post(
            f"{self.base_url}/api/v1/request",
            headers=self.headers,
            json={
                "mediaId": media_id,
                "mediaType": media_type,
                "seasons": seasons,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def get_request_status(self, request_id: int) -> dict:
        """Get the status of a media request by its Overseerr request ID."""
        resp = httpx.get(
            f"{self.base_url}/api/v1/request/{request_id}",
            headers=self.headers,
        )
        resp.raise_for_status()
        return resp.json()
