"""AniList adapter — fetches anime watch history and candidate pool.

Based on the Phase 0 work done for the Homelab Anime Recommender.
AniList blocks Python's default User-Agent, so we set a custom one.
"""

from __future__ import annotations

import json
import re
from urllib.request import Request, urlopen

from omakase.adapters.base import SourceAdapter, register
from omakase.types import MediaItem, SourceData

API_URL = "https://graphql.anilist.co"
USER_AGENT = "Omakase/0.1 (homelab; +https://github.com/HeyiTzSenpai/omakase)"

# AniList relation types that mean "same franchise". Used to drop candidates
# whose relations point at anything in the user's history.
FRANCHISE_RELATION_TYPES = frozenset(
    {
        "PREQUEL",
        "SEQUEL",
        "PARENT",
        "SIDE_STORY",
        "SUMMARY",
        "ALTERNATIVE",
        "SPIN_OFF",
        "COMPILATION",
        "CONTAINS",
    }
)

_PAREN_TAIL = re.compile(r"\s*\([^)]*\)\s*$")
_SUBTITLE_SEPARATORS = (": ", " - ", " – ", " — ")
_SEASON_TAIL = re.compile(
    r"\s+(?:season\s+\d+|s\d+|part\s+\d+|\d+(?:st|nd|rd|th)\s+season|"
    r"the\s+(?:movie|final|animation)|movie|film|ova|special|specials|"
    r"i{2,}|iv|vi+|ix|x|\d+)$",
    re.IGNORECASE,
)


def _title_stem(title: str | None) -> str:
    """Normalize an anime title to its franchise stem for prefix matching.

    Used as a belt to AniList's relations graph: if AniList's metadata is
    incomplete, two titles sharing a normalized stem (e.g. "gintama" from
    both "Gintama Season 2" and "Gintama: THE VERY FINAL") get treated as
    the same franchise.
    """
    if not title:
        return ""
    s = title.strip().lower()
    s = _PAREN_TAIL.sub("", s).strip()
    # Split at the EARLIEST subtitle separator, not the first one in tuple
    # order — a title like "Chainsaw Man – The Movie: Reze Arc" has both
    # " – " and ": " and we want the dash, not the colon.
    earliest = min(
        (i for i in (s.find(sep) for sep in _SUBTITLE_SEPARATORS) if i != -1),
        default=-1,
    )
    if earliest != -1:
        s = s[:earliest]
    # Strip iteratively in case multiple markers stack (e.g. "X Season 2 Part 2").
    while True:
        new = _SEASON_TAIL.sub("", s).strip()
        if new == s:
            break
        s = new
    return s


def _build_franchise_block(history: list[MediaItem]) -> tuple[set[int], set[str]]:
    """Build the franchise exclusion set from history.

    Returns (history_ids, history_stems). A candidate is "in-franchise" iff
    its id, any of its related_ids, or its title stem hits one of these sets.
    """
    ids = {m.id for m in history if m.id}
    stems = set()
    for m in history:
        for t in (m.title_english, m.title_romaji):
            stem = _title_stem(t)
            if stem:
                stems.add(stem)
    return ids, stems


def _candidate_is_in_franchise(
    candidate: MediaItem, history_ids: set[int], history_stems: set[str]
) -> bool:
    """Return True if this candidate belongs to the same franchise as any history entry."""
    if candidate.id in history_ids:
        return True
    if any(rid in history_ids for rid in candidate.related_ids):
        return True
    for t in (candidate.title_english, candidate.title_romaji):
        stem = _title_stem(t)
        if stem and stem in history_stems:
            return True
    return False


@register("anilist")
class AniListAdapter(SourceAdapter):
    name = "anilist"

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        req = Request(
            API_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())

    def _fetch_history(self, username: str) -> list[MediaItem]:
        query = """
        query ($username: String) {
          MediaListCollection(userName: $username, type: ANIME) {
            lists {
              entries {
                score(format: POINT_10)
                status
                media {
                  id
                  title { romaji english }
                  genres
                  tags { name rank }
                  meanScore
                  format
                  episodes
                  studios(isMain: true) { nodes { name } }
                }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"username": username})
        lists = data.get("data", {}).get("MediaListCollection", {}).get("lists", [])
        seen_ids: set[int] = set()
        items: list[MediaItem] = []
        for lst in lists:
            for entry in lst.get("entries", []):
                media = entry.get("media", {})
                mid = media.get("id")
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)
                tags = [t["name"] for t in media.get("tags", []) if t.get("rank", 0) >= 80]
                studio = None
                studios = media.get("studios", {}).get("nodes", [])
                if studios:
                    studio = studios[0].get("name")
                items.append(
                    MediaItem(
                        id=mid,
                        title_romaji=media.get("title", {}).get("romaji", ""),
                        title_english=media.get("title", {}).get("english"),
                        genres=media.get("genres", []),
                        tags=tags,
                        score=entry.get("score"),
                        status=entry.get("status"),
                        format=media.get("format"),
                        episodes=media.get("episodes"),
                        studio=studio,
                        mean_score=media.get("meanScore"),
                    )
                )
        return items

    def _analyze_genre_affinity(self, history: list[MediaItem]) -> list[str]:
        """Calculate which genres the user actually likes based on scored history.

        Returns up to 5 genres with the strongest positive affinity, sorted best-first.
        """
        genre_scores: dict[str, list[int]] = {}
        for m in history:
            if m.score is None:
                continue
            for g in m.genres:
                genre_scores.setdefault(g, []).append(m.score)

        if not genre_scores:
            return []

        affinity: list[tuple[str, float]] = []
        for genre, scores in genre_scores.items():
            if len(scores) < 2:
                continue
            avg = sum(scores) / len(scores)
            loved = sum(1 for s in scores if s >= 8)
            # Weight: average score + bonus for each loved entry
            affinity.append((genre, avg + loved * 0.5))

        affinity.sort(key=lambda x: x[1], reverse=True)
        return [g for g, _ in affinity[:5]]

    def _fetch_candidates(self, exclude_ids: list[int], pool_size: int) -> list[MediaItem]:
        """Fetch candidates using genre-targeted search when history is available.

        Falls back to global SCORE_DESC when called without history context.
        """
        # Base query used for both targeted and global fetches
        base_query = """
        query ($excludeIds: [Int], $page: Int, $genres: [String]) {
          Page(perPage: 50, page: $page) {
            media(
              type: ANIME
              sort: [SCORE_DESC]
              genre_in: $genres
              status_in: [RELEASING, FINISHED]
              id_not_in: $excludeIds
            ) {
              id
              title { romaji english }
              genres
              tags { name rank }
              meanScore
              description(asHtml: false)
              format
              episodes
              studios(isMain: true) { nodes { name } }
              relations { edges { relationType node { id type } } }
            }
          }
        }
        """

        def _parse_media(media_list: list[dict]) -> list[MediaItem]:
            items: list[MediaItem] = []
            for m in media_list:
                tags = [t["name"] for t in m.get("tags", []) if t.get("rank", 0) >= 80]
                studio = None
                studios = m.get("studios", {}).get("nodes", [])
                if studios:
                    studio = studios[0].get("name")
                desc = m.get("description", "")
                if desc and len(desc) > 200:
                    desc = desc[:200] + "..."
                related_ids: list[int] = []
                for edge in m.get("relations", {}).get("edges", []) or []:
                    rtype = edge.get("relationType")
                    node = edge.get("node") or {}
                    if (
                        rtype in FRANCHISE_RELATION_TYPES
                        and node.get("type") == "ANIME"
                        and node.get("id")
                    ):
                        related_ids.append(node["id"])
                items.append(
                    MediaItem(
                        id=m["id"],
                        title_romaji=m.get("title", {}).get("romaji", ""),
                        title_english=m.get("title", {}).get("english"),
                        genres=m.get("genres", []),
                        tags=tags,
                        format=m.get("format"),
                        episodes=m.get("episodes"),
                        studio=studio,
                        description=desc,
                        mean_score=m.get("meanScore"),
                        related_ids=related_ids,
                    )
                )
            return items

        def _fetch_page(page: int, genres: list[str] | None = None) -> list[dict]:
            data = self._graphql(
                base_query,
                {"excludeIds": exclude_ids, "page": page, "genres": genres or []},
            )
            return data.get("data", {}).get("Page", {}).get("media", [])

        seen_ids: set[int] = set(exclude_ids)
        all_candidates: list[MediaItem] = []

        # Fetch a baseline of 100 top-scored anime (no genre filter)
        page = 1
        while len(all_candidates) < 100:
            media_list = _fetch_page(page)
            if not media_list:
                break
            for item in _parse_media(media_list):
                if item.id not in seen_ids:
                    seen_ids.add(item.id)
                    all_candidates.append(item)
            page += 1

        return all_candidates[:pool_size]

    def _fetch_candidates_targeted(
        self,
        exclude_ids: list[int],
        pool_size: int,
        preferred_genres: list[str],
    ) -> list[MediaItem]:
        """Fetch candidates targeted by the user's preferred genres.

        Fetches ~60 per preferred genre + a baseline of 60 general top-scored.
        Deduplicates across all batches.
        """
        base_query = """
        query ($excludeIds: [Int], $page: Int, $genres: [String]) {
          Page(perPage: 50, page: $page) {
            media(
              type: ANIME
              sort: [SCORE_DESC]
              genre_in: $genres
              status_in: [RELEASING, FINISHED]
              id_not_in: $excludeIds
            ) {
              id
              title { romaji english }
              genres
              tags { name rank }
              meanScore
              description(asHtml: false)
              format
              episodes
              studios(isMain: true) { nodes { name } }
              relations { edges { relationType node { id type } } }
            }
          }
        }
        """

        def _parse_media(media_list: list[dict]) -> list[MediaItem]:
            items: list[MediaItem] = []
            for m in media_list:
                tags = [t["name"] for t in m.get("tags", []) if t.get("rank", 0) >= 80]
                studio = None
                studios = m.get("studios", {}).get("nodes", [])
                if studios:
                    studio = studios[0].get("name")
                desc = m.get("description", "")
                if desc and len(desc) > 200:
                    desc = desc[:200] + "..."
                related_ids: list[int] = []
                for edge in m.get("relations", {}).get("edges", []) or []:
                    rtype = edge.get("relationType")
                    node = edge.get("node") or {}
                    if (
                        rtype in FRANCHISE_RELATION_TYPES
                        and node.get("type") == "ANIME"
                        and node.get("id")
                    ):
                        related_ids.append(node["id"])
                items.append(
                    MediaItem(
                        id=m["id"],
                        title_romaji=m.get("title", {}).get("romaji", ""),
                        title_english=m.get("title", {}).get("english"),
                        genres=m.get("genres", []),
                        tags=tags,
                        format=m.get("format"),
                        episodes=m.get("episodes"),
                        studio=studio,
                        description=desc,
                        mean_score=m.get("meanScore"),
                        related_ids=related_ids,
                    )
                )
            return items

        def _fetch_page(page: int, genres: list[str]) -> list[dict]:
            data = self._graphql(
                base_query,
                {"excludeIds": exclude_ids, "page": page, "genres": genres},
            )
            return data.get("data", {}).get("Page", {}).get("media", [])

        seen_ids: set[int] = set(exclude_ids)
        all_candidates: list[MediaItem] = []

        # Fetch per preferred genre (60 each, max 2 pages)
        per_genre = min(60, pool_size // len(preferred_genres)) if preferred_genres else 60
        for genre in preferred_genres:
            for page in (1, 2):
                media_list = _fetch_page(page, [genre])
                if not media_list:
                    break
                for item in _parse_media(media_list):
                    if item.id not in seen_ids:
                        seen_ids.add(item.id)
                        all_candidates.append(item)
                if len([c for c in all_candidates if genre in c.genres]) >= per_genre:
                    break

        # Top up with general top-scored (no genre filter) to reach pool_size
        if len(all_candidates) < pool_size:
            remaining = pool_size - len(all_candidates)
            page = 1
            while len(all_candidates) < pool_size:
                media_list = _fetch_page(page, [])
                if not media_list:
                    break
                for item in _parse_media(media_list):
                    if item.id not in seen_ids:
                        seen_ids.add(item.id)
                        all_candidates.append(item)
                page += 1
                if page > (remaining // 50) + 3:
                    break

        return all_candidates[:pool_size]

    def _fetch_planning(self, username: str) -> list[MediaItem]:
        """Fetch the user's Planning (plan-to-watch) entries as candidates."""
        query = """
        query ($username: String) {
          MediaListCollection(userName: $username, type: ANIME) {
            lists {
              entries {
                status
                media {
                  id
                  title { romaji english }
                  genres
                  tags { name rank }
                  meanScore
                  description(asHtml: false)
                  format
                  episodes
                  studios(isMain: true) { nodes { name } }
                }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"username": username})
        lists = data.get("data", {}).get("MediaListCollection", {}).get("lists", [])
        items: list[MediaItem] = []
        for lst in lists:
            for entry in lst.get("entries", []):
                if entry.get("status") != "PLANNING":
                    continue
                media = entry.get("media", {})
                tags = [t["name"] for t in media.get("tags", []) if t.get("rank", 0) >= 80]
                studio = None
                studios = media.get("studios", {}).get("nodes", [])
                if studios:
                    studio = studios[0].get("name")
                desc = media.get("description", "")
                if desc and len(desc) > 200:
                    desc = desc[:200] + "..."
                items.append(
                    MediaItem(
                        id=media["id"],
                        title_romaji=media.get("title", {}).get("romaji", ""),
                        title_english=media.get("title", {}).get("english"),
                        genres=media.get("genres", []),
                        tags=tags,
                        format=media.get("format"),
                        episodes=media.get("episodes"),
                        studio=studio,
                        description=desc,
                        mean_score=media.get("meanScore"),
                    )
                )
        return items

    def fetch(self, username: str, pool_size: int = 100, **kwargs) -> SourceData:
        history = self._fetch_history(username)
        exclude_ids = [m.id for m in history if m.id]

        use_planning = kwargs.get("use_planning", False)
        if use_planning:
            candidates = self._fetch_planning(username)
            watched_ids = {
                m.id for m in history if m.status in {"CURRENT", "COMPLETED", "DROPPED", "PAUSED"}
            }
            candidates = [c for c in candidates if c.id not in watched_ids]
        else:
            # Analyze genre affinity from scored history
            preferred_genres = self._analyze_genre_affinity(history)
            if preferred_genres:
                candidates = self._fetch_candidates_targeted(
                    exclude_ids, pool_size, preferred_genres
                )
            else:
                candidates = self._fetch_candidates(exclude_ids, pool_size)
            # Drop anything that's in the same franchise as a history entry:
            # (a) AniList id_not_in only catches exact id matches — sequels and
            # spin-offs have different ids and slip through;
            # (b) we expanded the GraphQL to fetch each candidate's franchise
            # relations, so a sequel of a dropped show points back at the
            # dropped id and we exclude it;
            # (c) belt-and-suspenders: title-stem dedup catches franchises
            # AniList's relations metadata doesn't connect.
            history_ids, history_stems = _build_franchise_block(history)
            candidates = [
                c
                for c in candidates
                if not _candidate_is_in_franchise(c, history_ids, history_stems)
            ]
        return SourceData(
            username=username,
            history=history,
            candidates=candidates,
            source_name="anilist",
        )
