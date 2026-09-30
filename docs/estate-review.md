# Public counter estate review, 2026-09-30

## Surface and direction

Audience: a curious anime viewer trying one guest recommendation with their own model provider. The counter's job is to explain its three steps, keep the charge and privacy boundary clear, and let the viewer correct an input without losing place. The existing midnight plum, brass, serif type, and recommendation-room illustration remain the identity. The image is editorial art, not a sample recommendation or account screenshot.

The review keeps the working counter and all provider behavior intact. It names the provider API key explicitly, describes the three steps in ordinary language, keeps step numbers visible on narrow screens, and gives validation a clear edge. No provider call, paid model use, account data, or private Plus runtime is involved.

## Review preview

From this worktree:

```powershell
uv run --frozen python -m uvicorn omakase.web.server:app --host 127.0.0.1 --port 8776
```

Review URL: http://127.0.0.1:8776/ . This is a loopback preview, not a production release.

## Verification

- `uv run --frozen --extra dev pytest -q`: 164 passed, one Windows-only POSIX permission skip. Existing Starlette TestClient deprecation warning.
- `uv run --frozen --extra dev ruff check .`: passed.
- Isolated headless Edge at 1280 and 390 pixels: no horizontal overflow, no browser errors. The original illustration and primary action remain readable at both widths.
- Mobile primary action reaches the counter. Continuing without a key shows the existing specific error and focuses the key field. A fake unsubmitted test key permits navigation to History and clears that error. No recommendation request was submitted.

Visual critique: the form retains the established editorial counter layout. Keeping step numbers on mobile makes the journey legible while preserving the compact navigation. The hero copy now states the direct provider-charge boundary before the viewer enters a key. Measured browser checks establish containment and interaction, not subjective owner acceptance.

Remaining review: owner visual acceptance and a fresh production release check if deployment is later authorized. Keep the loopback preview running for the current review.
