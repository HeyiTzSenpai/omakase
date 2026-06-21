"""Tests for the Plan → Nyaa → Real-Debrid automation pipeline."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from omakase.plus.nyaa import NyaaTorrent


def test_automation_module_imports():
    """Verify the automation module can be imported."""
    from omakase.plus import automation

    assert hasattr(automation, "search_and_download")


def test_db_fixture_works():
    """Verify test database setup works."""
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE user_secrets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            key_name TEXT NOT NULL,
            encrypted_value TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, key_name)
        )"""
    )
    conn.execute(
        "INSERT INTO user_secrets (user_id, key_name, encrypted_value) VALUES (?, ?, ?)",
        (1, "realdebrid_api_key", "encrypted-fake-key"),
    )
    conn.commit()
    rows = conn.execute("SELECT * FROM user_secrets WHERE user_id = 1").fetchall()
    assert len(rows) == 1
    assert rows[0]["key_name"] == "realdebrid_api_key"
    conn.close()


def test_search_and_download_uses_title_level_batch_preference():
    """Plus Download should prefer a complete high-quality batch over one episode."""
    from omakase.plus.automation import search_and_download

    batch = NyaaTorrent(
        title="[SubsPlease] Frieren - 01-28 Complete [1080p][WEB-DL][AAC]",
        magnet="magnet:?xt=urn:btih:BATCH",
        seeders=80,
        leechers=5,
        size_bytes=28_000_000_000,
        size_display="26.1 GiB",
        pub_date=datetime.now(timezone.utc),
        is_trusted=False,
        is_batch=True,
    )
    single_episode = NyaaTorrent(
        title="[GoodGroup] Frieren - 01 [1080p][WEB-DL]",
        magnet="magnet:?xt=urn:btih:SINGLE",
        seeders=100,
        leechers=5,
        size_bytes=1_400_000_000,
        size_display="1.3 GiB",
        pub_date=datetime.now(timezone.utc),
        is_trusted=False,
        is_batch=False,
    )

    class FakeRealDebridClient:
        def __init__(self, api_key: str):
            self.api_key = api_key

        async def add_magnet(self, magnet: str) -> str | None:
            assert magnet == batch.magnet
            return "rd-batch"

        async def select_files(self, torrent_id: str, files: str = "all") -> bool:
            assert torrent_id == "rd-batch"
            assert files == "all"
            return True

    async def fake_search(title: str, trusted_only: bool = False):
        assert title == "Frieren"
        assert trusted_only is False
        return [single_episode, batch]

    with (
        patch("omakase.plus.automation.read_secret", return_value="rd-key"),
        patch("omakase.plus.automation.search", side_effect=fake_search),
        patch("omakase.plus.automation.RealDebridClient", new=FakeRealDebridClient),
    ):
        result = asyncio.run(search_and_download(db=None, user_id=1, title="Frieren"))

    assert result["status"] == "ok"
    assert result["rd_id"] == "rd-batch"
    assert result["magnet"] == batch.magnet
    assert result["torrent_title"] == batch.title
    assert result["size"] == batch.size_display
