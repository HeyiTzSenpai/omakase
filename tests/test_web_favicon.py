from pathlib import Path

from fastapi.testclient import TestClient

from omakase.web import server
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


def test_generated_counter_art_is_present():
    art = Path(server.__file__).parent / "static" / "generated" / "omakase-counter-v2.png"

    assert art.is_file()
    assert art.stat().st_size > 1_000_000
