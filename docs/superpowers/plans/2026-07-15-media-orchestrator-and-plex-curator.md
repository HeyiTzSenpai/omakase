# Omakase Media Orchestrator and Plex Curator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Omakase anime request complete, quality-aware, automatically current while airing, and reproducibly visible in a four-section Plex library.

**Architecture:** Omakase owns AniList identity, globally ranked Nyaa candidates, episode coverage, Real-Debrid assets, scheduled monitors, and a private manifest. A versioned CT 111 curator consumes that manifest, builds normalized anime overlays, maintains Plex collections/topology, and triggers scoped scans. Riven remains the non-anime TV/movie path.

**Tech Stack:** Python 3.12/3.13, FastAPI, SQLite, httpx, Jinja2/vanilla JS/CSS, systemd timers, Plex HTTP API, pytest, Ruff, Docker Compose.

**Orchestration:** Level 3 - backend/data, private UI, and Plex/runtime deployment are coordinated domains with one shared manifest contract.

**Strategic Planner Checkpoints:** Startup; after the backend/manifest wave; after the Plex/UI integration wave; before deploy/handoff.

---

## Orchestration Map

**Scale:** Level 3 - master orchestrator -> Strategic Planner + domain instructors -> workers/reviewers

| Domain | Instructor Owns | File Ownership | Worker Tasks | Review Gates |
|---|---|---|---|---|
| Backend/data | identity, coverage, RD reconciliation, monitors, manifest | `src/omakase/plus/*.py`, migrations, `tests/plus/test_media_*` | Tasks 1-7 | focused TDD, schema review, API/security review |
| Private UI | bulk add, jobs/monitor controls, responsive status | Plus template/static files and dashboard tests | Task 8 | browser desktop/mobile, accessibility and secret-leak review |
| Plex/runtime | curator, systemd assets, Plex topology, deployment | `deploy/**`, curator tests, deploy docs | Tasks 9-12 | isolated filesystem tests, live CT/Plex proof, rollback review |

The master agent owns product decisions, cross-domain integration, secrets,
commits/pushes, backups, deployment, Plex mutations, real-target proof, and
final state synchronization.

## File Structure

### Omakase backend

- Create `src/omakase/plus/media_types.py`: immutable target, coverage,
  candidate, asset, and sync-result contracts shared across backend modules.
- Modify `src/omakase/plus/direct.py`: rich AniList fields, ambiguity-safe
  search, season number, genres, and next-airing metadata.
- Create `src/omakase/plus/coverage.py`: release-title episode parsing,
  quality eligibility, and deterministic set-cover selection.
- Create `src/omakase/plus/catalog.py`: all-alias Nyaa search, torrent-hash
  deduplication, and global ranking.
- Modify `src/omakase/plus/realdebrid.py`: candidate status polling and safe
  cached/queued classification.
- Create `src/omakase/plus/media_store.py`: SQL access for monitors, assets,
  and sync-run receipts.
- Create `src/omakase/plus/media_sync.py`: idempotent immediate/scheduled
  reconciliation and stalled-candidate replacement.
- Create `src/omakase/plus/media_cli.py`: one-shot scheduler entry point.
- Create `src/omakase/plus/manifest.py`: sanitized manifest construction and
  machine-token verification.
- Modify `src/omakase/plus/routes.py`: bulk/preview/sync routes, dashboard data,
  automatic monitor enrollment, and manifest endpoint.
- Modify `src/omakase/cli.py`: `plus sync-media` command.
- Create `src/omakase/plus/migrations/005-media-orchestrator.sql`.

### Private UI

- Modify `src/omakase/plus/templates/dashboard.html`.
- Modify `src/omakase/plus/static/dashboard.css`.
- Modify `src/omakase/plus/static/dashboard.js`.

### Plex/runtime

- Create `deploy/plex-curator/plex_curator.py`: orchestration and atomic swaps.
- Create `deploy/plex-curator/episode_parser.py`: filesystem episode parsing.
- Create `deploy/plex-curator/plex_client.py`: narrow Plex HTTP client.
- Create `deploy/plex-curator/config.example.json`.
- Create `deploy/systemd/omakase-media-sync.service` and `.timer`.
- Create `deploy/systemd/plex-anime-curator.service` and `.timer`.
- Modify `compose-plus.yaml`, `DEPLOY-PLUS.md`, and project/vault runtime docs.

### Tests

- Create `tests/plus/test_media_schema.py`.
- Create `tests/plus/test_media_identity.py`.
- Create `tests/plus/test_media_coverage.py`.
- Create `tests/plus/test_media_catalog.py`.
- Create `tests/plus/test_media_sync.py`.
- Create `tests/plus/test_media_manifest.py`.
- Create `tests/plex_curator/test_episode_parser.py`.
- Create `tests/plex_curator/test_curator.py`.
- Create `tests/plex_curator/test_plex_client.py`.
- Modify `tests/plus/test_dashboard.py`, `test_direct_download.py`,
  `test_schema.py`, and `test_realdebrid.py`.

## Wave 1 - Data and Identity

### Task 1: Add Media-Orchestrator Persistence

**Files:**
- Create: `src/omakase/plus/migrations/005-media-orchestrator.sql`
- Create: `src/omakase/plus/media_store.py`
- Create: `tests/plus/test_media_schema.py`
- Modify: `tests/plus/test_schema.py`

- [ ] **Step 1: Write the failing schema tests**

```python
def test_media_orchestrator_tables_exist(_fresh_db):
    names = {
        row["name"]
        for row in _fresh_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"media_monitors", "media_assets", "media_sync_runs"} <= names


def test_monitor_is_unique_per_user_and_anilist_id(_fresh_db, user_id):
    values = (user_id, 178789, "Mushoku Tensei Season 3", "RELEASING", 3)
    _fresh_db.execute(
        "INSERT INTO media_monitors "
        "(user_id, anilist_id, title, release_status, season_number) "
        "VALUES (?, ?, ?, ?, ?)",
        values,
    )
    with pytest.raises(sqlite3.IntegrityError):
        _fresh_db.execute(
            "INSERT INTO media_monitors "
            "(user_id, anilist_id, title, release_status, season_number) "
            "VALUES (?, ?, ?, ?, ?)",
            values,
        )
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_media_schema.py tests/plus/test_schema.py -q`

Expected: failure because the three tables do not exist.

- [ ] **Step 3: Add migration 005**

```sql
CREATE TABLE media_monitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    anilist_planning_id INTEGER REFERENCES anilist_plannings(id) ON DELETE SET NULL,
    anilist_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    search_titles_json TEXT NOT NULL DEFAULT '[]',
    genres_json TEXT NOT NULL DEFAULT '[]',
    format TEXT NOT NULL DEFAULT 'TV',
    release_status TEXT NOT NULL DEFAULT '',
    season_number INTEGER,
    expected_episodes INTEGER,
    aired_episodes INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    last_checked_at TEXT,
    last_success_at TEXT,
    next_check_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_result TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, anilist_id)
);

CREATE TABLE media_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id INTEGER NOT NULL REFERENCES media_monitors(id) ON DELETE CASCADE,
    torrent_hash TEXT NOT NULL,
    rd_torrent_id TEXT NOT NULL DEFAULT '',
    source_title TEXT NOT NULL,
    source_folder TEXT NOT NULL DEFAULT '',
    quality TEXT NOT NULL DEFAULT '',
    seeders INTEGER NOT NULL DEFAULT 0,
    coverage_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'candidate',
    progress REAL NOT NULL DEFAULT 0,
    stagnant_checks INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(monitor_id, torrent_hash)
);

CREATE TABLE media_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id INTEGER NOT NULL REFERENCES media_monitors(id) ON DELETE CASCADE,
    outcome TEXT NOT NULL,
    required_count INTEGER NOT NULL DEFAULT 0,
    covered_count INTEGER NOT NULL DEFAULT 0,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: Implement typed store functions**

`media_store.py` must expose `upsert_monitor`, `list_due_monitors`,
`upsert_asset`, `record_sync_run`, and `covered_episodes`. All JSON is encoded
with deterministic separators and all details are capped at 500 characters.

```python
def covered_episodes(db: sqlite3.Connection, monitor_id: int) -> set[int]:
    covered: set[int] = set()
    rows = db.execute(
        "SELECT coverage_json FROM media_assets "
        "WHERE monitor_id = ? AND status IN ('selected', 'downloaded')",
        (monitor_id,),
    )
    for row in rows:
        covered.update(int(value) for value in json.loads(row["coverage_json"]))
    return covered
```

- [ ] **Step 5: Run GREEN tests and commit**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_media_schema.py tests/plus/test_schema.py -q`

Expected: all selected tests pass.

Commit: `feat: add media monitor persistence`

### Task 2: Enrich AniList Identity and Resolve Ambiguity

**Files:**
- Create: `src/omakase/plus/media_types.py`
- Modify: `src/omakase/plus/direct.py`
- Create: `tests/plus/test_media_identity.py`
- Modify: `tests/plus/test_direct_download.py`

- [ ] **Step 1: Write failing identity tests**

```python
def test_target_includes_airing_and_library_metadata(monkeypatch):
    monkeypatch.setattr(direct, "_post", lambda *_: {
        "data": {"Media": {
            "id": 178789,
            "title": {"english": "Mushoku Tensei: Jobless Reincarnation Season 3",
                      "romaji": "Mushoku Tensei III", "native": "無職転生Ⅲ"},
            "format": "TV", "status": "RELEASING", "episodes": 14,
            "season": "SUMMER", "seasonYear": 2026,
            "genres": ["Adventure", "Drama", "Fantasy"],
            "nextAiringEpisode": {"episode": 4, "airingAt": 1783900000},
        }}
    })
    target = direct.resolve_direct_request("178789", "Season 3")
    assert target.season_number == 3
    assert target.aired_episodes == 3
    assert target.genres == ("Adventure", "Drama", "Fantasy")


def test_empty_search_results_raise_value_error_not_index_error(monkeypatch):
    monkeypatch.setattr(direct, "_post", lambda *_: {"data": {"Page": {"media": []}}})
    with pytest.raises(ValueError, match="No AniList anime found"):
        direct.resolve_direct_request("not a real anime")
```

- [ ] **Step 2: Verify RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_media_identity.py -q`

Expected: missing fields and the existing empty-list `IndexError` fail the tests.

- [ ] **Step 3: Add immutable contracts and resolver fields**

```python
@dataclass(frozen=True)
class MediaTarget:
    anilist_id: int
    title: str
    search_titles: tuple[str, ...]
    format: str
    status: str
    episodes: int | None
    aired_episodes: int
    season_number: int | None
    season: str
    season_year: int | None
    genres: tuple[str, ...]
    next_airing_at: int | None
```

The AniList field selection must include `genres` and
`nextAiringEpisode { episode airingAt }`. Add `search_direct_requests()` that
returns up to five `MediaTarget` values; `resolve_direct_request()` returns the
only exact/unambiguous match and raises `AmbiguousMediaError(options)` when the
top results do not have a decisive exact title/season match.

- [ ] **Step 4: Run GREEN tests and commit**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_media_identity.py tests/plus/test_direct_download.py -q`

Expected: all selected tests pass.

Commit: `feat: enrich AniList media identity`

## Wave 2 - Coverage, Ranking, and Reconciliation

### Task 3: Parse Episode Coverage and Select Complete Sets

**Files:**
- Create: `src/omakase/plus/coverage.py`
- Create: `tests/plus/test_media_coverage.py`

- [ ] **Step 1: Write failing parser and selection tests**

```python
@pytest.mark.parametrize(("title", "season", "episodes"), [
    ("Show - S03E04 [1080p]", 3, {4}),
    ("Show S03E01-S03E03 [1080p]", 3, {1, 2, 3}),
    ("Show Season 3 (01-12) [Batch]", 3, set(range(1, 13))),
    ("My Hero Academia - 64 (My Hero Academia S4 - 01)", 4, {1}),
])
def test_parse_episode_coverage(title, season, episodes):
    assert parse_episode_coverage(title, season, expected=14).episodes == episodes


def test_select_minimal_non_overlapping_cover():
    chosen = select_episode_cover(
        required={1, 2, 3},
        candidates=[candidate("batch", {1, 2}, score=100),
                    candidate("ep2", {2}, score=500),
                    candidate("ep3", {3}, score=400)],
    )
    assert [item.torrent_hash for item in chosen] == ["batch", "ep3"]
```

- [ ] **Step 2: Verify RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_media_coverage.py -q`

Expected: import failure because `coverage.py` does not exist.

- [ ] **Step 3: Implement parsing and deterministic greedy cover**

`parse_episode_coverage()` must reject wrong explicit seasons, exclude NCOP,
NCED, PV, sample, and special-only files from numbered coverage, and cap ranges
at the target's expected episode count. `select_episode_cover()` repeatedly
chooses the candidate with the highest `(new_episodes, quality_score,
seeders, -size_bytes, torrent_hash)` tuple until coverage is complete or no
candidate adds coverage.

```python
def select_episode_cover(required: set[int], candidates: Sequence[MediaCandidate]) -> list[MediaCandidate]:
    missing = set(required)
    chosen: list[MediaCandidate] = []
    pool = list(candidates)
    while missing:
        ranked = sorted(
            ((len(item.coverage & missing), item.score, item.seeders,
              -item.size_bytes, item.torrent_hash, item) for item in pool),
            reverse=True,
        )
        if not ranked or ranked[0][0] == 0:
            break
        item = ranked[0][-1]
        chosen.append(item)
        missing -= item.coverage
        pool.remove(item)
    return chosen
```

- [ ] **Step 4: Run GREEN tests and commit**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_media_coverage.py -q`

Commit: `feat: model anime episode coverage`

### Task 4: Search All Aliases and Enforce the Quality Profile

**Files:**
- Create: `src/omakase/plus/catalog.py`
- Modify: `src/omakase/plus/nyaa.py`
- Create: `tests/plus/test_media_catalog.py`
- Modify: `tests/plus/test_nyaa.py`

- [ ] **Step 1: Write failing global-search tests**

```python
@pytest.mark.asyncio
async def test_searches_every_alias_and_deduplicates_hash(monkeypatch):
    calls = []
    async def fake_search(alias, **_):
        calls.append(alias)
        return [torrent(alias, hash="AAA", seeders=10)]
    monkeypatch.setattr(catalog, "search", fake_search)
    found = await catalog.search_all_aliases(["English", "Romaji", "Native"])
    assert calls == ["English", "Romaji", "Native"]
    assert [item.torrent_hash for item in found] == ["AAA"]


def test_quality_profile_rejects_raw_480p_and_wrong_season():
    assert not is_eligible(candidate("Show S03E01 [RAW][480p]"), season=3)
    assert not is_eligible(candidate("Show S02 [1080p][Multi-Subs]"), season=3)
    assert is_eligible(candidate("Show S03E01 [1080p][Multi-Subs]"), season=3)
```

- [ ] **Step 2: Verify RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_media_catalog.py tests/plus/test_nyaa.py -q`

- [ ] **Step 3: Implement hash parsing, global merge, and eligibility**

Search aliases sequentially to avoid hammering Nyaa. Deduplicate by uppercase
BTIH hash and retain the highest-quality representation. Score 1080p above
720p, known groups above unknown groups, English/multi-sub or dual-audio above
unlabeled releases, and seeders using the existing buckets. Expose the rejection
reason for every filtered candidate.

- [ ] **Step 4: Run GREEN tests and commit**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_media_catalog.py tests/plus/test_nyaa.py -q`

Commit: `feat: rank complete cross-alias anime sources`

### Task 5: Reconcile Real-Debrid Assets Safely

**Files:**
- Modify: `src/omakase/plus/realdebrid.py`
- Create: `src/omakase/plus/media_sync.py`
- Create: `tests/plus/test_media_sync.py`
- Modify: `tests/plus/test_realdebrid.py`

- [ ] **Step 1: Write failing reconciliation tests**

```python
@pytest.mark.asyncio
async def test_provider_block_falls_through_to_next_candidate(store, rd):
    rd.add_magnet.side_effect = [RealDebridProviderBlock(
        http_status=451, error_code="infringing_file", detail="blocked"
    ), "rd-good"]
    rd.select_files.return_value = True
    result = await reconcile_candidates(
        store.monitor(), [candidate("blocked", {1}), candidate("good", {1})], rd=rd
    )
    assert result.covered == {1}
    assert result.accepted_assets[0].rd_torrent_id == "rd-good"


@pytest.mark.asyncio
async def test_two_stagnant_checks_replace_asset(store, rd):
    asset = store.asset(status="downloading", progress=0, stagnant_checks=1)
    rd.get_torrent.return_value = RDTorrent(
        id=asset.rd_torrent_id, filename="show", status="downloading",
        progress=0, bytes_total=1, bytes_done=0, links=[]
    )
    await refresh_assets(store.monitor(), rd=rd)
    rd.delete_torrent.assert_awaited_once_with(asset.rd_torrent_id)
    assert store.reload(asset).status == "stalled"
```

- [ ] **Step 2: Verify RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_media_sync.py tests/plus/test_realdebrid.py -q`

- [ ] **Step 3: Implement bounded reconciliation**

`reconcile_monitor()` refreshes accepted asset status, computes missing
episodes, searches/ranks candidates, selects a cover, tries no more than 20
eligible candidates per run, and records a `media_sync_runs` receipt. Zero-seed
candidates are selected only when their first post-selection status is already
`downloaded`; otherwise delete them and continue. Stalled assets are deleted
only after two persisted no-progress checks.

- [ ] **Step 4: Preserve compatibility status**

Update the matching planning row with a concise aggregate such as
`Coverage 3/14; active airing monitor; last sync downloaded E03` and point
`rd_torrent_id` at the latest accepted asset.

- [ ] **Step 5: Run GREEN tests and commit**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_media_sync.py tests/plus/test_realdebrid.py tests/plus/test_automation.py -q`

Commit: `feat: reconcile complete Real-Debrid coverage`

### Task 6: Add Automatic Monitors and the One-Shot CLI

**Files:**
- Create: `src/omakase/plus/media_cli.py`
- Modify: `src/omakase/cli.py`
- Modify: `src/omakase/plus/routes.py`
- Create: `deploy/systemd/omakase-media-sync.service`
- Create: `deploy/systemd/omakase-media-sync.timer`
- Modify: `compose-plus.yaml`
- Modify: `tests/plus/test_media_sync.py`

- [ ] **Step 1: Write failing enrollment and CLI tests**

```python
def test_releasing_direct_request_auto_enrolls(client, resolved_releasing_target):
    with patch("omakase.plus.direct.resolve_direct_request", return_value=resolved_releasing_target), \
         patch("omakase.plus.media_sync.reconcile_monitor", new=AsyncMock()):
        response = client.post("/plus/dashboard/direct-download", data={"query": "Jobless S3"})
    assert response.status_code == 302
    assert db_monitor(client, resolved_releasing_target.anilist_id).active == 1


def test_finished_monitor_closes_only_after_complete_coverage(store):
    monitor = store.monitor(release_status="FINISHED", expected_episodes=12)
    store.assets_covering(monitor, range(1, 13))
    finalize_monitor(store.db, monitor.id)
    assert store.monitor_by_id(monitor.id).active == 0
```

- [ ] **Step 2: Verify RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_media_sync.py tests/plus/test_direct_download.py -q`

- [ ] **Step 3: Implement immediate and due-monitor flows**

The direct route creates/reuses the planning row, upserts a monitor, and runs an
immediate reconciliation. `RELEASING` monitors remain active. `FINISHED`
monitors close only after complete declared coverage. The CLI command exits 0
when every due monitor produced a receipt, even if a receipt is `degraded`; it
exits nonzero only on an unhandled scheduler failure.

```python
@plus.command("sync-media")
def sync_media_command() -> None:
    """Reconcile every due media monitor once."""
    raise SystemExit(asyncio.run(run_due_monitors()))
```

- [ ] **Step 4: Add hardened systemd assets**

Service requirements: `Type=oneshot`, `flock -n /run/omakase-media-sync.lock`,
`docker exec omakase-plus omakase plus sync-media`, `NoNewPrivileges=true`,
and journal output. Timer requirements: `OnBootSec=5m`,
`OnUnitActiveSec=4h`, `RandomizedDelaySec=10m`, `Persistent=true`.

- [ ] **Step 5: Run GREEN tests and commit**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_media_sync.py tests/plus/test_direct_download.py -q`

Commit: `feat: monitor airing anime automatically`

### Task 7: Publish a Sanitized Private Manifest

**Files:**
- Create: `src/omakase/plus/manifest.py`
- Modify: `src/omakase/plus/routes.py`
- Modify: `compose-plus.yaml`
- Create: `tests/plus/test_media_manifest.py`

- [ ] **Step 1: Write failing authorization and redaction tests**

```python
def test_manifest_requires_machine_token(client, monkeypatch):
    monkeypatch.setenv("OMAKASE_LIBRARY_MANIFEST_TOKEN", "machine-secret")
    assert client.get("/plus/api/library-manifest").status_code == 401
    assert client.get(
        "/plus/api/library-manifest",
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 401


def test_manifest_excludes_sensitive_fields(client, manifest_asset, monkeypatch):
    monkeypatch.setenv("OMAKASE_LIBRARY_MANIFEST_TOKEN", "machine-secret")
    response = client.get(
        "/plus/api/library-manifest",
        headers={"Authorization": "Bearer machine-secret"},
    )
    text = response.text.lower()
    assert response.status_code == 200
    assert "magnet:" not in text
    assert "rd_torrent_id" not in text
    assert "api_key" not in text
```

- [ ] **Step 2: Verify RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_media_manifest.py -q`

- [ ] **Step 3: Implement the contract**

Use `secrets.compare_digest()` on an `Authorization: Bearer` token. Return only
schema version, generation timestamp, canonical title/year/format/season,
genres, expected/aired counts, and for selected/downloaded assets: uppercase
torrent hash, source folder/title, quality, and episode coverage. Add
`Cache-Control: private, no-store`.

- [ ] **Step 4: Run GREEN tests, the backend wave gate, and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/plus/test_media_*.py tests/plus/test_direct_download.py tests/plus/test_automation.py tests/plus/test_realdebrid.py -q
.\.venv\Scripts\python.exe -m ruff check src/omakase/plus tests/plus
.\.venv\Scripts\python.exe -m ruff format --check src/omakase/plus tests/plus
```

Commit: `feat: expose private library manifest`

Strategic Planner checkpoint: compare Wave 1-2 behavior to the approved design,
classify any gap as required now, and do not expand into public Omakase.

## Wave 3 - Private UI

### Task 8: Rework the Plus Dashboard into Library Operations

**Files:**
- Modify: `src/omakase/plus/templates/dashboard.html`
- Modify: `src/omakase/plus/static/dashboard.css`
- Modify: `src/omakase/plus/static/dashboard.js`
- Modify: `src/omakase/plus/routes.py`
- Modify: `tests/plus/test_dashboard.py`

- [ ] **Step 1: Write failing dashboard contract tests**

```python
def test_dashboard_has_bulk_add_and_monitor_status(client, signed_in_user):
    html = client.get("/plus/dashboard").text
    assert 'name="bulk_query"' in html
    assert 'action="/plus/dashboard/bulk-preview"' in html
    assert 'id="library-jobs"' in html
    assert "Airing monitors" in html


def test_bulk_preview_requires_disambiguation_only_for_ambiguous_rows(client):
    response = client.post(
        "/plus/dashboard/bulk-preview",
        data={"bulk_query": "178789\nAmbiguous Hero"},
    )
    assert "Ready" in response.text
    assert "Choose the intended title" in response.text
```

- [ ] **Step 2: Verify RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/plus/test_dashboard.py -q`

- [ ] **Step 3: Implement routes and template states**

Bulk input accepts one nonblank line per request, maximum 25 lines. Preview
stores no secrets and renders canonical title, season, status, and ambiguity
choices. Confirm submits exact AniList IDs. Library job cards show `covered /
required`, quality, source, RD/Plex state, next check, and bounded last result.
`Sync now` posts only the monitor ID owned by the signed-in user.

- [ ] **Step 4: Implement responsive styling and safe JS**

At 390px: one column, 44px minimum touch targets, no horizontal overflow, and
status text independent of color. Desktop may use two columns. Respect
`prefers-reduced-motion`. JS manages preview selection and an accessible busy
state; it never renders provider HTML or secrets through `innerHTML`.

- [ ] **Step 5: Run tests and local real-browser proof**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/plus/test_dashboard.py tests/plus/test_direct_download.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

Start a private local Plus server with a disposable database and no production
credentials. Verify Chromium at 1280px and 390px: bulk preview, ambiguity,
monitor cards, Sync Now, keyboard focus, reduced motion, no console/page errors,
and no overflow. Save screenshots under `output/playwright/media-orchestrator/`.

- [ ] **Step 6: Commit**

Commit: `feat: rework Plus library operations UI`

## Wave 4 - Plex Curator

### Task 9: Normalize Anime Files and Build Atomic Overlays

**Files:**
- Create: `deploy/plex-curator/episode_parser.py`
- Create: `deploy/plex-curator/plex_curator.py`
- Create: `deploy/plex-curator/config.example.json`
- Create: `tests/plex_curator/test_episode_parser.py`
- Create: `tests/plex_curator/test_curator.py`

- [ ] **Step 1: Write failing filesystem tests**

```python
def test_absolute_my_hero_number_maps_to_local_season_episode():
    name = "Boku no Hero - 64 (My Hero Academia S4 - 01).mkv"
    assert parse_file_episode(name, season=4, expected=25) == EpisodeRef(4, 1, False)


def test_curator_excludes_riven_referenced_top_level_folder(tmp_path):
    layout = fixture_layout(tmp_path, anime_folder="Invincible.LF_[rutor]")
    layout.riven_show_link.symlink_to(
        "/zurg/__all__/Invincible.LF_[rutor]/Invincible.S01E01.avi"
    )
    result = build_overlays(layout.config, manifest=[])
    assert "Invincible.LF_[rutor]" in result.excluded_riven_folders
    assert not (layout.anime_overlay / "Invincible.LF_[rutor]").exists()


def test_atomic_build_preserves_previous_overlay_on_validation_failure(tmp_path):
    layout = fixture_layout(tmp_path, broken_target=True)
    (layout.anime_overlay / "old.txt").write_text("old")
    with pytest.raises(ValidationError):
        build_overlays(layout.config, manifest=layout.manifest)
    assert (layout.anime_overlay / "old.txt").read_text() == "old"
```

- [ ] **Step 2: Verify RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/plex_curator/test_episode_parser.py tests/plex_curator/test_curator.py -q`

- [ ] **Step 3: Implement parser and curator**

The parser recognizes SxxEyy, SxxEyy-Ezz, explicit local episode hints such as
`S4 - 01`, simple ranges, and specials. The curator:

1. fetches and validates manifest schema/version;
2. derives excluded zurg folders from Riven symlink targets;
3. matches manifest assets by torrent hash/source-folder title;
4. creates normalized links in temporary Anime and Anime Movies overlays;
5. mirrors untracked historical anime folders unless explicitly excluded;
6. validates required link targets and manifest-backed coverage;
7. atomically swaps changed overlays and retains the previous tree until the
   swap succeeds.

- [ ] **Step 4: Run GREEN tests and commit**

Run: `\.venv\Scripts\python.exe -m pytest tests/plex_curator/test_episode_parser.py tests/plex_curator/test_curator.py -q`

Commit: `feat: add atomic Plex anime curator`

### Task 10: Manage Plex Collections and Four-Library Topology

**Files:**
- Create: `deploy/plex-curator/plex_client.py`
- Modify: `deploy/plex-curator/plex_curator.py`
- Create: `tests/plex_curator/test_plex_client.py`

- [ ] **Step 1: Write failing Plex-client tests**

```python
def test_bucket_rule_is_exactly_one_collection():
    assert anime_bucket(["Action", "Comedy"]) == "Action & Adventure Anime"
    assert anime_bucket(["Comedy", "Slice of Life"]) == "Comedy & Couch Anime"
    assert anime_bucket(["Drama"]) == "Comedy & Couch Anime"


def test_collection_update_preserves_unmanaged_tags(fake_plex):
    client = PlexClient(fake_plex.base_url, token="secret")
    client.set_managed_collection(
        rating_key="42",
        existing=["Favorites", "Comedy & Couch Anime"],
        desired="Action & Adventure Anime",
    )
    assert fake_plex.last_collection_tags == ["Favorites", "Action & Adventure Anime"]


def test_topology_plan_does_not_delete_media():
    plan = plan_library_topology(current_sections_fixture())
    assert plan.rename == {"Riven TV": "TV Shows"}
    assert plan.remove_sections == ["Real-Debrid"]
    assert all("delete_media" not in action for action in plan.actions)
```

- [ ] **Step 2: Verify RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/plex_curator/test_plex_client.py -q`

- [ ] **Step 3: Implement narrow Plex API operations**

Read the token from Plex `Preferences.xml`; never log it. Implement section
inventory, path verification, scoped scan, metadata/genre inventory, managed
collection-tag update, section rename, section creation, and section removal.
Every mutating method supports `dry_run=True`. Topology mutation refuses to run
unless a backup receipt path is supplied and all four intended source paths are
readable inside the Plex container.

- [ ] **Step 4: Implement collection convergence**

Action or Adventure metadata selects `Action & Adventure Anime`; otherwise
select `Comedy & Couch Anime`. Apply versioned overrides after the base rule.
Remove only the other managed bucket tag and preserve every unrelated tag.

- [ ] **Step 5: Run GREEN tests and commit**

Run: `\.venv\Scripts\python.exe -m pytest tests/plex_curator -q`

Commit: `feat: curate Plex libraries and anime collections`

### Task 11: Add Curator Scheduling, Deployment, and Rollback Assets

**Files:**
- Create: `deploy/systemd/plex-anime-curator.service`
- Create: `deploy/systemd/plex-anime-curator.timer`
- Modify: `DEPLOY-PLUS.md`
- Create: `deploy/plex-curator/README.md`
- Modify: `compose-plus.yaml`

- [ ] **Step 1: Add systemd asset assertions**

Append these exact assertions to `tests/plex_curator/test_curator.py`:

```python
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("unit_name", "required_lines"),
    [
        (
            "plex-anime-curator.service",
            (
                "Type=oneshot",
                "NoNewPrivileges=true",
                "EnvironmentFile=/etc/omakase/plex-curator.env",
            ),
        ),
        (
            "plex-anime-curator.timer",
            (
                "Persistent=true",
                "OnBootSec=5m",
                "OnUnitActiveSec=15m",
            ),
        ),
    ],
)
def test_plex_curator_systemd_units_are_hardened(unit_name, required_lines):
    unit = REPO_ROOT / "deploy" / "systemd" / unit_name
    text = unit.read_text(encoding="utf-8")

    for required_line in required_lines:
        assert required_line in text

    assert "PLEX_TOKEN=" not in text
    assert "OMAKASE_LIBRARY_MANIFEST_TOKEN=" not in text
```

- [ ] **Step 2: Verify RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/plex_curator/test_curator.py -q`

- [ ] **Step 3: Add deploy and rollback instructions**

Document exact CT 101/CT 111 paths, 0600 machine-token env files, SQLite/Plex
backup commands, unit install/daemon-reload/enable commands, dry-run invocation,
atomic overlay rollback, previous Plus image/source rollback, and proof queries.
Public `omakase.jhinx.dev/plus*` must remain 404.

- [ ] **Step 4: Run the consolidated local gate**

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Expected: all tests pass, Ruff check/format exit 0, and no warnings newly
introduced by this release.

- [ ] **Step 5: Run integrated spec and quality/security review**

The spec review checks every accepted-design requirement against a task and
test. The quality/security review checks SQL constraints, path containment,
symlink target safety, token redaction, bounded provider details, route auth,
timer concurrency, rollback completeness, and destructive Plex guards. Resolve
all required findings before deploy.

- [ ] **Step 6: Commit**

Commit: `docs: add media orchestrator deployment runbook`

Strategic Planner checkpoint: classify every review finding and any newly found
optimization as Required now, Next-session carry, or Backlog. Required work is
fixed before deploy.

## Wave 5 - Exact Deployment and Requested Library Reconciliation

### Task 12: Deploy, Reconcile Every Requested Title, and Prove Plex

**Files:**
- Modify after live proof only: vault Omakase project state/history and affected
  Plex/Riven/Omakase service docs.

- [ ] **Step 1: Classify worktrees and create exact release commit**

Run full git status/diff review. Stage only intentional implementation, tests,
assets, and docs. Commit coherent remaining changes and push the exact intended
head to private `origin/plus-mvp`. Record previous remote/deployed head as the
source rollback.

- [ ] **Step 2: Capture production backups**

On CT 101, copy the live Omakase SQLite database to the timestamped backups
directory. On CT 111, stop no services yet; copy Plex database/config metadata,
the current overlay script/config, current overlay trees, relevant systemd
units, and section inventory. Record paths without printing secrets.

- [ ] **Step 3: Deploy exact Omakase head to CT 101**

Use the checked-in `DEPLOY-PLUS.md` recipe, preserving the private environment
and new manifest token. Rebuild/recreate only `omakase-plus`. Prove installed
module paths, migration 005, health, exact source commit/version receipt, login,
and manifest auth/redaction. Confirm public `/plus/login` remains 404.

- [ ] **Step 4: Deploy curator to CT 111 in dry-run mode**

Install versioned files, config, and 0600 token environment file. Run curator
dry-run and inspect exclusions, intended overlay changes, expected coverage,
and topology plan. Refuse live mode until Invincible is excluded and every
manifest-backed link target resolves in the Plex container.

- [ ] **Step 5: Run live curator and converge Plex topology**

Run atomic overlay build, scoped scans, collection convergence, rename `Riven
TV` to `TV Shows`, create `Movies`, point `Anime Movies` to its curated overlay,
and remove the `Real-Debrid` section only after all replacement paths scan.
Enable/start both timers and manually invoke each service once.

- [ ] **Step 6: Reconcile the requested anime**

Submit exact AniList IDs for My Hero S4-S8, Jobless S3, Daily Lives, My
Ribdiculous Reincarnation, Heavy Object, and KAMUI. Run immediate reconciliation
until all currently aired episodes are covered or the persisted evidence proves
no eligible source. Prefer a new 1080p My Hero S4 asset; retain the accepted
720p dual-audio source only after 1080p exhaustion. Replace the stalled Daily
Lives asset according to policy.

- [ ] **Step 7: Verify current coverage and playback**

From fresh Plex API queries, record library, seasons, and episode counts for
every requested title. For each title with available media, request at least
1 KiB from a representative Plex media-part URL with a byte range and require
HTTP 206 plus nonzero bytes. For airing titles, compare Plex coverage to the
fresh AniList aired count and prove the monitor remains active.

- [ ] **Step 8: Verify real UI on desktop and mobile**

Use a fresh authenticated Chromium session against `anime.jhinx.dev` and Plex
Web. Verify bulk add, monitor status, safe errors, four Plex sections, both Anime
collections, requested titles, 1280px/390px no overflow, and zero unexpected
console/page errors. Capture screenshots outside the repository or under the
documented output path.

- [ ] **Step 9: Prove reboot/schedule resilience**

Verify both systemd timers are enabled and list their next run. Run a controlled
service invocation after the container is healthy and prove the lock prevents a
concurrent run. Do not reboot CTs solely for this feature; prove `Persistent`
and enabled boot wiring from systemd unless a routine reboot is otherwise safe.

- [ ] **Step 10: Synchronize state and finish**

Only after live proof, update canonical project/runtime state and affected
projections, record backups/rollback/deployed head/test counts/browser evidence,
commit/push private history, verify local `HEAD == origin/plus-mvp == deployed
head == canonical repo head`, classify every in-scope dirty file, notify Discord
with `finished` only if the convergence and media acceptance bars are met, and
provide the remaining unavailable-title evidence if any.

## Final Requirement Checklist

- [ ] Every Omakase `RELEASING` direct request auto-enrolls.
- [ ] Finished episode-only anime reconcile until complete.
- [ ] All aliases are searched and globally ranked.
- [ ] RD 451 and stalled candidates fall through safely.
- [ ] Manifest is private and contains no secret-bearing fields.
- [ ] My Hero S4 absolute numbering normalizes to S04E01-S04E25.
- [ ] Invincible remains only in TV Shows and has S1-S4 coverage.
- [ ] Plex shows exactly Anime, Anime Movies, TV Shows, and Movies.
- [ ] Every Anime show has exactly one managed bucket collection.
- [ ] Requested-title Plex counts and playback probes meet the live bar.
- [ ] Omakase/Plex backups and rollback commands are recorded.
- [ ] Exact source/live/canonical convergence is proved before `finished`.
