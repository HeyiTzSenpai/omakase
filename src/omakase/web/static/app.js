"use strict";

const accountState = window.OmakaseAccountState;

const PROVIDERS = {
  openai: {
    label: "OpenAI",
    url: "https://api.openai.com",
    fast: "gpt-4o-mini",
    pro: "gpt-4o",
    hint: "Create or manage a key in your OpenAI account.",
  },
  anthropic: {
    label: "Anthropic",
    url: "https://api.anthropic.com",
    fast: "claude-haiku-4-5",
    pro: "claude-sonnet-4-6",
    hint: "Use a key from your Anthropic Console account.",
  },
  gemini: {
    label: "Gemini",
    url: "https://generativelanguage.googleapis.com",
    fast: "gemini-2.5-flash",
    pro: "gemini-2.5-pro",
    hint: "Use a Gemini API key from Google AI Studio.",
  },
  deepseek: {
    label: "DeepSeek",
    url: "https://api.deepseek.com",
    fast: "deepseek-v4-flash",
    pro: "deepseek-v4-pro",
    hint: "Use a key from the DeepSeek Platform.",
  },
  openrouter: {
    label: "OpenRouter",
    url: "https://openrouter.ai/api",
    fast: "openai/gpt-4o-mini",
    pro: "anthropic/claude-sonnet-4.6",
    hint: "Use an OpenRouter key with access to the selected model.",
  },
};

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const COURSE_ORDER = ["model", "history", "taste"];
const LOADING_PHASES = [
  { at: 0, text: "Reading your anime history..." },
  { at: 4, text: "Mapping the patterns in your scores..." },
  { at: 8, text: "Pairing the tasting menu..." },
  { at: 28, text: "The model is taking a closer look..." },
  { at: 65, text: "Still preparing. Deep models can take longer." },
];

let activeCourse = "model";
let malExportB64 = "";
let loadingTimer = null;
let loadingStartedAt = 0;
let requestController = null;
let activeJobId = "";
let accountSession = { authenticated: false };
let accountSessionLoaded = false;
let pendingWatchedButton = null;

const byId = (id) => document.getElementById(id);

function showCourse(name, focusHeading = true) {
  if (!COURSE_ORDER.includes(name)) return;
  activeCourse = name;
  document.querySelectorAll("[data-course]").forEach((panel) => {
    const selected = panel.dataset.course === name;
    panel.hidden = !selected;
    panel.classList.toggle("is-active", selected);
  });
  document.querySelectorAll("[data-course-target]").forEach((button) => {
    const selected = button.dataset.courseTarget === name;
    button.classList.toggle("is-active", selected);
    if (selected) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  });
  if (focusHeading) {
    const heading = document.querySelector(`[data-course="${name}"] h3`);
    heading?.setAttribute("tabindex", "-1");
    heading?.focus({ preventScroll: true });
  }
}

function showError(message, course = activeCourse) {
  if (course !== activeCourse) showCourse(course, false);
  byId("error-text").textContent = message;
  const banner = byId("error-banner");
  banner.hidden = false;
  banner.focus({ preventScroll: true });
}

function clearError() {
  byId("error-banner").hidden = true;
  byId("error-text").textContent = "";
}

function selectedValue(name) {
  return document.querySelector(`input[name="${name}"]:checked`)?.value || "";
}

function updateSelectedCards(selector) {
  document.querySelectorAll(selector).forEach((label) => {
    const input = label.querySelector("input");
    label.classList.toggle("is-selected", Boolean(input?.checked));
  });
}

function updateProvider() {
  const name = selectedValue("provider") || "openai";
  const provider = PROVIDERS[name];
  const mode = selectedValue("model-mode") || "fast";
  byId("llm_type").value = name;
  byId("llm_url").value = provider.url;
  byId("mode").value = mode;
  byId("model_override").disabled = false;
  const override = byId("model_override").value.trim();
  byId("model").value = override || provider[mode];
  byId("key-hint").textContent = provider.hint;
  byId("model-hint").textContent = `${mode === "fast" ? "Quick" : "Deep"} currently selects ${provider[mode]}.`;
  const saved = accountState.hasSavedProviderKey(accountSession, name);
  const hint = accountSession.provider_keys?.[name]?.hint;
  if (saved) {
    byId("api_key").placeholder = `Saved key ending ${hint} — leave blank to use it`;
    byId("key-status").textContent = `${provider.label} key saved to your account. Leave this blank to use it, or paste a replacement.`;
  } else if (accountSession.authenticated) {
    byId("api_key").placeholder = `Paste a ${provider.label} key to save it`;
    byId("key-status").textContent = `No ${provider.label} key saved yet. The next key you use will be encrypted for your account.`;
  } else {
    byId("api_key").placeholder = "Paste for this request only";
    byId("key-status").textContent = "";
  }
  updateSelectedCards(".choice-card");
}

function updateSource() {
  const source = selectedValue("list-source") || "anilist";
  byId("source").value = source;
  byId("anilist-inputs").hidden = source !== "anilist";
  byId("mal-inputs").hidden = source !== "myanimelist";
  document.querySelectorAll(".source-switch label").forEach((label) => {
    label.classList.toggle("is-selected", Boolean(label.querySelector("input")?.checked));
  });
  if (source === "anilist") clearMalFile();
}

function updateProfileState() {
  const skip = byId("skip_profile").checked;
  byId("profile-field").hidden = skip;
  if (!skip) updateWordCount();
}

function updateWordCount() {
  const words = byId("profile").value.trim().split(/\s+/).filter(Boolean).length;
  byId("profile-count").textContent = `${words} ${words === 1 ? "word" : "words"}`;
}

function validateCourse(name) {
  if (name === "model") {
    const provider = byId("llm_type").value;
    if (
      !byId("api_key").value.trim()
      && !accountState.hasSavedProviderKey(accountSession, provider)
    ) {
      showError(
        accountSession.authenticated
          ? "Paste a key for this provider, or choose a provider with a saved key."
          : "Paste the key for the provider you selected.",
        "model",
      );
      byId("api_key").focus();
      return false;
    }
  }
  if (name === "history") {
    const source = byId("source").value;
    if (source === "anilist" && !byId("username").value.trim()) {
      showError("Enter the public AniList username whose scores should guide the menu.", "history");
      byId("username").focus();
      return false;
    }
    if (source === "myanimelist" && !malExportB64) {
      showError("Choose a MyAnimeList Anime export before continuing.", "history");
      byId("mal_export_file").focus();
      return false;
    }
  }
  if (name === "taste") {
    const skip = byId("skip_profile").checked;
    const planOnly = byId("use_planning").checked;
    if (!skip && !planOnly && !byId("profile").value.trim()) {
      showError("Add a few taste notes, choose scores only, or limit the menu to Plan to Watch.", "taste");
      byId("profile").focus();
      return false;
    }
  }
  clearError();
  return true;
}

function navigateToCourse(target) {
  const currentIndex = COURSE_ORDER.indexOf(activeCourse);
  const targetIndex = COURSE_ORDER.indexOf(target);
  if (targetIndex > currentIndex && !validateCourse(activeCourse)) return;
  showCourse(target);
}

function onMalFileSelected() {
  const input = byId("mal_export_file");
  const file = input.files?.[0];
  if (!file) {
    clearMalFile();
    return;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    showError("That file is larger than 10 MB. Check that you selected an Anime export.", "history");
    clearMalFile();
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    const result = String(reader.result || "");
    const comma = result.indexOf(",");
    malExportB64 = comma >= 0 ? result.slice(comma + 1) : "";
    byId("upload-trigger-label").textContent = `${file.name} is ready`;
    byId("upload-clear").hidden = false;
    clearError();
  };
  reader.onerror = () => {
    showError("Omakase could not read that export. Choose the file again.", "history");
    clearMalFile();
  };
  reader.readAsDataURL(file);
}

function clearMalFile() {
  malExportB64 = "";
  if (byId("mal_export_file")) byId("mal_export_file").value = "";
  if (byId("upload-trigger-label")) byId("upload-trigger-label").textContent = "Choose your MAL export";
  if (byId("upload-clear")) byId("upload-clear").hidden = true;
}

function renderLoading() {
  const elapsed = Math.floor((Date.now() - loadingStartedAt) / 1000);
  const phase = LOADING_PHASES.filter((item) => elapsed >= item.at).at(-1) || LOADING_PHASES[0];
  byId("loading-text").textContent = phase.text;
  byId("loading-elapsed").textContent = `${elapsed}s`;
}

function setLoading(active) {
  const loading = byId("loading");
  const form = byId("setup-form");
  loading.hidden = !active;
  form.hidden = active;
  byId("run-btn").disabled = active;
  byId("recommend-again").disabled = active;
  byId("start-over").disabled = active;
  byId("results").setAttribute("aria-busy", String(active));
  if (active) {
    loadingStartedAt = Date.now();
    renderLoading();
    loadingTimer = window.setInterval(renderLoading, 1000);
    byId("cancel-request").focus({ preventScroll: true });
  } else {
    window.clearInterval(loadingTimer);
    loadingTimer = null;
  }
}

function buildPayload() {
  const skipProfile = byId("skip_profile").checked;
  return {
    llm_type: byId("llm_type").value,
    llm_url: byId("llm_url").value,
    api_key: byId("api_key").value,
    model: byId("model").value,
    source: byId("source").value,
    username: byId("source").value === "anilist" ? byId("username").value.trim() : "",
    profile: skipProfile ? "" : byId("profile").value,
    mode: byId("mode").value,
    use_planning: byId("use_planning").checked,
    skip_profile: skipProfile,
    mal_export_b64: malExportB64,
    mal_client_id: "",
  };
}

async function readApiResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new Error(
      response.status >= 500
        ? "The recommendation service was interrupted. Please try again."
        : "Omakase received an unexpected response. Please try again.",
    );
  }
  try {
    return await response.json();
  } catch {
    throw new Error("Omakase received an incomplete response. Please try again.");
  }
}

function abortableDelay(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, milliseconds);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

async function pollRecommendationJob(jobId, signal) {
  while (true) {
    await abortableDelay(1200, signal);
    const response = await fetch(`/api/recommend/jobs/${encodeURIComponent(jobId)}`, {
      credentials: "same-origin",
      cache: "no-store",
      signal,
    });
    const data = await readApiResponse(response);
    if (!response.ok) throw new Error(data.detail || "Omakase lost track of this menu.");
    if (data.status === "done") return data;
    if (data.status === "error") {
      throw new Error(data.detail || "Omakase could not finish this menu.");
    }
    if (data.status === "cancelled") throw new DOMException("Aborted", "AbortError");
  }
}

async function runRecommendations(event) {
  event.preventDefault();
  await submitRecommendations(false);
}

async function submitRecommendations(preserveResults) {
  if (!accountSessionLoaded) await loadAccountSession();
  for (const course of COURSE_ORDER) {
    if (!validateCourse(course)) return;
  }

  requestController = new AbortController();
  setLoading(true);
  if (!preserveResults) byId("results").hidden = true;
  try {
    const headers = { "Content-Type": "application/json" };
    if (accountSession.authenticated) headers["X-CSRF-Token"] = accountSession.csrf_token;
    const response = await fetch("/api/recommend/jobs", {
      method: "POST",
      headers,
      credentials: "same-origin",
      cache: "no-store",
      body: JSON.stringify(buildPayload()),
      signal: requestController.signal,
    });
    const receipt = await readApiResponse(response);
    if (!response.ok) throw new Error(receipt.detail || "Omakase could not start this menu.");
    activeJobId = receipt.job_id;
    const data = await pollRecommendationJob(activeJobId, requestController.signal);
    displayResults(data);
    byId("api_key").value = "";
    if (data.account_saved) await loadAccountSession(false);
  } catch (error) {
    if (error.name === "AbortError") showError("The menu was cancelled before it could be saved.", "taste");
    else showError(error.message || "Omakase could not finish this menu.", "taste");
  } finally {
    activeJobId = "";
    requestController = null;
    setLoading(false);
  }
}

function createText(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = value;
  return element;
}

function displayResults(data) {
  const results = byId("results");
  const list = byId("results-list");
  list.replaceChildren();
  const sourceLabel = data.source === "myanimelist" ? "MAL export" : "AniList";
  byId("results-meta").textContent = `${sourceLabel} / ${data.recommendations.length} picks${data.account_saved ? " / saved to My counter" : ""}`;

  if (!data.recommendations.length) {
    const empty = createText("p", "empty-state", "The model returned no usable picks. Try Quick mode or another provider.");
    list.appendChild(empty);
  } else {
    data.recommendations.forEach((recommendation, index) => {
      const article = document.createElement("article");
      article.className = "recommendation";
      article.style.setProperty("--result-index", index);

      const number = createText("span", "recommendation__number", String(index + 1).padStart(2, "0"));
      const body = document.createElement("div");
      body.className = "recommendation__body";
      body.append(
        createText("h3", "recommendation__title", recommendation.title),
        createText("p", "recommendation__reason", recommendation.reasoning || "A promising match from the selected model."),
      );
      const match = document.createElement("p");
      match.className = "recommendation__match";
      match.append("Pairs with ", createText("strong", "", recommendation.best_match_from_history || "your scored history"));
      body.appendChild(match);

      const score = document.createElement("div");
      score.className = "recommendation__score";
      score.append(createText("strong", "", Number(recommendation.predicted_score || 0).toFixed(1)), createText("span", "", "/ 10 fit"));

      article.append(number, body, score);
      const safeUrl = accountState.safeExternalUrl(recommendation.url);
      if (safeUrl) {
        const link = createText("a", "recommendation__link", "Open anime page ↗");
        link.href = safeUrl;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.setAttribute("aria-label", `Open ${recommendation.title} in a new tab`);
        article.appendChild(link);
      }
      if (data.account_saved && recommendation.id) {
        const actions = document.createElement("div");
        actions.className = "recommendation__actions";
        actions.setAttribute("aria-label", `Feedback for ${recommendation.title}`);
        [
          ["not_interested", "Not interested"],
          ["saved", "Add to My List"],
          ["watched", "Already watched"],
        ].forEach(([state, label]) => {
          const button = createText("button", "recommendation__feedback", label);
          button.type = "button";
          button.dataset.feedback = state;
          button.dataset.recommendationId = recommendation.id;
          button.dataset.recommendationTitle = recommendation.title;
          button.dataset.defaultLabel = label;
          button.setAttribute("aria-pressed", String(recommendation.feedback_state === state));
          if (
            state === "watched"
            && recommendation.feedback_state === state
            && recommendation.watched_score
          ) {
            button.textContent = `Watched · ${recommendation.watched_score}/10`;
            button.dataset.watchedScore = recommendation.watched_score;
          }
          actions.appendChild(button);
        });
        const status = createText("p", "recommendation__feedback-status", "");
        status.setAttribute("aria-live", "polite");
        actions.appendChild(status);
        article.appendChild(actions);
      }
      list.appendChild(article);
    });
  }

  results.hidden = false;
  byId("recommend-again").hidden = !data.account_saved;
  results.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

async function saveFeedback(button, watchedScore = null) {
  if (!accountSession.authenticated) return;
  const state = button.dataset.feedback;
  const actions = button.closest(".recommendation__actions");
  const status = actions.querySelector(".recommendation__feedback-status");
  const buttons = actions.querySelectorAll("[data-feedback]");
  let payload;
  try {
    payload = accountState.feedbackPayload(state, watchedScore);
  } catch (error) {
    status.textContent = error.message;
    return false;
  }
  buttons.forEach((item) => { item.disabled = true; });
  status.textContent = "Saving…";
  try {
    const response = await fetch(`/api/account/recommendations/${button.dataset.recommendationId}/feedback`, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": accountSession.csrf_token,
      },
      body: JSON.stringify(payload),
    });
    const data = await readApiResponse(response);
    if (!response.ok) throw new Error(data.detail || "That preference could not be saved.");
    buttons.forEach((item) => {
      item.setAttribute("aria-pressed", String(item === button));
      item.textContent = item.dataset.defaultLabel;
      delete item.dataset.watchedScore;
    });
    if (state === "watched") {
      button.textContent = `Watched · ${data.watched_score}/10`;
      button.dataset.watchedScore = data.watched_score;
    }
    status.textContent = accountState.feedbackConfirmation(data.state, data.watched_score);
    return true;
  } catch (error) {
    status.textContent = error.message || "That preference could not be saved.";
    return false;
  } finally {
    buttons.forEach((item) => { item.disabled = false; });
  }
}

function applyRememberedSetup() {
  const setup = accountState.rememberedSetup(accountSession);
  if (!setup.provider) return;
  const provider = document.querySelector(`input[name="provider"][value="${setup.provider}"]`);
  const mode = document.querySelector(`input[name="model-mode"][value="${setup.mode}"]`);
  const source = document.querySelector(`input[name="list-source"][value="${setup.source}"]`);
  if (provider) provider.checked = true;
  if (mode) mode.checked = true;
  if (source) source.checked = true;
  byId("username").value = setup.sourceUsername;
  byId("use_planning").checked = setup.usePlanning;
  byId("skip_profile").checked = setup.skipProfile;
  updateProvider();
  updateSource();
  updateProfileState();
}

function setPrivacyReceipt(strongText, detailText) {
  const strong = document.createElement("strong");
  strong.textContent = strongText;
  byId("privacy-receipt-copy").replaceChildren(strong, ` ${detailText}`);
}

async function loadAccountSession(applySetup = true) {
  try {
    const response = await fetch("/api/account/session", {
      credentials: "same-origin",
      cache: "no-store",
    });
    if (response.ok) accountSession = await response.json();
  } catch {
    accountSession = { authenticated: false };
  }
  accountSessionLoaded = true;
  const signedIn = Boolean(accountSession.authenticated);
  byId("account-request-link").hidden = signedIn;
  byId("account-login-link").hidden = signedIn;
  byId("account-home-link").hidden = !signedIn;
  if (signedIn) {
    byId("account-home-link").textContent = `${accountSession.display_name} · My counter`;
    setPrivacyReceipt(
      "Encrypted for your account.",
      "Your Lite account remembers provider keys, taste notes, completed recommendations, and feedback. A key is decrypted only for a request to its provider.",
    );
    byId("history-privacy-copy").textContent = "Your history and notes go to that provider for this request. Completed picks, scores, and feedback are saved to your Lite account.";
    if (accountSession.taste_profile) {
      byId("profile").value = accountSession.taste_profile;
      updateWordCount();
    }
    if (applySetup) applyRememberedSetup();
  } else {
    setPrivacyReceipt(
      "Request-local by design.",
      "Your key and taste notes are used for this menu and never written to disk, logs, cookies, or a database.",
    );
  }
  updateProvider();
}

async function cancelActiveRequest() {
  if (activeJobId) {
    fetch(`/api/recommend/jobs/${encodeURIComponent(activeJobId)}`, {
      method: "DELETE",
      credentials: "same-origin",
      keepalive: true,
    }).catch(() => {});
  }
  requestController?.abort();
}

function startOver() {
  byId("results").hidden = true;
  showCourse("model", false);
  const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  byId("counter").scrollIntoView({ behavior, block: "start" });
  if (accountState.hasSavedProviderKey(accountSession, byId("llm_type").value)) {
    byId("model-course-title").setAttribute("tabindex", "-1");
    byId("model-course-title").focus({ preventScroll: true });
  } else {
    byId("api_key").focus({ preventScroll: true });
  }
}

function openWatchedDialog(button) {
  pendingWatchedButton = button;
  const dialog = byId("watched-dialog");
  byId("watched-dialog-title").textContent = `What score did you give ${button.dataset.recommendationTitle}?`;
  byId("watched-dialog-error").hidden = true;
  byId("watched-score-form").reset();
  const priorScore = button.dataset.watchedScore;
  if (priorScore) {
    const prior = dialog.querySelector(`input[name="watched-score"][value="${priorScore}"]`);
    if (prior) prior.checked = true;
  }
  dialog.showModal();
}

async function submitWatchedScore(event) {
  event.preventDefault();
  if (!pendingWatchedButton) return;
  const score = selectedValue("watched-score");
  const dialogError = byId("watched-dialog-error");
  try {
    accountState.feedbackPayload("watched", score);
  } catch (error) {
    dialogError.textContent = error.message;
    dialogError.hidden = false;
    return;
  }
  const saved = await saveFeedback(pendingWatchedButton, score);
  if (saved) {
    byId("watched-dialog").close();
    pendingWatchedButton.focus();
    pendingWatchedButton = null;
  } else {
    const cardStatus = pendingWatchedButton
      .closest(".recommendation__actions")
      ?.querySelector(".recommendation__feedback-status");
    dialogError.textContent = cardStatus?.textContent || "That score could not be saved.";
    dialogError.hidden = false;
  }
}

function closeWatchedDialog() {
  byId("watched-dialog").close();
  pendingWatchedButton?.focus();
  pendingWatchedButton = null;
}

function bindEvents() {
  document.querySelectorAll("[data-course-target]").forEach((button) => {
    button.addEventListener("click", () => navigateToCourse(button.dataset.courseTarget));
  });
  document.querySelectorAll("[data-next-course]").forEach((button) => {
    button.addEventListener("click", () => navigateToCourse(button.dataset.nextCourse));
  });
  document.querySelectorAll('input[name="provider"], input[name="model-mode"]').forEach((input) => input.addEventListener("change", updateProvider));
  document.querySelectorAll('input[name="list-source"]').forEach((input) => input.addEventListener("change", updateSource));
  byId("model_override").addEventListener("input", updateProvider);
  byId("skip_profile").addEventListener("change", updateProfileState);
  byId("profile").addEventListener("input", updateWordCount);
  byId("mal_export_file").addEventListener("change", onMalFileSelected);
  byId("upload-clear").addEventListener("click", clearMalFile);
  byId("setup-form").addEventListener("submit", runRecommendations);
  byId("cancel-request").addEventListener("click", cancelActiveRequest);
  byId("start-over").addEventListener("click", startOver);
  byId("recommend-again").addEventListener("click", () => submitRecommendations(true));
  byId("results-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-feedback]");
    if (!button) return;
    if (button.dataset.feedback === "watched") openWatchedDialog(button);
    else saveFeedback(button);
  });
  byId("watched-score-form").addEventListener("submit", submitWatchedScore);
  byId("cancel-watched-score").addEventListener("click", closeWatchedDialog);
  byId("watched-dialog").addEventListener("cancel", () => {
    pendingWatchedButton = null;
  });
  byId("toggle-key").addEventListener("click", () => {
    const key = byId("api_key");
    const reveal = key.classList.contains("masked-credential");
    key.classList.toggle("masked-credential", !reveal);
    byId("toggle-key").textContent = reveal ? "Hide" : "Show";
    byId("toggle-key").setAttribute("aria-pressed", String(reveal));
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  updateProvider();
  updateSource();
  updateProfileState();
  showCourse("model", false);
  loadAccountSession();
});
