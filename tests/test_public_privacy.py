from __future__ import annotations

import inspect
import os

from fastapi.testclient import TestClient

from omakase.adapters import myanimelist
from omakase.engine import RecommendationOutputError
from omakase.types import Recommendation
from omakase.web import server


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("OMAKASE_PUBLIC_HOSTED", "true")
    return TestClient(server.app)


def _valid_payload() -> dict[str, object]:
    return {
        "llm_type": "openai",
        "llm_url": "https://api.openai.com",
        "api_key": "request-only-key",
        "model": "gpt-4o-mini",
        "source": "anilist",
        "username": "demo-user",
        "profile": "I like thoughtful science fiction.",
        "mode": "fast",
    }


def test_recommendation_keeps_key_and_profile_request_local(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    captured = {}
    monkeypatch.setenv("OMAKASE_API_KEY", "pre-existing-key")
    monkeypatch.setenv("MAL_CLIENT_ID", "pre-existing-mal-id")
    monkeypatch.setattr(server.Path, "home", classmethod(lambda cls: tmp_path))

    def fake_run(cfg):
        captured["cfg"] = cfg
        return [
            Recommendation(
                title="Frieren: Beyond Journey's End",
                predicted_score=9.2,
                reasoning="Reflective fantasy with patient character writing.",
                best_match_from_history="Mushishi",
                url="https://anilist.co/anime/154587/",
                source="anilist",
            )
        ]

    monkeypatch.setattr(server, "run_pipeline", fake_run)
    response = client.post("/api/recommend", json=_valid_payload())

    assert response.status_code == 200
    cfg = captured["cfg"]
    assert cfg.api_key == "request-only-key"
    assert cfg.taste_profile == "I like thoughtful science fiction."
    assert cfg.profile_path == ""
    assert os.environ["OMAKASE_API_KEY"] == "pre-existing-key"
    assert os.environ["MAL_CLIENT_ID"] == "pre-existing-mal-id"
    assert not (tmp_path / ".omakase" / "profile.md").exists()


def test_hosted_demo_rejects_local_and_custom_provider_urls(monkeypatch):
    client = _client(monkeypatch)

    local_payload = _valid_payload() | {
        "llm_type": "ollama",
        "llm_url": "http://localhost:11434",
        "model": "qwen2.5:7b",
    }
    assert client.post("/api/recommend", json=local_payload).status_code == 400

    custom_payload = _valid_payload() | {"llm_url": "https://example.invalid/proxy"}
    response = client.post("/api/recommend", json=custom_payload)
    assert response.status_code == 400
    assert "official provider endpoint" in response.json()["detail"]


def test_hosted_demo_accepts_official_deepseek_provider(monkeypatch):
    client = _client(monkeypatch)
    captured = {}

    def fake_run(cfg):
        captured["cfg"] = cfg
        return []

    monkeypatch.setattr(server, "run_pipeline", fake_run)
    payload = _valid_payload() | {
        "llm_type": "deepseek",
        "llm_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
    }

    response = client.post("/api/recommend", json=payload)

    assert response.status_code == 200
    cfg = captured["cfg"]
    assert cfg.llm_type == "deepseek"
    assert cfg.llm_url == "https://api.deepseek.com"
    assert cfg.model == "deepseek-v4-flash"
    assert cfg.supports_json_mode is True


def test_candidate_catalog_failure_is_retryable_and_does_not_blame_the_key(monkeypatch):
    client = _client(monkeypatch)

    def fail(_cfg):
        raise myanimelist.CandidateSourceError("candidate anime catalog unavailable")

    monkeypatch.setattr(server, "run_pipeline", fail)
    response = client.post("/api/recommend", json=_valid_payload())

    assert response.status_code == 503
    assert "candidate anime catalog" in response.json()["detail"]
    assert "key" not in response.json()["detail"].lower()


def test_hosted_mal_requires_an_export_instead_of_shared_credentials(monkeypatch):
    client = _client(monkeypatch)
    payload = _valid_payload() | {
        "source": "myanimelist",
        "username": "demo-user",
        "mal_client_id": "must-not-be-used",
    }
    response = client.post("/api/recommend", json=payload)
    assert response.status_code == 400
    assert "export" in response.json()["detail"].lower()


def test_unknown_failure_is_redacted(monkeypatch):
    client = _client(monkeypatch)

    def fail(_cfg):
        raise RuntimeError("provider leaked request-only-key in its response")

    monkeypatch.setattr(server, "run_pipeline", fail)
    response = client.post("/api/recommend", json=_valid_payload())
    assert response.status_code == 500
    assert response.json()["detail"] == "Omakase could not finish this menu. Try again shortly."
    assert "request-only-key" not in response.text


def test_incomplete_model_json_returns_a_retryable_provider_error(monkeypatch):
    client = _client(monkeypatch)

    def fail(_cfg):
        raise RecommendationOutputError("private generated output")

    monkeypatch.setattr(server, "run_pipeline", fail)
    response = client.post("/api/recommend", json=_valid_payload())

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "The selected model returned an incomplete menu. Try again, or use Fast mode."
    )
    assert "private generated output" not in response.text


def test_health_reports_exact_source_commit(monkeypatch):
    monkeypatch.setenv("OMAKASE_SOURCE_COMMIT", "a" * 40)
    response = TestClient(server.app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "service": "omakase-public",
        "version": server.__version__,
        "sourceCommit": "a" * 40,
    }


def test_recommendation_endpoint_runs_blocking_pipeline_off_the_event_loop():
    assert not inspect.iscoroutinefunction(server.recommend)


def test_hosted_demo_closes_profile_and_model_discovery_endpoints(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/api/profile").status_code == 404
    assert client.get("/api/models?url=http://127.0.0.1:9000").status_code == 404


def test_hosted_home_never_prefills_a_server_profile(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "taste-profile.md").write_text(
        "PRIVATE SERVER OWNER PROFILE MARKER", encoding="utf-8"
    )
    client = _client(monkeypatch)

    response = client.get("/")

    assert response.status_code == 200
    assert "PRIVATE SERVER OWNER PROFILE MARKER" not in response.text
    assert "Thoughtful science fiction and fantasy" in response.text
