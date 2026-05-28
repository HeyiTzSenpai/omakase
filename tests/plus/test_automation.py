"""Tests for the Plan → Nyaa → Real-Debrid automation pipeline."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path


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
