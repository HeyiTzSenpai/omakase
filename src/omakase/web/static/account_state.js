"use strict";

(function exposeAccountState(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.OmakaseAccountState = api;
}(typeof globalThis === "object" ? globalThis : this, () => {
  const PROVIDERS = new Set([
    "openai",
    "openwebui",
    "anthropic",
    "gemini",
    "deepseek",
    "openrouter",
  ]);
  const MODES = new Set(["fast", "pro"]);
  const SOURCES = new Set(["anilist", "myanimelist"]);
  const FEEDBACK_STATES = new Set(["neutral", "not_interested", "saved", "watched"]);

  function hasSavedProviderKey(session, provider) {
    return Boolean(
      session?.authenticated
      && PROVIDERS.has(provider)
      && session.provider_keys?.[provider]?.saved === true,
    );
  }

  function feedbackPayload(state, watchedScore = null) {
    if (!FEEDBACK_STATES.has(state)) throw new Error("Choose a valid preference.");
    if (state !== "watched") return { state };
    const score = Number(watchedScore);
    if (!Number.isInteger(score) || score < 1 || score > 10) {
      throw new Error("Already watched needs a score from 1 to 10.");
    }
    return { state, watched_score: score };
  }

  function feedbackConfirmation(state, watchedScore = null) {
    if (state === "not_interested") {
      return "Not interested saved. This title will stay out of future menus.";
    }
    if (state === "saved") {
      return "Added to My List. This title will stay out of future menus.";
    }
    if (state === "watched") {
      return `Already watched saved with your ${Number(watchedScore)}/10 score. Future menus will use it.`;
    }
    return "Preference cleared.";
  }

  function safeExternalUrl(value) {
    if (typeof value !== "string" || !value.trim()) return "";
    try {
      const parsed = new URL(value);
      return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : "";
    } catch {
      return "";
    }
  }

  function rememberedSetup(session) {
    const setup = session?.remembered_setup;
    if (
      !setup
      || !PROVIDERS.has(setup.provider)
      || !MODES.has(setup.mode)
      || !SOURCES.has(setup.source)
    ) {
      return {};
    }
    const normalized = {
      provider: setup.provider,
      mode: setup.mode,
      source: setup.source,
      sourceUsername: typeof setup.source_username === "string" ? setup.source_username : "",
      usePlanning: setup.use_planning === true,
      skipProfile: setup.skip_profile === true,
    };
    if (setup.provider === "openwebui") {
      normalized.llmUrl = typeof setup.llm_url === "string" ? setup.llm_url : "";
      normalized.model = typeof setup.model === "string" ? setup.model : "";
    }
    return normalized;
  }

  return {
    feedbackConfirmation,
    feedbackPayload,
    hasSavedProviderKey,
    rememberedSetup,
    safeExternalUrl,
  };
}));
