from __future__ import annotations

import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def _load_public_app(monkeypatch):
    monkeypatch.delenv("OMAKASE_PLUS_PRIVATE", raising=False)
    sys.modules.pop("omakase.web.server", None)
    server = importlib.import_module("omakase.web.server")
    return server.app


def test_public_mode_promotes_byok_and_keeps_plus_private(monkeypatch):
    monkeypatch.setenv("OMAKASE_PUBLIC_HOSTED", "true")
    app = _load_public_app(monkeypatch)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Cook my recommendation menu" in response.text
    assert "never written to disk, logs, cookies, or a database" in response.text
    assert "Lite accounts are invitation-only" in response.text
    assert "/account/request" not in response.text
    assert "Private Plus automation is not connected to this demo." in response.text
    assert "waitlist" not in response.text.lower()
    assert "Ollama" not in response.text
    assert "LM Studio" not in response.text
    assert "DeepSeek" in response.text
    assert "OpenWebUI" in response.text
    assert 'id="openwebui_url"' in response.text
    assert 'id="openwebui_model"' in response.text
    assert "—" not in response.text
    assert "/static/account_state.js?v=" in response.text
    assert 'name="provider_credential"' in response.text
    assert 'class="masked-credential"' in response.text
    assert 'name="provider_credential"\n                    type="password"' not in response.text
    assert 'name="watched-score" value="10"' in response.text
    assert "{% for" not in response.text


def test_public_mode_does_not_mount_plus_routes(monkeypatch):
    app = _load_public_app(monkeypatch)
    client = TestClient(app)

    for path in ("/plus", "/plus/login", "/plus/dashboard"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404


def test_production_overlay_mounts_only_the_lite_keyring_secret():
    overlay = Path("compose.production.yaml").read_text(encoding="utf-8")

    assert "lite_keyring" in overlay
    assert "access_discord" not in overlay
    assert "webhook" not in overlay.lower()
