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
  try {
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
  } catch {
    status.textContent = "Could not save";
  } finally {
    button.disabled = false;
  }
}

function providerLabel(provider) {
  const option = [...document.querySelectorAll("#account-key-provider option")]
    .find((item) => item.value === provider);
  return option?.textContent || provider;
}

function refreshProviderKeyEmptyState() {
  const list = document.querySelector("#provider-key-list");
  const empty = document.querySelector("#provider-key-empty");
  if (!list || !empty) return;
  const hasKeys = Boolean(list.querySelector("[data-saved-provider]"));
  list.hidden = !hasKeys;
  empty.hidden = hasKeys;
}

function upsertProviderKey(provider, hint) {
  const list = document.querySelector("#provider-key-list");
  if (!list) return;
  let row = list.querySelector(`[data-saved-provider="${CSS.escape(provider)}"]`);
  if (!row) {
    row = document.createElement("li");
    row.dataset.savedProvider = provider;
    const details = document.createElement("div");
    details.append(document.createElement("strong"), document.createElement("small"));
    const forget = document.createElement("button");
    forget.type = "button";
    forget.className = "text-button";
    forget.dataset.forgetProvider = provider;
    forget.textContent = "Forget key";
    row.append(details, forget);
    list.appendChild(row);
  }
  row.querySelector("strong").textContent = providerLabel(provider);
  row.querySelector("small").textContent = `Saved · ending ${hint}`;
  refreshProviderKeyEmptyState();
}

async function saveProviderKey(button) {
  const provider = document.querySelector("#account-key-provider")?.value || "";
  const credential = document.querySelector("#account-provider-credential");
  const status = document.querySelector("#provider-key-status");
  const value = credential?.value.trim() || "";
  if (!value) {
    status.textContent = "Paste a provider key first.";
    credential?.focus();
    return;
  }
  button.disabled = true;
  status.textContent = "Encrypting…";
  try {
    const response = await fetch(`/api/account/provider-keys/${encodeURIComponent(provider)}`, {
      method: "PUT",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({ provider_key: value }),
    });
    const result = await readJson(response);
    if (!response.ok) throw new Error(result.detail || "Could not save this key.");
    credential.value = "";
    upsertProviderKey(result.provider, result.hint);
    status.textContent = `${providerLabel(result.provider)} key saved and encrypted.`;
  } catch (error) {
    status.textContent = error.message || "Could not save this key.";
  } finally {
    button.disabled = false;
  }
}

async function forgetProviderKey(button) {
  const provider = button.dataset.forgetProvider;
  if (!window.confirm(`Forget the saved ${providerLabel(provider)} key?`)) return;
  const status = document.querySelector("#provider-key-status");
  button.disabled = true;
  try {
    const response = await fetch(`/api/account/provider-keys/${encodeURIComponent(provider)}`, {
      method: "DELETE",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": csrfToken },
    });
    const result = await readJson(response);
    if (!response.ok) throw new Error(result.detail || "Could not forget this key.");
    button.closest("[data-saved-provider]")?.remove();
    refreshProviderKeyEmptyState();
    status.textContent = `${providerLabel(provider)} key forgotten.`;
  } catch (error) {
    status.textContent = error.message || "Could not forget this key.";
    button.disabled = false;
  }
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

function prepareInviteForm() {
  const form = document.querySelector("#invite-form");
  const tokenField = document.querySelector("#invite-token");
  if (!form || !tokenField) return;
  if (window.location.hash) {
    try {
      tokenField.value = decodeURIComponent(window.location.hash.slice(1));
    } catch {
      tokenField.value = "";
    }
    window.history.replaceState(null, "", window.location.pathname);
  }
  form.addEventListener("submit", (event) => {
    if (tokenField.value) return;
    event.preventDefault();
    document.querySelector("#invite-client-error").hidden = false;
  });
}

document.addEventListener("click", async (event) => {
  const profile = event.target.closest("#save-profile");
  if (profile) {
    await saveProfile(profile);
    return;
  }

  const saveKey = event.target.closest("#save-provider-key");
  if (saveKey) {
    await saveProviderKey(saveKey);
    return;
  }

  const forgetKey = event.target.closest("[data-forget-provider]");
  if (forgetKey) {
    await forgetProviderKey(forgetKey);
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

prepareInviteForm();
