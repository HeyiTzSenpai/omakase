# Omakase Plus Dashboard UI Rework Design

## Intent

Omakase Plus is now a working private anime control room: add a show, ask for a tasting menu, plan/download the pick, and inspect Real-Debrid state. The current dashboard works, but it reads like chronological feature cards. The redesign turns it into a mobile-first command surface where the most common phone actions are visible immediately.

## Visual Direction

Use a deep violet nocturne palette derived from the current Plus colors:

- Page void: `#090414`
- Elevated void: `#130724`
- Panel: `#1a0b32`
- Panel high: `#26104a`
- Border: `#39215f`
- Bright purple: `#9f4cff`
- Glow purple: `#d8b4fe`
- Text: `#f8f3ff`
- Muted: `#cbb8ea`
- Quiet: `#8f7ab5`
- Semantic green/yellow/red remain for planned, warning, and error states.

The dashboard should feel like a polished anime-night operations desk: dark, intimate, crisp, slightly luminous, with a few sharp violet highlights. Avoid decorative blobs and avoid making every surface the same purple rectangle.

## Information Architecture

Phone-first order:

1. **Dashboard masthead:** Omakase Plus identity, signed-in user, Settings, logout, and quick status summary.
2. **Primary action:** Add Anime as the first useful control, with title/URL input, season/arc input, and a single strong action.
3. **Run Recommendation:** compact lane segmented control, core source/username/count/mode controls, optional advanced controls, loading state.
4. **Tonight's Tasting Menu:** recommendation cards with score, chips, reasoning, Plan/Download, and feedback actions.
5. **Planning Queue:** queue rows/cards with RD state, details, attempts, Retry/Download, Remove.
6. **Taste Profile:** still available but de-emphasized below the action surfaces, because it changes less often on phone.
7. **Recent Runs:** compact history rail/list.

Desktop can use a two-column rhythm, but phone remains the source of truth.

## Component Model

- `dashboard-shell`: page wrapper with background atmosphere and constrained content.
- `dashboard-topbar`: identity, account actions, and small status chips.
- `hero-panel`: first action band for Add Anime plus queue summary.
- `panel`: reusable non-nested dashboard surface.
- `section-title`: compact section heading row.
- `field`, `input`, `select`, `textarea`: deliberate control typography and focus rings.
- `lane-grid`: 2x2 segmented radio buttons on phone, row on wider screens.
- `rec-card`: result card with stable score badge, metadata chips, and stacked phone actions.
- `queue-table`: desktop table that becomes clear mobile row cards using `data-label`.
- `status-badge`: semantic badges with accessible contrast.
- `loading-state`: centered progress state with reduced-motion-safe spinner.

## Interaction Rules

- Keep existing form routes and API contracts.
- Keep vanilla JS for run polling and feedback.
- Add only small UI state behavior if it improves phone ergonomics, such as advanced-control grouping.
- Preserve keyboard focus states and label associations.
- Respect `prefers-reduced-motion`.

## Acceptance Details

- At 390px width, all primary controls fit without horizontal scrolling.
- Add Anime and Run Recommendation are reachable before Taste Profile.
- Queue status and retry/download actions are understandable without expanding developer-like details first.
- RD attempt details remain safe: no magnet URL leak.
- Existing tests for Plan, Download, Retry, direct-download, lane, feedback, and settings prefill keep passing.
