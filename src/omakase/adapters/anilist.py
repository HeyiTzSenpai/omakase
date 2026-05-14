"""AniList adapter — fetches anime watch history and candidate pool.

Based on the Phase 0 work done for the Homelab Anime Recommender.
AniList blocks Python's default User-Agent, so we set a custom one.
"""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

from omakase.adapters.base import SourceAdapter, register
from omakase.types import MediaItem, SourceData

API_URL = "https://graphql.anilist.co"
USER_AGENT = "Omakase/0.1 (homelab; +https://github.com/HeyiTzSenpai/omakase)"


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

    def _fetch_candidates(self, exclude_ids: list[int], pool_size: int) -> list[MediaItem]:
        """Fetch candidate shows the user hasn't watched.

        AniList's unauthenticated Page caps at 50 per request,
        so we paginate if pool_size > 50.
        """
        query = """
        query ($excludeIds: [Int], $page: Int) {
          Page(perPage: 50, page: $page) {
            media(
              type: ANIME
              sort: [POPULARITY_DESC]
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
            }
          }
        }
        """
        items: list[MediaItem] = []
        page = 1
        while len(items) < pool_size:
            data = self._graphql(query, {"excludeIds": exclude_ids, "page": page})
            media_list = data.get("data", {}).get("Page", {}).get("media", [])
            if not media_list:
                break
            for m in media_list:
                tags = [t["name"] for t in m.get("tags", []) if t.get("rank", 0) >= 80]
                studio = None
                studios = m.get("studios", {}).get("nodes", [])
                if studios:
                    studio = studios[0].get("name")
                desc = m.get("description", "")
                if desc and len(desc) > 200:
                    desc = desc[:200] + "..."
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
                    )
                )
            page += 1
        return items[:pool_size]

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
            # In planning mode the candidates ARE drawn from the user's lists,
            # so they intentionally overlap with history. Only drop entries
            # the user has actively watched/dropped/paused.
            watched_ids = {m.id for m in history if m.status in {"CURRENT", "COMPLETED", "DROPPED", "PAUSED"}}
            candidates = [c for c in candidates if c.id not in watched_ids]
        else:
            candidates = self._fetch_candidates(exclude_ids, pool_size)
            # Safety filter for the popular-pool path: no history IDs may leak.
            seen = set(exclude_ids)
            candidates = [c for c in candidates if c.id not in seen]
        return SourceData(
            username=username,
            history=history,
            candidates=candidates,
            source_name="anilist",
        )
