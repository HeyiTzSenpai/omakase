"""AniList OAuth helpers for Omakase Lite account list synchronization."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx

API_URL = "https://graphql.anilist.co"
OAUTH_URL = "https://anilist.co/api/v2/oauth"
USER_AGENT = "Omakase/0.3 (+https://github.com/HeyiTzSenpai/omakase)"


class AniListWriteError(ValueError):
    """AniList refused or did not prove an account-list mutation."""


def generate_authorization_state() -> str:
    """Return an unpredictable state token for one authorization attempt."""
    return secrets.token_urlsafe(32)


def build_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
) -> str:
    params = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
    )
    return f"{OAUTH_URL}/authorize?{params}"


def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
) -> str:
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        response = client.post(
            f"{OAUTH_URL}/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        token = response.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("AniList returned an invalid access token.")
    return token


def viewer_identity(access_token: str) -> dict[str, object]:
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        response = client.post(
            API_URL,
            json={"query": "query { Viewer { id name } }"},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        response.raise_for_status()
        payload = response.json()
    viewer = (payload.get("data") or {}).get("Viewer") if isinstance(payload, dict) else None
    if (
        not isinstance(viewer, dict)
        or isinstance(viewer.get("id"), bool)
        or not isinstance(viewer.get("id"), int)
        or viewer["id"] <= 0
        or not isinstance(viewer.get("name"), str)
        or not viewer["name"].strip()
    ):
        raise ValueError("AniList did not identify the connected account.")
    return {"id": viewer["id"], "name": viewer["name"].strip()}


def save_completed_entry(
    access_token: str,
    media_id: int,
    *,
    score_ten: int | float,
) -> dict[str, object]:
    if isinstance(media_id, bool) or not isinstance(media_id, int) or media_id <= 0:
        raise ValueError("AniList media ID must be a positive integer.")
    if isinstance(score_ten, bool) or not isinstance(score_ten, (int, float)):
        raise ValueError("AniList score must be a number from 1 to 10.")
    normalized_score = float(score_ten)
    if not 1 <= normalized_score <= 10:
        raise ValueError("AniList score must be a number from 1 to 10.")
    score_raw = int(round(normalized_score * 10))
    mutation = """
    mutation ($mediaId: Int, $status: MediaListStatus, $scoreRaw: Int) {
      SaveMediaListEntry(mediaId: $mediaId, status: $status, scoreRaw: $scoreRaw) {
        id
        mediaId
        status
        score(format: POINT_100)
      }
    }
    """
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        response = client.post(
            API_URL,
            json={
                "query": mutation,
                "variables": {
                    "mediaId": media_id,
                    "status": "COMPLETED",
                    "scoreRaw": score_raw,
                },
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        response.raise_for_status()
        payload = response.json()
    errors = payload.get("errors") if isinstance(payload, dict) else None
    entry = (
        (payload.get("data") or {}).get("SaveMediaListEntry") if isinstance(payload, dict) else None
    )
    if errors or not isinstance(entry, dict):
        raise AniListWriteError("AniList refused the watched-list update.")
    returned_score = entry.get("score")
    if (
        isinstance(entry.get("id"), bool)
        or not isinstance(entry.get("id"), int)
        or entry["id"] <= 0
        or entry.get("mediaId") != media_id
        or entry.get("status") != "COMPLETED"
        or isinstance(returned_score, bool)
        or not isinstance(returned_score, (int, float))
        or float(returned_score) != float(score_raw)
    ):
        raise AniListWriteError("AniList returned an invalid watched-list receipt.")
    return entry


def save_current_entry(
    access_token: str,
    media_id: int,
    *,
    progress: int,
) -> dict[str, object]:
    if isinstance(media_id, bool) or not isinstance(media_id, int) or media_id <= 0:
        raise ValueError("AniList media ID must be a positive integer.")
    if isinstance(progress, bool) or not isinstance(progress, int) or progress <= 0:
        raise ValueError("AniList progress must be a positive whole number of episodes.")
    mutation = """
    mutation ($mediaId: Int, $status: MediaListStatus, $progress: Int) {
      SaveMediaListEntry(mediaId: $mediaId, status: $status, progress: $progress) {
        id
        mediaId
        status
        progress
      }
    }
    """
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        response = client.post(
            API_URL,
            json={
                "query": mutation,
                "variables": {
                    "mediaId": media_id,
                    "status": "CURRENT",
                    "progress": progress,
                },
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        response.raise_for_status()
        payload = response.json()
    errors = payload.get("errors") if isinstance(payload, dict) else None
    entry = (
        (payload.get("data") or {}).get("SaveMediaListEntry") if isinstance(payload, dict) else None
    )
    returned_progress = entry.get("progress") if isinstance(entry, dict) else None
    if errors or not isinstance(entry, dict):
        raise AniListWriteError("AniList refused the progress update.")
    if (
        isinstance(entry.get("id"), bool)
        or not isinstance(entry.get("id"), int)
        or entry["id"] <= 0
        or entry.get("mediaId") != media_id
        or entry.get("status") != "CURRENT"
        or isinstance(returned_progress, bool)
        or not isinstance(returned_progress, int)
        or returned_progress != progress
    ):
        raise AniListWriteError("AniList returned an invalid progress receipt.")
    return entry
