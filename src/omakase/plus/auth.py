"""Authentication utilities for Omakase Plus.

Uses Argon2 for password hashing and cryptographically-random session
tokens stored in SQLite.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher

_hasher = PasswordHasher()


# ── Password helpers ────────────────────────────────────────


def hash_password(plain: str) -> str:
    """Return an Argon2id hash of *plain*."""
    return _hasher.hash(plain)


def verify_password(plain: str, hash_value: str) -> bool:
    """Return True if *plain* matches *hash_value*.

    Returns False on any Argon2 exception (wrong format, verification
    failure, etc.) instead of propagating the error.
    """
    try:
        return _hasher.verify(hash_value, plain)
    except Exception:
        return False


# ── Session helpers ─────────────────────────────────────────


def create_session(db, user_id: int) -> str:
    """Insert a new session for *user_id*, valid for 30 days.

    Returns the raw session id (64 hex chars) that should be stored
    in the ``omakase_session`` cookie.
    """
    session_id = secrets.token_hex(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    db.execute(
        "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
        (session_id, user_id, expires_at),
    )
    db.commit()
    return session_id


def validate_session(db, session_id: str) -> int | None:
    """Look up *session_id* and return the associated ``user_id``.

    Expired sessions are silently deleted during lookup and ``None``
    is returned.
    """
    row = db.execute(
        "SELECT user_id, expires_at FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    expires = datetime.fromisoformat(row["expires_at"])
    if expires < datetime.now(timezone.utc):
        db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        db.commit()
        return None
    return row["user_id"]


def delete_session(db, session_id: str) -> None:
    """Remove *session_id* from the database."""
    db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    db.commit()
