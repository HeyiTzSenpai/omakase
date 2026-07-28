# Omakase Lite account experience

Date: 2026-07-28
Status: proposed for owner review

## Goal

Make the public recommendation-only account feel persistent and personal:

- a signed-in member can securely reuse a provider key;
- marking a title watched records a 1–10 score and clearly confirms the action;
- feedback immediately improves the next menu;
- a member can cook another menu from the results screen without rebuilding the
  same setup;
- guests retain the request-local BYOK privacy contract;
- Lite remains isolated from Plex, downloads, acquisition, and private Plus.

The already-deployed AniList error repair is separate: a missing or private
AniList handle now receives an actionable 400 message instead of a generic
failure.

## Product choices

### 1. Provider selection stays explicit

The key itself cannot reliably identify its provider. DeepSeek and OpenAI keys
can both use an `sk-` prefix, and a mistaken guess could send a credential to
the wrong company. Members choose the provider once; Omakase remembers the
saved credential for that provider and preselects the last successful setup.

### 2. Signed-in keys are encrypted server-side

Recommended approach:

- Add one credential row per member and provider.
- Encrypt each key with an application master key stored only as a protected
  CT101 Docker secret file.
- Store ciphertext, provider, a non-sensitive hint such as the final four
  characters, and timestamps. Never store or log plaintext.
- The session endpoint returns only saved/unsaved status and the hint.
- A submitted replacement key is encrypted before persistence. A blank key uses
  the member's saved key for the selected provider.
- Decrypt only while constructing the in-memory recommendation job, then clear
  the request/config references in `finally`.
- Provide explicit **Replace key** and **Forget key** actions.
- Guests never persist a key.

Use the established `cryptography` Fernet implementation rather than custom
cryptography. The deployment must preserve the master secret with protected
rollback material; restoring only the database without its matching secret
must not be presented as recoverable.

Alternatives rejected:

- Browser storage is device-specific and exposes the key to page JavaScript and
  XSS.
- Plaintext database storage would turn a database disclosure into immediate
  provider-account compromise.
- Key-prefix auto-detection is ambiguous and can route secrets incorrectly.

### 3. The key field is an API credential, not a login password

The existing page places a username-like field near
`<input type="password">`, which Chrome can interpret as a login form. The new
control will:

- use an API-credential-specific id/name rather than `api_key`;
- retain masking and the explicit Show/Hide control;
- use `autocomplete="off"` and common password-manager ignore attributes;
- avoid focusing an empty credential field when a saved key is available;
- display **Saved DeepSeek key · ending 1234** beside Replace/Forget controls;
- explain that signed-in keys are encrypted for reuse while guest keys remain
  request-local.

Real Chrome verification must confirm that entering the provider credential no
longer opens or offers the password-manager save flow.

### 4. “Already watched” requires a local score

Selecting **Already watched** opens an accessible inline score dialog with
buttons 1–10, Save, and Cancel. The server accepts a watched state only with an
integer score from 1 through 10. Other feedback states clear any former watched
score.

After save, the card and a live status region say, for example:

> Marked watched — 8/10. Future menus will use this.

The score is Omakase-local. Lite does not currently hold AniList OAuth
authorization, so this release will not write to the user's AniList account.
AniList write-through remains a separate, explicit OAuth project.

### 5. Feedback changes future menus and prevents obvious repeats

The personalization context will include title and score for watched items,
along with saved and not-interested titles. All non-neutral feedback titles are
excluded from the next candidate set when the source adapter provides stable
identities; normalized-title suppression is the fallback.

The prompt context should express:

- not interested: avoid and infer disliked traits;
- saved: interest signal, but do not immediately recommend the same title;
- watched with score: preference evidence weighted by the user's score.

This keeps feedback useful without treating “saved for later” as “watched.”

### 6. Results become the start of the next menu

After a successful signed-in run, show a prominent **Use my feedback and cook
another menu** action beside **Adjust setup**.

The new-menu action:

- reuses provider, model mode, source, username, planning choice, and taste
  profile from the last successful run;
- uses the saved provider key without returning it to the browser;
- includes all feedback saved since the prior run;
- keeps the current cards visible until the new job is accepted;
- then shows the normal progress and cancellation state;
- replaces the cards on success, moves focus to the results heading, and
  announces that a new menu is ready;
- records the new run in account history.

Guests keep the existing start-over path because their key is intentionally
forgotten after the request.

For a MyAnimeList export, **cook another menu** can reuse the in-memory upload
while the current page remains open. After a reload or later sign-in, the member
must choose the export again because Omakase does not persist uploaded history
files.

## Account experience

The public counter will expose a compact signed-in account bar:

- member display name and **My Counter** link;
- saved-provider status;
- account-safe privacy copy;
- Sign out.

The account page will show:

- recent menus with feedback state and watched score;
- saved-list items;
- saved provider credentials with Replace/Forget;
- taste-profile edit;
- clear empty, loading, success, and error states.

Feedback controls use a persistent per-card state plus a concise live
confirmation. A failed save leaves the previous state intact and provides a
retryable error instead of optimistically lying.

## Data model

Add migration `002-account-experience.sql`.

### `account_provider_keys`

- `user_id` — foreign key to `account_users`, cascade delete
- `provider` — allowlisted provider identifier
- `encrypted_key` — Fernet token, never plaintext
- `key_hint` — non-sensitive last four characters
- `created_at`, `updated_at`
- primary key `(user_id, provider)`

### `account_recommendations`

Add nullable `watched_score INTEGER` with a 1–10 constraint. Existing rows
remain valid.

### Remembered setup

Add the member's last successful recommendation setup to
`account_profiles` as explicit columns or a validated JSON object. It contains
only non-secret choices: provider, mode, source, source username, planning
choice, and skip-profile choice. MAL export contents and provider keys are not
stored in this object.

The implementation plan will choose explicit columns unless migration review
shows a strong reason for validated JSON; explicit columns are easier to query
and constrain.

## API behavior

- `GET /api/account/session` adds saved-provider summaries and remembered
  non-secret setup; it never returns ciphertext or plaintext.
- `PUT /api/account/provider-keys/{provider}` replaces the signed-in member's
  key after CSRF/origin validation and returns only status/hint.
- `DELETE /api/account/provider-keys/{provider}` forgets the signed-in member's
  key after CSRF/origin validation.
- `POST /api/recommend/jobs` accepts a nullable provider credential for
  authenticated members. A submitted key wins and is saved; otherwise the
  selected provider's saved key is used. Guests must still submit a key.
- `POST /api/account/recommendations/{id}/feedback` accepts
  `{state, watched_score}` and returns the persisted state/score.

All credential routes are owner-scoped through the authenticated session.
Responses and exception details must not contain submitted keys.

## Failure and recovery behavior

- Missing saved/submitted key: actionable 400 focused on the selected provider.
- Master key missing or invalid: fail closed with a generic credential
  unavailable message; never overwrite ciphertext.
- Decryption failure for one member/provider: report that the saved key must be
  replaced; do not expose cryptographic detail.
- Provider authentication failure: explain that the saved key may need
  replacement.
- Score save failure: keep the dialog/card state recoverable and do not claim
  success.
- New-menu failure: preserve the prior menu and offer Retry or Adjust setup.

Deployment recovery requires the prior image, a consistent Lite database
backup, and the matching protected master-secret backup. Migration rollback is
database restore plus prior image; no down-migration mutates production rows.

## Security and privacy acceptance

- Fernet encrypt/decrypt and wrong-master-key tests pass.
- Ciphertext and database bytes do not contain the submitted key.
- Session, credential responses, logs, errors, job debug state, and persisted
  recommendation rows never expose the key.
- Guest jobs do not create credential rows.
- Credential replace/forget, ownership, CSRF, origin, and provider allowlist
  tests pass.
- Decrypted values are held only for the bounded job setup/execution path and
  cleared afterward.
- The key secret file is readable only by the runtime identity and is absent
  from source, image layers, environment dumps, and evidence.
- Lite retains no access to Plex, acquisition, download providers, or the
  private Plus database.

## Functional and UI acceptance

- A member saves a DeepSeek key, reloads/signs in again, and runs Quick or Deep
  without repasting it.
- Replace and Forget work; after Forget, the next run asks for a key.
- Chrome does not offer to save the provider credential as a login password.
- Already Watched cannot save without a 1–10 score; a valid score persists,
  appears in history, and is included in later personalization.
- Not Interested, Save, Watched, and neutral transitions persist correctly;
  leaving Watched clears its score.
- **Cook another menu** starts from results, uses new feedback, and does not
  repeat excluded titles.
- Failed regeneration leaves the previous menu usable.
- Signed-in desktop and 390px mobile flows pass keyboard/focus, live-region,
  overflow, reduced-motion, loading, empty, and error-state checks with no
  console warnings/errors.
- Full Python tests, Ruff, JavaScript syntax, package/container build, Linux CI,
  external TLS, exact commit convergence, and fresh live browser verification
  pass before release completion.

## Out of scope

- AniList OAuth or writing scores/status back to AniList
- Plex, Overseerr, Real-Debrid, downloads, or acquisition
- storing MAL export files
- sharing one provider key across accounts
- automatic provider detection from key text
