"""AniList OAuth (Authorization Code with PKCE) and GraphQL write operations."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Iterator
from contextlib import contextmanager

import httpx

API_URL = "https://graphql.anilist.co"
OAUTH_URL = "https://anilist.co/api/v2/oauth"
USER_AGENT = "Omakase/0.1 (+https://github.com/HeyiTzSenpai/omakase)"

# In-memory PKCE state: {user_id: (code_verifier, code_challenge)}
_pkce_state: dict[int, tuple[str, str]] = {}


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE code verifier and challenge.

    Returns ``(code_verifier, code_challenge)`` where *code_challenge* is
    ``SHA256(code_verifier)`` base64url-encoded without padding.
    """
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def build_authorize_url(client_id: str, redirect_uri: str, code_challenge: str) -> str:
    """Build the AniList OAuth authorization URL with PKCE parameters."""
    params = (
        f"client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )
    return f"{OAUTH_URL}/authorize?{params}"


def exchange_code(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> str:
    """Exchange an authorization code for an access token.

    POSTs ``application/x-www-form-urlencoded`` data to the AniList OAuth
    token endpoint.  Returns the ``access_token`` string.
    """
    with httpx.Client() as client:
        resp = client.post(
            f"{OAUTH_URL}/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
                "code_verifier": code_verifier,
            },
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


def add_to_planning(access_token: str, anilist_id: int, status: str = "PLANNING") -> dict:
    """Add an anime to the authenticated user's AniList planning list.

    Uses the ``SaveMediaListEntry`` GraphQL mutation.  Returns the full
    JSON response as a dict.
    """
    mutation = """
    mutation ($mediaId: Int, $status: MediaListStatus) {
      SaveMediaListEntry(mediaId: $mediaId, status: $status) {
        id
        mediaId
        status
      }
    }
    """
    with httpx.Client() as client:
        resp = client.post(
            API_URL,
            json={
                "query": mutation,
                "variables": {"mediaId": anilist_id, "status": status},
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        resp.raise_for_status()
        return resp.json()


def is_token_valid(access_token: str) -> bool:
    """Quickly check whether the AniList access token is still valid.

    Queries the ``Viewer`` endpoint.  Returns True on HTTP 200, False on
    401 or any transport error.
    """
    query = "query { Viewer { id } }"
    try:
        with httpx.Client() as client:
            resp = client.post(
                API_URL,
                json={"query": query},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


@contextmanager
def with_valid_token(
    db,
    user_id: int,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> Iterator[str]:
    """Context manager that yields the user's valid AniList OAuth token.

    Reads the token from the secrets store and runs a live validity check
    against AniList's ``Viewer`` endpoint.  Raises ``ValueError`` if the
    token is missing or invalid.

    *client_id*, *client_secret*, and *redirect_uri* are accepted for
    forward compatibility.  AniList OAuth tokens are long-lived and do not
    support refresh tokens, so these parameters are unused today.
    """
    from omakase.plus.secrets import read_secret

    token = read_secret(db, user_id, "anilist_oauth_token")
    if token is None:
        raise ValueError("No AniList OAuth token found. Please connect AniList first.")
    if not is_token_valid(token):
        raise ValueError(
            "AniList OAuth token is invalid or expired. Please disconnect and reconnect AniList."
        )
    yield token
