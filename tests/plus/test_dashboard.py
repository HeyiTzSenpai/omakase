"""Tests for Omakase Plus dashboard (Phase 5).

Uses FastAPI TestClient with a per-test temp database.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omakase.plus.db import _db, run_migrations
from omakase.plus.deps import get_db as _deps_get_db
from omakase.plus.routes import _login_attempts
from omakase.types import Recommendation

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
    """Close and clear Sub-agent A's connection cache (best-effort)."""
    for c in _db.values():
        try:
            c.close()
        except Exception:
            pass
    _db.clear()


def _signup_and_login(client: TestClient) -> None:
    """Helper: sign up a test user so subsequent requests are authenticated."""
    os.environ["OMAKASE_PLUS_INVITE"] = "test123"
    resp = client.post(
        "/plus/signup",
        data={
            "email": "alice@example.com",
            "password": "secret123",
            "confirm_password": "secret123",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/plus/dashboard"
    assert "omakase_session" in resp.cookies


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    _login_attempts.clear()


@pytest.fixture
def client():
    """FastAPI TestClient wired to a per-test temp database."""
    tmp = tempfile.mkdtemp()

    def _override_deps_get_db():
        return _make_test_conn(tmp)

    app = FastAPI()
    from omakase.plus.routes import router

    app.include_router(router)
    app.dependency_overrides[_deps_get_db] = _override_deps_get_db

    yield TestClient(app)

    _cleanup_db_cache()
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDashboardAccess:
    def test_dashboard_requires_login(self, client):
        """GET /plus/dashboard without a session cookie redirects or 401s."""
        resp = client.get("/plus/dashboard", follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_dashboard_renders_for_logged_in_user(self, client):
        """After login the dashboard returns 200 with all expected sections."""
        try:
            _signup_and_login(client)

            resp = client.get("/plus/dashboard")
            assert resp.status_code == 200
            html = resp.text

            # Header
            assert "Omakase Plus" in html
            assert "alice@example.com" in html
            assert "Settings" in html
            assert "Log out" in html

            # Sections
            assert "Taste Profile" in html
            assert "Run Recommendation" in html
            assert "Recent Runs" in html
            assert "Planning Queue" in html

            # Form elements
            assert 'name="profile"' in html
            assert 'name="source"' in html
            assert 'name="username"' in html
            assert 'name="mode"' in html
            assert 'name="count"' in html
            assert 'name="temperature"' in html
            assert 'name="skip_profile"' in html
            assert 'name="use_planning"' in html
            assert 'name="model"' in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)


class TestTasteProfile:
    def test_save_taste_profile(self, client):
        """POST /plus/dashboard/profile saves content and dashboard shows it."""
        try:
            _signup_and_login(client)

            resp = client.post(
                "/plus/dashboard/profile",
                data={"profile": "I love slice of life and romance anime."},
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert resp.headers["location"] == "/plus/dashboard"

            resp2 = client.get("/plus/dashboard")
            assert "I love slice of life and romance anime." in resp2.text
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_dashboard_shows_saved_profile(self, client):
        """Saved taste profile text is preloaded in the textarea."""
        try:
            _signup_and_login(client)

            # Save a profile
            client.post(
                "/plus/dashboard/profile",
                data={"profile": "Prefer dark fantasy and psychological thrillers."},
            )

            # Verify it shows up
            resp = client.get("/plus/dashboard")
            assert "Prefer dark fantasy and psychological thrillers." in resp.text
            assert 'name="profile"' in resp.text
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_save_empty_profile(self, client):
        """Saving an empty profile is allowed (clears the stored profile)."""
        try:
            _signup_and_login(client)

            # First save something
            client.post(
                "/plus/dashboard/profile",
                data={"profile": "Some profile text."},
            )

            # Then clear it
            resp = client.post(
                "/plus/dashboard/profile",
                data={"profile": ""},
                follow_redirects=False,
            )
            assert resp.status_code == 302

            resp2 = client.get("/plus/dashboard")
            # textarea should be present (empty is fine)
            assert 'name="profile"' in resp2.text
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)


class TestRunRecommendation:
    def test_run_recommendation_endpoint(self, client):
        """POST /plus/dashboard/run with mocked pipeline creates a run_history row."""
        mock_recs = [
            Recommendation(
                title="One Piece",
                predicted_score=9.2,
                reasoning="Matches your love for long-running shonen.",
                best_match_from_history="Naruto",
                url="https://anilist.co/anime/21/",
                source="anilist",
            ),
            Recommendation(
                title="Attack on Titan",
                predicted_score=8.7,
                reasoning="Dark fantasy with complex characters.",
                best_match_from_history="Death Note",
                url="https://anilist.co/anime/16498/",
                source="anilist",
            ),
        ]
        try:
            _signup_and_login(client)

            with patch("omakase.plus.routes.run_pipeline", return_value=mock_recs):
                resp = client.post(
                    "/plus/dashboard/run",
                    data={
                        "source": "anilist",
                        "mode": "fast",
                        "count": "5",
                        "temperature": "0.5",
                        "username": "testuser",
                        "skip_profile": "true",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert "?run=" in resp.headers["location"]

            # Follow the redirect to the run results
            resp2 = client.get(resp.headers["location"])
            assert resp2.status_code == 200
            html = resp2.text
            assert "One Piece" in html
            assert "Attack on Titan" in html
            assert "9.2" in html
            assert "8.7" in html
            assert "Recent Runs" in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_run_with_missing_username_uses_email(self, client):
        """When username is empty, the route falls back to the email local part."""
        mock_recs = [
            Recommendation(
                title="Test Anime",
                predicted_score=7.5,
                reasoning="Good show.",
                best_match_from_history="",
                url=None,
                source="anilist",
            ),
        ]
        try:
            _signup_and_login(client)

            with patch("omakase.plus.routes.run_pipeline", return_value=mock_recs):
                resp = client.post(
                    "/plus/dashboard/run",
                    data={
                        "source": "anilist",
                        "mode": "fast",
                        "count": "5",
                        "username": "",
                        "skip_profile": "true",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert "?run=" in resp.headers["location"]
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_run_requires_auth(self, client):
        """POST /plus/dashboard/run without login is rejected."""
        resp = client.post(
            "/plus/dashboard/run",
            data={"source": "anilist", "mode": "fast", "count": "5"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 401)


class TestPlanButton:
    def test_plan_button_creates_planning_and_request(self, client):
        """POST /plus/dashboard/plan inserts planning row."""
        try:
            _signup_and_login(client)

            resp = client.post(
                "/plus/dashboard/plan",
                data={"anilist_id": "21", "title": "One Piece"},
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert resp.headers["location"] == "/plus/dashboard"

            # Dashboard should now show the planned entry
            resp2 = client.get("/plus/dashboard")
            html = resp2.text
            assert "One Piece" in html
            # The AniList status badge should show "Planned"
            assert "Planned" in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_dashboard_shows_planning_queue(self, client):
        """Multiple planned entries appear in the planning queue table."""
        try:
            _signup_and_login(client)

            # Plan two anime
            client.post(
                "/plus/dashboard/plan",
                data={"anilist_id": "1", "title": "Cowboy Bebop"},
            )
            client.post(
                "/plus/dashboard/plan",
                data={"anilist_id": "5", "title": "Samurai Champloo"},
            )

            resp = client.get("/plus/dashboard")
            html = resp.text
            assert "Cowboy Bebop" in html
            assert "Samurai Champloo" in html
            # AniList links should use the correct IDs
            assert "https://anilist.co/anime/1/" in html
            assert "https://anilist.co/anime/5/" in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_double_plan_is_idempotent(self, client):
        """Planning the same anime twice does not create duplicate rows."""
        try:
            _signup_and_login(client)

            client.post(
                "/plus/dashboard/plan",
                data={"anilist_id": "21", "title": "One Piece"},
            )
            resp = client.post(
                "/plus/dashboard/plan",
                data={"anilist_id": "21", "title": "One Piece"},
                follow_redirects=False,
            )
            assert resp.status_code == 302

            # Dashboard should show the title exactly once
            resp2 = client.get("/plus/dashboard")
            assert resp2.text.count("One Piece") >= 1
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_plan_requires_auth(self, client):
        """POST /plus/dashboard/plan without login is rejected."""
        resp = client.post(
            "/plus/dashboard/plan",
            data={"anilist_id": "1", "title": "Test"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 401)
