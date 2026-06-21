from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omakase.plus.db import _db, run_migrations
from omakase.plus.deps import get_db as _deps_get_db
from omakase.plus.feedback import feedback_for_prompt, save_feedback
from omakase.plus.routes import _login_attempts


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    run_migrations(conn)
    return conn


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


def create_user(conn: sqlite3.Connection, email: str) -> int:
    return conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        (email, "hash"),
    ).lastrowid


def create_run(conn: sqlite3.Connection, user_id: int, run_id: int) -> int:
    return conn.execute(
        "INSERT INTO run_history (id, user_id, source, model, picks) VALUES (?, ?, ?, ?, ?)",
        (run_id, user_id, "anilist", "gpt-4o-mini", "[]"),
    ).lastrowid


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


def _current_user_id(client: TestClient) -> int:
    with _connect_client_db(client) as conn:
        return conn.execute(
            "SELECT id FROM users WHERE email = ?",
            ("alice@example.com",),
        ).fetchone()["id"]


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


def test_save_feedback_and_prompt_summary():
    conn = db()
    user_id = create_user(conn, "user@example.com")
    run_id = create_run(conn, user_id, 4)

    save_feedback(conn, user_id, "anilist", 123, "Base 2", "interested", run_id)

    signals = feedback_for_prompt(conn, user_id)
    assert signals[0].media_id == 123
    assert signals[0].feedback_type == "interested"


def test_save_feedback_rejects_invalid_feedback_type():
    conn = db()
    user_id = create_user(conn, "invalid@example.com")

    with pytest.raises(ValueError, match="Unsupported feedback type"):
        save_feedback(conn, user_id, "anilist", 123, "Base 2", "maybe", None)


def test_feedback_for_prompt_scopes_user_newest_first_and_respects_limit():
    conn = db()
    user_id = create_user(conn, "scoped@example.com")
    other_user_id = create_user(conn, "other@example.com")

    save_feedback(conn, user_id, "anilist", 101, "First", "interested", None)
    save_feedback(conn, other_user_id, "anilist", 999, "Other", "not_for_me", None)
    save_feedback(conn, user_id, "anilist", 102, "Second", "not_for_me", None)
    save_feedback(conn, user_id, "anilist", 103, "Third", "already_watched", None)

    signals = feedback_for_prompt(conn, user_id, limit=2)

    assert [signal.media_id for signal in signals] == [103, 102]
    assert [signal.title for signal in signals] == ["Third", "Second"]


def test_feedback_api_saves_row(client):
    try:
        _signup_and_login(client)
        response = client.post(
            "/plus/api/feedback",
            json={
                "source": "anilist",
                "media_id": 123,
                "title": "Base 2",
                "feedback_type": "not_for_me",
                "run_id": None,
            },
        )

        assert response.json()["status"] == "ok"
        with _connect_client_db(client) as conn:
            signals = feedback_for_prompt(conn, _current_user_id(client))
        assert signals[0].feedback_type == "not_for_me"
        assert signals[0].media_id == 123
    finally:
        os.environ.pop("OMAKASE_PLUS_INVITE", None)


def test_feedback_api_rejects_invalid_feedback_type(client):
    try:
        _signup_and_login(client)
        response = client.post(
            "/plus/api/feedback",
            json={
                "source": "anilist",
                "media_id": 123,
                "title": "Base 2",
                "feedback_type": "maybe",
                "run_id": None,
            },
        )

        assert response.json()["status"] == "error"
        assert "Unsupported feedback type" in response.json()["detail"]
    finally:
        os.environ.pop("OMAKASE_PLUS_INVITE", None)


def test_feedback_api_requires_title(client):
    try:
        _signup_and_login(client)
        response = client.post(
            "/plus/api/feedback",
            json={
                "source": "anilist",
                "media_id": 123,
                "title": "",
                "feedback_type": "not_for_me",
                "run_id": None,
            },
        )

        assert response.json() == {"status": "error", "detail": "title is required"}
    finally:
        os.environ.pop("OMAKASE_PLUS_INVITE", None)


def test_feedback_api_rejects_invalid_json_body(client):
    try:
        _signup_and_login(client)
        response = client.post(
            "/plus/api/feedback",
            content="not-json",
            headers={"content-type": "application/json"},
        )

        assert response.json() == {"status": "error", "detail": "Invalid JSON body"}
    finally:
        os.environ.pop("OMAKASE_PLUS_INVITE", None)


def test_feedback_api_coerces_integer_string_ids(client):
    try:
        _signup_and_login(client)
        user_id = _current_user_id(client)
        with _connect_client_db(client) as conn:
            run_id = create_run(conn, user_id, 4)
            conn.commit()

        response = client.post(
            "/plus/api/feedback",
            json={
                "source": "anilist",
                "media_id": "123",
                "title": "Base 2",
                "feedback_type": "interested",
                "run_id": str(run_id),
            },
        )

        assert response.json()["status"] == "ok"
        with _connect_client_db(client) as conn:
            row = conn.execute(
                "SELECT media_id, run_id FROM recommendation_feedback WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        assert row["media_id"] == 123
        assert row["run_id"] == run_id
    finally:
        os.environ.pop("OMAKASE_PLUS_INVITE", None)


def test_feedback_api_accepts_blank_optional_ids(client):
    try:
        _signup_and_login(client)
        response = client.post(
            "/plus/api/feedback",
            json={
                "source": "anilist",
                "media_id": "",
                "title": "Base 2",
                "feedback_type": "already_watched",
                "run_id": "",
            },
        )

        assert response.json()["status"] == "ok"
        with _connect_client_db(client) as conn:
            row = conn.execute(
                "SELECT media_id, run_id FROM recommendation_feedback WHERE user_id = ?",
                (_current_user_id(client),),
            ).fetchone()
        assert row["media_id"] is None
        assert row["run_id"] is None
    finally:
        os.environ.pop("OMAKASE_PLUS_INVITE", None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("media_id", "not-an-int"),
        ("run_id", "not-an-int"),
    ],
)
def test_feedback_api_rejects_invalid_integer_ids(client, field, value):
    try:
        _signup_and_login(client)
        payload = {
            "source": "anilist",
            "media_id": 123,
            "title": "Base 2",
            "feedback_type": "not_for_me",
            "run_id": None,
        }
        payload[field] = value

        response = client.post("/plus/api/feedback", json=payload)

        assert response.json() == {
            "status": "error",
            "detail": f"{field} must be an integer",
        }
    finally:
        os.environ.pop("OMAKASE_PLUS_INVITE", None)
