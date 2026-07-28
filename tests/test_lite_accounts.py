from __future__ import annotations

from datetime import datetime, timedelta, timezone

from omakase.lite import auth, db
from omakase.types import Recommendation


def _connection(tmp_path):
    return db.connect(tmp_path)


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
