"use strict";

const state = { bootstrap: null, surface: null, controller: null, assistantNode: null };
const $ = (selector) => document.querySelector(selector);
const text = (tag, value, className) => {
  const node = document.createElement(tag);
  node.textContent = value == null ? "" : String(value);
  if (className) node.className = className;
  return node;
};

function currentSurface() {
  const parts = location.pathname.split("/").filter(Boolean);
  if (parts[1] === "quests" && parts[2]) return `quest:${parts[2]}`;
  const value = parts[1];
  return value || "today";
}

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof body.detail === "string" ? body.detail : "That view is unavailable.");
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

function renderNavigation(items) {
  const nav = $("#primary-nav");
  nav.replaceChildren();
  const active = currentSurface();
  for (const item of items) {
    const link = text("a", item.label, "nav-link");
    link.href = item.href;
    if (item.id === active) link.setAttribute("aria-current", "page");
    nav.append(link);
  }
}

function combinedNavigation(value) {
  const standard = [...value.navigation];
  for (const quest of value.temporary_surfaces || []) {
    standard.push({ id: `quest:${quest.stable_id}`, label: quest.title, href: quest.href });
  }
  return standard;
}

function card(title, items, emptyCopy = "Nothing needs your attention here.") {
  const section = document.createElement("section");
  section.className = "card";
  section.append(text("h2", title));
  if (!Array.isArray(items) || items.length === 0) {
    section.append(text("p", emptyCopy, "empty"));
    return section;
  }
  const list = document.createElement("ul");
  list.className = "item-list";
  for (const item of items.slice(0, 20)) {
    const li = document.createElement("li");
    li.className = "item";
    li.append(text("strong", item.title || item.display_name || item.description || item.text || item.name || item.kind || "Household item"));
    const status = item.status || item.state;
    const meta = text("span", status || item.occurred_at || item.due_at || item.due_date || "Ready", "meta");
    if (status) meta.classList.add("state-label", `state-${String(status).replaceAll("_", "-")}`);
    li.append(meta);
    if (item.reason) li.append(text("small", item.reason));
    if (item.last_event_id) {
      const button = text("button", "View evidence", "link-button");
      button.type = "button";
      button.addEventListener("click", () => showEvidence(item.last_event_id));
      li.append(button);
    }
    list.append(li);
  }
  section.append(list);
  return section;
}

function rankedItems(value) {
  if (!value) return [];
  if (Array.isArray(value.items)) return value.items;
  if (Array.isArray(value.ranked)) return value.ranked;
  if (value.action) return [value.action];
  if (value.horizons) {
    return ["right_now", "next", "today", "this_week", "this_month", "someday"]
      .map((key) => value.horizons[key])
      .filter(Boolean);
  }
  return [];
}

function whatNowCard(value, mode) {
  const ranked = rankedItems(value);
  if (mode === "compressed") return card("Decision queue", ranked, "Your queue is clear. Leave it that way for a minute.");
  const item = ranked[0];
  const section = document.createElement("section");
  section.className = "hero-card";
  section.append(text("p", "ONE SMALL MOVE", "eyebrow"));
  if (!item) {
    section.append(text("h2", "Nothing urgent is asking for you.", "action-title"));
    section.append(text("p", "You can pause, or choose something gently on purpose."));
    return section;
  }
  section.append(text("h2", item.title || item.description || "Take the next small step", "action-title"));
  section.append(text("p", item.micro_step || "Open it, look once, and decide only the first move.", "microstep"));
  section.append(text("p", [item.energy, item.effort, item.context].filter(Boolean).join(" · ") || "Low decision load", "meta"));
  const progress = document.createElement("div");
  progress.className = "progress-track";
  progress.setAttribute("role", "progressbar");
  progress.setAttribute("aria-label", "Starting progress");
  progress.setAttribute("aria-valuenow", "34"); progress.setAttribute("aria-valuemin", "0"); progress.setAttribute("aria-valuemax", "100");
  progress.append(document.createElement("span"));
  section.append(progress);
  if (item.last_event_id && item.id) {
    const row = document.createElement("div"); row.className = "button-row";
    for (const action of ["complete", "defer", "snooze", "block"]) {
      const button = text("button", action[0].toUpperCase() + action.slice(1), action === "complete" ? "button button-primary" : "button button-quiet");
      button.type = "button";
      button.addEventListener("click", () => advanceAction(item, action, section));
      row.append(button);
    }
    section.append(row);
  }
  return section;
}

async function advanceAction(item, action, section) {
  const buttons = section.querySelectorAll("button"); buttons.forEach((button) => { button.disabled = true; });
  const banner = $("#state-banner"); banner.hidden = false; banner.textContent = "Nice—recording that outcome and finding the next small step…";
  const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
  try {
    const result = await api("/api/member/v1/lifecycle", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        item_kind: item.kind, item_id: item.id, action,
        expected_last_event_id: item.last_event_id, request_id: crypto.randomUUID(),
        until: ["defer", "snooze"].includes(action) ? tomorrow : null,
        reason: action === "block" ? "Paused from Today; needs one unblock condition." : null,
        note: action === "complete" ? "Completed from the member portal" : null,
      }),
    });
    const next = result.next_action || result.what_now;
    banner.textContent = action === "complete" ? "Recorded. Here’s what fits now." : "Updated. Here’s the recalculated next move.";
    section.replaceWith(whatNowCard(next, state.surface?.mode || state.bootstrap.mode));
  } catch (error) {
    banner.textContent = `${error.message} Nothing was implied as complete.`;
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function renderSurface(data) {
  state.surface = data;
  const root = $("#surface");
  root.setAttribute("aria-busy", "false");
  root.replaceChildren();
  $("#page-title").textContent = data.label;
  $("#mode-label").textContent = `${data.mode} · ${data.member_id}`;
  if (data.surface === "today") {
    root.append(whatNowCard(data.what_now, data.mode), card("Open lists", data.lists, "No open list items."));
  } else if (data.surface === "inbox") {
    const grid = document.createElement("div"); grid.className = "card-grid";
    grid.append(card("Needs review", data.review), card("Open work", data.tasks)); root.append(grid);
  } else if (data.surface === "people") {
    root.append(card("People", data.people, "No people are visible in this scope."), card("Recent contact", data.recent_interactions));
  } else if (data.surface === "knowledge") {
    root.append(card("Private knowledge", data.knowledge), card("Capture evidence", data.artifacts), card("Lists", data.lists));
  } else if (data.surface === "finance") {
    root.append(card("Recent money movement", data.flows, "Finance is connected, with no visible movement in this period."));
  } else if (data.surface === "calendar") {
    root.append(card("This week", data.events, "The calendar is connected and this week is open."));
  } else if (data.surface === "scoreboard") {
    const score = data.scoreboard || {};
    const hero = document.createElement("section"); hero.className = "hero-card";
    hero.append(text("p", "YOUR RHYTHM", "eyebrow"), text("h2", `${score.open_count || 0} gentle next steps`, "action-title"), text("p", score.reward_language === "warm" ? "Every small finish counts. You’re building momentum." : "Progress is evidence, not pressure."));
    root.append(hero, card("Up next", score.next_steps));
  } else if (data.surface === "settings") {
    root.append(card("Capability health", data.capabilities), card("Principal workbooks", data.workbooks), card("Chat transport", [data.chat]));
  }
}

function renderQuest(data) {
  const root = $("#surface"); root.setAttribute("aria-busy", "false"); root.replaceChildren();
  $("#page-title").textContent = data.surface.title;
  $("#mode-label").textContent = `${data.mode} · temporary lens`;
  if (data.state === "degraded") {
    const unavailable = document.createElement("section"); unavailable.className = "state-card";
    unavailable.append(text("h2", "This lens is waiting on a capability."), text("p", "The underlying household records are unchanged."));
    root.append(unavailable); return;
  }
  const intro = document.createElement("section"); intro.className = "hero-card";
  intro.append(text("p", "PRIVATE QUEST", "eyebrow"), text("h2", data.surface.title, "action-title"), text("p", data.surface.purpose));
  root.append(intro);
  for (const result of data.results) {
    const value = result.value;
    if (result.query === "what_now") root.append(whatNowCard(value, data.mode));
    else root.append(card(result.query.replace(".", " · "), Array.isArray(value) ? value : rankedItems(value)));
  }
  const note = document.createElement("section"); note.className = "state-card";
  note.append(text("strong", "Disposable view, stable household truth"), text("p", "Archiving this lens removes only its navigation entry. It never deletes or copies the records shown here."));
  root.append(note);
}

function renderFailure(error) {
  const root = $("#surface"); root.setAttribute("aria-busy", "false"); root.replaceChildren();
  const box = document.createElement("section"); box.className = "state-card";
  box.append(text("h2", error.status === 404 ? "This view is not available to this member." : "This view is temporarily unavailable."));
  box.append(text("p", "Your household data has not been changed. Try again after checking capability health."));
  root.append(box);
}

async function showEvidence(eventId) {
  const dialog = $("#evidence-dialog"); const content = $("#evidence-content");
  content.replaceChildren(text("p", "Loading evidence…")); dialog.showModal();
  try {
    const value = await api(`/api/member/v1/evidence/${encodeURIComponent(eventId)}`);
    const dl = document.createElement("dl"); dl.className = "evidence-grid";
    const fields = [
      ["Event", `${value.type} · ${value.event_id}`], ["Source", `${value.source.adapter} / ${value.source.account}`],
      ["Actor", value.actor || "System"], ["Occurred", value.occurred_at], ["Ingested", value.ingested_at],
      ["Confidence", value.confidence ?? "Not asserted"], ["Scope", `${value.scope.owner} · ${value.scope.sensitivity}`],
      ["Why", value.why.causation_id || value.why.correlation_id || "Direct observation"],
      ["Correction", value.correction || "No correction recorded"], ["Approval", value.approval || "Not applicable"],
      ["Delivery", value.delivery || "No delivery claimed"],
    ];
    for (const [label, result] of fields) { dl.append(text("dt", label), text("dd", result)); }
    content.replaceChildren(dl);
  } catch (error) { content.replaceChildren(text("p", error.message)); }
}

function appendMessage(role, value) {
  const item = text("li", value, `message ${role}`);
  $("#chat-messages").append(item);
  item.scrollIntoView({ block: "nearest" });
  return item;
}

async function loadChatHistory() {
  if (!state.bootstrap.chat.enabled || state.bootstrap.chat.state !== "healthy") return;
  try {
    const value = await api("/api/member/v1/chat/history");
    for (const item of value.items) appendMessage(item.role, item.text);
  } catch { $("#chat-state").textContent = "Conversation history is temporarily unavailable."; }
}

async function sendChat(event) {
  event.preventDefault();
  const input = $("#chat-input"); const message = input.value.trim();
  if (!message || state.controller) return;
  input.value = ""; appendMessage("user", message); state.assistantNode = appendMessage("assistant", "");
  state.controller = new AbortController(); $("#chat-stop").hidden = false; $("#chat-send").disabled = true;
  $("#chat-state").textContent = "Preparing context…";
  try {
    const response = await fetch("/api/member/v1/chat/stream", {
      method: "POST", credentials: "same-origin", cache: "no-store", signal: state.controller.signal,
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({ message, request_id: crypto.randomUUID() }),
    });
    if (!response.ok || !response.body) throw new Error("Chat is temporarily unavailable.");
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
    while (true) {
      const part = await reader.read(); if (part.done) break; buffer += decoder.decode(part.value, { stream: true });
      const frames = buffer.split("\n\n"); buffer = frames.pop() || "";
      for (const frame of frames) {
        const line = frame.split("\n").find((entry) => entry.startsWith("data: ")); if (!line) continue;
        const value = JSON.parse(line.slice(6));
        if (value.kind === "status") $("#chat-state").textContent = value.phase === "starting_model" ? "Thinking with OpenClaw…" : "Preparing context…";
        if (value.kind === "delta") state.assistantNode.textContent = value.replace ? value.text : state.assistantNode.textContent + value.text;
        if (value.kind === "final") $("#chat-state").textContent = "Ready";
        if (value.kind === "error") $("#chat-state").textContent = "The model connection ended. Your household views are still available.";
        if (value.kind === "aborted") $("#chat-state").textContent = "Stopped.";
      }
    }
  } catch (error) {
    if (error.name !== "AbortError") $("#chat-state").textContent = "The model connection ended. Your household views are still available.";
  } finally { state.controller = null; state.assistantNode = null; $("#chat-stop").hidden = true; $("#chat-send").disabled = false; }
}

function configureChat(policy) {
  const form = $("#chat-form");
  if (!policy.enabled || policy.state !== "healthy") {
    form.hidden = true;
    const copy = policy.reason === "model_provider_not_configured" ? "Chat is enabled for you, but no model provider is configured in OpenClaw yet." : policy.reason === "guardian_disabled_child_chat" ? "Chat is off for this child profile." : "Chat is not enabled for this member.";
    $("#chat-state").textContent = copy;
    if (!policy.enabled) $("#chat-open").hidden = true;
  } else { form.hidden = false; $("#chat-state").textContent = "OpenClaw owns this private conversation."; }
}

async function initialize() {
  $("#chat-close").addEventListener("click", () => { $("#chat-panel").classList.remove("open"); $("#chat-open").setAttribute("aria-expanded", "false"); });
  $("#chat-open").addEventListener("click", () => { $("#chat-panel").classList.add("open"); $("#chat-open").setAttribute("aria-expanded", "true"); $("#chat-input").focus(); });
  $("#chat-form").addEventListener("submit", sendChat);
  $("#chat-stop").addEventListener("click", async () => { state.controller?.abort(); await fetch("/api/member/v1/chat/abort", { method: "POST", credentials: "same-origin" }); });
  $("[data-close-dialog]").addEventListener("click", () => $("#evidence-dialog").close());
  try {
    state.bootstrap = await api("/api/member/v1/bootstrap"); renderNavigation(combinedNavigation(state.bootstrap)); configureChat(state.bootstrap.chat);
    $("#member-button").textContent = state.bootstrap.member.id.slice(0, 1).toUpperCase();
    $("#observation-pill").textContent = state.bootstrap.observation_mode ? "Observation mode" : "Member-safe";
    const active = currentSurface();
    const surfaceRequest = active.startsWith("quest:")
      ? api(`/api/member/v1/temporary-surfaces/${encodeURIComponent(active.slice(6))}/evaluate`).then(renderQuest)
      : api(`/api/member/v1/surfaces/${encodeURIComponent(active)}`).then(renderSurface);
    await Promise.all([surfaceRequest, loadChatHistory()]);
  } catch (error) { renderFailure(error); }
}

initialize();
