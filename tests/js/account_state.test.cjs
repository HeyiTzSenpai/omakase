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

test("builds strict feedback payloads for completed scores and episode progress", () => {
  assert.deepEqual(state.feedbackPayload("saved"), { state: "saved" });
  assert.deepEqual(state.feedbackPayload("watched", "8"), {
    state: "watched",
    watched_score: 8,
  });
  assert.deepEqual(state.feedbackPayload("watching", null, "4"), {
    state: "watching",
    watched_episodes: 4,
  });
  assert.throws(
    () => state.feedbackPayload("watched", ""),
    /score from 1 to 10/,
  );
  assert.throws(
    () => state.feedbackPayload("watched", "11"),
    /score from 1 to 10/,
  );
  assert.throws(
    () => state.feedbackPayload("watching", null, ""),
    /positive whole number/,
  );
  assert.throws(
    () => state.feedbackPayload("watching", null, "1.5"),
    /positive whole number/,
  );
  assert.throws(
    () => state.feedbackPayload("watching", null, "0"),
    /positive whole number/,
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
    "Saved in Omakase with your 9/10 score. Future menus will use it.",
  );
  assert.equal(
    state.feedbackConfirmation("watched", 9, null, {
      state: "synced",
      detail: "Added to Friend’s AniList as Completed · 9/10.",
    }),
    "Added to Friend’s AniList as Completed · 9/10.",
  );
  assert.equal(
    state.feedbackConfirmation("watched", 9, null, {
      state: "connection_required",
      detail: "Connect AniList to add this title and score to your anime list.",
    }),
    "Saved in Omakase with your 9/10 score. Connect AniList to add this title and score to your anime list.",
  );
  assert.equal(
    state.feedbackConfirmation("watching", null, 4),
    "Saved in Omakase at 4 episodes. Future menus will use it.",
  );
  assert.equal(
    state.feedbackConfirmation("watching", null, 4, {
      state: "connection_required",
      detail: "Connect AniList to sync 4 watched episodes to your anime list.",
    }),
    "Saved in Omakase at 4 episodes. Connect AniList to sync 4 watched episodes to your anime list.",
  );
  assert.equal(
    state.feedbackConfirmation("watching", null, 1),
    "Saved in Omakase at 1 episode. Future menus will use it.",
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

test("restores only the non-secret OpenWebUI instance and model choices", () => {
  assert.deepEqual(
    state.rememberedSetup({
      remembered_setup: {
        provider: "openwebui",
        mode: "fast",
        source: "anilist",
        source_username: "friend",
        use_planning: false,
        skip_profile: false,
        llm_url: "https://models.example.com/team",
        model: "llama3.1:8b",
      },
    }),
    {
      provider: "openwebui",
      mode: "fast",
      source: "anilist",
      sourceUsername: "friend",
      usePlanning: false,
      skipProfile: false,
      llmUrl: "https://models.example.com/team",
      model: "llama3.1:8b",
    },
  );
});
