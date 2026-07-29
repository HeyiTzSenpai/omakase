# Lite Episode Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a distinct Watching action that stores episode progress locally and synchronizes compatible AniList recommendations as CURRENT with progress.

**Architecture:** Keep the production table migration additive by representing both logical watch states with the existing checked `feedback_state = 'watched'`, then distinguish them with a new `watch_status` column. Route and browser APIs expose `watching` as a first-class logical state, while a generalized AniList sync boundary sends either CURRENT/progress or COMPLETED/score and validates the exact returned receipt.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite migrations, Jinja2, browser JavaScript, Node test runner, httpx MockTransport, pytest, Docker Compose.

## Global Constraints

- Migration 007 must be additive; do not rebuild `account_recommendations` or change the 24 known production foreign-key orphan rows.
- Current progress requires a positive strict integer and no local score.
- Completed feedback requires a strict score from 1 through 10 and no local episode progress.
- Existing watched rows must migrate to `watch_status = 'completed'`.
- AniList writes require the existing encrypted token, authenticated Viewer binding, source-username match, verified AniList media ID, and exact returned receipt.
- A Watching AniList write sends `status: CURRENT` and `progress` without a score.
- Local feedback must survive a missing connection, account mismatch, unavailable media ID, network error, or AniList validation error.
- Both watch states stay out of later recommendation menus.
- Do not mutate a real user's AniList progress merely to prove deployment.
- On this Windows host, run Python tests with `$env:PYTHONPATH=(Join-Path (Get-Location) 'src')` so the old rollback checkout cannot shadow the active worktree.

---

### Task 1: Additive progress storage and local feedback behavior

**Files:**
- Create: `src/omakase/lite/migrations/007-episode-progress.sql`
- Modify: `src/omakase/lite/db.py`
- Test: `tests/test_lite_accounts.py`

**Interfaces:**
- Consumes: existing `account_recommendations`, `set_recommendation_feedback`, `recommendation_history`, `feedback_context`, and tracker receipt columns.
- Produces: `set_recommendation_feedback(..., state: str, watched_score: int | None = None, watched_episodes: int | None = None)`, `watching_list(conn, user_id)`, and `pending_anilist_entries(conn, *, user_id, limit=100)`.

- [ ] **Step 1: Write migration and invariant tests that fail against migration 006**

Extend the existing legacy watched-row migration fixture and assert literal
post-migration values:

```python
columns = {
    row["name"]
    for row in migrated.execute("PRAGMA table_info(account_recommendations)")
}
assert {"watch_status", "watched_episodes"} <= columns
row = migrated.execute(
    """
    SELECT feedback_state, watch_status, watched_score, watched_episodes
      FROM account_recommendations
     WHERE id = 1
    """
).fetchone()
assert dict(row) == {
    "feedback_state": "watched",
    "watch_status": "completed",
    "watched_score": 8,
    "watched_episodes": None,
}
```

Add a test using the existing account/recommendation fixtures:

```python
db.set_recommendation_feedback(
    conn,
    user_id=user_id,
    recommendation_id=recommendation_id,
    state="watching",
    watched_episodes=3,
)
item = db.recommendation_history(conn, user_id)[0]["recommendations"][0]
assert item["feedback_state"] == "watched"
assert item["watch_status"] == "current"
assert item["watched_episodes"] == 3
assert item["watched_score"] is None
assert db.watching_list(conn, user_id)[0]["title"] == "Pluto"
assert "Currently watching: Pluto (3 episodes)." in db.feedback_context(conn, user_id)
```

The same test must assert that `True`, `0`, `-1`, and `1.5` raise
`ValueError("Watching needs a positive whole number of episodes.")`, that a
completed write clears episodes, and that saved/not-interested/neutral clear
status, episodes, score, and tracker fields.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
python -m pytest tests/test_lite_accounts.py::test_anilist_sync_migration_preserves_existing_watched_feedback tests/test_lite_accounts.py::test_watching_feedback_requires_positive_episodes_and_clears_incompatible_fields -q
```

Expected: FAIL because migration 007, `watched_episodes`, `watch_status`,
`watching_list`, and the `watching` state do not exist.

- [ ] **Step 3: Add migration 007**

Create:

```sql
ALTER TABLE account_recommendations
    ADD COLUMN watch_status TEXT
        CHECK (watch_status IS NULL OR watch_status IN ('current', 'completed'));

ALTER TABLE account_recommendations
    ADD COLUMN watched_episodes INTEGER
        CHECK (watched_episodes IS NULL OR watched_episodes >= 1);

UPDATE account_recommendations
   SET watch_status = 'completed'
 WHERE feedback_state = 'watched';
```

- [ ] **Step 4: Implement local state normalization**

Extend `_FEEDBACK_STATES` with logical `watching`, but map it to stored
`feedback_state = 'watched'` inside `set_recommendation_feedback`.

For `watching`, validate `watched_episodes` as a non-boolean positive integer,
set `watch_status = 'current'`, and clear score. For `watched`, retain existing
score validation, set `watch_status = 'completed'`, and clear episodes. For
other states, clear all watch fields.

Update the SQL write to reset tracker fields:

```sql
UPDATE account_recommendations
   SET feedback_state = ?,
       watch_status = ?,
       watched_score = ?,
       watched_episodes = ?,
       feedback_at = CURRENT_TIMESTAMP,
       tracker_sync_state = 'local_only',
       tracker_sync_detail = NULL,
       tracker_remote_entry_id = NULL,
       tracker_synced_at = NULL
 WHERE id = ? AND user_id = ?
```

Add `watch_status` and `watched_episodes` to saved recommendation return
dictionaries, history queries, and list queries. `watching_list` filters
`feedback_state = 'watched' AND watch_status = 'current'`; `watched_list`
filters completed rows and accepts null `watch_status` for defensive legacy
compatibility.

Rename `pending_anilist_watched` to `pending_anilist_entries` and return
`watch_status`, score, episodes, URL, and source username for both valid
current and completed rows.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: both tests PASS.

- [ ] **Step 6: Commit the storage cycle**

```powershell
git add -- src/omakase/lite/migrations/007-episode-progress.sql src/omakase/lite/db.py tests/test_lite_accounts.py
git commit -m "feat(lite): store episode progress"
```

---

### Task 2: AniList CURRENT/progress mutation boundary

**Files:**
- Modify: `src/omakase/lite/anilist.py`
- Test: `tests/test_lite_anilist.py`

**Interfaces:**
- Consumes: `API_URL`, `USER_AGENT`, `AniListWriteError`, and the established httpx request pattern.
- Produces: `save_current_entry(access_token: str, media_id: int, *, progress: int) -> dict[str, object]`.

- [ ] **Step 1: Write a failing CURRENT/progress contract test**

Add a MockTransport test whose server returns:

```python
{
    "data": {
        "SaveMediaListEntry": {
            "id": 78,
            "mediaId": 99088,
            "status": "CURRENT",
            "progress": 3,
        }
    }
}
```

Call:

```python
result = anilist.save_current_entry(
    "one-year-access-token",
    99088,
    progress=3,
)
```

Assert the literal variables:

```python
assert observed["payload"]["variables"] == {
    "mediaId": 99088,
    "status": "CURRENT",
    "progress": 3,
}
assert result == {
    "id": 78,
    "mediaId": 99088,
    "status": "CURRENT",
    "progress": 3,
}
```

Add parameterized validation for `True`, `0`, `-1`, and `1.5`, plus malformed
receipts with the wrong media ID, status, or progress.

- [ ] **Step 2: Run the AniList test and verify RED**

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
python -m pytest tests/test_lite_anilist.py -q
```

Expected: FAIL because `save_current_entry` is absent.

- [ ] **Step 3: Implement the minimal mutation**

Add `save_current_entry` with strict positive-integer validation. Send:

```graphql
mutation ($mediaId: Int, $status: MediaListStatus, $progress: Int) {
  SaveMediaListEntry(mediaId: $mediaId, status: $status, progress: $progress) {
    id
    mediaId
    status
    progress
  }
}
```

Reject GraphQL errors, non-dict data, non-positive entry IDs, or any returned
media ID/status/progress that differs from the request by raising
`AniListWriteError("AniList returned an invalid progress receipt.")`.

- [ ] **Step 4: Run the AniList tests and verify GREEN**

Run the command from Step 2. Expected: all `test_lite_anilist.py` tests PASS.

- [ ] **Step 5: Commit the AniList cycle**

```powershell
git add -- src/omakase/lite/anilist.py tests/test_lite_anilist.py
git commit -m "feat(lite): sync current AniList progress"
```

---

### Task 3: Route validation, synchronization, and connection replay

**Files:**
- Modify: `src/omakase/lite/routes.py`
- Test: `tests/test_lite_routes.py`

**Interfaces:**
- Consumes: Task 1's normalized watch fields and pending-entry query; Task 2's `save_current_entry`.
- Produces: feedback payload `{"state": "watching", "watched_episodes": int}`, generalized `_sync_watch_state_to_anilist(...)`, and replay of both watch states during OAuth callback.

- [ ] **Step 1: Write failing route tests**

Add a local-only test that posts:

```python
response = client.post(
    endpoint,
    headers={"X-CSRF-Token": session["csrf_token"]},
    json={"state": "watching", "watched_episodes": 3},
)
assert response.status_code == 200
assert response.json() == {
    "ok": True,
    "state": "watching",
    "watched_score": None,
    "watched_episodes": 3,
    "tracker_sync": {
        "state": "connection_required",
        "detail": (
            "Connect AniList to add this title and episode progress "
            "to your anime list."
        ),
        "connect_url": "/account/integrations/anilist/connect",
    },
}
```

Assert missing/zero/fraction/boolean progress is rejected, `watched_score` is
rejected for watching, and `watched_episodes` is rejected for completed or
other states.

Add a matching-account test that replaces `anilist.save_current_entry` with a
specific fake returning CURRENT/progress receipt and asserts arguments
`("access-token", 99088, progress=3)`. Assert the response detail is:

```text
Updated OwnerOnAniList’s AniList as Current · episode 3.
```

Extend the mismatch test to prove the fake mutation is never called. Extend the
OAuth callback replay test with one current-progress row and one completed row;
assert both synchronize and the redirect reports `synced=2`.

- [ ] **Step 2: Run route tests and verify RED**

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
python -m pytest tests/test_lite_routes.py -q
```

Expected: FAIL because `FeedbackInput` rejects `watching` and no progress sync
path exists.

- [ ] **Step 3: Extend the request model and cross-field validation**

Change `FeedbackInput` to:

```python
class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["neutral", "not_interested", "saved", "watching", "watched"]
    watched_score: int | None = Field(default=None, ge=1, le=10, strict=True)
    watched_episodes: int | None = Field(default=None, ge=1, strict=True)
```

Before the database write, reject score outside `watched` and episodes outside
`watching` with clear 400 responses. Pass both fields to the database.

- [ ] **Step 4: Generalize synchronization**

Replace `_sync_watched_to_anilist` with `_sync_watch_state_to_anilist` accepting
`watch_status`, optional score, and optional episodes. Preserve every identity,
media-ID, token, and receipt-state branch. Dispatch:

```python
if watch_status == "current":
    remote = anilist.save_current_entry(
        access_token,
        media_id,
        progress=int(watched_episodes),
    )
    detail = (
        f"Updated {connected_username}’s AniList as Current · "
        f"episode {watched_episodes}."
    )
else:
    remote = anilist.save_completed_entry(
        access_token,
        media_id,
        score_ten=int(watched_score),
    )
```

Use state-specific failure copy: “Try Watching again.” or “Try Already watched
again.” The route maps logical `watching` to `watch_status = 'current'` and
`watched` to completed, then returns both nullable data fields.

Update the OAuth callback to iterate `pending_anilist_entries` and call the
generalized sync helper for each row.

- [ ] **Step 5: Add dashboard data**

Pass `db.watching_list(conn, user.id)` as `watching` in the account dashboard
context. No template change is made in this task.

- [ ] **Step 6: Run route tests and verify GREEN**

Run the command from Step 2. Expected: all route tests PASS.

- [ ] **Step 7: Commit the route cycle**

```powershell
git add -- src/omakase/lite/routes.py tests/test_lite_routes.py
git commit -m "feat(lite): accept and replay episode progress"
```

---

### Task 4: Browser payloads, dialogs, confirmations, and dashboard

**Files:**
- Modify: `src/omakase/web/static/account_state.js`
- Modify: `src/omakase/web/static/app.js`
- Modify: `src/omakase/web/static/style.css`
- Modify: `src/omakase/web/templates/index.html`
- Modify: `src/omakase/lite/templates/dashboard.html`
- Test: `tests/js/account_state.test.cjs`
- Test: `tests/test_public_mode.py`
- Test: `tests/test_lite_routes.py`

**Interfaces:**
- Consumes: Task 3's `watching` API payload/response and dashboard `watching` list.
- Produces: strict `feedbackPayload(state, watchedScore, watchedEpisodes)`, progress confirmation text, a Watching dialog, and separate dashboard rendering.

- [ ] **Step 1: Write failing JavaScript tests**

Add:

```javascript
assert.deepEqual(state.feedbackPayload("watching", null, "3"), {
  state: "watching",
  watched_episodes: 3,
});
assert.throws(
  () => state.feedbackPayload("watching", null, "0"),
  /positive whole number of episodes/,
);
assert.equal(
  state.feedbackConfirmation("watching", null, 3),
  "Saved in Omakase at episode 3. Future menus will use it.",
);
assert.equal(
  state.feedbackConfirmation("watching", null, 3, {
    state: "synced",
    detail: "Updated Friend’s AniList as Current · episode 3.",
  }),
  "Updated Friend’s AniList as Current · episode 3.",
);
```

Update existing completed confirmation calls to supply the explicit episodes
slot as null.

- [ ] **Step 2: Write failing rendered-surface tests**

Extend public-mode assertions for:

```python
assert 'id="watching-dialog"' in response.text
assert 'name="watched-episodes"' in response.text
assert 'min="1"' in response.text
```

Extend dashboard route assertions to require
`<h2 id="watching-title">Currently watching</h2>`, `Episode 3`, and the tracker
receipt for a current row, while the completed row remains only in Watched &
rated.

- [ ] **Step 3: Run JavaScript and rendered tests and verify RED**

```powershell
node --test tests/js/*.test.cjs
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
python -m pytest tests/test_public_mode.py tests/test_lite_routes.py -q
```

Expected: FAIL because the Watching payload, dialog, action, and list are absent.

- [ ] **Step 4: Implement state helpers**

Add `watching` to `FEEDBACK_STATES`. Make
`feedbackPayload(state, watchedScore = null, watchedEpisodes = null)` validate
only the relevant field and return only the relevant key.

Make
`feedbackConfirmation(state, watchedScore = null, watchedEpisodes = null, trackerSync = null)`
return the sync detail on confirmed writes and otherwise combine the local
receipt with connection/failure detail.

- [ ] **Step 5: Add the Watching interaction**

Render a Watching feedback button before Already watched. Add
`data-watched-episodes` handling and label a selected progress action
`Watching · episode 3`.

Change `saveFeedback` to accept an options object:

```javascript
async function saveFeedback(
  button,
  { watchedScore = null, watchedEpisodes = null } = {},
) {
  const payload = accountState.feedbackPayload(
    state,
    watchedScore,
    watchedEpisodes,
  );
}
```

Add a `watching-dialog` with a number input named `watched-episodes`, `min="1"`,
`step="1"`, explanatory copy, an inline error region, Cancel, and Save progress.
Mirror the existing watched-dialog focus restoration and cancel behavior.

Use the existing dialog visual language; add only focused number-input styles
needed for readable desktop/mobile layout.

- [ ] **Step 6: Render current and completed lists separately**

In `dashboard.html`, add **Currently watching** above **Watched & rated**.
Current rows show `Episode N` and their tracker detail. History labels derive
from `watch_status`: `watching · episode N` for current and
`watched · score/10` for completed. Update AniList connection copy to mention
progress and completed scores.

- [ ] **Step 7: Run JavaScript and rendered tests and verify GREEN**

Run the commands from Step 3. Expected: all selected tests PASS.

- [ ] **Step 8: Commit the UI cycle**

```powershell
git add -- src/omakase/web/static/account_state.js src/omakase/web/static/app.js src/omakase/web/static/style.css src/omakase/web/templates/index.html src/omakase/lite/templates/dashboard.html tests/js/account_state.test.cjs tests/test_public_mode.py tests/test_lite_routes.py
git commit -m "feat(lite): add watching progress controls"
```

---

### Task 5: Documentation and consolidated local gate

**Files:**
- Modify: `README.md`
- Modify: `.env.example` only if no new runtime variable is required; expected disposition is unchanged.
- Verify: all source and test files from Tasks 1–4.

**Interfaces:**
- Consumes: the finished feature.
- Produces: durable self-hosting/user behavior documentation and a clean release candidate.

- [ ] **Step 1: Update durable documentation**

In the Lite account behavior section, distinguish:

- Watching → local positive episode progress, AniList CURRENT/progress when
  connected, retry after connection;
- Already watched → local 1–10 score, AniList COMPLETED/score when connected.

State that source-account identity matching and returned-receipt validation
apply to both.

- [ ] **Step 2: Run the consolidated local gate**

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
python -m pytest -q
python -m ruff format --check .
python -m ruff check .
node --test tests/js/*.test.cjs
node --check src/omakase/web/static/account_state.js
node --check src/omakase/web/static/app.js
python -m build
docker compose -f compose.yaml -f compose.production.yaml config --quiet
docker compose -f compose.yaml -f compose.production.yaml -f compose.anilist.yaml config --quiet
git diff --check
```

Expected: all tests/checks pass. If the local Docker engine remains unavailable,
record that exact boundary and use CT101 for the image/runtime proof.

- [ ] **Step 3: Run local browser QA**

The flow under test is: authenticated public counter → generate or load a
recommendation → choose Watching → enter episode progress → see local/sync
receipt → open My Counter → see the title only in Currently watching.

Use the Browser plugin, leave tabs open, and verify:

- desktop and 390-pixel mobile layouts;
- title/URL and meaningful DOM;
- no framework overlay;
- no relevant console warnings/errors;
- no horizontal overflow;
- Watching dialog keyboard/focus behavior;
- invalid zero value stays in the dialog with actionable copy;
- successful local test data updates the selected button and dashboard.

- [ ] **Step 4: Commit documentation and local-gate fixes**

```powershell
git add -- README.md
git commit -m "docs: explain Lite episode progress"
```

Do not commit generated build artifacts.

---

### Task 6: Public integration, exact deploy, and live proof

**Files:**
- Modify after live proof: vault `Agent-Sessions/omakase/21-lite-episode-progress.md`
- Modify after live proof: vault `Projects/omakase/current-state.json`
- Modify after live proof: vault `Projects/omakase/features.md`
- Modify after live proof: vault `Projects/omakase/history.md`
- Modify after live proof: vault `Projects/omakase/whats-new.md`
- Create after live proof: vault live-verification and session-metrics evidence.

**Interfaces:**
- Consumes: a clean, tested feature branch and the current CT101 public stack.
- Produces: exact local/remote/CT101/image/health/canonical convergence and rollback.

- [ ] **Step 1: Push the feature branch and verify CI**

```powershell
git push -u origin codex/lite-episode-progress-20260729
gh run list --repo HeyiTzSenpai/omakase --branch codex/lite-episode-progress-20260729 --limit 1
```

Require success for the exact feature head.

- [ ] **Step 2: Fast-forward designated public main**

From `E:\Projects\omakase\.worktrees\omakase-public-main-current`:

```powershell
git fetch origin
git merge --ff-only codex/lite-episode-progress-20260729
git push origin main
```

Require clean main and `HEAD == origin/main`.

- [ ] **Step 3: Capture rollback and deploy exact main**

On CT101:

- verify the stack source is clean at `8be7b4553165ecbdee8cb029457463bf282dc89d`;
- capture source, `.env`/secret configuration, and an SQLite online backup;
- tag the current running image with a timestamped pre-progress rollback tag;
- fetch and fast-forward to the exact public main head;
- atomically update `OMAKASE_SOURCE_COMMIT` to that 40-character head;
- build and recreate with
  `compose.yaml`, `compose.production.yaml`, and `compose.anilist.yaml`.

- [ ] **Step 4: Prove production preservation and convergence**

Require:

- container healthy with restart count 0;
- image label, local `/api/health`, fresh external `/api/health`, CT source,
  local main, and `origin/main` all equal the same commit;
- migration 007 applied once;
- users, runs, recommendations, and existing watched counts preserved;
- existing completed rows backfilled to `watch_status = 'completed'`;
- SQLite `integrity_check = ok`;
- exactly 24 pre-existing foreign-key findings remain;
- protected secret mount remains readable and non-world-readable;
- security headers remain present.

- [ ] **Step 5: Run live browser QA without unauthorized mutation**

On the authenticated public dashboard, prove the new Currently watching
section and the Watching dialog on desktop and mobile. Exercise validation and
cancel without submitting a real AniList progress change. Confirm no relevant
console errors, overlays, or overflow. Leave browser tabs open per the Codex
desktop guardrail.

- [ ] **Step 6: Record canonical state only after proof**

Create Brief 21, live evidence, and redacted measured telemetry. Update current
capability/history/what's-new, set public repo/deployment head to the exact live
commit, retain Brief 19 as the separate active OpenWebUI carry, then run:

```powershell
python Tools\agent-workflow\agent_workflow.py sync --project omakase
$publicHead = git -C E:\Projects\omakase\.worktrees\omakase-public-main-current rev-parse HEAD
$deploymentVersion = "private a013144de325d84696853d0b6054dd8b67b23521; public $publicHead; jhinx.dev aaba555f6d9e3d0ab5419850c71c0a843bba6cb3"
python Tools\agent-workflow\agent_workflow.py validate --project omakase --check-git --deployed-version $deploymentVersion
python Tools\agent-workflow\agent_workflow.py lint-docs --project omakase
```

Commit and push only the intended Omakase vault paths; preserve unrelated vault
changes.

- [ ] **Step 7: Finish and notify**

Classify the feature, public-main, and vault worktrees; require session-owned
paths clean and pushed. Send the configured Discord notification with honest
`finished` status only after exact live/canonical convergence. The friend's
one-time AniList consent and Brief 19 OpenWebUI endpoint proof remain explicit
external carries rather than release drift.
