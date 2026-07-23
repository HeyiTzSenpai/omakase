# Omakase Public BYOK Counter Implementation Plan

## Release 1: truthful request boundary

1. Add failing tests for request-local key and inline profile handling, no
   environment mutation, provider URL allowlisting, redacted unknown failures,
   public-only routes, and exact health metadata.
2. Extend the runtime configuration with request-local values and pass them
   directly through the engine to the LLM client.
3. Remove public writes of taste notes and credentials. Restrict hosted cloud
   provider URLs and keep local or custom endpoints for self-hosted mode only.
4. Run focused tests, then the full Python suite and Ruff.

## Release 2: chef's counter interface

1. Replace the monolithic public template with semantic sections and a separate
   public JavaScript module while preserving API compatibility.
2. Build the nocturnal counter token system, responsive layout, visible focus,
   honest privacy receipt, guided source/provider controls, loading route,
   inline errors, and result menu.
3. Add a generated still-life asset only if it improves the first viewport
   after a static composition review.
4. Add browser coverage for desktop, 390 pixel mobile, reduced motion, form
   validation, provider/source conditional UI, mocked results, accessibility,
   console errors, and overflow.

## Release 3: exact public deployment

1. Update public README and durable deployment documentation without including
   private Plus topology or data.
2. Commit and push the public branch, then advance public `main` to the reviewed
   exact commit.
3. Capture the current public container/image as a rollback reference. Build and
   deploy the exact commit through the existing CT101 stack and overlay.
4. Prove the live health commit, public/private route boundary, desktop/mobile
   interface, favicon, primary validation flow, and no horizontal overflow.
5. Update the jhinx.dev Omakase case study only if the live destination or
   visitor-facing product claim changed, then record canonical state and
   Discord completion evidence.
