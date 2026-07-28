"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const state = require("../../src/omakase/web/static/account_state.js");

test("detects only a saved key for the selected provider", () => {
  const session = {
    authenticated: true,
    provider_keys: { deepseek: { saved: true, hint: "1234" } },
  };

  assert.equal(state.hasSavedProviderKey(session, "deepseek"), true);
  assert.equal(state.hasSavedProviderKey(session, "openai"), false);
  assert.equal(state.hasSavedProviderKey({ authenticated: false }, "deepseek"), false);
});

test("builds strict feedback payloads and requires a watched score", () => {
  assert.deepEqual(state.feedbackPayload("saved"), { state: "saved" });
  assert.deepEqual(state.feedbackPayload("watched", "8"), {
    state: "watched",
    watched_score: 8,
  });
  assert.throws(
    () => state.feedbackPayload("watched", ""),
    /score from 1 to 10/,
  );
  assert.throws(
    () => state.feedbackPayload("watched", "11"),
    /score from 1 to 10/,
  );
});

test("writes clear feedback confirmations", () => {
  assert.equal(
    state.feedbackConfirmation("not_interested"),
    "Not interested saved. This title will stay out of future menus.",
  );
  assert.equal(
    state.feedbackConfirmation("saved"),
    "Added to My List. This title will stay out of future menus.",
  );
  assert.equal(
    state.feedbackConfirmation("watched", 9),
    "Already watched saved with your 9/10 score. Future menus will use it.",
  );
});

test("allows only http and https recommendation links", () => {
  assert.equal(
    state.safeExternalUrl("https://anilist.co/anime/21"),
    "https://anilist.co/anime/21",
  );
  assert.equal(state.safeExternalUrl("javascript:alert(1)"), "");
  assert.equal(state.safeExternalUrl("data:text/html,unsafe"), "");
  assert.equal(state.safeExternalUrl("not a url"), "");
});

test("normalizes remembered setup without trusting unknown values", () => {
  assert.deepEqual(
    state.rememberedSetup({
      remembered_setup: {
        provider: "deepseek",
        mode: "pro",
        source: "anilist",
        source_username: "friend",
        use_planning: true,
        skip_profile: false,
      },
    }),
    {
      provider: "deepseek",
      mode: "pro",
      source: "anilist",
      sourceUsername: "friend",
      usePlanning: true,
      skipProfile: false,
    },
  );
  assert.deepEqual(
    state.rememberedSetup({
      remembered_setup: { provider: "unsafe", mode: "turbo", source: "other" },
    }),
    {},
  );
});
