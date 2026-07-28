"use strict";

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
    if (!byId("api_key").value.trim()) {
      showError("Paste the key for the provider you selected.", "model");
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
  for (const course of COURSE_ORDER) {
    if (!validateCourse(course)) return;
  }

  requestController = new AbortController();
  setLoading(true);
  byId("results").hidden = true;
  try {
    if (!accountSessionLoaded) await loadAccountSession();
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
      if (recommendation.url) {
        const link = createText("a", "recommendation__link", "Open anime page ↗");
        link.href = recommendation.url;
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
          button.setAttribute("aria-pressed", String(recommendation.feedback_state === state));
          actions.appendChild(button);
        });
        article.appendChild(actions);
      }
      list.appendChild(article);
    });
  }

  results.hidden = false;
  results.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

async function saveFeedback(button) {
  if (!accountSession.authenticated) return;
  const state = button.dataset.feedback;
  const response = await fetch(`/api/account/recommendations/${button.dataset.recommendationId}/feedback`, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": accountSession.csrf_token,
    },
    body: JSON.stringify({ state }),
  });
  const data = await readApiResponse(response);
  if (!response.ok) {
    showError(data.detail || "That preference could not be saved.", "taste");
    return;
  }
  button.parentElement.querySelectorAll("[data-feedback]").forEach((item) => {
    item.setAttribute("aria-pressed", String(item === button));
  });
}

async function loadAccountSession() {
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
    byId("privacy-receipt-copy").innerHTML = "<strong>Your key remains request-local.</strong> Your Lite account saves taste notes, completed recommendations, and your feedback—but never the model key.";
    byId("history-privacy-copy").textContent = "Your history and notes go to that provider for this request. Completed picks and your feedback are saved to your Lite account.";
    if (accountSession.taste_profile) {
      byId("profile").value = accountSession.taste_profile;
      updateWordCount();
    }
  }
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
  byId("counter").scrollIntoView({ behavior: "smooth", block: "start" });
  byId("api_key").focus({ preventScroll: true });
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
  byId("results-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-feedback]");
    if (button) saveFeedback(button);
  });
  byId("toggle-key").addEventListener("click", () => {
    const key = byId("api_key");
    const reveal = key.type === "password";
    key.type = reveal ? "text" : "password";
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
