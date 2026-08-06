"use strict";

const state = {
  bootstrap: null,
  surface: null,
  controller: null,
  assistantNode: null,
  todayView: "brief",
  carouselIndex: 0,
  carouselItems: [],
  triageView: "ready",
  triageSelected: null,
  commsView: "focus",
  commsAccount: "all",
  commsThread: null,
  remindersView: "now",
  remindersAccount: "all",
  remindersList: "all",
  reminderItem: null,
  calendarView: "week",
  calendarAnchor: null,
  calendarToday: null,
  calendarLens: "capacity",
  calendarHiddenSources: [],
  calendarSelectedEvent: null,
  calendarDuration: 60,
};
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

function replaceQuery(values) {
  const params = new URLSearchParams(location.search);
  for (const [key, value] of Object.entries(values)) {
    if (value == null || value === "") params.delete(key);
    else params.set(key, String(value));
  }
  const query = params.toString();
  history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}`);
}

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : body.detail?.reason;
    const error = new Error(detail || "That view is unavailable.");
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

async function reloadActiveSurface() {
  const active = currentSurface();
  if (active.startsWith("quest:")) return;
  let path = `/api/member/v1/surfaces/${encodeURIComponent(active)}`;
  if (active === "calendar") {
    const params = new URLSearchParams({ view: state.calendarView });
    if (state.calendarAnchor) params.set("date", state.calendarAnchor);
    path += `?${params}`;
  }
  renderSurface(await api(path));
}

async function decideTriage(item, action, buttons) {
  buttons.forEach((button) => { button.disabled = true; });
  const banner = $("#state-banner"); banner.hidden = false;
  banner.textContent = action === "approve" ? "Confirming this candidate through the household gate…" : "Dismissing this candidate…";
  try {
    await api(`/api/member/v1/triage/${encodeURIComponent(item.proposal_id)}/${action}`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({ request_id: crypto.randomUUID(), expected_last_event_id: item.last_event_id, reason: action === "dismiss" ? "Dismissed from Triage" : null }),
    });
    banner.textContent = action === "approve" ? "Confirmed. The routed household record is now being materialized." : "Dismissed. No household record or provider write was created.";
    await reloadActiveSurface();
  } catch (error) {
    banner.textContent = `${error.message} Nothing was confirmed or dismissed.`;
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function field(label, control, extraClass = "") {
  const wrapper = document.createElement("label");
  wrapper.className = `field ${extraClass}`.trim();
  wrapper.append(text("span", label), control);
  return wrapper;
}

function selectControl(options, selected, emptyLabel = "Choose…") {
  const select = document.createElement("select");
  if (emptyLabel) {
    const empty = text("option", emptyLabel); empty.value = ""; select.append(empty);
  }
  for (const option of options) {
    const node = text("option", option.label); node.value = option.value;
    if (option.value === selected) node.selected = true;
    select.append(node);
  }
  return select;
}

function replaceSelectOptions(select, options, selected = "", emptyLabel = "Choose…") {
  select.replaceChildren();
  if (emptyLabel) {
    const empty = text("option", emptyLabel); empty.value = ""; select.append(empty);
  }
  for (const option of options) {
    const node = text("option", option.label); node.value = option.value;
    if (option.value === selected) node.selected = true;
    select.append(node);
  }
}

async function saveTriageForm(item, form) {
  const values = new FormData(form);
  const targetKind = String(values.get("target_kind") || item.target_kind);
  const candidateUpdates = {};
  for (const key of ["due", "start", "end", "owed_to_party_id"]) {
    const value = String(values.get(key) || "").trim();
    if (value) candidateUpdates[key] = value;
  }
  let route = null;
  if (item.source_kind !== "commitment_proposal") {
    route = {
      member_id: String(values.get("member_id") || state.bootstrap.member.id),
      provider: String(values.get("provider") || "") || null,
      provider_account_id: String(values.get("provider_account_id") || "") || null,
      container_external_id: String(values.get("container_external_id") || "") || null,
    };
  }
  const buttons = [...form.querySelectorAll("button")];
  buttons.forEach((button) => { button.disabled = true; });
  setBanner("Saving these details as a proposal—nothing official is being created yet…");
  try {
    await api(`/api/member/v1/triage/${encodeURIComponent(item.proposal_id)}/enrich`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        request_id: crypto.randomUUID(),
        expected_last_event_id: item.last_event_id,
        title: String(values.get("title") || item.title).trim(),
        target_kind: targetKind,
        candidate_updates: candidateUpdates,
        route,
      }),
    });
    setBanner("Details saved. Review the destination and preview once more before confirming.");
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} The proposal was not changed.`);
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function triageDetail(item, data, readOnly = false) {
  const section = document.createElement("section"); section.className = "card triage-detail";
  if (!item) {
    section.append(text("h2", "Choose a proposal"), text("p", "Select one item to review its evidence, destination, and exact effect.", "empty"));
    return section;
  }
  section.append(text("p", readOnly ? "RECENT DECISION" : "REVIEW ONE ITEM", "eyebrow"), text("h2", item.title));
  if (item.reasons?.length) section.append(text("p", item.reasons.join(" · "), "meta"));
  if (readOnly) {
    section.append(text("p", `${titleCase(item.state)} ${item.resolved_at ? `on ${new Date(item.resolved_at).toLocaleString()}` : ""}.`, "review-state ready"));
    if (item.materialized_event_ids?.length) section.append(text("p", `${item.materialized_event_ids.length} audited materialization event${item.materialized_event_ids.length === 1 ? "" : "s"}.`, "meta"));
    const evidence = text("button", "View source evidence", "link-button"); evidence.type = "button"; evidence.addEventListener("click", () => showEvidence(item.source_event_id)); section.append(evidence);
    return section;
  }

  const form = document.createElement("form"); form.className = "triage-review-form";
  const grid = document.createElement("div"); grid.className = "field-grid";
  const title = document.createElement("input"); title.name = "title"; title.required = true; title.maxLength = 1000; title.value = item.title || "";
  grid.append(field("Review title", title, "field-span"));
  const targetOptions = ["task", "commitment", "calendar_event", "reminder", "list", "list_item", "party_identifier"].map((value) => ({ value, label: titleCase(value) }));
  const target = selectControl(targetOptions, item.target_kind, ""); target.name = "target_kind"; target.disabled = item.source_kind === "commitment_proposal";
  grid.append(field("Creates", target));
  const members = (data.destinations?.members || []).map((value) => ({ value: value.member_id, label: `${value.label} · ${value.role || "member"}` }));
  const member = selectControl(members, item.route?.member_id || state.bootstrap.member.id, ""); member.name = "member_id"; member.disabled = item.source_kind === "commitment_proposal";
  grid.append(field("Household member", member));

  const candidate = item.candidate || {};
  if (item.target_kind === "commitment") {
    const owed = document.createElement("input"); owed.name = "owed_to_party_id"; owed.value = candidate.owed_to_party_id || ""; owed.placeholder = "Required party ID";
    grid.append(field("Owed to", owed));
  }
  if (item.target_kind === "calendar_event") {
    const start = document.createElement("input"); start.name = "start"; start.value = candidate.start || candidate.due || ""; start.placeholder = "ISO 8601 with time zone";
    const end = document.createElement("input"); end.name = "end"; end.value = candidate.end || ""; end.placeholder = "ISO 8601 with time zone";
    grid.append(field("Starts", start), field("Ends", end));
  } else if (["task", "commitment", "reminder", "list_item"].includes(item.target_kind)) {
    const due = document.createElement("input"); due.name = "due"; due.value = candidate.due || candidate.due_at || ""; due.placeholder = "Optional ISO 8601 date/time";
    grid.append(field("Due", due));
  }

  const external = ["calendar_event", "reminder", "list", "list_item"].includes(item.target_kind);
  if (external) {
    const calendarRoutes = (data.destinations?.calendars || []).map((value) => ({
      provider: value.provider, account: value.provider_account_id, container: value.calendar_external_id,
      label: value.calendar_name || titleCase(value.destination_kind),
    }));
    const reminderRoutes = (data.destinations?.reminder_lists || []).map((value) => ({
      provider: value.provider, account: value.provider_account_id, container: value.list_external_id,
      label: value.list_name || "Reminder list",
    }));
    const routes = item.target_kind === "calendar_event" ? calendarRoutes : reminderRoutes;
    const providers = [...new Set(routes.map((value) => value.provider))].map((value) => ({ value, label: titleCase(value) }));
    const provider = selectControl(providers, item.route?.provider || ""); provider.name = "provider";
    const account = document.createElement("select"); account.name = "provider_account_id";
    const container = document.createElement("select"); container.name = "container_external_id";
    const refreshContainers = (selected = "") => {
      const options = routes.filter((value) => value.provider === provider.value && value.account === account.value).map((value) => ({ value: value.container, label: value.label }));
      replaceSelectOptions(container, options, selected, item.target_kind === "list" ? "Provider default" : "Choose destination…");
    };
    const refreshAccounts = (selectedAccount = "", selectedContainer = "") => {
      const options = [...new Set(routes.filter((value) => value.provider === provider.value).map((value) => value.account))].map((value) => ({ value, label: value }));
      replaceSelectOptions(account, options, selectedAccount, "Choose account…");
      refreshContainers(selectedContainer);
    };
    provider.addEventListener("change", () => refreshAccounts());
    account.addEventListener("change", () => refreshContainers());
    refreshAccounts(item.route?.provider_account_id || "", item.route?.container_external_id || "");
    grid.append(field("Provider", provider), field("Account", account), field(item.target_kind === "calendar_event" ? "Calendar" : "List", container));
  }
  form.append(grid);
  if (item.calendar_preview) {
    const preview = item.calendar_preview;
    const box = document.createElement("section"); box.className = "state-card";
    box.append(text("h3", "Calendar impact preview"));
    if (preview.state === "needs_details") {
      box.append(text("p", `Add ${preview.missing.map(titleCase).join(", ")} to calculate conflicts, buffers, and guardrails.`, "state-note"));
    } else {
      const event = preview.event || {};
      const destination = preview.destination?.calendar_name || preview.destination?.destination_kind;
      box.append(text("p", destination ? `Destination: ${destination}.` : "This is not an approved writable destination.", destination ? "calendar-route-ready" : "calendar-route-missing"));
      box.append(text("p", `Effective load: ${titleCase(event.effect)} · buffer ${event.buffer_before_minutes || 0} min before / ${event.buffer_after_minutes || 0} min after.`, "meta"));
      box.append(text("p", preview.conflicts?.length ? `${preview.conflicts.length} conflict or pressure warning${preview.conflicts.length === 1 ? "" : "s"}: ${preview.conflicts.map((value) => value.why).join(" · ")}` : "No buffered conflict appears in this range.", preview.conflicts?.length ? "calendar-route-missing" : "calendar-route-ready"));
      box.append(text("p", preview.guardrails?.length ? `Overlaps ${preview.guardrails.map((value) => value.label || titleCase(value.kind)).join(", ")}.` : "No household guardrail overlaps this proposal.", "state-note"));
      box.append(text("p", preview.observation?.active ? "Observation mode: confirmation would be audited without changing the provider." : "Confirmation would enter the governed provider-write path.", "meta"));
    }
    form.append(box);
  }
  const missing = item.review_state?.missing || [];
  const review = text("p", missing.length ? `Needs: ${missing.map(titleCase).join(", ")}. Save the missing details before confirming.` : "Ready for your final confirmation. Every real write still passes governance and observation controls.", `review-state ${missing.length ? "" : "ready"}`.trim());
  form.append(review);
  const row = document.createElement("div"); row.className = "button-row";
  const save = text("button", "Save details", "button button-quiet"); save.type = "submit";
  const approve = text("button", "Confirm and route", "button button-primary"); approve.type = "button"; approve.disabled = missing.length > 0;
  const dismiss = text("button", "Dismiss", "button button-quiet"); dismiss.type = "button";
  const evidence = text("button", "View source evidence", "link-button"); evidence.type = "button";
  const buttons = [save, approve, dismiss];
  form.addEventListener("submit", (event) => { event.preventDefault(); saveTriageForm(item, form); });
  approve.addEventListener("click", () => decideTriage(item, "approve", buttons));
  dismiss.addEventListener("click", () => decideTriage(item, "dismiss", buttons));
  evidence.addEventListener("click", () => showEvidence(item.source_event_id));
  row.append(save, approve, dismiss, evidence); form.append(row); section.append(form);
  return section;
}

function triageSurface(data) {
  const fragment = document.createDocumentFragment();
  const intro = document.createElement("section"); intro.className = "hero-card";
  intro.append(text("p", "HUMAN GATE", "eyebrow"), text("h2", "One decision at a time.", "action-title"), text("p", "Derived candidates wait here until you verify their meaning and destination. Raw provider observations never enter this queue."));
  fragment.append(intro);
  const pending = data.items || [];
  const recent = data.recent_items || [];
  const groups = {
    ready: pending.filter((item) => item.review_state?.state === "ready"),
    needs_details: pending.filter((item) => item.review_state?.state !== "ready"),
    recent,
  };
  if (!groups[state.triageView]) state.triageView = "ready";
  const controls = document.createElement("section"); controls.className = "card comms-controls";
  const tabs = document.createElement("div"); tabs.className = "comms-filter-row"; tabs.setAttribute("role", "group"); tabs.setAttribute("aria-label", "Triage views");
  for (const [key, label] of [["ready", "Ready"], ["needs_details", "Needs details"], ["recent", "Recent"]]) {
    const button = text("button", `${label} ${groups[key].length}`, "comms-filter"); button.type = "button"; button.setAttribute("aria-pressed", String(state.triageView === key));
    button.addEventListener("click", () => { state.triageView = key; state.triageSelected = null; replaceQuery({ view: key, item: null }); renderSurface(data); }); tabs.append(button);
  }
  controls.append(tabs); fragment.append(controls);
  const visible = groups[state.triageView];
  if (!state.triageSelected || !visible.some((item) => item.proposal_id === state.triageSelected)) state.triageSelected = visible[0]?.proposal_id || null;
  const selected = visible.find((item) => item.proposal_id === state.triageSelected) || null;
  const workspace = document.createElement("div"); workspace.className = "triage-workspace";
  const master = document.createElement("section"); master.className = "card triage-master"; master.append(text("h2", state.triageView === "recent" ? "Recent decisions" : "Awaiting review"));
  if (!visible.length) master.append(text("p", state.triageView === "ready" ? "Nothing is ready to confirm." : state.triageView === "needs_details" ? "No proposals need more details." : "No recent decisions yet.", "empty"));
  const list = document.createElement("ul"); list.className = "triage-master-list";
  for (const item of visible) {
    const li = document.createElement("li"); const button = document.createElement("button"); button.type = "button"; button.className = "triage-master-button"; button.setAttribute("aria-current", String(item.proposal_id === state.triageSelected));
    button.append(text("strong", item.title), text("small", `${titleCase(item.target_kind)} · ${state.triageView === "recent" ? titleCase(item.state) : titleCase(item.review_state?.state)}`));
    button.addEventListener("click", () => { state.triageSelected = item.proposal_id; replaceQuery({ view: state.triageView, item: item.proposal_id }); renderSurface(data); }); li.append(button); list.append(li);
  }
  master.append(list); workspace.append(master, triageDetail(selected, data, state.triageView === "recent")); fragment.append(workspace); return fragment;
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
  if (item.last_event_id && item.id && ["task", "commitment"].includes(item.kind)) {
    const row = document.createElement("div"); row.className = "button-row";
    for (const action of ["complete", "defer", "block"].filter((value) => (item.actions || []).includes(value))) {
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

function titleCase(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function todayItemMeta(item) {
  return [item.time_label, item.context, item.status ? titleCase(item.status) : null]
    .filter(Boolean)
    .join(" · ");
}

function setBanner(message) {
  const banner = $("#state-banner");
  banner.hidden = false;
  banner.textContent = message;
}

async function executeTodayAction(item, action, container) {
  if (action === "evidence") {
    if (item.evidence_event_id || item.last_event_id) await showEvidence(item.evidence_event_id || item.last_event_id);
    return;
  }
  if (["open_triage", "open_comms", "open_reminder", "open_calendar"].includes(action)) {
    location.href = item.surface_link?.href || ({
      open_triage: "/member/triage",
      open_comms: "/member/comms",
      open_reminder: "/member/reminders",
      open_calendar: "/member/calendar",
    })[action];
    return;
  }
  if (action === "open_chat") {
    $("#chat-open").click();
    return;
  }
  const buttons = container.querySelectorAll("button");
  buttons.forEach((button) => { button.disabled = true; });
  try {
    if (["task", "commitment", "recurrence"].includes(item.kind) && action === "complete") {
      setBanner("Recording that finish and rebuilding the rest of today…");
      await api("/api/member/v1/lifecycle", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({
          item_kind: item.kind,
          item_id: item.id,
          action: "complete",
          expected_last_event_id: item.last_event_id,
          request_id: crypto.randomUUID(),
          note: "Completed from Today",
        }),
      });
      setBanner("Recorded. Today has been recalculated from household truth.");
    } else if (item.kind === "calendar_event" && ["attended", "missed"].includes(action)) {
      setBanner("Recording this calendar outcome without changing the event itself…");
      await api(`/api/member/v1/calendar/${encodeURIComponent(item.id)}/outcome`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({
          request_id: crypto.randomUUID(),
          expected_last_event_id: item.last_event_id,
          outcome: action,
        }),
      });
      setBanner(action === "attended" ? "Attendance recorded." : "Missed event recorded. Nothing else was inferred.");
    } else if (item.kind === "routine" && ["complete_step", "skip_step"].includes(action)) {
      const step = item.current_step;
      setBanner(action === "complete_step" ? "Recording this routine step…" : "Skipping only this step and keeping the routine moving…");
      await api(`/api/member/v1/routines/${encodeURIComponent(item.id)}/occurrences/${encodeURIComponent(item.occurrence_date)}/steps/${encodeURIComponent(step.step_id)}/outcome`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({
          request_id: crypto.randomUUID(),
          expected_last_event_id: item.last_event_id,
          outcome: action === "complete_step" ? "completed" : "skipped",
        }),
      });
      setBanner(action === "complete_step" ? "Step recorded. Here is the next one." : "Step skipped without changing any linked task.");
    } else if (item.kind === "list_item" && action === "complete_external") {
      setBanner("Requesting completion through the reminder provider…");
      await api("/api/member/v1/reminders/commands", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({
          provider: item.provider,
          provider_account_id: item.provider_account_id,
          operation: item.provider === "google_tasks" ? "complete_task" : "complete_reminder",
          list_external_id: item.list_external_id,
          item_external_id: item.item_external_id,
          expected_last_event_id: item.last_event_id,
          expected_provider_revision: item.provider_revision,
          payload: {},
          request_id: crypto.randomUUID(),
          openclaw_approved: true,
        }),
      });
      setBanner("Completion requested. It will leave Today after the provider confirms it.");
    }
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} Nothing was implied as complete.`);
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function actionButton(item, action, container) {
  const labels = {
    complete: "Done",
    complete_external: "Done",
    complete_step: "Step done",
    skip_step: "Skip step",
    attended: "Attended",
    missed: "Missed",
    open_triage: "Review",
    open_comms: "Open conversation",
    open_reminder: "Review copies",
    open_calendar: "Open calendar",
    open_chat: "Talk it through",
    evidence: "Why this is here",
  };
  const primary = ["complete", "complete_external", "complete_step", "attended", "open_triage"].includes(action);
  const button = text("button", labels[action] || titleCase(action), action === "evidence" ? "link-button" : `button ${primary ? "button-primary" : "button-quiet"}`);
  button.type = "button";
  button.addEventListener("click", () => executeTodayAction(item, action, container));
  return button;
}

function possibleCopySources(item, compact = false) {
  const copies = item.possible_copy?.items || [];
  if (!copies.length) return null;
  const box = document.createElement(compact ? "small" : "section");
  box.className = "possible-copy possible-copy-sources";
  box.append(text("strong", `${copies.length} possible provider copies are grouped only in this view. `));
  const links = document.createElement("span");
  copies.forEach((copy, index) => {
    if (index) links.append(document.createTextNode(" · "));
    const anchor = text("a", copy.list_name || `Source ${index + 1}`);
    anchor.href = copy.surface_link?.href || "/member/reminders";
    links.append(anchor);
  });
  box.append(links);
  return box;
}

function todayItem(item, { interactive = true } = {}) {
  const li = document.createElement("li");
  li.className = `day-item day-item-${String(item.kind).replaceAll("_", "-")}`;
  const body = document.createElement("div");
  body.className = "day-item-body";
  body.append(text("strong", item.title || "Household item"));
  const meta = todayItemMeta(item);
  if (meta) body.append(text("span", meta, "meta"));
  if (item.micro_step) body.append(text("small", item.micro_step));
  const copies = possibleCopySources(item, true); if (copies) body.append(copies);
  li.append(body);
  if (interactive) {
    const row = document.createElement("div");
    row.className = "day-item-actions";
    const actions = (item.actions || []).filter((action) => ["complete", "complete_external", "complete_step", "skip_step", "attended", "missed", "open_triage", "open_comms", "open_reminder", "open_calendar", "open_chat", "evidence"].includes(action));
    for (const action of actions) row.append(actionButton(item, action, li));
    if (actions.length) li.append(row);
  }
  return li;
}

function daySection(title, items, emptyCopy) {
  const section = document.createElement("section");
  section.className = "card day-section";
  const heading = document.createElement("div");
  heading.className = "section-heading";
  heading.append(text("h2", title), text("span", `${items.length}`, "count-badge"));
  section.append(heading);
  if (!items.length) {
    section.append(text("p", emptyCopy, "empty compact-empty"));
    return section;
  }
  const list = document.createElement("ol");
  list.className = "day-list";
  for (const item of items) list.append(todayItem(item));
  section.append(list);
  return section;
}

function todayViewControls(data) {
  const controls = document.createElement("div");
  controls.className = "today-controls";
  const switcher = document.createElement("div");
  switcher.className = "view-switcher";
  switcher.setAttribute("role", "group");
  switcher.setAttribute("aria-label", "Today view");
  for (const [view, label] of [["brief", "Day Brief"], ["carousel", "Carousel"]]) {
    const button = text("button", label, "view-button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.todayView === view));
    button.addEventListener("click", () => {
      state.todayView = view;
      replaceQuery({ view });
      if (view === "carousel") state.carouselIndex = 0;
      renderToday(data);
    });
    switcher.append(button);
  }
  const whatNow = text("button", "What Now?", "button button-primary what-now-button");
  whatNow.type = "button";
  whatNow.addEventListener("click", () => {
    const first = data.brief?.carousel?.recommended_item || data.brief?.what_now?.horizons?.right_now;
    const match = state.carouselItems.findIndex((item) => item.kind === first?.kind && item.id === first?.id);
    state.carouselIndex = match >= 0 ? match : 0;
    state.todayView = "carousel";
    renderToday(data);
    $(".carousel-card")?.focus();
  });
  controls.append(switcher, whatNow);
  return controls;
}

function briefHero(brief) {
  const section = document.createElement("section");
  section.className = "hero-card day-hero";
  section.append(
    text("p", `${brief.daypart} briefing`, "eyebrow"),
    text("h2", "Here is the shape of your day.", "action-title"),
    text("p", brief.summary_text, "brief-summary"),
  );
  if (brief.next_event) {
    const next = document.createElement("div");
    next.className = "next-event";
    next.append(text("span", "NEXT ON THE CLOCK", "meta"), text("strong", brief.next_event.title), text("span", brief.next_event.time_label, "meta"));
    section.append(next);
  }
  const stats = document.createElement("dl");
  stats.className = "brief-stats";
  for (const [label, value] of [["Timed", brief.stats.timed_events ?? brief.stats.events], ["Day context", brief.stats.day_context || 0], ["Moves", brief.stats.moves], ["Decisions", brief.stats.questions]]) {
    stats.append(text("div", "", "stat"));
    stats.lastElementChild.append(text("dt", label), text("dd", value));
  }
  section.append(stats);
  return section;
}

function todayCalendarGuidance(brief) {
  const guidance = brief.calendar_guidance || {};
  const section = document.createElement("section");
  section.className = "card day-calendar-guidance";
  section.append(text("h2", "Time guardrails"));
  const current = guidance.current_guardrail;
  if (current) {
    const currentRow = document.createElement("p");
    currentRow.append(text("strong", current.label || titleCase(current.kind)));
    const effect = current.effect === "hard_block" ? "Protected" : titleCase(current.effect);
    currentRow.append(text("span", ` · ${effect} until ${new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(current.end_at))}`, "meta"));
    section.append(currentRow);
    if (current.effect === "context") section.append(text("p", "Context only — it does not block your time.", "state-note"));
  }
  const now = new Date();
  const opening = (guidance.open_windows || []).find((window) => new Date(window.end_at) > now);
  if (opening) {
    const start = new Date(opening.start_at);
    const end = new Date(opening.end_at);
    const isOpen = start <= now;
    section.append(text("p", isOpen ? `Open now until ${new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(end)}.` : `Next opening: ${calendarTime(start)}–${new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(end)}.`, "calendar-open-now"));
  }
  const softLoad = Number(guidance.counts?.soft_hold || 0);
  if (softLoad) section.append(text("p", `${softLoad} soft-load ${softLoad === 1 ? "item is" : "items are"} visible. It warns without silently blocking your day.`, "state-note"));
  if (!current && !opening && !softLoad) section.append(text("p", "No calendar guardrail needs attention right now.", "empty compact-empty"));
  return section;
}

function renderDayBrief(brief) {
  const fragment = document.createDocumentFragment();
  fragment.append(briefHero(brief), todayCalendarGuidance(brief));
  const grid = document.createElement("div");
  grid.className = "brief-grid";
  grid.append(
    daySection("On the clock", brief.timed_schedule || [], "No timed events are on the calendar today."),
    daySection("Moves for today", brief.plan || [], "No planned moves are due today."),
  );
  fragment.append(grid);
  if ((brief.day_context || []).length) fragment.append(daySection("Day context", brief.day_context, "No all-day context."));
  if ((brief.questions || []).length || (brief.messages || []).length) {
    const attention = document.createElement("div");
    attention.className = "brief-grid";
    attention.append(
      daySection("Needs a decision", brief.questions || [], "No clarifying decisions are waiting."),
      daySection("Messages to notice", brief.messages || [], "No message needs a response right now."),
    );
    fragment.append(attention);
  }
  return fragment;
}

function renderCarousel(brief) {
  const items = state.carouselItems;
  const section = document.createElement("section");
  section.className = "hero-card carousel-card";
  section.tabIndex = -1;
  if (!items.length) {
    section.append(text("p", "WHAT NOW", "eyebrow"), text("h2", "You are caught up for now.", "action-title"), text("p", "Pause, or choose something gently on purpose."));
    return section;
  }
  state.carouselIndex = Math.min(Math.max(state.carouselIndex, 0), items.length - 1);
  const item = items[state.carouselIndex];
  section.append(
    text("p", `${titleCase(item.kind)} · ${state.carouselIndex + 1} of ${items.length}`, "eyebrow"),
    text("h2", item.title || "Take the next small step", "action-title"),
    text("p", item.micro_step || "Open it and decide only the first move.", "microstep"),
  );
  const meta = todayItemMeta(item);
  if (meta) section.append(text("p", meta, "meta carousel-meta"));
  const copies = possibleCopySources(item); if (copies) section.append(copies);
  const progress = document.createElement("div");
  progress.className = "progress-track carousel-progress";
  progress.setAttribute("role", "progressbar");
  progress.setAttribute("aria-label", "Carousel position");
  progress.setAttribute("aria-valuenow", String(state.carouselIndex + 1));
  progress.setAttribute("aria-valuemin", "1");
  progress.setAttribute("aria-valuemax", String(items.length));
  const fill = document.createElement("span");
  fill.style.width = `${((state.carouselIndex + 1) / items.length) * 100}%`;
  progress.append(fill);
  section.append(progress);
  const row = document.createElement("div");
  row.className = "button-row carousel-actions";
  for (const action of (item.actions || []).filter((value) => ["complete", "complete_external", "complete_step", "skip_step", "attended", "missed", "open_triage", "open_comms", "open_reminder", "open_calendar", "open_chat"].includes(value))) {
    row.append(actionButton(item, action, section));
  }
  if (items.length > 1) {
    const later = text("button", "Later", "button button-quiet");
    later.type = "button";
    later.title = "Move to the next card without changing this item's due date or status.";
    later.addEventListener("click", () => {
      state.carouselIndex = (state.carouselIndex + 1) % items.length;
      renderToday(state.surface);
    });
    row.append(later);
  }
  if (item.evidence_event_id || item.last_event_id) row.append(actionButton(item, "evidence", section));
  section.append(row);
  if (items.length > 1) {
    const navigation = document.createElement("div");
    navigation.className = "carousel-navigation";
    const back = text("button", "← Previous", "link-button");
    back.type = "button";
    back.addEventListener("click", () => {
      state.carouselIndex = (state.carouselIndex - 1 + items.length) % items.length;
      renderToday(state.surface);
    });
    navigation.append(back, text("small", "Later only changes this view. Defer changes household truth and is always explicit."));
    section.append(navigation);
  }
  return section;
}

function renderToday(data) {
  const root = $("#surface");
  const brief = data.brief;
  root.replaceChildren();
  root.append(freshnessStrip(data));
  $("#mode-label").textContent = brief?.date_label || "Your daily briefing";
  root.append(todayViewControls(data));
  root.append(state.todayView === "carousel" ? renderCarousel(brief) : renderDayBrief(brief));
}

function commsMatches(thread) {
  const view = state.commsView;
  const viewMatch = view === "all"
    || (view === "focus" && ["focus", "needs_reply"].includes(thread.bucket))
    || (view === "needs_reply" && ["focus", "needs_reply"].includes(thread.bucket))
    || thread.bucket === view;
  const accountMatch = state.commsAccount === "all" || thread.provider_account_id === state.commsAccount;
  return viewMatch && accountMatch;
}

function commsControls(cockpit, data) {
  const controls = document.createElement("section");
  controls.className = "comms-controls card";
  const views = document.createElement("div");
  views.className = "comms-filter-row";
  views.setAttribute("role", "group");
  views.setAttribute("aria-label", "Communication focus view");
  const choices = [
    ["focus", "Focus", cockpit.counts.focus],
    ["waiting", "Waiting", cockpit.counts.waiting],
    ["snoozed", "Snoozed", cockpit.counts.snoozed],
    ["quieted", "Quieted", cockpit.counts.quieted],
    ["all", "All", cockpit.counts.all],
  ];
  for (const [value, label, count] of choices) {
    const button = text("button", `${label} ${count}`, "comms-filter");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.commsView === value));
    button.addEventListener("click", () => { state.commsView = value; replaceQuery({ view: value }); renderComms(data); });
    views.append(button);
  }
  const accounts = document.createElement("div");
  accounts.className = "comms-filter-row account-filters";
  accounts.setAttribute("role", "group");
  accounts.setAttribute("aria-label", "Communication account");
  for (const account of [{ provider_account_id: "all", label: "All accounts", workspace: "" }, ...(cockpit.accounts || [])]) {
    const button = text("button", `${account.label}${account.workspace ? ` · ${titleCase(account.workspace)}` : ""}`, "account-filter");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.commsAccount === account.provider_account_id));
    button.addEventListener("click", () => { state.commsAccount = account.provider_account_id; replaceQuery({ account: state.commsAccount }); renderComms(data); });
    accounts.append(button);
  }
  controls.append(views, accounts);
  return controls;
}

function commsHero(cockpit) {
  const hero = document.createElement("section");
  hero.className = "hero-card comms-hero";
  hero.append(
    text("p", "COMMUNICATION COCKPIT", "eyebrow"),
    text("h2", cockpit.counts.focus === 1 ? "1 conversation deserves a look." : cockpit.counts.focus ? `${cockpit.counts.focus} conversations deserve a look.` : "No conversation is pulling on your working memory.", "action-title"),
    text("p", "Focus uses typed action signals—not unread status—to protect your attention. Messages stay in their original member, workspace, provider, and privacy boundary.", "brief-summary"),
  );
  const stats = document.createElement("dl");
  stats.className = "brief-stats comms-stats";
  for (const [label, value] of [["Needs reply", cockpit.counts.needs_reply], ["Waiting", cockpit.counts.waiting], ["To Triage", cockpit.counts.triage], ["Quieted", cockpit.counts.quieted]]) {
    const stat = text("div", "", "stat"); stat.append(text("dt", label), text("dd", value)); stats.append(stat);
  }
  hero.append(stats);
  return hero;
}

async function runCommsCommand(thread, operation, extras = {}) {
  setBanner(operation === "reply" ? "Sending through the governed provider gate…" : "Updating this conversation…");
  try {
    const result = await api(`/api/member/v1/comms/${encodeURIComponent(thread.last_interaction_id)}/commands`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        operation,
        expected_last_event_id: thread.last_event_id,
        request_id: crypto.randomUUID(),
        openclaw_approved: false,
        ...extras,
      }),
    });
    setBanner(result.local ? "Updated. The focus view has been recalculated." : result.observation_mode ? "Approved and audited. Observation mode suppressed the external change." : "Approved. Waiting for the provider result.");
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} Nothing was changed.`);
  }
}

function messageBubble(message) {
  const item = document.createElement("li");
  item.className = `comms-message ${message.direction === "outbound" ? "outbound" : "inbound"}`;
  const people = message.direction === "outbound" ? `To ${message.to.join(", ") || "recipient"}` : `From ${message.from.join(", ") || "sender"}`;
  const timestamp = message.occurred_at ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(message.occurred_at)) : "";
  item.append(text("span", `${people}${timestamp ? ` · ${timestamp}` : ""}`, "meta"));
  if (message.subject) item.append(text("strong", message.subject));
  item.append(text("p", message.body || message.preview));
  if (message.attachments?.length) {
    const attachments = document.createElement("ul"); attachments.className = "attachment-list";
    for (const attachment of message.attachments) {
      const size = attachment.size ? ` · ${Math.max(1, Math.round(attachment.size / 1024))} KB` : "";
      attachments.append(text("li", `${attachment.filename || "Attachment"}${size}`));
    }
    item.append(attachments);
  }
  if (message.evidence_event_ids?.[0]) {
    const evidence = text("button", "Why this is here", "link-button"); evidence.type = "button";
    evidence.addEventListener("click", () => showEvidence(message.evidence_event_ids[0])); item.append(evidence);
  }
  return item;
}

function commsReadingPane(thread) {
  const pane = document.createElement("section");
  pane.className = "card comms-reading";
  if (!thread) {
    pane.append(text("h2", "Choose one conversation"), text("p", "The combined view carries the remembering. You only need to look at one thread at a time.", "empty"));
    return pane;
  }
  const heading = document.createElement("div"); heading.className = "comms-reading-heading";
  const title = document.createElement("div");
  title.append(text("p", `${thread.account_label} · ${titleCase(thread.channel)}`, "eyebrow"), text("h2", thread.title));
  const badge = text("span", titleCase(thread.bucket), `pill ${thread.bucket === "focus" ? "pill-blush" : "pill-lavender"}`);
  heading.append(title, badge); pane.append(heading);
  const why = document.createElement("p"); why.className = "focus-reason"; why.append(text("strong", "Why it is here: "), document.createTextNode(thread.why.join(" · "))); pane.append(why);
  if (thread.command_state) {
    const labels = {
      pending: "Provider change requested; awaiting a terminal observation.",
      suppressed: "Observation mode recorded the approved change without contacting the provider.",
      completed: "Provider reported the requested change complete.",
      failed: "Provider change failed; the original message state remains authoritative.",
    };
    pane.append(text("p", labels[thread.command_state] || `Command state: ${titleCase(thread.command_state)}.`, `state-label state-${String(thread.command_state).replaceAll("_", "-")}`));
  }
  if (thread.triage_count) {
    const triage = text("a", `${thread.triage_count} extracted candidate${thread.triage_count === 1 ? "" : "s"} awaiting Triage`, "triage-link"); triage.href = "/member/triage"; pane.append(triage);
  }
  const messages = document.createElement("ol"); messages.className = "comms-transcript";
  for (const message of thread.messages) messages.append(messageBubble(message)); pane.append(messages);
  const actions = document.createElement("div"); actions.className = "button-row comms-actions";
  const latest = thread.messages[thread.messages.length - 1];
  if (thread.capabilities.mark_read) {
    const operation = latest.unread ? "mark_read" : "mark_unread";
    const button = text("button", latest.unread ? "Mark read" : "Mark unread", "button button-quiet"); button.type = "button";
    button.addEventListener("click", () => runCommsCommand(thread, operation, { openclaw_approved: true })); actions.append(button);
  }
  if (thread.capabilities.archive) {
    const operation = thread.archived ? "restore" : "archive";
    const button = text("button", thread.archived ? "Restore" : "Archive", "button button-quiet"); button.type = "button";
    button.addEventListener("click", () => runCommsCommand(thread, operation, { openclaw_approved: true })); actions.append(button);
  }
  const snooze = text("button", thread.bucket === "snoozed" ? "Unsnooze" : "Snooze 2h", "button button-quiet"); snooze.type = "button";
  snooze.addEventListener("click", () => runCommsCommand(thread, thread.bucket === "snoozed" ? "unsnooze" : "snooze", thread.bucket === "snoozed" ? {} : { snoozed_until: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString() })); actions.append(snooze);
  const quiet = text("button", thread.quieted ? "Restore to focus" : "Quiet this", "button button-quiet"); quiet.type = "button";
  quiet.addEventListener("click", () => runCommsCommand(thread, thread.quieted ? "unquiet" : "quiet")); actions.append(quiet);
  pane.append(actions);
  const compose = document.createElement("form"); compose.className = "comms-compose";
  compose.append(text("label", "Reply in this thread"));
  const input = document.createElement("textarea"); input.rows = 4; input.maxLength = 20000; input.placeholder = "Write the reply you intend to send…"; compose.append(input);
  if (thread.capabilities.reply) {
    const approvalLabel = document.createElement("label"); approvalLabel.className = "approval-check";
    const approval = document.createElement("input"); approval.type = "checkbox";
    approvalLabel.append(approval, document.createTextNode(" Approve the independent OpenClaw delivery gate for this exact reply"));
    const send = text("button", "Send reply", "button button-primary"); send.type = "submit";
    compose.append(approvalLabel, send);
    compose.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!approval.checked) { setBanner("Check the OpenClaw delivery approval before sending. Nothing was sent."); return; }
      runCommsCommand(thread, "reply", { body: input.value.trim(), openclaw_approved: true });
    });
  } else {
    input.disabled = true;
    compose.append(text("p", "This account is connected read-only. Reconnect it with provider write access to reply or manage messages here.", "state-note"));
  }
  pane.append(compose);
  return pane;
}

function commsThreadList(threads) {
  const list = document.createElement("section"); list.className = "card comms-list";
  const heading = document.createElement("div"); heading.className = "section-heading"; heading.append(text("h2", "Conversations"), text("span", threads.length, "count-badge")); list.append(heading);
  if (!threads.length) { list.append(text("p", "Nothing in this lens. Try All, another account, or enjoy the quiet.", "empty")); return list; }
  const items = document.createElement("ol"); items.className = "comms-thread-items";
  for (const thread of threads) {
    const li = document.createElement("li");
    const button = document.createElement("button"); button.type = "button"; button.className = "comms-thread";
    if (thread.thread_key === state.commsThread) button.setAttribute("aria-current", "true");
    const first = document.createElement("span"); first.className = "comms-thread-title"; first.append(text("strong", thread.title), text("span", thread.account_label, "meta"));
    const count = thread.unread_count ? text("span", thread.unread_count, "unread-badge") : text("span", titleCase(thread.bucket), "meta");
    button.append(first, count, text("span", thread.preview, "thread-preview"), text("span", thread.why[0], "focus-explanation"));
    button.addEventListener("click", () => { state.commsThread = thread.thread_key; replaceQuery({ thread: thread.thread_key }); renderComms(state.surface); });
    li.append(button); items.append(li);
  }
  list.append(items); return list;
}

function renderComms(data) {
  const root = $("#surface"); root.replaceChildren();
  const cockpit = data.cockpit;
  $("#mode-label").textContent = "Private · deterministic focus";
  root.append(freshnessStrip(data), commsHero(cockpit), commsControls(cockpit, data));
  const visible = cockpit.threads.filter(commsMatches);
  if (!state.commsThread || !visible.some((thread) => thread.thread_key === state.commsThread)) state.commsThread = visible[0]?.thread_key || null;
  const selected = visible.find((thread) => thread.thread_key === state.commsThread);
  const workspace = document.createElement("div"); workspace.className = "comms-workspace";
  workspace.append(commsThreadList(visible), commsReadingPane(selected)); root.append(workspace);
  if (!(cockpit.accounts || []).length) {
    const setup = document.createElement("section"); setup.className = "state-card";
    setup.append(text("strong", "No member-bound communication account is connected."), text("p", "Connect Gmail, Outlook, a trusted Messages bridge, Twilio SMS, or WhatsApp in Settings. Unbound accounts are deliberately excluded to prevent cross-member leakage.")); root.append(setup);
  }
}

function reminderOperation(item, stem) {
  const suffix = item.provider === "google_tasks" ? "task" : "reminder";
  return `${stem}_${suffix}`;
}

async function runReminderCommand(item, operation, payload = {}) {
  setBanner("Sending one explicit change through both approval gates…");
  try {
    const result = await api("/api/member/v1/reminders/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        provider: item.provider,
        provider_account_id: item.provider_account_id,
        operation,
        list_external_id: item.list_external_id || null,
        item_external_id: item.item_external_id || null,
        expected_last_event_id: item.last_event_id || null,
        expected_provider_revision: item.provider_revision || null,
        payload,
        request_id: crypto.randomUUID(),
        openclaw_approved: true,
      }),
    });
    setBanner(result.observation_mode ? "Approved and audited. Observation mode suppressed the provider change." : "Approved. The provider result will update this view when observed.");
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} Nothing was changed or implied.`);
  }
}

function openActionDialog(title, content) {
  const dialog = $("#action-dialog");
  $("#action-title").textContent = title;
  $("#action-content").replaceChildren(content);
  dialog.showModal();
  content.querySelector("input, select, textarea, button")?.focus();
}

function closeActionDialog() { $("#action-dialog").close(); }

function configureReminderCue(item) {
  const form = document.createElement("form"); form.className = "dialog-form";
  const signalKey = document.createElement("input"); signalKey.required = true; signalKey.placeholder = "presence:home or weather:rain";
  const source = selectControl(["home_assistant", "member_bridge", "weather", "device", "network", "calendar", "member_profile", "manual"].map((value) => ({ value, label: titleCase(value) })), "home_assistant", "");
  const kind = selectControl(["location", "presence", "weather", "device_state", "network_presence", "calendar_transition", "energy", "capacity"].map((value) => ({ value, label: titleCase(value) })), "presence", "");
  const operator = selectControl(["equals", "not_equals", "above", "below", "entered", "left", "present"].map((value) => ({ value, label: titleCase(value) })), "equals", "");
  const expected = document.createElement("input"); expected.value = "present"; expected.placeholder = "State or numeric threshold";
  const note = text("p", "This compares one fresh, scoped signal with one explicit operator. It does not change provider reminder truth.", "state-note");
  const submit = text("button", "Save cue", "button button-primary"); submit.type = "submit";
  form.append(field("Signal key", signalKey), field("Source", source), field("Signal kind", kind), field("Match rule", operator), field("Expected value", expected), note, submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); submit.disabled = true; setBanner("Saving a deterministic cue. This will not change the source reminder…");
    try {
      await api("/api/member/v1/reminders/cues", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({
          cue_id: crypto.randomUUID(), list_external_id: item.list_external_id,
          item_external_id: item.item_external_id, expected_last_event_id: item.last_event_id,
          signal_key: signalKey.value.trim(), source: source.value, kind: kind.value, operator: operator.value,
          expected_state: ["equals", "not_equals"].includes(operator.value) ? expected.value : null,
          expected_number: ["above", "below"].includes(operator.value) ? Number(expected.value) : null,
          enabled: true,
        }),
      });
      closeActionDialog(); setBanner("Cue saved. It activates only when a fresh authorized signal matches."); await reloadActiveSurface();
    } catch (error) { setBanner(`${error.message} No cue was created.`); submit.disabled = false; }
  });
  openActionDialog("Add a context cue", form);
}

function editReminderDialog(item) {
  const form = document.createElement("form"); form.className = "dialog-form";
  const title = document.createElement("input"); title.required = true; title.maxLength = 1000; title.value = item.title;
  const submit = text("button", "Save title", "button button-primary"); submit.type = "submit";
  form.append(field("Reminder title", title), submit);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runReminderCommand(item, reminderOperation(item, "update"), { title: title.value.trim() }); });
  openActionDialog("Edit provider reminder", form);
}

function moveReminderDialog(item, cockpit) {
  const candidates = (cockpit.lists || []).filter((list) => list.provider === item.provider && list.provider_account_id === item.provider_account_id && list.list_external_id !== item.list_external_id);
  const form = document.createElement("form"); form.className = "dialog-form";
  const destination = selectControl(candidates.map((list) => ({ value: list.list_external_id, label: list.list_name })), candidates[0]?.list_external_id || "", candidates.length ? "" : "No other list available"); destination.required = true;
  const submit = text("button", "Move reminder", "button button-primary"); submit.type = "submit"; submit.disabled = !candidates.length;
  form.append(field("Destination list", destination), submit);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runReminderCommand(item, reminderOperation(item, "move"), { destination_list_external_id: destination.value }); });
  openActionDialog("Move provider reminder", form);
}

function deleteReminderDialog(item) {
  const wrapper = document.createElement("div"); wrapper.className = "dialog-form";
  wrapper.append(text("p", `Delete “${item.title}” from ${item.account_label}? This is a real provider action after approval; it is suppressed while observation mode is active.`));
  const row = document.createElement("div"); row.className = "button-row";
  const cancel = text("button", "Cancel", "button button-quiet"); cancel.type = "button"; cancel.addEventListener("click", closeActionDialog);
  const confirm = text("button", "Delete from provider", "button button-primary"); confirm.type = "button"; confirm.addEventListener("click", async () => { closeActionDialog(); await runReminderCommand(item, reminderOperation(item, "delete")); });
  row.append(cancel, confirm); wrapper.append(row); openActionDialog("Confirm deletion", wrapper);
}

function reminderMatches(item) {
  return item.bucket === state.remindersView
    && (state.remindersAccount === "all" || item.provider_account_id === state.remindersAccount)
    && (state.remindersList === "all" || item.list_external_id === state.remindersList);
}

function remindersHero(cockpit) {
  const hero = document.createElement("section"); hero.className = "hero-card reminders-hero";
  const nowCount = cockpit.counts.now || 0;
  hero.append(
    text("p", "REMINDERS COCKPIT", "eyebrow"),
    text("h2", nowCount ? `${nowCount} reminder${nowCount === 1 ? " is" : "s are"} active now.` : "Nothing is asking to be remembered right now.", "action-title"),
    text("p", "Time, place, and context cues are deterministic lenses over provider-owned reminders. A reminder never silently becomes a task, promise, or calendar event.", "brief-summary"),
  );
  const stats = document.createElement("dl"); stats.className = "brief-stats reminder-stats";
  for (const [label, value] of [["Now", cockpit.counts.now], ["Scheduled", cockpit.counts.scheduled], ["Places", cockpit.counts.places], ["Anytime", cockpit.counts.anytime]]) {
    const stat = text("div", "", "stat"); stat.append(text("dt", label), text("dd", value || 0)); stats.append(stat);
  }
  hero.append(stats); return hero;
}

function remindersControls(cockpit, data) {
  const controls = document.createElement("section"); controls.className = "card reminder-controls";
  const views = document.createElement("div"); views.className = "comms-filter-row"; views.setAttribute("role", "group"); views.setAttribute("aria-label", "Reminder activation lens");
  for (const [value, label] of [["now", "Now"], ["scheduled", "Scheduled"], ["places", "Places"], ["anytime", "Anytime"], ["completed", "Completed"]]) {
    const button = text("button", `${label} ${cockpit.counts[value] || 0}`, "comms-filter"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.remindersView === value));
    button.addEventListener("click", () => { state.remindersView = value; replaceQuery({ view: value }); renderReminders(data); }); views.append(button);
  }
  const accounts = document.createElement("div"); accounts.className = "comms-filter-row account-filters";
  for (const account of [{ provider_account_id: "all", label: "All accounts" }, ...(cockpit.accounts || [])]) {
    const button = text("button", account.label, "account-filter"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.remindersAccount === account.provider_account_id));
    button.addEventListener("click", () => { state.remindersAccount = account.provider_account_id; state.remindersList = "all"; replaceQuery({ account: state.remindersAccount, list: null }); renderReminders(data); }); accounts.append(button);
  }
  const lists = document.createElement("div"); lists.className = "comms-filter-row account-filters";
  for (const list of [{ list_external_id: "all", list_name: "All lists" }, ...(cockpit.lists || []).filter((row) => state.remindersAccount === "all" || row.provider_account_id === state.remindersAccount)]) {
    const button = text("button", list.list_name, "account-filter"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.remindersList === list.list_external_id));
    button.addEventListener("click", () => { state.remindersList = list.list_external_id; replaceQuery({ list: state.remindersList }); renderReminders(data); }); lists.append(button);
  }
  controls.append(views, accounts, lists); return controls;
}

function reminderItemButton(item) {
  const button = document.createElement("button"); button.type = "button"; button.className = "reminder-item";
  if (state.reminderItem === item.id) button.setAttribute("aria-current", "true");
  const heading = document.createElement("span"); heading.className = "reminder-item-heading";
  heading.append(text("strong", item.title), text("span", `${item.account_label} · ${item.list_name}`, "meta"));
  button.append(heading, text("span", item.why?.[0] || "Available", "focus-explanation"));
  if (item.due_at) button.append(text("span", new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(item.due_at)), "meta"));
  button.addEventListener("click", () => { state.reminderItem = item.id; replaceQuery({ item: item.id }); renderReminders(state.surface); });
  return button;
}

function reminderDetail(item, cockpit) {
  const pane = document.createElement("section"); pane.className = "card reminder-detail";
  if (!item) { pane.append(text("h2", "Choose one reminder"), text("p", "The cockpit carries the remembering. You only need to decide about one item at a time.", "empty")); return pane; }
  pane.append(text("p", `${item.account_label} · ${item.list_name}`, "eyebrow"), text("h2", item.title));
  if (item.notes) pane.append(text("p", item.notes));
  const why = document.createElement("p"); why.className = "focus-reason"; why.append(text("strong", "Why it is here: "), document.createTextNode((item.why || []).join(" · "))); pane.append(why);
  const facts = document.createElement("dl"); facts.className = "reminder-facts";
  const fields = [["Provider", item.account_label], ["Scheduled", item.due_at ? new Intl.DateTimeFormat(undefined, { dateStyle: "full" }).format(new Date(item.due_at)) : "No date"], ["Priority", item.priority === 1 ? "High" : item.priority === 5 ? "Medium" : item.priority === 9 ? "Low" : "None"], ["Repeats", item.recurrence_rules?.length ? item.recurrence_rules.join(", ") : "No"], ["Context cues", item.cues?.length || 0]];
  for (const [label, value] of fields) facts.append(text("dt", label), text("dd", value)); pane.append(facts);
  if (item.read_only_reason) pane.append(text("p", item.read_only_reason, "state-note"));
  if (item.possible_copy) pane.append(text("p", `${item.possible_copy.count} possible copies are preserved across provider lists. Review each source before changing or completing anything.`, "possible-copy"));
  const actions = document.createElement("div"); actions.className = "button-row";
  if (item.can_complete || item.completed) {
    const stem = item.completed ? "uncomplete" : "complete"; const button = text("button", item.completed ? "Restore" : "Done", "button button-primary"); button.type = "button";
    button.addEventListener("click", () => runReminderCommand(item, reminderOperation(item, stem))); actions.append(button);
  }
  if (item.can_edit) {
    const edit = text("button", "Edit title", "button button-quiet"); edit.type = "button";
    edit.addEventListener("click", () => editReminderDialog(item)); actions.append(edit);
    const move = text("button", "Move", "button button-quiet"); move.type = "button";
    move.addEventListener("click", () => moveReminderDialog(item, cockpit)); actions.append(move);
    const remove = text("button", "Delete", "button button-quiet"); remove.type = "button";
    remove.addEventListener("click", () => deleteReminderDialog(item)); actions.append(remove);
  }
  const cue = text("button", "Add context cue", "button button-quiet"); cue.type = "button";
  cue.addEventListener("click", () => configureReminderCue(item)); actions.append(cue);
  pane.append(actions);
  if (item.source_links?.length) { const links = document.createElement("div"); links.className = "source-links"; for (const link of item.source_links) { const anchor = text("a", link.description || "Open source", "link-button"); anchor.href = link.url; anchor.target = "_blank"; anchor.rel = "noreferrer"; links.append(anchor); } pane.append(links); }
  return pane;
}

function reminderQuickAdd(cockpit) {
  const cardNode = document.createElement("section"); cardNode.className = "card reminder-quick-add"; cardNode.append(text("h2", "Quick add"), text("p", "Capture it now; enrich it only if useful.", "state-note"));
  const writable = (cockpit.accounts || []).filter((account) => account.state !== "read_only" && account.operations?.some((operation) => ["create_task", "create_reminder"].includes(operation)));
  if (!writable.length) { cardNode.append(text("p", "Connect a writable Apple Reminders or Google Tasks account in Settings.", "empty")); return cardNode; }
  const form = document.createElement("form"); form.className = "reminder-add-form";
  const title = document.createElement("input"); title.required = true; title.maxLength = 1000; title.placeholder = "What should not be forgotten?"; title.setAttribute("aria-label", "Reminder title");
  const accountSelect = document.createElement("select"); accountSelect.setAttribute("aria-label", "Reminder account");
  for (const account of writable) { const option = document.createElement("option"); option.value = `${account.provider}|${account.provider_account_id}`; option.textContent = account.label; accountSelect.append(option); }
  const listSelect = document.createElement("select"); listSelect.setAttribute("aria-label", "Reminder list");
  const refreshLists = () => { listSelect.replaceChildren(); const [provider, accountId] = accountSelect.value.split("|"); for (const list of (cockpit.lists || []).filter((row) => row.provider === provider && row.provider_account_id === accountId)) { const option = document.createElement("option"); option.value = list.list_external_id; option.textContent = list.list_name; listSelect.append(option); } };
  accountSelect.addEventListener("change", refreshLists); refreshLists();
  const due = document.createElement("input"); due.type = "date"; due.setAttribute("aria-label", "Scheduled date (optional)");
  const submit = text("button", "Add reminder", "button button-primary"); submit.type = "submit"; form.append(title, accountSelect, listSelect, due, submit);
  form.addEventListener("submit", (event) => { event.preventDefault(); const [provider, providerAccountId] = accountSelect.value.split("|"); runReminderCommand({ provider, provider_account_id: providerAccountId, list_external_id: listSelect.value }, provider === "google_tasks" ? "create_task" : "create_reminder", { title: title.value.trim(), ...(due.value ? { due: due.value } : {}) }); });
  cardNode.append(form); return cardNode;
}

function reminderProviderTruth(cockpit) {
  const section = document.createElement("section"); section.className = "card provider-truth"; section.append(text("h2", "What each provider can really do"));
  for (const account of cockpit.accounts || []) { const details = document.createElement("details"); const summary = text("summary", `${account.label} · ${titleCase(account.state)}`); details.append(summary, text("p", `Native: ${(account.native || []).map(titleCase).join(", ")}.`)); for (const limit of account.limitations || []) details.append(text("p", limit, "state-note")); section.append(details); }
  if (!(cockpit.accounts || []).length) section.append(text("p", "No reminder account is connected yet.", "empty")); return section;
}

function calendarTime(value, options = {}) {
  if (!value) return "";
  const timeZone = options.timeZone || state.surface?.cockpit?.timezone;
  return new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "numeric", minute: "2-digit", ...(timeZone ? { timeZone } : {}), ...options }).format(new Date(value));
}

function calendarLocalDate(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function calendarDate(value) {
  if (value instanceof Date) return value;
  const [year, month, day] = String(value).slice(0, 10).split("-").map(Number);
  return new Date(year, month - 1, day, 12, 0, 0);
}

function calendarZonedParts(value, timeZone = state.surface?.cockpit?.timezone) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(value instanceof Date ? value : new Date(value));
  return Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, Number(part.value)]));
}

function calendarDateKey(value, timeZone = state.surface?.cockpit?.timezone) {
  const parts = calendarZonedParts(value, timeZone);
  return `${parts.year}-${String(parts.month).padStart(2, "0")}-${String(parts.day).padStart(2, "0")}`;
}

function calendarHour(value, timeZone = state.surface?.cockpit?.timezone) {
  const parts = calendarZonedParts(value, timeZone);
  return parts.hour + (parts.minute / 60);
}

function calendarSourceKey(source) {
  return `${source.provider || ""}|${source.provider_account_id || ""}|${source.calendar_external_id || ""}`;
}

function calendarEffectLabel(effect) {
  return ({ hard_block: "Hard block", soft_hold: "Soft load", context: "Context", ignore: "Ignored" })[effect] || titleCase(effect);
}

function calendarSources(cockpit) {
  const rules = new Map((cockpit.rules || []).map((rule) => [calendarSourceKey(rule), rule]));
  return (cockpit.sources || []).map((source) => ({ ...source, rule: rules.get(calendarSourceKey(source)) || {} }));
}

function calendarSourceColor(source, cockpit) {
  const palette = ["#2d736e", "#8d5b53", "#7b659f", "#a5762e", "#4f6f8b", "#8b5e79", "#55774d", "#846a54"];
  const keys = calendarSources(cockpit).map(calendarSourceKey).sort();
  return palette[Math.max(0, keys.indexOf(calendarSourceKey(source))) % palette.length];
}

function calendarEventIsVisible(event) {
  const routes = event.source_routes?.length ? event.source_routes : [event];
  return routes.some((route) => !state.calendarHiddenSources.includes(calendarSourceKey(route)));
}

function calendarEventOccursOn(event, dateKey, timeZone = state.surface?.cockpit?.timezone) {
  const startKey = calendarDateKey(event.start_at, timeZone);
  const endKey = calendarDateKey(event.end_at, timeZone);
  const endsAtMidnight = calendarHour(event.end_at, timeZone) === 0;
  return startKey <= dateKey && (event.all_day || endsAtMidnight ? dateKey < endKey : dateKey <= endKey);
}

function calendarRangeDates(cockpit) {
  const dates = [];
  const cursor = calendarDate(cockpit.range?.start_at);
  const end = calendarDate(cockpit.range?.end_at);
  while (cursor < end) {
    dates.push(new Date(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return dates;
}

async function setCalendarView(view, anchor = state.calendarAnchor) {
  state.calendarView = view;
  state.calendarAnchor = anchor || state.calendarToday || calendarLocalDate();
  state.calendarSelectedEvent = null;
  replaceQuery({ view, date: state.calendarAnchor, event: null });
  await reloadActiveSurface();
}

function shiftCalendar(direction) {
  const anchor = calendarDate(state.calendarAnchor || state.calendarToday || calendarLocalDate());
  if (state.calendarView === "month") anchor.setMonth(anchor.getMonth() + direction, 1);
  else {
    const amount = state.calendarView === "day" ? 1 : state.calendarView === "week" ? 7 : 14;
    anchor.setDate(anchor.getDate() + (amount * direction));
  }
  setCalendarView(state.calendarView, calendarLocalDate(anchor));
}

function calendarRangeTitle(cockpit) {
  const anchor = calendarDate(state.calendarAnchor || state.calendarToday || calendarLocalDate());
  if (state.calendarView === "rules") return "Calendar policy";
  if (state.calendarView === "day") return new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" }).format(anchor);
  if (state.calendarView === "month") return new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(anchor);
  const dates = calendarRangeDates(cockpit);
  if (!dates.length) return "Calendar";
  const first = dates[0]; const last = dates.at(-1);
  const firstMonth = new Intl.DateTimeFormat(undefined, { month: "short" }).format(first);
  const lastMonth = new Intl.DateTimeFormat(undefined, { month: "short" }).format(last);
  const year = last.getFullYear();
  return first.getMonth() === last.getMonth()
    ? `${firstMonth} ${first.getDate()}–${last.getDate()}, ${year}`
    : `${firstMonth} ${first.getDate()} – ${lastMonth} ${last.getDate()}, ${year}`;
}

function calendarHero(cockpit) {
  const section = document.createElement("section"); section.className = "card calendar-hero";
  const counts = cockpit.counts || {}; const onboarding = cockpit.onboarding || {};
  const copy = document.createElement("div"); copy.className = "calendar-hero-copy";
  copy.append(text("p", "CALENDAR REASONING", "eyebrow"), text("h2", "One schedule, explicit rules.", "action-title"), text("p", `${onboarding.source_count || 0} connected calendars are composed without merging provider truth. View filters never change availability or write routes.`, "state-note"));
  const stats = document.createElement("dl"); stats.className = "calendar-stats";
  for (const [label, value, kind] of [["Hard", counts.hard_block, "hard"], ["Soft load", counts.soft_hold, "soft"], ["Context", counts.context, "context"], ["Conflicts", counts.conflicts, "conflict"]]) { const node = document.createElement("div"); node.className = `calendar-stat calendar-stat-${kind}`; node.append(text("dt", label), text("dd", value || 0)); stats.append(node); }
  section.append(copy, stats); return section;
}

function calendarControls(cockpit) {
  const controls = document.createElement("section"); controls.className = "card calendar-commandbar";
  const dateControls = document.createElement("div"); dateControls.className = "calendar-date-controls";
  const today = text("button", "Today", "button button-quiet calendar-today"); today.type = "button"; today.addEventListener("click", () => setCalendarView(state.calendarView === "rules" ? "week" : state.calendarView, state.calendarToday || calendarLocalDate()));
  const previous = text("button", "‹", "calendar-step"); previous.type = "button"; previous.setAttribute("aria-label", "Previous date range"); previous.addEventListener("click", () => shiftCalendar(-1));
  const next = text("button", "›", "calendar-step"); next.type = "button"; next.setAttribute("aria-label", "Next date range"); next.addEventListener("click", () => shiftCalendar(1));
  const title = text("h2", calendarRangeTitle(cockpit), "calendar-range-title");
  dateControls.append(today, previous, next, title);
  const switcher = document.createElement("div"); switcher.className = "view-switcher calendar-view-switcher";
  for (const [id, label] of [["day", "Day"], ["week", "Week"], ["month", "Month"], ["agenda", "Agenda"], ["find", "Find time"]]) { const button = text("button", label, "view-button"); button.type = "button"; button.setAttribute("aria-pressed", String(state.calendarView === id)); button.addEventListener("click", () => setCalendarView(id)); switcher.append(button); }
  const settings = text("button", "Calendar settings", "button calendar-settings-button"); settings.type = "button"; settings.setAttribute("aria-pressed", String(state.calendarView === "rules")); settings.addEventListener("click", () => setCalendarView(state.calendarView === "rules" ? "week" : "rules"));
  const right = document.createElement("div"); right.className = "calendar-command-actions"; right.append(switcher, settings);
  controls.append(dateControls, right); return controls;
}

function calendarSourcePanel(cockpit, data) {
  const section = document.createElement("section"); section.className = "card calendar-source-panel";
  const heading = document.createElement("div"); heading.className = "calendar-source-heading";
  const copy = document.createElement("div"); copy.append(text("h2", "Calendars in view"), text("p", "Show only what is useful right now. This is a private display filter; household rules remain active.", "state-note"));
  const lens = document.createElement("div"); lens.className = "calendar-lens-switcher";
  for (const [id, label] of [["capacity", "Capacity colors"], ["source", "Calendar colors"]]) { const button = text("button", label, "account-filter"); button.type = "button"; button.setAttribute("aria-pressed", String(state.calendarLens === id)); button.addEventListener("click", () => { state.calendarLens = id; renderCalendar(data); }); lens.append(button); }
  heading.append(copy, lens); section.append(heading);
  const filters = document.createElement("div"); filters.className = "calendar-source-filters";
  for (const source of calendarSources(cockpit)) { const key = calendarSourceKey(source); const label = document.createElement("label"); label.className = "calendar-source-filter"; const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = !state.calendarHiddenSources.includes(key); checkbox.addEventListener("change", () => { state.calendarHiddenSources = checkbox.checked ? state.calendarHiddenSources.filter((value) => value !== key) : [...new Set([...state.calendarHiddenSources, key])]; state.calendarSelectedEvent = null; renderCalendar(data); }); const dot = document.createElement("span"); dot.className = "calendar-source-dot"; dot.style.background = calendarSourceColor(source, cockpit); const body = document.createElement("span"); body.append(text("strong", source.calendar_name || "Calendar"), text("small", `${titleCase(source.rule.purpose || "unclassified")} · ${calendarEffectLabel(source.rule.effect || "context")}`)); label.append(checkbox, dot, body); filters.append(label); }
  section.append(filters);
  const actions = document.createElement("div"); actions.className = "calendar-filter-actions";
  const all = text("button", "Show all", "link-button"); all.type = "button"; all.addEventListener("click", () => { state.calendarHiddenSources = []; renderCalendar(data); });
  const capacity = text("button", "Capacity only", "link-button"); capacity.type = "button"; capacity.addEventListener("click", () => { state.calendarHiddenSources = calendarSources(cockpit).filter((source) => !["hard_block", "soft_hold"].includes(source.rule.effect)).map(calendarSourceKey); renderCalendar(data); });
  actions.append(all, capacity, text("span", `${calendarSources(cockpit).length - state.calendarHiddenSources.length} of ${calendarSources(cockpit).length} visible`, "meta")); section.append(actions);
  return section;
}

function calendarEventButton(event, cockpit, compact = false) {
  const source = event.source_routes?.find((route) => !state.calendarHiddenSources.includes(calendarSourceKey(route))) || event.source_routes?.[0] || event;
  const button = document.createElement("button"); button.type = "button";
  button.className = `calendar-event-block calendar-effect-${event.effect} calendar-lens-${state.calendarLens}${compact ? " calendar-event-compact" : ""}`;
  button.style.setProperty("--source-color", calendarSourceColor(source, cockpit));
  const time = event.all_day ? "All day" : new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", timeZone: cockpit.timezone }).format(new Date(event.start_at));
  button.setAttribute("aria-label", `${event.title}, ${time}, ${calendarEffectLabel(event.effect)}`);
  if (compact) button.append(text("span", event.all_day ? "" : time, "calendar-event-time"), text("strong", event.title));
  else button.append(text("strong", event.title), text("span", `${time} · ${calendarEffectLabel(event.effect)}`, "calendar-event-meta"));
  button.addEventListener("click", () => { state.calendarSelectedEvent = event.cluster_id || event.id; replaceQuery({ event: state.calendarSelectedEvent }); renderCalendar(state.surface); });
  return button;
}

function calendarEventDetail(cockpit) {
  const pane = document.createElement("aside"); pane.className = "card calendar-event-detail"; pane.setAttribute("aria-label", "Calendar event details");
  const events = (cockpit.events || []).filter(calendarEventIsVisible);
  const event = events.find((item) => (item.cluster_id || item.id) === state.calendarSelectedEvent);
  if (!event) { pane.append(text("p", "EVENT DETAIL", "eyebrow"), text("h2", "Select a calendar item"), text("p", "Open an item to see its source, effect, buffers, duplicate copies, and evidence without leaving the calendar.", "state-note")); return pane; }
  pane.append(text("p", "EVENT DETAIL", "eyebrow"), text("h2", event.title));
  const effect = text("span", calendarEffectLabel(event.effect), `pill calendar-effect-pill calendar-effect-${event.effect}`); pane.append(effect);
  const facts = document.createElement("dl"); facts.className = "calendar-event-facts";
  const rows = [
    ["When", event.all_day ? `${new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric", timeZone: cockpit.timezone }).format(new Date(event.start_at))} · all day` : `${calendarTime(event.start_at)}–${calendarTime(event.end_at, { weekday: undefined })}`],
    ["Calendar", (event.source_calendars || [event.calendar_name]).filter(Boolean).join(" · ")],
    ["Purpose", titleCase(event.purpose)],
    ["Buffer", event.buffer_before_minutes || event.buffer_after_minutes ? `${event.buffer_before_minutes || 0} min before · ${event.buffer_after_minutes || 0} min after` : "None"],
    ["Location", event.location],
    ["Provider", titleCase(event.provider)],
  ];
  for (const [label, value] of rows) if (value) facts.append(text("dt", label), text("dd", value));
  pane.append(facts);
  if (event.why?.length) pane.append(text("h3", "Why it behaves this way"), text("p", event.why.join(" "), "state-note"));
  if (event.duplicate_collapsed) pane.append(text("p", `${event.copy_count} provider copies are grouped here; none were deleted.`, "calendar-duplicate"));
  const actions = document.createElement("div"); actions.className = "button-row";
  if (event.last_event_id) { const evidence = text("button", "View evidence", "button button-quiet"); evidence.type = "button"; evidence.addEventListener("click", () => showEvidence(event.last_event_id)); actions.append(evidence); }
  if (event.html_link) { const provider = text("a", "Open in provider", "button button-quiet"); provider.href = event.html_link; provider.target = "_blank"; provider.rel = "noreferrer"; actions.append(provider); }
  pane.append(actions); return pane;
}

function calendarEventLayout(events) {
  const ends = []; const layouts = new Map();
  for (const event of [...events].sort((left, right) => new Date(left.start_at) - new Date(right.start_at))) { const start = new Date(event.start_at).getTime(); let column = ends.findIndex((end) => end <= start); if (column < 0) column = ends.length; ends[column] = new Date(event.end_at).getTime(); layouts.set(event.cluster_id || event.id, column); }
  return { columns: Math.max(1, ends.length), layouts };
}

function calendarTimeGrid(cockpit) {
  const dates = calendarRangeDates(cockpit).slice(0, state.calendarView === "day" ? 1 : 7);
  const visible = (cockpit.events || []).filter(calendarEventIsVisible);
  const timed = visible.filter((event) => !event.all_day); const allDay = visible.filter((event) => event.all_day);
  const minHour = Math.max(0, Math.min(6, ...timed.map((event) => calendarHour(event.start_at, cockpit.timezone))));
  const maxHour = Math.min(24, Math.max(22, ...timed.map((event) => Math.ceil(calendarHour(event.end_at, cockpit.timezone)))));
  const hourHeight = 56; const height = Math.max(8, maxHour - minHour) * hourHeight;
  const board = document.createElement("section"); board.className = "card calendar-board";
  const boardHeading = document.createElement("div"); boardHeading.className = "calendar-board-heading"; boardHeading.append(text("h2", state.calendarView === "day" ? "Day plan" : "Week at a glance"));
  const legend = document.createElement("div"); legend.className = "calendar-legend"; for (const [effect, label] of [["hard_block", "Hard"], ["soft_hold", "Soft"], ["context", "Context"], ["policy", "Guardrail"], ["buffer", "Buffer"]]) { const item = document.createElement("span"); item.className = `calendar-legend-${effect}`; item.append(document.createElement("i"), document.createTextNode(label)); legend.append(item); } boardHeading.append(legend); board.append(boardHeading);
  const scroller = document.createElement("div"); scroller.className = "calendar-grid-scroll";
  const grid = document.createElement("div"); grid.className = "calendar-time-grid"; grid.style.setProperty("--calendar-days", dates.length);
  grid.append(text("span", cockpit.timezone || "", "calendar-timezone"));
  for (const date of dates) { const head = document.createElement("div"); head.className = `calendar-day-head${calendarLocalDate(date) === state.calendarToday ? " is-today" : ""}`; head.append(text("span", new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(date)), text("strong", date.getDate())); grid.append(head); }
  grid.append(text("span", "all-day", "calendar-all-day-label"));
  for (const date of dates) { const cell = document.createElement("div"); cell.className = "calendar-all-day-cell"; for (const event of allDay.filter((item) => calendarEventOccursOn(item, calendarLocalDate(date), cockpit.timezone)).slice(0, 4)) cell.append(calendarEventButton(event, cockpit, true)); grid.append(cell); }
  const axis = document.createElement("div"); axis.className = "calendar-time-axis"; axis.style.height = `${height}px`; for (let hour = minHour; hour <= maxHour; hour += 1) { const label = text("span", new Intl.DateTimeFormat(undefined, { hour: "numeric" }).format(new Date(2020, 0, 1, hour)), "calendar-hour-label"); label.style.top = `${(hour - minHour) * hourHeight}px`; axis.append(label); } grid.append(axis);
  for (const date of dates) {
    const key = calendarLocalDate(date); const lane = document.createElement("div"); lane.className = "calendar-day-lane"; lane.style.height = `${height}px`; lane.style.setProperty("--hour-height", `${hourHeight}px`);
    for (const window of (cockpit.schedule_windows || []).filter((item) => calendarDateKey(item.start_at, cockpit.timezone) === key)) { const startHour = calendarHour(window.start_at, cockpit.timezone); const endHour = calendarDateKey(window.end_at, cockpit.timezone) !== calendarDateKey(window.start_at, cockpit.timezone) ? 24 : calendarHour(window.end_at, cockpit.timezone); const top = Math.max(0, startHour - minHour) * hourHeight; const blockHeight = Math.max(0, Math.min(maxHour, endHour) - Math.max(minHour, startHour)) * hourHeight; if (blockHeight <= 0) continue; const policy = text("div", blockHeight >= 34 ? window.label : "", `calendar-policy-window calendar-policy-${window.effect}`); policy.style.top = `${top}px`; policy.style.height = `${blockHeight}px`; policy.title = `${window.label} · ${calendarEffectLabel(window.effect)}`; lane.append(policy); }
    const dayEvents = timed.filter((event) => calendarEventOccursOn(event, key, cockpit.timezone)); const layout = calendarEventLayout(dayEvents);
    for (const event of dayEvents) { const startHour = calendarDateKey(event.start_at, cockpit.timezone) === key ? calendarHour(event.start_at, cockpit.timezone) : 0; const endHour = calendarDateKey(event.end_at, cockpit.timezone) === key ? calendarHour(event.end_at, cockpit.timezone) : 24; const top = Math.max(0, startHour - minHour) * hourHeight; const eventHeight = Math.max(28, (Math.min(maxHour, endHour) - Math.max(minHour, startHour)) * hourHeight); const column = layout.layouts.get(event.cluster_id || event.id) || 0; const left = `calc(${(column / layout.columns) * 100}% + 2px)`; const width = `calc(${100 / layout.columns}% - 4px)`; if (event.buffer_before_minutes || event.buffer_after_minutes) { const bufferedStartHour = calendarDateKey(event.buffered_start_at, cockpit.timezone) === key ? calendarHour(event.buffered_start_at, cockpit.timezone) : 0; const bufferedEndHour = calendarDateKey(event.buffered_end_at, cockpit.timezone) === key ? calendarHour(event.buffered_end_at, cockpit.timezone) : 24; const halo = document.createElement("span"); halo.className = `calendar-buffer-halo calendar-buffer-${event.effect}`; halo.style.top = `${Math.max(0, bufferedStartHour - minHour) * hourHeight}px`; halo.style.height = `${Math.max(0, Math.min(maxHour, bufferedEndHour) - Math.max(minHour, bufferedStartHour)) * hourHeight}px`; halo.style.left = left; halo.style.width = width; halo.title = `${event.buffer_before_minutes || 0} minute preparation buffer and ${event.buffer_after_minutes || 0} minute recovery buffer`; lane.append(halo); } const block = calendarEventButton(event, cockpit); block.style.top = `${top}px`; block.style.height = `${eventHeight}px`; block.style.left = left; block.style.width = width; lane.append(block); }
    if (key === state.calendarToday) { const hour = calendarHour(new Date(), cockpit.timezone); if (hour >= minHour && hour <= maxHour) { const line = document.createElement("span"); line.className = "calendar-now-line"; line.style.top = `${(hour - minHour) * hourHeight}px`; lane.append(line); } }
    grid.append(lane);
  }
  scroller.append(grid); board.append(scroller); const layout = document.createElement("div"); layout.className = "calendar-board-layout"; layout.append(board, calendarEventDetail(cockpit)); return layout;
}

function calendarAgenda(cockpit) {
  const section = document.createElement("section"); section.className = "card calendar-agenda"; section.append(text("h2", "Agenda"));
  const events = (cockpit.events || []).filter(calendarEventIsVisible); const dates = calendarRangeDates(cockpit);
  let found = false;
  for (const date of dates) { const dayEvents = events.filter((event) => calendarEventOccursOn(event, calendarLocalDate(date), cockpit.timezone)); if (!dayEvents.length) continue; found = true; const group = document.createElement("section"); group.className = "calendar-agenda-day"; group.append(text("h3", new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(date))); const list = document.createElement("div"); list.className = "calendar-agenda-items"; for (const event of dayEvents) list.append(calendarEventButton(event, cockpit)); group.append(list); section.append(group); }
  if (!found) section.append(text("p", "No visible calendar items in this range. Hidden calendars and provider records are unchanged.", "empty")); return section;
}

function calendarMonth(cockpit) {
  const section = document.createElement("section"); section.className = "card calendar-month"; section.append(text("h2", "Month at a glance")); const grid = document.createElement("div"); grid.className = "calendar-month-grid";
  for (const label of ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) grid.append(text("span", label, "calendar-month-weekday"));
  const anchor = calendarDate(state.calendarAnchor || state.calendarToday || calendarLocalDate()); const events = (cockpit.events || []).filter(calendarEventIsVisible);
  for (const date of calendarRangeDates(cockpit)) { const key = calendarLocalDate(date); const cell = document.createElement("div"); cell.className = `calendar-month-day${date.getMonth() !== anchor.getMonth() ? " outside-month" : ""}${key === state.calendarToday ? " is-today" : ""}`; const head = text("button", String(date.getDate()), "calendar-month-date"); head.type = "button"; head.setAttribute("aria-label", `Open ${new Intl.DateTimeFormat(undefined, { month: "long", day: "numeric" }).format(date)} in Day view`); head.addEventListener("click", () => setCalendarView("day", key)); cell.append(head); const dayEvents = events.filter((event) => calendarEventOccursOn(event, key, cockpit.timezone)); for (const event of dayEvents.slice(0, 3)) cell.append(calendarEventButton(event, cockpit, true)); if (dayEvents.length > 3) { const more = text("button", `+${dayEvents.length - 3} more`, "calendar-month-more"); more.type = "button"; more.addEventListener("click", () => setCalendarView("day", key)); cell.append(more); } grid.append(cell); }
  section.append(grid); return section;
}

function calendarFindTime(cockpit) {
  const fragment = document.createDocumentFragment(); const section = document.createElement("section"); section.className = "card calendar-find";
  const heading = document.createElement("div"); heading.className = "calendar-find-heading"; const copy = document.createElement("div"); copy.append(text("h2", "Find a workable window"), text("p", "Hard blocks, buffers, and protected time are removed. Soft load is flagged, never silently overruled.", "state-note"));
  const durations = document.createElement("div"); durations.className = "calendar-duration-switcher"; for (const minutes of [30, 60, 90, 120]) { const button = text("button", `${minutes} min`, "account-filter"); button.type = "button"; button.setAttribute("aria-pressed", String(state.calendarDuration === minutes)); button.addEventListener("click", () => { state.calendarDuration = minutes; renderCalendar(state.surface); }); durations.append(button); } heading.append(copy, durations); section.append(heading);
  const now = Date.now(); const soft = (cockpit.events || []).filter((event) => event.effect === "soft_hold"); const candidates = (cockpit.open_windows || []).filter((window) => new Date(window.end_at) - new Date(window.start_at) >= state.calendarDuration * 60000 && new Date(window.start_at).getTime() >= now - 60000).map((window) => { const suggestedEnd = new Date(new Date(window.start_at).getTime() + state.calendarDuration * 60000); return { ...window, suggested_end_at: suggestedEnd.toISOString(), soft_load: soft.some((event) => new Date(event.start_at) < suggestedEnd && new Date(event.end_at) > new Date(window.start_at)) }; }).sort((left, right) => Number(left.soft_load) - Number(right.soft_load) || new Date(left.start_at) - new Date(right.start_at));
  if (!candidates.length) section.append(text("p", `No ${state.calendarDuration}-minute unprotected window appears in this range.`, "empty")); else { const list = document.createElement("ol"); list.className = "calendar-window-list"; for (const [index, window] of candidates.slice(0, 18).entries()) { const li = document.createElement("li"); const body = document.createElement("div"); body.append(text("strong", `${index === 0 ? "Earliest fit · " : ""}${new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: cockpit.timezone }).format(new Date(window.start_at))}`), text("span", `${calendarTime(window.start_at, { weekday: undefined })}–${calendarTime(window.suggested_end_at, { weekday: undefined })}`, "meta")); li.append(body, text("span", window.soft_load ? "Soft load nearby" : "Clear", `pill ${window.soft_load ? "calendar-window-soft" : "pill-teal"}`)); list.append(li); } section.append(list); }
  const conditional = document.createElement("section"); conditional.className = "card calendar-conditional-card"; conditional.append(text("h2", "Available only if you accept")); for (const window of cockpit.conditional_windows || []) conditional.append(text("p", `${window.label}: ${calendarTime(window.start_at)}–${calendarTime(window.end_at, { weekday: undefined })}`, "calendar-conditional")); if (!(cockpit.conditional_windows || []).length) conditional.append(text("p", "No conditional household windows in this range.", "empty")); fragment.append(section, conditional, calendarConflictPanel(cockpit)); return fragment;
}

function calendarConflictPanel(cockpit) {
  const section = document.createElement("section"); section.className = "card calendar-conflicts"; section.append(text("h2", "Conflicts and pressure"));
  if (!(cockpit.conflicts || []).length) section.append(text("p", "No buffered hard conflict or soft-load pressure is visible.", "empty compact-empty"));
  for (const conflict of cockpit.conflicts || []) { const row = document.createElement("div"); row.className = `calendar-conflict calendar-${conflict.kind}`; row.append(text("strong", `${titleCase(conflict.kind)} · ${conflict.minutes} minutes`), text("span", conflict.why, "state-note")); section.append(row); } return section;
}

function calendarPolicyOverview(cockpit) {
  const fragment = document.createDocumentFragment(); const policy = cockpit.schedule_policy || {};
  const section = document.createElement("section"); section.className = "card calendar-policy-overview"; section.append(text("h2", "Household time guardrails"), text("p", "These recurring windows shape availability before any suggested opening is shown.", "state-note"));
  const dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]; const list = document.createElement("div"); list.className = "calendar-policy-list";
  for (const window of policy.windows || []) { const row = document.createElement("div"); row.className = `calendar-policy-row calendar-effect-${window.effect}`; row.append(text("strong", window.label), text("span", `${window.days.map((day) => dayNames[day]).join(", ")} · ${window.start_local}–${window.end_local}`, "meta"), text("span", calendarEffectLabel(window.effect), "calendar-policy-effect")); list.append(row); } section.append(list);
  const buffers = document.createElement("section"); buffers.className = "card calendar-buffer-card"; buffers.append(text("h2", "Preparation and recovery buffers")); const bufferList = document.createElement("dl"); bufferList.className = "calendar-buffer-list"; const values = policy.buffer_minutes || {}; for (const kind of ["remote", "appointment", "medical", "handoff", "deep_work"]) { const before = values[`${kind}_before`] || 0; const after = values[`${kind}_after`] || 0; const row = document.createElement("div"); row.append(text("dt", titleCase(kind)), text("dd", `${before} min before · ${after} min after`)); bufferList.append(row); } buffers.append(bufferList); fragment.append(section, buffers); return fragment;
}

function calendarRuleEditor(cockpit, data) {
  const fragment = document.createDocumentFragment(); fragment.append(calendarPolicyOverview(cockpit));
  const section = document.createElement("section"); section.className = "card calendar-rules"; section.append(text("h2", "Calendar routing and capacity"), text("p", "Provider access is factual. Purpose, capacity effect, and approved write destinations are explicit household policy.", "state-note"));
  const disclosure = document.createElement("details"); disclosure.className = "calendar-rule-disclosure"; disclosure.append(text("summary", "Review or change calendar rules"));
  const form = document.createElement("form"); form.className = "calendar-rule-form";
  const existing = new Map((cockpit.rules || []).map((rule) => [`${rule.provider}|${rule.provider_account_id}|${rule.calendar_external_id}`, rule]));
  for (const source of cockpit.sources || []) { const key = `${source.provider}|${source.provider_account_id}|${source.calendar_external_id}`; const rule = existing.get(key) || {}; const row = document.createElement("fieldset"); row.dataset.source = JSON.stringify(source); row.append(text("legend", source.calendar_name || "Calendar"), text("small", `${titleCase(source.access_role)} · ${source.write_events ? "can write" : "read only"}`, "meta")); const purposeLabel = text("label", "Purpose", "calendar-rule-label"); const purpose = document.createElement("select"); purpose.dataset.field = "purpose"; purpose.setAttribute("aria-label", `${source.calendar_name} purpose`); for (const value of ["personal", "work", "household", "coparenting", "property", "family_context", "holidays", "test", "other"]) { const option = new Option(titleCase(value), value); option.selected = (rule.purpose || "other") === value; purpose.append(option); } purposeLabel.append(purpose); const effectLabel = text("label", "Capacity effect", "calendar-rule-label"); const effect = document.createElement("select"); effect.dataset.field = "effect"; effect.setAttribute("aria-label", `${source.calendar_name} capacity effect`); for (const value of ["hard_block", "soft_hold", "context", "ignore"]) { const option = new Option(calendarEffectLabel(value), value); option.selected = (rule.effect || "context") === value; effect.append(option); } effectLabel.append(effect); const write = document.createElement("input"); write.type = "checkbox"; write.dataset.field = "write"; write.checked = Boolean(rule.write_target); write.disabled = !source.write_events; const writeLabel = document.createElement("label"); writeLabel.className = "calendar-write-label"; writeLabel.append(write, document.createTextNode(source.write_events ? " Approved Triage write destination" : " Read-only provider calendar")); row.append(purposeLabel, effectLabel, writeLabel); form.append(row); }
  const save = text("button", "Save reviewed rules", "button button-primary"); save.type = "submit"; form.append(save);
  form.addEventListener("submit", async (event) => { event.preventDefault(); save.disabled = true; const rules = [...form.querySelectorAll("fieldset")].map((row) => { const source = JSON.parse(row.dataset.source); const old = existing.get(`${source.provider}|${source.provider_account_id}|${source.calendar_external_id}`) || {}; const writeTarget = row.querySelector('[data-field="write"]').checked; const purpose = row.querySelector('[data-field="purpose"]').value; const writableKinds = ["personal", "work", "household", "coparenting", "property"]; return { source_member_id: source.member_id, provider: source.provider, provider_account_id: source.provider_account_id, calendar_external_id: source.calendar_external_id, purpose, effect: row.querySelector('[data-field="effect"]').value, tentative_effect: old.tentative_effect || "soft_hold", caregiving_load: old.caregiving_load || "ignore", caregiving_summary_values: old.caregiving_summary_values || [], buffer_kind: old.buffer_kind || "auto", write_target: writeTarget, destination_kind: writeTarget ? (old.destination_kind || (writableKinds.includes(purpose) ? purpose : "personal")) : null, enabled: true }; }); try { await api("/api/member/v1/calendar/onboarding", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ request_id: crypto.randomUUID(), member_id: data.member_id, timezone: cockpit.timezone, rules, windows: cockpit.schedule_policy?.windows || [], buffer_minutes: cockpit.schedule_policy?.buffer_minutes || {}, accepted_chores_required: cockpit.schedule_policy?.accepted_chores_required ?? true, one_confirm_write: cockpit.schedule_policy?.one_confirm_write ?? true }) }); setBanner("Calendar rules saved. Availability was recalculated from provider facts."); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} Calendar rules were not changed.`); save.disabled = false; } });
  disclosure.append(form); section.append(disclosure); fragment.append(section); return fragment;
}

function renderCalendar(data) {
  const root = $("#surface"); root.replaceChildren(); const cockpit = data.cockpit || {};
  state.calendarAnchor ||= data.calendar_anchor || calendarLocalDate();
  state.calendarToday = data.calendar_today || state.calendarToday || calendarLocalDate();
  $("#mode-label").textContent = "Private · deterministic calendar policy";
  root.append(freshnessStrip(data), calendarHero(cockpit), calendarControls(cockpit));
  if (state.calendarView !== "rules") root.append(calendarSourcePanel(cockpit, data));
  if (state.calendarView === "find") root.append(calendarFindTime(cockpit));
  else if (state.calendarView === "rules") root.append(calendarRuleEditor(cockpit, data));
  else if (state.calendarView === "month") root.append(calendarMonth(cockpit), calendarConflictPanel(cockpit));
  else if (state.calendarView === "agenda") root.append(calendarAgenda(cockpit), calendarConflictPanel(cockpit));
  else root.append(calendarTimeGrid(cockpit), calendarConflictPanel(cockpit));
}

function renderReminders(data) {
  const root = $("#surface"); root.replaceChildren(); const cockpit = data.cockpit;
  $("#mode-label").textContent = "Private · provider-backed · deterministic";
  root.append(freshnessStrip(data), remindersHero(cockpit), remindersControls(cockpit, data));
  const visible = cockpit.items.filter(reminderMatches);
  if (!state.reminderItem || !visible.some((item) => item.id === state.reminderItem)) state.reminderItem = visible[0]?.id || null;
  const selected = visible.find((item) => item.id === state.reminderItem);
  const workspace = document.createElement("div"); workspace.className = "reminder-workspace";
  const list = document.createElement("section"); list.className = "card reminder-list"; list.append(text("h2", `${titleCase(state.remindersView)} reminders`));
  if (!visible.length) list.append(text("p", "Nothing in this lens. Your provider truth is unchanged.", "empty")); else { const items = document.createElement("ol"); items.className = "reminder-items"; for (const item of visible) { const li = document.createElement("li"); li.append(reminderItemButton(item)); items.append(li); } list.append(items); }
  workspace.append(list, reminderDetail(selected, cockpit)); root.append(workspace, reminderQuickAdd(cockpit), reminderProviderTruth(cockpit));
}

function freshnessStrip(data) {
  const value = data.freshness;
  const strip = document.createElement("div"); strip.className = "freshness-strip";
  if (!value) return strip;
  strip.append(text("span", titleCase(value.state), `pill state-label state-${String(value.state).replaceAll("_", "-")}`));
  const latest = value.latest_source_at ? `Latest source ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value.latest_source_at))}` : "No source watermark yet";
  strip.append(text("span", `${latest} · ${value.row_count} visible record${value.row_count === 1 ? "" : "s"}`));
  if (value.degraded_reason) strip.append(text("strong", value.degraded_reason));
  return strip;
}

function renderSurface(data) {
  state.surface = data;
  document.body.dataset.surface = data.surface;
  const root = $("#surface");
  root.setAttribute("aria-busy", "false");
  root.replaceChildren();
  $("#page-title").textContent = data.label;
  $("#mode-label").textContent = "Private member view";
  if (data.surface === "today") {
    state.carouselItems = [...(data.brief?.carousel?.items || [])];
    state.carouselIndex = 0;
    renderToday(data);
  } else if (data.surface === "triage") {
    root.append(freshnessStrip(data), triageSurface(data));
  } else if (data.surface === "comms") {
    renderComms(data);
  } else if (data.surface === "reminders") {
    renderReminders(data);
  } else if (data.surface === "people") {
    root.append(card("People", data.people, "No people are visible in this scope."), card("Recent contact", data.recent_interactions));
  } else if (data.surface === "knowledge") {
    root.append(card("Private knowledge", data.knowledge), card("Capture evidence", data.artifacts), card("Lists", data.lists));
  } else if (data.surface === "finance") {
    root.append(card("Recent money movement", data.flows, "Finance is connected, with no visible movement in this period."));
  } else if (data.surface === "calendar") {
    renderCalendar(data);
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
  $("#mode-label").textContent = "Temporary lens";
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
  $("[data-close-action]").addEventListener("click", closeActionDialog);
  try {
    state.bootstrap = await api("/api/member/v1/bootstrap"); renderNavigation(combinedNavigation(state.bootstrap)); configureChat(state.bootstrap.chat);
    $("#member-button").textContent = state.bootstrap.member.id.slice(0, 1).toUpperCase();
    $("#observation-pill").textContent = state.bootstrap.observation_mode ? "Observation mode" : "Member-safe";
    $("#observation-banner").hidden = !state.bootstrap.observation_mode;
    const active = currentSurface();
    const params = new URLSearchParams(location.search);
    const requestedView = params.get("view");
    if (active === "today" && ["brief", "carousel"].includes(requestedView)) state.todayView = requestedView;
    if (active === "triage") {
      if (["ready", "needs_details", "recent"].includes(requestedView)) state.triageView = requestedView;
      state.triageSelected = params.get("item");
    }
    if (active === "comms") {
      if (["focus", "waiting", "snoozed", "quieted", "all"].includes(requestedView)) state.commsView = requestedView;
      state.commsAccount = params.get("account") || "all";
      state.commsThread = params.get("thread");
    }
    if (active === "reminders") {
      if (["now", "scheduled", "places", "anytime", "completed"].includes(requestedView)) state.remindersView = requestedView;
      state.remindersAccount = params.get("account") || "all";
      state.remindersList = params.get("list") || "all";
      state.reminderItem = params.get("item");
    }
    if (active === "calendar") {
      if (["day", "week", "month", "agenda", "find", "rules"].includes(requestedView)) state.calendarView = requestedView;
      state.calendarAnchor = params.get("date");
      state.calendarSelectedEvent = params.get("event");
    }
    let path = `/api/member/v1/surfaces/${encodeURIComponent(active)}`;
    if (active === "calendar") {
      const query = new URLSearchParams({ view: state.calendarView });
      if (state.calendarAnchor) query.set("date", state.calendarAnchor);
      path += `?${query}`;
    }
    const surfaceRequest = active.startsWith("quest:")
      ? api(`/api/member/v1/temporary-surfaces/${encodeURIComponent(active.slice(6))}/evaluate`).then(renderQuest)
      : api(path).then(renderSurface);
    await Promise.all([surfaceRequest, loadChatHistory()]);
  } catch (error) { renderFailure(error); }
}

initialize();
