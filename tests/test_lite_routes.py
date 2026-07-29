from __future__ import annotations

import hashlib
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from omakase.lite import anilist, auth, credentials, db, routes
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


def test_inbox_lists_every_accepted_invitation_with_member_information(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    _bootstrap_admin(tmp_path)
    conn = db.connect(tmp_path)
    request_id = db.create_access_request(
        conn,
        email="first@example.com",
        display_name="First Friend",
        contact="@first",
        note="Original access request",
    )
    legacy_invite = db.approve_access_request(conn, request_id=request_id, admin_id=1)
    db.claim_invite(
        conn,
        token=legacy_invite,
        password="a-strong-first-password",
        display_name="First Friend",
    )
    conn.close()

    _login(client, "owner@example.com", "owner-password")
    owner_session = client.get("/api/account/session").json()
    issued = client.post(
        "/account/admin/invites",
        data={"csrf_token": owner_session["csrf_token"]},
    )
    direct_token = urlsplit(issued.json()["invite_url"]).fragment
    client.post(
        "/account/logout",
        data={"csrf_token": owner_session["csrf_token"]},
        follow_redirects=False,
    )
    client.post(
        "/account/invite/claim",
        data={
            "token": direct_token,
            "email": "second@example.com",
            "display_name": "Second Friend",
            "password": "a-strong-second-password",
            "confirm_password": "a-strong-second-password",
        },
        follow_redirects=False,
    )
    member_session = client.get("/api/account/session").json()
    client.post(
        "/account/logout",
        data={"csrf_token": member_session["csrf_token"]},
        follow_redirects=False,
    )
    _login(client, "owner@example.com", "owner-password")

    inbox = client.get("/account/admin/requests")

    assert inbox.status_code == 200
    assert "Accepted invitations" in inbox.text
    assert "Everyone who has accepted a Lite invitation appears here." in inbox.text
    assert '<div class="request-id">#1</div>' in inbox.text
    assert '<div class="request-id">#2</div>' in inbox.text
    assert "<h3>First Friend</h3>" in inbox.text
    assert "first@example.com" in inbox.text
    assert "<h3>Second Friend</h3>" in inbox.text
    assert "second@example.com" in inbox.text
    assert inbox.text.count('class="status status--accepted"') == 2
    assert inbox.text.count("<time ") == 2


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


def test_anilist_connect_requires_login_and_stores_one_time_authorization(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("OMAKASE_ANILIST_CLIENT_ID", "public-client-id")
    monkeypatch.setenv("OMAKASE_ANILIST_CLIENT_SECRET", "public-client-secret")
    _bootstrap_admin(tmp_path)

    guest = client.get(
        "/account/integrations/anilist/connect",
        follow_redirects=False,
    )
    assert guest.status_code == 401

    _login(client, "owner@example.com", "owner-password")
    connected = client.get(
        "/account/integrations/anilist/connect",
        follow_redirects=False,
    )

    assert connected.status_code == 302
    location = urlsplit(connected.headers["location"])
    assert (location.scheme, location.netloc, location.path) == (
        "https",
        "anilist.co",
        "/api/v2/oauth/authorize",
    )
    query = parse_qs(location.query)
    assert query["client_id"] == ["public-client-id"]
    assert query["redirect_uri"] == [
        "https://omakase.example/account/integrations/anilist/callback"
    ]
    assert query["response_type"] == ["code"]
    assert "code_challenge" not in query
    assert "code_challenge_method" not in query
    state = query["state"][0]
    assert len(state) >= 32

    conn = db.connect(tmp_path)
    flow = conn.execute(
        """
        SELECT state_hash, expires_at
          FROM account_oauth_flows
        """
    ).fetchone()
    assert flow["state_hash"] == hashlib.sha256(state.encode("utf-8")).hexdigest()
    conn.close()


def test_anilist_connect_fails_before_redirect_when_client_secret_is_unavailable(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("OMAKASE_ANILIST_CLIENT_ID", "public-client-id")
    _bootstrap_admin(tmp_path)
    _login(client, "owner@example.com", "owner-password")
    dashboard = client.get("/account")
    assert "AniList synchronization has not been enabled on this server yet." in dashboard.text
    assert 'href="/account/integrations/anilist/connect"' not in dashboard.text

    unavailable = client.get(
        "/account/integrations/anilist/connect",
        follow_redirects=False,
    )

    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "AniList connection is not configured yet."}
    conn = db.connect(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM account_oauth_flows").fetchone()[0] == 0
    conn.close()


def test_anilist_callback_binds_identity_and_encrypts_access_token(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("OMAKASE_ANILIST_CLIENT_ID", "public-client-id")
    monkeypatch.setenv("OMAKASE_ANILIST_CLIENT_SECRET", "public-client-secret")
    _bootstrap_admin(tmp_path)
    _login(client, "owner@example.com", "owner-password")
    started = client.get(
        "/account/integrations/anilist/connect",
        follow_redirects=False,
    )
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    observed = {}

    def exchange_code(*, client_id, client_secret, redirect_uri, code):
        observed.update(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            }
        )
        return "one-year-access-token"

    monkeypatch.setattr(anilist, "exchange_code", exchange_code, raising=False)
    monkeypatch.setattr(
        anilist,
        "viewer_identity",
        lambda access_token: {"id": 42, "name": "OwnerOnAniList"},
        raising=False,
    )
    callback = client.get(
        f"/account/integrations/anilist/callback?code=one-time-code&state={state}",
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "/account?anilist=connected&synced=0"
    assert observed == {
        "client_id": "public-client-id",
        "client_secret": "public-client-secret",
        "redirect_uri": "https://omakase.example/account/integrations/anilist/callback",
        "code": "one-time-code",
    }
    conn = db.connect(tmp_path)
    connection = conn.execute(
        """
        SELECT anilist_user_id, anilist_username, encrypted_access_token
          FROM account_anilist_connections
        """
    ).fetchone()
    assert dict(connection) == {
        "anilist_user_id": 42,
        "anilist_username": "OwnerOnAniList",
        "encrypted_access_token": connection["encrypted_access_token"],
    }
    assert "one-year-access-token" not in connection["encrypted_access_token"]
    assert conn.execute("SELECT COUNT(*) FROM account_oauth_flows").fetchone()[0] == 0
    conn.close()
    dashboard = client.get("/account")
    assert "Connected as OwnerOnAniList" in dashboard.text
    assert "one-year-access-token" not in dashboard.text


def test_hosted_anilist_callback_refuses_environment_only_client_secret(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("OMAKASE_ANILIST_CLIENT_ID", "public-client-id")
    monkeypatch.setenv("OMAKASE_ANILIST_CLIENT_SECRET", "must-not-be-used-in-hosted-mode")
    _bootstrap_admin(tmp_path)
    _login(client, "owner@example.com", "owner-password")
    started = client.get(
        "/account/integrations/anilist/connect",
        follow_redirects=False,
    )
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    monkeypatch.setenv("OMAKASE_PUBLIC_HOSTED", "true")

    def reject_exchange(**kwargs):
        raise AssertionError("hosted mode read the AniList secret from container environment")

    monkeypatch.setattr(anilist, "exchange_code", reject_exchange)
    callback = client.get(
        f"/account/integrations/anilist/callback?code=one-time-code&state={state}",
        follow_redirects=False,
    )

    assert callback.status_code == 503
    assert callback.json() == {"detail": "AniList connection is not configured yet."}
    assert "must-not-be-used-in-hosted-mode" not in callback.text


def test_anilist_callback_syncs_existing_matching_watched_scores(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("OMAKASE_ANILIST_CLIENT_ID", "public-client-id")
    monkeypatch.setenv("OMAKASE_ANILIST_CLIENT_SECRET", "public-client-secret")
    user_id = _bootstrap_admin(tmp_path)
    conn = db.connect(tmp_path)
    _, saved = db.save_recommendation_run(
        conn,
        user_id=user_id,
        source="anilist",
        source_username="OwnerOnAniList",
        provider="deepseek",
        model="deepseek-v4-pro",
        mode="pro",
        recommendations=[
            Recommendation(
                title="Pluto",
                predicted_score=9.1,
                reasoning="A careful mystery.",
                best_match_from_history="Monster",
                url="https://anilist.co/anime/99088/Pluto/",
                source="anilist",
            )
        ],
    )
    db.set_recommendation_feedback(
        conn,
        user_id=user_id,
        recommendation_id=saved[0]["id"],
        state="watched",
        watched_score=8,
    )
    conn.close()
    _login(client, "owner@example.com", "owner-password")
    started = client.get(
        "/account/integrations/anilist/connect",
        follow_redirects=False,
    )
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    monkeypatch.setattr(anilist, "exchange_code", lambda **kwargs: "one-year-access-token")
    monkeypatch.setattr(
        anilist,
        "viewer_identity",
        lambda access_token: {"id": 42, "name": "owneronanilist"},
    )

    def save_completed_entry(access_token, media_id, *, score_ten):
        if (access_token, media_id, score_ten) != (
            "one-year-access-token",
            99088,
            8,
        ):
            raise AssertionError("pending AniList sync received the wrong row")
        return {
            "id": 77,
            "mediaId": 99088,
            "status": "COMPLETED",
            "score": 80,
        }

    monkeypatch.setattr(anilist, "save_completed_entry", save_completed_entry)
    callback = client.get(
        f"/account/integrations/anilist/callback?code=one-time-code&state={state}",
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "/account?anilist=connected&synced=1"
    conn = db.connect(tmp_path)
    row = conn.execute(
        """
        SELECT tracker_sync_state, tracker_remote_entry_id, tracker_synced_at
          FROM account_recommendations
         WHERE id = ?
        """,
        (saved[0]["id"],),
    ).fetchone()
    assert row["tracker_sync_state"] == "synced"
    assert row["tracker_remote_entry_id"] == 77
    assert row["tracker_synced_at"]
    conn.close()


def test_anilist_disconnect_requires_csrf_and_removes_only_the_local_token(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("OMAKASE_ANILIST_CLIENT_ID", "public-client-id")
    monkeypatch.setenv("OMAKASE_ANILIST_CLIENT_SECRET", "public-client-secret")
    user_id = _bootstrap_admin(tmp_path)
    conn = db.connect(tmp_path)
    db.upsert_anilist_connection(
        conn,
        user_id=user_id,
        anilist_user_id=42,
        anilist_username="OwnerOnAniList",
        encrypted_access_token=credentials.encrypt_secret("one-year-access-token"),
    )
    conn.close()
    _login(client, "owner@example.com", "owner-password")
    session = client.get("/api/account/session").json()

    missing_csrf = client.post(
        "/account/integrations/anilist/disconnect",
        follow_redirects=False,
    )
    assert missing_csrf.status_code == 403
    disconnected = client.post(
        "/account/integrations/anilist/disconnect",
        data={"csrf_token": session["csrf_token"]},
        follow_redirects=False,
    )

    assert disconnected.status_code == 302
    assert disconnected.headers["location"] == "/account?anilist=disconnected"
    conn = db.connect(tmp_path)
    assert db.anilist_connection_summary(conn, user_id=user_id) is None
    conn.close()
    dashboard = client.get("/account")
    assert "Connect AniList" in dashboard.text
    assert "one-year-access-token" not in dashboard.text


def test_watched_feedback_route_requires_anilist_connection_and_lists_score(
    monkeypatch,
    tmp_path,
):
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
                url="https://anilist.co/anime/99088/Pluto/",
                source="anilist",
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
    assert scored.json() == {
        "ok": True,
        "state": "watched",
        "watched_score": 8,
        "tracker_sync": {
            "state": "connection_required",
            "detail": "Connect AniList to add this title and score to your anime list.",
            "connect_url": "/account/integrations/anilist/connect",
        },
    }
    dashboard = client.get("/account")
    assert '<h2 id="watched-title">Watched &amp; rated</h2>' in dashboard.text
    assert "1 title" in dashboard.text
    assert "Pluto" in dashboard.text
    assert "watched · 8/10" in dashboard.text
    assert 'data-tracker-sync-state="connection_required"' in dashboard.text
    assert "Connect AniList to add this title and score to your anime list." in dashboard.text

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


def test_watched_feedback_syncs_completed_score_to_matching_anilist_account(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    user_id = _bootstrap_admin(tmp_path)
    conn = db.connect(tmp_path)
    db.upsert_anilist_connection(
        conn,
        user_id=user_id,
        anilist_user_id=42,
        anilist_username="OwnerOnAniList",
        encrypted_access_token=credentials.encrypt_secret("one-year-access-token"),
    )
    _, saved = db.save_recommendation_run(
        conn,
        user_id=user_id,
        source="anilist",
        source_username="owneronanilist",
        provider="deepseek",
        model="deepseek-v4-pro",
        mode="pro",
        recommendations=[
            Recommendation(
                title="Pluto",
                predicted_score=9.1,
                reasoning="A careful mystery.",
                best_match_from_history="Monster",
                url="https://anilist.co/anime/99088/Pluto/",
                source="anilist",
            )
        ],
    )
    conn.close()

    def save_completed_entry(access_token, media_id, *, score_ten):
        if (access_token, media_id, score_ten) != (
            "one-year-access-token",
            99088,
            8,
        ):
            raise AssertionError("AniList completion received the wrong identity or score")
        return {
            "id": 77,
            "mediaId": 99088,
            "status": "COMPLETED",
            "score": 80,
        }

    monkeypatch.setattr(
        anilist,
        "save_completed_entry",
        save_completed_entry,
        raising=False,
    )
    _login(client, "owner@example.com", "owner-password")
    session = client.get("/api/account/session").json()
    scored = client.post(
        f"/api/account/recommendations/{saved[0]['id']}/feedback",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"state": "watched", "watched_score": 8},
    )

    assert scored.status_code == 200
    assert scored.json() == {
        "ok": True,
        "state": "watched",
        "watched_score": 8,
        "tracker_sync": {
            "state": "synced",
            "detail": "Added to OwnerOnAniList’s AniList as Completed · 8/10.",
        },
    }
    conn = db.connect(tmp_path)
    item = db.recommendation_history(conn, user_id)[0]["recommendations"][0]
    assert item["tracker_sync_state"] == "synced"
    assert item["tracker_remote_entry_id"] == 77
    assert item["tracker_synced_at"]
    conn.close()
    dashboard = client.get("/account")
    assert 'data-tracker-sync-state="synced"' in dashboard.text
    assert "Added to OwnerOnAniList’s AniList as Completed · 8/10." in dashboard.text


def test_watched_feedback_never_writes_to_a_different_connected_anilist_account(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    user_id = _bootstrap_admin(tmp_path)
    conn = db.connect(tmp_path)
    db.upsert_anilist_connection(
        conn,
        user_id=user_id,
        anilist_user_id=42,
        anilist_username="OwnerOnAniList",
        encrypted_access_token=credentials.encrypt_secret("one-year-access-token"),
    )
    _, saved = db.save_recommendation_run(
        conn,
        user_id=user_id,
        source="anilist",
        source_username="SomeoneElse",
        provider="deepseek",
        model="deepseek-v4-pro",
        mode="pro",
        recommendations=[
            Recommendation(
                title="Pluto",
                predicted_score=9.1,
                reasoning="A careful mystery.",
                best_match_from_history="Monster",
                url="https://anilist.co/anime/99088/Pluto/",
                source="anilist",
            )
        ],
    )
    conn.close()

    def reject_any_write(*args, **kwargs):
        raise AssertionError("a mismatched AniList account was mutated")

    monkeypatch.setattr(
        anilist,
        "save_completed_entry",
        reject_any_write,
        raising=False,
    )
    _login(client, "owner@example.com", "owner-password")
    session = client.get("/api/account/session").json()
    scored = client.post(
        f"/api/account/recommendations/{saved[0]['id']}/feedback",
        headers={"X-CSRF-Token": session["csrf_token"]},
        json={"state": "watched", "watched_score": 8},
    )

    assert scored.status_code == 200
    assert scored.json()["tracker_sync"] == {
        "state": "account_mismatch",
        "detail": (
            "This menu used SomeoneElse, but AniList is connected as "
            "OwnerOnAniList. No AniList list was changed."
        ),
    }
