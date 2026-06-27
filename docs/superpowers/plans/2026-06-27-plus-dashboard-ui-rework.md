# Omakase Plus Dashboard UI Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework private Omakase Plus `/plus/dashboard` into a polished mobile-first command dashboard and deploy it live to `anime.jhinx.dev`.

**Architecture:** Keep FastAPI/Jinja server rendering and vanilla JavaScript. Split dashboard CSS/JS into packaged Plus static assets served by the Plus router, then simplify the dashboard template into semantic sections that preserve the current routes and data contracts.

**Tech Stack:** Python 3.12 via existing `.venv`, FastAPI, Jinja2, SQLite, vanilla CSS/JS, pytest, ruff, Browser/IAB or Playwright fallback for visual QA.

**Orchestration:** Level 2 - master orchestrator with worker/spec-review/quality-review loops for non-trivial implementation slices. This is one frontend-heavy surface with small backend asset serving support.

**Strategic Planner Checkpoints:** Startup after brief/design lock; before live deploy; before handoff.

---

## File Structure

- Create `src/omakase/plus/static/dashboard.css` for dashboard tokens, layout, responsive rules, and state styling.
- Create `src/omakase/plus/static/dashboard.js` for the existing run-polling and feedback behavior.
- Modify `src/omakase/plus/routes.py` to serve allowlisted Plus static assets and pass small dashboard summary data.
- Modify `src/omakase/plus/templates/dashboard.html` to reference external assets, reorder dashboard sections, and add semantic shell classes.
- Modify `pyproject.toml` package data so `plus/static/*` ships in the installed wheel/container.
- Extend `tests/plus/test_dashboard.py` for asset serving, template references, shell markers, section order, and preserved queue/feedback/action anchors.

---

### Task 1: Serve Packaged Plus Dashboard Assets

**Files:**
- Modify: `src/omakase/plus/routes.py`
- Modify: `pyproject.toml`
- Test: `tests/plus/test_dashboard.py`

- [ ] **Step 1: Write failing asset tests**

Add tests that authenticate when needed, then assert:

```python
def test_dashboard_references_external_assets(client):
    _signup_and_login(client)
    html = client.get("/plus/dashboard").text
    assert 'href="/plus/static/dashboard.css"' in html
    assert 'src="/plus/static/dashboard.js"' in html
    assert "<style>" not in html


def test_plus_static_asset_route_serves_dashboard_css(client):
    resp = client.get("/plus/static/dashboard.css")
    assert resp.status_code == 200
    assert "dashboard-shell" in resp.text
    assert "text/css" in resp.headers["content-type"]
```

- [ ] **Step 2: Verify red**

Run:

```powershell
$env:PYTHONPATH="C:\Users\qazws\Projects\omakase\.worktrees\plus-dashboard-ui-rework\src"
& C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest tests\plus\test_dashboard.py::TestDashboardAccess::test_dashboard_references_external_assets tests\plus\test_dashboard.py::TestDashboardAccess::test_plus_static_asset_route_serves_dashboard_css -q
```

Expected: fails because assets/routes do not exist and dashboard still has inline style/script.

- [ ] **Step 3: Implement minimal asset serving**

Create static files, add package data `"plus/static/*"`, and add an allowlisted `GET /plus/static/{asset}` route using `FileResponse`.

- [ ] **Step 4: Verify green**

Run the same focused tests and confirm they pass.

---

### Task 2: Rebuild Dashboard Template Around Mobile-First Sections

**Files:**
- Modify: `src/omakase/plus/templates/dashboard.html`
- Modify: `src/omakase/plus/routes.py`
- Test: `tests/plus/test_dashboard.py`

- [ ] **Step 1: Write failing structure/order tests**

Add tests that assert:

```python
def test_dashboard_has_mobile_first_shell_and_priority_order(client):
    _signup_and_login(client)
    html = client.get("/plus/dashboard").text
    assert 'class="dashboard-shell"' in html
    assert 'class="hero-panel"' in html
    assert 'class="quick-stats"' in html
    assert html.index("Add Anime") < html.index("Run Recommendation") < html.index("Taste Profile")
    assert "Planning Queue" in html
```

- [ ] **Step 2: Verify red**

Run the new test and confirm it fails on missing shell/classes/order.

- [ ] **Step 3: Implement template restructure**

Preserve existing form names/actions and data attributes. Move Add Anime first, Run Recommendation second, results and queue ahead of Taste Profile, and Recent Runs last. Add route-provided summary counts only if they come from already-loaded planning data.

- [ ] **Step 4: Verify green**

Run focused dashboard tests.

---

### Task 3: Apply Final CSS/JS Polish

**Files:**
- Modify: `src/omakase/plus/static/dashboard.css`
- Modify: `src/omakase/plus/static/dashboard.js`
- Test: `tests/plus/test_dashboard.py`

- [ ] **Step 1: Preserve behavior anchors**

Run existing dashboard tests before visual polish:

```powershell
$env:PYTHONPATH="C:\Users\qazws\Projects\omakase\.worktrees\plus-dashboard-ui-rework\src"
& C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest tests\plus\test_dashboard.py -q
```

- [ ] **Step 2: Polish CSS**

Implement tokens, responsive shell, hero panel, controls, recommendation cards, queue mobile cards, semantic badges, focus states, and reduced-motion rules. Keep radius at 8px or less for most controls/panels except small badges.

- [ ] **Step 3: Polish JS**

Move existing polling/feedback JS into `dashboard.js` with no route-contract changes. Keep failure text and redirect behavior intact.

- [ ] **Step 4: Verify dashboard suite**

Run `tests\plus\test_dashboard.py -q`.

---

### Task 4: Full Local Verification

**Files:** no intended source edits unless failures are found.

- [ ] **Step 1: Run full tests**

```powershell
$env:PYTHONPATH="C:\Users\qazws\Projects\omakase\.worktrees\plus-dashboard-ui-rework\src"
& C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m pytest -q
```

- [ ] **Step 2: Run ruff**

```powershell
& C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m ruff check .
& C:\Users\qazws\Projects\omakase\.venv\Scripts\python.exe -m ruff format --check .
```

- [ ] **Step 3: Browser QA locally**

Start private Plus with a temporary DB and master key, sign up a smoke account, then verify desktop and 390px mobile dashboard screenshots. Exercise Add Anime empty submit and at least one visible feedback/state path that does not spend BYOK tokens.

---

### Task 5: Deploy To Live Plus And Verify

**Files:** docs only unless deploy reveals source issue.

- [ ] **Step 1: Commit and push branch/plus-mvp as appropriate**

Keep public `main` untouched.

- [ ] **Step 2: Deploy Plus**

Use `DEPLOY-PLUS.md`/current CT 101 Plus recipe. Create a DB backup before rebuild.

- [ ] **Step 3: Live smoke**

Verify `https://anime.jhinx.dev/plus/login` returns `200`, live dashboard renders new assets after authenticated smoke login, desktop/mobile screenshots have no horizontal overflow or console errors, and public `https://omakase.jhinx.dev/plus/login` returns `404`.

- [ ] **Step 4: Update docs and notify**

Update Agent-Sessions brief/README, vault project README/history, memory if present, repo docs, and send Discord notification. Use `finished` only after live target verification passes.
