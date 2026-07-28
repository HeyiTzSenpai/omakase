from fastapi.testclient import TestClient

from omakase.lite import routes as lite_routes
from omakase.web import server


def test_public_responses_set_browser_security_headers(monkeypatch):
    monkeypatch.setenv("OMAKASE_PUBLIC_HOSTED", "true")
    response = TestClient(server.app).get("/")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["strict-transport-security"].startswith("max-age=")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "script-src 'self'" in response.headers["content-security-policy"]


def test_account_and_api_responses_are_not_browser_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("OMAKASE_LITE_DATA_DIR", str(tmp_path))
    client = TestClient(server.app)

    assert client.get("/account/login").headers["cache-control"] == "no-store"
    assert client.get("/api/account/session").headers["cache-control"] == "no-store"


def test_cross_origin_invite_claim_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("OMAKASE_LITE_DATA_DIR", str(tmp_path))
    response = TestClient(server.app, base_url="https://omakase.example").post(
        "/account/invite/claim",
        headers={"Origin": "https://attacker.example"},
        data={
            "token": "not-a-real-token",
            "email": "friend@example.com",
            "display_name": "Friend",
            "password": "a-strong-friend-password",
            "confirm_password": "a-strong-friend-password",
        },
    )

    assert response.status_code == 403


def test_public_origin_is_accepted_when_proxy_rewrites_request_host(monkeypatch, tmp_path):
    monkeypatch.setenv("OMAKASE_LITE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OMAKASE_PUBLIC_URL", "https://omakase.example")
    monkeypatch.setenv("OMAKASE_TRUST_PROXY", "true")
    response = TestClient(server.app, base_url="http://internal:8765").post(
        "/account/invite/claim",
        headers={"Origin": "https://omakase.example"},
        data={
            "token": "not-a-real-token",
            "email": "friend@example.com",
            "display_name": "Friend",
            "password": "a-strong-friend-password",
            "confirm_password": "a-strong-friend-password",
        },
    )

    assert response.status_code == 400
    assert "invite is invalid" in response.text


def test_opaque_origin_is_accepted_only_with_same_origin_fetch_metadata(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("OMAKASE_LITE_DATA_DIR", str(tmp_path))
    client = TestClient(server.app, base_url="https://omakase.example")
    form = {
        "token": "not-a-real-token",
        "email": "friend@example.com",
        "display_name": "Friend",
        "password": "a-strong-friend-password",
        "confirm_password": "a-strong-friend-password",
    }

    same_origin = client.post(
        "/account/invite/claim",
        headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"},
        data=form,
    )
    cross_site = client.post(
        "/account/invite/claim",
        headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"},
        data=form,
    )

    assert same_origin.status_code == 400
    assert "invite is invalid" in same_origin.text
    assert cross_site.status_code == 403


def test_public_access_request_route_is_not_mounted():
    route_paths = {
        path
        for route in lite_routes.page_router.routes
        if (path := getattr(route, "path", None)) is not None
    }

    assert "/account/request" not in route_paths


def test_invite_secret_is_never_part_of_a_server_route():
    route_paths = {
        path
        for route in lite_routes.page_router.routes
        if (path := getattr(route, "path", None)) is not None
    }

    assert "/account/invite" in route_paths
    assert "/account/invite/claim" in route_paths
    assert all("{token}" not in path for path in route_paths)
