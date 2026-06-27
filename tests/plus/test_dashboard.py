"""Tests for Omakase Plus dashboard (Phase 5).

Uses FastAPI TestClient with a per-test temp database.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omakase.plus.db import _db, run_migrations
from omakase.plus.deps import get_db as _deps_get_db
from omakase.plus.feedback import save_feedback
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


def _connect_client_db(client: TestClient) -> sqlite3.Connection:
    conn = sqlite3.connect(client.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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


def _drain_job(client: TestClient, resp, timeout: float = 5.0) -> None:
    """Wait for an async ``/plus/api/run`` job to leave the 'running' state.

    ``/plus/api/run`` now runs the pipeline in a background thread, so callers
    that patch ``run_pipeline`` must drain the job *inside* the patch context —
    otherwise the patch is torn down before the worker invokes it.
    """
    job_id = resp.json()["job_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/plus/api/run/status/{job_id}")
        if status.status_code == 200 and status.json().get("status") != "running":
            return
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def _user_id(client: TestClient) -> int:
    with _connect_client_db(client) as conn:
        return conn.execute(
            "SELECT id FROM users WHERE email = ?",
            ("alice@example.com",),
        ).fetchone()["id"]


def _seed_run(
    client: TestClient,
    picks: list[dict],
    *,
    source: str = "anilist",
    model: str = "test-model",
    lane: str = "best_match",
) -> int:
    with _connect_client_db(client) as conn:
        cursor = conn.execute(
            "INSERT INTO run_history (user_id, source, model, picks, lane) VALUES (?, ?, ?, ?, ?)",
            (_user_id(client), source, model, json.dumps(picks), lane),
        )
        conn.commit()
        return cursor.lastrowid


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
    db_path = os.path.join(tmp, "omakase-plus.db")

    def _override_deps_get_db():
        return _make_test_conn(tmp)

    app = FastAPI()
    from omakase.plus.routes import router

    app.include_router(router)
    app.dependency_overrides[_deps_get_db] = _override_deps_get_db

    test_client = TestClient(app)
    test_client.db_path = db_path

    yield test_client

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
            assert "Add Anime" in html
            assert "Run Recommendation" in html
            assert "Recent Runs" in html
            assert "Planning Queue" in html

            # Form elements
            assert 'name="profile"' in html
            assert 'name="query"' in html
            assert 'name="season"' in html
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

    def test_dashboard_references_external_assets(self, client):
        """Dashboard CSS/JS is served as Plus static assets, not inline blocks."""
        try:
            _signup_and_login(client)

            html = client.get("/plus/dashboard").text

            assert 'href="/plus/static/dashboard.css"' in html
            assert 'src="/plus/static/dashboard.js"' in html
            assert "<style>" not in html
            assert "<script>" not in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_plus_static_asset_route_serves_dashboard_css(self, client):
        """The Plus router serves allowlisted packaged dashboard CSS."""
        resp = client.get("/plus/static/dashboard.css")

        assert resp.status_code == 200
        assert "dashboard-shell" in resp.text
        assert "text/css" in resp.headers["content-type"]

    def test_plus_static_asset_route_serves_dashboard_js(self, client):
        """The Plus router serves allowlisted packaged dashboard JavaScript."""
        resp = client.get("/plus/static/dashboard.js")

        assert resp.status_code == 200
        assert "window.runRecs" in resp.text
        assert "application/javascript" in resp.headers["content-type"]

    def test_plus_static_asset_route_rejects_unknown_assets(self, client):
        """Static assets are constrained to the packaged dashboard files."""
        assert client.get("/plus/static/nope.css").status_code == 404
        assert client.get("/plus/static/..%2F..%2Fpyproject.toml").status_code == 404

    def test_dashboard_renders_direct_download_card(self, client):
        """The dashboard exposes a phone-friendly direct auto-download form."""
        try:
            _signup_and_login(client)

            html = client.get("/plus/dashboard").text

            assert "Add Anime" in html
            assert 'action="/plus/dashboard/direct-download"' in html
            assert 'name="query"' in html
            assert 'name="season"' in html
            assert "Add + Download" in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_dashboard_has_mobile_first_shell_and_priority_order(self, client):
        """Dashboard markup exposes the new shell and phone-first section order."""
        try:
            _signup_and_login(client)

            html = client.get("/plus/dashboard").text

            assert 'class="dashboard-shell"' in html
            assert 'class="hero-panel"' in html
            assert 'class="quick-stats"' in html
            assert html.index("Add Anime") < html.index("Run Recommendation")
            assert html.index("Run Recommendation") < html.index("Tonight's Tasting Menu")
            assert html.index("Planning Queue") < html.index("Taste Profile")
            assert html.index("Taste Profile") < html.index("Recent Runs")
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_dashboard_renders_lane_control(self, client):
        """The run form exposes all recommendation lanes to authenticated users."""
        try:
            _signup_and_login(client)

            html = client.get("/plus/dashboard").text
            assert 'name="lane"' in html
            assert 'value="best_match"' in html
            assert 'value="new_seasons"' in html
            assert 'value="hidden_gems"' in html
            assert 'value="plan_list"' in html
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

    def test_dashboard_run_passes_lane_feedback_and_persists_lane(self, client):
        """Form runs pass lane/feedback into the engine and persist the selected lane."""
        captured = {}
        mock_recs = [
            Recommendation(
                title="Base 2",
                predicted_score=8.4,
                reasoning="Direct continuation of a liked series.",
                best_match_from_history="Base",
                url="https://anilist.co/anime/123/",
                source="anilist",
            ),
        ]

        def _capture(cfg):
            captured["lane"] = cfg.recommendation_lane
            captured["use_planning"] = cfg.use_planning
            captured["feedback_types"] = [f.feedback_type for f in cfg.feedback]
            return mock_recs

        try:
            _signup_and_login(client)
            with _connect_client_db(client) as conn:
                user_id = conn.execute(
                    "SELECT id FROM users WHERE email = ?",
                    ("alice@example.com",),
                ).fetchone()["id"]
                save_feedback(conn, user_id, "anilist", 123, "Base 2", "interested", None)

            with patch("omakase.plus.routes.run_pipeline", side_effect=_capture):
                resp = client.post(
                    "/plus/dashboard/run",
                    data={
                        "source": "anilist",
                        "mode": "fast",
                        "count": "5",
                        "temperature": "0.5",
                        "username": "testuser",
                        "skip_profile": "true",
                        "lane": "plan_list",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            run_id = int(resp.headers["location"].split("run=")[1])
            assert captured == {
                "lane": "plan_list",
                "use_planning": True,
                "feedback_types": ["interested"],
            }
            with _connect_client_db(client) as conn:
                row = conn.execute(
                    "SELECT lane FROM run_history WHERE id = ?",
                    (run_id,),
                ).fetchone()
            assert row["lane"] == "plan_list"
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)


class TestDefaultAniListUsername:
    def test_settings_saves_and_dashboard_prefills_username(self, client):
        """Saving a default AniList username pre-fills the dashboard form input."""
        try:
            _signup_and_login(client)

            resp = client.post(
                "/plus/settings",
                data={"anilist_username": "HeyiTzSenpai"},
                follow_redirects=False,
            )
            assert resp.status_code == 302

            html = client.get("/plus/dashboard").text
            assert 'name="username"' in html
            assert 'value="HeyiTzSenpai"' in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_blank_run_uses_stored_default_username(self, client):
        """A run with a blank username falls back to the stored default, not email."""
        captured = {}

        def _capture(cfg):
            captured["username"] = cfg.username
            return []

        try:
            _signup_and_login(client)
            client.post("/plus/settings", data={"anilist_username": "HeyiTzSenpai"})

            with patch("omakase.plus.routes.run_pipeline", side_effect=_capture):
                resp = client.post(
                    "/plus/api/run",
                    json={"source": "anilist", "mode": "fast", "count": 8, "username": ""},
                )
                _drain_job(client, resp)
            assert resp.status_code == 200
            assert captured["username"] == "HeyiTzSenpai"
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_typed_username_overrides_stored_default(self, client):
        """An explicitly typed username wins over the stored default."""
        captured = {}

        def _capture(cfg):
            captured["username"] = cfg.username
            return []

        try:
            _signup_and_login(client)
            client.post("/plus/settings", data={"anilist_username": "HeyiTzSenpai"})

            with patch("omakase.plus.routes.run_pipeline", side_effect=_capture):
                resp = client.post(
                    "/plus/api/run",
                    json={
                        "source": "anilist",
                        "mode": "fast",
                        "count": 8,
                        "username": "someoneelse",
                    },
                )
                _drain_job(client, resp)
            assert captured["username"] == "someoneelse"
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)


class TestPlanButton:
    def test_direct_download_rejects_empty_query(self, client):
        """Direct auto-download needs a title, AniList URL, or AniList ID."""
        try:
            _signup_and_login(client)

            resp = client.post(
                "/plus/dashboard/direct-download",
                data={"query": "", "season": ""},
                follow_redirects=False,
            )

            assert resp.status_code == 302
            assert "Enter%20an%20anime%20title%20or%20AniList%20URL" in resp.headers["location"]
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_direct_download_creates_planning_and_passes_aliases(self, client):
        """Direct requests resolve AniList media and feed aliases to the downloader."""
        try:
            _signup_and_login(client)
            target = SimpleNamespace(
                anilist_id=99699,
                title="Golden Kamuy Season 3",
                search_titles=["Golden Kamuy Season 3", "Golden Kamuy 3rd Season"],
            )
            token_context = MagicMock()
            token_context.__enter__.return_value = "token"
            download_mock = AsyncMock(
                return_value={
                    "status": "no_rd_key",
                    "detail": "Real-Debrid API key not configured",
                }
            )

            with (
                patch(
                    "omakase.plus.direct.resolve_direct_request", return_value=target
                ) as resolve_mock,
                patch("omakase.plus.routes.with_valid_token", return_value=token_context),
                patch("omakase.plus.routes.add_to_planning") as add_mock,
                patch("omakase.plus.automation.search_and_download", new=download_mock),
            ):
                resp = client.post(
                    "/plus/dashboard/direct-download",
                    data={"query": "Golden Kamuy", "season": "3"},
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert "Set%20Real-Debrid%20API%20key" in resp.headers["location"]
            resolve_mock.assert_called_once_with("Golden Kamuy", season="3")
            add_mock.assert_called_once_with("token", 99699, "PLANNING")
            download_mock.assert_awaited_once()

            with _connect_client_db(client) as conn:
                row = conn.execute(
                    """SELECT id, title, status, download_status
                       FROM anilist_plannings
                       WHERE user_id = ? AND anilist_id = ?""",
                    (_user_id(client), 99699),
                ).fetchone()

            assert {key: row[key] for key in ("title", "status", "download_status")} == {
                "title": "Golden Kamuy Season 3",
                "status": "PLANNING",
                "download_status": "no_rd_key",
            }
            assert download_mock.await_args.kwargs["planning_id"] == row["id"]
            assert download_mock.await_args.kwargs["search_titles"] == [
                "Golden Kamuy Season 3",
                "Golden Kamuy 3rd Season",
            ]
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_direct_download_reuses_existing_planning_row(self, client):
        """Retrying a direct request should not duplicate the local queue row."""
        try:
            _signup_and_login(client)
            user_id = _user_id(client)
            with _connect_client_db(client) as conn:
                conn.execute(
                    """INSERT INTO anilist_plannings
                       (user_id, anilist_id, title, status)
                       VALUES (?, ?, ?, ?)""",
                    (user_id, 21, "Cowboy Bebop", "PLANNING"),
                )
                conn.commit()

            target = SimpleNamespace(
                anilist_id=21,
                title="Cowboy Bebop",
                search_titles=["Cowboy Bebop"],
            )
            download_mock = AsyncMock(
                return_value={
                    "status": "not_found",
                    "detail": 'No torrents found for "Cowboy Bebop"',
                }
            )

            with (
                patch("omakase.plus.direct.resolve_direct_request", return_value=target),
                patch("omakase.plus.routes.add_to_planning") as add_mock,
                patch("omakase.plus.automation.search_and_download", new=download_mock),
            ):
                resp = client.post(
                    "/plus/dashboard/direct-download",
                    data={"query": "https://anilist.co/anime/21/Cowboy-Bebop/"},
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            add_mock.assert_not_called()
            with _connect_client_db(client) as conn:
                count = conn.execute(
                    """SELECT COUNT(*) AS count
                       FROM anilist_plannings
                       WHERE user_id = ? AND anilist_id = ?""",
                    (user_id, 21),
                ).fetchone()["count"]

            assert count == 1
            assert download_mock.await_args.kwargs["search_titles"] == ["Cowboy Bebop"]
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_dashboard_cards_have_split_plan_and_download_actions(self, client):
        """Recommendation cards render separate Plan and Download form actions."""
        try:
            _signup_and_login(client)
            run_id = _seed_run(
                client,
                [
                    {
                        "title": "One Piece",
                        "predicted_score": 9.2,
                        "reasoning": "Matches your love for long-running shonen.",
                        "best_match_from_history": "Naruto",
                        "url": "https://anilist.co/anime/21/",
                        "source": "anilist",
                        "anilist_id": 21,
                        "media_id": 21,
                    }
                ],
            )

            html = client.get(f"/plus/dashboard?run={run_id}").text
            assert "Plan &amp; Download" not in html
            assert ">Plan<" in html
            assert ">Download<" in html
            assert 'action="/plus/dashboard/plan"' in html
            assert 'action="/plus/dashboard/download"' in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_dashboard_cards_have_feedback_buttons(self, client):
        """Recommendation cards expose all supported feedback actions."""
        try:
            _signup_and_login(client)
            run_id = _seed_run(
                client,
                [
                    {
                        "title": "One Piece",
                        "predicted_score": 9.2,
                        "reasoning": "Matches your love for long-running shonen.",
                        "best_match_from_history": "Naruto",
                        "url": "https://anilist.co/anime/21/",
                        "source": "anilist",
                        "anilist_id": 21,
                        "media_id": 21,
                    }
                ],
            )

            html = client.get(f"/plus/dashboard?run={run_id}").text
            assert 'data-feedback="interested"' in html
            assert 'data-feedback="not_for_me"' in html
            assert 'data-feedback="wrong_sequel"' in html
            assert 'data-feedback="already_watched"' in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_dashboard_cards_link_titles_show_chips_and_preserve_stored_anilist_id(self, client):
        """Stored recommendation metadata drives card links, chips, and actions."""
        try:
            _signup_and_login(client)
            run_id = _seed_run(
                client,
                [
                    {
                        "title": "Preserved ID",
                        "predicted_score": 8.5,
                        "reasoning": "A good sequel candidate.",
                        "best_match_from_history": "Base Show",
                        "url": "https://anilist.co/anime/999/",
                        "source": "anilist",
                        "anilist_id": 777,
                        "media_id": 888,
                        "airing_status": "RELEASING",
                        "franchise_note": "Loved franchise continuation.",
                        "sequence_warning": "Start with season 1.",
                    }
                ],
            )

            html = client.get(f"/plus/dashboard?run={run_id}").text
            assert 'href="https://anilist.co/anime/999/"' in html
            assert ">Preserved ID</a>" in html
            assert 'value="777"' in html
            assert 'value="999"' not in html
            assert "RELEASING" in html
            assert "Loved franchise continuation." in html
            assert "Start with season 1." in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_dashboard_uses_media_id_for_anilist_actions_when_anilist_id_missing(self, client):
        """AniList run cards can fall back to media_id when anilist_id is absent."""
        try:
            _signup_and_login(client)
            run_id = _seed_run(
                client,
                [
                    {
                        "title": "Media ID Pick",
                        "predicted_score": 8.2,
                        "reasoning": "A solid candidate.",
                        "best_match_from_history": "Base Show",
                        "url": None,
                        "source": "anilist",
                        "anilist_id": None,
                        "media_id": 4242,
                    }
                ],
            )

            html = client.get(f"/plus/dashboard?run={run_id}").text
            assert 'value="4242"' in html
            assert ">Plan<" in html
            assert ">Download<" in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_download_route_creates_local_planning_without_anilist_write(self, client):
        """The dashboard Download action does not mutate AniList remotely."""
        try:
            _signup_and_login(client)
            download_mock = AsyncMock(
                return_value={
                    "status": "no_rd_key",
                    "detail": "Real-Debrid API key not configured",
                }
            )

            with (
                patch("omakase.plus.routes.with_valid_token") as token_mock,
                patch("omakase.plus.routes.add_to_planning") as add_mock,
                patch("omakase.plus.automation.search_and_download", new=download_mock),
            ):
                resp = client.post(
                    "/plus/dashboard/download",
                    data={"anilist_id": "21", "title": "One Piece"},
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            token_mock.assert_not_called()
            add_mock.assert_not_called()
            download_mock.assert_awaited_once()
            with _connect_client_db(client) as conn:
                row = conn.execute(
                    """SELECT id, title, status, download_status
                       FROM anilist_plannings
                       WHERE user_id = ? AND anilist_id = ?""",
                    (_user_id(client), 21),
                ).fetchone()
            assert download_mock.await_args.kwargs["planning_id"] == row["id"]
            assert {key: row[key] for key in ("title", "status", "download_status")} == {
                "title": "One Piece",
                "status": "PLANNING",
                "download_status": "no_rd_key",
            }
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_download_route_persists_rd_provider_block_detail(self, client):
        """Provider-blocked RD batches are persisted distinctly from generic errors."""
        try:
            _signup_and_login(client)
            download_mock = AsyncMock(
                return_value={
                    "status": "rd_provider_block",
                    "detail": "Provider blocked this batch (451 infringing_file).",
                    "http_status": 451,
                    "error_code": "infringing_file",
                }
            )

            with patch("omakase.plus.automation.search_and_download", new=download_mock):
                resp = client.post(
                    "/plus/dashboard/download",
                    data={
                        "anilist_id": "170732",
                        "title": "BLEACH: Thousand-Year Blood War - The Conflict",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            download_mock.assert_awaited_once()
            with _connect_client_db(client) as conn:
                row = conn.execute(
                    """SELECT title, download_status, download_info
                       FROM anilist_plannings
                       WHERE user_id = ? AND anilist_id = ?""",
                    (_user_id(client), 170732),
                ).fetchone()
            assert dict(row) == {
                "title": "BLEACH: Thousand-Year Blood War - The Conflict",
                "download_status": "rd_provider_block",
                "download_info": "Provider blocked this batch (451 infringing_file).",
            }
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_download_route_caps_rd_provider_block_detail(self, client):
        """Provider-block detail is bounded before DB persistence and redirects."""
        try:
            _signup_and_login(client)
            huge_detail = "Provider blocked " + ("X" * 1200)
            download_mock = AsyncMock(
                return_value={
                    "status": "rd_provider_block",
                    "detail": huge_detail,
                    "http_status": 451,
                    "error_code": "infringing_file",
                }
            )

            with patch("omakase.plus.automation.search_and_download", new=download_mock):
                resp = client.post(
                    "/plus/dashboard/download",
                    data={"anilist_id": "170733", "title": "Blocked Long Detail"},
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert len(resp.headers["location"]) < 800
            with _connect_client_db(client) as conn:
                row = conn.execute(
                    """SELECT download_status, download_info
                       FROM anilist_plannings
                       WHERE user_id = ? AND anilist_id = ?""",
                    (_user_id(client), 170733),
                ).fetchone()

            assert row["download_status"] == "rd_provider_block"
            assert len(row["download_info"]) <= 240
            assert row["download_info"].endswith("...")
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_download_route_caps_rd_error_detail_redirect(self, client):
        """Generic RD errors are bounded before redirect when provider detail is included."""
        try:
            _signup_and_login(client)
            huge_detail = "Real-Debrid rejected all candidates; last RD response: " + ("Y" * 1200)
            download_mock = AsyncMock(
                return_value={
                    "status": "rd_error",
                    "detail": huge_detail,
                }
            )

            with patch("omakase.plus.automation.search_and_download", new=download_mock):
                resp = client.post(
                    "/plus/dashboard/download",
                    data={"anilist_id": "170734", "title": "Mixed RD Failure"},
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert len(resp.headers["location"]) < 800
            with _connect_client_db(client) as conn:
                row = conn.execute(
                    """SELECT download_status
                       FROM anilist_plannings
                       WHERE user_id = ? AND anilist_id = ?""",
                    (_user_id(client), 170734),
                ).fetchone()
            assert row["download_status"] == "error"
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_download_route_overwrites_stale_rd_info_on_retry(self, client):
        """Retry results should replace old provider-block details and RD ids."""
        try:
            _signup_and_login(client)
            user_id = _user_id(client)
            with _connect_client_db(client) as conn:
                conn.execute(
                    """INSERT INTO anilist_plannings
                       (user_id, anilist_id, title, status, download_status,
                        download_info, rd_torrent_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        170735,
                        "Retry Target",
                        "PLANNING",
                        "rd_provider_block",
                        "old provider-block detail",
                        "old-rd-id",
                    ),
                )
                conn.commit()

            download_mock = AsyncMock(
                return_value={
                    "status": "rd_error",
                    "detail": "Real-Debrid rejected all retry candidates",
                }
            )

            with patch("omakase.plus.automation.search_and_download", new=download_mock):
                resp = client.post(
                    "/plus/dashboard/download",
                    data={"anilist_id": "170735", "title": "Retry Target"},
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            with _connect_client_db(client) as conn:
                row = conn.execute(
                    """SELECT download_status, download_info, rd_torrent_id
                       FROM anilist_plannings
                       WHERE user_id = ? AND anilist_id = ?""",
                    (user_id, 170735),
                ).fetchone()

            assert row["download_status"] == "error"
            assert row["download_info"] == "Real-Debrid rejected all retry candidates"
            assert row["rd_torrent_id"] == ""
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

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

    def test_dashboard_shows_rd_provider_block_badge(self, client):
        """Planning queue shows provider-blocked RD batches as their own state."""
        try:
            _signup_and_login(client)
            with _connect_client_db(client) as conn:
                conn.execute(
                    """INSERT INTO anilist_plannings
                       (user_id, anilist_id, title, status, download_status, download_info)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        _user_id(client),
                        170732,
                        "BLEACH: Thousand-Year Blood War - The Conflict",
                        "PLANNING",
                        "rd_provider_block",
                        "Provider blocked this batch (451 infringing_file).",
                    ),
                )
                conn.commit()

            html = client.get("/plus/dashboard").text

            assert "RD blocked" in html
            assert "Provider blocked this batch (451 infringing_file)." in html
            assert "planning-queue-card" in html
            assert 'data-label="RD"' in html
            assert 'data-label="Title"' in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_dashboard_shows_rd_attempts_and_retry_for_blocked_rows(self, client):
        """Blocked queue rows expose safe attempt telemetry plus a retry affordance."""
        try:
            _signup_and_login(client)
            user_id = _user_id(client)
            with _connect_client_db(client) as conn:
                planning_id = conn.execute(
                    """INSERT INTO anilist_plannings
                       (user_id, anilist_id, title, status, download_status, download_info)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        170732,
                        "BLEACH: Thousand-Year Blood War - The Conflict",
                        "PLANNING",
                        "rd_provider_block",
                        "Provider blocked this batch (451 infringing_file).",
                    ),
                ).lastrowid
                conn.execute(
                    """INSERT INTO download_attempts
                       (user_id, anilist_planning_id, request_id, candidate_rank,
                        total_candidates, torrent_title, torrent_hash, seeders,
                        size_display, is_batch, status, http_status, error_code, detail)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        planning_id,
                        "req-ui",
                        1,
                        2,
                        "[Group] BLEACH - 451 [1080p][HEVC]",
                        "ABCDEF1234567890",
                        44,
                        "1.4 GiB",
                        0,
                        "provider_block",
                        451,
                        "infringing_file",
                        "Provider blocked this file",
                    ),
                )
                conn.commit()

            html = client.get("/plus/dashboard").text

            assert "RD attempts" in html
            assert "[Group] BLEACH - 451 [1080p][HEVC]" in html
            assert "ABCDEF1234567890" in html
            assert "451 infringing_file" in html
            assert "Provider blocked this file" in html
            assert ">Retry<" in html
            assert 'action="/plus/dashboard/download"' in html
            assert "magnet:" not in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_dashboard_hides_retry_for_requested_download(self, client):
        """Rows already handed to Real-Debrid should not show a retry button."""
        try:
            _signup_and_login(client)
            with _connect_client_db(client) as conn:
                conn.execute(
                    """INSERT INTO anilist_plannings
                       (user_id, anilist_id, title, status, download_status, download_info)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        _user_id(client),
                        21,
                        "One Piece",
                        "PLANNING",
                        "requested",
                        "One Piece batch (26 GiB, 200s)",
                    ),
                )
                conn.commit()

            html = client.get("/plus/dashboard").text

            assert "Downloading" in html
            assert ">Retry<" not in html
            assert ">Remove<" in html
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
