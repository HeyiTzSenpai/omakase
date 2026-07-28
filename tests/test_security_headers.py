from fastapi.testclient import TestClient

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


def test_cross_origin_access_request_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("OMAKASE_LITE_DATA_DIR", str(tmp_path))
    response = TestClient(server.app, base_url="https://omakase.example").post(
        "/account/request",
        headers={"Origin": "https://attacker.example"},
        data={
            "email": "friend@example.com",
            "display_name": "Friend",
            "contact": "",
            "note": "",
            "website": "",
        },
    )

    assert response.status_code == 403


def test_invite_secret_is_never_part_of_a_server_route():
    route_paths = {
        path
        for route in server.app.routes
        if (path := getattr(route, "path", None)) is not None
    }

    assert "/account/invite" in route_paths
    assert "/account/invite/claim" in route_paths
    assert all("{token}" not in path for path in route_paths)
