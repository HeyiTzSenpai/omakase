"""Tests for Real-Debrid API client.

Network-dependent tests are skipped; pure logic is tested here.
For integration tests, set REALDEBRID_API_KEY in env.
"""

from __future__ import annotations

from omakase.plus.realdebrid import RDTorrent, RealDebridClient


class TestRDTorrent:
    def test_from_dict(self):
        t = RDTorrent(
            id="ABC123",
            filename="[SubsPlease] Dungeon Meshi - 01",
            status="downloading",
            progress=50.0,
            bytes_total=1_500_000_000,
            bytes_done=750_000_000,
            links=[],
        )
        assert t.id == "ABC123"
        assert t.status == "downloading"
        assert t.progress == 50.0

    def test_statuses(self):
        for status in ["magnet_error", "waiting_files_selection", "downloading", "downloaded", "error"]:
            t = RDTorrent(id="x", filename="f", status=status, progress=0, bytes_total=0, bytes_done=0, links=[])
            assert t.status == status


class TestRealDebridClient:
    def test_headers(self):
        client = RealDebridClient("test-api-key")
        headers = client._headers()
        assert headers["Authorization"] == "Bearer test-api-key"

    def test_instantiation(self):
        client = RealDebridClient("api-key-123", timeout=30.0)
        assert client.api_key == "api-key-123"
        assert client.timeout == 30.0
