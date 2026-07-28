from __future__ import annotations

from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from omakase.lite import auth, credentials, db, routes
from omakase.types import Recommendation
from omakase.web import server


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("OMAKASE_LITE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OMAKASE_ACCOUNT_SECURE_COOKIE", "false")
    monkeypatch.setenv("OMAKASE_PUBLIC_URL", "https://omakase.example")
    keyring_path = tmp_path / "lite-keyring"
    keyring_path.write_bytes(Fernet.generate_key())
    monkeypatch.setenv("OMAKASE_LITE_KEYRING_FILE", str(keyring_path))
    routes.reset_rate_limits()
    return TestClient(server.app, base_url="https://omakase.example")


def _bootstrap_admin(tmp_path):
    conn = db.connect(tmp_path)
    admin_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )
    conn.close()
    return admin_id


def _login(client: TestClient, email: str, password: str):
    return client.post(
        "/account/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )


def test_public_access_request_route_is_not_available(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    get_response = client.get("/account/request")
    post_response = client.post(
        "/account/request",
        data={
            "email": "friend@example.com",
            "display_name": "Friend",
            "contact": "friend-on-discord",
            "note": "Private request note",
            "website": "",
        },
    )

    assert get_response.status_code == 404
    assert post_response.status_code == 404
    conn = db.connect(tmp_path)
    assert db.list_access_requests(conn) == []


def test_admin_can_approve_and_friend_can_claim_invite(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _bootstrap_admin(tmp_path)
    conn = db.connect(tmp_path)
    db.create_access_request(
        conn,
        email="friend@example.com",
        display_name="Friend",
        contact="",
        note="",
    )
    conn.close()

    login = _login(client, "owner@example.com", "owner-password")
    assert login.status_code == 302
    session = client.get("/api/account/session").json()
    assert session["authenticated"] is True
    assert session["role"] == "admin"

    inbox = client.get("/account/admin/requests")
    assert inbox.status_code == 200
    assert "friend@example.com" in inbox.text
    approved = client.post(
        "/account/admin/requests/1/approve",
        data={"csrf_token": session["csrf_token"]},
    )
    assert approved.status_code == 200
    invite_url = approved.json()["invite_url"]
    assert invite_url.startswith("https://omakase.example/account/invite#")

    client.post(
        "/account/logout",
        data={"csrf_token": session["csrf_token"]},
        follow_redirects=False,
    )
    token = urlsplit(invite_url).fragment
    assert token
    assert client.get("/account/invite").status_code == 200
    mismatch = client.post(
        "/account/invite/claim",
        data={
            "token": token,
            "email": "friend@example.com",
            "display_name": "Friend",
            "password": "a-strong-friend-password",
            "confirm_password": "a-different-password",
        },
    )
    assert mismatch.status_code == 400
    assert f'value="{token}"' in mismatch.text
    assert 'value="Friend"' in mismatch.text
    wrong_email = client.post(
        "/account/invite/claim",
        data={
            "token": token,
            "email": "someone-else@example.com",
            "display_name": "Friend",
            "password": "a-strong-friend-password",
            "confirm_password": "a-strong-friend-password",
        },
    )
    assert wrong_email.status_code == 400
    assert "same email address" in wrong_email.text
    claim = client.post(
        "/account/invite/claim",
        data={
            "token": token,
            "email": "friend@example.com",
            "display_name": "Friend",
            "password": "a-strong-friend-password",
            "confirm_password": "a-strong-friend-password",
        },
        follow_redirects=False,
    )
    assert claim.status_code == 302
    assert claim.headers["location"] == "/account"
    member_session = client.get("/api/account/session").json()
    assert member_session["authenticated"] is True
    assert member_session["role"] == "member"


def test_admin_can_issue_direct_invite_and_friend_fills_claim_form(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _bootstrap_admin(tmp_path)
    login = _login(client, "owner@example.com", "owner-password")
    assert login.status_code == 302
    session = client.get("/api/account/session").json()

    missing_csrf = client.post("/account/admin/invites")
    assert missing_csrf.status_code == 403
    cross_origin = client.post(
        "/account/admin/invites",
        data={"csrf_token": session["csrf_token"]},
        headers={"Origin": "https://evil.example"},
    )
    assert cross_origin.status_code == 403
    issued = client.post(
        "/account/admin/invites",
        data={"csrf_token": session["csrf_token"]},
    )
    assert issued.status_code == 200
    invite_url = issued.json()["invite_url"]
    assert invite_url.startswith("https://omakase.example/account/invite#")

    client.post(
        "/account/logout",
        data={"csrf_token": session["csrf_token"]},
        follow_redirects=False,
    )
    token = urlsplit(invite_url).fragment
    missing_email = client.post(
        "/account/invite/claim",
        data={
            "token": token,
            "email": "",
            "display_name": "Invited Friend",
            "password": "a-strong-invited-password",
            "confirm_password": "a-strong-invited-password",
        },
    )
    assert missing_email.status_code == 400
    assert "valid email" in missing_email.text

    claim = client.post(
        "/account/invite/claim",
        data={
            "token": token,
            "email": "invited@example.com",
            "display_name": "Invited Friend",
            "password": "a-strong-invited-password",
            "confirm_password": "a-strong-invited-password",
        },
        follow_redirects=False,
    )
    assert claim.status_code == 302
    assert claim.headers["location"] == "/account"
    member_session = client.get("/api/account/session").json()
    assert member_session["authenticated"] is True
    assert member_session["display_name"] == "Invited Friend"


def test_inbox_shows_public_request_number_not_internal_row_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _bootstrap_admin(tmp_path)
    conn = db.connect(tmp_path)
    db.create_access_request(
        conn,
        email="friend@example.com",
        display_name="Friend",
        contact="",
        note="",
    )
    conn.execute("UPDATE account_access_requests SET id = 4 WHERE id = 1")
    conn.commit()
    conn.close()

    _login(client, "owner@example.com", "owner-password")
    inbox = client.get("/account/admin/requests?focus=1")

    assert inbox.status_code == 200
    assert "Invite someone directly" in inbox.text
    assert "/static/account.css?v=" in inbox.text
    assert "/static/account.js?v=" in inbox.text
    assert 'class="request-row is-focus"' in inbox.text
    assert '<div class="request-id">#1</div>' in inbox.text
    assert '<div class="request-id">#4</div>' not in inbox.text


def test_guest_admin_inbox_redirects_to_login(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.get("/account/admin/requests", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/account/login"


def test_member_cannot_open_admin_inbox_or_change_another_users_feedback(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    owner_id = _bootstrap_admin(tmp_path)
    conn = db.connect(tmp_path)
    member_id = db.create_user(
        conn,
        email="friend@example.com",
        password_hash=auth.hash_password("friend-password"),
        display_name="Friend",
    )
    _, saved = db.save_recommendation_run(
        conn,
        user_id=owner_id,
        source="anilist",
        source_username="owner",
        provider="deepseek",
        model="deepseek-v4-flash",
        mode="fast",
        recommendations=[],
    )
    conn.close()
    assert saved == []

    login = _login(client, "friend@example.com", "friend-password")
    assert login.status_code == 302
    session = client.get("/api/account/session").json()
    assert session["user_id"] == member_id
    assert client.get("/account/admin/requests").status_code == 403
    assert (
        client.post(
            "/account/admin/invites",
            data={"csrf_token": session["csrf_token"]},
        ).status_code
        == 403
    )

    response = client.post(
        "/api/account/recommendations/999/feedback",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"state": "saved"},
    )
    assert response.status_code == 404


def test_authenticated_profile_is_saved_but_guest_session_stays_empty(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _bootstrap_admin(tmp_path)

    assert client.get("/api/account/session").json() == {"authenticated": False}
    _login(client, "owner@example.com", "owner-password")
    session = client.get("/api/account/session").json()
    response = client.post(
        "/api/account/profile",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"taste_profile": "I like patient mysteries."},
    )
    assert response.status_code == 200
    assert client.get("/api/account/session").json()["taste_profile"] == (
        "I like patient mysteries."
    )


def test_member_can_replace_view_and_forget_only_their_redacted_provider_key(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    _bootstrap_admin(tmp_path)
    _login(client, "owner@example.com", "owner-password")
    session = client.get("/api/account/session").json()
    assert session["provider_keys"] == {}
    assert session["remembered_setup"] == {}

    missing_csrf = client.put(
        "/api/account/provider-keys/deepseek",
        json={"provider_key": "sk-owner-secret-1234"},
    )
    assert missing_csrf.status_code == 403

    saved = client.put(
        "/api/account/provider-keys/deepseek",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"provider_key": "sk-owner-secret-1234"},
    )
    assert saved.status_code == 200
    assert saved.json() == {"provider": "deepseek", "saved": True, "hint": "1234"}
    assert "sk-owner-secret-1234" not in saved.text
    assert "gAAAA" not in saved.text

    refreshed = client.get("/api/account/session").json()
    assert refreshed["provider_keys"] == {"deepseek": {"saved": True, "hint": "1234"}}
    assert "sk-owner-secret-1234" not in repr(refreshed)
    dashboard = client.get("/account")
    assert dashboard.status_code == 200
    assert "DeepSeek" in dashboard.text
    assert "ending 1234" in dashboard.text
    assert 'name="account_provider_credential"' in dashboard.text
    assert 'class="masked-credential"' in dashboard.text
    assert 'data-form-type="other"' in dashboard.text

    forgotten = client.delete(
        "/api/account/provider-keys/deepseek",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )
    assert forgotten.status_code == 200
    assert forgotten.json() == {"provider": "deepseek", "saved": False}
    conn = db.connect(tmp_path)
    try:
        assert credentials.provider_key_summaries(conn, user_id=session["user_id"]) == {}
    finally:
        conn.close()


def test_provider_key_routes_reject_unknown_provider_and_fail_closed_without_keyring(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    _bootstrap_admin(tmp_path)
    _login(client, "owner@example.com", "owner-password")
    session = client.get("/api/account/session").json()

    unknown = client.put(
        "/api/account/provider-keys/not-a-provider",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"provider_key": "secret"},
    )
    assert unknown.status_code == 400
    assert unknown.json() == {"detail": "Choose a supported model provider."}

    monkeypatch.setenv("OMAKASE_LITE_KEYRING_FILE", str(tmp_path / "missing"))
    unavailable = client.put(
        "/api/account/provider-keys/deepseek",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"provider_key": "sk-owner-secret-1234"},
    )
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "Saved provider keys are temporarily unavailable."}
    assert "sk-owner-secret-1234" not in unavailable.text


def test_watched_feedback_route_requires_and_returns_persisted_score(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    user_id = _bootstrap_admin(tmp_path)
    conn = db.connect(tmp_path)
    _, saved = db.save_recommendation_run(
        conn,
        user_id=user_id,
        source="anilist",
        source_username="owner",
        provider="deepseek",
        model="deepseek-v4-pro",
        mode="pro",
        recommendations=[
            Recommendation(
                title="Pluto",
                predicted_score=9.1,
                reasoning="A careful mystery.",
                best_match_from_history="Monster",
            )
        ],
    )
    conn.close()
    _login(client, "owner@example.com", "owner-password")
    session = client.get("/api/account/session").json()
    endpoint = f"/api/account/recommendations/{saved[0]['id']}/feedback"

    missing = client.post(
        endpoint,
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"state": "watched"},
    )
    assert missing.status_code == 400
    assert missing.json() == {"detail": "Already watched needs a score from 1 to 10."}

    scored = client.post(
        endpoint,
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"state": "watched", "watched_score": 8},
    )
    assert scored.status_code == 200
    assert scored.json() == {"ok": True, "state": "watched", "watched_score": 8}
    dashboard = client.get("/account")
    assert "watched · 8/10" in dashboard.text

    cleared = client.post(
        endpoint,
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"state": "not_interested"},
    )
    assert cleared.status_code == 200
    assert cleared.json() == {
        "ok": True,
        "state": "not_interested",
        "watched_score": None,
    }
