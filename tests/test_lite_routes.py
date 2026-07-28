from __future__ import annotations

from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from omakase.lite import auth, db, routes
from omakase.web import server


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("OMAKASE_LITE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OMAKASE_ACCOUNT_SECURE_COOKIE", "false")
    monkeypatch.setenv("OMAKASE_PUBLIC_URL", "https://omakase.example")
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


def test_access_request_is_generic_and_discord_notification_is_redacted(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    captured = {}

    def capture_notification(*, request_id, display_name, admin_url):
        captured.update(
            request_id=request_id,
            display_name=display_name,
            admin_url=admin_url,
        )

    monkeypatch.setattr(routes, "_send_access_notification", capture_notification)
    response = client.post(
        "/account/request",
        data={
            "email": "friend@example.com",
            "display_name": "Friend",
            "contact": "friend-on-discord",
            "note": "Private request note",
            "website": "",
        },
    )

    assert response.status_code == 202
    assert "If this is a new request" in response.text
    assert captured == {
        "request_id": 1,
        "display_name": "Friend",
        "admin_url": "https://omakase.example/account/admin/requests?focus=1",
    }
    assert "friend@example.com" not in repr(captured)
    assert "Private request note" not in repr(captured)


def test_admin_can_approve_and_friend_can_claim_invite(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _bootstrap_admin(tmp_path)
    monkeypatch.setattr(routes, "_send_access_notification", lambda **_kwargs: None)
    client.post(
        "/account/request",
        data={
            "email": "friend@example.com",
            "display_name": "Friend",
            "contact": "",
            "note": "",
            "website": "",
        },
    )

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
            "display_name": "Friend",
            "password": "a-strong-friend-password",
            "confirm_password": "a-different-password",
        },
    )
    assert mismatch.status_code == 400
    assert f'value="{token}"' in mismatch.text
    assert 'value="Friend"' in mismatch.text
    claim = client.post(
        "/account/invite/claim",
        data={
            "token": token,
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
