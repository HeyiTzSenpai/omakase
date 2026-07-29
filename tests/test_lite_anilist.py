from __future__ import annotations

import json

import httpx
import pytest

from omakase.lite import anilist


def test_save_completed_entry_sends_completed_status_and_raw_score(monkeypatch):
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers["Authorization"]
        observed["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {
                    "SaveMediaListEntry": {
                        "id": 77,
                        "mediaId": 99088,
                        "status": "COMPLETED",
                        "score": 80,
                    }
                }
            },
        )

    transport = httpx.MockTransport(handler)
    client_factory = httpx.Client
    monkeypatch.setattr(
        anilist.httpx,
        "Client",
        lambda **kwargs: client_factory(transport=transport),
    )

    result = anilist.save_completed_entry(
        "one-year-access-token",
        99088,
        score_ten=8,
    )

    assert observed["authorization"] == "Bearer one-year-access-token"
    assert observed["payload"]["variables"] == {
        "mediaId": 99088,
        "status": "COMPLETED",
        "scoreRaw": 80,
    }
    query = observed["payload"]["query"]
    assert "SaveMediaListEntry" in query
    assert "scoreRaw: $scoreRaw" in query
    assert "score(format: POINT_100)" in query
    assert result == {
        "id": 77,
        "mediaId": 99088,
        "status": "COMPLETED",
        "score": 80,
    }


def test_save_current_entry_sends_current_status_and_episode_progress(monkeypatch):
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers["Authorization"]
        observed["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {
                    "SaveMediaListEntry": {
                        "id": 78,
                        "mediaId": 99088,
                        "status": "CURRENT",
                        "progress": 3,
                    }
                }
            },
        )

    transport = httpx.MockTransport(handler)
    client_factory = httpx.Client
    monkeypatch.setattr(
        anilist.httpx,
        "Client",
        lambda **kwargs: client_factory(transport=transport),
    )

    result = anilist.save_current_entry(
        "one-year-access-token",
        99088,
        progress=3,
    )

    assert observed["authorization"] == "Bearer one-year-access-token"
    assert observed["payload"]["variables"] == {
        "mediaId": 99088,
        "status": "CURRENT",
        "progress": 3,
    }
    query = observed["payload"]["query"]
    assert "SaveMediaListEntry" in query
    assert "progress: $progress" in query
    assert result == {
        "id": 78,
        "mediaId": 99088,
        "status": "CURRENT",
        "progress": 3,
    }


@pytest.mark.parametrize("progress", [True, 0, -1, 1.5])
def test_save_current_entry_rejects_invalid_episode_progress(progress):
    with pytest.raises(
        ValueError,
        match="AniList progress must be a positive whole number of episodes",
    ):
        anilist.save_current_entry(
            "one-year-access-token",
            99088,
            progress=progress,
        )


@pytest.mark.parametrize(
    "receipt",
    [
        {"id": 78, "mediaId": 1, "status": "CURRENT", "progress": 3},
        {"id": 78, "mediaId": 99088, "status": "COMPLETED", "progress": 3},
        {"id": 78, "mediaId": 99088, "status": "CURRENT", "progress": 2},
    ],
)
def test_save_current_entry_rejects_a_mismatched_receipt(monkeypatch, receipt):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"SaveMediaListEntry": receipt}},
        )

    transport = httpx.MockTransport(handler)
    client_factory = httpx.Client
    monkeypatch.setattr(
        anilist.httpx,
        "Client",
        lambda **kwargs: client_factory(transport=transport),
    )

    with pytest.raises(
        anilist.AniListWriteError,
        match="invalid progress receipt",
    ):
        anilist.save_current_entry(
            "one-year-access-token",
            99088,
            progress=3,
        )
