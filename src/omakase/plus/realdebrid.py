"""Real-Debrid REST API client — add magnet links and manage torrents.

API docs: https://api.real-debrid.com/
Token: https://real-debrid.com/apitoken
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

RD_API = "https://api.real-debrid.com/rest/1.0"


class RealDebridProviderBlock(Exception):
    """Real-Debrid rejected a torrent because the provider blocked the hash."""

    def __init__(self, *, http_status: int, error_code: str, detail: str):
        self.http_status = http_status
        self.error_code = error_code
        self.detail = detail
        super().__init__(detail)


@dataclass
class RDTorrent:
    """A torrent in the Real-Debrid queue."""

    id: str
    filename: str
    status: str  # magnet_error, waiting_files_selection, downloading, downloaded, error
    progress: float  # 0.0–100.0
    bytes_total: int
    bytes_done: int
    links: list[str]


class RealDebridClient:
    """Async client for the Real-Debrid REST API."""

    def __init__(self, api_key: str, timeout: float = 15.0):
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def add_magnet(self, magnet: str) -> str | None:
        """Add a magnet link to Real-Debrid.

        Returns the torrent ID on success, or ``None`` on failure.
        Raises ``RealDebridProviderBlock`` for known provider-blocked hashes.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{RD_API}/torrents/addMagnet",
                headers=self._headers(),
                data={"magnet": magnet},
            )
            if resp.status_code in (201, 200):
                data = resp.json()
                return data.get("id")
            block = _provider_block_from_response(resp)
            if block is not None:
                raise block
            return None

    async def select_files(self, torrent_id: str, files: str = "all") -> bool:
        """Select files to download from a torrent. Default: all files."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{RD_API}/torrents/selectFiles/{torrent_id}",
                headers=self._headers(),
                data={"files": files},
            )
            return resp.status_code in (200, 201, 204)

    async def get_torrent(self, torrent_id: str) -> RDTorrent | None:
        """Get info for a specific torrent."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{RD_API}/torrents/info/{torrent_id}",
                headers=self._headers(),
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            return RDTorrent(
                id=data.get("id", ""),
                filename=data.get("filename", ""),
                status=data.get("status", ""),
                progress=float(data.get("progress", 0)),
                bytes_total=int(data.get("bytes", 0)),
                bytes_done=0,
                links=data.get("links", []) or [],
            )

    async def list_torrents(self) -> list[RDTorrent]:
        """List all active torrents."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{RD_API}/torrents",
                headers=self._headers(),
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            torrents: list[RDTorrent] = []
            for t in data:
                torrents.append(
                    RDTorrent(
                        id=t.get("id", ""),
                        filename=t.get("filename", ""),
                        status=t.get("status", ""),
                        progress=float(t.get("progress", 0)),
                        bytes_total=int(t.get("bytes", 0)),
                        bytes_done=0,
                        links=t.get("links", []) or [],
                    )
                )
            return torrents

    async def delete_torrent(self, torrent_id: str) -> bool:
        """Delete a torrent from Real-Debrid. Returns True on success."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.delete(
                f"{RD_API}/torrents/delete/{torrent_id}",
                headers=self._headers(),
            )
            return resp.status_code in (200, 204)


def _provider_block_from_response(resp: httpx.Response) -> RealDebridProviderBlock | None:
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    error = str(data.get("error") or "")
    error_code = error or str(data.get("error_code") or "")
    detail = str(
        data.get("error_details")
        or data.get("error_description")
        or data.get("error")
        or getattr(resp, "text", "")
        or f"Real-Debrid request failed with HTTP {resp.status_code}"
    )

    if resp.status_code == 451 or error == "infringing_file" or "infringing_file" in detail:
        return RealDebridProviderBlock(
            http_status=resp.status_code,
            error_code=error_code or "provider_block",
            detail=detail,
        )
    return None
