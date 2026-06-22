# Provider Block Observability Design

Goal: make Real-Debrid provider blocks actionable in private Omakase Plus by recording each attempted torrent candidate and exposing a compact retry path in the planning queue.

## Decisions

- Store attempts in a new `download_attempts` table keyed by `user_id` and `anilist_planning_id`.
- Generate a `request_id` per download invocation so a later dashboard can distinguish retries.
- Store safe torrent metadata only: title, rank, seeders, size label, batch flag, info hash, RD status, HTTP status, error code, capped detail, and RD torrent id. Do not store full magnet URLs.
- Keep the queue compact: show the current `download_status` as today, then a `<details>` block with recent attempts.
- Use the existing `/plus/dashboard/download` POST as the manual retry action. Existing rows are re-used; the route re-runs search/download and appends a new attempt group.

## User Experience

Provider-blocked rows keep the yellow `RD BLOCKED` badge and detail. Beneath it, the row can show "2 RD attempts" as an expandable list. Each attempt shows rank, outcome, torrent title, seeders/size, and the provider/generic error reason when present.

Rows with no RD state, `not_found`, `error`, or `rd_provider_block` get a small `Retry` button in the Actions column. Rows already downloading keep the existing remove action and do not show Retry.

## Data Flow

1. `dashboard_download` resolves or creates the local `anilist_plannings` row and passes its database id to `search_and_download`.
2. `search_and_download` creates a `request_id`, ranks candidates, and writes one `download_attempts` row for every candidate it actually tries.
3. Dashboard loading fetches recent attempts for visible planning rows and attaches them to each planning view model.
4. The template renders attempts under the RD cell and the retry button under Actions.

## Verification

- Tests cover schema creation/cascade, attempt rows for provider-block/fallback/success flows, route passing `planning_id`, attempts rendering, and retry button rendering.
- Browser QA checks desktop and mobile queue layout for no horizontal overflow.
- Live deploy verification uses a temporary QA user/row and cleans it up after screenshots.
