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
  const mode = selectedValue("model-mode") || "fast";
  const provider = PROVIDERS[name];
  byId("llm_type").value = name;
  byId("llm_url").value = provider.url;
  byId("mode").value = mode;
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

async function runRecommendations(event) {
  event.preventDefault();
  for (const course of COURSE_ORDER) {
    if (!validateCourse(course)) return;
  }

  requestController = new AbortController();
  setLoading(true);
  byId("results").hidden = true;
  try {
    const response = await fetch("/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "omit",
      cache: "no-store",
      body: JSON.stringify(buildPayload()),
      signal: requestController.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Omakase could not finish this menu.");
    displayResults(data);
    byId("api_key").value = "";
  } catch (error) {
    if (error.name === "AbortError") showError("The request was cancelled. Nothing was saved.", "taste");
    else showError(error.message || "Omakase could not finish this menu.", "taste");
  } finally {
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
  byId("results-meta").textContent = `${sourceLabel} / ${data.recommendations.length} picks`;

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
      list.appendChild(article);
    });
  }

  results.hidden = false;
  results.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
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
  byId("cancel-request").addEventListener("click", () => requestController?.abort());
  byId("start-over").addEventListener("click", startOver);
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
});
