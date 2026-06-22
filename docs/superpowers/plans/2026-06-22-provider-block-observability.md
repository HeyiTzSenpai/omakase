# Provider Block Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add durable Real-Debrid candidate-attempt telemetry and a manual Retry control to the private Plus planning queue.

**Architecture:** A small `download_attempts` table records the candidates actually tried during each download invocation. `search_and_download` writes attempts while preserving the existing return contract. The dashboard loads recent attempts for visible planning rows and renders them in an expandable details block.

**Tech Stack:** Python 3.12, FastAPI, SQLite migrations, Jinja2 templates, pytest, Ruff.

**Orchestration:** Level 2 - one backend/schema task and one route/template task are enough; reviews run after implementation.

**Strategic Planner Checkpoints:** Before handoff only.

---

## File Map

- `src/omakase/plus/migrations/004-download-attempts.sql`: create `download_attempts` and indexes.
- `src/omakase/plus/automation.py`: record candidate attempts with safe metadata.
- `src/omakase/plus/routes.py`: pass `planning_id` to automation and load attempts for dashboard rows.
- `src/omakase/plus/templates/dashboard.html`: render attempts and retry button.
- `tests/plus/test_schema.py`: schema and cascade coverage.
- `tests/plus/test_automation.py`: attempt-writing behavior.
- `tests/plus/test_dashboard.py`: route/template behavior.

### Task 1: Schema

**Files:**
- Create: `src/omakase/plus/migrations/004-download-attempts.sql`
- Modify: `tests/plus/test_schema.py`

- [ ] **Step 1: Write failing schema tests**

Add tests asserting `download_attempts` exists and rows cascade when a planning row is deleted:

```python
def test_download_attempts_table_exists(_fresh_db):
    tables = {row["name"] for row in _fresh_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "download_attempts" in tables

def test_download_attempts_cascade_with_planning(_fresh_db):
    ...
```

- [ ] **Step 2: Run red test**

Run:

```powershell
$env:PYTHONPATH='src'; C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest tests/plus/test_schema.py -q
```

Expected: fails because `download_attempts` does not exist.

- [ ] **Step 3: Add migration**

Create a table with `user_id`, `anilist_planning_id`, `request_id`, `candidate_rank`, `torrent_title`, `torrent_hash`, `seeders`, `size_display`, `is_batch`, `status`, `http_status`, `error_code`, `detail`, `rd_torrent_id`, and `created_at`.

- [ ] **Step 4: Verify green**

Run the same schema command and confirm it passes.

### Task 2: Attempt Recording

**Files:**
- Modify: `src/omakase/plus/automation.py`
- Modify: `tests/plus/test_automation.py`

- [ ] **Step 1: Write failing automation tests**

Add tests for:

```python
result = asyncio.run(search_and_download(db, user_id, "Bleach", planning_id=planning_id))
attempts = db.execute("SELECT status, candidate_rank, torrent_hash FROM download_attempts").fetchall()
```

Required assertions: provider blocks record `provider_block`, fallback success records both `provider_block` and `selected`, generic add failures record `rd_add_failed`, and `db=None` remains supported without recording.

- [ ] **Step 2: Run red focused tests**

Run:

```powershell
$env:PYTHONPATH='src'; C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest tests/plus/test_automation.py -q
```

Expected: fails because `planning_id` is unsupported and rows are not recorded.

- [ ] **Step 3: Implement recording**

Add optional `planning_id: int | None = None`; generate `request_id = uuid.uuid4().hex`; add helpers `_torrent_hash` and `_record_download_attempt`. Call the helper after provider block, RD add rejection, select failure, and success.

- [ ] **Step 4: Verify green**

Run the focused automation tests and confirm they pass.

### Task 3: Dashboard Retry And Attempts UI

**Files:**
- Modify: `src/omakase/plus/routes.py`
- Modify: `src/omakase/plus/templates/dashboard.html`
- Modify: `tests/plus/test_dashboard.py`

- [ ] **Step 1: Write failing dashboard tests**

Add tests that prove:

```python
download_mock.assert_awaited_once()
assert download_mock.await_args.kwargs["planning_id"] == planning_id
assert "RD attempts" in html
assert "Retry" in html
```

Also assert requested/downloading rows do not show Retry for that row.

- [ ] **Step 2: Run red focused tests**

Run:

```powershell
$env:PYTHONPATH='src'; C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest tests/plus/test_dashboard.py -q
```

Expected: fails because attempts are not loaded, retry is absent, and route does not pass `planning_id`.

- [ ] **Step 3: Implement route/template changes**

Keep `/plus/dashboard/download`. Capture the planning row id whether existing or newly inserted, pass it to `search_and_download`, load recent attempts for the dashboard, attach `download_attempts` to each planning dict, and render a compact details block plus Retry button.

- [ ] **Step 4: Verify green**

Run the focused dashboard tests and confirm they pass.

### Task 4: Verification, Deploy, Docs

**Files:**
- Modify: `AGENTS.md`
- Modify vault docs/memory as needed.

- [ ] **Step 1: Run full verification**

Run:

```powershell
$env:PYTHONPATH='src'; C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest -q
ruff check .
ruff format --check .
git diff --check
```

- [ ] **Step 2: Browser QA locally**

Start private Plus on a temp DB, seed a provider-block row with attempts, capture desktop/mobile screenshots, and verify no horizontal overflow.

- [ ] **Step 3: Push and deploy private Plus**

Push branch to `origin/plus-mvp` only after verification. Deploy CT 101 Plus with `ssh omakase-ct`, back up DB, rebuild `omakase-plus`, and verify container health.

- [ ] **Step 4: Live browser QA and cleanup**

Seed a temporary QA user/row on live Plus, verify attempts + Retry in Chromium desktop/mobile, then delete the QA user and confirm cleanup.

- [ ] **Step 5: Close docs/session**

Update Brief 02 outcome, Omakase README/history, memory, and Discord notification. Keep public site untouched.
