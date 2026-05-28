"""Download automation — search nyaa.si and add to Real-Debrid.

Orchestrates the pipeline:
  Plan on AniList → Search nyaa.si for best magnet → Add to Real-Debrid

Called from the ``POST /plus/api/plan-and-download`` route.
"""

from __future__ import annotations

from omakase.plus.nyaa import find_best, search
from omakase.plus.realdebrid import RealDebridClient
from omakase.plus.secrets import read_secret


async def search_and_download(db, user_id: int, title: str) -> dict:
    """Search nyaa.si for an anime title and add the best torrent to Real-Debrid.

    Returns a status dict suitable for JSON response:
      {"status": "ok", "rd_id": "...", "magnet": "...", "torrent_title": "..."}
      {"status": "not_found", "detail": "No torrents found on nyaa.si"}
      {"status": "rd_error", "detail": "..."}
      {"status": "no_rd_key", "detail": "Real-Debrid API key not configured"}
    """
    # 1. Read Real-Debrid API key from stored secrets
    rd_key = read_secret(db, user_id, "realdebrid_api_key")
    if not rd_key:
        return {"status": "no_rd_key", "detail": "Real-Debrid API key not configured"}

    # 2. Search nyaa.si
    results = await search(title, trusted_only=False)
    if not results:
        return {"status": "not_found", "detail": f'No torrents found for "{title}" on nyaa.si'}

    best = find_best(results, prefer_trusted=True, prefer_no_batch=True)
    if best is None:
        return {"status": "not_found", "detail": f'No seedable torrents found for "{title}"'}

    # 3. Add magnet to Real-Debrid
    client = RealDebridClient(rd_key)
    rd_id = await client.add_magnet(best.magnet)
    if rd_id is None:
        return {"status": "rd_error", "detail": "Real-Debrid rejected the magnet link"}

    # 4. Select all files to start download
    await client.select_files(rd_id)

    return {
        "status": "ok",
        "rd_id": rd_id,
        "magnet": best.magnet,
        "torrent_title": best.title,
        "seeders": best.seeders,
        "size": best.size_display,
    }
