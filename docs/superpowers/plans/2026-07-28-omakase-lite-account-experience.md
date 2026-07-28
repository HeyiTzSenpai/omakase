# Omakase Lite account experience implementation plan

> Approved design:
> `docs/superpowers/specs/2026-07-28-omakase-lite-account-experience-design.md`

## Objective

Ship the approved recommendation-only account experience: provider-scoped
encrypted key reuse, scored watched feedback, remembered non-secret setup,
repeat suppression, and cook-another-menu UX. Preserve request-local guest BYOK,
manual account approval, and strict isolation from private Plus/Plex/acquisition.

Work in
`E:\Projects\omakase\.worktrees\omakase-lite-accounts-20260728` on
`codex/omakase-lite-account-experience-20260728`. Build and deploy containers
only on CT101.

## Task 1 — Encrypted credential data layer

1. Add failing data-layer tests in `tests/test_lite_accounts.py` proving:
   - migration 002 creates provider-key storage and remembered-setup fields;
   - a saved key is ciphertext in SQLite and decrypts only with the configured
     Fernet master key;
   - a wrong/missing master key fails closed;
   - provider identifiers are allowlisted;
   - summaries expose only provider, saved status, and a last-four hint;
   - replace and forget are user-scoped.
2. Run the focused tests and confirm the expected missing-feature failures.
3. Add `cryptography` to `pyproject.toml`.
4. Add additive migration `002-account-experience.sql`.
5. Add `src/omakase/lite/credentials.py` using Fernet loaded from
   `OMAKASE_LITE_KEYRING_FILE`; never implement custom crypto.
6. Add parameterized DB functions for provider-key CRUD and explicit remembered
   setup fields.
7. Run the focused tests green, then refactor without changing behavior.

## Task 2 — Authenticated credential API and account surface

1. Add failing route tests in `tests/test_lite_routes.py` proving:
   - session output contains only saved credential summaries and remembered
     non-secret setup;
   - PUT replaces the current user's provider key;
   - DELETE forgets it;
   - missing auth, wrong CSRF/origin, unknown providers, and unavailable
     keyring fail safely;
   - neither plaintext nor ciphertext appears in responses.
2. Run the focused tests red.
3. Add strict Pydantic request models and credential PUT/DELETE routes.
4. Extend the dashboard context and markup with saved-provider status plus
   Replace/Forget controls.
5. Extend `account.js` with safe fetch/error/status behavior and no DOM HTML
   injection.
6. Run the focused tests green.

## Task 3 — Saved-key recommendation jobs and remembered setup

1. Add failing tests in `tests/test_recommendation_jobs.py` proving:
   - a signed-in submitted key is encrypted, saved, and used by the job;
   - a blank credential uses that user's saved key;
   - a request key replaces the saved key;
   - guests must submit a key and never create credential rows;
   - a saved-key authentication failure tells the member to replace it without
     leaking it;
   - job debug state, errors, and stored recommendation rows contain no key;
   - successful account jobs store the non-secret setup.
2. Run the focused tests red.
3. Resolve/store the authenticated credential before configuration creation,
   pass plaintext only into the bounded in-memory config, and clear references
   in `finally`.
4. Return only saved-key usage/status metadata needed by the UI.
5. Persist remembered setup only after a successful account run.
6. Run the focused tests green.

## Task 4 — Scored feedback and repeat suppression

1. Add failing data/route/engine tests proving:
   - watched requires an integer 1–10 score;
   - watched score persists and appears in history;
   - changing to another state clears the score;
   - feedback remains owner-scoped;
   - personalization text distinguishes disliked, saved, and watched-with-score;
   - all non-neutral feedback titles are removed from later candidate pools
     using normalized English/Romaji titles;
   - a fully suppressed candidate pool produces an actionable message.
2. Run the focused tests red.
3. Extend migration/data methods and use a strict feedback request model.
4. Add excluded-title configuration and candidate filtering before prompt
   construction.
5. Run focused tests green and verify existing recommendation behavior.

## Task 5 — Main-counter member UX

1. Add failing TestClient/Node behavior tests proving:
   - the provider credential control has password-manager-ignore semantics and
     is optional only when the selected provider has a saved key;
   - session state selects the remembered provider/source/mode/setup;
   - watched feedback requires a score payload;
   - feedback confirmation text includes the saved score;
   - the member-only cook-another-menu action preserves prior results on failure
     and can submit without returning a saved key to the browser.
2. Run the focused tests red.
3. Update `index.html` with:
   - an API-credential-specific input and saved-key status;
   - account identity/status copy;
   - a shared accessible watched-score dialog;
   - a polite feedback live region;
   - primary **Use my feedback and cook another menu** and secondary
     **Adjust setup** actions.
4. Add a small testable static UI-state helper and update `app.js` to:
   - apply saved credential and remembered setup state;
   - validate a key only when no saved key exists;
   - open/save/cancel the score dialog;
   - confirm persisted feedback;
   - regenerate while keeping the prior menu until replacement succeeds;
   - avoid `innerHTML`, localStorage, and sessionStorage for account/secret data.
5. Extend existing design tokens/components in `style.css`; add no new visual
   language or generated assets.
6. Run focused Python and Node tests green.

## Task 6 — Consolidated local release gate

1. Re-read the approved design and check every acceptance item.
2. Run:
   - focused account/job/engine tests;
   - full `pytest`;
   - Ruff check and format check;
   - Node syntax and UI-state tests;
   - `git diff --check`;
   - package build and wheel-content inspection;
   - tracked-source secret scan.
3. Start a local non-Docker server only if needed for pre-deploy browser
   inspection; use Browser/IAB first.
4. Inspect desktop and 390px signed-in states, score dialog, regeneration,
   focus/live regions, reduced motion, overflow, and console output.
5. Commit and push the exact release candidate only after the full gate passes.

## Task 7 — CT101 rollback, secret plumbing, deploy, and proof

1. On CT101, capture:
   - prior image/revision;
   - consistent Lite SQLite backup;
   - source/compose backup;
   - protected copy of the existing runtime secrets.
2. Create the Fernet master-key secret directly on CT101 without printing it;
   make it owner-readable only. Add the protected production-secret mount and
   validate the resolved Compose config on CT101.
3. Clone/build the exact pushed commit on CT101; verify OCI revision before
   deployment.
4. Deploy the exact image with the production overlay; prove:
   - container running/healthy;
   - health, deployed marker, OCI revision, public remote, and local release
     head converge;
   - migration 002 is applied once;
   - database and secret permissions are private;
   - Lite remains isolated from private Plus/Plex/acquisition.
5. Use a fresh approved test member and non-production provider stub where
   possible to prove credential save/reload/forget, score persistence, history,
   and setup reuse without printing secrets. Use the real owner-approved
   provider key only from the protected runtime/browser flow for a final real
   recommendation if already available; never expose it in commands/evidence.
6. Use a fresh browser through valid public TLS for desktop and 390px:
   - signed-in saved-key status;
   - no password-manager save overlay;
   - recommendation results;
   - Already Watched score and confirmation;
   - cook-another-menu with prior feedback;
   - account dashboard/history;
   - console, overflow, focus, loading/error, and reduced-motion checks.
7. Remove synthetic proof accounts/runs/credentials and the temporary source
   clone after verification. Retain rollback material.

## Task 8 — Finish and convergence

1. Append the verified Outcome to Brief 13 and evidence once live proof exists.
2. Update only affected Omakase README/features/what's-new/history projections.
3. Set canonical public release head, deployment, rollback, and verification in
   `current-state.json`; sync and validate `CURRENT.md`/`NEXT-PROMPT.md`.
4. Commit/push the scoped private vault history.
5. Classify every in-scope worktree; preserve unrelated existing vault changes.
6. Send an honest Discord `finished` notification only after exact source/live
   convergence; otherwise record `partial` with exact drift.
