"""Tests for the omakase Plus schema and database layer."""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from omakase.plus import db as plus_db
from omakase.plus.db import _db, get_db, run_migrations


@pytest.fixture(autouse=True)
def _fresh_db():
    """Provide a fresh database connection in a temp directory per test.

    Clears the module-level connection cache on teardown so subsequent
    tests start with a clean slate.
    """
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_db(tmp)
        yield conn
        conn.close()
        _db.clear()


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------


def test_migration_001_applies_cleanly():
    """Applying migration 001 twice should be a no-op (no error)."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "omakase-plus.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        run_migrations(conn)
        conn.close()

        # Second connection to the same file — run migrations again
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        run_migrations(conn)
        conn.close()


def test_all_tables_exist(_fresh_db):
    """Verify all expected tables are present after migration."""
    conn = _fresh_db
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    tables = {row["name"] for row in cursor.fetchall()}

    expected = {
        "users",
        "sessions",
        "user_secrets",
        "taste_profiles",
        "run_history",
        "anilist_plannings",
        "overseerr_requests",
        "recommendation_feedback",
        "_migrations",  # internal tracking table
    }
    assert tables == expected, (
        f"Mismatch — missing: {expected - tables}, extra: {tables - expected}"
    )
    assert len(tables) == 9


def test_recommendation_feedback_migration_runs_before_existing_lane_error(monkeypatch, tmp_path):
    """Feedback table should exist even if run_history.lane was added before migration 003."""
    first_pass_dir = tmp_path / "migrations-without-003"
    first_pass_dir.mkdir()
    migrations_dir = plus_db._MIGRATIONS_DIR

    for path in migrations_dir.iterdir():
        if path.name == "003-recommendation-intelligence.sql":
            continue
        if path.suffix == ".sql":
            (first_pass_dir / path.name).write_text(path.read_text(encoding="utf-8"))

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(plus_db, "_MIGRATIONS_DIR", first_pass_dir)
    plus_db.run_migrations(conn)

    conn.execute("ALTER TABLE run_history ADD COLUMN lane TEXT NOT NULL DEFAULT 'best_match'")
    conn.commit()

    monkeypatch.setattr(plus_db, "_MIGRATIONS_DIR", migrations_dir)
    plus_db.run_migrations(conn)

    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        ("recommendation_feedback",),
    ).fetchone()
    assert row is not None


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


def test_user_roundtrip(_fresh_db):
    """Insert a user and read it back, verifying email."""
    conn = _fresh_db
    conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        ("alice@example.com", "hashed_pw_123"),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM users WHERE email = ?", ("alice@example.com",)).fetchone()
    assert row is not None
    assert row["email"] == "alice@example.com"
    assert row["password_hash"] == "hashed_pw_123"
    assert row["id"] == 1
    assert row["created_at"] is not None


def test_session_roundtrip(_fresh_db):
    """Insert a session with a FK to an existing user and read it back."""
    conn = _fresh_db
    conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        ("bob@example.com", "hashed_pw_456"),
    )
    conn.commit()
    (user_id,) = conn.execute(
        "SELECT id FROM users WHERE email = ?", ("bob@example.com",)
    ).fetchone()

    session_id = "sess_abc123"
    conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
        (session_id, user_id, "2026-06-01T00:00:00"),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    assert row is not None
    assert row["id"] == session_id
    assert row["user_id"] == user_id
    assert row["expires_at"] == "2026-06-01T00:00:00"
    assert row["created_at"] is not None


def test_user_secret_unique_constraint(_fresh_db):
    """Inserting two secrets with the same (user_id, key_name) must fail."""
    conn = _fresh_db
    conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        ("carol@example.com", "hashed_pw_789"),
    )
    conn.commit()
    (user_id,) = conn.execute(
        "SELECT id FROM users WHERE email = ?", ("carol@example.com",)
    ).fetchone()

    conn.execute(
        "INSERT INTO user_secrets (user_id, key_name, encrypted_value) VALUES (?, ?, ?)",
        (user_id, "anilist_token", "encrypted_value_1"),
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO user_secrets (user_id, key_name, encrypted_value) VALUES (?, ?, ?)",
            (user_id, "anilist_token", "encrypted_value_2"),
        )
        conn.commit()


def test_run_history_roundtrip(_fresh_db):
    """Insert a run_history record and read it back."""
    conn = _fresh_db
    conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        ("dave@example.com", "hashed_pw_000"),
    )
    conn.commit()
    (user_id,) = conn.execute(
        "SELECT id FROM users WHERE email = ?", ("dave@example.com",)
    ).fetchone()

    conn.execute(
        "INSERT INTO run_history (user_id, source, model, picks) VALUES (?, ?, ?, ?)",
        (user_id, "anilist", "gpt-4o-mini", '["Anime A", "Anime B"]'),
    )
    conn.commit()

    rows = conn.execute("SELECT * FROM run_history WHERE user_id = ?", (user_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["source"] == "anilist"
    assert rows[0]["model"] == "gpt-4o-mini"
    assert rows[0]["picks"] == '["Anime A", "Anime B"]'


def test_recommendation_feedback_roundtrip(_fresh_db):
    conn = _fresh_db
    user_id = conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        ("feedback@example.com", "hash"),
    ).lastrowid
    conn.execute(
        "INSERT INTO run_history (id, user_id, source, model, picks) VALUES (?, ?, ?, ?, ?)",
        (7, user_id, "anilist", "gpt-4o-mini", "[]"),
    )
    conn.execute(
        """INSERT INTO recommendation_feedback
           (user_id, source, media_id, title, feedback_type, run_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, "anilist", 123, "Base 2", "wrong_sequel", 7),
    )
    row = conn.execute(
        "SELECT * FROM recommendation_feedback WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    assert row["feedback_type"] == "wrong_sequel"


# ---------------------------------------------------------------------------
# Constraint / cascade tests
# ---------------------------------------------------------------------------


def test_cascade_delete(_fresh_db):
    """Deleting a user should cascade-delete their sessions."""
    conn = _fresh_db
    conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        ("eve@example.com", "hashed_pw_cascade"),
    )
    conn.commit()
    (user_id,) = conn.execute(
        "SELECT id FROM users WHERE email = ?", ("eve@example.com",)
    ).fetchone()

    conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
        ("sess_cascade", user_id, "2026-07-01T00:00:00"),
    )
    conn.commit()

    # Verify session exists before delete
    (before,) = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
    ).fetchone()
    assert before == 1

    # Delete the user — cascade should remove the session
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()

    (after,) = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
    ).fetchone()
    assert after == 0
