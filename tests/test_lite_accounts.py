from __future__ import annotations

import os
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from omakase.lite import auth, credentials, db
from omakase.types import Recommendation


def _connection(tmp_path):
    return db.connect(tmp_path)


def test_owner_invite_migration_backfills_retained_request_as_public_number_one(tmp_path):
    database_path = tmp_path / "omakase-lite.db"
    conn = sqlite3.connect(database_path)
    conn.execute(
        """
        CREATE TABLE account_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for migration_name in ("001-lite-accounts.sql", "002-account-experience.sql"):
        conn.executescript((db._MIGRATIONS / migration_name).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO account_migrations (name) VALUES (?)", (migration_name,))
    for request_id in range(1, 5):
        conn.execute(
            """
            INSERT INTO account_access_requests
                (id, email, display_name, contact, note)
            VALUES (?, ?, ?, '', '')
            """,
            (request_id, f"person-{request_id}@example.com", f"Person {request_id}"),
        )
    conn.execute("DELETE FROM account_access_requests WHERE id < 4")
    conn.execute(
        """
        INSERT INTO account_users
            (id, email, password_hash, display_name, role)
        VALUES (1, 'owner@example.com', 'hash', 'Owner', 'admin')
        """
    )
    conn.execute(
        """
        INSERT INTO account_invites
            (id, access_request_id, email, token_hash, expires_at, claimed_at,
             created_by)
        VALUES (1, 4, 'person-4@example.com', 'stored-hash',
                '2099-01-01T00:00:00+00:00', '2026-07-28T00:00:00+00:00', 1)
        """
    )
    conn.commit()
    conn.close()

    migrated = db.connect(tmp_path)
    retained = db.list_access_requests(migrated)

    assert [(row["id"], row["public_number"]) for row in retained] == [(4, 1)]
    invite_columns = {
        row["name"]: row for row in migrated.execute("PRAGMA table_info(account_invites)")
    }
    assert invite_columns["access_request_id"]["notnull"] == 0
    assert invite_columns["email"]["notnull"] == 0
    assert "kind" in invite_columns
    preserved_invite = migrated.execute(
        "SELECT access_request_id, email, kind, token_hash FROM account_invites"
    ).fetchone()
    assert dict(preserved_invite) == {
        "access_request_id": 4,
        "email": "person-4@example.com",
        "kind": "request",
        "token_hash": "stored-hash",
    }


def test_accepted_invitation_migration_backfills_legacy_and_direct_claims(tmp_path):
    database_path = tmp_path / "omakase-lite.db"
    conn = sqlite3.connect(database_path)
    conn.execute(
        """
        CREATE TABLE account_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for migration_name in (
        "001-lite-accounts.sql",
        "002-account-experience.sql",
        "003-owner-issued-invites.sql",
    ):
        conn.executescript((db._MIGRATIONS / migration_name).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO account_migrations (name) VALUES (?)", (migration_name,))
    conn.executemany(
        """
        INSERT INTO account_users
            (id, email, password_hash, display_name, role, created_at)
        VALUES (?, ?, 'hash', ?, ?, ?)
        """,
        (
            (1, "owner@example.com", "Owner", "admin", "2026-07-28 04:00:00"),
            (5, "first@example.com", "First Friend", "member", "2026-07-28 04:14:12"),
            (9, "second@example.com", "Second Friend", "member", "2026-07-28 11:53:02"),
        ),
    )
    conn.execute(
        """
        INSERT INTO account_access_requests
            (id, email, display_name, contact, note, status, public_number)
        VALUES (4, 'first@example.com', 'First Friend', '@first', 'First request',
                'claimed', 1)
        """
    )
    conn.execute("UPDATE account_request_number_sequence SET next_number = 2 WHERE singleton = 1")
    conn.executemany(
        """
        INSERT INTO account_invites
            (id, access_request_id, email, kind, token_hash, expires_at,
             claimed_at, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, '2026-08-04T00:00:00+00:00', ?, 1, ?)
        """,
        (
            (
                1,
                4,
                "first@example.com",
                "request",
                "legacy-hash",
                "2026-07-28T04:14:12.200000+00:00",
                "2026-07-28 04:10:00",
            ),
            (
                5,
                None,
                None,
                "direct",
                "direct-hash",
                "2026-07-28T11:53:02.364104+00:00",
                "2026-07-28 06:34:18",
            ),
        ),
    )
    conn.commit()
    conn.close()

    migrated = db.connect(tmp_path)
    accepted = db.list_accepted_invitations(migrated)

    assert [
        (
            row["public_number"],
            row["display_name"],
            row["email"],
            row["invite_kind"],
            row["accepted_at"],
        )
        for row in accepted
    ] == [
        (
            1,
            "First Friend",
            "first@example.com",
            "request",
            "2026-07-28T04:14:12.200000+00:00",
        ),
        (
            2,
            "Second Friend",
            "second@example.com",
            "direct",
            "2026-07-28T11:53:02.364104+00:00",
        ),
    ]
    assert (
        migrated.execute(
            "SELECT next_number FROM account_request_number_sequence WHERE singleton = 1"
        ).fetchone()["next_number"]
        == 3
    )


def test_account_experience_migration_adds_credentials_scores_and_setup(tmp_path):
    conn = _connection(tmp_path)

    credential_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(account_provider_keys)")
    }
    profile_columns = {row["name"] for row in conn.execute("PRAGMA table_info(account_profiles)")}
    recommendation_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(account_recommendations)")
    }

    assert credential_columns == {
        "user_id",
        "provider",
        "encrypted_key",
        "key_hint",
        "created_at",
        "updated_at",
    }
    assert {
        "last_provider",
        "last_mode",
        "last_source",
        "last_source_username",
        "last_use_planning",
        "last_skip_profile",
        "last_llm_url",
        "last_model",
    } <= profile_columns
    assert "watched_score" in recommendation_columns


def test_openwebui_migration_preserves_existing_provider_keys(tmp_path):
    database_path = tmp_path / "omakase-lite.db"
    conn = sqlite3.connect(database_path)
    conn.execute(
        """
        CREATE TABLE account_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for migration_name in (
        "001-lite-accounts.sql",
        "002-account-experience.sql",
        "003-owner-issued-invites.sql",
        "004-accepted-invitation-ledger.sql",
    ):
        conn.executescript((db._MIGRATIONS / migration_name).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO account_migrations (name) VALUES (?)", (migration_name,))
    conn.execute(
        """
        INSERT INTO account_users (id, email, password_hash, display_name)
        VALUES (1, 'friend@example.com', 'hash', 'Friend')
        """
    )
    conn.execute(
        "INSERT INTO account_profiles (user_id, taste_profile) VALUES (1, '')"
    )
    conn.execute(
        """
        INSERT INTO account_provider_keys
            (user_id, provider, encrypted_key, key_hint)
        VALUES (1, 'openai', 'encrypted-value', '1234')
        """
    )
    conn.commit()
    conn.close()

    migrated = db.connect(tmp_path)

    assert dict(
        migrated.execute(
            """
            SELECT user_id, provider, encrypted_key, key_hint
              FROM account_provider_keys
            """
        ).fetchone()
    ) == {
        "user_id": 1,
        "provider": "openai",
        "encrypted_key": "encrypted-value",
        "key_hint": "1234",
    }
    migrated.execute(
        """
        INSERT INTO account_provider_keys
            (user_id, provider, encrypted_key, key_hint)
        VALUES (1, 'openwebui', 'another-encrypted-value', '5678')
        """
    )
    migrated.commit()


def test_provider_key_is_encrypted_at_rest_and_summary_is_redacted(monkeypatch, tmp_path):
    keyring_path = tmp_path / "lite-keyring"
    keyring_path.write_bytes(Fernet.generate_key())
    monkeypatch.setenv("OMAKASE_LITE_KEYRING_FILE", str(keyring_path))
    conn = _connection(tmp_path)
    user_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )

    credentials.save_provider_key(
        conn,
        user_id=user_id,
        provider="deepseek",
        plaintext_key="sk-account-secret-1234",
    )

    stored = conn.execute(
        "SELECT encrypted_key, key_hint FROM account_provider_keys "
        "WHERE user_id = ? AND provider = ?",
        (user_id, "deepseek"),
    ).fetchone()
    assert stored is not None
    assert "sk-account-secret-1234" not in stored["encrypted_key"]
    assert stored["key_hint"] == "1234"
    assert (
        credentials.load_provider_key(
            conn,
            user_id=user_id,
            provider="deepseek",
        )
        == "sk-account-secret-1234"
    )
    assert credentials.provider_key_summaries(conn, user_id=user_id) == {
        "deepseek": {"saved": True, "hint": "1234"}
    }


def test_openwebui_provider_key_is_encrypted_and_supported(monkeypatch, tmp_path):
    keyring_path = tmp_path / "lite-keyring"
    keyring_path.write_bytes(Fernet.generate_key())
    monkeypatch.setenv("OMAKASE_LITE_KEYRING_FILE", str(keyring_path))
    conn = _connection(tmp_path)
    user_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )

    result = credentials.save_provider_key(
        conn,
        user_id=user_id,
        provider="openwebui",
        plaintext_key="owui-account-secret-5678",
    )

    assert result == {"provider": "openwebui", "saved": True, "hint": "5678"}
    assert (
        credentials.load_provider_key(
            conn,
            user_id=user_id,
            provider="openwebui",
        )
        == "owui-account-secret-5678"
    )


def test_provider_key_replace_and_forget_are_user_scoped(monkeypatch, tmp_path):
    keyring_path = tmp_path / "lite-keyring"
    keyring_path.write_bytes(Fernet.generate_key())
    monkeypatch.setenv("OMAKASE_LITE_KEYRING_FILE", str(keyring_path))
    conn = _connection(tmp_path)
    first_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )
    second_id = db.create_user(
        conn,
        email="friend@example.com",
        password_hash=auth.hash_password("friend-password"),
        display_name="Friend",
    )
    credentials.save_provider_key(
        conn,
        user_id=first_id,
        provider="deepseek",
        plaintext_key="sk-owner-original-1111",
    )
    credentials.save_provider_key(
        conn,
        user_id=second_id,
        provider="deepseek",
        plaintext_key="sk-friend-secret-2222",
    )

    credentials.save_provider_key(
        conn,
        user_id=first_id,
        provider="deepseek",
        plaintext_key="sk-owner-replacement-3333",
    )
    assert (
        credentials.load_provider_key(conn, user_id=first_id, provider="deepseek")
        == "sk-owner-replacement-3333"
    )
    assert credentials.forget_provider_key(conn, user_id=first_id, provider="deepseek")
    assert credentials.load_provider_key(conn, user_id=first_id, provider="deepseek") is None
    assert (
        credentials.load_provider_key(conn, user_id=second_id, provider="deepseek")
        == "sk-friend-secret-2222"
    )


def test_provider_keyring_failures_are_safe_and_unknown_providers_are_rejected(
    monkeypatch,
    tmp_path,
):
    keyring_path = tmp_path / "lite-keyring"
    keyring_path.write_bytes(Fernet.generate_key())
    monkeypatch.setenv("OMAKASE_LITE_KEYRING_FILE", str(keyring_path))
    conn = _connection(tmp_path)
    user_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )
    credentials.save_provider_key(
        conn,
        user_id=user_id,
        provider="deepseek",
        plaintext_key="sk-account-secret-1234",
    )

    keyring_path.write_bytes(Fernet.generate_key())
    with pytest.raises(credentials.SavedCredentialInvalid) as wrong_key:
        credentials.load_provider_key(conn, user_id=user_id, provider="deepseek")
    assert str(wrong_key.value) == (
        "The saved provider key cannot be used. Replace it and try again."
    )

    monkeypatch.setenv("OMAKASE_LITE_KEYRING_FILE", str(tmp_path / "missing-keyring"))
    with pytest.raises(credentials.KeyringUnavailable) as unavailable:
        credentials.load_provider_key(conn, user_id=user_id, provider="deepseek")
    assert str(unavailable.value) == "Saved provider keys are temporarily unavailable."

    with pytest.raises(credentials.CredentialError, match="supported model provider"):
        credentials.save_provider_key(
            conn,
            user_id=user_id,
            provider="other-provider",
            plaintext_key="secret",
        )


def test_remembered_setup_contains_only_non_secret_member_choices(tmp_path):
    conn = _connection(tmp_path)
    user_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )

    db.update_remembered_setup(
        conn,
        user_id=user_id,
        provider="deepseek",
        mode="pro",
        source="anilist",
        source_username="owner-list",
        use_planning=True,
        skip_profile=False,
    )

    assert db.get_remembered_setup(conn, user_id) == {
        "provider": "deepseek",
        "mode": "pro",
        "source": "anilist",
        "source_username": "owner-list",
        "use_planning": True,
        "skip_profile": False,
    }
    stored = conn.execute(
        "SELECT * FROM account_profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    assert "key" not in " ".join(stored.keys()).lower()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_lite_database_uses_private_permissions(tmp_path):
    conn = _connection(tmp_path)
    conn.close()

    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "omakase-lite.db").stat().st_mode) == 0o600


def test_manual_access_request_invite_claim_and_session_are_hash_backed(tmp_path):
    conn = _connection(tmp_path)
    admin_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )
    request_id = db.create_access_request(
        conn,
        email="friend@example.com",
        display_name="Friend",
        contact="friend-on-discord",
        note="I would like saved anime recommendations.",
    )

    pending = db.list_access_requests(conn, status="pending")
    assert [row["id"] for row in pending] == [request_id]

    invite = db.approve_access_request(conn, request_id=request_id, admin_id=admin_id)
    stored = conn.execute(
        "SELECT token_hash, expires_at FROM account_invites WHERE access_request_id = ?",
        (request_id,),
    ).fetchone()
    assert stored is not None
    assert invite not in stored["token_hash"]
    assert datetime.fromisoformat(stored["expires_at"]) > datetime.now(timezone.utc)

    member_id = db.claim_invite(
        conn,
        token=invite,
        password="a-strong-friend-password",
        display_name="Friend",
    )
    member = db.get_user_by_id(conn, member_id)
    assert member is not None
    assert member.email == "friend@example.com"
    assert member.role == "member"

    session = auth.create_session(conn, member_id)
    stored_session = conn.execute(
        "SELECT token_hash, csrf_token FROM account_sessions WHERE user_id = ?",
        (member_id,),
    ).fetchone()
    assert stored_session is not None
    assert session.token not in stored_session["token_hash"]
    assert auth.validate_session(conn, session.token).id == member_id
    assert auth.validate_csrf(conn, session.token, session.csrf_token)


def test_owner_can_issue_direct_invite_and_recipient_supplies_account_details(tmp_path):
    conn = _connection(tmp_path)
    admin_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )

    invite = db.create_direct_invite(conn, admin_id=admin_id)
    stored = conn.execute(
        """
        SELECT access_request_id, email, kind, token_hash, expires_at, claimed_at
          FROM account_invites
         WHERE kind = 'direct'
        """
    ).fetchone()

    assert stored is not None
    assert stored["access_request_id"] is None
    assert stored["email"] is None
    assert stored["kind"] == "direct"
    assert invite not in stored["token_hash"]
    assert datetime.fromisoformat(stored["expires_at"]) > datetime.now(timezone.utc)

    member_id = db.claim_invite(
        conn,
        token=invite,
        email="NewFriend@Example.com",
        password="a-strong-friend-password",
        display_name="New Friend",
    )
    member = db.get_user_by_id(conn, member_id)
    assert member is not None
    assert member.email == "newfriend@example.com"
    assert member.display_name == "New Friend"
    accepted = db.list_accepted_invitations(conn)
    assert [
        (
            row["public_number"],
            row["display_name"],
            row["email"],
            row["invite_kind"],
        )
        for row in accepted
    ] == [(1, "New Friend", "newfriend@example.com", "direct")]

    with pytest.raises(db.InviteError, match="already been used"):
        db.claim_invite(
            conn,
            token=invite,
            email="another@example.com",
            password="another-strong-password",
            display_name="Another",
        )
    assert len(db.list_accepted_invitations(conn)) == 1


def test_direct_invite_allows_only_one_concurrent_claim(tmp_path):
    setup = _connection(tmp_path)
    admin_id = db.bootstrap_admin(
        setup,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )
    invite = db.create_direct_invite(setup, admin_id=admin_id)
    setup.close()
    gate = threading.Barrier(2)

    def claim(email: str):
        conn = _connection(tmp_path)
        gate.wait()
        try:
            return db.claim_invite(
                conn,
                token=invite,
                email=email,
                password="a-strong-concurrent-password",
                display_name="Concurrent Friend",
            )
        except db.InviteError as exc:
            return str(exc)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("first@example.com", "second@example.com")))

    assert len([result for result in results if isinstance(result, int)]) == 1
    assert len([result for result in results if "already been used" in str(result)]) == 1
    check = _connection(tmp_path)
    assert (
        check.execute("SELECT COUNT(*) FROM account_users WHERE role = 'member'").fetchone()[0] == 1
    )


def test_public_request_numbers_do_not_reuse_deleted_numbers(tmp_path):
    conn = _connection(tmp_path)
    first_id = db.create_access_request(
        conn,
        email="first@example.com",
        display_name="First",
        contact="",
        note="",
    )
    first = db.list_access_requests(conn)[0]
    assert first["id"] == first_id
    assert first["public_number"] == 1

    conn.execute("DELETE FROM account_access_requests WHERE id = ?", (first_id,))
    conn.commit()
    second_id = db.create_access_request(
        conn,
        email="second@example.com",
        display_name="Second",
        contact="",
        note="",
    )
    second = db.list_access_requests(conn)[0]

    assert second["id"] == second_id
    assert second["public_number"] == 2


def test_expired_invite_cannot_be_claimed(tmp_path):
    conn = _connection(tmp_path)
    admin_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )
    request_id = db.create_access_request(
        conn,
        email="friend@example.com",
        display_name="Friend",
        contact="",
        note="",
    )
    invite = db.approve_access_request(conn, request_id=request_id, admin_id=admin_id)
    conn.execute(
        "UPDATE account_invites SET expires_at = ? WHERE access_request_id = ?",
        ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), request_id),
    )
    conn.commit()

    try:
        db.claim_invite(
            conn,
            token=invite,
            password="a-strong-friend-password",
            display_name="Friend",
        )
    except db.InviteError as exc:
        assert "expired" in str(exc).lower()
    else:
        raise AssertionError("expired invite was accepted")


def test_declined_person_can_request_access_again(tmp_path):
    conn = _connection(tmp_path)
    admin_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )
    request_id = db.create_access_request(
        conn,
        email="friend@example.com",
        display_name="Friend",
        contact="old-contact",
        note="First request",
    )
    db.decline_access_request(conn, request_id=request_id, admin_id=admin_id)

    repeated_id = db.create_access_request(
        conn,
        email="FRIEND@example.com",
        display_name="Friend Again",
        contact="new-contact",
        note="Trying again",
    )

    assert repeated_id == request_id
    pending = db.list_access_requests(conn, status="pending")
    assert pending[0]["display_name"] == "Friend Again"
    assert pending[0]["contact"] == "new-contact"


def test_recommendation_history_feedback_and_list_are_user_scoped(tmp_path):
    conn = _connection(tmp_path)
    first_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )
    second_id = db.create_user(
        conn,
        email="friend@example.com",
        password_hash=auth.hash_password("friend-password"),
        display_name="Friend",
    )
    recommendations = [
        Recommendation(
            title="Monster",
            predicted_score=9.4,
            reasoning="Patient psychological tension.",
            best_match_from_history="Death Note",
            url="https://myanimelist.net/anime/19/",
            source="myanimelist",
        ),
        Recommendation(
            title="Steins;Gate",
            predicted_score=9.1,
            reasoning="A precise time-travel thriller.",
            best_match_from_history="Death Note",
            url="https://myanimelist.net/anime/9253/",
            source="myanimelist",
        ),
    ]

    run_id, saved = db.save_recommendation_run(
        conn,
        user_id=first_id,
        source="myanimelist",
        source_username="uploaded-list",
        provider="deepseek",
        model="deepseek-v4-flash",
        mode="fast",
        recommendations=recommendations,
    )
    assert run_id > 0
    assert len(saved) == 2

    db.set_recommendation_feedback(
        conn,
        user_id=first_id,
        recommendation_id=saved[0]["id"],
        state="not_interested",
    )
    db.set_recommendation_feedback(
        conn,
        user_id=first_id,
        recommendation_id=saved[1]["id"],
        state="saved",
    )

    assert [item["title"] for item in db.saved_list(conn, first_id)] == ["Steins;Gate"]
    assert db.saved_list(conn, second_id) == []
    context = db.feedback_context(conn, first_id)
    assert "Avoid recommending again: Monster." in context
    assert "Saved for later: Steins;Gate." in context
    assert db.feedback_titles(conn, first_id) == ["Steins;Gate", "Monster"]
    assert db.feedback_titles(conn, second_id) == []

    try:
        db.set_recommendation_feedback(
            conn,
            user_id=second_id,
            recommendation_id=saved[0]["id"],
            state="saved",
        )
    except db.OwnershipError:
        pass
    else:
        raise AssertionError("a second user changed another account's recommendation")


def test_watched_feedback_requires_score_and_other_states_clear_it(tmp_path):
    conn = _connection(tmp_path)
    user_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )
    _, saved = db.save_recommendation_run(
        conn,
        user_id=user_id,
        source="anilist",
        source_username="owner",
        provider="deepseek",
        model="deepseek-v4-flash",
        mode="fast",
        recommendations=[
            Recommendation(
                title="Pluto",
                predicted_score=9.2,
                reasoning="A careful mystery.",
                best_match_from_history="Monster",
            )
        ],
    )
    recommendation_id = saved[0]["id"]

    with pytest.raises(ValueError, match="score from 1 to 10"):
        db.set_recommendation_feedback(
            conn,
            user_id=user_id,
            recommendation_id=recommendation_id,
            state="watched",
        )
    db.set_recommendation_feedback(
        conn,
        user_id=user_id,
        recommendation_id=recommendation_id,
        state="watched",
        watched_score=8,
    )
    history_item = db.recommendation_history(conn, user_id)[0]["recommendations"][0]
    assert history_item["feedback_state"] == "watched"
    assert history_item["watched_score"] == 8
    assert "Already watched and rated: Pluto (8/10)." in db.feedback_context(conn, user_id)

    db.set_recommendation_feedback(
        conn,
        user_id=user_id,
        recommendation_id=recommendation_id,
        state="saved",
    )
    changed = db.recommendation_history(conn, user_id)[0]["recommendations"][0]
    assert changed["feedback_state"] == "saved"
    assert changed["watched_score"] is None
