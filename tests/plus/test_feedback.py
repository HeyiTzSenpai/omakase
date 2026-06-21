from __future__ import annotations

import sqlite3

import pytest

from omakase.plus.db import run_migrations
from omakase.plus.feedback import feedback_for_prompt, save_feedback


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    run_migrations(conn)
    return conn


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
