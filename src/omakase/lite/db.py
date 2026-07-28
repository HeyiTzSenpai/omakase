"""SQLite state for the optional Omakase Lite account experience."""

from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from omakase.lite.models import AccountUser
from omakase.types import Recommendation

_MIGRATIONS = Path(__file__).resolve().parent / "migrations"
_FEEDBACK_STATES = {"neutral", "not_interested", "saved", "watched"}


class InviteError(ValueError):
    pass


class OwnershipError(ValueError):
    pass


def _data_dir(data_dir: str | os.PathLike[str] | None = None) -> Path:
    return Path(data_dir or os.getenv("OMAKASE_LITE_DATA_DIR", "data/lite"))


def connect(data_dir: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    directory = _data_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    database_path = directory / "omakase-lite.db"
    conn = sqlite3.connect(database_path, check_same_thread=False)
    try:
        database_path.chmod(0o600)
    except OSError:
        pass
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    _run_migrations(conn)
    return conn


def _run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {
        row["name"] for row in conn.execute("SELECT name FROM account_migrations").fetchall()
    }
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        if path.name in applied:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO account_migrations (name) VALUES (?)", (path.name,))
        conn.commit()


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if "@" not in normalized or len(normalized) > 254:
        raise ValueError("Enter a valid email address.")
    return normalized


def create_user(
    conn: sqlite3.Connection,
    *,
    email: str,
    password_hash: str,
    display_name: str,
    role: str = "member",
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO account_users (email, password_hash, display_name, role)
        VALUES (?, ?, ?, ?)
        """,
        (normalize_email(email), password_hash, display_name.strip()[:80], role),
    )
    user_id = int(cursor.lastrowid)
    conn.execute(
        "INSERT INTO account_profiles (user_id, taste_profile) VALUES (?, '')",
        (user_id,),
    )
    conn.commit()
    return user_id


def bootstrap_admin(
    conn: sqlite3.Connection,
    *,
    email: str,
    password_hash: str,
    display_name: str,
) -> int:
    normalized = normalize_email(email)
    row = conn.execute("SELECT id FROM account_users WHERE email = ?", (normalized,)).fetchone()
    if row is not None:
        conn.execute(
            """
            UPDATE account_users
               SET password_hash = ?, display_name = ?, role = 'admin', active = 1
             WHERE id = ?
            """,
            (password_hash, display_name.strip()[:80], row["id"]),
        )
        conn.commit()
        return int(row["id"])
    return create_user(
        conn,
        email=normalized,
        password_hash=password_hash,
        display_name=display_name,
        role="admin",
    )


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> AccountUser | None:
    row = conn.execute(
        "SELECT id, email, display_name, role FROM account_users WHERE id = ? AND active = 1",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    return AccountUser(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
    )


def get_login_record(conn: sqlite3.Connection, email: str):
    return conn.execute(
        """
        SELECT id, email, display_name, role, password_hash
          FROM account_users
         WHERE email = ? AND active = 1
        """,
        (normalize_email(email),),
    ).fetchone()


def create_access_request(
    conn: sqlite3.Connection,
    *,
    email: str,
    display_name: str,
    contact: str,
    note: str,
) -> int:
    normalized = normalize_email(email)
    existing = conn.execute(
        "SELECT id, status FROM account_access_requests WHERE email = ?",
        (normalized,),
    ).fetchone()
    if existing is not None:
        if existing["status"] == "declined":
            conn.execute(
                """
                UPDATE account_access_requests
                   SET display_name = ?, contact = ?, note = ?, status = 'pending',
                       decided_at = NULL, decided_by = NULL
                 WHERE id = ?
                """,
                (
                    display_name.strip()[:80],
                    contact.strip()[:120],
                    note.strip()[:1000],
                    existing["id"],
                ),
            )
            conn.commit()
        return int(existing["id"])
    cursor = conn.execute(
        """
        INSERT INTO account_access_requests
            (email, display_name, contact, note)
        VALUES (?, ?, ?, ?)
        """,
        (
            normalized,
            display_name.strip()[:80],
            contact.strip()[:120],
            note.strip()[:1000],
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_access_requests(conn: sqlite3.Connection, *, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute(
            """
            SELECT id, email, display_name, contact, note, status, created_at, decided_at
              FROM account_access_requests
             WHERE status = ?
             ORDER BY id DESC
            """,
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, email, display_name, contact, note, status, created_at, decided_at
              FROM account_access_requests
             ORDER BY id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def approve_access_request(
    conn: sqlite3.Connection,
    *,
    request_id: int,
    admin_id: int,
    now: datetime | None = None,
) -> str:
    row = conn.execute(
        "SELECT email, status FROM account_access_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    if row is None:
        raise InviteError("Access request not found.")
    current = now or datetime.now(timezone.utc)
    raw_token = secrets.token_urlsafe(32)
    from omakase.lite.auth import hash_token

    conn.execute(
        "UPDATE account_invites SET claimed_at = COALESCE(claimed_at, ?) "
        "WHERE access_request_id = ? AND claimed_at IS NULL",
        (current.isoformat(), request_id),
    )
    conn.execute(
        """
        INSERT INTO account_invites
            (access_request_id, email, token_hash, expires_at, created_by)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            request_id,
            row["email"],
            hash_token(raw_token),
            (current + timedelta(days=7)).isoformat(),
            admin_id,
        ),
    )
    conn.execute(
        """
        UPDATE account_access_requests
           SET status = 'approved', decided_at = ?, decided_by = ?
         WHERE id = ?
        """,
        (current.isoformat(), admin_id, request_id),
    )
    conn.commit()
    return raw_token


def decline_access_request(conn: sqlite3.Connection, *, request_id: int, admin_id: int) -> None:
    conn.execute(
        """
        UPDATE account_access_requests
           SET status = 'declined', decided_at = ?, decided_by = ?
         WHERE id = ?
        """,
        (datetime.now(timezone.utc).isoformat(), admin_id, request_id),
    )
    conn.commit()


def claim_invite(
    conn: sqlite3.Connection,
    *,
    token: str,
    password: str,
    display_name: str,
    now: datetime | None = None,
) -> int:
    from omakase.lite.auth import hash_password, hash_token

    row = conn.execute(
        """
        SELECT i.id, i.email, i.expires_at, i.claimed_at, i.access_request_id
          FROM account_invites i
         WHERE i.token_hash = ?
        """,
        (hash_token(token),),
    ).fetchone()
    if row is None:
        raise InviteError("This invite is invalid.")
    current = now or datetime.now(timezone.utc)
    if row["claimed_at"]:
        raise InviteError("This invite has already been used.")
    if datetime.fromisoformat(row["expires_at"]) <= current:
        raise InviteError("This invite has expired.")
    if conn.execute("SELECT id FROM account_users WHERE email = ?", (row["email"],)).fetchone():
        raise InviteError("An account already exists for this invite.")

    user_id = create_user(
        conn,
        email=row["email"],
        password_hash=hash_password(password),
        display_name=display_name,
    )
    conn.execute(
        "UPDATE account_invites SET claimed_at = ? WHERE id = ?",
        (current.isoformat(), row["id"]),
    )
    conn.execute(
        "UPDATE account_access_requests SET status = 'claimed' WHERE id = ?",
        (row["access_request_id"],),
    )
    conn.commit()
    return user_id


def update_profile(conn: sqlite3.Connection, user_id: int, taste_profile: str) -> None:
    conn.execute(
        "UPDATE account_profiles SET taste_profile = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE user_id = ?",
        (taste_profile.strip()[:10000], user_id),
    )
    conn.commit()


def get_profile(conn: sqlite3.Connection, user_id: int) -> str:
    row = conn.execute(
        "SELECT taste_profile FROM account_profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return row["taste_profile"] if row else ""


def save_recommendation_run(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    source: str,
    source_username: str,
    provider: str,
    model: str,
    mode: str,
    recommendations: list[Recommendation],
) -> tuple[int, list[dict]]:
    cursor = conn.execute(
        """
        INSERT INTO account_recommendation_runs
            (user_id, source, source_username, provider, model, mode)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, source, source_username, provider, model, mode),
    )
    run_id = int(cursor.lastrowid)
    saved: list[dict] = []
    for index, recommendation in enumerate(recommendations):
        item = conn.execute(
            """
            INSERT INTO account_recommendations
                (run_id, user_id, position, title, predicted_score, reasoning,
                 best_match_from_history, url, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                user_id,
                index,
                recommendation.title,
                recommendation.predicted_score,
                recommendation.reasoning,
                recommendation.best_match_from_history,
                recommendation.url,
                recommendation.source,
            ),
        )
        saved.append(
            {
                "id": int(item.lastrowid),
                "title": recommendation.title,
                "predicted_score": recommendation.predicted_score,
                "reasoning": recommendation.reasoning,
                "best_match_from_history": recommendation.best_match_from_history,
                "url": recommendation.url,
                "source": recommendation.source,
                "feedback_state": "neutral",
            }
        )
    conn.commit()
    return run_id, saved


def set_recommendation_feedback(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    recommendation_id: int,
    state: str,
) -> None:
    if state not in _FEEDBACK_STATES:
        raise ValueError("Unknown feedback state.")
    cursor = conn.execute(
        """
        UPDATE account_recommendations
           SET feedback_state = ?, feedback_at = CURRENT_TIMESTAMP
         WHERE id = ? AND user_id = ?
        """,
        (state, recommendation_id, user_id),
    )
    if cursor.rowcount != 1:
        raise OwnershipError("Recommendation not found.")
    conn.commit()


def saved_list(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, title, predicted_score, reasoning, best_match_from_history,
               url, source, feedback_state, created_at
          FROM account_recommendations
         WHERE user_id = ? AND feedback_state = 'saved'
         ORDER BY feedback_at DESC, id DESC
        """,
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def recommendation_history(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    runs = conn.execute(
        """
        SELECT id, source, source_username, provider, model, mode, created_at
          FROM account_recommendation_runs
         WHERE user_id = ?
         ORDER BY id DESC
         LIMIT 20
        """,
        (user_id,),
    ).fetchall()
    result: list[dict] = []
    for run in runs:
        items = conn.execute(
            """
            SELECT id, title, predicted_score, reasoning, best_match_from_history,
                   url, source, feedback_state
              FROM account_recommendations
             WHERE run_id = ? AND user_id = ?
             ORDER BY position
            """,
            (run["id"], user_id),
        ).fetchall()
        result.append({**dict(run), "recommendations": [dict(item) for item in items]})
    return result


def feedback_context(conn: sqlite3.Connection, user_id: int) -> str:
    rows = conn.execute(
        """
        SELECT title, feedback_state
          FROM account_recommendations
         WHERE user_id = ? AND feedback_state != 'neutral'
         ORDER BY feedback_at DESC, id DESC
         LIMIT 60
        """,
        (user_id,),
    ).fetchall()
    grouped = {
        "not_interested": [],
        "saved": [],
        "watched": [],
    }
    for row in rows:
        grouped[row["feedback_state"]].append(row["title"])
    lines: list[str] = []
    if grouped["not_interested"]:
        lines.append(f"Avoid recommending again: {', '.join(grouped['not_interested'])}.")
    if grouped["saved"]:
        lines.append(f"Saved for later: {', '.join(grouped['saved'])}.")
    if grouped["watched"]:
        lines.append(f"Already watched: {', '.join(grouped['watched'])}.")
    return "\n".join(lines)
