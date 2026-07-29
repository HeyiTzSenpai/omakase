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
_FEEDBACK_STATES = {"neutral", "not_interested", "saved", "watching", "watched"}


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
    commit: bool = True,
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
    if commit:
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


def create_oauth_flow(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    provider: str,
    state_hash: str,
    expires_at: str,
) -> None:
    conn.execute(
        "DELETE FROM account_oauth_flows WHERE user_id = ? AND provider = ?",
        (user_id, provider),
    )
    conn.execute(
        """
        INSERT INTO account_oauth_flows
            (state_hash, user_id, provider, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (state_hash, user_id, provider, expires_at),
    )
    conn.commit()


def consume_oauth_flow(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    provider: str,
    state_hash: str,
    now: datetime | None = None,
) -> bool:
    row = conn.execute(
        """
        SELECT expires_at
          FROM account_oauth_flows
         WHERE state_hash = ? AND user_id = ? AND provider = ?
        """,
        (state_hash, user_id, provider),
    ).fetchone()
    if row is None:
        return False
    conn.execute("DELETE FROM account_oauth_flows WHERE state_hash = ?", (state_hash,))
    conn.commit()
    current = now or datetime.now(timezone.utc)
    if datetime.fromisoformat(row["expires_at"]) <= current:
        return False
    return True


def upsert_anilist_connection(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    anilist_user_id: int,
    anilist_username: str,
    encrypted_access_token: str,
) -> None:
    conn.execute(
        """
        INSERT INTO account_anilist_connections
            (user_id, anilist_user_id, anilist_username, encrypted_access_token)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            anilist_user_id = excluded.anilist_user_id,
            anilist_username = excluded.anilist_username,
            encrypted_access_token = excluded.encrypted_access_token,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, anilist_user_id, anilist_username, encrypted_access_token),
    )
    conn.commit()


def anilist_connection_summary(
    conn: sqlite3.Connection,
    *,
    user_id: int,
) -> dict[str, object] | None:
    row = conn.execute(
        """
        SELECT anilist_user_id, anilist_username, connected_at, updated_at
          FROM account_anilist_connections
         WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def anilist_connection_record(
    conn: sqlite3.Connection,
    *,
    user_id: int,
) -> dict[str, object] | None:
    row = conn.execute(
        """
        SELECT anilist_user_id, anilist_username, encrypted_access_token
          FROM account_anilist_connections
         WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def delete_anilist_connection(
    conn: sqlite3.Connection,
    *,
    user_id: int,
) -> bool:
    cursor = conn.execute(
        "DELETE FROM account_anilist_connections WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()
    return cursor.rowcount == 1


def pending_anilist_entries(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    limit: int = 100,
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT recommendation.id, recommendation.url,
               COALESCE(recommendation.watch_status, 'completed') AS watch_status,
               recommendation.watched_score, recommendation.watched_episodes,
               run.source_username
          FROM account_recommendations AS recommendation
          JOIN account_recommendation_runs AS run
            ON run.id = recommendation.run_id
         WHERE recommendation.user_id = ?
           AND recommendation.source = 'anilist'
           AND recommendation.feedback_state = 'watched'
           AND (
               (
                   recommendation.watch_status = 'current'
                   AND recommendation.watched_episodes >= 1
               )
               OR (
                   (
                       recommendation.watch_status = 'completed'
                       OR recommendation.watch_status IS NULL
                   )
                   AND recommendation.watched_score BETWEEN 1 AND 10
               )
           )
           AND recommendation.tracker_sync_state != 'synced'
         ORDER BY recommendation.feedback_at, recommendation.id
         LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def pending_anilist_watched(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    limit: int = 100,
) -> list[dict]:
    """Return completed pending rows for callers migrating to the generalized query."""
    return [
        item
        for item in pending_anilist_entries(conn, user_id=user_id, limit=limit)
        if item["watch_status"] == "completed"
    ]


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
    try:
        conn.execute(
            """
            UPDATE account_request_number_sequence
               SET next_number = next_number + 1
             WHERE singleton = 1
            """
        )
        number_row = conn.execute(
            """
            SELECT next_number - 1 AS public_number
              FROM account_request_number_sequence
             WHERE singleton = 1
            """
        ).fetchone()
        if number_row is None:
            raise RuntimeError("The access-request number sequence is unavailable.")
        cursor = conn.execute(
            """
            INSERT INTO account_access_requests
                (email, display_name, contact, note, public_number)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                normalized,
                display_name.strip()[:80],
                contact.strip()[:120],
                note.strip()[:1000],
                number_row["public_number"],
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    except Exception:
        conn.rollback()
        raise


def list_access_requests(conn: sqlite3.Connection, *, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute(
            """
            SELECT id, public_number, email, display_name, contact, note, status,
                   created_at, decided_at
              FROM account_access_requests
             WHERE status = ?
             ORDER BY id DESC
            """,
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, public_number, email, display_name, contact, note, status,
                   created_at, decided_at
              FROM account_access_requests
             ORDER BY id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_accepted_invitations(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT accepted.public_number,
               accepted.invite_id AS id,
               member.email,
               member.display_name,
               invite.kind AS invite_kind,
               invite.created_at AS invited_at,
               accepted.accepted_at,
               COALESCE(request.contact, '') AS contact,
               COALESCE(request.note, '') AS note
          FROM account_invitation_acceptances AS accepted
          JOIN account_invites AS invite
            ON invite.id = accepted.invite_id
          JOIN account_users AS member
            ON member.id = accepted.user_id
          LEFT JOIN account_access_requests AS request
            ON request.id = invite.access_request_id
         ORDER BY accepted.public_number
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
            (access_request_id, email, kind, token_hash, expires_at, created_by)
        VALUES (?, ?, 'request', ?, ?, ?)
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


def create_direct_invite(
    conn: sqlite3.Connection,
    *,
    admin_id: int,
    now: datetime | None = None,
) -> str:
    current = now or datetime.now(timezone.utc)
    raw_token = secrets.token_urlsafe(32)
    from omakase.lite.auth import hash_token

    conn.execute(
        """
        INSERT INTO account_invites
            (access_request_id, email, kind, token_hash, expires_at, created_by)
        VALUES (NULL, NULL, 'direct', ?, ?, ?)
        """,
        (
            hash_token(raw_token),
            (current + timedelta(days=7)).isoformat(),
            admin_id,
        ),
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
    email: str = "",
    password: str,
    display_name: str,
    now: datetime | None = None,
) -> int:
    from omakase.lite.auth import hash_password, hash_token

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            """
            SELECT i.id, i.email, i.kind, i.expires_at, i.claimed_at,
                   i.access_request_id, request.public_number AS request_public_number
              FROM account_invites i
              LEFT JOIN account_access_requests AS request
                ON request.id = i.access_request_id
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
        if row["kind"] == "direct":
            claimed_email = normalize_email(email)
        else:
            claimed_email = row["email"]
            if email and normalize_email(email) != claimed_email:
                raise InviteError("Use the same email address that requested access.")
        if conn.execute(
            "SELECT id FROM account_users WHERE email = ?",
            (claimed_email,),
        ).fetchone():
            raise InviteError("An account already exists for this invite.")

        user_id = create_user(
            conn,
            email=claimed_email,
            password_hash=hash_password(password),
            display_name=display_name,
            commit=False,
        )
        if row["kind"] == "request":
            public_number = row["request_public_number"]
            if public_number is None:
                raise RuntimeError("The invitation history number is unavailable.")
        else:
            conn.execute(
                """
                UPDATE account_request_number_sequence
                   SET next_number = next_number + 1
                 WHERE singleton = 1
                """
            )
            number_row = conn.execute(
                """
                SELECT next_number - 1 AS public_number
                  FROM account_request_number_sequence
                 WHERE singleton = 1
                """
            ).fetchone()
            if number_row is None:
                raise RuntimeError("The invitation history number sequence is unavailable.")
            public_number = number_row["public_number"]
        conn.execute(
            "UPDATE account_invites SET claimed_at = ? WHERE id = ?",
            (current.isoformat(), row["id"]),
        )
        conn.execute(
            """
            INSERT INTO account_invitation_acceptances
                (public_number, invite_id, user_id, accepted_at)
            VALUES (?, ?, ?, ?)
            """,
            (public_number, row["id"], user_id, current.isoformat()),
        )
        if row["access_request_id"] is not None:
            conn.execute(
                "UPDATE account_access_requests SET status = 'claimed' WHERE id = ?",
                (row["access_request_id"],),
            )
        conn.commit()
        return user_id
    except Exception:
        conn.rollback()
        raise


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


def update_remembered_setup(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    provider: str,
    mode: str,
    source: str,
    source_username: str,
    use_planning: bool,
    skip_profile: bool,
    llm_url: str = "",
    model: str = "",
) -> None:
    conn.execute(
        """
        UPDATE account_profiles
           SET last_provider = ?,
               last_mode = ?,
               last_source = ?,
               last_source_username = ?,
               last_use_planning = ?,
               last_skip_profile = ?,
               last_llm_url = ?,
               last_model = ?,
               updated_at = CURRENT_TIMESTAMP
         WHERE user_id = ?
        """,
        (
            provider.strip()[:32],
            mode.strip()[:16],
            source.strip()[:32],
            source_username.strip()[:120],
            int(use_planning),
            int(skip_profile),
            llm_url.strip()[:2048],
            model.strip()[:200],
            user_id,
        ),
    )
    conn.commit()


def get_remembered_setup(conn: sqlite3.Connection, user_id: int) -> dict[str, object]:
    row = conn.execute(
        """
        SELECT last_provider, last_mode, last_source, last_source_username,
               last_use_planning, last_skip_profile, last_llm_url, last_model
          FROM account_profiles
         WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    if row is None or not row["last_provider"]:
        return {}
    setup: dict[str, object] = {
        "provider": row["last_provider"],
        "mode": row["last_mode"],
        "source": row["last_source"],
        "source_username": row["last_source_username"],
        "use_planning": bool(row["last_use_planning"]),
        "skip_profile": bool(row["last_skip_profile"]),
    }
    if row["last_provider"] == "openwebui":
        setup["llm_url"] = row["last_llm_url"]
        setup["model"] = row["last_model"]
    return setup


def upsert_provider_key(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    provider: str,
    encrypted_key: str,
    key_hint: str,
) -> None:
    conn.execute(
        """
        INSERT INTO account_provider_keys
            (user_id, provider, encrypted_key, key_hint)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, provider) DO UPDATE SET
            encrypted_key = excluded.encrypted_key,
            key_hint = excluded.key_hint,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, provider, encrypted_key, key_hint),
    )
    conn.commit()


def get_provider_key_record(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    provider: str,
):
    return conn.execute(
        """
        SELECT encrypted_key, key_hint
          FROM account_provider_keys
         WHERE user_id = ? AND provider = ?
        """,
        (user_id, provider),
    ).fetchone()


def provider_key_records(conn: sqlite3.Connection, *, user_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT provider, key_hint
          FROM account_provider_keys
         WHERE user_id = ?
         ORDER BY provider
        """,
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_provider_key(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    provider: str,
) -> bool:
    cursor = conn.execute(
        "DELETE FROM account_provider_keys WHERE user_id = ? AND provider = ?",
        (user_id, provider),
    )
    conn.commit()
    return cursor.rowcount == 1


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
                "watch_status": None,
                "watched_score": None,
                "watched_episodes": None,
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
    watched_score: int | None = None,
    watched_episodes: int | None = None,
) -> None:
    if state not in _FEEDBACK_STATES:
        raise ValueError("Unknown feedback state.")
    stored_state = state
    watch_status = None
    if state == "watching":
        if (
            isinstance(watched_episodes, bool)
            or not isinstance(watched_episodes, int)
            or watched_episodes < 1
        ):
            raise ValueError("Watching needs a positive whole number of episodes.")
        stored_state = "watched"
        watch_status = "current"
        watched_score = None
    elif state == "watched":
        if (
            isinstance(watched_score, bool)
            or not isinstance(watched_score, int)
            or not 1 <= watched_score <= 10
        ):
            raise ValueError("Already watched needs a score from 1 to 10.")
        watch_status = "completed"
        watched_episodes = None
    else:
        watched_score = None
        watched_episodes = None
    cursor = conn.execute(
        """
        UPDATE account_recommendations
           SET feedback_state = ?,
               watch_status = ?,
               watched_score = ?,
               watched_episodes = ?,
               feedback_at = CURRENT_TIMESTAMP,
               tracker_sync_state = 'local_only',
               tracker_sync_detail = NULL,
               tracker_remote_entry_id = NULL,
               tracker_synced_at = NULL
         WHERE id = ? AND user_id = ?
        """,
        (
            stored_state,
            watch_status,
            watched_score,
            watched_episodes,
            recommendation_id,
            user_id,
        ),
    )
    if cursor.rowcount != 1:
        raise OwnershipError("Recommendation not found.")
    conn.commit()


def saved_list(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, title, predicted_score, reasoning, best_match_from_history,
               url, source, feedback_state, watch_status, watched_score,
               watched_episodes, created_at
          FROM account_recommendations
         WHERE user_id = ? AND feedback_state = 'saved'
         ORDER BY feedback_at DESC, id DESC
        """,
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def watched_list(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, title, predicted_score, reasoning, best_match_from_history,
               url, source, feedback_state, watch_status, watched_score,
               watched_episodes, feedback_at,
               tracker_sync_state, tracker_sync_detail, tracker_synced_at
          FROM account_recommendations
         WHERE user_id = ?
           AND feedback_state = 'watched'
           AND (watch_status = 'completed' OR watch_status IS NULL)
         ORDER BY feedback_at DESC, id DESC
        """,
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def watching_list(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, title, predicted_score, reasoning, best_match_from_history,
               url, source, feedback_state, watch_status, watched_score,
               watched_episodes, feedback_at,
               tracker_sync_state, tracker_sync_detail, tracker_synced_at
          FROM account_recommendations
         WHERE user_id = ?
           AND feedback_state = 'watched'
           AND watch_status = 'current'
         ORDER BY feedback_at DESC, id DESC
        """,
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def recommendation_for_user(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    recommendation_id: int,
) -> dict | None:
    row = conn.execute(
        """
        SELECT recommendation.id, recommendation.title, recommendation.url,
               recommendation.source, run.source_username
          FROM account_recommendations AS recommendation
          JOIN account_recommendation_runs AS run
            ON run.id = recommendation.run_id
         WHERE recommendation.id = ? AND recommendation.user_id = ?
        """,
        (recommendation_id, user_id),
    ).fetchone()
    return dict(row) if row is not None else None


def set_recommendation_tracker_sync(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    recommendation_id: int,
    state: str,
    detail: str,
    remote_entry_id: int | None = None,
) -> None:
    synced_at = datetime.now(timezone.utc).isoformat() if state == "synced" else None
    cursor = conn.execute(
        """
        UPDATE account_recommendations
           SET tracker_sync_state = ?,
               tracker_sync_detail = ?,
               tracker_remote_entry_id = ?,
               tracker_synced_at = ?
         WHERE id = ? AND user_id = ?
        """,
        (
            state,
            detail,
            remote_entry_id,
            synced_at,
            recommendation_id,
            user_id,
        ),
    )
    if cursor.rowcount != 1:
        raise OwnershipError("Recommendation not found.")
    conn.commit()


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
                   url, source, feedback_state, watch_status, watched_score,
                   watched_episodes,
                   tracker_sync_state, tracker_sync_detail,
                   tracker_remote_entry_id, tracker_synced_at
              FROM account_recommendations
             WHERE run_id = ? AND user_id = ?
             ORDER BY position
            """,
            (run["id"], user_id),
        ).fetchall()
        result.append({**dict(run), "recommendations": [dict(item) for item in items]})
    return result


def feedback_titles(conn: sqlite3.Connection, user_id: int) -> list[str]:
    """Return distinct titles the member has explicitly acted on, newest first."""
    rows = conn.execute(
        """
        SELECT title
          FROM account_recommendations
         WHERE user_id = ? AND feedback_state != 'neutral'
         ORDER BY feedback_at DESC, id DESC
        """,
        (user_id,),
    ).fetchall()
    result: list[str] = []
    seen: set[str] = set()
    for row in rows:
        title = row["title"].strip()
        normalized = title.casefold()
        if title and normalized not in seen:
            result.append(title)
            seen.add(normalized)
    return result


def feedback_context(conn: sqlite3.Connection, user_id: int) -> str:
    rows = conn.execute(
        """
        SELECT title, feedback_state, watch_status, watched_score,
               watched_episodes
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
        "watching": [],
        "watched": [],
    }
    for row in rows:
        value = row["title"]
        group = row["feedback_state"]
        if (
            row["feedback_state"] == "watched"
            and row["watch_status"] == "current"
            and row["watched_episodes"] is not None
        ):
            episode_label = "episode" if row["watched_episodes"] == 1 else "episodes"
            value = f"{value} ({row['watched_episodes']} {episode_label})"
            group = "watching"
        elif row["feedback_state"] == "watched" and row["watched_score"] is not None:
            value = f"{value} ({row['watched_score']}/10)"
        grouped[group].append(value)
    lines: list[str] = []
    if grouped["not_interested"]:
        lines.append(f"Avoid recommending again: {', '.join(grouped['not_interested'])}.")
    if grouped["saved"]:
        lines.append(f"Saved for later: {', '.join(grouped['saved'])}.")
    if grouped["watching"]:
        lines.append(f"Currently watching: {', '.join(grouped['watching'])}.")
    if grouped["watched"]:
        lines.append(f"Already watched and rated: {', '.join(grouped['watched'])}.")
    return "\n".join(lines)
