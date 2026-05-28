"""Nyaa.si RSS feed client — search anime torrents and find the best magnet.

Uses the RSS feed at ``https://nyaa.si/?page=rss`` which returns clean XML
with magnet links, seeders, size, and title. No API key required.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

NYAA_RSS_URL = "https://nyaa.si/"

# Category codes: 1_2 = Anime - English Translated
DEFAULT_CATEGORY = "1_2"

# Namespaces used in nyaa.si RSS feed
_NS = {
    "nyaa": "https://nyaa.si/xmlns/nyaa",
    "torrent": "http://xmlns.ezrss.it/0.1/",
}


@dataclass
class NyaaTorrent:
    """A single torrent result from nyaa.si."""

    title: str
    magnet: str
    seeders: int
    leechers: int
    size_bytes: int
    size_display: str
    pub_date: datetime
    is_trusted: bool
    is_batch: bool


def _parse_size(size_str: str) -> tuple[int, str]:
    """Parse a nyaa size string like '1.4 GiB' into (bytes, display)."""
    if not size_str:
        return 0, ""
    parts = size_str.strip().split()
    if len(parts) != 2:
        return 0, size_str
    try:
        value = float(parts[0])
    except ValueError:
        return 0, size_str
    unit = parts[1].upper()
    multipliers = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}
    return int(value * multipliers.get(unit, 1)), size_str


def _parse_pubdate(date_str: str) -> datetime:
    """Parse RSS pubDate like 'Sun, 28 May 2026 00:00:00 -0000'."""
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(date_str)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


_BATCH_PATTERNS = [
    re.compile(r"\b(complete|batch|all.episodes?|s\d{2})\b", re.IGNORECASE),
    re.compile(r"\bseason\s*\d\b", re.IGNORECASE),
    re.compile(r"\b\d{2,3}-\d{2,3}\b"),  # episode ranges like 01-12
]


def _is_likely_batch(title: str) -> bool:
    """Heuristic: detect batch/complete-series torrents from title patterns."""
    return any(p.search(title) for p in _BATCH_PATTERNS)


async def search(
    query: str,
    category: str = DEFAULT_CATEGORY,
    trusted_only: bool = False,
    timeout: float = 10.0,
) -> list[NyaaTorrent]:
    """Search nyaa.si RSS feed for anime torrents.

    Args:
        query: Search term (e.g. anime title).
        category: Nyaa category code (default: 1_2 = Anime English).
        trusted_only: If True, only return trusted uploads.
        timeout: HTTP timeout in seconds.

    Returns:
        List of NyaaTorrent results, sorted by seeders descending.
    """
    params = {
        "page": "rss",
        "q": query,
        "c": category,
        "f": "2" if trusted_only else "0",  # 2 = trusted only
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(NYAA_RSS_URL, params=params)
        resp.raise_for_status()

    # Nyaa RSS sometimes contains invalid XML characters (control chars,
    # bare ampersands in titles). Clean them up before parsing.
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", resp.text)
    # Fix unescaped & that aren't part of a valid entity
    text = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)", "&amp;", text)

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    torrents: list[NyaaTorrent] = []

    for item in root.findall(".//item"):
        title_el = item.find("title")
        pubdate_el = item.find("pubDate")

        title = title_el.text if title_el is not None and title_el.text else ""
        if not title:
            continue

        # Construct magnet link from the <nyaa:infoHash>
        magnet = ""
        info_hash_el = item.find("nyaa:infoHash", _NS)
        if info_hash_el is not None and info_hash_el.text:
            magnet = f"magnet:?xt=urn:btih:{info_hash_el.text}&dn={title}"

        # Get nyaa-specific fields
        seeders_el = item.find("nyaa:seeders", _NS)
        leechers_el = item.find("nyaa:leechers", _NS)
        size_el = item.find("nyaa:size", _NS)
        trusted_el = item.find("nyaa:trusted", _NS)

        seeders = int(seeders_el.text) if seeders_el is not None and seeders_el.text else 0
        leechers = int(leechers_el.text) if leechers_el is not None and leechers_el.text else 0
        size_display = size_el.text if size_el is not None and size_el.text else ""
        size_bytes, _ = _parse_size(size_display)
        is_trusted = trusted_el is not None and trusted_el.text == "Yes"
        pub_date = _parse_pubdate(
            pubdate_el.text if pubdate_el is not None and pubdate_el.text else ""
        )

        torrents.append(
            NyaaTorrent(
                title=title,
                magnet=magnet,
                seeders=seeders,
                leechers=leechers,
                size_bytes=size_bytes,
                size_display=size_display,
                pub_date=pub_date,
                is_trusted=is_trusted,
                is_batch=_is_likely_batch(title),
            )
        )

    # Sort by seeders descending
    torrents.sort(key=lambda t: t.seeders, reverse=True)
    return torrents


def find_best(
    torrents: list[NyaaTorrent],
    *,
    prefer_trusted: bool = True,
    prefer_no_batch: bool = True,
    min_seeders: int = 1,
) -> NyaaTorrent | None:
    """Pick the best torrent from search results.

    Heuristic (in priority order):
    1. Trusted uploads preferred
    2. Non-batch releases preferred (individual episodes over complete series)
    3. Highest seeders wins

    Returns ``None`` if no torrent meets the minimum seeders threshold.
    """
    if not torrents:
        return None

    candidates = [t for t in torrents if t.seeders >= min_seeders]
    if not candidates:
        return None

    # Sort by: trusted > non-batch > seeders
    def _key(t: NyaaTorrent) -> tuple[int, int, int]:
        return (
            0 if (prefer_trusted and t.is_trusted) else 1,
            0 if (prefer_no_batch and not t.is_batch) else 1,
            -t.seeders,  # negative for descending
        )

    candidates.sort(key=_key)
    return candidates[0]
