"""Argon2id passwords and hash-backed browser sessions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher

from omakase.lite.models import AccountUser, SessionToken

_hasher = PasswordHasher()
SESSION_DAYS = 30


def hash_password(plain: str) -> str:
    if len(plain) < 12:
        raise ValueError("Password must be at least 12 characters.")
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain)
    except Exception:
        return False


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_session(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    now: datetime | None = None,
) -> SessionToken:
    current = now or datetime.now(timezone.utc)
    raw_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    conn.execute(
        """
        INSERT INTO account_sessions
            (token_hash, user_id, csrf_token, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            hash_token(raw_token),
            user_id,
            csrf_token,
            (current + timedelta(days=SESSION_DAYS)).isoformat(),
        ),
    )
    conn.commit()
    return SessionToken(token=raw_token, csrf_token=csrf_token)


def validate_session(
    conn: sqlite3.Connection,
    raw_token: str,
    *,
    now: datetime | None = None,
) -> AccountUser | None:
    if not raw_token:
        return None
    row = conn.execute(
        """
        SELECT s.expires_at, u.id, u.email, u.display_name, u.role, u.active
          FROM account_sessions s
          JOIN account_users u ON u.id = s.user_id
         WHERE s.token_hash = ?
        """,
        (hash_token(raw_token),),
    ).fetchone()
    if row is None or not row["active"]:
        return None
    current = now or datetime.now(timezone.utc)
    if datetime.fromisoformat(row["expires_at"]) <= current:
        delete_session(conn, raw_token)
        return None
    return AccountUser(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
    )


def validate_csrf(conn: sqlite3.Connection, raw_token: str, csrf_token: str) -> bool:
    if not raw_token or not csrf_token:
        return False
    row = conn.execute(
        "SELECT csrf_token FROM account_sessions WHERE token_hash = ?",
        (hash_token(raw_token),),
    ).fetchone()
    return row is not None and hmac.compare_digest(row["csrf_token"], csrf_token)


def delete_session(conn: sqlite3.Connection, raw_token: str) -> None:
    if not raw_token:
        return
    conn.execute(
        "DELETE FROM account_sessions WHERE token_hash = ?",
        (hash_token(raw_token),),
    )
    conn.commit()
