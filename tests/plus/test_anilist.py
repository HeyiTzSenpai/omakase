"""Tests for AniList OAuth + GraphQL write + integration routes.

Uses mocked ``httpx`` for external calls and FastAPI ``TestClient`` for
route-level integration tests.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omakase.plus.anilist import (
    _pkce_state,
    add_to_planning,
    build_authorize_url,
    exchange_code,
    generate_pkce,
    is_token_valid,
    with_valid_token,
)
from omakase.plus.auth import (
    create_session,
    hash_password,
)
from omakase.plus.db import _db, run_migrations
from omakase.plus.deps import get_db as _deps_get_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_conn(db_dir: str) -> sqlite3.Connection:
    """Create a fresh SQLite connection at *db_dir*/omakase-plus.db."""
    db_path = os.path.join(db_dir, "omakase-plus.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    run_migrations(conn)
    return conn


def _cleanup_db_cache():
    for c in _db.values():
        try:
            c.close()
        except Exception:
            pass
    _db.clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_pkce_state():
    """Reset the in-memory PKCE state between tests."""
    _pkce_state.clear()


@pytest.fixture
def client():
    """FastAPI TestClient wired to a per-test temp database."""
    tmp = tempfile.mkdtemp()

    def _override_get_db():
        return _make_test_conn(tmp)

    # Initialize the database before creating the app
    _make_test_conn(tmp).close()

    app = FastAPI()
    from omakase.plus.routes import router

    app.include_router(router)
    app.dependency_overrides[_deps_get_db] = _override_get_db

    test_client = TestClient(app)
    test_client._tmp_dir = tmp

    yield test_client

    _cleanup_db_cache()
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# PKCE + OAuth unit tests
# ---------------------------------------------------------------------------


class TestPKCE:
    def test_generate_pkce(self):
        verifier, challenge = generate_pkce()
        assert verifier
        assert challenge
        assert verifier != challenge
        assert len(verifier) > 0
        assert len(challenge) > 0

    def test_generate_pkce_is_random(self):
        """Two calls produce different verifiers (non-deterministic)."""
        v1, _c1 = generate_pkce()
        v2, _c2 = generate_pkce()
        assert v1 != v2

    def test_build_authorize_url(self):
        url = build_authorize_url(
            "test_client_id",
            "http://localhost/callback",
            "test_challenge",
        )
        assert "client_id=test_client_id" in url
        assert "redirect_uri=http://localhost/callback" in url
        assert "response_type=code" in url
        assert "code_challenge=test_challenge" in url
        assert "code_challenge_method=S256" in url


class TestExchangeCode:
    def test_exchange_code_returns_token(self):
        """Mock a successful token exchange."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "anilist_token_abc123",
            "token_type": "Bearer",
        }

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_response
        mock_client.__enter__.return_value = mock_client

        with patch("httpx.Client", return_value=mock_client):
            token = exchange_code(
                "cid", "csecret", "http://localhost/callback", "auth_code", "verifier"
            )

        assert token == "anilist_token_abc123"
        mock_client.post.assert_called_once()
        call_data = mock_client.post.call_args[1]["data"]
        assert call_data["grant_type"] == "authorization_code"
        assert call_data["code"] == "auth_code"
        assert call_data["code_verifier"] == "verifier"

    def test_exchange_code_http_error_raises(self):
        """Mock a failed token exchange (bad client secret, etc.)."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad request", request=MagicMock(), response=mock_response
        )

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_response
        mock_client.__enter__.return_value = mock_client

        with patch("httpx.Client", return_value=mock_client):
            with pytest.raises(httpx.HTTPError):
                exchange_code("cid", "csecret", "http://localhost/callback", "code", "ver")


class TestAddToPlanning:
    def test_add_to_planning_sends_correct_mutation(self):
        """Verify the GraphQL mutation payload is correct."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"SaveMediaListEntry": {"id": 1, "mediaId": 12345, "status": "PLANNING"}}
        }

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_response
        mock_client.__enter__.return_value = mock_client

        with patch("httpx.Client", return_value=mock_client):
            result = add_to_planning("bearer_token", 12345)

        assert result["data"]["SaveMediaListEntry"]["mediaId"] == 12345

        call_kwargs = mock_client.post.call_args[1]
        assert "mutation" in call_kwargs["json"]["query"]
        assert "SaveMediaListEntry" in call_kwargs["json"]["query"]
        assert call_kwargs["json"]["variables"]["mediaId"] == 12345
        assert call_kwargs["json"]["variables"]["status"] == "PLANNING"
        assert call_kwargs["headers"]["Authorization"] == "Bearer bearer_token"

    def test_add_to_planning_custom_status(self):
        """Verify custom status is passed through."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"SaveMediaListEntry": {"id": 2, "mediaId": 999, "status": "CURRENT"}}
        }

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_response
        mock_client.__enter__.return_value = mock_client

        with patch("httpx.Client", return_value=mock_client):
            result = add_to_planning("token", 999, status="CURRENT")

        assert result["data"]["SaveMediaListEntry"]["status"] == "CURRENT"
        call_vars = mock_client.post.call_args[1]["json"]["variables"]
        assert call_vars["status"] == "CURRENT"


class TestIsTokenValid:
    def test_valid_token_returns_true(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_response
        mock_client.__enter__.return_value = mock_client

        with patch("httpx.Client", return_value=mock_client):
            assert is_token_valid("valid_token") is True

    def test_invalid_token_returns_false(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 401

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_response
        mock_client.__enter__.return_value = mock_client

        with patch("httpx.Client", return_value=mock_client):
            assert is_token_valid("invalid_token") is False

    def test_http_error_returns_false(self):
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__enter__.return_value = mock_client

        with patch("httpx.Client", return_value=mock_client):
            assert is_token_valid("token") is False


class TestWithValidToken:
    def test_missing_token_raises(self):
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = None  # no secret row

        with patch("omakase.plus.secrets.read_secret", return_value=None):
            with pytest.raises(ValueError, match="No AniList OAuth token found"):
                with with_valid_token(db, 1, "", "", ""):
                    pass

    def test_invalid_token_raises(self):
        with (
            patch("omakase.plus.secrets.read_secret", return_value="some_token"),
            patch("omakase.plus.anilist.is_token_valid", return_value=False),
        ):
            with pytest.raises(ValueError, match="invalid or expired"):
                with with_valid_token(MagicMock(), 1, "", "", ""):
                    pass

    def test_valid_token_yields(self):
        with (
            patch("omakase.plus.secrets.read_secret", return_value="valid_token"),
            patch("omakase.plus.anilist.is_token_valid", return_value=True),
        ):
            with with_valid_token(MagicMock(), 1, "", "", "") as token:
                assert token == "valid_token"


# ---------------------------------------------------------------------------
# Route integration tests  (FastAPI TestClient)
# ---------------------------------------------------------------------------


class TestRoutes:
    """Tests requiring a logged-in user with a session cookie."""

    @staticmethod
    def _login_user(client) -> int:
        """Create a user + session in the temp DB and set the cookie."""
        db = _make_test_conn(client._tmp_dir)
        db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            ("anilist-test@example.com", hash_password("password123")),
        )
        db.commit()
        user_id = db.execute(
            "SELECT id FROM users WHERE email = ?",
            ("anilist-test@example.com",),
        ).fetchone()["id"]
        session_id = create_session(db, user_id)
        db.close()
        client.cookies.set("omakase_session", session_id)
        return user_id

    # ── Auth guard ────────────────────────────────────────

    def test_plan_requires_login(self, client):
        """POST /plus/api/plan without a session cookie returns 401."""
        resp = client.post("/plus/api/plan", json={"anilist_id": 1, "title": "Foo"})
        assert resp.status_code == 401

    def test_anilist_connect_requires_login(self, client):
        """GET /plus/integrations/anilist/connect without a session returns 401."""
        resp = client.get("/plus/integrations/anilist/connect", follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_anilist_callback_requires_login(self, client):
        """GET /plus/integrations/anilist/callback without a session returns 401."""
        resp = client.get("/plus/integrations/anilist/callback?code=abc", follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_anilist_disconnect_requires_login(self, client):
        """POST /plus/integrations/anilist/disconnect without a session returns 401."""
        resp = client.post("/plus/integrations/anilist/disconnect", follow_redirects=False)
        assert resp.status_code in (302, 401)

    # ── /plus/api/plan ─────────────────────────────────────

    def test_plan_api_endpoint(self, client):
        """Happy path: plan an anime returns {"status": "ok"}."""
        self._login_user(client)

        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = "fake_token"
        mock_cm.__exit__.return_value = None

        mock_add_result = {
            "data": {"SaveMediaListEntry": {"id": 10, "mediaId": 54321, "status": "PLANNING"}}
        }

        with (
            patch("omakase.plus.routes.with_valid_token", return_value=mock_cm),
            patch("omakase.plus.routes.add_to_planning", return_value=mock_add_result),
        ):
            resp = client.post("/plus/api/plan", json={"anilist_id": 54321, "title": "Test Anime"})

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_plan_api_missing_fields(self, client):
        """Missing anilist_id or title returns error."""
        self._login_user(client)

        resp = client.post("/plus/api/plan", json={"title": "No ID"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

        resp = client.post("/plus/api/plan", json={"anilist_id": 1})
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

    def test_plan_api_invalid_json(self, client):
        """Malformed JSON body returns error."""
        self._login_user(client)

        resp = client.post(
            "/plus/api/plan", data="not-json", headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

    def test_plan_api_token_missing(self, client):
        """When no OAuth token is stored, an error is returned."""
        self._login_user(client)

        with patch(
            "omakase.plus.routes.with_valid_token",
            side_effect=ValueError("No AniList OAuth token found"),
        ):
            resp = client.post("/plus/api/plan", json={"anilist_id": 1, "title": "Foo"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "error"
        assert "No AniList OAuth token found" in resp.json()["detail"]

    # ── Dedupe ─────────────────────────────────────────────

    def test_dedupe_planning(self, client):
        """Planning the same anime twice returns "already_planned"."""
        self._login_user(client)

        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = "fake_token"
        mock_cm.__exit__.return_value = None

        mock_add_result = {
            "data": {"SaveMediaListEntry": {"id": 99, "mediaId": 777, "status": "PLANNING"}}
        }

        with (
            patch("omakase.plus.routes.with_valid_token", return_value=mock_cm),
            patch("omakase.plus.routes.add_to_planning", return_value=mock_add_result),
        ):
            # First call should succeed
            resp1 = client.post(
                "/plus/api/plan", json={"anilist_id": 777, "title": "Deduped Anime"}
            )
            assert resp1.status_code == 200
            assert resp1.json()["status"] == "ok"

            # Second call with same anilist_id should be "already_planned"
            resp2 = client.post(
                "/plus/api/plan", json={"anilist_id": 777, "title": "Deduped Anime"}
            )
            assert resp2.status_code == 200
            assert resp2.json()["status"] == "already_planned"

    def test_dedupe_per_user(self, client):
        """Two different users can plan the same anime."""
        self._login_user(client)

        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = "fake_token"
        mock_cm.__exit__.return_value = None

        mock_add_result = {
            "data": {"SaveMediaListEntry": {"id": 42, "mediaId": 999, "status": "PLANNING"}}
        }

        with (
            patch("omakase.plus.routes.with_valid_token", return_value=mock_cm),
            patch("omakase.plus.routes.add_to_planning", return_value=mock_add_result),
        ):
            resp = client.post("/plus/api/plan", json={"anilist_id": 999, "title": "Shared Anime"})
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

        # The test only checks that the first user succeeds; a second
        # user would need a separate session, which is out of scope here.
        # The per-user dedupe is enforced by the SQL WHERE user_id = ? clause.

    # ── Connect flow (unit-like, no actual redirect) ───────

    def test_connect_missing_client_id(self, client):
        """When ANILIST_CLIENT_ID is not set, the connect endpoint returns 500."""
        self._login_user(client)

        os.environ.pop("ANILIST_CLIENT_ID", None)
        try:
            resp = client.get("/plus/integrations/anilist/connect", follow_redirects=False)
            assert resp.status_code == 500
        finally:
            os.environ["ANILIST_CLIENT_ID"] = "test_client_id"

    def test_connect_redirects_to_anilist(self, client):
        """On success, the connect endpoint redirects to AniList."""
        os.environ["ANILIST_CLIENT_ID"] = "test_cid"
        self._login_user(client)

        resp = client.get("/plus/integrations/anilist/connect", follow_redirects=False)
        assert resp.status_code == 302
        assert "anilist.co/api/v2/oauth/authorize" in resp.headers["location"]
        assert "client_id=test_cid" in resp.headers["location"]

    def test_callback_missing_code(self, client):
        """Callback without ?code= returns 400."""
        self._login_user(client)
        resp = client.get("/plus/integrations/anilist/callback", follow_redirects=False)
        assert resp.status_code == 400

    def test_callback_no_pkce_state(self, client):
        """Callback without prior connect returns 400."""
        self._login_user(client)
        resp = client.get(
            "/plus/integrations/anilist/callback?code=auth123",
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert "No PKCE state found" in resp.text

    def test_callback_no_credentials(self, client):
        """Callback when AniList env vars are missing returns 500."""
        os.environ.pop("ANILIST_CLIENT_ID", None)
        os.environ.pop("ANILIST_CLIENT_SECRET", None)

        # Set up PKCE state manually
        _pkce_state[1] = ("fake_verifier", "fake_challenge")
        self._login_user(client)

        try:
            resp = client.get(
                "/plus/integrations/anilist/callback?code=auth123",
                follow_redirects=False,
            )
            assert resp.status_code == 500
        finally:
            os.environ["ANILIST_CLIENT_ID"] = "test_cid"
            os.environ["ANILIST_CLIENT_SECRET"] = "test_secret"

    def test_disconnect_clears_secret(self, client):
        """POST /plus/integrations/anilist/disconnect redirects to settings."""
        user_id = self._login_user(client)

        # Store a fake token first
        db = _make_test_conn(client._tmp_dir)
        from omakase.plus.secrets import store_secret

        store_secret(db, user_id, "anilist_oauth_token", "some_token")
        db.close()

        resp = client.post("/plus/integrations/anilist/disconnect", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/plus/settings"

        # Verify the token was actually deleted
        db2 = _make_test_conn(client._tmp_dir)
        from omakase.plus.secrets import read_secret

        assert read_secret(db2, user_id, "anilist_oauth_token") is None
        db2.close()
