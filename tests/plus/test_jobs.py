"""Tests for the async job + polling lifecycle of /plus/api/run.

The recommendation run executes in a background thread so the HTTP request
returns immediately with a ``job_id``; the client then polls a status
endpoint. This decouples the (multi-minute) LLM run from any single
request's lifetime, which is what previously tripped the reverse-proxy
read timeout.

Uses FastAPI TestClient with a per-test temp database, mirroring
``test_dashboard.py``.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from unittest.mock import patch

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
    for c in _db.values():
        try:
            c.close()
        except Exception:
            pass
    _db.clear()


def _signup_and_login(client: TestClient) -> None:
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
    assert "omakase_session" in resp.cookies


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    """Poll the status endpoint until the job leaves the 'running' state.

    Must be called inside the ``patch`` context so the background worker
    sees the mocked pipeline.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/plus/api/run/status/{job_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        if data.get("status") != "running":
            return data
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


_MOCK_RECS = [
    Recommendation(
        title="Vinland Saga",
        predicted_score=9.1,
        reasoning="Morally complex protagonist, earns its arc.",
        best_match_from_history="Berserk",
        url="https://anilist.co/anime/101348/",
        source="anilist",
    ),
    Recommendation(
        title="Monster",
        predicted_score=8.8,
        reasoning="Psychological, slow-burn, dense.",
        best_match_from_history="Death Note",
        url="https://anilist.co/anime/19/",
        source="anilist",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    _login_attempts.clear()


@pytest.fixture
def client():
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


class TestJobLifecycle:
    def test_run_returns_job_id_immediately(self, client):
        """POST /plus/api/run returns a job_id without waiting for the run."""
        try:
            _signup_and_login(client)
            with patch("omakase.plus.routes.run_pipeline", return_value=_MOCK_RECS):
                resp = client.post(
                    "/plus/api/run",
                    json={"source": "anilist", "mode": "pro", "count": 8, "skip_profile": True},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "running"
                assert data["job_id"]
                # Drain the worker so the patch stays active until it runs.
                _wait_for_job(client, data["job_id"])
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_job_completes_with_recommendations(self, client):
        """Polling a finished job returns the recommendations the pipeline produced."""
        try:
            _signup_and_login(client)
            with patch("omakase.plus.routes.run_pipeline", return_value=_MOCK_RECS):
                resp = client.post(
                    "/plus/api/run",
                    json={"source": "anilist", "mode": "pro", "count": 8, "skip_profile": True},
                )
                job_id = resp.json()["job_id"]
                result = _wait_for_job(client, job_id)

            assert result["status"] == "ok"
            assert result["run_id"]
            titles = [r["title"] for r in result["recommendations"]]
            assert titles == ["Vinland Saga", "Monster"]
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_completed_job_persists_to_run_history(self, client):
        """A finished run is written to run_history and shows on the dashboard."""
        try:
            _signup_and_login(client)
            with patch("omakase.plus.routes.run_pipeline", return_value=_MOCK_RECS):
                resp = client.post(
                    "/plus/api/run",
                    json={"source": "anilist", "mode": "pro", "count": 8, "skip_profile": True},
                )
                job_id = resp.json()["job_id"]
                result = _wait_for_job(client, job_id)

            run_id = result["run_id"]
            html = client.get(f"/plus/dashboard?run={run_id}").text
            assert "Vinland Saga" in html
            assert "Monster" in html
            assert "Recent Runs" in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_job_reports_pipeline_error(self, client):
        """A pipeline exception surfaces as status 'error' with a detail, no run saved."""
        try:
            _signup_and_login(client)
            with patch(
                "omakase.plus.routes.run_pipeline",
                side_effect=RuntimeError("DeepSeek refused the request"),
            ):
                resp = client.post(
                    "/plus/api/run",
                    json={"source": "anilist", "mode": "pro", "count": 8, "skip_profile": True},
                )
                job_id = resp.json()["job_id"]
                result = _wait_for_job(client, job_id)

            assert result["status"] == "error"
            assert "DeepSeek refused the request" in result["detail"]

            # No run_history row was written.
            html = client.get("/plus/dashboard").text
            assert "Vinland Saga" not in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_status_requires_auth(self, client):
        """Polling job status without a session is rejected."""
        resp = client.get("/plus/api/run/status/whatever", follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_unknown_job_reports_error(self, client):
        """Polling a job id that doesn't exist returns a clean error, not a 500."""
        try:
            _signup_and_login(client)
            resp = client.get("/plus/api/run/status/does-not-exist")
            assert resp.status_code == 200
            assert resp.json()["status"] == "error"
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_job_reports_llm_parse_error(self, client):
        """A truncated/malformed LLM response surfaces as a clean error.

        Regression guard: this used to come through as ``status="ok"`` with
        an empty ``recommendations`` list — a "0 picks" run landed silently
        in the dashboard with no indication of what failed.
        """
        from omakase.engine import LLMOutputParseError

        try:
            _signup_and_login(client)
            with patch(
                "omakase.plus.routes.run_pipeline",
                side_effect=LLMOutputParseError(
                    "LLM output couldn't be parsed (truncated mid-string)."
                ),
            ):
                resp = client.post(
                    "/plus/api/run",
                    json={"source": "anilist", "mode": "pro", "count": 8, "skip_profile": True},
                )
                job_id = resp.json()["job_id"]
                result = _wait_for_job(client, job_id)

            assert result["status"] == "error"
            assert "truncated" in result["detail"].lower()

            # No silent 0-pick row was written.
            html = client.get("/plus/dashboard").text
            assert "Vinland Saga" not in html
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_api_run_passes_lane_and_feedback_to_pipeline(self, client, monkeypatch):
        """POST /plus/api/run passes the selected lane and local feedback into config."""
        seen = {}

        def fake_run(cfg):
            seen["lane"] = cfg.recommendation_lane
            seen["feedback_types"] = [f.feedback_type for f in cfg.feedback]
            return []

        try:
            _signup_and_login(client)
            with _connect_client_db(client) as conn:
                user_id = conn.execute(
                    "SELECT id FROM users WHERE email = ?",
                    ("alice@example.com",),
                ).fetchone()["id"]
                save_feedback(conn, user_id, "anilist", 123, "Base 2", "not_for_me", None)

            monkeypatch.setattr("omakase.plus.routes.run_pipeline", fake_run)
            monkeypatch.setattr(
                "omakase.plus.routes.read_secret",
                lambda db, user_id, key: "HeyiTzSenpai" if key == "anilist_username" else "",
            )
            response = client.post(
                "/plus/api/run",
                json={
                    "source": "anilist",
                    "username": "me",
                    "mode": "fast",
                    "count": 3,
                    "lane": "new_seasons",
                },
            )
            job_id = response.json()["job_id"]
            status = _wait_for_job(client, job_id)

            assert status["status"] == "ok"
            assert seen["lane"] == "new_seasons"
            assert "not_for_me" in seen["feedback_types"]
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_api_run_defaults_invalid_lane_to_best_match(self, client, monkeypatch):
        """Invalid public lane values fall back to best_match."""
        seen = {}

        def fake_run(cfg):
            seen["lane"] = cfg.recommendation_lane
            return []

        try:
            _signup_and_login(client)
            monkeypatch.setattr("omakase.plus.routes.run_pipeline", fake_run)
            response = client.post(
                "/plus/api/run",
                json={"source": "anilist", "mode": "fast", "count": 3, "lane": "surprise_me"},
            )
            status = _wait_for_job(client, response.json()["job_id"])

            assert status["status"] == "ok"
            assert status["lane"] == "best_match"
            assert seen["lane"] == "best_match"
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_api_run_plan_list_lane_forces_planning(self, client, monkeypatch):
        """The plan_list lane always uses the user's planning list as the source pool."""
        seen = {}

        def fake_run(cfg):
            seen["lane"] = cfg.recommendation_lane
            seen["use_planning"] = cfg.use_planning
            return []

        try:
            _signup_and_login(client)
            monkeypatch.setattr("omakase.plus.routes.run_pipeline", fake_run)
            response = client.post(
                "/plus/api/run",
                json={
                    "source": "anilist",
                    "mode": "fast",
                    "count": 3,
                    "lane": "plan_list",
                    "use_planning": False,
                },
            )
            status = _wait_for_job(client, response.json()["job_id"])

            assert status["status"] == "ok"
            assert seen == {"lane": "plan_list", "use_planning": True}
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_completed_job_persists_and_returns_lane(self, client):
        """A finished async run stores lane in run_history and returns it from status."""
        try:
            _signup_and_login(client)
            with patch("omakase.plus.routes.run_pipeline", return_value=_MOCK_RECS):
                response = client.post(
                    "/plus/api/run",
                    json={
                        "source": "anilist",
                        "mode": "pro",
                        "count": 8,
                        "skip_profile": True,
                        "lane": "hidden_gems",
                    },
                )
                result = _wait_for_job(client, response.json()["job_id"])

            assert result["status"] == "ok"
            assert result["lane"] == "hidden_gems"
            with _connect_client_db(client) as conn:
                row = conn.execute(
                    "SELECT lane FROM run_history WHERE id = ?",
                    (result["run_id"],),
                ).fetchone()
            assert row["lane"] == "hidden_gems"
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)
