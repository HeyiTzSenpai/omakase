(() => {
  let loadingTimer = null;
  let loadingStart = 0;

  const byId = (id) => document.getElementById(id);

  function esc(value) {
    const node = document.createElement("div");
    node.textContent = value || "";
    return node.innerHTML;
  }

  function intOrNull(value) {
    const parsed = parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function tickLoading() {
    const elapsed = Math.floor((Date.now() - loadingStart) / 1000);
    const elapsedNode = byId("loading-elapsed");
    const textNode = byId("loading-text");
    if (elapsedNode) elapsedNode.textContent = `${elapsed}s`;
    if (!textNode) return;

    const phases = [
      { at: 0, text: "Asking the LLM to pair your tasting menu..." },
      { at: 15, text: "Still thinking. Pro mode takes longer." },
      { at: 45, text: "Reasoning models can take 1-2 minutes..." },
      { at: 90, text: "Still working. Try Fast mode next time if this is too slow." },
    ];
    let phase = phases[0];
    for (const nextPhase of phases) {
      if (elapsed >= nextPhase.at) phase = nextPhase;
    }
    textNode.textContent = phase.text;
  }

  function renderResults(data) {
    const container = byId("results-list");
    if (!container || !data || !Array.isArray(data.recommendations)) return;
    container.innerHTML = "";
    for (const rec of data.recommendations) {
      const score = Number(rec.predicted_score);
      let scoreClass = "score-mid";
      if (score >= 8) scoreClass = "score-high";
      else if (score < 6) scoreClass = "score-low";

      container.insertAdjacentHTML(
        "beforeend",
        `<article class="rec-card">` +
          `<div class="rec-card-top"><h3 class="rec-title">${esc(rec.title)}</h3>` +
          `<span class="score ${scoreClass}">${esc(String(rec.predicted_score))}/10</span></div>` +
          `<p class="reasoning">${esc(rec.reasoning)}</p>` +
          (rec.best_match_from_history
            ? `<p class="match">Pairs with: <strong>${esc(rec.best_match_from_history)}</strong></p>`
            : "") +
          `</article>`,
      );
    }
  }

  function setLoading(active) {
    const button = byId("run-btn");
    const loading = byId("loading");
    if (button) {
      button.disabled = active;
      button.setAttribute("aria-busy", active ? "true" : "false");
    }
    if (loading) loading.hidden = !active;
    if (active) {
      loadingStart = Date.now();
      loadingTimer = window.setInterval(tickLoading, 1000);
      tickLoading();
    } else if (loadingTimer) {
      window.clearInterval(loadingTimer);
      loadingTimer = null;
    }
  }

  function showRunError(message) {
    const error = byId("run-error");
    setLoading(false);
    if (!error) return;
    error.textContent = message;
    error.hidden = false;
  }

  async function runRecs(event) {
    event.preventDefault();
    const error = byId("run-error");
    if (error) error.hidden = true;
    setLoading(true);

    const selectedLane = document.querySelector('input[name="lane"]:checked');
    const lane = selectedLane ? selectedLane.value : "best_match";
    const body = JSON.stringify({
      source: byId("run-source").value,
      username: byId("run-username").value,
      mode: byId("run-mode").value,
      count: parseInt(byId("run-count").value, 10),
      temperature: parseFloat(byId("run-temp").value),
      model: byId("run-model").value,
      lane,
      use_planning: lane === "plan_list" || byId("run-planning").checked,
      skip_profile: byId("run-skip").checked,
    });

    let job;
    try {
      const response = await fetch("/plus/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      job = await response.json();
    } catch (errorStart) {
      showRunError(`Network error starting run: ${errorStart.message}`);
      return;
    }

    if (!job || job.status !== "running" || !job.job_id) {
      showRunError(`Could not start run: ${(job && job.detail) || "unexpected response"}`);
      return;
    }

    const deadline = Date.now() + 10 * 60 * 1000;
    const poll = async () => {
      if (Date.now() > deadline) {
        showRunError("Run is taking unusually long. Refresh the page to check for results.");
        return;
      }

      let data;
      try {
        const response = await fetch(`/plus/api/run/status/${encodeURIComponent(job.job_id)}`);
        data = await response.json();
      } catch {
        window.setTimeout(poll, 3000);
        return;
      }

      if (data.status === "running") {
        window.setTimeout(poll, 3000);
        return;
      }

      if (data.status === "ok") {
        renderResults(data);
        window.location.href = `/plus/dashboard?run=${data.run_id}`;
        return;
      }

      showRunError(`Recommendation failed: ${data.detail || "unknown error"}`);
    };

    poll();
  }

  function setupRunForm() {
    const form = byId("run-form");
    if (form) form.addEventListener("submit", runRecs);
  }

  function setupFeedback() {
    document.querySelectorAll(".btn-feedback").forEach((button) => {
      button.addEventListener("click", async () => {
        const group = button.closest(".feedback-actions");
        if (!group) return;
        const original = button.textContent;
        button.disabled = true;
        try {
          const response = await fetch("/plus/api/feedback", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              feedback_type: button.dataset.feedback,
              title: group.dataset.title,
              source: group.dataset.source || "anilist",
              media_id: intOrNull(group.dataset.mediaId),
              run_id: intOrNull(group.dataset.runId),
            }),
          });
          const data = await response.json();
          if (!response.ok || !data || data.status !== "ok") throw new Error();
          button.textContent = "Saved";
        } catch {
          button.disabled = false;
          button.textContent = original;
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    setupRunForm();
    setupFeedback();
  });

  window.runRecs = runRecs;
})();
