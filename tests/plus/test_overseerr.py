"""Tests for the Phase 4 Overseerr integration.

Covers the API client (``OverseerrClient``), the match heuristic
(``_find_best_match``), the automation orchestrator, and the new
FastAPI routes.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omakase.plus.automation import _find_best_match, trigger_request_after_plan
from omakase.plus.db import _db, run_migrations
from omakase.plus.deps import get_db as _deps_get_db
from omakase.plus.overseerr import OverseerrClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_conn(db_dir: str) -> sqlite3.Connection:
    """Create a fresh SQLite connection with migrations applied."""
    db_path = os.path.join(db_dir, "omakase-plus.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    run_migrations(conn)
    return conn


def _cleanup_db_cache():
    """Close and clear the module-level connection cache."""
    for c in _db.values():
        try:
            c.close()
        except Exception:
            pass
    _db.clear()


def _create_user(conn, email="overseerr-test@example.com"):
    """Insert a bare user row and return the user id."""
    conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        (email, "hash"),
    )
    conn.commit()
    return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]


def _create_planning(conn, user_id, anilist_id=1, title="Cowboy Bebop"):
    """Insert an anilist_planning row and return its primary key."""
    cursor = conn.execute(
        "INSERT INTO anilist_plannings (user_id, anilist_id, title) VALUES (?, ?, ?)",
        (user_id, anilist_id, title),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# OverseerrClient unit tests
# ---------------------------------------------------------------------------


class TestOverseerrClient:
    """Tests for the raw Overseerr API client (httpx mocked)."""

    def test_search_by_title(self):
        """GET /api/v1/search with query param, parse results."""
        mock_response = {
            "results": [
                {"id": 1, "mediaType": "tv", "title": "Cowboy Bebop", "tmdbId": 1},
                {"id": 2, "mediaType": "movie", "title": "Cowboy Bebop: Movie", "tmdbId": 2},
            ]
        }
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: mock_response,
                raise_for_status=lambda: None,
            )
            client = OverseerrClient("http://overseerr:5055", "key123")
            results = client.search("Cowboy Bebop")

        assert len(results) == 2
        assert results[0]["title"] == "Cowboy Bebop"
        mock_get.assert_called_once_with(
            "http://overseerr:5055/api/v1/search",
            headers={"X-Api-Key": "key123", "Accept": "application/json"},
            params={"query": "Cowboy Bebop"},
        )

    def test_request_media(self):
        """POST /api/v1/request with mediaId, mediaType, seasons."""
        mock_response = {"id": 42, "status": "pending"}
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=201,
                json=lambda: mock_response,
                raise_for_status=lambda: None,
            )
            client = OverseerrClient("http://overseerr:5055", "key123")
            result = client.request_media(99, "tv", "all")

        assert result == mock_response
        mock_post.assert_called_once_with(
            "http://overseerr:5055/api/v1/request",
            headers={"X-Api-Key": "key123", "Accept": "application/json"},
            json={"mediaId": 99, "mediaType": "tv", "seasons": "all"},
        )

    def test_get_request_status(self):
        """GET /api/v1/request/{id} and return status info."""
        mock_response = {"id": 42, "status": "approved", "media": {"tmdbId": 1}}
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: mock_response,
                raise_for_status=lambda: None,
            )
            client = OverseerrClient("http://overseerr:5055", "key123")
            result = client.get_request_status(42)

        assert result["status"] == "approved"
        mock_get.assert_called_once_with(
            "http://overseerr:5055/api/v1/request/42",
            headers={"X-Api-Key": "key123", "Accept": "application/json"},
        )


# ---------------------------------------------------------------------------
# _find_best_match unit tests
# ---------------------------------------------------------------------------


class TestFindBestMatch:
    """Tests for the search-result matching heuristic."""

    def test_prefers_tv_exact_title_match(self):
        """Exact title match among TV results beats movies."""
        results = [
            {"id": 1, "mediaType": "movie", "title": "Steins;Gate Movie", "tmdbId": 2},
            {"id": 2, "mediaType": "tv", "title": "Steins;Gate", "tmdbId": 1},
        ]
        match = _find_best_match(results, "Steins;Gate")
        assert match is not None
        assert match["id"] == 2

    def test_returns_none_when_no_tv_results(self):
        """Only movies should yield no match."""
        results = [
            {"id": 1, "mediaType": "movie", "title": "Anime Movie", "tmdbId": 1},
        ]
        assert _find_best_match(results, "Anime") is None

    def test_token_overlap_fallback(self):
        """Higher token overlap among TV results wins."""
        results = [
            {"id": 1, "mediaType": "tv", "title": "Cowboy Bebop Movie", "tmdbId": 1},
            {"id": 2, "mediaType": "tv", "title": "Cowboy Bebop", "tmdbId": 2},
        ]
        match = _find_best_match(results, "Cowboy Bebop")
        assert match is not None
        assert match["id"] == 2  # exact match

    def test_returns_none_for_empty_results(self):
        assert _find_best_match([], "Any Anime") is None

    def test_first_tv_result_as_fallback(self):
        """When no tokens overlap, fall back to first TV result."""
        results = [
            {"id": 1, "mediaType": "tv", "title": "Completely Unrelated", "tmdbId": 1},
            {"id": 2, "mediaType": "tv", "title": "Also Unrelated", "tmdbId": 2},
        ]
        match = _find_best_match(results, "Something Else Entirely")
        assert match is not None
        assert match["id"] == 1  # first TV result


# ---------------------------------------------------------------------------
# Automation orchestrator tests
# ---------------------------------------------------------------------------


class TestTriggerRequestAfterPlan:
    """Tests for ``trigger_request_after_plan`` with mocked HTTP."""

    @pytest.fixture(autouse=True)
    def _set_api_key(self):
        """Ensure an API key is available via env var fallback."""
        os.environ["OVERSEERR_API_KEY"] = "test-key"
        yield
        os.environ.pop("OVERSEERR_API_KEY", None)

    @pytest.fixture
    def db_conn(self):
        """Temp database with a user and a planning row."""
        with tempfile.TemporaryDirectory() as tmp:
            conn = _make_test_conn(tmp)
            user_id = _create_user(conn, "automation@test.com")
            planning_id = _create_planning(conn, user_id)
            yield conn, user_id, planning_id
            conn.close()
            _cleanup_db_cache()

    def test_trigger_request_found(self, db_conn):
        """Search returns results -> request is submitted -> 'requested'."""
        conn, user_id, planning_id = db_conn

        with (
            patch("httpx.get") as mock_get,
            patch("httpx.post") as mock_post,
        ):
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "results": [{"id": 99, "mediaType": "tv", "title": "Cowboy Bebop", "tmdbId": 1}]
                },
                raise_for_status=lambda: None,
            )
            mock_post.return_value = MagicMock(
                status_code=201,
                json=lambda: {"id": 42, "status": "pending"},
                raise_for_status=lambda: None,
            )

            status = trigger_request_after_plan(conn, user_id, planning_id, "Cowboy Bebop")

        assert status == "requested"

        row = conn.execute(
            "SELECT * FROM overseerr_requests WHERE anilist_planning_id = ?",
            (planning_id,),
        ).fetchone()
        assert row is not None
        assert row["status"] == "requested"
        assert row["overseerr_request_id"] == 42

    def test_trigger_request_not_found(self, db_conn):
        """Search returns empty -> 'not_found' row inserted."""
        conn, user_id, planning_id = db_conn

        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"results": []},
                raise_for_status=lambda: None,
            )

            status = trigger_request_after_plan(conn, user_id, planning_id, "Nonexistent Anime")

        assert status == "not_found"

        row = conn.execute(
            "SELECT * FROM overseerr_requests WHERE anilist_planning_id = ?",
            (planning_id,),
        ).fetchone()
        assert row is not None
        assert row["status"] == "not_found"


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------


class TestAutoRequestRoute:
    """FastAPI TestClient integration tests for the auto-request routes."""

    @pytest.fixture
    def client(self):
        """App with an overridden get_db dependency."""
        tmp = tempfile.mkdtemp()

        def _override_deps_get_db():
            return _make_test_conn(tmp)

        app = FastAPI()
        from omakase.plus.routes import router

        app.include_router(router)
        app.dependency_overrides[_deps_get_db] = _override_deps_get_db

        yield TestClient(app)

        _cleanup_db_cache()
        shutil.rmtree(tmp, ignore_errors=True)

    def _signup_and_login(self, client):
        """Sign up a test user (cookie stored automatically by TestClient)."""
        os.environ["OMAKASE_PLUS_INVITE"] = "test123"
        try:
            resp = client.post(
                "/plus/signup",
                data={
                    "email": "route-test@example.com",
                    "password": "secret123",
                    "confirm_password": "secret123",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 302
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_auto_request_endpoint(self, client):
        """POST /plus/api/auto-request returns requested status."""
        self._signup_and_login(client)

        os.environ["OVERSEERR_API_KEY"] = "test-key"
        try:
            with patch("omakase.plus.automation.OverseerrClient") as MockClient:
                mock_instance = MagicMock()
                mock_instance.search.return_value = [
                    {"id": 99, "mediaType": "tv", "title": "Cowboy Bebop", "tmdbId": 1}
                ]
                mock_instance.request_media.return_value = {
                    "id": 42,
                    "status": "pending",
                }
                MockClient.return_value = mock_instance

                resp = client.post(
                    "/plus/api/auto-request",
                    json={"anilist_id": 1, "title": "Cowboy Bebop"},
                )
        finally:
            os.environ.pop("OVERSEERR_API_KEY", None)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "requested"
        assert data["overseerr_request_id"] == 42

    def test_auto_request_requires_login(self, client):
        """POST without session cookie returns 401."""
        resp = client.post(
            "/plus/api/auto-request",
            json={"anilist_id": 1, "title": "Cowboy Bebop"},
        )
        assert resp.status_code == 401

    def test_overseerr_status_endpoint(self, client):
        """GET /plus/integrations/overseerr/status returns request list."""
        self._signup_and_login(client)

        resp = client.get("/plus/integrations/overseerr/status")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_overseerr_status_requires_login(self, client):
        """GET status without session cookie returns 401."""
        resp = client.get("/plus/integrations/overseerr/status")
        assert resp.status_code == 401
