from fastapi.testclient import TestClient

from omakase.web.server import app

client = TestClient(app)


def test_home_links_static_favicon():
    response = client.get("/")

    assert response.status_code == 200
    assert '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">' in response.text


def test_favicon_ico_compat_route_serves_svg():
    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "bento" in response.text
