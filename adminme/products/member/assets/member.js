"use strict";

const state = {
  bootstrap: null,
  surface: null,
  controller: null,
  assistantNode: null,
  todayView: "brief",
  carouselIndex: 0,
  carouselItems: [],
  commsView: "focus",
  commsAccount: "all",
  commsThread: null,
  remindersView: "now",
  remindersAccount: "all",
  remindersList: "all",
  reminderItem: null,
  calendarView: "week",
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
  renderSurface(await api(`/api/member/v1/surfaces/${encodeURIComponent(active)}`));
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

async function enrichTriage(item, buttons) {
  const title = window.prompt("Confirm or correct the title", item.title || "");
  if (title == null) return;
  let targetKind = item.target_kind;
  if (item.source_kind !== "commitment_proposal") {
    targetKind = window.prompt("Target type (task, commitment, calendar_event, reminder, list, list_item, or party_identifier)", item.target_kind);
    if (targetKind == null) return;
  }
  const external = ["calendar_event", "reminder", "list", "list_item"].includes(targetKind);
  const candidateUpdates = {};
  let route = item.route || { member_id: state.bootstrap.member.id };
  if (item.source_kind !== "commitment_proposal") {
    const member = window.prompt("Destination household member ID", route.member_id || state.bootstrap.member.id);
    if (member == null) return;
    route = { ...route, member_id: member };
  } else {
    route = null;
  }
  if (targetKind === "commitment" && item.source_kind !== "commitment_proposal") {
    const owedTo = window.prompt("Party ID this commitment is owed to", item.candidate?.owed_to_party_id || "");
    if (owedTo == null) return;
    candidateUpdates.owed_to_party_id = owedTo;
  }
  if (targetKind === "calendar_event") {
    const start = window.prompt("Start date/time (ISO 8601 with time zone)", item.candidate?.start || item.candidate?.due || "");
    if (start == null) return;
    const end = window.prompt("End date/time (ISO 8601 with time zone)", item.candidate?.end || "");
    if (end == null) return;
    candidateUpdates.start = start;
    candidateUpdates.end = end;
  }
  if (external) {
    const provider = window.prompt("Provider (google_calendar, google_tasks, apple_calendar, or apple_reminders)", route?.provider || "");
    if (provider == null) return;
    const account = window.prompt("Provider account ID", route?.provider_account_id || "");
    if (account == null) return;
    const container = targetKind === "list" ? route?.container_external_id || null : window.prompt("Destination calendar or list ID", route?.container_external_id || "");
    if (container == null && targetKind !== "list") return;
    route = { member_id: route.member_id, provider, provider_account_id: account, container_external_id: container || null };
  }
  buttons.forEach((button) => { button.disabled = true; });
  const banner = $("#state-banner"); banner.hidden = false; banner.textContent = "Saving the enrichment without creating official household state…";
  try {
    await api(`/api/member/v1/triage/${encodeURIComponent(item.proposal_id)}/enrich`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({ request_id: crypto.randomUUID(), expected_last_event_id: item.last_event_id, title, target_kind: targetKind, candidate_updates: candidateUpdates, route }),
    });
    banner.textContent = "Enrichment saved. Review the candidate once more before confirming it.";
    await reloadActiveSurface();
  } catch (error) {
    banner.textContent = `${error.message} The candidate was not changed.`;
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function triageSurface(items, calendarDestinations = []) {
  const fragment = document.createDocumentFragment();
  const intro = document.createElement("section"); intro.className = "hero-card";
  intro.append(text("p", "HUMAN GATE", "eyebrow"), text("h2", "Nothing becomes household truth by inference alone.", "action-title"), text("p", "Review extracted candidates, add missing routing details, then confirm or dismiss. Provider observations remain facts and do not wait here."));
  fragment.append(intro);
  const section = document.createElement("section"); section.className = "card"; section.append(text("h2", "Awaiting your decision"));
  if (!Array.isArray(items) || items.length === 0) {
    section.append(text("p", "Triage is clear. No extracted candidates are waiting for you.", "empty")); fragment.append(section); return fragment;
  }
  const list = document.createElement("ul"); list.className = "item-list";
  for (const item of items) {
    const li = document.createElement("li"); li.className = "item triage-item";
    li.append(text("strong", item.title), text("span", `${String(item.target_kind).replaceAll("_", " ")} · ${item.source_kind.replaceAll("_", " ")}`, "meta"));
    if (item.reasons?.length) li.append(text("small", item.reasons.join(" · ")));
    if (item.target_kind === "calendar_event") {
      const route = item.route || {};
      const destination = calendarDestinations.find((candidate) => candidate.provider === route.provider && candidate.provider_account_id === route.provider_account_id && candidate.calendar_external_id === route.container_external_id);
      li.append(text("small", destination ? `Will write to ${destination.calendar_name || titleCase(destination.destination_kind)} after this confirmation.` : "Choose one approved calendar destination before confirming.", destination ? "calendar-route-ready" : "calendar-route-missing"));
    }
    const row = document.createElement("div"); row.className = "button-row";
    const enrich = text("button", item.source_kind === "commitment_proposal" ? "Edit" : "Enrich", "button button-quiet");
    const approve = text("button", "Confirm", "button button-primary");
    const dismiss = text("button", "Dismiss", "button button-quiet");
    const evidence = text("button", "View evidence", "link-button"); evidence.type = "button"; evidence.addEventListener("click", () => showEvidence(item.source_event_id));
    const buttons = [enrich, approve, dismiss]; buttons.forEach((button) => { button.type = "button"; });
    enrich.addEventListener("click", () => enrichTriage(item, buttons)); approve.addEventListener("click", () => decideTriage(item, "approve", buttons)); dismiss.addEventListener("click", () => decideTriage(item, "dismiss", buttons));
    row.append(enrich, approve, dismiss, evidence); li.append(row); list.append(li);
  }
  section.append(list); fragment.append(section); return fragment;
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
  if (action === "open_triage") {
    location.href = "/member/triage";
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
    open_chat: "Talk it through",
    evidence: "Why this is here",
  };
  const primary = ["complete", "complete_external", "complete_step", "attended", "open_triage"].includes(action);
  const button = text("button", labels[action] || titleCase(action), action === "evidence" ? "link-button" : `button ${primary ? "button-primary" : "button-quiet"}`);
  button.type = "button";
  button.addEventListener("click", () => executeTodayAction(item, action, container));
  return button;
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
  li.append(body);
  if (interactive) {
    const row = document.createElement("div");
    row.className = "day-item-actions";
    const actions = (item.actions || []).filter((action) => ["complete", "complete_external", "complete_step", "skip_step", "attended", "missed", "open_triage", "open_chat", "evidence"].includes(action));
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
  for (const [label, value] of [["Events", brief.stats.events], ["Moves", brief.stats.moves], ["Messages", brief.stats.messages], ["Decisions", brief.stats.questions]]) {
    stats.append(text("div", "", "stat"));
    stats.lastElementChild.append(text("dt", label), text("dd", value));
  }
  section.append(stats);
  return section;
}

function renderDayBrief(brief) {
  const fragment = document.createDocumentFragment();
  fragment.append(briefHero(brief));
  const grid = document.createElement("div");
  grid.className = "brief-grid";
  grid.append(
    daySection("On the clock", brief.schedule || [], "No timed events are on the calendar today."),
    daySection("Moves for today", brief.plan || [], "No planned moves are due today."),
  );
  fragment.append(grid);
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
  for (const action of (item.actions || []).filter((value) => ["complete", "complete_external", "complete_step", "skip_step", "attended", "missed", "open_triage", "open_chat"].includes(value))) {
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
    ["focus", "Focus", cockpit.counts.needs_reply],
    ["waiting", "Waiting", cockpit.counts.waiting],
    ["snoozed", "Snoozed", cockpit.counts.snoozed],
    ["quieted", "Quieted", cockpit.counts.quieted],
    ["all", "All", cockpit.counts.all],
  ];
  for (const [value, label, count] of choices) {
    const button = text("button", `${label} ${count}`, "comms-filter");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.commsView === value));
    button.addEventListener("click", () => { state.commsView = value; renderComms(data); });
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
    button.addEventListener("click", () => { state.commsAccount = account.provider_account_id; renderComms(data); });
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
    text("h2", cockpit.counts.needs_reply === 1 ? "1 conversation deserves a look." : cockpit.counts.needs_reply ? `${cockpit.counts.needs_reply} conversations deserve a look.` : "No conversation is pulling on your working memory.", "action-title"),
    text("p", "Focus is a deterministic lens over your connected accounts. Messages stay in their original member, workspace, provider, and privacy boundary.", "brief-summary"),
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
    button.addEventListener("click", () => { state.commsThread = thread.thread_key; renderComms(state.surface); });
    li.append(button); items.append(li);
  }
  list.append(items); return list;
}

function renderComms(data) {
  const root = $("#surface"); root.replaceChildren();
  const cockpit = data.cockpit;
  $("#mode-label").textContent = "Private · deterministic focus";
  root.append(commsHero(cockpit), commsControls(cockpit, data));
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

async function configureReminderCue(item) {
  const signalKey = window.prompt("Typed signal key (for example presence:home or weather:rain)", "");
  if (!signalKey?.trim()) return;
  const source = window.prompt("Signal source (home_assistant, member_bridge, weather, device, network, calendar, member_profile, or manual)", "home_assistant");
  if (!source) return;
  const kind = window.prompt("Signal kind (location, presence, weather, device_state, network_presence, calendar_transition, energy, or capacity)", "presence");
  if (!kind) return;
  const operator = window.prompt("Match rule (equals, not_equals, above, below, entered, left, or present)", "equals");
  if (!operator) return;
  const expected = ["equals", "not_equals"].includes(operator) ? window.prompt("Expected state", "present") : ["above", "below"].includes(operator) ? window.prompt("Threshold number", "0") : null;
  if (["equals", "not_equals", "above", "below"].includes(operator) && expected == null) return;
  setBanner("Saving a deterministic cue. This will not change the source reminder…");
  try {
    await api("/api/member/v1/reminders/cues", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        cue_id: crypto.randomUUID(), list_external_id: item.list_external_id,
        item_external_id: item.item_external_id, expected_last_event_id: item.last_event_id,
        signal_key: signalKey.trim(), source, kind, operator,
        expected_state: ["equals", "not_equals"].includes(operator) ? expected : null,
        expected_number: ["above", "below"].includes(operator) ? Number(expected) : null,
        enabled: true,
      }),
    });
    setBanner("Cue saved. It activates only when a fresh authorized signal matches.");
    await reloadActiveSurface();
  } catch (error) { setBanner(`${error.message} No cue was created.`); }
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
    button.addEventListener("click", () => { state.remindersView = value; renderReminders(data); }); views.append(button);
  }
  const accounts = document.createElement("div"); accounts.className = "comms-filter-row account-filters";
  for (const account of [{ provider_account_id: "all", label: "All accounts" }, ...(cockpit.accounts || [])]) {
    const button = text("button", account.label, "account-filter"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.remindersAccount === account.provider_account_id));
    button.addEventListener("click", () => { state.remindersAccount = account.provider_account_id; state.remindersList = "all"; renderReminders(data); }); accounts.append(button);
  }
  const lists = document.createElement("div"); lists.className = "comms-filter-row account-filters";
  for (const list of [{ list_external_id: "all", list_name: "All lists" }, ...(cockpit.lists || []).filter((row) => state.remindersAccount === "all" || row.provider_account_id === state.remindersAccount)]) {
    const button = text("button", list.list_name, "account-filter"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.remindersList === list.list_external_id));
    button.addEventListener("click", () => { state.remindersList = list.list_external_id; renderReminders(data); }); lists.append(button);
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
  button.addEventListener("click", () => { state.reminderItem = item.id; renderReminders(state.surface); });
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
  const actions = document.createElement("div"); actions.className = "button-row";
  if (item.can_complete || item.completed) {
    const stem = item.completed ? "uncomplete" : "complete"; const button = text("button", item.completed ? "Restore" : "Done", "button button-primary"); button.type = "button";
    button.addEventListener("click", () => runReminderCommand(item, reminderOperation(item, stem))); actions.append(button);
  }
  if (item.can_edit) {
    const edit = text("button", "Edit title", "button button-quiet"); edit.type = "button";
    edit.addEventListener("click", () => { const title = window.prompt("Reminder title", item.title); if (title?.trim()) runReminderCommand(item, reminderOperation(item, "update"), { title: title.trim() }); }); actions.append(edit);
    const move = text("button", "Move", "button button-quiet"); move.type = "button";
    move.addEventListener("click", () => { const candidates = (cockpit.lists || []).filter((list) => list.provider === item.provider && list.provider_account_id === item.provider_account_id && list.list_external_id !== item.list_external_id); const destination = window.prompt(`Destination list ID\n${candidates.map((list) => `${list.list_name}: ${list.list_external_id}`).join("\n")}`, candidates[0]?.list_external_id || ""); if (destination) runReminderCommand(item, reminderOperation(item, "move"), { destination_list_external_id: destination }); }); actions.append(move);
    const remove = text("button", "Delete", "button button-quiet"); remove.type = "button";
    remove.addEventListener("click", () => { if (window.confirm("Delete this reminder from its source provider?")) runReminderCommand(item, reminderOperation(item, "delete")); }); actions.append(remove);
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
  return new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "numeric", minute: "2-digit", ...options }).format(new Date(value));
}

function calendarHero(cockpit) {
  const section = document.createElement("section"); section.className = "hero-card calendar-hero";
  const counts = cockpit.counts || {};
  section.append(text("p", "CALENDAR REASONING", "eyebrow"), text("h2", "Your time, without every calendar competing for attention.", "action-title"), text("p", "Provider facts stay intact. Approved rules decide what blocks, what warns, what is context, and where one confirmed Triage item may be written."));
  const stats = document.createElement("div"); stats.className = "calendar-stats";
  for (const [label, value] of [["Hard blocks", counts.hard_block], ["Soft load", counts.soft_hold], ["Context", counts.context], ["Duplicates grouped", counts.duplicates_collapsed], ["Conflicts", counts.conflicts]]) { const node = document.createElement("div"); node.append(text("strong", value || 0), text("span", label)); stats.append(node); }
  section.append(stats); return section;
}

function calendarControls() {
  const controls = document.createElement("section"); controls.className = "calendar-controls card";
  const switcher = document.createElement("div"); switcher.className = "view-switcher";
  for (const [id, label] of [["day", "Day"], ["week", "Week"], ["find", "Find time"], ["rules", "Rules"]]) { const button = text("button", label, "view-button"); button.type = "button"; button.setAttribute("aria-pressed", String(state.calendarView === id)); button.addEventListener("click", () => { state.calendarView = id; renderCalendar(state.surface); }); switcher.append(button); }
  controls.append(switcher); return controls;
}

function calendarEventList(cockpit) {
  const section = document.createElement("section"); section.className = "card calendar-agenda"; section.append(text("h2", state.calendarView === "day" ? "Today" : "The next seven days"));
  const today = new Date().toDateString();
  const events = (cockpit.events || []).filter((event) => state.calendarView !== "day" || new Date(event.start_at).toDateString() === today);
  if (!events.length) { section.append(text("p", "No effective events in this lens. Context calendars and provider records remain connected.", "empty")); return section; }
  const list = document.createElement("ol"); list.className = "calendar-events";
  for (const event of events) { const li = document.createElement("li"); li.className = `calendar-event calendar-effect-${event.effect}`; const heading = document.createElement("div"); heading.className = "calendar-event-heading"; heading.append(text("strong", event.title), text("span", titleCase(event.effect), `pill calendar-effect-pill calendar-effect-${event.effect}`)); li.append(heading, text("span", event.all_day ? `${new Intl.DateTimeFormat(undefined, { weekday: "long", month: "short", day: "numeric" }).format(new Date(event.start_at))} · all day` : `${calendarTime(event.start_at)}–${new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(event.end_at))}`, "meta")); if (event.source_calendars?.length) li.append(text("small", event.source_calendars.join(" · "))); if (event.why?.length) li.append(text("small", event.why.join(" "), "calendar-why")); if (event.duplicate_collapsed) li.append(text("small", `${event.copy_count} provider copies grouped as one effective event.`, "calendar-duplicate")); list.append(li); }
  section.append(list); return section;
}

function calendarFindTime(cockpit) {
  const fragment = document.createDocumentFragment();
  const section = document.createElement("section"); section.className = "card"; section.append(text("h2", "Open windows"), text("p", "Hard blocks, buffers, and protected household time are already removed. Soft load is shown separately so you choose deliberately.", "state-note"));
  const windows = cockpit.open_windows || [];
  if (!windows.length) section.append(text("p", "No unprotected 20-minute window appears in this range.", "empty")); else { const list = document.createElement("ul"); list.className = "time-windows"; for (const window of windows.slice(0, 24)) { const li = document.createElement("li"); li.append(text("strong", calendarTime(window.start_at, { month: "short", day: "numeric", weekday: undefined })), text("span", `until ${calendarTime(window.end_at, { weekday: undefined })}`, "meta")); list.append(li); } section.append(list); }
  const conditional = document.createElement("section"); conditional.className = "card"; conditional.append(text("h2", "Only if accepted")); for (const window of cockpit.conditional_windows || []) conditional.append(text("p", `${window.label}: ${calendarTime(window.start_at)}–${calendarTime(window.end_at, { weekday: undefined })}`, "calendar-conditional")); if (!(cockpit.conditional_windows || []).length) conditional.append(text("p", "No conditional household window in this range.", "empty"));
  fragment.append(section, conditional); return fragment;
}

function calendarConflictPanel(cockpit) {
  const section = document.createElement("section"); section.className = "card"; section.append(text("h2", "Conflicts and pressure"));
  if (!(cockpit.conflicts || []).length) section.append(text("p", "No buffered hard conflict or soft-load pressure is visible.", "empty"));
  for (const conflict of cockpit.conflicts || []) { const row = document.createElement("div"); row.className = `calendar-conflict calendar-${conflict.kind}`; row.append(text("strong", `${titleCase(conflict.kind)} · ${conflict.minutes} minutes`), text("span", conflict.why, "state-note")); section.append(row); }
  return section;
}

function calendarRuleEditor(cockpit, data) {
  const section = document.createElement("section"); section.className = "card calendar-rules"; section.append(text("h2", "Calendar rules"), text("p", "These are explicit household policy, not model guesses. Re-saving replaces each route deterministically.", "state-note"));
  const form = document.createElement("form"); form.className = "calendar-rule-form";
  const existing = new Map((cockpit.rules || []).map((rule) => [`${rule.provider}|${rule.provider_account_id}|${rule.calendar_external_id}`, rule]));
  for (const source of cockpit.sources || []) { const key = `${source.provider}|${source.provider_account_id}|${source.calendar_external_id}`; const rule = existing.get(key) || {}; const row = document.createElement("fieldset"); row.dataset.source = JSON.stringify(source); row.append(text("legend", source.calendar_name || "Calendar"), text("small", `${titleCase(source.access_role)} · ${source.write_events ? "can write" : "read only"}`, "meta")); const purpose = document.createElement("select"); purpose.dataset.field = "purpose"; for (const value of ["personal", "work", "household", "coparenting", "property", "family_context", "holidays", "test", "other"]) { const option = new Option(titleCase(value), value); option.selected = (rule.purpose || "other") === value; purpose.append(option); } const effect = document.createElement("select"); effect.dataset.field = "effect"; for (const value of ["hard_block", "soft_hold", "context", "ignore"]) { const option = new Option(titleCase(value), value); option.selected = (rule.effect || "context") === value; effect.append(option); } const write = document.createElement("input"); write.type = "checkbox"; write.dataset.field = "write"; write.checked = Boolean(rule.write_target); write.disabled = !source.write_events; const writeLabel = document.createElement("label"); writeLabel.append(write, document.createTextNode(" approved write destination")); row.append(purpose, effect, writeLabel); form.append(row); }
  const save = text("button", "Save reviewed rules", "button button-primary"); save.type = "submit"; form.append(save);
  form.addEventListener("submit", async (event) => { event.preventDefault(); save.disabled = true; const rules = [...form.querySelectorAll("fieldset")].map((row) => { const source = JSON.parse(row.dataset.source); const old = existing.get(`${source.provider}|${source.provider_account_id}|${source.calendar_external_id}`) || {}; const writeTarget = row.querySelector('[data-field="write"]').checked; const purpose = row.querySelector('[data-field="purpose"]').value; const writableKinds = ["personal", "work", "household", "coparenting", "property"]; return { source_member_id: source.member_id, provider: source.provider, provider_account_id: source.provider_account_id, calendar_external_id: source.calendar_external_id, purpose, effect: row.querySelector('[data-field="effect"]').value, tentative_effect: old.tentative_effect || "soft_hold", caregiving_load: old.caregiving_load || "ignore", caregiving_summary_values: old.caregiving_summary_values || [], buffer_kind: old.buffer_kind || "auto", write_target: writeTarget, destination_kind: writeTarget ? (old.destination_kind || (writableKinds.includes(purpose) ? purpose : "personal")) : null, enabled: true }; }); try { await api("/api/member/v1/calendar/onboarding", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ request_id: crypto.randomUUID(), member_id: data.member_id, timezone: cockpit.timezone, rules, windows: cockpit.schedule_policy?.windows || [], buffer_minutes: cockpit.schedule_policy?.buffer_minutes || {}, accepted_chores_required: cockpit.schedule_policy?.accepted_chores_required ?? true, one_confirm_write: cockpit.schedule_policy?.one_confirm_write ?? true }) }); setBanner("Calendar rules saved. Availability was recalculated from provider facts."); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} Calendar rules were not changed.`); save.disabled = false; } });
  section.append(form); return section;
}

function renderCalendar(data) {
  const root = $("#surface"); root.replaceChildren(); const cockpit = data.cockpit || {};
  $("#mode-label").textContent = "Private · deterministic calendar policy";
  root.append(calendarHero(cockpit), calendarControls());
  if (state.calendarView === "find") root.append(calendarFindTime(cockpit), calendarConflictPanel(cockpit));
  else if (state.calendarView === "rules") root.append(calendarRuleEditor(cockpit, data));
  else root.append(calendarEventList(cockpit), calendarConflictPanel(cockpit));
}

function renderReminders(data) {
  const root = $("#surface"); root.replaceChildren(); const cockpit = data.cockpit;
  $("#mode-label").textContent = "Private · provider-backed · deterministic";
  root.append(remindersHero(cockpit), remindersControls(cockpit, data));
  const visible = cockpit.items.filter(reminderMatches);
  if (!state.reminderItem || !visible.some((item) => item.id === state.reminderItem)) state.reminderItem = visible[0]?.id || null;
  const selected = visible.find((item) => item.id === state.reminderItem);
  const workspace = document.createElement("div"); workspace.className = "reminder-workspace";
  const list = document.createElement("section"); list.className = "card reminder-list"; list.append(text("h2", `${titleCase(state.remindersView)} reminders`));
  if (!visible.length) list.append(text("p", "Nothing in this lens. Your provider truth is unchanged.", "empty")); else { const items = document.createElement("ol"); items.className = "reminder-items"; for (const item of visible) { const li = document.createElement("li"); li.append(reminderItemButton(item)); items.append(li); } list.append(items); }
  workspace.append(list, reminderDetail(selected, cockpit)); root.append(workspace, reminderQuickAdd(cockpit), reminderProviderTruth(cockpit));
}

function renderSurface(data) {
  state.surface = data;
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
    root.append(triageSurface(data.items, data.calendar_destinations));
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
