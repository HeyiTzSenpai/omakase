"use strict";

const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

async function saveProfile(button) {
  const status = document.querySelector("#profile-status");
  button.disabled = true;
  const response = await fetch("/api/account/profile", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify({
      taste_profile: document.querySelector("#account-profile").value,
    }),
  });
  status.textContent = response.ok ? "Saved" : "Could not save";
  button.disabled = false;
}

function showInvite(target, inviteUrl) {
  const label = document.createElement("label");
  label.append("One-time invite");
  const input = document.createElement("input");
  input.readOnly = true;
  input.value = inviteUrl;
  label.appendChild(input);
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "copy-invite";
  copy.textContent = "Copy";
  target.replaceChildren(label, copy);
}

async function decideRequest(button, action, requestId) {
  const row = button.closest(".request-row");
  const target = row.querySelector(".invite-result");
  button.disabled = true;
  const body = new FormData();
  body.set("csrf_token", csrfToken);
  const response = await fetch(
    `/account/admin/requests/${encodeURIComponent(requestId)}/${action}`,
    { method: "POST", body, credentials: "same-origin" },
  );
  const result = await readJson(response);
  if (response.ok && result.invite_url) {
    showInvite(target, result.invite_url);
    row.querySelector(".status").textContent = "approved";
  } else if (response.ok) {
    target.textContent = "Declined";
    row.querySelector(".status").textContent = "declined";
  } else {
    target.textContent = result.detail || "Could not update this request.";
    button.disabled = false;
  }
}

document.addEventListener("click", async (event) => {
  const profile = event.target.closest("#save-profile");
  if (profile) {
    await saveProfile(profile);
    return;
  }

  const approve = event.target.closest("[data-approve]");
  const decline = event.target.closest("[data-decline]");
  if (approve || decline) {
    const button = approve || decline;
    const action = approve ? "approve" : "decline";
    await decideRequest(button, action, button.dataset[action]);
    return;
  }

  const copy = event.target.closest(".copy-invite");
  if (copy) {
    const input = copy.parentElement.querySelector("input");
    await navigator.clipboard.writeText(input.value);
    copy.textContent = "Copied";
  }
});
