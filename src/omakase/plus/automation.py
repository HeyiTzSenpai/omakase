"""Download automation — search nyaa.si and add to Real-Debrid.

Orchestrates the pipeline:
  Plan on AniList → Search nyaa.si for best magnet → Add to Real-Debrid

Called from the ``POST /plus/api/plan-and-download`` route.
"""

from __future__ import annotations

from omakase.plus.nyaa import rank_best, search
from omakase.plus.realdebrid import RealDebridClient, RealDebridProviderBlock
from omakase.plus.secrets import read_secret

MAX_RD_CANDIDATES = 5


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

    candidates = rank_best(results, expected_title=title)
    if not candidates:
        return {"status": "not_found", "detail": f'No seedable torrents found for "{title}"'}
    batch_candidates = [candidate for candidate in candidates if candidate.is_batch]
    if batch_candidates:
        candidates = batch_candidates

    # 3. Add magnets to Real-Debrid, falling back when a specific hash is rejected.
    client = RealDebridClient(rd_key)
    last_failed_rd_id: str | None = None
    last_failure_detail: str | None = None
    provider_blocks: list[RealDebridProviderBlock] = []
    non_provider_failures = 0
    attempted = 0

    for candidate in candidates[:MAX_RD_CANDIDATES]:
        attempted += 1
        try:
            rd_id = await client.add_magnet(candidate.magnet)
        except RealDebridProviderBlock as exc:
            provider_blocks.append(exc)
            last_failure_detail = (
                f"Real-Debrid provider blocked a candidate "
                f"({exc.http_status} {exc.error_code}): {exc.detail}"
            )
            continue
        if rd_id is None:
            non_provider_failures += 1
            continue

        # 4. Select all files to start download
        files_selected = await client.select_files(rd_id)
        if not files_selected:
            non_provider_failures += 1
            last_failed_rd_id = rd_id
            last_failure_detail = "Real-Debrid failed to select files for download"
            delete_torrent = getattr(client, "delete_torrent", None)
            if delete_torrent is not None:
                try:
                    await delete_torrent(rd_id)
                except Exception:
                    pass
            continue

        return {
            "status": "ok",
            "rd_id": rd_id,
            "magnet": candidate.magnet,
            "torrent_title": candidate.title,
            "seeders": candidate.seeders,
            "size": candidate.size_display,
        }

    if provider_blocks and non_provider_failures == 0:
        last_block = provider_blocks[-1]
        plural = "candidate" if len(provider_blocks) == 1 else "candidates"
        return {
            "status": "rd_provider_block",
            "http_status": last_block.http_status,
            "error_code": last_block.error_code,
            "detail": (
                f'Real-Debrid provider blocked {len(provider_blocks)} {plural} for "{title}" '
                f"({attempted} of {len(candidates)} ranked candidates attempted); "
                f"last RD response: {last_block.http_status} {last_block.error_code} - "
                f"{last_block.detail}"
            ),
        }

    response = {
        "status": "rd_error",
        "detail": (
            f'Real-Debrid rejected or failed all candidate torrents tried for "{title}" '
            f"({attempted} of {len(candidates)} ranked candidates attempted)"
        ),
    }
    if last_failure_detail is not None:
        response["detail"] = f"{response['detail']}; last failure: {last_failure_detail}"
    elif provider_blocks:
        last_block = provider_blocks[-1]
        response["detail"] = (
            f"{response['detail']}; provider blocked {len(provider_blocks)} candidate"
            f"{'' if len(provider_blocks) == 1 else 's'}; "
            f"last RD response: {last_block.http_status} {last_block.error_code} - "
            f"{last_block.detail}"
        )
    if last_failed_rd_id is not None:
        response["rd_id"] = last_failed_rd_id
    return response
