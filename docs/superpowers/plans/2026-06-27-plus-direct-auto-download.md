# Plus Direct Auto-Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a logged-in Plus user enter an anime title, AniList URL, AniList ID, or specific season from a phone and have Omakase resolve it, queue it, and start the best available Real-Debrid download.

**Architecture:** Keep Brief 03 private Plus only. Add a small AniList resolver module for URL/id/title search, reuse `anilist_plannings` plus `download_attempts` as the durable queue/telemetry model, and refactor route-level download status persistence so recommendation downloads, queue retries, and direct requests share one implementation path.

**Tech Stack:** FastAPI routes, Jinja dashboard template, SQLite migrations already present, AniList GraphQL over `httpx`, Nyaa RSS ranking, Real-Debrid client, pytest.

---

### Task 1: AniList Direct Resolver

**Files:**
- Create: `src/omakase/plus/direct.py`
- Test: `tests/plus/test_direct_download.py`

- [ ] **Step 1: Write failing resolver tests**

```python
from unittest.mock import MagicMock, patch

from omakase.plus.direct import parse_anilist_id, resolve_direct_request


def test_parse_anilist_id_accepts_url_and_bare_id():
    assert parse_anilist_id("https://anilist.co/anime/21/Cowboy-Bebop/") == 21
    assert parse_anilist_id("21") == 21
    assert parse_anilist_id("Cowboy Bebop") is None


def test_resolve_direct_request_fetches_media_by_id():
    response = MagicMock()
    response.json.return_value = {
        "data": {
            "Media": {
                "id": 21,
                "title": {"romaji": "Cowboy Bebop", "english": "Cowboy Bebop", "native": "カウボーイビバップ"},
                "format": "TV",
                "status": "FINISHED",
                "episodes": 26,
                "season": "SPRING",
                "seasonYear": 1998,
            }
        }
    }
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.post.return_value = response
    client.__enter__.return_value = client

    with patch("httpx.Client", return_value=client):
        result = resolve_direct_request("https://anilist.co/anime/21/Cowboy-Bebop/")

    assert result.anilist_id == 21
    assert result.title == "Cowboy Bebop"
    assert result.search_titles[:2] == ["Cowboy Bebop", "カウボーイビバップ"]


def test_resolve_direct_request_searches_title_with_season_hint():
    response = MagicMock()
    response.json.return_value = {
        "data": {
            "Page": {
                "media": [
                    {
                        "id": 99699,
                        "title": {"romaji": "Golden Kamuy 3rd Season", "english": "Golden Kamuy Season 3", "native": "ゴールデンカムイ 第三期"},
                        "format": "TV",
                        "status": "FINISHED",
                        "episodes": 12,
                        "season": "FALL",
                        "seasonYear": 2020,
                    }
                ]
            }
        }
    }
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.post.return_value = response
    client.__enter__.return_value = client

    with patch("httpx.Client", return_value=client):
        result = resolve_direct_request("Golden Kamuy", season="3")

    assert result.anilist_id == 99699
    assert result.title == "Golden Kamuy Season 3"
    assert "Golden Kamuy 3rd Season" in result.search_titles
```

- [ ] **Step 2: Run resolver tests to verify RED**

Run: `PYTHONPATH=src C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest tests/plus/test_direct_download.py -q`

Expected: import failure because `omakase.plus.direct` does not exist.

- [ ] **Step 3: Implement resolver**

Create `src/omakase/plus/direct.py` with:

```python
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

API_URL = "https://graphql.anilist.co"
USER_AGENT = "Omakase/0.1 (+https://github.com/HeyiTzSenpai/omakase)"

_ANILIST_ID_RE = re.compile(r"anilist\.co/anime/(\d+)", re.I)


@dataclass(frozen=True)
class DirectDownloadTarget:
    anilist_id: int
    title: str
    search_titles: list[str]
    format: str = ""
    status: str = ""
    episodes: int | None = None
    season: str = ""
    season_year: int | None = None


def parse_anilist_id(value: str) -> int | None:
    text = (value or "").strip()
    match = _ANILIST_ID_RE.search(text)
    if match:
        return int(match.group(1))
    if text.isdigit():
        return int(text)
    return None


def _preferred_title(media: dict) -> str:
    title = media.get("title") or {}
    return title.get("english") or title.get("romaji") or title.get("native") or ""


def _search_titles(media: dict, season_hint: str = "") -> list[str]:
    title = media.get("title") or {}
    values = [title.get("english"), title.get("romaji"), title.get("native")]
    if season_hint:
        values.extend([f"{v} Season {season_hint}" for v in values if v])
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _target_from_media(media: dict, season_hint: str = "") -> DirectDownloadTarget:
    return DirectDownloadTarget(
        anilist_id=int(media["id"]),
        title=_preferred_title(media),
        search_titles=_search_titles(media, season_hint),
        format=media.get("format") or "",
        status=media.get("status") or "",
        episodes=media.get("episodes"),
        season=media.get("season") or "",
        season_year=media.get("seasonYear"),
    )


def _post(query: str, variables: dict) -> dict:
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            API_URL,
            json={"query": query, "variables": variables},
            headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        return response.json()


def resolve_direct_request(query_text: str, season: str = "") -> DirectDownloadTarget:
    text = (query_text or "").strip()
    season_hint = (season or "").strip()
    if not text:
        raise ValueError("Enter an anime title or AniList URL.")

    media_id = parse_anilist_id(text)
    fields = "id title { romaji english native } format status episodes season seasonYear"
    if media_id is not None:
        data = _post(f"query ($id: Int) {{ Media(id: $id, type: ANIME) {{ {fields} }} }}", {"id": media_id})
        media = (data.get("data") or {}).get("Media")
    else:
        search = f"{text} Season {season_hint}".strip() if season_hint else text
        data = _post(
            f"query ($search: String!) {{ Page(page: 1, perPage: 5) {{ media(search: $search, type: ANIME, isAdult: false) {{ {fields} }} }} }}",
            {"search": search},
        )
        media = ((data.get("data") or {}).get("Page") or {}).get("media", [None])[0]

    if not media:
        raise ValueError(f'No AniList anime found for "{text}".')
    return _target_from_media(media, season_hint)
```

- [ ] **Step 4: Run resolver tests to verify GREEN**

Run: `PYTHONPATH=src C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest tests/plus/test_direct_download.py -q`

Expected: all direct resolver tests pass.

### Task 2: Season-Aware Nyaa Matching

**Files:**
- Modify: `src/omakase/plus/nyaa.py`
- Test: `tests/plus/test_nyaa.py`

- [ ] **Step 1: Add failing season-intent tests**

Add tests proving `Berserk Season 2` can match a season-2 release but not a season-1/full-franchise release:

```python
def test_specific_season_query_accepts_matching_short_title_season(self):
    target = self._make_torrent("[GoodGroup] Berserk Season 2 [1080p][HEVC]", 80)
    wrong = self._make_torrent("[GoodGroup] Berserk (1997) Complete [1080p]", 200)
    best = find_best([wrong, target], expected_title="Berserk Season 2")
    assert best is target


def test_specific_season_query_rejects_wrong_short_title_season(self):
    wrong = self._make_torrent("[GoodGroup] Berserk Season 1 [1080p][HEVC]", 80)
    assert find_best([wrong], expected_title="Berserk Season 2") is None
```

- [ ] **Step 2: Run Nyaa tests to verify RED**

Run: `PYTHONPATH=src C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest tests/plus/test_nyaa.py -q`

Expected: the new season tests fail with current short-title matching.

- [ ] **Step 3: Implement explicit-season matching**

Add a helper that detects explicit season markers in the expected title and candidate title, and skip the short-title exact-release guard only when the candidate has the same explicit season:

```python
_ORDINAL_SEASON_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+season\b", re.I)
_SEASON_RE = re.compile(r"\bseason[\s._-]*(\d{1,2})\b|\bs[\s._-]*(\d{1,2})\b", re.I)


def _explicit_season_number(title: str) -> int | None:
    for pattern in (_SEASON_RE, _ORDINAL_SEASON_RE):
        match = pattern.search(title)
        if match:
            for group in match.groups():
                if group:
                    return int(group)
    return None
```

Then in `_title_match_score`, before the one-token branch:

```python
    expected_season = _explicit_season_number(expected_title)
    if expected_season is not None:
        candidate_season = _explicit_season_number(candidate_title)
        if candidate_season != expected_season:
            return 0.0
```

And change the short-title branch guard to only run when `expected_season is None`.

- [ ] **Step 4: Run Nyaa tests to verify GREEN**

Run: `PYTHONPATH=src C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest tests/plus/test_nyaa.py -q`

Expected: Nyaa tests pass.

### Task 3: Shared Direct Download Route

**Files:**
- Modify: `src/omakase/plus/routes.py`
- Test: `tests/plus/test_dashboard.py`

- [ ] **Step 1: Write failing route tests**

Add tests for:
- dashboard renders a Direct Auto-Download card;
- empty direct request redirects with a helpful error;
- direct request resolves via `resolve_direct_request`, creates/reuses a planning row, best-effort calls AniList Planning, and calls `search_and_download` with `planning_id`;
- route passes aliases from the resolver into the downloader.

- [ ] **Step 2: Run dashboard direct tests to verify RED**

Run: `PYTHONPATH=src C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest tests/plus/test_dashboard.py -q`

Expected: new tests fail because the route and card do not exist.

- [ ] **Step 3: Implement route helper**

Extract the status persistence from `dashboard_download()` into a helper:

```python
async def _run_download_for_planning(db, user, *, anilist_id: int, title: str, search_titles: list[str] | None = None) -> str:
    existing = db.execute("SELECT id FROM anilist_plannings WHERE user_id = ? AND anilist_id = ?", (user.id, anilist_id)).fetchone()
    if not existing:
        cursor = db.execute("INSERT INTO anilist_plannings (user_id, anilist_id, title, status) VALUES (?, ?, ?, ?)", (user.id, anilist_id, title, "PLANNING"))
        planning_id = cursor.lastrowid
        db.commit()
        messages = ["Queued"]
    else:
        planning_id = existing["id"]
        messages = []

    from omakase.plus.automation import search_and_download
    result = await search_and_download(db, user.id, title, planning_id=planning_id, search_titles=search_titles)
    # Apply the same status update branches currently in dashboard_download().
    return " · ".join(messages)
```

Keep the existing status messages exactly compatible with current tests, then have both `dashboard_download()` and the new direct route call the helper.

- [ ] **Step 4: Implement `/plus/dashboard/direct-download`**

Add:

```python
@router.post("/dashboard/direct-download")
async def dashboard_direct_download(
    db=Depends(get_db),
    user=Depends(require_user),
    query: str = Form(""),
    season: str = Form(""),
):
    from omakase.plus.direct import resolve_direct_request
    try:
        target = resolve_direct_request(query, season=season)
    except (ValueError, httpx.HTTPError) as e:
        return RedirectResponse(url=f"/plus/dashboard?error={quote(str(e), safe='')}", status_code=302)
    # best-effort AniList write, matching dashboard_plan()
    ...
    msg = await _run_download_for_planning(db, user, anilist_id=target.anilist_id, title=target.title, search_titles=target.search_titles)
    return RedirectResponse(url=f"/plus/dashboard?error={quote(msg, safe='')}", status_code=302)
```

- [ ] **Step 5: Run route tests to verify GREEN**

Run: `PYTHONPATH=src C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest tests/plus/test_dashboard.py -q`

Expected: dashboard tests pass.

### Task 4: Downloader Alias Support

**Files:**
- Modify: `src/omakase/plus/automation.py`
- Test: `tests/plus/test_automation.py`

- [ ] **Step 1: Write failing alias tests**

Add a test proving `search_and_download(..., title="English Title", search_titles=["Romaji Title", "English Title"])` tries the next alias when Nyaa has no results for the first alias.

- [ ] **Step 2: Implement alias search**

Change the signature:

```python
async def search_and_download(db, user_id: int, title: str, planning_id: int | None = None, search_titles: list[str] | None = None) -> dict:
```

Search each alias in order, deduping blank/duplicate strings. Return `not_found` only after all aliases fail to produce ranked candidates. Preserve existing behavior when `search_titles` is `None`.

- [ ] **Step 3: Run automation tests**

Run: `PYTHONPATH=src C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest tests/plus/test_automation.py -q`

Expected: automation tests pass.

### Task 5: Dashboard Card And Verification

**Files:**
- Modify: `src/omakase/plus/templates/dashboard.html`
- Modify: `tests/plus/test_dashboard.py`
- Create: `Homelab Vault/Agent-Sessions/omakase/03-plus-direct-auto-download.md`

- [ ] **Step 1: Add Direct Auto-Download card**

Place it after the header/error and before Taste Profile:

```html
<div class="card">
  <h2 style="margin-bottom:0.75rem">Add Anime</h2>
  <form method="post" action="/plus/dashboard/direct-download">
    <div class="field">
      <label>Anime or AniList URL</label>
      <input type="text" name="query" placeholder="Golden Kamuy or https://anilist.co/anime/99699/" autocomplete="off">
    </div>
    <div class="field-row">
      <div class="field">
        <label>Season / arc (optional)</label>
        <input type="text" name="season" placeholder="3 or Final Season Part 2">
      </div>
    </div>
    <button type="submit" class="btn btn-primary">Add + Download</button>
  </form>
</div>
```

- [ ] **Step 2: Verify locally**

Run:

```powershell
$env:PYTHONPATH='src'
C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest -q
C:\Users\qazws\Projects\omakase\.venv\Scripts\ruff.exe check .
C:\Users\qazws\Projects\omakase\.venv\Scripts\ruff.exe format --check .
```

- [ ] **Step 3: Browser smoke**

Start private Plus locally with a temp DB and smoke account. Verify in Chromium desktop and 390px mobile:
- Direct card visible without horizontal overflow.
- Empty title shows a clear error.
- Mocked or no-RD-key direct request creates a planning row and shows status.
- Public mode is unchanged.

- [ ] **Step 4: Live deploy gate**

Before deploying private Plus, take a DB backup and verify the deploy target. Do not touch public `omakase.jhinx.dev`. After deploy, verify live private Plus from a fresh browser and public `/plus/login` still `404`.
