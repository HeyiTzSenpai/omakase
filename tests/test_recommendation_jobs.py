from __future__ import annotations

import time
from io import BytesIO
from threading import Event
from urllib.error import HTTPError

from fastapi.testclient import TestClient

from omakase.lite import auth, db, routes
from omakase.types import Recommendation
from omakase.web import server


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("OMAKASE_LITE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OMAKASE_ACCOUNT_SECURE_COOKIE", "false")
    monkeypatch.setenv("OMAKASE_PUBLIC_HOSTED", "true")
    routes.reset_rate_limits()
    server.reset_recommendation_jobs()
    return TestClient(server.app, base_url="https://omakase.example")


def _payload(**overrides):
    payload = {
        "llm_type": "deepseek",
        "llm_url": "https://api.deepseek.com",
        "api_key": "request-only-secret",
        "model": "deepseek-v4-pro",
        "source": "anilist",
        "username": "friend",
        "profile": "Patient mysteries and earned character arcs.",
        "mode": "pro",
        "use_planning": False,
        "skip_profile": False,
        "mal_export_b64": "",
        "mal_client_id": "",
    }
    payload.update(overrides)
    return payload


def _poll_until_terminal(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/recommend/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        if data["status"] in {"done", "error", "cancelled"}:
            return data
        time.sleep(0.01)
    raise AssertionError("recommendation job did not finish")


def test_hosted_deepseek_pro_runs_as_background_job_and_forgets_key(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    captured = {}

    def fake_pipeline(cfg):
        captured["model"] = cfg.model
        captured["key"] = cfg.api_key
        return [
            Recommendation(
                title="Monster",
                predicted_score=9.2,
                reasoning="A patient psychological mystery.",
                best_match_from_history="Pluto",
                url="https://anilist.co/anime/19",
                source="anilist",
            )
        ]

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)
    response = client.post("/api/recommend/jobs", json=_payload())

    assert response.status_code == 202
    result = _poll_until_terminal(client, response.json()["job_id"])
    assert result["status"] == "done"
    assert result["recommendations"][0]["title"] == "Monster"
    assert result["account_saved"] is False
    assert captured == {"model": "deepseek-v4-pro", "key": "request-only-secret"}
    assert "request-only-secret" not in server.recommendation_jobs_debug_snapshot()


def test_anilist_user_not_found_job_returns_actionable_error(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def missing_anilist_user(_cfg):
        raise HTTPError(
            "https://graphql.anilist.co",
            404,
            "Not Found",
            {},
            BytesIO(b'{"errors":[{"message":"User not found","status":404}]}'),
        )

    monkeypatch.setattr(server, "run_pipeline", missing_anilist_user)
    response = client.post("/api/recommend/jobs", json=_payload(username="missing-user"))

    assert response.status_code == 202
    result = _poll_until_terminal(client, response.json()["job_id"])
    assert result["status"] == "error"
    assert result["status_code"] == 400
    assert result["detail"] == (
        "AniList could not find that user. Check the username and make sure "
        "the anime list is public."
    )
    assert "key" not in result["detail"].lower()


def test_account_job_saves_results_and_uses_profile_feedback_context(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    conn = db.connect(tmp_path)
    user_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )
    _, prior = db.save_recommendation_run(
        conn,
        user_id=user_id,
        source="anilist",
        source_username="owner",
        provider="deepseek",
        model="deepseek-v4-flash",
        mode="fast",
        recommendations=[
            Recommendation(
                title="A Repeat I Dislike",
                predicted_score=5,
                reasoning="Old result",
                best_match_from_history="Old title",
            )
        ],
    )
    db.set_recommendation_feedback(
        conn,
        user_id=user_id,
        recommendation_id=prior[0]["id"],
        state="not_interested",
    )
    conn.close()

    login = client.post(
        "/account/login",
        data={"email": "owner@example.com", "password": "owner-password"},
        follow_redirects=False,
    )
    assert login.status_code == 302
    csrf = client.get("/api/account/session").json()["csrf_token"]
    captured = {}

    def fake_pipeline(cfg):
        captured["profile"] = cfg.taste_profile
        return [
            Recommendation(
                title="Odd Taxi",
                predicted_score=9.0,
                reasoning="A layered mystery.",
                best_match_from_history="Baccano!",
                url="https://anilist.co/anime/128547",
                source="anilist",
            )
        ]

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)
    response = client.post(
        "/api/recommend/jobs",
        headers={"X-CSRF-Token": csrf},
        json=_payload(profile="Quiet mysteries with tight plotting."),
    )
    assert response.status_code == 202
    result = _poll_until_terminal(client, response.json()["job_id"])

    assert result["status"] == "done"
    assert result["account_saved"] is True
    assert result["recommendations"][0]["id"] > 0
    assert result["recommendations"][0]["feedback_state"] == "neutral"
    assert "Quiet mysteries with tight plotting." in captured["profile"]
    assert "Avoid recommending again: A Repeat I Dislike." in captured["profile"]

    conn = db.connect(tmp_path)
    history = db.recommendation_history(conn, user_id)
    conn.close()
    assert history[0]["recommendations"][0]["title"] == "Odd Taxi"


def test_signed_in_job_requires_csrf(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    conn = db.connect(tmp_path)
    db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )
    conn.close()
    client.post(
        "/account/login",
        data={"email": "owner@example.com", "password": "owner-password"},
    )

    response = client.post("/api/recommend/jobs", json=_payload())
    assert response.status_code == 403


def test_job_capacity_is_bounded(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    release = Event()

    def blocked_pipeline(_cfg):
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(server, "run_pipeline", blocked_pipeline)
    try:
        started = [
            client.post(
                "/api/recommend/jobs",
                json=_payload(username=f"friend-{index}"),
            )
            for index in range(3)
        ]
        assert [response.status_code for response in started] == [202, 202, 202]
        overflow = client.post(
            "/api/recommend/jobs",
            json=_payload(username="one-too-many"),
        )
        assert overflow.status_code == 429
        assert "counter is full" in overflow.json()["detail"]
    finally:
        release.set()


def test_cancelled_account_job_does_not_save_results(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    conn = db.connect(tmp_path)
    user_id = db.bootstrap_admin(
        conn,
        email="owner@example.com",
        password_hash=auth.hash_password("owner-password"),
        display_name="Owner",
    )
    conn.close()
    client.post(
        "/account/login",
        data={"email": "owner@example.com", "password": "owner-password"},
    )
    csrf = client.get("/api/account/session").json()["csrf_token"]
    release = Event()

    def blocked_pipeline(_cfg):
        release.wait(timeout=2)
        return [
            Recommendation(
                title="Should Not Persist",
                predicted_score=8,
                reasoning="Cancelled result",
                best_match_from_history="None",
            )
        ]

    monkeypatch.setattr(server, "run_pipeline", blocked_pipeline)
    started = client.post(
        "/api/recommend/jobs",
        headers={"X-CSRF-Token": csrf},
        json=_payload(),
    )
    job_id = started.json()["job_id"]
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if client.get(f"/api/recommend/jobs/{job_id}").json()["status"] == "running":
            break
        time.sleep(0.01)
    assert client.delete(f"/api/recommend/jobs/{job_id}").json()["status"] == "cancelled"
    release.set()
    time.sleep(0.05)

    conn = db.connect(tmp_path)
    history = db.recommendation_history(conn, user_id)
    conn.close()
    assert history == []


def test_expired_job_receipt_is_pruned(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "run_pipeline", lambda _cfg: [])
    started = client.post("/api/recommend/jobs", json=_payload())
    job_id = started.json()["job_id"]
    assert _poll_until_terminal(client, job_id)["status"] == "done"

    with server._job_lock:
        server._recommendation_jobs[job_id]["updated_at"] = (
            time.monotonic() - server._JOB_TTL_SECONDS - 1
        )
    assert client.get(f"/api/recommend/jobs/{job_id}").status_code == 404
