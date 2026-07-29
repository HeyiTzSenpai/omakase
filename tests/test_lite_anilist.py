from __future__ import annotations

import json

import httpx

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
