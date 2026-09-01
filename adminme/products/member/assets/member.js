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
  triageSort: "attention",
  triageSource: "all",
  triageKind: "all",
  triageSelected: null,
  triageMatter: null,
  triageFocusPending: false,
  triageBannerKind: null,
  triageRefreshRequired: false,
  triageLoadGeneration: 0,
  triageCursorRefreshPending: false,
  triageOperationGeneration: 0,
  triageOperationPending: false,
  addForReviewOperationGeneration: 0,
  addForReviewOperationPending: false,
  addForReviewRecoveryRequired: false,
  commsView: "focus",
  commsAccount: "all",
  commsThread: null,
  remindersView: "now",
  remindersAccount: "all",
  remindersList: "all",
  reminderItem: null,
  reminderDeepLinkPending: false,
  calendarView: "week",
  calendarAnchor: null,
  calendarToday: null,
  calendarLens: "capacity",
  calendarHiddenSources: [],
  calendarSelectedEvent: null,
  calendarDuration: 60,
  peopleLens: "all",
  peopleSelected: null,
  peopleQuery: "",
  peopleResults: null,
  peopleDetail: null,
  notesLens: "all",
  notesSource: "all",
  notesSelected: null,
  notesQuery: "",
  notesResults: null,
  financeView: "overview",
  financeScenario: null,
  settingsSection: "overview",
};
const $ = (selector) => document.querySelector(selector);
const dialogReturnFocus = new WeakMap();
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
  if (active === "triage") queueMicrotask(() => { nav.scrollLeft = 0; });
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
    const meta = text("span", status ? memberStatusLabel(status) : item.occurred_at || item.due_at || item.due_date || "Ready", "meta");
    if (status) meta.classList.add("state-label", `state-${String(status).replaceAll("_", "-")}`);
    li.append(meta);
    if (item.reason) li.append(text("small", item.reason));
    if (item.surface_link?.href) {
      const open = text("a", "Open item", "link-button"); open.href = item.surface_link.href; li.append(open);
    }
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

function moneyMinor(value, { compact = false } = {}) {
  const amount = Number(value || 0) / 100;
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: compact ? 0 : 2,
    notation: compact ? "compact" : "standard",
  }).format(amount);
}

function triageReviewView(item) {
  if (item?.decision_disposition && item.decision_disposition !== "decision") return "all_candidates";
  if (item?.review_state?.state === "needs_interpretation") return "needs_interpretation";
  if (item?.review_state?.state === "needs_details") return "needs_details";
  if (item?.review_state?.state === "ready") return item.queue_lane === "standard" ? "low_signal" : "ready";
  return "all_candidates";
}

function triageLanePresentation(lane) {
  return {
    security: ["Account or security", "A reviewed security phrase appears in this already-eligible item."],
    time_bound: ["Date or deadline", "This proposal contains an actual date or deadline."],
    financial: ["Money", "This proposal contains a reviewed bill or payment signal."],
    member_promise: ["Your promise", "This appears to describe something you agreed to do."],
    member_priority: ["Your priority", "You or a typed source marked this as a priority."],
    known_human_request: ["Known person request", "A known correspondent with prior interaction made an actionable request."],
    recurring_exception: ["Recurring issue", "A recurrence appears missed, overdue, failed, or exceptional."],
    standard: ["Other review", "No explicit security, date, money, promise, priority, or recurring-exception fact was found."],
  }[lane] || ["Review", "This proposal is waiting for a human decision."];
}

function triageCandidateDate(item) {
  const candidate = item?.candidate || {};
  return candidate.due || candidate.due_at || candidate.due_date || candidate.deadline || candidate.start || candidate.start_at || null;
}

function triageDateLabel(value) {
  if (!value) return null;
  const raw = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    const [year, month, day] = raw.split("-").map(Number);
    return new Date(year, month - 1, day).toLocaleDateString(undefined, { dateStyle: "medium" });
  }
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.valueOf())) return String(value);
  const hasTime = raw.includes("T");
  return parsed.toLocaleString(undefined, hasTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" });
}

function triageRequestParams({ cursor = null } = {}) {
  const params = new URLSearchParams({ triage_lens: "all_candidates", triage_sort: state.triageSort });
  if (state.triageSource !== "all") params.set("triage_source", state.triageSource);
  if (state.triageKind !== "all") params.set("triage_kind", state.triageKind);
  if (cursor) params.set("triage_cursor", cursor);
  params.set("triage_limit", "30");
  return params;
}

async function reloadActiveSurface({ triageOperationToken = null, triageFocus = true, triageReconcileExact = false } = {}) {
  const active = currentSurface();
  if (active.startsWith("quest:")) return;
  let path = `/api/member/v1/surfaces/${encodeURIComponent(active)}`;
  if (active === "calendar") {
    const params = new URLSearchParams({ view: state.calendarView });
    if (state.calendarAnchor) params.set("date", state.calendarAnchor);
    path += `?${params}`;
  }
  if (active === "triage") {
    const params = triageRequestParams();
    if (triageFocus && state.triageSelected) params.set("triage_item", state.triageSelected);
    path += `?${params}`;
  }
  if (active === "notes") {
    state.notesResults = null; state.notesQuery = ""; replaceQuery({ q: null });
  }
  if (active === "people") {
    state.peopleResults = null; state.peopleQuery = ""; state.peopleDetail = null;
    replaceQuery({ q: null });
  }
  const data = await api(path);
  if (active === "triage") {
    if (triageOperationToken != null && !triageOperationIsCurrent(triageOperationToken)) return null;
    if (state.triageRefreshRequired) return null;
    if (triageReconcileExact && state.triageSelected) {
      const exact = [...(data.items || []), ...(data.recent_items || [])].find((candidate) => candidate.proposal_id === state.triageSelected);
      if (data.focus?.state === "resolved") state.triageView = "recent";
      else if (data.focus?.state === "pending" && exact && !triageCandidateMatches(exact, state.triageView)) state.triageView = triageReviewView(exact);
      state.triageMatter = data.focus?.state === "not_found" ? null : data.focus?.matter_id || state.triageMatter;
      state.triageFocusPending = data.focus?.state !== "not_found";
      replaceQuery({ view: state.triageView, item: state.triageSelected, matter: state.triageMatter });
    }
  }
  renderSurface(data, { preserveTriageView: triageReconcileExact });
  return data;
}

function triageInteractionLocked() {
  return state.triageOperationPending || state.triageCursorRefreshPending || state.triageRefreshRequired;
}

function triageLockControls() {
  return document.querySelectorAll(
    "#surface button, #surface input, #surface select, #surface textarea, "
    + "#add-for-review, #action-dialog .add-for-review-form button:not([data-add-recovery]), "
    + "#action-dialog .add-for-review-form input, #action-dialog .add-for-review-form select, "
    + "#action-dialog .add-for-review-form textarea, #action-dialog [data-close-action]",
  );
}

function applyTriageInteractionLock() {
  if (!triageInteractionLocked()) return;
  for (const control of triageLockControls()) {
    if (!control.dataset.triagePriorDisabled) {
      control.dataset.triagePriorDisabled = control.dataset.addForReviewPriorDisabled ?? String(control.disabled);
    }
    control.disabled = true;
  }
}

function releaseTriageInteractionLock() {
  for (const control of document.querySelectorAll("[data-triage-prior-disabled]")) {
    control.disabled = control.dataset.triagePriorDisabled === "true" || addForReviewInteractionLocked();
    delete control.dataset.triagePriorDisabled;
  }
}

function addForReviewInteractionLocked() {
  return state.addForReviewOperationPending || state.addForReviewRecoveryRequired;
}

function addForReviewLockControls() {
  return document.querySelectorAll(
    "#add-for-review, #action-dialog .add-for-review-form button:not([data-add-recovery]), "
    + "#action-dialog .add-for-review-form input, #action-dialog .add-for-review-form select, "
    + "#action-dialog .add-for-review-form textarea, #action-dialog [data-close-action]",
  );
}

function applyAddForReviewInteractionLock() {
  if (!addForReviewInteractionLocked()) return;
  for (const control of addForReviewLockControls()) {
    if (!control.dataset.addForReviewPriorDisabled) {
      control.dataset.addForReviewPriorDisabled = control.dataset.triagePriorDisabled ?? String(control.disabled);
    }
    control.disabled = true;
  }
}

function releaseAddForReviewInteractionLock() {
  for (const control of document.querySelectorAll("[data-add-for-review-prior-disabled]")) {
    control.disabled = control.dataset.addForReviewPriorDisabled === "true" || triageInteractionLocked();
    delete control.dataset.addForReviewPriorDisabled;
  }
}

function beginAddForReviewOperation() {
  if (addForReviewInteractionLocked()) return null;
  state.addForReviewOperationPending = true;
  state.addForReviewOperationGeneration += 1;
  applyAddForReviewInteractionLock();
  return state.addForReviewOperationGeneration;
}

function addForReviewOperationIsCurrent(token) {
  return state.addForReviewOperationPending && token === state.addForReviewOperationGeneration;
}

function finishAddForReviewOperation(token) {
  if (!addForReviewOperationIsCurrent(token)) return;
  state.addForReviewOperationPending = false;
  state.addForReviewOperationGeneration += 1;
  if (state.addForReviewRecoveryRequired) applyAddForReviewInteractionLock();
  else releaseAddForReviewInteractionLock();
}

function latchAddForReviewRecovery(message, recoveryHref = null) {
  state.addForReviewRecoveryRequired = true;
  const banner = $("#state-banner");
  banner.hidden = false;
  banner.textContent = message;
  const form = document.querySelector("#action-dialog .add-for-review-form");
  if (form) {
    let recovery = form.querySelector("[data-add-recovery-status]");
    if (!recovery) {
      recovery = document.createElement("section");
      recovery.className = "state-card";
      recovery.dataset.addRecoveryStatus = "true";
      recovery.setAttribute("role", "alert");
      const recoveryMessage = text("p", message, "state-note");
      const destination = recoveryHref || "/member/triage?view=all_candidates";
      const reload = text("button", recoveryHref ? "Open saved Triage item" : "Check Triage before retrying", "button button-primary");
      reload.type = "button";
      reload.dataset.addRecovery = "true";
      reload.addEventListener("click", () => window.location.assign(destination));
      recovery.append(recoveryMessage, reload);
      form.append(recovery);
    }
  }
  applyAddForReviewInteractionLock();
  const recoveryControl = form?.querySelector("[data-add-recovery]");
  if (recoveryControl) {
    recoveryControl.disabled = false;
    delete recoveryControl.dataset.addForReviewPriorDisabled;
    recoveryControl.focus();
  }
}

function invalidateTriageLoad() {
  state.triageLoadGeneration += 1;
  const button = document.querySelector("[data-triage-more]");
  if (button?.dataset.triageLoading === "true") {
    button.disabled = false;
    button.textContent = "Show more";
    delete button.dataset.triageLoading;
  }
}

function beginTriageOperation() {
  if (triageInteractionLocked()) return null;
  invalidateTriageLoad();
  state.triageOperationPending = true;
  state.triageOperationGeneration += 1;
  applyTriageInteractionLock();
  return state.triageOperationGeneration;
}

function triageOperationIsCurrent(token) {
  return state.triageOperationPending && token === state.triageOperationGeneration;
}

function finishTriageOperation(token) {
  if (!triageOperationIsCurrent(token)) return;
  state.triageLoadGeneration += 1;
  state.triageOperationPending = false;
  state.triageOperationGeneration += 1;
  if (state.triageRefreshRequired) applyTriageInteractionLock();
  else releaseTriageInteractionLock();
}

function latchTriageRefreshFailure(message) {
  state.triageBannerKind = "refresh_failure";
  state.triageRefreshRequired = true;
  const banner = $("#state-banner");
  banner.hidden = false;
  banner.textContent = message;
  applyTriageInteractionLock();
}

function requestDefinitelyRejected(error) {
  if (error.body?.detail?.capture_state === "intent_and_features_recorded") return false;
  return Number.isInteger(error.status) && error.status >= 400 && error.status < 500 && error.status !== 408;
}

function triageDecisionHasDurableReceipt(item, action, requestId) {
  if (!item) return false;
  if (action === "dismiss") {
    return item.state === "dismissed"
      && Boolean(item.dismissal_event_id)
      && item.dismissal_request_id === requestId;
  }
  if (!item.confirmation_event_id || item.confirmation_request_id !== requestId) return false;
  if (item.state === "approved") {
    return ["approved_awaiting_openclaw", "approved_awaiting_provider"].includes(item.resolution_reason)
      && Boolean(item.domain_approval_event_id)
      && Boolean(item.materialization_event_id);
  }
  if (item.state === "failed") return Boolean(item.outcome_event_id);
  if (item.state !== "confirmed") return false;
  return Boolean(item.outcome_event_id) || (item.materialized_event_ids || []).length > 0;
}

async function reconcileTriageDecision(item, action, requestId, token, error, banner, { mayReportRejection = false } = {}) {
  try {
    const refreshed = await reloadActiveSurface({ triageOperationToken: token, triageReconcileExact: true });
    if (!refreshed || !triageOperationIsCurrent(token)) return "unresolved";
    const exact = [...(refreshed.items || []), ...(refreshed.recent_items || [])].find((candidate) => candidate.proposal_id === item.proposal_id);
    if (refreshed.focus?.requested_candidate_id === item.proposal_id && exact && triageDecisionHasDurableReceipt(exact, action, requestId)) {
      const operation = action === "approve" ? "confirmation" : "dismissal";
      state.triageBannerKind = "reconciliation";
      banner.hidden = false;
      banner.textContent = `The ${operation} response was interrupted, but Triage reconciled this exact candidate as ${memberStatusLabel(exact.state).toLowerCase()}. Review this receipt before taking another action.`;
      return "settled";
    }
    if (mayReportRejection && refreshed.focus?.requested_candidate_id === item.proposal_id && ["pending_interpretation", "pending_review"].includes(exact?.state)) {
      state.triageBannerKind = "error";
      banner.hidden = false;
      banner.textContent = `${error.message} Nothing was confirmed or dismissed.`;
      return "rejected";
    }
    if (action === "approve" && exact?.state === "approved" && exact.domain_approval_event_id) return "continuation_required";
    return "unresolved";
  } catch (refreshError) {
    return "unresolved";
  }
}

async function decideTriage(item, action, buttons, feedbackReason = null, { operationToken = null, contextCorrection = null } = {}) {
  const token = operationToken ?? beginTriageOperation();
  if (token == null || !triageOperationIsCurrent(token)) return;
  const banner = $("#state-banner"); banner.hidden = false;
  state.triageBannerKind = "progress";
  banner.textContent = action === "approve" ? "Confirming this candidate through the household gate…" : "Dismissing this candidate…";
  const workingView = state.triageView === "recent" ? "all_candidates" : state.triageView;
  let decisionRecorded = false;
  const decisionRequestId = crypto.randomUUID();
  const decisionRequest = {
    method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
    body: JSON.stringify({ request_id: decisionRequestId, expected_last_event_id: item.last_event_id, reason: action === "dismiss" ? "Dismissed from Triage" : null, feedback_reason: action === "dismiss" ? feedbackReason : null, context_correction: action === "dismiss" ? contextCorrection : null }),
  };
  try {
    const decisionPath = `/api/member/v1/triage/${encodeURIComponent(item.proposal_id)}/${action}`;
    let result;
    try {
      result = await api(decisionPath, decisionRequest);
    } catch (initialError) {
      const definitelyRejected = requestDefinitelyRejected(initialError);
      const reconciliation = await reconcileTriageDecision(
        item, action, decisionRequestId, token, initialError, banner, { mayReportRejection: definitelyRejected },
      );
      if (["settled", "rejected"].includes(reconciliation)) return;
      if (definitelyRejected && reconciliation !== "continuation_required") {
        latchTriageRefreshFailure(`The decision failed and Triage could not prove this exact candidate remained undecided: ${initialError.message} Reload this page before continuing.`);
        return;
      }
      try {
        result = await api(decisionPath, decisionRequest);
      } catch (retryError) {
        const retryReconciliation = await reconcileTriageDecision(item, action, decisionRequestId, token, retryError, banner);
        if (["settled", "rejected"].includes(retryReconciliation)) return;
        latchTriageRefreshFailure(`The decision response was interrupted and Triage could not prove an action-specific receipt after an idempotent retry: ${retryError.message} Reload this page before continuing.`);
        return;
      }
    }
    decisionRecorded = true;
    state.triageView = workingView;
    state.triageSelected = null;
    state.triageMatter = null;
    state.triageFocusPending = false;
    replaceQuery({ view: workingView, item: null, matter: null });
    const refreshed = await reloadActiveSurface({ triageOperationToken: token });
    if (!refreshed || !triageOperationIsCurrent(token)) return;
    const hasNextDecision = triageVisibleMatters(refreshed, workingView).length > 0;
    const continuation = hasNextDecision
      ? "The next decision in this view is ready."
      : refreshed.triage?.has_more
        ? "No other decision is loaded in this view. Load more matters or choose another view."
        : "You're caught up in this view.";
    state.triageBannerKind = "continuation";
    banner.replaceChildren();
    if (action === "approve") {
      if (result.destination) {
        const pending = result.destination.state === "awaiting_openclaw_approval";
        banner.append(document.createTextNode(pending ? `Confirmed. The governed provider action is awaiting approval. ${continuation} ` : `Confirmed and added to ${result.destination.label || titleCase(result.destination.surface)}. ${continuation} `));
        const link = text("a", `Open ${result.destination.label || titleCase(result.destination.surface)}`, "link-button"); link.href = result.destination.href; banner.append(link);
      } else banner.textContent = `Confirmed through the household gate. ${continuation}`;
    } else {
      banner.textContent = contextCorrection ? `Dismissed. The exact context correction was recorded and can be reversed. ${continuation}` : feedbackReason ? `Dismissed. No household record was created. Your reversible feedback was recorded. ${continuation}` : `Dismissed. No household record or provider write was created. ${continuation}`;
    }
  } catch (error) {
    if (decisionRecorded) {
      latchTriageRefreshFailure(`The decision was recorded, but Triage could not refresh: ${error.message} Reload this page before deciding another item.`);
    }
    else latchTriageRefreshFailure(`Triage could not finish or reconcile this decision: ${error.message} Reload this page before continuing.`);
  } finally {
    finishTriageOperation(token);
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

function triageDateTimeParts(value) {
  if (!value) return { date: "", time: "" };
  const raw = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return { date: raw, time: "" };
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return { date: raw.slice(0, 10), time: raw.slice(11, 16) };
  return {
    date: `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, "0")}-${String(parsed.getDate()).padStart(2, "0")}`,
    time: `${String(parsed.getHours()).padStart(2, "0")}:${String(parsed.getMinutes()).padStart(2, "0")}`,
  };
}

function triageFieldControl(spec, candidate, data) {
  const current = candidate?.[spec.name] ?? spec.default;
  if (spec.control === "date_time") {
    const parts = triageDateTimeParts(current);
    const wrapper = document.createElement("div"); wrapper.className = "date-time-control";
    const dateInput = document.createElement("input"); dateInput.type = "date"; dateInput.name = `${spec.name}__date`; dateInput.value = parts.date;
    dateInput.setAttribute("aria-label", `${spec.label} date`);
    const timeInput = document.createElement("input"); timeInput.type = "time"; timeInput.name = `${spec.name}__time`; timeInput.value = parts.time;
    timeInput.setAttribute("aria-label", `${spec.label} time (optional)`);
    wrapper.append(dateInput);
    if (!spec.date_only) wrapper.append(timeInput);
    return wrapper;
  }
  if (spec.control === "date") {
    const input = document.createElement("input"); input.type = "date"; input.name = spec.name; input.value = current ? String(current).slice(0, 10) : ""; return input;
  }
  if (spec.control === "textarea") {
    const input = document.createElement("textarea"); input.name = spec.name; input.rows = 3; input.maxLength = 20000; input.value = current || ""; return input;
  }
  if (spec.control === "select") {
    const options = (spec.options || []).map((value) => ({ value, label: titleCase(value) }));
    const input = selectControl(options, current || "", "Not set"); input.name = spec.name; return input;
  }
  if (spec.control === "party") {
    const options = (data.destinations?.people || []).map((person) => ({ value: person.party_id, label: `${person.label} · ${titleCase(person.kind)}` }));
    const input = selectControl(options, current || "", "Choose a person…"); input.name = spec.name; return input;
  }
  if (spec.control === "checkbox") {
    const input = document.createElement("input"); input.type = "checkbox"; input.name = spec.name; input.checked = Boolean(current); return input;
  }
  if (spec.control === "number") {
    const input = document.createElement("input"); input.type = "number"; input.name = spec.name; input.value = current ?? ""; input.min = spec.minimum ?? 0; if (spec.maximum != null) input.max = spec.maximum; return input;
  }
  if (spec.control === "priority") {
    const input = selectControl([{ value: "0", label: "None" }, { value: "1", label: "High" }, { value: "5", label: "Medium" }, { value: "9", label: "Low" }], String(current ?? 0), ""); input.name = spec.name; return input;
  }
  if (spec.control === "recurrence") {
    const existing = Array.isArray(current) ? current[0] : current;
    const known = new Map([
      ["FREQ=DAILY", "daily"],
      ["FREQ=WEEKLY", "weekly"],
      ["FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "weekdays"],
      ["FREQ=MONTHLY", "monthly"],
      ["FREQ=YEARLY", "yearly"],
    ]);
    const selected = known.get(String(existing || "").replace(/^RRULE:/, "")) || (existing ? "existing" : "none");
    const options = [
      { value: "none", label: "Does not repeat" }, { value: "daily", label: "Daily" },
      { value: "weekdays", label: "Weekdays" }, { value: "weekly", label: "Weekly" },
      { value: "monthly", label: "Monthly" }, { value: "yearly", label: "Yearly" },
    ];
    if (selected === "existing") options.push({ value: `existing:${existing}`, label: "Keep existing custom pattern" });
    const input = selectControl(options, selected === "existing" ? `existing:${existing}` : selected, ""); input.name = `${spec.name}__repeat`; return input;
  }
  if (spec.control === "alarm_offset") {
    const seconds = Array.isArray(current) ? Math.abs(Number(current[0]?.relative_offset_seconds || 0)) : 0;
    const minutes = seconds ? String(Math.round(seconds / 60)) : "none";
    const input = selectControl([
      { value: "none", label: "None" }, { value: "0", label: "At due time" },
      { value: "5", label: "5 minutes before" }, { value: "15", label: "15 minutes before" },
      { value: "30", label: "30 minutes before" }, { value: "60", label: "1 hour before" },
      { value: "1440", label: "1 day before" },
    ], minutes, ""); input.name = `${spec.name}__offset`; return input;
  }
  const input = document.createElement("input"); input.type = spec.control === "url" ? "url" : spec.control === "color" ? "color" : "text"; input.name = spec.name; input.value = current || ""; return input;
}

function triageCandidateUpdates(form, targetKind, schema, formValues = null) {
  const values = formValues ?? new FormData(form); const updates = {};
  const activeProvider = String(values.get("provider") || "");
  const target = schema?.targets?.[targetKind] || {};
  const source = target.fields_from ? schema.targets?.[target.fields_from] : target;
  for (const spec of source?.fields || []) {
    if (spec.provider && spec.provider !== activeProvider) continue;
    if (spec.control === "date_time") {
      const dateValue = String(values.get(`${spec.name}__date`) || "");
      const timeValue = String(values.get(`${spec.name}__time`) || "");
      updates[spec.name] = dateValue
        ? activeProvider === "google_tasks" && spec.name === "due"
          ? dateValue
          : timeValue ? `${dateValue}T${timeValue}` : dateValue
        : null;
    } else if (spec.control === "checkbox") {
      updates[spec.name] = values.has(spec.name);
    } else if (spec.control === "number") {
      const value = String(values.get(spec.name) || ""); updates[spec.name] = value ? Number(value) : null;
    } else if (spec.control === "priority") {
      updates[spec.name] = Number(values.get(spec.name) || 0);
    } else if (spec.control === "recurrence") {
      const selected = String(values.get(`${spec.name}__repeat`) || "none");
      const rules = { daily: "FREQ=DAILY", weekdays: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", weekly: "FREQ=WEEKLY", monthly: "FREQ=MONTHLY", yearly: "FREQ=YEARLY" };
      const rule = selected.startsWith("existing:") ? selected.slice(9) : rules[selected];
      updates[spec.name] = spec.name === "recurrence_rules" ? (rule ? [rule] : []) : (rule || null);
    } else if (spec.control === "alarm_offset") {
      const selected = String(values.get(`${spec.name}__offset`) || "none");
      updates[spec.name] = selected !== "none" ? [{ kind: "relative", relative_offset_seconds: -Number(selected) * 60, proximity: "none" }] : [];
    } else {
      const value = String(values.get(spec.name) || "").trim(); updates[spec.name] = value || null;
    }
  }
  return updates;
}

async function saveTriageForm(item, form, { reload = true, operationToken = null, formValues = null } = {}) {
  const values = formValues ?? new FormData(form);
  const ownsOperation = operationToken == null;
  const token = operationToken ?? beginTriageOperation();
  if (token == null || !triageOperationIsCurrent(token)) return null;
  const targetKind = String(values.get("target_kind") || item.target_kind);
  const candidateUpdates = triageCandidateUpdates(form, targetKind, state.surface?.enrichment_schema, values);
  let route = null;
  if (item.source_kind !== "commitment_proposal") {
    route = {
      member_id: String(values.get("member_id") || state.bootstrap.member.id),
      provider: String(values.get("provider") || "") || null,
      provider_account_id: String(values.get("provider_account_id") || "") || null,
      container_external_id: String(values.get("container_external_id") || "") || null,
    };
  }
  setBanner("Saving these details as a proposal—nothing official is being created yet…");
  let saveRecorded = false;
  try {
    const result = await api(`/api/member/v1/triage/${encodeURIComponent(item.proposal_id)}/enrich`, {
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
    if (!triageOperationIsCurrent(token)) return null;
    saveRecorded = true;
    Object.assign(item, result.triage);
    delete form.dataset.dirty;
    delete form.dataset.structuralChanged;
    const confirm = [...form.querySelectorAll("button")].find((button) => ["Save & confirm", "Save destination", "Confirm and route"].includes(button.textContent));
    if (confirm) confirm.textContent = "Confirm and route";
    setBanner("Details saved. Review the destination and preview once more before confirming.");
    if (reload) {
      const refreshed = await reloadActiveSurface({ triageOperationToken: token });
      if (!refreshed || !triageOperationIsCurrent(token)) return null;
    }
    return result.triage;
  } catch (error) {
    if (!triageOperationIsCurrent(token)) return null;
    if (saveRecorded) {
      latchTriageRefreshFailure(`The details were saved, but Triage could not refresh: ${error.message} Reload this page before continuing.`);
    } else setBanner(`${error.message} The proposal was not changed.`);
    return null;
  } finally {
    if (ownsOperation) finishTriageOperation(token);
  }
}

function triageSourceContext(item) {
  const context = item.source_context || {};
  const section = document.createElement("section"); section.className = "triage-source-context";
  section.append(text("p", "WHY THIS IS HERE", "eyebrow"));
  if (context.state !== "available") {
    section.append(text("p", context.state === "withheld" ? "Source context is outside this member view." : "The immutable source is unavailable. Do not confirm until its evidence can be reviewed.", "review-state"));
    return section;
  }
  const heading = document.createElement("div"); heading.className = "triage-source-heading";
  const channel = [context.channel && titleCase(context.channel), context.direction && titleCase(context.direction)].filter(Boolean).join(" · ");
  heading.append(text("strong", channel || titleCase(context.source_event_type || "Source")));
  section.append(heading);
  if (context.provider_category) {
    const category = document.createElement("div"); category.className = "triage-provider-category";
    category.append(text("span", context.provider_category.label, "pill pill-lavender"), text("small", `${context.provider_category.provenance} marked this source as ${context.provider_category.label}. It is context only; it does not decide importance or hide the item.`));
    section.append(category);
  }
  const facts = document.createElement("dl"); facts.className = "triage-source-facts";
  for (const [label, value] of [[context.direction === "outbound" ? "Counterparty" : "Sender", context.sender], ["Subject / source title", context.subject], ["Message date", context.occurred_at ? new Date(context.occurred_at).toLocaleString() : null], ["Imported / recorded", context.recorded_at && context.recorded_at !== context.occurred_at ? new Date(context.recorded_at).toLocaleString() : null]]) {
    if (value) facts.append(text("dt", label), text("dd", value));
  }
  section.append(facts);
  const meaning = context.meaning || {};
  const meaningBox = document.createElement("div"); meaningBox.className = "triage-meaning-box";
  meaningBox.append(text("strong", `AdministrateMe found a possible ${titleCase(meaning.target_kind || item.target_kind || "item")}`), text("p", meaning.summary || item.title));
  if (meaning.extracted_fields?.length) {
    const values = document.createElement("dl"); values.className = "triage-extracted-fields";
    for (const entry of meaning.extracted_fields) values.append(text("dt", entry.label), text("dd", Array.isArray(entry.value) ? entry.value.join(", ") : String(entry.value)));
    meaningBox.append(values);
  }
  section.append(meaningBox);
  const interpretation = context.interpretation;
  if (interpretation) {
    const guide = document.createElement("div"); guide.className = "triage-context-guide";
    guide.append(text("p", "CONTEXT SUMMARY · REVIEW AID", "eyebrow"));
    for (const [label, value] of [["What this is", interpretation.case_summary], ["Why it may need you", interpretation.why_member], ["Smallest next step", interpretation.next_step]]) {
      if (!value) continue;
      const row = document.createElement("div"); row.className = "triage-context-guide-row";
      row.append(text("strong", label), text("p", value)); guide.append(row);
    }
    const status = [interpretation.thread_state && titleCase(interpretation.thread_state), interpretation.action_owner && `Owner: ${titleCase(interpretation.action_owner)}`, interpretation.lifecycle_state && `Status: ${titleCase(interpretation.lifecycle_state)}`].filter(Boolean).join(" · ");
    if (status) guide.append(text("p", status, "meta"));
    if (interpretation.unresolved_questions?.length) {
      guide.append(text("strong", "Still unresolved"));
      const questions = document.createElement("ul"); questions.className = "triage-context-questions";
      for (const question of interpretation.unresolved_questions) questions.append(text("li", question));
      guide.append(questions);
    }
    if (interpretation.source_chain?.length > 1) {
      const chain = document.createElement("details"); chain.className = "triage-source-chain";
      chain.append(text("summary", `${interpretation.source_chain.length} messages used for context`));
      const list = document.createElement("ol");
      for (const source of interpretation.source_chain) {
        const relation = source.relation_reason_codes?.length ? `${titleCase(source.relation)}: ${source.relation_reason_codes.map(titleCase).join(", ")}` : titleCase(source.relation);
        const label = [relation, source.subject || source.sender || titleCase(source.channel), source.occurred_at ? new Date(source.occurred_at).toLocaleString() : null].filter(Boolean).join(" · ");
        list.append(text("li", label));
      }
      chain.append(list); guide.append(chain);
    }
    guide.append(text("small", "This explanation came from bounded language interpretation. Exact source words remain the evidence below; nothing is official until you confirm it.", "state-note"));
    section.append(guide);
  }
  const evidence = context.evidence || {};
  if (evidence.quote) {
    const quote = document.createElement("blockquote"); quote.className = "triage-evidence-quote"; quote.textContent = evidence.quote;
    section.append(text("strong", "Exact source evidence"), quote);
  } else {
    section.append(text("p", "No exact text span applies to this structured provider rule. Review the source facts before confirming.", "state-note"));
  }
  const technical = document.createElement("details"); technical.className = "triage-technical-source";
  technical.append(text("summary", "Technical source details"));
  const coordinates = document.createElement("dl"); coordinates.className = "triage-source-facts";
  for (const [label, value] of [["Source", context.source_id], ["Adapter", context.adapter], ["Account", context.account], ["Event type", context.source_event_type]]) {
    if (value) coordinates.append(text("dt", label), text("dd", value));
  }
  technical.append(coordinates);
  if (context.methods?.length) {
    const methods = document.createElement("div"); methods.className = "triage-methods";
    for (const method of context.methods) { const badge = text("span", method.label, "pill pill-lavender"); badge.title = method.detail; methods.append(badge); }
    technical.append(methods);
  }
  section.append(technical);
  return section;
}

function triageDetail(item, data, readOnly = false) {
  const section = document.createElement("section"); section.className = "card triage-detail";
  if (!item) {
    section.append(text("h2", "Choose a proposal"), text("p", "Select one item to review its evidence, destination, and exact effect.", "empty"));
    return section;
  }
  section.dataset.triageItem = item.proposal_id;
  section.tabIndex = -1;
  const reviewHeading = item.interpretation?.compact_summary || item.title;
  section.append(text("p", readOnly ? "RECENT DECISION" : "YOUR DECISION", "eyebrow"), text("h2", reviewHeading));
  if (!readOnly) {
    const [laneLabel, laneReason] = triageLanePresentation(item.queue_lane);
    const reason = document.createElement("div"); reason.className = "triage-attention-reason";
    reason.append(text("strong", laneLabel), text("span", laneReason));
    section.append(reason);
  }
  section.append(triageSourceContext(item));
  if (readOnly) {
    const eligibilityChanged = item.resolution_reason === "eligibility_rule_replaced";
    const outcome = eligibilityChanged ? "Retired after context or eligibility changed" : item.resolution_reason ? titleCase(item.resolution_reason) : titleCase(item.state);
    section.append(text("p", `${outcome}${item.resolved_at ? ` on ${new Date(item.resolved_at).toLocaleString()}` : ""}.`, `review-state ${item.state === "failed" ? "" : "ready"}`.trim()));
    if (eligibilityChanged) section.append(text("p", "This untouched candidate was replaced after its source context or eligibility changed. It is historical lifecycle evidence, not a sender-wide rule created by this dismissal.", "state-note"));
    if (item.materialized_event_ids?.length) section.append(text("p", `${item.materialized_event_ids.length} audited materialization event${item.materialized_event_ids.length === 1 ? "" : "s"}.`, "meta"));
    if (item.domain_approval_event_id) section.append(text("p", item.materialized_event_ids?.length ? "Your confirmation was recorded before canonical materialization." : "Your domain confirmation is recorded before any provider action.", "meta"));
    if (item.outcome_event_id) section.append(text("p", "A terminal provider-path outcome is linked to this decision.", "meta"));
    if (item.destination) {
      const destination = text("a", `Open ${item.destination.label || titleCase(item.destination.surface)}`, "button button-quiet");
      destination.href = item.destination.href;
      section.append(destination);
    }
    const evidence = text("button", "View source evidence", "link-button"); evidence.type = "button"; evidence.addEventListener("click", () => showEvidence(item.source_event_id)); section.append(evidence);
    return section;
  }

  const form = document.createElement("form"); form.className = "triage-review-form";
  const effect = document.createElement("div"); effect.className = "triage-proposed-effect";
  effect.append(text("p", "PROPOSED EFFECT", "eyebrow"), text("strong", `Create a ${titleCase(item.target_kind || "household item")} for ${state.bootstrap.member.label || "this household member"}.`), text("small", "Nothing becomes official until you confirm. Provider work still passes governance and observation controls."));
  form.append(effect);
  const grid = document.createElement("div"); grid.className = "field-grid";
  const schema = data.enrichment_schema || {};
  const title = document.createElement("input"); title.name = "title"; title.required = true; title.maxLength = 1000; title.value = item.title || "";
  grid.append(field("Review title", title, "field-span"));
  const targetOptions = Object.entries(schema.targets || {}).map(([value, contract]) => ({ value, label: contract.label || titleCase(value) }));
  const target = selectControl(targetOptions, item.target_kind, ""); target.name = "target_kind"; target.disabled = item.source_kind === "commitment_proposal";
  grid.append(field("Creates", target));
  const members = (data.destinations?.members || []).map((value) => ({ value: value.member_id, label: `${value.label} · ${value.role || "member"}` }));
  const member = selectControl(members, item.route?.member_id || state.bootstrap.member.id, "Choose a household member…"); member.name = "member_id"; member.required = true; member.disabled = item.source_kind === "commitment_proposal";
  grid.append(field("Household member", member));

  const candidate = item.candidate || {};
  const targetSchema = schema.targets?.[item.target_kind] || {};
  const fieldSchema = targetSchema.fields_from ? schema.targets?.[targetSchema.fields_from] : targetSchema;
  const activeProvider = item.route?.provider || "";
  for (const spec of fieldSchema?.fields || []) {
    if (spec.provider && spec.provider !== activeProvider) continue;
    const currentCandidate = { ...candidate };
    if (spec.name === "due_date" && !currentCandidate.due_date) currentCandidate.due_date = candidate.due || candidate.due_at;
    if (spec.name === "notes" && currentCandidate.notes == null) currentCandidate.notes = candidate.description;
    if (spec.name === "start" && item.target_kind === "calendar_event" && !currentCandidate.start) currentCandidate.start = candidate.due;
    const effectiveSpec = activeProvider === "google_tasks"
      && ["reminder", "list_item"].includes(item.target_kind)
      && spec.name === "due"
      ? { ...spec, label: "Scheduled date", date_only: true }
      : spec;
    grid.append(field(effectiveSpec.label, triageFieldControl(effectiveSpec, currentCandidate, data), effectiveSpec.control === "textarea" ? "field-span" : ""));
  }
  if (targetSchema.capability_note) grid.append(text("p", targetSchema.capability_note, "state-note field-span"));
  if (activeProvider && schema.provider_notes?.[activeProvider]) grid.append(text("p", schema.provider_notes[activeProvider], "state-note field-span"));

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
    provider.addEventListener("change", () => { if (triageInteractionLocked()) return; refreshAccounts(); form.dataset.structuralChanged = "true"; });
    account.addEventListener("change", () => { if (triageInteractionLocked()) return; refreshContainers(); form.dataset.structuralChanged = "true"; });
    container.addEventListener("change", () => { if (triageInteractionLocked()) return; form.dataset.structuralChanged = "true"; });
    refreshAccounts(item.route?.provider_account_id || "", item.route?.container_external_id || "");
    grid.append(field("Provider", provider), field("Account", account), field(item.target_kind === "calendar_event" ? "Calendar" : "List", container));
  }
  const editDetails = document.createElement("details"); editDetails.className = "triage-edit-details";
  editDetails.open = item.review_state?.state !== "ready";
  editDetails.append(text("summary", item.review_state?.state === "ready" ? "Edit details or destination" : "Complete the missing details"), grid);
  form.append(editDetails);
  let contextCorrection = null;
  const correctionCapabilities = item.source_context?.correction_capabilities || {};
  const correctableRelatedSourceIds = new Set(Array.isArray(correctionCapabilities.unrelated_source_event_ids) ? correctionCapabilities.unrelated_source_event_ids : []);
  const relatedContextSources = (item.source_context?.interpretation?.source_chain || []).filter((source) => source.relation === "related" && source.source_event_id && correctableRelatedSourceIds.has(source.source_event_id));
  const canCorrectQuotedHistory = correctionCapabilities.quoted_or_forwarded_history === true;
  if (item.source_context?.state === "available" && (relatedContextSources.length || canCorrectQuotedHistory)) {
    contextCorrection = document.createElement("fieldset"); contextCorrection.className = "triage-context-correction"; contextCorrection.hidden = true;
    contextCorrection.append(text("legend", "Correct this exact context"), text("p", "These corrections affect only the selected source relationship or quoted text. They do not create a sender-, account-, or keyword-wide rule.", "state-note"));
    for (const source of relatedContextSources) {
      const control = document.createElement("input"); control.type = "checkbox"; control.dataset.unrelatedSourceEventId = source.source_event_id;
      const sourceLabel = [source.subject || source.sender || "Related message", source.occurred_at ? new Date(source.occurred_at).toLocaleString() : null].filter(Boolean).join(" · ");
      const wrapper = document.createElement("label"); wrapper.className = "check-control"; wrapper.append(control, document.createTextNode(` This message is unrelated: ${sourceLabel}`)); contextCorrection.append(wrapper);
    }
    if (canCorrectQuotedHistory) {
      const quoted = document.createElement("input"); quoted.type = "checkbox"; quoted.dataset.quotedOrForwardedHistory = "true";
      const quotedWrapper = document.createElement("label"); quotedWrapper.className = "check-control"; quotedWrapper.append(quoted, document.createTextNode(" The displayed ask came only from quoted or forwarded history, not newly authored text")); contextCorrection.append(quotedWrapper);
    }
    form.append(contextCorrection);
  }
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
  const decisionEligible = item.decision_disposition === "decision";
  const review = text(
    "p",
    !decisionEligible
      ? item.decision_disposition === "attach_existing"
        ? "This matches existing household truth. It can be attached as evidence or edited into a distinct item, but it cannot create duplicate work."
        : "This is supporting evidence, not a separate decision. It can be edited or dismissed, but it cannot create household truth."
      : missing.length
      ? `Needs: ${missing.map(titleCase).join(", ")}. Save the missing details before confirming.`
      : "Ready for your final confirmation. Every real write still passes governance and observation controls.",
    `review-state ${decisionEligible && !missing.length ? "ready" : ""}`.trim(),
  );
  form.append(review);
  const row = document.createElement("div"); row.className = "button-row";
  const save = text("button", "Save details", "button button-quiet"); save.type = "submit";
  const approve = text("button", "Confirm and route", "button button-primary"); approve.type = "button"; approve.disabled = missing.length > 0 || !decisionEligible;
  const feedback = document.createElement("select"); feedback.setAttribute("aria-label", "Why this candidate should leave Triage");
  for (const [value, label] of [["", "Choose dismissal reason…"], ["not_actionable", "Not actionable"], ["already_done", "Already done"], ["duplicate", "Duplicate"], ["wrong_kind", "Wrong kind"], ["wrong_context", "Wrong context"], ["wrong_owner", "Wrong owner"], ["not_mine", "Not mine"], ["recurring_normal", "Normal recurrence"], ["low_signal", "Low signal"], ["provider_promotion", "Promotion / marketing"], ["spam", "Spam"], ["other", "Other"]]) { const option = document.createElement("option"); option.value = value; option.textContent = label; feedback.append(option); }
  const dismiss = text("button", "Dismiss & teach", "button button-quiet"); dismiss.type = "button"; dismiss.disabled = true;
  const evidence = text("button", "View source evidence", "link-button"); evidence.type = "button";
  const buttons = [save, approve, dismiss];
  form.addEventListener("submit", (event) => { event.preventDefault(); saveTriageForm(item, form); });
  const markDirty = (event) => { if (triageInteractionLocked() || event?.target?.closest?.(".triage-context-correction")) return; form.dataset.dirty = "true"; approve.textContent = form.dataset.structuralChanged ? "Save destination" : "Save & confirm"; };
  target.addEventListener("change", () => { if (triageInteractionLocked()) return; form.dataset.structuralChanged = "true"; markDirty(); });
  member.addEventListener("change", () => { if (triageInteractionLocked()) return; form.dataset.structuralChanged = "true"; markDirty(); });
  form.addEventListener("input", markDirty);
  form.addEventListener("change", markDirty);
  approve.addEventListener("click", async () => {
    if (form.dataset.dirty) {
      const formValues = new FormData(form);
      const operationToken = beginTriageOperation();
      if (operationToken == null) return;
      const structuralChange = Boolean(form.dataset.structuralChanged);
      const saved = await saveTriageForm(item, form, { reload: structuralChange, operationToken, formValues });
      if (!saved || structuralChange) {
        finishTriageOperation(operationToken);
        return;
      }
      if (saved.review_state?.state !== "ready") {
        state.triageView = saved.review_state?.state === "needs_interpretation" ? "needs_interpretation" : "needs_details";
        replaceQuery({ view: state.triageView });
        try {
          const refreshed = await reloadActiveSurface({ triageOperationToken: operationToken, triageReconcileExact: true });
          if (refreshed && triageOperationIsCurrent(operationToken)) {
            setBanner("Details saved. This candidate needs more review before it can be confirmed.");
          }
        } catch (error) {
          if (triageOperationIsCurrent(operationToken)) {
            latchTriageRefreshFailure(`The details were saved and now need more review, but Triage could not refresh: ${error.message} Reload this page before continuing.`);
          }
        } finally {
          finishTriageOperation(operationToken);
        }
        return;
      }
      await decideTriage(saved, "approve", buttons, null, { operationToken });
      return;
    }
    await decideTriage(item, "approve", buttons);
  });
  feedback.addEventListener("change", () => { if (triageInteractionLocked()) return; dismiss.disabled = !feedback.value; if (contextCorrection) contextCorrection.hidden = feedback.value !== "wrong_context"; });
  dismiss.addEventListener("click", () => {
    const unrelatedSourceEventIds = contextCorrection ? [...contextCorrection.querySelectorAll("[data-unrelated-source-event-id]:checked")].map((control) => control.dataset.unrelatedSourceEventId) : [];
    const quotedOrForwardedHistory = Boolean(contextCorrection?.querySelector("[data-quoted-or-forwarded-history]:checked"));
    const correction = feedback.value === "wrong_context" && (unrelatedSourceEventIds.length || quotedOrForwardedHistory) ? { unrelated_source_event_ids: unrelatedSourceEventIds, quoted_or_forwarded_history: quotedOrForwardedHistory } : null;
    decideTriage(item, "dismiss", buttons, feedback.value, { contextCorrection: correction });
  });
  evidence.addEventListener("click", () => showEvidence(item.source_event_id));
  row.append(save, approve, feedback, dismiss, evidence); form.append(row); section.append(form);
  return section;
}

function triageCandidateMatches(item, view) {
  if (view === "recent") return false;
  if (view === "all_candidates") return true;
  if (view === "needs_interpretation") return item.review_state?.state === "needs_interpretation";
  if (view === "needs_details") return item.review_state?.state === "needs_details" && item.decision_disposition === "decision";
  if (view === "ready") return item.queue_lane !== "standard" && item.decision_disposition === "decision";
  if (view === "low_signal") return item.queue_lane === "standard" && item.decision_disposition === "decision";
  if (view === "recurring_exceptions") return item.queue_lane === "recurring_exception";
  if (view === "learned_rules") return item.policy_disposition !== "none";
  return true;
}

function triageVisibleMatters(data, view) {
  const pending = data.items || [];
  if (view === "recent") {
    return (data.recent_items || []).map((item) => ({
      context_id: `recent:${item.proposal_id}`,
      label: item.title,
      queue_lane: item.state,
      candidate_count: 1,
      source_count: 1,
      candidates: [item],
    }));
  }
  const matters = data.triage?.matters || pending.map((item) => ({
    context_id: item.context_id || item.proposal_id,
    label: item.group_label || item.title,
    queue_lane: item.queue_lane || "standard",
    candidate_count: 1,
    source_count: 1,
    candidates: [item],
  }));
  return matters
    .map((matter) => ({
      ...matter,
      candidates: (matter.candidates || []).filter((item) => triageCandidateMatches(item, view)),
    }))
    .filter((matter) => matter.candidates.length);
}

async function loadMoreTriage(data) {
  if (triageInteractionLocked()) return;
  const cursor = data.triage?.next_cursor;
  if (!cursor) return;
  const generation = state.triageLoadGeneration;
  const button = document.querySelector("[data-triage-more]"); if (button) { button.dataset.triageLoading = "true"; button.disabled = true; button.textContent = "Loading…"; }
  try {
    const params = triageRequestParams({ cursor });
    const next = await api(`/api/member/v1/surfaces/triage?${params}`);
    if (triageInteractionLocked() || generation !== state.triageLoadGeneration) return;
    const known = new Set((data.triage.matters || []).map((matter) => matter.context_id));
    for (const matter of next.triage?.matters || []) if (!known.has(matter.context_id)) data.triage.matters.push(matter);
    const knownItems = new Set((data.items || []).map((item) => item.proposal_id));
    for (const item of next.items || []) if (!knownItems.has(item.proposal_id)) data.items.push(item);
    data.triage.next_cursor = next.triage?.next_cursor || null; data.triage.has_more = Boolean(next.triage?.has_more);
    renderSurface(data);
  } catch (error) {
    if (triageInteractionLocked() || generation !== state.triageLoadGeneration) return;
    if (error.status === 422 && error.message === "stale triage cursor") {
      state.triageCursorRefreshPending = true;
      state.triageLoadGeneration += 1;
      applyTriageInteractionLock();
      const root = $("#surface");
      root.setAttribute("aria-busy", "true");
      const notice = document.createElement("section");
      notice.className = "state-card";
      notice.setAttribute("role", "status");
      notice.append(text("h2", "Triage changed while you were reviewing it."), text("p", "Refreshing the complete scoped queue before showing another matter."));
      root.replaceChildren(notice);
      try {
        const refreshed = await reloadActiveSurface({ triageReconcileExact: Boolean(state.triageSelected) });
        if (!refreshed) throw new Error("The refreshed queue was not available.");
        state.triageCursorRefreshPending = false;
        releaseTriageInteractionLock();
        state.triageBannerKind = "status";
        const banner = $("#state-banner");
        banner.hidden = false;
        banner.textContent = "Triage changed in another view, so the queue was refreshed before pagination continued.";
      } catch (refreshError) {
        state.triageCursorRefreshPending = false;
        root.setAttribute("aria-busy", "false");
        const failure = document.createElement("section");
        failure.className = "state-card";
        failure.setAttribute("role", "alert");
        failure.append(text("h2", "Triage needs an authoritative reload."), text("p", "The stale page was removed. Reload this page before reviewing another candidate."));
        root.replaceChildren(failure);
        latchTriageRefreshFailure(`Triage changed and the authoritative refresh failed: ${refreshError.message} Reload this page before continuing.`);
      }
      return;
    }
    state.triageBannerKind = "error";
    const banner = $("#state-banner");
    banner.hidden = false;
    banner.textContent = error.message;
    if (button) { delete button.dataset.triageLoading; button.disabled = false; button.textContent = "Show more"; }
  }
}

async function approveTriageSuggestion(suggestion, button) {
  const token = beginTriageOperation();
  if (token == null) return;
  const values = { scope_kind: suggestion.scope_kind, scope_value: suggestion.scope_value, disposition: suggestion.suggested_disposition };
  const banner = $("#state-banner"); banner.hidden = false;
  state.triageBannerKind = "status";
  let ruleRecorded = false;
  try {
    if (!button.dataset.previewAffectedCount) {
      const preview = await api("/api/member/v1/triage-policy/preview", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(values) });
      if (!triageOperationIsCurrent(token)) return;
      button.dataset.previewAffectedCount = String(preview.affected_feedback_count);
      button.textContent = "Approve shadow rule";
      banner.textContent = `${preview.affected_feedback_count} matching feedback decisions. Select “Approve shadow rule” to confirm. It will not suppress, confirm, route, or write candidates.`;
      return;
    }
    const expectedAffectedCount = Number(button.dataset.previewAffectedCount);
    await api("/api/member/v1/triage-policy/approve", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ ...values, request_id: crypto.randomUUID(), expected_affected_count: expectedAffectedCount }) });
    if (!triageOperationIsCurrent(token)) return;
    ruleRecorded = true;
    banner.textContent = "Shadow rule recorded. It is reversible and has no automatic candidate or provider effect in this rollout.";
    const refreshed = await reloadActiveSurface({ triageOperationToken: token });
    if (!refreshed || !triageOperationIsCurrent(token)) return;
  } catch (error) {
    if (!triageOperationIsCurrent(token)) return;
    if (ruleRecorded) latchTriageRefreshFailure(`The shadow rule was recorded, but Triage could not refresh: ${error.message} Reload this page before continuing.`);
    else banner.textContent = `${error.message} No rule was approved.`;
  } finally { finishTriageOperation(token); }
}

async function revokeTriageRule(rule, button) {
  const token = beginTriageOperation();
  if (token == null) return;
  const banner = $("#state-banner"); banner.hidden = false;
  state.triageBannerKind = "status";
  let revokeRecorded = false;
  try {
    await api(`/api/member/v1/triage-policy/${encodeURIComponent(rule.rule_id)}/revoke`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ request_id: crypto.randomUUID(), reason: "Revoked from Triage learning controls" }) });
    if (!triageOperationIsCurrent(token)) return;
    revokeRecorded = true;
    banner.textContent = "Shadow rule revoked.";
    const refreshed = await reloadActiveSurface({ triageOperationToken: token });
    if (!refreshed || !triageOperationIsCurrent(token)) return;
  } catch (error) {
    if (!triageOperationIsCurrent(token)) return;
    if (revokeRecorded) latchTriageRefreshFailure(`The shadow rule was revoked, but Triage could not refresh: ${error.message} Reload this page before continuing.`);
    else banner.textContent = `${error.message} The rule remains unchanged.`;
  } finally { finishTriageOperation(token); }
}

function triageSurface(data, { preserveView = false } = {}) {
  const fragment = document.createDocumentFragment();
  const pending = data.items || [];
  const recent = data.recent_items || [];
  const inventory = data.triage?.inventory || {};
  const candidateCount = inventory.total_candidates || 0; const matterCount = inventory.total_matters || 0;
  const suggestions = data.triage?.feedback_suggestions || [];
  const policyRules = data.triage?.policy_rules || [];
  const activePolicyRules = policyRules.filter((rule) => rule.status === "active");
  const views = new Set(["needs_interpretation", "ready", "needs_details", "low_signal", "recurring_exceptions", "learned_rules", "all_candidates", "recent"]);
  if (!views.has(state.triageView)) state.triageView = "ready";
  if (!preserveView && state.triageSelected && data.focus?.state === "pending") state.triageView = "all_candidates";
  if (!preserveView && state.triageSelected && data.focus?.state === "resolved") state.triageView = "recent";
  const viewCounts = inventory.views || {};
  const attentionCount = viewCounts.ready?.candidates ?? pending.filter((item) => triageCandidateMatches(item, "ready")).length;
  const overview = document.createElement("section"); overview.className = "card triage-overview";
  overview.append(text("p", "TRIAGE", "eyebrow"));
  if (attentionCount) {
    overview.append(text("h2", `${attentionCount} decision${attentionCount === 1 ? "" : "s"} need${attentionCount === 1 ? "s" : ""} your attention.`), text("p", "Start with the clearest security, date, money, promise, priority, and recurring-issue signals. Other suggestions stay available without crowding this queue."));
  } else {
    overview.append(text("h2", "You’re caught up on decisions that need attention."), text("p", candidateCount ? "Other suggestions and complete system accounting are still available when you want them." : "New eligible proposals will appear here when they need a human decision."));
  }
  const assurance = data.processing_health?.details?.source_assurance;
  if (assurance?.counts && (assurance.counts.missing || assurance.counts.failed)) {
    const sourceWarning = text("p", `${assurance.counts.failed || 0} source assessments failed and ${assurance.counts.missing || 0} are missing. Review system accounting before relying on this queue as complete.`, "review-state");
    sourceWarning.setAttribute("data-source-assurance", assurance.state || "unknown"); overview.append(sourceWarning);
  }
  const accounting = document.createElement("details"); accounting.className = "triage-system-accounting";
  accounting.append(text("summary", `System accounting · ${candidateCount} unresolved candidate${candidateCount === 1 ? "" : "s"} in ${matterCount} decision group${matterCount === 1 ? "" : "s"}`));
  accounting.append(text("p", `${inventory.decision_candidates || 0} are independent decisions · ${inventory.duplicate_evidence || 0} are exact duplicate evidence · ${inventory.existing_object_evidence || 0} link to existing work.`, "state-note"));
  if (assurance?.counts) {
    const counts = assurance.counts;
    const status = text("p", `${counts.accounted} of ${counts.obligations} source obligations accounted for · ${counts.eligible_candidates} produced candidates · ${counts.no_candidate} were explicitly assessed with none · ${counts.quarantined} safely quarantined${counts.pending ? ` · ${counts.pending} processing` : ""}.`, "state-note");
    status.setAttribute("data-source-assurance", assurance.state || "unknown"); accounting.append(status);
  }
  overview.append(accounting); fragment.append(overview);

  const controls = document.createElement("section"); controls.className = "triage-view-controls";
  const tabs = document.createElement("div"); tabs.className = "triage-lens-tabs"; tabs.setAttribute("role", "group"); tabs.setAttribute("aria-label", "Triage views");
  for (const [key, label] of [["ready", "Needs attention"], ["needs_details", "Needs details"], ["needs_interpretation", "Needs interpretation"], ["low_signal", "Low signal"], ["recurring_exceptions", "Recurring issues"], ["learned_rules", "Learned rules"], ["all_candidates", "All candidates"], ["recent", "Recent"]]) {
    const count = key === "recent" ? recent.length : key === "learned_rules" ? activePolicyRules.length : viewCounts[key]?.candidates ?? pending.filter((item) => triageCandidateMatches(item, key)).length;
    const button = document.createElement("button"); button.className = "comms-filter"; button.type = "button"; button.setAttribute("aria-pressed", String(state.triageView === key)); button.setAttribute("aria-label", `${label}, ${count}`);
    button.append(text("span", label), text("span", count, "triage-tab-count"));
    button.addEventListener("click", async () => {
      if (state.triageView === key || triageInteractionLocked()) return;
      if (state.triageBannerKind === "continuation") clearBanner();
      state.triageLoadGeneration += 1;
      state.triageView = key; state.triageSelected = null; state.triageMatter = null; state.triageFocusPending = false;
      data.focus = { requested_candidate_id: null, state: "not_requested", candidate_state: null, matter_id: null };
      const mustReload = key !== "all_candidates" && (state.triageSort !== "attention" || state.triageSource !== "all" || state.triageKind !== "all");
      if (mustReload) { state.triageSort = "attention"; state.triageSource = "all"; state.triageKind = "all"; }
      replaceQuery({ view: key, item: null, matter: null, sort: state.triageSort === "attention" ? null : state.triageSort, source: state.triageSource === "all" ? null : state.triageSource, kind: state.triageKind === "all" ? null : state.triageKind });
      if (!mustReload) { renderSurface(data); return; }
      try { await reloadActiveSurface({ triageFocus: false }); }
      catch (error) { setBanner(`The complete queue could not be reloaded: ${error.message}`); }
    }); tabs.append(button);
  }
  controls.append(tabs);
  if (state.triageView === "all_candidates") {
    const facets = inventory.facets || {};
    const advanced = document.createElement("div"); advanced.className = "triage-inventory-filters";
    const sort = selectControl([{ value: "attention", label: "Attention order" }, { value: "oldest", label: "Oldest first" }, { value: "newest", label: "Newest first" }], state.triageSort, ""); sort.setAttribute("aria-label", "Sort complete inventory");
    const source = selectControl([{ value: "all", label: "All sources" }, ...(facets.sources || []).map((value) => ({ value: value.id, label: `${value.label} (${value.candidates})` }))], state.triageSource, ""); source.setAttribute("aria-label", "Filter complete inventory by source");
    const kind = selectControl([{ value: "all", label: "All types" }, ...(facets.types || []).map((value) => ({ value: value.id, label: `${value.label} (${value.candidates})` }))], state.triageKind, ""); kind.setAttribute("aria-label", "Filter complete inventory by type");
    const refresh = async () => {
      if (triageInteractionLocked()) return;
      state.triageSort = sort.value; state.triageSource = source.value; state.triageKind = kind.value;
      state.triageSelected = null; state.triageMatter = null; state.triageLoadGeneration += 1;
      replaceQuery({ item: null, matter: null, sort: state.triageSort === "attention" ? null : state.triageSort, source: state.triageSource === "all" ? null : state.triageSource, kind: state.triageKind === "all" ? null : state.triageKind });
      for (const control of [sort, source, kind]) control.disabled = true;
      try { await reloadActiveSurface({ triageFocus: false }); }
      catch (error) { setBanner(`The inventory filter could not be applied: ${error.message}`); for (const control of [sort, source, kind]) control.disabled = false; }
    };
    for (const control of [sort, source, kind]) control.addEventListener("change", refresh);
    advanced.append(field("Sort", sort), field("Source", source), field("Type", kind));
    const selectedCount = data.triage?.selection?.candidates ?? candidateCount;
    advanced.append(text("p", `${selectedCount} matching candidate${selectedCount === 1 ? "" : "s"}. Filters change only this recovery view, never importance or candidate state.`, "state-note field-span"));
    controls.append(advanced);
  }
  fragment.append(controls);
  const visibleMatters = triageVisibleMatters(data, state.triageView);
  const missingRequestedCandidate = Boolean(state.triageSelected && data.focus?.state === "not_found");
  if (state.triageSelected) state.triageMatter = visibleMatters.find((matter) => matter.candidates.some((item) => item.proposal_id === state.triageSelected))?.context_id || state.triageMatter;
  if (missingRequestedCandidate) state.triageMatter = null;
  else if (!state.triageMatter || !visibleMatters.some((matter) => matter.context_id === state.triageMatter)) state.triageMatter = visibleMatters[0]?.context_id || null;
  const selectedMatter = visibleMatters.find((matter) => matter.context_id === state.triageMatter) || null;
  const workspace = document.createElement("div"); workspace.className = "triage-workspace";
  const workspaceLabels = { ready: "Decisions that need you", needs_details: "Decisions missing details", needs_interpretation: "Items to classify", low_signal: "Other suggestions", recurring_exceptions: "Recurring issues", learned_rules: "Items matching shadow rules", all_candidates: "Complete candidate inventory", recent: "Recent decisions" };
  const master = document.createElement("section"); master.className = "card triage-master"; master.append(text("h2", workspaceLabels[state.triageView] || "Decisions to review"));
  if (state.triageView === "recent") master.append(text("p", "Read-only audit history. System-retired candidates are historical lifecycle receipts, not new sender rules.", "state-note"));
  if (!visibleMatters.length) master.append(text("p", state.triageView === "ready" ? "Nothing needs your attention right now." : state.triageView === "needs_details" ? "No decisions are waiting for missing details." : state.triageView === "needs_interpretation" ? "No items are waiting for a type." : state.triageView === "low_signal" ? "No lower-signal suggestions are waiting." : state.triageView === "recent" ? "No recent decisions yet." : "Nothing matches this view.", "empty"));
  const list = document.createElement("ul"); list.className = "triage-master-list";
  let priorLane = null;
  const groupByAttention = state.triageView === "ready" || (state.triageView === "all_candidates" && state.triageSort === "attention");
  for (const matter of visibleMatters) {
    if (groupByAttention && matter.queue_lane !== priorLane) {
      priorLane = matter.queue_lane;
      const [laneLabel] = triageLanePresentation(matter.queue_lane);
      const heading = document.createElement("li"); heading.className = "triage-lane-heading"; heading.append(text("span", laneLabel), text("small", "Explainable attention group")); list.append(heading);
    }
    const li = document.createElement("li"); const button = document.createElement("button"); button.type = "button"; button.className = "triage-master-button"; button.setAttribute("aria-current", String(matter.context_id === state.triageMatter));
    const first = matter.candidates[0]; const source = first?.source_context?.state === "available" ? [first.source_context.channel && titleCase(first.source_context.channel), first.source_context.sender].filter(Boolean).join(" · ") : null;
    const [laneLabel] = triageLanePresentation(matter.queue_lane); const due = triageDateLabel(triageCandidateDate(first)); const category = first?.source_context?.provider_category?.label;
    const cues = [laneLabel, due ? `Date: ${due}` : null, source, category ? `Provider category: ${category}` : null, matter.candidates.length > 1 ? `${matter.candidates.length} related decisions` : null, matter.source_count > 1 ? `${matter.source_count} sources` : null].filter(Boolean).join(" · ");
    button.append(text("strong", matter.label || first?.title || "Review decision"), text("small", cues));
    button.addEventListener("click", () => { if (triageInteractionLocked()) return; state.triageMatter = matter.context_id; state.triageSelected = matter.candidates[0]?.proposal_id || null; state.triageFocusPending = true; replaceQuery({ view: state.triageView, item: state.triageSelected, matter: matter.context_id }); renderSurface(data); }); li.append(button); list.append(li);
  }
  master.append(list);
  const visibleCandidateCount = visibleMatters.reduce((count, matter) => count + matter.candidates.length, 0);
  const completeViewCount = state.triageView === "all_candidates" ? data.triage?.selection?.candidates ?? candidateCount : viewCounts[state.triageView]?.candidates ?? visibleCandidateCount;
  const viewCountIsComplete = state.triageView !== "learned_rules";
  if (data.triage?.has_more && state.triageView !== "recent" && (!viewCountIsComplete || visibleCandidateCount < completeViewCount)) { const more = text("button", "Show more", "button button-quiet"); more.type = "button"; more.dataset.triageMore = "true"; more.addEventListener("click", () => loadMoreTriage(data)); master.append(more); }
  const detail = document.createElement("div"); detail.className = "triage-matter-detail";
  if (missingRequestedCandidate) {
    const unavailable = document.createElement("section"); unavailable.className = "card triage-detail"; unavailable.setAttribute("role", "status");
    unavailable.append(text("p", "EXACT REVIEW LINK", "eyebrow"), text("h2", "This candidate is not available in your member scope."), text("p", "It may have been removed by source correction, or the link may belong to a different private member. AdministrateMe did not substitute another decision.", "state-note"));
    detail.append(unavailable);
  } else if (selectedMatter) { if (selectedMatter.candidates.length > 1) detail.append(text("p", `${selectedMatter.candidates.length} related decisions are shown together. Confirming one never confirms the others.`, "state-note")); for (const item of selectedMatter.candidates) detail.append(triageDetail(item, data, state.triageView === "recent")); }
  workspace.append(master, detail); fragment.append(workspace);
  if (suggestions.length || activePolicyRules.length) {
    const learning = document.createElement("section"); learning.className = "card triage-learning"; learning.append(text("p", "REVERSIBLE LEARNING", "eyebrow"), text("h2", `${suggestions.length} suggested pattern${suggestions.length === 1 ? "" : "s"} · ${activePolicyRules.length} shadow rule${activePolicyRules.length === 1 ? "" : "s"}`), text("p", "Rules begin in shadow mode. They stay visible and reversible, and cannot silently confirm or suppress household truth.", "state-note"));
    for (const suggestion of suggestions.slice(0, 5)) { const row = document.createElement("div"); row.className = "button-row"; row.append(text("span", `${suggestion.occurrences} × ${titleCase(suggestion.reason)} in ${titleCase(suggestion.scope_kind)}`)); const approve = text("button", "Preview & approve shadow rule", "button button-quiet"); approve.type = "button"; approve.addEventListener("click", () => approveTriageSuggestion(suggestion, approve)); row.append(approve); learning.append(row); }
    for (const rule of activePolicyRules) { const row = document.createElement("div"); row.className = "button-row"; row.append(text("span", `${titleCase(rule.disposition)} · ${titleCase(rule.scope_kind)} · shadow only`)); const revoke = text("button", "Revoke", "button button-quiet"); revoke.type = "button"; revoke.addEventListener("click", () => revokeTriageRule(rule, revoke)); row.append(revoke); learning.append(row); }
    fragment.append(learning);
  }
  if (state.triageSelected && state.triageFocusPending && !missingRequestedCandidate) {
    queueMicrotask(() => {
      const target = [...document.querySelectorAll("[data-triage-item]")].find((node) => node.dataset.triageItem === state.triageSelected);
      if (!target) return;
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: "start", behavior: "smooth" });
      state.triageFocusPending = false;
    });
  }
  return fragment;
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
    for (const action of ["complete", "defer", "block"].filter((value) => (item.actions || []).includes(value) && (item.kind === "task" || value === "complete"))) {
      const button = text("button", action[0].toUpperCase() + action.slice(1), action === "complete" ? "button button-primary" : "button button-quiet");
      button.type = "button";
      button.addEventListener("click", () => advanceAction(item, action, section));
      row.append(button);
    }
    if (item.kind === "commitment" && (item.actions || []).some((action) => ["defer", "block", "unblock", "cancel", "delegate"].includes(action))) {
      const manage = text("button", "Manage promise…", "button button-quiet"); manage.type = "button"; manage.addEventListener("click", () => commitmentManageDialog(item)); row.append(manage);
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

function memberStatusLabel(value) {
  return value === "inbox" ? "Open" : titleCase(value);
}

function todayItemMeta(item) {
  return [item.time_label, item.context, item.status ? memberStatusLabel(item.status) : null]
    .filter(Boolean)
    .join(" · ");
}

function todayDecisionExplanation(item) {
  const value = item.decision_context;
  if (!value) return null;
  const section = document.createElement("div"); section.className = "today-decision-context";
  const happened = document.createElement("p"); happened.append(text("strong", "What happened: "), document.createTextNode(value.what_happened || "A source produced a candidate for review."));
  const why = document.createElement("p"); why.append(text("strong", "Why it is here: "), document.createTextNode(value.why_now || "It still needs your confirmation."));
  section.append(happened, why);
  if (value.source?.label) {
    const source = text("p", value.source.label, "today-decision-source");
    if (value.source.occurred_at) source.append(document.createTextNode(` · ${new Date(value.source.occurred_at).toLocaleString()}`));
    section.append(source);
  }
  const details = document.createElement("details"); details.className = "today-decision-details";
  details.append(text("summary", "Context, uncertainties, and consequences"));
  details.append(text("p", value.smallest_next_step || item.micro_step, "state-note"));
  if (value.uncertainties?.length) {
    details.append(text("strong", "Still unknown"));
    const list = document.createElement("ul"); for (const uncertainty of value.uncertainties) list.append(text("li", uncertainty)); details.append(list);
  }
  if (value.choices?.length) {
    details.append(text("strong", "Your choices"));
    const choices = document.createElement("dl"); choices.className = "today-decision-choices";
    for (const choice of value.choices) choices.append(text("dt", choice.label), text("dd", choice.consequence));
    details.append(choices);
  }
  details.append(text("p", value.interpretation_disclosure === "bounded_language_review_aid" ? "The explanation is a bounded language review aid. Exact source words remain evidence; only your decision can make anything official." : "This explanation uses typed source facts and fixed rules. Only your decision can make anything official.", "state-note"));
  section.append(details);
  return section;
}

function setBanner(message) {
  if (currentSurface() === "triage") state.triageBannerKind = "status";
  const banner = $("#state-banner");
  banner.hidden = false;
  banner.textContent = message;
}

function clearBanner() {
  const banner = $("#state-banner"); banner.hidden = true; banner.textContent = "";
  state.triageBannerKind = null;
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
  if (item.kind === "commitment" && action === "manage_commitment") {
    commitmentManageDialog(item);
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
    } else if (["recurrence", "routine"].includes(item.kind) && action === "pause") {
      await runRhythmLifecycle({ ...item, object_kind: item.kind }, "pause");
      return;
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
        }),
      });
      setBanner("Completion saved for approval. The provider is unchanged until OpenClaw confirms this exact action.");
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
    pause: "Pause",
    attended: "Attended",
    missed: "Missed",
    open_triage: "Review",
    open_comms: "Open conversation",
    open_reminder: "Review copies",
    open_calendar: "Open calendar",
    open_chat: "Talk it through",
    evidence: "Why this is here",
    manage_commitment: "Manage promise…",
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
  const decisionExplanation = todayDecisionExplanation(item);
  if (decisionExplanation) body.append(decisionExplanation);
  else if (item.micro_step) body.append(text("small", item.micro_step));
  const copies = possibleCopySources(item, true); if (copies) body.append(copies);
  li.append(body);
  if (interactive) {
    const row = document.createElement("div");
    row.className = "day-item-actions";
    const actions = (item.actions || []).filter((action) => ["complete", "complete_external", "complete_step", "skip_step", "pause", "attended", "missed", "open_triage", "open_comms", "open_reminder", "open_calendar", "open_chat", "evidence"].includes(action));
    if (item.kind === "commitment" && (item.actions || []).some((action) => ["defer", "block", "unblock", "cancel", "delegate"].includes(action))) actions.splice(Math.max(actions.length - Number(actions.includes("evidence")), 0), 0, "manage_commitment");
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
  );
  const decisionExplanation = todayDecisionExplanation(item);
  if (decisionExplanation) section.append(decisionExplanation);
  else section.append(text("p", item.micro_step || "Open it and decide only the first move.", "microstep"));
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
  const carouselActions = (item.actions || []).filter((value) => ["complete", "complete_external", "complete_step", "skip_step", "pause", "attended", "missed", "open_triage", "open_comms", "open_reminder", "open_calendar", "open_chat"].includes(value));
  if (item.kind === "commitment" && (item.actions || []).some((action) => ["defer", "block", "unblock", "cancel", "delegate"].includes(action))) carouselActions.push("manage_commitment");
  for (const action of carouselActions) {
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
  setBanner(operation === "reply" ? "Saving this exact reply for independent approval…" : "Saving this change for independent approval…");
  try {
    const result = await api(`/api/member/v1/comms/${encodeURIComponent(thread.last_interaction_id)}/commands`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        operation,
        expected_last_event_id: thread.last_event_id,
        request_id: crypto.randomUUID(),
        ...extras,
      }),
    });
    setBanner(result.local ? "Updated. The focus view has been recalculated." : "Your decision is saved. OpenClaw must separately approve this exact provider action before anything changes outside AdministrateMe.");
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
    const triage = text("button", "Send to Triage", "link-button"); triage.type = "button";
    triage.addEventListener("click", () => openAddForReview({
      exactText: [message.subject, message.body || message.preview].filter(Boolean).join("\n\n"),
      sourceEventId: message.evidence_event_ids[0], sourceSurface: "comms",
      dialogTitle: "Extract work from this message",
    }));
    item.append(triage);
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
    const triage = document.createElement("section"); triage.className = "comms-triage-links";
    triage.append(text("strong", `${thread.triage_count} extracted candidate${thread.triage_count === 1 ? "" : "s"} across ${thread.triage_matter_count || thread.triage_count} matter${(thread.triage_matter_count || thread.triage_count) === 1 ? "" : "s"}`));
    const links = document.createElement("ul");
    for (const candidate of thread.triage_candidates || []) {
      const item = document.createElement("li"); const link = text("a", candidate.title || "Review extracted candidate", "triage-link");
      link.href = candidate.surface_link?.href || "/member/triage"; item.append(link); links.append(item);
    }
    triage.append(links); pane.append(triage);
  }
  const messages = document.createElement("ol"); messages.className = "comms-transcript";
  for (const message of thread.messages) messages.append(messageBubble(message)); pane.append(messages);
  const actions = document.createElement("div"); actions.className = "button-row comms-actions";
  const latest = thread.messages[thread.messages.length - 1];
  if (thread.capabilities.mark_read) {
    const operation = latest.unread ? "mark_read" : "mark_unread";
    const button = text("button", latest.unread ? "Mark read" : "Mark unread", "button button-quiet"); button.type = "button";
    button.addEventListener("click", () => runCommsCommand(thread, operation)); actions.append(button);
  }
  if (thread.capabilities.archive) {
    const operation = thread.archived ? "restore" : "archive";
    const button = text("button", thread.archived ? "Restore" : "Archive", "button button-quiet"); button.type = "button";
    button.addEventListener("click", () => runCommsCommand(thread, operation)); actions.append(button);
  }
  const snooze = text("button", thread.bucket === "snoozed" ? "Unsnooze" : "Snooze 2h", "button button-quiet"); snooze.type = "button";
  snooze.addEventListener("click", () => runCommsCommand(thread, thread.bucket === "snoozed" ? "unsnooze" : "snooze", thread.bucket === "snoozed" ? {} : { snoozed_until: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString() })); actions.append(snooze);
  const quiet = text("button", thread.quieted ? "Restore to focus" : "Quiet this", "button button-quiet"); quiet.type = "button";
  quiet.addEventListener("click", () => runCommsCommand(thread, thread.quieted ? "unquiet" : "quiet")); actions.append(quiet);
  pane.append(actions);
  const compose = document.createElement("form"); compose.className = "comms-compose";
  const replyLabel = text("label", "Reply in this thread"); replyLabel.htmlFor = "comms-reply";
  const input = document.createElement("textarea"); input.id = "comms-reply"; input.rows = 4; input.maxLength = 20000; input.placeholder = "Write the reply you intend to send…"; compose.append(replyLabel, input);
  input.addEventListener("input", () => {
    input.removeAttribute("aria-invalid");
    input.removeAttribute("aria-describedby");
  });
  if (thread.capabilities.reply) {
    compose.append(text("p", "This saves your exact reply as a pending provider action. OpenClaw asks for its own one-time approval before delivery.", "state-note"));
    const send = text("button", "Save reply for approval", "button button-primary"); send.type = "submit";
    compose.append(send);
    compose.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!input.value.trim()) {
        input.setAttribute("aria-invalid", "true");
        input.setAttribute("aria-describedby", "state-banner");
        setBanner("Write the reply you want approved. Nothing was sent.");
        input.focus();
        return;
      }
      runCommsCommand(thread, "reply", { body: input.value.trim() });
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
  setBanner("Saving one explicit provider change for independent approval…");
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
      }),
    });
    setBanner("Your decision is saved. OpenClaw must approve this exact provider action before the provider can change.");
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} Nothing was changed or implied.`);
  }
}

async function runTaskLifecycle(item, action, extras = {}) {
  setBanner(`Recording task ${action}…`);
  try {
    await api("/api/member/v1/lifecycle", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        item_kind: "task", item_id: item.task_id, action,
        expected_last_event_id: item.last_event_id, request_id: crypto.randomUUID(),
        until: extras.until || null, reason: extras.reason || null, note: extras.note || null,
      }),
    });
    const confirmations = {
      complete: "Done. The canonical task completion is recorded.",
      cancel: "Abandoned. The task remains in history with your reason and evidence.",
      reopen: "Reopened. The task is active again without erasing its prior history.",
      snooze: "Snoozed. It will become actionable again at the time you chose.",
      defer: "Deferred. The reason and return time are recorded separately from a snooze.",
    };
    setBanner(confirmations[action] || `Task ${action} recorded.`);
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} The task was not changed.`);
  }
}

async function runCommitmentLifecycle(item, action, extras = {}) {
  setBanner(`Recording ${action} for this promise…`);
  try {
    await api("/api/member/v1/lifecycle", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        item_kind: "commitment", item_id: item.id || item.commitment_id, action,
        expected_last_event_id: item.last_event_id, request_id: crypto.randomUUID(),
        until: extras.until || null, reason: extras.reason || null, note: extras.note || null,
        delegate_to_party_id: extras.delegate_to_party_id || null,
      }),
    });
    const confirmations = {
      complete: "Done. The promise is recorded as completed.",
      unblock: "Unblocked. The promise is eligible for Today again.",
      defer: "Deferred. Its return time remains distinct from its due date.",
      block: "Waiting condition recorded. The promise remains visible in history.",
      cancel: "Released. The promise is closed with your reason and evidence.",
      delegate: "Hand-off recorded. The original promise remains yours in history; no obligation was silently assigned to the other person.",
    };
    setBanner(confirmations[action] || `Promise ${action} recorded.`);
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} The promise was not changed.`);
  }
}

function commitmentReasonDialog(item, action) {
  const form = document.createElement("form"); form.className = "dialog-form";
  const reason = document.createElement("textarea"); reason.rows = 3; reason.maxLength = 500; reason.required = action === "block";
  const copy = {
    block: ["What are you waiting on?", "Record waiting condition"],
    cancel: ["Why is this promise being released? (optional)", "Release promise"],
  }[action];
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const keep = text("button", "Keep it", "button button-quiet"); keep.type = "button"; keep.addEventListener("click", closeActionDialog);
  const submit = text("button", copy[1], "button button-primary"); submit.type = "submit"; buttons.append(keep, submit);
  form.append(field(copy[0], reason), buttons);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runCommitmentLifecycle(item, action, { reason: reason.value.trim() || null }); });
  openActionDialog(action === "block" ? "Put this promise in Waiting" : "Release this promise?", form);
}

function commitmentDelegateDialog(item) {
  const options = state.surface?.lifecycle_options?.commitment_delegates || [];
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", "This records that you handed off your original promise. It does not create or assign a new obligation to the other person without their consent.", "state-note"));
  const delegate = selectControl(options.map((person) => ({ value: person.party_id, label: `${person.label} · ${titleCase(person.kind)}` })), "", "Choose a confirmed person…"); delegate.required = true;
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const keep = text("button", "Keep it", "button button-quiet"); keep.type = "button"; keep.addEventListener("click", closeActionDialog);
  const submit = text("button", "Record hand-off", "button button-primary"); submit.type = "submit"; submit.disabled = !options.length; buttons.append(keep, submit);
  form.append(field("Handed off to", delegate), buttons);
  if (!options.length) form.prepend(text("p", "No other confirmed Party is visible. Confirm the person in People before recording a hand-off.", "state-note"));
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runCommitmentLifecycle(item, "delegate", { delegate_to_party_id: delegate.value }); });
  openActionDialog("Hand off this promise?", form);
}

function commitmentManageDialog(item) {
  const content = document.createElement("div"); content.className = "dialog-form";
  content.append(text("p", "Choose the exact state change. AdministrateMe will preserve the original promise and its evidence.", "state-note"));
  const actions = document.createElement("div"); actions.className = "button-row";
  const available = new Set(item.actions || []);
  if (available.has("unblock")) {
    const button = text("button", "No longer waiting", "button button-primary"); button.type = "button"; button.addEventListener("click", async () => { closeActionDialog(); await runCommitmentLifecycle(item, "unblock"); }); actions.append(button);
  }
  if (available.has("defer")) {
    const button = text("button", "Return tomorrow", "button button-quiet"); button.type = "button"; button.addEventListener("click", async () => { closeActionDialog(); await runCommitmentLifecycle(item, "defer", { until: new Date(Date.now() + 86400000).toISOString() }); }); actions.append(button);
  }
  if (available.has("block")) {
    const button = text("button", "Waiting on…", "button button-quiet"); button.type = "button"; button.addEventListener("click", () => { closeActionDialog(); commitmentReasonDialog(item, "block"); }); actions.append(button);
  }
  if (available.has("delegate")) {
    const button = text("button", "Hand off…", "button button-quiet"); button.type = "button"; button.addEventListener("click", () => { closeActionDialog(); commitmentDelegateDialog(item); }); actions.append(button);
  }
  if (available.has("cancel")) {
    const button = text("button", "Release promise…", "button button-quiet"); button.type = "button"; button.addEventListener("click", () => { closeActionDialog(); commitmentReasonDialog(item, "cancel"); }); actions.append(button);
  }
  const close = text("button", "Close", "button button-quiet"); close.type = "button"; close.addEventListener("click", closeActionDialog); actions.append(close);
  content.append(actions);
  openActionDialog("Manage this promise", content);
}

async function runRhythmLifecycle(item, action, extras = {}) {
  setBanner(`Recording ${action} for this ${item.object_kind || item.kind}…`);
  try {
    await api("/api/member/v1/lifecycle", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        item_kind: item.object_kind || item.kind, item_id: item.id, action,
        expected_last_event_id: item.last_event_id, request_id: crypto.randomUUID(),
        reason: extras.reason || null,
      }),
    });
    setBanner(action === "end" ? "Ended and retained in history." : action === "pause" ? "Paused. It will not enter Today until resumed." : "Resumed from the existing definition.");
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} The recurring structure was not changed.`);
  }
}

function endRhythmDialog(item) {
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", "Ending stops future occurrences but retains the definition and completed history.", "state-note"));
  const reason = document.createElement("textarea"); reason.rows = 3; reason.maxLength = 500;
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const keep = text("button", "Keep it", "button button-quiet"); keep.type = "button"; keep.addEventListener("click", closeActionDialog);
  const end = text("button", "End recurring item", "button button-primary"); end.type = "submit"; buttons.append(keep, end);
  form.append(field("Reason (optional)", reason), buttons);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runRhythmLifecycle(item, "end", { reason: reason.value.trim() || null }); });
  openActionDialog("End this recurring structure?", form);
}

function rhythmRuleEditor(initial = "FREQ=DAILY") {
  const presets = [
    { value: "FREQ=DAILY", label: "Every day" },
    { value: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", label: "Weekdays" },
    { value: "FREQ=WEEKLY", label: "Every week" },
    { value: "FREQ=MONTHLY", label: "Every month" },
    { value: "FREQ=YEARLY", label: "Every year" },
  ];
  const exact = presets.some((item) => item.value === initial);
  const select = selectControl([...presets, { value: "custom", label: "Custom rule" }], exact ? initial : "custom", "");
  const custom = document.createElement("input"); custom.value = exact ? "" : initial; custom.maxLength = 1000; custom.placeholder = "FREQ=MONTHLY;INTERVAL=3"; custom.hidden = exact;
  select.addEventListener("change", () => { custom.hidden = select.value !== "custom"; });
  const node = document.createElement("div"); node.className = "date-time-control"; node.append(select, custom);
  return { node, value: () => select.value === "custom" ? custom.value.trim() : select.value };
}

function recurrenceDefinitionDialog(item = null) {
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", "A recurrence is a repeating fact or trackable rhythm. It does not silently create a task until a reviewed rule says it should.", "state-note"));
  const name = document.createElement("input"); name.required = true; name.maxLength = 160; name.value = item?.kind || "";
  const rule = rhythmRuleEditor(item?.rrule || "FREQ=WEEKLY");
  const parts = triageDateTimeParts(item?.next_occurrence || new Date().toISOString());
  const due = document.createElement("div"); due.className = "date-time-control";
  const dueDate = document.createElement("input"); dueDate.type = "date"; dueDate.required = true; dueDate.value = parts.date;
  const dueTime = document.createElement("input"); dueTime.type = "time"; dueTime.required = true; dueTime.value = parts.time || "09:00"; due.append(dueDate, dueTime);
  const scope = selectControl([{ value: "member", label: "Only me" }, { value: "household", label: "Household" }], item?.linked_kind === "household" ? "household" : "member", ""); scope.disabled = Boolean(item);
  const lead = document.createElement("input"); lead.type = "number"; lead.min = "0"; lead.max = "365"; lead.value = String(item?.lead_time_days || 0);
  const trackable = document.createElement("input"); trackable.type = "checkbox"; trackable.checked = Boolean(item?.trackable);
  const trackWrap = document.createElement("label"); trackWrap.className = "check-control"; trackWrap.append(trackable, document.createTextNode(" Track each occurrence"));
  const notes = document.createElement("textarea"); notes.rows = 3; notes.maxLength = 5000; notes.value = item?.notes || "";
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const cancel = text("button", "Cancel", "button button-quiet"); cancel.type = "button"; cancel.addEventListener("click", closeActionDialog);
  const submit = text("button", item ? "Save recurrence" : "Add recurrence", "button button-primary"); submit.type = "submit"; buttons.append(cancel, submit);
  form.append(field("Name", name), field("Repeats", rule.node), field("Next occurrence", due), field("Visible to", scope), field("Lead time in days", lead), trackWrap, field("Notes (optional)", notes), buttons);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const rrule = rule.value();
    if (!rrule) { setBanner("Choose a repeat pattern or enter a valid custom rule."); return; }
    const recurrenceId = item?.id || `recurrence_${crypto.randomUUID().replaceAll("-", "")}`;
    closeActionDialog(); setBanner(item ? "Saving recurrence…" : "Adding recurrence…");
    try {
      await api(`/api/member/v1/recurrences/${encodeURIComponent(recurrenceId)}`, {
        method: "PUT", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({ request_id: crypto.randomUUID(), expected_last_event_id: item?.last_event_id || null, scope: scope.value, kind: name.value.trim(), rrule, next_occurrence: new Date(`${dueDate.value}T${dueTime.value}`).toISOString(), lead_time_days: Number(lead.value || 0), trackable: trackable.checked, notes: notes.value.trim() || null }),
      });
      setBanner(item ? "Recurrence updated with its prior history intact." : "Recurrence added."); await reloadActiveSurface();
    } catch (error) { setBanner(`${error.message} The recurrence was not saved.`); }
  });
  openActionDialog(item ? "Edit recurrence" : "Add a recurrence", form);
}

function routineDefinitionDialog(item = null) {
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", "A routine is an ordered sequence inside a repeat window. Put one step per line; add a tiny-start cue after a vertical bar.", "state-note"));
  const title = document.createElement("input"); title.required = true; title.maxLength = 500; title.value = item?.title || "";
  const rule = rhythmRuleEditor(item?.rrule || "FREQ=DAILY");
  const starts = document.createElement("input"); starts.type = "date"; starts.required = true; starts.value = item?.starts_on || new Date().toISOString().slice(0, 10);
  const window = document.createElement("div"); window.className = "date-time-control";
  const startTime = document.createElement("input"); startTime.type = "time"; startTime.required = true; startTime.value = item?.window_start_local || "08:00";
  const endTime = document.createElement("input"); endTime.type = "time"; endTime.required = true; endTime.value = item?.window_end_local || "09:00"; window.append(startTime, endTime);
  const steps = document.createElement("textarea"); steps.rows = 7; steps.required = true; steps.maxLength = 10000; steps.placeholder = "Put medication by water | Start by filling the glass\nReview Today | Open the first card"; steps.value = (item?.steps || []).map((step) => `${step.title}${step.micro_step ? ` | ${step.micro_step}` : ""}`).join("\n");
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const cancel = text("button", "Cancel", "button button-quiet"); cancel.type = "button"; cancel.addEventListener("click", closeActionDialog);
  const submit = text("button", item ? "Save routine" : "Add routine", "button button-primary"); submit.type = "submit"; buttons.append(cancel, submit);
  form.append(field("Routine name", title), field("Repeats", rule.node), field("Starts", starts), field("Daily window", window), field("Ordered steps", steps), buttons);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const lines = steps.value.split("\n").map((line) => line.trim()).filter(Boolean);
    const rrule = rule.value();
    if (!lines.length || !rrule) { setBanner("Add at least one routine step and a repeat pattern."); return; }
    const routineId = item?.id || `routine_${crypto.randomUUID().replaceAll("-", "")}`;
    const priorSteps = item?.steps || [];
    const definitions = lines.map((line, position) => { const parts = line.split("|"); return { step_id: priorSteps[position]?.step_id || `step_${position + 1}`, title: parts.shift().trim(), position, micro_step: parts.join("|").trim() || null, linked_task_id: priorSteps[position]?.linked_task_id || null }; });
    closeActionDialog(); setBanner(item ? "Saving routine…" : "Adding routine…");
    try {
      await api(`/api/member/v1/routines/${encodeURIComponent(routineId)}`, {
        method: "PUT", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({ request_id: crypto.randomUUID(), expected_last_event_id: item?.last_event_id || null, title: title.value.trim(), timezone: item?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone, starts_on: starts.value, rrule, window_start_local: startTime.value, window_end_local: endTime.value, steps: definitions }),
      });
      setBanner(item ? "Routine updated with its outcome history intact." : "Routine added."); await reloadActiveSurface();
    } catch (error) { setBanner(`${error.message} The routine was not saved.`); }
  });
  openActionDialog(item ? "Edit routine" : "Add a routine", form);
}

function rhythmDefinitionsCard(items) {
  const section = document.createElement("section"); section.className = "card"; section.append(text("h2", "Recurring structures"));
  const createActions = document.createElement("div"); createActions.className = "button-row";
  const addRecurrence = text("button", "Add recurrence", "button button-quiet"); addRecurrence.type = "button"; addRecurrence.addEventListener("click", () => recurrenceDefinitionDialog());
  const addRoutine = text("button", "Add routine", "button button-primary"); addRoutine.type = "button"; addRoutine.addEventListener("click", () => routineDefinitionDialog()); createActions.append(addRecurrence, addRoutine); section.append(createActions);
  if (!items?.length) { section.append(text("p", "No recurring structures are recorded.", "empty")); return section; }
  const list = document.createElement("ul"); list.className = "item-list";
  for (const item of items) {
    const li = document.createElement("li"); li.className = "item";
    li.append(text("strong", item.title), text("span", `${titleCase(item.object_kind)} · ${titleCase(item.status || "active")}`, `meta state-label state-${item.status || "active"}`));
    if (item.next_occurrence) li.append(text("small", `Next: ${reminderDate(item.next_occurrence)}`));
    const actions = document.createElement("div"); actions.className = "button-row";
    if (item.status !== "ended") {
      const edit = text("button", "Edit", "button button-quiet"); edit.type = "button"; edit.addEventListener("click", () => item.object_kind === "routine" ? routineDefinitionDialog(item) : recurrenceDefinitionDialog(item)); actions.append(edit);
    }
    if ((item.status || "active") === "active") {
      const pause = text("button", "Pause", "button button-quiet"); pause.type = "button"; pause.addEventListener("click", () => runRhythmLifecycle(item, "pause")); actions.append(pause);
    } else if (item.status === "paused") {
      const resume = text("button", "Resume", "button button-primary"); resume.type = "button"; resume.addEventListener("click", () => runRhythmLifecycle(item, "resume")); actions.append(resume);
    }
    if (item.status !== "ended") {
      const end = text("button", "End…", "button button-quiet"); end.type = "button"; end.addEventListener("click", () => endRhythmDialog(item)); actions.append(end);
    }
    if (item.last_event_id) { const evidence = text("button", "View evidence", "link-button"); evidence.type = "button"; evidence.addEventListener("click", () => showEvidence(item.last_event_id)); actions.append(evidence); }
    li.append(actions); list.append(li);
  }
  section.append(list); return section;
}

function taskPropertiesForm(initial, { create = false } = {}) {
  const form = document.createElement("form"); form.className = "dialog-form task-properties-form";
  const title = document.createElement("input"); title.name = "title"; title.required = true; title.maxLength = 1000; title.value = initial.title || "";
  const dueParts = triageDateTimeParts(initial.due_date);
  const due = document.createElement("div"); due.className = "date-time-control";
  const dueDate = document.createElement("input"); dueDate.type = "date"; dueDate.name = "due_date"; dueDate.value = dueParts.date; dueDate.setAttribute("aria-label", "Due date");
  const dueTime = document.createElement("input"); dueTime.type = "time"; dueTime.name = "due_time"; dueTime.value = dueParts.time; dueTime.setAttribute("aria-label", "Due time (optional)"); due.append(dueDate, dueTime);
  const availableParts = triageDateTimeParts(initial.available_from);
  const available = document.createElement("div"); available.className = "date-time-control";
  const availableDate = document.createElement("input"); availableDate.type = "date"; availableDate.name = "available_date"; availableDate.value = availableParts.date; availableDate.setAttribute("aria-label", "Available from date");
  const availableTime = document.createElement("input"); availableTime.type = "time"; availableTime.name = "available_time"; availableTime.value = availableParts.time; availableTime.setAttribute("aria-label", "Available from time (optional)"); available.append(availableDate, availableTime);
  const energy = selectControl(["low", "medium", "high"].map((value) => ({ value, label: titleCase(value) })), initial.energy || "", "Not set"); energy.name = "energy";
  const effort = selectControl(["tiny", "small", "medium", "large"].map((value) => ({ value, label: titleCase(value) })), initial.effort || "", "Not set"); effort.name = "effort";
  const itemType = selectControl(["task", "purchase", "appointment", "research", "decision", "chore", "maintenance"].map((value) => ({ value, label: titleCase(value) })), initial.item_type || "task", ""); itemType.name = "item_type";
  const domain = document.createElement("input"); domain.name = "domain"; domain.maxLength = 120; domain.value = initial.domain || "tasks";
  const micro = document.createElement("textarea"); micro.name = "micro_script"; micro.rows = 2; micro.maxLength = 2000; micro.value = initial.micro_script || "";
  const notes = document.createElement("textarea"); notes.name = "notes"; notes.rows = 4; notes.maxLength = 20000; notes.value = initial.notes || "";
  form.append(
    field("Task", title), field("Due", due), field("Available from", available), field("Energy needed", energy),
    field("Effort", effort), field("Work type", itemType), field("Area", domain),
    field("First tiny step", micro), field("Notes", notes),
  );
  const submit = text("button", create ? "Add task" : "Save task", "button button-primary"); submit.type = "submit"; form.append(submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); submit.disabled = true;
    const values = new FormData(form); const dateValue = String(values.get("due_date") || ""); const timeValue = String(values.get("due_time") || "");
    const availableDateValue = String(values.get("available_date") || ""); const availableTimeValue = String(values.get("available_time") || "");
    const payload = {
      request_id: crypto.randomUUID(), title: String(values.get("title") || "").trim(),
      due_date: dateValue ? (timeValue ? `${dateValue}T${timeValue}` : dateValue) : null,
      available_from: availableDateValue ? (availableTimeValue ? `${availableDateValue}T${availableTimeValue}` : availableDateValue) : null,
      energy: String(values.get("energy") || "") || null, effort: String(values.get("effort") || "") || null,
      item_type: String(values.get("item_type") || "task"), domain: String(values.get("domain") || "tasks").trim(),
      micro_script: String(values.get("micro_script") || "").trim() || null,
      notes: String(values.get("notes") || "").trim() || null,
    };
    if (!create) payload.expected_last_event_id = initial.last_event_id;
    try {
      const result = await api(create ? "/api/member/v1/tasks" : `/api/member/v1/tasks/${encodeURIComponent(initial.task_id)}`, {
        method: create ? "POST" : "PATCH", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(payload),
      });
      closeActionDialog(); state.reminderItem = `task:${result.task.task_id}`; setBanner(create ? "Task added to Reminders." : "Task properties saved."); await reloadActiveSurface();
    } catch (error) { setBanner(`${error.message} The task was not saved.`); submit.disabled = false; }
  });
  return form;
}

function deferTaskDialog(item, action = "defer") {
  const form = document.createElement("form"); form.className = "dialog-form";
  const until = document.createElement("input"); until.type = "datetime-local"; until.required = true;
  const reason = document.createElement("input"); reason.maxLength = 500; reason.placeholder = "Why is this being deferred?";
  const isSnooze = action === "snooze";
  const submit = text("button", isSnooze ? "Snooze task" : "Defer task", "button button-primary"); submit.type = "submit";
  form.append(field(isSnooze ? "Bring back at" : "Defer until", until));
  if (!isSnooze) form.append(field("Reason (optional)", reason));
  form.append(submit);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runTaskLifecycle(item, action, { until: until.value, reason: reason.value.trim() || null }); });
  openActionDialog(isSnooze ? "Snooze this task" : "Defer this task", form);
}

function blockTaskDialog(item) {
  const form = document.createElement("form"); form.className = "dialog-form";
  const reason = document.createElement("input"); reason.required = true; reason.maxLength = 500; reason.placeholder = "What are you waiting for?";
  const submit = text("button", "Mark waiting", "button button-primary"); submit.type = "submit"; form.append(field("Waiting on", reason), submit);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runTaskLifecycle(item, "block", { reason: reason.value.trim() }); });
  openActionDialog("Mark task as waiting", form);
}

function abandonTaskDialog(item) {
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", "Abandoning removes this from active work but keeps an auditable history. You can reopen it later.", "state-note"));
  const reason = document.createElement("textarea"); reason.rows = 3; reason.maxLength = 500; reason.placeholder = "Optional: why is this no longer worth doing?";
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const keep = text("button", "Keep task", "button button-quiet"); keep.type = "button"; keep.addEventListener("click", closeActionDialog);
  const abandon = text("button", "Abandon task", "button button-primary"); abandon.type = "submit";
  buttons.append(keep, abandon); form.append(field("Reason", reason), buttons);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runTaskLifecycle(item, "cancel", { reason: reason.value.trim() || null }); });
  openActionDialog("Abandon this task?", form);
}

function openActionDialog(title, content) {
  const dialog = $("#action-dialog");
  if (document.activeElement instanceof HTMLElement) dialogReturnFocus.set(dialog, document.activeElement);
  $("#action-title").textContent = title;
  $("#action-content").replaceChildren(content);
  dialog.showModal();
  content.querySelector("input, select, textarea, button")?.focus();
}

function closeActionDialog({ force = false } = {}) {
  const dialog = $("#action-dialog");
  if (!force && addForReviewInteractionLocked() && dialog.querySelector(".add-for-review-form")) return;
  dialog.close();
}

function openAddForReview({ exactText = "", sourceEventId = null, sourceSurface = null, dialogTitle = "Add for review" } = {}) {
  if (addForReviewInteractionLocked() || (currentSurface() === "triage" && triageInteractionLocked())) return;
  const form = document.createElement("form");
  form.className = "triage-review-form add-for-review-form";
  form.append(text("p", "This saves your thought in Triage. Nothing becomes a task, event, reminder, or provider change until you confirm it.", "state-note"));
  const input = document.createElement("textarea");
  input.name = "text"; input.rows = 5; input.maxLength = 2000; input.required = true;
  input.placeholder = "What just came up?";
  input.value = exactText.slice(0, 2000);
  form.append(field("Thought to hold", input));
  const hints = [
    { value: "", label: "Let Triage identify possible types" },
    { value: "task", label: "Do — a task" },
    { value: "commitment", label: "Promise — something I owe" },
    { value: "calendar_event", label: "Time — a calendar event" },
    { value: "reminder", label: "Reminder" },
    { value: "list", label: "List" },
    { value: "list_item", label: "List item" },
    { value: "recurrence", label: "Repeat — a recurring pattern" },
    { value: "party_identifier", label: "Link a person’s email or phone" },
    { value: "party_candidate", label: "Add a person or organization" },
    { value: "party_relationship", label: "Relationship between people" },
    { value: "relationship_fact", label: "Fact about a person" },
  ];
  const hint = selectControl(hints, "", ""); hint.name = "kind_hint";
  form.append(field("Type hint (optional)", hint));
  const note = text("p", "If the wording is unclear, it stays under Needs interpretation—never silently discarded.", "meta");
  const submit = text("button", "Add for review", "button button-primary"); submit.type = "submit";
  const cancel = text("button", "Cancel", "button button-quiet"); cancel.type = "button"; cancel.addEventListener("click", closeActionDialog);
  const actions = document.createElement("div"); actions.className = "button-row"; actions.append(submit, cancel);
  form.append(note, actions);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submittedText = input.value.trim();
    if (!submittedText) return;
    const submittedHint = hint.value || null;
    const triageActive = currentSurface() === "triage";
    const addOperationToken = beginAddForReviewOperation();
    if (addOperationToken == null) return;
    const operationToken = triageActive ? beginTriageOperation() : null;
    if (triageActive && operationToken == null) {
      finishAddForReviewOperation(addOperationToken);
      return;
    }
    setBanner("Saving your exact words before interpretation…");
    const allowedSurfaces = new Set(["today", "triage", "comms", "reminders", "calendar", "people", "notes", "finance", "scoreboard", "settings"]);
    const capturedSurface = allowedSurfaces.has(sourceSurface) ? sourceSurface : allowedSurfaces.has(currentSurface()) ? currentSurface() : "today";
    const clientRequestId = form.dataset.clientRequestId || crypto.randomUUID();
    form.dataset.clientRequestId = clientRequestId;
    const intentRequest = {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        text: submittedText,
        source_surface: capturedSurface,
        kind_hint: submittedHint,
        source_event_id: sourceEventId,
        client_request_id: clientRequestId,
      }),
    };
    let intentRecorded = false;
    let refocus = false;
    try {
      let result;
      try {
        result = await api("/api/member/v1/triage/intents", intentRequest);
      } catch (initialError) {
        if (requestDefinitelyRejected(initialError)) throw initialError;
        try {
          result = await api("/api/member/v1/triage/intents", intentRequest);
        } catch (retryError) {
          retryError.ambiguousAfterRetry = true;
          throw retryError;
        }
      }
      if (!addForReviewOperationIsCurrent(addOperationToken) || (triageActive && !triageOperationIsCurrent(operationToken))) return;
      intentRecorded = true;
      delete form.dataset.clientRequestId;
      closeActionDialog({ force: true });
      const savedMessage = result.state === "pending_interpretation" ? "Saved. Triage needs you to choose what this is; nothing was lost or created." : `${result.proposal_ids.length} review item${result.proposal_ids.length === 1 ? "" : "s"} saved in Triage. Nothing is committed yet.`;
      if (triageActive) {
        state.triageView = result.state === "pending_interpretation" ? "needs_interpretation" : "all_candidates";
        state.triageSelected = result.proposal_ids[0] || result.intent_id;
        replaceQuery({ view: state.triageView, item: state.triageSelected });
        const refreshed = await reloadActiveSurface({ triageOperationToken: operationToken });
        if (!refreshed || !triageOperationIsCurrent(operationToken)) return;
      }
      setBanner(savedMessage);
    } catch (error) {
      if (triageActive && intentRecorded) {
        latchTriageRefreshFailure(`Your thought was saved, but Triage could not refresh: ${error.message} Reload this page before continuing.`);
      } else if (error.ambiguousAfterRetry) {
        const recoveryHref = error.body?.detail?.recovery?.href || null;
        const recoveryInstruction = recoveryHref
          ? "Open the saved Triage item to reconcile the durable intent before trying again."
          : "Open Triage and check for the retained thought before trying again; the durable outcome is unknown.";
        const recoveryMessage = `The Add-for-review response remained interrupted after an idempotent retry: ${error.message} Your exact words are still visible. ${recoveryInstruction}`;
        latchAddForReviewRecovery(recoveryMessage, recoveryHref);
        if (triageActive) latchTriageRefreshFailure(recoveryMessage);
      } else {
        delete form.dataset.clientRequestId;
        setBanner(`${error.message} Your thought was not saved; the text is still here.`);
        refocus = true;
      }
    } finally {
      finishAddForReviewOperation(addOperationToken);
      if (triageActive) finishTriageOperation(operationToken);
      if (refocus && !addForReviewInteractionLocked()) input.focus();
    }
  });
  openActionDialog(dialogTitle, form);
}

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

function reminderDate(value, dateStyle = "medium") {
  if (!value) return "No date";
  const raw = String(value);
  return new Intl.DateTimeFormat(undefined, {
    dateStyle,
    ...(raw.includes("T") ? { timeStyle: "short" } : {}),
  }).format(raw.includes("T") ? new Date(raw) : calendarDate(raw));
}

function taskStatusLabel(value) {
  return ({ inbox: "Open", next: "Next", in_progress: "In progress", waiting_on: "Waiting", deferred: "Deferred", done: "Completed", cancelled: "Abandoned", dismissed: "Removed" })[value] || memberStatusLabel(value);
}

function remindersHero(cockpit) {
  const hero = document.createElement("section"); hero.className = "hero-card reminders-hero";
  const nowCount = cockpit.counts.now || 0;
  hero.append(
    text("p", "TASKS & REMINDERS", "eyebrow"),
    text("h2", nowCount ? `${nowCount} item${nowCount === 1 ? " needs" : "s need"} attention now.` : "Nothing is asking for attention right now.", "action-title"),
    text("p", "Canonical household tasks and provider-owned reminders share one calm workspace while retaining separate truth, provenance, and write paths.", "brief-summary"),
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
  for (const [value, label] of [["now", "Now"], ["scheduled", "Scheduled"], ["places", "Places"], ["anytime", "Anytime"], ["waiting", "Waiting"], ["completed", "Completed"]]) {
    const button = text("button", `${label} ${cockpit.counts[value] || 0}`, "comms-filter"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.remindersView === value));
    button.addEventListener("click", () => { state.remindersView = value; state.reminderItem = null; replaceQuery({ view: value, item: null }); renderReminders(data); }); views.append(button);
  }
  const accounts = document.createElement("div"); accounts.className = "comms-filter-row account-filters";
  for (const account of [{ provider_account_id: "all", label: "All accounts" }, ...(cockpit.accounts || [])]) {
    const button = text("button", account.label, "account-filter"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.remindersAccount === account.provider_account_id));
    button.addEventListener("click", () => { state.remindersAccount = account.provider_account_id; state.remindersList = "all"; state.reminderItem = null; replaceQuery({ account: state.remindersAccount, list: null, item: null }); renderReminders(data); }); accounts.append(button);
  }
  const lists = document.createElement("div"); lists.className = "comms-filter-row account-filters";
  for (const list of [{ list_external_id: "all", list_name: "All lists" }, ...(cockpit.lists || []).filter((row) => state.remindersAccount === "all" || row.provider_account_id === state.remindersAccount)]) {
    const button = text("button", list.list_name, "account-filter"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.remindersList === list.list_external_id));
    button.addEventListener("click", () => { state.remindersList = list.list_external_id; state.reminderItem = null; replaceQuery({ list: state.remindersList, item: null }); renderReminders(data); }); lists.append(button);
  }
  controls.append(views, accounts, lists); return controls;
}

function reminderItemButton(item) {
  const button = document.createElement("button"); button.type = "button"; button.className = "reminder-item";
  if (state.reminderItem === item.id) button.setAttribute("aria-current", "true");
  const heading = document.createElement("span"); heading.className = "reminder-item-heading";
  heading.append(text("strong", item.title), text("span", `${item.account_label} · ${item.list_name}`, "meta"));
  button.append(heading, text("span", item.why?.[0] || "Available", "focus-explanation"));
  if (item.due_at) button.append(text("span", reminderDate(item.due_at), "meta"));
  button.addEventListener("click", () => { state.reminderItem = item.id; replaceQuery({ item: item.id }); renderReminders(state.surface); });
  return button;
}

function taskDetail(item) {
  const pane = document.createElement("section"); pane.className = "card reminder-detail task-detail";
  pane.append(text("p", "ADMINISTRATEME TASK · CANONICAL HOUSEHOLD WORK", "eyebrow"), text("h2", item.title));
  if (item.notes) pane.append(text("p", item.notes));
  const why = document.createElement("p"); why.className = "focus-reason"; why.append(text("strong", "Why it is here: "), document.createTextNode((item.why || []).join(" · "))); pane.append(why);
  const facts = document.createElement("dl"); facts.className = "reminder-facts";
  for (const [label, value] of [
    ["Status", taskStatusLabel(item.status)], ["Due", reminderDate(item.due_date, "full")],
    ["Available from", item.available_from ? reminderDate(item.available_from, "full") : "Now"],
    ["Energy", item.energy ? titleCase(item.energy) : "Not set"], ["Effort", item.effort ? titleCase(item.effort) : "Not set"],
    ["Work type", titleCase(item.item_type || "task")], ["Area", titleCase(item.domain || "tasks")],
  ]) facts.append(text("dt", label), text("dd", value));
  pane.append(facts);
  if (item.micro_script) pane.append(text("p", `First tiny step: ${item.micro_script}`, "state-note"));
  pane.append(text("p", "This is an AdministrateMe task, not a provider reminder. Provider copies require an explicit, separately governed route.", "meta"));
  const actions = document.createElement("div"); actions.className = "button-row";
  const terminal = ["done", "cancelled", "dismissed"].includes(item.status);
  if (!terminal) {
    const done = text("button", "Done", "button button-primary"); done.type = "button"; done.addEventListener("click", () => runTaskLifecycle(item, "complete")); actions.append(done);
    const edit = text("button", "Edit properties", "button button-quiet"); edit.type = "button"; edit.addEventListener("click", () => openActionDialog("Edit household task", taskPropertiesForm(item))); actions.append(edit);
    const defer = text("button", "Defer", "button button-quiet"); defer.type = "button"; defer.addEventListener("click", () => deferTaskDialog(item)); actions.append(defer);
    const snooze = text("button", "Snooze", "button button-quiet"); snooze.type = "button"; snooze.addEventListener("click", () => deferTaskDialog(item, "snooze")); actions.append(snooze);
    if (item.status === "waiting_on") {
      const unblock = text("button", "No longer waiting", "button button-quiet"); unblock.type = "button"; unblock.addEventListener("click", () => runTaskLifecycle(item, "unblock")); actions.append(unblock);
    } else {
      const block = text("button", "Waiting on…", "button button-quiet"); block.type = "button"; block.addEventListener("click", () => blockTaskDialog(item)); actions.append(block);
    }
    const abandon = text("button", "Abandon…", "button button-quiet"); abandon.type = "button"; abandon.addEventListener("click", () => abandonTaskDialog(item)); actions.append(abandon);
  } else {
    const reopen = text("button", "Reopen", "button button-primary"); reopen.type = "button"; reopen.addEventListener("click", () => runTaskLifecycle(item, "reopen")); actions.append(reopen);
  }
  pane.append(actions); return pane;
}

function reminderDetail(item, cockpit) {
  if (item?.record_kind === "task") return taskDetail(item);
  const pane = document.createElement("section"); pane.className = "card reminder-detail";
  if (!item) { pane.append(text("h2", "Choose one reminder"), text("p", "The cockpit carries the remembering. You only need to decide about one item at a time.", "empty")); return pane; }
  pane.append(text("p", `${item.account_label} · ${item.list_name}`, "eyebrow"), text("h2", item.title));
  if (item.notes) pane.append(text("p", item.notes));
  const why = document.createElement("p"); why.className = "focus-reason"; why.append(text("strong", "Why it is here: "), document.createTextNode((item.why || []).join(" · "))); pane.append(why);
  const facts = document.createElement("dl"); facts.className = "reminder-facts";
  const fields = [["Provider", item.account_label], ["Scheduled", reminderDate(item.due_at, "full")], ["Priority", item.priority === 1 ? "High" : item.priority === 5 ? "Medium" : item.priority === 9 ? "Low" : "None"], ["Repeats", item.recurrence_rules?.length ? item.recurrence_rules.join(", ") : "No"], ["Context cues", item.cues?.length || 0]];
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
  if (item.last_event_id) {
    const triage = text("button", "Extract related work", "button button-quiet"); triage.type = "button";
    triage.addEventListener("click", () => openAddForReview({
      exactText: [item.title, item.notes].filter(Boolean).join("\n\n"), sourceEventId: item.last_event_id,
      sourceSurface: "reminders", dialogTitle: "Extract related work from this reminder",
    })); actions.append(triage);
  }
  pane.append(actions);
  if (item.source_links?.length) { const links = document.createElement("div"); links.className = "source-links"; for (const link of item.source_links) { const anchor = text("a", link.description || "Open source", "link-button"); anchor.href = link.url; anchor.target = "_blank"; anchor.rel = "noreferrer"; links.append(anchor); } pane.append(links); }
  return pane;
}

function reminderQuickAdd(cockpit) {
  const cardNode = document.createElement("section"); cardNode.className = "card reminder-quick-add"; cardNode.append(text("h2", "Add to a connected provider"), text("p", "Write directly only to the selected Apple Reminders or Google Tasks list.", "state-note"));
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

function taskQuickAdd() {
  const cardNode = document.createElement("section"); cardNode.className = "card task-quick-add";
  cardNode.append(text("h2", "Add household work"), text("p", "Create an explicit canonical task. It appears here immediately and remains separate from provider truth.", "state-note"));
  const button = text("button", "New task", "button button-primary"); button.type = "button"; button.addEventListener("click", () => openActionDialog("New household task", taskPropertiesForm({}, { create: true }))); cardNode.append(button); return cardNode;
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
  if (event.last_event_id) { const triage = text("button", "Extract related work", "button button-quiet"); triage.type = "button"; triage.addEventListener("click", () => openAddForReview({ exactText: [event.title, event.description, event.location].filter(Boolean).join("\n\n"), sourceEventId: event.last_event_id, sourceSurface: "calendar", dialogTitle: "Extract related work from this event" })); actions.append(triage); }
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
  if (state.reminderDeepLinkPending) {
    const linkedItem = cockpit.items.find((item) => item.id === state.reminderItem);
    if (linkedItem) {
      state.remindersView = linkedItem.bucket;
      state.remindersAccount = "all";
      state.remindersList = "all";
      replaceQuery({ view: linkedItem.bucket, account: null, list: null, item: linkedItem.id });
    }
    state.reminderDeepLinkPending = false;
  }
  $("#mode-label").textContent = "Private · canonical tasks + provider truth · deterministic";
  root.append(freshnessStrip(data), remindersHero(cockpit), remindersControls(cockpit, data));
  const visible = cockpit.items.filter(reminderMatches);
  if (!state.reminderItem || !visible.some((item) => item.id === state.reminderItem)) state.reminderItem = visible[0]?.id || null;
  const selected = visible.find((item) => item.id === state.reminderItem);
  const workspace = document.createElement("div"); workspace.className = "reminder-workspace";
  const list = document.createElement("section"); list.className = "card reminder-list"; list.append(text("h2", `${titleCase(state.remindersView)} tasks & reminders`));
  if (!visible.length) list.append(text("p", "Nothing in this lens. Your provider truth is unchanged.", "empty")); else { const items = document.createElement("ol"); items.className = "reminder-items"; for (const item of visible) { const li = document.createElement("li"); li.append(reminderItemButton(item)); items.append(li); } list.append(items); }
  workspace.append(list, reminderDetail(selected, cockpit)); root.append(workspace, taskQuickAdd(), reminderQuickAdd(cockpit), rhythmDefinitionsCard(cockpit.rhythms), reminderProviderTruth(cockpit));
}

function peopleMatchesLens(person) {
  if (state.peopleLens === "favorites") return Boolean(person.favorite);
  if (state.peopleLens === "organizations") return person.kind === "organization";
  if (state.peopleLens === "suppressed") return Boolean(person.suppressed);
  if (state.peopleLens === "household") return !["Contact", "Organization"].includes(person.relationship_label);
  return !person.suppressed;
}

function peopleHero(cockpit) {
  const section = document.createElement("section"); section.className = "hero-card people-hero";
  section.append(
    text("p", "PRIVATE RELATIONSHIP MEMORY", "eyebrow"),
    text("h2", "Remember the person, not another inbox.", "action-title"),
    text("p", "People joins only confirmed identity, relationship, contact, commitment, and interaction facts. Observed senders wait outside the directory until you confirm them."),
  );
  const stats = document.createElement("dl"); stats.className = "brief-stats people-stats";
  for (const [label, value] of [["People", cockpit.lens_counts?.all || 0], ["Household", cockpit.lens_counts?.household || 0], ["Gentle cues", cockpit.cues?.length || 0], ["Unresolved senders", cockpit.unresolved_endpoints?.count || 0]]) {
    const item = document.createElement("div"); item.className = "stat"; item.append(text("dt", label), text("dd", value)); stats.append(item);
  }
  section.append(stats); return section;
}

function peopleControls(data) {
  const cockpit = data.cockpit || {}; const section = document.createElement("section"); section.className = "card people-controls";
  const search = document.createElement("form"); search.className = "people-search";
  const input = document.createElement("input"); input.type = "search"; input.value = state.peopleQuery; input.placeholder = "Search names, organizations, circles, and confirmed facts"; input.setAttribute("aria-label", "Search people you are authorized to see");
  const submit = text("button", "Search", "button button-primary"); submit.type = "submit";
  const clear = text("button", "Clear", "button button-quiet"); clear.type = "button"; clear.hidden = !state.peopleQuery;
  search.append(input, submit, clear);
  search.addEventListener("submit", async (event) => {
    event.preventDefault(); const query = input.value.trim(); if (query.length < 2) return;
    submit.disabled = true; setBanner("Searching only relationship memory you are authorized to see…");
    try {
      const result = await api(`/api/member/v1/people/search?q=${encodeURIComponent(query)}`);
      const originals = new Map((cockpit.directory || []).map((person) => [person.party_id, person]));
      state.peopleQuery = query; state.peopleResults = result.results.map((person) => ({ ...(originals.get(person.party_id) || {}), ...person }));
      state.peopleSelected = state.peopleResults[0]?.party_id || null; state.peopleDetail = null;
      replaceQuery({ q: query, item: state.peopleSelected }); renderPeople(data);
      setBanner(`${state.peopleResults.length} authorized result${state.peopleResults.length === 1 ? "" : "s"}.`);
    } catch (error) { setBanner(`${error.message} No relationship data was changed.`); submit.disabled = false; }
  });
  clear.addEventListener("click", () => { state.peopleQuery = ""; state.peopleResults = null; state.peopleSelected = null; state.peopleDetail = null; clearBanner(); replaceQuery({ q: null, item: null }); renderPeople(data); });
  const lenses = document.createElement("div"); lenses.className = "comms-filter-row"; lenses.setAttribute("role", "group"); lenses.setAttribute("aria-label", "People lenses");
  for (const [key, label] of [["all", "All"], ["household", "Household"], ["favorites", "Favorites"], ["organizations", "Organizations"], ["suppressed", "Suppressed"]]) {
    const button = text("button", `${label} ${cockpit.lens_counts?.[key] || 0}`, "comms-filter"); button.type = "button"; button.setAttribute("aria-pressed", String(state.peopleLens === key));
    button.addEventListener("click", () => { state.peopleLens = key; state.peopleSelected = null; state.peopleDetail = null; clearBanner(); replaceQuery({ view: key, item: null }); renderPeople(data); }); lenses.append(button);
  }
  section.append(search, lenses); return section;
}

function peopleSources(cockpit) {
  const section = document.createElement("section"); section.className = "card people-sources";
  section.append(text("h2", "Sources"), text("p", "Each member's contacts stay private unless a Party or field group is explicitly shared.", "state-note"));
  const list = document.createElement("div"); list.className = "people-source-list";
  if (!(cockpit.sources || []).length) list.append(text("p", "No contact adapter is configured. Confirmed household Parties still remain available.", "empty"));
  for (const source of cockpit.sources || []) {
    const row = document.createElement("div"); row.className = "people-source-row";
    row.append(text("strong", source.label || titleCase(source.provider)), text("span", titleCase(source.state || "projected"), `pill state-label state-${String(source.state || "projected").replaceAll("_", "-")}`), text("small", `${source.record_count || 0} projected contact${Number(source.record_count || 0) === 1 ? "" : "s"} · ${titleCase(source.runtime)}`));
    if ((source.limitations || []).length) row.title = source.limitations.join(" "); list.append(row);
  }
  section.append(list);
  const settings = text("a", "Connect or review sources", "button button-quiet"); settings.href = "/member/settings"; section.append(settings); return section;
}

function peopleCuePanel(cockpit) {
  const section = document.createElement("section"); section.className = "card people-cues"; section.append(text("h2", "Relationship cues"), text("p", "Only cadences you explicitly enable can appear here.", "state-note"));
  if (!(cockpit.cues || []).length) { section.append(text("p", "No one is overdue by an enabled cadence. Silence is a valid result.", "empty")); return section; }
  const list = document.createElement("ul"); list.className = "item-list";
  for (const cue of cockpit.cues) { const item = document.createElement("li"); item.className = "item"; item.append(text("strong", cue.title), text("small", cue.reason)); const open = text("button", "Open person", "link-button"); open.type = "button"; open.addEventListener("click", () => document.querySelector(`[data-party-id="${CSS.escape(cue.party_id)}"]`)?.click()); item.append(open); list.append(item); }
  section.append(list); return section;
}

function peopleDirectory(people) {
  const section = document.createElement("section"); section.className = "card people-master";
  section.append(text("h2", state.peopleQuery ? `Results for “${state.peopleQuery}”` : titleCase(state.peopleLens)));
  if (!people.length) { section.append(text("p", state.peopleQuery ? "No authorized person matches this search." : "No confirmed people appear in this lens.", "empty")); return section; }
  const list = document.createElement("ol"); list.className = "people-master-list";
  for (const person of people) {
    const item = document.createElement("li"); const button = document.createElement("button"); button.type = "button"; button.className = "people-master-button"; button.dataset.partyId = person.party_id; button.setAttribute("aria-current", String(person.party_id === state.peopleSelected));
    const heading = document.createElement("span"); heading.className = "people-person-heading"; heading.append(text("strong", person.display_name || "Unnamed person")); if (person.favorite) heading.append(text("span", "Favorite", "pill pill-lavender"));
    const context = [person.relationship_label, person.organization, person.job_title].filter(Boolean).join(" · ");
    const contact = person.last_contact_at ? `Last visible contact ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(person.last_contact_at))}` : "No visible interaction yet";
    button.append(heading, text("span", context || "Confirmed Party", "people-context"), text("small", contact));
    button.addEventListener("click", () => { state.peopleSelected = person.party_id; state.peopleDetail = null; replaceQuery({ item: person.party_id }); renderPeople(state.surface); });
    item.append(button); list.append(item);
  }
  section.append(list); return section;
}

function peopleDetail(person, detail) {
  const section = document.createElement("section"); section.className = "card people-detail";
  if (!person) { section.append(text("h2", "Choose a person"), text("p", "Select one confirmed Party to see authorized relationship context, source records, commitments, and interactions.", "empty")); return section; }
  section.append(text("p", person.relationship_label || titleCase(person.kind), "eyebrow"), text("h2", person.display_name || "Unnamed person"), text("p", [person.organization, person.job_title].filter(Boolean).join(" · ") || "Confirmed identity", "meta"));
  if (!detail) { section.append(text("p", "Loading authorized relationship memory…", "empty")); return section; }
  const facts = document.createElement("dl"); facts.className = "people-facts";
  const identifiers = (detail.identifiers || []).map((value) => `${titleCase(value.kind)} · ${value.value}`);
  for (const [label, value] of [["Access", titleCase(detail.party?.access_mode || "authorized")], ["Contact", identifiers.join("\n") || "No authorized endpoint"], ["Sources", `${(detail.contact_records || []).length} member-owned record${(detail.contact_records || []).length === 1 ? "" : "s"}`], ["Last contact", detail.contact_gap?.last_contact_iso ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(detail.contact_gap.last_contact_iso)) : "No visible interaction"]]) facts.append(text("dt", label), text("dd", value));
  section.append(facts);
  if (["owner", "household_shared"].includes(detail.party?.access_mode)) {
    const profile = detail.member_profile || {}; const settings = document.createElement("details"); settings.className = "people-settings"; settings.append(text("summary", "Relationship settings"));
    const form = document.createElement("form"); form.className = "people-settings-form";
    const circle = document.createElement("input"); circle.value = profile.circle || ""; circle.placeholder = "Family, friend, school, professional…";
    const cadence = document.createElement("input"); cadence.type = "number"; cadence.min = "1"; cadence.max = "3650"; cadence.value = profile.desired_cadence_days || ""; cadence.placeholder = "No cadence";
    const tags = document.createElement("input"); tags.value = (profile.tags || []).join(", "); tags.placeholder = "Comma-separated private tags";
    const favorite = document.createElement("input"); favorite.type = "checkbox"; favorite.checked = Boolean(profile.favorite);
    const cues = document.createElement("input"); cues.type = "checkbox"; cues.checked = Boolean(profile.cues_enabled);
    const suppressed = document.createElement("input"); suppressed.type = "checkbox"; suppressed.checked = Boolean(profile.suppressed);
    const reason = document.createElement("input"); reason.value = profile.suppression_reason || ""; reason.placeholder = "Required when suppressed";
    const check = (label, control) => { const wrapper = document.createElement("label"); wrapper.className = "people-check"; wrapper.append(control, document.createTextNode(label)); return wrapper; };
    const toggles = document.createElement("div"); toggles.className = "people-toggle-row"; toggles.append(check("Favorite", favorite), check("Enable cadence cue", cues), check("Suppress cues", suppressed));
    const save = text("button", "Save relationship settings", "button button-primary"); save.type = "submit";
    form.append(field("Circle", circle), field("Cadence in days", cadence), field("Private tags", tags), toggles, field("Suppression reason", reason), save);
    form.addEventListener("submit", async (event) => { event.preventDefault(); save.disabled = true; setBanner("Saving your explicit relationship policy…"); try { await api(`/api/member/v1/people/${encodeURIComponent(person.party_id)}/profile`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ request_id: crypto.randomUUID(), expected_last_event_id: profile.last_event_id || null, circle: circle.value.trim() || null, desired_cadence_days: cadence.value ? Number(cadence.value) : null, cues_enabled: cues.checked, favorite: favorite.checked, suppressed: suppressed.checked, suppression_reason: reason.value.trim() || null, tags: tags.value.split(",").map((value) => value.trim()).filter(Boolean) }) }); setBanner("Relationship settings saved. Cues remain deterministic and opt-in."); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} Relationship settings were not changed.`); save.disabled = false; } });
    settings.append(form); section.append(settings);
  }
  const relationshipFacts = (detail.relationship_facts || []).filter((value) => !value.revoked_at);
  const factSection = document.createElement("section"); factSection.className = "people-detail-section"; factSection.append(text("h3", "Confirmed relationship facts"));
  if (!relationshipFacts.length) factSection.append(text("p", "No evidence-backed relationship facts have been confirmed.", "empty"));
  else { const list = document.createElement("ul"); list.className = "item-list"; for (const fact of relationshipFacts) { const item = document.createElement("li"); item.className = "item"; item.append(text("strong", fact.label || titleCase(fact.kind)), text("span", fact.value), text("small", `Confirmed from event ${fact.source_event_id}`)); list.append(item); } factSection.append(list); }
  const activity = document.createElement("section"); activity.className = "people-detail-section"; activity.append(text("h3", "Visible activity"));
  const combined = [...(detail.commitments || []).map((value) => ({ ...value, _kind: "Commitment" })), ...(detail.interactions || []).slice(0, 8).map((value) => ({ ...value, _kind: titleCase(value.channel || value.kind || "Interaction") }))];
  if (!combined.length) activity.append(text("p", "No visible commitments or interactions. AdministrateMe does not invent relationship history.", "empty"));
  else { const list = document.createElement("ul"); list.className = "item-list"; for (const value of combined.slice(0, 12)) { const item = document.createElement("li"); item.className = "item"; item.append(text("strong", value.title || value.subject || value.description || value._kind), text("small", value.occurred_at || value.due_at || (value.status ? memberStatusLabel(value.status) : value._kind))); list.append(item); } activity.append(list); }
  section.append(factSection, activity); return section;
}

function renderPeople(data) {
  const root = $("#surface"); root.replaceChildren(); const cockpit = data.cockpit || { directory: [], sources: [], cues: [], lens_counts: {} };
  $("#mode-label").textContent = "Private · confirmed identity · deterministic cues";
  const sourcePeople = state.peopleResults || cockpit.directory || []; const visible = sourcePeople.filter(peopleMatchesLens);
  if (!state.peopleSelected || !visible.some((person) => person.party_id === state.peopleSelected)) { state.peopleSelected = visible[0]?.party_id || null; state.peopleDetail = null; }
  const selected = visible.find((person) => person.party_id === state.peopleSelected) || null;
  const workspace = document.createElement("div"); workspace.className = "people-workspace";
  const rail = document.createElement("div"); rail.className = "people-rail-stack"; rail.append(peopleSources(cockpit), peopleCuePanel(cockpit));
  workspace.append(rail, peopleDirectory(visible), peopleDetail(selected, state.peopleDetail));
  root.append(freshnessStrip(data), peopleHero(cockpit), peopleControls(data), workspace);
  if (selected && !state.peopleDetail) api(`/api/member/v1/people/${encodeURIComponent(selected.party_id)}`).then((detail) => { if (state.peopleSelected === selected.party_id) { state.peopleDetail = detail; renderPeople(state.surface); } }).catch((error) => setBanner(`${error.message} Nothing was changed.`));
}

function parseJsonList(value) {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string" || !value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function noteSourceLabel(note) {
  return note.account_name || titleCase(note.source_kind || "note source");
}

function noteTimestamp(note) {
  const value = note.provider_modified_at || note.captured_at;
  if (!value) return "Time unavailable";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Time unavailable";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

function notePlainBody(note) {
  const body = String(note.body || "");
  if (note.body_format !== "html") return body;
  return new DOMParser().parseFromString(body, "text/html").body.textContent || "";
}

function noteSourceKey(note) {
  return `${note.source_kind || "unknown"}|${note.account_external_id || note.source_account_id || ""}`;
}

function noteMatchesLens(note) {
  if (state.notesSource !== "all" && noteSourceKey(note) !== state.notesSource) return false;
  if (state.notesLens === "recent") {
    const value = new Date(note.provider_modified_at || note.captured_at || 0).getTime();
    return value >= Date.now() - (30 * 24 * 60 * 60 * 1000);
  }
  if (state.notesLens === "shared") {
    return note.access_mode === "shared_reference" || (note.grants || []).some((grant) => !grant.revoked_at);
  }
  if (state.notesLens === "meetings") return note.source_type === "meeting_note";
  if (state.notesLens === "triage") return Number(note.triage_count || 0) > 0;
  return true;
}

function notesHero(data) {
  const counts = data.counts || {};
  const section = document.createElement("section"); section.className = "hero-card notes-hero";
  section.append(
    text("p", "PRIVATE RECALL", "eyebrow"),
    text("h2", "Write it down once. Find it when it matters.", "action-title"),
    text("p", "Notes keeps your member-owned sources searchable and turns only evidence-backed candidates into Triage proposals. Nothing becomes an official event, reminder, or task here."),
  );
  const stats = document.createElement("dl"); stats.className = "brief-stats notes-stats";
  for (const [label, value] of [["Visible notes", counts.notes || 0], ["Shared with me", counts.shared_with_me || 0], ["Meeting notes", counts.meeting_notes || 0], ["Needs Triage", counts.triage || 0]]) {
    const item = document.createElement("div"); item.className = "stat";
    item.append(text("dt", label), text("dd", value)); stats.append(item);
  }
  section.append(stats);
  return section;
}

function notesControls(data) {
  const section = document.createElement("section"); section.className = "card notes-controls";
  const search = document.createElement("form"); search.className = "notes-search";
  const input = document.createElement("input"); input.type = "search"; input.value = state.notesQuery; input.placeholder = "Search titles, notes, folders, and tags"; input.setAttribute("aria-label", "Search your authorized notes");
  const submit = text("button", "Search", "button button-primary"); submit.type = "submit";
  const clear = text("button", "Clear", "button button-quiet"); clear.type = "button"; clear.hidden = !state.notesQuery;
  search.append(input, submit, clear);
  search.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = input.value.trim();
    if (!query) return;
    submit.disabled = true;
    setBanner("Searching only notes you are authorized to search…");
    try {
      const params = new URLSearchParams({ q: query });
      if (state.notesSource !== "all") params.set("source", state.notesSource.split("|")[0]);
      const result = await api(`/api/member/v1/notes/search?${params}`);
      const originals = new Map((data.notes || []).map((note) => [note.knowledge_id, note]));
      state.notesQuery = query;
      state.notesResults = result.items.map((note) => ({ ...(originals.get(note.knowledge_id) || {}), ...note }));
      state.notesSelected = state.notesResults[0]?.knowledge_id || null;
      replaceQuery({ q: query, note: state.notesSelected });
      renderNotes(data);
      setBanner(`${state.notesResults.length} authorized search result${state.notesResults.length === 1 ? "" : "s"}.`);
    } catch (error) {
      setBanner(`${error.message} No note content was changed.`);
      submit.disabled = false;
    }
  });
  clear.addEventListener("click", () => {
    state.notesQuery = ""; state.notesResults = null; state.notesSelected = null;
    clearBanner(); replaceQuery({ q: null, note: null }); renderNotes(data);
  });
  const lenses = document.createElement("div"); lenses.className = "comms-filter-row"; lenses.setAttribute("role", "group"); lenses.setAttribute("aria-label", "Note lenses");
  for (const [key, label] of [["all", "All"], ["recent", "Recent"], ["shared", "Shared"], ["meetings", "Meetings"], ["triage", "Needs Triage"]]) {
    const button = text("button", label, "comms-filter"); button.type = "button"; button.setAttribute("aria-pressed", String(state.notesLens === key));
    button.addEventListener("click", () => { state.notesLens = key; state.notesSelected = null; clearBanner(); replaceQuery({ view: key, note: null }); renderNotes(data); }); lenses.append(button);
  }
  section.append(search, lenses);
  return section;
}

function notesSources(data) {
  const section = document.createElement("section"); section.className = "card notes-sources";
  section.append(text("h2", "Sources"), text("p", "Provider access and AdministrateMe sharing stay separate.", "state-note"));
  const list = document.createElement("div"); list.className = "notes-source-list";
  const all = text("button", `All sources · ${(data.notes || []).length}`, "notes-source-button"); all.type = "button"; all.setAttribute("aria-current", String(state.notesSource === "all"));
  all.addEventListener("click", () => { state.notesSource = "all"; state.notesSelected = null; state.notesResults = null; state.notesQuery = ""; clearBanner(); replaceQuery({ source: null, note: null, q: null }); renderNotes(data); }); list.append(all);
  for (const source of data.sources || []) {
    const key = `${source.source_kind || "unknown"}|${source.account_external_id || ""}`;
    const button = document.createElement("button"); button.type = "button"; button.className = "notes-source-button"; button.setAttribute("aria-current", String(state.notesSource === key));
    const name = source.account_name || titleCase(source.source_kind);
    button.append(text("strong", name), text("small", `${source.count} note${source.count === 1 ? "" : "s"} · ${source.state === "limited" ? "Limited" : "Ready"}`));
    if ((source.warnings || []).length) button.title = source.warnings.map(titleCase).join(" · ");
    button.addEventListener("click", () => { state.notesSource = key; state.notesSelected = null; state.notesResults = null; state.notesQuery = ""; clearBanner(); replaceQuery({ source: key, note: null, q: null }); renderNotes(data); }); list.append(button);
  }
  section.append(list);
  const settings = text("a", "Connect or review sources", "button button-quiet"); settings.href = "/member/settings"; section.append(settings);
  return section;
}

function noteMasterList(notes, data) {
  const section = document.createElement("section"); section.className = "card notes-master";
  section.append(text("h2", state.notesQuery ? `Results for “${state.notesQuery}”` : titleCase(state.notesLens)));
  if (!notes.length) {
    section.append(text("p", state.notesQuery ? "No authorized notes match this search." : "No notes appear in this lens. Connect a member-owned source in Settings, or choose another lens.", "empty"));
    return section;
  }
  const list = document.createElement("ol"); list.className = "notes-master-list";
  for (const note of notes) {
    const item = document.createElement("li"); const button = document.createElement("button"); button.type = "button"; button.className = "notes-master-button"; button.setAttribute("aria-current", String(note.knowledge_id === state.notesSelected));
    const title = note.title || "Untitled note";
    const preview = notePlainBody(note).replace(/\s+/g, " ").trim().slice(0, 130);
    button.append(text("strong", title), text("span", preview || "No note text", "notes-preview"), text("small", `${noteSourceLabel(note)} · ${noteTimestamp(note)}`));
    if (note.access_mode === "shared_reference") button.append(text("span", "Shared with me", "pill pill-lavender"));
    if (note.triage_count) button.append(text("span", `${note.triage_count} in Triage`, "pill pill-blush"));
    button.addEventListener("click", () => { state.notesSelected = note.knowledge_id; replaceQuery({ note: note.knowledge_id }); renderNotes(data); });
    item.append(button); list.append(item);
  }
  section.append(list);
  return section;
}

function noteSharing(note, data) {
  const section = document.createElement("section"); section.className = "notes-sharing";
  section.append(text("h3", "AdministrateMe access"), text("p", "A provider share never grants access here. Share one note explicitly and revoke it independently.", "state-note"));
  const activeGrants = (note.grants || []).filter((grant) => !grant.revoked_at);
  for (const grant of activeGrants) {
    const member = (data.sharing_members || []).find((value) => value.member_id === grant.grantee_member_id);
    const row = document.createElement("div"); row.className = "notes-grant-row";
    const body = document.createElement("div"); body.append(text("strong", member?.label || "Enrolled household adult"), text("small", (grant.permissions || []).map(titleCase).join(" · ")));
    const revoke = text("button", "Revoke", "link-button"); revoke.type = "button";
    revoke.addEventListener("click", async () => {
      revoke.disabled = true; setBanner("Revoking this AdministrateMe note grant…");
      try {
        await api(`/api/member/v1/notes/${encodeURIComponent(note.knowledge_id)}/grants/${encodeURIComponent(grant.grant_id)}/revoke`, {
          method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ request_id: crypto.randomUUID() }),
        });
        setBanner("Access revoked. Provider sharing, if any, was not changed."); await reloadActiveSurface();
      } catch (error) { setBanner(`${error.message} Access was not changed.`); revoke.disabled = false; }
    });
    row.append(body, revoke); section.append(row);
  }
  if (!(data.sharing_members || []).length) {
    section.append(text("p", "No other enrolled adult member is eligible for an AdministrateMe grant.", "empty"));
    return section;
  }
  const form = document.createElement("form"); form.className = "notes-share-form";
  const member = selectControl((data.sharing_members || []).map((value) => ({ value: value.member_id, label: value.label })), "", "Choose household adult…"); member.name = "member"; member.required = true;
  const submit = text("button", "Share note", "button button-quiet"); submit.type = "submit";
  const permission = document.createElement("input"); permission.value = "Read and search"; permission.disabled = true;
  form.append(field("Member", member), field("Permission", permission), submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); submit.disabled = true; setBanner("Recording one explicit note grant…");
    try {
      await api(`/api/member/v1/notes/${encodeURIComponent(note.knowledge_id)}/grants`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({ request_id: crypto.randomUUID(), grantee_member_id: member.value, permissions: ["read", "search"] }),
      });
      setBanner("Note access recorded. No provider permission was changed."); await reloadActiveSurface();
    } catch (error) { setBanner(`${error.message} Access was not changed.`); submit.disabled = false; }
  });
  section.append(form);
  return section;
}

function noteDetail(note, data) {
  const section = document.createElement("section"); section.className = "card notes-detail";
  if (!note) {
    section.append(text("h2", "Choose a note"), text("p", "Select one note to read its authorized copy, provenance, sharing state, and evidence-backed Triage candidates.", "empty"));
    return section;
  }
  section.append(text("p", titleCase(note.source_type || "note"), "eyebrow"), text("h2", note.title || "Untitled note"), text("p", `${noteSourceLabel(note)} · ${noteTimestamp(note)}`, "meta"));
  const tags = parseJsonList(note.tags_json);
  if (tags.length) { const row = document.createElement("div"); row.className = "notes-tag-row"; for (const tag of tags) row.append(text("span", tag, "pill pill-lavender")); section.append(row); }
  const body = text("pre", notePlainBody(note) || "This provider record has no readable note text.", "notes-body"); section.append(body);
  const actions = document.createElement("div"); actions.className = "button-row";
  if (note.source_url && /^https?:\/\//i.test(note.source_url)) { const open = text("a", "Open in source", "button button-quiet"); open.href = note.source_url; open.target = "_blank"; open.rel = "noopener noreferrer"; actions.append(open); }
  if (note.triage_href) { const triage = text("a", `Review ${note.triage_count} in Triage`, "button button-primary"); triage.href = note.triage_href; actions.append(triage); }
  if (note.last_event_id) { const extract = text("button", "Send to Triage", "button button-quiet"); extract.type = "button"; extract.addEventListener("click", () => openAddForReview({ exactText: [note.title, notePlainBody(note)].filter(Boolean).join("\n\n"), sourceEventId: note.last_event_id, sourceSurface: "notes", dialogTitle: "Extract work from this note" })); actions.append(extract); }
  if (note.last_event_id) { const evidence = text("button", "View source evidence", "link-button"); evidence.type = "button"; evidence.addEventListener("click", () => showEvidence(note.last_event_id)); actions.append(evidence); }
  section.append(actions);
  const facts = document.createElement("dl"); facts.className = "notes-facts";
  for (const [label, value] of [["Folder", note.folder_path || "Provider root"], ["Access", note.access_mode === "shared_reference" ? "Explicit shared reference" : "Owner"], ["Format", titleCase(note.body_format || "plain")], ["Revision", note.provider_revision || "Unreported"]]) facts.append(text("dt", label), text("dd", value));
  section.append(facts);
  const warnings = parseJsonList(note.capability_warnings_json);
  if (warnings.length) section.append(text("p", `Provider limits: ${warnings.map(titleCase).join(" · ")}.`, "possible-copy"));
  if (note.access_mode === "owner" && note.sensitivity !== "privileged") section.append(noteSharing(note, data));
  else if (note.sensitivity === "privileged") section.append(text("p", "Privileged notes stay owner-only and are excluded from model processing and search indexes.", "review-state"));
  return section;
}

function renderNotes(data) {
  const root = $("#surface"); root.replaceChildren();
  $("#mode-label").textContent = "Private · authorized recall · deterministic Triage";
  const notes = (state.notesResults || data.notes || []).filter(noteMatchesLens);
  if (!state.notesSelected || !notes.some((note) => note.knowledge_id === state.notesSelected)) state.notesSelected = notes[0]?.knowledge_id || null;
  const selected = notes.find((note) => note.knowledge_id === state.notesSelected) || null;
  const workspace = document.createElement("div"); workspace.className = "notes-workspace";
  workspace.append(notesSources(data), noteMasterList(notes, data), noteDetail(selected, data));
  root.append(freshnessStrip(data), notesHero(data), notesControls(data), workspace);
}

const FINANCE_VIEWS = [
  ["overview", "Overview"],
  ["cashflow", "Cash flow"],
  ["spending", "Spending"],
  ["scenarios", "Scenarios"],
  ["networth", "Net worth"],
  ["health", "Data health"],
];

function financeControls() {
  const controls = document.createElement("nav"); controls.className = "finance-controls"; controls.setAttribute("aria-label", "Finance views");
  for (const [value, label] of FINANCE_VIEWS) {
    const button = text("button", label, "finance-view-button"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.financeView === value));
    button.addEventListener("click", () => { state.financeView = value; replaceQuery({ view: value }); renderFinance(state.surface); });
    controls.append(button);
  }
  return controls;
}

function financeHero(cockpit) {
  const focus = cockpit.focus || {};
  const hero = document.createElement("section"); hero.className = `hero-card finance-hero finance-${focus.state || "steady"}`;
  const copy = document.createElement("div");
  copy.append(text("p", "HOUSEHOLD CASH-FLOW COCKPIT", "eyebrow"), text("h2", focus.title || "Finance plan", "action-title"), text("p", focus.why || "Deterministic workbook and transaction state.", "brief-summary"), text("p", focus.action || "", "microstep"));
  const baseline = (cockpit.scenarios || []).find((item) => item.is_baseline) || cockpit.scenarios?.[0];
  const quick = document.createElement("dl"); quick.className = "finance-hero-stats";
  for (const [label, value] of [
    ["Cash", moneyMinor(cockpit.summary.cash_minor, { compact: true })],
    ["Month-to-date", moneyMinor(cockpit.summary.mtd_net_minor, { compact: true })],
    ["Monthly plan", moneyMinor((baseline || {}).monthly_net_minor, { compact: true })],
    ["Cash runway", baseline?.cash_out_month ? `${baseline.cash_out_month} mo` : `>${baseline?.horizon_months || 60} mo`],
  ]) { const item = document.createElement("div"); item.append(text("dt", label), text("dd", value)); quick.append(item); }
  hero.append(copy, quick); return hero;
}

function financeKpis(cockpit) {
  const grid = document.createElement("section"); grid.className = "finance-kpi-grid"; grid.setAttribute("aria-label", "Finance summary");
  const values = [
    ["Forecast month-end", cockpit.summary.projected_month_income_minor - cockpit.summary.projected_month_expenses_minor, "Current pace, not a promise"],
    ["Monthly plan", cockpit.summary.base_monthly_income_minor - cockpit.summary.base_monthly_expenses_minor, "Workbook baseline"],
    ["Net worth", cockpit.summary.net_worth_minor, "Observed accounts + workbook assets − liabilities"],
    ["Spending vs prior", cockpit.summary.mtd_expenses_minor - cockpit.summary.prior_month_expenses_minor, "Same elapsed time last month"],
  ];
  for (const [label, value, note] of values) { const item = document.createElement("article"); item.className = "card finance-kpi"; item.append(text("span", label, "eyebrow"), text("strong", moneyMinor(value, { compact: true })), text("small", note)); grid.append(item); }
  return grid;
}

function financeCashFlow(cockpit) {
  const section = document.createElement("section"); section.className = "card finance-cashflow"; section.append(text("h2", "Cash flow plan"), text("p", "Observed activity is compared with the workbook baseline. The dashboard never rewrites your bank.", "state-note"));
  const rows = [
    ["Income", cockpit.summary.mtd_income_minor, cockpit.summary.base_monthly_income_minor],
    ["Expenses", cockpit.summary.mtd_expenses_minor, cockpit.summary.base_monthly_expenses_minor],
    ["Net", cockpit.summary.mtd_net_minor, cockpit.summary.base_monthly_income_minor - cockpit.summary.base_monthly_expenses_minor],
  ];
  const table = document.createElement("table"); table.className = "finance-table"; const head = document.createElement("thead"); const headRow = document.createElement("tr"); for (const value of ["Measure", "Month to date", "Monthly plan"]) headRow.append(text("th", value)); head.append(headRow); const body = document.createElement("tbody");
  for (const [label, actual, plan] of rows) { const row = document.createElement("tr"); row.append(text("th", label), text("td", moneyMinor(actual)), text("td", moneyMinor(plan))); body.append(row); }
  table.append(head, body); section.append(table); return section;
}

function financeBudgets(cockpit) {
  const section = document.createElement("section"); section.className = "card finance-budget"; section.append(text("h2", "Spending plan"));
  if (!(cockpit.budgets || []).length) { section.append(text("p", "No category budgets are published yet. Add them in the workbook; the dashboard will not invent targets.", "empty")); return section; }
  const list = document.createElement("ul"); list.className = "finance-budget-list";
  for (const item of cockpit.budgets) { const row = document.createElement("li"); const heading = document.createElement("div"); heading.append(text("strong", item.category), text("span", item.state === "watch" ? "Review" : "Within plan", `pill state-label state-${item.state === "watch" ? "degraded" : "healthy"}`)); const progress = document.createElement("div"); progress.className = "finance-progress"; const fill = document.createElement("span"); const ratio = item.planned_minor ? Math.min(1.2, item.actual_minor / item.planned_minor) : 0; fill.style.width = `${Math.min(100, ratio * 100)}%`; progress.append(fill); row.append(heading, progress, text("small", `${moneyMinor(item.actual_minor)} of ${moneyMinor(item.planned_minor)} · ${moneyMinor(item.remaining_minor)} remaining`)); list.append(row); }
  section.append(list); return section;
}

function financeScenarios(cockpit) {
  const section = document.createElement("section"); section.className = "card finance-scenarios"; section.append(text("h2", "Scenario lab"), text("p", "Scenarios are workbook-authored deltas. Switching here compares projections; it does not change the baseline.", "state-note"));
  const scenarios = cockpit.scenarios || [];
  if (!scenarios.length) { section.append(text("p", "No scenario is available.", "empty")); return section; }
  if (!state.financeScenario || !scenarios.some((item) => item.scenario_id === state.financeScenario)) state.financeScenario = (scenarios.find((item) => item.is_baseline) || scenarios[0]).scenario_id;
  const switcher = document.createElement("div"); switcher.className = "finance-scenario-switcher";
  for (const scenario of scenarios) { const button = text("button", scenario.name, "finance-scenario-button"); button.type = "button"; button.setAttribute("aria-pressed", String(scenario.scenario_id === state.financeScenario)); button.addEventListener("click", () => { state.financeScenario = scenario.scenario_id; renderFinance(state.surface); }); switcher.append(button); }
  const selected = scenarios.find((item) => item.scenario_id === state.financeScenario) || scenarios[0]; const stats = document.createElement("dl"); stats.className = "finance-scenario-stats";
  for (const [label, value] of [["Monthly net", moneyMinor(selected.monthly_net_minor)], ["Ending cash", moneyMinor(selected.ending_cash_minor)], ["Below reserve", selected.below_floor_month ? `Month ${selected.below_floor_month}` : "Not in horizon"], ["Cash out", selected.cash_out_month ? `Month ${selected.cash_out_month}` : "Not in horizon"]]) { const item = document.createElement("div"); item.append(text("dt", label), text("dd", value)); stats.append(item); }
  const chart = document.createElement("div"); chart.className = "finance-spark"; chart.setAttribute("aria-label", `${selected.name} cash forecast`); const points = selected.points || []; const max = Math.max(...points.map((item) => Math.abs(item.cash_minor)), 1); for (const point of points.filter((_item, index) => index % Math.max(1, Math.ceil(points.length / 24)) === 0)) { const bar = document.createElement("span"); bar.style.height = `${Math.max(4, Math.abs(point.cash_minor) / max * 100)}%`; bar.classList.toggle("negative", point.cash_minor < 0); bar.title = `Month ${point.month}: ${moneyMinor(point.cash_minor)}`; chart.append(bar); }
  section.append(switcher, stats, chart); if (selected.notes) section.append(text("p", selected.notes, "meta")); return section;
}

function financeGoals(cockpit) {
  const section = document.createElement("section"); section.className = "card finance-goals";
  section.append(text("h2", "Goals and capital plans"), text("p", "Goals organize future uses of money. They never move funds or silently change a scenario.", "state-note"));
  const goals = cockpit.goals || [];
  if (!goals.length) { section.append(text("p", "No goals are published yet. Add a target in the workbook when it is useful.", "empty")); return section; }
  const list = document.createElement("ul"); list.className = "finance-goal-list";
  for (const goal of goals) {
    const row = document.createElement("li");
    const heading = document.createElement("div"); heading.append(text("strong", goal.name), text("span", titleCase(goal.status), `pill state-label state-${goal.status === "complete" ? "healthy" : "ready"}`));
    const target = Number(goal.target_minor || 0); const current = Number(goal.current_minor || 0); const ratio = target > 0 ? Math.min(1, current / target) : 0;
    const progress = document.createElement("div"); progress.className = "finance-progress"; const fill = document.createElement("span"); fill.style.width = `${ratio * 100}%`; progress.append(fill);
    const due = goal.target_date ? ` · target ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(`${goal.target_date}T00:00:00`))}` : "";
    row.append(heading, progress, text("small", `${moneyMinor(current)} of ${moneyMinor(target)}${due}`)); if (goal.notes) row.append(text("small", goal.notes));
    const actions = document.createElement("div"); actions.className = "button-row";
    if (goal.status === "active") {
      const pause = text("button", "Pause", "button button-quiet"); pause.type = "button"; pause.addEventListener("click", () => financeGoalLifecycleDialog(goal, "pause")); actions.append(pause);
      const complete = text("button", "Mark complete", "button button-primary"); complete.type = "button"; complete.addEventListener("click", () => financeGoalLifecycleDialog(goal, "complete")); actions.append(complete);
    } else if (["paused", "complete"].includes(goal.status)) {
      const reopen = text("button", "Reopen", "button button-primary"); reopen.type = "button"; reopen.addEventListener("click", () => runFinanceGoalLifecycle(goal, "reopen")); actions.append(reopen);
    }
    if (goal.last_event_id) { const evidence = text("button", "View evidence", "link-button"); evidence.type = "button"; evidence.addEventListener("click", () => showEvidence(goal.last_event_id)); actions.append(evidence); }
    row.append(actions); list.append(row);
  }
  section.append(list); return section;
}

async function runFinanceGoalLifecycle(goal, action, reason = null) {
  setBanner(`Recording ${action} for this finance goal…`);
  try {
    await api(`/api/member/v1/finance/goals/${encodeURIComponent(goal.goal_id)}/lifecycle`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        action, reason, expected_last_event_id: goal.last_event_id,
        request_id: crypto.randomUUID(),
      }),
    });
    setBanner(action === "complete" ? "Goal completed and retained in history." : action === "pause" ? "Goal paused without changing the plan or moving money." : "Goal reopened from its existing definition.");
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} The finance goal was not changed.`);
  }
}

function financeGoalLifecycleDialog(goal, action) {
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", action === "complete" ? "This records the planning outcome. It does not move funds." : "Pausing removes the goal from active attention without deleting its plan or evidence.", "state-note"));
  const reason = document.createElement("textarea"); reason.rows = 3; reason.maxLength = 500;
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const keep = text("button", "Keep active", "button button-quiet"); keep.type = "button"; keep.addEventListener("click", closeActionDialog);
  const submit = text("button", action === "complete" ? "Mark complete" : "Pause goal", "button button-primary"); submit.type = "submit"; buttons.append(keep, submit);
  form.append(field(action === "complete" ? "Outcome note (optional)" : "Reason (optional)", reason), buttons);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runFinanceGoalLifecycle(goal, action, reason.value.trim() || null); });
  openActionDialog(action === "complete" ? "Complete this goal?" : "Pause this goal?", form);
}

function financeTransactions(cockpit) {
  const section = document.createElement("section"); section.className = "card finance-transactions"; section.append(text("h2", "Recent money movement"));
  const flows = cockpit.recent_flows || [];
  if (!flows.length) { section.append(text("p", "No visible transactions in this scope.", "empty")); return section; }
  const list = document.createElement("ul"); list.className = "finance-transaction-list";
  for (const flow of flows) {
    const row = document.createElement("li"); const body = document.createElement("div");
    body.append(text("strong", flow.merchant_name || flow.memo || flow.notes || titleCase(flow.kind || "Money movement")), text("small", `${flow.occurred_at || "Date unavailable"} · ${flow.assigned_category || flow.category || flow.provider_category || "Uncategorized"}`));
    const signed = flow.kind === "received" ? Number(flow.amount_minor || 0) : -Math.abs(Number(flow.amount_minor || 0));
    row.append(body, text("strong", moneyMinor(signed), signed < 0 ? "finance-outflow" : "finance-inflow")); list.append(row);
  }
  section.append(list); return section;
}

function financeAccounts(cockpit) {
  const section = document.createElement("section"); section.className = "card"; section.append(text("h2", "Net worth inputs"), text("p", `Computed net worth: ${moneyMinor(cockpit.summary.net_worth_minor)}. Account details remain member-scoped and privileged.`, "state-note"));
  if (!(cockpit.accounts || []).length) { section.append(text("p", "No visible provider accounts. Workbook asset and liability assumptions still participate in the total.", "empty")); return section; }
  const list = document.createElement("ul"); list.className = "finance-account-list"; for (const account of cockpit.accounts) { const row = document.createElement("li"); const body = document.createElement("div"); const liability = ["credit", "loan"].includes(account.account_type); body.append(text("strong", account.display_name), text("small", `${account.institution_name} · ${liability ? "Liability" : "Asset"} · ${titleCase(account.account_type)} · •••• ${account.last4 || "—"}`)); const signed = liability ? -Math.abs(Number(account.current_balance_minor || 0)) : Number(account.current_balance_minor || 0); row.append(body, text("strong", moneyMinor(signed), liability ? "finance-outflow" : "")); list.append(row); } section.append(list); return section;
}

function financeHealth(cockpit) {
  const health = cockpit.data_health || {}; const workbook = cockpit.workbook;
  const section = document.createElement("section"); section.className = "card finance-health"; section.append(text("h2", "Source and model health"));
  const facts = document.createElement("dl"); facts.className = "finance-health-grid"; for (const [label, value] of [["State", titleCase(health.state)], ["Transactions", health.transaction_count || 0], ["Accounts", health.account_count || 0], ["Accounts needing attention", health.unhealthy_account_count || 0], ["Pending", health.pending_count || 0], ["Uncategorized", health.uncategorized_count || 0], ["Non-USD rows excluded", health.unsupported_currency_count || 0], ["Latest source", health.latest_source_at ? new Date(health.latest_source_at).toLocaleString() : "No source watermark"], ["Workbook", workbook ? `${titleCase(workbook.validation_state)} · schema ${workbook.workbook_schema_version}` : "Not published"]]) facts.append(text("dt", label), text("dd", value)); section.append(facts);
  if (workbook?.validation_failures?.length) { const list = document.createElement("ul"); list.className = "settings-rule-list"; for (const failure of workbook.validation_failures) list.append(text("li", failure)); section.append(text("h3", "Workbook checks"), list); }
  section.append(text("p", "Authority split: Plaid owns provider facts; the workbook owns reviewed planning inputs and manual overrides; the event log preserves provenance and replay.", "review-state")); return section;
}

function renderFinance(data) {
  const root = $("#surface"); root.replaceChildren(); $("#mode-label").textContent = "Private · deterministic planning · no bank writes";
  const cockpit = data.cockpit || { summary: {}, budgets: [], scenarios: [], accounts: [], data_health: {} };
  const requested = new URLSearchParams(location.search).get("view"); if (FINANCE_VIEWS.some(([value]) => value === requested)) state.financeView = requested;
  root.append(freshnessStrip(data), financeHero(cockpit), financeControls());
  if (state.financeView === "overview") root.append(financeKpis(cockpit), financeCashFlow(cockpit), financeBudgets(cockpit), financeScenarios(cockpit), financeGoals(cockpit));
  else if (state.financeView === "cashflow") root.append(financeCashFlow(cockpit), financeScenarios(cockpit));
  else if (state.financeView === "spending") root.append(financeBudgets(cockpit), financeTransactions(cockpit));
  else if (state.financeView === "scenarios") root.append(financeScenarios(cockpit), financeGoals(cockpit));
  else if (state.financeView === "networth") root.append(financeAccounts(cockpit));
  else root.append(financeHealth(cockpit));
}

const SETTINGS_SECTIONS = [
  ["overview", "Overview"],
  ["account", "My account"],
  ["behavior", "CoS behavior"],
  ["connections", "Connections"],
  ["candidate-identification", "Candidate identification"],
  ["bridges", "Bridges & devices"],
  ["registry", "Places, assets & accounts"],
  ["privacy", "Household & privacy"],
  ["system", "Safety & system"],
];

function settingsNav() {
  const nav = document.createElement("nav"); nav.className = "settings-nav"; nav.setAttribute("aria-label", "Settings sections");
  for (const [value, label] of SETTINGS_SECTIONS) {
    const button = text("button", label, "settings-nav-button"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.settingsSection === value));
    button.addEventListener("click", () => { state.settingsSection = value; replaceQuery({ section: value }); renderSettings(state.surface); });
    nav.append(button);
  }
  return nav;
}

function identitySummary(identity, compact = false) {
  const section = document.createElement("section"); section.className = compact ? "settings-identity compact" : "card settings-identity";
  const heading = document.createElement("div"); heading.append(text("p", "SIGNED-IN IDENTITY", "eyebrow"), text("h2", identity.authenticated_display_name));
  const status = text("span", identity.identity_bound ? "Identity bound" : "Binding incomplete", `pill state-label state-${identity.identity_bound ? "healthy" : "blocked"}`);
  section.append(heading, status);
  const facts = document.createElement("dl"); facts.className = "settings-facts";
  for (const [label, value] of [["Role", titleCase(identity.authenticated_role)], ["Authenticated through", identity.authentication_method], ["Reading as", identity.view_display_name], ["Actions execute as", identity.authenticated_display_name]]) facts.append(text("dt", label), text("dd", value));
  section.append(facts);
  if (identity.viewing_as) section.append(text("p", `You are viewing ${identity.view_display_name}. Settings changes are disabled; actions still belong to ${identity.authenticated_display_name}.`, "review-state"));
  return section;
}

function settingsHero(data) {
  const control = data.control; const identity = control.identity;
  const hero = document.createElement("section"); hero.className = "hero-card settings-hero";
  const copy = document.createElement("div"); copy.append(text("p", "MEMBER CONTROL PLANE", "eyebrow"), text("h2", `You’re signed in as ${identity.authenticated_display_name}.`, "action-title"), text("p", "See what AdministrateMe can access, what is actually running, and what it is allowed to change. Every control below follows explicit rules and leaves an audit receipt.", "brief-summary"));
  const stats = document.createElement("dl"); stats.className = "settings-hero-stats";
  const healthy = control.connections.filter((item) => item.runtime === "running").length;
  for (const [label, value] of [["Needs attention", control.needs_attention.length], ["Connected", control.connections.filter((item) => item.setup === "connected").length], ["Running", healthy], ["Writes execute", data.observation.active ? "No" : "Gated"]]) { const item = document.createElement("div"); item.append(text("dt", label), text("dd", value)); stats.append(item); }
  hero.append(copy, stats); return hero;
}

function attentionCard(items) {
  const section = document.createElement("section"); section.className = "card settings-attention"; section.append(text("h2", "Needs attention"));
  if (!items.length) { section.append(text("p", "Nothing needs intervention. Unknown state will still remain visible in Connections.", "empty compact-empty")); return section; }
  const list = document.createElement("ul"); list.className = "settings-attention-list";
  for (const item of items) { const row = document.createElement("li"); row.append(text("span", titleCase(item.severity), `pill state-label state-${item.severity}`)); const body = document.createElement("div"); body.append(text("strong", item.title), text("small", item.detail)); const open = text("button", "Review", "link-button"); open.type = "button"; open.addEventListener("click", () => { state.settingsSection = item.section; replaceQuery({ section: item.section }); renderSettings(state.surface); }); row.append(body, open); list.append(row); }
  section.append(list); return section;
}

function connectionAxis(label, value, stateClass = value) {
  const axis = document.createElement("div"); axis.className = "connection-axis"; axis.append(text("span", label), text("strong", titleCase(value), `state-label state-${String(stateClass).replaceAll("_", "-")}`)); return axis;
}

function permissionList(title, values) {
  const box = document.createElement("div"); box.className = "permission-box"; box.append(text("span", title));
  if (!values.length) box.append(text("strong", "None"));
  else { const list = document.createElement("ul"); for (const value of values) list.append(text("li", titleCase(value))); box.append(list); }
  return box;
}

function connectionCard(connection, data) {
  const section = document.createElement("article"); section.className = "card connection-card";
  const head = document.createElement("div"); head.className = "connection-heading";
  const copy = document.createElement("div"); copy.append(text("p", connection.product, "eyebrow"), text("h2", connection.label), text("small", `${connection.provider_label} · ${connection.account_hint} · ${titleCase(connection.workspace)}`)); head.append(copy, text("span", titleCase(connection.deployment), "pill pill-lavender")); section.append(head);
  const axes = document.createElement("div"); axes.className = "connection-axes"; axes.append(connectionAxis("Setup", connection.setup), connectionAxis("Runtime", connection.runtime));
  const credentialState = connection.permissions.credential_reference_present ? "stored" : "missing"; axes.append(connectionAxis("Credential ref", credentialState, credentialState === "stored" ? "healthy" : "blocked"), connectionAxis("Safety", connection.safety.observation_only ? "observation only" : connection.safety.writes_execute ? "writes gated" : "read only", connection.safety.observation_only ? "observation-only" : "healthy")); section.append(axes);
  const permissions = document.createElement("div"); permissions.className = "permission-grid"; permissions.append(permissionList("Reviewed read operations", connection.permissions.read), permissionList("Reviewed write operations", connection.permissions.write)); section.append(permissions);
  const note = !connection.safety.human_gate_required ? "This connection has no reviewed provider write capability." : connection.safety.observation_only ? "Observation mode suppresses provider changes even when a reviewed provider write capability exists." : "Writes still require member confirmation, governance, rate limits, privacy checks, and independent approval."; section.append(text("p", note, "state-note"));
  if (connection.error) section.append(text("p", `${titleCase(connection.error.severity)} ${titleCase(connection.error.kind)} error${connection.error.occurred_at ? ` · ${new Date(connection.error.occurred_at).toLocaleString()}` : ""}.`, "review-state"));
  const actions = document.createElement("div"); actions.className = "button-row";
  for (const action of connection.actions || []) { const button = text("button", action === "pause" ? "Pause sync" : "Resume sync", action === "pause" ? "button button-quiet" : "button button-primary"); button.type = "button"; button.disabled = !data.control.identity.can_mutate; button.addEventListener("click", async () => { button.disabled = true; setBanner(`${action === "pause" ? "Pausing" : "Resuming"} ${connection.label}…`); try { const result = await api(`/api/member/v1/settings/connections/${encodeURIComponent(connection.id)}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ action, expected_revision: data.control.revision, request_id: crypto.randomUUID() }) }); setBanner(`${connection.label} is ${result.state}. Runtime reconciliation was audited.`); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} The connection was not changed.`); button.disabled = false; } }); actions.append(button); }
  const technical = document.createElement("details"); technical.className = "settings-technical"; technical.append(text("summary", "Technical status")); const facts = document.createElement("dl"); facts.className = "settings-facts"; for (const [label, value] of [["Connection ID", connection.id], ["Provider", connection.provider], ["Last cursor evidence", connection.last_sync_at ? new Date(connection.last_sync_at).toLocaleString() : "No cursor evidence yet"]]) facts.append(text("dt", label), text("dd", value)); technical.append(facts); section.append(actions, technical); return section;
}

function renderConnections(data) {
  const fragment = document.createDocumentFragment(); const intro = document.createElement("section"); intro.className = "state-card"; intro.append(text("strong", "Connection truth has four separate axes."), text("p", "Configured, authenticated, running, and allowed to write are different facts. Unknown is never promoted to healthy.")); fragment.append(intro);
  if (!data.control.connections.length) fragment.append(text("p", "No reviewed provider connection is configured for this member.", "empty card"));
  else { const grid = document.createElement("div"); grid.className = "settings-card-grid"; for (const connection of data.control.connections) grid.append(connectionCard(connection, data)); fragment.append(grid); }
  const add = document.createElement("section"); add.className = "card settings-add-provider"; add.append(text("h2", "Add a provider"), text("p", "Only reviewed adapters appear here. OAuth consent and credential setup run through a provider-specific secure enrollment flow; secrets never enter this page.")); const list = document.createElement("div"); list.className = "provider-roadmap"; for (const value of ["Google account · reviewed central adapter", "Apple apps · requires a member bridge", "Outlook, Twilio and BlueBubbles · adapter not installed"]) list.append(text("span", value, "pill pill-lavender")); add.append(list); fragment.append(add); return fragment;
}

const CANDIDATE_MODE_COPY = {
  review: "Identify supported candidates and place only eligible proposals in Triage.",
  manual: "Retain and account for source facts, but extract candidates only when you request it.",
  quiet: "Retain and account for source facts without proposing candidates.",
};

function candidatePolicyCard(source, data) {
  const policy = source.policy || {};
  const locked = Boolean(source.locked) || !data.control.identity.can_mutate;
  const form = document.createElement("form"); form.className = "card candidate-policy-card";
  const head = document.createElement("div"); head.className = "candidate-policy-heading";
  const copy = document.createElement("div"); copy.append(text("p", `${source.projection} · ${source.channel_type}`, "eyebrow"), text("h3", source.label));
  const status = source.availability === "planned" ? "Planned" : source.availability === "configured" || source.availability === "available" ? "Available" : "Not configured";
  head.append(copy, text("span", status, `pill state-label state-${source.availability === "planned" ? "unknown" : source.availability === "available_not_configured" ? "degraded" : "healthy"}`)); form.append(head);
  const signals = document.createElement("div"); signals.className = "candidate-policy-signals";
  for (const value of source.signals || []) signals.append(text("span", value, "pill pill-lavender"));
  form.append(signals);

  const mode = document.createElement("select"); mode.name = "mode"; mode.disabled = locked;
  for (const [value, label] of [["review", "Review automatically"], ["manual", "Only when I ask"], ["quiet", "Do not propose"]]) { const option = text("option", label); option.value = value; option.selected = policy.mode === value; mode.append(option); }
  const modeNote = text("small", CANDIDATE_MODE_COPY[policy.mode] || CANDIDATE_MODE_COPY.review, "candidate-mode-copy");
  mode.addEventListener("change", () => { modeNote.textContent = CANDIDATE_MODE_COPY[mode.value]; });
  form.append(field("Candidate admission", mode), modeNote);

  const languageRow = document.createElement("label"); languageRow.className = "candidate-language-toggle";
  const language = document.createElement("input"); language.type = "checkbox"; language.name = "language_enabled"; language.checked = Boolean(policy.language_enabled); language.disabled = locked || !source.language_available;
  const languageCopy = document.createElement("span"); languageCopy.append(text("strong", "Use grounded language extraction"), text("small", "May contribute typed fields only when backed by exact source evidence. It cannot admit, route, confirm, or write an item.")); languageRow.append(language, languageCopy); form.append(languageRow);

  const kinds = document.createElement("fieldset"); kinds.className = "candidate-kind-grid"; kinds.disabled = locked; kinds.append(text("legend", "Candidate types"));
  for (const value of source.candidate_kinds || []) { const label = document.createElement("label"); const input = document.createElement("input"); input.type = "checkbox"; input.value = value; input.checked = (policy.candidate_kinds || []).includes(value); label.append(input, document.createTextNode(` ${titleCase(value)}`)); kinds.append(label); }
  form.append(kinds);

  const layers = document.createElement("details"); layers.className = "settings-technical"; layers.append(text("summary", "Signals and extraction layers")); const facts = document.createElement("dl"); facts.className = "settings-facts"; facts.append(text("dt", "Layers"), text("dd", (source.extraction_layers || []).join(" → ")), text("dt", "Source state"), text("dd", `${titleCase(source.implementation)} · ${titleCase(source.availability)}`), text("dt", "Candidate engine"), text("dd", source.policy_execution === "current" ? "Implemented" : "Planned · preference saved ahead of activation")); layers.append(facts); form.append(layers);
  if (source.locked) form.append(text("p", "Explicit member requests always remain eligible for Triage review. This safety path cannot be disabled.", "state-note"));
  else {
    const save = text("button", source.implementation === "planned" || source.policy_execution === "planned" ? "Save future preference" : "Save source settings", "button button-primary"); save.type = "submit"; save.disabled = !data.control.identity.can_mutate; form.append(save);
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); const selected = [...kinds.querySelectorAll("input:checked")].map((input) => input.value);
      if (!selected.length) { setBanner("Select at least one candidate type. Nothing was changed."); return; }
      save.disabled = true; setBanner(`Saving candidate identification settings for ${source.label}…`);
      try {
        await api(`/api/member/v1/settings/candidate-policies/${encodeURIComponent(source.id)}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ expected_revision: data.control.revision, request_id: crypto.randomUUID(), mode: mode.value, language_enabled: language.checked, candidate_kinds: selected }) });
        setBanner(`${source.label} settings saved. Source retention, privacy, routing, confirmation, and provider-write gates were unchanged.`); await reloadActiveSurface();
      } catch (error) { setBanner(`${error.message} Candidate identification settings were not changed.`); save.disabled = false; }
    });
  }
  return form;
}

const SOURCE_POLICY_V2_PRESETS = {
  focused: { acquisition_state: "active", triage_mode: "review", alert_mode: "financial_and_security", context_mode: "metadata_only", candidate_kinds: ["task", "commitment", "calendar_event", "reminder", "coordination"] },
  balanced: { acquisition_state: "active", triage_mode: "review", alert_mode: "financial_and_security", context_mode: "typed_facts" },
  review_everything: { acquisition_state: "active", triage_mode: "review", alert_mode: "actionable_and_critical", context_mode: "typed_facts_safe_snippet" },
};

function sourcePolicyV2Card(source, matrix) {
  const policy = source.policy || {};
  const form = document.createElement("form"); form.className = "card candidate-policy-card";
  const head = document.createElement("div"); head.className = "candidate-policy-heading";
  const copy = document.createElement("div"); copy.append(text("p", `${source.provider_label} · ${titleCase(source.workspace)}`, "eyebrow"), text("h3", source.label), text("small", source.account_hint ? `${source.account_hint} · exact adapter instance` : "Exact owner-scoped source"));
  head.append(copy, text("span", source.coordinate_state === "exact" ? "Exact source" : "Needs review", `pill state-label state-${source.coordinate_state === "exact" ? "healthy" : "degraded"}`)); form.append(head);
  if (source.direct_authenticated_intent) form.append(text("p", "An authenticated request you make here always remains eligible. Passive-source presets cannot suppress it.", "state-note"));
  if (source.migration_state === "migration_needed") form.append(text("p", "Your older source-family preference is still preserving live behavior. Choose a setting for this exact account to resolve the V2 shadow policy without applying one rule to every account.", "review-state"));
  if (!source.configurable && !source.direct_authenticated_intent) { form.append(text("p", "This source lacks one exact reviewed account or workspace coordinate. No V2 rule was expanded or activated.", "state-note")); return form; }

  const preset = document.createElement("select");
  for (const item of [...(matrix.presets || []), { id: "custom", label: "Advanced selection" }]) { const option = text("option", item.label); option.value = item.id; option.selected = policy.preset === item.id; preset.append(option); }
  preset.disabled = source.direct_authenticated_intent || !source.configurable;
  const acquisition = document.createElement("select"); for (const [value, label] of [["active", "Active"], ["paused", "Paused"]]) { const option = text("option", label); option.value = value; option.selected = policy.acquisition_state === value; acquisition.append(option); }
  const triage = document.createElement("select"); for (const [value, label] of [["direct_work_order_proposal", "Direct work-order proposal"], ["review", "Review in Triage"], ["alert_only", "Alert only"], ["context_only", "Context only"], ["ignore", "Ignore / suppress"], ["manual_only", "Only when I ask"]]) { const option = text("option", label); option.value = value; option.selected = policy.triage_mode === value; triage.append(option); }
  const alert = document.createElement("select"); for (const [value, label] of [["none", "No alerts"], ["security_only", "Security alerts"], ["financial_and_security", "Financial and security"], ["actionable_and_critical", "Actionable and critical"]]) { const option = text("option", label); option.value = value; option.selected = policy.alert_mode === value; alert.append(option); }
  const context = document.createElement("select"); for (const [value, label] of [["none", "No context"], ["metadata_only", "Metadata only"], ["typed_facts", "Typed facts"], ["typed_facts_safe_snippet", "Typed facts plus safe snippet"], ["content_on_explicit_authorized_demand", "Content only when I explicitly ask"]]) { const option = text("option", label); option.value = value; option.selected = policy.context_mode === value; context.append(option); }
  const controls = [acquisition, triage, alert, context]; controls.forEach((node) => { node.disabled = source.direct_authenticated_intent || !source.configurable; node.addEventListener("change", () => { preset.value = "custom"; }); });
  form.append(field("Recommended setup", preset));
  const advanced = document.createElement("details"); advanced.className = "settings-technical"; advanced.append(text("summary", "Advanced bounded selections"));
  const planes = document.createElement("div"); planes.className = "preference-grid"; planes.append(field("Acquisition and observation", acquisition), field("Triage", triage), field("Alerts", alert), field("Context", context)); advanced.append(planes);
  const kinds = document.createElement("fieldset"); kinds.className = "candidate-kind-grid"; kinds.disabled = source.direct_authenticated_intent || !source.configurable; kinds.append(text("legend", "Candidate types"));
  for (const value of matrix.candidate_kinds || []) { const label = document.createElement("label"); const input = document.createElement("input"); input.type = "checkbox"; input.value = value; input.checked = (policy.candidate_kinds || []).includes(value); input.addEventListener("change", () => { preset.value = "custom"; }); label.append(input, document.createTextNode(` ${titleCase(value)}`)); kinds.append(label); }
  advanced.append(kinds, text("p", "These choices cannot change credentials, scopes, identity, ownership, disclosure, or provider-write authority.", "state-note")); form.append(advanced);
  const preview = document.createElement("div"); preview.className = "state-note";

  function selectedPolicy() {
    return { preset: "custom", acquisition_state: acquisition.value, triage_mode: triage.value, alert_mode: alert.value, context_mode: context.value, candidate_kinds: [...kinds.querySelectorAll("input:checked")].map((input) => input.value), scope_widening: false, credential_creation: false, provider_write_authority: false, admission_active: false };
  }
  function requestSelection() {
    return preset.value === "custom" ? { preset: "custom", policy: selectedPolicy() } : { preset: preset.value };
  }
  function applyPreset() {
    const value = SOURCE_POLICY_V2_PRESETS[preset.value]; if (!value) return;
    acquisition.value = value.acquisition_state; triage.value = value.triage_mode; alert.value = value.alert_mode; context.value = value.context_mode;
    const selected = new Set(value.candidate_kinds || matrix.candidate_kinds || []); for (const input of kinds.querySelectorAll("input")) input.checked = selected.has(input.value);
  }
  preset.addEventListener("change", applyPreset);
  async function savePolicy(action = "set", forcedPolicy = null) {
    const selection = forcedPolicy || requestSelection();
    const body = { expected_revision: matrix.revision, request_id: crypto.randomUUID(), coordinate: source.coordinate, action, preset: selection.preset || "balanced" }; if (selection.policy) body.policy = selection.policy;
    return api(`/api/member/v1/settings/source-policies-v2/${encodeURIComponent(source.policy_key)}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(body) });
  }
  const actions = document.createElement("div"); actions.className = "button-row";
  const previewButton = text("button", "Preview recent effect", "button button-quiet"); previewButton.type = "button"; previewButton.addEventListener("click", async () => { previewButton.disabled = true; setBanner(`Previewing ${source.label} without changing it…`); try { const result = await api("/api/member/v1/settings/source-policies-v2/preview", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ coordinate: source.coordinate, recent_limit: 100, ...requestSelection() }) }); const counts = result.estimated_recent_effect; preview.replaceChildren(text("strong", `${counts.source_facts} recent source fact${counts.source_facts === 1 ? "" : "s"}: ${counts.triage} to Triage · ${counts.alerts} alerts · ${counts.context} context · ${counts.provenance_only} provenance only.`)); for (const example of result.examples || []) { const line = document.createElement("div"); line.append(text("small", `${example.synthetic ? "Synthetic example" : "Authorized recent example"} · Triage ${titleCase(example.route.triage)} · Alert ${titleCase(example.route.alert)} · Context ${titleCase(example.route.context)}`)); if (example.source_event_id) { const why = text("button", "Why did this appear?", "button button-quiet"); why.type = "button"; why.addEventListener("click", async () => { try { const result = await api(`/api/member/v1/settings/source-policies-v2/why/${encodeURIComponent(example.source_event_id)}`); line.append(text("p", `${result.current.explanation} ${result.shadow.explanation}`, "state-note")); why.disabled = true; } catch (error) { setBanner(error.message); } }); line.append(why); } preview.append(line); } setBanner("Preview complete. Nothing was changed."); } catch (error) { setBanner(`${error.message} Nothing was changed.`); } finally { previewButton.disabled = false; } }); actions.append(previewButton);
  if (source.configurable && !source.direct_authenticated_intent) {
    const pause = text("button", policy.acquisition_state === "paused" ? "Resume" : "Pause", "button button-quiet"); pause.type = "button"; pause.addEventListener("click", async () => { pause.disabled = true; const next = selectedPolicy(); next.acquisition_state = policy.acquisition_state === "paused" ? "active" : "paused"; try { await savePolicy("set", { preset: "custom", policy: next }); setBanner(`${source.label} ${next.acquisition_state === "paused" ? "paused" : "resumed"}. Live admission is still unchanged.`); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} The policy was not changed.`); pause.disabled = false; } });
    const reset = text("button", "Reset to recommended", "button button-quiet"); reset.type = "button"; reset.addEventListener("click", async () => { reset.disabled = true; try { await savePolicy("reset", { preset: "balanced" }); setBanner(`${source.label} reset to the recommended shadow default.`); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} The policy was not changed.`); reset.disabled = false; } });
    const save = text("button", "Save shadow policy", "button button-primary"); save.type = "submit"; actions.append(pause, reset, save);
    form.addEventListener("submit", async (event) => { event.preventDefault(); if (preset.value === "custom" && !selectedPolicy().candidate_kinds.length) { setBanner("Select at least one candidate type. Nothing was changed."); return; } save.disabled = true; try { await savePolicy(); setBanner(`${source.label} shadow policy saved. No live admission or provider action changed.`); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} The policy was not changed.`); save.disabled = false; } });
  }
  form.append(actions, preview); return form;
}

function candidateIdentificationSettings(data) {
  const fragment = document.createDocumentFragment(); const matrix = data.control.source_policy_v2 || { sources: [], presets: [], migrations: [], safety_limitations: [] }; const legacy = data.control.candidate_identification || { sources: [], non_configurable: [] };
  const intro = document.createElement("section"); intro.className = "hero-card candidate-policy-intro"; intro.append(text("p", "EXACT-SOURCE POLICY · SHADOW ONLY", "eyebrow"), text("h2", "Choose what reaches Triage, alerts, or context."), text("p", "Each setting belongs to you, one exact account, workspace, adapter instance, source class, and trusted route. V2 records what would change while current admission remains untouched.")); fragment.append(intro);
  if (!matrix.owner_visible) { const privateState = document.createElement("section"); privateState.className = "state-card"; privateState.append(text("strong", "These policies are owner-private."), text("p", "Adults and administrators cannot inspect or change another adult’s private source policy.")); fragment.append(privateState); return fragment; }
  const presets = document.createElement("section"); presets.className = "settings-summary-grid"; for (const item of matrix.presets || []) { const card = document.createElement("article"); card.className = "card settings-summary-card"; card.append(text("strong", item.label), text("small", item.explanation)); presets.append(card); } fragment.append(presets);
  if ((matrix.migrations || []).length) { const migration = document.createElement("section"); migration.className = "state-card"; migration.append(text("strong", "Older preferences need an exact account choice."), text("p", `${matrix.migrations.length} source-family preference${matrix.migrations.length === 1 ? "" : "s"} continue preserving live V1 behavior. Nothing was copied across every account, access was not expanded, and ambiguity was not discarded.`)); fragment.append(migration); }
  const providerSources = (matrix.sources || []).filter((source) => !source.direct_authenticated_intent); const directSources = (matrix.sources || []).filter((source) => source.direct_authenticated_intent);
  if (providerSources.length) { const section = document.createElement("details"); section.className = "candidate-policy-group"; section.open = true; section.append(text("summary", `Exact provider sources · ${providerSources.length}`)); const grid = document.createElement("div"); grid.className = "candidate-policy-grid"; for (const source of providerSources) grid.append(sourcePolicyV2Card(source, matrix)); section.append(grid); fragment.append(section); }
  const direct = document.createElement("details"); direct.className = "candidate-policy-group"; direct.append(text("summary", `Authenticated member entry · ${directSources.length} protected paths`)); const directGrid = document.createElement("div"); directGrid.className = "candidate-policy-grid"; for (const source of directSources) directGrid.append(sourcePolicyV2Card(source, matrix)); direct.append(directGrid); fragment.append(direct);
  const invariants = document.createElement("section"); invariants.className = "card candidate-policy-invariants"; invariants.append(text("h2", "Clear safety limitations")); const list = document.createElement("ul"); list.className = "settings-rule-list"; for (const value of matrix.safety_limitations || []) list.append(text("li", value)); invariants.append(list); fragment.append(invariants);
  const current = document.createElement("details"); current.className = "candidate-policy-group"; current.append(text("summary", "Current live V1 admission · retained until later activation")); const currentNote = document.createElement("section"); currentNote.className = "state-card"; currentNote.append(text("strong", "These coarse settings still control live admission."), text("p", "They are shown during migration so prior quiet or manual behavior is not lost. V2 does not copy one Gmail rule to every account.")); current.append(currentNote); const groups = new Map(); for (const source of legacy.sources || []) { if (!groups.has(source.group)) groups.set(source.group, []); groups.get(source.group).push(source); } for (const [group, sources] of groups) { const section = document.createElement("details"); section.className = "candidate-policy-group"; const reviewCount = sources.filter((source) => source.policy?.mode === "review").length; section.append(text("summary", `${group} · ${reviewCount} of ${sources.length} reviewing`)); const grid = document.createElement("div"); grid.className = "candidate-policy-grid"; for (const source of sources) grid.append(candidatePolicyCard(source, data)); section.append(grid); current.append(section); } fragment.append(current);
  const timing = document.createElement("section"); timing.className = "state-card"; timing.append(text("strong", "Every source fact gets a current and shadow route."), text("p", "The receipt keeps the exact reasons, policy version, source provenance, typed signal assessment, and zero-provider-effect result. Ignore never deletes history; context-only never puts the fact in Triage.")); fragment.append(timing); return fragment;
}

function defaultPreferenceValue(key, data) {
  const saved = data.control.behavior.preferences || {}; if (saved[key] != null) return saved[key]; const semantic = data.semantics || {};
  const defaults = { information_density: semantic.information_density ?? 2, planning_horizon_days: semantic.planning_horizon_days ?? 7, task_chunk_minutes: semantic.task_chunk_minutes ?? 15, interruption_budget: semantic.proactive_cap ?? 3, reminder_escalation: semantic.reminder_escalation ?? 1, celebration_intensity: semantic.celebration_intensity ?? 1, digest_hour: semantic.digest_hour ?? 8, quiet_start_hour: semantic.quiet_hours?.start ?? 22, quiet_end_hour: semantic.quiet_hours?.end ?? 7, location_cues: semantic.location_cues ?? false }; return defaults[key];
}

function behaviorSettings(data) {
  const fragment = document.createDocumentFragment(); const profiles = document.createElement("section"); profiles.className = "card"; profiles.append(text("h2", "Support profile"), text("p", "Profiles are validated data, not model personalities. They change presentation defaults without changing authority, privacy, or model routing.", "state-note")); const grid = document.createElement("div"); grid.className = "profile-grid";
  for (const profile of data.control.behavior.profiles) { const item = document.createElement("label"); item.className = "profile-choice"; const input = document.createElement("input"); input.type = "radio"; input.name = "profile"; input.value = profile.id; input.checked = profile.id === data.control.behavior.active_profile_id; input.disabled = !data.control.identity.can_mutate; const body = document.createElement("span"); body.append(text("strong", profile.label), text("small", `${titleCase(profile.format)} · ${profile.behavior.available_minutes} minute default steps`)); item.append(input, body); input.addEventListener("change", async () => { if (!input.checked) return; [...grid.querySelectorAll("input")].forEach((node) => { node.disabled = true; }); setBanner(`Activating ${profile.label} as deterministic member defaults…`); try { await api("/api/member/v1/settings/profile", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ profile_id: profile.id, request_id: crypto.randomUUID() }) }); setBanner(`${profile.label} is active. Authority and model routing were unchanged.`); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} The profile was not changed.`); await reloadActiveSurface(); } }); grid.append(item); }
  profiles.append(grid); fragment.append(profiles);
  const form = document.createElement("form"); form.className = "card preference-form"; form.append(text("h2", "Fine-tune your CoS"), text("p", "These bounded values feed deterministic ranking and presentation. AdministrateMe does not infer them from a diagnosis or conversation.", "state-note")); const fields = document.createElement("div"); fields.className = "preference-grid";
  const definitions = [["information_density", "Information shown at once", 1, 5], ["planning_horizon_days", "Default planning horizon (days)", 1, 14], ["task_chunk_minutes", "Default task chunk (minutes)", 5, 90], ["interruption_budget", "Proactive interruption budget", 0, 12], ["reminder_escalation", "Reminder escalation", 0, 3], ["celebration_intensity", "Celebration intensity", 0, 3], ["digest_hour", "Digest hour", 0, 23], ["quiet_start_hour", "Quiet hours start", 0, 23], ["quiet_end_hour", "Quiet hours end", 0, 23]];
  for (const [name, label, min, max] of definitions) { const input = document.createElement("input"); input.type = "number"; input.name = name; input.min = min; input.max = max; input.required = true; input.value = defaultPreferenceValue(name, data); input.disabled = !data.control.identity.can_mutate; fields.append(field(label, input)); }
  const location = document.createElement("input"); location.type = "checkbox"; location.name = "location_cues"; location.checked = Boolean(defaultPreferenceValue("location_cues", data)); location.disabled = !data.control.identity.can_mutate; const locationLabel = document.createElement("label"); locationLabel.className = "preference-check"; locationLabel.append(location, document.createTextNode(" Allow expiring, typed location cues from an authorized bridge")); fields.append(locationLabel); form.append(fields); const save = text("button", "Save fine-tuning", "button button-primary"); save.type = "submit"; save.disabled = !data.control.identity.can_mutate; form.append(save);
  form.addEventListener("submit", async (event) => { event.preventDefault(); save.disabled = true; const values = {}; for (const [name] of definitions) values[name] = Number(form.elements[name].value); values.location_cues = location.checked; setBanner("Saving bounded CoS preferences…"); try { await api("/api/member/v1/settings/preferences", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ expected_revision: data.control.revision, request_id: crypto.randomUUID(), values }) }); setBanner("CoS preferences saved and propagated to member surfaces."); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} Preferences were not changed.`); save.disabled = false; } }); fragment.append(form);
  const persona = data.control.behavior.persona; const personaCard = document.createElement("section"); personaCard.className = "card"; personaCard.append(text("h2", "CoS voice"), text("p", persona.label || titleCase(persona.id)), text("small", "Instance-wide persona · wording only · cannot change authority or model routing")); fragment.append(personaCard); return fragment;
}

function bridgeSettings(data) {
  const fragment = document.createDocumentFragment(); const intro = document.createElement("section"); intro.className = "state-card"; intro.append(text("strong", "A bridge keeps private device sources on the member’s Mac."), text("p", "The target member accepts a short-lived enrollment package. Tailscale identity, owner scope, reviewed capabilities, and macOS permissions are verified independently.")); fragment.append(intro);
  const grid = document.createElement("div"); grid.className = "settings-card-grid";
  for (const bridge of data.control.bridges) { const cardNode = document.createElement("article"); cardNode.className = "card bridge-card"; cardNode.append(text("p", titleCase(bridge.role), "eyebrow"), text("h2", bridge.member_label), text("span", titleCase(bridge.status), `pill state-label state-${String(bridge.status).replaceAll("_", "-")}`), text("p", bridge.identity_bound ? "Tailscale identity is bound." : "A verified Tailscale identity is still required.", "state-note")); const capabilities = bridge.adapters_active?.length ? bridge.adapters_active : bridge.requested_capabilities || []; if (capabilities.length) cardNode.append(permissionList("Capabilities", capabilities)); grid.append(cardNode); }
  if (data.control.bridges.length) fragment.append(grid);
  const form = document.createElement("form"); form.className = "card bridge-form"; form.append(text("h2", "Add Bridge"), text("p", "Create a resumable, secret-free enrollment request. Package export and acceptance remain a separate short-lived handoff on the target member’s device.", "state-note")); const member = selectControl(data.control.bridge_candidates.map((item) => ({ value: item.member_id, label: `${item.label} · ${titleCase(item.role)}` })), data.control.identity.view_member_id, "Choose member…"); member.name = "member"; member.required = true; member.disabled = !data.control.identity.can_mutate; form.append(field("Member", member)); const capabilities = document.createElement("fieldset"); capabilities.className = "bridge-capabilities"; capabilities.append(text("legend", "Device sources")); const choices = [["apple_calendar", "Apple Calendar"], ["apple_reminders", "Apple Reminders"], ["apple_notes", "Apple Notes"], ["apple_voice_memos", "Apple Voice Memos"], ["obsidian", "Obsidian vault"]]; for (const [value, label] of choices) { const wrap = document.createElement("label"); const input = document.createElement("input"); input.type = "checkbox"; input.name = "capability"; input.value = value; input.checked = ["apple_calendar", "apple_reminders", "apple_notes"].includes(value); input.disabled = !data.control.identity.can_mutate; wrap.append(input, document.createTextNode(` ${label}`)); capabilities.append(wrap); } form.append(capabilities); const submit = text("button", "Prepare enrollment", "button button-primary"); submit.type = "submit"; submit.disabled = !data.control.identity.can_mutate; form.append(submit);
  form.addEventListener("submit", async (event) => { event.preventDefault(); const selected = [...form.querySelectorAll('input[name="capability"]:checked')].map((input) => input.value); if (!selected.length) { setBanner("Choose at least one reviewed bridge capability."); return; } submit.disabled = true; setBanner("Recording a scoped bridge enrollment request…"); try { const result = await api("/api/member/v1/settings/bridges", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ expected_revision: data.control.revision, request_id: crypto.randomUUID(), member_id: member.value, capabilities: selected }) }); setBanner(result.next_step); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} The bridge request was not created.`); submit.disabled = false; } }); fragment.append(form); return fragment;
}

function privacySettings(data) {
  const fragment = document.createDocumentFragment(); const rules = document.createElement("section"); rules.className = "card"; rules.append(text("h2", "Privacy rules that cannot be toggled")); const list = document.createElement("ul"); list.className = "settings-rule-list"; for (const value of ["Adults control their own private provider credentials and device bridges.", "A principal may manage a child bridge, but cannot silently inspect another adult’s private sources.", "Provider-native sharing does not create AdministrateMe sharing.", "View-as changes what can be read; it never changes who performs an action.", "Privileged records never enter model processing or generic OpenClaw memory."]) list.append(text("li", value)); rules.append(list); fragment.append(rules);
  const household = document.createElement("section"); household.className = "card"; household.append(text("h2", "Household portal identities")); const members = document.createElement("ul"); members.className = "household-settings-list"; for (const member of data.control.household) { const item = document.createElement("li"); const body = document.createElement("div"); body.append(text("strong", member.label), text("small", titleCase(member.role))); item.append(body, text("span", member.portal_identity_bound ? "Tailscale bound" : "No portal login", `pill state-label state-${member.portal_identity_bound ? "healthy" : "not-configured"}`)); members.append(item); } household.append(members); fragment.append(household); return fragment;
}

async function runRegistryLifecycle(kind, item, action, reason = null) {
  const id = item[`${kind}_id`];
  setBanner(`${action === "archive" ? "Archiving" : "Restoring"} ${item.display_name}…`);
  try {
    await api(`/api/member/v1/registry/${encodeURIComponent(kind)}/${encodeURIComponent(id)}/lifecycle`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({ request_id: crypto.randomUUID(), expected_last_event_id: item.last_event_id, action, reason }),
    });
    setBanner(action === "archive" ? "Archived without erasing history. Linked records remain intact." : "Restored as an active household record.");
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} The household record was not changed.`);
  }
}

function archiveRegistryDialog(kind, item) {
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", "Archiving removes this record from active planning while preserving its links and complete event history.", "state-note"));
  const reason = document.createElement("textarea"); reason.rows = 3; reason.maxLength = 500;
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const keep = text("button", "Keep active", "button button-quiet"); keep.type = "button"; keep.addEventListener("click", closeActionDialog);
  const archive = text("button", "Archive record", "button button-primary"); archive.type = "submit"; buttons.append(keep, archive);
  form.append(field("Reason (optional)", reason), buttons);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runRegistryLifecycle(kind, item, "archive", reason.value.trim() || null); });
  openActionDialog(`Archive ${item.display_name}?`, form);
}

function registrySettings(data) {
  const fragment = document.createDocumentFragment();
  const intro = document.createElement("section"); intro.className = "state-card"; intro.append(text("strong", "Household objects have a reversible lifecycle."), text("p", "Archive stale places, assets, or service accounts without deleting evidence. Provider financial accounts remain read-only provider facts and are managed under Connections.")); fragment.append(intro);
  const registry = data.registry || {};
  for (const [plural, label] of [["places", "Places"], ["assets", "Assets"], ["accounts", "Household service accounts"]]) {
    const kind = plural.slice(0, -1); const section = document.createElement("section"); section.className = "card"; section.append(text("h2", label));
    const rows = registry[plural] || [];
    if (!rows.length) section.append(text("p", `No ${label.toLowerCase()} are recorded.`, "empty"));
    const list = document.createElement("ul"); list.className = "household-settings-list";
    for (const item of rows) {
      const row = document.createElement("li"); const body = document.createElement("div"); const status = item.lifecycle_status || "active";
      body.append(text("strong", item.display_name), text("small", `${titleCase(item.kind)} · ${titleCase(status)}${item.archive_reason ? ` · ${item.archive_reason}` : ""}`));
      const button = text("button", status === "archived" ? "Restore" : "Archive…", status === "archived" ? "button button-primary" : "button button-quiet"); button.type = "button"; button.disabled = !data.control.identity.can_mutate;
      button.addEventListener("click", () => status === "archived" ? runRegistryLifecycle(kind, item, "restore") : archiveRegistryDialog(kind, item));
      row.append(body, button); list.append(row);
    }
    section.append(list); fragment.append(section);
  }
  return fragment;
}

function systemSettings(data) {
  const fragment = document.createDocumentFragment(); const safety = document.createElement("section"); safety.className = "card settings-safety"; safety.append(text("p", "EXTERNAL-WRITE SAFETY", "eyebrow"), text("h2", data.system.observation.active ? "Observation mode is protecting providers" : "Provider writes are enabled behind every gate"), text("p", data.system.observation.explanation)); const safetyFacts = document.createElement("dl"); safetyFacts.className = "settings-facts"; for (const [label, value] of [["External writes execute", data.system.observation.writes_execute ? "Only after all gates" : "No"], ["OpenClaw", titleCase(data.system.runtime.openclaw)], ["Python runtime", titleCase(data.system.runtime.state)], ["Registered projections", `${data.system.runtime.projection_count} (constitutional count: 15)`]]) safetyFacts.append(text("dt", label), text("dd", value)); safety.append(safetyFacts); fragment.append(safety);
  const assuranceCapability = data.system.capabilities.find((value) => value.name === "source-assurance");
  if (assuranceCapability?.details?.counts) { const counts = assuranceCapability.details.counts; const assurance = document.createElement("section"); assurance.className = "card"; assurance.append(text("p", "SOURCE ASSURANCE", "eyebrow"), text("h2", assuranceCapability.state === "healthy" ? "Every processed source has an accounted disposition" : "A source fact is missing its required disposition")); const facts = document.createElement("dl"); facts.className = "settings-facts"; for (const [label, value] of [["Source facts", counts.source_facts], ["Accounted obligations", `${counts.accounted} / ${counts.obligations}`], ["Candidate sources", `${counts.eligible_candidates} proposed · ${counts.no_candidate} none`], ["Quarantined / failed", `${counts.quarantined} / ${counts.failed}`], ["Processing / missing", `${counts.pending} / ${counts.missing}`]]) facts.append(text("dt", label), text("dd", value)); assurance.append(facts, text("p", "Empty is a recorded outcome. Missing means the expected assessor, projection, or terminal action result did not arrive after its checkpoint.", "state-note")); fragment.append(assurance); }
  const workbooks = document.createElement("section"); workbooks.className = "card"; workbooks.append(text("h2", "Recovery and workbooks")); const workbookList = document.createElement("ul"); workbookList.className = "household-settings-list"; for (const workbook of data.system.workbooks) { const item = document.createElement("li"); const body = document.createElement("div"); body.append(text("strong", workbook.name), text("small", workbook.updated_at ? `Updated ${new Date(workbook.updated_at).toLocaleString()}` : "No workbook file yet")); item.append(body, text("span", titleCase(workbook.state), `pill state-label state-${String(workbook.state).replaceAll("_", "-")}`)); workbookList.append(item); } workbooks.append(workbookList); fragment.append(workbooks);
  const advanced = document.createElement("details"); advanced.className = "card settings-advanced"; advanced.append(text("summary", "Advanced capability inventory"), text("p", "Pack validation, configuration and runtime provider health are different facts. This inventory is diagnostic and does not grant a capability.", "state-note")); const list = document.createElement("ul"); list.className = "advanced-capability-list"; for (const capability of data.system.capabilities) { const item = document.createElement("li"); const body = document.createElement("div"); body.append(text("strong", capability.name), text("small", capability.kind)); item.append(body, text("span", titleCase(capability.state), `pill state-label state-${String(capability.state).replaceAll("_", "-")}`)); list.append(item); } advanced.append(list); fragment.append(advanced); return fragment;
}

function accountSettings(data) {
  const fragment = document.createDocumentFragment(); fragment.append(identitySummary(data.control.identity)); const explanation = document.createElement("section"); explanation.className = "card"; explanation.append(text("h2", "How sign-in works"), text("p", "Tailscale Serve authenticates the connecting user. AdministrateMe accepts that identity only from its loopback ingress and maps it to one reviewed household member."), text("p", "There is no separate AdministrateMe password or simulated logout. To change identity, switch the signed-in Tailscale user; bootstrap enrollment must review any mapping change.", "state-note")); fragment.append(explanation); return fragment;
}

function renderSettings(data) {
  const root = $("#surface"); root.replaceChildren(); $("#mode-label").textContent = "Private · deterministic control plane";
  root.append(settingsHero(data), settingsNav()); const content = document.createElement("div"); content.className = "settings-content";
  if (state.settingsSection === "overview") { const layout = document.createElement("div"); layout.className = "settings-overview-grid"; layout.append(attentionCard(data.control.needs_attention), identitySummary(data.control.identity, true)); content.append(layout); const sources = data.control.candidate_identification?.sources || []; const summaries = document.createElement("div"); summaries.className = "settings-summary-grid"; for (const [title, value, copy, target] of [["Connections", data.control.connections.length, "Provider accounts with separate setup, runtime, permission, and safety state.", "connections"], ["Candidate identification", sources.filter((item) => item.policy?.mode === "review").length, `Sources reviewing automatically out of ${sources.length} current and planned source paths.`, "candidate-identification"], ["Bridges", data.control.bridges.length, "Per-member device sources with explicit enrollment and consent.", "bridges"], ["CoS profile", data.control.behavior.profiles.find((item) => item.id === data.control.behavior.active_profile_id)?.label || "Not selected", "Validated behavior defaults; no authority or model-routing changes.", "behavior"]]) { const cardNode = document.createElement("button"); cardNode.type = "button"; cardNode.className = "card settings-summary-card"; cardNode.append(text("span", title, "eyebrow"), text("strong", value), text("small", copy)); cardNode.addEventListener("click", () => { state.settingsSection = target; replaceQuery({ section: target }); renderSettings(state.surface); }); summaries.append(cardNode); } content.append(summaries); }
  else if (state.settingsSection === "account") content.append(accountSettings(data));
  else if (state.settingsSection === "behavior") content.append(behaviorSettings(data));
  else if (state.settingsSection === "connections") content.append(renderConnections(data));
  else if (state.settingsSection === "candidate-identification") content.append(candidateIdentificationSettings(data));
  else if (state.settingsSection === "bridges") content.append(bridgeSettings(data));
  else if (state.settingsSection === "registry") content.append(registrySettings(data));
  else if (state.settingsSection === "privacy") content.append(privacySettings(data));
  else content.append(systemSettings(data));
  root.append(content);
}

function freshnessStrip(data) {
  const value = data.freshness;
  const strip = document.createElement("div"); strip.className = "freshness-strip";
  if (!value) return strip;
  strip.append(text("span", titleCase(value.state), `pill state-label state-${String(value.state).replaceAll("_", "-")}`));
  const latest = value.latest_source_at ? `Latest source ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value.latest_source_at))}` : "No source watermark yet";
  strip.append(text("span", `${latest} · ${value.row_count} visible record${value.row_count === 1 ? "" : "s"}`));
  if (value.checkpoint_required) strip.append(text("span", value.checkpoint_event_id ? `Checkpoint ${String(value.checkpoint_event_id).slice(-10)}` : "No processing checkpoint yet"));
  if (Number(value.lag_count || 0) > 0) strip.append(text("strong", `${value.lag_count} event${value.lag_count === 1 ? "" : "s"} waiting to process`));
  if (value.degraded_reason) strip.append(text("strong", value.degraded_reason));
  return strip;
}

function renderSurface(data, { preserveTriageView = false } = {}) {
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
    root.append(freshnessStrip(data), triageSurface(data, { preserveView: preserveTriageView }));
    applyTriageInteractionLock();
  } else if (data.surface === "comms") {
    renderComms(data);
  } else if (data.surface === "reminders") {
    renderReminders(data);
  } else if (data.surface === "people") {
    renderPeople(data);
  } else if (data.surface === "notes") {
    renderNotes(data);
  } else if (data.surface === "finance") {
    renderFinance(data);
  } else if (data.surface === "calendar") {
    renderCalendar(data);
  } else if (data.surface === "scoreboard") {
    const score = data.scoreboard || {};
    const hero = document.createElement("section"); hero.className = "hero-card";
    hero.append(text("p", "YOUR RHYTHM", "eyebrow"), text("h2", `${score.open_count || 0} gentle next steps`, "action-title"), text("p", score.reward_language === "warm" ? "Every small finish counts. You’re building momentum." : "Progress is evidence, not pressure."));
    const stats = document.createElement("dl"); stats.className = "brief-stats";
    for (const [label, value] of [["Active", score.open_count], ["Completed", score.completed_count], ["Closed", score.closed_count], ["Redirected", score.redirected_count]]) {
      const stat = text("div", "", "stat"); stat.append(text("dt", label), text("dd", value || 0)); stats.append(stat);
    }
    hero.append(stats);
    root.append(
      hero,
      card("Up next", score.next_steps),
      card("Outcomes and evidence", score.recent_outcomes, "No completed, closed, or redirected work has been recorded yet."),
      card("Completion reinforcement", score.reward_evidence, "No completion reinforcement event has been recorded yet."),
      rhythmDefinitionsCard(score.rhythms),
      card("Routine and recurrence history", score.rhythm_outcomes, "No recurring occurrence has been completed yet."),
    );
  } else if (data.surface === "settings") {
    renderSettings(data);
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
  if (document.activeElement instanceof HTMLElement) dialogReturnFocus.set(dialog, document.activeElement);
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
  const closeChat = () => {
    $("#chat-panel").classList.remove("open");
    $("#chat-open").setAttribute("aria-expanded", "false");
    $("#chat-open").focus();
  };
  $("#chat-close").addEventListener("click", closeChat);
  $("#chat-open").addEventListener("click", () => { $("#chat-panel").classList.add("open"); $("#chat-open").setAttribute("aria-expanded", "true"); $("#chat-input").focus(); });
  $("#chat-panel").addEventListener("keydown", (event) => { if (event.key === "Escape" && $("#chat-panel").classList.contains("open")) { event.preventDefault(); closeChat(); } });
  $("#chat-form").addEventListener("submit", sendChat);
  $("#chat-stop").addEventListener("click", async () => { state.controller?.abort(); await fetch("/api/member/v1/chat/abort", { method: "POST", credentials: "same-origin" }); });
  $("[data-close-dialog]").addEventListener("click", () => $("#evidence-dialog").close());
  $("[data-close-action]").addEventListener("click", closeActionDialog);
  for (const dialog of [$("#evidence-dialog"), $("#action-dialog")]) {
    dialog.addEventListener("close", () => {
      const target = dialogReturnFocus.get(dialog);
      dialogReturnFocus.delete(dialog);
      if (target?.isConnected && !target.matches(":disabled")) target.focus();
    });
  }
  $("#action-dialog").addEventListener("cancel", (event) => {
    if (addForReviewInteractionLocked() && event.currentTarget.querySelector(".add-for-review-form")) event.preventDefault();
  });
  $("#add-for-review").addEventListener("click", openAddForReview);
  try {
    state.bootstrap = await api("/api/member/v1/bootstrap"); renderNavigation(combinedNavigation(state.bootstrap)); configureChat(state.bootstrap.chat);
    const memberButton = $("#member-button"); memberButton.replaceChildren(); memberButton.className = "member-chip";
    const memberCopy = document.createElement("span"); memberCopy.append(text("strong", state.bootstrap.member.authenticated_display_name), text("small", `${titleCase(state.bootstrap.member.authenticated_role)} · ${state.bootstrap.member.authentication_method}`)); memberButton.append(memberCopy);
    memberButton.setAttribute("aria-label", `Open settings for ${state.bootstrap.member.authenticated_display_name}`);
    $("#observation-pill").textContent = state.bootstrap.observation_mode ? "Observation mode" : "Member-safe";
    $("#observation-banner").hidden = !state.bootstrap.observation_mode;
    const viewAs = $("#view-as-banner"); viewAs.hidden = !state.bootstrap.member.viewing_as;
    if (state.bootstrap.member.viewing_as) viewAs.textContent = `Viewing ${state.bootstrap.member.display_name}. Reads follow that member’s scope; actions remain attributed to ${state.bootstrap.member.authenticated_display_name}, and Settings changes are disabled.`;
    $("#add-for-review").disabled = Boolean(state.bootstrap.member.viewing_as);
    const active = currentSurface();
    const params = new URLSearchParams(location.search);
    const requestedView = params.get("view");
    if (active === "today" && ["brief", "carousel"].includes(requestedView)) state.todayView = requestedView;
    if (active === "triage") {
      if (["needs_interpretation", "ready", "needs_details", "low_signal", "recurring_exceptions", "learned_rules", "all_candidates", "recent"].includes(requestedView)) state.triageView = requestedView;
      state.triageSelected = params.get("item");
      state.triageMatter = params.get("matter");
      state.triageFocusPending = Boolean(state.triageSelected);
    }
    if (active === "comms") {
      if (["focus", "waiting", "snoozed", "quieted", "all"].includes(requestedView)) state.commsView = requestedView;
      state.commsAccount = params.get("account") || "all";
      state.commsThread = params.get("thread");
    }
    if (active === "reminders") {
      if (["now", "scheduled", "places", "anytime", "waiting", "completed"].includes(requestedView)) state.remindersView = requestedView;
      state.remindersAccount = params.get("account") || "all";
      state.remindersList = params.get("list") || "all";
      state.reminderItem = params.get("item");
      state.reminderDeepLinkPending = Boolean(state.reminderItem);
    }
    if (active === "calendar") {
      if (["day", "week", "month", "agenda", "find", "rules"].includes(requestedView)) state.calendarView = requestedView;
      state.calendarAnchor = params.get("date");
      state.calendarSelectedEvent = params.get("event");
    }
    if (active === "people") {
      if (["all", "household", "favorites", "organizations", "suppressed"].includes(requestedView)) state.peopleLens = requestedView;
      state.peopleSelected = params.get("item");
      state.peopleQuery = params.get("q") || "";
    }
    if (active === "notes") {
      if (["all", "recent", "shared", "meetings", "triage"].includes(requestedView)) state.notesLens = requestedView;
      state.notesSource = params.get("source") || "all";
      state.notesSelected = params.get("note");
      state.notesQuery = params.get("q") || "";
    }
    if (active === "settings") {
      const requestedSection = params.get("section");
      if (SETTINGS_SECTIONS.some(([value]) => value === requestedSection)) state.settingsSection = requestedSection;
    }
    if (active === "triage") {
      const requestedSort = params.get("sort");
      if (["attention", "oldest", "newest"].includes(requestedSort)) state.triageSort = requestedSort;
      state.triageSource = params.get("source") || "all";
      state.triageKind = params.get("kind") || "all";
    }
    let path = `/api/member/v1/surfaces/${encodeURIComponent(active)}`;
    if (active === "calendar") {
      const query = new URLSearchParams({ view: state.calendarView });
      if (state.calendarAnchor) query.set("date", state.calendarAnchor);
      path += `?${query}`;
    }
    if (active === "triage") {
      const triageParams = triageRequestParams();
      if (state.triageSelected) triageParams.set("triage_item", state.triageSelected);
      path += `?${triageParams}`;
    }
    let surfaceRequest;
    if (active.startsWith("quest:")) {
      surfaceRequest = api(`/api/member/v1/temporary-surfaces/${encodeURIComponent(active.slice(6))}/evaluate`).then(renderQuest);
    } else if (active === "notes") {
      surfaceRequest = api(path).then(async (data) => {
        if (state.notesQuery) {
          const query = new URLSearchParams({ q: state.notesQuery });
          if (state.notesSource !== "all") query.set("source", state.notesSource.split("|")[0]);
          const result = await api(`/api/member/v1/notes/search?${query}`);
          const originals = new Map((data.notes || []).map((note) => [note.knowledge_id, note]));
          state.notesResults = result.items.map((note) => ({ ...(originals.get(note.knowledge_id) || {}), ...note }));
        }
        renderSurface(data);
      });
    } else if (active === "people") {
      surfaceRequest = api(path).then(async (data) => {
        if (state.peopleQuery) {
          const result = await api(`/api/member/v1/people/search?q=${encodeURIComponent(state.peopleQuery)}`);
          const originals = new Map((data.cockpit?.directory || []).map((person) => [person.party_id, person]));
          state.peopleResults = result.results.map((person) => ({ ...(originals.get(person.party_id) || {}), ...person }));
        }
        renderSurface(data);
      });
    } else {
      surfaceRequest = api(path).then(renderSurface);
    }
    await Promise.all([surfaceRequest, loadChatHistory()]);
  } catch (error) { renderFailure(error); }
}

initialize();
