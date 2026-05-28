"""Tests for Omakase Plus auth, routes, and admin CLI.

Requires a temporary database per test.  Uses FastAPI ``TestClient``
for route-level integration tests.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omakase.plus.auth import (
    create_session,
    delete_session,
    hash_password,
    validate_session,
    verify_password,
)
from omakase.plus.db import _db, run_migrations
from omakase.plus.deps import get_db as _deps_get_db
from omakase.plus.routes import _login_attempts

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_conn(db_dir: str) -> sqlite3.Connection:
    """Create a fresh SQLite connection at *db_dir*/omakase-plus.db.

    Runs migrations to set up schema.  Uses ``check_same_thread=False``
    so the connection works across TestClient's async thread boundary.
    """
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """Reset the in-memory rate limiter between tests."""
    _login_attempts.clear()


@pytest.fixture
def db():
    """Direct database connection in a temp directory (for auth unit tests)."""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _make_test_conn(tmp)
        yield conn
        conn.close()
        _cleanup_db_cache()


@pytest.fixture
def client():
    """FastAPI TestClient wired to a per-test temp database.

    The ``get_db`` dependency is overridden so each request obtains a
    *fresh* connection (``check_same_thread=False``) to the shared temp
    database file.  This avoids SQLite thread-safety errors because the
    TestClient runs the app in a dedicated asyncio event-loop thread.
    """
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
# Auth unit tests
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    def test_hash_and_verify(self):
        pw = "super-secure-password-123!"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True
        assert verify_password("wrong-password", hashed) is False

    def test_verify_invalid_hash_returns_false(self):
        assert verify_password("anything", "not-a-valid-argon2-hash") is False

    def test_verify_empty_string(self):
        hashed = hash_password("")
        assert verify_password("", hashed) is True
        assert verify_password("x", hashed) is False


class TestSessionManagement:
    def test_create_and_validate_session(self, db):
        db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            ("alice@example.com", hash_password("pw")),
        )
        db.commit()
        user_id = db.execute(
            "SELECT id FROM users WHERE email = ?", ("alice@example.com",)
        ).fetchone()["id"]

        session_id = create_session(db, user_id)
        assert len(session_id) == 64  # 32 bytes as hex

        validated = validate_session(db, session_id)
        assert validated == user_id

        # Fake session id returns None
        assert validate_session(db, "nonexistent") is None

    def test_delete_session(self, db):
        db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            ("bob@example.com", hash_password("pw")),
        )
        db.commit()
        user_id = db.execute(
            "SELECT id FROM users WHERE email = ?", ("bob@example.com",)
        ).fetchone()["id"]

        session_id = create_session(db, user_id)
        assert validate_session(db, session_id) == user_id

        delete_session(db, session_id)
        assert validate_session(db, session_id) is None


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------


class TestSignupAndLogin:
    def test_signup_and_login_flow(self, client):
        """Full signup -> login -> dashboard flow."""
        os.environ["OMAKASE_PLUS_INVITE"] = "test123"
        try:
            resp = client.post(
                "/plus/signup",
                data={
                    "email": "newuser@example.com",
                    "password": "secret123",
                    "confirm_password": "secret123",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert resp.headers["location"] == "/plus/dashboard"
            assert "omakase_session" in resp.cookies

            # Login with the same credentials
            resp2 = client.post(
                "/plus/login",
                data={"email": "newuser@example.com", "password": "secret123"},
                follow_redirects=False,
            )
            assert resp2.status_code == 302
            assert resp2.headers["location"] == "/plus/dashboard"
            assert "omakase_session" in resp2.cookies

            # Dashboard should be accessible
            resp3 = client.get("/plus/dashboard")
            assert resp3.status_code == 200
            assert "Dashboard (coming in Phase 5)" in resp3.text
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)

    def test_signup_gated_when_not_private(self, client):
        """Signup page redirects to login when invite/private is not set."""
        os.environ.pop("OMAKASE_PLUS_INVITE", None)
        os.environ["OMAKASE_PLUS_PRIVATE"] = "false"

        resp = client.get("/plus/signup", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/plus/login"

    def test_login_bad_password(self, client):
        """Login with wrong credentials re-renders the form with error."""
        resp = client.post(
            "/plus/login",
            data={"email": "nobody@example.com", "password": "wrong"},
        )
        assert resp.status_code == 200
        assert "Invalid email or password" in resp.text

    def test_login_rate_limit(self, client):
        """After 5 failed attempts, the 6th gets a 429."""
        for i in range(5):
            resp = client.post(
                "/plus/login",
                data={"email": "spam@example.com", "password": "wrong"},
            )
            assert resp.status_code == 200, f"Attempt {i+1} failed: {resp.status_code}"

        resp = client.post(
            "/plus/login",
            data={"email": "spam@example.com", "password": "wrong"},
        )
        assert resp.status_code == 429
        assert "Too many login attempts" in resp.text

    def test_dashboard_requires_login(self, client):
        """Accessing dashboard without a cookie returns 401 or 302."""
        resp = client.get("/plus/dashboard", follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_logout_clears_session(self, client):
        """After login and logout, dashboard is no longer accessible."""
        os.environ["OMAKASE_PLUS_INVITE"] = "test123"
        try:
            resp = client.post(
                "/plus/signup",
                data={
                    "email": "logouttest@example.com",
                    "password": "secret123",
                    "confirm_password": "secret123",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 302

            # Dashboard works before logout
            resp2 = client.get("/plus/dashboard")
            assert resp2.status_code == 200

            # Logout
            resp3 = client.post("/plus/logout", follow_redirects=False)
            assert resp3.status_code == 302
            assert resp3.headers["location"] == "/"

            # Dashboard should now redirect
            resp4 = client.get("/plus/dashboard", follow_redirects=False)
            assert resp4.status_code in (302, 401)
        finally:
            os.environ.pop("OMAKASE_PLUS_INVITE", None)
